"""E5. 교체 설명(브리핑) 비교: 외부 LLM(gpt-6-luna) vs 사내 LLM(GLM 5.3·Z.ai, 추론 low) -- 같은 교체 100건.
사내 서버로는 측정할 수 없어 Z.ai 공식 API의 GLM 5.3을 사내 LLM으로 가정한다(사용자 결정 2026-10-07).

사용자 지시(2026-10-07): LLM을 쓰는 모든 곳은 외부 AI와 사내 GLM 5.3을 항상 함께 비교하되 전부 low로, 유의미한 표본 크기만.
서비스 What-if(`api/routes/whatif.py`)와 같은 재료로 claude-a의 `api.rag.briefing.generate_briefing`을 그대로 부른다(import만):
근거 문맥 `swap_context`, 근거 색인(가상 데이터라 원문 인용), 평가기(`core/evaluate/plan_eval`)의 점수 변화·새 위반.

교체 사례: 운영 중 시연 묶음(`demo/org-n100-operating`)의 현재 배치에서 seed 7로 100건 -- 빼는 사람 = 배치된 사람, 넣는 사람은
절반이 대기 인력(빈 사람), 절반이 그 사업 밖 아무나(무작위만이면 93%가 위반이라 위반 없는 교체를 볼 수 없었다). 결론 분포·일치율은
이 혼합 비율에 좌우된다 -- 위반 여부로 나눠 보고한다.

지표(다른 LLM이 채점하지 않는다 -- 비용·채점자 편향): 성공률(형식·가드 통과)과 실패 사유, 새 위반이 생긴 교체에서 '보류' 준수율
(가드가 어기면 버린다 → 실패 사유 recommends_infeasible로 센다), 검증된 인용 수, 결론(권고/조건부/보류) 분포와 두
모델 결론 일치율, 지연·토큰·비용. 짝지은 성공 차이는 McNemar 정확검정.

표본 크기: 100건이면 성공률·준수율의 95% 구간 반폭 ≤ ±9.8%p -- 건당 비용이 커서(문맥이 길다) 차이를 가늠할 최소 크기로 잡았다.

실행: `uv run --group benchmark python -m experiments.jev.e5_briefing` (OPENAI_API_KEY·ZAI_API_KEY). 한 건마다 중간 기록,
있으면 재사용(이어서는 E5_RESUME=1), 잔액 부족이면 즉시 멈춘다(E4와 같은 장치). 보고서: outputs/briefing-llm-comparison.html."""
from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from core.config import REPO_ROOT, load_env, load_pricing
from experiments.jev.e4_judges import (FatalApiError, GLM_MODEL, GLM_PRICE, GLM_URL, _atomic_json, _is_fatal,
                                       _mcnemar_exact, _wilson)

RESULTS = Path(__file__).parent / "results"
REPORT = REPO_ROOT / "outputs" / "briefing-llm-comparison.html"
BUNDLE = REPO_ROOT / "demo" / "org-n100-operating"
N_CASES = 100
SEED = 7
WORKERS = 8
MODELS = ("llm", "glm_low")
LABEL = {"llm": "외부 LLM (gpt-6-luna)", "glm_low": "사내 LLM low (GLM 5.3·Z.ai)"}


# --- 사례 -----------------------------------------------------------------------------

def build_cases():
    """서비스 What-if와 같은 재료로 교체 사례를 만든다. 반환: (활성 데이터셋, 사례 목록)."""
    from api.datasets import build_active, bundle_version
    from api.rag.context import swap_context
    from api.settings import PlacementSettings
    from core.evaluate.plan_eval import evaluate_plan
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine

    bundle, report = load_bundle(BUNDLE)
    ds, parsed = to_dataset(bundle, report)
    # 리뷰 글 판정(LLM)은 이 실험의 대상이 아니라 끈다 -- 근거 문맥·색인은 같다
    active = build_active(ds, parsed, dataset_id="e5", version=bundle_version(BUNDLE), source="demo-bundle",
                          synthetic=True, judge=False, manifest=bundle.manifest)
    g = active.graph
    params = PlacementSettings().to_milp_params(n_people=len(g.people))
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in active.current]
    before = evaluate_plan(g, S, C, params, entries)
    rng = random.Random(SEED)
    bench = list(active.scenario.get("bench", []))
    cases, seen = [], set()
    while len(cases) < N_CASES:
        out = rng.choice(entries)
        team = {e.person_id for e in entries if e.project_id == out.project_id}
        # 절반은 대기 인력(빈 사람)을 넣는 교체 -- 무작위로만 뽑으면 이미 꽉 찬 사람이 들어가 93%가 위반이 생겨,
        # 위반 없는 교체(권고·조건부 결론)를 거의 볼 수 없었다(2026-10-07 측정 전 점검).
        pool = [b for b in bench if b not in team] if len(cases) < N_CASES // 2 else []
        cand = pool or [p.id for p in g.people if p.id not in team]
        inn = rng.choice(cand)
        key = (out.person_id, inn, out.project_id)
        if key in seen:
            continue
        seen.add(key)
        after_entries = [e for e in entries if not (e.person_id == out.person_id and e.project_id == out.project_id)]
        after_entries.append(AssignEntry(person_id=inn, project_id=out.project_id, alloc=out.alloc))
        after = evaluate_plan(g, S, C, params, after_entries)
        old = {(v.code, v.location) for v in before.violations}
        new_v = [v.message for v in after.violations if (v.code, v.location) not in old]
        sc = {k: getattr(after.objective, k) - getattr(before.objective, k)
              for k in ("skill", "synergy", "overfamiliarity", "unfilled")}
        sc["total"] = after.objective.total - before.objective.total
        sc["feasible"] = not after.violations
        if new_v:
            sc["new_violations"] = new_v[:5]
        ctx = swap_context(active.sqlite_conn, out.person_id, inn, evidence=active.evidence, project_id=out.project_id)
        case = {"out": out.person_id, "in": inn, "project": out.project_id, "ctx": ctx, "score_change": sc,
                "kind": "bench" if pool else "random"}
        # 사례 지문: 문맥·점수 변화까지 -- 데이터·설정이 바뀌면 다른 사례의 답이 섞이지 않게 기록과 대조한다(리뷰 S2)
        case["key"] = hashlib.sha256(json.dumps([case["out"], case["in"], case["project"], ctx, sc], ensure_ascii=False,
                                                sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        cases.append(case)
    return active, cases


# --- 호출 -----------------------------------------------------------------------------

class _Capture:
    """generate_briefing이 부르는 client.chat.completions.create의 응답 usage를 붙잡는다(함수는 usage를 돌려주지 않는다)."""

    def __init__(self, client):
        self._client, self.usage = client, None
        self.chat = self
        self.completions = self

    def create(self, **kw):
        resp = self._client.chat.completions.create(**kw)
        self.usage = resp.usage
        return resp


_NEGATED = re.compile(r"비권고|권고\s*안\s*|권고(하지|할 수 없|하기 어렵|하지 않|드리지 않|드리기 어렵)")


def _first_sentence(rationale: str) -> str:
    return re.split(r"(?<=[.!?。])\s", rationale.strip(), maxsplit=1)[0]


def _conclusion(rationale: str) -> str:
    """첫 문장의 결론. '권고하지 않는다'·'권고하기 어렵다'는 권고가 아니다(리뷰 S3) -- 보류로 본다."""
    first = _first_sentence(rationale)
    if "보류" in first or _NEGATED.search(first):
        return "보류"
    for word, tag in (("조건부", "조건부"), ("권고", "권고")):
        if word in first:
            return tag
    return "불명"


def one(model_key: str, client, model: str, effort: str | None, case: dict, evidence) -> dict:
    from api.rag.briefing import generate_briefing
    cap = _Capture(client)
    t = time.monotonic()
    row = {"ok": False}
    for attempt in range(4):
        try:
            b = generate_briefing(cap, model, case["ctx"], case["out"], case["in"], evidence=evidence,
                                  score_change=case["score_change"], reasoning_effort=effort)
            kinds = [e["kind"] for e in b.get("evidence", [])]
            row = {"ok": True, "conclusion": _conclusion(b["rationale"]), "first": _first_sentence(b["rationale"])[:300],
                   "citations": len(kinds),
                   "quotes": kinds.count("quote"), "chars": len(b["rationale"]) + sum(map(len, b["risks"])) +
                   sum(map(len, b["alternatives"]))}
            break
        except ValueError as exc:                    # 형식·가드 실패(서비스는 규칙 기반 설명으로 대체한다)
            cause = exc.__cause__
            row = {"ok": False, "code": getattr(cause or exc, "code", None) or type(cause or exc).__name__}
            break
        except Exception as exc:                     # noqa: BLE001 -- API 오류
            if _is_fatal(exc):
                raise FatalApiError(str(exc)[:200]) from None
            if attempt == 3:
                row = {"ok": False, "code": f"api:{type(exc).__name__}"}
                break
            time.sleep(min(2 ** attempt, 8))
    row["key"] = case["key"]
    u = cap.usage
    ptd = getattr(u, "prompt_tokens_details", None) if u else None
    ctd = getattr(u, "completion_tokens_details", None) if u else None
    row.update(lat=time.monotonic() - t, in_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
               out_tokens=getattr(u, "completion_tokens", 0) if u else 0,
               cached=int(getattr(ptd, "cached_tokens", 0) or 0) if ptd else 0,
               reasoning=int(getattr(ctd, "reasoning_tokens", 0) or 0) if ctd else 0)
    return row


def _model_and_effort(model_key: str) -> tuple[str, str | None, str]:
    if model_key == "llm":
        # 서비스 설정 그대로: 브리핑 모델과 pricing.json의 추론 강도(gpt-6-luna는 low) -- 기록에 실제 값을 남긴다(리뷰 M2)
        model = load_pricing()["briefing_model"]
        # 실제로 부를 주소(OpenAI SDK는 OPENAI_BASE_URL을 스스로 읽는다) -- 프록시로 바뀌면 지문도 바뀐다(Codex 리뷰 P2)
        url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        return model, load_pricing().get("models", {}).get(model, {}).get("reasoning_effort"), url
    return GLM_MODEL, "low", GLM_URL


def run_config(model_key: str, cases: list[dict]) -> dict:
    """사례·설정 지문: 교체 사례 키 목록 + 모델·주소·추론 강도·브리핑 지시문 해시. 호출 전에 대조한다 --
    다른 사례나 다른 설정의 기록을 이어 붙이거나 재사용하지 않게(Codex 사후 리뷰 MUST 2026-10-11)."""
    from api.rag import briefing
    model, effort, url = _model_and_effort(model_key)
    prompt = "".join(str(getattr(briefing, n, "")) for n in ("_SYSTEM", "_SYSTEM_SOURCED", "_SYSTEM_HIDDEN"))
    return {"model": model, "effort": effort, "url": url,
            "prompt_sha": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "cases_sha": hashlib.sha256(json.dumps([c["key"] for c in cases], ensure_ascii=False).encode()).hexdigest()[:16]}


def _check_config(found: dict | None, cfg: dict, path: Path) -> None:
    if found is not None and found != cfg:
        raise ValueError(f"{path.name}은 지금과 다른 사례·설정으로 잰 기록이다(기록 {found} / 지금 {cfg}). 섞지 않도록 멈춘다.")


def run(model_key: str, cases: list[dict], evidence, resume: bool | None = None) -> dict:
    resume = os.environ.get("E5_RESUME") == "1" if resume is None else resume
    path = RESULTS / f"e5_{model_key}.json"
    load_env()
    cfg = run_config(model_key, cases)
    if path.exists():
        rec = json.loads(path.read_text("utf-8"))
        _check_config(rec.get("config"), cfg, path)
        return rec if rec.get("config") is not None else {**rec, "unverified_inputs": True}
    partial_path = path.with_suffix(".partial.json")
    partial = (json.loads(partial_path.read_text("utf-8")) if partial_path.exists()
               else {"rows": {}, "wall_s": 0.0, "config": cfg})
    _check_config(partial.get("config"), cfg, partial_path)
    legacy = partial_path.exists() and partial.get("config") is None
    if partial_path.exists() and not resume:
        return {**partial, "partial": True, **({"unverified_inputs": True} if legacy else {})}
    if legacy and os.environ.get("E5_ACCEPT_LEGACY") != "1":
        raise ValueError(f"{partial_path.name}에는 사례·설정 기록이 없어 이어 잴 수 없다 -- 기록을 옮기고 새로 재거나, "
                         "같은 조건임을 확인했으면 E5_ACCEPT_LEGACY=1로 이어 잰다.")
    if legacy:
        partial["accepted_legacy"] = True
    partial["config"] = cfg
    from openai import OpenAI
    model, effort, _url = _model_and_effort(model_key)
    client = (OpenAI(max_retries=0) if model_key == "llm"
              else OpenAI(base_url=GLM_URL, api_key=os.environ["ZAI_API_KEY"], timeout=300, max_retries=0))
    rows: dict = partial["rows"]
    lock, stop = threading.Lock(), threading.Event()
    t0 = time.monotonic()

    def work(ix):
        if stop.is_set():
            return
        try:
            row = one(model_key, client, model, effort, cases[ix], evidence)
        except FatalApiError:
            stop.set()
            raise
        with lock:
            rows[str(ix)] = row
            _atomic_json(partial_path, partial)

    stop_reason = None
    pool = ThreadPoolExecutor(WORKERS)
    try:
        for f in as_completed([pool.submit(work, i) for i in range(len(cases)) if str(i) not in rows]):
            f.result()
    except FatalApiError as exc:
        stop_reason = str(exc)[:200]
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        partial["wall_s"] += time.monotonic() - t0
        partial["stop_reason"] = stop_reason
        _atomic_json(partial_path, partial)
    rec = {**partial, "model": model, "effort": effort, "workers": WORKERS,
           "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if stop_reason is None:
        _atomic_json(path, rec)
        partial_path.unlink(missing_ok=True)
        return rec
    return {**rec, "partial": True}


# --- 요약·보고서 -----------------------------------------------------------------------

def _cost(model_key: str, rows: list[dict], model: str | None = None) -> float:
    if model_key == "llm":
        # 기록된 모델의 단가(지금 설정 모델이 아니라) -- Codex 사후 리뷰 SHOULD
        models = load_pricing()["models"]
        price = models.get(model or "", models[load_pricing()["briefing_model"]])
        return sum(r["in_tokens"] * price["input_per_1m"] + r["out_tokens"] * price["output_per_1m"] for r in rows) / 1e6
    return sum((r["in_tokens"] - r["cached"]) * GLM_PRICE["input"] + r["cached"] * GLM_PRICE["cached"]
               + r["out_tokens"] * GLM_PRICE["output"] for r in rows) / 1e6


# 보류 가드 이후 단계(인용 검증)의 실패 코드 -- api/rag/briefing._verified_citations·_check_inline_quotes
# (tests/test_e4_judges.py가 briefing.py의 BriefingRejected 코드와 대조한다 -- 어긋나면 시험이 실패한다)
_POST_GUARD = {"bad_citation_shape", "hidden_citation", "hidden_quote", "inline_quote_not_verbatim", "quote_not_verbatim",
               "unbalanced_quote", "uncited_marker", "unknown_marker", "unknown_source"}


def _held(row: dict) -> bool | None:
    """새 위반 교체에서 보류 규칙을 지켰나(형식 실패 등 결론을 모르면 None)."""
    if row.get("code") == "recommends_infeasible":
        return False
    if row["ok"] or row.get("code") in _POST_GUARD:
        return True
    return None


def summarize() -> dict:
    active, cases = build_cases()
    recs = {m: run(m, cases, active.evidence) for m in MODELS}
    for m, r in recs.items():
        for i, row in r["rows"].items():
            if row.get("ok") and row.get("first") is not None:
                row["conclusion"] = _conclusion(row["first"])        # 저장된 첫 문장으로 다시 분류(파서를 고쳐도 API 없이 재집계)
            if row.get("key") != cases[int(i)]["key"]:
                raise ValueError(f"e5_{m}의 {i}번 기록은 지금과 다른 교체 사례의 답이다(데이터·설정 변경?). 섞지 않도록 멈춘다.")
    common = sorted(set.intersection(*(set(int(i) for i in r["rows"]) for r in recs.values())))
    infra = {i for i in common for r in recs.values() if str(r["rows"][str(i)].get("code", "")).startswith("api:")}
    common = [i for i in common if i not in infra]       # 인프라 실패(API 4회 실패)는 모델 비교에서 뺀다(따로 센다)
    need_hold = [i for i in common if cases[i]["score_change"].get("new_violations")]
    out = {"unverified_inputs": sorted(m for m, r in recs.items() if r.get("unverified_inputs")),
           "n": len(common), "n_total": len(cases), "n_new_violation_cases": len(need_hold), "n_infra_excluded": len(infra),
           "models": {}}
    for m, r in recs.items():
        rows = [r["rows"][str(i)] for i in common]
        own = list(r["rows"].values())
        ok = [x for x in rows if x["ok"]]
        codes: dict = {}
        for x in rows:
            if not x["ok"]:
                codes[x["code"]] = codes.get(x["code"], 0) + 1
        # '보류' 준수율: 새 위반이 생긴 교체 중 읽을 수 있는 답(성공 또는 보류 규칙 위반)만 분모로 -- 형식 실패(JSON 깨짐 등)는
        # 결론을 알 수 없으므로 준수·위반 어느 쪽으로도 세지 않는다(형식 실패는 '실패 사유'에 따로 보인다).
        # 가드는 형식 검사 뒤·인용 검사 앞에 돈다 -- 형식 실패는 결론을 알 수 없어 분모에서 빼고, 인용 검사 실패는 이미 보류 규칙을
        # 통과한 답이라 준수로 센다(리뷰 S4).
        readable = [i for i in need_hold if r["rows"][str(i)]["ok"] or r["rows"][str(i)].get("code") in _POST_GUARD
                    or r["rows"][str(i)].get("code") == "recommends_infeasible"]
        hold_ok = sum(1 for i in readable if r["rows"][str(i)].get("code") != "recommends_infeasible")
        concl: dict = {}
        for x in ok:
            concl[x["conclusion"]] = concl.get(x["conclusion"], 0) + 1
        cost = _cost(m, own, (r.get("config") or {}).get("model") or r.get("model"))
        out["models"][m] = {
            "model": r.get("model"), "effort": r.get("effort"), "partial": bool(r.get("partial")),
            "n_run": len(own), "success": len(ok) / len(rows) if rows else None,
            "success_ci": _wilson(len(ok), len(rows)), "fail_codes": codes,
            "hold_compliance": hold_ok / len(readable) if readable else None,
            "hold_ci": _wilson(hold_ok, len(readable)), "hold_n": len(readable),
            "citations_mean": float(np.mean([x["citations"] for x in ok])) if ok else None,
            "quote_share": (sum(x["quotes"] for x in ok) / max(1, sum(x["citations"] for x in ok))) if ok else None,
            "chars_mean": float(np.mean([x["chars"] for x in ok])) if ok else None,
            "conclusions": concl,
            "lat_median_s": float(np.median([x["lat"] for x in own])), "lat_p95_s": float(np.percentile([x["lat"] for x in own], 95)),
            "in_tokens": sum(x["in_tokens"] for x in own), "out_tokens": sum(x["out_tokens"] for x in own),
            "reasoning_tokens": sum(x["reasoning"] for x in own), "cost_usd": cost,
            "per_100_usd": cost / max(len(own), 1) * 100, "wall_s": r.get("wall_s"), "stop_reason": r.get("stop_reason"),
        }
    a = [recs["llm"]["rows"][str(i)]["ok"] for i in common]
    b = [recs["glm_low"]["rows"][str(i)]["ok"] for i in common]
    out["success_only"] = {"llm": sum(x and not y for x, y in zip(a, b)), "glm_low": sum(y and not x for x, y in zip(a, b))}
    out["success_p"] = _mcnemar_exact(out["success_only"]["llm"], out["success_only"]["glm_low"])
    both = [i for i in common if recs["llm"]["rows"][str(i)]["ok"] and recs["glm_low"]["rows"][str(i)]["ok"]]
    same = lambda i: recs["llm"]["rows"][str(i)]["conclusion"] == recs["glm_low"]["rows"][str(i)]["conclusion"]
    forced = [i for i in both if cases[i]["score_change"].get("new_violations")]
    free = [i for i in both if not cases[i]["score_change"].get("new_violations")]
    # 새 위반 교체는 가드 때문에 성공한 답이 모두 '보류'다 -- 그 일치는 강제된 것이라 따로 센다(리뷰 M1)
    out["conclusion_agree_forced"] = (sum(map(same, forced)) / len(forced)) if forced else None
    out["conclusion_agree_free"] = (sum(map(same, free)) / len(free)) if free else None
    out["n_forced"], out["n_free"] = len(forced), len(free)
    out["free_pairs"] = {}
    for i in free:
        k = f"{recs['llm']['rows'][str(i)]['conclusion']}→{recs['glm_low']['rows'][str(i)]['conclusion']}"
        out["free_pairs"][k] = out["free_pairs"].get(k, 0) + 1
    out["free_conclusions"] = {m: {} for m in MODELS}
    for m in MODELS:
        for i in free:
            c = recs[m]["rows"][str(i)]["conclusion"]
            out["free_conclusions"][m][c] = out["free_conclusions"][m].get(c, 0) + 1
    out["n_both_ok"] = len(both)
    # 보류 준수 짝지은 비교(두 모델 모두 결론을 알 수 있는 새 위반 교체)
    hp = [(_held(recs["llm"]["rows"][str(i)]), _held(recs["glm_low"]["rows"][str(i)])) for i in need_hold]
    hp = [(a_, b_) for a_, b_ in hp if a_ is not None and b_ is not None]
    out["hold_paired"] = {"n": len(hp), "llm_only": sum(a_ and not b_ for a_, b_ in hp),
                          "glm_low_only": sum(b_ and not a_ for a_, b_ in hp)}
    out["hold_paired"]["p"] = _mcnemar_exact(out["hold_paired"]["llm_only"], out["hold_paired"]["glm_low_only"])
    out["history_v1"] = _history_v1()
    (RESULTS / "e5_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    return out


def _history_v1() -> dict | None:
    """1차 측정(같은 100건·같은 low 설정, 2026-10-07) 중 파서와 무관한 가드 기반 결과 -- 실패를 결과에서 빼지 않는다(리뷰 MUST).
    결론 분포·일치율은 파서 결함('권고하지 않는다'를 권고로 셈)으로 폐기했다."""
    out = {}
    for m in MODELS:
        f = RESULTS / "superseded" / f"e5_v1_e5_{m}.json"
        if not f.exists():
            return None
        rows = list(json.loads(f.read_text("utf-8"))["rows"].values())
        codes: dict = {}
        for r in rows:
            if not r["ok"]:
                codes[r["code"]] = codes.get(r["code"], 0) + 1
        out[m] = {"n": len(rows), "success": sum(r["ok"] for r in rows) / len(rows), "fail_codes": codes}
    return out


def _history_html(s: dict) -> str:
    h = s.get("history_v1")
    if not h:
        return ""
    e = html.escape
    codes = lambda m: ", ".join(f"{k} {v}" for k, v in sorted(m["fail_codes"].items())) or "없음"
    return (f'<div class="box warn"><b>1차 측정(같은 100건·같은 low 설정, 기록 <code>results/superseded/</code>)</b><ul>'
            f'<li>{e(LABEL["llm"])}: 성공 {_pct(h["llm"]["success"])} — 실패 {codes(h["llm"])}</li>'
            f'<li>{e(LABEL["glm_low"])}: 성공 {_pct(h["glm_low"]["success"])} — 실패 {codes(h["glm_low"])}</li></ul>'
            f'같은 사례·같은 설정이라도 실행마다 실패가 ±2건 정도 달라진다(외부의 보류 규칙 위반 <code>recommends_infeasible</code>, '
            f'사내의 형식 실패). 서비스는 이런 답을 버리고 규칙 기반 설명으로 대신 보인다. 1차의 결론 분포·일치율은 결론 파서가 '
            f'"권고하지 않는다"를 권고로 세는 결함이 있어 폐기하고, 결론 첫 문장을 저장하도록 고쳐 다시 쟀다(위 표).</div>')


def _pct(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def _ci(c):
    return "" if not c else f" ({c[0] * 100:.0f}~{c[1] * 100:.0f}%)"


def render(s: dict) -> str:
    e = html.escape
    L, G = s["models"]["llm"], s["models"]["glm_low"]
    verdict = ("차이가 통계적으로 확인된다" if s["success_p"] < 0.05 else "차이는 이 표본에서 통계적으로 확정되지 않는다")

    def row(label, f):
        return f"<tr><td>{e(label)}</td><td>{f(L)}</td><td>{f(G)}</td></tr>"
    codes = lambda m: ", ".join(f"{k} {v}" for k, v in sorted(m["fail_codes"].items())) or "없음"
    concl = lambda m: ", ".join(f"{k} {v}" for k, v in sorted(m["conclusions"].items())) or "–"
    partial = "".join(f"<li>{e(LABEL[k])}: {m['n_run']}/{s['n_total']}건만(중단: {e((m['stop_reason'] or '')[:80])})</li>"
                      for k, m in s["models"].items() if m["partial"])
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>교체 설명 LLM 비교</title>
<style>
body{{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;background:#fafaf7;color:#1f2328;margin:0;line-height:1.7}}
main{{max-width:900px;margin:0 auto;padding:32px 16px 64px}} h1{{font-size:1.6rem}} h2{{font-size:1.2rem;margin-top:2rem;border-left:4px solid #2563eb;padding-left:.6rem}}
table{{border-collapse:collapse;width:100%;font-size:.93rem;margin:10px 0;background:#fff}} td,th{{border:1px solid #e5e7eb;padding:6px 8px;text-align:left}}
th{{background:#f3f4f6}} .box{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px;margin:12px 0}}
.warn{{background:#fffbeb;border-color:#fcd34d}} .muted{{color:#6b7280;font-size:.88rem}}
</style></head><body><main>
<h1>교체 설명(AI 브리핑) 비교: 외부 LLM vs 사내 LLM(GLM 5.3 low)</h1>
<p class="muted">실험 E5 · 코드 <code>experiments/jev/e5_briefing.py</code> · 원시 결과 <code>experiments/jev/results/e5_*.json</code> ·
모든 데이터는 가상(합성)이며 사업 효과는 NOT_CALIBRATED · 사내 LLM = Z.ai 공식 API의 GLM 5.3으로 가정(사내 서버 측정 불가)</p>

{('<div class="box warn"><b>입력 동일성 미입증</b>: ' + e(', '.join(s['unverified_inputs'])) + ' 기록은 사례·설정 지문(2026-10-11 도입) 이전에 쟀다 — 사례 키는 행마다 대조했지만, 브리핑 지시문이 지금 코드와 같았는지는 기록으로 입증할 수 없다(모델·추론 강도는 기록에 있는 값을 표에 그대로 보인다).</div>') if s.get('unverified_inputs') else ''}
<h2>1. 무엇을 비교했나</h2>
<p>What-if 화면에서 "A 대신 B를 넣으면?"을 고르면 AI가 결론(권고/조건부/보류)·위험·대안을 쓴다(<code>api/rag/briefing.py</code>).
같은 교체 {s['n']}건을 서비스와 똑같은 재료(근거 문맥·원문 인용 색인·평가기 점수 변화·새 위반)로 두 모델에 맡겼다.
외부는 서비스 설정 그대로({e(L['model'] or '')}, 추론 <b>{e(str(L.get('effort') or '기본'))}</b>), 사내는 Z.ai 공식 API의
{e(G['model'] or '')} 모델(추론 <b>low</b>)을 사내 LLM으로 가정했다(사내 서버로는 측정할 수 없다 — 사용자 결정) — 양쪽 모두 low.</p>

<h2>2. 방법과 표본 크기</h2>
<ul>
<li>사례: 운영 중 시연 묶음(100명)의 현재 배치에서 seed {SEED}로 {s['n_total']}건(빼는 사람 = 배치된 사람). 넣는 사람은 절반이
대기 인력(빈 사람), 절반이 그 사업 밖 아무나 — 무작위로만 뽑으면 이미 꽉 찬 사람이 들어가 대부분 위반이 생긴다.
그중 <b>새 위반이 생기는 교체 {s['n_new_violation_cases']}건</b>은 규칙상 결론이 '보류'여야 한다.</li>
<li>표본 {s['n_total']}건 → 성공률 95% 구간 반폭 ≤ ±9.8%p(준수율은 분모가 70건 안팎이라 최대 ±12%p). 건당 문맥이 길어 비용이
크므로 차이를 가늠할 최소 크기로 잡았다. 인프라 실패(API 4회 실패) {s['n_infra_excluded']}건은 비교에서 뺐다.</li>
<li>채점은 다른 AI에게 맡기지 않는다(비용·채점자 편향). 서비스의 검증기가 판정하는 것만 센다: 형식·가드 통과, 인용 원문 대조.</li>
<li>짝지은 성공 차이는 McNemar 정확검정. 비율 구간은 Wilson 95%.</li>
</ul>
{f'<div class="box warn"><b>부분 결과</b><ul>{partial}</ul>표는 두 모델이 모두 처리한 같은 {s["n"]}건 기준.</div>' if partial else ""}

<h2>3. 결과</h2>
<table><tr><th>지표</th><th>{e(LABEL['llm'])}</th><th>{e(LABEL['glm_low'])}</th></tr>
{row("성공률(형식·가드 통과 — 실패하면 서비스는 규칙 기반 설명으로 대체)", lambda m: _pct(m['success']) + _ci(m['success_ci']))}
{row("실패 사유", codes)}
{row(f"새 위반이 생긴 교체({s['n_new_violation_cases']}건)에서 '보류' 결론을 지킨 비율(읽을 수 있는 답 기준 — 지키지 않으면 서비스 가드가 버린다)", lambda m: _pct(m['hold_compliance']) + _ci(m['hold_ci']) + f" · {m['hold_n']}건")}
{row("성공한 설명의 검증된 인용 수(평균)", lambda m: '–' if m['citations_mean'] is None else f"{m['citations_mean']:.1f}개")}
{row("설명 길이(글자, 평균)", lambda m: '–' if m['chars_mean'] is None else f"{m['chars_mean']:.0f}")}
{row("결론 분포(성공한 설명)", concl)}
</table>
<p><b>성공 여부 {verdict}</b>: 한쪽만 성공한 건 외부 {s['success_only']['llm']}건 · 사내 {s['success_only']['glm_low']}건(McNemar p≈{s['success_p']:.2f}).
'보류' 준수도 짝지어 보면(둘 다 결론을 알 수 있는 {s['hold_paired']['n']}건) 한쪽만 지킨 건 {s['hold_paired']['llm_only']}건 대
{s['hold_paired']['glm_low_only']}건(p≈{s['hold_paired']['p']:.2f}).</p>
<p><b>결론 일치는 둘로 나눠 봐야 한다.</b> 새 위반이 생긴 교체({s['n_forced']}건)는 가드 때문에 성공한 답이 모두 '보류'라 일치
{_pct(s['conclusion_agree_forced'])}가 강제된 값이다. <b>위반이 없어 자유롭게 판단한 교체 {s['n_free']}건의 일치율은
{_pct(s['conclusion_agree_free'])}</b>(표본이 작다 — 95% 구간 {_ci(_wilson(round((s['conclusion_agree_free'] or 0) * s['n_free']), s['n_free']))}).
자유 판단의 결론 분포: 외부 {", ".join(f"{k} {v}" for k, v in sorted(s['free_conclusions']['llm'].items()))} ·
사내 {", ".join(f"{k} {v}" for k, v in sorted(s['free_conclusions']['glm_low'].items()))}
(갈린 경우 외부→사내: {", ".join(f"{k} {v}" for k, v in sorted(s['free_pairs'].items()) if k.split('→')[0] != k.split('→')[1]) or "없음"}).</p>

{_history_html(s)}
<h2>4. 비용과 소요 시간</h2>
<table><tr><th>모델</th><th>건당 지연 중앙값 / 95%</th><th>입력 / 출력 토큰(추론)</th><th>비용</th><th>100건당</th></tr>
{"".join(f"<tr><td>{e(LABEL[k])} ({m['n_run']}건)</td><td>{m['lat_median_s']:.1f}초 / {m['lat_p95_s']:.1f}초</td><td>{m['in_tokens']:,} / {m['out_tokens']:,} ({m['reasoning_tokens']:,})</td><td>${m['cost_usd']:.4f}</td><td>${m['per_100_usd']:.4f}</td></tr>" for k, m in s['models'].items())}
</table>
<p class="muted">단가(2026-10): gpt-6-luna 입력 $0.10·출력 $0.50, GLM 5.3(Z.ai) 입력 $1.40·캐시 $0.26·출력 $4.40 / 100만 토큰(추론 토큰은 출력).
Z.ai를 사내 LLM으로 가정했으므로 사내 시간·비용 = Z.ai 기준이다(실제 온프렘이면 GPU 시간이 비용이 된다).</p>

<h2>5. 한계</h2>
<div class="box warn"><ul>
<li>설명의 <b>내용 품질</b>(설득력·정확한 판단)은 채점하지 않았다. 서비스가 기계적으로 검증하는 것(형식·보류 규칙·인용 원문 대조)만 비교했다.</li>
<li>사례는 절반이 대기 인력 투입·절반이 무작위 교체라 실제 사용자가 고르는 교체와 위반 비율이 다를 수 있다. 결론 분포·일치율은
이 혼합 비율에 좌우된다(두 모델 비교 자체는 같은 사례로 짝지어 공정하다).</li>
<li>근거 인용은 원문 공개 모드(가상 데이터)라 검증된 인용은 모두 직접 인용이다 — 인용 수만 비교했다.</li>
<li>결론은 첫 문장에서 '보류'·'권고하지 않/비권고'(→보류)·'조건부'·'권고'를 찾아 분류한다. "조건부 판단으로 … 권고하기 어렵다"처럼
모호한 문장이 있어 몇 건은 해석이 갈릴 수 있다(기록에 첫 문장을 남겨 다시 확인할 수 있다).</li>
<li>지연은 재시도 대기를 포함한 건당 시간이다(E4는 시도별 시간 — 정의가 다르다).</li>
<li>외부 모델은 서비스 설정 그대로(추론 강도 {e(str(L.get('effort') or '기본'))}), 사내는 low다.</li>
<li>가정: Z.ai 공식 API의 GLM 5.3 = 사내 LLM(사용자 결정). 실제 온프렘 서빙(양자화 등)이 다르면 결과·속도가 조금 달라질 수 있다.</li>
</ul></div>
</main></body></html>"""


def main() -> None:
    s = summarize()
    REPORT.write_text(render(s), "utf-8")
    print(json.dumps({k: v for k, v in s.items() if k != "models"}, ensure_ascii=False))
    for k, m in s["models"].items():
        print(k, {x: m[x] for x in ("success", "fail_codes", "hold_compliance", "citations_mean",
                                     "lat_median_s", "cost_usd", "per_100_usd", "n_run")})
    print("report:", REPORT)


if __name__ == "__main__":
    main()
