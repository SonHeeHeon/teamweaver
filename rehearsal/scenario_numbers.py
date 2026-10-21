"""영상 대본 상황 2·3 장면의 숫자(2026-10-19, claude-a -- claude-b 요청 (3)): 연말형 묶음(`demo/org-n100-yearend`)으로 서버 API를 차례로 불러
화면에 나올 이름·숫자를 모은다. 화면은 같은 API를 쓰므로 숫자가 같다. AI는 부르지 않는다(검토 자료 본문은 정해진 틀).

    uv run python -m rehearsal.scenario_numbers      # → rehearsal/results/scenarios-1019.json (데이터 폴더를 안 주면 임시 폴더)

흐름: 고도화 과제 등록(이전 사업 J007 복사 -- 생성기 v4 연말형에서 올해 말에 끝나는 개발 사업) → 후보 세 갈래(끝나 가는 인력·이전 팀원 표시) →
손 편성 확정(끝나 가는 사람 + 빼 오는 사람) → 인사팀 검토 자료 → J004 긴급 투입(빼 오기 1명 허용) → 확정 → 검토 자료 →
J015 긴급 투입 "프로젝트 동시 수행으로 지원"(원래 사업 유지, 허용 사유 긴급 지원 -- 2026-10-19 사용자 결정) → 확정 → 검토 자료.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rehearsal" / "results" / "scenarios-1019.json"


PREDECESSOR = "J007"       # 연말형(생성기 v4)에서 올해 말 끝나는 개발 사업 -- 고도화 과제의 이전 사업
SUPPORT = "J015"           # 동시 수행 지원 장면의 진행 사업(끝나지 않는 큰 팀, v4 고도화 9명)
EMERGENCY = "J004"         # 긴급 투입 장면의 진행 사업(끝나지 않는 9명 개발 사업 -- v4에서 J007은 올해 말에 끝난다)
SPOKEN = "데이터 마트 적재가 계속 실패해요 — 스파크랑 금융 업무 아는 중급 한 명 급히"   # D7 상황을 말로(2026-10-22)


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
    if not os.environ.get("TEAMWEAVER_DATA_DIR", "").strip():
        # 이 스크립트는 작업 데이터를 바꾼다(등록·확정·검토 자료) -- 지정이 없으면 ~/.teamweaver가 아니라 임시 폴더에(리뷰 P2)
        import tempfile
        os.environ["TEAMWEAVER_DATA_DIR"] = tempfile.mkdtemp(prefix="tw-scenario-numbers-")
    os.environ.setdefault("TEAMWEAVER_LOGIN", "off")
    # 관리자 비밀번호·토큰이 .env에 있으면 LOGIN=off여도 로그인이 켜진다 -- 이 임시 서버에서는 비운다(빈 값은 .env가 다시 채우지 않는다, Codex 리뷰)
    for k in ("TEAMWEAVER_ADMIN_PASSWORD", "TEAMWEAVER_ADMIN_PASSWORD_HASH", "TEAMWEAVER_ADMIN_TOKEN"):
        os.environ[k] = ""
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
        pred = c.get(f"/api/workspace/projects/{PREDECESSOR}/form").json()
        form = {**pred, "name": pred["name"].replace("[실행] ", "") + " 고도화", "start_month": "2027-01", "end_month": "2028-06",
                "stage": "예정", "predecessor": PREDECESSOR}
        r = c.post("/api/workspace/projects", json={"dataset_version": ws["dataset_version"], "project": form})
        r.raise_for_status()
        pid, ws = r.json()["project_id"], r.json()["workspace"]
        out["register"] = {"project_id": pid, "copied_from": PREDECESSOR, "name": form["name"], "seats": next(p for p in ws["pipeline"] if p["project_id"] == pid)["seats"]}
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
        applied = r.json()
        v1 = applied["workspace"]["dataset_version"]
        out["manual_confirm"] = {"people": [x["name"] for x in chosen], "changes": applied["changes"]}
        r = c.post(f"/api/review-docs/{v1}/{pid}")
        r.raise_for_status()
        doc = r.json()
        out["review_new"] = _brief_doc(doc)
        ws = applied["workspace"]                    # 확정 응답(바로 위 r은 검토 자료 응답이다, Codex 리뷰)
        r = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": EMERGENCY, "n": 1,
                                                "pull_budget": 1, "note": "장애 대응"})
        r.raise_for_status()
        plan = r.json()
        # 상황을 말로(2026-10-22 D7): 같은 판에서 상황 글을 요구로 읽고(AI 없이 -- 이 글의 기술·인원·등급은 사전 별칭·규칙만으로 같다)
        # 그 요구로 추천을 받으면 무엇이 나오나. 화면은 AI로 읽어도 글에 쓴 기술은 같다(미루어 본 기술은 기본 꺼짐)
        from api.rag.request_parse import parse_request
        from core.ingest.skills import load_dictionary
        said = parse_request(SPOKEN, load_dictionary())
        skills = [x["name"] for x in said["skills"] if x["kind"] == "explicit"]
        r = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": EMERGENCY, "n": said["headcount"] or 1,
                                                "grade": said["grade"], "skills": skills, "pull_budget": 1, "note": SPOKEN})
        r.raise_for_status()
        spoken = r.json()
        same = sorted((e["person_id"], e["project_id"]) for e in spoken["best"]["entries"]) == sorted(
            (e["person_id"], e["project_id"]) for e in plan["best"]["entries"])
        out["emergency_spoken"] = {"text": SPOKEN, "skills": skills, "headcount": said["headcount"], "grade": said["grade"],
                                   "added": spoken["best"]["added"], "delta": spoken["best"].get("delta"),
                                   "fills": spoken["best"].get("fills"), "holes": spoken["best"].get("holes"),
                                   "apply_hint": spoken["apply_hint"], "same_entries_as_plain": same,
                                   "standby": [x.get("name") for x in spoken.get("standby", [])[:3]]}   # 요구 기술로 다시 매긴 대기 순위
        # 화면 "지금 바로 넣을 수 있는 사람"은 사업을 고를 때 기술 없이 뽑은 순위다 -- 말로 받기 전 계산의 것을 남긴다
        plain_standby = [x.get("name") for x in plan.get("standby", [])[:3]]
        if same:                                        # 같은 배치면 아래 확정·검토 자료 숫자는 그대로다 -- 화면 흐름대로 말로 받은 추천을 확정한다
            plan = spoken
        out["emergency"] = {"added": plan["best"]["added"], "delta": plan["best"].get("delta"), "apply_hint": plan["apply_hint"],
                            "standby": plain_standby,
                            "moved": plan["best"].get("diff", {}).get("moved")}
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": plan["best"]["entries"],
                                                 "source": "emergency", "note": f"{EMERGENCY} 장애 대응", **plan["apply_hint"]})
        r.raise_for_status()
        v2 = r.json()["workspace"]["dataset_version"]
        r = c.post(f"/api/review-docs/{v2}/{EMERGENCY}")
        r.raise_for_status()
        doc = r.json()
        out["review_emergency"] = _brief_doc(doc)
        # 프로젝트 동시 수행으로 지원(원래 사업 유지): SUPPORT 긴급 투입 후보 중 허용이 필요한 첫 사람 -- 확정이 허용(긴급 지원)을 함께 적는다
        ws = r_ws = c.get("/api/workspace").json()
        r = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": SUPPORT, "n": 1,
                                                "note": "기술 지원 요청"})
        r.raise_for_status()
        spare = [x for x in r.json()["candidates"]["spare"] if x.get("needs_grant") and not x["new_violations"]]
        out["concurrent_support"] = {"candidates": pick(spare)}
        if spare:
            x = spare[0]
            entries = _entries(r_ws) + [{"person_id": x["person_id"], "project_id": SUPPORT, "alloc": 1.0}]
            r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "emergency",
                                                     "note": f"{SUPPORT} 기술 지원(프로젝트 동시 수행)", **x["apply_hint"],
                                                     "concurrency_add": [{"person_id": x["person_id"], "memo": "기술 지원 요청"}]})
            r.raise_for_status()
            ws3 = r.json()["workspace"]
            out["concurrent_support"].update({"person": x["name"], "keeps": x.get("current_projects"),
                                              "grant": [g for g in ws3["concurrency"] if g["person_id"] == x["person_id"]],
                                              "monthly_cost": x["monthly_cost"], "delta": round(x["delta_total"], 2),
                                              "apply_hint": x["apply_hint"]})
            r = c.post(f"/api/review-docs/{ws3['dataset_version']}/{SUPPORT}")
            r.raise_for_status()
            out["review_concurrent"] = _brief_doc(r.json())
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
