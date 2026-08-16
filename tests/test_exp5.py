"""실험 5(영속성 벤치마크) 러너 검증 -- Plan 4에서 SQLite 전용으로 축소.

이 파일이 지키는 것은 "러너가 돌아간다"가 아니라 **계측이 유효하다**이다.
플랜 3 규칙 2(영속성 4지표 중 3+ 우위면 Neo4j keep)를 판정한 수치는 이미
experiments/results/exp5_persistence*.json에 고정돼 있고 이 축소된 러너는
그 판정을 다시 내리지 않는다 — 이 파일은 SQLite 쪽 계측 그 자체가 여전히
유효한지만 잠근다.

가장 중요한 잠금은 test_timed_append_sequence_does_not_corrupt_sqlite_rehydration
이다. 같은 (a, b) 쌍을 반복 측정에 재사용하면
  - SQLite는 review_item을 계속 누적해 ReviewSection(max_length=5)를 위반하고
    from_sqlite가 ValidationError로 죽는다(= rehydrate_ms 측정 자체가 불가),
  - csr_matrix는 중복 좌표를 합산해 cowork_months가 조용히 불어난다 —
즉 계측기가 조용히 무효화된다. 태스크 5 리포트 §6(1)이 지정한 컨트롤이다.
"""
import sqlite3

import pytest

from core.graph import rehydrate
from core.graph.sqlite_store import append_cowork, append_review, build_sqlite
from experiments.bench import datasets, harness
from experiments.bench import exp5_persistence as e5

SCALE = (100, 20)


@pytest.fixture(scope="module")
def sqlite_only():
    """한 규모만 돌린 결과. run()은 적재·재수화를 수십 회 반복하므로
    테스트마다 새로 돌리지 않고 모듈 스코프로 한 번만 만든다."""
    return e5.run(scales=[SCALE])


def test_run_reports_all_metrics(sqlite_only):
    metrics = {r["metric"] for r in sqlite_only["rows"] if r["backend"] == "sqlite"}
    assert {"load_ms", "disk_bytes", "append_review_ms", "append_cowork_ms",
            "rehydrate_ms"} <= metrics
    for r in sqlite_only["rows"]:
        assert r["value"] > 0
        assert r["unit"] in ("ms", "bytes")


def test_timing_rows_keep_the_full_measure_record(sqlite_only):
    """median/p95만 남기지 말고 harness.measure의 기록을 통째로 보존해야 한다.

    태스크 4 리뷰어는 커밋된 JSON만 보고 warmup 값을 감사했다. repeats=3에서는
    min/max 스프레드가 "웜업이 실제로 됐는가"를 볼 수 있는 유일한 신호이기도
    하다.
    """
    for r in sqlite_only["rows"]:
        if r["unit"] != "ms":
            continue
        for key in ("median_ms", "p95_ms", "min_ms", "max_ms", "repeats", "warmup"):
            assert key in r, f"{r['metric']} 행에 {key}가 없다"
        assert r["value"] == r["median_ms"]
        assert r["warmup"] >= 1
        assert r["min_ms"] <= r["median_ms"] <= r["max_ms"]


def test_calibration_records_the_shared_python_cost(sqlite_only):
    """rehydrate_ms 중 저장소와 무관한 몫(_assemble = MemoryGraph.build)을
    따로 재야 한다 — 이 몫이 지배적이면 지표가 저장소 성능에 둔감해진다
    (태스크 5 리포트 §6(3))."""
    by_metric = {(r["metric"], r["n_people"]): r for r in sqlite_only["rows"]}
    assemble = by_metric[("assemble_ms", SCALE[0])]
    unlink = by_metric[("sqlite_unlink_ms", SCALE[0])]
    assert assemble["value"] > 0
    assert unlink["value"] >= 0


def test_new_pairs_are_absent_from_the_dataset_and_mutually_distinct():
    """타이밍 루프가 쓸 쌍은 (1) 데이터셋에 방향 무관으로 없어야 하고
    (2) 서로 달라야 하며 (3) 실재하는 Person이어야 한다.

    (3)이 필요한 이유: 없는 id로 append하면 person 테이블 밖을 가리키는 고아
    레코드가 생겨 이 실험이 재려는 "실제 존재하는 두 사람 사이의 갱신"
    워크로드가 아니게 된다.
    """
    ds, _, _ = datasets.build_scale(*SCALE, 42)
    used = set()
    first = e5._new_pairs(ds, 40, used)
    second = e5._new_pairs(ds, 40, used)

    occupied = {frozenset((c.a_id, c.b_id)) for c in ds.coworks}
    occupied |= {frozenset((r.reviewer_id, r.reviewee_id)) for r in ds.reviews}
    keys = [frozenset(p) for p in first + second]
    assert len(keys) == 80
    assert len(set(keys)) == 80, "생성된 쌍에 중복이 있다"
    assert not (set(keys) & occupied), "데이터셋에 이미 있는 쌍이 섞였다"

    ids = {p.id for p in ds.people}
    assert all(a in ids and b in ids for a, b in first + second)

    # 결정적이어야 재현이 된다.
    assert e5._new_pairs(ds, 40, set()) == first


def test_timed_append_sequence_does_not_corrupt_sqlite_rehydration(tmp_path):
    """append 측정이 쓰는 시퀀스를 그대로 흘려도 재수화가 살아 있어야 한다.

    같은 쌍을 재사용하면 6번째 append_review부터 from_sqlite가 ValidationError로
    죽는다. 25회로 그 한계(max_length=5)를 넉넉히 넘겨 확인한다.
    """
    ds, parsed, _ = datasets.build_scale(*SCALE, 42)
    db = tmp_path / "append_seq.db"
    build_sqlite(ds, parsed, db)
    conn = sqlite3.connect(db)
    try:
        used = set()
        records = e5._cowork_records(e5._new_pairs(ds, 25, used))
        payloads = e5._review_payloads(ds, parsed, e5._new_pairs(ds, 25, used))
        for rec in records:
            append_cowork(conn, rec)
        for review, pr in payloads:
            append_review(conn, review, pr)

        g = rehydrate.from_sqlite(conn)

        for rec in records:
            i, j = g.pid_index[rec.a_id], g.pid_index[rec.b_id]
            assert g.cowork_months[i, j] == rec.co_months, (
                "cowork_months가 append 값과 다르다 — 중복 좌표가 합산됐다")
        for review, _pr in payloads:
            key = tuple(sorted((g.pid_index[review.reviewer_id],
                                g.pid_index[review.reviewee_id])))
            assert key in g.pair_review_score, "append한 리뷰가 재수화에 없다"
    finally:
        conn.close()


def test_generated_pair_pool_matches_harness_measure_call_count():
    """n_appends = APPEND_WARMUP + APPEND_REPEATS는 harness.measure의 호출
    횟수와 **우연히** 같다. measure의 루프가 바뀌면 피더가 StopIteration으로
    죽거나(시끄럽고 괜찮음) 조용히 더 적은 쌍만 시간 재게 된다. 결합을 잠근다.
    """
    ds, _, _ = datasets.build_scale(*SCALE, 42)
    n_appends = e5.APPEND_WARMUP + e5.APPEND_REPEATS
    recs = e5._cowork_records(e5._new_pairs(ds, n_appends, set()))
    nxt = e5._feeder(recs)
    seen = []
    harness.measure(lambda: seen.append(nxt()),
                    repeats=e5.APPEND_REPEATS, warmup=e5.APPEND_WARMUP)
    assert len(seen) == n_appends, (
        f"harness.measure가 {len(seen)}회 호출했다 — 풀은 {n_appends}개다")
    assert len({(r.a_id, r.b_id) for r in seen}) == n_appends, "재사용된 쌍이 있다"
    with pytest.raises(StopIteration):
        nxt()                                       # 풀이 정확히 소진됐다


def test_run_accepts_a_subset_of_scales_so_a_single_scale_can_be_rerun():
    out = e5.run(scales=[(300, 60)])
    assert {r["n_people"] for r in out["rows"]} == {300}


def test_committed_decision_artifacts_untouched_by_this_runner():
    """판정 근거 exp5_persistence.json / exp5_persistence_primed.json은 이
    축소된 러너가 절대 건드리지 않는다 -- main()이 다른 이름
    (exp5_persistence_sqlite_only)으로 저장하므로 존재 자체로 증명된다."""
    for name in ("exp5_persistence", "exp5_persistence_primed"):
        result = harness.load_result(name)  # 없으면 FileNotFoundError로 실패해야 정상.
        assert any(r["backend"] == "neo4j" for r in result["data"]["rows"]), (
            f"커밋된 판정 아티팩트({name})는 neo4j 행을 포함해야 한다 -- "
            "축소된 러너로 덮어써지면 이 값이 사라진다")


def test_save_partial_writes_a_partial_marked_json(tmp_path):
    """_save_partial은 Plan 4에서 run()의 시그니처가 축소되며 이 파일 안에서는
    더 이상 호출되지 않지만(도커 재기동에 기인하던 스윕 중단 위험이 사라졌다),
    브리핑의 "SQLite 전용으로 남는 것" 목록이 명시적으로 지정하므로 그대로
    남겨 뒀다 -- 재개 가능한 스윕이 다시 필요해지면 재사용할 수 있어야 하므로
    최소한 동작은 계속 확인한다."""
    path = tmp_path / "partial.json"
    e5._save_partial({"rows": [{"backend": "sqlite", "value": 1}]}, path)
    import json
    doc = json.loads(path.read_text("utf-8"))
    assert doc["partial"] is True
    assert doc["data"]["rows"][0]["backend"] == "sqlite"
