"""운영 중 편성 측정 — 변경 예산 K별 개선량과 보강 시뮬레이션(시연·보고서용, 2026-10-06).

    uv run --group benchmark python -m rehearsal.operating_check --sizes 100 200 300

운영 중 시나리오 묶음(core/ingest/org_profile scenario="operating", seed 2026)에서
- S1: K = 0..3 각각 최선 편성, K=0 대비 배치 품질·빈자리 변화, 누가 어디로 옮겼는지, 풀이 시간
- S2: 진행 사업 하나(가장 팀이 큰 일반 사업)에 대해 후보 순위 상위 5명과 최선 2명(빼 오기 0명/1명)
결과: rehearsal/results/operating-check.json
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

from rehearsal.run import RESULTS, SEED, _env_info, _now

OUT = RESULTS / "operating-check.json"


def measure(size: int) -> dict:
    from api.settings import PlacementSettings
    from core.evaluate.operating import compare_move_budgets
    from core.evaluate.staffing_sim import best_additions, rank_candidates
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.ingest.org_profile import generate_org_bundle
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    root = generate_org_bundle(Path(tempfile.mkdtemp(prefix=f"operating-n{size}-")) / "b", size, seed=SEED,
                               scenario="operating")
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    t = time.perf_counter()
    s1 = compare_move_budgets(graph, S, C, params, ds.current, ks=(0, 1, 2, 3))
    s1_s = round(time.perf_counter() - t, 1)
    for r in s1:
        r.pop("entries", None)
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current]
    locked = {(c.person_id, c.project_id) for c in ds.current if c.locked}       # 잠긴 배치는 빼 오지 않는다
    teams = {}
    for c in ds.current:
        teams[c.project_id] = teams.get(c.project_id, 0) + 1
    target = max((p for p in teams if p != manifest.get("flagship_project")), key=lambda p: (teams[p], p))
    t = time.perf_counter()
    ranked = rank_candidates(graph, S, C, params, entries, target, include_pull=True, top=5, locked=locked)
    rank_s = round(time.perf_counter() - t, 2)
    best = {}
    for pull in (0, 1):
        t = time.perf_counter()
        r = best_additions(graph, S, C, params, entries, target, 2, pull_budget=pull, locked=locked)
        r.pop("entries", None)
        r["elapsed_s"] = round(time.perf_counter() - t, 2)
        best[str(pull)] = r
    return {"size": size, "dataset": manifest["dataset_id"], "bench": len(manifest["bench"]),
            "proposals": manifest["proposals"], "current": len(ds.current), "time_limit": params.time_limit,
            "s1": s1, "s1_elapsed_s": s1_s,
            "s2": {"project": target, "team_size": teams[target], "ranked": ranked, "rank_elapsed_s": rank_s,
                   "best_two": best}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 200, 300])
    args = ap.parse_args()
    out = {"env": _env_info(), "started_at": _now(), "seed": SEED, "runs": []}
    for n in args.sizes:
        r = measure(n)
        out["runs"].append(r)
        print(json.dumps({"size": n, "s1": [(x["k"], x.get("elapsed_s"), x.get("quality"), x.get("unfilled_seats"),
                                             x.get("termination")) for x in r["s1"]],
                          "s2_rank_s": r["s2"]["rank_elapsed_s"],
                          "s2_best": {k: (v.get("elapsed_s"), round(v["delta"]["total"], 3) if v.get("accepted") else None)
                                      for k, v in r["s2"]["best_two"].items()}}, ensure_ascii=False), flush=True)
    out["finished_at"] = _now()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
