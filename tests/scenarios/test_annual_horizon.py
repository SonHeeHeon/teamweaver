"""계획기간 1년 확장(2026-10-19, 사용자 "계획기간 1년 확장도 하자")과 연초 계획 가용률 새 가정("대부분 100%, 3% 육아휴직 1년,
2% 한 달 휴가"): 데이터마다 6 또는 12개월, 연간 시연 묶음, 화면이 쓰는 계획 기간."""
import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.domain.models import (Dataset, Grade, Person, Project, ProjectPhase, Sector, SkillRequirement)

ROOT = Path(__file__).resolve().parents[2]
ANNUAL = ROOT / "demo" / "org-n100-annual"
N100 = ROOT / "demo" / "org-n100"


def _person(i: int, avail: list[float], level: int = 3) -> Person:
    return Person(id=f"p{i}", name=f"사람{i}", grade=Grade.MID, monthly_rate=1000, skills={"Java": level},
                  availability=avail)


def _project(start: int, end: int, pid: str = "j1") -> Project:
    return Project(id=pid, name=pid, sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=start,
                   end_month=end, grade_headcount={Grade.MID: 1},
                   requirements=[SkillRequirement(skill="Java", min_level=1, headcount=1)], monthly_budget=5000)


def test_a_dataset_can_plan_six_or_twelve_months_but_one_length_only():
    ds = Dataset(people=[_person(0, [1.0] * 12), _person(1, [1.0] * 12)], projects=[_project(8, 11)], coworks=[],
                 reviews=[])
    assert ds.horizon_months == 12
    assert Dataset(people=[_person(0, [1.0] * 6)], projects=[_project(0, 5)], coworks=[], reviews=[]).horizon_months == 6
    with pytest.raises(ValidationError, match="one planning horizon"):
        Dataset(people=[_person(0, [1.0] * 12), _person(1, [1.0] * 6)], projects=[], coworks=[], reviews=[])
    with pytest.raises(ValidationError, match="end after the 6-month horizon"):
        Dataset(people=[_person(0, [1.0] * 6)], projects=[_project(4, 8)], coworks=[], reviews=[])
    with pytest.raises(ValidationError):
        _person(0, [1.0] * 9)                      # 6·12만
    with pytest.raises(ValidationError):
        _project(0, 12)                            # 12개월 밖


def test_the_solver_respects_availability_in_the_second_half_year():
    """하반기 사업(9~12번째 달): 기술이 더 좋은 사람이 11번째 달에 휴직이면 들어가지 못한다 -- 모델이 달 수를 데이터에서 읽는다."""
    from core.datagen.parse_reviews import parse_reviews_rule_based
    from core.graph.memory_graph import MemoryGraph
    from core.optimize.milp import MilpParams, solve_milp
    from core.scoring.engine import ScoringEngine
    away = [1.0] * 12
    away[10] = 0.0
    ds = Dataset(people=[_person(0, away, level=5), _person(1, [1.0] * 12, level=2)], projects=[_project(8, 11)],
                 coworks=[], reviews=[])
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    plan = solve_milp(g, eng.skill_matrix({}), eng.synergy_matrix(), MilpParams(time_limit=30, gap=0.0))
    assert {e.person_id for e in plan.entries} == {"p1"}
    # 휴직이 없으면 기술이 좋은 사람
    ds2 = Dataset(people=[_person(0, [1.0] * 12, level=5), _person(1, [1.0] * 12, level=2)],
                  projects=[_project(8, 11)], coworks=[], reviews=[])
    g2 = MemoryGraph.build(ds2, parse_reviews_rule_based(ds2.reviews))
    eng2 = ScoringEngine(g2)
    assert {e.person_id for e in solve_milp(g2, eng2.skill_matrix({}), eng2.synergy_matrix(),
                                            MilpParams(time_limit=30, gap=0.0)).entries} == {"p0"}


def test_the_loader_reads_a_twelve_month_manifest():
    from core.ingest.loader import load_bundle
    bundle, report = load_bundle(ANNUAL)
    assert report.ok, report.summary()
    assert len(bundle.horizon) == 12
    assert bundle.horizon[0].strftime("%Y-%m") == "2026-10" and bundle.horizon[-1].strftime("%Y-%m") == "2027-09"


def _rows(root: Path, name: str) -> list[dict]:
    with (root / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_the_annual_bundle_is_the_n100_organisation_over_twelve_months(tmp_path):
    from scripts.make_annual_demo import make
    rebuilt = make(dst=tmp_path / "annual")
    for p in sorted(ANNUAL.iterdir()):                                       # 커밋한 묶음 = 지금 도구 출력
        assert (rebuilt / p.name).read_bytes() == p.read_bytes(), p.name
    # 사람·이력·평가 글이 원본과 바이트까지 같다 -- 판정 동봉이 그대로 쓰인다
    for name in ("people.csv", "person_skills.csv", "work_history.csv", "reviews.csv", "review_items.csv"):
        assert (ANNUAL / name).read_bytes() == (N100 / name).read_bytes(), name
    m = json.loads((ANNUAL / "manifest.json").read_text("utf-8"))
    assert m["horizon_months"] == 12 and m["horizon_start"] == "2026-10"
    assert _rows(ANNUAL, "current_assignments.csv") == []                  # 백지
    # 상반기 사업은 원본 그대로, 대표 사업은 기간 끝까지
    assert len(_rows(ANNUAL, "availability.csv")) == 12 * 100
    old = {r["project_id"]: r for r in _rows(N100, "projects.csv")}
    projects = {r["project_id"]: r for r in _rows(ANNUAL, "projects.csv")}
    assert projects["J001"]["end_month"] == "2027-09"
    assert all(projects[j] == r for j, r in old.items() if j != "J001")
    late = [r for r in projects.values() if r["start_month"] >= "2027-04"]
    assert len(late) == len(m["annual"]["new_projects"]) > 0


def test_annual_availability_is_full_time_except_parental_leave():
    """연간 묶음 가용률(사용자 가정 2026-10-19): 100%와 육아휴직 달의 0%뿐. 3%는 육아휴직(12개월 연속, 해를 걸침), 2%는 한 달 휴가(가용률은 그대로)."""
    from core.ingest.leave import offset as _offset          # 휴직 창은 생성기 v3의 core.ingest.leave(2026-10-19 claude-a)
    m = json.loads((ANNUAL / "manifest.json").read_text("utf-8"))
    leave = {x["person_id"]: x for x in m["leave"]["people"]}
    kinds = [x["kind"] for x in leave.values()]
    assert kinds.count("육아휴직") == 3 and kinds.count("한 달 휴가") == 2 and m["leave"]["calibrated"] is False
    zero = set()
    for r in _rows(ANNUAL, "availability.csv"):
        if r["available_mm"] == "0.0":
            w = leave[r["person_id"]]
            assert w["kind"] == "육아휴직" and 0 <= _offset(w["start"], r["month"]) < 12
            zero.add(r["person_id"])
        else:
            assert r["available_mm"] == "1.0"
    assert zero == {p for p, w in leave.items() if w["kind"] == "육아휴직"}   # 셋 모두 계획 기간과 겹친다


@pytest.fixture
def annual(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(ANNUAL.parent))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(ANNUAL))
    return ANNUAL


def test_the_server_tells_the_screen_the_planning_period(annual):
    from api.main import app
    with TestClient(app) as c:
        meta = c.get("/api/meta").json()
        ws = c.get("/api/workspace").json()
    assert meta["horizon_start"] == "2026-10" and meta["horizon_months"] == 12
    assert ws["horizon_start"] == "2026-10" and ws["horizon_last"] == "2027-09"
    # 상반기에 끝나는 사업은 "계획 기간 안에 끝나는 진행 사업"이다
    assert any(o["ends_in_horizon"] for o in ws["ongoing"]) or ws["ongoing"] == []


def test_six_month_bundles_still_report_six_months(yearend):
    from api.main import app
    with TestClient(app) as c:
        meta = c.get("/api/meta").json()
        ws = c.get("/api/workspace").json()
    assert meta["horizon_months"] == 6 and ws["horizon_last"] == "2027-03"


def test_people_on_parental_leave_are_listed_as_unavailable_with_the_reason(annual):
    """사용자 지시(2026-10-19): 그 달에 육아휴직이면 후보에서 빼지 말고 보이되 고를 수 없게, 사유와 함께."""
    from api.main import app
    with TestClient(app) as c:
        meta = c.get("/api/meta").json()
        v = meta["dataset_version"]
        late = c.get("/api/pipeline/J026/candidates", params={"dataset_version": v}).json()   # 하반기 신규 사업
        early = c.get("/api/pipeline/J002/candidates", params={"dataset_version": v}).json()  # 2026-12 한 달
    dp4 = next(p for p in meta["people"] if p["id"] == "DP0004")
    assert dp4["leave"] == {"kind": "육아휴직", "start": "2026-12", "months": 12}
    assert dp4["availability"][:2] == [1.0, 1.0] and set(dp4["availability"][2:]) == {0.0}
    blocked = {u["person_id"]: u for u in late["unavailable"]}
    j021 = next(j for j in meta["projects"] if j["id"] == "J026")   # 하반기 신규 사업(v5: 첫 하반기 신규는 J022 -- J026도 하반기 사업)
    months = list(range(j021["start_month"], j021["end_month"] + 1))
    assert months[0] >= 6 and blocked["DP0004"]["months"] == months     # 하반기 사업 기간 전체가 휴직과 겹친다
    assert blocked["DP0004"]["reason"].startswith("육아휴직 — 27.") and blocked["DP0004"]["reason"].endswith("월 투입 불가")
    picked = {r["person_id"] for g in ("bench", "ending", "pull") for r in late[g]}
    assert not picked & set(blocked)                                    # 후보로는 나오지 않는다
    assert "DP0011" not in blocked                                      # 2025-11~2026-10 휴직: 2027-04에는 복귀
    assert "DP0011" not in {u["person_id"] for u in early["unavailable"]}
    one_month = {x["person_id"] for x in json.loads((ANNUAL / "manifest.json").read_text("utf-8"))["leave"]["people"]
                 if x["kind"] == "한 달 휴가"}
    assert not one_month & {u["person_id"] for u in late["unavailable"] + early["unavailable"]}   # 사업이 자체 감당


def test_twelve_month_data_gets_a_longer_automatic_time_limit(annual):
    """실측(2026-10-19, 연간 100명·시드 4): 30초면 안 A가 빈자리 62항목, 120초면 0 -- 12개월은 권장 시간 ×4."""
    from api.main import app
    from api.settings import PlacementSettings, recommended_seconds
    assert recommended_seconds(100, "fixed", 12) == 4 * recommended_seconds(100, "fixed", 6)
    assert PlacementSettings().to_milp_params(n_people=300, months=12).time_limit <= 900
    with TestClient(app) as c:
        body = c.get("/api/settings").json()
    # 서비스 기본은 전일 투입 -- 고정 투입률 표와 값이 같아 통과하던 비교를 바로잡았다(v4 측정으로 전일 100명이 60초가 되며 드러남)
    assert body["effective_time_limit"] == recommended_seconds(100, "full", 12) == 120
    assert body["recommended_time_full"]["per_solve_s"] == body["effective_time_limit"]
    assert "12개월" in body["recommended_time_full"]["basis"] and "12개월" in body["recommended_time"]["basis"]


def test_second_half_monthly_allocations_pass_the_api(annual):
    """Codex 리뷰(2026-10-19, high): API 입력의 달 키 상한이 5로 남아 12개월 하반기 달별 투입률(키 6~11)이 422였다."""
    from api.main import app
    with TestClient(app) as c:
        g = app.state.dataset.graph
        j = next(p for p in g.projects if p.start_month >= 6 and len(p.months) >= 2)
        person = next(p for p in g.people if all(p.availability[m] >= 1.0 for m in j.months))
        e = {"person_id": person.id, "project_id": j.id, "alloc": 0.5}
        profile = {m: (0.3 if k == 0 else 0.6) for k, m in enumerate(j.months)}
        res = c.post("/api/plans/apply-alloc", json={"entries": [e], "change": {"person_id": person.id, "project_id": j.id,
                                                                                 "monthly_alloc": profile}})
        assert res.status_code == 200, res.text
        out = res.json()["entries"][0]
        assert {int(k): v for k, v in out["monthly_alloc"].items()} == profile
        # 달별 명단을 그대로 교체 검토에 넘겨도 받는다
        other = next(p for p in g.people if p.id != person.id and all(p.availability[m] >= 1.0 for m in j.months))
        res = c.post("/api/whatif", json={"entries": [out], "swap": {"out_person_id": person.id, "in_person_id": other.id,
                                                                     "project_id": j.id}})
        assert res.status_code == 200, res.text


def test_six_month_data_rejects_month_keys_past_its_horizon(yearend):
    from api.main import app
    with TestClient(app) as c:
        g = app.state.dataset.graph
        j = next(p for p in g.projects if len(p.months) >= 2)
        e = {"person_id": g.people[0].id, "project_id": j.id, "alloc": 0.5}
        res = c.post("/api/plans/apply-alloc", json={"entries": [e], "change": {"person_id": g.people[0].id, "project_id": j.id,
                                                                                 "monthly_alloc": {8: 0.5}}})
        assert res.status_code == 422                                     # 500이 아니라 거절
