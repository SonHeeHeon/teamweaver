"""Knowledge graph (core/kg, 2026-10-07): one graph built from the CSV bundle, several views on top."""
import datetime as dt

import pytest

from core.ingest.convert import LOOKBACK_MONTHS, lookback_start, to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from core.kg import build_kg, project_evidence, skill_map, with_plan
from core.kg.views import PRACTICAL_MONTHS
from core.optimize.types import AssignEntry


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
    assert st["edges"]["HAS_SKILL"] == len(skills)
    assert max(e["months"] for e in kg.edges if e["type"] == "HAS_SKILL") <= LOOKBACK_MONTHS
    work = [r for r in t["work_history.csv"] if r.get("end_date") is None or r["end_date"] >= since]
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
