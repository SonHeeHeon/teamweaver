"""Render the recorded Phase 0 evidence as a self-contained Korean HTML page."""
import argparse
import html
import json
from pathlib import Path
from typing import Any


def _text(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return _text(value)


def _status(value: Any) -> str:
    text = _text(value)
    css = "pass" if value in ("PASS", True, "Optimal") else "hold"
    if value in ("FAIL", False):
        css = "fail"
    return f'<span class="status {css}">{text}</span>'


def _case_rows(cases: list[dict]) -> str:
    rows = []
    for case in cases:
        rows.append(
            "<tr>"
            f"<td>{_text(case.get('name', '—'))}</td>"
            f"<td>{_status(case.get('passed', False))}</td>"
            f"<td>{_number(case.get('cbc_objective'))}</td>"
            f"<td>{_number(case.get('oracle_objective'))}</td>"
            f"<td>{_number(case.get('objective_difference'), 8)}</td>"
            f"<td>{_status(case.get('validation_valid', False))}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="6">기록된 작은 문제 없음</td></tr>'


def _failure_rows(failures: list[dict]) -> str:
    if not failures:
        return '<p class="quiet">실패 기록 없음</p>'
    return "<ul>" + "".join(
        f"<li><b>{_text(item.get('name', 'unknown'))}</b> — {_text(item.get('reason', ''))}</li>"
        for item in failures
    ) + "</ul>"


def render_report(result: dict, output: Path) -> Path:
    """Write a direct-openable report; all runtime text is HTML-escaped."""
    result = result.get("data", result)
    cases = result.get("cases", [])
    invariants = result.get("invariants", [])
    pair_cap = result.get("pair_cap") or {}
    smoke = result.get("smoke") or {}
    failures = result.get("failures", [])
    invariant_rows = "".join(
        "<tr>"
        f"<td>{_text(row.get('name', '—'))}</td>"
        f"<td>{_number(row.get('base_objective'))}</td>"
        f"<td>{_number(row.get('raised_objective'))}</td>"
        f"<td>{_status(row.get('passed', False))}</td>"
        "</tr>"
        for row in invariants
    ) or '<tr><td colspan="4">기록 없음</td></tr>'
    pair_loss = _number(pair_cap.get("relative_loss_pct"), 3)
    smoke_size = " × ".join(
        [_number(smoke.get("people"), 0), _number(smoke.get("projects"), 0)]
    ) if smoke else "—"
    report = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; img-src data:; font-src data:; media-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>TeamWeaver Phase 0 검증 결과</title>
<style>
:root {{ color-scheme: light; --ink:#172033; --muted:#5d6878; --line:#d8e0ea; --paper:#f6f8fb; --blue:#2667d8; --green:#18794e; --amber:#9a6200; --red:#ba2d35; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--paper); color:var(--ink); font-family:ui-sans-serif,system-ui,sans-serif; line-height:1.45; }}
main {{ max-width:980px; margin:auto; padding:48px 20px 72px; }} h1 {{ font-size:clamp(2rem,5vw,3.5rem); line-height:1.05; margin:0 0 12px; }} h2 {{ margin:0 0 14px; font-size:1.45rem; }}
.eyebrow {{ color:var(--blue); font-weight:800; letter-spacing:.08em; font-size:.76rem; }} .lead {{ color:var(--muted); max-width:720px; font-size:1.08rem; }}
.verdicts {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin:28px 0; }} .verdict,.panel {{ background:#fff; border:1px solid var(--line); border-radius:18px; padding:20px; }}
.verdict h2 {{ font-size:1rem; color:var(--muted); }} .big {{ font-size:2rem; font-weight:850; margin:.15em 0; }} .pass-text {{ color:var(--green); }} .hold-text {{ color:var(--amber); }}
.flow {{ display:grid; grid-template-columns:repeat(4,1fr); align-items:center; gap:8px; margin:20px 0 30px; }} .step {{ min-height:112px; border:2px solid var(--blue); background:#fff; border-radius:14px; padding:14px; font-weight:750; position:relative; }} .step b {{ display:block; color:var(--blue); font-size:1.6rem; }} .arrow {{ height:2px; background:var(--blue); position:relative; }} .arrow:after {{ content:""; position:absolute; right:-1px; top:-4px; border-left:8px solid var(--blue); border-top:5px solid transparent; border-bottom:5px solid transparent; }}
.grid {{ display:grid; grid-template-columns:1.1fr .9fr; gap:18px; }} table {{ width:100%; border-collapse:collapse; font-size:.9rem; }} th,td {{ padding:10px 8px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }} th {{ color:var(--muted); font-size:.76rem; text-transform:uppercase; }}
.status {{ display:inline-block; padding:3px 8px; border-radius:999px; font-size:.75rem; font-weight:800; }} .pass {{ background:#dff6e9; color:var(--green); }} .hold {{ background:#fff0ce; color:var(--amber); }} .fail {{ background:#fde4e5; color:var(--red); }}
.metric {{ display:grid; grid-template-columns:1fr auto; gap:8px; padding:10px 0; border-bottom:1px solid var(--line); }} .metric:last-child {{ border-bottom:0; }} .quiet {{ color:var(--muted); }} ul {{ margin:0; padding-left:20px; }} details {{ margin-top:12px; }} summary {{ cursor:pointer; font-weight:700; }}
@media (max-width:700px) {{ main {{ padding:32px 14px 56px; }} .verdicts,.grid {{ grid-template-columns:1fr; }} .flow {{ grid-template-columns:1fr; }} .arrow {{ width:2px; height:16px; margin:auto; }} .arrow:after {{ right:-4px; top:10px; border-left:5px solid transparent; border-right:5px solid transparent; border-top:8px solid var(--blue); border-bottom:0; }} .step {{ min-height:82px; }} .scroll {{ overflow-x:auto; }} }}
@media (prefers-reduced-motion:reduce) {{ * {{ scroll-behavior:auto !important; transition:none !important; }} }}
</style>
</head>
<body><main>
<p class="eyebrow">TEAMWEAVER · PHASE 0</p>
<h1>모델이 약속한 계산을<br>제대로 하는지 확인했습니다</h1>
<p class="lead">이 페이지는 계산 검증입니다. 실제 고객 만족이나 프로젝트 성과를 예측했다는 뜻은 아닙니다.</p>
<section class="verdicts" aria-label="두 종류의 판정">
<article class="verdict"><h2>계산 검증</h2><p class="big {'pass-text' if result.get('calculation_status') == 'PASS' else 'hold-text'}">{_text(result.get('calculation_status', '—'))}</p><p>수식 · 제약 · 작은 정답기 대조</p></article>
<article class="verdict"><h2>사업 성과 검증</h2><p class="big hold-text">{_text(result.get('business_validity', 'NOT_CALIBRATED'))}</p><p>실제 성과를 검증한 결과가 아닙니다</p></article>
</section>
<section class="panel"><h2>무엇을 거쳤나</h2><div class="flow" aria-label="작은 문제부터 독립 검증까지의 흐름"><div class="step"><b>1</b>작은 문제</div><div class="arrow" aria-hidden="true"></div><div class="step"><b>2</b>CBC와 정답기</div><div class="arrow" aria-hidden="true"></div><div class="step"><b>3</b>별도 검증기</div><div class="arrow" aria-hidden="true"></div><div class="step"><b>4</b>PASS 또는 FAIL</div></div></section>
<section class="grid"><article class="panel"><h2>작은 문제 정답 대조</h2><div class="scroll"><table><thead><tr><th>케이스</th><th>판정</th><th>CBC</th><th>정답기</th><th>차이</th><th>제약</th></tr></thead><tbody>{_case_rows(cases)}</tbody></table></div></article>
<article class="panel"><h2>현재 범위</h2><div class="metric"><span>솔버</span><b>{_text(result.get('solver', 'CBC'))}</b></div><div class="metric"><span>총 시간</span><b>{_number(result.get('elapsed_seconds'), 2)}초</b></div><div class="metric"><span>입력</span><b>합성 데이터</b></div><div class="metric"><span>사업 효과</span><b>아직 미검증</b></div></article></section>
<section class="grid" style="margin-top:18px"><article class="panel"><h2>단조성 검사</h2><div class="scroll"><table><thead><tr><th>조건 완화</th><th>전</th><th>후</th><th>판정</th></tr></thead><tbody>{invariant_rows}</tbody></table></div></article>
<article class="panel"><h2>pair 절삭 관찰</h2><div class="metric"><span>전체 pair</span><b>{_number(pair_cap.get('full_reward_pairs'), 0)}</b></div><div class="metric"><span>남긴 pair</span><b>{_number(pair_cap.get('capped_reward_pairs'), 0)}</b></div><div class="metric"><span>full 기준 손실</span><b>{pair_loss}%</b></div><div class="metric"><span>제약 재검사</span>{_status(pair_cap.get('validation_valid', pair_cap.get('passed', False)))}</div></article></section>
<section class="panel" style="margin-top:18px"><h2>CBC 호환성 스모크</h2><div class="metric"><span>규모</span><b>{smoke_size}</b></div><div class="metric"><span>상태</span>{_status(smoke.get('status', '—'))}</div><div class="metric"><span>독립 제약 검사</span>{_status(smoke.get('validation_valid', False))}</div><div class="metric"><span>총 시간</span><b>{_number((smoke.get('timings_seconds') or {{}}).get('total'), 2)}초</b></div></section>
<section class="panel" style="margin-top:18px"><h2>실패 기록</h2>{_failure_rows(failures)}<details><summary>이 결과를 어떻게 써야 하나요?</summary><p>계산 PASS는 현재 수식과 합성 입력에서 코드가 일관되게 동작했다는 증거입니다. 실제 인력 배치의 효과, 고객 평가, 교체 위험은 독립된 실제 결과 데이터가 있어야 검증할 수 있습니다.</p></details></section>
<p class="quiet">경계: {_text(result.get('data_boundary', '합성 입력만 사용했습니다.'))}</p>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    render_report(payload, args.output)
    print(f"rendered: {args.output}")


if __name__ == "__main__":
    main()
