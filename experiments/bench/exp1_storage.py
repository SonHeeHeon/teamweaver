"""실험 1: 저장·탐색 계층 3자 비교 (SQLite / Neo4j / 인메모리).

공정성 원칙: 동일 데이터를 3중 적재하고, 연결 수립·적재 시간은 제외한 뒤
쿼리 시간만 측정한다. 세 백엔드가 같은 도달 집합을 내는지 매 실행마다 검증해
기록한다(parity) — 하나라도 다르면 비교 자체가 무의미하다.
"""
import sqlite3
import tempfile
from pathlib import Path

from core.graph.sqlite_store import build_sqlite, synergy_context_sql
from experiments.bench import datasets, harness

DEFAULT_HOPS = (1, 2, 3, 4)


def _seed_ids(ds, k):
    return [p.id for p in ds.people[:k]]


def run(scales=None, hops=DEFAULT_HOPS, seeds_per_run: int = 5,
        neo4j: bool = True) -> dict:
    scales = scales or datasets.SCALES
    rows, parity, skipped = [], [], []

    for n_people, n_projects in scales:
        ds, parsed, g = datasets.build_scale(n_people, n_projects)
        seeds = _seed_ids(ds, seeds_per_run)

        tmpdir = Path(tempfile.mkdtemp())
        db_path = tmpdir / f"exp1_{n_people}.db"
        build_sqlite(ds, parsed, db_path)          # 적재 시간은 측정에서 제외
        conn = sqlite3.connect(db_path)

        driver = None
        if neo4j:
            try:
                from core.graph.neo4j_store import get_driver, load_neo4j
                driver = get_driver()
                driver.verify_connectivity()
                load_neo4j(driver, ds, parsed)
            except Exception as exc:               # 미기동/연결 실패는 조용히 넘기지 않는다
                skipped.append({"backend": "neo4j", "n_people": n_people,
                                "reason": f"{type(exc).__name__}: {exc}"})
                driver = None
        else:
            skipped.append({"backend": "neo4j", "n_people": n_people,
                            "reason": "neo4j=False 로 명시적으로 제외"})

        for h in hops:
            sql_rows = synergy_context_sql(conn, seeds, h)
            mem_rows = g.synergy_context_memory(seeds, h)
            sql_set = {(r[0], r[1]) for r in sql_rows}
            mem_set = {(r[0], r[1]) for r in mem_rows}
            entry = {"n_people": n_people, "hops": h,
                     "sqlite_count": len(sql_set), "memory_count": len(mem_set),
                     "match": sql_set == mem_set}

            rows.append({"backend": "sqlite", "n_people": n_people,
                         "n_projects": n_projects, "hops": h,
                         "result_count": len(sql_set),
                         **harness.measure(lambda: synergy_context_sql(conn, seeds, h))})
            rows.append({"backend": "memory", "n_people": n_people,
                         "n_projects": n_projects, "hops": h,
                         "result_count": len(mem_set),
                         **harness.measure(lambda: g.synergy_context_memory(seeds, h))})

            if driver is not None:
                from core.graph.neo4j_store import synergy_context_cypher
                cy_rows = synergy_context_cypher(driver, seeds, h)
                cy_set = {(r["src"], r["other"]) for r in cy_rows}
                entry["neo4j_count"] = len(cy_set)
                entry["match"] = entry["match"] and (cy_set == sql_set)
                rows.append({"backend": "neo4j", "n_people": n_people,
                             "n_projects": n_projects, "hops": h,
                             "result_count": len(cy_set),
                             **harness.measure(lambda: synergy_context_cypher(driver, seeds, h))})
            parity.append(entry)

        conn.close()
        if driver is not None:
            driver.close()

    return {"rows": rows, "parity": parity, "skipped": skipped,
            "seeds_per_run": seeds_per_run}


def main():
    out = run()
    path = harness.save_result("exp1_storage", out)
    bad = [p for p in out["parity"] if not p["match"]]
    print(f"saved: {path}  rows={len(out['rows'])}  parity_mismatch={len(bad)}")
    if out["skipped"]:
        print(f"skipped: {out['skipped']}")


if __name__ == "__main__":
    main()
