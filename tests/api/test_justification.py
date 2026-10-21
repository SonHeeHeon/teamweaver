"""인사팀 소명 글 API(claude-a 계약 docs/requests/2026-10-09-hr-justification.md).

계산·검사의 정확성은 claude-a 시험(tests/test_justify*.py)이 본다. 여기서는 배선만: 명단을 서버가 정하는지, AI 정책
(키 없음·실데이터 외부 전송 불가 → 템플릿), 화면에 내보내지 않을 것(llm_text·탈락 문장), 409·404·422."""
import dataclasses
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from core.ingest.org_profile import generate_org_bundle


@pytest.fixture(scope="module")
def bundle_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("just")
    generate_org_bundle(root / "org-n100-operating", 100, seed=11, scenario="operating")
    return root


@pytest.fixture
def jc(bundle_root, monkeypatch, ai_client):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(bundle_root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(bundle_root / "org-n100-operating"))
    from api.main import app
    with ai_client(None), TestClient(app) as c:
        yield c, app


def _body(c, **kw):
    st = c.get("/api/operating/state").json()
    entries = [{"person_id": x["person_id"], "project_id": x["project_id"], "alloc": x["alloc"]} for x in st["current"]]
    project = entries[0]["project_id"]
    from api.plan_token import sign_plan
    from core.optimize.milp import MilpParams
    token = sign_plan(st["dataset_version"], "A", entries, {}, MilpParams())       # 서버가 계산한 원 플랜인 것처럼 서명
    return {"dataset_version": st["dataset_version"], "project_id": project, "entries": entries,
            "plan_label": "A", "plan_token": token, **kw}


def test_template_first_then_no_client(jc):
    c, _ = jc
    body = _body(c, ai=False)
    out = c.post("/api/justification", json=body).json()
    assert out["method"] == "template" and out["fallback_reason"] is None and "[F" in out["text"]
    assert out["facts"] and {"id", "kind", "text", "adverse"} <= set(out["facts"][0])
    assert "llm_text" not in out and out["ai_available"] is False and out["ai_unavailable_reason"] == "no_client"
    out = c.post("/api/justification", json={**body, "ai": True}).json()
    assert out["method"] == "template" and out["fallback_reason"] == "no_client"


def test_ai_text_is_never_sent_and_rejected_sentences_stay_hidden(jc, ai_client):
    c, _ = jc
    fake = MagicMock()
    fake.base_url = "https://api.openai.com/v1/"
    fake.with_options.return_value = fake
    secret = "DP0001은 틀린 주장입니다."
    fake.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"text": secret})))],
        usage=MagicMock(prompt_tokens=10, completion_tokens=5))
    with ai_client(fake):
        out = c.post("/api/justification", json=_body(c)).json()
    assert fake.chat.completions.create.called                       # 가상 시연 데이터라 AI를 부른다
    assert out["method"] == "template" and out["fallback_reason"].startswith("verify:")
    dumped = json.dumps(out, ensure_ascii=False)
    assert secret not in dumped and "llm_text" not in out             # 탈락한 AI 문장은 어디에도 없다
    assert out["verification"]["ok"] is False and out["verification"]["rules"]


def test_real_data_is_not_sent_to_an_external_llm(jc, monkeypatch, ai_client):
    c, app = jc
    fake = MagicMock()
    fake.base_url = "https://api.openai.com/v1/"
    monkeypatch.delenv("TEAMWEAVER_REVIEW_ALLOW_EXTERNAL", raising=False)
    monkeypatch.setattr(app.state.dataset, "info", dataclasses.replace(app.state.dataset.info, synthetic=False))
    with ai_client(fake) as used:
        out = c.post("/api/justification", json=_body(c)).json()
    assert used                                                       # 가짜 클라이언트가 정말 들어갔다(막힘이 "클라이언트 없음" 탓이 아니다)
    assert out["fallback_reason"] == "external_blocked" and out["ai_unavailable_reason"] == "external_blocked"
    fake.chat.completions.create.assert_not_called()


def test_errors(jc):
    c, _ = jc
    body = _body(c, ai=False)
    assert c.post("/api/justification", json={**body, "project_id": "NOPE"}).status_code == 404
    absent = [p for p in c.get("/api/meta").json()["projects"] if p["id"] not in {e["project_id"] for e in body["entries"]}]
    if absent:
        assert c.post("/api/justification", json={**body, "project_id": absent[0]["id"]}).status_code == 404
    swap = {"out_person_id": body["entries"][0]["person_id"], "in_person_id": body["entries"][1]["person_id"],
            "project_id": body["project_id"]}
    assert c.post("/api/justification", json={**body, "applied_swaps": [swap]}).status_code == 422
    assert c.post("/api/justification", json={**body, "dataset_version": "x"}).status_code == 409
    # 서명과 다른 명단(사람을 바꿔 끼움)이면 소명을 만들지 않는다(Codex 리뷰 P1)
    tampered = [dict(e) for e in body["entries"]]
    tampered[0]["person_id"] = tampered[1]["person_id"] if tampered[1]["person_id"] != tampered[0]["person_id"] else "DP9999"
    res = c.post("/api/justification", json={**body, "entries": tampered})
    assert res.status_code == 422 and "서명" in res.json()["detail"]
    assert c.post("/api/justification", json={k: v for k, v in body.items() if k != "plan_token"}).status_code == 422


def test_fixture_dataset_has_no_knowledge_graph(client):
    meta = client.get("/api/meta").json()
    from api.plan_token import sign_plan
    from core.optimize.milp import MilpParams
    entries = [{"person_id": meta["people"][0]["id"], "project_id": meta["projects"][0]["id"], "alloc": 1.0}]
    body = {"dataset_version": meta["dataset_version"], "project_id": meta["projects"][0]["id"], "entries": entries,
            "ai": False, "plan_label": "A", "plan_token": sign_plan(meta["dataset_version"], "A", entries, {}, MilpParams())}
    res = client.post("/api/justification", json=body)
    assert res.status_code == 409 and "묶음 데이터" in res.json()["detail"]


def _capture_pdf_payload(monkeypatch, tmp_path):
    import api.routes.report as report_route
    index = tmp_path / "index.html"
    index.write_text("x")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    seen = []

    async def fake_render(payload, origin, timeout_s):
        seen.append(payload)
        return b"%PDF-fake"
    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    return seen


def test_pdf_includes_a_justification_per_staffed_project(jc, monkeypatch, tmp_path):
    c, _ = jc
    seen = _capture_pdf_payload(monkeypatch, tmp_path)
    body = _body(c)
    req = {"plan_label": "A", "entries": body["entries"], "objective": 1.0, "fulfillment": 1.0, "optimization_ratio": 1.0,
           "dataset_version": body["dataset_version"], "include_justifications": True, "plan_token": body["plan_token"]}
    assert c.post("/api/report", json={**req, "plan_token": None}).status_code == 200      # 서명 없으면 소명은 빠진다
    assert seen.pop(0)["justifications"] == []
    assert c.post("/api/report", json=req).status_code == 200
    js = seen[0]["justifications"]
    staffed = {e["project_id"] for e in body["entries"]}
    assert {j["project_id"] for j in js} == staffed and all(j["text"] and j["facts"] for j in js)
    assert all("llm_text" not in j for j in js) and all(j["fallback_reason"] == "no_client" for j in js)
    # 요약도 PDF까지 간다(2026-10-20, Codex: 허용 칸에 없어 버려졌다) -- AI 없으면 규칙 요약
    assert all("summary" in j for j in js) and any(j["summary"] and j["summary_method"] == "rule" for j in js)
    assert c.post("/api/report", json={**req, "include_justifications": False}).status_code == 200
    assert "justifications" not in seen[1] or not seen[1].get("justifications")


def test_pdf_on_fixture_data_notes_that_justifications_need_a_bundle(client, monkeypatch, tmp_path):
    seen = _capture_pdf_payload(monkeypatch, tmp_path)
    req = {"plan_label": "A", "entries": [], "objective": 1.0, "fulfillment": 1.0, "optimization_ratio": 1.0,
           "include_justifications": True}
    assert client.post("/api/report", json=req).status_code == 200
    assert seen[0]["justifications"] == [] and "묶음 데이터" in seen[0]["justifications_note"]


def test_pdf_time_allowance_grows_with_the_number_of_projects():
    from api.routes.report import _justify_allowance
    from api.routes.justification import AI_TIMEOUT_S
    from api.schemas import ReportRequest
    req = lambda n: ReportRequest(plan_label="A", objective=1, fulfillment=1, optimization_ratio=1,
                                  entries=[{"person_id": f"p{i}", "project_id": f"J{i}", "alloc": 1.0} for i in range(n)])
    assert _justify_allowance(req(6)) == AI_TIMEOUT_S + 30 and _justify_allowance(req(30)) == 5 * AI_TIMEOUT_S + 30


def test_ai_falls_back_to_the_template_when_too_many_ai_texts_run(jc, monkeypatch, ai_client):
    # 동시 AI 호출 상한(폴백 리뷰 SHOULD): 상한이 차 있으면 AI를 부르지 않고 정해진 틀로, 이유 busy
    import threading
    import api.routes.justification as jr
    c, _ = jc
    fake = MagicMock()
    fake.base_url = "https://api.openai.com/v1/"
    monkeypatch.setattr(jr, "AI_SLOTS", threading.BoundedSemaphore(1))
    assert jr.AI_SLOTS.acquire(blocking=False)
    with ai_client(fake) as used:
        out = c.post("/api/justification", json=_body(c)).json()
    assert used
    assert out["method"] == "template" and out["fallback_reason"] == "busy"
    fake.chat.completions.create.assert_not_called()
    jr.AI_SLOTS.release()


def test_synthetic_names_replace_ids_with_matching_particles():
    from types import SimpleNamespace
    from api.routes.justification import apply_names, display_names, with_names
    names = {"DP0001": "김민준", "DP0002": "이서아"}
    assert with_names("DP0001은 Java[F3]이며 DP0002은 Spark[F4]입니다.", names) == "김민준은 Java[F3]이며 이서아는 Spark[F4]입니다."
    assert with_names("DP00012는 그대로", names) == "DP00012는 그대로"            # 다른 ID의 앞부분은 건드리지 않는다
    assert with_names("팀장은 DP0001이며", names) == "팀장은 김민준이며"           # '이며'의 '이'는 조사가 아니다
    assert with_names("DP0002로 바꾸고 DP0001으로 둔다", names) == "이서아로 바꾸고 김민준으로 둔다"
    # 받침을 몰라 괄호로 쓴 조사(규칙 설명 폴백 "DP0004(으)로 교체 검토")도 이름 받침에 맞춘다 -- 예전엔 "손하윤(으)로"로 남았다
    assert with_names("DP0001(으)로 교체 검토, DP0002(으)로 바꾼다", names) == "김민준으로 교체 검토, 이서아로 바꾼다"
    assert with_names("DP0001(이)가 맡고 DP0002은(는) 남는다", names) == "김민준이 맡고 이서아는 남는다"
    out = apply_names({"text": "DP0002의 Python[F1]", "addendum": None,
                       "facts": [{"id": "F1", "text": "DP0002 Python", "phrase": "Python", "phrase_full": "DP0002의 Python"}]}, names)
    assert out["text"] == "이서아의 Python[F1]" and out["facts"][0]["phrase_full"] == "이서아의 Python"
    person = lambda i, n: SimpleNamespace(id=i, name=n)
    ds = lambda syn, people: SimpleNamespace(info=SimpleNamespace(synthetic=syn), graph=SimpleNamespace(people=people))
    assert display_names(ds(False, [person("DP0001", "김민준")])) == {}                 # 실데이터는 이름을 쓰지 않는다
    assert display_names(ds(True, [person("DP0001", "가상DP0001")])) == {}             # 자리표시 이름은 바꾸지 않는다
    assert display_names(ds(True, [person("DP0001", "김민준")])) == {"DP0001": "김민준"}


def test_addendum_groups_are_passed_through_with_names():
    from api.routes.justification import apply_names, public_view
    out = public_view({"project_id": "J1", "method": "graphrag", "text": "글",
                       "addendum": "다만 …:\n- DP0001: Java 미달[F2]", "appended_adverse": ["F2"],
                       "addendum_groups": [{"owner": "DP0001", "facts": ["F2"]}], "facts": [], "llm_text": "숨김"})
    assert out["addendum_groups"] == [{"owner": "DP0001", "owner_label": "DP0001", "facts": ["F2"]}] and "llm_text" not in out
    named = apply_names(out, {"DP0001": "김민준"})
    assert named["addendum_groups"][0]["owner_label"] == "김민준" and named["addendum"].endswith("- 김민준: Java 미달[F2]")


def test_skill_map_on_bundle_data(jc):
    c, _ = jc
    body = c.get("/api/skill-map").json()
    assert body["rows"] and body["summary"]["skills"] == len(body["rows"]) and "NOT_CALIBRATED" in body["note"]
    r = next(x for x in body["rows"] if x["practical"] > 0)
    assert len(r["practical_people"]) == r["practical"] and {"id", "name", "credited"} <= set(r["practical_people"][0])
    assert r["practical_credited"] == sum(p["credited"] for p in r["practical_people"])
    assert all(p["name"] and p["id"] not in p["name"] for x in body["rows"] for p in x["practical_people"])   # 시연 묶음에 가상 이름이 들어갔다(claude-a 2026-10-11)
    res = c.get("/api/skill-map", params={"dataset_version": "stale"})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "dataset_changed"


def test_skill_map_needs_bundle_data(client):
    assert client.get("/api/skill-map").status_code == 409


def test_skill_map_counts_only_credit_that_makes_someone_practical():
    from types import SimpleNamespace
    from api.routes.skill_map import build_skill_map
    edges = [{"src": "person:A", "months": 20, "own_months": 0, "implied_from": {"skill": "Spring"}},   # 인정 덕분에 실무 가능
             {"src": "person:B", "months": 30, "own_months": 14, "implied_from": {"skill": "Spring"}},  # 본인만으로도 실무 가능
             {"src": "person:C", "months": 24, "own_months": 24}]
    kg = SimpleNamespace(nodes_of=lambda t: [{"id": "skill:Java", "label": "Java"}], inc=lambda nid, et: edges)
    import core.kg.views as views
    rows = [{"skill": "Java", "practical_people": ["A", "B", "C"], "practical": 3, "proposal_gap": 0, "demand_headcount": 1,
             "scarce": False}]
    ds = SimpleNamespace(knowledge_graph=lambda: kg, info=SimpleNamespace(synthetic=True, version="v"),
                         graph=SimpleNamespace(people=[]))
    orig = views.skill_map
    views.skill_map = lambda k: rows
    try:
        r = build_skill_map(ds)["rows"][0]
    finally:
        views.skill_map = orig
    assert r["practical_credited"] == 1 and [p["credited"] for p in r["practical_people"]] == [True, False, False]


def test_round_trip_with_a_real_optimize_token_and_screen_settings(jc):
    # 폴백 리뷰 NIT(2026-10-11): 화면이 실제로 보내는 설정 전체(PlacementSettings 모양)와 /api/optimize가 낸 서명으로 왕복한다 --
    # MilpParamsIn과 PlacementSettings가 어긋나면 서명이 맞지 않아 이 시험이 잡는다.
    c, _ = jc
    st = c.get("/api/settings").json()
    params = {**st["settings"], "time_limit": 5}
    v = c.get("/api/meta").json()["dataset_version"]
    plan = next(json.loads(line[5:]) for line in c.post("/api/optimize", json={
        "weights": {}, "dataset_version": v, "milp_params": params, "n_alternatives": 0}).text.splitlines()
        if line.startswith("data:") and '"label"' in line)
    project = plan["entries"][0]["project_id"]
    body = {"dataset_version": v, "project_id": project, "entries": plan["entries"], "weights": {}, "milp_params": params,
            "ai": False, "plan_label": plan["label"], "plan_token": plan["plan_token"]}
    res = c.post("/api/justification", json=body)
    assert res.status_code == 200, res.text
    assert c.post("/api/justification", json={**body, "milp_params": {**params, "min_alloc": 0.5}}).status_code == 422


def test_names_leave_bracketed_markers_alone():
    from api.routes.justification import with_names
    names = {"DP0001": "최혜진", "DP0002": "김경민"}
    text = "DP0001은 소통이 좋다[rv:DP0002>DP0001#1:pos]. DP0002의 평가[F3]."
    assert with_names(text, names) == "최혜진은 소통이 좋다[rv:DP0002>DP0001#1:pos]. 김경민의 평가[F3]."


def test_whatif_briefing_shows_names_for_synthetic_data(jc):
    import api.routes.whatif as w
    from types import SimpleNamespace
    ds = SimpleNamespace(info=SimpleNamespace(synthetic=True), graph=SimpleNamespace(
        people=[SimpleNamespace(id="DP0001", name="최혜진"), SimpleNamespace(id="DP0002", name="김경민")]))
    b = {"rationale": "DP0001을 DP0002로 교체[rv:DP0002>DP0001#1:pos]", "risks": ["DP0002는 신규"], "alternatives": [],
         "evidence": [{"source_id": "rv:DP0002>DP0001#1:pos"}]}
    out = w._named(b, ds)
    assert out["rationale"] == "최혜진을 김경민으로 교체[rv:DP0002>DP0001#1:pos]" and out["risks"] == ["김경민은 신규"]
    assert out["evidence"] == b["evidence"]
    real = SimpleNamespace(info=SimpleNamespace(synthetic=False), graph=ds.graph)
    assert w._named(b, real) == b


def test_meta_carries_skill_credit(jc):
    # 사용자 결정(2026-10-11) "인정 경력은 구분해서 표시": /api/meta 사람에 skill_credit(인정분이 경력을 정한 기술만)
    c, app = jc
    people = c.get("/api/meta").json()["people"]
    credited = [p for p in people if p["skill_credit"]]
    assert all("skill_credit" in p for p in people)
    if credited:                                    # 시연 묶음에 하위 기술만 적은 사람이 있으면
        sk, cr = next(iter(credited[0]["skill_credit"].items()))
        assert {"source", "months", "depth", "own_months"} <= set(cr) and cr["months"] > cr["own_months"]
        assert sk in credited[0]["skills"]


def test_evidence_at_a_glance_rides_with_the_justification(jc):
    """근거 한눈에(2026-10-21): 소명 재료의 지식 그래프 근거를 표·그림용으로 -- 사실 목록과 같은 숫자, 평가 극성·인용은 싣지 않는다."""
    c, _ = jc
    body = _body(c, ai=False)
    out = c.post("/api/justification", json=body).json()
    ev = out["evidence"]
    team = sorted({e["person_id"] for e in body["entries"] if e["project_id"] == body["project_id"]})
    assert ev["project_id"] == body["project_id"] and sorted(m["person_id"] for m in ev["members"]) == team
    req_skills = [r["skill"] for r in ev["requirements"]]
    for m in ev["members"]:
        assert [r["skill"] for r in m["requirements"]] == req_skills                # 모든 팀원이 같은 열(요구 기술)
        assert all(r["status"] in ("met", "below", "missing") for r in m["requirements"])
        assert m["name"] and m["name"] != m["person_id"]                               # 가상 데이터는 이름
    for r in ev["requirements"]:                                                       # 사업 줄 = 사람 칸에서 센 충족 수
        assert r["met_by"] == sum(1 for m in ev["members"] for x in m["requirements"] if x["skill"] == r["skill"] and x["status"] == "met")
    assert set(ev["alternatives"]) <= set(team) and all(len(v) <= 1 for v in ev["alternatives"].values())
    # 글의 사실과 같은 재료: 요구 기술 사실(REQ)은 표의 "필요 n명 · 해당자 n명"과 같은 숫자, 대안 사실(ALT)은 대안 막대와 같은 점수 변화
    req_texts = [f["text"] for f in out["facts"] if f["kind"] == "REQ"]
    for r in ev["requirements"]:
        assert any(r["skill"] in t and f"{r['headcount']}명" in t and f"해당자 {r['met_by']}명" in t for t in req_texts), r
    alt_texts = " ".join(f["text"] for f in out["facts"] if f["kind"] == "ALT")
    for rows in ev["alternatives"].values():
        assert f"{rows[0]['delta_total']:+.2f}".lstrip("+") in alt_texts
    dumped = json.dumps(ev, ensure_ascii=False)
    assert "polarity" not in dumped and "quotes" not in dumped and "reviews" not in dumped


def test_evidence_credit_ratio_follows_the_depth():
    """하위 기술 인정 비율 = credit^깊이(Spring Boot→Java 깊이 2면 49%) -- credit만 보내면 70%로 보였다(폴백 리뷰 SHOULD)."""
    from api.justification_evidence import evidence_view
    ev = {"project_id": "J1", "requirements": [{"skill": "Java", "headcount": 1, "min_months": 36, "met_by": 1, "status": "met"}],
          "members": [{"person_id": "P1", "grade": "고급", "skill_fit": 1.0, "same_industry_projects": 0, "same_industry_months": 0,
                       "requirements": [{"skill": "Java", "months": 19, "level": 2, "min_level": 3, "status": "below",
                                         "implied_from": {"skill": "Spring Boot", "months": 40, "depth": 2, "credit": 0.7}}]}],
          "alternatives": {}}
    c = evidence_view(ev, {})["members"][0]["requirements"][0]["credited_from"]
    assert c == {"skill": "Spring Boot", "months": 40, "ratio": 0.49}


def test_evidence_uses_ids_for_real_data(jc, monkeypatch):
    c, app = jc
    monkeypatch.setattr(app.state.dataset, "info", dataclasses.replace(app.state.dataset.info, synthetic=False))
    ev = c.post("/api/justification", json=_body(c, ai=False)).json()["evidence"]
    assert all(m["name"] is None for m in ev["members"])
    assert all(r["name"] is None for rows in ev["alternatives"].values() for r in rows)


def test_a_saved_ai_text_also_gets_the_evidence(jc, monkeypatch):
    """밤 배치·낮에 미리 쓴 AI 글(저장본)을 꺼낼 때도 근거 한눈에가 붙는다 -- 저장본 자체에는 넣지 않는다."""
    import api.routes.justification as j
    c, _ = jc
    monkeypatch.setattr(j, "load_saved", lambda dataset, key: {"project_id": "x", "method": "graphrag", "text": "저장된 글",
                                                                 "facts": [], "addendum": None, "appended_adverse": []})
    out = c.post("/api/justification", json=_body(c)).json()
    assert out["text"] == "저장된 글" and out["evidence"]["members"]


def _skill_map_with(row: dict, synthetic: bool = False, names: dict | None = None) -> dict:
    from types import SimpleNamespace
    from api.routes.skill_map import build_skill_map
    import core.kg.views as views
    kg = SimpleNamespace(nodes_of=lambda t: [{"id": "skill:Java", "label": "Java"}], inc=lambda nid, et: [])
    people = [SimpleNamespace(id=k, name=v) for k, v in (names or {}).items()]
    ds = SimpleNamespace(knowledge_graph=lambda: kg, info=SimpleNamespace(synthetic=synthetic, version="v"),
                         graph=SimpleNamespace(people=people))
    orig = views.skill_map
    views.skill_map = lambda k: [row]
    try:
        return build_skill_map(ds)["rows"][0]
    finally:
        views.skill_map = orig


_BASE_ROW = {"skill": "Java", "practical": 2, "proposal_gap": 0, "demand_headcount": 1, "scarce": False,
             "broader": [], "narrower": [{"skill": "Spring", "practical": 5}]}


@pytest.mark.parametrize("shape", ["dicts", "dicts_person_id", "detail_map", "detail_list"])
def test_skill_map_passes_person_fields_through_in_any_shape(shape):
    """기술 관계 그림(2026-10-21): claude-a에 요청한 사람별 칸(비어 있음·지금 사업·숙련·인정 출처)과 계보가 오면 화면 모양으로 --
    사람 목록이 사전이든(`id`·`person_id`), 칸이 따로 오든 받는다. 인정 비율은 credit^깊이."""
    fields = {"A": {"months": 40, "level": 3, "expert": True, "free": True, "on": []},
              "B": {"months": 20, "level": 2, "expert": False, "free": False, "on": "J001",
                    "credited_from": {"skill": "Spring Boot", "months": 41, "depth": 2, "credit": 0.7}}}
    key = "person_id" if shape == "dicts_person_id" else "id"
    row = {**_BASE_ROW, "practical_people": [{key: k, **v} for k, v in fields.items()]} if shape.startswith("dicts") else \
          {**_BASE_ROW, "practical_people": list(fields), "practical_detail": fields if shape == "detail_map"
           else [{"id": k, **v} for k, v in fields.items()]}
    r = _skill_map_with(row)
    a, b = r["practical_people"]
    assert a == {"id": "A", "name": None, "credited": False, **fields["A"]}
    assert b["free"] is False and b["on"] == ["J001"]                                  # 문자열 하나도 목록으로
    assert b["credited_from"] == [{"skill": "Spring Boot", "months": 41, "ratio": 0.49}]
    assert "practical_detail" not in r and r["narrower"] == [{"skill": "Spring", "practical": 5}]


def test_skill_map_person_fields_fallbacks_and_names():
    """칸이 없는 사람은 예전 모양(이름·인정만), 인정 출처가 문자열·None이어도 받는다, 이름은 가상 데이터만(K5)."""
    row = {**_BASE_ROW, "practical_people": ["A", "B", "C"],
           "practical_detail": {"A": {"free": True, "credited_from": None}, "B": {"free": False, "credited_from": "Spring"}}}
    r = _skill_map_with(row, synthetic=True, names={"A": "김민준", "B": "이서연", "C": "박지훈"})
    a, b, c = r["practical_people"]
    assert a["name"] == "김민준" and a["credited_from"] is None
    assert b["credited_from"] == [{"skill": "Spring", "months": None, "ratio": None}]
    assert c == {"id": "C", "name": "박지훈", "credited": False}
    real = _skill_map_with(row, synthetic=False, names={"A": "김민준"})
    assert all(p["name"] is None for p in real["practical_people"])
