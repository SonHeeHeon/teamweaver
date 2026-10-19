"""부서장 점검(2026-10-19 claude-b, 점검표 f6·f7): 긴급 투입 화면에 적은 상황 글이 인사팀 검토 자료의 상황 메모 처음 값이 되고
(AI에는 가지 않음), 변경 이력의 고정 기록은 사번 대신 이름으로 남는다. 연말형 묶음 -- 사람·사업 번호는 test_review_docs와 같다."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

PULLED, BENCH, SRC, TGT = "DP0043", "DP0053", "J004", "J018"
SITUATION = "고객 요청으로 범위 확대 — 쿠버네티스 경험자 급히 필요"


def _ws(c):
    return c.get("/api/workspace").json()


def _confirm(c, *, source: str, situation: str) -> str:
    ws = _ws(c)
    entries = []
    for item in ws["pipeline"] + ws["ongoing"]:
        entries += [{"person_id": t["person_id"], "project_id": item["project_id"], "alloc": t["alloc"]} for t in item["team"]]
    entries = [e for e in entries if not (e["person_id"] == PULLED and e["project_id"] == SRC)]
    entries += [{"person_id": PULLED, "project_id": TGT, "alloc": 1.0}, {"person_id": BENCH, "project_id": TGT, "alloc": 1.0}]
    r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": source,
                                             "note": f"{TGT} 확정", "situation": situation})
    assert r.status_code == 200, r.text
    return r.json()["workspace"]["dataset_version"]


def test_emergency_situation_prefills_the_review_memo_and_never_reaches_the_ai(yearend, monkeypatch):
    from api.main import app
    calls = []

    def fake_summary(client, model, inp, **kw):          # 검토 자료 초안은 요약(2026-10-20) -- 재료(inp)만 본다
        calls.append(inp)
        return {"summary": None, "summary_method": None}
    monkeypatch.setattr("api.rag.justification.generate_summary", fake_summary)
    from api.routes import review_docs as rd       # 라우트가 쥔 의존성 함수(api.deps를 다시 불러온 시험이 있어도 맞게)
    app.dependency_overrides[rd.get_openai_client_or_none] = lambda: SimpleNamespace()
    try:
        with TestClient(app) as c:
            v = _confirm(c, source="emergency", situation=f"  {SITUATION}  ")
            doc = c.post(f"/api/review-docs/{v}/{TGT}").json()
            assert doc["confirm"]["kind"] == "emergency" and doc["situation_memo"] == SITUATION
            assert "situation" not in doc["confirm"]                 # 원문은 상황 메모 한 곳에만(폴백 리뷰 S1)
            assert c.post(f"/api/review-docs/{v}/{TGT}/draft").status_code == 200 and len(calls) == 1
            assert "쿠버네티스" not in repr(calls[0])               # 상황 메모는 AI 재료가 아니다(화면 표시 그대로)
            # 부서장이 고칠 수 있다(처음 값일 뿐)
            got = c.get(f"/api/review-docs/{v}/{TGT}").json()
            r = c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": got["revision"], "situation_memo": "고친 메모"})
            assert r.status_code == 200 and r.json()["situation_memo"] == "고친 메모"
    finally:
        app.dependency_overrides.clear()


def test_new_project_confirm_keeps_an_empty_memo(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c, source="manual", situation="손 편성에는 상황 메모 칸이 없다")
        doc = c.post(f"/api/review-docs/{v}/{TGT}").json()
        assert doc["situation_memo"] == "" and "situation" not in doc["confirm"]
        # 긴급 투입이 아닌 확정은 상황 글을 이력에도 남기지 않는다(보이지 않는 값으로 남지 않게)
        from api.review_docs import confirms
        from api.workspace import History
        assert [cf["situation"] for cf in confirms(History().entries())] == [""]


def test_lock_history_note_uses_names_not_ids(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        names = {p["id"]: p["name"] for p in c.get("/api/meta").json()["people"]}
        team = [t["person_id"] for t in next(o for o in ws["ongoing"] if o["project_id"] == SRC)["team"]]
        assert len(team) >= 6
        r = c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": SRC,
                                                "people": [PULLED], "locked": True})
        assert r.status_code == 200, r.text
        note = r.json()["workspace"]["history"][0]["note"]
        assert note == f"{SRC} 고정: {names[PULLED]}" and PULLED not in note
        six = [p for p in team if p != PULLED][:6]
        r = c.post("/api/workspace/lock", json={"dataset_version": r.json()["workspace"]["dataset_version"], "project_id": SRC,
                                                "people": six, "locked": True})
        assert r.status_code == 200, r.text
        note = r.json()["workspace"]["history"][0]["note"]
        assert note.endswith(" 외 1명") and all(names[p] in note for p in six[:5]) and names[six[5]] not in note
