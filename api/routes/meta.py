from fastapi import APIRouter, Depends
from api.datasets import ActiveDataset
from api.deps import get_dataset, get_graph
from api.schemas import CoworkOut, MetaResponse, PersonOut, ProjectOut
from core.config import load_review_items
from core.graph.memory_graph import MemoryGraph

router = APIRouter()


def _cowork_edges(graph: MemoryGraph) -> list[CoworkOut]:
    """csr_matrix(대칭)에서 상삼각만 훑어 무방향 엣지를 복원한다.

    MemoryGraph는 원본 CoworkRecord 리스트를 보관하지 않으므로 project_count는
    복원할 수 없다 -- 0으로 채운다. 네트워크 그래프는 co_months만 선 굵기에
    쓰므로 시각화에 영향이 없다. (project_count가 실제로 필요해지면 그때
    MemoryGraph가 원본을 들고 있게 바꾸는 것이 맞고, 여기서 추정하면 안 된다.)
    """
    ids = [p.id for p in graph.people]
    cw = graph.cowork_months.tocoo()
    out = []
    for i, j, v in zip(cw.row, cw.col, cw.data):
        if int(i) < int(j) and v:
            out.append(CoworkOut(a_id=ids[int(i)], b_id=ids[int(j)],
                                 co_months=int(v), project_count=0))
    out.sort(key=lambda e: (e.a_id, e.b_id))
    return out


def build_meta(graph: MemoryGraph, dataset_version: str) -> MetaResponse:
    """meta를 graph 하나에서 만든다. /api/report도 요청이 잡은 데이터셋으로 이걸 만들어
    PDF에 넣는다(렌더 중 전환돼도 이름이 섞이지 않게, K9)."""
    people = [PersonOut(id=p.id, name=p.name, grade=p.grade.value, skills=p.skills)
              for p in graph.people]
    projects = [ProjectOut(id=j.id, name=j.name, sector=j.sector.value, phase=j.phase.value,
                           start_month=j.start_month, end_month=j.end_month,
                           grade_headcount={g.value: n for g, n in j.grade_headcount.items()},
                           monthly_budget=j.monthly_budget)
                for j in graph.projects]
    return MetaResponse(people=people, projects=projects,
                        skills=sorted(graph.skill_index.keys()),
                        review_items=load_review_items(),
                        dataset_version=dataset_version,
                        coworks=_cowork_edges(graph))


@router.get("/api/meta", response_model=MetaResponse)
def get_meta(graph: MemoryGraph = Depends(get_graph),
             dataset: ActiveDataset = Depends(get_dataset)) -> MetaResponse:
    return build_meta(graph, dataset.info.version)
