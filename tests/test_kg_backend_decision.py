"""E8 판정 규칙(rehearsal/kg_backend_decision.py)이 문서에 적은 대로 동작하는지 -- 측정값 없이 만든 입력으로."""
from rehearsal.kg_backend_decision import QUESTIONS, SIZES, decide
from rehearsal.kg_backends import sensitivity


def _cand(ms, *, server=False, disk=False, loc=100, correct=True, slow_q=()):
    lat = {str(n): {q: {"p50_ms": (500.0 if (n == 300 and q in slow_q) else ms(n, q))} for q in QUESTIONS} for n in SIZES}
    return {"correct": {q: correct for q in QUESTIONS}, "latency": lat, "server": server, "disk_persist": disk, "loc": loc}


def _res(**cands):
    return {"baseline": "core_kg", "candidates": {"core_kg": _cand(lambda n, q: 0.1), **cands},
            "standards_requirement": {"required_now": False}, "rdf_export": {"ok": True}}


def test_wrong_answer_is_out():
    r = _res(a=_cand(lambda n, q: 1.0, correct=False), b=_cand(lambda n, q: 2.0))
    d = decide(r)
    assert d["decision"] == "b"
    assert any(t["rule"] == 1 and t["candidate"] == "a" for t in d["trail"])


def test_slow_on_three_questions_is_out_but_two_is_not():
    r = _res(a=_cand(lambda n, q: 1.0, slow_q=QUESTIONS[:3]), b=_cand(lambda n, q: 2.0))
    assert decide(r)["decision"] == "b"
    r = _res(a=_cand(lambda n, q: 1.0, slow_q=QUESTIONS[:2]), b=_cand(lambda n, q: 2.0))
    assert decide(r)["decision"] == "a"


def test_server_or_disk_goes_behind_even_if_faster():
    r = _res(fast_server=_cand(lambda n, q: 0.5, server=True, disk=True), mem=_cand(lambda n, q: 2.0))
    d = decide(r)
    assert d["decision"] == "mem"
    assert any(t["rule"] == 3 and t["candidate"] == "fast_server" for t in d["trail"])


def test_server_only_candidates_still_decided():
    r = _res(x=_cand(lambda n, q: 1.0, server=True), y=_cand(lambda n, q: 2.0, server=True))
    assert decide(r)["decision"] == "x"


def test_near_tie_goes_to_smaller_code():
    r = _res(a=_cand(lambda n, q: 1.00, loc=300), b=_cand(lambda n, q: 1.05, loc=120))
    d = decide(r)
    assert d["decision"] == "b"
    assert d["trail"][-2]["near_tie"]["picked_by_loc"] == "b"


def test_baseline_is_never_picked():
    r = _res(a=_cand(lambda n, q: 5.0))
    assert decide(r)["decision"] == "a"


def test_rule5_fires_only_if_required_and_export_fails():
    r = _res(mem=_cand(lambda n, q: 1.0))
    r["standards_requirement"] = {"required_now": True}
    r["rdf_export"] = {"ok": False}
    assert decide(r)["decision"] == "RDF 재검토"
    r["rdf_export"] = {"ok": True}
    assert decide(r)["decision"] == "mem"


def test_sensitivity_counts_cells():
    def ms(name):
        return lambda n, q: {"networkx": 1.0, "rdf_oxigraph": 2.0, "neo4j": 0.5 if q == "q1_skill_map" else 3.0}[name]
    r = _res(networkx=_cand(ms("networkx")), rdf_oxigraph=_cand(ms("rdf_oxigraph")), neo4j=_cand(ms("neo4j"), server=True))
    s = sensitivity(r)
    assert s["all_three"] == {"networkx": 9, "rdf_oxigraph": 0, "neo4j": 3}
    assert s["all_three_leader"] == "networkx" and s["all_three_majority"]
    assert s["core_vs_nx"] == {"core_kg": 12, "networkx": 0}


def test_known_gap_tie_on_wins_with_far_means_keeps_first_listed():
    """사전 등록 코드의 알려진 차이(리뷰 SHOULD, 고치지 않고 고정): 셀 승수 동률 + 평균 차이 10% 초과면 문서("코드 줄 수가 적은 쪽")와 달리
    max()가 먼저 나열된 후보를 고른다. 이 동작이 바뀌면 보고서의 "알려진 차이" 설명도 바꿔야 한다."""
    def ms_a(n, q):
        return 1.0 if q in QUESTIONS[:2] else 9.0

    def ms_b(n, q):
        return 2.0 if q in QUESTIONS[:2] else 1.0
    r = _res(a=_cand(ms_a, loc=300), b=_cand(ms_b, loc=100))
    d = decide(r)
    rule4 = next(t for t in d["trail"] if t["rule"] == 4)
    assert rule4["cell_wins"] == {"a": 6, "b": 6}
    assert d["decision"] == "a"
