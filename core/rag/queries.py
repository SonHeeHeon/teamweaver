"""RAG 서브그래프 검색 질의 5종의 계약.

실험 1은 정해진 질의 하나(N홉 도달 + 극성 평균)만 쟀다. RAG는 요청마다 형태가
달라지므로, 실제 XAI 브리핑이 필요로 하는 질의 형태들을 모아 워크로드로 삼는다.
양쪽 백엔드는 이 계약(입력·반환 키)을 동일하게 지켜야 하며, parity 테스트가
그것을 고정한다.

sql_complexity는 의사결정 규칙 3(표현력 격차) 판정에 쓰인다 — 구현을 마친 뒤
실제 SQL을 보고 채운 값이다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class QuerySpec:
    name: str
    description: str
    sql_complexity: str          # "simple" | "recursive_cte" | "multi_subquery"


RAG_QUERIES = [
    "swap_diff",
    "replacement_candidates",
    "team_cohesion",
    "overfamiliar_pairs",
    "skill_within_hops",
]

SPECS = {
    "swap_diff": QuerySpec(
        "swap_diff",
        "스왑 대상 두 사람의 스킬·협업이력·리뷰 근거를 한 번에 모아 XAI 브리핑의 "
        "대조 문맥을 만든다. 두 노드에서 각각 3종 관계를 따라간다.",
        "multi_subquery"),
    "replacement_candidates": QuerySpec(
        "replacement_candidates",
        "특정 스킬을 최소 레벨 이상 보유하면서 대상 팀원과 협업 이력이 있는 후보를 "
        "찾는다. 스킬 필터와 협업 엣지 조인이 겹친다.",
        "multi_subquery"),
    "team_cohesion": QuerySpec(
        "team_cohesion",
        "팀 전원 사이의 협업 엣지와 상호 리뷰 극성을 모아 팀 응집도 문맥을 만든다. "
        "집합 내 모든 쌍에 대한 질의다.",
        "simple"),
    "overfamiliar_pairs": QuerySpec(
        "overfamiliar_pairs",
        "공동 투입 개월이 임계 이상인 쌍을 찾는다(과숙련 탐지). 단순 필터지만 "
        "쌍 전체를 훑는다.",
        "simple"),
    "skill_within_hops": QuerySpec(
        "skill_within_hops",
        "특정 인력에서 N홉 이내에 도달 가능하면서 특정 스킬을 보유한 사람과 그 "
        "거리·레벨을 찾는다. 가변 길이 탐색과 속성 필터의 결합.",
        "recursive_cte"),
}

RETURN_KEYS = {
    "swap_diff": ("person_id", "kind", "key", "value"),
    "replacement_candidates": ("person_id", "level", "team_peer_id", "co_months"),
    "team_cohesion": ("a_id", "b_id", "co_months", "polarity"),
    "overfamiliar_pairs": ("a_id", "b_id", "co_months", "project_count"),
    "skill_within_hops": ("person_id", "hops", "level"),
}
