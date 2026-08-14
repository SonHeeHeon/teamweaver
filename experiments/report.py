"""results/*.json → optimization_report.md.

모든 수치는 JSON에서 읽는다. 리포트를 수기로 고치지 말 것 — 실험을 다시 돌리면
이 스크립트를 재실행해 갱신한다.

수치가 `experiments/results/*.json`에 없으면(예: 실험 2의 fixture 생성 비용처럼
`.omc/`에만 기록되고 git에 커밋되지 않는 값) 이 파일은 해당 수치를 절대 타이핑하지
않는다 — 대신 정성적으로 서술하거나 생략한다. 반대로 `fixtures/meta.json`처럼
git으로 추적되는 JSON은 results/*.json과 마찬가지로 유효한 소스로 취급한다.
README.md도 마찬가지다(git 추적 대상) — `_frozen_fixture_ratio()`가 손타이핑 대신
정규식으로 파싱해 읽는다.
"""
import json
import re

from core.config import FIXTURES_DIR, OPT_RATIO_TARGET, REPO_ROOT
from experiments import decision
from experiments.bench import harness

OUT_PATH = REPO_ROOT / "experiments" / "optimization_report.md"


def _median(rows: list[dict], backend: str, n: int, hops: int) -> float | None:
    for r in rows:
        if r["backend"] == backend and r["n_people"] == n and r["hops"] == hops:
            return r["median_ms"]
    return None


def _p95(rows: list[dict], backend: str, n: int, hops: int) -> float | None:
    for r in rows:
        if r["backend"] == backend and r["n_people"] == n and r["hops"] == hops:
            return r.get("p95_ms")
    return None


def _result_count(rows: list[dict], n: int, hops: int) -> int | None:
    """parity가 맞는 한(백엔드 결과 일치, 아래에서 확인) 결과 수는 백엔드 독립적이다
    -- 아무 백엔드에서나 하나 찾아 쓴다."""
    for r in rows:
        if r["n_people"] == n and r["hops"] == hops and "result_count" in r:
            return r["result_count"]
    return None


def _fixture_meta() -> dict:
    path = FIXTURES_DIR / "meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _frozen_fixture_ratio() -> float | None:
    """README.md(git 추적 대상)의 LLM 재동결 기록에서 커밋된 데모 fixture(n=100,
    review_mode=llm)의 optimization_ratio(0.9300)를 정규식으로 읽는다 -- 손타이핑
    대신 커밋된 소스에서 직접 파싱해 README 서술이 바뀌면 이 값도 따라간다.

    이 값은 exp3 스윕이 만들어내는 값(같은 n=100이지만 seed=42로 새로 생성한
    템플릿 리뷰 데이터셋)과 다른 데이터에서 나온다 -- 최종 리뷰 Critical 2가
    지적한 "리포트가 스윕을 데모 fixture라고 잘못 부른다"는 문제를 바로잡기
    위한 참고치다. README를 못 읽거나 패턴이 안 맞으면 조용히 None을 반환한다
    (리포트 생성 자체를 깨면 안 된다).
    """
    path = REPO_ROOT / "README.md"
    if not path.exists():
        return None
    try:
        text = path.read_text("utf-8")
    except OSError:
        return None
    m = re.search(r"ratio moved slightly on refreeze:\s*\*\*([\d.]+)\s*(?:→|->)\s*([\d.]+)\*\*", text)
    if not m:
        return None
    try:
        return float(m.group(2))
    except ValueError:
        return None


def _ipc_dominance(rows: list[dict], floor_median: float) -> tuple[float, float] | None:
    """hops=1에서 protocol_floor가 neo4j 시간의 몇 %를 차지하는지, (min, max) 범위."""
    pcts = []
    for n in sorted({r["n_people"] for r in rows}):
        neo = _median(rows, "neo4j", n, 1)
        if neo:
            pcts.append(floor_median / neo * 100)
    if not pcts:
        return None
    return min(pcts), max(pcts)


def _repeat_count_caption(rows: list[dict]) -> str:
    """rows에 기록된 harness.measure repeats를 그대로 읽어 단위 각주를 만든다.

    실측값을 20으로 타이핑해두면 실험을 다른 repeats로 재실행했을 때 이 각주만
    조용히 낡아버린다(다른 곳은 이미 rows에서 동적으로 읽고 있었다) — 그래서 rows
    자체에서 읽는다. 백엔드마다 repeats가 갈리는 경우(현재 데이터에는 없음, 전부
    20)까지 대비해 값이 하나가 아니면 그 사실 자체를 각주에 남긴다.
    """
    counts = sorted({r["repeats"] for r in rows if "repeats" in r})
    if not counts:
        return "*단위: ms, 중앙값. 연결 수립·적재 시간 제외.*"
    if len(counts) == 1:
        return f"*단위: ms, 반복 {counts[0]}회 중앙값. 연결 수립·적재 시간 제외.*"
    return (f"*단위: ms, 중앙값(반복 횟수는 백엔드마다 다름: {', '.join(str(c) for c in counts)}회). "
            "연결 수립·적재 시간 제외.*")


def _neo4j_sqlite_ratio_at_hops4(rows: list[dict]) -> tuple[float, float] | None:
    ratios = []
    for n in sorted({r["n_people"] for r in rows}):
        neo = _median(rows, "neo4j", n, 4)
        sql = _median(rows, "sqlite", n, 4)
        if neo and sql:
            ratios.append(neo / sql)
    if not ratios:
        return None
    return min(ratios), max(ratios)


def _exp1_headline(rows: list[dict]) -> str | None:
    """원 가설("규모·hops가 커지면 Neo4j가 역전한다")이 성립하는지 실측으로 직접 판정해
    24행 표 위에 한 줄 headline으로 얹는다 — 표를 끝까지 훑지 않아도 결론을 놓치지
    않도록. neo4j가 sqlite보다 빠른 (인원, hops) 조합이 하나라도 있으면 가설이
    성립하는 구간이 있다고, 하나도 없으면 가설이 기각됐다고 데이터로부터 판정한다.
    """
    keys = sorted({(r["n_people"], r["hops"]) for r in rows})
    if not keys:
        return None
    crossovers = []
    full_order_holds = True          # memory < sqlite < neo4j 가 전 구간에서 유지되는가
    for n, h in keys:
        neo = _median(rows, "neo4j", n, h)
        sql = _median(rows, "sqlite", n, h)
        mem = _median(rows, "memory", n, h)
        if neo is not None and sql is not None and neo < sql:
            crossovers.append((n, h))
        # 3자 순서도 데이터에서 판정한다 — headline이 검증하지 않은 주장을 하면
        # 재실행에서 순서가 바뀌어도 문장이 그대로 남아 사실과 어긋난다.
        if None in (neo, sql, mem) or not (mem < sql < neo):
            full_order_holds = False
    n_lo, n_hi = keys[0][0], keys[-1][0]
    h_lo, h_hi = min(h for _, h in keys), max(h for _, h in keys)
    n_scope = f"{n_lo}~{n_hi}명" if n_lo != n_hi else f"{n_lo}명"
    scope = f"{n_scope}, hops {h_lo}~{h_hi}" if h_lo != h_hi else f"{n_scope}, hops {h_lo}"
    if not crossovers:
        order = ("전 구간에서 memory < sqlite < neo4j 순서가 유지된다"
                 if full_order_holds else
                 "전 구간에서 neo4j가 sqlite보다 느리다(단, memory를 포함한 3자 순서가 "
                 "모든 조합에서 유지되지는 않는다 — 표 참조)")
        return (f"**측정 범위({scope}) {order} — "
                "\"규모·hops가 커지면 Neo4j가 역전한다\"는 이 실험의 원 가설은 기각된다.**")
    pts = ", ".join(f"n={n}·hops={h}" for n, h in crossovers)
    return (f"**측정 범위({scope})에서 neo4j가 sqlite보다 빠른 구간이 존재한다({pts}) — "
            "아래 표에서 직접 확인할 것.**")


def _result_count_monotonicity_note(rows: list[dict]) -> str | None:
    """인원 수(n_people)가 늘어난다고 순회 결과 수(result_count, 곧 실제 작업량)가
    항상 함께 늘지는 않는다 — 실측으로 발견되면(예: n=500이 n=300보다 결과 수가
    적음) 표에 그대로 드러나지만, "인원이 많을수록 느릴 것"이라는 직관과
    어긋나는 지점이라 명시적으로 짚어 둔다(최종 리뷰 Important 8: n=500 dip
    미설명). 손타이핑 없이 rows에서 직접 찾는다.
    """
    by_hops: dict[int, dict[int, int]] = {}
    for r in rows:
        rc = r.get("result_count")
        if rc is None:
            continue
        by_hops.setdefault(r["hops"], {})[r["n_people"]] = rc
    dips = []
    for h, by_n in sorted(by_hops.items()):
        ordered = sorted(by_n.items())
        for (n0, rc0), (n1, rc1) in zip(ordered, ordered[1:]):
            if rc1 < rc0:
                dips.append((h, n0, rc0, n1, rc1))
    if not dips:
        return None
    examples = "; ".join(f"hops={h}: n={n0}→{n1}에서 결과수 {rc0}→{rc1}"
                         for h, n0, rc0, n1, rc1 in dips)
    return (f"> 인원 수가 늘어난다고 순회 결과 수(result_count)가 항상 함께 늘지는 않는다 "
            f"— {examples}. 인원 수를 작업량의 단조 대리지표로 쓰지 말 것(결과 수가 적으면 "
            "순회할 것도 적어 그만큼 더 빠르다).")


def _p95_summary_lines(rows: list[dict]) -> list[str]:
    """실험 1 방법론은 "중앙값과 p95"를 보고한다고 명시하지만 기존 표는 중앙값뿐이었다
    (최종 리뷰 Important 5). 24행 표를 그대로 두 배로 넓히는 대신, hop별 p95 범위를
    별도의 소형 표로 낸다."""
    if not any("p95_ms" in r for r in rows):
        return []
    n_values = sorted({r["n_people"] for r in rows})
    n_lo, n_hi = n_values[0], n_values[-1]
    n_scope = f"{n_lo}~{n_hi}명" if n_lo != n_hi else f"{n_lo}명"
    hops_list = sorted({r["hops"] for r in rows})
    lines = [f"**p95 요약 (hop별, 인원 {n_scope} 범위의 최소~최대):**", "",
             "| hops | sqlite p95(ms) | neo4j p95(ms) | memory p95(ms) |",
             "|---:|---:|---:|---:|"]
    for h in hops_list:
        cells = []
        for b in ("sqlite", "neo4j", "memory"):
            vals = [r["p95_ms"] for r in rows
                    if r["backend"] == b and r["hops"] == h and "p95_ms" in r]
            if vals:
                lo, hi = min(vals), max(vals)
                cells.append(f"{lo:.3f}" if lo == hi else f"{lo:.3f}~{hi:.3f}")
            else:
                cells.append("—")
        lines.append(f"| {h} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _exp1(d: dict) -> str:
    rows, parity, skipped = d["rows"], d["parity"], d["skipped"]
    floor = d.get("protocol_floor")
    lines = ["## 실험 1 — 저장·탐색 계층 3자 비교", "",
             "**질문:** 인력 집합의 N-hop 협업 문맥을 수집·집계할 때 어느 저장 방식이 빠른가?", ""]
    headline = _exp1_headline(rows)
    if headline:
        lines += [headline, ""]
    lines += ["| 인원 | hops | sqlite | neo4j | memory | 결과수 |",
             "|---:|---:|---:|---:|---:|---:|"]
    keys = sorted({(r["n_people"], r["hops"]) for r in rows})
    for n, h in keys:
        cells = []
        for b in ("sqlite", "neo4j", "memory"):
            m = _median(rows, b, n, h)
            cells.append(f"{m:.3f}" if m is not None else "—")
        rc = _result_count(rows, n, h)
        cells.append(str(rc) if rc is not None else "—")
        lines.append(f"| {n} | {h} | " + " | ".join(cells) + " |")
    lines += ["", _repeat_count_caption(rows), ""]

    bad = [p for p in parity if not p["match"]]
    if bad:
        lines += [f"> ⚠️ **백엔드 결과 불일치 {len(bad)}건** — 동일 질문에 서로 다른 답을 내므로 "
                  "이 구간의 비교는 무효다. 원인 규명 전까지 수치를 인용하지 말 것.", ""]
    else:
        lines += [f"> ✅ 백엔드 결과 일치 {len(parity)}건 전부 확인 — 세 방식이 같은 도달 집합을 반환한다.", ""]
    if skipped:
        lines += ["> 제외된 측정: " + "; ".join(
            f"{s['backend']}@{s.get('n_people','-')}({s['reason']})" for s in skipped), ""]

    if floor is not None:
        lines += [f"**프로토콜 바닥값(protocol_floor):** 중앙값 {floor['median_ms']:.4f}ms / "
                  f"p95 {floor['p95_ms']:.4f}ms — 실제 그래프 순회 없이 요청·응답만 왕복하는 "
                  "최소 IPC 비용의 실측치다.", ""]

    lines += _p95_summary_lines(rows)

    dip_note = _result_count_monotonicity_note(rows)
    if dip_note:
        lines += [dip_note, ""]

    lines += ["### 스코프 명시 (반드시 함께 읽을 것)", ""]
    n_stmt = 1
    ipc = _ipc_dominance(rows, floor["median_ms"]) if floor is not None else None
    if ipc:
        lo, hi = ipc
        pct_str = f"{lo:.0f}%" if lo == hi else f"{lo:.0f}~{hi:.0f}%"
        # 바닥값이 hops=1 쿼리 시간을 넘어서는 구간(비율 > 100%)이 나오면 "몇 %를
        # 차지한다"는 표현 자체가 성립하지 않는다 — 둘이 측정 오차 안에서 구별되지
        # 않는다는 뜻이고, 그게 오히려 더 강한 진술이다(순회 작업이 사실상 없다).
        if hi > 100:
            lines.append(
                f"{n_stmt}. **hops=1 비교는 순회가 아니라 IPC가 지배한다.** "
                f"hops=1 neo4j 시간은 순수 왕복 비용(protocol_floor, 중앙값 "
                f"{floor['median_ms']:.3f}ms)과 측정 오차 안에서 구별되지 않는다 "
                f"— 바닥값이 쿼리 시간의 {pct_str}에 해당해 일부 구간에서는 바닥값이 "
                "쿼리 시간을 넘어서기까지 한다. 즉 이 구간에서 잰 것은 그래프 순회 "
                "비용이 아니라 프로토콜 왕복 비용이므로, 얕은 hop만 보고 백엔드를 "
                "비교하면 오해를 부른다.")
        else:
            lines.append(
                f"{n_stmt}. **hops=1 비교는 순회가 아니라 IPC가 지배한다.** "
                f"protocol_floor(중앙값 {floor['median_ms']:.3f}ms)가 hops=1 neo4j 시간의 "
                f"{pct_str}를 차지한다 — 얕은 hop만 보고 백엔드를 비교하는 것은 "
                "그래프 순회 비용보다 프로토콜 왕복 비용을 주로 재는 셈이라 오해를 부른다.")
        n_stmt += 1
    lines.append(f"{n_stmt}. **인메모리 백엔드의 승리는 거의 동어반복적(near-tautological)이다.** "
                 "이 측정은 빌드/적재 비용을 제외했고, 인메모리 구조는 이미 전량 RAM에 상주하며 "
                 "이번 워크로드는 어차피 RAM에 다 들어간다 — \"발견\"이 아니라 설계상 당연한 결과다.")
    n_stmt += 1
    lines.append(f"{n_stmt}. **이 실험은 제안된 하이브리드(\"Neo4j 저장·탐색 + 인메모리 연산\")가 "
                 "아니라 세 개의 독립 대안을 각각 측정한 것이다.** 하이브리드의 실제 가치(영속성, "
                 "임의 탐색, Graph RAG 서브그래프 검색)는 이 벤치마크가 다루지 않는다.")
    n_stmt += 1
    lines.append(f"{n_stmt}. **측정 구간 자체가 백엔드 간 비대칭이다.** 인메모리 방식은 "
                 "`node_polarity`를 그래프 빌드 시점에 미리 계산해 두고(빌드는 측정 밖) 순회 "
                 "시점에는 조회만 한다(`core/graph/memory_graph.py:56-58`). 반면 SQLite는 매 "
                 "호출마다 `node_polarity` CTE를 다시 계산하고, Neo4j는 매 호출마다 "
                 "`avg(v.polarity)`를 다시 집계한다 — 즉 측정된 인메모리 시간에는 이 집계 비용이 "
                 "아예 들어있지 않다. 스코프 노트 2(적재 비용 제외)와는 별개의 비대칭이다.")
    lines.append("")

    ratio4 = _neo4j_sqlite_ratio_at_hops4(rows)
    if ratio4:
        lo, hi = ratio4
        ratio_str = f"{lo:.2f}배" if lo == hi else f"{lo:.2f}~{hi:.2f}배"
        lines += [f"> neo4j/sqlite 배율은 hops=4 기준 {ratio_str} 범위에서 평평하다 — "
                  "규모가 커진다고 격차가 좁혀지는 추세는 관측되지 않았다.", ""]

    lines += ["![실험1](figures/exp1_crossover.png)", ""]
    return "\n".join(lines)


def _exp2(d: dict) -> str:
    t, c = d["token_counts"], d["cost_usd"]
    lines = ["## 실험 2 — 파이프라인 비용 (Full-LLM vs Hybrid)", "",
             f"**모델:** `{d['model']}` · **단가 기준일:** {d['pricing_as_of']} · "
             f"**리뷰 {t['review_count']}건**", "",
             "| 방식 | 입력 토큰 | 추정 비용(USD) |", "|---|---:|---:|",
             f"| Full-LLM (정형+항목+서술 전부) | {t['full_llm']:,} | {c['full_llm']:.4f} |",
             f"| Hybrid (자유서술만) | {t['hybrid']:,} | {c['hybrid']:.4f} |", "",
             f"**측정된 절감률: {d['savings_pct']:.1f}%**", ""]
    lines += ["> 이 절감률은 대수적으로 입력 토큰 비율(1 − hybrid/full)과 같다 — 출력 토큰 "
             "가정(out_ratio)이 두 arm에 같은 배수로 적용되는 한 그 배수는 cost 비율에서 "
             "상쇄되어 결과에 전혀 영향을 주지 않는다"
             "(`tests/test_exp2.py::test_savings_pct_is_independent_of_out_ratio`가 회귀 "
             "테스트로 고정). 즉 아래 달러 금액은 출력 토큰 가정에 따라 크게 바뀌지만, 이 "
             "절감률 자체는 그 가정과 무관하다 — 달러는 노출을 더할 뿐 절감률에 대한 추가 "
             "정보를 주지 않는다.", ""]
    lines += ["> 절감률은 목표치가 아니라 **측정 결과**다. 우리 fixture의 정형:비정형 비중이 "
             "이 값을 결정하므로 아래 민감도 곡선을 함께 본다.", "",
             "> 프로젝트 초기 설계 문서는 이보다 **훨씬 높은** 절감률을 목표로 제시했다 — 위 측정치는 "
             "그 목표에 크게 못 미친다. 최초 목표 수치 자체는 `experiments/results/*.json` 범위 밖의 "
             "로컬 설계 문서에만 있어 이 리포트에는 싣지 않는다(원 목표는 "
             "`.omc/plan/2026-08-02-teamweaver-poc-design.md` 참조 — 이 경로는 `.gitignore`의 "
             "`.omc/` 규칙 대상이라 이 저장소를 클론한 사람은 파일을 열어볼 수 없다; 그래서 정확한 "
             "수치를 싣는 대신 존재와 위치만 밝힌다).", ""]
    if d.get("output_token_assumption") is not None:
        lines += [f"*출력 토큰/입력 토큰 비율 가정: {d['output_token_assumption']:.3f} — "
                  f"근거: {d.get('output_token_assumption_basis', '-')}*", ""]

    oms = d.get("output_model_sensitivity")
    if oms:
        _labels = {"output_proportional_to_input": "출력 ∝ 입력 (이 실험이 채택)",
                  "output_constant_across_arms": "출력 두 arm 동일(실측 completion 토큰 고정)"}
        lines += ["**출력 토큰 모델 민감도** — savings_pct가 출력 토큰을 어떻게 가정하느냐에 "
                 "얼마나 의존하는지 정반대 가정 두 가지로 계산했다:", "",
                 "| 출력 토큰 가정 | 절감률(%) |", "|---|---:|"]
        for s in oms:
            lines.append(f"| {_labels.get(s['model'], s['model'])} | {s['savings_pct']:.2f} |")
        lines.append("")
        by_model = {s["model"]: s for s in oms}
        prop = by_model.get("output_proportional_to_input")
        const = by_model.get("output_constant_across_arms")
        if prop and const:
            lines += [f"> 출력 토큰 가정을 '입력에 비례'에서 '두 arm 동일'로 바꾸면 절감률은 "
                     f"{prop['savings_pct']:.1f}%→{const['savings_pct']:.1f}%로 바뀐다 — 이 실험이 "
                     "실제로 채택한 가정(출력 ∝ 입력)이 헤드라인 절감률을 얼마나 떠받치고 있는지를 "
                     "보여준다. 게다가 5.777이라는 비율 자체는 이 실험의 페이로드가 아니라 "
                     "hybrid-shaped parse 워크로드(자유서술만 담은 기존 파싱 프롬프트)에서 측정된 "
                     "것이므로, 위 표의 '이 실험이 채택' 행은 서로 다른 페이로드 형태 간 외삽에 "
                     "기댄 결과라는 점을 함께 읽을 것 — 특히 그 비율이 큰 이유 자체가 추론 모델의 "
                     "내부 추론 토큰(과제 난이도에 비례, 프롬프트 길이에 비례하지 않음)이라서 "
                     "고정 출력 스키마를 갖는 이 추출 과제에 '출력 ∝ 입력' 가정을 적용하는 것은 "
                     "특히 취약하다.", ""]

    if d["sensitivity"]:
        lines += ["| 자유서술 분량 배수 | 절감률(%) |", "|---:|---:|"]
        for s in d["sensitivity"]:
            lines.append(f"| {s['freetext_multiplier']}× | {s['savings_pct']:.1f} |")
        lines.append("")
        by_mult = sorted(d["sensitivity"], key=lambda s: s["freetext_multiplier"])
        if len(by_mult) > 1:
            lo, hi = by_mult[0], by_mult[-1]
            lines += [f"> 자유서술 분량이 {lo['freetext_multiplier']}×~{hi['freetext_multiplier']}×"
                      f"일 때 절감률은 {lo['savings_pct']:.1f}%→{hi['savings_pct']:.1f}%로 단조 "
                      "감소한다 — 절감률은 고정 상수가 아니라 정형/비정형 리뷰 내용 비중에 강하게 "
                      "의존하는 값이다.", ""]

    acc = d["accuracy"]
    lines += [f"**정확도 검증:** {acc['note']}"
              + (f" (표본 {acc['checked']}건)" if acc["checked"] else ""), ""]

    lat = d.get("latency")
    if lat is not None:
        status = "미측정" if not lat.get("measured") else "측정됨"
        lines += [f"**지연(latency) 검증:** {status} — {lat.get('note', '')}", ""]
        if lat.get("basis"):
            lines += [f"> 참고치 근거: {lat['basis']}", ""]
        if lat.get("caveat"):
            lines += [f"> 주의: {lat['caveat']}", ""]
        if lat.get("source"):
            lines += [f"> 출처: `{lat['source']}`", ""]

    meta = _fixture_meta()
    if meta.get("review_mode") == "llm":
        model_bits = ""
        if meta.get("gen_model") or meta.get("parse_model"):
            model_bits = (f", 생성 모델 `{meta.get('gen_model', '-')}`, 파싱 모델 "
                          f"`{meta.get('parse_model', '-')}`(`fixtures/meta.json` 기록)")
        lines += [f"> 이 비교의 전제는 fixture가 실제 LLM 호출로 생성된 리뷰"
                  f"({t['review_count']}건, `review_mode=llm`{model_bits})라는 것이다 — 템플릿 생성 "
                  "리뷰였다면 자유서술의 극성(polarity)이 정형 `item_score`의 결정론적 함수였으므로 "
                  "비정형 항목이 별도 정보를 담지 않아, 정형 대 비정형을 비교하는 이 실험의 전제 "
                  "자체가 성립하지 않았다. (정확한 재동결 비용은 results/*.json 범위 밖이라 이 "
                  "리포트에는 싣지 않는다 — Task 6 작업 기록 참조.)", ""]

    lines += ["![실험2](figures/exp2_savings.png)", ""]
    return "\n".join(lines)


def _exp3(d: dict) -> str:
    lines = ["## 실험 3 — 알고리즘 (Greedy vs MILP)", ""]
    if d.get("data_source"):
        lines += [f"> **데이터 출처:** {d['data_source']}", ""]
    lines += ["| 알고리즘 | 인원 | pair_cap | solve(ms) | 최적화율 | 미충원 |",
             "|---|---:|---:|---:|---:|---:|"]
    rows = sorted(d["rows"], key=lambda r: (r["n_people"], r["algorithm"],
                                             r["pair_cap"] if r["pair_cap"] is not None else -1))
    for r in rows:
        cap = r["pair_cap"] if r["pair_cap"] is not None else "—"
        lines.append(f"| {r['algorithm']} | {r['n_people']} | {cap} | "
                     f"{r['solve_ms']:.1f} | {r['optimization_ratio']:.3f} | {r['unfilled']} |")
    lines += ["", "### 제약 준수 — 이 실험의 핵심", "",
              "| 알고리즘 | 인원 | pair_cap | 예산 위반 프로젝트 수 |", "|---|---:|---:|---:|"]
    violations = sorted(d["violations"], key=lambda v: (v["n_people"], v["algorithm"],
                         v.get("pair_cap") if v.get("pair_cap") is not None else -1))
    for v in violations:
        cap = v.get("pair_cap")
        cap_cell = cap if cap is not None else "—"
        lines.append(f"| {v['algorithm']} | {v['n_people']} | {cap_cell} | "
                     f"{v['budget_violations']} |")
    lines += ["", "> Greedy는 점수만 보고 담아 예산을 넘긴 해를 내놓는다. MILP는 예산을 "
              "지키는 대신 부족분을 미충원으로 **정직하게 드러낸다** — 실행 가능한 해와 "
              "그렇지 않은 해의 차이다.", ""]
    lines += ["> Greedy는 구조적으로 예산 로직이 없다 — 각 프로젝트에 필요한 인력을 점수순으로 "
              "다 배정한 *뒤에야* 그 프로젝트의 누적 비용을 예산과 비교해 위반 여부만 *기록*할 "
              "뿐, 그 결과로 어떤 후보도 거부하거나 배정을 되돌리지 않는다"
              "(`core/optimize/greedy.py`, `for j, proj in ...` 루프 안에서 해당 프로젝트의 배정이 "
              "끝난 뒤 `if cost > proj.monthly_budget`을 확인). 따라서 아래 \"MILP 0건 vs Greedy "
              "0→1→2건\"은 예산 제약을 아예 갖지 않는 베이스라인이 치르는 구조적 대가를 재는 "
              "것이지, **저비용의 제약-인지 휴리스틱이 존재하지 않는다는 근거가 아니다.**", ""]

    milp_v = [v for v in violations if v["algorithm"] == "milp"]
    greedy_v = sorted((v for v in violations if v["algorithm"] == "greedy"),
                       key=lambda v: v["n_people"])
    if milp_v and all(v["budget_violations"] == 0 for v in milp_v):
        milp_ns = sorted({v["n_people"] for v in milp_v})
        milp_caps = sorted({v.get("pair_cap") for v in milp_v if v.get("pair_cap") is not None})
        lines += [f"> MILP는 측정된 모든 규모({', '.join(str(n) for n in milp_ns)}명)·모든 "
                  f"pair_cap({', '.join(str(c) for c in milp_caps)})에서 예산 위반 0건이다.", ""]
    if greedy_v:
        # 최종 리뷰 Important 4: 원시 건수(0→1→2)만 보면 "규모가 커질수록 늘어난다"는
        # 인상을 주지만, 프로젝트 수 대비 비율로 정규화하면(exp1이 이미 그렇게 하듯)
        # 0%→5%→5%로 평평하다. 원시 건수와 비율을 나란히 보여주고 성장 서사를 주장하지
        # 않는다 — 판단은 독자에게 맡긴다.
        parts = []
        for v in greedy_v:
            npj = v.get("n_projects")
            raw = v["budget_violations"]
            if npj:
                parts.append(f"n={v['n_people']}: {raw}/{npj}건({raw / npj * 100:.0f}%)")
            else:
                parts.append(f"n={v['n_people']}: {raw}건")
        lines += [f"> Greedy의 예산 위반(원시 건수/프로젝트 수 대비 비율): {', '.join(parts)}.", ""]

    # optimization_ratio vs 정책 목표(90%) — cap이 여럿이면 가장 작은 cap을 기준으로 삼는다.
    milp_rows = [r for r in d["rows"] if r["algorithm"] == "milp" and r["pair_cap"] is not None]
    if milp_rows:
        min_cap = min(r["pair_cap"] for r in milp_rows)
        base_rows = sorted((r for r in milp_rows if r["pair_cap"] == min_cap),
                            key=lambda r: r["n_people"])
        parts = ", ".join(f"n={r['n_people']}: {r['optimization_ratio']:.4f}" for r in base_rows)
        below = [r for r in base_rows if r["optimization_ratio"] < OPT_RATIO_TARGET]
        lines.append(f"> MILP(pair_cap={min_cap})의 optimization_ratio는 {parts}이다.")
        if below:
            below_ns = ", ".join(f"n={r['n_people']}" for r in below)
            lines.append(f"> **{below_ns}에서 {OPT_RATIO_TARGET:.0%} 목표선 아래로 떨어진다** — "
                          "\"목표 90%\"는 이 스윕이 기준으로 삼은 n=100(seed=42로 새로 생성한 "
                          "데이터셋 — 커밋된 데모 fixture가 아니다)에서만 성립하고, 규모가 커지면 "
                          "유지된다는 보장이 없다.")
        fixed_ratio = _frozen_fixture_ratio()
        n100_row = next((r for r in base_rows if r["n_people"] == 100), None)
        if fixed_ratio is not None and n100_row is not None:
            lines.append(f"> 참고로 커밋된 데모 fixture(LLM 모드 리뷰, `README.md`의 E2E 스모크 "
                          f"기록 참고)는 n=100에서 optimization_ratio={fixed_ratio:.4f}를 낸다 — "
                          f"이 스윕의 n=100 값({n100_row['optimization_ratio']:.4f})과는 다른 "
                          "수치다. 인원·프로젝트 수는 fixture와 같지만 리뷰 텍스트가 다르게 "
                          "생성되어 C 행렬(시너지)이 달라지기 때문이다 — 두 수치를 같은 것으로 "
                          "섞어 인용하지 말 것.")
        lines.append("")

        sample_params = next((r.get("milp_params") for r in base_rows if r.get("milp_params")), None)
        if sample_params:
            lines += [f"> **optimization_ratio가 재는 것:** 분자·분모 모두 스킬 항(Σ S_ij·a_ij)만 "
                     "쓴다(`core/optimize/metrics.py::optimization_ratio`) — 하지만 배치 자체는 "
                     "`solve_milp`가 스킬 + λ·시너지 − μ·클리크 패널티 − slack 패널티라는 다중 항 "
                     "목적함수를 최대화해 고른 것이다. 즉 이 비율은 그 다중 항 목적함수 중 스킬 "
                     f"항 하나만 측정한다. 실제 solve 파라미터: λ={sample_params['lam']}, "
                     f"μ={sample_params['mu']}, gap={sample_params['gap']}, "
                     f"time_limit={sample_params['time_limit']}s, "
                     f"pair_keep_ratio={sample_params['pair_keep_ratio']}, "
                     f"min_alloc={sample_params['min_alloc']}, "
                     f"slack_penalty={sample_params['slack_penalty']}"
                     "(max_pairs는 cap별로 다름, 위 표 참고).", ""]

    # pair_cap 1000 vs 5000 비교
    caps_present = sorted({r["pair_cap"] for r in milp_rows}) if milp_rows else []
    if len(caps_present) >= 2:
        lo_cap, hi_cap = caps_present[0], caps_present[-1]
        lines += ["### pair_cap 절삭 수준 비교", "",
                  f"| 인원 | 최적화율(cap={lo_cap}) | 최적화율(cap={hi_cap}) | solve(ms, cap={lo_cap}) "
                  f"| solve(ms, cap={hi_cap}) | 시간 배율 |", "|---:|---:|---:|---:|---:|---:|"]
        deltas, mults = [], []
        for n in sorted({r["n_people"] for r in milp_rows}):
            r_lo = next((r for r in milp_rows if r["n_people"] == n and r["pair_cap"] == lo_cap), None)
            r_hi = next((r for r in milp_rows if r["n_people"] == n and r["pair_cap"] == hi_cap), None)
            if r_lo is None or r_hi is None:
                continue
            mult = r_hi["solve_ms"] / r_lo["solve_ms"] if r_lo["solve_ms"] else float("nan")
            deltas.append(abs(r_hi["optimization_ratio"] - r_lo["optimization_ratio"]))
            mults.append(mult)
            lines.append(f"| {n} | {r_lo['optimization_ratio']:.4f} | {r_hi['optimization_ratio']:.4f} | "
                         f"{r_lo['solve_ms']:.1f} | {r_hi['solve_ms']:.1f} | {mult:.2f}× |")
        lines.append("")
        if deltas:
            lines += [f"> 최적화율 차이는 최대 {max(deltas):.4f}으로 사실상 잡음 수준이다. 반면 시간 "
                      f"배율은 {min(mults):.2f}×~{max(mults):.2f}× 범위다 — cap을 올리는 것이 "
                      "품질 이득 없이 시간만 태우는 규모가 있다.", ""]

    if d.get("alternatives"):
        lines += ["### 대안(Plan B/C/D) 품질", "",
                  "| 인원 | 대안 수 | 라벨 | Plan A 대비 품질 | Plan A와 Jaccard |",
                  "|---:|---:|---|---|---|"]
        for a in d["alternatives"]:
            q = ", ".join(f"{x:.3f}" if x is not None else "—" for x in a["quality_vs_a"][1:])
            j = ", ".join(f"{x:.3f}" for x in a["jaccard_vs_a"][1:])
            lines.append(f"| {a['n_people']} | {a['plan_count'] - 1} | "
                         f"{'/'.join(a['labels'])} | {q} | {j} |")
        lines += ["", "> 이 표의 Plan A는 gap=0.01로, 대안(B/C/D)은 gap=0.05로 solve된다(둘 다 "
                 "max_pairs=5000 고정 — `core/optimize/alternatives.py::generate_plans`) — 위쪽의 "
                 "\"pair_cap 절삭 수준 비교\" 표(gap=0.05, cap∈{1000, 5000})와는 gap·cap 조합이 "
                 "달라 직접 비교할 수 없다. Plan A 자체도 그 표의 어느 행과도 같은 조건으로 solve된 "
                 "것이 아니다.", ""]

    failures = d.get("failures") or []
    if failures:
        lines += ["### 미측정 규모 (n≥300)", "",
                  "| 인원 | 프로젝트 | 상태 | 사유 |", "|---:|---:|---|---|"]
        for f in sorted(failures, key=lambda x: x["n_people"]):
            lines.append(f"| {f['n_people']} | {f.get('n_projects', '—')} | {f['status']} | "
                         f"{f['reason']} |")
        lines += ["", "> 위 규모는 **미측정(skipped)** 이다 — 아래 표의 solve time·optimization_ratio "
                  "수치는 이 규모들을 다루지 않는다. 인용 시 \"n≥300은 외삽 근거로 제외했다\"는 "
                  "사실을 함께 밝힐 것.", ""]

    lines += ["![실험3](figures/exp3_tradeoff.png)", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 실험 4 — RAG 워크로드
# ---------------------------------------------------------------------------

def _cells(rows: list[dict]) -> dict:
    """(backend, query, n_people) -> row. 표·집계가 같은 접근자를 쓰게 한다."""
    return {(r["backend"], r["query"], r["n_people"]): r for r in rows}


def _exp4_tally(rows: list[dict]) -> tuple[int, int]:
    """규칙 1이 소비하는 수치. `decision._neo4j_faster_cells`와 같은 계산이지만
    이쪽은 부분 아티팩트에도 표를 그릴 수 있게 관대하다(판정은 decision이 한다)."""
    by = _cells(rows)
    wins = total = 0
    for q, n in sorted({(k[1], k[2]) for k in by}):
        s, g = by.get(("sqlite", q, n)), by.get(("neo4j", q, n))
        if s is None or g is None:
            continue
        total += 1
        wins += int(g["median_ms"] < s["median_ms"])
    return wins, total


def _exp4_separation(rows: list[dict]) -> tuple[int, int, str, float] | None:
    """셀별 min/max 겹침 검사. 중앙값 비교가 표본 분산 안에서 흔들릴 여지가 있는지를
    본다 — Neo4j **최솟값**이 SQLite **최댓값**보다 크면 그 셀은 어떤 추정량으로도
    뒤집히지 않는다. 가장 근접한(비율이 가장 작은) 셀도 함께 돌려준다."""
    by = _cells(rows)
    clean = total = 0
    closest = None
    for q, n in sorted({(k[1], k[2]) for k in by}):
        s, g = by.get(("sqlite", q, n)), by.get(("neo4j", q, n))
        if s is None or g is None or "min_ms" not in g or "max_ms" not in s:
            continue
        total += 1
        ratio = g["min_ms"] / s["max_ms"] if s["max_ms"] else float("inf")
        clean += int(g["min_ms"] > s["max_ms"])
        if closest is None or ratio < closest[1]:
            closest = (f"{q} @ n={n}", ratio)
    if total == 0 or closest is None:
        return None
    return clean, total, closest[0], closest[1]


def _exp4_calibration_share(rows: list[dict], value: float) -> tuple[float, float] | None:
    """캘리브레이션 값이 Neo4j 중앙값의 몇 %인지, (최소, 최대) 범위."""
    pcts = [value / r["median_ms"] * 100 for r in rows
            if r["backend"] == "neo4j" and r.get("median_ms")]
    return (min(pcts), max(pcts)) if pcts else None


def _exp4_flip_count(rows: list[dict], subtract: float) -> tuple[int, int]:
    """Neo4j 중앙값에서 subtract만큼 빼면 몇 개 셀이 뒤집히는가(민감도)."""
    by = _cells(rows)
    flips = total = 0
    for q, n in sorted({(k[1], k[2]) for k in by}):
        s, g = by.get(("sqlite", q, n)), by.get(("neo4j", q, n))
        if s is None or g is None:
            continue
        total += 1
        flips += int(g["median_ms"] - subtract < s["median_ms"])
    return flips, total


def _exp4_output_growth(rows: list[dict]) -> tuple[dict[str, tuple], dict[str, tuple]]:
    """규모에 따라 출력 행 수가 자라는 질의와 사실상 고정인 질의를 갈라 낸다.
    "규모가 커지면 격차가 좁혀진다"를 규모에 대한 추세 주장으로 옮기지 않기
    위한 근거다(A-4) — 대부분의 셀은 그래프 규모가 아니라 호출당 고정비를 잰다.
    기준은 두 가지를 **모두** 만족하는가이다: (1) 규모에 따라 출력이 단조 증가하고
    (2) 최대 규모의 출력이 최소 규모의 2배 이상. 배수만 보면 3행→6행처럼 절대량이
    작고 순서도 오르내리는 질의가 "규모에 따라 자란다"로 잘못 분류된다 — 그런
    질의는 규모 추세가 아니라 인자와 데이터 우연에 따라 흔들리는 것이다."""
    growing, flat = {}, {}
    for q in sorted({r["query"] for r in rows}):
        counts = [(r["n_people"], r["result_count"]) for r in rows
                  if r["query"] == q and r["backend"] == "sqlite" and "result_count" in r]
        if len(counts) < 2:
            continue
        counts.sort()
        series = [c for _, c in counts]
        lo, hi = series[0], series[-1]
        tracks_scale = all(a < b for a, b in zip(series, series[1:])) and hi >= lo * 2
        (growing if tracks_scale else flat)[q] = (lo, hi)
    return growing, flat


def _fmt_growth(group: dict[str, tuple]) -> str:
    return ", ".join(f"`{q}`({lo}→{hi}행)" for q, (lo, hi) in group.items()) or "없다"


def _exp4_repeat_caption(rows: list[dict]) -> str:
    bits = []
    for b in sorted({r["backend"] for r in rows}):
        reps = sorted({r["repeats"] for r in rows if r["backend"] == b and "repeats" in r})
        warm = sorted({r["warmup"] for r in rows if r["backend"] == b and "warmup" in r})
        if reps and warm:
            bits.append(f"{b} 반복 {'/'.join(map(str, reps))}회·웜업 {'/'.join(map(str, warm))}회")
    if not bits:
        return "*단위: ms, 중앙값.*"
    return f"*단위: ms, 중앙값 ({', '.join(bits)}). 적재는 양쪽 다 측정 밖.*"


def _exp4(d: dict) -> str:
    rows, parity, skipped = d["rows"], d.get("parity", []), d.get("skipped", [])
    cal = d.get("calibration") or {}
    by = _cells(rows)
    queries = sorted({r["query"] for r in rows})
    scales = sorted({r["n_people"] for r in rows})
    wins, cells = _exp4_tally(rows)

    lines = ["## 실험 4 — RAG 서브그래프 검색 워크로드 (SQLite vs Neo4j)", "",
             "**질문:** XAI 브리핑이 실제로 필요로 하는 5가지 형태의 문맥 검색에서 어느 "
             "저장 방식이 빠른가? 실험 1이 잰 것은 정해진 질의 하나였고, 여기서는 "
             "\"임의 형태의 서브그래프 검색\"이라는 Neo4j 본래 용도를 잰다.", "",
             f"**헤드라인 — 의사결정 규칙 1이 소비하는 수치: {cells}개 셀 중 Neo4j가 더 빠른 "
             f"셀 {wins}개.** (사전 고정 임계: 과반 "
             f"{decision.MAJORITY_CELLS}개 이상이면 성능 근거로 존치)", "",
             "| 질의 | 인원 | sqlite | neo4j | neo4j/sqlite | 결과수 |",
             "|---|---:|---:|---:|---:|---:|"]
    for q in queries:
        for n in scales:
            s, g = by.get(("sqlite", q, n)), by.get(("neo4j", q, n))
            if s is None and g is None:
                continue
            sm = f"{s['median_ms']:.4f}" if s else "—"
            gm = f"{g['median_ms']:.4f}" if g else "—"
            ratio = (f"{g['median_ms'] / s['median_ms']:.1f}×"
                     if s and g and s["median_ms"] else "—")
            rc = (s or g).get("result_count")
            lines.append(f"| `{q}` | {n} | {sm} | {gm} | {ratio} | "
                         f"{rc if rc is not None else '—'} |")
    lines += ["", _exp4_repeat_caption(rows), ""]

    bad = [p for p in parity if not p.get("match")]
    if bad:
        detail = "; ".join(f"{p['query']}@n={p['n_people']}"
                           f"(sqlite {p.get('sqlite_count')} vs neo4j {p.get('neo4j_count')})"
                           for p in bad)
        lines += [f"> ⚠️ **백엔드 결과 불일치 {len(bad)}건** — {detail}. 같은 질문에 서로 다른 "
                  "답을 내므로 해당 셀의 지연 비교는 무효다. 원인 규명 전까지 수치를 인용하지 "
                  "말 것.", ""]
    elif parity:
        lines += [f"> ✅ 백엔드 결과 일치 {len(parity)}건 전부 확인 — 두 구현이 같은 행 집합을 "
                  "반환한다(부동소수 표기 차만 정규화). 아래 지연 차이는 결과 크기 차이가 "
                  "아니라 백엔드 자체의 특성이다.", ""]
    if skipped:
        lines += ["> 제외된 측정: " + "; ".join(
            f"{s['backend']}@{s.get('n_people', '-')}({s['reason']})" for s in skipped), ""]

    sep = _exp4_separation(rows)
    if sep:
        clean, total, name, ratio = sep
        if clean == total:
            lines += [f"> **표본 겹침 없음: {clean}/{total}개 셀에서 Neo4j 최솟값이 SQLite "
                      f"최댓값보다 크다.** 가장 근접한 셀조차 `{name}`에서 {ratio:.1f}배 차이다 "
                      "— 중앙값 대신 어떤 추정량을 써도 승패가 뒤집히지 않는다.", ""]
        else:
            lines += [f"> 표본 겹침: {total - clean}/{total}개 셀에서 두 백엔드의 관측 구간이 "
                      "겹친다 — 해당 셀은 추정량 선택에 민감할 수 있다.", ""]

    floor = cal.get("neo4j_session_plus_trivial_query_ms")
    sess = cal.get("neo4j_session_open_ms")
    if floor is not None and sess is not None:
        lines += ["### 캘리브레이션 — 반드시 **구간**으로 읽을 것", "",
                  "Neo4j 쪽에만 붙고 SQLite 쪽에는 없는 호출당 고정비다(`neo4j_rag._run`은 "
                  "호출마다 세션을 연다. SQLite는 벤치마크가 이미 열어 둔 `conn`을 받는다). "
                  "**원 수치에서 빼지 않는다** — 사전 고정 규칙은 원값으로 판정한다.", "",
                  "| 항목 | 중앙값(ms) | p95(ms) | Neo4j 중앙값 대비 |", "|---|---:|---:|---|"]
        for label, key in (("세션 획득/반납만", "neo4j_session_open"),
                            ("세션 + `RETURN 1` (고정 바닥 전체)",
                             "neo4j_session_plus_trivial_query")):
            v = cal.get(f"{key}_ms")
            p = cal.get(f"{key}_p95_ms")
            share = _exp4_calibration_share(rows, v) if v is not None else None
            share_s = f"{share[0]:.2f}~{share[1]:.1f}%" if share else "—"
            lines.append(f"| {label} | {v:.4f} | "
                         f"{p:.4f} | {share_s} |" if v is not None and p is not None
                         else f"| {label} | — | — | {share_s} |")
        if cal.get("sqlite_noop_ms") is not None:
            lines.append(f"| (대칭) SQLite `SELECT 1` | {cal['sqlite_noop_ms']:.4f} | "
                         f"{cal.get('sqlite_noop_p95_ms', float('nan')):.4f} | — |")
        _, flat_q = _exp4_output_growth(rows)
        small = [r for r in rows if r["backend"] == "neo4j" and r["query"] in flat_q]
        small_share = _exp4_calibration_share(small, floor)
        lines.append("")
        if small_share:
            lines += [f"> 세션 획득만 보면 Neo4j 중앙값의 극히 일부지만, 커넥션 획득과 왕복까지 "
                      f"포함한 **고정 바닥 전체**는 출력이 고정에 가까운 질의 {len(flat_q)}종의 "
                      f"중앙값 중 {small_share[0]:.0f}~{small_share[1]:.0f}%를 차지한다. "
                      "**제거 가능한 "
                      "성분(풀 체크아웃 상환분)은 분리 측정되지 않았다** — 낮은 쪽 끝만 인용하면 "
                      "고정비를 과소 보고하는 것이고, 높은 쪽 끝만 인용하면 실제로 뺄 수 있는 "
                      "양을 과대 보고하는 것이다. 두 끝을 함께 읽을 것.", ""]
        f_sess, tot = _exp4_flip_count(rows, sess)
        f_floor, _ = _exp4_flip_count(rows, floor)
        lines += ["**민감도(규칙 1이 이 고정비에 얼마나 민감한가):**", "",
                  f"- 세션 획득분을 Neo4j 수치에서 빼면 뒤집히는 셀 **{f_sess}/{tot}**",
                  f"- 고정 바닥 전체를 빼면(= 실제로 뺄 수 있는 양보다 과보정) 뒤집히는 셀 "
                  f"**{f_floor}/{tot}**",
                  "", f"> 즉 규칙 1(과반 {decision.MAJORITY_CELLS}개)은 이 고정비를 어떻게 "
                  "처리하든 결론이 바뀌지 않는다 — 과보정을 해도 임계 근처에도 가지 않는다. "
                  "이 비대칭은 증명 가능하게 판정과 무관하다.", ""]

    ratios = [by[("neo4j", q, n)]["median_ms"] / by[("sqlite", q, n)]["median_ms"]
              for q in queries for n in scales
              if ("neo4j", q, n) in by and ("sqlite", q, n) in by
              and by[("sqlite", q, n)]["median_ms"]]
    ratio_span = (f"{min(ratios):.0f}~{max(ratios):.0f}배" if ratios else "위 표의")

    lines += ["### 한계 (반드시 함께 읽을 것)", ""]
    lines += [
        "1. **폐기된 첫 스윕이 있다.** 커밋된 JSON은 두 번의 스윕 중 두 번째다. 첫 실행은 "
        "다섯 질의 전부가 n=300에서 봉우리를 만든 뒤 회복했고, 재현되지 않았다 — 중간 지점의 "
        "봉우리는 워밍업 교락의 형태(n=100부터 단조 감소)가 아니다. 첫 실행의 집계도 "
        "Neo4j 0/20이었다. **다만 첫 실행의 원자료는 보존되지 않았고 복구할 수 없다** "
        "(`harness.save_result`가 고정 경로를 덮어쓴다). 그래서 \"첫 실행이 Neo4j에 더 "
        "불리했다\"처럼 확인할 수 없는 주장은 하지 않는다 — 셀 단위로는 성립하지도 않았다.",
    ]
    lines += [
        "2. **배포 토폴로지가 대칭이 아니다.** Neo4j는 컨테이너(`neo4j:5-community`, "
        "`bolt://localhost:7687`, 힙·페이지캐시 튜닝 없음)에서 돌고, Apple Silicon에서는 "
        "host↔Linux VM 경계까지 통과한다. SQLite는 in-process 네이티브 라이브러리다. 위 "
        f"{ratio_span} 격차를 이것만으로 뒤집을 수는 없지만, 이를 \"네트워크 왕복\" 한마디로 "
        "적으면 축소 서술이다.",
    ]
    growing, flat = _exp4_output_growth(rows)
    if growing or flat:
        flat_cells = len(flat) * len(scales)
        lines += [
            f"3. **n={scales[-1]}을 넘어 외삽하지 말 것.** 출력이 규모에 따라 실제로 커지는 "
            f"질의는 {_fmt_growth(growing)} 뿐이고, 나머지({_fmt_growth(flat)})는 사실상 고정 "
            f"출력이다. 즉 {cells}개 셀 중 {flat_cells}개는 그래프 규모가 아니라 **호출당 "
            "고정비 + 앵커된 인덱스 조회**를 재고 있다. 일부 질의에서 관측되는 격차 축소를 "
            "규모에 대한 추세 주장으로 옮기지 말 것.",
        ]
    reps = sorted({r["repeats"] for r in rows if "repeats" in r})
    if reps:
        lines += [
            f"4. **p95는 약한 추정량이다.** 반복 {'/'.join(map(str, reps))}회에서 p95는 정렬된 "
            f"표본 중 위에서 두 번째 값에 해당한다. 양쪽에 동일하게 적용되고 이 정도 격차는 "
            "어떤 추정량에서도 유지되지만(위 표본 겹침 검사), 백분위수의 통상적 의미로 "
            "읽지 말 것.",
        ]
    lines += ["", "![실험4](figures/exp4_rag.png)", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 실험 5 — 영속성
# ---------------------------------------------------------------------------

def _mvals(rows: list[dict]) -> dict:
    return {(r["backend"], r["metric"], r["n_people"]): r["value"] for r in rows}


def _mrows(rows: list[dict]) -> dict:
    return {(r["backend"], r["metric"], r["n_people"]): r for r in rows}


def _fmt_value(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.3f}"


def _cal(d: dict, n: int, key: str) -> float | None:
    for cell in (d.get("calibration") or {}).get("per_scale", []):
        if cell["n_people"] == n:
            return cell.get(key)
    return None


def _exp5_metric_table(d: dict, metrics: list[str]) -> list[str]:
    vals = _mvals(d["rows"])
    scales = sorted({r["n_people"] for r in d["rows"]})
    out = ["| 지표 | 인원 | sqlite | neo4j | neo4j/sqlite |", "|---|---:|---:|---:|---:|"]
    for m in metrics:
        for n in scales:
            s, g = vals.get(("sqlite", m, n)), vals.get(("neo4j", m, n))
            if s is None and g is None:
                continue
            ratio = f"{g / s:,.1f}×" if s and g else "—"
            out.append(f"| `{m}` | {n} | {_fmt_value(s)} | {_fmt_value(g)} | {ratio} |")
    return out


def _exp5_tally_lines(label: str, exp5: dict) -> list[str]:
    """규칙 2 집계를 decision.evaluate가 실제로 쓴 계산으로 다시 낸다 — 리포트가
    판정과 다른 숫자를 말하는 일이 없도록 같은 함수를 통과시킨다."""
    tally = decision._rule2_tally(exp5)
    out = [f"**{label}** (disk 판독: `{tally['disk_reading']}`)", "",
           "| 지표 | 센 항목 | 셀 | Neo4j 우위 셀 | 지표 판정 |",
           "|---|---|---:|---:|---|"]
    for m in tally["per_metric"]:
        out.append(f"| `{m['metric']}` | {', '.join(f'`{c}`' for c in m['counted_metrics'])} | "
                   f"{m['cells']} | {m['neo4j_wins']} | "
                   f"{'우위' if m['neo4j_ahead'] else '열세'} |")
    out += ["", f"→ Neo4j 우위 지표 **{tally['neo4j_metric_wins']} / {tally['metrics']}** "
            f"(사전 고정 임계 {decision.PERSISTENCE_THRESHOLD} 이상이면 존치)", ""]
    return out


def _exp5_disk_lines(d: dict) -> list[str]:
    """어느 판독으로 규칙 2를 판정했는지 명시하고, Neo4j에 가장 유리한 대안 판독도
    함께 제시한다(A-11)."""
    detail = {(x["backend"], x["n_people"], x["reading"]): x
              for x in d.get("disk_detail", [])}
    scales = sorted({x["n_people"] for x in d.get("disk_detail", [])})
    clean = [(n, detail[("neo4j", n, "clean")], detail[("sqlite", n, "clean")])
             for n in scales
             if ("neo4j", n, "clean") in detail and ("sqlite", n, "clean") in detail]
    if not clean:
        return []
    out = ["**`disk_bytes`는 어느 판독으로 판정했는가 — 명시한다.**", "",
           "- **판정 기준: `disk_bytes_clean`** — 볼륨 삭제 → 재기동 → 해당 규모 하나만 적재 "
           "→ 체크포인트를 흘려보내기 위해 컨테이너 정지 후 측정.",
           "- 누적 판독(`disk_bytes`)은 이전 규모의 잔여를 포함해 단조 증가하므로 규모별 "
           "비교에 쓸 수 없다. **그렇다고 지표를 \"비교 불가\"로 빼지는 않았다** — 뺐다면 "
           f"규칙 2가 몰래 \"3 of 3\"이 되어 사전 고정 규칙을 사후 변경하는 셈이 된다.", ""]

    fixed = {n: g["txlog_bytes"] + g["system_txlog_bytes"] + g["system_store_bytes"]
             for n, g, _ in clean}
    if len(set(fixed.values())) == 1:
        const = next(iter(fixed.values()))
        out += [f"- 클린 판독 총량 중 **{const:,} B는 네 규모에서 값이 완전히 같다** "
                "(데이터베이스당 트랜잭션 로그 선할당 + system 데이터베이스). 즉 이 부분은 "
                "데이터 크기와 무관한 **고정 선불**이고, 그래서 가장 작은 규모의 Neo4j조차 "
                "가장 큰 규모의 SQLite를 압도한다.", ""]

    out += ["**대안 판독 — Neo4j에 가장 유리한 읽기(스토어 파일만, 트랜잭션 로그·system 제외):**",
            "", "| 인원 | sqlite store(B) | neo4j store(B) | 배율 |", "|---:|---:|---:|---:|"]
    ratios = []
    for n, g, s in clean:
        r = g["store_bytes"] / s["store_bytes"] if s["store_bytes"] else float("nan")
        ratios.append(r)
        out.append(f"| {n} | {s['store_bytes']:,} | {g['store_bytes']:,} | {r:.1f}× |")
    out += ["", f"> 트랜잭션 로그와 system 데이터베이스를 전부 빼 준 이 판독에서도 Neo4j는 "
            f"{min(ratios):.1f}~{max(ratios):.1f}배 크다 — 규칙 2의 disk 지표는 어느 판독으로 "
            "읽어도 뒤집히지 않는다.", ""]
    return out


def _calibration_contradictions(d: dict) -> list[str]:
    """캘리브레이션이 물리적으로 불가능한 순서를 낸 셀을 찾는다.

    `noop_match`(MATCH 1회)는 `two_endpoint_match`(2회)보다 클 수 없고, MATCH만
    하는 질의가 MATCH+MERGE를 하는 `append_*`보다 클 수도 없다. 이런 셀이 있으면
    캘리브레이션 자체의 드리프트가 잔차보다 크다는 뜻이고, 그 위에 세운 차감
    논증은 어느 방향으로도 성립하지 않는다."""
    vals = _mvals(d["rows"])
    out = []
    for n in sorted({r["n_people"] for r in d["rows"]}):
        te = _cal(d, n, "neo4j_two_endpoint_match_ms")
        nm = _cal(d, n, "neo4j_noop_match_ms")
        if nm is not None and te is not None and nm > te:
            out.append(f"n={n}: `noop_match` {nm:.3f} > `two_endpoint_match` {te:.3f} "
                       "(MATCH 1회가 2회보다 클 수 없다)")
        for metric in ("append_cowork_ms", "append_review_ms"):
            g = vals.get(("neo4j", metric, n))
            if g is None:
                continue
            if nm is not None and nm > g:
                out.append(f"n={n}: `noop_match` {nm:.3f} > `{metric}` {g:.3f} "
                           "(MATCH 1회가 MATCH+MERGE보다 클 수 없다)")
            if te is not None and g < te:
                out.append(f"n={n}: `{metric}` {g:.3f} < `two_endpoint_match` {te:.3f} "
                           "(MATCH+MERGE가 MATCH보다 작을 수 없다 — 잔차가 음수)")
    return out


def _exp5_calibration_lines(d: dict, first: dict) -> list[str]:
    """캘리브레이션 공개(A-12). **원 수치에서 빼지 않는다** — 사전 고정 규칙은 원값으로
    판정한다. 뺄셈이 무엇을 할 수 있고 무엇을 할 수 없는지를 데이터로 보인다."""
    vals = _mvals(d["rows"])
    scales = sorted({r["n_people"] for r in d["rows"]})
    if not any(_cal(d, n, "neo4j_detach_delete_ms") is not None for n in scales):
        return []

    out = ["### 캘리브레이션 — 공개용이며 헤드라인에서 차감하지 않는다", "",
           "| 인원 | DETACH DELETE(ms) | = neo4j `load_ms`의 | SQLite `unlink`(ms) | "
           "= sqlite `load_ms`의 | `_assemble`(ms) | = sqlite/neo4j `rehydrate_ms`의 | "
           "두 엔드포인트 MATCH(ms) | = neo4j `append_cowork_ms`의 |",
           "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n in scales:
        dd = _cal(d, n, "neo4j_detach_delete_ms")
        ul = _cal(d, n, "sqlite_unlink_ms")
        asm = _cal(d, n, "assemble_ms")
        te = _cal(d, n, "neo4j_two_endpoint_match_ms")
        nl, sl = vals.get(("neo4j", "load_ms", n)), vals.get(("sqlite", "load_ms", n))
        nr, sr = vals.get(("neo4j", "rehydrate_ms", n)), vals.get(("sqlite", "rehydrate_ms", n))
        ac = vals.get(("neo4j", "append_cowork_ms", n))
        out.append(
            f"| {n} | {dd:.3f} | {dd / nl * 100:.1f}% | {ul:.3f} | {ul / sl * 100:.2f}% | "
            f"{asm:.3f} | {asm / sr * 100:.1f}% / {asm / nr * 100:.1f}% | "
            f"{te:.3f} | {te / ac * 100:.0f}% |")
    out += ["", "관측되는 사실 네 가지:", ""]

    # 1. 철거 비대칭
    out += ["1. **철거 비대칭은 실재하지만 격차를 설명하지 못한다.** `load_neo4j`의 타이밍 "
            "구간에는 `MATCH (n) DETACH DELETE n`(O(n))이 들어 있고 `build_sqlite`에는 "
            "`path.unlink()`(O(1))만 들어 있다. 양쪽에서 각각 빼면:"]
    disc = []
    out += ["", "| 인원 | 철거분 차감 후 sqlite(ms) | 차감 후 neo4j(ms) | 배율 |",
            "|---:|---:|---:|---:|"]
    for n in scales:
        nl, sl = vals[("neo4j", "load_ms", n)], vals[("sqlite", "load_ms", n)]
        a = nl - _cal(d, n, "neo4j_detach_delete_ms")
        b = sl - _cal(d, n, "sqlite_unlink_ms")
        disc.append(a / b)
        out.append(f"| {n} | {b:.3f} | {a:.3f} | {a / b:.1f}× |")
    out += ["", f"   → 차감 후에도 {min(disc):.1f}~{max(disc):.1f}배 열세다.", ""]

    # 2. _assemble — 빼면 Neo4j가 더 나빠진다
    ratios_raw, ratios_disc = [], []
    for n in scales:
        nr, sr = vals[("neo4j", "rehydrate_ms", n)], vals[("sqlite", "rehydrate_ms", n)]
        a = _cal(d, n, "assemble_ms")
        ratios_raw.append(nr / sr)
        ratios_disc.append((nr - a) / (sr - a))
    out += [f"2. **공유 파이썬 비용(`_assemble`)을 빼면 Neo4j가 *더* 나빠진다.** 두 백엔드가 "
            f"똑같이 내는 `MemoryGraph.build` 비용은 SQLite `rehydrate_ms`에서 더 큰 몫을 "
            f"차지한다. 빼면 배율이 {min(ratios_raw):.1f}~{max(ratios_raw):.1f}× → "
            f"{min(ratios_disc):.1f}~{max(ratios_disc):.1f}×로 **올라간다** — 이 항목의 할인은 "
            "Neo4j에 유리하지 않다.", ""]

    # 3. append_* 반사실
    cw = rv = cells = 0
    resid = []
    for n in scales:
        te = _cal(d, n, "neo4j_two_endpoint_match_ms")
        for metric, counter in (("append_cowork_ms", "cw"), ("append_review_ms", "rv")):
            g, s = vals[("neo4j", metric, n)], vals[("sqlite", metric, n)]
            cells += 1
            resid.append(g - te)
            if g - te < s:
                if counter == "cw":
                    cw += 1
                else:
                    rv += 1
    out += [f"3. **`append_*`의 엔드포인트 `MATCH`는 차감하지 않는다 — 그러나 반사실은 "
            f"명시한다.** Neo4j의 `append_*`는 두 `Person`을 id로 `MATCH`한 뒤 관계를 "
            "`MERGE`하고, SQLite의 `collaboration`에는 외래키가 없어 INSERT가 읽기를 0회 "
            "한다. 이것은 \"엔진 외 오버헤드\"가 아니라 **엔진·스키마의 진짜 비용**이므로 "
            f"빼지 않는다. 그래도 빼 보면 {cells}개 셀 중 **{cw + rv}개**에서 Neo4j 잔차가 "
            f"SQLite보다 작아져 `append_*` 지표가 통째로 뒤집힌다(cowork {cw}, review {rv}). "
            f"그러면 규칙 2는 **1 of {decision.PERSISTENCE_METRICS}**가 되지만 임계 "
            f"{decision.PERSISTENCE_THRESHOLD}에는 **여전히 미달이라 판정은 유지된다**.", ""]

    # 4. 캘리브레이션 자체의 모순 — 두 스윕 모두에서 찾는다
    bad = _calibration_contradictions(d)
    bad_first = _calibration_contradictions(first)
    lo_r, hi_r = min(resid), max(resid)
    out += ["4. **그 반사실 자체가 못 미덥다.** 캘리브레이션 질의 "
            "`MATCH (a),(b) RETURN a, b`는 노드 레코드 2개를 실제로 반환하는데 `append_*` "
            "안의 MATCH는 아무것도 반환하지 않는다 — 빼는 값과 위 표의 몫(%)이 **과대**다. "
            "게다가 캘리브레이션이 물리적으로 불가능한 순서를 내는 셀이 두 스윕 모두에 있다:"]
    out += [""]
    out += ([f"   - (판정 스윕) {b}" for b in bad] or
            ["   - (판정 스윕에서는 모순 셀이 관측되지 않았다)"])
    out += ([f"   - (첫 스윕) {b}" for b in bad_first] or
            ["   - (첫 스윕에서는 모순 셀이 관측되지 않았다)"])
    out += [""]
    out += [f"   잔차 자체가 {lo_r:+.3f}~{hi_r:+.3f} ms로 캘리브레이션의 드리프트와 같은 "
            "자릿수다. 즉 **`append_*`에 대해서는 차감에 기반한 어떤 주장도 어느 방향으로든 "
            "판정 불가**다. 판정은 원값으로 한다.", ""]

    # 성립하는 지표로 좁힌 "할인 후에도 남는 열세"
    store_ratios = []
    detail = {(x["backend"], x["n_people"], x["reading"]): x for x in d.get("disk_detail", [])}
    for n in scales:
        g, s = detail.get(("neo4j", n, "clean")), detail.get(("sqlite", n, "clean"))
        if g and s and s["store_bytes"]:
            store_ratios.append(g["store_bytes"] / s["store_bytes"])
    holds = [("`load_ms`(철거분 차감)", min(disc), max(disc)),
             ("`rehydrate_ms`(원값 — 차감은 Neo4j에 불리)", min(ratios_raw), max(ratios_raw))]
    if store_ratios:
        holds.append(("`disk_bytes`(스토어 파일만)", min(store_ratios), max(store_ratios)))
    lo = min(x[1] for x in holds)
    hi = max(x[2] for x in holds)
    out += ["> **\"모든 비대칭을 할인해도 …배 열세가 남는다\"는 네 지표 전체에 대한 진술로 "
            "쓸 수 없다.** `append_*`는 위 4번 때문에 차감 기반 진술이 성립하지 않는다. "
            f"성립하는 세 지표로만 좁혀 재도출하면 **{lo:.1f}~{hi:.1f}배**다 — "
            + " · ".join(f"{name} {a:.1f}~{b:.1f}×" for name, a, b in holds) + ".", ""]
    return out


def _exp5_warmup_lines(primed: dict, first: dict) -> list[str]:
    """워밍업 정당화(A-13)와 첫 스윕에 남아 있던 light path 램프(A-13b)."""
    reps = sorted({(r.get("repeats"), r.get("warmup")) for r in primed["rows"]
                   if r.get("unit") == "ms" and "repeats" in r})
    out = ["### 워밍업 — 무엇을 했고 왜 했는가", ""]
    if reps:
        out += ["- 이 리포트가 쓰는 (반복, 웜업) 조합: "
                + ", ".join(f"({a}, {b})" for a, b in reps)
                + ". 두 백엔드에 **같은 값**을 쓴다.",
                "- 추가로 스케일 루프 **밖에서** 전역 예열을 한 번 돌린다 — 웜업 상태가 규모 "
                "순서와 교락되면 \"규모가 커질수록 빨라지는\" 가짜 추세가 만들어진다"
                "(Plan 2에서 웜업 부족이 Neo4j를 최대 8배 부풀린 전례가 있다)."]
    spreads = [(r["metric"], r["n_people"], r["max_ms"] / r["min_ms"])
               for r in primed["rows"]
               if r["backend"] == "neo4j" and r.get("min_ms")
               and r["metric"] in ("load_ms", "rehydrate_ms")]
    if spreads:
        worst = max(spreads, key=lambda x: x[2])
        out += [f"- 예열이 실제로 됐는지는 타이밍 표본의 스프레드로 확인한다: Neo4j의 무거운 "
                f"지표(`load_ms`·`rehydrate_ms`)에서 max/min은 "
                f"{min(s[2] for s in spreads):.2f}~{max(s[2] for s in spreads):.2f} "
                f"범위다(최대는 `{worst[0]}` n={worst[1]}). 타이밍 구간 안에서 계속 빨라지고 "
                "있었다면 스프레드가 이보다 훨씬 컸을 것이다.", ""]
    else:
        out += [""]

    # light path 램프: 첫 스윕에서 neo4j append_* 계열이 규모에 따라 단조 감소했는가
    def _series(d, backend, metric):
        s = sorted((r["n_people"], r["value"]) for r in d["rows"]
                   if r["backend"] == backend and r["metric"] == metric)
        return [v for _, v in s]

    def _monotone_down(v):
        return len(v) > 2 and all(a > b for a, b in zip(v, v[1:]))

    ramp_rows = []
    for metric in ("append_cowork_ms", "append_review_ms"):
        for backend in ("neo4j", "sqlite"):
            fv, pv = _series(first, backend, metric), _series(primed, backend, metric)
            if not fv or not pv:
                continue
            ramp_rows.append((backend, metric, fv, pv))
    if ramp_rows:
        out += ["**첫 스윕에는 light path 워밍업 램프가 남아 있었다.** 첫 스윕의 `_prime_neo4j`는 "
                "무거운 경로(적재·재수화)만 예열했고 단문 왕복 경로는 예열하지 않았다. 그 결과 "
                "Neo4j의 `append_*` 계열이 규모에 따라 단조 감소하는데(SQLite는 평탄) — 이는 "
                "이 플랜이 사전에 지목한 워밍업 교락의 signature다. 재측정 스윕은 세션 획득·"
                "두 엔드포인트 MATCH·실제 `append_*`까지 예열한다.", "",
                "| 백엔드 | 지표 | 첫 스윕(규모 오름차순) | 단조 감소? | 재측정 | 단조 감소? |",
                "|---|---|---|---|---|---|"]
        for backend, metric, fv, pv in ramp_rows:
            out.append(f"| {backend} | `{metric}` | "
                       + " / ".join(f"{v:.3f}" for v in fv) + " | "
                       + ("**예**" if _monotone_down(fv) else "아니오") + " | "
                       + " / ".join(f"{v:.3f}" for v in pv) + " | "
                       + ("**예**" if _monotone_down(pv) else "아니오") + " |")
        head = []
        for metric in ("append_cowork_ms", "append_review_ms"):
            fv_n, fv_s = _series(first, "neo4j", metric), _series(first, "sqlite", metric)
            pv_n, pv_s = _series(primed, "neo4j", metric), _series(primed, "sqlite", metric)
            if fv_n and fv_s and pv_n and pv_s:
                head.append(f"`{metric}` {fv_n[0] / fv_s[0]:.1f}× → {pv_n[0] / pv_s[0]:.1f}×")
        if head:
            out += ["", "> 결과적으로 첫 스윕의 **최소 규모 헤드라인 배율은 부풀려져 있었다** "
                    f"({", ".join(head)}). **정정 방향은 "
                    "Neo4j에 불리하다**(Neo4j 수치를 부풀리는 것은 SQLite를 좋아 보이게 한다 — "
                    "첫 리포트가 이 방향을 반대로 서술했다). 그래도 두 스윕 모두 SQLite가 전 셀 "
                    "우세라 판정은 바뀌지 않는다.", ""]
    return out


def _exp5(primed: dict, first: dict) -> str:
    tally = decision._rule2_tally(primed)
    rows = primed["rows"]
    vals = _mvals(rows)
    scales = sorted({r["n_people"] for r in rows})
    reps = sorted({r["repeats"] for r in rows if r.get("unit") == "ms" and "repeats" in r})

    lines = ["## 실험 5 — 영속성 (SQLite vs Neo4j)", "",
             "**질문:** 인메모리 계층에는 영속성이 없다 — 재시작하면 저장소에서 전량 다시 "
             "읽어야 한다. 그렇다면 저장소의 가치는 탐색 속도가 아니라 (1) 적재 (2) 공간 "
             "(3) 증분 갱신 (4) 재수화에서 나온다. 실험 1·4가 재지 않은 축이다.", "",
             f"**헤드라인 — 의사결정 규칙 2가 소비하는 수치: {tally['metrics']}지표 중 Neo4j "
             f"우위 {tally['neo4j_metric_wins']}개.** (사전 고정 임계: "
             f"{decision.PERSISTENCE_THRESHOLD}개 이상이면 영속성 근거로 존치)", "",
             "> **판정 대상 스윕:** `experiments/results/exp5_persistence_primed.json` "
             "(light path까지 예열한 재측정). 첫 스윕도 "
             "`experiments/results/exp5_persistence.json`으로 **함께 커밋돼 있다** — Task 4에서 "
             "재실행이 첫 스윕을 통째로 덮어써 원자료를 잃은 전례가 있어 이번에는 두 실행을 "
             "모두 보존했다.", ""]

    metrics = ["load_ms", tally["disk_reading"], "append_cowork_ms",
               "append_review_ms", "rehydrate_ms"]
    lines += _exp5_metric_table(primed, metrics)
    if reps:
        lines += ["", f"*단위: ms 또는 bytes. 적재·재수화는 반복 {min(reps)}회, 증분 갱신은 "
                  f"반복 {max(reps)}회(둘 다 양쪽 백엔드 동일).*", ""]
    else:
        lines += [""]

    lines += _exp5_tally_lines("규칙 2 집계 — 지표별 규모 셀의 과반으로 판정", primed)
    lines += ["> 집계 방식(지표당 규모 셀의 과반)은 사전 고정 규칙에 명시돼 있지 않아 실험 "
              "5에서 정하고 여기에 밝힌다. `experiments/decision.py`와 실험 5 러너가 이 집계를 "
              "**독립적으로 두 번** 계산하며, `tests/test_decision.py::"
              "test_rule2_reproduces_the_tally_persisted_by_task6`이 커밋된 JSON에 저장된 "
              "집계와 지표별로 일치함을 고정한다.", ""]

    lines += ["**두 스윕의 집계 — 둘 다 같은 답을 낸다.**", ""]
    lines += _exp5_tally_lines("첫 스윕 (`exp5_persistence.json`)", first)
    lines += ["> ⚠️ **두 스윕의 절대값을 서로 빼서 비교하지 말 것** — Neo4j `append_*`의 실행 간 "
              "분산이 크다. 판정은 **한 스윕 내부**에서 한다. 두 실행이 지표별로 같은 결론에 "
              "도달했다는 사실만 인용할 것.", ""]

    lines += _exp5_disk_lines(primed)
    lines += _exp5_calibration_lines(primed, first)
    lines += _exp5_warmup_lines(primed, first)

    lines += ["### 남아 있는 비대칭 — 고칠 수 없어 공개만 한다 (전부 SQLite에 유리한 방향)", "",
              "1. **INSERT vs MERGE.** SQLite `review`에는 PRIMARY KEY가 없어 INSERT가 무조건 "
              "덧붙이는 반면, Neo4j `MERGE`는 찾아서 갱신한다. 같은 의미의 연산이 아니다.",
              "2. **엔드포인트 해소.** Neo4j `append_*`는 `Person` 두 개를 id로 `MATCH`(인덱스 "
              "시크 2회)한 뒤에야 관계를 `MERGE`할 수 있다. SQLite `collaboration`에는 외래키가 "
              "없어 INSERT가 **읽기를 0회** 한다. 바꿔 말하면 Neo4j만 참조 무결성을 지키고 "
              "있다.",
              "3. **타임드 구간 안의 추가 페이로드.** Neo4j `append_review`는 `evidence` 텍스트를 "
              "쓰지만 SQLite `review` 테이블에는 evidence 컬럼이 아예 없다. `disk_bytes`뿐 "
              "아니라 `append_review_ms`도 Neo4j 쪽으로 부풀린다. (실측 페이로드 길이는 "
              "results/*.json 범위 밖이라 수치는 싣지 않는다 — Task 5·6 작업 기록 참조.)",
              "4. **호출당 세션.** `with driver.session()`이 Neo4j 쪽에서만 타임드 구간 안에 "
              "있다. SQLite `conn`은 호출자가 구간 밖에서 연다.", ""]

    lines += ["### 이 실험이 무엇을 비교하고 있는지 — 축소해서 규정한다", "",
              "- **`rehydrate_ms`는 \"엔진 대 엔진\"이 아니라 \"이 스키마 + 이 엔진\"이다.** "
              "SQLite의 정규화 스키마는 skills와 review_item을 파이썬에서 dict로 조립하게 "
              "만들고, Neo4j는 서버가 `collect()`로 미리 묶어 준다. 그 서버 작업은 왕복 안에 "
              "있으므로 계산에 **포함**된다 — 공정하지만, 스키마 설계가 결과의 일부라는 점을 "
              "함께 읽을 것.",
              "- **여기서 \"영속성\"은 커밋되어 새 클라이언트에서 보인다는 뜻이다.** 양쪽 다 "
              "**크래시 내구성은 시험하지 않았다.** 대칭이므로 비교는 성립하지만, 이 결과를 "
              "크래시 내구성으로 보고하지 말 것.", ""]

    lines += ["### 방법론 한계", ""]
    if reps:
        lines += [f"- **적재·재수화는 축소 반복({min(reps)}회)으로 측정했다** — 다른 실험의 "
                  f"20/3과 다르며, 회당 비용이 커 전체 실행 시간을 감당할 수 없기 때문이다. "
                  f"대신 웜업을 크게 줬다.",
                  f"- **반복 {min(reps)}회에서 `p95_ms`는 백분위수가 아니라 사실상 최댓값이다.** "
                  "백분위수로 제시하지 말 것."]
    lines += ["- **재수화는 `availability`·`projects`·`evidence`를 복원하지 않는다**(상수 또는 "
              "생략). 이 지표의 목적은 재수화 **비용** 비교이지 완전한 상태 복원이 아니다.",
              "- **`disk_bytes`의 누적 판독은 이전 규모의 잔여를 포함한다.** 어느 판독으로 "
              "규칙 2를 판정했는지는 위에 명시했다."]

    noisy = []
    for n in scales:
        for backend in ("sqlite", "neo4j"):
            r = _mrows(rows).get((backend, "rehydrate_ms", n))
            if r and r.get("min_ms") and r["min_ms"] < r["value"] * 0.75:
                other = "neo4j" if backend == "sqlite" else "sqlite"
                noisy.append((backend, other, n, r["min_ms"], r["value"]))
    for backend, other, n, mn, med in noisy:
        lines.append(f"- **{backend} n={n} `rehydrate_ms`는 축소 반복에서 노이즈가 크다"
                     f"(min {mn:.3f} / median {med:.3f}).** 이 방향은 **{other}에 유리**하며, "
                     f"그럼에도 {other}가 진다.")
    lines += [
        "- **재현성 한계 — 무엇이 삭제됐는지의 기록은 검증 불가능하다.** 컨테이너 볼륨이 "
        "바인드 마운트(`./.neo4j/data:/data`)라 `docker compose down -v`로 지워지지 않아, "
        "클린 슬레이트 측정을 위해 구현자가 그 디렉터리를 직접 비웠다(`.neo4j/`는 "
        "`.gitignore` 대상이고 모든 실험이 실행 시점에 적재하므로 삭제 자체는 범위 내이며 "
        "정당했다). 다만 삭제 직전 상태의 기록은 사후 검증할 수 없으므로, 이 리포트는 그 "
        "수치를 다시 쓰지 않는다.",
        "- **`clean_disk` 경로에는 단위 테스트가 없다** — 도커 컨테이너를 내렸다 올리므로 "
        "독립 스모크로만 확인했다.", ""]

    lines += ["![실험5](figures/exp5_persistence.png)", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 의사결정
# ---------------------------------------------------------------------------

_RULE_TEXT = {
    1: "RAG 20개 셀 중 Neo4j 중앙값이 더 빠른 셀이 **과반(11개 이상)** 이면 → 존치(성능 근거)",
    2: "아니면, 영속성 **4지표 중 3개 이상**에서 Neo4j가 우위이면 → 존치(영속성 근거)",
    3: "아니면, SQL이 재귀 CTE나 3중 이상 서브쿼리를 요구하는 질의가 **5종 중 3종 이상**이면 "
       "→ 존치하되 \"표현력 근거이며 성능·영속성은 열세\"를 리포트에 명시",
    4: "위 셋 다 아니면 → **제거 권고**. SQLite로 영속·탐색·RAG를 모두 처리하고, "
       "Neo4j·docker-compose·드라이버 의존을 제거하는 **계획을 함께 제시한다**",
}


def _decision(exp4: dict, exp5: dict) -> str:
    d = decision.evaluate(exp4, exp5)
    ev = d["evidence"]
    lines = [
        "## 저장 계층 의사결정 — 사전 고정된 규칙의 적용", "",
        "> 이 절의 판정은 사람이 결과를 보고 내린 것이 아니다. 아래 규칙은 **측정을 시작하기 "
        "전에** 플랜 문서에 고정됐고(`.omc/plan/2026-08-12-teamweaver-plan-3-storage-decision.md` "
        "§\"의사결정 규칙\"), `experiments/decision.py`가 그 규칙을 코드로 옮겨 커밋된 결과 "
        "JSON을 먹고 판정을 낸다. 측정 후에 기준을 만들면 어떤 숫자든 정당화할 수 있다 — "
        "그래서 순서를 뒤집어 두었다.", "",
        "**사전 고정 규칙(원문 요지):**", "",
    ]
    lines += [f"{k}. {v}" for k, v in sorted(_RULE_TEXT.items())]
    lines += ["", "**규칙별 근거 수치(전부 커밋된 `experiments/results/*.json`에서 계산):**", "",
              "| 규칙 | 근거 | 임계 | 실측 | 판정 |", "|---:|---|---|---|---|"]
    lines.append(f"| 1 | RAG 워크로드 셀 중 Neo4j 우위 | {ev['thresholds']['majority_cells']} / "
                 f"{ev['total_cells']} 이상 | **{ev['neo4j_faster_cells']} / "
                 f"{ev['total_cells']}** | "
                 f"{'충족' if ev['neo4j_faster_cells'] >= ev['thresholds']['majority_cells'] else '미달'} |")
    lines.append(f"| 2 | 영속성 지표 중 Neo4j 우위 (disk 판독 `{ev['disk_reading']}`) | "
                 f"{ev['thresholds']['persistence']} / {ev['total_persistence_metrics']} 이상 | "
                 f"**{ev['neo4j_better_persistence_metrics']} / "
                 f"{ev['total_persistence_metrics']}** | "
                 f"{'충족' if ev['neo4j_better_persistence_metrics'] >= ev['thresholds']['persistence'] else '미달'} |")
    lines.append(f"| 3 | SQL 표현력 부담이 큰 질의 | {ev['thresholds']['complexity']} / "
                 f"{ev['total_queries']} 이상 | **{ev['complex_sql_queries']} / "
                 f"{ev['total_queries']}** (enum) · **{ev['complex_sql_queries_literal']} / "
                 f"{ev['total_queries']}** (문언 그대로) | "
                 f"{'충족' if ev['complex_sql_queries_adjudicated'] >= ev['thresholds']['complexity'] else '미달'} |")
    lines += ["", "**규칙 3은 두 해석을 모두 계산한다 — 둘 다 임계 미달이므로 판정은 "
              "영향받지 않지만, 하나만 인용하면 유리한 해석을 고른 셈이 된다.**", "",
              "| 질의 | `sql_complexity` 라벨 | 실제 SQL의 서브쿼리 수 | 재귀 CTE | enum 해석 | 문언 해석 |",
              "|---|---|---:|---|---|---|"]
    for q, st in ev["sql_structure"].items():
        lines.append(f"| `{q}` | `{st['label']}` | {st['subqueries']} | "
                     f"{'예' if st['recursive_cte'] else '아니오'} | "
                     f"{'포함' if q in ev['complex_sql_queries_queries'] else '—'} | "
                     f"{'포함' if q in ev['complex_sql_queries_literal_queries'] else '—'} |")
    lines += ["", f"> enum 해석은 `sql_complexity`가 `recursive_cte` 또는 `multi_subquery`인 "
              f"질의를 센다({ev['complex_sql_queries']}종). 규칙 3의 문언은 \"재귀 CTE나 **3중 "
              f"이상** 서브쿼리\"인데, `team_cohesion`의 상관 서브쿼리는 정확히 1개이므로 문언 "
              f"해석에서는 빠진다({ev['complex_sql_queries_literal']}종). 서브쿼리 수는 라벨을 "
              "다시 읽은 것이 아니라 커밋된 `core/rag/sqlite_rag.py`의 실제 SQL에서 세었다. "
              f"판정에는 Neo4j에 **유리한** 쪽({ev['complex_sql_queries_adjudicated']})을 썼다.", "",
              "> 이 라벨들은 원래 3/5로 잘못 적혀 있었고, 존재하지 않는 표현력 근거로 Neo4j를 "
              "살릴 뻔했다. Task 3에서 실제 SQL·Cypher를 대조해 바로잡았으며 수정 방향은 "
              "Neo4j에 **불리**했다.", "",
              f"### 판정: 규칙 {d['rule']} 적용 → **`{d['verdict']}`**", "",
              d["rationale"], ""]

    plan = d.get("removal_plan")
    if plan:
        lines += [f"> 사전 고정 규칙 4는 판정만으로 끝나지 않는다 — {plan['trigger']}. "
                  "아래가 그 계획이다.", "",
                  "#### 제거 대상", "", "| 대상 | 비고 |", "|---|---|"]
        lines += [f"| `{x['path']}` | {x['note']} |" for x in plan["remove"]]
        lines += ["", "#### 유지·대체 (이미 존재하므로 새로 만들 것이 없다)", "",
                  "| 대상 | 비고 |", "|---|---|"]
        lines += [f"| `{x['path']}` | {x['note']} |" for x in plan["keep"]]
        lines += ["", "#### 무엇을 잃는가 — 이득만 적지 않는다", ""]
        lines += [f"- {x}" for x in plan["lost"]]
        lines += ["", f"> {plan['scope']}", ""]
    return "\n".join(lines)


def build() -> str:
    e1 = harness.load_result("exp1_storage")
    e2 = harness.load_result("exp2_pipeline")
    e3 = harness.load_result("exp3_algorithm")
    # 실험 4·5는 Plan 3에서 추가됐다. 실험 5는 스윕이 두 번 돌았고 **둘 다 커밋돼
    # 있다** — 판정은 light path까지 예열한 재측정(_primed)으로 하고, 첫 스윕은
    # 두 실행이 같은 결론에 도달했음을 보이는 데 쓴다(절대값 비교는 하지 않는다).
    e4 = decision.payload(harness.load_result("exp4_rag"))
    e5 = decision.payload(harness.load_result("exp5_persistence_primed"))
    e5_first = decision.payload(harness.load_result("exp5_persistence"))
    env = e1.get("environment", {})

    d1 = e1["data"]
    warmups = sorted({(r["backend"], r.get("repeats"), r.get("warmup")) for r in d1["rows"]
                       if "repeats" in r and "warmup" in r})
    if warmups:
        by_backend = ", ".join(f"{b} 웜업 {w}회" for b, _, w in warmups)
        methodology_warmup = (f"실험 1은 반복 {warmups[0][1]}회, 백엔드별 웜업은 {by_backend} "
                               "(neo4j는 드라이버/서버 예열을 위해 더 큰 웜업을 쓴다)")
    else:
        # 결과 JSON에 repeats/warmup이 없으면 횟수를 지어내지 않는다 —
        # 손으로 적은 숫자는 재실행 시 조용히 낡는다(이 리포트의 재현성 원칙).
        methodology_warmup = "실험 1의 반복·웜업 횟수는 결과 JSON에 기록되지 않았다"

    # 최종 리뷰 Important 13: "세 실험 공통 시드"를 exp3 rows에서만 읽고 손으로
    # "세 실험 공통"이라 타이핑해뒀다 -- fixtures/meta.json(git 추적 대상)에도 seed를
    # 기록해 두 소스가 실제로 일치하는지 교차 확인한 뒤에만 "공통"이라 서술한다.
    e3_rows = e3["data"].get("rows", [])
    e3_seed = e3_rows[0]["seed"] if e3_rows and "seed" in e3_rows[0] else None
    meta_seed = _fixture_meta().get("seed")
    if meta_seed is not None and e3_seed is not None and meta_seed == e3_seed:
        seed_line = (f"- 데이터 생성 시드 고정(seed={meta_seed}) — `fixtures/meta.json`과 실험 3 "
                     "결과가 같은 시드를 기록해 세 실험 공통임을 교차 확인했다. 동일 시드로 "
                     "재실행하면 동일 데이터가 생성된다")
    elif e3_seed is not None:
        seed_line = (f"- 데이터 생성 시드 고정(seed={e3_seed}, 실험 3 결과에서 확인) — 동일 시드로 "
                     "재실행하면 동일 데이터가 생성된다. `fixtures/meta.json`에 seed가 기록돼 있지 "
                     "않아 다른 실험과 공통인지는 교차 검증하지 못했다")
    else:
        seed_line = "- 시드 고정 — 동일 시드로 재실행하면 동일 데이터가 생성된다"

    env_bits = [f"Python {env.get('python', '-')}", env.get('platform', '-'),
               f"CPU {env.get('cpu_count', '-')}코어" +
               (f"({env.get('cpu_model')})" if env.get('cpu_model') else ""),
               f"RAM {env.get('ram', '-')}" if env.get('ram') else None,
               f"Neo4j 서버 {env.get('neo4j_server')}" if env.get('neo4j_server') else None]
    env_line = " · ".join(b for b in env_bits if b)

    parts = [
        "# TeamWeaver 최적화 실험 리포트",
        "",
        "> 이 파일은 `experiments/report.py`가 `experiments/results/*.json`에서 생성한다. "
        "**직접 수정하지 말 것** — 실험을 다시 돌린 뒤 `uv run python -m experiments.report`로 "
        "갱신하면 동일 입력에 대해 **재현 가능**한 동일 결과가 나온다.",
        "",
        "## 측정 방법론",
        "",
        f"- {methodology_warmup}, **중앙값과 p95**를 보고한다",
        "- 실험 3(알고리즘)은 solver 특성상 반복 없이 1회 solve 시간을 그대로 보고한다",
        seed_line,
        "- 실험 코드는 제품 코어(`core/`)를 그대로 import한다. **벤치마크 수치 = 실제 제품 성능**",
        "- 결과가 가설과 다르면 결과를 조작하지 않고 논지를 결과에 맞춰 서술한다",
        "",
        f"**실행 환경:** {env_line}",
        "",
        "---",
        "",
        _exp1(d1),
        "---",
        "",
        _exp2(e2["data"]),
        "---",
        "",
        _exp3(e3["data"]),
        "---",
        "",
        _exp4(e4),
        "---",
        "",
        _exp5(e5, e5_first),
        "---",
        "",
        _decision(e4, e5),
        "---",
        "",
        "## 한계 (반드시 함께 읽을 것)",
        "",
        "- **단일 시드 기준**: 규모별 수치는 시드 하나에 대한 한 번의 관측이다. 분산은 측정하지 않았다.",
        "- **로컬 단일 머신**: 네트워크·디스크 조건이 다른 운영 환경의 수치를 보장하지 않는다.",
        "- **실험 1의 hops=1 잔존 노이즈**: protocol_floor는 프로토콜 왕복 비용의 실측 하한이지만, "
        "재시작 직후 JVM/드라이버 웜업의 잔여 편차가 hops=1 절대값에 남아 있을 수 있다 — 얕은 hop "
        "비교는 참고용으로만 쓸 것.",
        "- **CBC(오픈소스 솔버) 기준**: 상용 솔버(Gurobi 등)를 쓰면 solve time이 크게 달라진다.",
        "- **실험 3의 n≥300 미측정**: n=50/100/200의 solve time 증가율을 외삽한 결과 세션 예산을 "
        "넘어서 스킵했다 — 대규모 구간에서 MILP가 실용적인지는 이 실험만으로는 답할 수 없다 "
        "(자세한 사유는 위 \"미측정 규모\" 표).",
        "- **시너지 쌍 절삭**: 규모 스윕에서는 |C| 상위 쌍만 시너지 항에 포함했다. 절삭 수준(pair_cap)별 "
        "영향은 실험 3 표에 함께 실었다.",
        "- **실험 2의 정확도 축 미측정**: Full-LLM 재추출 항목과 원본 체크박스의 일치율은 실호출로 "
        "검증하지 않았다(live=False).",
        "- **실험 2의 지연(latency) 축 미측정**: 이번 실행은 오프라인 토큰 카운팅만 수행했다. "
        "Task 6 체크포인트 실측치를 참고로 인용했으나, 다른 프롬프트 형태로 측정된 하한 성격의 "
        "수치이지 이번 페이로드로 직접 측정된 값이 아니다.",
        "- **실험 2의 절감률은 출력 토큰 가정에 대해 검증되지 않았다**: savings_pct는 출력 토큰 "
        "가정(out_ratio) 자체에는 불변이지만, 그 가정 형태(출력 ∝ 입력 vs 출력 고정)를 바꾸면 "
        "크게 달라진다(위 \"출력 토큰 모델 민감도\" 표) — 이 실험의 5.777 비율은 다른 형태의 "
        "페이로드에서 측정한 외삽치다.",
        "- **실험 3의 스윕은 커밋된 데모 fixture를 로드하지 않는다**: 규모마다 seed=42로 새로 "
        "생성한 데이터셋(템플릿 리뷰)을 쓴다 — n=100 지점도 fixture와 인원·프로젝트 수만 같을 뿐 "
        "리뷰 텍스트가 달라 optimization_ratio가 다르다(위 실험 3 절 참고).",
        "- **실험 4의 첫 스윕은 보존되지 않았다**: 커밋된 JSON은 두 번의 스윕 중 두 번째이고, "
        "첫 실행의 원자료는 `harness.save_result`의 덮어쓰기로 복구할 수 없다 — 그래서 첫 "
        "실행에 대해서는 확인 가능한 진술만 남겼다(위 실험 4 한계 1).",
        "- **실험 4·5의 Neo4j는 컨테이너, SQLite는 in-process다**: 배포 토폴로지가 대칭이 "
        "아니며, 이 비대칭은 호출당 고정비로 캘리브레이션해 공개했지만 제거하지는 않았다.",
        "- **실험 5의 크래시 내구성 미측정**: 여기서 \"영속성\"은 커밋되어 새 클라이언트에서 "
        "보인다는 뜻이다. 전원 차단·프로세스 강제 종료에 대한 내구성은 어느 쪽도 시험하지 "
        "않았다.",
        "- **실험 5의 축소 반복**: 적재·재수화는 반복 3회로 측정했다(다른 실험은 20회). "
        "이 구간의 `p95_ms`는 백분위수가 아니라 최댓값이다.",
        "",
        "## 결론",
        "",
        "위 다섯 실험의 수치가 아키텍처 선택의 근거다. 각 실험의 표와 그림을 그대로 인용하되, "
        "**한계 섹션을 함께 제시할 것** — 조건을 밝히지 않은 수치는 심사에서 방어할 수 없다.",
        "",
        "저장 계층에 대해서는 결론이 하나 더 있다. 실험 4·5의 결과에 **측정 전에 고정된 규칙**을 "
        "적용한 판정이 위 \"저장 계층 의사결정\" 절에 있다 — 그 판정은 이 리포트를 생성할 때 "
        "`experiments/decision.py`가 커밋된 결과 JSON에서 다시 계산하므로, 데이터를 바꾸지 "
        "않고서는 결론만 바꿀 수 없다.",
        "",
    ]
    return "\n".join(parts)


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(build(), "utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
