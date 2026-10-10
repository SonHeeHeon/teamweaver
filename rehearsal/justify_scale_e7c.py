"""E7c: 인사팀 소명 글 재측정 -- 불리한 사실 서버 덧붙임(사용자 결정 2026-10-10, 선택지 b) + 지시문 개선 뒤.

E7b(`rehearsal/justify_scale.py`, 사전 등록 be6c902, 결과 ca1dfbf: G1 통과, G2 94.7% 조건부)와 같은 방법에 세 가지를 더했다.
(1) 채택된 글이 빠뜨린 불리한 사실을 서버가 정해진 문단으로 덧붙인다(core/kg/justify.adverse_addendum) -- 무결성 검사에 넣는다.
(2) 지시문만 고쳤다(api/rag/justification._system: 라벨별 사실 종류 표·"한 사람의 여러 종류 사실은 사람 주어로"·", 다만" 금지·머리에 기술 이름 금지·한국어만).
    문법·LABELS·검사기(verify)는 그대로다 -- E7b와 G1이 비교 가능하다.
(3) Codex 사후 리뷰(2026-10-10) 지적: G1에 변형 종류별 최소 건수와 무결성 검사 건수 하한을 둔다.

사전 등록(이 docstring과 아래 상수가 기준이다 -- 측정 전에 커밋한다. 로컬 계획 .omc/plan/2026-10-10-justification-adverse-e7c.md는 참고):
- 대상 A(조율 집합): E7b와 같은 시연 묶음 6개 × 배치 있는 사업 전부 × RUNS회, 같은 미리 계산 배치(파일 해시를 결과에 남긴다).
  **E7b 탈락 33건을 보고 지시문을 고쳤으므로 이 집합의 채택률은 낙관적이다.**
- 대상 B(보류 집합, 지시문을 고칠 때 보지 않은 데이터): 같은 생성기(core.ingest.org_profile)로 새 시드 HOLDOUT_SEED의 묶음 3개(SET_B)를 만들고,
  이 스크립트 안에서 서비스 기본 설정(PlacementSettings())으로 배치를 푼다(연초 = 안 A, 운영 중 = K 비교 0~3의 가장 큰 K 배치).
  리뷰 글 판정은 규칙 판정(LLM 판정 캐시를 쓰지 않는다 -- 비용·실제 데이터 폴더 보호). 사실 형식은 같다.
- G1(필수): (a) 판별 가능한 주입 오류 탐지율 100%, M·H 묶음 각각 100% (b) 변형 16종 각각 만든 건수 ≥ max(FAMILY_FLOOR_MIN, FAMILY_FLOOR_SHARE × 채택 글 수)
  (c) 무결성 검사 건수 = 채택 글 수 ≥ INTEGRITY_MIN, 실패 0. 무결성 = E7b 항목(중괄호·칩 수·칩 앞 문구·귀속 attribution_problems) +
  덧붙임 확인(서버 함수를 쓰지 않고 따로 계산): 최종 글이 AI 부분 채움으로 시작하고, 덧붙임 문단의 칩이 정확히 "AI 원문에 자리표시가 없는 불리한 사실"(사실 순서)이며
  각 칩 앞이 그 사실의 phrase_full이고 덧붙임 문단이 ADDENDUM_LEAD로 시작하며, 모든 불리한 사실의 칩이 최종 글에 있다. 빠진 것이 없으면 덧붙임이 없어야 한다.
  변형·오류 주입은 E7b와 같다(rehearsal.justify_scale.mutations, 시드만 SEED).
- G2(목표, 대상 A): 채택률 ≥95% 달성, 90~95% 조건부, <90% 미달. 분모 = 대상 A의 계획한 호출 전부(AI 호출 오류 포함).
- G2h(목표, 대상 B): 같은 기준. "처음 보는 데이터"의 추정치는 G2h다.
- G3(보고, 기준 없음): E7b 대비 묶음별 채택률·탈락 규칙 변화, AI 자체 불리한 사실 인용률, 덧붙임이 필요한 글 비율·건수, 비용·지연·길이·팀 크기별 채택률·연결 말 감사.
- 모델: fixtures/pricing.json briefing_model(gpt-6-luna, 추론 low) -- 서비스와 같다. 비용 상한 COST_CAP_USD에서 멈추면 "미완".

  uv run python -m rehearsal.justify_scale_e7c             → rehearsal/results/justify-scale-e7c.{json.gz,html}
  uv run python -m rehearsal.justify_scale_e7c --render-only
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import html
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rehearsal import justify_scale as e7b  # noqa: E402

OUT_JSON = ROOT / "rehearsal" / "results" / "justify-scale-e7c.json.gz"
OUT_HTML = ROOT / "rehearsal" / "results" / "justify-scale-e7c.html"
E7B_JSON = ROOT / "rehearsal" / "results" / "justify-scale.json.gz"
SET_A = e7b.BUNDLES
HOLDOUT_SEED = 20261010
SET_B = (("holdout-n100", 100, "planning"), ("holdout-n200", 200, "planning"), ("holdout-n100-operating", 100, "operating"))
RUNS, WORKERS, COST_CAP_USD, SEED = 3, 6, 3.0, 20261010
# 사전 판정 기준(측정 전 고정)
G1_DETECTION = 1.0
FAMILY_FLOOR_MIN, FAMILY_FLOOR_SHARE = 100, 0.5     # 변형 종류마다 만든 건수 하한
INTEGRITY_MIN = 300                                 # 무결성 검사 건수 하한(= 채택 글 수여야 한다)
G2_TARGET, G2_FLOOR = 0.95, 0.90
FAMILIES = ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "H1", "H2", "H3", "H4", "H5", "H6", "H7")
CALIBRATION = ("지시문은 E7b 탈락 33건(라벨-사실 종류 불일치 25, 일본어·한자 5, ', 다만' 2, 기술 이름 머리 1)을 보고 고쳤다(대상 A = 그 탈락이 나온 집합). "
               "측정 전 배관 확인: 대상 A 100명 묶음 사업 3개 × 1회 → 3/3 채택, 덧붙임 없음, 무결성 문제 없음(지시문 변경 없음). 보류 묶음 100명 생성·풀이 확인(31초).")


# ---------------------------------------------------------------- 재료
def holdout_inputs(name: str, size: int, scenario: str, tmp: Path) -> tuple[list, dict]:
    """보류 묶음: 새 시드로 만들고 서비스 기본 설정으로 배치를 푼다. 검사기용 ID는 e7b의 캐시에 넣는다(inject가 같은 함수로 읽는다)."""
    import time
    from api.settings import PlacementSettings
    from core.evaluate.operating import compare_move_budgets
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.ingest.org_profile import generate_org_bundle
    from core.kg import build_kg
    from core.kg.justify import justification_input
    from core.optimize.alternatives import generate_plans_streaming
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    root = generate_org_bundle(tmp / name, size, seed=HOLDOUT_SEED, scenario=scenario)
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    kg = build_kg(b, ds, parsed)
    t0 = time.perf_counter()
    if scenario == "operating":
        rows = [r for r in compare_move_budgets(g, S, C, params, ds.current, ks=(0, 1, 2, 3)) if r.get("accepted") and r.get("entries")]
        row = max(rows, key=lambda r: r["k"])
        entries = [AssignEntry(**e) if isinstance(e, dict) else e for e in row["entries"]]
        source = f"운영 중 K={row['k']} 배치"
    else:
        gen = generate_plans_streaming(g, S, C, params, 1, {})
        entries = list(next(gen).entries)
        gen.close()
        source = "안 A"
    solve_s = round(time.perf_counter() - t0, 1)
    projects = sorted({e.project_id for e in entries})
    out = [justification_input(kg, j, entries, graph=g, S=S, C=C, params=params) for j in projects]
    e7b._PERSON_CACHE[name] = {p.id for p in ds.people}
    e7b._ID_CACHE[name] = e7b._PERSON_CACHE[name] | {p.id for p in ds.projects}
    return out, {"bundle": name, "plan": source, "people": len(ds.people), "projects": len(projects), "review_judge": "rule",
                 "seed": HOLDOUT_SEED, "scenario": scenario, "solve_s": solve_s}


# ---------------------------------------------------------------- 실행
def run() -> dict:
    from openai import OpenAI
    from api.rag.justification import generate_justification
    from core.config import load_env, load_pricing
    from core.kg.justify import template_text
    load_env()
    model = load_pricing()["briefing_model"]
    if model not in load_pricing().get("models", {}):
        raise SystemExit(f"{model}의 단가가 pricing.json에 없어 비용 상한이 작동하지 않는다")
    client = OpenAI(timeout=180, max_retries=1)
    files = ("rehearsal/justify_scale_e7c.py", "rehearsal/justify_scale.py", "core/kg/justify.py", "api/rag/justification.py", "core/kg/views.py",
             "core/kg/graph.py", "core/ingest/skills.py", "core/ingest/skill_dictionary.json", "core/ingest/convert.py", "core/ingest/org_profile.py")
    pre = {n: e7b._sha(ROOT / "demo" / "precomputed" / f"{n}.json") for n in SET_A}
    res = {"experiment": "E7c", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *files], capture_output=True, text=True).stdout.strip()),
           "sha256": {f: e7b._sha(ROOT / f) for f in files}, "precomputed_sha256": pre, "model": model, "runs": RUNS,
           "criteria": {"G1_detection": G1_DETECTION, "family_floor_min": FAMILY_FLOOR_MIN, "family_floor_share": FAMILY_FLOOR_SHARE,
                        "integrity_min": INTEGRITY_MIN, "G2_target": G2_TARGET, "G2_floor": G2_FLOOR, "holdout_seed": HOLDOUT_SEED},
           "sets": {"A": list(SET_A), "B": [n for n, _, _ in SET_B]}, "bundles": {}, "calls": [], "inputs": {}}
    spent, lock = 0.0, threading.Lock()
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="tw-e7c-") as td:
        src = Path.home() / ".teamweaver" / "review_judgments_synthetic.json"
        cache = None
        if src.exists():                                   # 실제 데이터 폴더는 읽기만 -- 임시 복사본을 쓴다
            cache = Path(td) / "judge.json"
            shutil.copy(src, cache)
        jobs = []
        prepared = [(n, "A", lambda n=n: e7b.inputs_for(n, Path(td), cache)) for n in SET_A]
        prepared += [(n, "B", lambda n=n, s=s, sc=sc: holdout_inputs(n, s, sc, Path(td))) for n, s, sc in SET_B]
        for name, part, make in prepared:
            inps, info = make()
            res["bundles"][name] = {**info, "set": part}
            for inp in inps:
                key = f"{name}/{inp.project_id}"
                res["inputs"][key] = {"team": inp.team, "facts": [asdict(f) for f in inp.facts],
                                      "template_chars": len(template_text(inp.facts))}
                jobs += [(name, part, inp, r) for r in range(RUNS)]
            print(f"{name}: {res['bundles'][name]}", flush=True)
        res["planned_calls"] = len(jobs)
        res["planned_by_set"] = {p: sum(1 for j in jobs if j[1] == p) for p in ("A", "B")}

        def one(job):
            name, part, inp, r = job
            if stop.is_set():
                return None
            out = generate_justification(client, model, inp)
            ver = out["verification"] or {}
            return {"bundle": name, "set": part, "project_id": inp.project_id, "run": r, "team": len(inp.team), "method": out["method"],
                    "fallback_reason": out["fallback_reason"], "rules": sorted({v["rule"] for v in ver.get("violations", [])}),
                    "violations": ver.get("violations", [])[:6], "coverage": ver.get("coverage"), "sentences": ver.get("sentences"),
                    "adverse": ver.get("adverse"), "chars": ver.get("chars") or 0, "usage": out["usage"], "llm_text": out["llm_text"],
                    "rendered": out["text"] if out["method"] == "graphrag" else None,
                    "addendum": out["addendum"], "appended_adverse": out["appended_adverse"],
                    "final_chars": len(out["text"]) if out["method"] == "graphrag" else None}
        with ThreadPoolExecutor(WORKERS) as ex:
            futs = [ex.submit(one, j) for j in jobs]
            for i, f in enumerate(as_completed(futs), 1):
                row = f.result()
                if row is None:
                    continue
                with lock:
                    res["calls"].append(row)
                    spent += (row["usage"] or {}).get("cost_usd", 0)
                    if spent > COST_CAP_USD:
                        stop.set()
                if i % 50 == 0:
                    acc = sum(1 for c in res["calls"] if c["method"] == "graphrag") / len(res["calls"])
                    print(f"  {i}/{len(jobs)} accepted {acc:.1%} spent ${spent:.3f}", flush=True)
    res["spent_usd"] = round(spent, 4)
    res["stopped_by_cost_cap"] = stop.is_set()
    res["mutations"] = inject(res)
    res["free_text_audit"] = e7b.audit(res)
    res["summary"] = summarize(res)
    OUT_JSON.write_bytes(gzip.compress(json.dumps(res, ensure_ascii=False, indent=1).encode("utf-8")))
    return res


def _inp(res: dict, c: dict):
    from core.kg.justify import Fact, JustificationInput
    meta = res["inputs"][f"{c['bundle']}/{c['project_id']}"]
    return JustificationInput(c["project_id"], "", meta["team"],
                              [Fact(f["id"], f["kind"], f["text"], f["phrase"], tuple(f["owners"]), f["skill"], f["status"], f["phrase_full"],
                                    f["adverse"]) for f in meta["facts"]], e7b._known_ids(c["bundle"]))


def integrity_problems(c: dict, inp, person_ids: set) -> list[str]:
    """채택된 글 하나의 무결성(검사기·서버 덧붙임 함수와 따로 판단). 빈 목록이면 통과."""
    from core.kg.justify import ADDENDUM_LEAD, SLOT, render_ok
    from core.kg.justify import render as render_fill
    facts = {f.id: f for f in inp.facts}
    llm, final = c["llm_text"], c["rendered"] or ""
    ai = render_fill(llm, inp)
    bad = [] if render_ok(llm, ai) else ["중괄호 또는 칩 수"]
    for fid in SLOT.findall(llm):
        f = facts.get(fid)
        if f is None or (f"{f.phrase}[{fid}]" not in ai and f"{f.phrase_full}[{fid}]" not in ai):
            bad.append(f"{fid} 문구")
    slotted = set(re.findall(r"\{\s*(F\d+)\s*\}", llm))
    want = [f.id for f in inp.facts if f.adverse and f.id not in slotted]
    if not final.startswith(ai):
        bad.append("최종 글이 AI 부분 채움으로 시작하지 않는다")
    tail = final[len(ai):]
    if want:
        body = tail[1:] if tail.startswith("\n") else None
        if body is None or not body.startswith(ADDENDUM_LEAD):
            bad.append("덧붙임 문단이 없거나 정해진 머리말이 아니다")
        else:
            chips = re.findall(r"\[(F\d+)\]", body)
            if chips != want:
                bad.append(f"덧붙임 칩 {chips} ≠ 빠진 불리한 사실 {want}")
            bad += [f"덧붙임 {fid} 앞 문구가 phrase_full이 아니다" for fid in chips if fid in facts and f"{facts[fid].phrase_full}[{fid}]" not in body]
        if c.get("appended_adverse") != want:
            bad.append(f"appended_adverse {c.get('appended_adverse')} ≠ {want}")
    elif tail or c.get("addendum") or c.get("appended_adverse"):
        bad.append("빠진 불리한 사실이 없는데 덧붙였다")
    missing = [f.id for f in inp.facts if f.adverse and f"[{f.id}]" not in final]
    if missing:
        bad.append(f"최종 글에 불리한 사실 칩 없음 {missing}")
    if "{" in final or "}" in final:
        bad.append("최종 글에 중괄호")
    return bad + e7b.attribution_problems(final, inp, person_ids)


def inject(res: dict) -> dict:
    from core.kg.justify import verify
    rng = random.Random(SEED)
    mut: dict = {}
    integ = {"checked": 0, "failed": 0, "examples": []}
    for c in res["calls"]:
        if c["method"] != "graphrag":
            continue
        inp = _inp(res, c)
        bad = integrity_problems(c, inp, e7b._person_ids(c["bundle"]) | set(inp.team))
        integ["checked"] += 1
        if bad:
            integ["failed"] += 1
            if len(integ["examples"]) < 5:
                integ["examples"].append({"call": f"{c['bundle']}/{c['project_id']}#{c['run']}", "problems": bad[:5]})
        for mname, (state, mtext) in e7b.mutations(c["llm_text"], inp, rng).items():
            m = mut.setdefault(mname, {"made": 0, "detected": 0, "no_target": 0, "noop": 0, "coincidentally_true": 0, "missed": [], "rules": {}})
            if state != "made":
                m[state] += 1
                continue
            m["made"] += 1
            v = verify(mtext, inp)
            if not v["ok"]:
                m["detected"] += 1
                for r in {x["rule"] for x in v["violations"]}:
                    m["rules"][r] = m["rules"].get(r, 0) + 1
            elif len(m["missed"]) < 5:
                m["missed"].append({"call": f"{c['bundle']}/{c['project_id']}#{c['run']}", "text": mtext[:600]})
    return {"render_integrity": integ, **dict(sorted(mut.items()))}


# ---------------------------------------------------------------- 요약
def _verdict(rate, complete) -> str:
    return "미완(비용 상한 중단)" if not complete else "달성" if rate >= G2_TARGET else "조건부" if rate >= G2_FLOOR else "미달"


def _set_summary(res: dict, part: str) -> dict:
    names = [n for n, b in res["bundles"].items() if b["set"] == part]
    calls = [c for c in res["calls"] if c["set"] == part]
    sub = {**res, "calls": calls, "bundles": {n: res["bundles"][n] for n in names}, "mutations": {},
           "planned_calls": res.get("planned_by_set", {}).get(part)}
    s = e7b.summarize(sub)
    complete = not res.get("stopped_by_cost_cap") and len(calls) == sub["planned_calls"]
    acc = [c for c in calls if c["method"] == "graphrag"]
    adv = [c for c in acc if c.get("adverse") and c["adverse"]["available"]]
    add = [c for c in acc if c.get("appended_adverse")]
    s.pop("G1", None)
    s["G2"] = {"rate": s["acceptance_rate"], "verdict": _verdict(s["acceptance_rate"], complete), "complete": complete,
               "accepted": s["accepted"], "planned": sub["planned_calls"]}
    s["addendum"] = {"texts_with_adverse": len(adv), "texts_appended": len(add),
                     "appended_share": round(len(add) / len(adv), 3) if adv else None,
                     "appended_facts": sum(len(c["appended_adverse"]) for c in add),
                     "adverse_available": sum(c["adverse"]["available"] for c in adv),
                     "ai_cited_rate": round(sum(c["adverse"]["cited"] for c in adv) / sum(c["adverse"]["available"] for c in adv), 3) if adv else None,
                     "final_chars_p50": statistics.median([c["final_chars"] for c in acc]) if acc else None}
    return s


def summarize(res: dict) -> dict:
    accepted = sum(1 for c in res["calls"] if c["method"] == "graphrag")
    fams = {k: m for k, m in res["mutations"].items() if k != "render_integrity"}
    floor = max(FAMILY_FLOOR_MIN, FAMILY_FLOOR_SHARE * accepted)
    per = {f: next((m for k, m in fams.items() if k.split("_")[0] == f), {"made": 0, "detected": 0}) for f in FAMILIES}
    short = [f for f, m in per.items() if m["made"] < floor]
    groups = {g: {"made": sum(m["made"] for f, m in per.items() if f.startswith(g)),
                  "detected": sum(m["detected"] for f, m in per.items() if f.startswith(g))} for g in ("M", "H")}
    made, det = sum(m["made"] for m in per.values()), sum(m["detected"] for m in per.values())
    integ = res["mutations"].get("render_integrity", {"checked": 0, "failed": 0})
    detection_ok = made > 0 and det / made >= G1_DETECTION and all(g["made"] and g["detected"] == g["made"] for g in groups.values())
    integrity_ok = integ["checked"] == accepted and integ["checked"] >= INTEGRITY_MIN and integ["failed"] == 0
    g1 = {"made": made, "detected": det, "rate": round(det / made, 4) if made else None, "by_group": groups,
          "family_floor": floor, "families_below_floor": short, "per_family_made": {f: m["made"] for f, m in per.items()},
          "render_integrity": integ, "accepted_texts": accepted,
          "detection_ok": detection_ok, "coverage_ok": not short, "integrity_ok": integrity_ok,
          "pass": detection_ok and not short and integrity_ok}
    base = {}
    if E7B_JSON.exists():
        old = json.loads(gzip.decompress(E7B_JSON.read_bytes()))
        base = {n: {"rate": b["rate"], "rules": b["rules"], "calls": b["calls"]} for n, b in old["summary"]["by_bundle"].items()}
        base["_total"] = {"rate": old["summary"]["acceptance_rate"], "rules": old["summary"]["rules"], "calls": old["summary"]["total_calls"]}
    return {"G1": g1, "A": _set_summary(res, "A"), "B": _set_summary(res, "B"), "e7b": base,
            "total_calls": len(res["calls"]), "planned_calls": res.get("planned_calls")}


# ---------------------------------------------------------------- 보고서
def _f(x, fmt="{:.1f}"):
    return "—" if x is None else fmt.format(x)


def render(res: dict) -> None:
    e = html.escape
    s, g1 = res["summary"], res["summary"]["G1"]
    base = s.get("e7b", {})

    def bundle_rows(part):
        out = ""
        for n, b in s[part]["by_bundle"].items():
            old = base.get(n)
            out += (f"<tr><td>{e(n)}</td><td>{e(res['bundles'][n]['plan'])}</td><td class='num'>{b['calls']}</td>"
                    f"<td class='num'>{_f(old['rate'], '{:.1%}') if old else '—'}</td><td class='num'><b>{_f(b['rate'], '{:.1%}')}</b></td>"
                    f"<td class='num'>{e(b['projects_all_runs_accepted'])}</td>"
                    f"<td>{e(', '.join(f'{k} {v}' for k, v in (old or {}).get('rules', {}).items()) or '—')}</td>"
                    f"<td>{e(', '.join(f'{k} {v}' for k, v in b['rules'].items()) or '—')}</td>"
                    f"<td class='num'>{_f(b['latency_p50'])} / {_f(b['latency_p95'])}</td><td class='num'>${b['cost_usd']:.3f}</td>"
                    f"<td class='num'>{_f(b['chars_p50'], '{:,.0f}')} / {_f(b['template_chars_p50'], '{:,.0f}')}</td></tr>")
        return out

    def bins(part):
        t = s[part]
        return "".join(f"<tr><td>{e(k)}</td><td class='num'>{t['by_team_size_n'][k]}</td><td class='num'>{_f(v, '{:.1%}')}</td></tr>"
                       for k, v in t["by_team_size"].items())

    def add_row(part, label):
        a = s[part]["addendum"]
        return (f"<tr><td>{e(label)}</td><td class='num'>{a['texts_with_adverse']}</td><td class='num'>{_f(a['ai_cited_rate'], '{:.1%}')}</td>"
                f"<td class='num'>{a['texts_appended']} ({_f(a['appended_share'], '{:.1%}')})</td><td class='num'>{a['appended_facts']} / {a['adverse_available']}</td>"
                f"<td class='num'>{_f(a['final_chars_p50'], '{:,.0f}')}</td></tr>")
    mrows = "".join(f"<tr><td>{e(k)}</td><td class='num'>{m['made']}</td><td class='num'>{m['detected']}</td>"
                    f"<td class='num'>{m['no_target']} / {m['noop']} / {m['coincidentally_true']}</td>"
                    f"<td>{e(', '.join(f'{r} {c}' for r, c in sorted(m['rules'].items())))}</td><td class='num'>{m['made'] - m['detected']}</td></tr>"
                    for k, m in res["mutations"].items() if k != "render_integrity")
    missed = "".join(f"<li><b>{e(k)}</b> {e(x['call'])}: {e(x['text'][:300])}</li>" for k, m in res["mutations"].items()
                     if k != "render_integrity" for x in m["missed"])
    integ = g1["render_integrity"]
    iex = "".join(f"<li>{e(x['call'])}: {e(' / '.join(x['problems']))}</li>" for x in integ.get("examples", []))
    rej = [c for c in res["calls"] if c["method"] != "graphrag"]
    rej_html = "".join(f"<li><b>{e(c['bundle'])}/{e(c['project_id'])}</b>(팀 {c['team']}명) {e(c['fallback_reason'] or '')}: "
                       + e(" / ".join(f"{v['rule']} {v['detail']}" for v in c["violations"][:2])) + "</li>" for c in rej[:20])
    ex = next((c for c in res["calls"] if c["method"] == "graphrag" and c.get("appended_adverse") and c["set"] == "B"), None) or \
        next((c for c in res["calls"] if c["method"] == "graphrag" and c.get("appended_adverse")), None)
    A, B = s["A"], s["B"]
    old_total = base.get("_total", {})
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>소명 글 재측정</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--box:#edf3f9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#a8a8a8;--acc:#8ab8e0;--line:#3a3a3a;--box:#222b33}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1100px;margin:auto}}h2{{color:var(--acc);border-bottom:2px solid var(--line);padding-bottom:.2em;margin-top:2em}}
table{{border-collapse:collapse;width:100%;font-size:.88em}}th,td{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}}th{{color:var(--muted)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}.sub{{color:var(--muted);font-size:.85em}}.w{{overflow-x:auto}}.box{{background:var(--box);border-radius:10px;padding:10px 16px}}
pre{{white-space:pre-wrap;background:var(--box);padding:10px;border-radius:8px;font-size:.85em}}
</style></head><body><main>
<h1>인사팀 소명 글 재측정 (E7c) — 불리한 사실 덧붙임 + 지시문 개선</h1>
<p class="sub">TeamWeaver · {e(res['when'])} · 커밋 {e(res['commit'])}{' (측정 당시 커밋 안 된 변경 있음)' if res.get('dirty') else ''} · 모델 {e(res['model'])} · 사업마다 {res['runs']}회 ·
호출 {s['total_calls']}/{s.get('planned_calls')}{' · 비용 상한으로 중단' if res.get('stopped_by_cost_cap') else ''} · 비용 ${res['spent_usd']:.3f} · 가상 데이터</p>
<div class="box">
<p><b>G1 실수 잡기·무결성</b>: 주입 오류 {g1['detected']}/{g1['made']}(M {g1['by_group']['M']['detected']}/{g1['by_group']['M']['made']}, H {g1['by_group']['H']['detected']}/{g1['by_group']['H']['made']}) ·
종류별 하한 {g1['family_floor']:.0f}건 {'모두 충족' if g1['coverage_ok'] else '미달 ' + ', '.join(g1['families_below_floor'])} ·
무결성(덧붙임 포함) {integ['checked'] - integ['failed']}/{integ['checked']}(채택 글 {g1['accepted_texts']}) → <b>{'통과' if g1['pass'] else '실패'}</b></p>
<p><b>G2 채택률(대상 A, 조율 집합)</b>: {A['acceptance_rate']:.1%}({A['accepted']}/{A['G2']['planned']}) → <b>{e(A['G2']['verdict'])}</b>
· E7b {_f(old_total.get('rate'), '{:.1%}')}<br>
<b>G2h 채택률(대상 B, 처음 보는 데이터)</b>: {_f(B['acceptance_rate'], '{:.1%}')}({B['accepted']}/{B['G2']['planned']}) → <b>{e(B['G2']['verdict'])}</b> (달성 ≥95%, 조건부 90~95%)</p>
<p class="sub">판정 기준은 측정 전에 고정했다(<code>rehearsal/justify_scale_e7c.py</code> docstring·상수, 측정 전 커밋). 분모는 계획한 호출 전부(AI 호출 오류 포함).
{e(CALIBRATION)} 대상 B는 새 시드 {HOLDOUT_SEED}로 만든 묶음이고 리뷰 글은 규칙 판정이다. 대상 A의 미리 계산 배치 파일 해시는 결과 JSON(precomputed_sha256)에 있다.</p></div>
<h2>1. 묶음별 결과 (E7b 대비)</h2>
<h3>대상 A — 조율 집합(E7b와 같은 사업·배치)</h3>
<div class="w"><table><tr><th>묶음</th><th>배치</th><th>호출</th><th>E7b 채택률</th><th>E7c 채택률</th><th>3회 모두 채택</th><th>E7b 탈락 규칙</th><th>E7c 탈락 규칙</th><th>지연 p50/p95 초</th><th>비용</th><th>AI 글 길이 p50 / 템플릿</th></tr>{bundle_rows('A')}</table></div>
<h3>대상 B — 보류 집합(새 시드)</h3>
<div class="w"><table><tr><th>묶음</th><th>배치</th><th>호출</th><th>E7b</th><th>E7c 채택률</th><th>3회 모두 채택</th><th>—</th><th>탈락 규칙</th><th>지연 p50/p95 초</th><th>비용</th><th>AI 글 길이 p50 / 템플릿</th></tr>{bundle_rows('B')}</table></div>
<h3>팀 크기별 채택률</h3>
<div class="w"><table><tr><th>대상 A 팀 크기</th><th>호출</th><th>채택률</th></tr>{bins('A')}<tr><th>대상 B 팀 크기</th><th>호출</th><th>채택률</th></tr>{bins('B')}</table></div>
<p class="sub">탈락 이유: 대상 A {e(', '.join(f'{k} {v}' for k, v in A['reasons'].items()))} · 대상 B {e(', '.join(f'{k} {v}' for k, v in B['reasons'].items()))}.
E7b 전체 탈락 규칙: {e(', '.join(f'{k} {v}' for k, v in old_total.get('rules', {}).items()))}.
규칙: S1 없는 자리표시 · S2 문법 밖 연결 말 · S3 팀원 아닌·지어낸 ID · S4 사실과 머리(주어·라벨)의 짝 · S5 사람별 사실이 없는 팀원 · S6 빈 글.</p>
<h3>탈락 예(앞 20건)</h3><ul>{rej_html or '<li>없음</li>'}</ul>
<h2>2. 불리한 사실 — AI 인용과 서버 덧붙임</h2>
<div class="w"><table><tr><th>대상</th><th>불리한 사실이 있는 채택 글</th><th>AI 자체 인용률</th><th>덧붙인 글(비율)</th><th>덧붙인 사실 / 전체 불리한 사실</th><th>최종 글 길이 p50</th></tr>
{add_row('A', '대상 A')}{add_row('B', '대상 B')}</table></div>
<p class="sub">불리한 사실 = 요구 기술 부족 · 사람별 기술 미달 · 규칙 위반·빈자리 · 전체 점수가 오르는 다른 후보 · 부정 동료 평가(Fact.adverse).
AI가 빠뜨린 것은 서버가 "다만 다음 사항도 함께 확인이 필요합니다: …" 한 문단으로 덧붙인다(사용자 결정 2026-10-10). 최종 글에 모든 불리한 사실이 들어갔는지는 무결성 검사(G1)가 확인한다.</p>
<h2>3. 오류 주입(검사기가 틀린 글을 잡는가)</h2>
<div class="w"><table><tr><th>변형</th><th>만든 수</th><th>잡음</th><th>못 만듦: 대상 없음 / 변화 없음 / 우연히 참</th><th>잡은 규칙</th><th>놓침</th></tr>{mrows}</table></div>
<p class="sub">E7b와 같은 변형(M1~M9 규칙별 구조 변형, H1~H7 독립 리뷰 예문). 종류마다 하한 max({FAMILY_FLOOR_MIN}, 채택 글 × {FAMILY_FLOOR_SHARE})건(Codex 사후 리뷰 지적으로 추가).</p>
<h3>놓친 예</h3><ul>{missed or '<li>없음</li>'}</ul>
<h3>무결성 실패 예</h3><ul>{iex or '<li>없음</li>'}</ul>
<h2>4. AI 연결 말 감사(채택된 글의 조각 전부, 빈도순 — ID는 &lt;ID&gt;)</h2>
<p class="sub">서로 다른 조각 {res.get('free_text_audit', {}).get('distinct', 0)}개.</p>
<div class="w"><table><tr><th>조각</th><th>횟수</th></tr>{''.join(f"<tr><td>{e(t)}</td><td class='num'>{n}</td></tr>" for t, n in res.get('free_text_audit', {}).get('all', []))}</table></div>
<h2>5. 덧붙임이 있는 채택 글 예 — AI 원문과 최종 글</h2>
<pre>{e((ex or {}).get('llm_text') or '')}</pre>
<pre>{e((ex or {}).get('rendered') or '')}</pre>
<p class="sub">알려진 한계: 대상 A는 지시문을 고칠 때 본 집합이다. 대상 B는 같은 생성기의 다른 시드라 "다른 회사의 실데이터"보다는 가깝다. 사내 GLM은 재지 않았다.</p>
</main></body></html>"""
    OUT_HTML.write_text(page, "utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args()
    res = json.loads(gzip.decompress(OUT_JSON.read_bytes())) if a.render_only else run()
    render(res)
    print(json.dumps(res["summary"]["G1"], ensure_ascii=False))
    print(json.dumps({p: res["summary"][p]["G2"] for p in ("A", "B")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
