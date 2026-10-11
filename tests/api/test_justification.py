"""인사팀 소명 글 API(claude-a 계약 docs/requests/2026-10-09-hr-justification.md).

계산·검사의 정확성은 claude-a 시험(tests/test_justify*.py)이 본다. 여기서는 배선만: 명단을 서버가 정하는지, AI 정책
(키 없음·실데이터 외부 전송 불가 → 템플릿), 화면에 내보내지 않을 것(llm_text·탈락 문장), 409·404·422."""
import dataclasses
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.deps import get_openai_client_or_none
from core.ingest.org_profile import generate_org_bundle


@pytest.fixture(scope="module")
def bundle_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("just")
    generate_org_bundle(root / "org-n100-operating", 100, seed=11, scenario="operating")
    return root


@pytest.fixture
def jc(bundle_root, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(bundle_root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(bundle_root / "org-n100-operating"))
    from api.main import app
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    with TestClient(app) as c:
        yield c, app
    app.dependency_overrides.pop(get_openai_client_or_none, None)


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


def test_ai_text_is_never_sent_and_rejected_sentences_stay_hidden(jc):
    c, app = jc
    fake = MagicMock()
    fake.base_url = "https://api.openai.com/v1/"
    fake.with_options.return_value = fake
    secret = "DP0001은 틀린 주장입니다."
    fake.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"text": secret})))],
        usage=MagicMock(prompt_tokens=10, completion_tokens=5))
    app.dependency_overrides[get_openai_client_or_none] = lambda: fake
    out = c.post("/api/justification", json=_body(c)).json()
    assert fake.chat.completions.create.called                       # 가상 시연 데이터라 AI를 부른다
    assert out["method"] == "template" and out["fallback_reason"].startswith("verify:")
    dumped = json.dumps(out, ensure_ascii=False)
    assert secret not in dumped and "llm_text" not in out             # 탈락한 AI 문장은 어디에도 없다
    assert out["verification"]["ok"] is False and out["verification"]["rules"]


def test_real_data_is_not_sent_to_an_external_llm(jc, monkeypatch):
    c, app = jc
    fake = MagicMock()
    fake.base_url = "https://api.openai.com/v1/"
    app.dependency_overrides[get_openai_client_or_none] = lambda: fake
    monkeypatch.delenv("TEAMWEAVER_REVIEW_ALLOW_EXTERNAL", raising=False)
    monkeypatch.setattr(app.state.dataset, "info", dataclasses.replace(app.state.dataset.info, synthetic=False))
    out = c.post("/api/justification", json=_body(c)).json()
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


def test_ai_falls_back_to_the_template_when_too_many_ai_texts_run(jc, monkeypatch):
    # 동시 AI 호출 상한(폴백 리뷰 SHOULD): 상한이 차 있으면 AI를 부르지 않고 정해진 틀로, 이유 busy
    import threading
    import api.routes.justification as jr
    c, app = jc
    fake = MagicMock()
    fake.base_url = "https://api.openai.com/v1/"
    app.dependency_overrides[get_openai_client_or_none] = lambda: fake
    monkeypatch.setattr(jr, "AI_SLOTS", threading.BoundedSemaphore(1))
    assert jr.AI_SLOTS.acquire(blocking=False)
    out = c.post("/api/justification", json=_body(c)).json()
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
    assert all(p["name"] is None for x in body["rows"] for p in x["practical_people"])   # 시연 이름이 자리표시라 아직 이름 없음
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
