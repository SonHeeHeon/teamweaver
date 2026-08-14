"""실험 5(영속성 벤치마크) 러너 검증.

이 파일이 지키는 것은 "러너가 돌아간다"가 아니라 **계측이 유효하다**이다.
플랜 3 규칙 2(영속성 4지표 중 3+ 우위면 Neo4j keep)를 판정하는 수치가
exp5_persistence.run()에서 나오므로, 계측을 조용히 무효화하는 경로를 잠근다.

가장 중요한 잠금은 test_timed_append_sequence_does_not_corrupt_sqlite_rehydration
이다. 브리프 참조 코드처럼 같은 (a, b) 쌍을 반복 측정에 재사용하면
  - SQLite는 review_item을 계속 누적해 ReviewSection(max_length=5)를 위반하고
    from_sqlite가 ValidationError로 죽는다(= rehydrate_ms 측정 자체가 불가),
  - csr_matrix는 중복 좌표를 합산해 cowork_months가 조용히 불어나며,
  - Neo4j 쪽 MERGE는 멱등이라 아무 일도 없다 —
즉 계측기가 SQLite 편에서만 깨진다. 태스크 5 리포트 §6(1)이 지정한 컨트롤이다.
"""
import json
import sqlite3

import pytest

from core.graph import rehydrate
from core.graph.sqlite_store import append_cowork, append_review, build_sqlite
from experiments.bench import datasets
from experiments.bench import exp5_persistence as e5

SCALE = (100, 20)


@pytest.fixture(scope="module")
def sqlite_only():
    """Neo4j 없이 한 규모만 돌린 결과. run()은 적재·재수화를 수십 회 반복하므로
    테스트마다 새로 돌리지 않고 모듈 스코프로 한 번만 만든다."""
    return e5.run(scales=[SCALE], neo4j=False)


def test_run_reports_all_metrics(sqlite_only):
    metrics = {r["metric"] for r in sqlite_only["rows"] if r["backend"] == "sqlite"}
    assert {"load_ms", "disk_bytes", "append_review_ms", "append_cowork_ms",
            "rehydrate_ms"} <= metrics
    for r in sqlite_only["rows"]:
        assert r["value"] > 0
        assert r["unit"] in ("ms", "bytes")


def test_neo4j_exclusion_is_recorded(sqlite_only):
    assert any("neo4j" in s["backend"] for s in sqlite_only["skipped"])


def test_timing_rows_keep_the_full_measure_record(sqlite_only):
    """median/p95만 남기지 말고 harness.measure의 기록을 통째로 보존해야 한다.

    태스크 4 리뷰어는 커밋된 JSON만 보고 warmup 값을 감사했다. repeats=3에서는
    min/max 스프레드가 "JVM이 실제로 데워졌는가"를 볼 수 있는 유일한 신호이기도
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
    per_scale = sqlite_only["calibration"]["per_scale"]
    cell = next(c for c in per_scale if c["n_people"] == SCALE[0])
    assert cell["assemble_ms"] > 0
    assert cell["sqlite_unlink_ms"] >= 0


def test_new_pairs_are_absent_from_the_dataset_and_mutually_distinct():
    """타이밍 루프가 쓸 쌍은 (1) 데이터셋에 방향 무관으로 없어야 하고
    (2) 서로 달라야 하며 (3) 실재하는 Person이어야 한다.

    (3)이 필요한 이유: Neo4j의 append_*는 두 Person을 MATCH한 뒤 MERGE한다.
    존재하지 않는 id를 쓰면 MATCH가 0행이라 MERGE가 아예 실행되지 않아
    Neo4j만 "아무 일도 안 하는" 측정이 된다.
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

    # 결정적이어야 두 백엔드에 같은 시퀀스를 줄 수 있고 재현도 된다.
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


@pytest.mark.neo4j
def test_neo4j_append_sequence_actually_writes_every_generated_pair():
    """생성한 쌍이 Neo4j에서 실제로 쓰이는지 확인한다.

    Neo4j의 append_*는 두 Person을 MATCH한 뒤 MERGE한다 — id가 실재하지 않으면
    MATCH가 0행이라 MERGE가 실행되지 않고 **아무것도 쓰지 않은 채 조용히
    성공한다.** 그러면 append_*_ms는 "빈 질의 왕복 시간"이 되어 Neo4j에
    부당하게 유리해진다(0/4 판정에서는 방향이 반대지만, 계측이 무효인 것은
    어느 방향이든 똑같이 심각하다). 쓰기가 실제로 남았는지 되읽어 확인한다.
    """
    from core.graph.neo4j_store import append_cowork as n_append_cowork
    from core.graph.neo4j_store import append_review as n_append_review
    from core.graph.neo4j_store import get_driver, load_neo4j

    ds, parsed, _ = datasets.build_scale(*SCALE, 42)
    used = set()
    records = e5._cowork_records(e5._new_pairs(ds, 25, used))
    payloads = e5._review_payloads(ds, parsed, e5._new_pairs(ds, 25, used))

    driver = get_driver()
    try:
        load_neo4j(driver, ds, parsed)
        for rec in records:
            n_append_cowork(driver, rec)
        for review, pr in payloads:
            n_append_review(driver, review, pr)
        with driver.session() as s:
            for rec in records:
                row = s.run("MATCH (:Person {id:$a})-[w:WORKED_WITH]->(:Person {id:$b}) "
                            "RETURN w.co_months AS m", a=rec.a_id, b=rec.b_id).single()
                assert row is not None, f"{rec.a_id}->{rec.b_id} WORKED_WITH가 없다"
                assert row["m"] == rec.co_months
            for review, pr in payloads:
                row = s.run("MATCH (:Person {id:$a})-[v:REVIEWED]->(:Person {id:$b}) "
                            "RETURN v.polarity AS pol, v.evidence AS ev",
                            a=review.reviewer_id, b=review.reviewee_id).single()
                assert row is not None, (
                    f"{review.reviewer_id}->{review.reviewee_id} REVIEWED가 없다")
                assert row["pol"] == pytest.approx(pr.text_polarity)
                # evidence는 Neo4j만 쓰는 필드다 — 이 비대칭이 append_review_ms와
                # disk_bytes 양쪽에 실재한다는 증거를 코드로 남긴다.
                assert row["ev"] == pr.evidence
    finally:
        driver.close()


def test_partial_results_are_written_after_each_completed_scale(tmp_path, monkeypatch):
    """스윕이 도중에 끊겨도 끝난 규모는 남아야 한다.

    태스크 4에서 스윕 재실행이 이전 결과를 통째로 덮어써 잃은 전례가 있다.
    두 번째 규모에서 강제로 예외를 내고, 첫 규모 결과가 partial 파일에 남아
    있는지 본다.
    """
    partial = tmp_path / "exp5.partial.json"
    real_build = datasets.build_scale
    seen = []

    def flaky(n_people, n_projects, seed=42):
        seen.append(n_people)
        if len(seen) > 1:
            raise RuntimeError("스윕 중단 모의")
        return real_build(n_people, n_projects, seed)

    monkeypatch.setattr(e5.datasets, "build_scale", flaky)
    with pytest.raises(RuntimeError):
        e5.run(scales=[SCALE, (300, 60)], neo4j=False, partial_path=partial)

    doc = json.loads(partial.read_text("utf-8"))
    assert doc["partial"] is True
    assert doc["data"]["completed_scales"] == [list(SCALE)]
    assert {r["n_people"] for r in doc["data"]["rows"]} == {SCALE[0]}


def test_run_accepts_a_subset_of_scales_so_an_interrupted_sweep_can_resume():
    out = e5.run(scales=[(300, 60)], neo4j=False)
    assert {r["n_people"] for r in out["rows"]} == {300}
    assert out["completed_scales"] == [[300, 60]]
