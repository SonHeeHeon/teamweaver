"""'익숙한 쌍(너무 오래 함께 일한 쌍)' 기준 비교 — 최종 보고서용 근거(2026-10-06 사용자 요청).

    uv run --group benchmark python -m rehearsal.rule_compare            # 3기준 x 100/200/300 x 1·4시드, 약 30분
    uv run --group benchmark python -m rehearsal.rule_compare --report   # JSON -> HTML만 다시

사용자 요청: "지금 기준과 3년 기준 12개월, 5년 중 12개월 다 최종 보고서때 작성할 수 있도록 결과를 남겨놔줘. 각각의
소요시간과 성과 비교분석해서 최종 결정을 내렸다는 근거가 필요하니."

측정 방법: 조직형 가상 묶음(seed 2026)에서 안 A를 서비스 자동 시간(인원 기준)으로 푼다. 기준을 바꾸려고 이 도구 안에서만
세 곳(서비스 모델 `milp._overfamiliar_pairs`, 독립 검증기 `validation._independent_penalty_pairs`, 평가기
`plan_eval._overfamiliar_pairs`)을 같은 쌍 집합으로 맞춘다 -- 기준이 정해지면 정식에 제대로 넣는다.
목적값은 기준마다 감점 대상이 달라 직접 비교하지 않고, 기준과 무관한 지표(빈자리·기술 적합도·협업 보상)와
교차 채점(각 안을 현행 기준으로 다시 채점)으로 비교한다.
"""
import argparse
import datetime as dt
import html
import json
import tempfile
import time
from pathlib import Path

from rehearsal.run import RESULTS, SEED, _bundle, _env_info, _now

RULES = {                   # key -> (label, lookback months, minimum months together)
    "current": ("현행: 10년 중 6개월 이상", 120, 6),
    "y5m12": ("후보 A: 최근 5년 중 12개월 이상", 60, 12),
    "y3m12": ("후보 B: 최근 3년 중 12개월 이상", 36, 12),
}
SIZES = (100, 200, 300)
SEEDS = (1, 4)
OUT_JSON = RESULTS / "rule-compare.json"
OUT_HTML = RESULTS / "rule-compare.html"
ANALYSIS = RESULTS / "rule-compare-analysis.json"      # 해석(측정값과 분리), 사용자 결정도 여기에


def familiar_pairs(bundle, graph, lookback_months: int, min_months: int) -> set[tuple[int, int]]:
    """work_history에서 최근 lookback_months 동안 같은 사업에 함께 있던 개월 수가 min_months 이상인 쌍."""
    from core.ingest.convert import _coworks, lookback_start
    first = bundle.horizon[0]
    cw = _coworks(bundle.tables["work_history.csv"], first - dt.timedelta(days=1), lookback_start(first, lookback_months))
    pidx = graph.pid_index
    return {tuple(sorted((pidx[c.a_id], pidx[c.b_id]))) for c in cw if c.co_months >= min_months}


def _use_rule(pairs: set[tuple[int, int]]) -> None:
    import core.evaluate.plan_eval as plan_eval
    import core.optimize.milp as milp
    import core.optimize.validation as validation
    milp._overfamiliar_pairs = lambda graph, threshold, window_months=None: set(pairs)
    plan_eval._overfamiliar_pairs = lambda graph, threshold, window_months=None: set(pairs)
    validation._independent_penalty_pairs = lambda graph, params: tuple(sorted(pairs))


def _measure(size: int) -> list[dict]:
    import core.evaluate.plan_eval as plan_eval
    import core.optimize.milp as milp
    import core.optimize.validation as validation
    from api.settings import PlacementSettings
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.alternatives import _PLAN_A_GAP
    from core.optimize.time_budget import recommend
    from core.scoring.engine import ScoringEngine
    originals = (milp._overfamiliar_pairs, plan_eval._overfamiliar_pairs, validation._independent_penalty_pairs)
    work = Path(tempfile.mkdtemp(prefix=f"rule-compare-n{size}-"))
    bundle, report = load_bundle(_bundle(size, work))
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    limit = recommend(size).per_solve_s
    sets = {k: familiar_pairs(bundle, graph, months, minimum) for k, (_, months, minimum) in RULES.items()}
    rows = []
    try:
        for key in RULES:
            for seeds in SEEDS:
                _use_rule(sets[key])
                # 서비스는 안 A를 gap 1%로 푼다(alternatives._PLAN_A_GAP) -- 같은 조건(리뷰 SHOULD, 2026-10-06 첫 측정은 5%)
                params = PlacementSettings().to_milp_params().model_copy(
                    update={"time_limit": limit, "solver": "highs", "solver_seeds": seeds,
                            "gap": min(PlacementSettings().gap, _PLAN_A_GAP)})
                row = {"size": size, "rule": key, "seeds": seeds, "time_limit": limit, "gap_target": params.gap,
                       "pairs": len(sets[key])}
                t = time.perf_counter()
                try:
                    a = milp.solve_milp_assessment(graph, S, C, params)
                    row["elapsed_s"] = round(time.perf_counter() - t, 1)
                    ev = a.native_capture.evidence if a.native_capture is not None else None
                    cand = a.accepted
                    row["termination"] = getattr(ev, "termination_reason", None)
                    row["accepted"] = cand is not None
                    if cand is not None:
                        entries = list(cand.plan.entries)
                        own = plan_eval.evaluate_plan(graph, S, C, params, entries).objective
                        row.update(_plan_metrics(graph, S, entries, sets, own, cand.objective,
                                                 None if ev is None else ev.best_bound, params.slack_penalty))
                        _use_rule(sets["current"])           # 교차 채점: 같은 안을 현행 기준으로
                        strict = plan_eval.evaluate_plan(graph, S, C, params, entries).objective
                        row["strict_total"] = round(strict.total, 3)
                        row["strict_overfamiliarity"] = round(strict.overfamiliarity, 3)
                except RuntimeError as exc:
                    row.update({"elapsed_s": round(time.perf_counter() - t, 1), "error": str(exc)[:300]})
                print(json.dumps(row, ensure_ascii=False), flush=True)
                rows.append(row)
    finally:
        milp._overfamiliar_pairs, plan_eval._overfamiliar_pairs, validation._independent_penalty_pairs = originals
    return rows


def _plan_metrics(graph, S, entries, sets, own, objective, bound, slack_penalty: float) -> dict:
    """objective = 그 기준 자신의 목적값(기준끼리 비교하지 않는다). seats = 사람×사업 배정 건수(겸임은 여러 번).
    together_familiar = 같은 사업에 함께 배정된 서로 다른 쌍 중 각 정의에 걸리는 쌍 수(감점은 쌍×사업 단위)."""
    pdx, jdx = graph.pid_index, graph.project_index
    teams: dict[int, list[int]] = {}
    for e in entries:
        teams.setdefault(jdx[e.project_id], []).append(pdx[e.person_id])
    together = {(min(p, q), max(p, q)) for members in teams.values()
                for i, p in enumerate(members) for q in members[i + 1:]}
    seats = [S[pdx[e.person_id], jdx[e.project_id]] for e in entries]
    return {
        "objective_own_rule": round(objective, 4),
        "best_bound": None if bound is None else round(bound, 4),
        # 음수 목적(빈자리 감점)이면 상대 갭은 의미가 없다
        "gap": None if bound is None or objective <= 1e-9 else round(abs(bound - objective) / abs(objective), 4),
        "unfilled_seats": round(-own.unfilled / slack_penalty),
        "skill": round(own.skill, 3), "synergy": round(own.synergy, 3), "overfamiliarity": round(own.overfamiliarity, 3),
        "seats": len(entries), "avg_seat_fit": round(sum(seats) / len(seats), 4) if seats else None,
        "pairs_together": len(together),
        "together_familiar": {k: len(together & s) for k, s in sets.items()},
    }


def run(sizes=SIZES) -> dict:
    out = {"env": _env_info(), "started_at": _now(), "seed": SEED, "rules": {k: list(v) for k, v in RULES.items()},
           "runs": []}
    for size in sizes:
        out["runs"] += _measure(size)
    out["finished_at"] = _now()
    return out


def _fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return f"{v:,}"


def render(data: dict) -> str:
    runs = data["runs"]
    label = {k: v[0] for k, v in data["rules"].items()}
    rows_html = []
    for size in sorted({r["size"] for r in runs}):
        for seeds in sorted({r["seeds"] for r in runs}):
            sel = [r for r in runs if r["size"] == size and r["seeds"] == seeds]
            if not sel:
                continue
            rows_html.append(f"<h3>{size}명 · {'단일 시드' if seeds == 1 else f'시드 {seeds}개 동시'} · 안 A 시간 한도 {sel[0]['time_limit']}초</h3>")
            rows_html.append("<div class='scroll'><table><tr><th>기준</th><th>익숙한 쌍</th><th>소요</th><th>종료</th><th>빈자리</th>"
                             "<th>배정 건수<br>(사람×사업)</th><th>기술 적합 합계</th><th>자리당 적합도</th><th>협업 보상</th><th>갭</th>"
                             "<th>함께 배정된 쌍 중 '익숙'<br>현행 / 5년 / 3년 잣대</th><th>현행 기준으로 채점</th></tr>")
            for r in sel:
                if "error" in r or not r.get("accepted"):
                    rows_html.append(f"<tr><td>{html.escape(label[r['rule']])}</td><td>{_fmt(r['pairs'])}</td>"
                                     f"<td>{_fmt(r['elapsed_s'])}초</td><td colspan='9'>해 없음: "
                                     f"{html.escape(r.get('error', 'validation rejected'))}</td></tr>")
                    continue
                term = (f"목표 갭 {100 * r.get('gap_target', 0.05):.0f}% 도달" if r["termination"] == "Optimal"
                        else "시간 한도")
                rows_html.append(
                    f"<tr><td>{html.escape(label[r['rule']])}</td><td>{_fmt(r['pairs'])}</td><td>{_fmt(r['elapsed_s'])}초</td>"
                    f"<td>{term}</td><td class='{'bad' if r['unfilled_seats'] else ''}'>{r['unfilled_seats']}</td>"
                    f"<td>{_fmt(r['seats'])}</td><td>{_fmt(r['skill'], 2)}</td><td>{_fmt(r['avg_seat_fit'], 3)}</td><td>{_fmt(r['synergy'], 2)}</td>"
                    f"<td>{_fmt(None if r['gap'] is None else 100 * r['gap'])}%</td>"
                    f"<td>{_fmt(r['together_familiar']['current'])} / {_fmt(r['together_familiar']['y5m12'])} / "
                    f"{_fmt(r['together_familiar']['y3m12'])} (전체 {_fmt(r['pairs_together'])}쌍)</td>"
                    f"<td>{_fmt(r.get('strict_total'), 2)}</td></tr>")
            rows_html.append("</table></div>")
    counts = {}
    for r in runs:
        counts.setdefault(r["rule"], {})[r["size"]] = r["pairs"]
    count_rows = "".join(f"<tr><td>{html.escape(label[k])}</td>" + "".join(f"<td>{_fmt(counts[k].get(s))}</td>" for s in SIZES)
                         + "</tr>" for k in data["rules"] if k in counts)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>익숙한 쌍 기준 비교</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f6f5e;--box:#eef5f2;--line:#ddd;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1d1c;--fg:#e8e6e1;--muted:#aaa;--acc:#7cc4ad;--box:#24302c;--line:#444;--bad:#f2b8b5}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.7 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:980px;margin:auto}}h1{{margin:0 0 .2em}}.sub{{color:var(--muted)}}h2{{color:var(--acc);margin-top:2em}}
.box{{background:var(--box);border-radius:10px;padding:12px 16px;margin:1em 0}}.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:.9em}}td,th{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;white-space:nowrap}}
.bad{{color:var(--bad);font-weight:600}}</style></head><body><main>
<h1>'익숙한 쌍' 기준 비교</h1>
<div class="sub">TeamWeaver · 측정 {html.escape(data['started_at'])} ~ {html.escape(data.get('finished_at', ''))} · 커밋 {html.escape(str(data['env'].get('commit')))} · 가상 데이터 seed {data['seed']} · 사업 효과 NOT_CALIBRATED</div>
<div class="box"><b>왜 비교했나.</b> 실제 같은 10년 이력에서는 "10년 중 6개월 이상 함께 일함"에 걸리는 쌍이 수천~1만 개라
배치 계산이 200명부터 무너졌다. 사용자 요청으로 현행·최근 5년 중 12개월·최근 3년 중 12개월을 같은 데이터·같은 조건에서 쟀다.<br>
<b>읽는 법.</b> 목적값은 기준마다 감점 대상이 달라 직접 비교하지 않는다. 빈자리·자리당 적합도·협업 보상은 기준과 무관하게 비교할 수 있다(기술 적합 <b>합계</b>는 배치 인원이 많을수록 커진다 —
정원에 적히지 않은 등급은 예산 안에서 자유롭게 추가되므로 느슨한 기준일수록 인원이 늘 수 있다).
"현행 기준으로 채점"은 같은 안을 가장 엄격한 잣대로 다시 매긴 점수다. 시간 한도는 서비스 자동 시간(인원 기준)이다.<br>
<b>측정 조건 주의.</b> {html.escape(data.get('note', ''))} 기준에 따라 바뀌는 것은 감점 쌍뿐이고, 협업 점수(C)와 기술 점수(S)는
10년 조회 그대로다 — 정식 반영 때도 감점용 조회 기간을 따로 두어야 한다(10년 창 자체를 줄이면 C·S까지 바뀐다).</div>
<h2>기준별 익숙한 쌍 수</h2><table><tr><th>기준</th>{''.join(f'<th>{s}명</th>' for s in SIZES)}</tr>{count_rows}</table>
<h2>안 A 결과</h2>{''.join(rows_html)}
{_analysis_html()}
</main></body></html>"""


def _analysis_html() -> str:
    if not ANALYSIS.exists():
        return "<h2>결정</h2><div class='box'>사용자 결정 대기</div>"
    a = json.loads(ANALYSIS.read_text("utf-8"))
    items = "".join(f"<li>{html.escape(x)}</li>" for x in a.get("findings", []))
    limits = "".join(f"<li>{html.escape(x)}</li>" for x in a.get("limits", []))
    decision = a.get("decision")
    return (f"<h2>분석</h2><ul>{items}</ul>"
            f"<h2>권장</h2><div class='box'>{html.escape(a.get('recommendation', ''))}</div>"
            f"<h2>한계</h2><ul>{limits}</ul>"
            f"<h2>결정</h2><div class='box'>{html.escape(decision) if decision else '사용자 결정 대기'}</div>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="re-render the HTML from the saved JSON")
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    args = ap.parse_args()
    if not args.report:
        data = run(args.sizes)
        OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    data = json.loads(OUT_JSON.read_text("utf-8"))
    OUT_HTML.write_text(render(data), "utf-8")
    print(f"written {OUT_JSON} and {OUT_HTML}")


if __name__ == "__main__":
    main()
