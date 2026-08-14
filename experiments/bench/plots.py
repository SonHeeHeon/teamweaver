"""결과 JSON → PNG. 노트북은 이 함수들을 호출만 한다(계산 로직 없음)."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                 # 헤드리스 환경에서도 동작
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter, NullFormatter

from core.config import OPT_RATIO_TARGET, REPO_ROOT
from experiments import decision

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


def _byte_log_formatter() -> FuncFormatter:
    """바이트 축 눈금을 "541000000" 이나 "5e+08" 대신 "500M"으로 그린다.

    실험 5의 disk 패널은 147KB부터 541MB까지 네 자릿수 이상을 걸친다. 일반
    십진 포매터(`{x:g}`)는 이 구간에서 "5e+08" 같은 지수 표기를 내놓고, 눈금이
    촘촘한 로그축에서는 그 라벨들이 서로 겹쳐 아예 읽을 수 없었다(실측 확인).
    """
    units = ((1024 ** 3, "G"), (1024 ** 2, "M"), (1024, "K"))

    def fmt(x, _pos):
        if x <= 0:
            return str(x)
        for size, suffix in units:
            if x >= size:
                v = x / size
                return f"{v:.0f}{suffix}" if v >= 10 or v == int(v) else f"{v:.1f}{suffix}"
        return f"{x:g}"
    return FuncFormatter(fmt)


def _apply_log_ticks(ax, values, byte_axis: bool) -> None:
    """로그 y축의 주·부 눈금 포매터를 데이터 범위에 맞춰 정한다.

    값의 폭이 두 자릿수 미만이면 matplotlib이 **부눈금**에도 라벨을 다는데,
    부눈금 기본 포매터는 mathtext 지수 표기라 한글 폰트에서 유니코드 마이너스가
    깨진다. 반대로 폭이 넓은 축(디스크: 10^5~10^9)에 부눈금 라벨을 강제하면
    라벨 수십 개가 겹쳐 축 자체를 못 읽는다 — 그럴 때는 부눈금 라벨을 끈다.
    """
    formatter = _byte_log_formatter() if byte_axis else _plain_log_formatter()
    ax.yaxis.set_major_formatter(formatter)
    finite = [v for v in values if v and v > 0]
    span = (max(finite) / min(finite)) if finite else 1.0
    ax.yaxis.set_minor_formatter(formatter if span < 100 else NullFormatter())


def _save(fig, name: str) -> Path:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURES_DIR / name
    fig.savefig(path, dpi=144, bbox_inches="tight")
    plt.close(fig)
    return path


def exp1_crossover(result: dict) -> Path:
    rows = result["data"]["rows"]
    hops = sorted({r["hops"] for r in rows})
    fig, axes = plt.subplots(1, len(hops), figsize=(4.2 * len(hops), 3.6), sharey=True,
                             constrained_layout=True)
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
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6), constrained_layout=True)
    for algo in ("greedy", "milp"):
        pts = [(r["solve_ms"], r["optimization_ratio"]) for r in d["rows"]
               if r["algorithm"] == algo]
        if pts:
            ax1.scatter([p[0] for p in pts], [p[1] for p in pts],
                        label=algo, color=_COLORS[algo], s=45)
    ax1.set_xscale("log"); ax1.set_xlabel("solve time (ms)")
    ax1.xaxis.set_major_formatter(_plain_log_formatter())
    ax1.set_ylabel("최적화율"); ax1.axhline(OPT_RATIO_TARGET, ls="--", c="gray", lw=1)
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


def exp4_rag(result: dict) -> Path:
    """질의 × 규모 지연을 백엔드별로 그린다.

    y축은 로그다 — 같은 그림 안에서 0.02ms(sqlite)와 16ms(neo4j)를 함께 봐야 하고,
    선형축이면 SQLite 계열이 전부 바닥에 붙어 규모 추세를 읽을 수 없다.
    """
    d = decision.payload(result)
    rows = d["rows"]
    queries = sorted({r["query"] for r in rows})
    fig, axes = plt.subplots(1, len(queries), figsize=(3.1 * len(queries), 3.4),
                             sharey=True, constrained_layout=True)
    axes = [axes] if len(queries) == 1 else list(axes)
    for ax, q in zip(axes, queries):
        for backend in ("sqlite", "neo4j"):
            pts = sorted((r["n_people"], r["median_ms"]) for r in rows
                         if r["query"] == q and r["backend"] == backend)
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o",
                        label=backend, color=_COLORS[backend])
        ax.set_title(q, fontsize=9); ax.set_xlabel("인원 수")
        ax.set_xscale("log"); ax.set_yscale("log"); ax.grid(alpha=0.3)
        ax.xaxis.set_major_formatter(_plain_log_formatter())
        ax.yaxis.set_major_formatter(_plain_log_formatter())
    axes[0].set_ylabel("질의 지연 (ms, 중앙값)")
    axes[-1].legend()
    fig.suptitle("실험 4 — RAG 서브그래프 검색 워크로드 (질의별 · 규모별)")
    return _save(fig, "exp4_rag.png")


# 규모 축 막대의 좌우 배치. 지표별 서브플롯 안에서 백엔드 두 개를 나란히 세운다.
_EXP5_METRICS = ("load_ms", "disk_bytes_clean", "append_cowork_ms",
                 "append_review_ms", "rehydrate_ms")


def exp5_persistence(result: dict) -> Path:
    """지표별 막대(규모 × 백엔드).

    disk_bytes는 클린 슬레이트 판독(`disk_bytes_clean`)을 쓴다 — 누적 판독은
    이전 규모의 잔여를 포함해 규모별 비교에 쓸 수 없고, 의사결정 규칙 2도 클린
    판독으로 판정한다. y축은 로그다(147KB vs 541MB, 0.2ms vs 340ms).
    """
    d = decision.payload(result)
    rows = d["rows"]
    present = [m for m in _EXP5_METRICS if any(r["metric"] == m for r in rows)]
    scales = sorted({r["n_people"] for r in rows})
    vals = {(r["backend"], r["metric"], r["n_people"]): r["value"] for r in rows}

    fig, axes = plt.subplots(1, len(present), figsize=(3.0 * len(present), 3.4),
                             constrained_layout=True)
    axes = [axes] if len(present) == 1 else list(axes)
    width = 0.38
    for ax, m in zip(axes, present):
        xs = range(len(scales))
        seen = []
        for off, backend in ((-width / 2, "sqlite"), (width / 2, "neo4j")):
            ys = [vals.get((backend, m, n)) for n in scales]
            seen += [y for y in ys if y]
            ax.bar([x + off for x in xs], [y if y else float("nan") for y in ys],
                   width, label=backend, color=_COLORS[backend])
        ax.set_xticks(list(xs)); ax.set_xticklabels([str(n) for n in scales])
        ax.set_title(m, fontsize=9); ax.set_xlabel("인원 수")
        ax.set_yscale("log"); ax.grid(axis="y", alpha=0.3)
        byte_axis = m.startswith("disk")
        _apply_log_ticks(ax, seen, byte_axis)
        if seen:
            # 로그 막대는 기본적으로 축 상단이 최댓값에 정확히 붙어, 가장 높은
            # 막대의 꼭대기가 테두리와 겹치고 그 위에 눈금이 하나도 없어 크기를
            # 읽을 기준이 사라진다. 한 자릿수의 절반쯤 여유를 준다.
            ax.set_ylim(top=max(seen) * 2)
        ax.set_ylabel("bytes" if byte_axis else "ms")
    axes[-1].legend()
    # 패널은 5개(load/disk/append_cowork/append_review/rehydrate)지만 규칙 2가
    # 세는 지표는 4개다 — append_cowork_ms·append_review_ms는 "증분 갱신 비용"
    # 한 지표로 합쳐 센다(decision._RULE2_GROUPS). 제목이 패널 수와 다른 숫자를
    # 말하면 혼란스러우므로 둘 다 명시한다.
    fig.suptitle("실험 5 — 영속성 5개 패널 · 규칙 2 판정 지표는 4개"
                 "(append_cowork·append_review는 한 지표로 합산, 낮을수록 좋다, 로그 축)")
    return _save(fig, "exp5_persistence.png")
