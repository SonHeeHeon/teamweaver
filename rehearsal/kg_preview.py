"""지식 그래프 미리보기(2026-10-07) -- 화면(claude-b) 전에 두 보기를 사용자가 직접 보고 판단하도록 정적 HTML로 그린다.

    uv run python -m rehearsal.kg_preview      # -> rehearsal/results/kg-preview.html

1. 조직 기술 지도(시연 운영 중 묶음 100명): 기술마다 실무 가능 보유자·지금 비어 있는 보유자·수요(진행·제안), 신규 제안 부족.
2. 사업별 근거 그래프(시연 연초 계획 100명, 미리 계산 안 A): 요구 기술 — 배치된 사람 — 충족/미달, 함께 일해 본 이력, 같은 산업 경험,
   "왜 다른 사람이 아니었나"(같은 등급 후보로 바꿨을 때 현행 평가기의 점수 변화·생기는 위반).
외부 라이브러리 없는 SVG. 데이터는 가상.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

from rehearsal.run import RESULTS

ROOT = Path(__file__).resolve().parents[1]
OUT = RESULTS / "kg-preview.html"


def _load(name: str):
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(ROOT / "demo" / name)
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    return b, ds, parsed, build_kg(b, ds, parsed), g, eng.skill_matrix({}), eng.synergy_matrix()


def skill_map_section() -> tuple[str, list]:
    from core.kg import skill_map
    _, _, _, kg, _, _, _ = _load("org-n100-operating")
    rows = [r for r in skill_map(kg) if r["demand_headcount"] > 0]
    width, bar = 560, 360
    mx = max(max(r["practical"], r["demand_headcount"]) for r in rows) or 1
    y, parts = 10, []
    for r in rows:
        w_sup = bar * r["practical"] / mx
        w_free = bar * r["practical_free"] / mx
        w_dem = bar * r["demand_headcount"] / mx
        color = "#c0392b" if r["proposal_gap"] > 0 else ("#e67e22" if r["scarce"] else "#2f6f5e")
        parts.append(f'<text x="0" y="{y + 13}" class="lbl">{html.escape(r["skill"])}</text>'
                     f'<rect x="170" y="{y}" width="{w_sup:.1f}" height="9" class="sup"/>'
                     f'<rect x="170" y="{y}" width="{w_free:.1f}" height="9" class="free"/>'
                     f'<rect x="170" y="{y + 11}" width="{w_dem:.1f}" height="6" fill="{color}"/>'
                     f'<text x="{176 + max(w_sup, w_dem):.1f}" y="{y + 13}" class="num">보유 {r["practical"]}(비어 있음 {r["practical_free"]}) · 수요 {r["demand_headcount"]}'
                     + (f' · <tspan class="gap">제안 부족 {r["proposal_gap"]}</tspan>' if r["proposal_gap"] else "") + "</text>")
        y += 26
    svg = f'<svg viewBox="0 0 {width + 140} {y + 6}" class="chart" role="img" aria-label="조직 기술 지도">{"".join(parts)}</svg>'
    table = "".join(
        f"<tr><td>{html.escape(r['skill'])}</td><td>{r['practical']}</td><td>{r['expert']}</td><td>{r['practical_free']}</td>"
        f"<td>{r['demand_headcount']}</td><td>{r['proposal_headcount']}</td><td>{'<b class=gap>' + str(r['proposal_gap']) + '</b>' if r['proposal_gap'] else 0}</td>"
        f"<td>{html.escape(', '.join(d['project_id'] + ('(제안)' if d['proposal'] else '') for d in r['demand'][:4]))}</td></tr>"
        for r in rows)
    return (f"""<h2>1. 조직 기술 지도 <small>운영 중 시연 묶음(100명: 90명 배치 중, 대기 10명, 신규 제안 2)</small></h2>
<p>막대 위: 실무 가능 보유자(경력 12개월 이상, 진한 부분 = 지금 비어 있는 사람). 막대 아래: 진행·제안 사업이 요구하는 인원.
<b class="gap">빨강</b> = 신규 제안이 요구하는데 지금 배치되지 않은 보유자가 모자란 기술(참고 — 투입률·가용률·요구 최소 경력은 따로 보지 않는다).
대기 인력만으로는 못 채울 수 있어 진행 사업에서 옮길 후보가 필요할 수 있다(운영 중 편성 K 비교와 이어진다).</p>
{svg}
<div class="w"><table><tr><th>기술</th><th>실무 가능</th><th>숙련(36개월+)</th><th>비어 있음</th><th>수요 인원</th><th>제안 수요</th><th>제안 부족</th><th>요구 사업</th></tr>{table}</table></div>""",
            rows)


def evidence_section(project_id: str | None = None) -> tuple[str, dict]:
    from api.settings import PlacementSettings
    from core.kg import project_evidence
    from core.optimize.types import AssignEntry
    b, ds, parsed, kg, g, S, C = _load("org-n100")
    pre = json.loads((ROOT / "demo" / "precomputed" / "org-n100.json").read_text("utf-8"))
    entries = [AssignEntry(**e) for e in pre["optimize"]["plans"][0]["entries"]]
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    if project_id is None:                          # 팀이 크고 요구 기술이 여럿인 사업
        teams = {}
        for e in entries:
            teams[e.project_id] = teams.get(e.project_id, 0) + 1
        project_id = max(teams, key=lambda j: (len(kg.out(f"project:{j}", "REQUIRES")), teams[j], j))
    ev = project_evidence(kg, project_id, entries, graph=g, S=S, C=C, params=params)
    members, reqs = ev["members"], ev["requirements"]
    W = 900
    H = max(260, 60 + 44 * max(len(members), len(reqs)))
    sx, mxp, px = 150, 470, 760
    ys = {r["skill"]: 50 + i * (H - 80) / max(1, len(reqs) - 1 if len(reqs) > 1 else 1) for i, r in enumerate(reqs)}
    if len(reqs) == 1:
        ys = {reqs[0]["skill"]: H / 2}
    ym = {m["person_id"]: 40 + i * (H - 70) / max(1, len(members) - 1) for i, m in enumerate(members)}
    parts = []
    for m in members:                               # 사람 → 요구 기술(충족 초록·미달 주황·없음 점선)
        for c in m["requirements"]:
            col = {"met": "#2f8f5e", "below": "#e67e22", "missing": "#b9b9b9"}[c["status"]]
            dash = ' stroke-dasharray="4 4"' if c["status"] == "missing" else ""
            parts.append(f'<line x1="{sx}" y1="{ys[c["skill"]]:.1f}" x2="{mxp}" y2="{ym[m["person_id"]]:.1f}" stroke="{col}" stroke-width="{2 if c["status"] == "met" else 1}"{dash} opacity=".8"/>')
    for m in members:                               # 팀 안 협업 이력(호)
        for cw in m["cowork_in_team"]:
            if cw["with"] > m["person_id"] and cw["with"] in ym:
                y1, y2 = ym[m["person_id"]], ym[cw["with"]]
                bend = 40 + 4 * min(cw["months_total"] or 0, 12)
                parts.append(f'<path d="M{mxp + 70},{y1:.1f} C{mxp + 70 + bend},{y1:.1f} {mxp + 70 + bend},{y2:.1f} {mxp + 70},{y2:.1f}" fill="none" stroke="#3b6fb6" stroke-width="{1 + (cw["months_total"] or 0) / 6:.1f}" opacity=".5"/>')
    for r in reqs:
        col = "#2f8f5e" if r["status"] == "met" else "#c0392b"
        parts.append(f'<rect x="{sx - 140}" y="{ys[r["skill"]] - 14:.1f}" width="140" height="28" rx="6" fill="var(--card)" stroke="{col}"/>'
                     f'<text x="{sx - 132}" y="{ys[r["skill"]] + 4:.1f}" class="lbl">{html.escape(r["skill"])} {r["met_by"]}/{r["headcount"]}</text>')
    for m in members:
        y = ym[m["person_id"]]
        badge = f' · 같은 산업 {m["same_industry_projects"]}건' if m["same_industry_projects"] else ""
        parts.append(f'<rect x="{mxp}" y="{y - 14:.1f}" width="70" height="28" rx="14" fill="var(--acc)"/>'
                     f'<text x="{mxp + 35}" y="{y + 4:.1f}" class="node">{html.escape(m["person_id"])}</text>'
                     f'<text x="{px - 20}" y="{y + 4:.1f}" class="num">{html.escape(m["grade"] or "")} · 적합 {m["skill_fit"]}{badge}'
                     + (f' · 같은 고객사 {len(m["same_client_projects"])}건' if m["same_client_projects"] else "") + "</text>")
    svg = f'<svg viewBox="0 0 {W + 200} {H}" class="chart" role="img" aria-label="사업별 근거 그래프">{"".join(parts)}</svg>'
    alt_rows = ""
    for m in members:
        alts = ev.get("alternatives", {}).get(m["person_id"], [])
        if not alts:
            continue
        a = alts[0]
        why = ("바꾸면 " + ", ".join(v.split(":")[0] for v in a["new_violations"][:3]) + " 위반") if a["new_violations"] else \
              ("바꾸면 점수 " + (f"+{a['delta_total']:.2f} — 이 사람이 더 낫다(숨기지 않음)" if a["delta_total"] > 0 else f"{a['delta_total']:.2f}"))
        alt_rows += (f"<tr><td>{html.escape(m['person_id'])}</td><td>{html.escape(a['person_id'])} (적합 {a['skill_fit']})</td>"
                     f"<td>{a['delta_skill']:+.2f}</td><td>{a['delta_synergy']:+.2f}</td><td>{html.escape(why)}</td></tr>")
    return (f"""<h2>2. 사업별 근거 그래프 <small>{html.escape(ev['project'])} — 연초 계획 100명, 미리 계산 안 A</small></h2>
<p>고객사 {html.escape(ev['client'] or '—')}{'(사업 이름에서 추정)' if ev.get('client_inferred') else ''} · 산업 {html.escape(ev['industry'] or '—')}. 왼쪽 요구 기술(충족 인원/필요 인원) — 가운데 배치된 사람.
선: <span style="color:#2f8f5e">충족</span>·<span style="color:#e67e22">미달</span>·<span style="color:#888">없음(점선)</span>. 오른쪽 파란 호 = 팀 안에서 함께 일해 본 이력(굵을수록 길게).</p>
{svg}
<h3>왜 다른 사람이 아니었나 <small>같은 등급 후보 중 기술 적합 상위 5명으로 바꿔 현행 평가기로 잰 결과(가장 나은 대안 1명)</small></h3>
<div class="w"><table><tr><th>배치된 사람</th><th>가장 나은 대안</th><th>기술 변화</th><th>협업 변화</th><th>결과</th></tr>{alt_rows}</table></div>""", ev)


def main() -> None:
    sm_html, sm = skill_map_section()
    ev_html, ev = evidence_section()
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>지식 그래프 미리보기</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#aaa;--acc:#3f7ab0;--line:#3a3a3a;--card:#23272b}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1100px;margin:auto}}h2{{color:var(--acc);margin-top:1.8em}}small{{color:var(--muted);font-weight:400}}
.chart{{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:.6em 0}}
.lbl{{font-size:12px;fill:var(--fg)}}.num{{font-size:11px;fill:var(--muted)}}.node{{font-size:11px;fill:#fff;text-anchor:middle}}
.sup{{fill:#9db7d4}}.free{{fill:var(--acc)}}.gap{{color:#c0392b;fill:#c0392b;font-weight:600}}
.w{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left}}
th{{color:var(--muted);font-weight:600}}</style></head><body><main>
<h1>지식 그래프 미리보기 — 그래프 하나, 보기 둘</h1>
<p>사람·기술·사업·과거 사업·고객사·산업·평가를 묶은 그래프 하나(core/kg) 위에서 두 질문에 답한 화면 시안이다. 실제 화면은 claude-b가 만든다.
데이터는 가상이고, 점수 변화는 현행 평가기 기준의 계산값이다(사업 효과 NOT_CALIBRATED).</p>
{sm_html}{ev_html}</main></body></html>"""
    OUT.write_text(page, "utf-8")
    print(f"written {OUT}")
    print("project", ev["project_id"], "members", len(ev["members"]), "skills with demand", len(sm))


if __name__ == "__main__":
    main()
