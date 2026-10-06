"""실험 G: '오래 붙어 다닌 사이' 감점(쌍) vs 파트너 다양성 하한(사람) -- 같은 데이터·같은 설정으로 비교(2026-10-06).

    TEAMWEAVER_SOLVER_SEEDS=4 uv run --group benchmark python -m rehearsal.partner_compare   # -> rehearsal/results/partner-compare.{json,html}

배경(docs/demo-notes.md 7절): 최신 근거(Akşin 외 2021, Management Science)는 "익숙함이 해롭다"가 아니라 "새 파트너와 섞일
기회가 성과를 높인다"고 본다. 지금 모델은 익숙한 두 사람(최근 36개월 중 12개월 이상)이 같은 사업에 들어갈 때마다 감점한다.
대안은 사람 단위 하한이다: 최근 파트너가 있는 사람은 팀 안에 새 동료가 m명 이상 있어야 하고, 모자라는 명수만큼 감점.

설정 넷(나머지는 서비스 기본값, 안 A만):
  none     감점 없음(μ=0)
  pair     지금 서비스(μ=0.2 쌍 감점)
  floor1   μ=0 + 하한 m=1(감점 0.2/명)
  floor2   μ=0 + 하한 m=2(감점 0.2/명)
데이터: 시연 연초 계획 묶음 100/200/300명(리뷰 글은 항목 균형 판정 -- 설정 간 비교라 같은 입력이면 된다).
사업 효과는 NOT_CALIBRATED: 어느 쪽이 실제 성과에 좋은지는 이 측정으로 말할 수 없다. 계산 비용과 편성 모양의 차이만 본다.
"""
from __future__ import annotations

import argparse
import html
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

from rehearsal.run import RESULTS, _env_info, _now

DEMO = Path(__file__).resolve().parents[1] / "demo"
OUT = RESULTS / "partner-compare.json"
CONFIGS = {
    "none": {"mu": 0.0},
    "pair": {},
    "floor1": {"mu": 0.0, "partner_floor": 1, "partner_floor_weight": 0.2},
    "floor2": {"mu": 0.0, "partner_floor": 2, "partner_floor_weight": 0.2},
}
LABELS = {"none": "감점 없음", "pair": "지금(쌍 감점 μ=0.2)", "floor1": "하한 m=1", "floor2": "하한 m=2"}


def _bundle(n: int) -> Path:
    if n == 100:
        return DEMO / "org-n100"
    d = Path(tempfile.mkdtemp(prefix=f"partner-n{n}-"))
    zipfile.ZipFile(io.BytesIO((DEMO / f"org-n{n}.zip").read_bytes())).extractall(d)
    return d                                    # 실행이 끝나면 run()이 지운다


def shape(graph, params, entries) -> dict:
    """편성 모양: 익숙한 쌍이 같은 팀인 수, 사람별 새 파트너 수(최근 파트너가 아닌 팀 동료)."""
    from core.optimize.milp import _overfamiliar_pairs, partner_map
    pairs = _overfamiliar_pairs(graph, params.clique_threshold_months, params.clique_window_months)
    partners = partner_map(pairs)
    teams: dict[str, set[int]] = {}
    for e in entries:
        teams.setdefault(e.project_id, set()).add(graph.pid_index[e.person_id])
    together = sum(1 for p, q in pairs for t in teams.values() if p in t and q in t)
    new, familiar, zero_new, with_partner_rows = [], [], 0, 0
    for t in teams.values():
        for i in t:
            fam = len(partners.get(i, set()) & t)
            n_new = len(t) - 1 - fam
            new.append(n_new)
            familiar.append(fam)
            if i in partners:
                with_partner_rows += 1
                zero_new += n_new == 0
    rows = len(new)
    return {"familiar_pairs_total": len(pairs), "familiar_pairs_together": together,
            "people_with_recent_partners": len(partners),
            "avg_new_partners": round(sum(new) / rows, 3) if rows else None,
            "avg_familiar_teammates": round(sum(familiar) / rows, 3) if rows else None,
            "share_no_new_partner": round(zero_new / with_partner_rows, 4) if with_partner_rows else None,
            "assignments": rows}


def run(n: int) -> list[dict]:
    from api.settings import PlacementSettings
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.milp import solve_milp_assessment
    from core.scoring.engine import ScoringEngine
    import shutil
    root = _bundle(n)
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep)
    if n != 100:
        shutil.rmtree(root, ignore_errors=True)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    base = PlacementSettings().to_milp_params(n_people=len(ds.people))
    out = []
    for name, over in CONFIGS.items():
        params = base.model_copy(update=over)
        t = time.perf_counter()
        try:
            a = solve_milp_assessment(graph, S, C, params)
        except RuntimeError as exc:
            out.append({"size": n, "config": name, "accepted": False, "error": str(exc)[:300],
                        "elapsed_s": round(time.perf_counter() - t, 2)})
            continue
        elapsed = round(time.perf_counter() - t, 2)
        ev_cap = a.native_capture.evidence if a.native_capture is not None else None
        row = {"size": n, "config": name, "label": LABELS[name], "params": over, "elapsed_s": elapsed,
               "accepted": a.accepted is not None, "termination": getattr(ev_cap, "termination_reason", None),
               "best_bound": None if ev_cap is None else ev_cap.best_bound,
               "variables": a.native_capture.variable_count if a.native_capture is not None else None}
        if a.accepted is not None:
            plan = a.accepted.plan
            ev = evaluate_plan(graph, S, C, params, plan.entries)
            o = ev.objective
            row.update({"objective": round(plan.objective, 4),
                        "gap": (round(abs(row["best_bound"] - plan.objective) / abs(plan.objective), 4)
                                if row["best_bound"] is not None and plan.objective else None),
                        "quality": round(o.skill + o.synergy, 4), "skill": round(o.skill, 4),
                        "synergy": round(o.synergy, 4), "familiarity_term": round(o.overfamiliarity, 4),
                        "unfilled_seats": int(round(-o.unfilled / params.slack_penalty)),
                        **shape(graph, params, plan.entries)})
        print(json.dumps({k: row.get(k) for k in ("size", "config", "elapsed_s", "termination", "quality",
                                                    "unfilled_seats", "familiar_pairs_together", "avg_new_partners",
                                                    "share_no_new_partner")}, ensure_ascii=False), flush=True)
        out.append(row)
    return out


def render(runs: list[dict]) -> str:
    def cell(v, f="{}"):
        return "—" if v is None else f.format(v)
    body = ""
    for n in sorted({r["size"] for r in runs}):
        rows = [r for r in runs if r["size"] == n]
        body += f"<h2>{n}명</h2><div class='w'><table><tr><th>설정</th><th>시간</th><th>종료</th><th>배치 품질<br>(기술+협업)</th>" \
                "<th>빈자리</th><th>익숙한 쌍<br>같은 팀</th><th>배치당<br>새 파트너</th><th>새 파트너 0명<br>(파트너 있는 배치 중)</th></tr>"
        for r in rows:
            body += (f"<tr><td>{html.escape(r.get('label', r['config']))}</td><td>{cell(r.get('elapsed_s'), '{:.1f}초')}</td>"
                     f"<td>{html.escape(str(r.get('termination')))}</td><td>{cell(r.get('quality'), '{:.2f}')}</td>"
                     f"<td>{cell(r.get('unfilled_seats'))}</td><td>{cell(r.get('familiar_pairs_together'))}</td>"
                     f"<td>{cell(r.get('avg_new_partners'), '{:.2f}')}</td>"
                     f"<td>{cell(None if r.get('share_no_new_partner') is None else 100 * r['share_no_new_partner'], '{:.1f}%')}</td></tr>")
        body += "</table></div>"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>익숙함 감점 재설계 비교</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#aaa;--acc:#8ab8e0;--line:#3a3a3a}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:24px 16px}}
main{{max-width:900px;margin:auto}}h2{{color:var(--acc)}}.w{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right}}
th:first-child,td:first-child{{text-align:left}}th{{color:var(--muted);font-weight:600}}</style></head><body><main>
<h1>'오래 붙어 다닌 사이' 감점: 쌍 감점 vs 파트너 다양성 하한</h1>
<p>같은 데이터(시연 연초 계획 묶음)·같은 서비스 설정, 안 A 한 번씩. 익숙한 쌍 = 최근 36개월 중 12개월 이상 함께 일한 두 사람.
하한 m = 최근 파트너가 있는 사람은 팀 안에 새 동료가 m명 이상 있어야 함(모자라는 명수 × 0.2 감점).
배치 품질은 감점 항을 뺀 기술+협업이다. "새 파트너"는 배치(사람×사업) 한 줄마다 센다(두 사업에 든 사람은 두 번).
종료가 "time_limit_incumbent"면 시간 한도에서 멈춘 해라, 설정 간 품질 차이 일부는 모델 차이가 아니라 풀이가 덜 끝난 차이일 수 있다.
<b>사업 효과는 검증 전(NOT_CALIBRATED)</b> — 계산 비용과 편성 모양의 차이만 보여 준다.</p>
{body}<p>원자료: rehearsal/results/partner-compare.json · 도구: rehearsal/partner_compare.py</p></main></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 200, 300])
    args = ap.parse_args()
    runs = []
    for n in args.sizes:
        runs += run(n)
    OUT.write_text(json.dumps({"env": _env_info(), "finished_at": _now(), "configs": CONFIGS, "runs": runs},
                              ensure_ascii=False, indent=1), "utf-8")
    OUT.with_suffix(".html").write_text(render(runs), "utf-8")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
