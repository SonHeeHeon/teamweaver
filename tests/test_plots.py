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
