"""영상 D2·D3·D4·D8 장면 숫자를 미리 계산 결과에서 뽑는다(2026-10-19, claude-a -- 생성기 v3·전일 투입으로 다시 만든 뒤).

외부 AI를 부르지 않는다(리뷰 글 판정은 동봉 값, 소명 글은 사실 재료만, 교체는 평가기 채점만). 화면이 보이는 값과 같은 함수를 쓴다.
  D2: 연초 100명 안 A~D -- 목적값·평가기 점수·배치 건수·최적화율·충족률·증명 상한 대비 차이
  D3: 안 A의 대표 사업(J001) 팀과 소명 근거 사실 수·불리한 사실 수
  D4: J001 팀원 하나를 같은 등급의 쉬는 사람으로 바꾼 교체의 점수 변화·위반(교체 검토와 같은 평가기, 참고값)
  D8: 연초 300명 안 A~D

    TEAMWEAVER_DATA_DIR=<임시 폴더> uv run --group benchmark python -m rehearsal.plan_scene_numbers   # → rehearsal/results/plan-scenes-1019.json
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT = ROOT / "rehearsal" / "results" / "plan-scenes-1019.json"
FLAGSHIP = "J001"


def _plans(name: str, graph, S, C):
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.metrics import matching_fulfillment, optimization_ratio
    from core.optimize.milp import MilpParams
    from core.optimize.types import AssignEntry, PlanAssignment
    pre = json.loads((DEMO / "precomputed" / f"{name}.json").read_text("utf-8"))
    params = MilpParams(**pre["optimize"]["milp_params"])
    rows = []
    for p in pre["optimize"]["plans"]:
        entries = [AssignEntry(**e) for e in p["entries"]]
        plan = PlanAssignment(entries=entries, objective=p["objective"], unfilled=p["unfilled"], violations=[], label=p["label"])
        ev = evaluate_plan(graph, S, C, params, entries)
        bound = p.get("best_bound")
        rows.append({"label": p["label"], "objective": round(p["objective"], 2), "eval_objective": round(ev.objective.total, 2),
                     "assignments": len(entries), "people": len({e.person_id for e in entries}),
                     "unfilled_seats": sum(s.missing for s in ev.shortfalls), "violations": len(ev.violations),
                     "optimization_ratio": round(optimization_ratio(graph, S, plan, params), 3),
                     "fulfillment": round(matching_fulfillment(graph, plan, {}), 3),
                     "termination": p.get("termination"), "time_limited": p.get("time_limited"),
                     "gap_to_bound": (round((bound - p["objective"]) / abs(p["objective"]), 3)
                                      if bound is not None and p["objective"] else None),
                     "elapsed_s": p.get("elapsed_s")})
    return pre, params, rows


def main() -> None:
    if not os.environ.get("TEAMWEAVER_DATA_DIR", "").strip():
        os.environ["TEAMWEAVER_DATA_DIR"] = tempfile.mkdtemp(prefix="tw-plan-scenes-")
    for k in ("OPENAI_API_KEY", "ZAI_API_KEY", "TYPESAFE_API_KEY"):
        os.environ[k] = ""                          # 리뷰 글 판정은 동봉 값(빠진 것이 있으면 항목 점수) -- 외부 AI 없이
    os.environ.setdefault("TEAMWEAVER_DEMO_DIR", str(DEMO))
    from api.demos import build_demo
    from api.routes.justification import build_input, display_names
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.milp import full_eligible
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    out: dict = {}
    for name, key in (("org-n100", "D2"), ("org-n300.zip", "D8")):
        act = build_demo(DEMO / name)
        g = act.graph
        eng = ScoringEngine(g)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        pre, params, rows = _plans(name.removesuffix(".zip"), g, S, C)
        if pre.get("dataset_version") != act.info.version:
            raise SystemExit(f"{name}: 데이터 버전이 미리 계산과 다르다 -- precompute부터")
        out[key] = {"bundle": name, "plans": rows}
        if key != "D2":
            act.retire()
            continue
        names = display_names(act)
        a = [AssignEntry(**e) for e in pre["optimize"]["plans"][0]["entries"]]
        kg = act.knowledge_graph()
        inp = build_input(kg, FLAGSHIP, a, g, S, C, params)
        team = sorted({e.person_id for e in a if e.project_id == FLAGSHIP})
        out["D3"] = {"project": FLAGSHIP, "team": [names.get(p, p) for p in team],
                     "facts": len(inp.facts), "adverse_facts": sum(1 for f in inp.facts if f.adverse)}
        # D4: J001 팀원 중 적합도가 가장 낮은 사람을 같은 등급의 쉬는 사람 중 적합도가 가장 높은 사람으로(자격 있는 사람만)
        j = g.project_index[FLAGSHIP]
        busy = {e.person_id for e in a}
        out_p = min(team, key=lambda p: (float(S[g.pid_index[p], j]), p))
        grade = g.people[g.pid_index[out_p]].grade
        pool = [p for p in g.people if p.grade == grade and p.id not in busy and full_eligible(p, g.projects[j])]
        in_p = max(pool, key=lambda p: (float(S[g.pid_index[p.id], j]), p.id)) if pool else None
        before = evaluate_plan(g, S, C, params, a)
        swap = None
        if in_p is not None:
            after_entries = [AssignEntry(person_id=in_p.id, project_id=e.project_id, alloc=e.alloc)
                             if (e.person_id, e.project_id) == (out_p, FLAGSHIP) else e for e in a]
            after = evaluate_plan(g, S, C, params, after_entries)
            swap = {"out": names.get(out_p, out_p), "in": names.get(in_p.id, in_p.id), "grade": grade.value,
                    "delta_total": round(after.objective.total - before.objective.total, 3),
                    "delta_skill": round(after.objective.skill - before.objective.skill, 3),
                    "delta_synergy": round(after.objective.synergy - before.objective.synergy, 3),
                    "violations": [v.code for v in after.violations]}
        out["D4"] = swap
        act.retire()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:5000])


if __name__ == "__main__":
    main()
