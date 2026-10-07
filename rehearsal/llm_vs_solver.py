"""E6: LLM에게 배치를 통째로 맡기기 vs 모델+솔버 -- 같은 문제, 같은 채점(2026-10-07, 사용자 요청).

    TEAMWEAVER_SOLVER_SEEDS=4 uv run --group benchmark python -m rehearsal.llm_vs_solver --pilot   # 시연 문제로 모델별 1회
    TEAMWEAVER_SOLVER_SEEDS=4 uv run --group benchmark python -m rehearsal.llm_vs_solver           # 본 실험 -> rehearsal/results/llm-vs-solver.{json,html}

사용자: "Llm(gpt) 직접 배치 시켜서 최적화는 모델과 솔버로 하는게 더 낫다는걸 증명해보이자. 비용이 많이 나오면 안되니까 적당한 양의 실험을 하자."
설계(.omc/plan/2026-10-07-llm-vs-solver.md):
- 문제 3개: 가상 20명×사업 4, 가상 50명×10, 시연 실제 형식 100명×20(demo/org-n100). 서비스 설정(최소 투입률 30% 등).
- LLM 조건: A 원자료(기술·요구·단가·가용률·예산·정원 + 협업 점수) / B 우리 점수(S·C 숫자)까지. B는 위반이 있으면 목록을 돌려주고 1회 수정.
- 모델: 외부 gpt-6-luna(추론 high -- LLM에게 가장 유리하게), 사내 대리 GLM 5.3(추론 high). 사용자 결정: LLM 비교는 외부·사내 함께.
  GLM 추론 max는 시범(100명)에서 출력 한도 65,536토큰을 추론에 다 써 답을 못 냈다(884초, $0.33 -- llm-vs-solver-pilot.json) -> high.
- 채점: 현행 평가기(core/evaluate/plan_eval) -- 위반(정원 초과·예산·가용률·동시 사업·투입률 범위), 빈자리, 목적값·배치 품질.
- 비교: MILP 안 A(서비스 설정), 단순 규칙(기술 1등 우선). 비용 상한을 넘으면 남은 호출을 건너뛴다.
결과를 미리 정하지 않는다: LLM이 잘하면 그대로 기록한다. 사업 효과는 NOT_CALIBRATED.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import time
from pathlib import Path

from rehearsal.run import RESULTS, _env_info, _now

ROOT = Path(__file__).resolve().parents[1]
OUT = RESULTS / "llm-vs-solver.json"
COST_CAP_USD = 10.0
GLM_URL = "https://api.z.ai/api/paas/v4"
MODELS = {
    "luna": {"label": "외부 gpt-6-luna(추론 high)", "model": "gpt-6-luna", "effort": "high",
             "price": {"input": 0.10, "output": 0.50}},
    "glm": {"label": "사내 대리 GLM 5.3(추론 high)", "model": "glm-5.3", "effort": "high",
            "price": {"input": 1.40, "output": 4.40, "cached": 0.26}},
    "gpt55": {"label": "외부 상위 gpt-5.5(추론 high)", "model": "gpt-5.5", "effort": "high",
              "price": {"input": 5.0, "output": 30.0}},
}
SYSTEM = ("너는 SI 회사의 인력 배치 담당자다. 주어진 데이터로 6개월 계획의 인력 배치를 만든다. 규칙(제약)을 하나도 어기지 않으면서 "
          "목적 점수를 최대로 만드는 배치를 찾는다. 출력은 JSON 하나뿐이다: "
          '{"assignments": [{"person_id": "...", "project_id": "...", "alloc": 0.5}, ...]}. 설명·코드 블록 없이 JSON만.')


class Problem:
    def __init__(self, name, graph, S, C, params):
        self.name, self.graph, self.S, self.C, self.params = name, graph, S, C, params


def problems(only: list[str] | None = None) -> list[Problem]:
    from api.settings import PlacementSettings
    from core.datagen.generator import generate_dataset
    from core.datagen.parse_reviews import parse_reviews_rule_based
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.scoring.engine import ScoringEngine
    out = []
    for name, n, j, seed in (("small-20x4", 20, 4, 1), ("medium-50x10", 50, 10, 2)):
        if only and name not in only:
            continue
        ds = generate_dataset(n, j, seed=seed)
        g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
        e = ScoringEngine(g)
        out.append(Problem(name, g, e.skill_matrix({}), e.synergy_matrix(),
                           PlacementSettings().to_milp_params(n_people=n)))
    if not only or "demo-100x20" in only:
        b, rep = load_bundle(ROOT / "demo" / "org-n100")
        ds, parsed = to_dataset(b, rep)
        g = MemoryGraph.build(ds, parsed)
        e = ScoringEngine(g)
        out.append(Problem("demo-100x20", g, e.skill_matrix({}), e.synergy_matrix(),
                           PlacementSettings().to_milp_params(n_people=len(ds.people))))
    return out


def prompt(pb: Problem, condition: str) -> str:
    """같은 규칙·목적 설명. A는 원자료(적합도를 LLM이 계산), B는 우리 점수(S)를 숫자로 준다. 협업 점수(C)는 둘 다 준다
    (리뷰 원문을 통째로 넣을 수 없어 요약값을 준다)."""
    from core.optimize.milp import _overfamiliar_pairs, pruned_pairs
    g, p = pb.graph, pb.params
    needed = sorted({rq.skill for pj in g.projects for rq in pj.requirements})
    people = []
    for i, pe in enumerate(g.people):
        row = {"id": pe.id, "grade": pe.grade.value, "monthly_rate": pe.monthly_rate,
               "availability_by_month": [round(a, 2) for a in pe.availability]}
        if condition == "A":
            row["skills"] = {s: int(g.skill_levels[i, g.skill_index[s]]) for s in needed
                             if s in g.skill_index and g.skill_levels[i, g.skill_index[s]] > 0}
        people.append(row)
    projects = []
    for j, pj in enumerate(g.projects):
        row = {"id": pj.id, "months": list(pj.months), "monthly_budget": pj.monthly_budget,
               "grade_headcount": {k.value: v for k, v in pj.grade_headcount.items() if v}}
        if condition == "A":
            row["requirements"] = [{"skill": rq.skill, "min_level": rq.min_level} for rq in pj.requirements]
        else:
            row["skill_fit"] = {g.people[i].id: round(float(pb.S[i, j]), 3)
                                for i in sorted(range(len(g.people)), key=lambda i: -pb.S[i, j]) if pb.S[i, j] > 0}
        projects.append(row)
    reward = [[g.people[a].id, g.people[b].id, round(float(pb.C[a, b]), 3)]
              for a, b in pruned_pairs(pb.C, p.pair_keep_ratio, p.max_pairs)]
    familiar = [[g.people[a].id, g.people[b].id]
                for a, b in sorted(_overfamiliar_pairs(g, p.clique_threshold_months, p.clique_window_months))]
    fit_rule = ("기술 적합 S(사람, 사업) = 그 사업의 요구 기술마다 min(보유 레벨 / 최소 레벨, 1)을 구해 평균한 값(보유하지 않은 기술은 0)."
                if condition == "A" else "기술 적합 S(사람, 사업)는 projects[].skill_fit에 숫자로 주었다(없는 사람은 0).")
    rules = f"""규칙(하나라도 어기면 쓸 수 없는 배치):
1. 각 배정은 (person_id, project_id, alloc). alloc은 그 사업 기간 내내 같은 투입률이며 {p.min_alloc}~1.0 사이.
2. 가용률: 사람마다 각 달(0~5)에, 그 달에 진행되는 사업들의 alloc 합 ≤ availability_by_month[달].
3. 예산: 사업마다 진행되는 각 달에 Σ(monthly_rate × alloc) ≤ monthly_budget.
4. 동시 사업: 한 사람이 같은 달에 들어가는 사업은 {p.max_concurrent_projects}개 이하.
5. 정원: grade_headcount에 적힌 등급은 그 인원을 넘길 수 없다(모자라면 빈자리). 적히지 않은 등급은 예산·가용률 안에서 더 넣어도 된다.
목적(최대화): Σ S(사람, 사업) × alloc + {p.lam} × (같은 사업에 함께 들어간 collaboration_pairs 쌍의 점수 합)
  − {p.mu} × (같은 사업에 함께 들어간 familiar_pairs 쌍의 수) − {p.slack_penalty} × (빈자리 수).
{fit_rule}
빈자리 하나가 −{p.slack_penalty}점이라 정원을 채우는 것이 가장 중요하다. 규칙 위반은 점수와 상관없이 실패다."""
    data = {"people": people, "projects": projects, "collaboration_pairs": reward, "familiar_pairs": familiar}
    return rules + "\n\n데이터(JSON):\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def client_for(key: str):
    from openai import OpenAI
    if key == "glm":
        return OpenAI(base_url=GLM_URL, api_key=os.environ["ZAI_API_KEY"], timeout=1800, max_retries=1)
    return OpenAI(timeout=1800, max_retries=1)


def call(key: str, messages: list[dict]) -> dict:
    m = MODELS[key]
    t = time.monotonic()
    resp = client_for(key).chat.completions.create(
        model=m["model"], messages=messages, response_format={"type": "json_object"},
        extra_body={"reasoning_effort": m["effort"]})
    lat = time.monotonic() - t
    u = resp.usage
    ptd, ctd = getattr(u, "prompt_tokens_details", None), getattr(u, "completion_tokens_details", None)
    cached = int(getattr(ptd, "cached_tokens", 0) or 0) if ptd else 0
    price = m["price"]
    cost = ((u.prompt_tokens - cached) * price["input"] + cached * price.get("cached", price["input"])
            + u.completion_tokens * price["output"]) / 1e6
    return {"content": resp.choices[0].message.content or "", "latency_s": round(lat, 1),
            "in_tokens": u.prompt_tokens, "cached_tokens": cached, "out_tokens": u.completion_tokens,
            "reasoning_tokens": int(getattr(ctd, "reasoning_tokens", 0) or 0) if ctd else 0,
            "cost_usd": round(cost, 5), "model": getattr(resp, "model", m["model"])}


def parse(pb: Problem, content: str) -> tuple[list, dict]:
    from core.optimize.types import AssignEntry
    info = {"parse_error": None, "unknown_ids": 0, "invalid_entries": 0, "duplicates": 0}
    try:
        rows = json.loads(content).get("assignments", [])
        assert isinstance(rows, list)
    except Exception as exc:                                  # noqa: BLE001
        info["parse_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return [], info
    pids, jids = set(pb.graph.pid_index), set(pb.graph.project_index)
    seen, entries = set(), []
    for r in rows:
        try:
            pid, jid, alloc = str(r["person_id"]), str(r["project_id"]), float(r["alloc"])
            assert alloc == alloc and abs(alloc) != float("inf")          # NaN·무한대는 쓸 수 없는 배정
        except Exception:                                     # noqa: BLE001
            info["invalid_entries"] += 1
            continue
        if pid not in pids or jid not in jids:
            info["unknown_ids"] += 1
            continue
        if (pid, jid) in seen:
            info["duplicates"] += 1
            continue
        try:
            entries.append(AssignEntry(person_id=pid, project_id=jid, alloc=alloc))
            seen.add((pid, jid))
        except Exception:                                     # noqa: BLE001 -- 모델 검증에 걸린 배정
            info["invalid_entries"] += 1
    return entries, info


def score(pb: Problem, entries: list) -> dict:
    from collections import Counter
    from core.evaluate.plan_eval import evaluate_plan
    ev = evaluate_plan(pb.graph, pb.S, pb.C, pb.params, entries)
    o = ev.objective
    return {"objective": round(o.total, 4), "quality": round(o.skill + o.synergy + o.overfamiliarity, 4),
            "skill": round(o.skill, 4), "synergy": round(o.synergy, 4),
            "unfilled_seats": int(sum(s.missing for s in ev.shortfalls)),
            "violations": len(ev.violations), "violation_codes": dict(sorted(Counter(v.code for v in ev.violations).items())),
            "violation_samples": [f"{v.code} {v.location}: {round(v.actual, 3)} > {round(v.limit, 3)}"
                                  for v in ev.violations[:25]],
            "assignments": len(entries)}


def baselines(pb: Problem) -> dict:
    from core.optimize.greedy import solve_greedy
    from core.optimize.milp import solve_milp_assessment
    t = time.perf_counter()
    a = solve_milp_assessment(pb.graph, pb.S, pb.C, pb.params)
    milp_s = time.perf_counter() - t
    ev = a.native_capture.evidence if a.native_capture is not None else None
    milp = {**score(pb, list(a.accepted.plan.entries)), "seconds": round(milp_s, 1),
            "termination": getattr(ev, "termination_reason", None)}
    t = time.perf_counter()
    greedy = solve_greedy(pb.graph, pb.S, pb.params.max_concurrent_projects, min_alloc=pb.params.min_alloc)
    return {"milp": milp, "greedy": {**score(pb, list(greedy.entries)), "seconds": round(time.perf_counter() - t, 3)}}


def run_llm(pb: Problem, key: str, condition: str, budget: dict, repair: bool) -> dict:
    with budget["lock"]:
        over = budget["spent"] >= COST_CAP_USD
    if over:
        return {"skipped": f"비용 상한 ${COST_CAP_USD} 도달"}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt(pb, condition)}]
    try:
        r1 = call(key, messages)
    except Exception as exc:                                  # noqa: BLE001 -- 실패도 결과로 남긴다
        return {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    with budget["lock"]:
        budget["spent"] += r1["cost_usd"]
    entries, info = parse(pb, r1["content"])
    first = {**score(pb, entries), **info, **{k: v for k, v in r1.items() if k != "content"}, "raw": r1["content"][:20000]}
    out = {"first": first}
    if repair and first["violations"] and budget["spent"] < COST_CAP_USD and not info["parse_error"]:
        fix = ("네 배치를 같은 규칙으로 채점했더니 위반이 있다(형식: 종류 위치: 실제값 > 한도). 위반이 하나도 없도록 고친 배치 전체를 같은 JSON 형식으로 "
               "다시 내라. 빈자리도 가능한 한 줄여라.\n위반 " + str(first["violations"]) + "건(처음 25건):\n"
               + "\n".join(first["violation_samples"]))
        messages += [{"role": "assistant", "content": r1["content"]}, {"role": "user", "content": fix}]
        try:
            r2 = call(key, messages)
            with budget["lock"]:
                budget["spent"] += r2["cost_usd"]
            e2, info2 = parse(pb, r2["content"])
            out["repaired"] = {**score(pb, e2), **info2, **{k: v for k, v in r2.items() if k != "content"},
                               "raw": r2["content"][:20000]}
        except Exception as exc:                              # noqa: BLE001
            out["repaired"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return out


NOT_RUN = ("1113", "APIConnectionError", "비용 상한")       # 모델 탓이 아닌 미실행(잔액 소진·연결 오류·상한)


def _not_run(res: dict) -> str | None:
    why = res.get("skipped") or res.get("error") or ""
    if not why:
        return None
    if "1113" in why:
        return "Z.ai 계정 잔액 소진(429·1113)"
    return why[:60] if any(k in why for k in NOT_RUN) else None


def summarize(data: dict, pilot: dict | None = None) -> list[dict]:
    """문제·모델(추론 강도)·조건마다: 실행된 시도, 쓸 수 있는 배치, 솔버 대비 최고 품질(쓸 수 있는 것만), 응답 시간(첫 호출), 비용.
    쓸 수 있는 배치 = 답이 있고, 제약 위반 0, 버려진 항목(없는 ID·중복·형식 오류) 0, 빈자리가 솔버보다 많지 않음(수정 기회가 있으면 수정 뒤).
    잔액 소진·연결 오류처럼 실행되지 않은 시도는 분모에서 빼고 '미실행'으로 센다(리뷰 MUST). 시범 기록은 추론 강도가 같을 때만 합치고,
    다르면 따로 행을 둔다."""
    rows = []
    for pr in data["problems"]:
        base = pr["baselines"]["milp"]["quality"]
        base_unfilled = pr["baselines"]["milp"]["unfilled_seats"]
        groups: dict[tuple, list] = {}
        for r in pr["llm"]:
            groups.setdefault((r["model"], data["models"][r["model"]]["effort"], r["condition"], False), []).append(r["result"])
        if pilot:
            for p2 in pilot["problems"]:
                if p2["name"] != pr["name"]:
                    continue
                for r in p2["llm"]:
                    eff = pilot["models"][r["model"]]["effort"]
                    same = eff == data["models"][r["model"]]["effort"]
                    groups.setdefault((r["model"], eff, r["condition"], not same), []).append(r["result"])
        for (key, effort, cond, pilot_only), rs in groups.items():
            usable, quals, lats, cost, fails, not_run = 0, [], [], 0.0, [], []
            for res in rs:
                nr = _not_run(res)
                if nr:
                    not_run.append(nr)
                    continue
                for st in ("first", "repaired"):
                    if res.get(st) and "error" not in res[st]:
                        cost += res[st].get("cost_usd", 0.0)
                if res.get("first") and "error" not in res["first"]:
                    lats.append(res["first"].get("latency_s", 0.0))
                final = res.get("repaired") if res.get("repaired") and "error" not in res["repaired"] else res.get("first")
                if not final or "error" in final:
                    fails.append(((res.get("error") or (final or {}).get("error") or "?"))[:60])
                    continue
                if final.get("parse_error"):
                    fails.append("답 없음(출력 65,536토큰을 추론에 다 씀 -- max_tokens 미지정 설정의 한도)"
                                 if final.get("out_tokens", 0) >= 65000 else f"JSON 오류: {final['parse_error'][:40]}")
                    continue
                dropped = final.get("unknown_ids", 0) + final.get("duplicates", 0) + final.get("invalid_entries", 0)
                bad = []
                if final["violations"]:
                    bad.append(f"위반 {final['violations']}")
                if dropped:
                    bad.append(f"버려진 항목 {dropped}")
                if final["unfilled_seats"] > base_unfilled:
                    bad.append(f"빈자리 {final['unfilled_seats']}(솔버 {base_unfilled})")
                if bad:
                    fails.append("·".join(bad))
                    continue
                usable += 1
                quals.append(final["quality"])
            label = f"{MODELS[key]['label'].split('(')[0]}(추론 {effort}{', 시범' if pilot_only else ''})"
            rows.append({"problem": pr["name"], "model": key, "effort": effort, "label": label, "condition": cond,
                         "attempts": len(rs) - len(not_run), "not_run": len(not_run), "not_run_reasons": sorted(set(not_run)),
                         "usable": usable,
                         "best_pct": round(100 * max(quals) / base, 1) if quals and base else None,
                         "median_latency_s": sorted(lats)[len(lats) // 2] if lats else None,
                         "cost_usd": round(cost, 4), "failures": fails, "solver_s": pr["baselines"]["milp"]["seconds"],
                         "solver_termination": pr["baselines"]["milp"].get("termination")})
    return rows


def render(data: dict, pilot: dict | None = None) -> str:
    summary = summarize(data, pilot)
    srows = "".join(
        f"<tr><td>{html.escape(r['problem'])}</td><td>{html.escape(r['label'])}</td>"
        f"<td>{'원자료(수정 기회 없음)' if r['condition'] == 'A' else '우리 점수 제공(+1회 수정)'}</td>"
        f"<td>{r['usable']} / {r['attempts']}" + (f"<br><small>미실행 {r['not_run']}: {html.escape(', '.join(r['not_run_reasons']))}</small>" if r['not_run'] else "") + "</td>"
        f"<td>{'—' if r['best_pct'] is None else str(r['best_pct']) + '%'}</td>"
        f"<td>{'—' if r['median_latency_s'] is None else str(round(r['median_latency_s'])) + '초'} (솔버 {r['solver_s']}초)</td>"
        f"<td>${r['cost_usd']:.3f}</td><td><small>{html.escape('; '.join(r['failures'])[:220])}</small></td></tr>"
        for r in summary)

    def pct(v, base):
        return "—" if v is None or not base else f"{100 * v / base:.0f}%"
    rows_html = ""
    for pr in data["problems"]:
        if pilot:                                               # 시범 회차도 상세표에 보인다(요약 최고값의 출처)
            extra = [{**r, "tag": f" · 시범(추론 {pilot['models'][r['model']]['effort']})"}
                     for p2 in pilot["problems"] if p2["name"] == pr["name"] for r in p2["llm"]]
            pr = {**pr, "llm": list(pr["llm"]) + extra}
        b = pr["baselines"]
        base_q = b["milp"]["quality"]
        rows_html += f"<h2>{html.escape(pr['name'])}</h2><div class='w'><table><tr><th>방법</th><th>위반</th><th>빈자리</th>" \
                     "<th>배치 품질</th><th>솔버 대비</th><th>시간</th><th>비용</th></tr>"
        rows_html += (f"<tr class='solver'><td>모델+솔버(MILP 안 A)</td><td>{b['milp']['violations']}</td><td>{b['milp']['unfilled_seats']}</td>"
                      f"<td>{b['milp']['quality']:.2f}</td><td>100%</td><td>{b['milp']['seconds']}초</td><td>$0</td></tr>")
        rows_html += (f"<tr><td>단순 규칙(기술 1등 우선)</td><td>{b['greedy']['violations']}</td><td>{b['greedy']['unfilled_seats']}</td>"
                      f"<td>{b['greedy']['quality']:.2f}</td><td>{pct(b['greedy']['quality'], base_q)}{' (빈자리 더 많음)' if b['greedy']['unfilled_seats'] > b['milp']['unfilled_seats'] else ''}</td><td>{b['greedy']['seconds']}초</td><td>$0</td></tr>")
        for run in pr["llm"]:
            for stage in ("first", "repaired"):
                r = run["result"].get(stage)
                if not r:
                    continue
                mlabel = MODELS[run["model"]]["label"].split("(")[0] if run.get("tag") else MODELS[run["model"]]["label"]
                label = f"{mlabel} · {'원자료' if run['condition'] == 'A' else '우리 점수 제공'}" \
                        f"{' · 1회 수정 후' if stage == 'repaired' else ''} · 회차 {run['rep']}"
                if "error" in r:
                    rows_html += f"<tr><td>{html.escape(label)}</td><td colspan='6'>오류: {html.escape(r['error'][:160])}</td></tr>"
                    continue
                codes = ", ".join(f"{k} {v}" for k, v in r["violation_codes"].items())
                extra = f" (JSON 오류)" if r.get("parse_error") else ""
                usable = not r["violations"] and not r.get("parse_error")
                rows_html += (f"<tr><td>{html.escape(label)}{run.get('tag', '')}{extra}</td><td>{r['violations']}<br><small>{html.escape(codes)}</small></td>"
                              f"<td>{r['unfilled_seats']}</td><td>{r['quality']:.2f}</td><td>{pct(r['quality'], base_q) if usable else '—(쓸 수 없음)'}</td>"
                              f"<td>{r['latency_s']}초</td><td>${r['cost_usd']:.3f}</td></tr>")
            if run["result"].get("skipped") or run["result"].get("error") and "first" not in run["result"]:
                why = _not_run(run["result"]) or run["result"].get("skipped") or run["result"].get("error")
                cond_label = "원자료" if run["condition"] == "A" else "우리 점수 제공"
                rows_html += (f"<tr><td>{html.escape(MODELS[run['model']]['label'])} · {cond_label} · 회차 {run['rep']}{run.get('tag', '')}</td>"
                              f"<td colspan='6'>미실행·오류: {html.escape(str(why)[:160])}</td></tr>")
        rows_html += "</table></div>"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM 직접 배치 vs 솔버</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--hi:#eef5f2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#aaa;--acc:#8ab8e0;--line:#3a3a3a;--hi:#24302c}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1000px;margin:auto}}h2{{color:var(--acc)}}.w{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right;vertical-align:top}}th:first-child,td:first-child{{text-align:left}}
th{{color:var(--muted);font-weight:600}}tr.solver{{background:var(--hi)}}small{{color:var(--muted)}}</style></head><body><main>
<h1>LLM에게 배치를 통째로 맡기면? — 모델+솔버와 같은 채점으로 비교</h1>
<p>같은 문제·같은 규칙·같은 목적을 LLM에게 설명하고 JSON 배치를 받았다. 채점은 서비스 평가기(위반·빈자리·목적). "원자료"는 적합도 계산까지 LLM이,
"우리 점수 제공"은 기술 적합(S)·협업(C) 숫자를 주어 최적화만 맡긴 조건(LLM에게 가장 유리) — 이 조건에서만 위반이 있으면 목록을 돌려주고 1회 수정하게 했다.
배치 품질 = 기술 + 협업 − 익숙한 쌍(빈자리 감점 제외). <b>위반이 1건이라도 있으면 그대로 쓸 수 없는 배치</b>다. 데이터는 가상, 사업 효과는 NOT_CALIBRATED.
총비용 ${data['spent_usd']:.2f}{f" + 시범 ${pilot['spent_usd']:.2f}" if pilot else ""}.</p>
<h2>요약</h2><p>"쓸 수 있는 배치" = 답이 있고 제약 위반 0, 버려진 항목 0, 빈자리가 솔버보다 많지 않음(수정 기회가 있으면 수정 뒤).
품질은 쓸 수 있는 배치 중 최고를 솔버(MILP 안 A) 대비 %로 — 빈자리 감점을 뺀 지표다. 잔액 소진·연결 오류로 <b>실행되지 않은 시도는 분모에서 뺐다</b>.
응답 시간은 첫 호출 기준(수정 호출 제외).</p>
<p><b>읽을 때 주의</b>: 칸마다 2~3회뿐이라 표본이 작다. 100명 문제의 솔버 해는 30초 시간 한도에서 멈춘 해(최선 증명 전)라 실제 최적은 더 높을 수 있다 —
LLM 대비 %는 보수적이다. 비용은 성공한 호출만 센 <b>하한</b>이다(실패 호출·SDK 자동 재시도 1회 비용 제외 — Z.ai 콘솔 청구와 대조 필요).
GLM의 "답 없음"은 max_tokens를 지정하지 않은 이 설정에서 추론이 출력 한도를 다 쓴 것이다. 원자료 조건(A)은 수정 기회 없이 첫 답만 봤다.
시범 1회(100명, 우리 점수 제공)는 같은 추론 강도면 같은 칸에 합쳤고, GLM 시범(추론 max)은 따로 표시했다.</p>
<div class='w'><table><tr><th>문제</th><th>모델</th><th>조건</th><th>쓸 수 있는 배치</th><th>최고 품질(솔버 대비)</th><th>응답 시간(중앙)</th><th>비용</th><th>실패 이유</th></tr>{srows}</table></div>
<h2>문제별 상세</h2>{rows_html}<p>원자료: rehearsal/results/llm-vs-solver.json · 도구: rehearsal/llm_vs_solver.py</p></main></body></html>"""


def main() -> None:
    from core.config import load_env
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="시연 문제·조건 B로 모델별 1회만(토큰·비용 확인)")
    ap.add_argument("--models", nargs="+", default=["luna", "glm"])
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--render-only", action="store_true", help="실험 없이 저장된 기록으로 HTML만 다시 그린다")
    args = ap.parse_args()
    if args.render_only:
        data = json.loads(OUT.read_text("utf-8"))
        pilot_path = RESULTS / "llm-vs-solver-pilot.json"
        pilot = json.loads(pilot_path.read_text("utf-8")) if pilot_path.is_file() else None
        data["summary"] = summarize(data, pilot)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        OUT.with_suffix(".html").write_text(render(data, pilot), "utf-8")
        print(json.dumps(data["summary"], ensure_ascii=False, indent=1))
        return
    load_env()
    import threading
    from concurrent.futures import ThreadPoolExecutor
    budget = {"spent": 0.0, "lock": threading.Lock()}
    data = {"env": _env_info(), "started_at": _now(), "cost_cap_usd": COST_CAP_USD, "models": MODELS, "problems": []}
    for pb in problems(["demo-100x20"] if args.pilot else args.only):
        print(f"{pb.name}: baselines ...", flush=True)
        pr = {"name": pb.name, "people": len(pb.graph.people), "projects": len(pb.graph.projects),
              "baselines": baselines(pb), "llm": []}
        print(f"  milp {json.dumps({k: pr['baselines']['milp'][k] for k in ('quality', 'unfilled_seats', 'violations', 'seconds')})}"
              f" greedy {json.dumps({k: pr['baselines']['greedy'][k] for k in ('quality', 'unfilled_seats', 'violations')})}", flush=True)
        conds = ["B"] if args.pilot else ["A", "B"]
        jobs = [(cond, key, rep) for cond in conds for key in args.models
                for rep in range(1, (1 if args.pilot else args.reps) + 1)]
        # 호출은 네트워크 대기라 동시에 4개까지(GLM 한 번이 수 분~15분) -- 결과는 정해진 순서로 남긴다
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [(job, pool.submit(run_llm, pb, job[1], job[0], budget, job[0] == "B" and not args.pilot))
                       for job in jobs]
            for (cond, key, rep), fut in futures:
                res = fut.result()
                pr["llm"].append({"model": key, "condition": cond, "rep": rep, "result": res})
                f = res.get("first", {})
                print(f"  {key} {cond} #{rep}: " + (json.dumps({k: f.get(k) for k in ("violations", "unfilled_seats", "quality", "latency_s", "in_tokens", "out_tokens", "reasoning_tokens", "cost_usd", "parse_error")}, ensure_ascii=False)
                                                   if f else json.dumps(res, ensure_ascii=False)[:300])
                      + (f" | repaired {json.dumps({k: res['repaired'].get(k) for k in ('violations', 'unfilled_seats', 'quality', 'cost_usd')})}" if res.get("repaired") and "error" not in res["repaired"] else "")
                      + f" | spent ${budget['spent']:.3f}", flush=True)
        data["problems"].append(pr)
        data["spent_usd"] = round(budget["spent"], 4)
        out = RESULTS / ("llm-vs-solver-pilot.json" if args.pilot else "llm-vs-solver.json")
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    data["finished_at"] = _now()
    data["spent_usd"] = round(budget["spent"], 4)
    out = RESULTS / ("llm-vs-solver-pilot.json" if args.pilot else "llm-vs-solver.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    out.with_suffix(".html").write_text(render(data), "utf-8")
    print(f"written {out} · spent ${budget['spent']:.3f}")


if __name__ == "__main__":
    main()
