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
from experiments.bench import datasets, harness
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


@pytest.mark.neo4j
def test_priming_exercises_the_light_path_and_its_writes_are_wiped(monkeypatch):
    """예열은 무거운 경로만으로는 부족하다 — 첫 스윕에서 append_* / noop MATCH /
    세션 획득이 규모에 따라 단조 감소했다(규모 순서와 교락된 하강 램프).
    _prime_neo4j가 **실제 append 경로**까지 도는지 확인하고, 동시에 그 쓰기가
    측정 전에 사라진다는 안전 주장(load_neo4j의 DETACH DELETE)도 확인한다.
    """
    from core.graph.neo4j_store import get_driver, load_neo4j

    monkeypatch.setattr(e5, "PRIMING_ROUNDS", 1)
    monkeypatch.setattr(e5, "LIGHT_PRIMING_ROUNDS", 5)
    ds, parsed, _ = datasets.build_scale(*e5.PRIMING_SCALE, 42)
    pairs = e5._new_pairs(ds, 5, set())              # 예열이 쓰는 협업 쌍과 동일(결정적)

    def written():
        with driver.session() as s:
            return sum(s.run("MATCH (:Person {id:$a})-[w:WORKED_WITH]->(:Person {id:$b}) "
                             "RETURN count(w) AS c", a=a, b=b).single()["c"]
                       for a, b in pairs)

    driver = get_driver()
    try:
        e5._prime_neo4j(driver, load_neo4j)
        assert written() == len(pairs), "예열이 가벼운 경로(append)를 돌지 않았다"

        load_neo4j(driver, ds, parsed)               # 각 규모가 이것으로 시작한다
        assert written() == 0, "예열이 만든 쓰기가 측정 전에 지워지지 않았다"
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


def test_rule2_tally_is_persisted_not_just_printed(sqlite_only):
    """규칙 2 집계는 태스크 7이 인용할 숫자다 — stdout이 아니라 결과 payload에
    있어야 뒷받침하는 아티팩트가 생긴다. 어느 disk 읽기로 셌는지도 함께."""
    tally = sqlite_only["rule2_tally"]
    assert tally["adjudicated_on"] == e5.RULE2_DISK_METRIC
    for reading in ("disk_bytes", "disk_bytes_clean"):
        t = tally["by_disk_reading"][reading]
        assert t["disk_metric"] == reading
        assert t["keep_threshold"] == 3
        assert t["keep"] is (t["neo4j_metric_wins"] >= 3)
        labels = [m["metric"] for m in t["per_metric"]]
        assert labels == ["load_ms", "disk_bytes", "append_*_ms", "rehydrate_ms"]
        # neo4j 없이 돌린 결과이므로 비교 가능한 셀이 0이고 우위 지표도 0이어야 한다.
        assert all(m["cells"] == 0 and not m["neo4j_ahead"] for m in t["per_metric"])
        assert t["neo4j_metric_wins"] == 0


def test_rule2_tally_counts_cells_and_flips_with_the_disk_reading():
    """집계 로직 자체를 합성 행으로 잠근다 — append_*는 두 연산을 한 지표로
    합쳐 세고, disk 지표는 어느 읽기를 쓰느냐로 답이 달라질 수 있다."""
    def row(backend, metric, n, value):
        return {"backend": backend, "n_people": n, "metric": metric, "value": value}

    rows = []
    for n in (100, 300):
        rows += [row("sqlite", "load_ms", n, 1.0), row("neo4j", "load_ms", n, 2.0),
                 row("sqlite", "rehydrate_ms", n, 1.0), row("neo4j", "rehydrate_ms", n, 2.0),
                 # append: neo4j가 4셀 중 3셀 우위 → 지표 우위
                 row("sqlite", "append_cowork_ms", n, 1.0),
                 row("neo4j", "append_cowork_ms", n, 0.5),
                 row("sqlite", "append_review_ms", n, 1.0),
                 row("neo4j", "append_review_ms", n, 0.5 if n == 100 else 2.0),
                 # 오염된 읽기에서만 neo4j 우위, clean에서는 열세
                 row("sqlite", "disk_bytes", n, 10.0), row("neo4j", "disk_bytes", n, 5.0),
                 row("sqlite", "disk_bytes_clean", n, 10.0),
                 row("neo4j", "disk_bytes_clean", n, 20.0)]

    contaminated = e5._rule2_tally(rows, "disk_bytes")
    clean = e5._rule2_tally(rows, "disk_bytes_clean")
    by_label = {m["metric"]: m for m in clean["per_metric"]}
    assert by_label["append_*_ms"]["cells"] == 4
    assert by_label["append_*_ms"]["neo4j_wins"] == 3
    assert by_label["append_*_ms"]["neo4j_ahead"] is True
    assert by_label["load_ms"]["neo4j_ahead"] is False
    assert clean["neo4j_metric_wins"] == 1 and clean["keep"] is False
    assert contaminated["neo4j_metric_wins"] == 2 and contaminated["keep"] is False


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


def test_wipe_target_guard_rejects_paths_that_are_not_the_repo_neo4j_data_dir(tmp_path):
    """rm -rf 가드는 상수를 정의한 식을 되풀이하는 assert가 아니라 실제
    전제 검사여야 한다(python -O에서 assert는 사라진다)."""
    assert e5._check_wipe_target(e5.NEO4J_DATA_DIR).name == "data"

    for bad in (tmp_path / "data",                      # 리포 밖
                tmp_path / ".neo4j" / "nope"):          # 이름이 data가 아님
        bad.mkdir(parents=True)
        with pytest.raises(RuntimeError, match="삭제 거부"):
            e5._check_wipe_target(bad)

    link_parent = tmp_path / "link_root"
    link_parent.mkdir()
    link = link_parent / "data"
    link.symlink_to(tmp_path / "data")
    with pytest.raises(RuntimeError, match="심링크"):
        e5._check_wipe_target(link)
