"""지식 그래프(2026-10-07, claude-a): 사람·기술·사업·과거 사업·고객사·산업·평가를 노드·간선 하나로 묶는다.

사용자 결정("용도별로 다 각각 만들어야 하나?" → 아니다): 그래프는 하나이고, 용도별로는 그 위의 보기(core/kg/views.py)만 다르다 --
조직 기술 지도, 사업별 근거(인사팀 소명), 기술 이름 의미 레이어. 그래프 DB는 쓰지 않는다(8월 실험에서 이점 0, docs/project-context.md).

원천은 실제 시스템 형식 CSV 묶음(Bundle.tables)과 그것을 변환한 Dataset이다. 입력 단계(core/ingest/convert)와 같은 규칙을 따른다:
최근 10년(lookback) 안에 쓴 기술만, 경력은 최대 120개월; 과거 사업도 lookback 안에 끝난 것만. 점수(S·C)와 그래프 사실이 어긋나지 않게.

노드 id: "person:<id>", "skill:<이름>", "project:<id>", "past:<project_code>", "client:<이름>", "industry:<이름>".
간선 type: HAS_SKILL, REQUIRES, WORKED_ON, FOR_CLIENT, IN_INDUSTRY, COWORKED(두 사람, 작은 id → 큰 id), REVIEWED, CURRENT_ON, ASSIGNED.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# 사업 부문(projects.sector) ↔ 과거 이력 산업(work_history.industry). 시연 묶음 값 기준(core/ingest/org_profile).
SECTOR_INDUSTRY = {"대외금융": "금융", "대내": "그룹사(대내)", "대외공공": "공공"}
RECENT_WINDOW_MONTHS = 36          # 익숙한 쌍 판단 기간(서비스 기본, api/settings)과 같은 "최근" -- build_kg(recent_window=)로 바꾼다


@dataclass
class KnowledgeGraph:
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    _out: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    _in: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    # 관계 종류별 색인(2026-10-09, 실험 E8b): 전체 집계가 종류 하나(예: 지금 배치 CURRENT_ON)를 찾으려고 모든 간선을 훑지 않게.
    # 3,000명 조직 기술 지도 671 ms → 이 색인과 views.skill_map의 "레벨별 보유자 한 번 세기"를 함께 고쳐 31 ms(따로 잰 값은 없다)
    _by_type: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))

    def add_node(self, nid: str, ntype: str, label: str, **props) -> str:
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "type": ntype, "label": label, **props}
        return nid

    def add_edge(self, etype: str, src: str, dst: str, **props) -> dict:
        e = {"type": etype, "src": src, "dst": dst, **props}
        self.edges.append(e)
        self._out[src].append(len(self.edges) - 1)
        self._in[dst].append(len(self.edges) - 1)
        self._by_type[etype].append(len(self.edges) - 1)
        return e

    def out(self, nid: str, etype: str | None = None) -> list[dict]:
        return [self.edges[k] for k in self._out.get(nid, []) if etype is None or self.edges[k]["type"] == etype]

    def inc(self, nid: str, etype: str | None = None) -> list[dict]:
        return [self.edges[k] for k in self._in.get(nid, []) if etype is None or self.edges[k]["type"] == etype]

    def edges_of(self, etype: str) -> list[dict]:
        return [self.edges[k] for k in self._by_type.get(etype, [])]

    def nodes_of(self, ntype: str) -> list[dict]:
        return [n for n in self.nodes.values() if n["type"] == ntype]

    def stats(self) -> dict:
        nt, et = defaultdict(int), defaultdict(int)
        for n in self.nodes.values():
            nt[n["type"]] += 1
        for e in self.edges:
            et[e["type"]] += 1
        return {"nodes": dict(sorted(nt.items())), "edges": dict(sorted(et.items()))}

    def export(self, node_ids=None, edge_filter=None) -> dict:
        """화면·PDF용 JSON(노드·간선). node_ids를 주면 그 노드끼리의 간선만."""
        keep = set(self.nodes) if node_ids is None else set(node_ids)
        edges = [e for e in self.edges if e["src"] in keep and e["dst"] in keep and (edge_filter is None or edge_filter(e))]
        return {"nodes": [self.nodes[n] for n in sorted(keep) if n in self.nodes], "edges": edges}


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else str(v)


def _client_of(project_name: str, clients: list[str]) -> str | None:
    """사업 이름 안에 들어 있는 고객사명(가장 긴 것) -- **추정값**(원천 projects.csv에 고객사 칸이 없다). 단어 경계를 본다:
    고객사명 앞뒤가 공백·괄호·문장부호·문자열 끝이어야 한다("SK"가 "SKT" 안에서 걸리지 않게)."""
    hits = [c for c in clients if c and re.search(r"(?:^|[\s\[\](){}·,/])" + re.escape(c) + r"(?=$|[\s\[\](){}·,/])",
                                                  project_name)]
    return max(hits, key=len) if hits else None


def build_kg(bundle, dataset, parsed=None, *, skill_dictionary=True, partial_credit: float | None = None,
             reveal_text: bool | None = None, recent_window: int = RECENT_WINDOW_MONTHS) -> KnowledgeGraph:
    """bundle: core.ingest.loader.Bundle, dataset: core.ingest.convert.to_dataset 결과, parsed: 리뷰 판정(ParsedReview 목록).
    skill_dictionary·partial_credit: 기술 이름 사전과 하위 기술 부분 인정(core/ingest/skills) -- **to_dataset과 같은 값을 넘긴다**(기본도 같다).
    입력 단계와 같은 함수(person_skill_months)로 경력을 만들어 그래프 근거가 점수 S와 같은 기준이 된다(리뷰 MUST 2026-10-09).
    인정받은 경력의 HAS_SKILL 간선에는 출처(implied_from: 하위 기술·그 경력·깊이·계수)를 단다 -- 본인이 적은 경력과 구분해 보이게.
    reveal_text: 평가 원문 문장을 간선에 넣을지. 기본은 manifest가 명시적으로 가상(synthetic=true)일 때만 -- 실데이터 평가 원문은
    화면·AI에 보내지 않는다는 결정(K5)과 같다. 실데이터는 항목 라벨(review_items: "좋은 점: 소통")만 넣고, 사람 이름(표시명)과
    과거 업무 요약(summary)도 넣지 않는다(사람은 id로만 -- 기존 근거 색인과 같다).
    recent_window: "최근 함께 일함" 기간(개월, 서비스 익숙한 쌍 기준과 맞춘다).
    교체 기록(replacements.csv)은 개인에게 민감할 수 있어 넣지 않는다."""
    if reveal_text is None:
        reveal_text = bundle.manifest.get("synthetic") is True
    from core.ingest.convert import LOOKBACK_MONTHS, level_from_months, lookback_start
    from core.ingest.skills import load_dictionary, person_skill_months
    sd = load_dictionary() if skill_dictionary is True else (skill_dictionary or None)
    credit = (sd.narrower_credit if partial_credit is None else partial_credit) if sd else 0.0
    canon = sd.canonical if sd else (lambda s: s)

    class _Tables:                                   # Bundle.tables 키는 "people.csv" 같은 파일 이름이다
        def get(self, name, default=None):
            return bundle.tables.get(name if name.endswith(".csv") else f"{name}.csv", default if default is not None else [])
    t = _Tables()
    since = lookback_start(bundle.horizon[0])
    cutoff = bundle.horizon[0] - dt.timedelta(days=1)          # 계획 시작 전날(입력 단계 협업 계산과 같은 끝)
    kg = KnowledgeGraph()
    people = {p.id: p for p in dataset.people}
    for r in t.get("people", []):
        pid = r["person_id"]
        if pid not in people:
            continue
        kg.add_node(f"person:{pid}", "person", (r.get("display_name") or pid) if reveal_text else pid, person_id=pid,
                    grade=r.get("career_grade"),
                    role_type=r.get("role_type"), job_family=r.get("job_family"), monthly_rate=people[pid].monthly_rate)
    rows: dict[str, list] = defaultdict(list)
    best: dict[tuple, dict] = {}
    for r in t.get("person_skills", []):
        if f"person:{r['person_id']}" not in kg.nodes or not r.get("experience_months"):
            continue
        last = r.get("last_used_month")
        if last is not None and last < since:                      # 입력 단계와 같이: 최근 10년 안에 쓴 기술만
            continue
        name = canon(r["skill_name"])
        kg.add_node(f"skill:{name}", "skill", name, category=r.get("skill_category"))
        months = min(int(r["experience_months"]), LOOKBACK_MONTHS)
        rows[r["person_id"]].append((r["skill_name"], months))
        key = (r["person_id"], name)                               # 별칭을 합치면 같은 (사람, 표준 기술)이 겹친다 -- 가장 긴 경력 행의 부가 정보
        if key not in best or months > best[key]["months"]:
            best[key] = {"months": months, "project_count": r.get("project_count"), "last_used": _iso(last),
                         "raw_name": r["skill_name"]}
    for pid, rs in rows.items():
        sm = person_skill_months(rs, sd, credit)                   # 입력 단계(to_dataset)와 같은 함수 -- S와 같은 경력
        for name, months in sm.months.items():
            props = dict(best.get((pid, name)) or {"project_count": None, "last_used": None, "raw_name": None})
            props.update(months=months, own_months=sm.own.get(name, 0))
            imp = sm.implied.get(name)
            if imp and imp["months"] > sm.own.get(name, 0):
                props["implied_from"] = {"skill": imp["from"], "months": imp["from_months"], "depth": imp["depth"], "credit": credit}
                if f"skill:{name}" not in kg.nodes:
                    c = sd.lookup(name) if sd else None
                    kg.add_node(f"skill:{name}", "skill", name, category=c.get("category") if c else None)
            kg.add_edge("HAS_SKILL", f"person:{pid}", f"skill:{name}", **props)
    clients = sorted({r.get("client") for r in t.get("work_history", []) if r.get("client")})
    industries = {r.get("industry") for r in t.get("work_history", []) if r.get("industry")}
    proposals = set(bundle.manifest.get("proposals") or [])
    projects = {p.id: p for p in dataset.projects}
    for r in t.get("projects", []):
        jid = r["project_id"]
        if jid not in projects:
            continue
        p = projects[jid]
        industry = SECTOR_INDUSTRY.get(r.get("sector"))            # 대응이 없으면 None(모름) -- "0건"으로 보이지 않게
        if industry not in industries:                              # 과거 이력의 업종 이름과 다르면(실데이터) 역시 모름
            industry = None
        client = _client_of(r.get("project_name", ""), clients)
        kg.add_node(f"project:{jid}", "project", r.get("project_name") or jid, project_id=jid, sector=r.get("sector"),
                    phase=r.get("phase"), months=list(p.months), monthly_budget=p.monthly_budget,
                    grade_headcount={g.value: n for g, n in p.grade_headcount.items() if n},
                    proposal=jid in proposals or r.get("phase") == "제안", client=client, client_inferred=client is not None,
                    industry=industry)
        if client:
            kg.add_node(f"client:{client}", "client", client)
            kg.add_edge("FOR_CLIENT", f"project:{jid}", f"client:{client}")
        if industry:
            kg.add_node(f"industry:{industry}", "industry", industry)
            kg.add_edge("IN_INDUSTRY", f"project:{jid}", f"industry:{industry}")
    reqs: dict[tuple, dict] = {}
    for r in t.get("project_skill_requirements", []):
        if f"project:{r['project_id']}" not in kg.nodes:
            continue
        name = canon(r["skill_name"])
        e = {"min_months": r.get("min_experience_months"), "headcount": r.get("headcount"), "raw_name": r["skill_name"]}
        key = (r["project_id"], name)                              # 입력 단계와 같이: 같은 기술을 다른 이름으로 두 번 요구하면 높은 요구 하나만
        if key in reqs:
            old = reqs[key]
            e = max((old, e), key=lambda x: (level_from_months(x["min_months"] or 0), x["headcount"] or 0))
        reqs[key] = e
    for (jid, name), e in reqs.items():
        kg.add_node(f"skill:{name}", "skill", name)
        kg.add_edge("REQUIRES", f"project:{jid}", f"skill:{name}", **e)
    outcomes = {r["project_code"]: r for r in t.get("project_outcomes", []) if r.get("project_code")}
    for r in t.get("work_history", []):
        end = r.get("end_date")
        if f"person:{r['person_id']}" not in kg.nodes or (end is not None and end < since):
            continue                                                # 최근 10년 안에 끝난 이력만
        code = r.get("project_code") or r.get("work_id")
        past = f"past:{code}"
        if past not in kg.nodes:
            oc = outcomes.get(code, {})
            kg.add_node(past, "past_project", r.get("work_name") or code, project_code=code,
                        client=r.get("client"), industry=r.get("industry"),
                        customer_score=oc.get("customer_score"), schedule=oc.get("schedule"),
                        follow_on=oc.get("follow_on"), closed_month=_iso(oc.get("closed_month")))
            if r.get("client"):
                kg.add_node(f"client:{r['client']}", "client", r["client"])
                kg.add_edge("FOR_CLIENT", past, f"client:{r['client']}")
            if r.get("industry"):
                kg.add_node(f"industry:{r['industry']}", "industry", r["industry"])
                kg.add_edge("IN_INDUSTRY", past, f"industry:{r['industry']}")
        start = r.get("start_date")
        months = None
        if start and end:                                          # 10년 창과 계획 시작 전날로 자른다(입력 단계 협업 계산과 같다)
            s0, e0 = max(start, since), min(end, cutoff)
            months = max(0, (e0.year - s0.year) * 12 + e0.month - s0.month + 1)
        kg.add_edge("WORKED_ON", f"person:{r['person_id']}", past, start=_iso(start), end=_iso(end), months=months,
                    status=r.get("status"), **({"summary": r.get("summary")} if reveal_text else {}))
    for c in dataset.coworks:
        a, b = sorted((c.a_id, c.b_id))
        if f"person:{a}" not in kg.nodes or f"person:{b}" not in kg.nodes:
            continue
        recent = sum(1 for m in c.months_ago if m <= recent_window) if c.months_ago is not None else None
        kg.add_edge("COWORKED", f"person:{a}", f"person:{b}", months_total=c.co_months, months_recent=recent,
                    project_count=c.project_count)
    pol: dict[tuple, list[float]] = defaultdict(list)
    quotes: dict[tuple, list] = defaultdict(list)
    for pr in parsed or []:
        pol[(pr.reviewer_id, pr.reviewee_id)].append(pr.text_polarity)
        if reveal_text:
            quotes[(pr.reviewer_id, pr.reviewee_id)].extend(pr.evidence[:4])
    items: dict[str, list] = defaultdict(list)
    for r in t.get("review_items", []):
        items[r["review_id"]].append(("좋은 점" if r.get("polarity") == "positive" else "아쉬운 점", r.get("item")))
    rounds: dict[tuple, list] = defaultdict(list)
    for r in t.get("reviews", []):
        rounds[(r["reviewer_id"], r["reviewee_id"])].append((r.get("project_code"), r.get("review_round"), r.get("review_id")))
    for (a, b), rs in rounds.items():
        if f"person:{a}" not in kg.nodes or f"person:{b}" not in kg.nodes:
            continue
        ps = pol.get((a, b))
        lab = Counter(f"{k}: {v}" for _, _, rid in rs for k, v in items.get(rid, []) if v)
        e = kg.add_edge("REVIEWED", f"person:{a}", f"person:{b}", rounds=len(rs),
                        project_codes=sorted({c for c, _, _ in rs if c}),
                        polarity=round(sum(ps) / len(ps), 3) if ps else None,
                        labels=[k for k, _ in lab.most_common(6)])
        if reveal_text and quotes.get((a, b)):
            e["quotes"] = quotes[(a, b)][:4]                # 가상 데이터만(원문 공개)
    for c in dataset.current:
        if f"person:{c.person_id}" in kg.nodes and f"project:{c.project_id}" in kg.nodes:
            kg.add_edge("CURRENT_ON", f"person:{c.person_id}", f"project:{c.project_id}", alloc=c.alloc, locked=c.locked)
    return kg


def with_plan(kg: KnowledgeGraph, entries, label: str = "A") -> KnowledgeGraph:
    """계산 결과(배치)를 ASSIGNED 간선으로 얹은 새 그래프(원래 그래프는 그대로)."""
    out = KnowledgeGraph(nodes=copy.deepcopy(kg.nodes), edges=[])
    for e in kg.edges:
        out.add_edge(e["type"], e["src"], e["dst"], **copy.deepcopy({k: v for k, v in e.items() if k not in ("type", "src", "dst")}))
    for en in entries:
        if f"person:{en.person_id}" in out.nodes and f"project:{en.project_id}" in out.nodes:
            out.add_edge("ASSIGNED", f"person:{en.person_id}", f"project:{en.project_id}", alloc=en.alloc, plan=label)
    return out
