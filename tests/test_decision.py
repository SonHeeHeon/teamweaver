"""저장 계층 의사결정 규칙 검증.

이 파일이 지키는 것은 "함수가 돈다"가 아니라 **사전 고정된 규칙이 데이터에
의해 판정된다**이다. 판정이 사람 손을 타면 플랜 3의 설계 전체가 무의미해지므로,
아래 세 종류를 잠근다.

1. 규칙 자체 — 인공 픽스처로 네 갈래(keep/keep/keep_expressiveness/drop)를 모두
   태운다. 브리프 참조 테스트를 그대로 쓰되 규칙 4 케이스는 `in (3, 4)` 같은
   느슨한 단언을 쓰지 않는다(어느 쪽이 나와도 통과하는 테스트는 아무것도
   검증하지 않는다 — 실제 SPECS에서 복합 질의는 임계 미만이라 규칙 4가
   결정론적으로 적용된다).
2. **커밋된 실제 아티팩트** — 픽스처만 통과하고 실물에서 깨지는 것을 막는다.
   실제 파일은 harness.save_result가 씌운 래퍼(`{"environment", "data"}`)를
   갖고 있고, exp5 rows에는 브리프에 없는 `disk_bytes_clean`이 들어 있다.
   브리프의 `_LOWER_IS_BETTER` 집합은 이 이름을 빠뜨려 Neo4j의 541 MB가
   SQLite의 147 KB를 "이기는" 가짜 우위를 만들었다 — 규칙 2를 판정하는 바로 그
   지표에서. 아래 test_disk_bytes_clean_counts_smaller_as_better가 그 회귀를
   고정한다.
3. **Task 6이 JSON에 남긴 집계와의 교차 검증** — 같은 수치를 서로 다른 코드가
   독립으로 계산해 일치하는지 본다. 어긋나면 손으로 화해시키지 말고 테스트가
   깨져야 한다.
"""
import json

import pytest

from experiments import decision
from experiments.bench import harness


# ---------------------------------------------------------------------------
# 브리프 참조 픽스처 (래퍼 없는 안쪽 payload — evaluate가 받는 형태)
# ---------------------------------------------------------------------------

def _exp4(neo_wins: int, total: int = 20):
    """neo_wins개 셀에서 neo4j가 빠른 가짜 결과를 만든다."""
    rows = []
    for i in range(total):
        n = 100 + i
        fast, slow = (1.0, 2.0) if i < neo_wins else (2.0, 1.0)
        rows.append({"backend": "neo4j", "query": f"q{i}", "n_people": n,
                     "median_ms": fast, "p95_ms": fast, "result_count": 1})
        rows.append({"backend": "sqlite", "query": f"q{i}", "n_people": n,
                     "median_ms": slow, "p95_ms": slow, "result_count": 1})
    return {"rows": rows, "parity": [], "skipped": []}


def _exp5(neo_better: int):
    """4개 지표 중 neo_better개에서 neo4j가 우위인 가짜 결과."""
    metrics = ["load_ms", "disk_bytes", "append_cowork_ms", "rehydrate_ms"]
    rows = []
    for i, m in enumerate(metrics):
        neo, sql = (1.0, 2.0) if i < neo_better else (2.0, 1.0)
        rows.append({"backend": "neo4j", "n_people": 100, "metric": m,
                     "value": neo, "unit": "ms", "p95": None})
        rows.append({"backend": "sqlite", "n_people": 100, "metric": m,
                     "value": sql, "unit": "ms", "p95": None})
    return {"rows": rows, "skipped": []}


def _cell(backend: str, metric: str, n: int, value: float) -> dict:
    return {"backend": backend, "n_people": n, "metric": metric,
            "value": value, "unit": "ms"}


# ---------------------------------------------------------------------------
# 규칙 네 갈래 (브리프 참조 테스트)
# ---------------------------------------------------------------------------

def test_rule1_keep_when_neo4j_wins_majority():
    d = decision.evaluate(_exp4(11), _exp5(0))
    assert d["rule"] == 1 and d["verdict"] == "keep"


def test_rule2_keep_on_persistence_when_perf_loses():
    d = decision.evaluate(_exp4(3), _exp5(3))
    assert d["rule"] == 2 and d["verdict"] == "keep"


def test_rule4_drop_when_neither():
    """브리프 참조 테스트는 `rule in (3, 4)` / `verdict in (...)`였다 — 실제
    SPECS에서 복합 질의 수는 임계(3) 미만이므로 규칙 4가 결정론적으로 적용된다.
    어느 쪽이 나와도 통과하는 단언은 아무것도 검증하지 않으므로 정확히 못박는다."""
    d = decision.evaluate(_exp4(3), _exp5(1))
    assert d["rule"] == 4
    assert d["verdict"] == "drop"


def test_evidence_records_counts():
    d = decision.evaluate(_exp4(7), _exp5(2))
    assert d["evidence"]["neo4j_faster_cells"] == 7
    assert d["evidence"]["total_cells"] == 20
    assert d["evidence"]["neo4j_better_persistence_metrics"] == 2


def test_rationale_is_substantive():
    d = decision.evaluate(_exp4(3), _exp5(1))
    assert len(d["rationale"]) > 40


# ---------------------------------------------------------------------------
# 방향(lower-is-better) — 부호 뒤집힘 회귀
# ---------------------------------------------------------------------------

def test_disk_bytes_clean_counts_smaller_as_better():
    """`disk_bytes_clean`은 실제 exp5 rows에 있는 지표 이름이다.

    브리프의 `_LOWER_IS_BETTER` 집합에는 이 이름이 없어 "크면 좋다" 분기로
    떨어졌고, Neo4j의 541 MB가 SQLite의 147 KB를 이기는 가짜 우위가 만들어졌다
    — 규칙 2를 판정하는 바로 그 지표에서. 값 자체로 방향을 확인한다.
    """
    rows = [_cell("neo4j", "disk_bytes_clean", 100, 541_458_599),
            _cell("sqlite", "disk_bytes_clean", 100, 147_456)]
    for m in ("load_ms", "append_cowork_ms", "rehydrate_ms"):
        rows += [_cell("neo4j", m, 100, 9.0), _cell("sqlite", m, 100, 1.0)]
    d = decision.evaluate(_exp4(0), {"rows": rows, "skipped": []})
    assert d["evidence"]["neo4j_better_persistence_metrics"] == 0, \
        "541 MB가 147 KB를 이기면 안 된다"


def test_unknown_metric_raises_instead_of_guessing_a_direction():
    """손으로 관리하는 멤버십 집합은 지표가 하나 늘면 조용히 틀린 방향을 고른다.
    모르는 지표는 방향을 추측하지 말고 크게 깨져야 한다."""
    rows = [_cell("neo4j", "brand_new_metric_ms", 100, 1.0),
            _cell("sqlite", "brand_new_metric_ms", 100, 2.0)]
    with pytest.raises(ValueError, match="brand_new_metric_ms"):
        decision.evaluate(_exp4(0), {"rows": rows, "skipped": []})


# ---------------------------------------------------------------------------
# 규칙 2의 분모와 집계 방식
# ---------------------------------------------------------------------------

def test_rule2_denominator_is_exactly_four():
    """사전 고정 규칙은 "영속성 **4**지표 중 **3**+"이다. exp5 rows에는 지표
    이름이 여섯 개 있으므로(load/disk/disk_clean/append_cowork/append_review/
    rehydrate) 이름마다 하나씩 세면 분모가 조용히 6이 되어 사전 고정 임계를
    사후 변경하는 셈이 된다."""
    exp5 = _real_exp5_primed()
    d = decision.evaluate(_real_exp4(), exp5)
    assert d["evidence"]["total_persistence_metrics"] == 4
    assert decision.PERSISTENCE_THRESHOLD == 3


def test_append_metrics_are_merged_into_one_metric():
    """append_cowork_ms와 append_review_ms는 사전 고정 규칙의 "증분 갱신 비용"
    한 지표다. 둘로 세면 4지표가 5지표가 되고 임계의 의미가 달라진다."""
    rows = []
    for m in ("append_cowork_ms", "append_review_ms"):          # neo4j 우위
        rows += [_cell("neo4j", m, 100, 1.0), _cell("sqlite", m, 100, 2.0)]
    for m in ("load_ms", "disk_bytes", "rehydrate_ms"):          # sqlite 우위
        rows += [_cell("neo4j", m, 100, 2.0), _cell("sqlite", m, 100, 1.0)]
    d = decision.evaluate(_exp4(0), {"rows": rows, "skipped": []})
    assert d["evidence"]["neo4j_better_persistence_metrics"] == 1
    assert d["evidence"]["total_persistence_metrics"] == 4


def test_rule2_decides_by_cell_majority_not_by_averaging_across_scales():
    """브리프 참조 구현은 규모별 값을 평균낸 뒤 비교했다. Task 6이 JSON에 남긴
    집계는 **규모 셀의 과반**으로 센다. 한쪽 규모에 큰 이상치가 있으면 두 방식이
    갈린다 — 아래 데이터에서 평균은 Neo4j 우위(9.5 vs 100.75), 셀 과반은
    SQLite 우위(3셀 중 1셀)다. 커밋된 tally와 맞추려면 셀 과반이어야 한다.
    """
    rows = []
    for n, neo, sql in ((100, 1.0, 400.0), (300, 9.0, 1.0), (500, 18.5, 2.0)):
        rows += [_cell("neo4j", "load_ms", n, neo), _cell("sqlite", "load_ms", n, sql)]
    for m in ("disk_bytes", "append_cowork_ms", "rehydrate_ms"):
        rows += [_cell("neo4j", m, 100, 2.0), _cell("sqlite", m, 100, 1.0)]
    d = decision.evaluate(_exp4(0), {"rows": rows, "skipped": []})
    per = {m["metric"]: m for m in d["evidence"]["rule2_per_metric"]}
    assert per["load_ms"]["cells"] == 3 and per["load_ms"]["neo4j_wins"] == 1
    assert per["load_ms"]["neo4j_ahead"] is False
    assert d["evidence"]["neo4j_better_persistence_metrics"] == 0


def test_disk_group_prefers_the_clean_reading_and_records_which_it_used():
    """오염된 누적 판독(disk_bytes)과 클린 판독(disk_bytes_clean)이 모두 있으면
    클린 쪽으로 판정하고, **어느 판독으로 셌는지 근거에 남겨야 한다**(A-11).
    지표를 통째로 빼면 규칙 2가 몰래 "3 of 3"이 되어 사전 고정 규칙이 바뀐다."""
    rows = [_cell("neo4j", "disk_bytes", 100, 1.0),          # 오염 판독은 neo4j 우위
            _cell("sqlite", "disk_bytes", 100, 2.0),
            _cell("neo4j", "disk_bytes_clean", 100, 2.0),    # 클린 판독은 열세
            _cell("sqlite", "disk_bytes_clean", 100, 1.0)]
    for m in ("load_ms", "append_cowork_ms", "rehydrate_ms"):
        rows += [_cell("neo4j", m, 100, 2.0), _cell("sqlite", m, 100, 1.0)]
    d = decision.evaluate(_exp4(0), {"rows": rows, "skipped": []})
    assert d["evidence"]["disk_reading"] == "disk_bytes_clean"
    assert d["evidence"]["neo4j_better_persistence_metrics"] == 0

    only_contaminated = [r for r in rows if r["metric"] != "disk_bytes_clean"]
    d2 = decision.evaluate(_exp4(0), {"rows": only_contaminated, "skipped": []})
    assert d2["evidence"]["disk_reading"] == "disk_bytes"


# ---------------------------------------------------------------------------
# 규칙 3 — 두 해석
# ---------------------------------------------------------------------------

def test_rule3_reports_both_readings_and_both_are_below_threshold():
    """A-6: enum 기준 2/5, 규칙 3의 문자 그대로의 프로즈("재귀 CTE 또는 3중 이상
    서브쿼리") 기준 1/5. 둘 다 임계 미달이므로 판정은 바뀌지 않지만 **둘 다**
    드러나야 한다."""
    d = decision.evaluate(_real_exp4(), _real_exp5_primed())
    ev = d["evidence"]
    assert ev["complex_sql_queries"] == 2
    assert ev["complex_sql_queries_literal"] == 1
    assert ev["total_queries"] == 5
    assert ev["complex_sql_queries"] < decision.COMPLEXITY_THRESHOLD
    assert ev["complex_sql_queries_literal"] < decision.COMPLEXITY_THRESHOLD


def test_rule3_uses_the_reading_most_favourable_to_neo4j():
    """두 해석이 갈릴 때 Neo4j에 **불리한** 쪽을 골라 drop을 만들면 안 된다 —
    판정에 쓰는 값은 둘 중 큰 쪽이어야 한다."""
    ev = decision.evaluate(_real_exp4(), _real_exp5_primed())["evidence"]
    assert ev["complex_sql_queries_adjudicated"] == max(
        ev["complex_sql_queries"], ev["complex_sql_queries_literal"])


def test_literal_reading_is_derived_from_the_actual_sql_not_from_the_label():
    """프로즈 해석은 라벨을 다시 읽는 것이 아니라 실제 SQL에서 세어야 한다.
    team_cohesion의 상관 서브쿼리는 정확히 1개이고, 재귀 CTE는
    skill_within_hops에만 있다 — 라벨(enum)과 독립으로 확인한다."""
    counts = decision.sql_structure()
    assert counts["team_cohesion"]["subqueries"] == 1
    assert counts["team_cohesion"]["recursive_cte"] is False
    assert counts["skill_within_hops"]["recursive_cte"] is True
    for q in ("swap_diff", "replacement_candidates", "overfamiliar_pairs"):
        assert counts[q]["subqueries"] == 0 and counts[q]["recursive_cte"] is False


def test_sql_structure_agrees_with_the_committed_enum_labels():
    """실제 SQL에서 센 구조와 SPECS의 enum 라벨이 어긋나면 둘 중 하나가 낡은
    것이다 — 조용히 넘기지 말고 깨져야 한다."""
    from core.rag.queries import RAG_QUERIES, SPECS
    counts = decision.sql_structure()
    for q in RAG_QUERIES:
        assert counts[q]["recursive_cte"] == (SPECS[q].sql_complexity == "recursive_cte"), q


# ---------------------------------------------------------------------------
# 커밋된 실제 아티팩트
# ---------------------------------------------------------------------------

def _real_exp4() -> dict:
    return decision.payload(harness.load_result("exp4_rag"))


def _real_exp5_primed() -> dict:
    return decision.payload(harness.load_result("exp5_persistence_primed"))


def _real_exp5_first() -> dict:
    return decision.payload(harness.load_result("exp5_persistence"))


def test_payload_accepts_both_the_wrapped_and_the_bare_shape():
    """harness.save_result는 `{"environment", "generated_at_note", "data"}`로
    감싼다. 브리프의 픽스처는 감싸지 않은 안쪽 payload다 — 경계에서 한 번만
    벗기고 evaluate는 안쪽만 본다."""
    bare = _exp4(3)
    wrapped = {"environment": {}, "generated_at_note": "x", "data": bare}
    assert decision.payload(wrapped) is bare
    assert decision.payload(bare) is bare


def test_evaluate_runs_on_the_committed_artifacts_and_returns_drop():
    """픽스처만 통과하고 실물에서 깨지는 것을 막는 테스트 — `exp4["rows"]`가
    실제 파일에서는 KeyError이던 문제를 잡는 자리다."""
    d = decision.evaluate(_real_exp4(), _real_exp5_primed())
    ev = d["evidence"]
    assert (ev["neo4j_faster_cells"], ev["total_cells"]) == (0, 20)
    assert (ev["neo4j_better_persistence_metrics"], ev["total_persistence_metrics"]) == (0, 4)
    assert (ev["complex_sql_queries"], ev["total_queries"]) == (2, 5)
    assert d["rule"] == 4 and d["verdict"] == "drop"


def test_both_committed_sweeps_agree_on_rule2():
    """두 스윕(원본 / light path 예열 재측정)이 모두 0 of 4여야 한다. 판정은
    재측정 쪽으로 하되 둘이 일치한다는 사실 자체를 테스트로 고정한다(A-13b).
    ⚠️ 두 스윕의 절대값을 서로 빼는 비교는 하지 않는다 — 실행 간 분산이 크다."""
    for exp5 in (_real_exp5_primed(), _real_exp5_first()):
        d = decision.evaluate(_real_exp4(), exp5)
        assert d["evidence"]["neo4j_better_persistence_metrics"] == 0
        assert d["evidence"]["total_persistence_metrics"] == 4
        assert d["rule"] == 4 and d["verdict"] == "drop"


def test_rule2_reproduces_the_tally_persisted_by_task6():
    """두 개의 독립된 계산이 같은 답을 내는지 본다. exp5 러너가 JSON에 남긴
    `rule2_tally`와 decision.evaluate의 집계가 지표별·전체 모두 일치해야 한다.
    어긋나면 손으로 화해시키지 말고 이 테스트가 깨져야 한다."""
    exp5 = _real_exp5_primed()
    persisted = exp5["rule2_tally"]
    reading = persisted["adjudicated_on"]
    expected = persisted["by_disk_reading"][reading]

    ev = decision.evaluate(_real_exp4(), exp5)["evidence"]
    assert ev["disk_reading"] == reading
    assert ev["neo4j_better_persistence_metrics"] == expected["neo4j_metric_wins"]
    assert ev["total_persistence_metrics"] == expected["metrics"]

    got = {m["metric"]: m for m in ev["rule2_per_metric"]}
    want = {m["metric"]: m for m in expected["per_metric"]}
    assert got.keys() == want.keys()
    for name, w in want.items():
        assert got[name]["cells"] == w["cells"], name
        assert got[name]["neo4j_wins"] == w["neo4j_wins"], name
        assert got[name]["neo4j_ahead"] == w["neo4j_ahead"], name


def test_committed_artifact_cell_count_matches_the_prefixed_rule():
    """사전 고정 규칙은 "20개 셀 중 11+"라는 절대 임계다. 셀 수가 20이 아닌
    아티팩트에 이 임계를 그대로 적용하면 규칙의 의미가 달라진다."""
    with pytest.raises(ValueError, match="20"):
        decision.evaluate(_exp4(3, total=8), _exp5(0))


# ---------------------------------------------------------------------------
# 규칙 4의 문언 — 제거 계획 동반
# ---------------------------------------------------------------------------

def test_drop_verdict_carries_a_removal_plan():
    """사전 고정 규칙 4의 문언은 "drop (Neo4j·docker-compose·드라이버 의존 제거
    계획 동반 제시)"다. 판정만 내고 계획이 없으면 규칙을 절반만 지킨 것이다."""
    d = decision.evaluate(_exp4(3), _exp5(1))
    plan = d["removal_plan"]
    assert plan["remove"] and plan["keep"] and plan["lost"]
    joined = json.dumps(plan, ensure_ascii=False)
    assert "docker-compose.yml" in joined
    assert "core/graph/neo4j_store.py" in joined
    assert "core/rag/sqlite_rag.py" in joined      # 대체가 이미 존재한다는 근거


def test_keep_verdicts_do_not_carry_a_removal_plan():
    assert decision.evaluate(_exp4(11), _exp5(0)).get("removal_plan") is None
