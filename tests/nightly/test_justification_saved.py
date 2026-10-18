"""소명 글 저장본(api/justification_cache.py)과 밤 소명 단계: 같은 조건이면 AI를 다시 부르지 않는다, 조건이 바뀌면 키가 바뀐다,
AI 글만 저장한다, 밤에 쓴 글을 화면이 쓴다, AI 호출 상한, 실데이터는 회사 밖 AI로 보내지 않는다."""
import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import api.justification_cache as jc
import api.nightly as nightly
from core.ingest.org_profile import generate_org_bundle
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry

from tests.nightly.helpers import current_entries, run_now, web_settings


# --- 키·저장소 ------------------------------------------------------------------------------------------------------

def _e(p, j, a=0.5, monthly=None):
    return AssignEntry(person_id=p, project_id=j, alloc=a, monthly_alloc=monthly)


def test_key_changes_with_every_input_that_shapes_the_text():
    base = dict(version="v1", project_id="J1", entries=[_e("P1", "J1"), _e("P2", "J2")], weights={},
                params=MilpParams(), ai={"model": "m", "base_url": "u", "effort": "low"}, inputs="f1")
    k = jc.key_for(**base)
    assert k == jc.key_for(**{**base, "entries": list(reversed(base["entries"]))})      # 순서는 상관없다
    from api.schemas import EntryIn
    assert k == jc.key_for(**{**base, "entries": [EntryIn(person_id="P1", project_id="J1", alloc=0.5),
                                                  EntryIn(person_id="P2", project_id="J2", alloc=0.5)]})
    changed = [{"version": "v2"}, {"project_id": "J2"}, {"entries": [_e("P1", "J1", 0.6), _e("P2", "J2")]},
               {"entries": [_e("P1", "J1"), _e("P2", "J2", 0.5, {0: 0.5, 1: 0.4})]},
               {"weights": {"Java": 3}}, {"params": MilpParams(min_alloc=0.4)},
               {"ai": {"model": "m2", "base_url": "u", "effort": "low"}}, {"ai": {"model": "m", "base_url": "u", "effort": "high"}},
               {"inputs": "f2"}]
    for ch in changed:
        assert jc.key_for(**{**base, **ch}) != k, ch
    assert len(jc.code_fingerprint()) == 16


def test_input_fingerprint_ignores_set_order():
    from core.kg.justify import JustificationInput
    a = JustificationInput(project_id="J1", project="사업", team=["P1"], facts=[], known_ids={"P1", "P2", "J1"})
    b = JustificationInput(project_id="J1", project="사업", team=["P1"], facts=[], known_ids={"J1", "P2", "P1"})
    assert jc.input_fingerprint(a) == jc.input_fingerprint(b)
    assert jc.input_fingerprint(a) != jc.input_fingerprint(JustificationInput(project_id="J1", project="사업2", team=["P1"],
                                                                             facts=[]))


def test_code_change_changes_the_key(monkeypatch):
    base = dict(version="v1", project_id="J1", entries=[_e("P1", "J1")], weights={}, params=MilpParams(), ai={})
    k = jc.key_for(**base)
    monkeypatch.setattr(jc, "_code_fp", "0" * 16)
    assert jc.key_for(**base) != k


def test_store_keeps_only_ai_texts_and_only_the_current_version(tmp_path, monkeypatch):
    store = jc.JustificationCache(tmp_path)
    assert store.put("v1", "k1", {"method": "template", "text": "틀"}, "live") is False
    assert store.put("v1", "k1", {"method": "graphrag", "text": "AI 글"}, "nightly") is True
    got = store.get("v1", "k1")
    assert got["text"] == "AI 글" and got["prepared_by"] == "nightly" and got["prepared_at"]
    assert store.get("v1", "k2") is None and store.get("v2", "k1") is None
    store.put("v2", "k9", {"method": "graphrag", "text": "새 데이터"}, "live")
    assert store.get("v1", "k1") is not None                  # 저장은 다른 버전을 지우지 않는다(늦게 끝난 옛 요청 보호)
    assert store.prune("v2") == 1                             # 옛 데이터의 글은 prune이 지운다(부팅·밤 실행 시작)
    assert store.get("v1", "k1") is None
    assert [p.name for p in (tmp_path / jc.DIRNAME).glob("*.json")] == ["v2.json"]
    monkeypatch.setattr(jc, "MAX_PER_VERSION", 2)
    for i in range(4):
        store.put("v2", f"x{i}", {"method": "graphrag", "text": str(i)}, "live")
        time.sleep(1.05 if i == 0 else 0)                     # 시각(초 단위)이 다른 것부터 버린다
    assert store.count("v2") == 2


# --- 화면 라우트와 밤 단계 -------------------------------------------------------------------------------------------

class FakeClient:
    base_url = "https://api.openai.com/v1/"

    def with_options(self, **kw):
        return self

    class models:                                              # noqa: N801 -- SDK 모양
        @staticmethod
        def retrieve(model):
            return {"id": model}


@pytest.fixture
def fake_ai(monkeypatch):
    """AI 클라이언트·글 생성을 가짜로(실제 호출 없음). calls = AI로 쓴 사업 목록."""
    import api.deps as deps
    import api.rag.justification as rag
    from api.main import app
    from api.routes import justification as jr
    fake = FakeClient()
    calls = []

    def gen(client, model, inp, **kw):
        if client is None:
            return {"method": "template", "text": "정해진 틀", "facts": [], "verification": None,
                    "fallback_reason": "no_client", "usage": None}
        calls.append(model)
        return {"method": "graphrag", "text": f"AI 글 {len(calls)}", "facts": [], "verification": {"ok": True, "violations": []},
                "fallback_reason": None, "usage": {"model": model, "cost_usd": 0.001, "latency_s": 0.1}}
    monkeypatch.setattr(rag, "generate_justification", gen)
    monkeypatch.setattr(deps, "get_openai_client_or_none", lambda: fake)
    app.dependency_overrides[jr.get_openai_client_or_none] = lambda: fake
    yield calls
    app.dependency_overrides[jr.get_openai_client_or_none] = lambda: None


@pytest.fixture(scope="module")
def op_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo-j")
    return root, generate_org_bundle(root / "org-n100-operating", 100, seed=11, scenario="operating")


@pytest.fixture
def boot_op(op_bundle, monkeypatch):
    root, b = op_bundle
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(b))


def _request(c, app, project_id=None):
    from api.plan_token import sign_plan
    from api.schemas import MilpParamsIn
    version = c.get("/api/datasets/active").json()["version"]
    entries = [e.model_dump() for e in current_entries(app.state.dataset)]
    web = web_settings(c)
    token = sign_plan(version, "A", entries, {}, MilpParamsIn(**web).to_milp_params())
    pid = project_id or entries[0]["project_id"]
    return {"dataset_version": version, "project_id": pid, "entries": entries, "weights": {}, "milp_params": web,
            "ai": True, "plan_label": "A", "plan_token": token}


def test_route_reuses_a_saved_ai_text(boot_op, fake_ai):
    from api.main import app
    with TestClient(app) as c:
        body = _request(c, app)
        first = c.post("/api/justification", json=body).json()
        assert first["method"] == "graphrag" and first.get("prepared_at") is None and len(fake_ai) == 1
        again = c.post("/api/justification", json=body).json()
        assert again["text"] == first["text"] and again["prepared_by"] == "live" and again["prepared_at"]
        assert len(fake_ai) == 1                                     # AI를 다시 부르지 않았다
        tmpl = c.post("/api/justification", json={**body, "ai": False}).json()
        assert tmpl["method"] == "template" and len(fake_ai) == 1     # 정해진 틀 요청은 저장본과 무관
        other = {**body, "milp_params": {**body["milp_params"], "min_alloc": 0.4}}
        from api.plan_token import sign_plan
        from api.schemas import MilpParamsIn
        other["plan_token"] = sign_plan(body["dataset_version"], "A", body["entries"], {},
                                        MilpParamsIn(**other["milp_params"]).to_milp_params())
        c.post("/api/justification", json=other)
        assert len(fake_ai) == 2                                     # 설정이 다르면 새로 쓴다


def test_nightly_writes_texts_the_morning_screen_uses(boot_op, fake_ai, fake_solvers, fast_checks):
    from api.main import app
    with TestClient(app) as c:
        report = run_now(app)
        js = report["justifications"]
        n_projects = len({e.project_id for e in current_entries(app.state.dataset)})
        assert js["plans"] == ["A"] and js["prepared"] == n_projects == js["ai_calls"] and js["fallback"] == 0
        assert js["cost_usd"] == pytest.approx(0.001 * n_projects)
        assert [ch for ch in report["checks"] if ch["id"] == "ai"][0]["status"] == "ok"
        before = len(fake_ai)
        got = c.post("/api/justification", json=_request(c, app)).json()
        assert got["prepared_by"] == "nightly" and len(fake_ai) == before          # 아침 화면은 AI를 기다리지 않는다
        again = run_now(app)                                                       # 다음 밤: 이미 쓴 글은 건너뛴다
        assert again["justifications"]["already"] == n_projects and again["justifications"]["ai_calls"] == 0


def test_nightly_ai_call_cap(boot_op, fake_ai, fake_solvers, fast_checks, monkeypatch):
    monkeypatch.setenv(nightly.AI_MAX_ENV, "2")
    from api.main import app
    with TestClient(app):
        js = run_now(app)["justifications"]
    assert js["ai_calls"] == 2 and js["prepared"] == 2 and "상한" in js["stopped_reason"]


def test_real_data_is_not_sent_to_an_outside_ai(boot_op, fake_ai, fake_solvers, fast_checks):
    from api.main import app
    with TestClient(app):
        ds = app.state.dataset
        ds.info = replace(ds.info, synthetic=False)
        report = run_now(app)
    js = report["justifications"]
    assert js["ai_calls"] == 0 and "회사 밖" in js["skipped_reason"] and fake_ai == []
    assert [ch for ch in report["checks"] if ch["id"] == "ai"][0]["status"] == "skipped"


def test_pdf_justifications_use_saved_texts(boot_op, fake_ai):
    from api.main import app
    from api.routes.justification import justify_projects
    from api.schemas import MilpParamsIn
    with TestClient(app) as c:
        body = _request(c, app)
        c.post("/api/justification", json=body)
        assert len(fake_ai) == 1
        outs = justify_projects(app.state.dataset, [AssignEntry(**e) for e in body["entries"]], {},
                                MilpParamsIn(**body["milp_params"]), FakeClient())
        first = [o for o in outs if o["project_id"] == body["project_id"] or o.get("prepared_by")]
        assert any(o.get("prepared_by") == "live" for o in first)
        n_projects = len({e["project_id"] for e in body["entries"]})
        assert len(fake_ai) == 1 + (n_projects - 1)                   # 저장된 한 사업은 AI를 부르지 않았다
        json.dumps(outs)


def test_a_change_in_the_facts_changes_the_key(boot_op, fake_ai, monkeypatch):
    """평가기·점수 코드가 바뀌어 소명 재료가 달라지면 저장본을 쓰지 않는다(Codex 리뷰 2026-10-18)."""
    from api.main import app
    from api.routes import justification as jr
    with TestClient(app) as c:
        body = _request(c, app)
        c.post("/api/justification", json=body)
        assert len(fake_ai) == 1
        real = jr.build_input

        def changed(*a, **kw):
            inp = real(*a, **kw)
            inp.facts = inp.facts[:-1]                               # 평가 결과가 달라진 것처럼
            return inp
        monkeypatch.setattr(jr, "build_input", changed)
        got = c.post("/api/justification", json=body).json()
        assert got.get("prepared_by") is None and len(fake_ai) == 2


def test_the_ai_slot_wait_rechecks_stop(boot_op, fake_ai, fake_solvers, fast_checks):
    """AI 자리를 기다리는 동안 멈추면 AI를 부르지 않는다(Codex 리뷰 2026-10-18)."""
    from api.main import app
    from api.routes import justification as jr
    held = 0
    while jr.AI_SLOTS.acquire(blocking=False):
        held += 1
    try:
        with TestClient(app):
            n = app.state.nightly
            assert n.start_run("manual", time.time() + 600)
            for _ in range(200):
                if (n.progress or {}).get("phase") == "소명 글(안 A)":
                    break
                time.sleep(0.05)
            time.sleep(0.3)
            n.request_stop("finish")
            for _ in range(held):
                jr.AI_SLOTS.release()
            held = 0
            n._run_thread.join(60)
            report = n.store.read("report.json")
    finally:
        for _ in range(held):
            jr.AI_SLOTS.release()
    assert fake_ai == [] and report["stopped"] is True


def test_a_retired_dataset_does_not_save(boot_op, fake_ai):
    """데이터가 바뀐 뒤 끝난 요청은 저장하지 않는다(Codex 리뷰 2026-10-18)."""
    from api.main import app
    from api.routes.justification import save_ai_text
    with TestClient(app):
        ds = app.state.dataset
        ds._retired = True
        try:
            assert save_ai_text(ds, "k", {"method": "graphrag", "text": "x"}, "live") is False
        finally:
            ds._retired = False
        assert save_ai_text(ds, "k", {"method": "graphrag", "text": "x"}, "live") is True


def test_boot_prunes_texts_of_other_data(boot_op, _isolated):
    from api.main import app
    jc.JustificationCache().put("old-version", "k", {"method": "graphrag", "text": "옛 글"}, "live")
    with TestClient(app):
        assert jc.JustificationCache().get("old-version", "k") is None
