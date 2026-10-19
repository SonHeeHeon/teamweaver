"""검토 자료용 사실 지문·긴급 투입 사실 줄이기(core.kg.justify.fact_key·focus_input, 2026-10-19)."""
from dataclasses import replace

from core.kg.justify import fact_key, focus_input
from tests.test_kg_justify import _small


def test_fact_key_ignores_numbers_and_ids_but_not_owner():
    s = _small()
    f = s.facts[2]                                      # P1 MEM
    assert fact_key(f) == fact_key(replace(f, id="F99", text=f.text.replace("0.3", "0.6")))
    assert fact_key(f) != fact_key(s.facts[5])          # P2 MEM
    assert len({fact_key(x) for x in s.facts}) == len(s.facts)


def test_focus_keeps_group_facts_and_new_people_only():
    s = _small()
    out = focus_input(s, {"P2"})
    assert [f.id for f in out.facts] == [f"F{i}" for i in range(1, len(out.facts) + 1)]
    assert {f.kind for f in out.facts if not f.owners} == {"PRJ", "REQ", "CON"}
    assert all(f.owners[0] == "P2" for f in out.facts if f.owners)
    assert out.team == ["P2"] and out.project_id == s.project_id
    assert {fact_key(f) for f in out.facts} <= {fact_key(f) for f in s.facts}   # 번호만 바뀌고 같은 사실


def test_focus_keeps_cowork_when_the_new_person_is_second_owner():
    s = _small()                                       # F7: CW owners (P1, P2)
    out = focus_input(s, {"P2"})
    cw = [f for f in out.facts if f.kind == "CW"]
    assert len(cw) == 1 and cw[0].owners[0] == "P2" and cw[0].text == s.facts[6].text


def test_focused_input_still_produces_a_checked_template():
    from api.rag.justification import generate_justification
    out = generate_justification(None, "none", focus_input(_small(), {"P1"}))
    assert out["method"] == "template" and "P2(고급)" not in out["text"]
