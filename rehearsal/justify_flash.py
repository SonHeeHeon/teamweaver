"""E7c-F: 인사팀 소명 글의 사내 열 -- 사내 LLM 대리 GLM-5.3-Flash(추론 high)로 E7c 보류 집합을 다시 쓴다(2026-10-11, claude-a).

사용자 결정(2026-10-11): 사내 LLM = GLM-5.3-Flash(Z.ai, 예산 $5 -- 꼭 필요한 것만). 사용자 지시: "flash에서 low로 하면 성과가 잘 안나온다는걸
확인했으니 high로" (E4 리뷰 판정에서 Flash low 52%, claude-b `1dc6a96`). claude-b 요청(work-split 2026-10-11): 소명 글 E7c에 사내 열이 없다 -- 작은 표본으로.
실데이터는 외부로 보내지 않으므로(api/deps.llm_client_for) 실데이터 소명 AI 글은 사내 LLM이 쓴다 -- 그 품질·속도를 재는 것이 목적이다.

사전 등록(이 docstring과 아래 상수가 기준이다 -- 측정 전에 커밋한다. 로컬 계획 .omc/plan/2026-10-11-glm-flash-justification-context.md는 참고):
- 대상: E7c 결과 파일(rehearsal/results/justify-scale-e7c.json.gz)의 보류 집합 B(새 시드 묶음 3개, 지시문을 고칠 때 보지 않은 데이터) 사업 75건 × 1회.
  입력(팀·사실)은 파일에 저장된 그대로다 -- E7c 1회차(run 0)의 외부 gpt-6-luna(low)와 같은 입력으로 짝 비교한다.
  대상 A(조율 집합)는 쓰지 않는다: 비용(전체 281건 약 $1.4·2시간 추정)과, A는 지시문을 고칠 때 본 집합이라 낙관적이다.
- 모델: glm-5.3-flash, reasoning_effort=high, Z.ai 공식 주소(가상 데이터만). 지시문·문법·검사기·덧붙임은 서비스 그대로(api/rag/justification.generate_justification).
  지시문 해시를 결과에 남긴다. 클라이언트 시간 한도는 TIMEOUT_S(실제 걸리는 시간을 보려고) -- 서비스 상한 SERVICE_TIMEOUT_S 안 비율은 따로 보고한다.
- F1(필수): 채택된 글 전부가 무결성 검사(rehearsal.justify_scale_e7c.integrity_problems, 사람별 덧붙임 형식)를 실패 0으로 통과. 채택 0이면 "해당 없음".
- F2(목표): 채택률(분모 = 계획한 75건 전부, AI 호출 오류·시간 초과 포함) ≥95% 달성 · 90~95% 조건부 · <90% 미달 -- E7c G2와 같은 기준.
- F3(보고, 기준 없음): luna 1회차와 짝 비교(채택 여부 McNemar 정확 검정), 탈락 규칙, 지연 p50·p95·최대와 서비스 상한 안 비율, 편당 비용·출력 토큰,
  AI가 스스로 인용한 불리한 사실 비율, 덧붙임이 붙은 글 비율, 팀 크기별(1~10·11~20·21명 이상).
- 비용 상한 COST_CAP_USD를 넘으면 새 호출을 멈추고 "미완"으로 표시한다. 동시 WORKERS, 재시도 1.
- **측정 뒤 수정(2026-10-11)**: 귀속 검사용 사람 ID 모양이 DP만 잡고 바로 붙은 한글 조사 앞에서 끊지 못해(_PID) 200명 묶음의 AI·AU 사람이 사실 문구에만 나올 때 빠질 수 있었다. 모양을 고치고
  저장된 채택 글의 무결성을 AI 호출 없이 다시 검사한다(--render-only가 recheck). 기준은 그대로이고 검사가 엄격해지는 방향이다.
- 측정 전 배관 확인(지시문·기준 변경 없음, 기록만): 대상 A의 3건(팀 4·9·28명) → 3/3 채택, 지연 84·114·245초, 출력 토큰 5.6천·7.2천·18.5천, 비용 $0.0034·$0.0046·$0.012.

  uv run python -m rehearsal.justify_flash             → rehearsal/results/justify-flash.{json.gz,html}
  uv run python -m rehearsal.justify_flash --render-only
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rehearsal import justify_scale as e7b  # noqa: E402
from rehearsal import justify_scale_e7c as e7c  # noqa: E402

OUT_JSON = ROOT / "rehearsal" / "results" / "justify-flash.json.gz"
OUT_HTML = ROOT / "rehearsal" / "results" / "justify-flash.html"
MODEL, EFFORT, URL = "glm-5.3-flash", "high", "https://api.z.ai/api/paas/v4"
SET, RUN = "B", 0
WORKERS, COST_CAP_USD, TIMEOUT_S = 4, 1.0, 600
SERVICE_TIMEOUT_S = 40.0            # api/routes/justification.AI_TIMEOUT_S(claude-b) -- 시험이 같은지 확인한다
F2_TARGET, F2_FLOOR = e7c.G2_TARGET, e7c.G2_FLOOR
BINS = ((1, 10, "1~10명"), (11, 20, "11~20명"), (21, 999, "21명 이상"))
_PID = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{4}(?!\d)")   # 사람 ID 모양 DP·AI·AU+4자리(사업은 J+3자리). 한글 조사가 바로 붙어도(\b는 한글을 글자로 본다) 잡는다


# ---------------------------------------------------------------- 재료
def load_source() -> dict:
    return json.loads(gzip.decompress(e7c.OUT_JSON.read_bytes()))


def prime_ids(src: dict) -> dict[str, set]:
    """검사기(known_ids)·귀속 검사용 사람 ID를 저장된 입력으로 채운다(보류 묶음은 디스크에 없다). 팀 밖 ID는 어느 쪽이든 S3라 채택 여부는 같다."""
    people: dict[str, set] = {}
    for key, meta in src["inputs"].items():
        b = key.split("/", 1)[0]
        ids = people.setdefault(b, set())
        ids.update(meta["team"])
        for f in meta["facts"]:
            ids.update(f["owners"])
            ids.update(_PID.findall(f["text"]))
    for b, ids in people.items():
        e7b._PERSON_CACHE[b] = ids
        e7b._ID_CACHE[b] = ids | {k.split("/", 1)[1] for k in src["inputs"] if k.startswith(b + "/")}
    return people


def planned(src: dict) -> list[dict]:
    """대상 = 보류 집합 B의 1회차(luna 짝)."""
    return [c for c in src["calls"] if c["set"] == SET and c["run"] == RUN]


def prompt_sha() -> str:
    from api.rag.justification import SYSTEM, TASK
    return hashlib.sha256((SYSTEM + "\n" + TASK).encode("utf-8")).hexdigest()[:16]


def make_client():
    from openai import OpenAI
    from core.config import load_env
    load_env()
    if not os.environ.get("ZAI_API_KEY"):
        raise SystemExit("ZAI_API_KEY가 없다(.env)")
    return OpenAI(base_url=URL, api_key=os.environ["ZAI_API_KEY"], timeout=TIMEOUT_S, max_retries=1)


# ---------------------------------------------------------------- 측정
def run(client=None, *, limit: int | None = None, cost_cap: float = COST_CAP_USD, workers: int = WORKERS) -> dict:
    from api.rag.justification import generate_justification
    from core.config import load_pricing
    if MODEL not in load_pricing().get("models", {}):
        raise SystemExit(f"{MODEL}의 단가가 pricing.json에 없어 비용 상한이 작동하지 않는다")
    src = load_source()
    people = prime_ids(src)
    jobs = planned(src)[:limit] if limit else planned(src)
    client = client or make_client()
    files = ("rehearsal/justify_flash.py", "rehearsal/justify_scale_e7c.py", "rehearsal/justify_scale.py", "core/kg/justify.py",
             "api/rag/justification.py", "fixtures/pricing.json")
    res = {"experiment": "E7c-F", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *files], capture_output=True, text=True).stdout.strip()),
           "sha256": {f: e7b._sha(ROOT / f) for f in files}, "source": "rehearsal/results/justify-scale-e7c.json.gz", "source_commit": src["commit"],
           "source_model": src["model"], "model": MODEL, "reasoning_effort": EFFORT, "url": URL, "prompt_sha": prompt_sha(),
           "criteria": {"F2_target": F2_TARGET, "F2_floor": F2_FLOOR, "set": SET, "run": RUN, "cost_cap_usd": cost_cap,
                        "timeout_s": TIMEOUT_S, "service_timeout_s": SERVICE_TIMEOUT_S, "workers": workers},
           "planned_calls": len(jobs), "calls": []}
    spent, lock, stop = [0.0], threading.Lock(), threading.Event()

    def one(c):
        if stop.is_set():
            return None
        inp = e7c._inp(src, c)
        t0 = time.monotonic()
        out = generate_justification(client, MODEL, inp, reasoning_effort=EFFORT)
        wall = round(time.monotonic() - t0, 2)       # 호출 오류·시간 초과도 걸린 시간이 남는다(usage는 성공한 호출에만 있다)
        with lock:                                   # 끝난 호출마다 바로 센다 -- 넘으면 아직 시작 안 한 호출은 부르지 않는다(이미 나간 호출만큼 넘을 수 있다)
            spent[0] += (out["usage"] or {}).get("cost_usd", 0) or 0
            if spent[0] > cost_cap:
                stop.set()
        ver = out["verification"] or {}
        row = {"bundle": c["bundle"], "project_id": c["project_id"], "team": c["team"], "method": out["method"],
               "fallback_reason": out["fallback_reason"], "rules": sorted({v["rule"] for v in ver.get("violations", [])}),
               "violations": ver.get("violations", [])[:6], "adverse": ver.get("adverse"), "usage": out["usage"], "wall_s": wall,
               "llm_text": out["llm_text"],
               "rendered": out["text"] if out["method"] == "graphrag" else None, "addendum": out["addendum"],
               "appended_adverse": out["appended_adverse"], "addendum_groups": out["addendum_groups"],
               "luna_method": c["method"], "luna_fallback": c.get("fallback_reason"), "luna_usage": c.get("usage"),
               "adverse_total": sum(1 for f in inp.facts if f.adverse)}
        if out["method"] == "graphrag":
            row["integrity"] = e7c.integrity_problems(row, inp, people[c["bundle"]])
        return row

    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, c) for c in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            row = f.result()
            if row is None:
                continue
            res["calls"].append(row)
            if i % 10 == 0:
                acc = sum(1 for r in res["calls"] if r["method"] == "graphrag")
                print(f"  {i}/{len(jobs)} 채택 {acc}/{len(res['calls'])} ${spent[0]:.3f}", flush=True)
    res["calls"].sort(key=lambda r: (r["bundle"], r["project_id"]))
    res["spent_usd"] = round(spent[0], 4)
    res["stopped_by_cost_cap"] = stop.is_set()
    res["summary"] = summarize(res)
    return res


def recheck(res: dict) -> dict:
    """저장된 채택 글의 무결성을 지금 검사기·사람 ID로 다시 본다(AI 호출 없음, 결정적)."""
    src = load_source()
    people = prime_ids(src)
    changed = 0
    for r in res["calls"]:
        if r["method"] == "graphrag":
            new = e7c.integrity_problems(r, e7c._inp(src, {"bundle": r["bundle"], "project_id": r["project_id"]}), people[r["bundle"]])
            changed += new != r.get("integrity")
            r["integrity"] = new
    res["integrity_rechecked"] = {"when": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "changed": changed,
                                  "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()}
    return res


# ---------------------------------------------------------------- 요약
def mcnemar_exact(b: int, c: int) -> float:
    """짝 이진 결과의 정확 McNemar(양측): 불일치 b+c건 중 적은 쪽 이하가 나올 확률 × 2."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def _q(xs: list, p: float):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def verdict(rate: float, complete: bool) -> str:
    if not complete:
        return "미완"
    return "달성" if rate >= F2_TARGET else ("조건부" if rate >= F2_FLOOR else "미달")


def _block(rows: list[dict], denom: int) -> dict:
    acc = [r for r in rows if r["method"] == "graphrag"]
    lat = [r["wall_s"] for r in rows]
    cost = [r["usage"]["cost_usd"] for r in rows if r.get("usage")]
    outs = [r["usage"]["out"] for r in rows if r.get("usage")]
    luna_lat = [r["luna_usage"]["latency_s"] for r in rows if r.get("luna_usage")]
    return {"n": len(rows), "planned": denom, "accepted": len(acc), "rate": len(acc) / denom if denom else None,
            "luna_accepted": sum(1 for r in rows if r["luna_method"] == "graphrag"),
            "latency_p50": _q(lat, .5), "latency_p95": _q(lat, .95), "latency_max": max(lat) if lat else None,
            "within_service": sum(1 for x in lat if x <= SERVICE_TIMEOUT_S), "luna_latency_p50": _q(luna_lat, .5),
            "errors": sum(1 for r in rows if (r["fallback_reason"] or "").startswith("llm_")),
            "cost_total": round(sum(cost), 4), "cost_per_text": (sum(cost) / len(cost)) if cost else None, "out_p50": _q(outs, .5)}


def summarize(res: dict) -> dict:
    rows, denom = res["calls"], res["planned_calls"]
    complete = not res["stopped_by_cost_cap"] and len(rows) == denom
    acc = [r for r in rows if r["method"] == "graphrag"]
    bad = [r for r in acc if r.get("integrity")]
    s = _block(rows, denom)
    s.update(complete=complete, F2=verdict(s["rate"] or 0, complete),
             F1=("해당 없음" if not acc else ("통과" if not bad else "실패")), F1_checked=len(acc), F1_failed=len(bad),
             F1_examples=[{"key": f"{r['bundle']}/{r['project_id']}", "problems": r["integrity"][:5]} for r in bad[:5]])
    b = sum(1 for r in rows if r["luna_method"] == "graphrag" and r["method"] != "graphrag")
    c = sum(1 for r in rows if r["method"] == "graphrag" and r["luna_method"] != "graphrag")
    s["paired"] = {"luna_only": b, "flash_only": c, "p": mcnemar_exact(b, c)}
    s["fallback_reasons"] = dict(Counter((r["fallback_reason"] or "").split(":")[0] for r in rows if r["method"] != "graphrag"))
    s["rules"] = dict(Counter(rule for r in rows if r["method"] != "graphrag" for rule in r["rules"]))
    adv_total = sum(r["adverse_total"] for r in acc)
    adv_appended = sum(len(r["appended_adverse"] or []) for r in acc)
    s["adverse"] = {"facts": adv_total, "self_cited": adv_total - adv_appended,
                    "self_cite_rate": (adv_total - adv_appended) / adv_total if adv_total else None,
                    "texts_with_addendum": sum(1 for r in acc if r["addendum"])}
    s["by_bundle"] = {b_: _block([r for r in rows if r["bundle"] == b_], sum(1 for r in rows if r["bundle"] == b_))
                      for b_ in sorted({r["bundle"] for r in rows})}
    s["by_team"] = {label: _block(g, len(g)) for lo, hi, label in BINS if (g := [r for r in rows if lo <= r["team"] <= hi])}
    return s


# ---------------------------------------------------------------- 보고서
def _f(x, fmt="{:.1f}"):
    return "—" if x is None else fmt.format(x)


def _pct(x):
    return "—" if x is None else f"{x:.1%}"


def render(res: dict) -> str:
    s = res["summary"]
    rows_b = "".join(f"<tr><td>{html.escape(k)}</td><td>{v['accepted']}/{v['planned']}</td><td>{v['luna_accepted']}/{v['planned']}</td>"
                     f"<td>{_f(v['latency_p50'])}초</td><td>{_f(v['latency_max'])}초</td><td>{v['within_service']}/{v['n']}</td>"
                     f"<td>${_f(v['cost_per_text'], '{:.4f}')}</td></tr>" for k, v in s["by_bundle"].items())
    rows_t = "".join(f"<tr><td>{k}</td><td>{v['accepted']}/{v['planned']}</td><td>{_f(v['latency_p50'])}초</td><td>{_f(v['latency_p95'])}초</td>"
                     f"<td>{v['within_service']}/{v['n']}</td><td>{_f(v['out_p50'], '{:,.0f}')}</td><td>${_f(v['cost_per_text'], '{:.4f}')}</td></tr>"
                     for k, v in s["by_team"].items())
    rej = [r for r in res["calls"] if r["method"] != "graphrag"]
    rej_rows = "".join(f"<tr><td>{html.escape(r['bundle'])}/{html.escape(r['project_id'])}</td><td>{r['team']}</td>"
                       f"<td>{html.escape(r['fallback_reason'] or '')}</td><td>{html.escape('; '.join(v.get('rule', '') + ' ' + (v.get('detail') or v.get('sentence') or '')[:80] for v in r['violations'][:3]))}</td></tr>"
                       for r in rej[:30]) or "<tr><td colspan=4>없음</td></tr>"
    ad = s["adverse"]
    p = s["paired"]
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>소명 글 사내 열(Flash high)</title><style>
body{{font:15px/1.7 -apple-system,"Apple SD Gothic Neo",sans-serif;max-width:980px;margin:auto;padding:24px 16px;color:#222;background:#fff}}
table{{border-collapse:collapse;width:100%;margin:.6em 0 1.4em;font-size:.92em}}td,th{{border-bottom:1px solid #ddd;padding:5px 7px;text-align:left;vertical-align:top}}
.box{{background:#f3f6fa;border-radius:8px;padding:10px 16px;margin:1em 0}}.warn{{background:#fff5e6}}.sub{{color:#666}}.w{{overflow-x:auto}}
</style></head><body>
<h1>E7c-F: 인사팀 소명 글 — 사내 LLM(GLM-5.3-Flash·Z.ai, 추론 high)</h1>
<p class="sub">{html.escape(res['when'])} · 코드 {html.escape(res['commit'])}{' (작업 트리 변경 있음)' if res['dirty'] else ''} · 지시문 해시 {res['prompt_sha']} ·
입력 = E7c(<code>{html.escape(res['source_commit'])}</code>) 보류 집합 B 1회차, 짝 = 외부 {html.escape(res['source_model'])}(low) · 비용 ${res['spent_usd']:.3f}
{' · <b>비용 상한으로 멈춤(미완)</b>' if res['stopped_by_cost_cap'] else ''}
{f" · 측정 뒤 무결성 다시 검사(사람 ID 모양 수정, 코드 {html.escape(res['integrity_rechecked']['commit'])}): 바뀐 글 {res['integrity_rechecked']['changed']}" if res.get('integrity_rechecked') else ''}</p>
<div class="box"><b>판정</b> — F1 무결성: <b>{s['F1']}</b>(채택 글 {s['F1_checked']}편 검사, 실패 {s['F1_failed']}) ·
F2 채택률: <b>{_pct(s['rate'])} ({s['accepted']}/{s['planned']}) → {s['F2']}</b>(기준 ≥{F2_TARGET:.0%} 달성, {F2_FLOOR:.0%}~{F2_TARGET:.0%} 조건부)</div>
<div class="w"><table><tr><th></th><th>사내 Flash high</th><th>외부 luna low(E7c 1회차, 같은 입력)</th></tr>
<tr><td>채택</td><td>{s['accepted']}/{s['planned']}</td><td>{s['luna_accepted']}/{s['planned']}</td></tr>
<tr><td>짝 비교</td><td colspan=2>luna만 채택 {p['luna_only']} · Flash만 채택 {p['flash_only']} · McNemar 정확 p={p['p']:.3g}</td></tr>
<tr><td>AI 호출 오류·시간 초과</td><td>{s['errors']}</td><td>—</td></tr>
<tr><td>지연 p50 / p95 / 최대</td><td>{_f(s['latency_p50'])} / {_f(s['latency_p95'])} / {_f(s['latency_max'])}초</td><td>p50 {_f(s['luna_latency_p50'])}초</td></tr>
<tr><td>서비스 대기 상한({SERVICE_TIMEOUT_S:.0f}초) 안</td><td>{s['within_service']}/{s['n']}</td><td>—</td></tr>
<tr><td>편당 비용 · 출력 토큰 p50</td><td>${_f(s['cost_per_text'], '{:.4f}')} · {_f(s['out_p50'], '{:,.0f}')}</td><td>—</td></tr>
<tr><td>불리한 사실 스스로 인용</td><td>{_pct(ad['self_cite_rate'])}({ad['self_cited']}/{ad['facts']}) · 덧붙임 붙은 글 {ad['texts_with_addendum']}편</td><td>—</td></tr>
</table></div>
<h2>묶음별</h2><div class="w"><table><tr><th>묶음</th><th>Flash 채택</th><th>luna 채택</th><th>지연 p50</th><th>최대</th><th>{SERVICE_TIMEOUT_S:.0f}초 안</th><th>편당 비용</th></tr>{rows_b}</table></div>
<h2>팀 크기별</h2><div class="w"><table><tr><th>팀</th><th>채택</th><th>지연 p50</th><th>p95</th><th>{SERVICE_TIMEOUT_S:.0f}초 안</th><th>출력 토큰 p50</th><th>편당 비용</th></tr>{rows_t}</table></div>
<h2>탈락·대체</h2><p>대체 사유 {html.escape(json.dumps(s['fallback_reasons'], ensure_ascii=False))} · 검사 규칙 {html.escape(json.dumps(s['rules'], ensure_ascii=False))}</p>
<div class="w"><table><tr><th>사업</th><th>팀</th><th>사유</th><th>위반(앞 3개)</th></tr>{rej_rows}</table></div>
<div class="box warn"><b>읽을 때 주의</b><ul>
<li>사내 LLM은 Z.ai 공식 API의 GLM-5.3-Flash로 가정했다(사용자 결정). 실제 사내 서버는 서빙 환경이 달라 속도·결과가 다를 수 있다.</li>
<li>지연은 호출 전체 시간(오류 포함)이고 동시 {res['criteria']['workers']}건으로 잰 값이다. 실패한 호출의 비용은 0으로 셌다(usage 없음). 서비스는 소명 AI를 동시 6건까지 부른다.</li>
<li>지시문은 외부 luna의 E7b 탈락을 보고 고쳤다. Flash에 맞춰 고친 적은 없다. 표본은 75건 × 1회다(묶음 3개).</li>
<li>서비스 기본 추론 강도는 pricing.json의 Flash 항목(low)이다. 이 측정은 사용자 지시로 high다.</li>
<li>틀린 글이 나가지 않는가(F1)는 모델과 무관한 검사기가 지킨다. 채택률·속도는 화면에서 AI 글 대신 정해진 틀이 보이는 빈도를 정한다.</li></ul></div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args()
    if a.render_only:
        res = recheck(json.loads(gzip.decompress(OUT_JSON.read_bytes())))
        res["summary"] = summarize(res)
        OUT_JSON.write_bytes(gzip.compress(json.dumps(res, ensure_ascii=False, indent=1).encode("utf-8")))
    else:
        res = run()
        OUT_JSON.write_bytes(gzip.compress(json.dumps(res, ensure_ascii=False, indent=1).encode("utf-8")))
    OUT_HTML.write_text(render(res), encoding="utf-8")
    s = res["summary"]
    print(f"F1 {s['F1']} · F2 {s['rate']:.1%} {s['F2']} · 짝 {s['paired']} · 지연 p50 {s['latency_p50']} · {SERVICE_TIMEOUT_S:.0f}초 안 "
          f"{s['within_service']}/{s['n']} · ${res['spent_usd']} → {OUT_HTML.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
