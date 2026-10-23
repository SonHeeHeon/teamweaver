"""영상 D6 "발주 자료에서 필요 기술 읽기"에 쓸 실제 공공 자료를 한 번 읽어 둔다(2026-10-23, claude-a -- claude-b 요청 `docs/requests/2026-10-23-rfp-chunked-read.md` 3).

자료는 저장소에 넣지 않는다(기관 문서 -- 시연자가 화면에서 올린다). 이 스크립트는 그 글자(서비스 글자 뽑기 결과)를 받아
서비스와 같은 `read_requirements`(OpenAI, 서비스 모델)로 읽고, 등록 양식이 "추정 n개 모두 선택"으로 넣을 기술(필수, 경력 2년·1명 기본)을
`rehearsal/data/d6_bid_doc_skills.json`에 남긴다 -- `rehearsal.scenario_numbers`가 그 기술로 사업을 등록해 뒤 장면 숫자를 다시 잰다.
AI 결과는 실행마다 조금 다르다(b 확인: RAG·LLM 파인튜닝이 필수였다가 우대) -- 녹화 때 화면은 그때 읽은 값이다.

  uv run python -m rehearsal.d6_bid_doc --text <글자 파일> [--source "기관 · 사업명 · 사전규격(2026. 3.)"]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rehearsal" / "data" / "d6_bid_doc_skills.json"
DEFAULT_MONTHS, DEFAULT_HEADCOUNT = 24, 1          # 등록 양식이 경력·인원 없는 제안을 넣을 때의 기본(claude-b SkillReader)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--source", default="")
    a = ap.parse_args()
    from openai import OpenAI

    from api.rag.rfp_read import read_requirements
    from core.config import load_env, load_pricing
    from core.ingest.skills import load_dictionary
    load_env()
    pricing = load_pricing()
    model = pricing["briefing_model"]
    effort = pricing["models"].get(model, {}).get("reasoning_effort")
    text = Path(a.text).read_text("utf-8")
    out = read_requirements(text, load_dictionary(), client=OpenAI(timeout=120, max_retries=1), model=model, reasoning_effort=effort,
                            price=pricing["models"].get(model))
    req = [s for s in out["skills"] if s["kind"] == "required"]
    res = {"note": "D6 시연 자료를 서비스와 같은 함수로 한 번 읽은 결과(자료 원문은 저장소 밖). 등록은 필수 제안 중 경력·인원 없는 '추정'을 모두 넣은 경우.",
           "source": a.source, "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "chars": len(text),
           "method": out["method"], "model": out["model"], "usage": out["usage"], "chunks": len(out.get("chunks") or []),
           "warnings": out["warnings"],
           "required": [{"name": s["name"], "months": s["months"], "headcount": s["headcount"], "quote": s["quote"]} for s in req],
           "preferred": [s["name"] for s in out["skills"] if s["kind"] == "preferred"],
           "mentioned_not_required": [m["name"] for m in out["mentioned_not_required"]], "unmapped_terms": out["unmapped_terms"],
           "register_skills": [{"name": s["name"], "min_months": s["months"] or DEFAULT_MONTHS, "headcount": s["headcount"] or DEFAULT_HEADCOUNT}
                               for s in req]}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps({k: res[k] for k in ("method", "model", "usage", "required", "preferred", "mentioned_not_required", "unmapped_terms")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
