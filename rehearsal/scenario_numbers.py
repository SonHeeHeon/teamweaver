"""영상 대본 상황 2·3 장면의 숫자(2026-10-19, claude-a -- claude-b 요청 (3)): 연말형 묶음(`demo/org-n100-yearend`)으로 서버 API를 차례로 불러
화면에 나올 이름·숫자를 모은다. 화면은 같은 API를 쓰므로 숫자가 같다. AI는 부르지 않는다(검토 자료 본문은 정해진 틀).

    uv run python -m rehearsal.scenario_numbers      # → rehearsal/results/scenarios-1019.json (데이터 폴더를 안 주면 임시 폴더)

흐름: 고도화 과제 등록(이전 사업 J003 복사) → 후보 세 갈래(끝나 가는 인력·이전 팀원 표시) → 손 편성 확정(끝나 가는 사람 + 빼 오는 사람) →
인사팀 검토 자료 → J007 긴급 투입(빼 오기 1명 허용) → 확정 → 검토 자료.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rehearsal" / "results" / "scenarios-1019.json"


def _entries(ws):
    out = []
    for item in ws["pipeline"] + ws["ongoing"]:
        out += [{"person_id": t["person_id"], "project_id": item["project_id"], "alloc": t["alloc"]} for t in item["team"]]
    return out


def _brief_doc(doc):
    return {"project": f"{doc['project_name']}({doc['project_id']})", "kind": doc["confirm"]["kind"],
            "joined": [{"name": doc["names"].get(j["person_id"], j["person_id"]), "grade": j["grade"], "origin": j["origin"],
                        "sources": [s["project_id"] for s in j["sources"]]} for j in doc["moves"]["joined"]],
            "move_facts": [f["text"] for f in doc["facts"] if f["section"] == "move"],
            "adverse_facts": sum(1 for f in doc["facts"] if f["adverse"]), "facts": len(doc["facts"])}


def main() -> None:
    if not os.environ.get("TEAMWEAVER_DATA_DIR"):
        # 이 스크립트는 작업 데이터를 바꾼다(등록·확정·검토 자료) -- 지정이 없으면 ~/.teamweaver가 아니라 임시 폴더에(리뷰 P2)
        import tempfile
        os.environ["TEAMWEAVER_DATA_DIR"] = tempfile.mkdtemp(prefix="tw-scenario-numbers-")
    os.environ.setdefault("TEAMWEAVER_LOGIN", "off")
    os.environ.setdefault("TEAMWEAVER_NIGHTLY", "off")
    os.environ.setdefault("TEAMWEAVER_SKIP_WARM", "1")
    os.environ["TEAMWEAVER_DEMO_BUNDLE"] = str(ROOT / "demo" / "org-n100-yearend")
    for k in ("OPENAI_API_KEY", "ZAI_API_KEY", "TYPESAFE_API_KEY"):
        os.environ[k] = ""                               # 외부 AI 없이(빈 값이면 .env가 다시 채우지 않는다)
    from fastapi.testclient import TestClient
    from api.main import app
    out: dict = {}
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
        out["start"] = {"pipeline": [(p["project_id"], p["name"], p["stage"]) for p in ws["pipeline"]],
                        "ending": [o["project_id"] for o in ws["ongoing"] if o["ends_in_horizon"]], "bench": len(ws["bench"])}
        pred = c.get("/api/workspace/projects/J003/form").json()
        form = {**pred, "name": "보험사 B 고도화", "start_month": "2027-01", "end_month": "2028-06", "stage": "예정", "predecessor": "J003"}
        r = c.post("/api/workspace/projects", json={"dataset_version": ws["dataset_version"], "project": form})
        r.raise_for_status()
        pid, ws = r.json()["project_id"], r.json()["workspace"]
        out["register"] = {"project_id": pid, "copied_from": "J003", "seats": next(p for p in ws["pipeline"] if p["project_id"] == pid)["seats"]}
        cands = c.get(f"/api/pipeline/{pid}/candidates", params={"dataset_version": ws["dataset_version"]}).json()
        pick = lambda rows: [{"name": x["name"], "grade": x["grade"], "delta": round(x["delta_total"], 2),   # noqa: E731
                              "predecessor_team": x.get("predecessor_team"), "from": x.get("pulled_from")} for x in rows[:3]]
        out["candidates"] = {g: pick(cands[g]) for g in ("bench", "ending", "pull")}
        chosen = (cands["ending"][:1] or cands["bench"][:1]) + cands["pull"][:1]
        pulled = {f"{x['person_id']}@{s}" for x in chosen for s in x.get("pulled_from") or []}
        entries = [e for e in _entries(ws) if f"{e['person_id']}@{e['project_id']}" not in pulled]
        entries += [{"person_id": x["person_id"], "project_id": pid, "alloc": x["alloc"]} for x in chosen]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual",
                                                 "note": f"{pid} 손 편성"})
        r.raise_for_status()
        v1 = r.json()["workspace"]["dataset_version"]
        out["manual_confirm"] = {"people": [x["name"] for x in chosen], "changes": r.json()["changes"]}
        r = c.post(f"/api/review-docs/{v1}/{pid}")
        r.raise_for_status()
        doc = r.json()
        out["review_new"] = _brief_doc(doc)
        ws = r.json()["workspace"]
        r = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": "J007", "n": 1,
                                                "pull_budget": 1, "note": "장애 대응"})
        r.raise_for_status()
        plan = r.json()
        out["emergency"] = {"added": plan["best"]["added"], "delta": plan["best"].get("delta"), "apply_hint": plan["apply_hint"],
                            "standby": [x.get("name") for x in plan.get("standby", [])[:3]],
                            "moved": plan["best"].get("diff", {}).get("moved")}
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": plan["best"]["entries"],
                                                 "source": "emergency", "note": "J007 장애 대응", **plan["apply_hint"]})
        r.raise_for_status()
        v2 = r.json()["workspace"]["dataset_version"]
        r = c.post(f"/api/review-docs/{v2}/J007")
        r.raise_for_status()
        doc = r.json()
        out["review_emergency"] = _brief_doc(doc)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
