"""PDF 계산 신뢰도 배지의 단위(2026-10-22 폴백 리뷰 S2): 서버가 원안 명단의 빈자리를 `base_unfilled`로 싣는다.

배지는 원안(솔버가 푼 명단)의 목적값 기준이다. 원안에 빈자리가 있으면 목적값이 0 근처여서 %가 부풀려지므로,
화면 결과 카드처럼 점수로 보이게 원안 빈자리를 알려 준다. 교체를 적용했으면 최종 명단의 빈자리와 다를 수 있다."""
from api.routes.report import _replay_applied_swaps
from api.schemas import ReportRequest
from core.domain.models import Dataset, Grade, Person, Project, ProjectPhase, Sector, SkillRequirement
from core.graph.memory_graph import MemoryGraph


def _graph():
    people = [Person(id=p, name=p.upper(), grade=Grade.MID, monthly_rate=1_000, skills={"Python": 3},
                     availability=[1.0] * 6) for p in ("p0", "p1")]
    projects = [Project(id="j0", name="J0", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                        start_month=0, end_month=2, grade_headcount={Grade.MID: 2},
                        requirements=[SkillRequirement(skill="Python", min_level=3, headcount=1)],
                        monthly_budget=10_000)]
    return MemoryGraph.build(Dataset(people=people, projects=projects, coworks=[], reviews=[]), parsed=[])


def _req(**extra):
    return ReportRequest.model_validate({"plan_label": "A", "objective": 0, "fulfillment": 0, "optimization_ratio": None,
                                         "entries": [{"person_id": "p0", "project_id": "j0", "alloc": 1.0}], **extra})


def test_without_swaps_base_unfilled_is_the_roster_shortfall():
    out = _replay_applied_swaps(_req(), _graph())
    assert out["base_unfilled"] == out["unfilled"] == ["j0:중급:1명 미충원"]


def test_with_swaps_base_unfilled_is_the_original_roster_not_the_final_one():
    base = [{"person_id": "p0", "project_id": "j0", "alloc": 1.0}]
    out = _replay_applied_swaps(_req(base_entries=base, applied_swaps=[
        {"out_person_id": "p0", "in_person_id": "p1", "project_id": "j0"}]), _graph())
    assert out["base_unfilled"] == ["j0:중급:1명 미충원"]                      # 원안: 2자리 중 1자리
    assert [s["in_person_id"] for s in out["applied_swaps"]] == ["p1"]
