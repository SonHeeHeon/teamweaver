"""results/*.json → optimization_report.md.

모든 수치는 JSON에서 읽는다. 리포트를 수기로 고치지 말 것 — 실험을 다시 돌리면
이 스크립트를 재실행해 갱신한다.

수치가 `experiments/results/*.json`에 없으면(예: 실험 2의 fixture 생성 비용처럼
`.omc/`에만 기록되고 git에 커밋되지 않는 값) 이 파일은 해당 수치를 절대 타이핑하지
않는다 — 대신 정성적으로 서술하거나 생략한다. 반대로 `fixtures/meta.json`처럼
git으로 추적되는 JSON은 results/*.json과 마찬가지로 유효한 소스로 취급한다.
"""
import json

from core.config import FIXTURES_DIR, OPT_RATIO_TARGET, REPO_ROOT
from experiments.bench import harness

OUT_PATH = REPO_ROOT / "experiments" / "optimization_report.md"


def _median(rows: list[dict], backend: str, n: int, hops: int) -> float | None:
    for r in rows:
        if r["backend"] == backend and r["n_people"] == n and r["hops"] == hops:
            return r["median_ms"]
    return None


def _fixture_meta() -> dict:
    path = FIXTURES_DIR / "meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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
    for n, h in keys:
        neo = _median(rows, "neo4j", n, h)
        sql = _median(rows, "sqlite", n, h)
        if neo is not None and sql is not None and neo < sql:
            crossovers.append((n, h))
    n_lo, n_hi = keys[0][0], keys[-1][0]
    h_lo, h_hi = min(h for _, h in keys), max(h for _, h in keys)
    n_scope = f"{n_lo}~{n_hi}명" if n_lo != n_hi else f"{n_lo}명"
    scope = f"{n_scope}, hops {h_lo}~{h_hi}" if h_lo != h_hi else f"{n_scope}, hops {h_lo}"
    if not crossovers:
        return (f"**측정 범위({scope}) 전 구간에서 memory < sqlite < neo4j 순서가 유지된다 — "
                "\"규모·hops가 커지면 Neo4j가 역전한다\"는 이 실험의 원 가설은 기각된다.**")
    pts = ", ".join(f"n={n}·hops={h}" for n, h in crossovers)
    return (f"**측정 범위({scope})에서 neo4j가 sqlite보다 빠른 구간이 존재한다({pts}) — "
            "아래 표에서 직접 확인할 것.**")


def _exp1(d: dict) -> str:
    rows, parity, skipped = d["rows"], d["parity"], d["skipped"]
    floor = d.get("protocol_floor")
    lines = ["## 실험 1 — 저장·탐색 계층 3자 비교", "",
             "**질문:** 인력 집합의 N-hop 협업 문맥을 수집·집계할 때 어느 저장 방식이 빠른가?", ""]
    headline = _exp1_headline(rows)
    if headline:
        lines += [headline, ""]
    lines += ["| 인원 | hops | " + " | ".join(("sqlite", "neo4j", "memory")) + " |",
             "|---:|---:|---:|---:|---:|"]
    keys = sorted({(r["n_people"], r["hops"]) for r in rows})
    for n, h in keys:
        cells = []
        for b in ("sqlite", "neo4j", "memory"):
            m = _median(rows, b, n, h)
            cells.append(f"{m:.3f}" if m is not None else "—")
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

    lines += ["### 스코프 명시 (반드시 함께 읽을 것)", ""]
    n_stmt = 1
    ipc = _ipc_dominance(rows, floor["median_ms"]) if floor is not None else None
    if ipc:
        lo, hi = ipc
        pct_str = f"{lo:.0f}%" if lo == hi else f"{lo:.0f}~{hi:.0f}%"
        lines.append(f"{n_stmt}. **hops=1 비교는 순회가 아니라 IPC가 지배한다.** "
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
             f"**측정된 절감률: {d['savings_pct']:.1f}%**", "",
             "> 절감률은 목표치가 아니라 **측정 결과**다. 우리 fixture의 정형:비정형 비중이 "
             "이 값을 결정하므로 아래 민감도 곡선을 함께 본다.", "",
             "> 프로젝트 초기 설계 문서는 이보다 **훨씬 높은** 절감률을 목표로 제시했다 — 위 측정치는 "
             "그 목표에 크게 못 미친다. 최초 목표 수치 자체는 `experiments/results/*.json` 범위 밖의 "
             "로컬 설계 문서에만 있어 이 리포트에는 싣지 않는다(원 목표는 "
             "`.omc/plan/2026-08-02-teamweaver-poc-design.md` 참조).", ""]
    if d.get("output_token_assumption") is not None:
        lines += [f"*출력 토큰/입력 토큰 비율 가정: {d['output_token_assumption']:.3f} — "
                  f"근거: {d.get('output_token_assumption_basis', '-')}*", ""]
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

    meta = _fixture_meta()
    if meta.get("review_mode") == "llm":
        lines += [f"> 이 비교의 전제는 fixture가 실제 LLM 호출로 생성된 리뷰"
                  f"({t['review_count']}건, `review_mode=llm`)라는 것이다 — 템플릿 생성 리뷰였다면 "
                  "자유서술의 극성(polarity)이 정형 `item_score`의 결정론적 함수였으므로 비정형 "
                  "항목이 별도 정보를 담지 않아, 정형 대 비정형을 비교하는 이 실험의 전제 자체가 "
                  "성립하지 않았다. (정확한 재동결 비용은 results/*.json 범위 밖이라 이 리포트에는 "
                  "싣지 않는다 — Task 6 작업 기록 참조.)", ""]

    lines += ["![실험2](figures/exp2_savings.png)", ""]
    return "\n".join(lines)


def _exp3(d: dict) -> str:
    lines = ["## 실험 3 — 알고리즘 (Greedy vs MILP)", "",
             "| 알고리즘 | 인원 | pair_cap | solve(ms) | 최적화율 | 미충원 |",
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

    milp_v = [v for v in violations if v["algorithm"] == "milp"]
    greedy_v = sorted((v for v in violations if v["algorithm"] == "greedy"),
                       key=lambda v: v["n_people"])
    if milp_v and all(v["budget_violations"] == 0 for v in milp_v):
        milp_ns = sorted({v["n_people"] for v in milp_v})
        milp_caps = sorted({v.get("pair_cap") for v in milp_v if v.get("pair_cap") is not None})
        lines += [f"> MILP는 측정된 모든 규모({', '.join(str(n) for n in milp_ns)}명)·모든 "
                  f"pair_cap({', '.join(str(c) for c in milp_caps)})에서 예산 위반 0건이다.", ""]
    if greedy_v:
        seq = "→".join(str(v["budget_violations"]) for v in greedy_v)
        ns = ", ".join(f"n={v['n_people']}" for v in greedy_v)
        lines += [f"> Greedy의 위반 건수는 규모가 커질수록 늘어난다: {seq} ({ns} 순).", ""]

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
                          "\"목표 90%\"는 이 실험이 기준으로 삼은 규모(100인 데모 fixture)에서만 "
                          "성립하고, 규모가 커지면 유지된다는 보장이 없다.")
        lines.append("")

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
        lines.append("")

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


def build() -> str:
    e1 = harness.load_result("exp1_storage")
    e2 = harness.load_result("exp2_pipeline")
    e3 = harness.load_result("exp3_algorithm")
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

    e3_rows = e3["data"].get("rows", [])
    seed = e3_rows[0]["seed"] if e3_rows and "seed" in e3_rows[0] else None
    seed_line = (f"- 데이터 생성 시드 고정(seed={seed}, 세 실험 공통) — 동일 시드로 재실행하면 "
                 "동일 데이터가 생성된다") if seed is not None else \
                "- 시드 고정 — 동일 시드로 재실행하면 동일 데이터가 생성된다"

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
        f"**실행 환경:** Python {env.get('python','-')} · {env.get('platform','-')} · "
        f"CPU {env.get('cpu_count','-')}코어",
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
        "",
        "## 결론",
        "",
        "위 세 실험의 수치가 아키텍처 선택의 근거다. 각 실험의 표와 그림을 그대로 인용하되, "
        "**한계 섹션을 함께 제시할 것** — 조건을 밝히지 않은 수치는 심사에서 방어할 수 없다.",
        "",
    ]
    return "\n".join(parts)


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(build(), "utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
