"""E7: 인사팀 소명 글 -- 지식 그래프 템플릿 vs GraphRAG vs 일반 RAG(그래프 없음) (2026-10-07, 사용자 "실험해보고 결정하자").

    uv run python -m rehearsal.justification_compare      # -> rehearsal/results/justification-compare.{json,html}

같은 배치(시연 연초 계획 100명, 미리 계산 안 A)의 사업마다 "왜 이 사람들을 이 사업에 넣었나" 소명 글을 세 방식으로 만든다.
- KG 템플릿: 그래프 사실(core/kg project_evidence)을 정해진 문장 틀에 채운다. LLM 없음. 문장마다 사실 번호 [F#].
- GraphRAG: LLM이 같은 사실 목록(번호 붙은 한 줄짜리 사실)만 받아 글을 쓰고 문장마다 [F#]를 인용한다.
- 일반 RAG: LLM이 그래프 없이 원본 표의 행(팀·기술·과거 이력·평가 라벨, 번호 [R#])만 받아 쓴다 -- 다른 후보 비교 같은 계산 사실은 없다.
자동 지표: 없는 번호 인용, 근거 없는 숫자(문장 속 숫자가 그 문장이 인용한 사실·행에 없음), 인용 없는 문장, 소명 필수 요소 7가지 포함,
길이·비용·시간. 읽기 쉬움은 나란히 놓은 견본으로 사용자가 판단한다(결과를 미리 정하지 않는다).
모델: 외부 gpt-6-luna(서비스 설명과 같은 추론 low). 사내 GLM은 Z.ai 잔액 소진(E6)으로 이번엔 제외하고 표시한다.
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

from rehearsal.run import RESULTS, _env_info, _now

ROOT = Path(__file__).resolve().parents[1]
OUT = RESULTS / "justification-compare.json"
MODEL = "gpt-6-luna"
PRICE = {"input": 0.10, "output": 0.50}
ELEMENTS = {                      # 소명 필수 요소 -- 사실 번호 접두어로 판정
    "요구 기술 충족": "REQ", "사람별 기술 근거": "SKL", "같은 산업 경험": "IND", "같은 고객사 경험": "CLI",
    "함께 일한 이력": "CW", "동료 평가": "REV", "다른 후보 비교": "ALT", "규칙 준수": "CON"}
SYSTEM = ("너는 인사팀에 인력 배치를 소명하는 담당자다. 주어진 근거만 사용해 한국어로 소명 글을 쓴다. 근거에 없는 사실·숫자를 만들지 않는다. "
          "모든 문장 끝에 근거 번호를 대괄호로 단다(예: [F3][F7]). 출력은 JSON {\"text\": \"...\"} 하나.")


def _lv(months) -> int:
    from core.ingest.convert import level_from_months
    return level_from_months(months or 0)


def facts_for(kg, ev: dict, violations: int) -> list[tuple[str, str, str]]:
    """(번호, 종류, 사실 문장). 번호는 F1.. 순서대로."""
    out = []

    def add(kind, text):
        out.append((f"F{len(out) + 1}", kind, text))
    add("PRJ", f"사업 {ev['project']}(고객사 {ev.get('client') or '미상'}, 산업 {ev.get('industry') or '미상'})")
    for r in ev["requirements"]:
        add("REQ", f"요구 기술 {r['skill']}: {r['headcount']}명 필요(최소 경력 {r['min_months']}개월 = 레벨 {_lv(r['min_months'])}), 팀에서 충족 {r['met_by']}명 → "
                   + ("충족" if r["status"] == "met" else "부족"))
    for m in ev["members"]:
        add("MEM", f"{m['person_id']}({m['grade']}) 투입률 {m['alloc']}, 기술 적합 {m['skill_fit']}")
        for c in m["requirements"]:
            if c["status"] != "missing":
                add("SKL", f"{m['person_id']}의 {c['skill']} 경력 {c['months']}개월 = 레벨 {c['level']}(요구 레벨 {c['min_level']}) → "
                           + ("충족" if c["status"] == "met" else "미달"))
        if m["same_industry_projects"]:              # None(산업 모름)·0은 사실로 넣지 않는다
            add("IND", f"{m['person_id']}: 같은 산업 과거 사업 {m['same_industry_projects']}건, {m['same_industry_months']}개월")
        if m["same_client_projects"]:
            add("CLI", f"{m['person_id']}: 같은 고객사 과거 사업 {len(m['same_client_projects'])}건({', '.join(m['same_client_projects'][:2])})")
        for cw in m["cowork_in_team"][:3]:
            if cw["with"] > m["person_id"]:
                add("CW", f"{m['person_id']}와 {cw['with']}: 함께 일한 {cw['months_total']}개월(최근 3년 {cw['months_recent']}개월)")
        for rv in m["reviews_from_team"][:2]:
            add("REV", f"{rv['from']}가 {m['person_id']}를 평가: 판정 {rv['polarity']}, 항목 {', '.join(rv['labels'][:3])}")
        for a in (ev.get("alternatives", {}).get(m["person_id"]) or [])[:1]:
            v = f", 새 위반 {len(a['new_violations'])}건" if a["new_violations"] else ""
            add("ALT", f"{m['person_id']} 대신 {a['person_id']}(기술 적합 {a['skill_fit']})로 바꾸면 전체 점수 {a['delta_total']:+.2f}"
                       f"(기술 {a['delta_skill']:+.2f}, 협업 {a['delta_synergy']:+.2f}){v}")
    add("CON", f"이 배치 전체의 규칙 위반(예산·가용률·정원·동시 사업·투입률) {violations}건, 독립 검증 통과")
    return out


def raw_rows_for(bundle, ev: dict) -> list[tuple[str, str]]:
    """일반 RAG 입력: 원본 표의 행(그래프·계산 없음). 팀원의 요구 기술 관련 기술 행, 최근 과거 이력, 팀 안 평가 라벨."""
    t = bundle.tables
    team = {m["person_id"] for m in ev["members"]}
    skills = {r["skill"] for r in ev["requirements"]}
    rows = [f"사업: {ev['project']}"]
    for r in t["project_skill_requirements.csv"]:
        if r["project_id"] == ev["project_id"]:
            rows.append(f"요구: {r['skill_name']} 최소 {r['min_experience_months']}개월, {r['headcount']}명")
    for m in ev["members"]:
        rows.append(f"배치: {m['person_id']} 등급 {m['grade']} 투입률 {m['alloc']}")
    for r in t["person_skills.csv"]:
        if r["person_id"] in team and r["skill_name"] in skills:
            rows.append(f"기술: {r['person_id']} {r['skill_name']} 경력 {r['experience_months']}개월")
    hist = [r for r in t["work_history.csv"] if r["person_id"] in team]
    for r in sorted(hist, key=lambda r: r["end_date"], reverse=True)[:3 * len(team)]:
        rows.append(f"이력: {r['person_id']} {r['work_name']}(고객사 {r['client']}, 산업 {r['industry']}) {r['start_date']}~{r['end_date']}")
    items = {}
    for r in t["review_items.csv"]:
        items.setdefault(r["review_id"], []).append(("좋은 점" if r["polarity"] == "positive" else "아쉬운 점") + f" {r['item']}")
    for r in t["reviews.csv"]:
        if r["reviewer_id"] in team and r["reviewee_id"] in team:
            rows.append(f"평가: {r['reviewer_id']}→{r['reviewee_id']} ({', '.join(items.get(r['review_id'], [])[:3])})")
    return [(f"R{i + 1}", x) for i, x in enumerate(rows)]


def template_text(facts) -> str:
    """KG 템플릿: 사실을 순서대로 엮는다(LLM 없음)."""
    by = {}
    for fid, kind, text in facts:
        by.setdefault(kind, []).append((fid, text))
    parts = []
    for kind, head in (("PRJ", "대상 사업"), ("REQ", "요구 기술 충족"), ("MEM", "배치 인원"), ("SKL", "기술 근거"),
                       ("IND", "같은 산업 경험"), ("CLI", "같은 고객사 경험"), ("CW", "함께 일한 이력"), ("REV", "동료 평가"),
                       ("ALT", "다른 후보와 비교"), ("CON", "규칙 준수")):
        for fid, text in by.get(kind, []):
            parts.append(f"{head}: {text}. [{fid}]")
    return "\n".join(parts)


def _numbers(s: str) -> list[str]:
    """문장 속 숫자. 사람·사업 ID 안의 숫자(DP0013·J002)는 빼고, 작은 정수 0~3은 서수·접속에 흔해 뺀다."""
    return [n for n in re.findall(r"(?<![A-Za-z0-9.])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9])", s) if n not in ("0", "1", "2", "3")]


def check(text: str, sources: dict[str, str]) -> dict:
    """문장마다 인용 번호가 실제로 있는지, 문장 속 숫자가 그 문장이 인용한 근거에 있는지."""
    raw = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n", text) if s.strip()]
    sentences = []
    for s in raw:                   # 마침표 뒤 다음 문장 맨 앞에 붙은 인용은 앞 문장의 것이다("…이다. [R1][R2] 다음 문장")
        lead = re.match(r"^((?:\[[FR]\d+\])+)\s*(.*)$", s)
        if lead and sentences:
            sentences[-1] += " " + lead.group(1)
            s = lead.group(2).strip()
            if not s:
                continue
        sentences.append(s)
    bad_ref, no_ref, unsupported, cited = 0, 0, 0, set()
    for s in sentences:
        refs = re.findall(r"\[([FR]\d+)\]", s)
        if not refs:
            no_ref += 1
            continue
        known = [r for r in refs if r in sources]
        bad_ref += len(refs) - len(known)
        cited.update(known)
        pool = " ".join(sources[r] for r in known)
        body = re.sub(r"\[[FR]\d+\]", "", s)
        for n in _numbers(body):
            if n.lstrip("+") not in pool and n not in pool:
                unsupported += 1
    return {"sentences": len(sentences), "invalid_refs": bad_ref, "sentences_without_ref": no_ref,
            "unsupported_numbers": unsupported, "cited": sorted(cited), "chars": len(text)}


def coverage(cited: set[str], facts) -> dict:
    kinds = {fid: kind for fid, kind, _ in facts}
    have = {kinds[c] for c in cited if c in kinds}
    avail = {kind for _, kind, _ in facts}
    return {name: (prefix in have) if prefix in avail else None for name, prefix in ELEMENTS.items()}


def llm(client, user: str) -> dict:
    t = time.monotonic()
    resp = client.chat.completions.create(model=MODEL, response_format={"type": "json_object"},
                                          extra_body={"reasoning_effort": "low"},
                                          messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    u = resp.usage
    try:
        text = json.loads(resp.choices[0].message.content or "{}").get("text", "")
    except ValueError:
        text = ""
    return {"text": text, "latency_s": round(time.monotonic() - t, 1), "in": u.prompt_tokens, "out": u.completion_tokens,
            "cost_usd": round((u.prompt_tokens * PRICE["input"] + u.completion_tokens * PRICE["output"]) / 1e6, 5)}


def main() -> None:
    from openai import OpenAI
    from api.settings import PlacementSettings
    from core.config import load_env
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg, project_evidence
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    import sys
    if "--rescore" in sys.argv:                      # 저장된 글을 고친 채점기로 다시 채점(LLM 호출 없음)
        rescore()
        return
    load_env()
    b, rep = load_bundle(ROOT / "demo" / "org-n100")
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    kg = build_kg(b, ds, parsed)
    pre = json.loads((ROOT / "demo" / "precomputed" / "org-n100.json").read_text("utf-8"))
    entries = [AssignEntry(**e) for e in pre["optimize"]["plans"][0]["entries"]]
    violations = len(evaluate_plan(g, S, C, params, entries).violations)
    teams = {}
    for e in entries:
        teams[e.project_id] = teams.get(e.project_id, 0) + 1
    projects = sorted(teams, key=lambda j: (-teams[j], j))[:8]          # 팀이 큰 사업 8개(소명할 거리가 많은 쪽)
    client = OpenAI(timeout=300, max_retries=1)
    data = {"env": _env_info(), "started_at": _now(), "model": MODEL, "plan": "demo/org-n100 미리 계산 안 A",
            "violations_in_plan": violations, "projects": []}
    spent = 0.0
    for jid in projects:
        ev = project_evidence(kg, jid, entries, graph=g, S=S, C=C, params=params)
        facts = facts_for(kg, ev, violations)
        fsrc = {fid: text for fid, _, text in facts}
        rows = raw_rows_for(b, ev)
        rsrc = {rid: text for rid, text in rows}
        task = "이 사업에 왜 이 사람들을 배치했는지 인사팀에 소명하라. 요구 기술 충족, 사람별 근거, 산업·고객사 경험, 함께 일한 이력, 동료 평가, " \
               "다른 후보와의 비교, 규칙 준수를 근거가 있는 만큼 다룬다. 400~700자."
        tmpl = template_text(facts)
        g_res = llm(client, task + "\n근거(사실 목록, 번호 [F#]):\n" + "\n".join(f"[{fid}] {t}" for fid, _, t in facts))
        r_res = llm(client, task.replace("[F#]", "[R#]") + "\n근거(원본 표의 행, 번호 [R#] -- 문장 끝에 [R#]로 인용):\n"
                    + "\n".join(f"[{rid}] {t}" for rid, t in rows))
        spent += g_res["cost_usd"] + r_res["cost_usd"]
        row = {"project_id": jid, "project": ev["project"], "team": len(ev["members"]), "facts": len(facts), "raw_rows": len(rows),
               "fact_list": [list(f) for f in facts], "raw_list": [list(r) for r in rows],    # 다시 채점할 때 같은 근거와 대조
               "kg_template": {"text": tmpl, **check(tmpl, fsrc)},
               "graphrag": {**g_res, **check(g_res["text"], fsrc)},
               "plain_rag": {**r_res, **check(r_res["text"], rsrc)}}
        for k in ("kg_template", "graphrag"):
            row[k]["coverage"] = coverage(set(row[k]["cited"]), facts)
        row["plain_rag"]["coverage"] = None                 # 원본 행에는 비교·규칙 사실이 없다 -- 다룬 요소는 사람이 읽고 판단
        print(json.dumps({"project": jid, "graphrag": {k: row["graphrag"][k] for k in ("invalid_refs", "unsupported_numbers", "sentences_without_ref", "chars", "latency_s", "cost_usd")},
                          "plain_rag": {k: row["plain_rag"][k] for k in ("invalid_refs", "unsupported_numbers", "sentences_without_ref", "chars", "latency_s", "cost_usd")}},
                         ensure_ascii=False), flush=True)
        data["projects"].append(row)
    data["spent_usd"] = round(spent, 4)
    data["finished_at"] = _now()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    OUT.with_suffix(".html").write_text(render(data), "utf-8")
    print(f"written {OUT} · spent ${spent:.3f}")


def rescore() -> None:
    """저장된 글을 저장된 근거(생성 당시의 사실·원본 행)와 고친 채점기로 다시 채점한다(LLM 호출 없음)."""
    data = json.loads(OUT.read_text("utf-8"))
    for row in data["projects"]:
        facts = [tuple(f) for f in row["fact_list"]]
        fsrc = {fid: text for fid, _, text in facts}
        rsrc = {rid: text for rid, text in row["raw_list"]}
        for key, src in (("kg_template", fsrc), ("graphrag", fsrc), ("plain_rag", rsrc)):
            row[key].update(check(row[key]["text"], src))
        for key in ("kg_template", "graphrag"):
            row[key]["coverage"] = coverage(set(row[key]["cited"]), facts)
    data["rescored_at"] = _now()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    OUT.with_suffix(".html").write_text(render(data), "utf-8")
    for p in data["projects"]:
        print(p["project_id"], {k: (p[k]["invalid_refs"], p[k]["unsupported_numbers"], p[k]["sentences_without_ref"]) for k in ("kg_template", "graphrag", "plain_rag")})


def render(data: dict) -> str:
    def agg(key, field):
        vals = [p[key][field] for p in data["projects"] if p[key].get(field) is not None]
        return sum(vals)
    n = len(data["projects"])
    cov = {}
    for key in ("kg_template", "graphrag"):
        hits = [v for p in data["projects"] for v in (p[key]["coverage"] or {}).values() if v is not None]
        cov[key] = f"{100 * sum(hits) / len(hits):.0f}%" if hits else "—"
    summary = "".join(
        f"<tr><td>{label}</td><td>{agg(key, 'invalid_refs')}</td><td>{agg(key, 'unsupported_numbers')}</td><td>{agg(key, 'sentences_without_ref')}</td>"
        f"<td>{cov.get(key, '사람 판단')}</td><td>{agg(key, 'chars') // n}</td>"
        f"<td>{'—' if key == 'kg_template' else str(round(agg(key, 'latency_s') / n, 1)) + '초'}</td>"
        f"<td>{'$0' if key == 'kg_template' else '$' + format(agg(key, 'cost_usd'), '.3f')}</td></tr>"
        for key, label in (("kg_template", "KG 템플릿(LLM 없음)"), ("graphrag", "GraphRAG(LLM + 그래프 사실)"), ("plain_rag", "일반 RAG(LLM + 원본 행)")))
    samples = ""
    for p in data["projects"]:
        samples += f"<h3>{html.escape(p['project'])} <small>팀 {p['team']}명 · 사실 {p['facts']}개 · 원본 행 {p['raw_rows']}개</small></h3><div class='cols'>"
        for key, label in (("kg_template", "KG 템플릿"), ("graphrag", "GraphRAG"), ("plain_rag", "일반 RAG")):
            r = p[key]
            samples += (f"<div class='col'><b>{label}</b><small> · 근거 없는 숫자 {r['unsupported_numbers']} · 없는 번호 {r['invalid_refs']} · 인용 없는 문장 {r['sentences_without_ref']}</small>"
                        f"<pre>{html.escape(r['text'])}</pre></div>")
        samples += "</div>"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>인사팀 소명 방식 비교</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#aaa;--acc:#8ab8e0;--line:#3a3a3a;--card:#23272b}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1200px;margin:auto}}h2,h3{{color:var(--acc)}}small{{color:var(--muted);font-weight:400}}
table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right}}th:first-child,td:first-child{{text-align:left}}
th{{color:var(--muted)}}.cols{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}@media (max-width:900px){{.cols{{grid-template-columns:1fr}}}}
.col{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px}}pre{{white-space:pre-wrap;font:13px/1.55 inherit;margin:.4em 0}}</style></head><body><main>
<h1>인사팀 소명 글: 지식 그래프 템플릿 vs GraphRAG vs 일반 RAG</h1>
<p>같은 배치({html.escape(data['plan'])}, 규칙 위반 {data['violations_in_plan']}건)의 사업 {n}개. KG 템플릿은 그래프 사실을 정해진 틀에 채우고(LLM 없음),
GraphRAG는 LLM이 같은 사실 목록만 보고 쓰며 문장마다 사실 번호를 단다. 일반 RAG는 그래프 없이 원본 표의 행만 받는다(다른 후보 비교 같은 계산 사실 없음).
모델 {html.escape(data['model'])}(추론 low). 사내 GLM은 Z.ai 잔액 소진으로 이번엔 제외. 비용 ${data['spent_usd']:.3f}.</p>
<h2>요약(사업 {n}개 합계)</h2><p>"근거 없는 숫자" = 문장 속 숫자가 그 문장이 인용한 근거에 없음(작은 정수 0~3 제외 — 거짓 경보 가능). 필수 요소 = 요구 기술 충족·사람별 기술·
같은 산업·같은 고객사·함께 일한 이력·동료 평가·다른 후보 비교·규칙 준수 중 근거가 있는 것을 인용했는지(일반 RAG는 근거 종류가 달라 사람이 판단).</p>
<table><tr><th>방식</th><th>없는 번호 인용</th><th>근거 없는 숫자</th><th>인용 없는 문장</th><th>필수 요소 포함</th><th>평균 글자 수</th><th>평균 시간</th><th>비용</th></tr>{summary}</table>
<h2>견본 — 읽기 쉬움은 직접 판단</h2>{samples}</main></body></html>"""


if __name__ == "__main__":
    main()
