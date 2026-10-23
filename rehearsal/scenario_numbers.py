"""영상 대본 상황 2·3 장면의 숫자(2026-10-19, claude-a -- claude-b 요청 (3)): 연말형 묶음(`demo/org-n100-yearend`)으로 서버 API를 차례로 불러
화면에 나올 이름·숫자를 모은다. 화면은 같은 API를 쓰므로 숫자가 같다. AI는 부르지 않는다(검토 자료 본문은 정해진 틀).

    uv run python -m rehearsal.scenario_numbers [--d6 doc|copy]   # → rehearsal/results/scenarios-1023.json(doc) · scenarios-1019.json(copy)
                                                                  #   (데이터 폴더를 안 주면 임시 폴더)

흐름: 신규 사업 등록 -- doc(2026-10-23 기본, 영상 D6): 실제 공공 입찰공고(산안공고 제2026-68호, 사전규격 R26BD00208180)의 사업을
"10월 공고·12월 시작"으로 가정(사용자)하고 공고문에서 채운 기간 길이 7개월·월 예산(1억 원 ÷ 7, 1점 = 1만 원)으로, 필요 기술은 제안요청서를 읽은 것
(`rehearsal/data/d6_bid_doc_skills.json`, `rehearsal.d6_bid_doc` -- 제안요청서는 사전규격과 글자가 같다)을 등록 양식 "추정 모두 선택"처럼 경력 2년·1명으로
· copy(예전): 고도화 과제(이전 사업 J007 복사 -- 생성기 v4 연말형에서 올해 말에 끝나는 개발 사업)
→ 후보 세 갈래(끝나 가는 인력·이전 팀원 표시) →
손 편성 확정(끝나 가는 사람 + 빼 오는 사람) → 인사팀 검토 자료 → J009 긴급 투입(상황 글 두 명, 빼 오기 1명 허용) → 확정 → 검토 자료 →
J015 긴급 투입 "프로젝트 동시 수행으로 지원"(원래 사업 유지, 허용 사유 긴급 지원 -- 2026-10-19 사용자 결정) → 확정 → 검토 자료.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTS = {"doc": ROOT / "rehearsal" / "results" / "scenarios-1023.json", "copy": ROOT / "rehearsal" / "results" / "scenarios-1019.json"}
BID_DOC = ROOT / "rehearsal" / "data" / "d6_bid_doc_skills.json"
# 영상 D6 사업(2026-10-23 밤 -- claude-b 요청 `docs/requests/2026-10-23-doc-meta-brief.md` 3): 한국산업안전보건공단 입찰공고의 사업, 이전 사업 없음.
# 사업명·고객 구분·기간 길이·월 예산은 "자료에서 사업 정보 채우기"가 공고문에서 채우는 값(7개월 = AI가 계약기간 날짜로 센 값, 1,429 = 1억 원 ÷ 7 ÷ 1만 원),
# 시작 달 2026-12는 시연 가정. 정원은 시연자가 적는 값 -- 월 1,429점에 들어가는 중급 1명(중급 개발 1,000 · 중급 둘이면 2,000으로 넘친다)
BID_PROJECT = {"name": "생성형 AI 기반 산업안전보건 비정형 데이터 정제·구조화 및 활용체계 구축 연구 용역", "sector": "대외공공",
               "start_month": "2026-12", "end_month": "2027-06", "monthly_budget": 1429, "stage": "예정", "predecessor": None, "parent": None,
               "external_headcount": 0, "grades": {"특급": 0, "고급": 0, "중급": 1, "초급": 0}}


PREDECESSOR = "J007"       # 연말형(생성기 v4)에서 올해 말 끝나는 개발 사업 -- 고도화 과제의 이전 사업
SUPPORT = "J015"           # 동시 수행 지원 장면의 진행 사업(끝나지 않는 큰 팀, v4 고도화 9명)
EMERGENCY = "J009"         # 긴급 투입 장면의 진행 사업([고도화] 비정형 문서 메타데이터 전환 -- 이 100명은 모두 데이터플랫폼 실행그룹, 사용자 2026-10-23 밤 "데이터플랫폼은 데이터 마트나 비정형쪽")
# D7 상황을 말로(2026-10-24 사용자 "두명 문장으로 가자" -- 적재 장애 + 손글씨 신청서 인식을 합친 글): 기술 이름 없이 일로만 쓴 현업 문장.
# 규칙은 기술 0개·인원 1명으로 읽고, AI는 OCR·ETL·SQL(미루어 봄)과 인원 2명을 읽는다(OpenAI 3/3·GLM 2/2). 미루어 본 기술은 화면에서 꺼져 있어 시연자가 켠다 -- SPOKEN_SKILLS.
# 한 명에게 셋을 다 바라면 OCR 1개월인 데이터 엔지니어가 1위였다(세 기술을 다 가진 사람이 없다) -- 두 명으로 나눠 쓴 글이다.
# 예전: J004 "원천 시스템 테이블 구조가 바뀌어서 …"(사용자 "애매하네"), J001 "… AI 에이전트 개발 및 T2SQL …", 그 전 J004 "… 스파크랑 금융 업무 …"(기술 이름이 있어 규칙과 AI가 같았다)
SPOKEN = ("고도화 범위에 손글씨 신청서 인식이 추가됐는데, 인식한 결과를 싣는 새벽 적재 배치도 계속 실패해요. "
          "스캔 문서에서 글자 뽑아 본 사람 한 명, 적재 프로그램이랑 쿼리 고칠 사람 한 명, 둘 다 중급으로 급히 필요합니다")
SPOKEN_SKILLS = ["비정형 문서 처리(OCR)", "ETL", "SQL"]
SPOKEN_HEADCOUNT = 2       # AI가 읽은 인원(규칙은 "한 명"만 보고 1) -- 화면은 AI 값으로 칸을 채운다


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--d6", choices=("doc", "copy"), default="doc")
    mode = ap.parse_args().d6
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
        if mode == "copy":
            pred = c.get(f"/api/workspace/projects/{PREDECESSOR}/form").json()
            form = {**pred, "name": pred["name"].replace("[실행] ", "") + " 고도화", "start_month": "2027-01", "end_month": "2028-06",
                    "stage": "예정", "predecessor": PREDECESSOR}
        else:
            form = {**BID_PROJECT, "skills": json.loads(BID_DOC.read_text("utf-8"))["register_skills"]}
        r = c.post("/api/workspace/projects", json={"dataset_version": ws["dataset_version"], "project": form})
        r.raise_for_status()
        pid, ws = r.json()["project_id"], r.json()["workspace"]
        out["register"] = {"project_id": pid, "copied_from": PREDECESSOR if mode == "copy" else None, "name": form["name"],
                           "skills": [s["name"] for s in form["skills"]],
                           "seats": next(p for p in ws["pipeline"] if p["project_id"] == pid)["seats"]}
        cands = c.get(f"/api/pipeline/{pid}/candidates", params={"dataset_version": ws["dataset_version"]}).json()
        pick = lambda rows: [{"name": x["name"], "grade": x["grade"], "delta": round(x["delta_total"], 2),   # noqa: E731
                              "predecessor_team": x.get("predecessor_team"), "from": x.get("pulled_from")} for x in rows[:3]]
        out["candidates"] = {g: pick(cands[g]) for g in ("bench", "ending", "pull")}
        chosen = (cands["ending"][:1] or cands["bench"][:1]) + (cands["pull"][:1] if mode == "copy" else [])   # 영상 D6은 끝나 가는 인력 맨 위 한 명
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
                                                "pull_budget": 1, "note": "과업 추가·적재 장애"})
        r.raise_for_status()
        plan = r.json()
        # 상황을 말로(D7): 같은 판에서 상황 글의 요구(인원·등급은 규칙, 기술은 AI가 미루어 보고 시연자가 켠 SPOKEN_SKILLS)로 추천을 받으면 무엇이 나오나.
        # 이 스크립트는 AI를 부르지 않는다 -- 켤 기술은 위 측정(OpenAI 3회·GLM 2회)에서 매번 나온 것으로 고정했다
        from api.rag.request_parse import parse_request
        from core.ingest.skills import load_dictionary
        said = parse_request(SPOKEN, load_dictionary())    # 등급(규칙도 같다) -- 기술은 AI가 미루어 본 것을 시연자가 켜고, 인원은 AI가 읽은 2명
        skills = list(dict.fromkeys([x["name"] for x in said["skills"] if x["kind"] == "explicit"] + SPOKEN_SKILLS))
        said["headcount"] = SPOKEN_HEADCOUNT
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
        # 화면 흐름대로 말로 받은 추천을 확정한다(2026-10-23 밤 -- J001 글의 추천은 기술 없이 찾은 추천과 다르다. 기술 없는 추천은 참고로만 남긴다)
        out["emergency_plain"] = {"added": plan["best"]["added"], "delta": plan["best"].get("delta"), "apply_hint": plan["apply_hint"],
                                  "moved": plan["best"].get("diff", {}).get("moved")}
        plan = spoken
        out["emergency"] = {"added": plan["best"]["added"], "delta": plan["best"].get("delta"), "apply_hint": plan["apply_hint"],
                            "standby": plain_standby,
                            "moved": plan["best"].get("diff", {}).get("moved")}
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": plan["best"]["entries"],
                                                 "source": "emergency", "note": f"{EMERGENCY} 과업 추가·적재 장애", **plan["apply_hint"]})
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
    OUTS[mode].write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
