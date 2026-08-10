from experiments.bench import plots, harness

def test_plot_functions_write_png(tmp_path, monkeypatch):
    monkeypatch.setattr(plots, "FIGURES_DIR", tmp_path)
    exp1 = {"data": {"rows": [
        {"backend": "sqlite", "n_people": 50, "hops": 1, "median_ms": 0.4, "p95_ms": 0.6},
        {"backend": "memory", "n_people": 50, "hops": 1, "median_ms": 0.1, "p95_ms": 0.2},
        {"backend": "sqlite", "n_people": 50, "hops": 2, "median_ms": 0.9, "p95_ms": 1.1},
        {"backend": "memory", "n_people": 50, "hops": 2, "median_ms": 0.15, "p95_ms": 0.3}]}}
    p1 = plots.exp1_crossover(exp1)
    assert p1.exists() and p1.stat().st_size > 1000

    exp2 = {"data": {"token_counts": {"full_llm": 1000, "hybrid": 300, "review_count": 10},
                     "cost_usd": {"full_llm": 0.9, "hybrid": 0.3},
                     "sensitivity": [{"freetext_multiplier": 1.0, "savings_pct": 66.0},
                                     {"freetext_multiplier": 2.0, "savings_pct": 50.0}]}}
    p2 = plots.exp2_savings(exp2)
    assert p2.exists() and p2.stat().st_size > 1000

    exp3 = {"data": {"rows": [
        {"algorithm": "greedy", "n_people": 50, "solve_ms": 5.0, "optimization_ratio": 0.70},
        {"algorithm": "milp", "n_people": 50, "solve_ms": 800.0, "optimization_ratio": 0.93}],
        "violations": [{"algorithm": "greedy", "n_people": 50, "budget_violations": 2},
                       {"algorithm": "milp", "n_people": 50, "budget_violations": 0}]}}
    p3 = plots.exp3_tradeoff(exp3)
    assert p3.exists() and p3.stat().st_size > 1000
