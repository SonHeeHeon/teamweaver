"""실제 공공 발주 자료로 필요 기술 읽기 측정(2026-10-23 밤, claude-a -- 사용자 "실제 공공 자료 10개를 줄게. 이걸로 하자" · "glm에 공개자료 보내도 돼").

가상 자료 측정(`rehearsal.rfp_read_eval`)과 같은 함수·같은 채점(`score`)을 실제 공공 자료(나라장터 사전규격·입찰공고 첨부)에 쓴다.
자료 원문은 저장소에 넣지 않는다(기관 문서) -- `--docs-dir`(저장소 밖 폴더)에서 읽고, 결과에는 짧은 근거 말·기술 이름·글자 수만 남긴다.

정답표 `rehearsal/data/rfp_gold_real.json`(AI 측정 전에 커밋한다):
  - 작성 과정은 파일의 "authoring"에 사실대로 적는다(초안 작성자, 검토·확정한 사람, 초안을 쓸 때 AI 결과를 보지 않았는지).
  - 자료마다 required[{skill, basis: 명시|추정, quote}] · preferred · not_required(현행 시스템·연계 대상 등 요구가 아닌 언급) · ok(어느 쪽이어도 맞음) · seen_before
    (정답 작성 전에 이 자료의 AI 결과를 본 적이 있으면 참 -- 따로 보고).
  - 기준: required = 사업에 필요하다고 자료가 말하는 기술. "명시" = 인력 요건·과업 문장에 그 기술(사전 이름·별칭)이 적힘, "추정" = 과업을 하려면 그 기술이 없으면 안 되는 것.
    공공 자료는 대부분 인력 요건이 없어(claude-b 확인) 추정이 많다 -- 추정은 사람마다 갈릴 수 있어 명시·추정 재현율을 나눠 보고한다.

방법: 규칙(AI 없음) · OpenAI 서비스 모델 · GLM-5.3-Flash high(사내 LLM 대리). AI는 실행마다 달라 `--repeat`번(기본 3) 돌려 평균·범위를 남긴다.
관문은 두지 않는다(보고만). 가설(판정 아님): AI 정밀도 > 규칙 정밀도, 규칙은 현행 시스템 언급을 필수로 뽑는다, 명시 재현율 > 추정 재현율.

사람 시간은 **측정이 아니라 추정**이다(보고서 8.3과 같은 방식): 자료 한글 음절 수 ÷ 묵독 속도(분당 550음절 = 쉬운 단문 상한【관찰】 ~ 300음절 = 기술 문서 정독【가정】)
+ 기술 고르기·양식 10~20분【가정】. AI 시간은 실측(읽기 지연, 자료당).

  uv run python -m rehearsal.rfp_real_eval --docs-dir <저장소 밖 폴더> [--repeat 3] [--rule-only] [--gold …] [--out …]   → rehearsal/results/rfp-real.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
from pathlib import Path

from rehearsal.rfp_read_eval import GLM_EFFORT, GLM_MODEL, GLM_URL, _text_of, run, score

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "rehearsal" / "data" / "rfp_gold_real.json"
OUT = ROOT / "rehearsal" / "results" / "rfp-real.json"
READ_SPEED = (300, 550)            # 분당 음절(정독 가정 ~ 쉬운 단문 상한)
PICK_MINUTES = (10, 20)            # 기술 고르기·양식 입력(가정)
WATCHED = ("api/rag", "rehearsal/rfp_real_eval.py", "rehearsal/rfp_read_eval.py", "rehearsal/data/rfp_gold_real.json")


def load_items(docs_dir: Path, gold_path: Path = GOLD) -> tuple[list[dict], dict]:
    gold = json.loads(gold_path.read_text("utf-8"))
    items = []
    for d in gold["docs"]:
        text = _text_of(docs_dir / d["file"])
        items.append({"set": "real", "id": d["id"], "style": d.get("kind", ""), "text": text, "required": d["required"],
                      "preferred": d["preferred"], "not_required": d["not_required"], "ok": d.get("ok", []), "complete": True,
                      "seen_before": bool(d.get("seen_before")), "title": d.get("title", "")})
    return items, gold


def by_basis(items: list[dict], outs: list[dict], sd) -> dict:
    """명시·추정 정답의 재현율을 나눠 센다(필수로 뽑은 것만 맞음)."""
    lab = lambda s: sd.concepts[s["concept_id"]]["label"] if s["concept_id"] in sd.concepts else s["concept_id"]   # noqa: E731
    n = {"명시": [0, 0], "추정": [0, 0]}
    for it, out in zip(items, outs):
        pred = {lab(s) for s in out["skills"] if s["kind"] == "required"}
        for g in it["required"]:
            k = "명시" if g.get("basis") == "명시" else "추정"
            n[k][1] += 1
            n[k][0] += g["skill"] in pred
    return {k: {"found": a, "gold": b, "recall": round(a / b, 3) if b else None} for k, (a, b) in n.items()}


def human_minutes(text: str) -> dict:
    syl = len(re.findall("[가-힣]", text))
    lo = syl / READ_SPEED[1] + PICK_MINUTES[0]
    hi = syl / READ_SPEED[0] + PICK_MINUTES[1]
    return {"chars": len(text), "hangul_syllables": syl, "estimate_min": [round(lo, 1), round(hi, 1)]}


def summarize(runs: list[dict]) -> dict:
    """같은 방법을 여러 번 돌린 결과의 평균·범위(정밀도·재현율·요구 아닌 언급 오검출)."""
    keys = ("precision_hard", "recall", "nonreq_fp_rate", "pref_recall", "latency_p50", "cost_usd")
    out = {}
    for k in keys:
        v = [r[k] for r in runs if r.get(k) is not None]
        out[k] = {"mean": round(statistics.mean(v), 3), "min": min(v), "max": max(v)} if v else None
    out["basis"] = [r["basis"] for r in runs]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs-dir", required=True)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--rule-only", action="store_true")
    ap.add_argument("--gold", default=str(GOLD))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    from core.config import load_env, load_pricing
    from core.ingest.skills import load_dictionary
    load_env()
    sd = load_dictionary()
    pricing = load_pricing()
    items, gold = load_items(Path(a.docs_dir).expanduser(), Path(a.gold))
    git = lambda *c: subprocess.run(["git", "-C", str(ROOT), *c], capture_output=True, text=True).stdout.strip()   # noqa: E731
    res = {"experiment": "rfp-real", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "commit": git("rev-parse", "--short", "HEAD"),
           "dirty": bool(git("status", "--porcelain", "--", *WATCHED)), "authoring": gold.get("authoring"),
           "docs": [{"id": it["id"], "title": it["title"], "seen_before": it["seen_before"], **human_minutes(it["text"])} for it in items],
           "human_time_note": f"추정(측정 아님): 한글 음절 ÷ 분당 {READ_SPEED[0]}~{READ_SPEED[1]}음절 + 기술 고르기 {PICK_MINUTES[0]}~{PICK_MINUTES[1]}분",
           "methods": {}}
    print("규칙", flush=True)
    rule_outs = run(items, sd)
    res["methods"]["rule"] = {**score(items, rule_outs, sd), "basis": by_basis(items, rule_outs, sd)}
    if not a.rule_only:
        from openai import OpenAI
        model = pricing["briefing_model"]
        effort = pricing["models"].get(model, {}).get("reasoning_effort")
        clients = [("openai", OpenAI(timeout=300, max_retries=1), model, effort),
                   ("glm", OpenAI(base_url=GLM_URL, api_key=os.environ["ZAI_API_KEY"], timeout=600, max_retries=1), GLM_MODEL, GLM_EFFORT)
                   if os.environ.get("ZAI_API_KEY") else None]
        for c in filter(None, clients):
            name, client, m, e = c
            runs = []
            for i in range(a.repeat):
                print(f"{name} {m} ({e}) {i + 1}/{a.repeat}", flush=True)
                outs = run(items, sd, client=client, model=m, effort=e, price=pricing["models"].get(m))
                runs.append({**score(items, outs, sd), "basis": by_basis(items, outs, sd)})
            res["methods"][name] = {"model": m, "reasoning_effort": e, "runs": runs, "summary": summarize(runs)}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", "utf-8")
    for k, v in res["methods"].items():
        s = v.get("summary") or {x: v.get(x) for x in ("precision_hard", "recall", "nonreq_fp_rate")}
        print(k, json.dumps({x: s.get(x) for x in ("precision_hard", "recall", "nonreq_fp_rate")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
