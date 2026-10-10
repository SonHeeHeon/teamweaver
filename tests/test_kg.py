"""Knowledge graph (core/kg, 2026-10-07): one graph built from the CSV bundle, several views on top."""
import datetime as dt
from pathlib import Path

import pytest

from core.ingest.convert import LOOKBACK_MONTHS, lookback_start, to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from core.kg import build_kg, project_evidence, skill_map, with_plan
from core.kg.views import PRACTICAL_MONTHS
from core.optimize.types import AssignEntry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def operating(tmp_path_factory):
    root = generate_org_bundle(tmp_path_factory.mktemp("kg") / "op", 100, seed=11, scenario="operating")
    bundle, report = load_bundle(root)
    ds, parsed = to_dataset(bundle, report)
    return bundle, ds, parsed, build_kg(bundle, ds, parsed)


def test_graph_facts_match_the_source_tables(operating):
    bundle, ds, parsed, kg = operating
    t = bundle.tables
    since = lookback_start(bundle.horizon[0])
    st = kg.stats()
    assert st["nodes"]["person"] == len(ds.people) and st["nodes"]["project"] == len(ds.projects)
    skills = [r for r in t["person_skills.csv"] if r["experience_months"] and
              (r.get("last_used_month") is None or r["last_used_month"] >= since)]
    has = [e for e in kg.edges if e["type"] == "HAS_SKILL"]
    assert sum(1 for e in has if e["own_months"] > 0) == len(skills)          # 본인이 적은 경력 = 원천 행
    assert all(e.get("implied_from") for e in has if e["own_months"] == 0)    # 나머지는 하위 기술에서 인정된 경력(출처 있음)
    assert max(e["months"] for e in kg.edges if e["type"] == "HAS_SKILL") <= LOOKBACK_MONTHS
    cutoff = bundle.horizon[0] - dt.timedelta(days=1)
    work = [r for r in t["work_history.csv"] if (r.get("end_date") is None or r["end_date"] >= since)
            and (r.get("start_date") is None or r["start_date"] <= cutoff)]
    assert st["edges"]["WORKED_ON"] == len(work)
    assert st["edges"]["REQUIRES"] == len(t["project_skill_requirements.csv"])
    assert st["edges"]["COWORKED"] == len(ds.coworks) and st["edges"]["CURRENT_ON"] == len(ds.current)
    proposals = set(bundle.manifest["proposals"])
    assert {n["project_id"] for n in kg.nodes_of("project") if n["proposal"]} == proposals


def test_review_text_only_for_synthetic_data(operating):
    bundle, ds, parsed, kg = operating
    reviewed = [e for e in kg.edges if e["type"] == "REVIEWED"]
    assert reviewed and all(lab.startswith(("좋은 점: ", "아쉬운 점: ")) for e in reviewed for lab in e["labels"])
    assert any("quotes" in e for e in reviewed)                        # the demo bundle is synthetic
    hidden = build_kg(bundle, ds, parsed, reveal_text=False)          # real data: labels only (K5)
    assert not any("quotes" in e for e in hidden.edges if e["type"] == "REVIEWED")


def test_skill_map_counts_by_hand(operating):
    bundle, ds, parsed, kg = operating
    rows = {r["skill"]: r for r in skill_map(kg)}
    since = lookback_start(bundle.horizon[0])
    on = {c.person_id for c in ds.current}
    req = bundle.tables["project_skill_requirements.csv"][0]
    skill = req["skill_name"]
    practical = {r["person_id"] for r in bundle.tables["person_skills.csv"]
                 if r["skill_name"] == skill and min(r["experience_months"], LOOKBACK_MONTHS) >= PRACTICAL_MONTHS
                 and (r.get("last_used_month") is None or r["last_used_month"] >= since)}
    row = rows[skill]
    assert row["practical"] == len(practical) and row["practical_free"] == len(practical - on)
    need = sum(r["headcount"] for r in bundle.tables["project_skill_requirements.csv"] if r["skill_name"] == skill)
    assert row["demand_headcount"] == need
    prop = sum(r["headcount"] for r in bundle.tables["project_skill_requirements.csv"]
               if r["skill_name"] == skill and r["project_id"] in set(bundle.manifest["proposals"]))
    assert row["proposal_gap"] == max(0, prop - len(practical - on))


def test_project_evidence_and_why_not_others(operating):
    from api.settings import PlacementSettings
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.scoring.engine import ScoringEngine
    bundle, ds, parsed, kg = operating
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current]
    jid = ds.current[0].project_id
    ev = project_evidence(kg, jid, entries, graph=g, S=S, C=C, params=params)
    team = {e.person_id for e in entries if e.project_id == jid}
    assert {m["person_id"] for m in ev["members"]} == team
    for rq in ev["requirements"]:                                       # coverage = members meeting the minimum
        met = sum(1 for m in ev["members"] for c in m["requirements"] if c["skill"] == rq["skill"] and c["status"] == "met")
        assert rq["met_by"] == met and rq["status"] == ("met" if met >= rq["headcount"] else "short")
    base = evaluate_plan(g, S, C, params, entries).objective.total
    pid, alts = next(iter(ev["alternatives"].items()))
    for a in alts:                                                      # the delta is the evaluator's own number
        assert a["person_id"] not in team
        swapped = [e for e in entries if not (e.person_id == pid and e.project_id == jid)]
        alloc = next(e.alloc for e in entries if e.person_id == pid and e.project_id == jid)
        swapped.append(AssignEntry(person_id=a["person_id"], project_id=jid, alloc=alloc))
        assert a["delta_total"] == pytest.approx(evaluate_plan(g, S, C, params, swapped).objective.total - base, abs=1e-4)


def test_with_plan_adds_assignments_without_touching_the_base(operating):
    bundle, ds, parsed, kg = operating
    n = len(kg.edges)
    plan = with_plan(kg, [AssignEntry(person_id=ds.people[0].id, project_id=ds.projects[0].id, alloc=0.5)], "A")
    assert len(kg.edges) == n and sum(1 for e in plan.edges if e["type"] == "ASSIGNED") == 1
    assert plan.export([f"person:{ds.people[0].id}", f"project:{ds.projects[0].id}"])["edges"][-1]["type"] == "ASSIGNED"


def test_real_data_hides_names_text_and_summaries(operating):
    """Default privacy follows the manifest: anything but synthetic=true hides names, review text and work summaries."""
    bundle, ds, parsed, _ = operating
    import copy
    real = copy.copy(bundle)
    real.manifest = {k: v for k, v in bundle.manifest.items() if k != "synthetic"}
    kg = build_kg(real, ds, parsed)
    assert all(n["label"] == n["person_id"] for n in kg.nodes_of("person"))
    assert not any("quotes" in e for e in kg.edges if e["type"] == "REVIEWED")
    assert not any("summary" in e for e in kg.edges if e["type"] == "WORKED_ON")


def test_client_inference_respects_word_boundaries():
    from core.kg.graph import _client_of
    clients = ["SK", "SKT", "금융사 라", "금융사 라2"]
    assert _client_of("[제안] SKT 데이터 플랫폼", clients) == "SKT"
    assert _client_of("[실행] 금융사 라2 차세대 플랫폼", clients) == "금융사 라2"
    assert _client_of("[실행] 금융사 라 데이터 마트", clients) == "금융사 라"
    assert _client_of("ASKA 시스템", clients) is None


def test_phase_marks_proposals_and_unknown_industry_is_not_zero(operating):
    bundle, ds, parsed, kg = operating
    for n in kg.nodes_of("project"):
        row = next(r for r in bundle.tables["projects.csv"] if r["project_id"] == n["project_id"])
        assert n["proposal"] == (n["project_id"] in set(bundle.manifest["proposals"]) or row["phase"] == "제안")
    import copy
    odd = copy.copy(bundle)
    odd.tables = dict(bundle.tables)
    odd.tables["projects.csv"] = [{**r, "sector": "미지의 부문"} for r in bundle.tables["projects.csv"]]
    kg2 = build_kg(odd, ds, parsed)
    jid = ds.current[0].project_id
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current]
    ev = project_evidence(kg2, jid, entries)
    assert ev["industry"] is None and all(m["same_industry_projects"] is None for m in ev["members"])


def test_work_history_months_are_clipped_to_the_window(operating):
    bundle, ds, parsed, _ = operating
    import copy
    import datetime as dt
    since = lookback_start(bundle.horizon[0])
    b2 = copy.copy(bundle)
    b2.tables = dict(bundle.tables)
    first = dict(bundle.tables["work_history.csv"][0])
    first.update(start_date=since - dt.timedelta(days=400), end_date=bundle.horizon[0] + dt.timedelta(days=200))
    b2.tables["work_history.csv"] = [first]
    kg = build_kg(b2, ds, parsed)
    (e,) = [x for x in kg.edges if x["type"] == "WORKED_ON"]
    cutoff = bundle.horizon[0] - dt.timedelta(days=1)
    assert e["months"] == (cutoff.year - since.year) * 12 + cutoff.month - since.month + 1


def test_future_work_is_not_past_experience(operating):
    """Codex review MUST (2026-10-10): a row that starts after the planning cutoff is a future assignment, not history --
    no WORKED_ON edge, so it never shows up as "같은 산업 과거 사업" or "같은 고객사 과거 사업" in the justification."""
    import copy
    bundle, ds, parsed, _ = operating
    jid = ds.current[0].project_id
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current if c.project_id == jid]
    pid = entries[0].person_id
    kg0 = build_kg(bundle, ds, parsed)
    pj = kg0.nodes[f"project:{jid}"]
    b2 = copy.copy(bundle)
    b2.tables = dict(bundle.tables)
    row = dict(next(r for r in bundle.tables["work_history.csv"] if r["person_id"] == pid))
    row.update(project_code="FUTURE-1", work_id="FUTURE-1", work_name="FUTURE-1", client=pj.get("client") or "고객사X",
               industry=pj.get("industry") or "산업X", start_date=bundle.horizon[0] + dt.timedelta(days=10),
               end_date=bundle.horizon[0] + dt.timedelta(days=200))
    b2.tables["work_history.csv"] = [*bundle.tables["work_history.csv"], row]
    kg = build_kg(b2, ds, parsed)
    assert "past:FUTURE-1" not in kg.nodes
    assert [e for e in kg.out(f"person:{pid}", "WORKED_ON")] == [e for e in kg0.out(f"person:{pid}", "WORKED_ON")]
    before, after = project_evidence(kg0, jid, entries), project_evidence(kg, jid, entries)
    m0, m1 = (next(m for m in ev["members"] if m["person_id"] == pid) for ev in (before, after))
    assert m1["same_industry_projects"] == m0["same_industry_projects"]
    assert "FUTURE-1" not in m1["same_client_projects"] and m1["same_client_projects"] == m0["same_client_projects"]
    # 미래 이력에만 있는 고객사·산업 이름은 지금 사업의 고객사·산업 추정에도 쓰지 않는다(Codex 리뷰 2회째)
    proj = next(r for r in bundle.tables["projects.csv"] if r["project_id"] == jid)
    only_future = dict(row, client="미래고객", project_code="FUTURE-2", work_id="FUTURE-2", work_name="FUTURE-2")
    b3 = copy.copy(bundle)
    b3.tables = dict(bundle.tables)
    b3.tables["work_history.csv"] = [*bundle.tables["work_history.csv"], only_future]
    b3.tables["projects.csv"] = [dict(r, project_name="미래고객 " + r["project_name"]) if r is proj else r for r in bundle.tables["projects.csv"]]
    kg3 = build_kg(b3, ds, parsed)
    assert kg3.nodes[f"project:{jid}"]["client"] == pj.get("client") and "client:미래고객" not in kg3.nodes
    assert all((kg3.nodes[k].get("client"), kg3.nodes[k].get("industry")) == (kg0.nodes[k].get("client"), kg0.nodes[k].get("industry"))
               for k in kg0.nodes if k.startswith("project:"))
    ongoing = dict(row, project_code="ONGOING-1", work_id="ONGOING-1", work_name="ONGOING-1",
                   start_date=bundle.horizon[0] - dt.timedelta(days=60))   # 계획 전에 시작해 진행 중이면 계획 시작 전날까지 센다
    b2.tables["work_history.csv"] = [*bundle.tables["work_history.csv"], ongoing]
    (e,) = [x for x in build_kg(b2, ds, parsed).out(f"person:{pid}", "WORKED_ON") if x["dst"] == "past:ONGOING-1"]
    assert e["months"] >= 1


def test_alternatives_keep_the_monthly_allocation(operating):
    """Review MUST: swapping keeps the leaving person's monthly allocation, so the delta is the person effect only."""
    from api.settings import PlacementSettings
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.scoring.engine import ScoringEngine
    bundle, ds, parsed, kg = operating
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    c0 = ds.current[0]
    months = g.projects[g.project_index[c0.project_id]].months
    monthly = {m: (0.5 if k % 2 else 1.0) for k, m in enumerate(months)}
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current[1:]]
    entries.append(AssignEntry(person_id=c0.person_id, project_id=c0.project_id,
                               alloc=sum(monthly.values()) / len(monthly), monthly_alloc=monthly))
    import core.evaluate.plan_eval as pe
    seen = []
    real = pe.evaluate_plan

    team = {x.person_id for x in entries if x.project_id == c0.project_id}

    def spy(graph, S_, C_, params_, plan_entries):          # the score uses the mean, so check what is actually passed
        if not any(e.person_id == c0.person_id and e.project_id == c0.project_id for e in plan_entries):   # c0 swapped out
            seen.append([e for e in plan_entries if e.project_id == c0.project_id and e.person_id not in team])
        return real(graph, S_, C_, params_, plan_entries)
    pe.evaluate_plan = spy
    try:
        ev = project_evidence(kg, c0.project_id, entries, graph=g, S=S, C=C, params=params)
    finally:
        pe.evaluate_plan = real
    swapped_in = [x for batch in seen for x in batch]
    assert swapped_in and all(x.monthly_alloc == monthly for x in swapped_in)
    assert ev["alternatives"][c0.person_id]


@pytest.mark.parametrize("name", ["org-n100", "org-n300"])
def test_kg_skill_levels_match_the_scoring_input(name, tmp_path):
    """지식 그래프의 기술 경력이 입력 단계(점수 S의 입력)와 같은 기준이다 -- 사전 별칭·하위 기술 부분 인정 포함(리뷰 MUST 2026-10-09).
    예전엔 그래프가 사전을 쓰지 않아 300명에서 "S로는 충족인데 근거는 미충족"이 172건이었다."""
    from api.datasets import extract_bundle_zip
    from core.ingest.convert import level_from_months, to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg
    root = ROOT / "demo" / name
    if not root.exists():
        root = extract_bundle_zip((ROOT / "demo" / f"{name}.zip").read_bytes(), tmp_path / "bundle")
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep)
    kg = build_kg(b, ds, parsed)
    implied = 0
    for p in ds.people:
        edges = kg.out(f"person:{p.id}", "HAS_SKILL")
        assert {kg.nodes[e["dst"]]["label"]: level_from_months(e["months"]) for e in edges} == p.skills
        implied += sum(1 for e in edges if e.get("implied_from"))
    assert implied > 0
    for j in ds.projects:
        assert {(kg.nodes[e["dst"]]["label"], level_from_months(e["min_months"] or 0), e["headcount"])
                for e in kg.out(f"project:{j.id}", "REQUIRES")} == {(r.skill, r.min_level, r.headcount) for r in j.requirements}


@pytest.mark.parametrize("kw", [{}, {"partial_credit": 1.0}, {"skill_dictionary": False}])
def test_kg_matches_scoring_on_a_renamed_copy(kw, tmp_path):
    """별칭·버전 표기·괄호 병기로 기술 이름을 바꾼 사본에서도 그래프 경력·요구가 입력 단계와 같다(리뷰 SHOULD) -- 같은 설정을 둘에 넘긴다."""
    import csv
    import hashlib
    import json
    import shutil
    from core.ingest.convert import level_from_months
    from core.ingest.skills import load_dictionary
    sd = load_dictionary()
    root = tmp_path / "b"
    shutil.copytree(ROOT / "demo" / "org-n100", root)
    for f in ("person_skills.csv", "project_skill_requirements.csv"):
        with open(root / f, encoding="utf-8", newline="") as fh:
            r = csv.DictReader(fh)
            cols, rows = r.fieldnames, list(r)
        for i, row in enumerate(rows):
            c = sd.lookup(row["skill_name"])
            if i % 3 == 0 and c.get("aliases"):
                row["skill_name"] = c["aliases"][0]
            elif i % 3 == 1:
                row["skill_name"] = f"{c['label']} 2" if "(" in c["label"] or not c.get("aliases") else f"{c['aliases'][0]}({c['label']})"
        with open(root / f, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    m["files"] = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in m["files"]}
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep, **kw)
    kg = build_kg(b, ds, parsed, **kw)
    for p in ds.people:
        assert {kg.nodes[e["dst"]]["label"]: level_from_months(e["months"]) for e in kg.out(f"person:{p.id}", "HAS_SKILL")} == p.skills
    for j in ds.projects:
        assert {(kg.nodes[e["dst"]]["label"], level_from_months(e["min_months"] or 0), e["headcount"])
                for e in kg.out(f"project:{j.id}", "REQUIRES")} == {(r.skill, r.min_level, r.headcount) for r in j.requirements}
