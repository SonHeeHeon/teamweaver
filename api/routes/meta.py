from fastapi import APIRouter, Depends
from api.deps import get_graph
from api.schemas import MetaResponse, PersonOut, ProjectOut
from core.config import load_review_items
from core.graph.memory_graph import MemoryGraph

router = APIRouter()


@router.get("/api/meta", response_model=MetaResponse)
def get_meta(graph: MemoryGraph = Depends(get_graph)) -> MetaResponse:
    people = [PersonOut(id=p.id, name=p.name, grade=p.grade.value, skills=p.skills)
              for p in graph.people]
    projects = [ProjectOut(id=j.id, name=j.name, sector=j.sector.value, phase=j.phase.value,
                           start_month=j.start_month, end_month=j.end_month,
                           grade_headcount={g.value: n for g, n in j.grade_headcount.items()},
                           monthly_budget=j.monthly_budget)
                for j in graph.projects]
    skills = sorted(graph.skill_index.keys())
    return MetaResponse(people=people, projects=projects, skills=skills,
                        review_items=load_review_items())
