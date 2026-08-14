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


def _capture_saved_figure(monkeypatch, tmp_path):
    """plots._save를 가로채 fig.savefig 이후 plt.close()로 사라지기 전에
    Figure 객체 자체를 붙잡는다(실제 저장은 그대로 수행한다)."""
    monkeypatch.setattr(plots, "FIGURES_DIR", tmp_path)
    captured = {}
    real_save = plots._save

    def spy_save(fig, name):
        captured[name] = fig
        return real_save(fig, name)

    monkeypatch.setattr(plots, "_save", spy_save)
    return captured


def test_exp3_tradeoff_keeps_pair_caps_as_separate_series(tmp_path, monkeypatch):
    """실제 exp3_algorithm.json처럼 MILP가 n_people당 pair_cap(1000/5000)별로
    별도 violations 행을 갖는 경우, 두 cap이 하나의 "milp" 선으로 뭉개지지
    않고 서로 다른 선(라벨·선 스타일)으로 남아야 한다. n_people만으로
    정렬·연결하는 순진한 구현이라면 같은 x=50에서 두 cap의 점이 하나의
    "milp" 라인으로 섞여 이 테스트가 실패한다."""
    captured = _capture_saved_figure(monkeypatch, tmp_path)
    exp3 = {"data": {"rows": [
        {"algorithm": "greedy", "n_people": 50, "solve_ms": 5.0, "optimization_ratio": 0.70},
        {"algorithm": "milp", "n_people": 50, "solve_ms": 800.0, "optimization_ratio": 0.93}],
        "violations": [
            {"algorithm": "greedy", "n_people": 50, "budget_violations": 2},
            {"algorithm": "milp", "n_people": 50, "pair_cap": 1000, "budget_violations": 0},
            {"algorithm": "milp", "n_people": 50, "pair_cap": 5000, "budget_violations": 1},
        ]}}
    plots.exp3_tradeoff(exp3)

    ax2 = captured["exp3_tradeoff.png"].axes[1]
    lines_by_label = {line.get_label(): line for line in ax2.get_lines()}
    assert lines_by_label.keys() == {"greedy", "milp (cap=1000)", "milp (cap=5000)"}
    assert list(lines_by_label["milp (cap=1000)"].get_ydata()) == [0]
    assert list(lines_by_label["milp (cap=5000)"].get_ydata()) == [1]
    assert (lines_by_label["milp (cap=1000)"].get_linestyle()
            != lines_by_label["milp (cap=5000)"].get_linestyle())


def test_log_axis_ticks_are_plain_decimals_not_mathtext(tmp_path, monkeypatch):
    """한글 폰트(예: AppleGothic)는 mathtext의 유니코드 마이너스(U+2212)
    글리프가 없어, 로그축의 기본 지수 표기("$\\mathdefault{10^{-2}}$")가
    음의 지수에서 깨진 기호로 렌더링된다. plots.py는 이를 피하려고 로그축
    포매터를 일반 십진 문자열("0.01")로 바꿔 두었다 — 그 결과물이 실제로
    mathtext 마커($, ^)를 포함하지 않는지 렌더된 눈금 라벨 텍스트로 직접
    검증한다."""
    captured = _capture_saved_figure(monkeypatch, tmp_path)
    exp1 = {"data": {"rows": [
        {"backend": "sqlite", "n_people": 50, "hops": 1, "median_ms": 0.02, "p95_ms": 0.03},
        {"backend": "sqlite", "n_people": 1000, "hops": 1, "median_ms": 20.0, "p95_ms": 25.0},
    ]}}
    plots.exp1_crossover(exp1)

    fig = captured["exp1_crossover.png"]
    ax = fig.axes[0]
    fig.canvas.draw()  # 눈금 라벨 텍스트를 실제로 계산시킨다
    labels = [t.get_text() for t in ax.yaxis.get_majorticklabels()]
    assert labels, "y축 눈금 라벨이 생성되지 않았다"
    assert all("$" not in lbl and "^" not in lbl for lbl in labels), labels
    assert any(lbl.startswith("0.") for lbl in labels), labels  # 음의 지수 구간 포함


def test_exp4_and_exp5_plots_write_png(tmp_path, monkeypatch):
    """Plan 3의 두 그림. 실제 결과 JSON과 같은 형태(래퍼 포함)를 받아야 하고,
    지표 이름이 부분적으로만 있는 결과에도 죽지 않아야 한다."""
    monkeypatch.setattr(plots, "FIGURES_DIR", tmp_path)
    exp4 = {"environment": {}, "data": {"rows": [
        {"backend": "sqlite", "query": "swap_diff", "n_people": 100, "median_ms": 0.026},
        {"backend": "neo4j", "query": "swap_diff", "n_people": 100, "median_ms": 0.653},
        {"backend": "sqlite", "query": "swap_diff", "n_people": 1000, "median_ms": 0.024},
        {"backend": "neo4j", "query": "swap_diff", "n_people": 1000, "median_ms": 0.604},
        {"backend": "sqlite", "query": "overfamiliar_pairs", "n_people": 100, "median_ms": 0.095},
        {"backend": "neo4j", "query": "overfamiliar_pairs", "n_people": 100, "median_ms": 1.810},
        {"backend": "sqlite", "query": "overfamiliar_pairs", "n_people": 1000, "median_ms": 0.981},
        {"backend": "neo4j", "query": "overfamiliar_pairs", "n_people": 1000, "median_ms": 15.946},
    ]}}
    p4 = plots.exp4_rag(exp4)
    assert p4.exists() and p4.stat().st_size > 1000

    rows = []
    for m, s, g in (("load_ms", 4.4, 51.2), ("disk_bytes_clean", 147456, 541458599),
                    ("append_cowork_ms", 0.23, 0.55), ("rehydrate_ms", 3.35, 15.16)):
        for n in (100, 1000):
            rows.append({"backend": "sqlite", "n_people": n, "metric": m, "value": s})
            rows.append({"backend": "neo4j", "n_people": n, "metric": m, "value": g})
    p5 = plots.exp5_persistence({"environment": {}, "data": {"rows": rows}})
    assert p5.exists() and p5.stat().st_size > 1000


def test_exp5_plot_uses_the_clean_disk_reading_not_the_contaminated_one(tmp_path, monkeypatch):
    """누적 판독(disk_bytes)은 이전 규모의 잔여를 포함해 규모별 비교에 쓸 수
    없고, 의사결정 규칙 2도 클린 판독으로 판정한다 — 그림도 같은 판독을 써야
    리포트와 그림이 다른 이야기를 하지 않는다."""
    captured = _capture_saved_figure(monkeypatch, tmp_path)
    rows = []
    for m in ("load_ms", "disk_bytes", "disk_bytes_clean"):
        for n in (100, 1000):
            rows.append({"backend": "sqlite", "n_people": n, "metric": m, "value": 1.0})
            rows.append({"backend": "neo4j", "n_people": n, "metric": m, "value": 2.0})
    plots.exp5_persistence({"data": {"rows": rows}})
    titles = [ax.get_title() for ax in captured["exp5_persistence.png"].axes]
    assert "disk_bytes_clean" in titles
    assert "disk_bytes" not in titles


def test_exp5_minor_log_ticks_are_also_plain_decimals(tmp_path, monkeypatch):
    """지표 값이 한 자릿수 미만 구간(append_*는 0.2~0.9ms)이면 matplotlib이
    **부눈금**에도 라벨을 단다. 주눈금만 포매터를 바꿔 두면 부눈금이 mathtext
    지수 표기로 남아 한글 폰트에서 유니코드 마이너스가 깨진다(실측에서 경고 확인)."""
    captured = _capture_saved_figure(monkeypatch, tmp_path)
    rows = []
    for n in (100, 1000):
        for backend, v in (("sqlite", 0.23), ("neo4j", 0.86)):
            rows.append({"backend": backend, "n_people": n,
                         "metric": "append_cowork_ms", "value": v})
    plots.exp5_persistence({"data": {"rows": rows}})
    fig = captured["exp5_persistence.png"]
    ax = fig.axes[0]
    fig.canvas.draw()
    labels = ([t.get_text() for t in ax.yaxis.get_majorticklabels()]
              + [t.get_text() for t in ax.yaxis.get_minorticklabels()])
    assert any(labels)
    assert all("$" not in lbl and "^" not in lbl for lbl in labels), labels
