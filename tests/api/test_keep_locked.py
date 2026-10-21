"""연간 계획 고정 선택(2026-10-21 사용자 "고정된 사람은 계속 고정할지 아니면 고정 풀고 그 사람들도 다시 배치를 할지 물어보고"):
`/api/optimize keep_locked`가 모든 안에서 고정 배치를 지키는가, 키·기록·밤 배치가 그 선택을 따라가는가, 확정이 고정을 푸는가."""
import json

import pytest

from api.cache import ResultCache
from api.datasets import build_active
from core.domain.models import CurrentAssignment, Dataset, Grade, Person, Project, ProjectPhase, Sector, SkillRequirement
from core.optimize.milp import MilpParams

# p0은 Java만 있는데 Python 사업 P1에 고정돼 있다 -- 고정을 풀면 안 A가 p0을 Java 사업 P2로 옮긴다
FAST = {"allocation_mode": "full", "time_limit": 30, "gap": 0.0, "max_pairs": 10, "pair_keep_ratio": 1.0}


def _person(pid: str, skills: dict, availability=None) -> Person:
    return Person(id=pid, name=pid.upper(), grade=Grade.MID, monthly_rate=1_000, skills=skills,
                  availability=availability or [1.0] * 6)


def _dataset(p0_availability=None):
    people = [_person("p0", {"Java": 5}, p0_availability), _person("p1", {"Python": 5}),
              _person("p2", {"Java": 4}), _person("p3", {"Python": 2})]
    projects = [
        Project(id="P1", name="파이썬 사업", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=0, end_month=5,
                grade_headcount={Grade.MID: 1}, requirements=[SkillRequirement(skill="Python", min_level=3, headcount=1)],
                monthly_budget=10_000),
        Project(id="P2", name="자바 사업", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=0, end_month=5,
                grade_headcount={Grade.MID: 1}, requirements=[SkillRequirement(skill="Java", min_level=3, headcount=1)],
                monthly_budget=10_000),
    ]
    current = [CurrentAssignment(person_id="p0", project_id="P1", alloc=1.0, locked=True),
               CurrentAssignment(person_id="p2", project_id="P2", alloc=1.0)]
    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[], current=current)
    return build_active(ds, [], dataset_id="lock-mini", version="l" * 64, source="demo-bundle", synthetic=True,
                        judge=False, manifest={"scenario": "operating"})


@pytest.fixture
def lock_client(client):
    old = client.app.state.dataset
    client.app.state.dataset = _dataset()
    old.retire()
    return client


def _plans(c, **body) -> list[dict]:
    r = c.post("/api/optimize", json={"milp_params": FAST, "n_alternatives": 1, **body})
    assert r.status_code == 200, r.text
    out = []
    for block in r.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        assert lines["event"] != "error", lines["data"]
        if lines["event"] == "plan":
            out.append(json.loads(lines["data"]))
    return out


def _pairs(plan) -> set[tuple[str, str]]:
    return {(e["person_id"], e["project_id"]) for e in plan["entries"]}


def test_meta_lists_the_locked_assignments(lock_client):
    assert lock_client.get("/api/meta").json()["locked_assignments"] == [{"person_id": "p0", "project_id": "P1"}]


def test_keep_locked_keeps_the_lock_in_every_plan_and_release_moves_it(lock_client):
    released = _plans(lock_client)
    assert ("p0", "P1") not in _pairs(released[0])                 # 고정을 풀면 더 맞는 사람(p1)이 P1에 간다
    kept = _plans(lock_client, keep_locked=True)
    assert kept and all(("p0", "P1") in _pairs(p) for p in kept)  # 지키면 모든 안에 그대로
    assert kept[0]["objective"] < released[0]["objective"]        # 조건이 하나 더 붙은 계산


def test_keep_locked_is_part_of_the_cache_key_only_when_true():
    p = MilpParams()
    assert ResultCache.key({}, p, 3, "v") == ResultCache.key({}, p, 3, "v", keep_locked=False)   # 예전 키 그대로
    assert ResultCache.key({}, p, 3, "v") != ResultCache.key({}, p, 3, "v", keep_locked=True)


def test_a_lock_that_cannot_be_kept_is_refused_before_solving(client):
    old = client.app.state.dataset
    client.app.state.dataset = _dataset(p0_availability=[1.0, 1.0, 0.0, 1.0, 1.0, 1.0])   # P1 기간에 휴직 달
    old.retire()
    r = client.post("/api/optimize", json={"milp_params": FAST, "n_alternatives": 0, "keep_locked": True})
    assert r.status_code == 422 and "P0(P1)" in r.text and "고정 풀고" in r.text


def test_record_keeps_the_choice_and_refuses_plans_without_the_locks(client):
    """연초 6개월 시연 기록(고정 4건)의 안은 고정을 풀고 짠 안이다 -- "고정을 지켜 짠 안"으로 내보내거나 불러오면 거절한다."""
    from api.records import build_and_verify, export_record
    from core.config import REPO_ROOT
    raw = (REPO_ROOT / "demo" / "records" / "org-n100.json").read_bytes()
    rid = client.post("/api/records", content=raw, headers={"Content-Type": "application/json"}).json()["record"]["id"]
    opened = client.post(f"/api/records/{rid}/open", json={}).json()
    assert len(client.get("/api/meta").json()["locked_assignments"]) == 4
    r = client.post("/api/optimize", json={**opened["request"], "dataset_version": opened["dataset"]["version"]})
    plans = [json.loads(b.split("data: ", 1)[1]) for b in r.text.strip().split("\n\n") if b.startswith("event: plan")]
    body = {"title": "연초", "weights": opened["request"]["weights"], "milp_params": opened["request"]["milp_params"],
            "n_alternatives": opened["request"]["n_alternatives"], "plans": plans}
    app = client.app
    assert "keep_locked" not in export_record(app, app.state.dataset, **body)["request"]   # 풀고 짠 기록은 예전 모양 그대로
    with pytest.raises(Exception, match="고정을 지켜 짠 안이 아니다"):
        export_record(app, app.state.dataset, keep_locked=True, **body)
    rec = json.loads(raw)
    rec["request"]["keep_locked"] = True
    with pytest.raises(Exception, match="고정 배치가 빠져 있다"):
        build_and_verify(rec)


def test_nightly_install_refuses_a_keep_locked_entry_that_drops_a_lock(lock_client):
    """밤 배치가 고정을 지켜 짠 낮 요청을 다시 풀어 저장한 묶음은 고정 배치가 다 있어야 깐다(키도 그 선택을 담는다)."""
    from api.nightly import _install_entry
    app = lock_client.app
    released = _plans(lock_client)
    params = MilpParams(**{**FAST, "time_limit": 30})
    entry = {"kind": "optimize", "target": "낮 요청", "weights": {}, "n_alternatives": 1, "milp_params": params.model_dump(),
             "key": ResultCache.key({}, params, 1, app.state.dataset.info.version, keep_locked=True), "keep_locked": True,
             "plans": [{**p, "label": p["label"]} for p in released]}
    out = _install_entry(app, app.state.dataset, entry)
    assert out["status"] == "skipped" and "고정" in out["reason"]
