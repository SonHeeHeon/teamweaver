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
