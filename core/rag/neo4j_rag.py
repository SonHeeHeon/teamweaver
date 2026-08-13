"""RAG 질의 5종의 Neo4j 구현.

sqlite_rag와 동일한 이름·인자·반환 계약을 지킨다 — parity 테스트가 그것을
고정한다. 인덱스 사용 여부는 PROFILE_QUERIES로 검증한다(Plan 2에서 Neo4j가
라벨 스캔으로 dbHits의 78%를 낭비했던 전례).
"""

_SWAP_DIFF = """
MATCH (p:Person)-[h:HAS_SKILL]->(k:Skill)
WHERE p.id IN [$a, $b]
RETURN p.id AS person_id, 'skill' AS kind, k.name AS key, toString(h.level) AS value
UNION ALL
MATCH (p:Person)-[w:WORKED_WITH]-(o:Person)
WHERE p.id IN [$a, $b]
RETURN p.id AS person_id, 'cowork' AS kind, o.id AS key, toString(w.co_months) AS value
UNION ALL
MATCH (rv:Person)-[v:REVIEWED]->(re:Person)
WHERE re.id IN [$a, $b]
RETURN re.id AS person_id, 'evidence' AS kind, rv.id AS key,
       toString(v.polarity) AS value
"""

_REPLACEMENT = """
MATCH (c:Person)-[h:HAS_SKILL]->(k:Skill {name: $skill})
WHERE h.level >= $min_level AND NOT c.id IN $team
MATCH (c)-[w:WORKED_WITH]-(t:Person) WHERE t.id IN $team
RETURN c.id AS person_id, h.level AS level, t.id AS team_peer_id,
       w.co_months AS co_months
ORDER BY person_id, team_peer_id
"""

_TEAM_COHESION = """
MATCH (a:Person)-[w:WORKED_WITH]-(b:Person)
WHERE a.id IN $team AND b.id IN $team AND a.id < b.id
OPTIONAL MATCH (x:Person)-[v:REVIEWED]->(y:Person)
WHERE (x.id = a.id AND y.id = b.id) OR (x.id = b.id AND y.id = a.id)
RETURN a.id AS a_id, b.id AS b_id, w.co_months AS co_months,
       avg(v.polarity) AS polarity
ORDER BY a_id, b_id
"""

_OVERFAMILIAR = """
MATCH (a:Person)-[w:WORKED_WITH]-(b:Person)
WHERE w.co_months >= $threshold AND a.id < b.id
RETURN a.id AS a_id, b.id AS b_id, w.co_months AS co_months,
       w.project_count AS project_count
ORDER BY a_id, b_id
"""

_SKILL_WITHIN_HOPS = """
MATCH (p:Person {id: $pid})
CALL (p) {
  MATCH path = (p)-[:WORKED_WITH*1..%d]-(o:Person)
  RETURN o, min(length(path)) AS hops
}
MATCH (o)-[h:HAS_SKILL]->(k:Skill {name: $skill})
WHERE o.id <> $pid
RETURN o.id AS person_id, hops, h.level AS level
ORDER BY person_id
"""


def _run(driver, cypher: str, **params) -> list[dict]:
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, **params)]


def swap_diff(driver, person_a: str, person_b: str) -> list[dict]:
    rows = _run(driver, _SWAP_DIFF, a=person_a, b=person_b)
    return sorted(rows, key=lambda r: (r["person_id"], r["kind"], r["key"]))


def replacement_candidates(driver, skill: str, min_level: int,
                           team_ids: list[str]) -> list[dict]:
    return _run(driver, _REPLACEMENT, skill=skill, min_level=min_level,
                team=team_ids)


def team_cohesion(driver, team_ids: list[str]) -> list[dict]:
    return _run(driver, _TEAM_COHESION, team=team_ids)


def overfamiliar_pairs(driver, threshold_months: int) -> list[dict]:
    return _run(driver, _OVERFAMILIAR, threshold=threshold_months)


def skill_within_hops(driver, person_id: str, skill: str, hops: int) -> list[dict]:
    return _run(driver, _SKILL_WITHIN_HOPS % hops, pid=person_id, skill=skill)


PROFILE_QUERIES = {
    "swap_diff": lambda a, b: (_SWAP_DIFF, {"a": a, "b": b}),
    "skill_within_hops": lambda pid, skill, hops: (
        _SKILL_WITHIN_HOPS % hops, {"pid": pid, "skill": skill}),
}

ALL = {
    "swap_diff": swap_diff,
    "replacement_candidates": replacement_candidates,
    "team_cohesion": team_cohesion,
    "overfamiliar_pairs": overfamiliar_pairs,
    "skill_within_hops": skill_within_hops,
}
