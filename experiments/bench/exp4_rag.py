"""실험 4: RAG 서브그래프 검색 워크로드 (SQLite vs Neo4j).

실험 1은 정해진 질의 하나를 쟀다. 여기서는 XAI 브리핑이 실제로 필요로 하는
5가지 형태를 재서, "임의 형태의 문맥 검색"이라는 Neo4j의 본래 용도에서
어떤지 본다. 판정 기준은 플랜 문서 §"의사결정 규칙"에 사전 고정돼 있다
(규칙 1: 20개 셀 중 Neo4j가 과반(11개 이상) 빠르면 keep).

공정성 장치가 이 러너의 본체다:
  1) JVM 워밍업 — Plan 2에서 웜업 부족으로 Neo4j 수치가 최대 8배 부풀려진
     전례가 있다. 스케일 루프 전에 _prime_neo4j로 엔진을 한 번에 데우고,
     neo4j 측정에만 NEO4J_WARMUP을 따로 준다.
  2) 라벨 스캔 — Plan 2에서 Neo4j가 NodeByLabelScan으로 dbHits의 78%를
     낭비했던 전례. Task 3의 PROFILE 테스트가 5개 질의 전부를
     NodeByLabelScan·AllNodesScan 양쪽에 대해 잠가 뒀다.
  3) 빈 결과 — 0행을 반환하는 질의를 재는 것은 워크로드가 아니라 양쪽의
     "아무것도 못 찾는 경로"를 재는 것이다. _args의 인자는 네 규모 전부에서
     0행이 아님을 실측으로 확인하고 고른 값이다(§_args 주석).
  4) 호출당 세션 비용 — Neo4j만 한쪽으로 지는 고정비다. 빼거나 숨기지 않고
     calibration으로 따로 측정해 공개한다(§_calibrate_neo4j).

적재(build_sqlite / load_neo4j)는 양쪽 다 측정 밖이다.
"""
import sqlite3
import tempfile
from pathlib import Path

from core.graph.sqlite_store import build_sqlite
from core.rag import neo4j_rag, sqlite_rag
from core.rag.queries import RAG_QUERIES, RETURN_KEYS
from experiments.bench import datasets, harness

RAG_SCALES = [(100, 20), (300, 60), (500, 100), (1000, 200)]

# neo4j 측정 전용 warmup(공유 harness.measure 기본값 3의 16배 이상). 기본값을
# 건드리면 다른 실험의 방법론까지 조용히 바뀌므로 여기서만 올린다 — 실험 1과
# 동일한 값이다.
NEO4J_WARMUP = 50
# 스케일 루프 전 1회 실행하는 예열 버스트의 반복 수(질의 2종 × 이 횟수).
# 스케일 처리 순서와 예열 상태를 분리해 "규모가 커질수록 빨라지는" 가짜 추세를
# 막는다(Plan 2 전례).
PRIMING_CALLS = 200

OVERFAMILIAR_THRESHOLD = 6
SKILL = "Java"
MIN_LEVEL = 3
HOPS = 2
# replacement_candidates의 대상 팀 크기. 네 규모에서 3/2/4/6행으로 모두 0행이 아니다.
REPLACEMENT_TEAM_SIZE = 5
# team_cohesion의 팀 크기. 브리프의 6은 네 규모 전부에서 0행이라 30으로 넓혔다
# (Task 3의 parity 케이스와 동일한 값). §_args 주석 참고.
COHESION_TEAM_SIZE = 30


def _args(ds) -> dict:
    """질의별 인자. 양쪽 백엔드에 동일한 값을 주고, 규모가 달라져도 값 자체는
    고정한다(결과 행 수는 규모에 따라 달라져도 되지만 인자는 달라지면 안 된다 —
    달라지면 규모 추세가 아니라 인자 변화를 재게 된다).

    team_cohesion만 브리프의 참조 코드(ids[:6])에서 바꿨다. 실측 결과 ids[:6]
    (p000..p005)은 네 규모 전부에서 0행이다 — 팀 내부에 WORKED_WITH 엣지가 아예
    없다. 빈 결과를 재면 워크로드가 아니라 "아무것도 못 찾는 경로" 두 개를 재는
    셈이고, 규모에 따라 0행이었다 아니었다 하면 추세 자체가 인공물이 된다.
    ids[:30]으로 넓히면 네 규모에서 각각 10/4/4/2행이 나오고, 이는 Task 3의
    parity 테스트가 쓰는 것과 같은 인자다.

    나머지 넷은 브리프 값 그대로 네 규모 전부에서 0행이 아님을 확인했다
    (실측 행 수는 리포트의 result_count 표 참고).
    """
    ids = [p.id for p in ds.people]
    return {
        "swap_diff": (ids[0], ids[1]),
        "replacement_candidates": (SKILL, MIN_LEVEL, ids[:REPLACEMENT_TEAM_SIZE]),
        "team_cohesion": (ids[:COHESION_TEAM_SIZE],),
        "overfamiliar_pairs": (OVERFAMILIAR_THRESHOLD,),
        "skill_within_hops": (ids[0], SKILL, HOPS),
    }


def _norm(rows, name):
    """비교용 정규화: 키 순서·부동소수 오차·행 순서를 제거한다.

    swap_diff의 'value' 필드만 문자열이라도 숫자로 파싱해 비교한다. 이 필드는
    skill/cowork/evidence 세 종류를 한 문자열 컬럼으로 묶느라 SQLite는
    `CAST(... AS TEXT)`, Cypher는 `toString()`을 쓰는데, 두 변환은 반복소수
    (1/3, 2/3 등)에서 유효자릿수가 달라 같은 IEEE754 double이라도 문자열이
    어긋난다(실측: 폴라리티 값의 약 25%. 예: 1/3 → SQLite '0.33333333333333332'
    vs Neo4j '0.3333333333333333'). 값은 같으므로 이대로 두면 표기 차이 때문에
    parity 불일치가 나고, 스윕은 불일치 1건에 중단하도록 돼 있다.

    코어션을 이 한 필드로 한정한 것은 의도적이다 — 문자열을 일괄로 float 변환하면
    다른 질의의 id나 스킬명이 우연히 숫자 형태일 때 조용히 정규화돼 진짜 불일치를
    가려버린다. tests/test_neo4j_rag.py::_norm과 같은 규칙이다.
    """
    out = []
    for r in rows:
        item = []
        for k in RETURN_KEYS[name]:
            v = r[k]
            if isinstance(v, float):
                v = round(v, 9)
            elif name == "swap_diff" and k == "value" and isinstance(v, str):
                try:
                    v = round(float(v), 9)
                except ValueError:
                    pass
            item.append(v)
        out.append(tuple(item))
    return sorted(out, key=lambda t: tuple(str(x) for x in t))


def _prime_neo4j(driver, ds) -> None:
    """스케일 루프 전에 JVM을 한 번에 예열한다 — 워밍업 상태가 규모 순서와
    교락되면 '규모가 커질수록 빨라지는' 가짜 추세가 만들어진다(Plan 2 전례).
    결과는 버리고 타이밍도 기록하지 않는다."""
    ids = [p.id for p in ds.people]
    for _ in range(PRIMING_CALLS):
        neo4j_rag.overfamiliar_pairs(driver, OVERFAMILIAR_THRESHOLD)
        neo4j_rag.swap_diff(driver, ids[0], ids[1])


def _calibrate_neo4j(driver) -> dict:
    """한쪽으로만 지는 호출당 고정비를 측정한다(보정이 아니라 공개용).

    neo4j_rag._run은 호출마다 driver.session()을 새로 연다. 반면 sqlite_rag는
    벤치마크가 이미 열어 둔 conn을 받는다 — 즉 Neo4j 표본에만 붙고 SQLite
    표본에는 없는 비용이 있다. 이건 Plan 2의 라벨 스캔과 같은 종류의 측정
    비대칭이며, 방향만 Neo4j에게 불리한 쪽이다.

    두 값을 잰다. session() 열고 닫기만 하는 비용은 실측 ~0.004ms로 사실상
    공짜인데, 파이썬 드라이버가 풀에서 커넥션을 실제 질의 시점까지 지연
    획득하기 때문이다. 그래서 이 값만으로는 실제 호출당 고정비를 크게 과소
    보고하게 된다. 커넥션 획득·왕복까지 포함된 바닥값(RETURN 1을 질의와 똑같은
    세션-당-호출 패턴으로)을 함께 재서 상한을 같이 남긴다.

    측정치는 median_ms에서 빼지 않는다 — 의사결정 규칙 1은 원 수치를 그대로
    센다. 이 값은 Task 7 리포트가 "Neo4j 열세 중 얼마가 세션 고정비인가"를
    말할 수 있게 하는 공개 자료다.
    """
    def _open_close():
        with driver.session():
            pass

    def _trivial_query():
        with driver.session() as s:
            s.run("RETURN 1").consume()

    open_close = harness.measure(_open_close, warmup=NEO4J_WARMUP)
    floor = harness.measure(_trivial_query, warmup=NEO4J_WARMUP)
    return {"neo4j_session_open_ms": open_close["median_ms"],
            "neo4j_session_open_p95_ms": open_close["p95_ms"],
            "neo4j_session_plus_trivial_query_ms": floor["median_ms"],
            "neo4j_session_plus_trivial_query_p95_ms": floor["p95_ms"]}


def _calibrate_sqlite(conn) -> dict:
    """대칭을 맞추기 위한 SQLite 쪽 바닥값: 이미 열린 conn에 대한 사소한 질의
    한 번. Neo4j 쪽 고정비와 같은 자리에 놓고 볼 수 있게 같은 harness.measure로
    잰다."""
    m = harness.measure(lambda: conn.execute("SELECT 1").fetchall())
    return {"sqlite_noop_ms": m["median_ms"], "sqlite_noop_p95_ms": m["p95_ms"]}


_EMPTY_CALIBRATION = {"neo4j_session_open_ms": None,
                      "neo4j_session_open_p95_ms": None,
                      "neo4j_session_plus_trivial_query_ms": None,
                      "neo4j_session_plus_trivial_query_p95_ms": None,
                      "sqlite_noop_ms": None, "sqlite_noop_p95_ms": None}


def run(scales=None, neo4j: bool = True) -> dict:
    scales = scales or RAG_SCALES
    rows, parity, skipped = [], [], []
    calibration = dict(_EMPTY_CALIBRATION)

    driver, load_neo4j = None, None
    if neo4j:
        try:
            from core.graph.neo4j_store import get_driver, load_neo4j
            driver = get_driver()
            driver.verify_connectivity()
        except Exception as exc:                   # 미기동/연결 실패는 조용히 넘기지 않는다
            skipped.append({"backend": "neo4j", "n_people": "all",
                            "reason": f"{type(exc).__name__}: {exc}"})
            driver = None
    else:
        skipped.append({"backend": "neo4j", "n_people": "all",
                        "reason": "neo4j=False 로 명시적으로 제외"})

    if driver is not None:
        ds0, parsed0, _ = datasets.build_scale(*scales[0], 42)
        load_neo4j(driver, ds0, parsed0)
        _prime_neo4j(driver, ds0)                  # 측정 전 1회 예열
        calibration.update(_calibrate_neo4j(driver))

    for n_people, n_projects in scales:
        ds, parsed, _ = datasets.build_scale(n_people, n_projects, 42)
        # tempfile.TemporaryDirectory는 컨텍스트 매니저라 예외가 나도(질의가
        # 터져도) __exit__에서 디렉터리를 지운다. n=1000 DB가 가장 커서,
        # 지우지 않으면 재실행마다 누적된다.
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / f"exp4_{n_people}.db"
            build_sqlite(ds, parsed, db_path)      # 적재는 측정 밖
            conn = sqlite3.connect(db_path)
            try:
                if calibration["sqlite_noop_ms"] is None:
                    calibration.update(_calibrate_sqlite(conn))

                if driver is not None:
                    load_neo4j(driver, ds, parsed)     # 적재는 측정 밖

                args = _args(ds)
                for q in RAG_QUERIES:
                    a = args[q]
                    s_rows = sqlite_rag.ALL[q](conn, *a)
                    rows.append({"backend": "sqlite", "query": q, "n_people": n_people,
                                 "n_projects": n_projects, "result_count": len(s_rows),
                                 **harness.measure(lambda q=q, a=a: sqlite_rag.ALL[q](conn, *a))})

                    if driver is not None:
                        n_rows = neo4j_rag.ALL[q](driver, *a)
                        parity.append({"query": q, "n_people": n_people,
                                       "sqlite_count": len(s_rows), "neo4j_count": len(n_rows),
                                       "match": _norm(s_rows, q) == _norm(n_rows, q)})
                        rows.append({"backend": "neo4j", "query": q, "n_people": n_people,
                                     "n_projects": n_projects, "result_count": len(n_rows),
                                     **harness.measure(
                                         lambda q=q, a=a: neo4j_rag.ALL[q](driver, *a),
                                         warmup=NEO4J_WARMUP)})
            finally:
                conn.close()        # 디렉터리 삭제 전에 핸들부터 닫는다

    if driver is not None:
        driver.close()
    return {"rows": rows, "parity": parity, "skipped": skipped,
            "calibration": calibration,
            "args": {"skill": SKILL, "min_level": MIN_LEVEL, "hops": HOPS,
                     "overfamiliar_threshold": OVERFAMILIAR_THRESHOLD,
                     "replacement_team_size": REPLACEMENT_TEAM_SIZE,
                     "cohesion_team_size": COHESION_TEAM_SIZE}}


def _print_result_counts(rows) -> None:
    """질의별·규모별 반환 행 수. 4행짜리 질의와 1390행짜리 질의는 같은 워크로드가
    아니므로, 승패 집계를 읽는 사람이 무엇을 비교한 것인지 볼 수 있어야 한다."""
    scales = sorted({r["n_people"] for r in rows})
    print("result_count (backend/query x n_people):")
    print("  " + "".ljust(36) + "".join(f"{n:>8}" for n in scales))
    for b in sorted({r["backend"] for r in rows}):
        for q in RAG_QUERIES:
            cells = {r["n_people"]: r["result_count"] for r in rows
                     if r["backend"] == b and r["query"] == q}
            print("  " + f"{b}/{q}".ljust(36)
                  + "".join(f"{cells.get(n, '-'):>8}" for n in scales))


def _print_win_tally(rows) -> None:
    """의사결정 규칙 1이 소비하는 수치: 20개 셀(질의 5 × 규모 4) 중 Neo4j가
    median_ms 기준으로 더 빠른 셀이 몇 개인가."""
    med = {(r["backend"], r["query"], r["n_people"]): r["median_ms"] for r in rows}
    scales = sorted({r["n_people"] for r in rows})
    wins = 0
    for q in RAG_QUERIES:
        for n in scales:
            s, g = med.get(("sqlite", q, n)), med.get(("neo4j", q, n))
            if s is not None and g is not None and g < s:
                wins += 1
    cells = sum(1 for q in RAG_QUERIES for n in scales
                if ("neo4j", q, n) in med and ("sqlite", q, n) in med)
    print(f"neo4j wins: {wins}/{cells} cells (median_ms 기준)")


def main():
    out = run()
    path = harness.save_result("exp4_rag", out)
    bad = [p for p in out["parity"] if not p["match"]]
    print(f"saved: {path}  rows={len(out['rows'])}  parity_mismatch={len(bad)}")
    _print_result_counts(out["rows"])
    _print_win_tally(out["rows"])
    cal = out["calibration"]
    print(f"calibration: neo4j session open={cal['neo4j_session_open_ms']} ms, "
          f"session+RETURN 1={cal['neo4j_session_plus_trivial_query_ms']} ms, "
          f"sqlite noop={cal['sqlite_noop_ms']} ms")
    if out["skipped"]:
        print(f"skipped: {out['skipped']}")
    if bad:
        print("불일치:", bad)


if __name__ == "__main__":
    main()
