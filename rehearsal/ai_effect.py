"""평가 글을 AI가 읽느냐, 체크한 항목만 세느냐에 따라 배치가 어떻게 달라지나(2026-10-18, claude-a).

E4는 판정기 하나하나의 정확도(부품 지표)를 쟀다. 여기서는 그 차이가 **배치 결정**에 닿는지를 잰다.
같은 시연 묶음(100·200·300명)을 두 방식으로 만든다.
  - 규칙: 글 극성 = 고른 항목의 균형((좋은 항목 - 아쉬운 항목) / 전체). 글은 읽지 않는다(core.datagen.parse_reviews).
  - AI: 서비스와 같은 LLM 판정값(demo/review_judgments.json 동봉 값, 다시 부르지 않음).
재는 것
  1. 글에만 드러난 부정: AI는 부정(<0)으로 읽었는데 규칙은 0 이상인 평가 글 수.
  2. 그런 글이 오간 쌍이 협업 보상 대상(|C| 상위 쌍)에 들어가는 수 -- 규칙 C와 AI C 각각.
  3. 같은 설정으로 안 A를 두 C로 풀고(미리 계산과 같은 설정), 그런 쌍을 같은 사업에 함께 넣은 횟수와
     AI가 읽은 점수표로 다시 채점한 협업 항.
가상 데이터다(직설형 평가자 8%·어긋난 글 5%를 생성기가 넣었다) -- 사업 효과는 NOT_CALIBRATED.

    TEAMWEAVER_DATA_DIR=<임시 폴더> TEAMWEAVER_SOLVER_SEEDS=4 uv run --group benchmark python -m rehearsal.ai_effect
결과: rehearsal/results/ai-effect.json
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT = ROOT / "rehearsal" / "results" / "ai-effect.json"
BUNDLES = {100: "org-n100", 200: "org-n200.zip", 300: "org-n300.zip"}


def _load(root: Path):
    from api.datasets import build_active, bundle_version, extract_bundle_zip
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    if root.suffix == ".zip":
        tmp = tempfile.mkdtemp(prefix="tw-ai-effect-")
        root = extract_bundle_zip(root.read_bytes(), Path(tmp) / "bundle")
    bundle, report = load_bundle(root)
    ds, parsed_rule = to_dataset(bundle, report)
    kw = dict(dataset_id=str(bundle.manifest["dataset_id"]), version=bundle_version(root), source="demo-bundle",
              synthetic=True, manifest=bundle.manifest, bundle=bundle)
    rule = build_active(ds, parsed_rule, judge=False, **kw)
    ai = build_active(ds, parsed_rule, judge=True, **kw)
    if ai.info.review_judge != "llm":
        raise SystemExit(f"AI 판정값을 쓰지 못했다: {ai.info.judge_error}")
    from api.review_judge import judge_reviews
    from api.demos import demo_judgments_path
    from api.datasets import judge_cache_path
    parsed_ai = judge_reviews(ds, parsed_rule, cache_path=judge_cache_path(True), trim=False,
                              seed_path=demo_judgments_path())
    return ds, parsed_rule, parsed_ai, rule, ai


def measure(n: int) -> dict:
    import numpy as np
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.alternatives import _PLAN_A_GAP
    from core.optimize.milp import MilpParams, pruned_pairs, solve_milp
    from core.scoring.engine import ScoringEngine

    ds, parsed_rule, parsed_ai, rule, ai = _load(DEMO / BUNDLES[n])
    pre = json.loads((DEMO / "precomputed" / f"{BUNDLES[n].removesuffix('.zip')}.json").read_text("utf-8"))
    # 서비스와 같은 설정으로 푼다(Codex 리뷰 2026-10-18): 기록된 milp_params의 solver_seeds=1을 그대로 넘기면 "명시"로 잡혀
    # 미리 계산 때 쓴 시드 4개(파일의 solver_seeds)를 덮는다. 안 A는 서비스가 허용 차이를 1%로 좁혀 푼다(alternatives._PLAN_A_GAP).
    raw = {k: v for k, v in pre["optimize"]["milp_params"].items() if k != "solver_seeds"}
    params = MilpParams(**raw, solver_seeds=int(pre.get("solver_seeds") or 1))
    params = params.model_copy(update={"gap": min(params.gap, _PLAN_A_GAP)})
    g_rule, g_ai = rule.graph, ai.graph
    pid = g_ai.pid_index

    text_only_neg, rule_only_neg = [], []
    for r, a in zip(parsed_rule, parsed_ai):
        if a.text_polarity < 0 <= r.text_polarity:
            text_only_neg.append(tuple(sorted((pid[a.reviewer_id], pid[a.reviewee_id]))))
        if r.text_polarity < 0 <= a.text_polarity:
            rule_only_neg.append(tuple(sorted((pid[a.reviewer_id], pid[a.reviewee_id]))))
    flagged = set(text_only_neg)

    S = ScoringEngine(g_ai).skill_matrix({})
    C_rule = ScoringEngine(g_rule).synergy_matrix()
    C_ai = ScoringEngine(g_ai).synergy_matrix()
    reward_rule = set(pruned_pairs(C_rule, params.pair_keep_ratio, params.max_pairs))
    reward_ai = set(pruned_pairs(C_ai, params.pair_keep_ratio, params.max_pairs))

    def together(entries) -> int:
        teams: dict[str, set[int]] = {}
        for e in entries:
            teams.setdefault(e.project_id, set()).add(pid[e.person_id])
        return sum(1 for team in teams.values() for p, q in flagged if p in team and q in team)

    out = {"people": len(ds.people), "projects": len(ds.projects), "reviews": len(parsed_ai),
           "text_only_negative_reviews": len(text_only_neg), "rule_only_negative_reviews": len(rule_only_neg),
           "flagged_pairs": len(flagged),
           "reward_pairs": len(reward_ai),
           "flagged_in_reward": {"rule": len(flagged & reward_rule), "ai": len(flagged & reward_ai)},
           "reward_overlap": len(reward_rule & reward_ai),
           "time_limit_s": params.time_limit, "solver_seeds": params.solver_seeds, "gap": params.gap}
    plans = {}
    for name, C in (("rule", C_rule), ("ai", C_ai)):
        t0 = time.perf_counter()
        plan = solve_milp(g_ai, S, C, params)
        ev = evaluate_plan(g_ai, S, C_ai, params, plan.entries)       # AI가 읽은 점수표로 채점
        plans[name] = plan
        out[f"plan_{name}"] = {"elapsed_s": round(time.perf_counter() - t0, 1),
                               "flagged_together": together(plan.entries),
                               "synergy_under_ai": round(ev.objective.synergy, 4),
                               "total_under_ai": round(ev.objective.total, 4),
                               "violations": len(ev.violations), "assignments": len(plan.entries)}
    a = {(e.person_id, e.project_id) for e in plans["ai"].entries}
    r = {(e.person_id, e.project_id) for e in plans["rule"].entries}
    out["assignments_only_ai"] = len(a - r)            # 안의 배치 수가 달라 1:1 교체로 세지 않는다(Codex 리뷰)
    out["assignments_only_rule"] = len(r - a)
    out["np_check"] = bool(np.isfinite(C_ai).all())
    return out


def main() -> None:
    os.environ.setdefault("TEAMWEAVER_SKIP_WARM", "1")
    from core.config import load_env
    load_env()                                      # .env(판정기 설정) -- 판정값은 동봉 파일에서 온다
    sizes = [int(s) for s in os.environ.get("AI_EFFECT_SIZES", "100 200 300").split()]
    res = {"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sizes": {}}
    for n in sizes:
        res["sizes"][str(n)] = measure(n)
        print(n, json.dumps(res["sizes"][str(n)], ensure_ascii=False), flush=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    print("저장:", OUT)


if __name__ == "__main__":
    main()
