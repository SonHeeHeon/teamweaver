"""Run the model lab on an organisation-shaped synthetic bundle.

    uv run --group benchmark python -m core.evaluate.factor_lab --size 100 --random 16 --time-limit 60

Writes rehearsal/results/factor_lab/n{size}.json and .html. Data is synthetic and every "outcome" comes from a
stated assumption (core/evaluate/factor_lab/simulator.py) -- the report says so on every page.
"""
import argparse
import html
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from api.settings import PlacementSettings
from core.evaluate.factor_lab.factors import compute_factors
from core.evaluate.factor_lab.robustness import breakdown
from core.evaluate.factor_lab.search import candidates, evaluate, model_matrix, summarize
from core.evaluate.factor_lab.simulator import HELD_OUT, SCENARIOS
from core.graph.memory_graph import MemoryGraph
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from core.optimize.milp import solve_milp_assessment
from core.scoring.engine import ScoringEngine

OUT = Path(__file__).resolve().parents[3] / "rehearsal" / "results" / "factor_lab"


def _robustness(graph, F, weights, params):
    S = model_matrix(F, weights)
    a = solve_milp_assessment(graph, S, ScoringEngine(graph).synergy_matrix(), params)
    if a.accepted is None:
        return None
    return breakdown(graph, F["S"], a.accepted.plan.entries, a.accepted.plan.unfilled)


def _num(v):
    return "—" if v is None else f"{v:.1f}"


def _pct(v):
    return "—" if v is None else f"{v * 100:.0f}%"


def _html(res: dict) -> str:
    sc = {s.name: s.label for s in SCENARIOS}
    rows = "".join(
        f"<tr><td>{html.escape(r['name'])}</td><td>{html.escape(json.dumps(r['weights'], ensure_ascii=False))}</td>"
        + "".join(f"<td>{r['pct'][s]:.1f}</td>" for s in sc)
        + f"<td><b>{r['worst_pct']:.1f}</b></td><td><b>{_num(r['held_out_pct'])}</b></td>"
        f"<td>{r['fill_rate']*100:.1f}%</td><td>{r['unfilled']}</td><td>{html.escape(str(r.get('termination')))}</td></tr>"
        for r in res["summary"])
    failed = "".join(f"<li>{html.escape(r['name'])}: {html.escape(str(r.get('error')))}</li>"
                     for r in res["results"] if not r["accepted"])
    rob = ""
    for label, table in (("현재 모델(S만)", res.get("robustness_base")), ("최강건 후보", res.get("robustness_best"))):
        if not table:
            continue
        rob += f"<h3>{label}</h3>"
        for trait, rs in table.items():
            rob += ("<table><tr><th>" + trait + "</th><th>사업</th><th>정원</th><th>충원율</th><th>평균 기술 적합</th><th>미충원 비중</th></tr>"
                    + "".join(f"<tr><td>{html.escape(r['group'])}</td><td>{r['projects']}</td><td>{r['seats']}</td>"
                              f"<td>{_pct(r['fill_rate'])}</td>"
                              f"<td>{r['mean_fit']}</td><td>{r['unfilled_share']*100:.0f}%</td></tr>" for r in rs)
                    + "</table>")
    heads = "".join(f"<th title='{html.escape(l)}'>{n}</th>" for n, l in sc.items())
    legend = "".join(f"<li><b>{n}</b> {html.escape(l)}</li>" for n, l in sc.items())
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>모델 실험실 결과</title><style>
:root{{--bg:#fbfaf7;--fg:#1f2328;--muted:#666;--line:#dcd9d2;--acc:#2f6f5e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1d1c;--fg:#e8e6e1;--muted:#aaa;--line:#3a3d3b;--acc:#7cc4ad}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1000px;margin:auto}}h2{{color:var(--acc)}}.sub{{color:var(--muted)}}table{{border-collapse:collapse;width:100%;font-size:.9em;margin:.6em 0}}
td,th{{border-bottom:1px solid var(--line);padding:5px;text-align:left}}th{{color:var(--muted)}}div.w{{overflow-x:auto}}
</style></head><body><main>
<h1>모델(방정식) 실험실 — {res['size']}명</h1>
<p class="sub">가상 조직 데이터 · 성과는 아래 다섯 가지 <b>가정</b>으로 계산한 값이며 실제 성과가 아니다(NOT_CALIBRATED) · 솔버 HiGHS, 시간 한도 {res['time_limit']}초</p>
<ul>{legend}</ul>
<h2>후보 모델별 성과 (각 가정에서 가장 좋은 후보 = 100)</h2>
<p class="sub"><b>읽는 법(순환 주의)</b>: T1~T5는 후보 모델과 <b>같은 팩터</b>로 만든 가정이라, 어떤 팩터를 쓴 후보가 그 팩터를 중시하는 가정에서 이기는 것은 거의 당연하다 — 이 열들은 "무엇을 얻고 무엇을 잃는가"(트레이드오프)를 보여 줄 뿐이다.
T6·T7은 어느 후보도 점수에 넣지 않은 신호(필수 기술 공백, 쪼개기 비효율)로 만든 <b>검증용 가정</b>이라, 여기서도 좋은 후보만 "일반화된다"고 말할 수 있다.
"T1~T5 최악"은 T1~T5 중 가장 낮은 값, "검증 최악"은 T6·T7 중 낮은 값. 위에서부터 T1~T5 최악값 순.</p>
<div class="w"><table><tr><th>후보</th><th>가중치</th>{heads}<th>T1~T5 최악</th><th>검증 최악</th><th>충원율</th><th>미충원</th><th>솔버 종료</th></tr>{rows}</table></div>
{('<h3>실패한 후보</h3><ul>' + failed + '</ul>') if failed else ''}
<h2>검증용 가정으로 확인</h2><p>{html.escape(res['cross_validation'])}</p>
<h2>특이 프로젝트 점검</h2>{rob}
</main></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=100, choices=(100, 200, 300))
    ap.add_argument("--random", type=int, default=16)
    ap.add_argument("--time-limit", type=int, default=60)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    t0 = time.perf_counter()
    root = generate_org_bundle(Path(tempfile.mkdtemp()) / "b", args.size, seed=args.seed)
    bundle, report = load_bundle(root)
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    F = compute_factors(graph, bundle)
    params = PlacementSettings().to_milp_params().model_copy(update={"time_limit": args.time_limit})
    cands = candidates(args.random, args.seed)
    results = evaluate(graph, F, cands, params)
    summary = summarize(results)
    # pick on the in-sample assumptions (T1-T5), judge on the held-out ones (T6-T7) no candidate scores
    cv = "평가할 후보가 없다."
    if summary:
        pick = summary[0]                                  # best worst-case over T1-T5
        base = next((r for r in summary if r["name"] == "base:S"), None)
        cv = (f"T1~T5만 보고 고른 후보 '{pick['name']}'는 검증용 가정에서 "
              f"{', '.join(f'{s} {pick['pct'][s]:.1f}' for s in HELD_OUT)}"
              + (f" — 현재 모델(S만)은 {', '.join(f'{s} {base['pct'][s]:.1f}' for s in HELD_OUT)}." if base
                 else " — 현재 모델(S만)은 풀이에 실패해 비교 불가."))
    best = summary[0] if summary else None
    res = {"size": args.size, "seed": args.seed, "time_limit": args.time_limit,
           "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "scenarios": {s.name: s.label for s in SCENARIOS},
           "results": [r.__dict__ for r in results], "summary": summary, "cross_validation": cv,
           "robustness_base": _robustness(graph, F, {"S": 1.0}, params),
           "robustness_best": _robustness(graph, F, best["weights"], params) if best else None,
           "elapsed_s": round(time.perf_counter() - t0, 1)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"n{args.size}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / f"n{args.size}.html").write_text(_html(res), encoding="utf-8")
    print(f"[factor-lab] done n{args.size} in {res['elapsed_s']}s -> {OUT}")


if __name__ == "__main__":
    main()
