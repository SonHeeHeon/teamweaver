"""Past outcomes vs the model's assumptions -- report for the final report (2026-10-06).

    uv run python -m rehearsal.outcome_check

User: "실 데이터는 … 최종까지 어차피 없을거라 이걸 실 데이터라고 가정하고 검증할건 다 검증해줘". The organisation-
shaped bundles (seed 2026) are treated as the company's data; core/evaluate/outcome_check rebuilds every past team
from information available before it started and relates it to the recorded outcomes.
"""
import html
import json
import tempfile
from pathlib import Path

from rehearsal.run import RESULTS, SEED, _bundle, _env_info, _now

OUT_JSON = RESULTS / "outcome-check.json"
OUT_HTML = RESULTS / "outcome-check.html"
LABEL = {
    "prior_cowork": "이전에 함께 일한 쌍 비율",
    "cowork_strength": "함께 일한 개월 강도(협업 점수 C의 개월 항)",
    "familiar_new": "익숙한 쌍 비율 — 새 기준(최근 3년 중 12개월)",
    "familiar_old": "익숙한 쌍 비율 — 옛 기준(10년 중 6개월)",
    "industry_exp": "업종 경험자 비율",
    "team_size": "팀 인원",
    "churn": "짧게 빠진 인원 비율(사업 중 관측·고객 요청 교체 제외 — 계획 시점엔 모름)",
}
VERDICT = {"supported": "지지됨", "contradicted": "반대로 나옴", "not shown": "드러나지 않음"}


def run(sizes=(100, 200, 300)) -> dict:
    from core.evaluate.outcome_check import correlations, project_features, regression, verdicts
    from core.ingest.loader import load_bundle
    out = {"env": _env_info(), "started_at": _now(), "seed": SEED, "by_size": {}}
    for n in sizes:
        bundle, report = load_bundle(_bundle(n, Path(tempfile.mkdtemp(prefix=f"outcome-n{n}-"))))
        rows = project_features(bundle.tables)
        score = correlations(rows, "customer_score")
        follow = correlations(rows, "follow_on")
        reg = regression(rows)
        full = [r for r in rows if r["observed_36"]]            # 시작 전 36개월이 데이터 안에 온전히 있는 사업
        reg_full = regression(full)
        out["by_size"][str(n)] = {"projects": len(rows), "customer_score": score, "follow_on": follow,
                                  "regression": reg, "verdicts": verdicts(score, reg),
                                  "observed_36": {"projects": len(full), "regression": reg_full,
                                                  "verdicts": verdicts(correlations(full, "customer_score"), reg_full)},
                                  "mean_score": round(sum(r["customer_score"] for r in rows) / len(rows), 3)}
        print(n, len(rows), verdicts(score, reg), flush=True)
    out["finished_at"] = _now()
    return out


def render(d: dict) -> str:
    blocks = []
    for n, s in d["by_size"].items():
        rows = "".join(
            f"<tr><td>{html.escape(LABEL[f])}</td><td>{_r(s['customer_score'][f])}</td><td>{_r(s['follow_on'][f])}</td></tr>"
            for f in LABEL)
        ver = "".join(f"<li>{html.escape(k)}: <b>{VERDICT.get(v, v)}</b></li>" for k, v in s["verdicts"].items())
        full = s.get("observed_36", {})
        reg_rows = "".join(
            f"<tr><td>{html.escape(LABEL[f])}</td><td>{c['coef_per_sd']:+.2f} ({c['ci95'][0]:+.2f} ~ {c['ci95'][1]:+.2f})</td>"
            f"<td>{_c(full.get('regression', {}).get(f))}</td></tr>"
            for f, c in s.get("regression", {}).items())
        ver_full = "".join(f"<li>{html.escape(k)}: <b>{VERDICT.get(v, v)}</b></li>"
                           for k, v in full.get("verdicts", {}).items())
        blocks.append(f"<h2>{n}명 조직 · 과거 사업 {s['projects']}개 (평균 고객 평가 {s['mean_score']})</h2>"
                      f"<div class='scroll'><table><tr><th>팀 특징(사업 시작 전 정보)</th><th>고객 평가와의 순위상관 (95% 구간)</th>"
                      f"<th>후속 과제와의 순위상관 (95% 구간)</th></tr>{rows}</table></div>"
                      f"<p>함께 넣어 본 회귀(고객 평가, 표준편차 1단위당 변화 · 95% 구간) — 익숙한 쌍의 값은 '함께 일한 강도'를 고정했을 때의"
                      f" 추가 효과, 즉 감점 μ가 겨냥하는 효과다. 오른쪽은 시작 전 36개월이 데이터 안에 온전히 있는 사업 {full.get('projects', '—')}개만 본 민감도:</p>"
                      f"<div class='scroll'><table><tr><th>특징</th><th>계수 (95% 구간) · 전체</th><th>36개월 온전 관측 사업만</th></tr>{reg_rows}</table></div>"
                      f"<p>모델 가정 판정(회귀 계수의 95% 구간이 0을 넘는지) — 전체:</p><ul>{ver}</ul>"
                      f"<p>36개월 온전 관측 사업만:</p><ul>{ver_full}</ul>")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>과거 성과로 본 모델 가정</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f6f5e;--box:#eef5f2;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1d1c;--fg:#e8e6e1;--muted:#aaa;--acc:#7cc4ad;--box:#24302c;--line:#444}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.7 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:900px;margin:auto}}h1{{margin:0 0 .2em}}.sub{{color:var(--muted)}}h2{{color:var(--acc);margin-top:1.8em}}
.box{{background:var(--box);border-radius:10px;padding:12px 16px;margin:1em 0}}.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:.92em}}td,th{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left}}</style></head><body><main>
<h1>과거 성과로 본 모델 가정</h1>
<div class="sub">TeamWeaver · {html.escape(d['started_at'])} · 커밋 {html.escape(str(d['env'].get('commit')))} · 조직형 데이터 seed {d['seed']}를 실데이터로 가정 · 사업 효과 NOT_CALIBRATED</div>
<div class="box"><b>무엇을 봤나.</b> 끝난 과거 사업마다 업무 이력으로 팀을 복원하고, <b>그 사업이 시작되기 전에 알 수 있던 정보만으로</b> 팀 특징을 계산해
고객 평가·후속 과제와 순위상관(Spearman)을 쟀다. 괄호는 부트스트랩 1,000회 95% 구간이다. 배치 모델은 "함께 일해 본 사람끼리는 협업이 좋다(λ&gt;0)"와
"너무 오래 붙어 다닌 사이는 감점(μ&gt;0)"을 동시에 가정한다 — 이 두 가정이 과거 성과와 맞는지가 핵심 질문이다.<br>
<b>한계.</b> 이 성과는 가상 데이터의 숨은 규칙(실제 역량·이전 공동 경험·업종 경험·들락날락·고객 궁합·운)으로 만들었다. 이전 공동 경험과 업종 경험이
규칙에 들어 있으므로 그 방향이 다시 보이는 것은 절차가 제대로 돈다는 확인일 뿐, 현실 성과에 대한 증거가 아니다.</div>
{''.join(blocks)}
{_analysis()}
</main></body></html>"""


def _c(c: dict | None) -> str:
    if not c:
        return "—"
    return f"{c['coef_per_sd']:+.2f} ({c['ci95'][0]:+.2f} ~ {c['ci95'][1]:+.2f})"


def _r(c: dict) -> str:
    if c["rho"] is None:
        return "—"
    lo, hi = c["ci95"]
    return f"{c['rho']:+.2f} ({lo:+.2f} ~ {hi:+.2f})"


def _analysis() -> str:
    p = RESULTS / "outcome-check-analysis.json"
    if not p.exists():
        return ""
    a = json.loads(p.read_text("utf-8"))
    items = "".join(f"<li>{html.escape(x)}</li>" for x in a.get("findings", []))
    limits = "".join(f"<li>{html.escape(x)}</li>" for x in a.get("limits", []))
    return (f"<h2>분석</h2><ul>{items}</ul><h2>제안</h2><div class='box'>{html.escape(a.get('proposal', ''))}</div>"
            f"<h2>한계</h2><ul>{limits}</ul>"
            f"<h2>결정</h2><div class='box'>{html.escape(a.get('decision') or '사용자 결정 대기')}</div>")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not args.report:
        OUT_JSON.write_text(json.dumps(run(), ensure_ascii=False, indent=1), "utf-8")
    OUT_HTML.write_text(render(json.loads(OUT_JSON.read_text("utf-8"))), "utf-8")
    print(f"written {OUT_JSON} and {OUT_HTML}")


if __name__ == "__main__":
    main()
