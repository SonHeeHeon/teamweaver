"""E8: 지식 그래프 구현 기술 비교 -- RDF 트리플 저장소(Oxigraph) vs 그래프 DB(Neo4j) vs 메모리 그래프(networkx), 기준 = 현재 core/kg.

계획·사전 판정 규칙: .omc/plan/2026-10-09-kg-backend-compare.md, rehearsal/kg_backend_decision.py(이 파일보다 먼저 커밋 -- 6af8da0).
같은 사실(core/kg.build_kg가 CSV 묶음에서 뽑은 것 + 미리 계산 안 A의 배치)을 후보마다 그 기술의 방식으로 적재하고, 같은 질문 4개에 답하게 한다.
답은 정규화해 기준(core/kg)과 비교하고(규칙 1), 지연·적재 시간·메모리·코드 줄 수·운영 요소를 잰다.

  uv run --with pyoxigraph --with rdflib --with neo4j --with networkx --with psutil python -m rehearsal.kg_backends
  (Neo4j: Docker 컨테이너 tw-e8-neo4j, bolt://localhost:17687, 인증 없음 -- 스크립트가 띄우고 끝나면 지운다. --keep-neo4j로 남긴다.)
  --render-only: 저장된 JSON으로 HTML만 다시 만든다.

비교용 라이브러리는 프로젝트 의존성(lock)에 넣지 않는다 -- `uv run --with`로 이 실행에만 붙인다.
2026-10-09 리뷰(Opus 폴백) 반영 후 다시 쟀다: Q4는 간선의 모든 속성 비교, 측정 전 버리는 예열 회차, 질의 다듬기를 코드로 다시 재기,
메모리 두 방법, 95번째 값 계산, 코드 줄 수(AST), 내보내기 왕복은 트리플 내용 비교. 첫 측정은 kg-backends.superseded-1.json.
"""
from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import gc
import hashlib
import html
import inspect
import json
import math
import numbers
import platform
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / "rehearsal" / "results"
OUT_JSON = RESULTS / "kg-backends.json"
OUT_HTML = RESULTS / "kg-backends.html"

from rehearsal.kg_backend_decision import QUESTIONS, SIZES, decide    # noqa: E402

REPS, WARM, LOAD_REPS, BURN_IN = 20, 10, 3, 20
TUNING_BUDGET_S = 120.0                       # 다듬기 작성법 하나당 시간 한도(최소 1회는 잰다)
PRACTICAL, EXPERT = 12, 36                    # core/kg/views.py PRACTICAL_MONTHS·EXPERT_MONTHS와 같다(아래에서 확인)
CHECK_PERSONS = 30                            # Q4 정확성 확인 인원(사람 id 순 앞에서부터)
RDFLIB_CHECKS = 5                             # rdflib(참고)는 느려서 질문마다 앞 5건만 정확성 확인
NEO_CONTAINER, NEO_BOLT = "tw-e8-neo4j", "bolt://localhost:17687"
NEO_IMAGE = "neo4j:5-community"
NEO_ENV = {"NEO4J_AUTH": "none", "NEO4J_server_memory_heap_initial__size": "1G", "NEO4J_server_memory_heap_max__size": "1G",
           "NEO4J_server_memory_pagecache_size": "512M"}
# Q4(한 사람 주변 사실 전부)는 간선의 **모든 속성**을 비교한다(리뷰 MUST -- 처음엔 일부만 봤다). 목록 속성은 기본으로 집합(순서·중복 무시)으로 보고,
# 순서·중복까지 보는 엄격 비교는 따로 기록한다: RDF의 여러 값 속성은 정의상 순서가 없다(순서가 필요하면 rdf:List나 순번 속성을 따로 설계해야 한다).
LIST_KEYS = {"labels", "project_codes", "quotes"}


# ---------------------------------------------------------------- 데이터
def load_size(n: int):
    """시연 연초 계획 묶음(org-n{n}) → (지식 그래프 + 안 A 배치, 사실 추출 ms, 배치가 있는 사업 id, 사람 id)."""
    from api.datasets import extract_bundle_zip
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg, with_plan
    from core.optimize.types import AssignEntry
    name = f"org-n{n}"
    root = ROOT / "demo" / name
    tmp = None
    if not root.exists():
        tmp = tempfile.TemporaryDirectory(prefix="tw-e8-")
        root = extract_bundle_zip((ROOT / "demo" / f"{name}.zip").read_bytes(), Path(tmp.name) / "bundle")
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep)
    t0 = time.perf_counter()
    kg = build_kg(b, ds, parsed)
    extract_ms = (time.perf_counter() - t0) * 1000
    pre = json.loads((ROOT / "demo" / "precomputed" / f"{name}.json").read_text("utf-8"))
    entries = [AssignEntry(**e) for e in pre["optimize"]["plans"][0]["entries"]]
    kg = with_plan(kg, entries)
    if tmp:
        tmp.cleanup()
    projects = sorted({e["dst"] for e in kg.edges if e["type"] == "ASSIGNED"})
    people = sorted(k for k, v in kg.nodes.items() if v["type"] == "person")
    return kg, entries, extract_ms, projects, people


def bare(x: str) -> str:
    return x.split(":", 1)[1] if ":" in x else x


def nv(v):
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, numbers.Integral):
        return int(v)
    if isinstance(v, numbers.Real):
        return round(float(v), 6)
    return str(v)


def clean(v):
    """후보 저장소에 넣을 수 있는 값(스칼라·같은 타입 목록). 사전·섞인 목록은 JSON 문자열."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, numbers.Integral):
        return int(v)
    if isinstance(v, numbers.Real):
        return float(v)
    if isinstance(v, (list, tuple)):
        items = [clean(x) for x in v if x is not None]
        if all(isinstance(x, (bool, int, float, str)) for x in items) and len({type(x) for x in items}) <= 1:
            return items
        return json.dumps(items, ensure_ascii=False, default=str)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def req_t(label, min_months, headcount):
    return (label, nv(min_months or 0), nv(headcount or 1))


def q2_pack(req, months, industry, cowork, reviews) -> dict:
    return {"req": Counter(req), "months": Counter(months), "industry": None if industry is None else Counter(industry),
            "cowork": Counter(cowork), "reviews": Counter(reviews)}


def _cv(k, v, strict):
    if k in LIST_KEYS:
        xs = [] if v is None else [nv(x) for x in (v if isinstance(v, (list, tuple)) else [v])]
        return tuple(xs) if strict else tuple(sorted(set(xs), key=str))
    return nv(v)


def edge_t(etype, direction, other, props, strict=False) -> tuple:
    """간선 하나 → 비교용 튜플(모든 속성, 값이 없거나 빈 목록인 속성은 뺀다 -- Neo4j는 null 속성을, RDF는 빈 값을 저장하지 않는다)."""
    items = []
    for k, v in props.items():
        if k in ("type", "src", "dst"):
            continue
        c = _cv(k, v, strict)
        if c is not None and c != ():
            items.append((k, c))
    return (etype, direction, other, tuple(sorted(items, key=lambda kv: kv[0])))


def jsonable(ans):
    """정규화한 답 → 저장용(정렬된 목록)."""
    if isinstance(ans, Counter):
        return sorted(([jsonable(x) for x in k] if isinstance(k, tuple) else k, c) for k, c in ans.items()) if ans else []
    if isinstance(ans, dict):
        return {k: jsonable(v) for k, v in sorted(ans.items(), key=lambda kv: str(kv[0]))}
    if isinstance(ans, (list, tuple)):
        return [jsonable(x) for x in ans]
    return ans


class _Backend:
    """공통: Q4 정규화(후보는 q4_raw만 구현한다 -- (간선 type, 방향, 상대 노드 id, 속성 dict) 목록)."""
    server = disk_persist = False

    def q4(self, p):
        return Counter(edge_t(*r) for r in self.q4_raw(p))

    def q4_strict(self, p):
        return Counter(edge_t(*r, strict=True) for r in self.q4_raw(p))


# ---------------------------------------------------------------- 기준: 현재 core/kg(파이썬 dict 인덱스 + 운영 보기 코드)
class CoreKg(_Backend):
    name = "core_kg"

    def load(self, kg):
        self.kg = kg

    def q1(self):
        from core.kg.views import skill_map
        return {r["skill"]: (r["practical"], r["expert"], r["practical_free"], r["demand_headcount"], r["proposal_headcount"])
                for r in skill_map(self.kg)}

    def q2(self, j):
        from core.kg.views import project_evidence
        jid = bare(j)
        entries = [_Entry(bare(e["src"]), jid, e.get("alloc")) for e in self.kg.inc(j, "ASSIGNED")]
        ev = project_evidence(self.kg, jid, entries)
        req = [req_t(r["skill"], r["min_months"], r["headcount"]) for r in ev["requirements"]]
        months = [(m["person_id"], c["skill"], nv(c["months"])) for m in ev["members"] for c in m["requirements"]]
        industry = None if ev["industry"] is None else [(m["person_id"], m["same_industry_projects"]) for m in ev["members"]]
        cowork = {(min(m["person_id"], c["with"]), max(m["person_id"], c["with"]), nv(c["months_total"]))
                  for m in ev["members"] for c in m["cowork_in_team"]}
        reviews = [(r["from"], m["person_id"]) for m in ev["members"] for r in m["reviews_from_team"]]
        return q2_pack(req, months, industry, list(cowork), reviews)

    def q3(self, j):
        kg = self.kg
        team = {e["src"] for e in kg.inc(j, "ASSIGNED")}
        assigned = {e["src"] for e in kg.edges if e["type"] == "ASSIGNED"}
        req = {e["dst"] for e in kg.out(j, "REQUIRES")}
        out = set()
        for m in team:
            for e in kg.out(m, "COWORKED") + kg.inc(m, "COWORKED"):
                x = e["dst"] if e["src"] == m else e["src"]
                if (e.get("months_recent") or 0) > 0 and x not in assigned and \
                        any(h["dst"] in req and (h.get("months") or 0) >= PRACTICAL for h in kg.out(x, "HAS_SKILL")):
                    out.add(x)
        return sorted(out)

    def q4_raw(self, p):
        kg = self.kg
        return [(e["type"], "out", e["dst"], e) for e in kg.out(p)] + [(e["type"], "in", e["src"], e) for e in kg.inc(p)]


class _Entry:
    def __init__(self, person_id, project_id, alloc):
        self.person_id, self.project_id, self.alloc, self.monthly_alloc = person_id, project_id, alloc, None


# ---------------------------------------------------------------- C. 메모리 그래프: networkx MultiDiGraph
class Networkx(_Backend):
    name = "networkx"

    def load(self, kg):
        import networkx as nx
        g = nx.MultiDiGraph()
        g.add_nodes_from(kg.nodes.items())
        g.add_edges_from((e["src"], e["dst"], {k: v for k, v in e.items() if k not in ("src", "dst")}) for e in kg.edges)
        self.g = g

    def _out(self, n, t):
        return [(v, d) for _, v, d in self.g.out_edges(n, data=True) if d["type"] == t]

    def _in(self, n, t):
        return [(u, d) for u, _, d in self.g.in_edges(n, data=True) if d["type"] == t]

    def q1(self):
        g = self.g
        busy = {u for u, _, t in g.edges(data="type") if t == "CURRENT_ON"}
        out = {}
        for s, t in g.nodes(data="type"):
            if t != "skill":
                continue
            prac = [(u, d["months"]) for u, d in self._in(s, "HAS_SKILL") if (d.get("months") or 0) >= PRACTICAL]
            dem = [(u, d.get("headcount") or 1) for u, d in self._in(s, "REQUIRES")]
            out[g.nodes[s]["label"]] = (len(prac), sum(1 for _, m in prac if m >= EXPERT), sum(1 for u, _ in prac if u not in busy),
                                        sum(h for _, h in dem), sum(h for u, h in dem if g.nodes[u].get("proposal")))
        return out

    def q2(self, j):
        g = self.g
        pj = g.nodes[j]
        reqs = self._out(j, "REQUIRES")
        team = [u for u, _ in self._in(j, "ASSIGNED")]
        tset = set(team)
        req = [req_t(g.nodes[s]["label"], d.get("min_months"), d.get("headcount")) for s, d in reqs]
        months, industry, cowork, reviews = [], ([] if pj.get("industry") else None), [], []
        for m in team:
            have = {s: d.get("months") or 0 for s, d in self._out(m, "HAS_SKILL")}
            months += [(bare(m), g.nodes[s]["label"], nv(have.get(s, 0))) for s, _ in reqs]
            if industry is not None:
                industry.append((bare(m), len({v for v, _ in self._out(m, "WORKED_ON") if g.nodes[v].get("industry") == pj["industry"]})))
            cowork += [(bare(m), bare(v), nv(d.get("months_total"))) for v, d in self._out(m, "COWORKED") if v in tset]
            reviews += [(bare(m), bare(v)) for v, _ in self._out(m, "REVIEWED") if v in tset]
        return q2_pack(req, months, industry, cowork, reviews)

    def q3(self, j):
        # 다듬기(tuning): 후보(최근 함께 일한 사람)를 먼저 모아 중복을 없애고, 후보마다 나가는 간선을 한 번만 훑는다
        g = self.g
        team = [u for u, _ in self._in(j, "ASSIGNED")]
        req = {s for s, _ in self._out(j, "REQUIRES")}
        cands = set()
        for m in team:
            cands.update(x for _, x, d in g.out_edges(m, data=True) if d["type"] == "COWORKED" and (d.get("months_recent") or 0) > 0)
            cands.update(x for x, _, d in g.in_edges(m, data=True) if d["type"] == "COWORKED" and (d.get("months_recent") or 0) > 0)
        out = []
        for x in cands:
            ok = busy = False
            for _, s, d in g.out_edges(x, data=True):
                if d["type"] == "ASSIGNED":
                    busy = True
                    break
                if d["type"] == "HAS_SKILL" and s in req and (d.get("months") or 0) >= PRACTICAL:
                    ok = True
            if ok and not busy:
                out.append(x)
        return sorted(out)

    def q4_raw(self, p):
        g = self.g
        return [(d["type"], "out", v, d) for _, v, d in g.out_edges(p, data=True)] + \
               [(d["type"], "in", u, d) for u, _, d in g.in_edges(p, data=True)]


# ---------------------------------------------------------------- A. RDF: 같은 트리플 · 같은 SPARQL -- Oxigraph(후보), rdflib(참고)
BASE = "https://teamweaver.example/kg/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
XSD = "http://www.w3.org/2001/XMLSchema#"


def iri(nid: str) -> str:
    return BASE + "n/" + quote(nid, safe="")


def triples(kg):
    """속성 그래프 → RDF 1.1 트리플. 관계마다 (1) 바로 잇는 트리플(src p:TYPE dst -- 탐색용)과 (2) 속성을 담는 관계 노드
    (n-ary 패턴: e a c:rel_TYPE; p:src; p:dst; p:<속성>)를 함께 둔다 -- 표준 RDF 1.1 범위에서 간선 속성을 담는 흔한 방식.
    목록 속성은 여러 값 속성(순서 없음)으로 담는다. 값: ("iri", str) 또는 ("lit", 어휘형, 데이터타입 IRI)."""
    def lits(v):
        v = clean(v)
        for x in (v if isinstance(v, list) else [v]):
            if x is None:
                continue
            if isinstance(x, bool):
                yield ("lit", "true" if x else "false", XSD + "boolean")
            elif isinstance(x, int):
                yield ("lit", str(x), XSD + "integer")
            elif isinstance(x, float):
                yield ("lit", repr(x), XSD + "double")
            else:
                yield ("lit", str(x), XSD + "string")
    for nid, n in kg.nodes.items():
        s = iri(nid)
        yield s, RDF_TYPE, ("iri", BASE + "c/" + n["type"])
        yield s, BASE + "p/id", ("lit", nid, XSD + "string")
        yield s, RDFS_LABEL, ("lit", str(n["label"]), XSD + "string")
        for k, v in n.items():
            if k not in ("id", "type", "label"):
                for o in lits(v):
                    yield s, BASE + "p/" + k, o
    for i, e in enumerate(kg.edges):
        a, b = iri(e["src"]), iri(e["dst"])
        yield a, BASE + "p/" + e["type"], ("iri", b)
        en = BASE + f"e/{i}"
        yield en, RDF_TYPE, ("iri", BASE + "c/rel_" + e["type"])
        yield en, BASE + "p/src", ("iri", a)
        yield en, BASE + "p/dst", ("iri", b)
        for k, v in e.items():
            if k not in ("type", "src", "dst"):
                for o in lits(v):
                    yield en, BASE + "p/" + k, o


PFX = f"""PREFIX p: <{BASE}p/>
PREFIX c: <{BASE}c/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
SQ1 = PFX + """SELECT ?name ?practical ?expert ?free ?need ?prop WHERE {
  ?s a c:skill ; rdfs:label ?name .
  OPTIONAL { SELECT ?s (COUNT(?h) AS ?practical) (SUM(IF(?m >= 36, 1, 0)) AS ?expert) (SUM(IF(?busy, 0, 1)) AS ?free) WHERE {
      ?h a c:rel_HAS_SKILL ; p:dst ?s ; p:src ?per ; p:months ?m . FILTER(?m >= 12)
      BIND(EXISTS { ?per p:CURRENT_ON ?any } AS ?busy) } GROUP BY ?s }
  OPTIONAL { SELECT ?s (SUM(?hc) AS ?need) (SUM(IF(?prop0, ?hc, 0)) AS ?prop) WHERE {
      ?r a c:rel_REQUIRES ; p:dst ?s ; p:src ?j . ?j p:proposal ?prop0 .
      OPTIONAL { ?r p:headcount ?hc0 } BIND(IF(BOUND(?hc0) && ?hc0 > 0, ?hc0, 1) AS ?hc) } GROUP BY ?s }
}"""
SQ2_REQ = PFX + """SELECT ?ind ?name ?min ?hc WHERE { OPTIONAL { <J> p:industry ?ind }
  OPTIONAL { ?r a c:rel_REQUIRES ; p:src <J> ; p:dst ?s . ?s rdfs:label ?name . OPTIONAL { ?r p:min_months ?min } OPTIONAL { ?r p:headcount ?hc } } }"""
# 팀원 × 요구 기술 경력: 안쪽 결합으로 있는 것만 받고 0은 파이썬에서 채운다(다듬기 -- OPTIONAL 작성법은 아래 SQ2_MONTHS_OPTIONAL).
# ?h에 a c:rel_HAS_SKILL을 붙이지 않았다: 기술 노드로 들어오는 관계 노드는 HAS_SKILL과 REQUIRES뿐이고 REQUIRES의 src는 사업이라 ?m과 결합되지 않는다.
SQ2_TEAM = PFX + """SELECT ?mid WHERE { ?m p:ASSIGNED <J> ; p:id ?mid }"""
SQ2_MONTHS = PFX + """SELECT ?mid ?name ?months WHERE { ?m p:ASSIGNED <J> ; p:id ?mid . <J> p:REQUIRES ?s . ?s rdfs:label ?name .
  ?m p:HAS_SKILL ?s . ?h p:src ?m ; p:dst ?s ; p:months ?months }"""
SQ2_IND = PFX + """SELECT ?mid (COUNT(DISTINCT ?pp) AS ?n) WHERE { <J> p:industry ?ind . ?m p:ASSIGNED <J> ; p:id ?mid .
  OPTIONAL { ?m p:WORKED_ON ?pp . ?pp p:industry ?ind } } GROUP BY ?mid"""
SQ2_CW = PFX + """SELECT ?a ?b ?mt WHERE { ?x p:ASSIGNED <J> ; p:id ?a . ?y p:ASSIGNED <J> ; p:id ?b .
  ?c a c:rel_COWORKED ; p:src ?x ; p:dst ?y . OPTIONAL { ?c p:months_total ?mt } }"""
SQ2_RV = PFX + """SELECT ?a ?b WHERE { ?x p:ASSIGNED <J> ; p:id ?a . ?y p:ASSIGNED <J> ; p:id ?b . ?x p:REVIEWED ?y }"""
# 3단계 탐색: 먼저 "팀원과 최근 함께 일한 사람"을 하위 질의로 좁힌다(다듬기 -- 다른 작성법은 아래 SQ3_V_*)
SQ3 = PFX + """SELECT DISTINCT ?xid WHERE {
  { SELECT DISTINCT ?x WHERE { ?m p:ASSIGNED <J> .
      { ?c p:src ?m ; p:dst ?x } UNION { ?c p:dst ?m ; p:src ?x }
      ?c a c:rel_COWORKED ; p:months_recent ?r . FILTER(?r > 0) } }
  FILTER NOT EXISTS { ?x p:ASSIGNED ?any }
  ?h p:src ?x ; a c:rel_HAS_SKILL ; p:dst ?s ; p:months ?mo . FILTER(?mo >= 12)
  <J> p:REQUIRES ?s .
  ?x p:id ?xid . }"""
SQ4 = PFX + """SELECT ?e ?dir ?oid ?k ?v WHERE {
  { ?e p:src <P> ; p:dst ?o . BIND("out" AS ?dir) } UNION { ?e p:dst <P> ; p:src ?o . BIND("in" AS ?dir) }
  ?o p:id ?oid . ?e ?k ?v . }"""
SPARQL_ADOPTED = [SQ1, SQ2_REQ, SQ2_TEAM, SQ2_MONTHS, SQ2_IND, SQ2_CW, SQ2_RV, SQ3, SQ4]


class _Sparql(_Backend):
    """같은 트리플·같은 SPARQL. 하위 클래스가 load와 _select(질의 → [{변수: 파이썬 값}])만 다르다."""

    def q1(self):
        return {r["name"]: (r.get("practical") or 0, r.get("expert") or 0, r.get("free") or 0, r.get("need") or 0, r.get("prop") or 0)
                for r in self._select(SQ1)}

    def q2(self, j):
        J = iri(j)
        rows = self._select(SQ2_REQ.replace("<J>", f"<{J}>"))
        ind = rows[0].get("ind") if rows else None
        req = [req_t(r["name"], r.get("min"), r.get("hc")) for r in rows if r.get("name") is not None]
        team = [bare(r["mid"]) for r in self._select(SQ2_TEAM.replace("<J>", f"<{J}>"))]
        have = {(bare(r["mid"]), r["name"]): r["months"] for r in self._select(SQ2_MONTHS.replace("<J>", f"<{J}>"))}
        names = {r["name"] for r in rows if r.get("name") is not None}
        months = [(m, nm, nv(have.get((m, nm)) or 0)) for m in team for nm in names]
        industry = None if ind is None else [(bare(r["mid"]), r["n"]) for r in self._select(SQ2_IND.replace("<J>", f"<{J}>"))]
        cowork = [(bare(r["a"]), bare(r["b"]), nv(r.get("mt"))) for r in self._select(SQ2_CW.replace("<J>", f"<{J}>"))]
        reviews = [(bare(r["a"]), bare(r["b"])) for r in self._select(SQ2_RV.replace("<J>", f"<{J}>"))]
        return q2_pack(req, months, industry, cowork, reviews)

    def q3(self, j):
        return sorted(r["xid"] for r in self._select(SQ3.replace("<J>", f"<{iri(j)}>")))

    def q4_raw(self, p):
        edges: dict[str, dict] = {}
        for r in self._select(SQ4.replace("<P>", f"<{iri(p)}>")):
            d = edges.setdefault(r["e"], {"type": None, "dir": r["dir"], "other": r["oid"], "props": {}})
            k = r["k"]
            if k == RDF_TYPE:
                d["type"] = r["v"].rsplit("/rel_", 1)[1]
            elif k.startswith(BASE + "p/") and k not in (BASE + "p/src", BASE + "p/dst"):
                key = k[len(BASE) + 2:]
                if key in d["props"]:                           # 여러 값 속성: 모두 모은다(리뷰 MUST -- 예전엔 마지막 값으로 덮어썼다)
                    old = d["props"][key]
                    d["props"][key] = (old if isinstance(old, list) else [old]) + [r["v"]]
                else:
                    d["props"][key] = r["v"]
        return [(d["type"], d["dir"], d["other"], d["props"]) for d in edges.values()]


class Oxigraph(_Sparql):
    name = "rdf_oxigraph"

    def load(self, kg):
        import pyoxigraph as ox
        cache: dict = {}

        def term(o):
            if o[0] == "iri":
                t = cache.get(o[1])
                if t is None:
                    t = cache[o[1]] = ox.NamedNode(o[1])
                return t
            return ox.Literal(o[1], datatype=cache.setdefault(o[2], ox.NamedNode(o[2])))
        st = ox.Store()                                  # 메모리 저장소(경로 없음 -- 디스크에 남지 않는다)
        st.bulk_extend(ox.Quad(term(("iri", s)), term(("iri", p)), term(o)) for s, p, o in triples(kg))
        self.st, self.ox = st, ox

    def _select(self, q):
        ox = self.ox
        res = self.st.query(q)
        names = [v.value for v in res.variables]
        out = []
        for sol in res:
            row = {}
            for n in names:
                t = sol[n]
                if t is None:
                    continue
                if isinstance(t, ox.Literal):
                    dt_ = t.datatype.value
                    row[n] = (int(t.value) if dt_ in (XSD + "integer", XSD + "int", XSD + "long") else
                              float(t.value) if dt_ in (XSD + "double", XSD + "decimal", XSD + "float") else
                              t.value == "true" if dt_ == XSD + "boolean" else t.value)
                else:
                    row[n] = t.value
            out.append(row)
        return out


class Rdflib(_Sparql):
    name = "rdf_rdflib"

    def load(self, kg):
        import rdflib
        g = rdflib.Graph()
        for s, p, o in triples(kg):
            g.add((rdflib.URIRef(s), rdflib.URIRef(p),
                   rdflib.URIRef(o[1]) if o[0] == "iri" else rdflib.Literal(o[1], datatype=rdflib.URIRef(o[2]))))
        self.g = g

    def _select(self, q):
        import rdflib
        out = []
        for row in self.g.query(q):
            out.append({k: (v.toPython() if isinstance(v, rdflib.Literal) else str(v)) for k, v in row.asdict().items()})
        return out


# ---------------------------------------------------------------- B. 그래프 DB: Neo4j(Bolt, Cypher)
LABEL = {"person": "Person", "skill": "Skill", "project": "Project", "past_project": "PastProject", "client": "Client",
         "industry": "Industry"}
CQ1 = """MATCH (s:Skill)
OPTIONAL MATCH (p:Person)-[h:HAS_SKILL]->(s) WHERE h.months >= 12
WITH s, count(p) AS practical, count(CASE WHEN h.months >= 36 THEN 1 END) AS expert,
     count(CASE WHEN p IS NOT NULL AND NOT EXISTS { (p)-[:CURRENT_ON]->() } THEN 1 END) AS free
OPTIONAL MATCH (j:Project)-[r:REQUIRES]->(s)
WITH s, practical, expert, free, CASE WHEN r IS NULL THEN 0 WHEN coalesce(r.headcount, 0) > 0 THEN r.headcount ELSE 1 END AS hc, j
RETURN s.label AS name, practical, expert, free, sum(hc) AS need, sum(CASE WHEN j.proposal THEN hc ELSE 0 END) AS prop"""
CQ2 = """MATCH (j:Project {id: $j})
CALL (j) { MATCH (j)-[r:REQUIRES]->(s:Skill) RETURN collect([s.label, r.min_months, r.headcount]) AS req }
CALL (j) { MATCH (j)<-[:ASSIGNED]-(m:Person) RETURN collect(m.id) AS team }
CALL (j) { MATCH (j)<-[:ASSIGNED]-(m:Person)-[h:HAS_SKILL]->(s:Skill)<-[:REQUIRES]-(j) RETURN collect([m.id, s.label, h.months]) AS months }
CALL (j) { MATCH (j)<-[:ASSIGNED]-(m:Person) OPTIONAL MATCH (m)-[:WORKED_ON]->(pp:PastProject) WHERE pp.industry = j.industry
           WITH m, count(DISTINCT pp) AS n RETURN collect([m.id, n]) AS ind }
CALL (j) { MATCH (j)<-[:ASSIGNED]-(a:Person)-[c:COWORKED]->(b:Person)-[:ASSIGNED]->(j) RETURN collect([a.id, b.id, c.months_total]) AS cw }
CALL (j) { MATCH (j)<-[:ASSIGNED]-(a:Person)-[:REVIEWED]->(b:Person)-[:ASSIGNED]->(j) RETURN collect([a.id, b.id]) AS rv }
RETURN j.industry AS ind_name, req, team, months, ind, cw, rv"""
CQ3 = """MATCH (j:Project {id: $j})<-[:ASSIGNED]-(m:Person)-[c:COWORKED]-(x:Person)
WHERE c.months_recent > 0 AND NOT EXISTS { (x)-[:ASSIGNED]->() }
  AND EXISTS { MATCH (x)-[h:HAS_SKILL]->(:Skill)<-[:REQUIRES]-(j) WHERE h.months >= 12 }
RETURN DISTINCT x.id AS xid"""
CQ4 = """MATCH (p:Person {id: $p})-[r]-(o)
RETURN type(r) AS t, CASE WHEN startNode(r) = p THEN 'out' ELSE 'in' END AS dir, o.id AS other, properties(r) AS props"""
CYPHER_ADOPTED = [CQ1, CQ2, CQ3, CQ4]


class Neo4j(_Backend):
    name, server, disk_persist = "neo4j", True, True     # 별도 서버(JVM) · 기본 설정에서 저장소 파일이 디스크(/data)에 남는다

    def __init__(self):
        from neo4j import GraphDatabase
        self.drv = GraphDatabase.driver(NEO_BOLT, auth=None)
        self.s = self.drv.session(database="neo4j")

    def load(self, kg):
        s = self.s
        s.run("MATCH (n) DETACH DELETE n").consume()
        for lab in LABEL.values():
            s.run(f"CREATE CONSTRAINT {lab.lower()}_id IF NOT EXISTS FOR (n:{lab}) REQUIRE n.id IS UNIQUE").consume()
        by_type = defaultdict(list)
        for n in kg.nodes.values():
            by_type[n["type"]].append({k: clean(v) for k, v in n.items() if k != "type"})
        for t, rows in by_type.items():
            for i in range(0, len(rows), 5000):
                s.run(f"UNWIND $rows AS r CREATE (n:{LABEL[t]}) SET n = r", rows=rows[i:i + 5000]).consume()
        by_rel = defaultdict(list)
        for e in kg.edges:
            key = (e["type"], kg.nodes[e["src"]]["type"], kg.nodes[e["dst"]]["type"])
            by_rel[key].append({"src": e["src"], "dst": e["dst"],
                                "props": {k: clean(v) for k, v in e.items() if k not in ("type", "src", "dst")}})
        for (t, a, b), rows in by_rel.items():
            for i in range(0, len(rows), 5000):
                s.run(f"UNWIND $rows AS r MATCH (a:{LABEL[a]} {{id: r.src}}) MATCH (b:{LABEL[b]} {{id: r.dst}}) "
                      f"CREATE (a)-[e:{t}]->(b) SET e = r.props", rows=rows[i:i + 5000]).consume()

    def _rows(self, q, **kw):
        return [r.data() for r in self.s.run(q, **kw)]

    def q1(self):
        return {r["name"]: (r["practical"], r["expert"], r["free"], r["need"], r["prop"]) for r in self._rows(CQ1)}

    def q2(self, j):
        r = self._rows(CQ2, j=j)[0]
        names = {x[0] for x in r["req"]}
        have = {(bare(x[0]), x[1]): x[2] for x in r["months"]}
        months = [(bare(m), nm, nv(have.get((bare(m), nm)) or 0)) for m in r["team"] for nm in names]
        industry = None if r["ind_name"] is None else [(bare(x[0]), x[1]) for x in r["ind"]]
        return q2_pack([req_t(*x) for x in r["req"]], months, industry, [(bare(a), bare(b), nv(c)) for a, b, c in r["cw"]],
                       [(bare(a), bare(b)) for a, b in r["rv"]])

    def q3(self, j):
        return sorted(r["xid"] for r in self._rows(CQ3, j=j))

    def q4_raw(self, p):
        return [(r["t"], r["dir"], r["other"], r["props"]) for r in self._rows(CQ4, p=p)]

    def close(self):
        self.s.close()
        self.drv.close()


# ---------------------------------------------------------------- 측정 전 질의 다듬기: 같은 답의 다른 작성법(리뷰 SHOULD -- 다시 잴 수 있게 코드로 남긴다)
SQ3_V_BGP = PFX + """SELECT DISTINCT ?xid WHERE {
  ?m p:ASSIGNED <J> .
  { ?c a c:rel_COWORKED ; p:src ?m ; p:dst ?x } UNION { ?c a c:rel_COWORKED ; p:src ?x ; p:dst ?m }
  ?c p:months_recent ?r . FILTER(?r > 0)
  FILTER NOT EXISTS { ?x p:ASSIGNED ?any }
  <J> p:REQUIRES ?s .
  ?h a c:rel_HAS_SKILL ; p:src ?x ; p:dst ?s ; p:months ?mo . FILTER(?mo >= 12)
  ?x p:id ?xid . }"""
SQ3_V_EXISTS = PFX + """SELECT DISTINCT ?xid WHERE {
  ?m p:ASSIGNED <J> .
  { ?c p:src ?m ; p:dst ?x } UNION { ?c p:dst ?m ; p:src ?x }
  ?c a c:rel_COWORKED ; p:months_recent ?r . FILTER(?r > 0)
  FILTER NOT EXISTS { ?x p:ASSIGNED ?any }
  FILTER EXISTS { <J> p:REQUIRES ?s . ?h p:src ?x ; p:dst ?s ; a c:rel_HAS_SKILL ; p:months ?mo . FILTER(?mo >= 12) }
  ?x p:id ?xid . }"""
SQ3_V_DIRECT = PFX + """SELECT DISTINCT ?xid WHERE {
  ?m p:ASSIGNED <J> .
  { ?c p:src ?m ; p:dst ?x } UNION { ?c p:dst ?m ; p:src ?x }
  ?c p:months_recent ?r . FILTER(?r > 0)
  FILTER NOT EXISTS { ?x p:ASSIGNED ?any }
  ?x p:HAS_SKILL ?s . <J> p:REQUIRES ?s .
  ?h p:src ?x ; p:dst ?s ; p:months ?mo . FILTER(?mo >= 12)
  ?x p:id ?xid . }"""
SQ2_MONTHS_OPTIONAL = PFX + """SELECT ?mid ?name ?months WHERE { ?m p:ASSIGNED <J> ; p:id ?mid . <J> p:REQUIRES ?s . ?s rdfs:label ?name .
  OPTIONAL { ?h a c:rel_HAS_SKILL ; p:src ?m ; p:dst ?s ; p:months ?months } }"""
CQ1_V_CALL = """MATCH (s:Skill)
CALL (s) { OPTIONAL MATCH (p:Person)-[h:HAS_SKILL]->(s) WHERE h.months >= 12
  RETURN count(p) AS practical, count(CASE WHEN h.months >= 36 THEN 1 END) AS expert,
         count(CASE WHEN p IS NOT NULL AND NOT EXISTS { (p)-[:CURRENT_ON]->() } THEN 1 END) AS free }
CALL (s) { OPTIONAL MATCH (j:Project)-[r:REQUIRES]->(s)
  WITH j, r, CASE WHEN coalesce(r.headcount, 0) > 0 THEN r.headcount ELSE 1 END AS hc
  RETURN sum(CASE WHEN r IS NULL THEN 0 ELSE hc END) AS need, sum(CASE WHEN r IS NOT NULL AND j.proposal THEN hc ELSE 0 END) AS prop }
RETURN s.label AS name, practical, expert, free, need, prop"""
CQ2_V_REQ = """MATCH (j:Project {id: $j}) OPTIONAL MATCH (j)-[r:REQUIRES]->(s:Skill)
RETURN j.industry AS ind, s.label AS name, r.min_months AS min, r.headcount AS hc"""
CQ2_V_MONTHS = """MATCH (j:Project {id: $j})<-[:ASSIGNED]-(m:Person), (j)-[:REQUIRES]->(s:Skill)
OPTIONAL MATCH (m)-[h:HAS_SKILL]->(s) RETURN m.id AS mid, s.label AS name, h.months AS months"""
CQ2_V_IND = """MATCH (j:Project {id: $j})<-[:ASSIGNED]-(m:Person)
OPTIONAL MATCH (m)-[:WORKED_ON]->(pp:PastProject) WHERE pp.industry = j.industry
RETURN m.id AS mid, count(DISTINCT pp) AS n"""
CQ2_V_CW = """MATCH (j:Project {id: $j})<-[:ASSIGNED]-(a:Person)-[c:COWORKED]->(b:Person)-[:ASSIGNED]->(j)
RETURN a.id AS a, b.id AS b, c.months_total AS mt"""
CQ2_V_RV = """MATCH (j:Project {id: $j})<-[:ASSIGNED]-(a:Person)-[:REVIEWED]->(b:Person)-[:ASSIGNED]->(j) RETURN a.id AS a, b.id AS b"""
CQ4_V_UNION = """MATCH (p:Person {id: $p})-[r]->(o) RETURN type(r) AS t, 'out' AS dir, o.id AS other, properties(r) AS props
UNION ALL
MATCH (p:Person {id: $p})<-[r]-(o) RETURN type(r) AS t, 'in' AS dir, o.id AS other, properties(r) AS props"""


def _sparql_q3(q):
    return lambda be, j: sorted(r["xid"] for r in be._select(q.replace("<J>", f"<{iri(j)}>")))


def _sparql_q2_optional(be, j):
    J = iri(j)
    rows = be._select(SQ2_REQ.replace("<J>", f"<{J}>"))
    ind = rows[0].get("ind") if rows else None
    req = [req_t(r["name"], r.get("min"), r.get("hc")) for r in rows if r.get("name") is not None]
    months = [(bare(r["mid"]), r["name"], nv(r.get("months") or 0)) for r in be._select(SQ2_MONTHS_OPTIONAL.replace("<J>", f"<{J}>"))]
    industry = None if ind is None else [(bare(r["mid"]), r["n"]) for r in be._select(SQ2_IND.replace("<J>", f"<{J}>"))]
    cowork = [(bare(r["a"]), bare(r["b"]), nv(r.get("mt"))) for r in be._select(SQ2_CW.replace("<J>", f"<{J}>"))]
    reviews = [(bare(r["a"]), bare(r["b"])) for r in be._select(SQ2_RV.replace("<J>", f"<{J}>"))]
    return q2_pack(req, months, industry, cowork, reviews)


def _neo_q1_call(be, _):
    return {r["name"]: (r["practical"], r["expert"], r["free"], r["need"], r["prop"]) for r in be._rows(CQ1_V_CALL)}


def _neo_q2_multi(be, j):
    rows = be._rows(CQ2_V_REQ, j=j)
    ind = rows[0]["ind"] if rows else None
    req = [req_t(r["name"], r["min"], r["hc"]) for r in rows if r["name"] is not None]
    months = [(bare(r["mid"]), r["name"], nv(r["months"] or 0)) for r in be._rows(CQ2_V_MONTHS, j=j)]
    industry = None if ind is None else [(bare(r["mid"]), r["n"]) for r in be._rows(CQ2_V_IND, j=j)]
    cowork = [(bare(r["a"]), bare(r["b"]), nv(r["mt"])) for r in be._rows(CQ2_V_CW, j=j)]
    reviews = [(bare(r["a"]), bare(r["b"])) for r in be._rows(CQ2_V_RV, j=j)]
    return q2_pack(req, months, industry, cowork, reviews)


def _neo_q4_union(be, p):
    return Counter(edge_t(r["t"], r["dir"], r["other"], r["props"]) for r in be._rows(CQ4_V_UNION, p=p))


def _nx_q3_rescan(be, j):
    team = [u for u, _ in be._in(j, "ASSIGNED")]
    req = {s for s, _ in be._out(j, "REQUIRES")}
    out = set()
    for m in team:
        for x, d in be._out(m, "COWORKED") + be._in(m, "COWORKED"):
            if (d.get("months_recent") or 0) > 0 and not be._out(x, "ASSIGNED") and \
                    any(s in req and (h.get("months") or 0) >= PRACTICAL for s, h in be._out(x, "HAS_SKILL")):
                out.add(x)
    return sorted(out)


def _nx_q3_dedupe(be, j):
    team = [u for u, _ in be._in(j, "ASSIGNED")]
    req = {s for s, _ in be._out(j, "REQUIRES")}
    cands = {x for m in team for x, d in be._out(m, "COWORKED") + be._in(m, "COWORKED") if (d.get("months_recent") or 0) > 0}
    return sorted(x for x in cands if not be._out(x, "ASSIGNED") and
                  any(s in req and (h.get("months") or 0) >= PRACTICAL for s, h in be._out(x, "HAS_SKILL")))


def tuning_variants():
    """(후보, 질문, 작성법, 함수(be, x), 채택 여부). 채택 작성법은 후보 클래스의 메서드 그대로다.
    다듬은 곳: 첫 작성에서 눈에 띄게 느렸거나(Oxigraph Q2·Q3, networkx Q3) 왕복이 많던(Neo4j Q2) 질문, 그리고 Neo4j Q1·Q4의 다른 흔한 작성법.
    나머지(Oxigraph Q1·Q4, Neo4j Q3, networkx Q1·Q2·Q4)는 처음 작성 그대로다."""
    return [
        ("rdf_oxigraph", "q3_three_hop", "한 BGP(처음 작성)", _sparql_q3(SQ3_V_BGP), False),
        ("rdf_oxigraph", "q3_three_hop", "FILTER EXISTS", _sparql_q3(SQ3_V_EXISTS), False),
        ("rdf_oxigraph", "q3_three_hop", "직접 트리플 HAS_SKILL 먼저", _sparql_q3(SQ3_V_DIRECT), False),
        ("rdf_oxigraph", "q3_three_hop", "하위 질의로 함께 일한 사람 먼저(채택)", lambda be, j: be.q3(j), True),
        ("rdf_oxigraph", "q2_project_evidence", "팀원 경력 OPTIONAL(처음 작성)", _sparql_q2_optional, False),
        ("rdf_oxigraph", "q2_project_evidence", "안쪽 결합 + 0은 파이썬(채택)", lambda be, j: be.q2(j), True),
        ("neo4j", "q1_skill_map", "CALL 하위 질의(처음 작성)", _neo_q1_call, False),
        ("neo4j", "q1_skill_map", "WITH 연결(채택)", lambda be, _: be.q1(), True),
        ("neo4j", "q2_project_evidence", "질의 5개 = 5번 왕복(처음 작성)", _neo_q2_multi, False),
        ("neo4j", "q2_project_evidence", "한 질의 = 1번 왕복(채택)", lambda be, j: be.q2(j), True),
        ("neo4j", "q4_ego", "UNION ALL(처음 작성)", _neo_q4_union, False),
        ("neo4j", "q4_ego", "방향 없는 매치(채택)", lambda be, p: be.q4(p), True),
        ("networkx", "q3_three_hop", "사람마다 간선 다시 훑기(처음 작성)", _nx_q3_rescan, False),
        ("networkx", "q3_three_hop", "후보 중복 제거", _nx_q3_dedupe, False),
        ("networkx", "q3_three_hop", "후보 중복 제거 + 한 번 훑기(채택)", lambda be, j: be.q3(j), True),
    ]


def run_tuning(backends, base, projects, people) -> list:
    """100명에서 작성법마다 같은 입력(사업·사람 앞 6개, Q1은 6회)으로 평균 지연과 기준과 같은 답인지. 작성법당 시간 한도 TUNING_BUDGET_S(최소 1회).
    작성법마다 기록하지 않는 준비 호출 1회를 먼저 한다 -- 채택 작성법만 예열 회차에서 실행 계획이 캐시돼 있으면 다른 작성법이 불리하다(첫 재측정에서 실측:
    Neo4j 처음 작성 질의가 컴파일 포함 29 ms로 잡힘)."""
    out = defaultdict(list)
    for bname, q, label, fn, adopted in tuning_variants():
        be = backends[bname]
        xs = [None] * 6 if q == "q1_skill_map" else (people if q == "q4_ego" else projects)[:6]
        ts, same = [], True
        fn(be, xs[0])                                   # 준비 호출(기록 안 함)
        t_start = time.perf_counter()
        for x in xs:
            t0 = time.perf_counter()
            got = fn(be, x)
            ts.append((time.perf_counter() - t0) * 1000)
            same = same and got == call_q(base, q, x)
            if time.perf_counter() - t_start > TUNING_BUDGET_S:
                break
        out[(bname, q)].append({"label": label, "mean_ms": round(statistics.mean(ts), 2), "calls": len(ts),
                                "same_answer": same, "adopted": adopted})
        print(f"  tuning {bname:12s} {q:20s} {label:28s} {statistics.mean(ts):10.2f} ms  n={len(ts)} same={same}", flush=True)
    return [{"backend": b, "question": q, "variants": v} for (b, q), v in out.items()]


# ---------------------------------------------------------------- Neo4j 컨테이너
def sh(*cmd, check=True) -> str:
    return subprocess.run(cmd, check=check, capture_output=True, text=True).stdout.strip()


def neo_up():
    sh("docker", "rm", "-f", NEO_CONTAINER, check=False)
    env = sum((["-e", f"{k}={v}"] for k, v in NEO_ENV.items()), [])
    sh("docker", "run", "-d", "--name", NEO_CONTAINER, "-p", "17474:7474", "-p", "17687:7687", *env, NEO_IMAGE)
    from neo4j import GraphDatabase
    t0 = time.time()
    while time.time() - t0 < 180:
        try:
            with GraphDatabase.driver(NEO_BOLT, auth=None) as d:
                d.verify_connectivity()
                rec = d.execute_query("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition").records[0]
                return {"product": rec["name"], "version": rec["versions"][0], "edition": rec["edition"], "startup_s": round(time.time() - t0, 1)}
        except Exception:
            time.sleep(2)
    raise RuntimeError("Neo4j가 180초 안에 뜨지 않았다")


def neo_footprint() -> dict:
    mem = sh("docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", NEO_CONTAINER, check=False)
    disk = sh("docker", "exec", NEO_CONTAINER, "du", "-sk", "/data", check=False).split()
    return {"container_mem": mem, "data_dir_kb": int(disk[0]) if disk and disk[0].isdigit() else None}


# ---------------------------------------------------------------- 측정
def _code_lines(src: str) -> int:
    """빈 줄·주석·docstring을 뺀 줄 수(AST로 docstring 위치를 찾는다)."""
    src = textwrap.dedent(src)
    doc = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                doc.update(range(first.lineno, first.end_lineno + 1))
    return sum(1 for i, ln in enumerate(src.splitlines(), 1) if i not in doc and ln.strip() and not ln.strip().startswith("#"))


def _query_lines(queries, prefix="") -> int:
    body = sum(1 for q in queries for ln in q[len(prefix):].splitlines() if ln.strip())
    return body + sum(1 for ln in prefix.splitlines() if ln.strip())       # 머리말(PREFIX)은 한 번만 센다


def loc(cls) -> int:
    """적재+질의 코드 줄 수(채택 작성법만, 공통 기반 _Backend·다듬기 작성법 제외). RDF는 트리플 변환기와 SPARQL 공통 기반을 포함한다."""
    parts = [cls] + ([triples, _Sparql] if issubclass(cls, _Sparql) else [])
    n = sum(_code_lines(inspect.getsource(p)) for p in parts)
    if cls is Neo4j:
        n += _query_lines(CYPHER_ADOPTED)
    if issubclass(cls, _Sparql):
        n += _query_lines(SPARQL_ADOPTED, PFX)
    return n


def time_calls(fn, inputs, reps, warm, budget_s=None) -> dict:
    for i in range(warm):
        fn(inputs[i % len(inputs)])
    ts = []
    t_start = time.perf_counter()
    for i in range(reps):
        t0 = time.perf_counter()
        fn(inputs[i % len(inputs)])
        ts.append((time.perf_counter() - t0) * 1000)
        if budget_s and time.perf_counter() - t_start > budget_s:
            break
    ts.sort()
    p95 = ts[max(0, math.ceil(0.95 * len(ts)) - 1)]            # 가장 가까운 순위(n=20이면 19번째 값)
    return {"p50_ms": round(statistics.median(ts), 3), "p95_ms": round(p95, 3), "max_ms": round(ts[-1], 3), "n": len(ts)}


def call_q(be, q, x):
    return {"q1_skill_map": lambda _: be.q1(), "q2_project_evidence": be.q2, "q3_three_hop": be.q3, "q4_ego": be.q4}[q](x)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def run(args):
    import networkx
    import pyoxigraph
    import rdflib
    import neo4j as neo4j_drv
    from core.kg import views
    assert (views.PRACTICAL_MONTHS, views.EXPERT_MONTHS) == (PRACTICAL, EXPERT), "실무·숙련 경계가 core/kg와 다르다"
    neo_info = neo_up()
    try:
        backends = {"core_kg": CoreKg(), "networkx": Networkx(), "rdf_oxigraph": Oxigraph(), "neo4j": Neo4j()}
        ref = Rdflib()
        res = {"candidates": {n: {"correct": {}, "mismatch": {}, "latency": {}, "load_ms": {}, "server": b.server,
                                  "disk_persist": b.disk_persist, "loc": loc(type(b)), "q4_order_preserved": True}
                              for n, b in backends.items()},
               "reference": {"rdf_rdflib": {"correct": {}, "mismatch": {}, "latency": {}, "load_ms": {}, "server": False,
                                            "disk_persist": False, "loc": loc(Rdflib), "q4_order_preserved": True}},
               "data": {}, "baseline": "core_kg", "footprint": {"neo4j_empty": neo_footprint()}}
        # 버리는 예열 회차(리뷰 SHOULD -- 첫 규모의 Neo4j가 JVM 예열 부족으로 느리게 잡혔다): 100명을 적재하고 질문마다 BURN_IN회, 기록하지 않는다
        kg0, _, _, projects0, people0 = load_size(SIZES[0])
        ins0 = {"q1_skill_map": [None], "q2_project_evidence": projects0, "q3_three_hop": projects0, "q4_ego": people0}
        for be in backends.values():
            be.load(kg0)
            for q in QUESTIONS:
                for i in range(BURN_IN):
                    call_q(be, q, ins0[q][i % len(ins0[q])])
        print("burn-in done", flush=True)
        res["tuning"] = run_tuning(backends, backends["core_kg"], projects0, people0)
        del kg0
        for n in SIZES:
            kg, entries, extract_ms, projects, people = load_size(n)
            gen = list(triples(kg))
            res["data"][str(n)] = {"extract_ms": round(extract_ms, 1), **kg.stats(), "triples": len(gen), "triples_unique": len(set(gen)),
                                   "projects_with_team": len(projects), "people": len(people)}
            del gen
            print(f"[{n}] nodes {sum(kg.stats()['nodes'].values())} edges {len(kg.edges)} triples {res['data'][str(n)]['triples']}", flush=True)
            ins = {"q1_skill_map": [None], "q2_project_evidence": projects, "q3_three_hop": projects, "q4_ego": people}
            checks = {"q1_skill_map": [None], "q2_project_evidence": projects, "q3_three_hop": projects,
                      "q4_ego": people[:CHECK_PERSONS]}
            truth = None
            truth_strict = None
            for name, be in list(backends.items()) + [("rdf_rdflib", ref)]:
                slot = res["reference"]["rdf_rdflib"] if name == "rdf_rdflib" else res["candidates"][name]
                loads = []
                for _ in range(1 if name == "rdf_rdflib" else LOAD_REPS):
                    gc.collect()
                    t0 = time.perf_counter()
                    be.load(kg)
                    loads.append((time.perf_counter() - t0) * 1000)
                slot["load_ms"][str(n)] = round(statistics.median(loads), 1)
                if name == "neo4j":
                    res["footprint"][f"neo4j_{n}"] = neo_footprint()
                if name == "core_kg":
                    truth = {q: {str(x): call_q(be, q, x) for x in checks[q]} for q in QUESTIONS}
                    truth_strict = {str(p): be.q4_strict(p) for p in checks["q4_ego"]}
                lat = {}
                for q in QUESTIONS:
                    xs = checks[q] if name != "rdf_rdflib" else checks[q][:RDFLIB_CHECKS]
                    bad = [str(x) for x in xs if call_q(be, q, x) != truth[q][str(x)]]
                    slot["correct"][q] = slot["correct"].get(q, True) and not bad
                    if bad:
                        x0 = bad[0]
                        got = call_q(be, q, None if x0 == "None" else x0)
                        slot["mismatch"].setdefault(q, []).append({"size": n, "input": x0, "n_bad": len(bad),
                                                                   "got": jsonable(got), "want": jsonable(truth[q][x0])})
                    if q == "q4_ego":                      # 엄격 비교(목록 순서·중복까지) -- 판정에는 쓰지 않고 기록
                        slot["q4_order_preserved"] = slot["q4_order_preserved"] and all(
                            be.q4_strict(p) == truth_strict[str(p)] for p in xs)
                    lat[q] = time_calls(lambda x: call_q(be, q, x), ins[q], reps=5 if name == "rdf_rdflib" else REPS,
                                        warm=1 if name == "rdf_rdflib" else WARM, budget_s=60 if name == "rdf_rdflib" else None)
                slot["latency"][str(n)] = lat
                print(f"  {name:13s} load {slot['load_ms'][str(n)]:9.1f} ms  " +
                      "  ".join(f"{q.split('_')[0]} {lat[q]['p50_ms']:8.2f}" for q in QUESTIONS) +
                      f"  correct {all(slot['correct'].values())} q4_order {slot['q4_order_preserved']}", flush=True)
            # 메모리(참고): 파이썬 쪽 후보는 별도 프로세스에서 적재 전후 RSS 차이와 파이썬 할당(tracemalloc)
            for name in ("core_kg", "networkx", "rdf_oxigraph", "rdf_rdflib"):
                out = subprocess.run([sys.executable, "-m", "rehearsal.kg_backends", "--probe", name, str(n)], cwd=ROOT,
                                     capture_output=True, text=True)
                try:
                    res["footprint"][f"{name}_{n}"] = json.loads(out.stdout.strip().splitlines()[-1])
                except Exception:
                    res["footprint"][f"{name}_{n}"] = {"error": (out.stderr or "")[-300:]}
        backends["neo4j"].close()
        res["rdf_export"] = rdf_export_check(kg)
        res["standards_requirement"] = STANDARDS
        res["env"] = {"when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                      "commit": sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"),
                      "dirty": bool(sh("git", "-C", str(ROOT), "status", "--porcelain", "--", "rehearsal/kg_backends.py",
                                       "rehearsal/kg_backend_decision.py", check=False)),
                      "script_sha256": {"kg_backends.py": _sha(Path(__file__)),
                                        "kg_backend_decision.py": _sha(ROOT / "rehearsal" / "kg_backend_decision.py")},
                      "python": platform.python_version(), "machine": platform.machine(), "os": platform.platform(),
                      "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string", check=False),
                      "versions": {"networkx": networkx.__version__, "pyoxigraph": pyoxigraph.__version__, "rdflib": rdflib.__version__,
                                   "neo4j_driver": neo4j_drv.__version__, "neo4j_server": neo_info},
                      "neo4j_config": NEO_ENV | {"image": NEO_IMAGE, "transport": "Bolt localhost → Docker Desktop(macOS 가상 머신 포트 포워딩)"},
                      "reps": REPS, "warm": WARM, "burn_in": BURN_IN, "load_reps": LOAD_REPS, "check_persons": CHECK_PERSONS,
                      "rdflib_checks": RDFLIB_CHECKS}
    finally:
        if not args.keep_neo4j:
            sh("docker", "rm", "-f", NEO_CONTAINER, check=False)
    res["decision"] = decide(res)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), "utf-8")
    return res


def probe(name: str, n: int):
    """적재 메모리(참고): RSS 차이는 앞서 풀린 파이썬 힙을 다시 쓰면 작게 나오고(리뷰 MUST), tracemalloc은 파이썬 할당만 본다(Rust로 할당하는
    Oxigraph는 거의 안 잡힌다) -- 둘 다 기록한다."""
    import psutil
    import tracemalloc
    kg, *_ = load_size(n)
    be = {"core_kg": CoreKg, "networkx": Networkx, "rdf_oxigraph": Oxigraph, "rdf_rdflib": Rdflib}[name]()
    gc.collect()
    proc = psutil.Process()
    before = proc.memory_info().rss
    tracemalloc.start()
    be.load(kg)
    gc.collect()
    cur, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    after = proc.memory_info().rss
    print(json.dumps({"rss_delta_mb": round((after - before) / 2**20, 1), "py_alloc_mb": round(cur / 2**20, 1)}))


# 규칙 5 근거: 표준 연동·추론이 지금 요구사항인가 -- 저장소 문서 기준(계획 작성 시 확인한 위치)
STANDARDS = {
    "required_now": False,
    "basis": ("입력 계약(core/domain/models.py·core/ingest 계약 CSV)과 docs/project-context.md 로드맵에 외부 표준 형식(RDF/SKOS/OWL) 교환이나 "
              "규칙 추론 요구가 없다. 기술 이름 사전(다음 단계)은 표준 구조(SKOS: 대표 이름·다른 이름·상위 개념)를 따르되 JSON으로 보관하고, "
              "필요하면 RDF(Turtle)로 내보내는 방식으로 충족 가능한지 rdf_export에서 시험한다."),
}


def rdf_export_check(kg) -> dict:
    """규칙 5 시험(내보내기 방향만): (1) 지식 그래프 전체를 RDF(Turtle)로 내보내고 다시 읽어 **같은 트리플**인가(값 기준 비교),
    (2) 기술 노드를 SKOS 개념 체계로 내보내고 다시 읽어 모든 기술이 개념으로 남는가. 외부 데이터 합치기·추론은 시험하지 않는다."""
    import rdflib
    from rdflib.namespace import RDF, SKOS
    t0 = time.perf_counter()
    g = rdflib.Graph()
    for s, p, o in triples(kg):
        g.add((rdflib.URIRef(s), rdflib.URIRef(p),
               rdflib.URIRef(o[1]) if o[0] == "iri" else rdflib.Literal(o[1], datatype=rdflib.URIRef(o[2]))))
    ttl = g.serialize(format="turtle")
    back = rdflib.Graph().parse(data=ttl, format="turtle")

    def norm(t):
        return (t.toPython(), str(t.datatype), t.language) if isinstance(t, rdflib.Literal) else str(t)
    same_terms = set(back) == set(g)
    same_values = {tuple(map(norm, tr)) for tr in back} == {tuple(map(norm, tr)) for tr in g}
    sk = rdflib.Graph()
    scheme = rdflib.URIRef(BASE + "skills")
    sk.add((scheme, RDF.type, SKOS.ConceptScheme))
    skills = [n for n in kg.nodes.values() if n["type"] == "skill"]
    for n in skills:
        c = rdflib.URIRef(iri(n["id"]))
        sk.add((c, RDF.type, SKOS.Concept))
        sk.add((c, SKOS.prefLabel, rdflib.Literal(n["label"], lang="ko")))
        sk.add((c, SKOS.inScheme, scheme))
    sttl = sk.serialize(format="turtle")
    sback = rdflib.Graph().parse(data=sttl, format="turtle")
    concepts = set(sback.subjects(RDF.type, SKOS.Concept))
    return {"ok": same_values and len(concepts) == len(skills), "full_triples": len(g), "full_roundtrip_terms": same_terms,
            "full_roundtrip_values": same_values, "full_turtle_kb": round(len(ttl.encode()) / 1024, 1),
            "skos_concepts": len(concepts), "skills": len(skills), "ms": round((time.perf_counter() - t0) * 1000, 1)}


# ---------------------------------------------------------------- 보고서(HTML)
NAMES = {"rdf_oxigraph": "RDF 트리플 저장소(Oxigraph, SPARQL)", "neo4j": "그래프 DB(Neo4j, Cypher)",
         "networkx": "메모리 그래프(networkx)", "core_kg": "현재 구현(core/kg, 파이썬 dict)", "rdf_rdflib": "RDF(rdflib, 순수 파이썬) — 참고"}
QNAMES = {"q1_skill_map": "Q1 조직 기술 지도(기술 37개, 전체 집계)", "q2_project_evidence": "Q2 사업별 근거(사업 하나)",
          "q3_three_hop": "Q3 3단계 탐색(팀원→최근 함께 일한 사람→요구 기술, 안 A 미배치자)", "q4_ego": "Q4 한 사람 주변 사실 전부(간선 모든 속성)"}


def sensitivity(res: dict) -> dict:
    """참고(판정에 쓰지 않음): 규칙 3 없이 세 후보 셀 승수, 현재 구현 vs networkx 셀 승수, Q4를 엄격 비교(목록 순서·중복)로 했을 때의 판정."""
    c = res["candidates"]

    def wins(names):
        w = {n: 0 for n in names}
        for n in SIZES:
            for q in QUESTIONS:
                w[min(names, key=lambda b: c[b]["latency"][str(n)][q]["p50_ms"])] += 1
        return w
    three = wins(["networkx", "rdf_oxigraph", "neo4j"])
    lead = max(three, key=three.get)
    out = {"all_three": three, "all_three_leader": lead, "all_three_majority": three[lead] >= 7,
           "core_vs_nx": wins(["core_kg", "networkx"])}
    if all("q4_order_preserved" in v for v in c.values()):
        strict = copy.deepcopy(res)
        for v in strict["candidates"].values():
            v["correct"]["q4_ego"] = v["correct"].get("q4_ego", False) and v["q4_order_preserved"]
        out["strict_q4_decision"] = decide(strict)["decision"]
        out["strict_q4_failed"] = [k for k, v in c.items() if not v["q4_order_preserved"]]
    return out


def render(res: dict):
    e = html.escape
    cands = res["candidates"]
    allb = {**cands, **res.get("reference", {})}
    order = ["networkx", "rdf_oxigraph", "neo4j", "core_kg", "rdf_rdflib"]
    dec = res["decision"]
    env = res["env"]

    def cell(name, n, q):
        v = allb[name]["latency"].get(str(n), {}).get(q)
        if not v:
            return "<td>—</td>"
        best = min(cands[c]["latency"][str(n)][q]["p50_ms"] for c in ("networkx", "rdf_oxigraph", "neo4j"))
        strong = name in ("networkx", "rdf_oxigraph", "neo4j") and v["p50_ms"] == best
        txt = f"{v['p50_ms']:.2f}" if v["p50_ms"] < 100 else f"{v['p50_ms']:,.0f}"
        return f"<td class='num{' win' if strong else ''}'>{txt}<span class='sub'> / {v['p95_ms']:.1f}</span></td>"

    lat_rows = ""
    for n in SIZES:
        for q in QUESTIONS:
            lat_rows += f"<tr><td>{n}명</td><td>{e(QNAMES[q])}</td>" + "".join(cell(b, n, q) for b in order) + "</tr>"
    load_rows = "".join(
        f"<tr><td>{n}명</td><td class='num'>{res['data'][str(n)]['extract_ms']:.0f}</td>" +
        "".join(f"<td class='num'>{allb[b]['load_ms'].get(str(n), 0):,.0f}</td>" for b in order) + "</tr>" for n in SIZES)
    fp = res.get("footprint", {})
    empty_kb = (fp.get("neo4j_empty") or {}).get("data_dir_kb")

    def mem(b, n):
        if b == "neo4j":
            f = fp.get(f"neo4j_{n}", {})
            kb = f.get("data_dir_kb")
            disk = "?" if kb is None else f"{kb / 1024:,.0f} MB" + ("" if empty_kb is None else f"(빈 DB {empty_kb / 1024:,.0f} MB)")
            return f"{e(f.get('container_mem', '?'))}<br><span class='sub'>/data {disk}</span>"
        f = fp.get(f"{b}_{n}", {})
        if "rss_delta_mb" not in f:
            return "—"
        return f"{f.get('py_alloc_mb', 0):,.1f} MB<br><span class='sub'>RSS 차이 {f['rss_delta_mb']:,.1f} MB</span>"
    mem_rows = "".join(f"<tr><td>{n}명</td>" + "".join(f"<td class='num'>{mem(b, n)}</td>" for b in order) + "</tr>" for n in SIZES)
    data_rows = "".join(
        f"<tr><td>{n}명</td><td class='num'>{sum(d['nodes'].values()):,}</td><td class='num'>{sum(d['edges'].values()):,}</td>"
        f"<td class='num'>{d.get('triples_unique', d['triples']):,}</td><td class='num'>{d['projects_with_team']}</td></tr>"
        for n, d in ((n, res["data"][str(n)]) for n in SIZES))
    corr_rows = "".join(
        f"<tr><td>{e(NAMES[b])}</td>" + "".join(
            f"<td>{'✔' if allb[b]['correct'].get(q) else '✘'}</td>" for q in QUESTIONS) +
        f"<td>{'✔' if allb[b].get('q4_order_preserved') else '✘'}</td>"
        f"<td class='num'>{'(운영 보기 코드 호출)' if b == 'core_kg' else allb[b]['loc']}</td><td>{'필요' if allb[b]['server'] else '없음'}</td>"
        f"<td>{'남음' if allb[b]['disk_persist'] else '안 남음'}</td></tr>" for b in order)
    trail = ""
    for t in dec["trail"]:
        rule = t.get("rule")
        who = NAMES.get(t.get("candidate", ""), t.get("candidate", ""))
        if rule == 4:
            wins = ", ".join(f"{NAMES[k]} {v}셀" for k, v in t["cell_wins"].items())
            means = ", ".join(f"{NAMES[k]} {v:.2f} ms" for k, v in t["mean_ms"].items())
            body = f"셀 승수: {e(wins)} (과반 {'예' if t['majority'] else '아니오'}) · 셀 평균 중앙값: {e(means)} → <b>{e(NAMES.get(t['result'], t['result']))}</b>"
            if t.get("near_tie"):
                body += f" · 근소 차(10% 이내) → 코드 줄 수로 {e(NAMES[t['near_tie']['picked_by_loc']])}"
        else:
            body = f"{e(who)} {e(str(t['result']))}" + (f" — {e(t['why'])}" if t.get("why") else "")
        trail += f"<li><b>규칙 {rule}</b>: {body}</li>"
    exp = res.get("rdf_export", {})
    sens = res.get("sensitivity") or sensitivity(res)
    sens_html = (f"<li>규칙 3(운영 원칙)을 빼고 세 후보 모두로 규칙 4를 계산: "
                 f"{e(', '.join(f'{NAMES[k]} {v}셀' for k, v in sens['all_three'].items()))} → <b>{e(NAMES[sens['all_three_leader']])}</b>"
                 f"{' (과반)' if sens['all_three_majority'] else ''}</li>"
                 f"<li>현재 구현(core/kg)과 networkx만 비교: {e(', '.join(f'{NAMES[k]} {v}셀' for k, v in sens['core_vs_nx'].items()))}</li>")
    if "strict_q4_decision" in sens:
        failed = ", ".join(NAMES.get(k, k) for k in sens["strict_q4_failed"]) or "없음"
        sens_html += (f"<li>Q4를 엄격 비교(목록 속성의 순서·중복까지)로 하면: 통과 못 하는 후보 {e(failed)} → 판정 "
                      f"<b>{e(NAMES.get(sens['strict_q4_decision'], str(sens['strict_q4_decision'])))}</b></li>")
    tune_rows = ""
    for t in res.get("tuning", []):
        vs = " · ".join(f"{'<b>' if v['adopted'] else ''}{e(v['label'])} {v['mean_ms']:,.2f} ms{'</b>' if v['adopted'] else ''}"
                        f"<span class='sub'>(n={v['calls']}{'' if v['same_answer'] else ', 다른 답'})</span>" for v in t["variants"])
        tune_rows += f"<tr><td>{e(NAMES.get(t['backend'], t['backend']))}</td><td>{e(t['question'])}</td><td>{vs}</td></tr>"
    ver = env["versions"]
    mism = ""
    for b in order:
        for q, lst in allb[b].get("mismatch", {}).items():
            for m in lst[:1]:
                mism += f"<li>{e(NAMES[b])} · {e(q)} · {m['size']}명 · 입력 {e(m['input'])} · 다른 답 {m['n_bad']}건</li>"
    dirty = " (측정 당시 스크립트에 커밋 안 된 변경 있음)" if env.get("dirty") else ""
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>지식 그래프 구현 기술 비교</title>
<style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--card:#fff;--win:#e3f1e8;--box:#edf3f9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#a8a8a8;--acc:#8ab8e0;--line:#3a3a3a;--card:#23272b;--win:#24392c;--box:#222b33}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1100px;margin:auto}}h1{{font-size:1.5em}}h2{{color:var(--acc);border-bottom:2px solid var(--line);padding-bottom:.2em;margin-top:2em}}
table{{border-collapse:collapse;width:100%;font-size:.9em;margin:.6em 0}}th,td{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600}}.num{{text-align:right;font-variant-numeric:tabular-nums}}.win{{background:var(--win);font-weight:700}}
.sub{{color:var(--muted);font-size:.85em;font-weight:400}}.box{{background:var(--box);border-radius:10px;padding:10px 16px}}.w{{overflow-x:auto}}
code{{font-family:ui-monospace,Menlo,monospace;font-size:.88em}}
</style></head><body><main>
<h1>지식 그래프 구현 기술 비교 (E8)</h1>
<p class="sub">TeamWeaver · 측정 {e(env['when'])} · 측정 스크립트 커밋 {e(env['commit'])}{e(dirty)} · sha256 {e(str(env.get('script_sha256', {})))} ·
판정 규칙은 측정 전에 커밋(<code>rehearsal/kg_backend_decision.py</code>, 6af8da0, 이후 변경 없음)</p>
<div class="box"><p><b>판정: {e(NAMES.get(dec['decision'], str(dec['decision'])))}</b></p><ol>{trail}</ol>
<p class="sub">판정은 세 후보(RDF·그래프 DB·networkx) 중에서 낸다. 현재 구현(core/kg)은 같은 범주(메모리 그래프)의 자체 구현으로, 기준 답과 참고 수치로만 쓴다.</p></div>

<h2>1. 무엇을 비교했나</h2>
<p>같은 사실(시연 연초 계획 묶음 100/200/300명을 <code>core/kg.build_kg</code>로 뽑은 것 + 미리 계산 안 A의 배치)을 세 기술에 각자의 방식으로 넣고,
지금 화면·소명이 실제로 쓰는 질문 4개에 답하게 했다. 답은 정규화해 현재 구현(core/kg)과 비교했다. Q4는 간선의 모든 속성을 비교한다(목록 속성은 집합으로 —
순서·중복까지 보는 엄격 비교는 2절 별도 칸).</p>
<ul><li><b>RDF</b>: Oxigraph 메모리 저장소, SPARQL 1.1. 간선 속성(경력 개월 등)은 RDF 1.1 표준 범위의 관계 노드(n-ary) 패턴 + 탐색용 직접 트리플, 목록 속성은 여러 값 속성.
같은 트리플·같은 SPARQL을 rdflib(순수 파이썬)에도 돌려 참고로 둔다(정확성 질문마다 {env.get('rdflib_checks', 5)}건, 지연 5회·질문당 60초 한도).</li>
<li><b>그래프 DB</b>: Neo4j {e(str(ver['neo4j_server'].get('version')))} {e(str(ver['neo4j_server'].get('edition')))}(Docker, Bolt, 힙 1G·페이지 캐시 512M — 이 실험이 정한 설정),
라벨별 id 고유 제약(색인), UNWIND 배치 적재, Cypher.</li>
<li><b>메모리 그래프</b>: networkx {e(ver['networkx'])} MultiDiGraph, 표준 API.</li></ul>
<div class="w"><table><tr><th>규모</th><th>노드</th><th>간선</th><th>RDF 트리플(고유)</th><th>배치 있는 사업</th></tr>{data_rows}</table></div>

<h2>2. 정확성·운영 요소</h2>
<div class="w"><table><tr><th>구현</th>{''.join(f'<th>{e(q.split("_")[0].upper())}</th>' for q in QUESTIONS)}<th>Q4 엄격(순서·중복)</th><th>적재+질의 코드 줄</th><th>별도 서버</th><th>디스크 잔류</th></tr>{corr_rows}</table></div>
{'<ul>' + mism + '</ul>' if mism else '<p>세 후보는 모든 질문에서 기준과 같은 답을 냈다(Q2·Q3는 배치가 있는 사업 전부, Q4는 사람 ' + str(env['check_persons']) + '명). rdflib는 질문마다 ' + str(env.get('rdflib_checks', 5)) + '건만 확인했다.</p>'}
<p class="sub">코드 줄: 빈 줄·주석·docstring 제외, 채택한 질의 문자열 포함(SPARQL 머리말은 한 번만). RDF는 트리플 변환기와 SPARQL 공통 코드를 포함한다.
현재 구현은 운영 코드(core/kg/views.py)를 부르므로 비교하지 않는다.</p>

<h2>3. 질의 지연 (중앙값 / 95번째, ms — 낮을수록 좋음, 초록 = 세 후보 중 최속)</h2>
<p class="sub">측정 전에 100명으로 버리는 예열 회차(질문마다 {env.get('burn_in', 0)}회) → 규모마다 질문별 준비 호출 {env['warm']}회 뒤 {env['reps']}회(사업·사람 앞 {env['reps']}개를 돌아가며). 한 프로세스, 같은 기기.
95번째 = 가장 가까운 순위(20회 중 19번째).</p>
<div class="w"><table><tr><th>규모</th><th>질문</th>{''.join(f'<th>{e(NAMES[b])}</th>' for b in order)}</tr>{lat_rows}</table></div>

<h2>4. 적재 시간 (ms, {env['load_reps']}회 중앙값) · 메모리</h2>
<p class="sub">"사실 추출" = CSV 묶음에서 지식 그래프 만들기(build_kg, 모든 후보 공통 — 묶음 읽기·변환은 제외). 각 칸 = 그 사실을 후보 저장소에 넣는 시간. 현재 구현은 추출 결과가 곧 그래프라 0.</p>
<div class="w"><table><tr><th>규모</th><th>사실 추출</th>{''.join(f'<th>{e(NAMES[b])}</th>' for b in order)}</tr>{load_rows}</table></div>
<p class="sub">메모리(참고): 파이썬 후보는 별도 프로세스에서 적재 중 파이썬 할당(tracemalloc, 위)과 적재 전후 RSS 차이(아래). 두 방법 모두 한계가 있다 —
RSS 차이는 앞서 풀린 파이썬 힙을 다시 쓰면 작게 나오고, tracemalloc은 Rust로 할당하는 Oxigraph를 거의 못 본다(Oxigraph는 RSS 차이가 실제에 가깝다).
그래프 자체(사실 추출 결과)는 모든 후보 공통이라 빼고 잰다. Neo4j는 컨테이너 전체 사용량(이 실험이 정한 힙 1G·페이지 캐시 512M가 대부분)과 /data 크기
(빈 DB의 기본 파일·미리 할당된 트랜잭션 로그가 대부분이고, 9회 다시 적재한 기록 포함 — 우리 데이터 자체는 수 MB 수준).</p>
<div class="w"><table><tr><th>규모</th>{''.join(f'<th>{e(NAMES[b])}</th>' for b in order)}</tr>{mem_rows}</table></div>

<h2>5. 결론이 규칙에 얼마나 기대는가(민감도, 참고)</h2>
<ul>{sens_html}</ul>
<h3>측정 전 질의 다듬기(이번 실행에서 다시 잼)</h3>
<p class="sub">첫 작성에서 느렸거나 왕복이 많던 질문은 같은 답이 나오는 다른 작성법과 비교해 가장 빠른 것(굵게)을 채택했다. 100명, 사업·사람 앞 6개(Q1은 6회) 평균, 작성법당 {TUNING_BUDGET_S:.0f}초 한도.
다듬지 않은 질문: Oxigraph Q1·Q4, Neo4j Q3, networkx Q1·Q2·Q4(처음 작성 그대로). 다른 작성법 질의는 스크립트에 그대로 남겨 두었다.</p>
<div class="w"><table><tr><th>후보</th><th>질문</th><th>작성법별 평균 지연</th></tr>{tune_rows}</table></div>

<h2>6. 표준 교환(규칙 5)</h2>
<p>지금 요구사항인가: <b>{'예' if res['standards_requirement']['required_now'] else '아니오'}</b> — {e(res['standards_requirement']['basis'])}</p>
<p>내보내기 시험(내보내기 방향만): 300명 지식 그래프 전체 → Turtle {exp.get('full_triples', 0):,}트리플({exp.get('full_turtle_kb', 0):,} KB), 다시 읽은 트리플이 원본과
{'같음(값 기준)' if exp.get('full_roundtrip_values') else '다름'}{'' if exp.get('full_roundtrip_terms') else '(표기 형식은 일부 다름)'} ·
기술 {exp.get('skills')}개 → SKOS 개념 {exp.get('skos_concepts')}개 · {exp.get('ms', 0):,.0f} ms → <b>{'통과' if exp.get('ok') else '실패'}</b>.
표준 형식으로 <b>내보내야</b> 할 때는 메모리 저장으로도 충족된다. 외부 데이터를 합치거나 규칙으로 추론하는 일은 이 시험의 범위가 아니다(7절).</p>

<h2>7. 이 결론의 조건(보고서에 함께 적을 것)</h2>
<ul><li>지금 시스템 기준이다: 100~300명, 단일 서버 PoC, 원본은 CSV 묶음(그래프는 매번 다시 만드는 색인), 질문 4종.</li>
<li>Neo4j는 같은 기기의 Docker Desktop(macOS 가상 머신 포트 포워딩)으로 Bolt 연결해 쟀다 — 지연에 이 왕복이 포함되며, 리눅스 서버 배포보다 길 수 있다.
수만 명·여러 시스템이 같은 그래프를 동시에 쓰고 고치는 경우, 여러 조직 데이터를 표준으로 합치거나 규칙 추론이 핵심 기능이 되는 경우는 결론이 달라질 수 있다.</li>
<li>판정 규칙 코드의 알려진 차이(사전 등록이라 고치지 않음): 셀 승수가 동률이고 평균 차이가 10%를 넘으면 문서("코드 줄 수가 적은 쪽")와 달리 먼저 나열된 후보를 고른다.
근소 차 규칙은 과반 후보에도 적용되고, 계획의 "의존성" 기준은 코드에 없다. 이번 결과(과반)에는 영향이 없다.</li>
<li>모든 데이터는 가상(seed 고정)이다. 지연 수치는 기기마다 다르며, 판정은 규칙(측정 전 고정)에 따라 기계적으로 냈다.</li></ul>
<p class="sub">환경: {e(env['cpu'])} · {e(env['os'])} · Python {e(env['python'])} · pyoxigraph {e(ver['pyoxigraph'])} · rdflib {e(ver['rdflib'])} ·
neo4j 드라이버 {e(ver['neo4j_driver'])} · 원자료 <code>rehearsal/results/kg-backends.json</code>(첫 측정 <code>kg-backends.superseded-1.json</code>) · 재현 <code>uv run --with pyoxigraph --with rdflib --with neo4j --with networkx --with psutil python -m rehearsal.kg_backends</code></p>
</main></body></html>"""
    OUT_HTML.write_text(page, "utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--keep-neo4j", action="store_true")
    ap.add_argument("--probe", nargs=2, metavar=("BACKEND", "SIZE"))
    a = ap.parse_args()
    if a.probe:
        return probe(a.probe[0], int(a.probe[1]))
    res = json.loads(OUT_JSON.read_text("utf-8")) if a.render_only else run(a)
    if a.render_only:
        res["decision"] = decide(res)
    res["sensitivity"] = sensitivity(res)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), "utf-8")
    render(res)
    print(json.dumps(res["decision"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
