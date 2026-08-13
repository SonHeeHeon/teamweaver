"""RAG 서브그래프 검색 질의 5종의 계약.

실험 1은 정해진 질의 하나(N홉 도달 + 극성 평균)만 쟀다. RAG는 요청마다 형태가
달라지므로, 실제 XAI 브리핑이 필요로 하는 질의 형태들을 모아 워크로드로 삼는다.
양쪽 백엔드는 이 계약(입력·반환 키)을 동일하게 지켜야 하며, parity 테스트가
그것을 고정한다.

sql_complexity는 의사결정 규칙 3(표현력 격차) 판정에 쓰인다. 이 값은 Task 1
단계(SQL·Cypher 구현 이전)에서 먼저 채워졌다가, Task 3에서 `core/rag/sqlite_rag.py`·
`core/rag/neo4j_rag.py`의 실제 SQL·Cypher를 보고 재판정해 바로잡았다 — swap_diff와
replacement_candidates는 `multi_subquery`로 과대 표기돼 있었으나 실제로는 서브쿼리가
없고(UNION ALL 또는 단일 OR 조인), team_cohesion은 `simple`로 과소 표기돼 있었으나
상관 서브쿼리가 하나 있다. 아래 값은 이 재판정 결과다.
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
        # 실제 SQL은 서브쿼리 없이 독립적인 flat SELECT 4개를 UNION ALL로 이어 붙인
        # 형태다(중첩·상관 서브쿼리 없음). Cypher도 동형(UNION ALL 3분기). "simple".
        "simple"),
    "replacement_candidates": QuerySpec(
        "replacement_candidates",
        "특정 스킬을 최소 레벨 이상 보유하면서 대상 팀원과 협업 이력이 있는 후보를 "
        "찾는다. 스킬 필터와 협업 엣지 조인이 겹친다.",
        # 실제 SQL은 person_skill과 collaboration을 OR로 결합한 ON 조건 하나로 조인하는
        # 단일 JOIN + WHERE 필터다(서브쿼리 없음). Cypher도 순차 MATCH 두 개로 동형. "simple".
        "simple"),
    "team_cohesion": QuerySpec(
        "team_cohesion",
        "팀 전원 사이의 협업 엣지와 상호 리뷰 극성을 모아 팀 응집도 문맥을 만든다. "
        "집합 내 모든 쌍에 대한 질의다.",
        # 실제 SQL은 SELECT 목록에 상관 서브쿼리(correlated scalar subquery)가 하나
        # 있다(쌍마다 review를 재평가해 AVG를 구함). "simple"·"recursive_cte" 어디에도
        # 해당하지 않아 남는 값인 "multi_subquery"로 분류한다 — 이름과 달리 서브쿼리
        # 개수는 1개이며 3개 이상은 아니다(과대 해석 금지, 근거만 정확히 남긴다).
        # Cypher도 OPTIONAL MATCH + avg()로 동일한 상관 집계를 표현해야 했다.
        "multi_subquery"),
    "overfamiliar_pairs": QuerySpec(
        "overfamiliar_pairs",
        "공동 투입 개월이 임계 이상인 쌍을 찾는다(과숙련 탐지). 단순 필터지만 "
        "쌍 전체를 훑는다.",
        # 실제 SQL은 단일 테이블(collaboration) WHERE 필터뿐이다(조인·서브쿼리 없음).
        # Cypher도 단일 MATCH-WHERE. 재판정해도 "simple" 그대로 맞다.
        "simple"),
    "skill_within_hops": QuerySpec(
        "skill_within_hops",
        "특정 인력에서 N홉 이내에 도달 가능하면서 특정 스킬을 보유한 사람과 그 "
        "거리·레벨을 찾는다. 가변 길이 탐색과 속성 필터의 결합.",
        # 실제 SQL은 WITH RECURSIVE CTE가 필수다(가변 홉 탐색). 재판정해도
        # "recursive_cte" 그대로 맞다. 다만 Cypher는 재귀 CTE 없이 네이티브 가변
        # 길이 패턴(`*1..N`)만으로 동일한 결과를 낸다 — SQL 쪽에만 있는 구조적 부담이며,
        # 이 질의에 한해 Neo4j의 표현력 이점이 실체가 있다는 뜻이다(Task 7 참고용 기록).
        "recursive_cte"),
}

RETURN_KEYS = {
    "swap_diff": ("person_id", "kind", "key", "value"),
    "replacement_candidates": ("person_id", "level", "team_peer_id", "co_months"),
    "team_cohesion": ("a_id", "b_id", "co_months", "polarity"),
    "overfamiliar_pairs": ("a_id", "b_id", "co_months", "project_count"),
    "skill_within_hops": ("person_id", "hops", "level"),
}
