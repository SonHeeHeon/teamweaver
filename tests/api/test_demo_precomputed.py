"""Precomputed demo results (demo extension E, 2026-10-06): used only when the data version, the server settings and a
re-score by the current evaluator all match; shown as precomputed; fresh=true solves again."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.demo_precomputed import FORMAT, load_precomputed, precomputed_path
from api.settings import PlacementSettings
from core.evaluate.plan_eval import evaluate_plan
from core.ingest.org_profile import generate_org_bundle
from core.optimize.types import AssignEntry, PlanAssignment
from core.scoring.engine import ScoringEngine

JSON = {"Content-Type": "application/json"}


def _plan(graph, params, label, entries):
    eng = ScoringEngine(graph)
    ev = evaluate_plan(graph, eng.skill_matrix({}), eng.synergy_matrix(), params, entries)
    return PlanAssignment(entries=entries, objective=ev.objective.total, unfilled=[], violations=[],
                          time_limited=True, label=label)


def _write(bundle, active, params, plans, *, version=None, operating=None, eval_objectives=None, stop_reason=None):
    """eval_objective = what the evaluator scored at precompute time (rehearsal.precompute_demo records it)."""
    path = precomputed_path(bundle)
    path.parent.mkdir(exist_ok=True)
    evals = eval_objectives or [p.objective for p in plans]
    path.write_text(json.dumps({
        "format": FORMAT, "preset": bundle.name, "dataset_version": version or active.info.version,
        "computed_at": "2026-10-06T12:00:00+00:00",
        "optimize": {"weights": {}, "n_alternatives": 3, "milp_params": params.model_dump(), "stop_reason": stop_reason,
                     "plans": [{**p.model_dump(), "eval_objective": e} for p, e in zip(plans, evals)]},
        "operating": operating}), "utf-8")
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A planning and an operating bundle, built the way the server builds them (LLM judging stubbed)."""
    import api.review_judge as rj
    from api.demos import build_demo
    root = tmp_path_factory.mktemp("demo")
    plan_b = generate_org_bundle(root / "org-n100", 100, seed=11)
    op_b = generate_org_bundle(root / "org-n100-operating", 100, seed=11, scenario="operating")
    mp = pytest.MonkeyPatch()
    mp.setattr(rj, "judge_reviews", lambda ds, parsed, **kw: list(parsed))
    try:
        actives = {b.name: build_demo(b) for b in (plan_b, op_b)}
    finally:
        mp.undo()
    return root, actives


def _params(active):
    return PlacementSettings().to_milp_params(n_people=len(active.graph.people))


def test_matching_file_is_loaded_and_each_mismatch_is_refused(built):
    root, actives = built
    bundle, active = root / "org-n100", actives["org-n100"]
    params = _params(active)
    first = active.current[:3]
    plans = [_plan(active.graph, params, "A", [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc)
                                              for c in first]),
             _plan(active.graph, params, "B", [])]
    _write(bundle, active, params, plans)
    pre = load_precomputed(bundle, active, params)
    assert pre.skipped == [] and [p.label for p in pre.plans] == ["A", "B"] and pre.optimize_key

    _write(bundle, active, params, plans, version="other")
    assert "데이터셋 버전" in load_precomputed(bundle, active, params).skipped[0]
    _write(bundle, active, params, plans)
    other = params.model_copy(update={"time_limit": params.time_limit + 1})
    pre = load_precomputed(bundle, active, other)
    assert pre.plans == [] and "설정" in pre.skipped[0]
    _write(bundle, active, params, plans, eval_objectives=[plans[0].objective + 1.0, plans[1].objective])
    pre = load_precomputed(bundle, active, params)                  # the evaluator changed since: refuse
    assert pre.plans == [] and "다시 채점" in pre.skipped[0]
    precomputed_path(bundle).write_text('{"format": 1, "dataset_version": "%s", "optimize": {"weights": {}, '
                                        '"milp_params": %s, "plans": [{"entries": 3}]}}'
                                        % (active.info.version, json.dumps(params.model_dump())), "utf-8")
    pre = load_precomputed(bundle, active, params)                  # malformed record: that part is dropped, no crash
    assert pre.plans == [] and "모양" in pre.skipped[0]
    precomputed_path(bundle).unlink()
    assert load_precomputed(bundle, active, params) is None


def test_a_solver_objective_that_differs_from_the_rescore_is_still_accepted(built):
    """Review MUST: the solver scores the raw allocations, the evaluator the displayed (floored) ones, so real plans
    differ by ~0.01 (measured 6.999558 vs 6.992778). The check compares re-scores, not the solver objective."""
    root, actives = built
    bundle, active = root / "org-n100", actives["org-n100"]
    params = _params(active)
    plan = _plan(active.graph, params, "A", [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc)
                                             for c in active.current])
    solver = plan.model_copy(update={"objective": plan.objective + 0.0068, "termination": "time_limit_incumbent",
                                     "best_bound": plan.objective + 1.0, "gap_used": 0.05})
    _write(bundle, active, params, [solver], eval_objectives=[plan.objective], stop_reason="time_limit")
    pre = load_precomputed(bundle, active, params)
    assert pre.skipped == [] and pre.plans[0].objective == solver.objective and pre.stop_reason == "time_limit"
    loaded = pre.plans[0]                      # the confidence badge fields survive (rehearsal 2026-10-07: "증명 정보 없음")
    assert (loaded.termination, loaded.best_bound, loaded.gap_used) == ("time_limit_incumbent", plan.objective + 1.0, 0.05)
    precomputed_path(bundle).unlink()


def test_operating_rows_are_rescored(built):
    from core.evaluate.operating import _row
    root, actives = built
    bundle, active = root / "org-n100-operating", actives["org-n100-operating"]
    params = _params(active)
    eng = ScoringEngine(active.graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in active.current]
    row = {**_row(active.graph, S, C, params, active.current, entries, []), "k": 0, "accepted": True}
    op = {"ks": [0], "milp_params": params.model_dump(), "rows": [row]}
    _write(bundle, active, params, [], operating=op)
    pre = load_precomputed(bundle, active, params)
    assert pre.skipped == ["안 A~D: 기록에 안이 없다"] and pre.operating["rows"][0]["k"] == 0
    _write(bundle, active, params, [], operating={**op, "rows": [{**row, "objective": row["objective"] + 5}]})
    pre = load_precomputed(bundle, active, params)
    assert pre.operating is None and "K=0" in pre.skipped[-1]


def test_server_serves_precomputed_plans_and_fresh_solves_again(built, monkeypatch):
    root, actives = built
    bundle, active = root / "org-n100", actives["org-n100"]
    params = _params(active)
    plan = _plan(active.graph, params, "A", [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc)
                                              for c in active.current])
    _write(bundle, active, params, [plan])
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(bundle))
    import api.routes.optimize as opt_route
    calls = []

    def fake_stream(graph, S, C, p, n, outcome):
        calls.append(p)
        yield plan.model_copy(update={"label": "A", "time_limited": False})
    monkeypatch.setattr(opt_route, "generate_plans_streaming", fake_stream)
    from api.main import app
    with TestClient(app) as c:
        info = c.get("/api/datasets/active").json()
        assert info["precomputed"]["plans"] == 1 and info["precomputed"]["skipped"] == []
        s = c.get("/api/settings").json()
        web = {**s["settings"], "time_limit": s["effective_time_limit"]}     # what the web sends (effectiveSettings)
        body = {"weights": {}, "milp_params": web, "n_alternatives": 3, "dataset_version": info["version"]}
        events = _sse(c.post("/api/optimize", json=body))
        plans = [d for e, d in events if e == "plan"]
        assert len(plans) == 1 and plans[0]["cached"] and plans[0]["precomputed_at"] == "2026-10-06T12:00:00+00:00"
        assert calls == []
        events = _sse(c.post("/api/optimize", json={**body, "fresh": True}))
        plans = [d for e, d in events if e == "plan"]
        assert len(calls) == 1 and not plans[0]["cached"] and plans[0]["precomputed_at"] is None
        # the live result was cacheable and replaced the precomputed one: no longer labelled precomputed
        plans = [d for e, d in _sse(c.post("/api/optimize", json=body)) if e == "plan"]
        assert plans[0]["cached"] and plans[0]["precomputed_at"] is None


def _sse(res):
    out, event = [], None
    for line in res.text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line.split(":", 1)[1])))
    return out


def test_operating_compare_serves_precomputed_rows_and_fresh_recomputes(built, monkeypatch):
    """/api/operating/compare (claude-b's route) streams the validated precomputed K rows; fresh=true solves again."""
    from core.evaluate.operating import _row
    root, actives = built
    bundle, active = root / "org-n100-operating", actives["org-n100-operating"]
    params = _params(active)
    eng = ScoringEngine(active.graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in active.current]
    rows = [{**_row(active.graph, S, C, params, active.current, entries, []), "k": k, "accepted": True} for k in (0, 1, 2, 3)]
    _write(bundle, active, params, [], operating={"ks": [0, 1, 2, 3], "milp_params": params.model_dump(), "rows": rows})
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(bundle))
    import core.evaluate.operating as op
    calls = []
    monkeypatch.setattr(op, "compare_move_budgets", lambda *a, **kw: calls.append(1) or [rows[0]])
    from api.main import app
    with TestClient(app) as c:
        info = c.get("/api/datasets/active").json()
        assert info["precomputed"]["operating"] is True
        s = c.get("/api/settings").json()
        body = {"milp_params": {**s["settings"], "time_limit": s["effective_time_limit"]},
                "dataset_version": info["version"], "ks": [0, 1, 2, 3]}
        got = [d for e, d in _sse(c.post("/api/operating/compare", json=body)) if e == "row"]
        assert [r["k"] for r in got] == [0, 1, 2, 3] and all(r["precomputed_at"] for r in got) and calls == []
        got = [d for e, d in _sse(c.post("/api/operating/compare", json={**body, "fresh": True})) if e == "row"]
        assert calls == [1] and "precomputed_at" not in got[0]
        skill = next(iter(active.graph.people[0].skills))          # review MUST: rows were made with weights {}
        got = [d for e, d in _sse(c.post("/api/operating/compare", json={**body, "weights": {skill: 3}})) if e == "row"]
        assert calls == [1, 1] and "precomputed_at" not in got[0]
    precomputed_path(bundle).unlink()



def test_boot_skips_the_warm_up_when_the_precomputed_plans_cover_it(built, monkeypatch):
    """Review SHOULD: with a validated precomputed entry under the warm-up key the boot does not solve again."""
    root, actives = built
    bundle, active = root / "org-n100", actives["org-n100"]
    params = _params(active)
    plan = _plan(active.graph, params, "A", [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc)
                                              for c in active.current])
    _write(bundle, active, params, [plan])
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(bundle))
    monkeypatch.setenv("TEAMWEAVER_SKIP_WARM", "0")
    import api.main as main_mod
    ran = []
    monkeypatch.setattr(main_mod, "generate_plans", lambda *a, **kw: ran.append(1) or [])   # boot swallows errors: count
    with TestClient(main_mod.app) as c:
        assert c.get("/api/datasets/active").json()["precomputed"]["plans"] == 1
    assert ran == []
    precomputed_path(bundle).unlink()


def test_every_plan_field_survives_the_round_trip(built):
    """Review SHOULD: rebuilding plans field by field dropped new fields; every PlanAssignment field must round-trip."""
    root, actives = built
    bundle, active = root / "org-n100", actives["org-n100"]
    params = _params(active)
    plan = _plan(active.graph, params, "B", [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc)
                                             for c in active.current[:4]])
    full = plan.model_copy(update={"termination": "Optimal", "best_bound": plan.objective + 0.5, "gap_used": 0.01,
                                   "unfilled": ["J001:중급:1명 미충원"], "time_limited": False})
    _write(bundle, active, params, [full])
    loaded = load_precomputed(bundle, active, params).plans[0]
    assert loaded.model_dump() == full.model_dump()
    legacy = {k: v for k, v in full.model_dump().items() if k not in ("termination", "best_bound", "gap_used")}
    path = precomputed_path(bundle)
    data = json.loads(path.read_text("utf-8"))
    data["optimize"]["plans"] = [{**legacy, "eval_objective": full.objective}]
    path.write_text(json.dumps(data), "utf-8")
    old = load_precomputed(bundle, active, params).plans[0]          # a record made before those fields existed
    assert old.termination is None and old.best_bound is None and old.label == "B"
    path.unlink()
