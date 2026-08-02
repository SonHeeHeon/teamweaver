import os
import neo4j
from core.domain.models import Dataset, ParsedReview

def get_driver(uri: str | None = None) -> neo4j.Driver:
    uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user, pw = os.environ.get("NEO4J_AUTH", "neo4j/teamweaver").split("/")
    return neo4j.GraphDatabase.driver(uri, auth=(user, pw))

def load_neo4j(driver, ds: Dataset, parsed: list[ParsedReview]) -> None:
    pol = {(p.reviewer_id, p.reviewee_id): p for p in parsed}
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        s.run("UNWIND $rows AS r MERGE (p:Person {id: r.id}) "
              "SET p.name = r.name, p.grade = r.grade, p.rate = r.rate",
              rows=[{"id": p.id, "name": p.name, "grade": p.grade.value,
                     "rate": p.monthly_rate} for p in ds.people])
        s.run("UNWIND $rows AS r MATCH (p:Person {id: r.pid}) "
              "MERGE (k:Skill {name: r.skill}) MERGE (p)-[h:HAS_SKILL]->(k) SET h.level = r.level",
              rows=[{"pid": p.id, "skill": sk, "level": lv}
                    for p in ds.people for sk, lv in p.skills.items()])
        s.run("UNWIND $rows AS r MERGE (j:Project {id: r.id}) "
              "SET j += {name: r.name, sector: r.sector, phase: r.phase, budget: r.budget}",
              rows=[{"id": j.id, "name": j.name, "sector": j.sector.value,
                     "phase": j.phase.value, "budget": j.monthly_budget} for j in ds.projects])
        s.run("UNWIND $rows AS r MATCH (j:Project {id: r.jid}) "
              "MERGE (k:Skill {name: r.skill}) MERGE (j)-[q:REQUIRES]->(k) "
              "SET q.min_level = r.min_level, q.headcount = r.headcount",
              rows=[{"jid": j.id, "skill": rq.skill, "min_level": rq.min_level,
                     "headcount": rq.headcount} for j in ds.projects for rq in j.requirements])
        s.run("UNWIND $rows AS r MATCH (a:Person {id: r.a}), (b:Person {id: r.b}) "
              "MERGE (a)-[w:WORKED_WITH]->(b) SET w.co_months = r.m, w.project_count = r.c",
              rows=[{"a": c.a_id, "b": c.b_id, "m": c.co_months, "c": c.project_count}
                    for c in ds.coworks])
        s.run("UNWIND $rows AS r MATCH (a:Person {id: r.rv}), (b:Person {id: r.re}) "
              "MERGE (a)-[v:REVIEWED]->(b) SET v += {pos_items: r.pos, neg_items: r.neg, "
              "polarity: r.pol, evidence: r.ev}",
              rows=[{"rv": r.reviewer_id, "re": r.reviewee_id,
                     "pos": r.positive.items, "neg": r.negative.items,
                     "pol": pol[(r.reviewer_id, r.reviewee_id)].text_polarity
                            if (r.reviewer_id, r.reviewee_id) in pol else 0.0,
                     "ev": pol[(r.reviewer_id, r.reviewee_id)].evidence
                           if (r.reviewer_id, r.reviewee_id) in pol else []}
                    for r in ds.reviews])

def synergy_context_cypher(driver, person_ids: list[str], hops: int) -> list[dict]:
    q = (f"MATCH (p:Person)-[:WORKED_WITH*1..{hops}]-(o:Person) "
         "WHERE p.id IN $ids AND o.id <> p.id "
         "WITH DISTINCT p, o "
         "OPTIONAL MATCH (o)<-[v:REVIEWED]-() "
         "RETURN p.id AS src, o.id AS other, avg(v.polarity) AS avg_polarity")
    with driver.session() as s:
        return [dict(r) for r in s.run(q, ids=person_ids)]
