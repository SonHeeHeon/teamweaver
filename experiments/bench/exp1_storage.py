"""실험 1: 저장·탐색 계층 3자 비교 (SQLite / Neo4j / 인메모리).

공정성 원칙: 동일 데이터를 3중 적재하고, 연결 수립·적재 시간은 제외한 뒤
쿼리 시간만 측정한다. 세 백엔드가 같은 도달 집합을 내는지 매 실행마다 검증해
기록한다(parity) — 하나라도 다르면 비교 자체가 무의미하다.

Neo4j JVM 예열 보정 (리뷰 반영, task-4-report.md "Critical 1" 참고):
재시작 직후의 Neo4j는 warmup=3 정도로는 정상 상태(steady state)에 도달하지
못한다 — 실측(재시작 직후 400회 연속 호출)으로도 지연시간이 계속 하락한다
(~3.7ms → ~1.1ms). 이 하락은 특정 (n_people, hops) 조합이 아니라 실행 엔진
전반의 JIT/워밍업으로 보인다(작은 그래프에서의 예열이 전혀 다른 규모·hop의
질의에도 전이됨을 확인). 원래 구현은 스윕을 n 오름차순으로 실행했으므로
"먼저 측정되는 작은 n=차가운 엔진, 나중에 측정되는 큰 n=이미 예열됨"이라는
혼입이 생겨, 실제로는 없는 "그래프가 커질수록 프로토콜 오버헤드가 희석된다"는
가짜 추세를 만들어냈다. 두 가지로 고친다:
  1) 스윕 시작 전 1회, 대표 그래프에 대량 호출을 흘려 엔진을 미리 데운다
     (측정에는 포함되지 않음) — 스케일 처리 순서와 예열 상태를 분리한다.
  2) neo4j 측정에만 훨씬 큰 backend-specific warmup을 적용한다. 공유
     harness.measure의 기본값(warmup=3)은 그대로 둔다 — sqlite/memory는
     이 정도로 충분하고, 기본값을 바꾸면 실험 3(Task 5)의 방법론까지
     조용히 바뀐다.
"""
import sqlite3
import tempfile
from pathlib import Path

from core.graph.sqlite_store import build_sqlite, synergy_context_sql
from experiments.bench import datasets, harness

DEFAULT_HOPS = (1, 2, 3, 4)

# neo4j 측정 전용 warmup. 기본값(3)의 10배 이상 — 재시작 직후 실측 곡선이
# 400콜까지도 완전히 평평해지지 않는 것을 감안해 넉넉히 잡았다(§리포트 참고).
NEO4J_MEASURE_WARMUP = 50
# 스윕 시작 전 딱 1회 실행하는 예열 버스트 호출 수. 특정 조합이 아니라 엔진
# 전반이 데워지는 것으로 확인됐으므로(다른 hops·다른 n 데이터에도 전이),
# 스케일마다 반복할 필요 없이 한 번이면 이후 모든 (n_people, hops) 측정이
# 동일하게 "이미 데워진" 상태에서 출발한다.
NEO4J_PRIMING_CALLS = 400
_PRIMING_SCALE = (50, 10, 42)  # (n_people, n_projects, seed) — 예열 전용, 측정 스케일과 무관


def _seed_ids(ds, k):
    return [p.id for p in ds.people[:k]]


def _prime_neo4j(driver, hops) -> None:
    """측정 시작 전 1회 실행하는 예열 버스트. 결과는 버리고 타이밍도 기록하지 않는다.

    실제로 순회할 데이터가 있어야 의미가 있으므로(빈 그래프에 대한 MATCH는
    순회 없이 즉시 반환돼 엔진을 데우지 못한다) 먼저 대표 그래프를 적재한다.
    """
    from core.graph.neo4j_store import load_neo4j, synergy_context_cypher
    ds, parsed, _ = datasets.build_scale(*_PRIMING_SCALE)
    load_neo4j(driver, ds, parsed)
    seeds = _seed_ids(ds, 5)
    hop_cycle = list(hops) or [1]
    for i in range(NEO4J_PRIMING_CALLS):
        synergy_context_cypher(driver, seeds, hop_cycle[i % len(hop_cycle)])


def _measure_protocol_floor(driver, warmup: int) -> dict:
    """순수 IPC/프로토콜 바닥값: `RETURN 1`을 실제 질의와 동일한
    세션-당-호출 패턴으로 측정한다. hops=1처럼 결과가 적은 질의는 이 바닥값이
    측정치 대부분을 차지할 수 있어, 트래버설 자체의 비용과 분리해 둔다.
    """
    def _floor():
        with driver.session() as s:
            s.run("RETURN 1").consume()
    return harness.measure(_floor, repeats=20, warmup=warmup)


def run(scales=None, hops=DEFAULT_HOPS, seeds_per_run: int = 5,
        neo4j: bool = True) -> dict:
    scales = scales or datasets.SCALES
    rows, parity, skipped = [], [], []
    protocol_floor = None

    if neo4j:
        priming_driver = None
        try:
            from core.graph.neo4j_store import get_driver
            priming_driver = get_driver()
            priming_driver.verify_connectivity()
            _prime_neo4j(priming_driver, hops)   # 측정 전 1회 예열 — 스케일 순서와 예열 상태 분리
            protocol_floor = _measure_protocol_floor(priming_driver, NEO4J_MEASURE_WARMUP)
        except Exception as exc:                 # 미기동/연결 실패는 조용히 넘기지 않는다
            skipped.append({"backend": "neo4j", "n_people": None,
                            "reason": f"priming/protocol_floor 실패: {type(exc).__name__}: {exc}"})
            neo4j = False
        finally:
            if priming_driver is not None:
                priming_driver.close()

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
                             **harness.measure(lambda: synergy_context_cypher(driver, seeds, h),
                                               warmup=NEO4J_MEASURE_WARMUP)})
            parity.append(entry)

        conn.close()
        if driver is not None:
            driver.close()

    return {"rows": rows, "parity": parity, "skipped": skipped,
            "seeds_per_run": seeds_per_run, "protocol_floor": protocol_floor}


def main():
    out = run()
    path = harness.save_result("exp1_storage", out)
    bad = [p for p in out["parity"] if not p["match"]]
    print(f"saved: {path}  rows={len(out['rows'])}  parity_mismatch={len(bad)}")
    if out["protocol_floor"]:
        print(f"neo4j protocol floor (RETURN 1): median={out['protocol_floor']['median_ms']:.3f}ms "
              f"p95={out['protocol_floor']['p95_ms']:.3f}ms")
    if out["skipped"]:
        print(f"skipped: {out['skipped']}")


if __name__ == "__main__":
    main()
