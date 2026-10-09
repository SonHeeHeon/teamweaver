"""E8b: RDF·그래프 DB의 장점이 이 프로젝트에서 얼마나 큰가(사용자 요청 2026-10-09: "두 포인트가 메모리 그래프에 비해 얼마나 큰 장점으로 활용될 수
있으며, 그럼에도 메모리 그래프가 어떤 면에서 상쇄하는지 구체적으로").

(A) 규모: 그래프 DB(Neo4j)는 100~300명에서 일부 질문(전체 집계·다단계 탐색)이 메모리 그래프보다 빨랐다(E8). 사람 수를 300 → 1,000 → 3,000명으로
    키워 그 차이가 화면 체감(200 ms) 수준으로 벌어지는지, 메모리 그래프의 메모리·다시 만드는 시간이 감당할 만한지 잰다.
    데이터: 조직형 가상 묶음 생성기(core/ingest/org_profile, 그룹 크기만 늘림), 배치는 단순 규칙(greedy) -- 질의 속도만 보므로 배치 품질은 상관없다.
(B) 추론: RDF의 강점인 표준 추론(OWL·RDFS)이 이 프로젝트에서 실제로 필요한 일 -- 기술 사전의 상위·하위 관계와 하위 기술 경력 부분 인정 --
    을 대신할 수 있는지. B1 상위 관계 전이(OWL-RL 추론기 owlrl, SPARQL 경로식 vs 파이썬), B2 가중 부분 인정(SPARQL 집계 vs 파이썬 person_skill_months).

  uv run --with pyoxigraph --with rdflib --with owlrl --with neo4j --with networkx python -m rehearsal.kg_advantages
결과: rehearsal/results/kg-advantages.{json,html}. Neo4j 컨테이너는 E8과 같은 설정(tw-e8-neo4j)으로 띄우고 끝나면 지운다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import html
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rehearsal import kg_backends as K      # noqa: E402  (같은 질문·같은 후보 구현을 그대로 쓴다)

RESULTS = ROOT / "rehearsal" / "results"
OUT_JSON, OUT_HTML = RESULTS / "kg-advantages.json", RESULTS / "kg-advantages.html"
SCALES = {300: {"DP": 100, "AI": 100, "AU": 100}, 1000: {"DP": 340, "AI": 330, "AU": 330}, 3000: {"DP": 1000, "AI": 1000, "AU": 1000}}
SEED, REPS, WARM, BURN_IN, CHECKS = 11, 20, 10, 20, 10
SCREEN_MS = 200.0                             # E8 규칙 2의 화면 응답 기준


# ---------------------------------------------------------------- (A) 규모
def build(n: int, tmp: Path) -> dict:
    import core.ingest.org_profile as op
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg, with_plan
    from core.optimize.greedy import solve_greedy
    from core.scoring.engine import ScoringEngine
    op.SIZES[n] = SCALES[n]                   # 생성기 모듈은 고치지 않고 이 실행에서만 그룹 크기를 늘린다
    t0 = time.perf_counter()
    root = op.generate_org_bundle(tmp / f"b{n}", n, seed=SEED)
    gen_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep)
    convert_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    kg = build_kg(b, ds, parsed)
    extract_ms = (time.perf_counter() - t0) * 1000
    del kg
    gc.collect()
    tracemalloc.start()                       # 메모리는 따로 한 번 더 만들어 잰다(시간 측정과 분리)
    kg = build_kg(b, ds, parsed)
    kg_mb = tracemalloc.get_traced_memory()[0] / 2**20
    tracemalloc.stop()
    g = MemoryGraph.build(ds, parsed)
    plan = solve_greedy(g, ScoringEngine(g).skill_matrix({}))
    kg = with_plan(kg, plan.entries)
    projects = sorted({e["dst"] for e in kg.edges if e["type"] == "ASSIGNED"})
    people = sorted(k for k, v in kg.nodes.items() if v["type"] == "person")
    return {"kg": kg, "projects": projects, "people": people,
            "info": {"people": n, **kg.stats(), "n_edges": len(kg.edges), "gen_s": round(gen_s, 1), "load_convert_s": round(convert_s, 1),
                     "extract_ms": round(extract_ms, 1), "kg_mb": round(kg_mb, 1), "assigned": len(plan.entries)}}


def scale_part(args) -> dict:
    backends = {"core_kg": K.CoreKg(), "networkx": K.Networkx(), "neo4j": None}
    neo_info = K.neo_up()
    out = {"neo4j_server": neo_info, "neo4j_empty": K.neo_footprint(), "sizes": {}}
    backends["neo4j"] = K.Neo4j()
    try:
        with tempfile.TemporaryDirectory(prefix="tw-e8b-") as td:
            first = True
            for n in SCALES:
                d = build(n, Path(td))
                kg, projects, people = d["kg"], d["projects"], d["people"]
                print(f"[{n}] edges {d['info']['n_edges']:,} kg {d['info']['kg_mb']} MB", flush=True)
                ins = {"q1_skill_map": [None], "q2_project_evidence": projects, "q3_three_hop": projects, "q4_ego": people}
                checks = {"q1_skill_map": [None], "q2_project_evidence": projects[:CHECKS], "q3_three_hop": projects[:CHECKS],
                          "q4_ego": people[:CHECKS]}
                row = {"info": d["info"], "backends": {}}
                truth = None
                for name, be in backends.items():
                    py_mb = 0.0
                    if name == "networkx":                            # 메모리는 따로 한 번 적재해 잰다(tracemalloc은 적재를 크게 느리게 한다)
                        gc.collect()
                        tracemalloc.start()
                        be.load(kg)
                        py_mb = tracemalloc.get_traced_memory()[0] / 2**20
                        tracemalloc.stop()
                        del be.g
                    gc.collect()
                    t0 = time.perf_counter()
                    be.load(kg)
                    load_ms = (time.perf_counter() - t0) * 1000
                    if first:                                         # 버리는 예열 회차(E8과 같은 이유)
                        for q in K.QUESTIONS:
                            for i in range(BURN_IN):
                                K.call_q(be, q, ins[q][i % len(ins[q])])
                    if name == "core_kg":
                        truth = {q: {str(x): K.call_q(be, q, x) for x in checks[q]} for q in K.QUESTIONS}
                    correct = {q: all(K.call_q(be, q, x) == truth[q][str(x)] for x in checks[q]) for q in K.QUESTIONS}
                    lat = {q: K.time_calls(lambda x: K.call_q(be, q, x), ins[q], reps=REPS, warm=WARM) for q in K.QUESTIONS}
                    fp = K.neo_footprint() if name == "neo4j" else {"py_alloc_mb": round(py_mb, 1)}
                    row["backends"][name] = {"load_ms": round(load_ms, 1), "correct": correct, "latency": lat, "footprint": fp}
                    print(f"  {name:9s} load {load_ms:10.1f} ms  " + "  ".join(f"{q.split('_')[0]} {lat[q]['p50_ms']:9.2f}" for q in K.QUESTIONS)
                          + f"  correct {all(correct.values())}  {fp}", flush=True)
                first = False
                for be in backends.values():
                    if hasattr(be, "g"):
                        del be.g
                out["sizes"][str(n)] = row
                del d, kg
                gc.collect()
    finally:
        backends["neo4j"].close()
        if not args.keep_neo4j:
            K.sh("docker", "rm", "-f", K.NEO_CONTAINER, check=False)
    return out


# ---------------------------------------------------------------- (B) 추론
SKOS = "http://www.w3.org/2004/02/skos/core#"


def reasoning_part() -> dict:
    import owlrl
    import pyoxigraph as ox
    import rdflib
    from rdflib.namespace import OWL, RDF, RDFS
    from rdflib.namespace import SKOS as RSKOS
    from core.ingest.skills import load_dictionary
    sd = load_dictionary()
    ttl = sd.to_turtle()
    out = {"dictionary_version": sd.version, "concepts": len(sd.concepts),
           "broader_edges": sum(len(c.get("broader") or []) for c in sd.concepts.values())}
    # B1 상위 관계 전이: 파이썬 vs OWL-RL 추론기 vs SPARQL 경로식
    t0 = time.perf_counter()
    py_pairs = {(c["label"], a) for c in sd.concepts.values() for a in sd.ancestors(c["label"])}
    py_ms = (time.perf_counter() - t0) * 1000
    g = rdflib.Graph().parse(data=ttl, format="turtle")
    # SKOS 스키마의 두 공리(skos:broader ⊑ skos:broaderTransitive, broaderTransitive는 전이적) -- 표준 SKOS 정의 그대로
    g.add((RSKOS.broader, RDFS.subPropertyOf, RSKOS.broaderTransitive))
    g.add((RSKOS.broaderTransitive, RDF.type, OWL.TransitiveProperty))
    n0 = len(g)
    t0 = time.perf_counter()
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    owl_ms = (time.perf_counter() - t0) * 1000
    label = {s: str(o) for s, o in g.subject_objects(RSKOS.prefLabel)}
    owl_pairs = {(label[s], label[o]) for s, o in g.subject_objects(RSKOS.broaderTransitive) if s != o and s in label and o in label}
    st = ox.Store()
    st.load(ttl.encode(), format=ox.RdfFormat.TURTLE)
    q = f"PREFIX skos: <{SKOS}> SELECT ?a ?b WHERE {{ ?x skos:broader+ ?y . ?x skos:prefLabel ?a . ?y skos:prefLabel ?b }}"
    t0 = time.perf_counter()
    sp_pairs = {(s["a"].value, s["b"].value) for s in st.query(q)}
    sp_ms = (time.perf_counter() - t0) * 1000
    out["b1_closure"] = {"pairs_python": len(py_pairs), "same_owlrl": owl_pairs == py_pairs, "same_sparql_path": sp_pairs == py_pairs,
                         "python_ms": round(py_ms, 2), "owlrl_ms": round(owl_ms, 1), "sparql_path_ms": round(sp_ms, 2),
                         "owlrl_triples_before": n0, "owlrl_triples_after": len(g),
                         "note": "표준 추론은 'A는 B의 하위'(참/거짓)까지만 낸다. 몇 단계 위인지(깊이)는 나오지 않는다."}
    # B2 가중 부분 인정: 300명 시연 묶음의 실제 보유 기술(10년 창·120개월 상한 적용) -- 파이썬 vs SPARQL(깊이를 펼친 UNION + 집계)
    from core.ingest.convert import LOOKBACK_MONTHS, lookback_start
    from core.ingest.loader import load_bundle
    from core.ingest.skills import person_skill_months
    from api.datasets import extract_bundle_zip
    with tempfile.TemporaryDirectory(prefix="tw-e8b-") as td:
        root = extract_bundle_zip((ROOT / "demo" / "org-n300.zip").read_bytes(), Path(td) / "bundle")
        b, _ = load_bundle(root)
    since = lookback_start(b.horizon[0])
    rows: dict[str, list] = {}
    for s in b.tables["person_skills.csv"]:
        if s["experience_months"] == 0 or (s.get("last_used_month") is not None and s["last_used_month"] < since):
            continue
        rows.setdefault(s["person_id"], []).append((s["skill_name"], min(s["experience_months"], LOOKBACK_MONTHS)))
    k = sd.narrower_credit
    t0 = time.perf_counter()
    py = {pid: person_skill_months(r, sd).months for pid, r in rows.items()}
    py2_ms = (time.perf_counter() - t0) * 1000
    max_depth = max((d for c in sd.concepts.values() for d in sd.ancestors(c["label"]).values()), default=0)
    st2 = ox.Store()
    st2.load(ttl.encode(), format=ox.RdfFormat.TURTLE)
    base = "https://teamweaver.example/skill/"
    tw = lambda x: ox.NamedNode(base + x)                     # noqa: E731
    xsd_int = ox.NamedNode("http://www.w3.org/2001/XMLSchema#integer")
    quads, i = [], 0
    for pid, r in rows.items():
        own = person_skill_months(r, sd, credit=0).own       # 이름 통일과 "가장 긴 경력"까지는 같은 입력(추론 비교 대상은 부분 인정)
        for name, m in own.items():
            cid = sd.by_label.get(name)
            if cid is None:
                continue
            h = tw(f"h{i}")
            i += 1
            quads += [ox.Quad(tw(f"p/{pid}"), tw("has"), h), ox.Quad(h, tw("skill"), tw(cid)),
                      ox.Quad(h, tw("months"), ox.Literal(str(m), datatype=xsd_int))]
    st2.bulk_extend(quads)
    unions = []
    for d in range(1, max_depth + 1):
        path = "/".join(["skos:broader"] * d)
        unions.append(f"{{ ?h tw:skill ?y ; tw:months ?m0 . ?y {path} ?s . BIND(FLOOR(?m0 * {k ** d}) AS ?mm) }}")
    sparql = (f"PREFIX skos: <{SKOS}> PREFIX tw: <{base}>\n"
              "SELECT ?p ?label (MAX(?mm) AS ?months) WHERE {\n  ?p tw:has ?h .\n"
              "  { ?h tw:skill ?s ; tw:months ?mm }\n  UNION " + "\n  UNION ".join(unions) +
              "\n  FILTER(?mm > 0) ?s skos:prefLabel ?label .\n} GROUP BY ?p ?label")
    t0 = time.perf_counter()
    sp: dict[str, dict] = {}
    for s in st2.query(sparql):
        sp.setdefault(s["p"].value.rsplit("/", 1)[1], {})[s["label"].value] = int(float(s["months"].value))
    sp2_ms = (time.perf_counter() - t0) * 1000
    known_only = {pid: {n: m for n, m in mm.items() if n in sd.by_label} for pid, mm in py.items()}
    diff = [pid for pid in known_only if known_only[pid] != sp.get(pid, {})]
    # 인정 건수 = 실제로 경력을 올린 것(인정분 > 본인 경력) -- 입력 단계 리포트·T3와 같은 정의(리뷰 SHOULD)
    credited = sum(1 for pid, r in rows.items() for sm in [person_skill_months(r, sd)]
                   for name, imp in sm.implied.items() if imp["months"] > sm.own.get(name, 0))
    out["b2_weighted_credit"] = {"people": len(rows), "max_depth": max_depth, "credit": k, "credited_pairs": credited,
                                 "same": not diff, "diff_people": diff[:5], "python_ms": round(py2_ms, 1), "sparql_ms": round(sp2_ms, 1),
                                 "sparql_lines": len(sparql.splitlines()), "sparql": sparql,
                                 "note": "표준 추론(OWL·RDFS)으로는 '절반만 인정' 같은 수치 가중을 표현할 수 없어 SPARQL 산술·집계로 직접 썼다. "
                                         "SPARQL 경로식은 경로 길이를 돌려주지 않아 깊이마다 UNION을 펼쳐야 한다(사전 깊이가 바뀌면 질의도 바뀜). "
                                         "처음 작성은 UNION 바깥에서 읽은 경력을 안쪽 BIND가 못 봐(SPARQL의 아래에서 위로 평가하는 범위 규칙) "
                                         "인정분이 오류 없이 빠졌다 -- 각 묶음 안에서 경력을 읽도록 고쳐 같아졌다."}
    print("B1", out["b1_closure"], "\nB2", {k_: v for k_, v in out["b2_weighted_credit"].items() if k_ != "sparql"}, flush=True)
    return out


# ---------------------------------------------------------------- 보고서
def render(res: dict) -> None:
    e = html.escape
    sizes = res["scale"]["sizes"]
    names = {"core_kg": "현재 구현(core/kg)", "networkx": "메모리 그래프(networkx)", "neo4j": "그래프 DB(Neo4j)"}
    qn = {"q1_skill_map": "Q1 조직 기술 지도", "q2_project_evidence": "Q2 사업별 근거", "q3_three_hop": "Q3 3단계 탐색", "q4_ego": "Q4 한 사람 주변"}
    rows = ""
    for n, r in sizes.items():
        for q in K.QUESTIONS:
            vals = {b: r["backends"][b]["latency"][q]["p50_ms"] for b in names}
            best = min(vals[b] for b in ("networkx", "neo4j"))
            rows += f"<tr><td>{int(n):,}명</td><td>{qn[q]}</td>" + "".join(
                f"<td class='num{' win' if b != 'core_kg' and vals[b] == best else ''}{' over' if vals[b] > SCREEN_MS else ''}'>{vals[b]:,.2f}</td>"
                for b in names) + "</tr>"
    res_rows = ""
    for n, r in sizes.items():
        i = r["info"]
        b = r["backends"]
        res_rows += (f"<tr><td>{int(n):,}명</td><td class='num'>{i['n_edges']:,}</td><td class='num'>{i['load_convert_s']} s + {i['extract_ms']/1000:.2f} s</td>"
                     f"<td class='num'>{i['kg_mb']:,.0f} MB</td><td class='num'>{b['networkx']['load_ms']/1000:,.2f} s · {b['networkx']['footprint']['py_alloc_mb']:,.0f} MB</td>"
                     f"<td class='num'>{b['neo4j']['load_ms']/1000:,.1f} s · {e(b['neo4j']['footprint'].get('container_mem', '?'))}</td>"
                     f"<td>{'✔' if all(all(x['correct'].values()) for x in b.values()) else '✘'}</td></tr>")
    b1, b2 = res["reasoning"]["b1_closure"], res["reasoning"]["b2_weighted_credit"]
    before_p = RESULTS / "kg-advantages.before-index.json"
    fix_rows = ""
    if before_p.exists():
        before = json.loads(before_p.read_text("utf-8"))["scale"]["sizes"]
        for n, r in sizes.items():
            if n in before:
                q1b = before[n]["backends"]["core_kg"]["latency"]["q1_skill_map"]["p50_ms"]
                q1a = r["backends"]["core_kg"]["latency"]["q1_skill_map"]["p50_ms"]
                q1n = r["backends"]["neo4j"]["latency"]["q1_skill_map"]["p50_ms"]
                fix_rows += (f"<tr><td>{int(n):,}명</td><td class='num{' over' if q1b > SCREEN_MS else ''}'>{q1b:,.2f}</td>"
                             f"<td class='num{' over' if q1a > SCREEN_MS else ''}'>{q1a:,.2f}</td><td class='num'>{q1n:,.2f}</td></tr>")
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RDF·그래프 DB 장점 실측</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--win:#e3f1e8;--over:#fbe3e3;--box:#edf3f9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#a8a8a8;--acc:#8ab8e0;--line:#3a3a3a;--win:#24392c;--over:#3d2626;--box:#222b33}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1000px;margin:auto}}h2{{color:var(--acc);border-bottom:2px solid var(--line);padding-bottom:.2em;margin-top:2em}}
table{{border-collapse:collapse;width:100%;font-size:.9em}}th,td{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left}}
th{{color:var(--muted)}}.num{{text-align:right;font-variant-numeric:tabular-nums}}.win{{background:var(--win);font-weight:700}}.over{{background:var(--over)}}
.sub{{color:var(--muted);font-size:.85em}}.w{{overflow-x:auto}}pre{{font-size:.8em;overflow-x:auto;background:var(--box);padding:8px;border-radius:8px}}
</style></head><body><main>
<h1>RDF·그래프 DB의 장점은 이 프로젝트에서 얼마나 큰가 (E8b)</h1>
<p class="sub">TeamWeaver · {e(res['when'])} · 커밋 {e(res['commit'])} · 가상 데이터 · E8(<code>kg-backends.html</code>)의 후속</p>
<h2>A. 규모를 키우면 그래프 DB가 앞서는가</h2>
<p class="sub">조직형 가상 묶음(seed {SEED}) 300 → 1,000 → 3,000명, 배치는 단순 규칙. 지연 중앙값 ms(준비 {WARM}회 뒤 {REPS}회, 첫 규모 전에 버리는 예열). 초록 = 두 후보 중 빠른 쪽, 빨강 = 화면 기준 {SCREEN_MS:.0f} ms 초과.
정확성은 질문마다 앞 {CHECKS}건을 현재 구현과 비교.</p>
<div class="w"><table><tr><th>규모</th><th>질문</th>{''.join(f'<th>{v}</th>' for v in names.values())}</tr>{rows}</table></div>
<div class="w"><table><tr><th>규모</th><th>간선</th><th>다시 만들기(읽기·변환 + 그래프)</th><th>그래프 메모리</th><th>networkx 적재·메모리</th><th>Neo4j 적재·컨테이너</th><th>정확</th></tr>{res_rows}</table></div>
<h3>메모리 그래프가 상쇄하는 방법: 질의 코드 개선(현재 구현 Q1, ms)</h3>
<p class="sub">개선 전 측정은 이 스크립트의 이전 판으로 돌렸고 그때의 core/kg 파일 해시는 기록하지 않았다(원자료만 보존). 개선 전(<code>kg-advantages.before-index.json</code>)에는 (1) "지금 배치된 사람"을 찾으려고 모든 간선을 훑었고 (2) 사업 요구마다 보유자 전원의 레벨을 다시 계산했다.
<code>core/kg</code>에 관계 종류별 색인을 더하고 레벨별 보유자 수를 기술마다 한 번만 세도록 고친 뒤 같은 조건으로 다시 쟀다. 그래프 DB는 질의 엔진이 해 주는 최적화를,
메모리 그래프에서는 코드에서 직접 챙겨야 한다는 뜻이다.</p>
<div class="w"><table><tr><th>규모</th><th>개선 전</th><th>개선 후</th><th>Neo4j</th></tr>{fix_rows}</table></div>
<h2>B. RDF 표준 추론이 기술 사전의 일을 대신할 수 있는가</h2>
<p>사전 {res['reasoning']['concepts']}개 개념, 상위 관계 {res['reasoning']['broader_edges']}개(버전 {e(res['reasoning']['dictionary_version'])}).</p>
<ul><li><b>B1 상위 관계 전이</b>(Spring Boot → Spring → Java): 파이썬 {b1['pairs_python']}쌍 {b1['python_ms']} ms ·
OWL-RL 추론기 {b1['owlrl_ms']:,} ms(트리플 {b1['owlrl_triples_before']:,} → {b1['owlrl_triples_after']:,}), 결과 {'같음' if b1['same_owlrl'] else '다름'} ·
SPARQL 경로식 <code>skos:broader+</code> {b1['sparql_path_ms']} ms, 결과 {'같음' if b1['same_sparql_path'] else '다름'}. {e(b1['note'])}</li>
<li><b>B2 가중 부분 인정</b>(하위 기술 경력의 절반을 상위에, 300명 시연 데이터 {b2['people']}명·인정 {b2['credited_pairs']}건): 파이썬 {b2['python_ms']} ms ·
SPARQL {b2['sparql_ms']} ms({b2['sparql_lines']}줄, 깊이 {b2['max_depth']}까지 펼침), 결과 {'같음' if b2['same'] else '다름'}. {e(b2['note'])}</li></ul>
<pre>{e(b2['sparql'])}</pre>
</main></body></html>"""
    OUT_HTML.write_text(page, "utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-neo4j", action="store_true")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--reasoning-only", action="store_true")
    a = ap.parse_args()
    if a.render_only:
        res = json.loads(OUT_JSON.read_text("utf-8"))
    else:
        res = {"when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
               "commit": K.sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"),
               "dirty": bool(K.sh("git", "-C", str(ROOT), "status", "--porcelain", "--", "rehearsal/kg_advantages.py", "rehearsal/kg_backends.py",
                                  "core/ingest/skills.py", "core/ingest/skill_dictionary.json", "core/ingest/convert.py", "core/kg/graph.py",
                                  "core/kg/views.py", check=False)),
               "sha256": {f: K._sha(ROOT / f) for f in ("rehearsal/kg_advantages.py", "rehearsal/kg_backends.py", "core/ingest/skills.py",
                                                          "core/ingest/skill_dictionary.json", "core/ingest/convert.py",
                                                          "core/kg/graph.py", "core/kg/views.py")}}
        res["reasoning"] = reasoning_part()
        if a.reasoning_only and OUT_JSON.exists():           # 규모 결과는 두고 추론 결과만 바꿔 저장(사전만 바뀐 경우)
            old = json.loads(OUT_JSON.read_text("utf-8"))
            old.update(reasoning=res["reasoning"], reasoning_when=res["when"], sha256_reasoning=res["sha256"])
            OUT_JSON.write_text(json.dumps(old, ensure_ascii=False, indent=1, default=str), "utf-8")
            render(old)
            return
        res["scale"] = scale_part(a)
        OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), "utf-8")
    render(res)


if __name__ == "__main__":
    main()
