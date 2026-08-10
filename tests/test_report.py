from experiments import report


def _exp1_fake(parity_match=True, with_extras=True):
    rows = [
        {"backend": "sqlite", "n_people": 100, "n_projects": 20, "hops": 1,
         "median_ms": 1.0, "p95_ms": 1.2, "repeats": 20, "warmup": 3},
        {"backend": "memory", "n_people": 100, "n_projects": 20, "hops": 1,
         "median_ms": 0.5, "p95_ms": 0.6, "repeats": 20, "warmup": 3},
        {"backend": "neo4j", "n_people": 100, "n_projects": 20, "hops": 1,
         "median_ms": 2.0, "p95_ms": 2.4, "repeats": 20, "warmup": 50},
        {"backend": "sqlite", "n_people": 100, "n_projects": 20, "hops": 3,
         "median_ms": 1.5, "p95_ms": 2.0, "repeats": 20, "warmup": 3},
        {"backend": "memory", "n_people": 100, "n_projects": 20, "hops": 3,
         "median_ms": 0.2, "p95_ms": 0.3, "repeats": 20, "warmup": 3},
        {"backend": "sqlite", "n_people": 100, "n_projects": 20, "hops": 4,
         "median_ms": 4.0, "p95_ms": 4.5, "repeats": 20, "warmup": 3},
        {"backend": "neo4j", "n_people": 100, "n_projects": 20, "hops": 4,
         "median_ms": 20.0, "p95_ms": 21.0, "repeats": 20, "warmup": 50},
        {"backend": "memory", "n_people": 100, "n_projects": 20, "hops": 4,
         "median_ms": 1.0, "p95_ms": 1.1, "repeats": 20, "warmup": 3},
    ]
    data = {
        "rows": rows,
        "parity": [{"n_people": 100, "hops": h, "match": parity_match} for h in (1, 3, 4)],
        "skipped": [],
        "seeds_per_run": 5,
    }
    if with_extras:
        data["protocol_floor"] = {"median_ms": 1.6, "p95_ms": 1.9, "repeats": 20, "warmup": 50}
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


def _exp3_fake(with_failures=True):
    data = {
        "rows": [
            {"algorithm": "greedy", "n_people": 100, "solve_ms": 5.0,
             "optimization_ratio": 0.707, "unfilled": 0, "pair_cap": None},
            {"algorithm": "milp", "n_people": 100, "solve_ms": 900.0,
             "optimization_ratio": 0.928, "unfilled": 0, "pair_cap": 1000},
            {"algorithm": "milp", "n_people": 100, "solve_ms": 1800.0,
             "optimization_ratio": 0.928, "unfilled": 0, "pair_cap": 5000},
        ],
        "violations": [
            {"algorithm": "greedy", "n_people": 100, "budget_violations": 1},
            {"algorithm": "milp", "n_people": 100, "pair_cap": 1000, "budget_violations": 0},
            {"algorithm": "milp", "n_people": 100, "pair_cap": 5000, "budget_violations": 0},
        ],
        "alternatives": [{"n_people": 100, "plan_count": 4, "total_ms": 30000,
                           "labels": ["A", "B", "C", "D"],
                           "quality_vs_a": [1.0, 0.99, 0.98, 0.97],
                           "jaccard_vs_a": [1.0, 0.65, 0.62, 0.60]}],
    }
    if with_failures:
        data["failures"] = [{"n_people": 300, "n_projects": 60, "seed": 42,
                              "status": "skipped", "reason": "extrapolated beyond budget"}]
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
