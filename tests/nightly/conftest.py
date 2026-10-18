"""야간 배치 시험 공용 준비(2026-10-18, claude-b). tests/api/conftest.py와 같은 격리를 한다 -- 사용자 홈(~/.teamweaver)을
읽거나 쓰지 않고, 실제 LLM을 부르지 않고, 예약 실행기는 끈다(시험은 수동 실행으로만 돌린다)."""
import pytest


@pytest.fixture(scope="session", autouse=True)
def _session_private_dirs(tmp_path_factory):
    import os
    base = tmp_path_factory.mktemp("teamweaver-nightly-session")
    keys = ("TEAMWEAVER_DATA_DIR", "TEAMWEAVER_SETTINGS_PATH", "TEAMWEAVER_NIGHTLY")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["TEAMWEAVER_DATA_DIR"] = str(base / "data")
    os.environ["TEAMWEAVER_SETTINGS_PATH"] = str(base / "settings.json")
    os.environ["TEAMWEAVER_NIGHTLY"] = "off"
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    import api.review_judge as rj
    monkeypatch.setenv("TEAMWEAVER_SKIP_WARM", "1")
    monkeypatch.setenv("TEAMWEAVER_NIGHTLY", "off")
    monkeypatch.setenv("TEAMWEAVER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TEAMWEAVER_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for k in ("TEAMWEAVER_PLAN_SECRET", "TEAMWEAVER_ADMIN_PASSWORD", "TEAMWEAVER_ADMIN_PASSWORD_HASH",
              "TEAMWEAVER_ADMIN_TOKEN", "TEAMWEAVER_DEMO_BUNDLE", "TEAMWEAVER_DEMO_DIR",
              "TEAMWEAVER_NIGHTLY_WINDOW", "TEAMWEAVER_NIGHTLY_AI_MAX_CALLS",
              "TEAMWEAVER_REVIEW_ALLOW_EXTERNAL", "TEAMWEAVER_REVIEW_ONPREM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(rj.BASE_URL_ENV, "http://llm.invalid/v1")
    monkeypatch.delenv(rj.REASONING_ENV, raising=False)
    monkeypatch.setattr(rj, "judge_reviews", lambda ds, parsed, **kw: list(parsed))
    # 설명 글 AI도 부르지 않는다: lifespan의 load_env는 이미 있는 값을 덮지 않으므로(setdefault) 닿지 않는 주소·가짜 키를 먼저 둔다.
    # 밤 실행은 api.deps.get_openai_client_or_none을 직접 부르고, 화면 라우트는 의존성으로 부른다 -- 둘 다 기본 None.
    # AI가 필요한 시험은 fake_ai 픽스처로 바꾼다(2026-10-18: 이 막음이 없어 시험이 실제 외부 AI를 42번 불렀다).
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://llm.invalid/v1")
    import api.deps as deps
    original = deps.get_openai_client_or_none
    monkeypatch.setattr(deps, "get_openai_client_or_none", lambda: None)
    from api.main import app
    app.dependency_overrides[original] = lambda: None
    from api.admin import THROTTLE
    THROTTLE._state.clear()
    THROTTLE._inflight.clear()
    yield tmp_path
    app.dependency_overrides.pop(original, None)


@pytest.fixture
def fast_checks(monkeypatch):
    """모델 검사(1~2초)·PDF 브라우저는 따로 시험한다 -- 여기선 바로 끝낸다."""
    import api.nightly as nightly
    monkeypatch.setattr(nightly._Run, "check_model", lambda self: self.check("model", "모델 검사", "ok", "stub"))
    monkeypatch.setattr(nightly._Run, "check_pdf", lambda self: self.check("pdf", "PDF 준비", "ok", "stub"))


@pytest.fixture
def fake_solvers(monkeypatch):
    """밤 풀이를 실제 솔버 없이: 안 A = 현재 배치(위반 없음), 운영 비교 = K마다 현재 배치. 받은 시간 한도를 기록한다."""
    import core.evaluate.operating as op_mod
    import core.optimize.alternatives as alt
    from core.optimize.types import PlanAssignment
    from tests.nightly.helpers import current_entries, evaluated
    seen = {"plans": [], "operating": []}

    def fake_stream(graph, S, C, params, n, outcome):
        from api.main import app
        active = app.state.dataset
        seen["plans"].append(params.time_limit)
        ev = evaluated(active, params, current_entries(active))
        outcome["time_limited"] = True
        yield PlanAssignment(entries=current_entries(active), objective=ev.objective.total, unfilled=[], violations=[],
                             time_limited=True, termination="time_limit_incumbent",
                             best_bound=ev.objective.total * 1.05, label="A")

    def fake_compare(graph, S, C, params, current, ks=(0, 1, 2, 3), on_row=None):
        from api.main import app
        active = app.state.dataset
        seen["operating"].append(params.time_limit)
        entries = current_entries(active)
        ev = evaluated(active, params, entries)
        return [{"k": k, "accepted": True, "entries": [e.model_dump() for e in entries], "objective": ev.objective.total,
                 "violations": [v.code for v in ev.violations], "termination": "Optimal",
                 "best_bound": ev.objective.total} for k in ks]
    monkeypatch.setattr(alt, "generate_plans_streaming", fake_stream)
    monkeypatch.setattr(op_mod, "compare_move_budgets", fake_compare)
    return seen
