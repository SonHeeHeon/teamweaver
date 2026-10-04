"""Render recorded Phase 1 evidence without inferring unrecorded success."""

from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
import math
from pathlib import Path
from typing import Any
from experiments.phase1.evidence import assess_record, classify_case, summarize_stages

from experiments.phase1.checkpoint import (
    CheckpointCorrupt,
    attempt_directory,
    fingerprint,
    read_bound_checkpoint,
)


CSP = ("default-src 'none'; base-uri 'none'; form-action 'none'; img-src data:; "
       "font-src data:; media-src data:; style-src 'unsafe-inline'; "
       "script-src 'unsafe-inline'")


def _text(value: Any) -> str:
    return escape("—" if value is None else str(value), quote=True)


def _number(value: Any, digits: int = 6) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    return f"{number:.{digits}f}" if math.isfinite(number) else "INVALID"


def _gap(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    return f"{number * 100:.4f}%" if math.isfinite(number) else "INVALID"


def _json(value: Any) -> str:
    return _text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(", ", ": ")))


def _load_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    run_dir = Path(run_dir)
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise CheckpointCorrupt("manifest is missing or malformed") from exc
    checkpoint = read_bound_checkpoint(run_dir, manifest)
    rows = []
    for case_id, case_row in checkpoint["cases"].items():
        try:
            attempt = case_row["attempt"]
            expected_path = attempt_directory(run_dir, case_id, attempt) / "result.json"
            recorded_path = run_dir / case_row.get(
                "result_path", str(expected_path.relative_to(run_dir)))
            if recorded_path.resolve() != expected_path.resolve():
                raise ValueError("result path differs from the reserved attempt")
            result = None
            if case_row["status"] != "RUNNING":
                result = json.loads(expected_path.read_text(encoding="utf-8"))
                if fingerprint(result) != case_row["result_sha256"]:
                    raise ValueError("terminal result checksum mismatch")
                if result.get("case_id", case_id) != case_id:
                    raise ValueError("terminal result belongs to another case")
            rows.append({"case_id": case_id, "checkpoint": case_row, "result": result,
                         "raw_candidate_available": (expected_path.parent / "raw-solution.json").is_file()})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CheckpointCorrupt(f"terminal result missing or changed: {case_id}") from exc
    return manifest, checkpoint, rows


def _overall_status(manifest: dict[str, Any], checkpoint: dict[str, Any]) -> str:
    if not checkpoint["cases"]:
        return "DESIGNED / NOT_RUN"
    scheduled = {row["case_id"] for row in manifest.get("schedule", [])}
    recorded = set(checkpoint["cases"])
    if checkpoint["status"] == "COMPLETE" and scheduled and recorded == scheduled:
        return "COMPLETE"
    return "PARTIAL"


def _case_table(rows: list[dict[str, Any]]) -> str:
    rendered = []
    for item in rows:
        case_id = item["case_id"]
        row = item["checkpoint"]
        result = item["result"] or {}
        payload = result.get("payload", {})
        case = row.get("case", {})
        assessment = assess_record(item)
        validation = payload.get("validation", {})
        issues = validation.get("issues", [])
        evidence = payload.get("evidence", {})
        rendered.append(f"""
        <tr>
          <td><code>{_text(case_id)}</code></td>
          <td>{_text(case.get('stage'))}</td>
          <td>{_text(case.get('solver_name', payload.get('solver_name')))}</td>
          <td>{_text(row.get('status'))}</td>
          <td>{_text(payload.get('quality_status'))}</td>
          <td class="num">{_number(payload.get('objective'))}</td>
          <td class="num">{_number(payload.get('best_bound'))}</td>
          <td class="num">{_gap(payload.get('normalized_gap'))}</td>
          <td>{_text(assessment.validation_state)} / {_text(assessment.evidence_source)}</td>
          <td>{_text(evidence.get('native_status'))}</td>
          <td class="num">{_number(result.get('elapsed_seconds'), 3)}</td>
          <td>{_text(assessment.failure_category)}: {_text('; '.join(assessment.error_messages) or ','.join(assessment.issue_codes))}</td>
        </tr>""")
    if not rendered:
        return '<tr><td colspan="12">기록된 실행 결과가 없습니다.</td></tr>'
    return "".join(rendered)


def _solver_table(manifest: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    result_unavailable = {
        item["checkpoint"].get("case", {}).get("solver_name")
        for item in rows if item["checkpoint"].get("status") == "UNAVAILABLE"
    }
    rendered = []
    for name, record in manifest.get("solvers", {}).items():
        observed = "UNAVAILABLE" if name in result_unavailable else "—"
        rendered.append(f"""
        <tr><td><strong>{_text(name)}</strong></td><td>{_text(record.get('state'))}</td>
        <td>{_text(record.get('version'))}</td><td><code>{_text(record.get('import_name'))}</code></td>
        <td>{_text(observed)}</td><td>{_text(record.get('error'))}</td></tr>""")
    return "".join(rendered) or '<tr><td colspan="6">솔버 정보가 기록되지 않았습니다.</td></tr>'


def render_report(run_dir: Path, output_path: Path) -> Path:
    run_dir = Path(run_dir)
    output_path = Path(output_path)
    manifest, checkpoint, rows = _load_run(run_dir)
    overall = _overall_status(manifest, checkpoint)
    counts = Counter(item["checkpoint"]["status"] for item in rows)
    quality_counts = Counter(
        (item["result"] or {}).get("payload", {}).get("quality_status", "NOT_RECORDED")
        for item in rows
    )
    validation_failures = sum(assess_record(item).validation_state == 'FAIL' for item in rows)
    stages = summarize_stages(manifest, rows)
    stage_rows = ''.join(f'<tr><td>{_text(solver)}</td><td>{_text(stage)}</td>'
                         + ''.join(f'<td>{values[key]}</td>' for key in ('planned', 'recorded', 'done', 'validated', 'quality_pass', 'validation_failures'))
                         + f'<td>{_json(values["failure_categories"])}</td><td>{_json(values["issue_codes"])}</td></tr>'
                         for solver, groups in sorted(stages.items()) for stage, values in sorted(groups.items()))
    unavailable = sorted({
        name for name, record in manifest.get("solvers", {}).items()
        if record.get("state") == "UNAVAILABLE"
    } | {
        item["checkpoint"].get("case", {}).get("solver_name")
        for item in rows if item["checkpoint"].get("status") == "UNAVAILABLE"
    } - {None})
    manifest_sha = fingerprint(manifest)
    status_badge = "ok" if overall == "COMPLETE" else "warn"
    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TeamWeaver Phase 1 솔버 비교 증거 보고서</title>
<style>
:root{{--paper:#f5f3ed;--ink:#182033;--muted:#667085;--line:#cbd2df;--card:#fff;--blue:#235ee7;--green:#176b48;--amber:#9b5b00;--red:#ae3030}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{width:min(1200px,calc(100% - 28px));margin:auto;padding:42px 0 70px}} h1{{font-size:clamp(34px,5vw,58px);line-height:1.06;margin:8px 0 14px;letter-spacing:-.035em}} h2{{margin:0 0 14px;font-size:25px}} p{{margin:7px 0}} .lead{{font-size:19px;color:var(--muted);max-width:850px}}
.badge{{display:inline-block;padding:7px 12px;border-radius:999px;font-weight:850;background:#fff0cf;color:var(--amber)}} .badge.ok{{background:#ddf5e9;color:var(--green)}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:28px 0}} .card,section{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px}} .card strong{{display:block;font-size:28px}} section{{margin:16px 0}}
.boundary{{border-left:8px solid var(--red);background:#fff1ef}} .boundary strong{{color:var(--red);font-size:24px}} .tablewrap{{overflow:auto;border:1px solid var(--line);border-radius:12px}} table{{width:100%;border-collapse:collapse;min-width:760px;background:#fff}} th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #e1e5ec;vertical-align:top}} th{{position:sticky;top:0;background:#e9efff;color:#25324b;white-space:nowrap}} td.num{{font-variant-numeric:tabular-nums;text-align:right}} code{{background:#edf0f5;padding:2px 5px;border-radius:5px;overflow-wrap:anywhere}}
.meta{{display:grid;grid-template-columns:190px 1fr;gap:8px 16px}} .meta dt{{font-weight:800}} .meta dd{{margin:0;overflow-wrap:anywhere}} .note{{color:var(--muted)}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr 1fr}}.meta{{grid-template-columns:1fr}}.meta dd{{margin-bottom:8px}}}} @media(max-width:440px){{.grid{{grid-template-columns:1fr}}main{{width:min(100% - 18px,1200px)}}}}
</style></head><body><main>
<div class="badge {status_badge}">{_text(overall)}</div>
<h1>Phase 1 솔버 비교 증거 보고서</h1>
<p class="lead">이 페이지는 저장된 체크포인트와 해시가 일치하는 결과만 보여줍니다. 기록되지 않은 성공이나 성능을 추정하지 않습니다.</p>
<div class="grid">
  <div class="card"><span>기록된 케이스</span><strong>{len(rows)}</strong></div>
  <div class="card"><span>활성 실행 시간</span><strong>{_number(checkpoint['active_seconds'], 3)}초</strong></div>
  <div class="card"><span>검증 실패</span><strong>{validation_failures}</strong></div>
  <div class="card"><span>사용 불가 솔버</span><strong>{len(unavailable)}</strong><small>{_text(', '.join(unavailable) or '없음')}</small></div>
</div>
<section class="boundary"><strong>{_text(manifest.get('business_validity', 'NOT_CALIBRATED'))}</strong>
<p>이 결과는 가상 입력에서 구현 정확도와 솔버 실행 특성을 비교하는 기술 실험입니다. 실제 인력 배치가 고객 만족, 프로젝트 성과, 인력 교체 감소로 이어진다는 현실 성과 검증은 아닙니다.</p></section>
<section><h2>실행 상태 요약</h2><p><b>터미널 상태:</b> {_json(dict(sorted(counts.items())))}</p><p><b>품질 상태:</b> {_json(dict(sorted(quality_counts.items())))}</p><p><b>체크포인트 상태:</b> {_text(checkpoint['status'])}</p><p class="note">완료 표시는 사전 등록 일정 전체가 기록되고 체크포인트도 COMPLETE일 때만 나옵니다.</p></section>
<section><h2>솔버·버전·가용성</h2><div class="tablewrap"><table><thead><tr><th>솔버</th><th>사전 상태</th><th>버전</th><th>연결 방식</th><th>실행 관찰</th><th>오류</th></tr></thead><tbody>{_solver_table(manifest, rows)}</tbody></table></div></section>
<section><h2>단계별 분모와 실패 원인</h2><p>core는 primary+confirmation입니다. oracle·pilot·compatibility는 별도 집계하며 미실행·실패·중단도 예정 분모에 남깁니다. DONE·검증 통과·품질 통과는 서로 다른 수치입니다.</p>
<div class="tablewrap"><table><thead><tr><th>솔버</th><th>단계</th><th>예정</th><th>기록</th><th>DONE</th><th>검증 통과</th><th>품질 통과</th><th>검증 실패</th><th>실패 범주</th><th>이슈</th></tr></thead><tbody>{stage_rows}</tbody></table></div>
<p class="note">반복 측정은 독립 고객 사례가 아닙니다. 완료 사례만의 속도는 생존 편향이 있으며 native Optimal만으로 검증 통과를 추정하지 않습니다. 과거 거절 원시 해가 없으면 잔차·원인 복원은 불가능합니다.</p></section>
<section><h2>고정 실행 조건과 신원</h2><dl class="meta">
<dt>매니페스트 SHA-256</dt><dd><code>{_text(manifest_sha)}</code></dd>
<dt>체크포인트의 SHA-256</dt><dd><code>{_text(checkpoint['manifest_sha256'])}</code></dd>
<dt>소스 커밋</dt><dd><code>{_text(manifest.get('source_commit'))}</code></dd>
<dt>의존성 잠금 해시</dt><dd><code>{_text(manifest.get('dependency_lock_sha256'))}</code></dd>
<dt>옵션</dt><dd><code>{_json(manifest.get('options', {}))}</code></dd>
<dt>의존성 버전</dt><dd><code>{_json(manifest.get('dependency_versions', {}))}</code></dd>
<dt>생성 / 갱신</dt><dd>{_text(checkpoint['created_at'])} / {_text(checkpoint['updated_at'])}</dd>
</dl></section>
<section><h2>케이스별 L / U / Gap / 검증</h2><p class="note">L은 찾은 배치의 점수, U는 솔버가 제공한 최고 가능 상한입니다. U가 없으면 Gap도 계산하지 않고 BOUND_UNKNOWN으로 남깁니다.</p>
<div class="tablewrap"><table><thead><tr><th>케이스</th><th>단계</th><th>솔버</th><th>실행 상태</th><th>품질 상태</th><th>L</th><th>U</th><th>Gap</th><th>독립 검증</th><th>원본 상태</th><th>초</th><th>오류·검증 이슈</th></tr></thead><tbody>{_case_table(rows)}</tbody></table></div></section>
<section><h2>해석 제한</h2><p>이 보고서는 실행 기록의 투명성을 높이지만, 가상 데이터가 실제 조직을 대표한다고 보장하지 않습니다. 솔버 간 속도 비교도 같은 장비·같은 입력·같은 제한에서 기록된 케이스 범위 안에서만 해석해야 합니다.</p></section>
</main></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    render_report(args.run_dir, args.output_path)
    print(args.output_path)


if __name__ == "__main__":
    main()
