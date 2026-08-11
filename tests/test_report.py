import pytest

from experiments import report


def _exp1_fake(parity_match=True, with_extras=True, repeats=20):
    rows = [
        {"backend": "sqlite", "n_people": 100, "n_projects": 20, "hops": 1,
         "median_ms": 1.0, "p95_ms": 1.2, "repeats": repeats, "warmup": 3, "result_count": 40},
        {"backend": "memory", "n_people": 100, "n_projects": 20, "hops": 1,
         "median_ms": 0.5, "p95_ms": 0.6, "repeats": repeats, "warmup": 3, "result_count": 40},
        {"backend": "neo4j", "n_people": 100, "n_projects": 20, "hops": 1,
         "median_ms": 2.0, "p95_ms": 2.4, "repeats": repeats, "warmup": 50, "result_count": 40},
        {"backend": "sqlite", "n_people": 100, "n_projects": 20, "hops": 3,
         "median_ms": 1.5, "p95_ms": 2.0, "repeats": repeats, "warmup": 3, "result_count": 90},
        {"backend": "memory", "n_people": 100, "n_projects": 20, "hops": 3,
         "median_ms": 0.2, "p95_ms": 0.3, "repeats": repeats, "warmup": 3, "result_count": 90},
        {"backend": "sqlite", "n_people": 100, "n_projects": 20, "hops": 4,
         "median_ms": 4.0, "p95_ms": 4.5, "repeats": repeats, "warmup": 3, "result_count": 95},
        {"backend": "neo4j", "n_people": 100, "n_projects": 20, "hops": 4,
         "median_ms": 20.0, "p95_ms": 21.0, "repeats": repeats, "warmup": 50, "result_count": 95},
        {"backend": "memory", "n_people": 100, "n_projects": 20, "hops": 4,
         "median_ms": 1.0, "p95_ms": 1.1, "repeats": repeats, "warmup": 3, "result_count": 95},
    ]
    data = {
        "rows": rows,
        "parity": [{"n_people": 100, "hops": h, "match": parity_match} for h in (1, 3, 4)],
        "skipped": [],
        "seeds_per_run": 5,
    }
    if with_extras:
        data["protocol_floor"] = {"median_ms": 1.6, "p95_ms": 1.9, "repeats": repeats, "warmup": 50}
    return {"environment": {"python": "3.12.0", "platform": "test", "cpu_count": 8}, "data": data}


def _exp2_fake(with_latency=True):
    data = {
        "model": "gpt-5-nano", "pricing_as_of": "2026-08-05",
        "token_counts": {"full_llm": 1000, "hybrid": 250, "review_count": 266},
        "cost_usd": {"full_llm": 0.9, "hybrid": 0.2}, "savings_pct": 75.0,
        "accuracy": {"checked": 0, "item_match_rate": None, "note": "미측정"},
        "sensitivity": [{"freetext_multiplier": 0.25, "savings_pct": 90.0},
                         {"freetext_multiplier": 1.0, "savings_pct": 75.0},
                         {"freetext_multiplier": 4.0, "savings_pct": 20.0}],
        "output_token_assumption": 5.0,
        "output_token_assumption_basis": "테스트 근거",
    }
    if with_latency:
        data["latency"] = {"measured": False, "note": "라이브 타이밍 미수행",
                            "basis": "Task 6 체크포인트 실측", "caveat": "직접 측정 아님"}
    return {"environment": {}, "data": data}


_FAKE_MILP_PARAMS = {"lam": 0.3, "mu": 0.2, "min_alloc": 0.2, "clique_threshold_months": 6,
                     "pair_keep_ratio": 0.15, "slack_penalty": 100.0, "time_limit": 120,
                     "gap": 0.05, "max_pairs": 1000}


def _exp3_fake(with_failures=True, with_n_projects=True, with_milp_params=True,
              with_data_source=True):
    data = {
        "rows": [
            {"algorithm": "greedy", "n_people": 100, "n_projects": 20, "solve_ms": 5.0,
             "optimization_ratio": 0.707, "unfilled": 0, "pair_cap": None},
            {"algorithm": "milp", "n_people": 100, "n_projects": 20, "solve_ms": 900.0,
             "optimization_ratio": 0.928, "unfilled": 0, "pair_cap": 1000,
             **({"milp_params": _FAKE_MILP_PARAMS} if with_milp_params else {})},
            {"algorithm": "milp", "n_people": 100, "n_projects": 20, "solve_ms": 1800.0,
             "optimization_ratio": 0.928, "unfilled": 0, "pair_cap": 5000,
             **({"milp_params": {**_FAKE_MILP_PARAMS, "max_pairs": 5000}} if with_milp_params else {})},
        ],
        "violations": [
            {"algorithm": "greedy", "n_people": 100, "budget_violations": 1,
             **({"n_projects": 20} if with_n_projects else {})},
            {"algorithm": "milp", "n_people": 100, "pair_cap": 1000, "budget_violations": 0,
             **({"n_projects": 20} if with_n_projects else {})},
            {"algorithm": "milp", "n_people": 100, "pair_cap": 5000, "budget_violations": 0,
             **({"n_projects": 20} if with_n_projects else {})},
        ],
        "alternatives": [{"n_people": 100, "plan_count": 4, "total_ms": 30000,
                           "labels": ["A", "B", "C", "D"],
                           "quality_vs_a": [1.0, 0.99, 0.98, 0.97],
                           "jaccard_vs_a": [1.0, 0.65, 0.62, 0.60]}],
    }
    if with_failures:
        data["failures"] = [{"n_people": 300, "n_projects": 60, "seed": 42,
                              "status": "skipped", "reason": "extrapolated beyond budget"}]
    if with_data_source:
        data["data_source"] = "테스트 데이터 출처 노트"
    return {"environment": {}, "data": data}


def test_build_contains_all_three_experiments(monkeypatch):
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    for heading in ("실험 1", "실험 2", "실험 3", "한계", "결론"):
        assert heading in md
    assert "0.928" in md or "92.8" in md
    assert "재현" in md


def test_build_renders_new_fields_not_in_original_brief(monkeypatch):
    """브리프 작성 시점엔 없던 필드(protocol_floor/latency/failures)가 실제로 렌더링되는지 확인."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    # exp1 protocol_floor: hops=1 IPC 지배 비율이 실제로 계산되어 나타난다 (1.6/2.0=80%)
    assert "80%" in md
    # exp1 neo4j/sqlite ratio at hops=4 (20.0/4.0=5.0x) computed, not hand-typed
    assert "5.00배" in md
    # exp2 latency disclosure block must be present and clearly say unmeasured
    assert "지연" in md and "미수행" in md
    # exp3 failures (n>=300 unmeasured) must be surfaced, not silently dropped
    assert "300" in md and ("미측정" in md or "skipped" in md or "extrapolated" in md)


def test_build_flags_parity_mismatch(monkeypatch):
    """백엔드 불일치는 리포트에 경고로 드러나야 한다 — 조용히 넘어가면 안 된다."""
    base = {"environment": {}, "data": {"rows": [], "parity": [
        {"n_people": 100, "hops": 2, "match": False}], "skipped": []}}
    fake = {"exp1_storage": base,
            "exp2_pipeline": {"environment": {}, "data": {
                "model": "m", "pricing_as_of": "x",
                "token_counts": {"full_llm": 1, "hybrid": 1, "review_count": 1},
                "cost_usd": {"full_llm": 1.0, "hybrid": 1.0}, "savings_pct": 0.0,
                "accuracy": {"checked": 0, "item_match_rate": None, "note": ""},
                "sensitivity": []}},
            "exp3_algorithm": {"environment": {}, "data": {
                "rows": [], "violations": [], "alternatives": []}}}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "⚠️" in md and "불일치" in md


def test_build_is_deterministic(monkeypatch):
    """동일 입력이면 두 번 생성한 출력이 바이트 단위로 동일해야 한다(타임스탬프 없음)."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    assert report.build() == report.build()


def test_repeat_count_reflects_data_not_hardcoded_20(monkeypatch):
    """반복 횟수 각주는 rows에서 읽어야 한다 — 20으로 타이핑해두면 다른 repeats로
    재실행했을 때 이 각주만 조용히 낡는다(코디네이터 리뷰 지적, 회귀 방지)."""
    fake = {"exp1_storage": _exp1_fake(repeats=5), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "반복 5회" in md
    assert "반복 20회" not in md


def test_build_propagates_missing_results_file(monkeypatch):
    """results/*.json 하나가 없으면 build()는 예외를 그대로 전파해야 한다 —
    조용히 리포트에 구멍을 내면 안 된다(브리프의 load-bearing 속성, 회귀 방지)."""
    def _raise(name):
        raise FileNotFoundError(f"missing {name}.json")
    monkeypatch.setattr(report.harness, "load_result", _raise)
    with pytest.raises(FileNotFoundError):
        report.build()


def test_exp1_headline_does_not_claim_unverified_memory_ordering(monkeypatch):
    """headline이 검증하지 않은 3자 순서를 단정하면 안 된다.

    memory가 sqlite보다 느린 조합이 있으면(= 3자 순서 미유지) neo4j 미역전
    사실만 말하고 'memory < sqlite < neo4j'는 주장하지 않아야 한다.
    """
    rows = []
    # neo4j는 항상 가장 느리지만(역전 없음), memory가 sqlite보다 느린 구간이 있다
    for backend, ms in (("memory", 5.0), ("sqlite", 1.0), ("neo4j", 9.0)):
        rows.append({"backend": backend, "n_people": 100, "n_projects": 20, "hops": 1,
                     "median_ms": ms, "p95_ms": ms, "repeats": 20, "warmup": 3,
                     "result_count": 10})
    head = report._exp1_headline(rows)
    assert "기각" in head, "역전이 없으므로 원 가설은 기각으로 판정돼야 한다"
    assert "memory < sqlite < neo4j" not in head, \
        "검증되지 않은 3자 순서를 단정하면 안 된다"


def test_exp1_headline_claims_full_order_when_data_supports_it():
    rows = []
    for backend, ms in (("memory", 0.1), ("sqlite", 1.0), ("neo4j", 9.0)):
        rows.append({"backend": backend, "n_people": 100, "n_projects": 20, "hops": 1,
                     "median_ms": ms, "p95_ms": ms, "repeats": 20, "warmup": 3,
                     "result_count": 10})
    assert "memory < sqlite < neo4j" in report._exp1_headline(rows)


# ---------------------------------------------------------------------------
# 최종 리뷰(2026-08-11) 수정 웨이브 회귀 테스트
# ---------------------------------------------------------------------------

def test_exp1_table_surfaces_result_count(monkeypatch):
    """Important 8: n=500 dip처럼 result_count가 인원 수의 단조 대리지표가 아님을
    보여주려면 표에 결과 수 자체가 있어야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "결과수" in md
    assert "| 100 | 1 | 1.000 | 2.000 | 0.500 | 40 |" in md


def test_exp1_result_count_dip_note_fires_when_data_shows_a_dip():
    """Important 8: n이 늘어도 result_count가 줄어드는 지점이 있으면 명시적으로
    짚어야 한다 -- 손타이핑이 아니라 rows에서 직접 찾아야 하므로 헬퍼를 직접 검증."""
    rows = [
        {"n_people": 300, "hops": 3, "result_count": 459},
        {"n_people": 500, "hops": 3, "result_count": 330},
        {"n_people": 300, "hops": 4, "result_count": 1038},
        {"n_people": 500, "hops": 4, "result_count": 990},
    ]
    note = report._result_count_monotonicity_note(rows)
    assert note is not None
    assert "300" in note and "500" in note and "459" in note and "330" in note


def test_exp1_result_count_dip_note_absent_when_monotonic():
    rows = [
        {"n_people": 300, "hops": 3, "result_count": 100},
        {"n_people": 500, "hops": 3, "result_count": 200},
    ]
    assert report._result_count_monotonicity_note(rows) is None


def test_exp1_p95_summary_present(monkeypatch):
    """Important 5: 방법론이 p95를 보고한다고 명시하므로 어딘가에는 실제로 있어야
    한다(표를 두 배로 넓히지 않기 위해 hop별 요약 표로)."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "p95 요약" in md
    assert "1.200" in md  # hops=1 sqlite p95


def test_exp1_scope_note_covers_node_polarity_asymmetry(monkeypatch):
    """Important 7: 인메모리는 node_polarity를 빌드 시점에 미리 계산해 두고,
    SQLite/Neo4j는 매 호출마다 다시 계산한다는 비대칭이 스코프에 있어야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "node_polarity" in md and "비대칭" in md


def test_exp1_environment_line_includes_cpu_ram_neo4j(monkeypatch):
    """Important 9: 헤드라인이 'Neo4j가 5배 느리다'인 벤치마크이니 CPU/RAM/Neo4j
    서버 버전이 리포트 환경 줄에 나와야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    fake["exp1_storage"]["environment"].update(
        {"cpu_model": "Apple M4", "ram": "16.0 GiB", "neo4j_server": "Neo4j Kernel 5.26.28 (community)"})
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "Apple M4" in md and "16.0 GiB" in md and "Neo4j Kernel 5.26.28" in md


def test_exp2_savings_invariance_and_output_model_sensitivity_rendered(monkeypatch):
    """Critical 1: savings_pct가 out_ratio에 불변이라는 사실(테스트 인용)과, 출력
    토큰 모델을 바꾸면 절감률이 크게 달라진다는 민감도 표가 둘 다 리포트에 있어야
    한다."""
    e2 = _exp2_fake()
    e2["data"]["output_model_sensitivity"] = [
        {"model": "output_proportional_to_input", "savings_pct": 31.4},
        {"model": "output_constant_across_arms", "savings_pct": 0.78},
    ]
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": e2, "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "test_savings_pct_is_independent_of_out_ratio" in md
    assert "출력 토큰 모델 민감도" in md
    assert "31.4" in md and "0.78" in md


def test_exp2_design_doc_citation_notes_it_is_gitignored(monkeypatch):
    """Important 10: 클론한 사람이 .omc/plan/... 경로를 열어볼 수 없다는 사실 자체를
    밝혀야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert ".gitignore" in md


def test_exp3_greedy_budget_blindness_disclosed(monkeypatch):
    """Important 3: greedy.py는 구조적으로 예산 로직이 없다는 사실을 리포트가
    명시해야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "core/optimize/greedy.py" in md
    assert "저비용의 제약-인지 휴리스틱이 존재하지 않는다는 근거가 아니다" in md


def test_exp3_violation_rate_normalized_and_growth_framing_dropped(monkeypatch):
    """Important 4: 원시 건수뿐 아니라 프로젝트 수 대비 비율도 나와야 하고,
    "규모가 커질수록 늘어난다"는 서사를 더 이상 주장하지 않아야 한다."""
    e3 = _exp3_fake()
    e3["data"]["violations"] = [
        {"algorithm": "greedy", "n_people": 50, "n_projects": 10, "budget_violations": 0},
        {"algorithm": "greedy", "n_people": 100, "n_projects": 20, "budget_violations": 1},
        {"algorithm": "greedy", "n_people": 200, "n_projects": 40, "budget_violations": 2},
        {"algorithm": "milp", "n_people": 100, "n_projects": 20, "pair_cap": 1000,
         "budget_violations": 0},
    ]
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(), "exp3_algorithm": e3}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "0/10건(0%)" in md and "1/20건(5%)" in md and "2/40건(5%)" in md
    assert "규모가 커질수록 늘어난다" not in md


def test_exp3_data_source_and_frozen_fixture_distinction(monkeypatch):
    """Critical 2: 스윕의 실제 데이터 출처가 명시되고, 커밋된 데모 fixture의 값
    (README에서 파싱)과 스윕의 n=100 값이 서로 다른 수치임이 드러나야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "테스트 데이터 출처 노트" in md
    fixed = report._frozen_fixture_ratio()
    if fixed is not None:
        assert f"{fixed:.4f}" in md
        assert "섞어 인용하지" in md


def test_exp3_milp_params_disclosed(monkeypatch):
    """Important 6: optimization_ratio가 스킬 항만 잰다는 사실과 실제 solve에 쓰인
    lam/mu 등 MilpParams가 리포트에 드러나야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "λ=0.3" in md and "μ=0.2" in md
    assert "스킬 항" in md


def test_exp3_alternatives_table_discloses_gap_cap_mismatch(monkeypatch):
    """Minor: Plan A(gap=0.01)와 대안(gap=0.05)이 위쪽 표(gap=0.05, cap별)와
    다른 조건으로 solve된다는 사실이 드러나야 한다."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake()}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "gap=0.01" in md and "gap=0.05" in md


def test_exp3_missing_optional_fields_do_not_crash(monkeypatch):
    """새 필드(n_projects/milp_params/data_source)가 없는 구형 결과 JSON에 대해서도
    build()가 죽지 않고 그 부분만 조용히 생략해야 한다(하위 호환)."""
    fake = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake(with_n_projects=False, with_milp_params=False,
                                         with_data_source=False)}
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()  # must not raise
    assert "실험 3" in md


def test_ipc_dominance_wording_when_floor_exceeds_query_time(monkeypatch):
    """바닥값이 쿼리 시간을 넘어서면(비율 > 100%) '몇 %를 차지한다'는 표현은
    성립하지 않는다 — 측정 오차 내 구별 불가로 서술해야 한다."""
    rows = []
    for backend, ms in (("memory", 0.05), ("sqlite", 0.10), ("neo4j", 0.50)):
        rows.append({"backend": backend, "n_people": 100, "n_projects": 20, "hops": 1,
                     "median_ms": ms, "p95_ms": ms, "repeats": 20, "warmup": 3,
                     "result_count": 10})
    d = {"rows": rows, "parity": [{"n_people": 100, "hops": 1, "match": True}],
         "skipped": [], "protocol_floor": {"median_ms": 0.60, "p95_ms": 0.9}}
    md = report._exp1(d)          # floor 0.60 > neo4j 0.50 → 120%
    assert "구별되지 않는다" in md, "오차 내 구별 불가로 서술해야 한다"
    assert "차지한다" not in md.split("스코프 명시")[1].split("2.")[0], \
        "100%를 넘는 비율에 '차지한다'를 쓰면 안 된다"
