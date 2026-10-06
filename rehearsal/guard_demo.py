"""시연용 'AI가 지어낸 근거를 막는' 장면 — 정해 둔 AI 응답 세 가지를 검증 장치에 넣는다(시연 확장 D, 2026-10-06).

    uv run python -m rehearsal.guard_demo          # -> rehearsal/results/guard-demo.html

외부 호출 없이(가짜 응답) 실제 근거 색인(demo/org-n100, 가상 데이터 = 원문 공개 모드)과 실제 검증 코드
(api/rag/briefing.generate_briefing)를 그대로 쓴다. 거절되면 서비스처럼 규칙 기반 설명으로 바꾼다.
"""
import html
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock

from rehearsal.run import RESULTS

OUT = RESULTS / "guard-demo.html"
DEMO = Path(__file__).resolve().parents[1] / "demo" / "org-n100"


def _client(payload: dict):
    c = MagicMock()
    c.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(payload, ensure_ascii=False)))])
    return c


def cases() -> list[dict]:
    from api.rag.briefing import BriefingRejected, generate_briefing
    from api.rag.fallback import rule_based_briefing
    from api.rag.evidence import build_evidence_index
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    b, rep = load_bundle(DEMO)
    ds, parsed = to_dataset(b, rep)
    index = build_evidence_index(ds, parsed, reveal_text=True)
    person = next(p.id for p in ds.people if any(e.kind == "quote" and len(e.text) > 40 for e in index.for_person(p.id)))
    other = next(p.id for p in ds.people if p.id != person)
    ev = next(e for e in index.for_person(person) if e.kind == "quote" and len(e.text) > 40)
    quote = next((x.strip() + "." for x in ev.text.split(". ") if len(x.strip()) >= 15), ev.text[:40]).rstrip(".") + "."
    if quote not in ev.text:
        quote = quote.rstrip(".")
    altered = quote.replace("했", "하였", 1) if "했" in quote else quote[:-3] + "였다"
    skills = {p.id: [{"key": s, "value": str(lv)} for s, lv in sorted(p.skills.items())][:6] for p in ds.people}
    ctx = {other: {"skills": skills[other], "coworks": [], "evidence": []},
           person: {"skills": skills[person], "coworks": [], "evidence": [asdict(e) for e in index.for_person(person)]}}
    scenarios = [
        ("원문 그대로 인용", {"rationale": f"교체를 권고한다. 동료는 “{quote}”라고 평가했다 [{ev.source_id}].",
                         "risks": [], "alternatives": [], "citations": [{"source_id": ev.source_id, "quote": quote}]}),
        ("원문을 살짝 바꾼 인용", {"rationale": f"교체를 권고한다. 동료는 “{altered}”라고 평가했다 [{ev.source_id}].",
                            "risks": [], "alternatives": [], "citations": [{"source_id": ev.source_id, "quote": altered}]}),
        ("없는 출처", {"rationale": "교체를 권고한다 [rv:XX9999>YY0000#1:pos].", "risks": [], "alternatives": [],
                   "citations": []}),
    ]
    out = []
    for name, payload in scenarios:
        try:
            br = generate_briefing(_client(payload), "demo", ctx, other, person, evidence=index)
            out.append({"case": name, "ai_text": payload["rationale"], "verdict": "채택", "code": None,
                        "shown": br["rationale"], "evidence": br.get("evidence", [])})
        except ValueError as exc:                        # 서비스가 BriefingRejected를 감싸 올린다(원인에 사유 코드)
            cause = exc.__cause__ if isinstance(exc.__cause__, BriefingRejected) else exc
            fb = rule_based_briefing(ctx, other, person)
            out.append({"case": name, "ai_text": payload["rationale"], "verdict": "거절 → 규칙 기반 설명으로 전환",
                        "code": getattr(cause, "code", "rejected"), "shown": fb["rationale"],
                        "evidence": fb.get("evidence", [])})
    return out


def render(rows: list[dict]) -> str:
    body = "".join(
        f"<section><h2>{html.escape(r['case'])}</h2><p><b>AI가 쓴 문장</b><br>{html.escape(r['ai_text'])}</p>"
        f"<p><b>검증 결과</b>: {html.escape(r['verdict'])}" + (f" (사유 코드 <code>{html.escape(r['code'])}</code>)" if r["code"] else "")
        + f"</p><p><b>화면에 나가는 설명</b><br>{html.escape(r['shown'])}</p></section>" for r in rows)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 근거 검증 장면</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f6f5e;--box:#eef5f2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1d1c;--fg:#e8e6e1;--muted:#aaa;--acc:#7cc4ad;--box:#24302c}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.7 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:760px;margin:auto}}h2{{color:var(--acc)}}section{{background:var(--box);border-radius:10px;padding:10px 16px;margin:1em 0}}
code{{font-size:.9em}}</style></head><body><main><h1>AI가 지어낸 근거를 막는 장면</h1>
<p>교체 검토의 AI 설명은 동료 평가를 인용할 때 <b>원문과 한 글자씩 대조</b>된다. 아래는 같은 질문에 대해 미리 정해 둔 AI 응답 세 가지를
실제 검증 코드에 넣은 결과다(외부 호출 없음, 가상 데이터).</p>{body}</main></body></html>"""


def main() -> None:
    OUT.write_text(render(cases()), "utf-8")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
