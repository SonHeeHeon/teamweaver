"""낯선 기술 이름 정리 측정(2026-10-22, claude-a -- 설계 `.omc/plan/2026-10-22-ai-value.md` 5절, 조사 권장 "보류 별칭").

세 묶음을 문자열 유사도만(AI 없음 -- `core.ingest.skill_mapping.suggest(client=None)`)과 AI(서비스 모델, 같은 함수·서버 검사)로 제안하게 해 비교한다.
1. 보류 별칭: 사전의 실제 별칭을 25개씩 사전에서 빼고(그 별칭만 모르는 사전) 원래 개념으로 되돌리는지. 다른 규칙(정규화·버전·괄호)으로 여전히
   맞는 별칭은 낯선 이름이 아니라 뺀다. 맥락(가진 사람·함께 가진 기술)은 없이 이름만 -- 보수적.
2. 시연 낯선 표기 14개(`scripts/make_messy_skill_demo.py` VARIANTS, 시연 묶음의 실제 맥락 포함).
3. 음성 20개: 기술이 아닌 말 10개(not_skill) + 사전에 없는 별개 기술 10개(new).
지표: 1위 정확도(합칠 개념이 정답), **상위 3 재현율**(관리자에게 보이는 후보 세 개 안에 정답 -- 고르기만 하면 되는 비율), 잘못 합침(헷갈리는 쌍 따로),
음성은 합치지 않은 비율. 끝으로 시연 묶음에 정답 결정을 승인했을 때 사람×사업 기술 점수(S)가 원본(연초 100명)과 같아지는지.

사전 등록(측정 전에 커밋) -- 판정: (1) AI 제안은 모두 사전 id(사전 밖 0건), (2) 음성 20개에서 AI가 합치자고 한 것 ≤ 2건,
(3) 승인 뒤 S 차이 0. 그 밖(정확도·재현율)은 판정 없이 보고하고 Wilson 95% 구간을 함께 적는다.
가설(판정 아님): AI는 보류 별칭의 상위 3 재현율과 1위 정확도를 문자열 유사도보다 올린다(특히 한글 음차·약어).

  uv run python -m rehearsal.skill_mapping_eval [--rule-only] [--limit N]     → rehearsal/results/skill-mapping.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import subprocess
import time
from pathlib import Path

from rehearsal.request_parse_eval import wilson

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rehearsal" / "results" / "skill-mapping.json"
NOT_SKILL = ["성실함", "리더십", "책임감", "꼼꼼함", "원만한 대인관계", "야근 가능", "주말 근무 가능", "운전면허 1종", "협조성", "긍정적인 태도"]
NEW_TECH = ["Haskell", "Elixir", "Godot", "Svelte", "OCaml", "Erlang", "Unreal Engine", "Delphi", "PowerBuilder", "Qlik"]
BATCH, SEED = 25, 20261022


def held_out(base) -> list[tuple[str, str]]:
    out = []
    for cid, c in base.concepts.items():
        for a in c.get("aliases") or []:
            if base.resolve(a)[0] and base.resolve(a)[0]["id"] == cid and base.resolve(a)[1] == "exact":
                out.append((a, cid))
    return out


def without(base, names: set[str]):
    """그 별칭들만 뺀 사전."""
    from core.ingest.skills import from_data
    concepts = [{**c, "aliases": [a for a in (c.get("aliases") or []) if a not in names]} for c in base.concepts.values()]
    return from_data({"version": base.version + "-holdout", "partial_credit": {"narrower_to_broader": base.narrower_credit},
                      "concepts": concepts})


def shown(item: dict, k: int = 3) -> list[str]:
    seq = ([item["concept"]] if item.get("concept") else []) + [a["id"] for a in item.get("alternatives") or []] \
        + [c["id"] for c in item.get("candidates") or []]
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out[:k]


def grade(items: list[dict], gold: dict[str, str | None], kind: str) -> dict:
    from core.ingest.skill_mapping import CONFUSABLE, MAP_DECISIONS
    n = len(items)
    if kind == "negative":
        merged = [i for i in items if i["decision"] in MAP_DECISIONS]
        exact = sum(1 for i in items if i["decision"] == gold[i["name"]])
        return {"n": n, "not_merged": n - len(merged), "merged": len(merged), "merged_names": [(i["name"], i["concept"]) for i in merged],
                "decision_exact": exact, "not_merged_ci": wilson(n - len(merged), n)}
    top1 = sum(1 for i in items if i["decision"] in MAP_DECISIONS and i["concept"] == gold[i["name"]])
    alias_exact = sum(1 for i in items if i["decision"] == "alias" and i["concept"] == gold[i["name"]])
    r3 = sum(1 for i in items if gold[i["name"]] in shown(i))
    wrong = [i for i in items if i["decision"] in MAP_DECISIONS and i["concept"] != gold[i["name"]]]
    conf = [i for i in wrong if frozenset((i["concept"], gold[i["name"]])) in CONFUSABLE]
    missed = sum(1 for i in items if i["decision"] not in MAP_DECISIONS)
    return {"n": n, "top1": top1, "top1_rate": round(top1 / n, 3) if n else None, "top1_ci": wilson(top1, n),
            "alias_exact": alias_exact, "recall3": r3, "recall3_rate": round(r3 / n, 3) if n else None, "recall3_ci": wilson(r3, n),
            "wrong_merge": len(wrong), "wrong_merge_confusable": len(conf), "said_new_or_not_skill": missed,
            "wrong_examples": [(i["name"], gold[i["name"]], i["concept"], i["decision"]) for i in wrong[:15]]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="보류 별칭을 이만큼만(0 = 전부)")
    args = ap.parse_args()
    from api.rag.justification import justification_settings
    from core.config import load_env, load_pricing
    from core.ingest.skill_mapping import suggest, unknown_names
    from core.ingest.skills import load_dictionary
    from scripts.make_messy_skill_demo import VARIANTS
    load_env()
    base = load_dictionary()
    pricing = load_pricing()
    model = pricing["briefing_model"]
    client = None
    if not args.rule_only:
        from openai import OpenAI
        client = OpenAI(timeout=180, max_retries=1)
    effort = justification_settings(model)["reasoning_effort"]
    price = pricing.get("models", {}).get(model, {})
    usage = {"calls": 0, "cost_usd": 0.0, "in": 0, "latency_s": 0.0}

    def run(sd, items, ai: bool):
        r = suggest(sd, items, client=client if ai else None, model=model, reasoning_effort=effort, price=price, batch=BATCH)
        if ai and r.get("usage"):
            for k in ("calls", "in"):
                usage[k] += r["usage"][k]
            usage["cost_usd"] = round(usage["cost_usd"] + r["usage"]["cost_usd"], 6)
            usage["latency_s"] = round(usage["latency_s"] + r["usage"]["latency_s"], 2)
        return r["items"]

    held = held_out(base)
    random.Random(SEED).shuffle(held)
    if args.limit:
        held = held[:args.limit]
    modes = ["string"] + ([] if args.rule_only else ["ai"])
    res = {"experiment": "skill-mapping", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "model": model, "dictionary_version": base.version, "held_out_total": len(held)}
    t0 = time.monotonic()
    # 1. 보류 별칭
    out = {m: [] for m in modes}
    gold_h, skipped = {}, 0
    for i in range(0, len(held), BATCH):
        batch = held[i:i + BATCH]
        sd = without(base, {a for a, _ in batch})
        items = []
        for a, cid in batch:
            c = sd.lookup(a)
            if c is not None:                    # 다른 규칙으로 여전히 맞는다 -- 낯선 이름이 아니다
                skipped += 1
                continue
            gold_h[a] = cid
            items.append({"name": a, "people": 0, "projects": [], "works": 0, "co_skills": []})
        for m in modes:
            out[m] += run(sd, items, m == "ai")
    res["held_out"] = {"skipped_still_known": skipped, **{m: grade(out[m], gold_h, "positive") for m in modes}}
    # 2. 시연 낯선 표기(실제 맥락)
    from api.workspace_edits import read_table
    messy = ROOT / "demo" / "org-n100-messy-skills"
    files = {p.name: p.read_text("utf-8") for p in messy.iterdir() if p.is_file()}
    tables = {n: read_table(files, n)[1] for n in ("person_skills.csv", "project_skill_requirements.csv", "work_skills.csv")}
    demo_items = unknown_names(tables, base)
    gold_d = {v: base.lookup(k)["id"] for k, v in VARIANTS.items()}
    res["demo_variants"] = {m: grade(run(base, demo_items, m == "ai"), gold_d, "positive") for m in modes}
    # 3. 음성
    neg = [{"name": n, "people": 0, "projects": [], "works": 0, "co_skills": []} for n in NOT_SKILL + NEW_TECH]
    gold_n = {**{n: "not_skill" for n in NOT_SKILL}, **{n: "new" for n in NEW_TECH}}
    res["negatives"] = {m: grade(run(base, neg, m == "ai"), gold_n, "negative") for m in modes}
    # 4. 승인 뒤 기술 점수(S)가 원본과 같은가 -- 정답 결정을 승인한 시연 묶음 vs 연초 100명
    res["score_recovery"] = score_recovery(messy, gold_d)
    if "ai" in modes:
        res["ai_usage"] = usage
        ai_ids = [i for i in out["ai"] if i.get("concept")]
        res["gates"] = {"ai_ids_in_dictionary": all(i["concept"] in base.concepts or i["concept"].startswith("x_") for i in ai_ids),
                        "negatives_merged_le_2": res["negatives"]["ai"]["merged"] <= 2,
                        "score_diff_zero_after_approval": res["score_recovery"]["max_abs_diff_after"] == 0}
        res["gates_pass"] = all(res["gates"].values())
    res["wall_s"] = round(time.monotonic() - t0, 1)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    for part in ("held_out", "demo_variants", "negatives"):
        for m in modes:
            g = res[part][m]
            print(part, m, {k: g[k] for k in g if k in ("n", "top1_rate", "recall3_rate", "wrong_merge", "wrong_merge_confusable", "not_merged", "merged")})
    print("score_recovery", res["score_recovery"], "gates", res.get("gates"), "usage", res.get("ai_usage"))


def score_recovery(messy: Path, gold: dict[str, str]) -> dict:
    """정답 결정(alias)을 manifest에 넣은 시연 묶음의 기술 점수(S)가 원본 연초 100명과 같은가. 결정 전 차이도 함께."""
    import shutil
    import tempfile
    import numpy as np
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.scoring.engine import ScoringEngine

    def S(root: Path):
        b, r = load_bundle(root)
        ds, parsed = to_dataset(b, r)
        g = MemoryGraph.build(ds, parsed)
        return ScoringEngine(g).skill_matrix({}), g
    s0, g0 = S(ROOT / "demo" / "org-n100")
    s1, g1 = S(messy)
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / "approved"
        shutil.copytree(messy, dst)
        m = json.loads((dst / "manifest.json").read_text("utf-8"))
        m["skill_names"] = [{"name": n, "decision": "alias", "concept": c, "reason": "정답"} for n, c in gold.items()]
        (dst / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
        s2, g2 = S(dst)
    assert [p.id for p in g0.people] == [p.id for p in g2.people] and [j.id for j in g0.projects] == [j.id for j in g2.projects]
    d1, d2 = np.abs(s0 - s1), np.abs(s0 - s2)
    return {"pairs": int(d1.size), "pairs_changed_before": int((d1 > 1e-12).sum()), "share_changed_before": round(float((d1 > 1e-12).mean()), 3),
            "max_abs_diff_before": round(float(d1.max()), 4), "pairs_changed_after": int((d2 > 1e-12).sum()),
            "max_abs_diff_after": round(float(d2.max()), 6)}


if __name__ == "__main__":
    main()
