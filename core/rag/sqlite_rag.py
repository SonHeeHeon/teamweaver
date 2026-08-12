"""RAG 질의 5종의 SQLite 구현.

재귀 CTE 주의: `JOIN collaboration c ON r.node IN (c.a_id, c.b_id)` 형태는
ix_col_a/ix_col_b 인덱스를 무력화한다(Plan 2에서 hops=4 기준 9.1배 손해로
적발). 인덱스를 쓰는 두 조인을 UNION 하는 형태로 쓸 것.
"""


def _rows(cur, keys) -> list[dict]:
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def swap_diff(conn, person_a: str, person_b: str) -> list[dict]:
    keys = ("person_id", "kind", "key", "value")
    cur = conn.execute("""
        SELECT person_id, 'skill' AS kind, skill AS key, CAST(level AS TEXT) AS value
          FROM person_skill WHERE person_id IN (?, ?)
        UNION ALL
        SELECT ?, 'cowork', CASE WHEN a_id = ? THEN b_id ELSE a_id END,
               CAST(co_months AS TEXT)
          FROM collaboration WHERE a_id = ? OR b_id = ?
        UNION ALL
        SELECT ?, 'cowork', CASE WHEN a_id = ? THEN b_id ELSE a_id END,
               CAST(co_months AS TEXT)
          FROM collaboration WHERE a_id = ? OR b_id = ?
        UNION ALL
        SELECT reviewee_id, 'evidence', reviewer_id, CAST(text_polarity AS TEXT)
          FROM review WHERE reviewee_id IN (?, ?)
        ORDER BY 1, 2, 3
    """, (person_a, person_b,
          person_a, person_a, person_a, person_a,
          person_b, person_b, person_b, person_b,
          person_a, person_b))
    return _rows(cur, keys)


def replacement_candidates(conn, skill: str, min_level: int,
                            team_ids: list[str]) -> list[dict]:
    keys = ("person_id", "level", "team_peer_id", "co_months")
    ph = ",".join("?" for _ in team_ids)
    cur = conn.execute(f"""
        SELECT ps.person_id, ps.level,
               CASE WHEN c.a_id = ps.person_id THEN c.b_id ELSE c.a_id END AS peer,
               c.co_months
          FROM person_skill ps
          JOIN collaboration c
            ON (c.a_id = ps.person_id AND c.b_id IN ({ph}))
            OR (c.b_id = ps.person_id AND c.a_id IN ({ph}))
         WHERE ps.skill = ? AND ps.level >= ?
           AND ps.person_id NOT IN ({ph})
         ORDER BY ps.person_id, peer
    """, (*team_ids, *team_ids, skill, min_level, *team_ids))
    return _rows(cur, keys)


def team_cohesion(conn, team_ids: list[str]) -> list[dict]:
    keys = ("a_id", "b_id", "co_months", "polarity")
    ph = ",".join("?" for _ in team_ids)
    cur = conn.execute(f"""
        SELECT MIN(c.a_id, c.b_id) AS a, MAX(c.a_id, c.b_id) AS b, c.co_months,
               (SELECT AVG(r.text_polarity) FROM review r
                 WHERE (r.reviewer_id = c.a_id AND r.reviewee_id = c.b_id)
                    OR (r.reviewer_id = c.b_id AND r.reviewee_id = c.a_id))
          FROM collaboration c
         WHERE c.a_id IN ({ph}) AND c.b_id IN ({ph})
         ORDER BY a, b
    """, (*team_ids, *team_ids))
    return _rows(cur, keys)


def overfamiliar_pairs(conn, threshold_months: int) -> list[dict]:
    keys = ("a_id", "b_id", "co_months", "project_count")
    cur = conn.execute("""
        SELECT MIN(a_id, b_id), MAX(a_id, b_id), co_months, project_count
          FROM collaboration WHERE co_months >= ?
         ORDER BY 1, 2
    """, (threshold_months,))
    return _rows(cur, keys)


def skill_within_hops(conn, person_id: str, skill: str, hops: int) -> list[dict]:
    keys = ("person_id", "hops", "level")
    cur = conn.execute("""
        WITH RECURSIVE reach(node, depth) AS (
            SELECT ?, 0
            UNION
            SELECT c.b_id, r.depth + 1
              FROM reach r JOIN collaboration c ON c.a_id = r.node
             WHERE r.depth < ?
            UNION
            SELECT c.a_id, r.depth + 1
              FROM reach r JOIN collaboration c ON c.b_id = r.node
             WHERE r.depth < ?
        ),
        nearest AS (SELECT node, MIN(depth) AS depth FROM reach GROUP BY node)
        SELECT n.node, n.depth, ps.level
          FROM nearest n
          JOIN person_skill ps ON ps.person_id = n.node AND ps.skill = ?
         WHERE n.node != ? AND n.depth > 0
         ORDER BY n.node
    """, (person_id, hops, hops, skill, person_id))
    return _rows(cur, keys)


ALL = {
    "swap_diff": swap_diff,
    "replacement_candidates": replacement_candidates,
    "team_cohesion": team_cohesion,
    "overfamiliar_pairs": overfamiliar_pairs,
    "skill_within_hops": skill_within_hops,
}
