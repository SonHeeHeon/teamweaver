"""영상 연간 계획·What-if 장면 숫자(2026-10-21, claude-a -- 사용자 "7,2번 시연 대본에 넣어주고, 시연 전체 … 업데이트").

연간 100명 편성 기록(12개월 백지, `demo/records/org-n100-annual.json`)을 열어 화면과 같은 API를 같은 순서로 부른다:
  D2 안 A~D(미리 계산 결과를 평가기로 다시 채점) · D3 안 A 대표 사업(J001) 소명 근거 사실 수
  D4 "안 A로 확정" -- 확정 창 미리 계산(지금 대비 옮김·새로·빠짐) → 확정(source plan)
  D5 What-if(지금 배치 = 확정한 안 A) -- 같은 등급 교체 하나, 등급이 하나 다른 교체(등급 대체) 하나 → 담기·확정(사유) → 인사팀 검토 자료
AI는 부르지 않는다(교체 설명은 규칙 기반 -- 점수·위반·등급 대체는 AI와 무관, 평가 글 판정은 기록에 동봉된 값).

    uv run --group benchmark python -m rehearsal.whatif_scene_numbers   # → rehearsal/results/whatif-scenes-1021.json (데이터 폴더를 안 주면 임시 폴더)

연초 6개월 기록(`org-n100.json`)이 아닌 까닭: 그 묶음은 진행 사업 배치 4건이 고정(locked)인데 전부 다시 짜는 안 A~D는 고정을 보지 않아
"안 A로 확정"이 고정 충돌로 막힌다(2026-10-21 측정 -- 운영 중 K 계산만 고정을 지킨다).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
NAME = "org-n100-annual"
RECORD = DEMO / "records" / f"{NAME}.json"
OUT = ROOT / "rehearsal" / "results" / "whatif-scenes-1021.json"
FLAGSHIP = "J001"
GRADES = ["특급", "고급", "중급", "초급"]
REASON = "데이터 정제·적재 범위라 이 등급으로 충분, 발주처와 협의함"


def _counts(pv: dict) -> dict:
    return {"moved": len(pv["moved"]), "joined": len(pv["joined"]), "left": len(pv["left"]),
            "grade_swaps": pv["grade_swaps"], "budget_add": pv["budget_add"],
            "new_violations": pv["new_violations"], "locked_dropped": pv["locked_dropped"]}


def _plan_numbers(out: dict) -> None:
    """D2·D3: 미리 계산의 안 A~D를 평가기로 다시 채점하고, 안 A J001 소명 재료의 사실 수를 센다(plan_scene_numbers와 같은 함수)."""
    from api.demos import build_demo
    from api.routes.justification import build_input, display_names
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    from rehearsal.plan_scene_numbers import _plans
    act = build_demo(DEMO / NAME)
    try:
        g = act.graph
        eng = ScoringEngine(g)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        pre, params, rows = _plans(NAME, g, S, C)
        if pre.get("dataset_version") != act.info.version:
            raise SystemExit(f"{NAME}: 데이터 버전이 미리 계산과 다르다 -- precompute부터")
        out["D2"] = {"bundle": NAME, "projects": len(g.projects), "plans": rows}
        names = display_names(act)
        a = [AssignEntry(**e) for e in pre["optimize"]["plans"][0]["entries"]]
        inp = build_input(act.knowledge_graph(), FLAGSHIP, a, g, S, C, params)
        team = sorted({e.person_id for e in a if e.project_id == FLAGSHIP})
        out["D3"] = {"project": FLAGSHIP, "team": [names.get(p, p) for p in team],
                     "facts": len(inp.facts), "adverse_facts": sum(1 for f in inp.facts if f.adverse)}
    finally:
        act.retire()


def main() -> None:
    if not os.environ.get("TEAMWEAVER_DATA_DIR", "").strip():
        # 기록 열기·확정이 작업 데이터를 바꾼다 -- 지정이 없으면 ~/.teamweaver가 아니라 임시 폴더에(scenario_numbers와 같음)
        import tempfile
        os.environ["TEAMWEAVER_DATA_DIR"] = tempfile.mkdtemp(prefix="tw-whatif-scenes-")
    os.environ.setdefault("TEAMWEAVER_LOGIN", "off")
    for k in ("TEAMWEAVER_ADMIN_PASSWORD", "TEAMWEAVER_ADMIN_PASSWORD_HASH", "TEAMWEAVER_ADMIN_TOKEN"):
        os.environ[k] = ""
    os.environ.setdefault("TEAMWEAVER_NIGHTLY", "off")
    os.environ.setdefault("TEAMWEAVER_SKIP_WARM", "1")
    os.environ.setdefault("TEAMWEAVER_DEMO_DIR", str(DEMO))
    os.environ["TEAMWEAVER_DEMO_BUNDLE"] = str(DEMO / "org-n100-yearend")
    for k in ("OPENAI_API_KEY", "ZAI_API_KEY", "TYPESAFE_API_KEY"):
        os.environ[k] = ""
    from fastapi.testclient import TestClient

    from api.main import app
    rec = json.loads(RECORD.read_text("utf-8"))
    plan_a = next(p for p in rec["optimize"]["plans"] if p["label"] == "A")
    out: dict = {"record": rec["title"]}
    _plan_numbers(out)
    with TestClient(app) as c:
        r = c.post("/api/records", content=RECORD.read_bytes(), headers={"Content-Type": "application/json"})
        r.raise_for_status()
        rid = r.json()["record"]["id"]
        r = c.post(f"/api/records/{rid}/open", json={})
        r.raise_for_status()
        settings = c.get("/api/settings").json()["settings"]
        meta = c.get("/api/meta").json()
        people = {p["id"]: p for p in meta["people"]}
        name = lambda pid: people[pid]["name"]
        ws = c.get("/api/workspace").json()
        before = sum(len(i["team"]) for i in ws["pipeline"] + ws["ongoing"])
        entries = [{"person_id": e["person_id"], "project_id": e["project_id"], "alloc": e["alloc"]} for e in plan_a["entries"]]
        # D4 연간 계획 "안 A로 확정": 확정 창 미리 계산 → 확정(source plan, 검토 자료 없음)
        pv = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": entries})
        pv.raise_for_status()
        out["D4_plan_confirm"] = {"current_entries_before": before, "plan_a_entries": len(entries), **_counts(pv.json())}
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries,
                                                 "source": "plan", "note": "연간 계획 안 A 확정"})
        r.raise_for_status()
        out["D4_plan_confirm"]["changes"] = r.json().get("changes")
        ws = r.json()["workspace"]
        base = [{"person_id": t["person_id"], "project_id": i["project_id"], "alloc": t["alloc"]}
                for i in ws["pipeline"] + ws["ongoing"] for t in i["team"]]
        team = [e["person_id"] for e in base if e["project_id"] == FLAGSHIP]
        placed = {e["person_id"] for e in base}
        bench = [p for p in meta["people"] if p["id"] not in placed]
        out["D5_start"] = {"current_entries": len(base), "team": [f"{name(p)}({people[p]['grade']})" for p in team],
                           "bench": [f"{p['name']}({p['grade']})" for p in bench]}

        def whatif(out_pid: str, in_pid: str) -> dict:
            body = {"entries": base, "swap": {"out_person_id": out_pid, "in_person_id": in_pid, "project_id": FLAGSHIP},
                    "weights": {}, "milp_params": settings, "dataset_version": ws["dataset_version"], "grade_swap": True}
            r = c.post("/api/whatif", json=body)
            if r.status_code != 200:
                return {"status": r.status_code}
            w = r.json()
            return {"out": name(out_pid), "out_grade": people[out_pid]["grade"], "in": name(in_pid), "in_grade": people[in_pid]["grade"],
                    "delta": round(w["objective_delta"], 3),
                    "parts": {k: round(w["after"][k] - w["before"][k], 3) for k in ("skill", "synergy", "overfamiliarity", "unfilled")},
                    "feasible": w["feasible"], "new_violations": [v.get("message") or v.get("code") for v in w["new_violations"]],
                    "new_shortfalls": len(w["new_shortfalls"]), "grade_swaps": w["grade_swaps"], "budget_add": w["budget_add"],
                    "risks": w["briefing"].get("risks", [])[:3], "fallback": w["fallback_used"],
                    "out_pid": out_pid, "in_pid": in_pid}

        tried = [w for o in team for b in bench if not (w := whatif(o, b["id"])).get("status")]
        clean = [w for w in tried if w["feasible"] and not w["new_violations"] and not w["budget_add"]]
        same = sorted((w for w in clean if w["out_grade"] == w["in_grade"]), key=lambda w: -w["delta"])
        near = sorted((w for w in clean if w["grade_swaps"]
                       and abs(GRADES.index(w["out_grade"]) - GRADES.index(w["in_grade"])) == 1), key=lambda w: -w["delta"])
        brief = lambda w: {k: w[k] for k in ("out", "out_grade", "in", "in_grade", "delta")}
        out["D5_tried"] = len(tried)
        out["D5_same_grade_options"] = [brief(w) for w in same[:5]]
        out["D5_grade_swap_options"] = [brief(w) for w in near[:5]]
        if not near:
            raise SystemExit("새 위반·예산 증액 없는 등급 대체 교체가 없다")
        pick = near[0]
        out["D5_same_grade"] = {k: v for k, v in same[0].items() if not k.endswith("_pid")} if same else None
        out["D5_grade_swap"] = {k: v for k, v in pick.items() if not k.endswith("_pid")}
        # 담기 → 확정: 바뀐 명단으로 확정 미리 계산 → 사유와 함께 확정(손 편성) → 검토 자료
        after = [e for e in base if not (e["person_id"] == pick["out_pid"] and e["project_id"] == FLAGSHIP)]
        after.append({"person_id": pick["in_pid"], "project_id": FLAGSHIP, "alloc": 1.0})
        pv = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": after})
        pv.raise_for_status()
        pvj = pv.json()
        out["D5_confirm"] = {**_counts(pvj), "lines": [f"{m['name']}: {'·'.join(m['from']) or '대기'} → {'·'.join(m['to']) or '대기'}"
                                                       for m in pvj["moved"] + pvj["joined"] + pvj["left"]]}
        swaps = [{"project_id": j, "seat": s["seat"], "grade": s["grade"], "count": s["count"], "reason": REASON}
                 for j, lst in pvj["grade_swaps"].items() for s in lst]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": after, "source": "manual",
                                                 "note": f"What-if 변경 1건: {pick['out']} → {pick['in']}({FLAGSHIP})",
                                                 "grade_swaps": swaps, **({"budget_add": pvj["budget_add"]} if pvj["budget_add"] else {})})
        r.raise_for_status()
        v = r.json()["workspace"]["dataset_version"]
        out["D5_confirm"]["changes"] = r.json().get("changes")
        item = next(i for i in r.json()["workspace"]["ongoing"] + r.json()["workspace"]["pipeline"] if i["project_id"] == FLAGSHIP)
        out["D5_confirm"]["seats_after"] = item.get("seats")
        r = c.post(f"/api/review-docs/{v}/{FLAGSHIP}")
        r.raise_for_status()
        doc = r.json()
        out["D5_review_doc"] = {"facts": len(doc["facts"]), "adverse": sum(1 for f in doc["facts"] if f["adverse"]),
                                "move_facts": [f["text"] for f in doc["facts"] if f["section"] == "move"][:6]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:9000])


if __name__ == "__main__":
    main()
