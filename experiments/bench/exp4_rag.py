"""실험 4: RAG 서브그래프 검색 워크로드 (SQLite).

Plan 3에서는 SQLite vs Neo4j 비교 워크로드였고, 판정 결과(rule 1: 0/20)가
experiments/results/exp4_rag.json에 고정돼 있다. Plan 4에서 Neo4j를 제거하며
이 러너도 비교 기능 없이 SQLite 단독 측정으로 축소한다 -- 커밋된 JSON은
판정의 근거이므로 이 축소와 무관하게 그대로 둔다(이 러너를 다시 돌려도 그
파일은 바뀌지 않는다. 덮어쓰려면 별도로 `main()`을 호출해야 하고,
`harness.save_result`는 이전 실행을 `.superseded-N.json`으로 보존한 뒤에만
새로 쓴다).
"""
import sqlite3
import tempfile
from pathlib import Path

from core.graph.sqlite_store import build_sqlite
from core.rag import sqlite_rag
from core.rag.queries import RAG_QUERIES
from experiments.bench import datasets, harness

RAG_SCALES = [(100, 20), (300, 60), (500, 100), (1000, 200)]

OVERFAMILIAR_THRESHOLD = 6
SKILL = "Java"
MIN_LEVEL = 3
HOPS = 2
REPLACEMENT_TEAM_SIZE = 5
COHESION_TEAM_SIZE = 30

# neo4j 예열 로직(_prime_neo4j)은 삭제됐지만 이 상수는 남긴다 --
# experiments/report.py:731이 `from experiments.bench.exp4_rag import PRIMING_CALLS`로
# 이미 측정된(Plan 3, neo4j 포함) 스윕의 예열 라운드 수를 리포트 문안에 인용한다.
# 지우면 report.build()가 ImportError로 깨진다(Step 9에서 바로 잡히지만, 애초에
# 지우지 않는 편이 낫다). 이 값은 이제 이 파일의 어떤 함수에서도 쓰이지 않는다 --
# 오직 report.py의 과거 서술을 위해 존재한다.
PRIMING_CALLS = 200


def _args(ds) -> dict:
    ids = [p.id for p in ds.people]
    return {
        "swap_diff": (ids[0], ids[1]),
        "replacement_candidates": (SKILL, MIN_LEVEL, ids[:REPLACEMENT_TEAM_SIZE]),
        "team_cohesion": (ids[:COHESION_TEAM_SIZE],),
        "overfamiliar_pairs": (OVERFAMILIAR_THRESHOLD,),
        "skill_within_hops": (ids[0], SKILL, HOPS),
    }


def _calibrate_sqlite(conn) -> dict:
    m = harness.measure(lambda: conn.execute("SELECT 1").fetchall())
    return {"sqlite_noop_ms": m["median_ms"], "sqlite_noop_p95_ms": m["p95_ms"]}


def run(scales=None) -> dict:
    scales = scales or RAG_SCALES
    rows = []
    calibration: dict = {}

    for n_people, n_projects in scales:
        ds, parsed, _ = datasets.build_scale(n_people, n_projects, 42)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / f"exp4_{n_people}.db"
            build_sqlite(ds, parsed, db_path)
            conn = sqlite3.connect(db_path)
            try:
                if not calibration:
                    calibration.update(_calibrate_sqlite(conn))
                args = _args(ds)
                for q in RAG_QUERIES:
                    a = args[q]
                    s_rows = sqlite_rag.ALL[q](conn, *a)
                    rows.append({"backend": "sqlite", "query": q, "n_people": n_people,
                                 "n_projects": n_projects, "result_count": len(s_rows),
                                 **harness.measure(lambda q=q, a=a: sqlite_rag.ALL[q](conn, *a))})
            finally:
                conn.close()

    return {"rows": rows, "calibration": calibration,
            "args": {"skill": SKILL, "min_level": MIN_LEVEL, "hops": HOPS,
                     "overfamiliar_threshold": OVERFAMILIAR_THRESHOLD,
                     "replacement_team_size": REPLACEMENT_TEAM_SIZE,
                     "cohesion_team_size": COHESION_TEAM_SIZE}}


def _print_result_counts(rows) -> None:
    scales = sorted({r["n_people"] for r in rows})
    print("result_count (query x n_people):")
    for q in RAG_QUERIES:
        cells = {r["n_people"]: r["result_count"] for r in rows if r["query"] == q}
        print("  " + q.ljust(28) + "".join(f"{cells.get(n, '-'):>8}" for n in scales))


def main():
    out = run()
    path = harness.save_result("exp4_rag_sqlite_only", out)
    print(f"saved: {path}  rows={len(out['rows'])}")
    _print_result_counts(out["rows"])


if __name__ == "__main__":
    main()
