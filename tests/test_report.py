import re

import pytest

from core.rag.queries import RAG_QUERIES
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


_SCALES = (100, 300, 500, 1000)


def _exp4_fake(parity_match=True, neo_wins=0):
    """5질의 × 4규모 = 20셀. 규칙 1의 임계가 "20셀 중 11+"라는 절대 수치라
    셀 수까지 실물과 같아야 의사결정 절이 렌더링된다."""
    rows, parity = [], []
    won = 0
    for q in RAG_QUERIES:
        for n in _SCALES:
            neo_fast = won < neo_wins
            won += int(neo_fast)
            s, g = (2.0, 1.0) if neo_fast else (0.05, 1.0)
            for backend, ms in (("sqlite", s), ("neo4j", g)):
                rows.append({"backend": backend, "query": q, "n_people": n,
                             "n_projects": n // 5, "result_count": 10 if q != "overfamiliar_pairs"
                             else n, "median_ms": ms, "p95_ms": ms * 1.1,
                             "min_ms": ms * 0.9, "max_ms": ms * 1.2,
                             "repeats": 20, "warmup": 50 if backend == "neo4j" else 3})
            parity.append({"query": q, "n_people": n, "sqlite_count": 10,
                           "neo4j_count": 10, "match": parity_match})
    return {"environment": {}, "data": {
        "rows": rows, "parity": parity, "skipped": [],
        "calibration": {"neo4j_session_open_ms": 0.004, "neo4j_session_open_p95_ms": 0.005,
                        "neo4j_session_plus_trivial_query_ms": 0.3,
                        "neo4j_session_plus_trivial_query_p95_ms": 0.34,
                        "sqlite_noop_ms": 0.0005, "sqlite_noop_p95_ms": 0.0006},
        "args": {}}}


def _exp5_fake(neo_better=0):
    """4지표(load/disk/append_*/rehydrate) × 4규모. append_*는 두 이름이 한 지표다."""
    rows, disk_detail = [], []
    metrics = ["load_ms", "disk_bytes_clean", "append_cowork_ms", "rehydrate_ms"]
    better = {m for m in metrics[:neo_better]}
    for n in _SCALES:
        for m in ("load_ms", "disk_bytes", "disk_bytes_clean",
                  "append_cowork_ms", "append_review_ms", "rehydrate_ms"):
            key = "append_cowork_ms" if m == "append_review_ms" else m
            neo, sql = (1.0, 2.0) if key in better else (2.0, 1.0)
            for backend, v in (("sqlite", sql), ("neo4j", neo)):
                rows.append({"backend": backend, "n_people": n, "metric": m, "value": v,
                             "unit": "bytes" if m.startswith("disk") else "ms",
                             "median_ms": v, "p95_ms": v, "min_ms": v * 0.9,
                             "max_ms": v * 1.1,
                             "repeats": 20 if m.startswith("append") else 3, "warmup": 20})
        for backend, store in (("sqlite", 1000), ("neo4j", 5000)):
            disk_detail.append({"backend": backend, "n_people": n, "reading": "clean",
                                "total_bytes": store + 100, "store_bytes": store,
                                "txlog_bytes": 0 if backend == "sqlite" else 50,
                                "system_store_bytes": 0, "system_txlog_bytes": 0})
    cal = {"per_scale": [{"n_people": n, "records": {}, "sqlite_unlink_ms": 0.02,
                          "assemble_ms": 0.3, "sqlite_noop_ms": 0.0005,
                          "neo4j_detach_delete_ms": 0.5, "neo4j_session_open_ms": 0.005,
                          "neo4j_noop_match_ms": 0.4,
                          "neo4j_two_endpoint_match_ms": 0.45} for n in _SCALES]}
    return {"environment": {}, "data": {
        "rows": rows, "skipped": [], "calibration": cal, "disk_detail": disk_detail,
        "completed_scales": [[n, n // 5] for n in _SCALES], "note": "테스트 노트"}}


def _fakes(**over):
    """리포트가 로드하는 결과 파일 전체. 개별 테스트는 필요한 것만 덮어쓴다."""
    base = {"exp1_storage": _exp1_fake(), "exp2_pipeline": _exp2_fake(),
            "exp3_algorithm": _exp3_fake(), "exp4_rag": _exp4_fake(),
            "exp5_persistence_primed": _exp5_fake(),
            "exp5_persistence": _exp5_fake()}
    base.update(over)
    return base


def test_build_contains_all_five_experiments(monkeypatch):
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    for heading in ("실험 1", "실험 2", "실험 3", "실험 4", "실험 5",
                    "저장 계층 의사결정", "한계", "결론"):
        assert heading in md
    assert "0.928" in md or "92.8" in md
    assert "재현" in md


def test_build_renders_new_fields_not_in_original_brief(monkeypatch):
    """브리프 작성 시점엔 없던 필드(protocol_floor/latency/failures)가 실제로 렌더링되는지 확인."""
    fake = _fakes()
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
    fake = _fakes(exp1_storage=base,
            exp2_pipeline={"environment": {}, "data": {
                "model": "m", "pricing_as_of": "x",
                "token_counts": {"full_llm": 1, "hybrid": 1, "review_count": 1},
                "cost_usd": {"full_llm": 1.0, "hybrid": 1.0}, "savings_pct": 0.0,
                "accuracy": {"checked": 0, "item_match_rate": None, "note": ""},
                "sensitivity": []}},
            exp3_algorithm={"environment": {}, "data": {
                "rows": [], "violations": [], "alternatives": []}})
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "⚠️" in md and "불일치" in md


def test_build_is_deterministic(monkeypatch):
    """동일 입력이면 두 번 생성한 출력이 바이트 단위로 동일해야 한다(타임스탬프 없음)."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    assert report.build() == report.build()


def test_repeat_count_reflects_data_not_hardcoded_20(monkeypatch):
    """반복 횟수 각주는 rows에서 읽어야 한다 — 20으로 타이핑해두면 다른 repeats로
    재실행했을 때 이 각주만 조용히 낡는다(코디네이터 리뷰 지적, 회귀 방지)."""
    fake = _fakes(exp1_storage=_exp1_fake(repeats=5))
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    # 실험 4·5도 자기 rows에서 각주를 읽으므로(각각 반복 20/3회) 문서 전체에서
    # "반복 20회"를 금지할 수는 없다 — 실험 1 절 안에서만 확인한다.
    exp1_section = md.split("## 실험 1")[1].split("## 실험 2")[0]
    assert "반복 5회" in exp1_section
    assert "반복 20회" not in exp1_section


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
    fake = _fakes()
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
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "p95 요약" in md
    assert "1.200" in md  # hops=1 sqlite p95


def test_exp1_scope_note_covers_node_polarity_asymmetry(monkeypatch):
    """Important 7: 인메모리는 node_polarity를 빌드 시점에 미리 계산해 두고,
    SQLite/Neo4j는 매 호출마다 다시 계산한다는 비대칭이 스코프에 있어야 한다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "node_polarity" in md and "비대칭" in md


def test_exp1_environment_line_includes_cpu_ram_neo4j(monkeypatch):
    """Important 9: 헤드라인이 'Neo4j가 5배 느리다'인 벤치마크이니 CPU/RAM/Neo4j
    서버 버전이 리포트 환경 줄에 나와야 한다."""
    fake = _fakes()
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
    fake = _fakes(exp2_pipeline=e2)
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "test_savings_pct_is_independent_of_out_ratio" in md
    assert "출력 토큰 모델 민감도" in md
    assert "31.4" in md and "0.78" in md


def test_exp2_design_doc_citation_notes_it_is_gitignored(monkeypatch):
    """Important 10: 클론한 사람이 .omc/plan/... 경로를 열어볼 수 없다는 사실 자체를
    밝혀야 한다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert ".gitignore" in md


def test_exp3_greedy_budget_blindness_disclosed(monkeypatch):
    """Important 3: greedy.py는 구조적으로 예산 로직이 없다는 사실을 리포트가
    명시해야 한다."""
    fake = _fakes()
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
    fake = _fakes(exp3_algorithm=e3)
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "0/10건(0%)" in md and "1/20건(5%)" in md and "2/40건(5%)" in md
    assert "규모가 커질수록 늘어난다" not in md


def test_exp3_data_source_and_frozen_fixture_distinction(monkeypatch):
    """Critical 2: 스윕의 실제 데이터 출처가 명시되고, 커밋된 데모 fixture의 값
    (README에서 파싱)과 스윕의 n=100 값이 서로 다른 수치임이 드러나야 한다."""
    fake = _fakes()
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
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "λ=0.3" in md and "μ=0.2" in md
    assert "스킬 항" in md


def test_exp3_alternatives_table_discloses_gap_cap_mismatch(monkeypatch):
    """Minor: Plan A(gap=0.01)와 대안(gap=0.05)이 위쪽 표(gap=0.05, cap별)와
    다른 조건으로 solve된다는 사실이 드러나야 한다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "gap=0.01" in md and "gap=0.05" in md


def test_exp3_missing_optional_fields_do_not_crash(monkeypatch):
    """새 필드(n_projects/milp_params/data_source)가 없는 구형 결과 JSON에 대해서도
    build()가 죽지 않고 그 부분만 조용히 생략해야 한다(하위 호환)."""
    fake = _fakes(exp3_algorithm=_exp3_fake(with_n_projects=False, with_milp_params=False,
                                            with_data_source=False))
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


# ---------------------------------------------------------------------------
# Plan 3 — 실험 4·5 절과 의사결정 절
# ---------------------------------------------------------------------------

def test_exp4_flags_parity_mismatch(monkeypatch):
    """실험 4의 백엔드 불일치도 ⚠️로 드러나야 한다 — 두 구현이 다른 답을 내면
    그 셀의 지연 비교 자체가 무효다."""
    fake = _fakes(exp4_rag=_exp4_fake(parity_match=False))
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    exp4 = md.split("## 실험 4")[1].split("## 실험 5")[0]
    assert "⚠️" in exp4 and "불일치" in exp4


def test_exp4_discloses_discarded_sweep_topology_and_extrapolation_limits(monkeypatch):
    """A-1/A-3/A-4/A-5: 폐기된 첫 스윕, 배포 토폴로지 비대칭, 규모 외삽 금지,
    p95 추정량의 약함이 실험 4 절에 모두 있어야 한다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp4 = report.build().split("## 실험 4")[1].split("## 실험 5")[0]
    assert "폐기된 첫 스윕" in exp4 and "복구할 수 없다" in exp4
    assert "neo4j:5-community" in exp4 and "in-process" in exp4
    assert "외삽하지 말 것" in exp4
    assert "p95는 약한 추정량이다" in exp4


def test_exp4_calibration_is_quoted_as_a_range_with_sensitivity(monkeypatch):
    """A-2: 세션 획득만이 아니라 고정 바닥 전체까지 두 끝을 함께 제시하고,
    차감 민감도(뒤집히는 셀 수)를 실제로 계산해 보여야 한다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp4 = report.build().split("## 실험 4")[1].split("## 실험 5")[0]
    assert "고정 바닥" in exp4
    assert "낮은 쪽 끝만 인용하면" in exp4
    assert "뒤집히는 셀" in exp4


def test_exp4_discloses_the_undirected_cypher_handicap(monkeypatch):
    """최종 리뷰 I-2: 출하된 Cypher가 `-[w:WORKED_WITH]-`를 무방향으로 확장한 뒤
    절반을 버려, 정확히 등가인 방향형보다 더 많은 일을 한다. 방향은 Neo4j에
    불리하고, 리포트는 이보다 한 자릿수 작은 세션 획득 비용은 이미 공개하고
    있었다 — 메커니즘·크기·방향·판정 무관성이 모두 실험 4 한계에 있어야 한다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp4 = report.build().split("## 실험 4")[1].split("## 실험 5")[0]
    assert "무방향" in exp4 and "a.id < b.id" in exp4          # 메커니즘
    assert "11~21% 많은 일을 한다" in exp4                      # 크기(dbHits에서 계산)
    assert "방향은 Neo4j에 불리하다" in exp4                    # 방향
    assert "판정은 움직이지 않는다" in exp4                     # 판정 무관성
    assert "질의는 고치지 않았다" in exp4                       # 계측기 동결


def test_exp4_cypher_handicap_magnitude_is_computed_from_the_dbhits(monkeypatch):
    """크기를 손으로 타이핑하면 dbHits를 갱신했을 때 조용히 낡는다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    monkeypatch.setitem(report.CYPHER_DIRECTION_DBHITS, "team_cohesion",
                        {"n_people": 1000, "shipped": 1000, "directed": 500})
    exp4 = report.build().split("## 실험 4")[1].split("## 실험 5")[0]
    assert "11~100% 많은 일을 한다" in exp4


def test_exp4_cross_references_the_priming_plateau_established_by_exp5(monkeypatch):
    """최종 리뷰 M-2: exp4의 예열은 5개 질의 중 2개만 돌려서 실험 5가 나중에
    실측한 평탄 구간 아래에 있다. 재측정하지 않는 대신 그 사실과, 남은 램프가
    이미 공개된 민감도에 갇힌다는 것을 밝힌다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp4 = report.build().split("## 실험 4")[1].split("## 실험 5")[0]
    assert "_prime_neo4j" in exp4 and "평탄 구간" in exp4
    assert "단조 감소하는 것은 **하나도 없다**" in exp4
    assert "재측정하지 않았다" in exp4


def test_exp4_limitation_items_are_numbered_without_gaps(monkeypatch):
    """한계 항목 번호는 렌더된 항목에서 매긴다 — 데이터에 따라 중간 항목이 빠지면
    손으로 적은 번호에 구멍이 생긴다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    limits = (report.build().split("## 실험 4")[1].split("## 실험 5")[0]
              .split("### 한계 (반드시 함께 읽을 것)")[1])
    nums = [int(m) for m in re.findall(r"^(\d+)\. \*\*", limits, re.MULTILINE)]
    assert nums == list(range(1, len(nums) + 1)) and len(nums) >= 6


def test_exp5_warmup_spread_claim_is_scoped_to_the_cells_that_earn_it(monkeypatch):
    """최종 리뷰 I-1: `harness.measure`가 `samples.sort()`로 시간 순서를 파괴하므로
    큰 스프레드는 램프와 단일 이상치를 구분하지 못한다 — 임계를 넘는 셀에 대해서는
    "예열됐다"고 주장하지 않고, 잔여 방향(Neo4j 최솟값으로 읽어도 열세)을 밝힌다."""
    ramped = _exp5_fake()
    for r in ramped["data"]["rows"]:
        if (r["backend"] == "neo4j" and r["metric"] == "rehydrate_ms"
                and r["n_people"] == _SCALES[-1]):
            r["max_ms"] = r["min_ms"] * (report.RAMP_SPREAD_LIMIT + 0.3)
    fake = _fakes(exp5_persistence_primed=ramped)
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "samples.sort()" in exp5                       # 왜 배제하지 못하는지
    assert "이 검사를 통과하지 못한다" in exp5
    assert "램프 가능성을 배제하지 못한다" in exp5
    assert "방향은 Neo4j에 불리하다" in exp5
    assert "1.8배 열세" in exp5      # neo4j min 1.8 / sqlite 중앙값 1.0 — 계산값


def test_exp5_warmup_spread_claim_holds_when_every_cell_is_tight(monkeypatch):
    """스프레드가 전부 임계 이하면 램프 배제 주장은 그대로 서고, 통과 못한 셀에
    대한 문단은 아예 나오지 않아야 한다(주장을 데이터에 맞춘다)."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "이 셀들에서는 램프가 배제된다" in exp5
    assert "이 검사를 통과하지 못한다" not in exp5


def test_exp5_warmup_repeats_line_is_scoped_to_exp5(monkeypatch):
    """최종 리뷰 M-5: 이 문장은 exp5 rows에서만 유도되는데 "이 리포트가 쓰는"으로
    적혀 있었다 — 실험 4는 일부러 다른 (반복, 웜업)을 쓴다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    md = report.build()
    assert "이 리포트가 쓰는 (반복, 웜업)" not in md
    assert "**실험 5가** 쓰는 (반복, 웜업) 조합" in md


def _exp5_fake_with_append_flip(neo_better=0):
    """엔드포인트 MATCH를 빼면 append_* 셀이 과반 뒤집히는 데이터."""
    fake = _exp5_fake(neo_better)
    for r in fake["data"]["rows"]:
        if r["backend"] == "neo4j" and r["metric"].startswith("append"):
            r["value"] = r["median_ms"] = 1.4      # 1.4 - 0.45 < sqlite 1.0
    return fake


def test_exp5_append_counterfactual_conclusion_follows_the_data(monkeypatch):
    """최종 리뷰 M-1: cw/rv/cells는 계산하면서 결론("통째로 뒤집힌다")과 반사실
    규칙 2 카운트("1 of 4")는 리터럴이었다 — 오늘 데이터에서만 맞다."""
    fake = _fakes()                                  # 잔차를 빼도 안 뒤집히는 데이터
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "통째로 뒤집힌다" not in exp5
    assert "뒤집히지 않는다" in exp5
    assert "**0 of 4**" in exp5


def test_exp5_append_counterfactual_rule2_count_is_computed(monkeypatch):
    """반사실 카운트는 다른 지표의 승패와 함께 움직여야 한다 — 1로 타이핑돼 있으면
    이미 Neo4j가 이긴 지표가 있는 데이터에서 조용히 틀린다."""
    fake = _fakes(exp5_persistence_primed=_exp5_fake_with_append_flip(neo_better=1))
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "통째로 뒤집힌다" in exp5
    assert "**2 of 4**" in exp5      # load_ms 1승 + append_* 반사실 1승
    assert "여전히 미달이라 판정은 유지된다" in exp5


def test_exp5_append_counterfactual_says_so_when_it_would_change_the_verdict(monkeypatch):
    """반사실이 임계를 넘으면 "판정은 유지된다"고 적으면 안 된다."""
    fake = _fakes(exp5_persistence_primed=_exp5_fake_with_append_flip(neo_better=2))
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "**3 of 4**" in exp5
    assert "판정이 달라진다" in exp5
    assert "여전히 미달이라 판정은 유지된다" not in exp5


def test_exp5_reports_both_sweeps_and_forbids_cross_sweep_subtraction(monkeypatch):
    """A-13b: 두 스윕의 집계를 모두 싣고, 절대값을 서로 빼지 말라는 경고를 남긴다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "exp5_persistence_primed.json" in exp5 and "exp5_persistence.json" in exp5
    assert "절대값을 서로 빼서 비교하지 말 것" in exp5


def test_exp5_states_which_disk_reading_decided_rule2(monkeypatch):
    """A-11: 어느 disk 판독으로 규칙 2를 판정했는지와, 지표를 빼지 않은 이유."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "disk_bytes_clean" in exp5
    assert "3 of 3" in exp5          # 지표를 빼면 분모가 몰래 바뀐다는 서술
    assert "스토어 파일만" in exp5    # Neo4j에 가장 유리한 대안 판독


def test_exp5_covers_the_required_limitations(monkeypatch):
    """브리프가 명시적으로 요구한 세 가지: (a) 적재·재수화 repeats 축소,
    (b) Neo4j 디스크 측정이 볼륨 전체를 재는 한계, (c) 재수화가
    availability/projects를 복원하지 않는다는 점."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "축소 반복" in exp5
    assert "누적 판독" in exp5
    assert "availability" in exp5 and "projects" in exp5
    assert "크래시 내구성은 시험하지 않았다" in exp5


def test_exp5_calibration_does_not_claim_a_discount_that_fails_for_append(monkeypatch):
    """A-12: append_*는 차감 기반 주장이 어느 방향으로도 성립하지 않는다는 사실과,
    반사실(뒤집힘)을 함께 밝혀야 한다. 그리고 그 범위를 네 지표 전체에 대한
    진술로 쓰면 안 된다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    exp5 = report.build().split("## 실험 5")[1].split("## 저장 계층 의사결정")[0]
    assert "판정 불가" in exp5
    assert "반사실" in exp5
    assert "네 지표 전체에 대한 진술로" in exp5


def test_decision_section_states_rule_number_evidence_verdict_and_plan(monkeypatch):
    """의사결정 절은 적용된 규칙 번호·근거 수치·판정·근거 문장을 모두 싣고,
    drop이면 제거 계획까지 동반해야 한다(규칙 4의 문언)."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    sec = report.build().split("## 저장 계층 의사결정")[1]
    assert "규칙 4 적용" in sec and "`drop`" in sec
    assert "0 / 20" in sec and "0 / 4" in sec
    assert "제거 대상" in sec and "docker-compose.yml" in sec
    assert "무엇을 잃는가" in sec


def test_decision_section_shows_both_rule3_readings(monkeypatch):
    """A-6: enum 해석과 문언 해석을 둘 다 보이고, 판정에는 Neo4j에 유리한 쪽을
    썼다는 사실까지 밝혀야 한다."""
    fake = _fakes()
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    sec = report.build().split("## 저장 계층 의사결정")[1]
    assert "2 / 5" in sec and "1 / 5" in sec
    assert "유리한" in sec
    assert "team_cohesion" in sec


def test_decision_section_flips_to_keep_when_the_data_says_so(monkeypatch):
    """판정이 데이터에서 나오는지 확인한다 — 규칙 1을 충족하는 데이터를 주면
    같은 코드가 keep을 내야 한다. 문장이 drop으로 굳어 있으면 여기서 깨진다."""
    fake = _fakes(exp4_rag=_exp4_fake(neo_wins=11))
    monkeypatch.setattr(report.harness, "load_result", lambda name: fake[name])
    sec = report.build().split("## 저장 계층 의사결정")[1]
    assert "규칙 1 적용" in sec and "`keep`" in sec
    assert "제거 대상" not in sec


def test_report_numbers_come_from_the_committed_artifacts(monkeypatch):
    """리포트가 실제 커밋된 JSON으로도 생성되며, 판정 수치가 decision.evaluate의
    결과와 일치하는지 본다(픽스처에서만 도는 것을 막는다)."""
    from experiments import decision
    md = report.build()          # monkeypatch 없음 — 실제 파일을 읽는다
    e4 = decision.payload(report.harness.load_result("exp4_rag"))
    e5 = decision.payload(report.harness.load_result("exp5_persistence_primed"))
    d = decision.evaluate(e4, e5)
    ev = d["evidence"]
    assert f"규칙 {d['rule']} 적용" in md
    assert f"`{d['verdict']}`" in md
    assert d["rationale"] in md
    assert f"**{ev['neo4j_faster_cells']} / {ev['total_cells']}**" in md
