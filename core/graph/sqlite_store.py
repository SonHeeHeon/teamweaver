import sqlite3
from pathlib import Path
from core.domain.models import CoworkRecord, Dataset, ParsedReview, PeerReview

_SCHEMA = """
CREATE TABLE person(id TEXT PRIMARY KEY, name TEXT, grade TEXT, monthly_rate INT);
CREATE TABLE person_skill(person_id TEXT, skill TEXT, level INT);
CREATE TABLE project(id TEXT PRIMARY KEY, name TEXT, sector TEXT, phase TEXT,
                     start_month INT, end_month INT, monthly_budget INT);
CREATE TABLE grade_headcount(project_id TEXT, grade TEXT, headcount INT);
CREATE TABLE requirement(project_id TEXT, skill TEXT, min_level INT, headcount INT);
CREATE TABLE collaboration(a_id TEXT, b_id TEXT, co_months INT, project_count INT);
CREATE TABLE review(reviewer_id TEXT, reviewee_id TEXT, text_polarity REAL);
CREATE TABLE review_item(reviewer_id TEXT, reviewee_id TEXT, item TEXT, is_positive INT);
CREATE INDEX ix_ps ON person_skill(person_id); CREATE INDEX ix_ps_skill ON person_skill(skill);
CREATE INDEX ix_col_a ON collaboration(a_id); CREATE INDEX ix_col_b ON collaboration(b_id);
CREATE INDEX ix_rev ON review(reviewee_id);
"""

def build_sqlite(ds: Dataset, parsed: list[ParsedReview], path: Path) -> None:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.executemany("INSERT INTO person VALUES(?,?,?,?)",
                     [(p.id, p.name, p.grade.value, p.monthly_rate) for p in ds.people])
    conn.executemany("INSERT INTO person_skill VALUES(?,?,?)",
                     [(p.id, s, lv) for p in ds.people for s, lv in p.skills.items()])
    conn.executemany("INSERT INTO project VALUES(?,?,?,?,?,?,?)",
                     [(j.id, j.name, j.sector.value, j.phase.value,
                       j.start_month, j.end_month, j.monthly_budget) for j in ds.projects])
    conn.executemany("INSERT INTO grade_headcount VALUES(?,?,?)",
                     [(j.id, g.value, n) for j in ds.projects for g, n in j.grade_headcount.items()])
    conn.executemany("INSERT INTO requirement VALUES(?,?,?,?)",
                     [(j.id, r.skill, r.min_level, r.headcount)
                      for j in ds.projects for r in j.requirements])
    conn.executemany("INSERT INTO collaboration VALUES(?,?,?,?)",
                     [(c.a_id, c.b_id, c.co_months, c.project_count) for c in ds.coworks])
    conn.executemany("INSERT INTO review VALUES(?,?,?)",
                     [(p.reviewer_id, p.reviewee_id, p.text_polarity) for p in parsed])
    conn.executemany("INSERT INTO review_item VALUES(?,?,?,?)",
                     [(r.reviewer_id, r.reviewee_id, it, flag)
                      for r in ds.reviews
                      for it, flag in ([(i, 1) for i in r.positive.items] +
                                       [(i, 0) for i in r.negative.items])])
    conn.commit(); conn.close()

def synergy_context_sql(conn, person_ids: list[str], hops: int) -> list[tuple]:
    ph = ",".join("?" for _ in person_ids)
    q = f"""
    WITH RECURSIVE reach(src, node, depth) AS (
        SELECT id, id, 0 FROM person WHERE id IN ({ph})
        UNION
        SELECT r.src, c.b_id, r.depth + 1
        FROM reach r JOIN collaboration c ON c.a_id = r.node
        WHERE r.depth < ?
        UNION
        SELECT r.src, c.a_id, r.depth + 1
        FROM reach r JOIN collaboration c ON c.b_id = r.node
        WHERE r.depth < ?
    ),
    deduped_reach AS (
        SELECT src, node, MIN(depth) AS depth
        FROM reach
        GROUP BY src, node
    ),
    node_polarity AS (
        SELECT reviewee_id, AVG(text_polarity) AS avg_polarity
        FROM review
        GROUP BY reviewee_id
    )
    SELECT dr.src, dr.node, np.avg_polarity
    FROM deduped_reach dr
    LEFT JOIN node_polarity np ON dr.node = np.reviewee_id
    WHERE dr.node != dr.src"""
    return conn.execute(q, [*person_ids, hops, hops]).fetchall()

def append_review(conn, review: PeerReview, parsed: ParsedReview) -> None:
    """증분 갱신: 새 피어리뷰 1건. 운영에서는 리뷰가 계속 들어온다.

    review 테이블에는 PRIMARY KEY가 없으므로 이 INSERT는 같은 (리뷰어, 피리뷰어)로
    다시 호출하면 행을 하나 더 쌓는다 — Neo4j 쪽 MERGE(멱등)와 하는 일이 다르다.
    측정할 때는 반드시 양쪽 모두에 없는 새 레코드를 써야 한다.
    """
    conn.execute("INSERT INTO review VALUES(?,?,?)",
                 (parsed.reviewer_id, parsed.reviewee_id, parsed.text_polarity))
    conn.executemany("INSERT INTO review_item VALUES(?,?,?,?)",
                     [(review.reviewer_id, review.reviewee_id, it, flag)
                      for it, flag in ([(i, 1) for i in review.positive.items] +
                                       [(i, 0) for i in review.negative.items])])
    conn.commit()


def append_cowork(conn, rec: CoworkRecord) -> None:
    """증분 갱신: 새 협업 이력 1건."""
    conn.execute("INSERT INTO collaboration VALUES(?,?,?,?)",
                 (rec.a_id, rec.b_id, rec.co_months, rec.project_count))
    conn.commit()
