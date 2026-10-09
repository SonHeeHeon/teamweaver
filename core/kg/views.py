"""지식 그래프의 보기(2026-10-07, claude-a) -- 그래프 하나(core/kg/graph.py) 위의 질의. 지금 화면으로 답할 수 없는 질문을 기준으로 골랐다.

1. skill_map: 조직 기술 지도 -- "2명 이하만 가진 기술 중 앞으로 6개월 수요가 있는 것은?", "수요 대비 공급이 모자란 기술은?"
2. project_evidence: 사업별 근거(인사팀 소명의 구조) -- 요구 기술 충족, 같은 산업·고객사 경험, 함께 일해 본 이력, 평가,
   그리고 "왜 다른 사람이 아니었나"(같은 등급 후보로 바꿨을 때 현행 평가기로 잰 점수 변화·생기는 위반).
"""
from __future__ import annotations

from collections import Counter

from core.ingest.convert import level_from_months
from core.kg.graph import KnowledgeGraph

PRACTICAL_MONTHS = 12          # "실무 가능" 경력(개월) -- 입력 단계 레벨 경계 12/36/60/96개월의 첫 경계
EXPERT_MONTHS = 36


def _skills_of(kg: KnowledgeGraph, person_node: str) -> dict[str, dict]:
    return {e["dst"]: e for e in kg.out(person_node, "HAS_SKILL")}


def skill_map(kg: KnowledgeGraph, *, busy: set[str] | None = None) -> list[dict]:
    """기술마다 공급(실무 가능 보유자·숙련자·지금 비어 있는 보유자)과 수요(요구하는 진행·제안 사업, 필요 인원).
    busy: 지금 사업에 들어가 있는 사람 id(없으면 CURRENT_ON 간선으로 센다)."""
    if busy is None:
        busy = {e["src"].split(":", 1)[1] for e in kg.edges_of("CURRENT_ON")}
    rows = []
    for sk in kg.nodes_of("skill"):
        holders = [e for e in kg.inc(sk["id"], "HAS_SKILL")]
        practical = [e for e in holders if (e.get("months") or 0) >= PRACTICAL_MONTHS]
        expert = [e for e in practical if e["months"] >= EXPERT_MONTHS]
        free = [e for e in practical if e["src"].split(":", 1)[1] not in busy]
        # 레벨별 보유자 수를 기술마다 한 번만 센다(실험 E8b: 요구마다 보유자 전원의 레벨을 다시 계산했다 -- 관계 종류별 색인과 함께 고쳐
        # 3,000명 조직 기술 지도 671 ms → 31 ms)
        by_level = Counter(level_from_months(h["months"]) for h in holders if (h.get("months") or 0) > 0)
        demand = []
        for e in kg.inc(sk["id"], "REQUIRES"):
            pj = kg.nodes[e["src"]]
            need_lv = level_from_months(e.get("min_months") or 0)
            qualified = sum(c for lv, c in by_level.items() if lv >= need_lv)
            demand.append({"project_id": pj["project_id"], "project": pj["label"], "proposal": pj.get("proposal", False),
                           "headcount": e.get("headcount") or 1, "min_months": e.get("min_months"), "qualified_people": qualified})
        need = sum(d["headcount"] for d in demand)
        rows.append({
            "skill": sk["label"], "category": sk.get("category"),
            "holders": len(holders), "practical": len(practical), "expert": len(expert), "practical_free": len(free),
            "demand_projects": len(demand), "demand_headcount": need,
            "proposal_headcount": sum(d["headcount"] for d in demand if d["proposal"]),
            # 신규 제안 수요 - 지금 배치되지 않은 실무 가능 보유자(참고: 양수면 대기 인력만으로 못 채울 수 있다 -- 투입률·가용률은 따로 안 봄)
            "proposal_gap": max(0, sum(d["headcount"] for d in demand if d["proposal"]) - len(free)),
            "coverage": round(len(practical) / need, 2) if need else None,
            "scarce": need > 0 and len(practical) <= 2,
            "demand": sorted(demand, key=lambda d: (-d["headcount"], d["project_id"])),
            "practical_people": sorted(e["src"].split(":", 1)[1] for e in practical),
        })
    return sorted(rows, key=lambda r: (not r["scarce"], -r["proposal_gap"],
                                       r["coverage"] if r["coverage"] is not None else 1e9, -r["demand_headcount"], r["skill"]))


def project_evidence(kg: KnowledgeGraph, project_id: str, entries, *, graph=None, S=None, C=None, params=None,
                     alternatives: int = 2, candidates: int = 5) -> dict:
    """사업 하나의 근거. entries: 배치(AssignEntry 목록 -- 안 A·적용된 배치·현재 배치). graph·S·C·params를 주면
    "왜 다른 사람이 아니었나"를 현행 평가기(core/evaluate/plan_eval)로 잰다: 팀원마다 같은 등급의 다른 사람(기술 적합 상위 candidates명)으로
    바꿨을 때 전체 점수 변화와 새로 생기는 위반. 바꿔도 위반이 없고 점수가 오르는 사람이 있으면 그대로 보인다(숨기지 않는다)."""
    pnode = f"project:{project_id}"
    pj = kg.nodes[pnode]
    reqs = {e["dst"]: e for e in kg.out(pnode, "REQUIRES")}
    team = [en for en in entries if en.project_id == project_id]
    team_ids = {en.person_id for en in team}
    members = []
    for en in team:
        node = f"person:{en.person_id}"
        skills = _skills_of(kg, node)
        coverage = []
        for sk, rq in reqs.items():
            have = skills.get(sk, {}).get("months") or 0
            need = rq.get("min_months") or 0
            # 충족 판정은 점수(S)와 같은 기준: 경력 개월을 레벨 구간(12/36/60/96)으로 바꿔 요구 레벨과 비교한다(리뷰 SHOULD --
            # 개월로 비교하면 "S는 충족인데 근거는 미달"처럼 어긋난다). 개월 숫자는 함께 보인다.
            ok = have > 0 and level_from_months(have) >= level_from_months(need)
            coverage.append({"skill": kg.nodes[sk]["label"], "months": have, "min_months": need,
                             "level": level_from_months(have) if have else 0, "min_level": level_from_months(need),
                             "status": "met" if ok else ("below" if have > 0 else "missing")})
        past = [e for e in kg.out(node, "WORKED_ON")]
        same_industry = [e for e in past if pj.get("industry") and kg.nodes[e["dst"]].get("industry") == pj["industry"]]
        same_client = [e for e in past if pj.get("client") and kg.nodes[e["dst"]].get("client") == pj["client"]]
        industry_projects = {e["dst"] for e in same_industry}       # 서로 다른 과거 사업 수(같은 사업 여러 기간은 하나)
        cowork = []
        for e in kg.out(node, "COWORKED") + kg.inc(node, "COWORKED"):
            other = e["dst"] if e["src"] == node else e["src"]
            if other.split(":", 1)[1] in team_ids:
                cowork.append({"with": other.split(":", 1)[1], "months_total": e.get("months_total"),
                               "months_recent": e.get("months_recent")})
        reviews = [{"from": e["src"].split(":", 1)[1], "polarity": e.get("polarity"), "labels": e.get("labels", []),
                    **({"quotes": e["quotes"]} if e.get("quotes") else {})}
                   for e in kg.inc(node, "REVIEWED") if e["src"].split(":", 1)[1] in team_ids]
        members.append({
            "person_id": en.person_id, "name": kg.nodes[node]["label"], "grade": kg.nodes[node].get("grade"), "alloc": en.alloc,
            "skill_fit": round(float(S[graph.pid_index[en.person_id], graph.project_index[project_id]]), 3)
            if S is not None and graph is not None else None,
            "requirements": coverage,
            "same_industry_projects": len(industry_projects) if pj.get("industry") else None,      # None = 사업 산업을 모름
            "same_industry_months": sum(e.get("months") or 0 for e in same_industry) if pj.get("industry") else None,
            "same_client_projects": sorted({kg.nodes[e["dst"]]["label"] for e in same_client}),
            "cowork_in_team": sorted(cowork, key=lambda c: -(c["months_total"] or 0)),
            "reviews_from_team": reviews,
        })
    coverage = []
    for sk, rq in reqs.items():
        need, heads = rq.get("min_months") or 0, rq.get("headcount") or 1
        n = sum(1 for m in members for c in m["requirements"] if c["skill"] == kg.nodes[sk]["label"] and c["status"] == "met")
        coverage.append({"skill": kg.nodes[sk]["label"], "headcount": heads, "min_months": need, "met_by": n,
                         "status": "met" if n >= heads else "short"})
    out = {"project_id": project_id, "project": pj["label"], "client": pj.get("client"),
           "client_inferred": pj.get("client_inferred", False), "industry": pj.get("industry"),
           "proposal": pj.get("proposal", False), "requirements": coverage, "members": members}
    if graph is not None and S is not None and C is not None and params is not None and team:
        out["alternatives"] = _alternatives(kg, project_id, list(entries), team, graph, S, C, params, alternatives, candidates)
    return out


def _alternatives(kg, project_id, entries, team, graph, S, C, params, keep: int, pool: int) -> dict:
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import AssignEntry
    j = graph.project_index[project_id]
    base = evaluate_plan(graph, S, C, params, entries)
    base_v = {(v.code, v.location) for v in base.violations}
    team_ids = {en.person_id for en in team}
    grade = {p.id: p.grade for p in graph.people}
    out = {}
    for en in team:
        cands = [p.id for p in graph.people if p.grade == grade[en.person_id] and p.id not in team_ids]
        cands = sorted(cands, key=lambda pid: (-S[graph.pid_index[pid], j], pid))[:pool]
        rows = []
        for cid in cands:
            swapped = [e for e in entries if not (e.person_id == en.person_id and e.project_id == project_id)]
            # 나가는 사람의 투입 형태(월별 투입률 포함)를 그대로 -- 교체 효과만 재게(What-if와 같은 규칙, 리뷰 MUST)
            swapped.append(AssignEntry(person_id=cid, project_id=project_id, alloc=en.alloc,
                                       monthly_alloc=getattr(en, "monthly_alloc", None)))
            ev = evaluate_plan(graph, S, C, params, swapped)
            new_v = sorted({f"{v.code}:{v.location}" for v in ev.violations if (v.code, v.location) not in base_v})
            d = ev.objective
            rows.append({"person_id": cid, "name": kg.nodes.get(f"person:{cid}", {}).get("label", cid),
                         "skill_fit": round(float(S[graph.pid_index[cid], j]), 3),
                         "delta_total": round(d.total - base.objective.total, 4),
                         "delta_skill": round(d.skill - base.objective.skill, 4),
                         "delta_synergy": round(d.synergy - base.objective.synergy, 4),
                         "new_violations": new_v[:5]})
        rows.sort(key=lambda r: (bool(r["new_violations"]), -r["delta_total"]))
        out[en.person_id] = rows[:keep]
    return out
