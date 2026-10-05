"""Build the comparison report for the scale rehearsal from rehearsal/results/n*/{pipeline,sweep}.json.

    uv run python -m rehearsal.report   ->  rehearsal/results/rehearsal-report.html

Self-contained HTML (inline CSS + SVG, no scripts) for demos. Numbers are copied from the result files as
they are; missing stages are shown as "미실행". Data is synthetic and business value is NOT_CALIBRATED.
"""
import html
import json
from pathlib import Path

from rehearsal.run import RESULTS

COLORS = {"cbc": "#c0392b", "highs": "#2459c4"}


def _load(size: int, name: str) -> dict | None:
    p = RESULTS / f"n{size}" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _fmt(v, nd=1, suffix=""):
    if v is None:
        return "—"
    return f"{v:,.{nd}f}{suffix}" if isinstance(v, (int, float)) else html.escape(str(v))


def _curve_svg(sweep: dict) -> str:
    """gap_to_best (%) against time limit, one line per solver. Unaccepted runs are drawn as an X at the top."""
    W, H, L, B = 360, 200, 46, 30
    limits = sweep["limits"]
    xs = {lim: L + (W - L - 10) * k / max(1, len(limits) - 1) for k, lim in enumerate(limits)}
    gaps = [r["gap_to_best"] * 100 for r in sweep["runs"] if r.get("gap_to_best") is not None]
    top = max(5.0, max(gaps, default=0) * 1.1)
    y = lambda g: 10 + (H - B - 10) * (g / top)           # 0% at the top line, worse goes down
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="시간 한도별 최선 대비 차이">',
             f'<line x1="{L}" y1="10" x2="{W-10}" y2="10" stroke="currentColor" stroke-opacity=".25"/>',
             f'<text x="{L-6}" y="14" text-anchor="end" font-size="10">0%</text>',
             f'<text x="{L-6}" y="{H-B+4}" text-anchor="end" font-size="10">{top:.0f}%</text>']
    for lim, x in xs.items():
        parts.append(f'<text x="{x}" y="{H-8}" text-anchor="middle" font-size="10">{lim}s</text>')
    for solver, color in COLORS.items():
        rows = [r for r in sweep["runs"] if r["solver"] == solver]
        pts = [(xs[r["time_limit"]], y(r["gap_to_best"] * 100)) for r in rows if r.get("gap_to_best") is not None]
        if len(pts) > 1:
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="'
                         + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + '"/>')
        for r in rows:
            x = xs[r["time_limit"]]
            if r.get("gap_to_best") is None:
                parts.append(f'<text x="{x}" y="{H-B}" text-anchor="middle" fill="{color}" font-size="13">✕</text>')
            else:
                parts.append(f'<circle cx="{x}" cy="{y(r["gap_to_best"]*100):.1f}" r="3" fill="{color}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _recommend(sweep: dict, solver: str, tol: float = 0.01):
    """Smallest time limit whose plan A is within `tol` of the best known objective (None if never)."""
    ok = [r["time_limit"] for r in sweep["runs"]
          if r["solver"] == solver and r.get("gap_to_best") is not None and r["gap_to_best"] <= tol]
    return min(ok) if ok else None


def build() -> Path:
    sizes = [s for s in (100, 200, 300) if (RESULTS / f"n{s}").exists()]
    pipe = {s: _load(s, "pipeline") for s in sizes}
    sweep = {s: _load(s, "sweep") for s in sizes}
    rows_pipe, rows_plans, rows_rec, curves = [], [], [], []
    for s in sizes:
        p = pipe[s]
        if p:
            st = p.get("steps", {})
            grp = p.get("bundle", {}).get("groups", {})
            rows_pipe.append(
                f"<tr><td>{s}명</td><td>{' + '.join(f'{k} {v}' for k, v in grp.items())}</td>"
                f"<td>{_fmt(st.get('upload_s'), 2, '초')}</td><td>{_fmt(st.get('optimize_s'), 1, '초')}</td>"
                f"<td>{_fmt(st.get('whatif_s'), 1, '초')}</td><td>{_fmt(st.get('pdf_s'), 1, '초')}</td>"
                f"<td>{len(p.get('plans', []))}/4 ({html.escape(str((p.get('optimize_done') or {}).get('stop_reason') or '정상'))})</td></tr>")
            for pl in p.get("plans", []):
                rows_plans.append(
                    f"<tr><td>{s}명</td><td>{pl['label']}</td><td>{_fmt(pl['arrived_s'], 1, '초')}</td>"
                    f"<td>{_fmt(pl['objective'], 1)}</td><td>{_fmt(pl['optimization_ratio'] * 100, 1, '%')}</td>"
                    f"<td>{_fmt(pl['fulfillment'] * 100, 1, '%')}</td><td>{len(pl['unfilled'])}</td><td>{pl['people']}</td></tr>")
        w = sweep[s]
        if w:
            rows_rec.append(f"<tr><td>{s}명</td><td>{_fmt(_recommend(w, 'cbc'), 0, '초')}</td>"
                            f"<td>{_fmt(_recommend(w, 'highs'), 0, '초')}</td>"
                            f"<td>{w['people']}명·{w['projects']}개</td></tr>")
            curves.append(f"<figure><figcaption>{s}명 — 시간 한도별 A안의 최선 대비 차이"
                          f"(<span style='color:{COLORS['cbc']}'>CBC</span> / "
                          f"<span style='color:{COLORS['highs']}'>HiGHS</span>, ✕ = 답 없음)</figcaption>{_curve_svg(w)}</figure>")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>규모별 리허설 비교</title><style>
:root{{--bg:#fbfaf7;--fg:#1f2328;--muted:#666;--line:#dcd9d2;--acc:#2f6f5e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1d1c;--fg:#e8e6e1;--muted:#aaa;--line:#3a3d3b;--acc:#7cc4ad}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:960px;margin:auto}}h1{{margin:0}}h2{{color:var(--acc);margin-top:2em}}.sub{{color:var(--muted)}}
table{{border-collapse:collapse;width:100%;font-size:.92em}}td,th{{border-bottom:1px solid var(--line);padding:6px;text-align:left}}
th{{color:var(--muted)}}.wrap{{overflow-x:auto}}figure{{margin:1em 0}}figcaption{{font-size:.9em;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
</style></head><body><main>
<h1>규모별 리허설 비교 (100·200·300명)</h1>
<p class="sub">가상 조직 데이터(core/ingest/org_profile.py) · 사업 효과 NOT_CALIBRATED · 수치는 rehearsal/results의 원본 그대로</p>
<h2>전 과정 소요 시간 (실제 서버, 관리자 기본 설정)</h2><div class="wrap"><table>
<tr><th>규모</th><th>구성</th><th>업로드</th><th>배치 A~D</th><th>교체 검토(LLM)</th><th>PDF</th><th>나온 안(종료 사유)</th></tr>
{''.join(rows_pipe) or '<tr><td colspan=7>미실행</td></tr>'}</table></div>
<h2>안별 결과</h2><div class="wrap"><table>
<tr><th>규모</th><th>안</th><th>도착</th><th>목적값</th><th>최적화율</th><th>기술 충족률</th><th>미충원</th><th>배치 인원</th></tr>
{''.join(rows_plans) or '<tr><td colspan=8>미실행</td></tr>'}</table></div>
<h2>A안 시간 한도별 품질 (최선 대비 차이, 작을수록 좋음)</h2><div class="grid">{''.join(curves) or '<p>미실행</p>'}</div>
<h2>최선 대비 1% 안에 드는 가장 짧은 시간 한도</h2><div class="wrap"><table>
<tr><th>규모</th><th>CBC(현재 서비스)</th><th>HiGHS(잠정 기본)</th><th>문제 크기</th></tr>
{''.join(rows_rec) or '<tr><td colspan=4>미실행</td></tr>'}</table></div>
<p class="sub">"최선"은 그 규모에서 어느 실행이든 찾은 가장 좋은 목적값이다(수학적 최적이 증명된 값이 아니다). "—"는 측정한 한도 안에서 1% 안에 들지 못했다는 뜻이다.
목적값은 미충원 감점(자리당 −100)이 크게 좌우하므로, 이 차이는 솔버 gap(5%)과 같은 척도가 아니다. 자리 수·기술항은 sweep.json의 unfilled_seats·skill_term에 있다.</p>
</main></body></html>"""
    out = RESULTS / "rehearsal-report.html"
    out.write_text(doc, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build())
