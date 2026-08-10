"""결과 JSON → PNG. 노트북은 이 함수들을 호출만 한다(계산 로직 없음)."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                 # 헤드리스 환경에서도 동작
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

from core.config import REPO_ROOT

FIGURES_DIR = REPO_ROOT / "experiments" / "figures"
_COLORS = {"sqlite": "#4C78A8", "neo4j": "#F58518", "memory": "#54A24B",
           "greedy": "#E45756", "milp": "#4C78A8"}

# 한글 라벨(인원 수, 쿼리 지연, 절감률 …)이 기본 폰트에서는 네모(tofu)로 깨진다.
# macOS는 AppleGothic을 기본 제공하므로 우선 사용하고, 없으면 다른 한글 폰트로
# 순서대로 대체한다. 설치된 폰트가 하나도 없으면(예: CI 리눅스 이미지) 경고 없이
# matplotlib 기본값으로 조용히 넘어간다 — 한글이 깨지더라도 그림 생성 자체가
# 실패해서는 안 된다.
_KOREAN_FONT_CANDIDATES = (
    "AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Malgun Gothic",
    "Noto Sans CJK KR", "Noto Sans KR",
)


def _configure_korean_font() -> None:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _KOREAN_FONT_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


_configure_korean_font()


def _plain_log_formatter() -> FuncFormatter:
    """로그축 눈금을 "10⁻¹" 같은 mathtext 지수 표기 대신 "0.1" 식 일반 십진
    표기로 그린다.

    mathtext 지수 라벨은 'default' 스타일 카테고리를 통해 font.family를 그대로
    물려받는데, 한글 폰트(AppleGothic 등)에는 유니코드 마이너스(U+2212) 글리프가
    없어 음의 지수("10⁻¹")의 '-' 부분이 더미 기호로 깨진다. axes.unicode_minus는
    선형축의 일반 숫자 라벨에만 적용될 뿐 이 mathtext 지수 표기에는 영향을 주지
    않는다 — 실측 exp1 그림에서 "10⁻¹"이 깨진 기호로 렌더링되는 것을 실제로
    확인했다. mathtext 지수 표기 경로 자체를 쓰지 않도록 포매터를 바꿔 회피한다.
    """
    def fmt(x, _pos):
        if x <= 0:
            return str(x)
        return f"{x:g}"
    return FuncFormatter(fmt)


def _save(fig, name: str) -> Path:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURES_DIR / name
    fig.savefig(path, dpi=144, bbox_inches="tight")
    plt.close(fig)
    return path


def exp1_crossover(result: dict) -> Path:
    rows = result["data"]["rows"]
    hops = sorted({r["hops"] for r in rows})
    fig, axes = plt.subplots(1, len(hops), figsize=(4.2 * len(hops), 3.6), sharey=True)
    axes = [axes] if len(hops) == 1 else list(axes)
    for ax, h in zip(axes, hops):
        for backend in ("sqlite", "neo4j", "memory"):
            pts = sorted([(r["n_people"], r["median_ms"]) for r in rows
                          if r["backend"] == backend and r["hops"] == h])
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o",
                        label=backend, color=_COLORS[backend])
        ax.set_title(f"{h}-hop"); ax.set_xlabel("인원 수"); ax.set_xscale("log")
        ax.set_yscale("log"); ax.grid(alpha=0.3)
        ax.xaxis.set_major_formatter(_plain_log_formatter())
        ax.yaxis.set_major_formatter(_plain_log_formatter())
    axes[0].set_ylabel("쿼리 지연 (ms, 중앙값)")
    axes[-1].legend()
    fig.suptitle("실험 1 — 저장·탐색 계층 지연 비교")
    return _save(fig, "exp1_crossover.png")


def exp2_savings(result: dict) -> Path:
    d = result["data"]
    # constrained_layout: 기본 레이아웃은 서브플롯 타이틀("민감도 — …")과
    # fig.suptitle("실험 2 — …")이 같은 수평 밴드에서 겹쳐 글자가 뒤섞였다
    # (실측 확인 후 발견) — constrained_layout이 suptitle을 위한 공간을
    # 자동으로 예약해 이를 피한다.
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6), constrained_layout=True)
    tokens = d["token_counts"]
    ax1.bar(["Full-LLM", "Hybrid"], [tokens["full_llm"], tokens["hybrid"]],
            color=["#E45756", "#54A24B"])
    ax1.set_ylabel("입력 토큰 수"); ax1.set_title("토큰 소비량")
    ax1.grid(axis="y", alpha=0.3)
    curve = d["sensitivity"]
    ax2.plot([c["freetext_multiplier"] for c in curve],
             [c["savings_pct"] for c in curve], marker="o", color="#4C78A8")
    ax2.set_xlabel("자유서술 분량 배수"); ax2.set_ylabel("절감률 (%)")
    ax2.set_title("민감도 — 비정형 비중에 따른 절감률"); ax2.grid(alpha=0.3)
    fig.suptitle("실험 2 — 파이프라인 비용")
    return _save(fig, "exp2_savings.png")


def exp3_tradeoff(result: dict) -> Path:
    d = result["data"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6))
    for algo in ("greedy", "milp"):
        pts = [(r["solve_ms"], r["optimization_ratio"]) for r in d["rows"]
               if r["algorithm"] == algo]
        if pts:
            ax1.scatter([p[0] for p in pts], [p[1] for p in pts],
                        label=algo, color=_COLORS[algo], s=45)
    ax1.set_xscale("log"); ax1.set_xlabel("solve time (ms)")
    ax1.xaxis.set_major_formatter(_plain_log_formatter())
    ax1.set_ylabel("최적화율"); ax1.axhline(0.90, ls="--", c="gray", lw=1)
    ax1.set_title("품질 vs 소요 시간"); ax1.legend(); ax1.grid(alpha=0.3)

    # violations는 algorithm별로 그룹화하는 것만으로는 부족하다: MILP는
    # pair_cap(1000/5000)마다 별도 행을 갖고 있어서 n_people을 키로 단순
    # 정렬·연결하면 같은 x값에 서로 다른 cap의 점이 섞여 지그재그 선이 된다.
    # (algorithm, pair_cap) 쌍으로 묶어 cap마다 별도 선을 그린다.
    groups: dict[tuple[str, object], list[tuple[int, int]]] = {}
    for v in d["violations"]:
        key = (v["algorithm"], v.get("pair_cap"))
        groups.setdefault(key, []).append((v["n_people"], v["budget_violations"]))
    _linestyles = {}  # pair_cap별 선 스타일(같은 algorithm 내에서 cap 구분)
    for algo, cap in sorted(groups, key=lambda k: (k[0], k[1] is None, k[1])):
        pts = sorted(groups[(algo, cap)])
        if cap is None:
            label = algo
            ls = "-"
        else:
            label = f"{algo} (cap={cap})"
            ls = _linestyles.setdefault(cap, "-" if len(_linestyles) == 0 else "--")
        ax2.plot([p[0] for p in pts], [p[1] for p in pts], marker="s", linestyle=ls,
                 label=label, color=_COLORS[algo])
    ax2.set_xlabel("인원 수"); ax2.set_ylabel("예산 위반 프로젝트 수")
    ax2.set_title("제약 준수"); ax2.legend(); ax2.grid(alpha=0.3)
    fig.suptitle("실험 3 — Greedy vs MILP")
    return _save(fig, "exp3_tradeoff.png")
