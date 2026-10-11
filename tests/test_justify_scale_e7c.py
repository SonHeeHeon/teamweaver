"""E7c 판정 도구(rehearsal/justify_scale_e7c.py): 덧붙임 무결성 확인과 G1 하한(Codex 사후 리뷰 2026-10-10) -- 측정 전에 도구가 실제로 잡는지 확인한다."""
import json

from rehearsal import justify_scale_e7c as e7c
from tests.test_kg_justify import GOOD, _Client, _small_adverse

PEOPLE = {"P1", "P2", "P9"}


def _row(raw):
    from api.rag.justification import generate_justification
    out = generate_justification(_Client(json.dumps({"text": raw}, ensure_ascii=False)), "gpt-6-luna", _small_adverse())
    assert out["method"] == "graphrag"
    return {"llm_text": raw, "rendered": out["text"], "addendum": out["addendum"], "appended_adverse": out["appended_adverse"],
            "addendum_groups": out["addendum_groups"]}


def test_integrity_passes_service_output_and_catches_addendum_faults():
    s = _small_adverse()
    raw = GOOD.replace("이며, {F5}", "").replace(" 동료 평가는 {F8}입니다.", "")
    row = _row(raw)
    assert e7c.integrity_problems(row, s, PEOPLE) == []
    assert e7c.integrity_problems(_row(GOOD), s, PEOPLE) == []                     # 빠진 것이 없으면 덧붙임도 없다
    no_add = {**row, "rendered": row["rendered"].split("\n")[0], "addendum": None, "appended_adverse": [], "addendum_groups": []}
    assert any("덧붙임 문단이 없" in p for p in e7c.integrity_problems(no_add, s, PEOPLE))
    assert any("불리한 사실 칩 없음" in p for p in e7c.integrity_problems(no_add, s, PEOPLE))
    wrong = {**row, "rendered": row["rendered"].replace("[F8]", "[F10]")}
    assert any("≠ 빠진 불리한 사실" in p for p in e7c.integrity_problems(wrong, s, PEOPLE))
    moved = {**row, "rendered": row["rendered"].replace("\n- P1: ", "\n- P2: ")}   # 남의 줄에 놓인 사실
    assert any("P2 줄에 있다(주인 P1)" in p for p in e7c.integrity_problems(moved, s, PEOPLE))
    phrase = {**row, "rendered": row["rendered"].replace("Spark 경력 8개월로", "Spark 경력 80개월로")}
    assert any("앞 문구가 사실 문구가 아니다" in p for p in e7c.integrity_problems(phrase, s, PEOPLE))
    assert any("appended_adverse" in p for p in e7c.integrity_problems({**row, "appended_adverse": ["F8", "F5"]}, s, PEOPLE))
    assert any("addendum_groups" in p for p in e7c.integrity_problems({**row, "addendum_groups": []}, s, PEOPLE))
    extra = {**_row(GOOD), "rendered": _row(GOOD)["rendered"] + "\n다만 다음 사항도 함께 확인이 필요합니다.\n- P1: x[F5]"}
    assert any("덧붙였다" in p for p in e7c.integrity_problems(extra, s, PEOPLE))


def test_integrity_checks_line_order_and_owner_lines():
    from core.kg.justify import Fact
    s = _small_adverse()
    s.facts.append(Fact("F13", "SKL", "P2의 Spark", "Spark 경력 3개월로 레벨 1(요구 레벨 2) 미달", ("P2",), "Spark", "short", adverse=True))
    raw = GOOD.replace("이며, {F5}", "").replace(" 마지막으로 규칙 준수 현황은 {F10}입니다.", "")
    from api.rag.justification import generate_justification
    out = generate_justification(_Client(json.dumps({"text": raw}, ensure_ascii=False)), "gpt-6-luna", s)
    row = {"llm_text": raw, "rendered": out["text"], "addendum": out["addendum"], "appended_adverse": out["appended_adverse"],
           "addendum_groups": out["addendum_groups"]}
    assert e7c.integrity_problems(row, s, PEOPLE) == []
    lead, *lines = out["addendum"].split("\n")
    assert [ln.split(":")[0] for ln in lines] == ["- 사업 전체", "- P1", "- P2"]
    swapped = "\n".join([lead, lines[1], lines[0], lines[2]])
    bad = {**row, "rendered": out["text"].replace(out["addendum"], swapped), "addendum": swapped}
    assert any("줄 순서" in p for p in e7c.integrity_problems(bad, s, PEOPLE))
    f13 = s.facts.pop()                                                              # 주인 없는 사람별 사실이 "사업 전체" 줄에 놓이면
    s.facts.append(Fact(f13.id, f13.kind, f13.text, f13.phrase, (), f13.skill, f13.status, adverse=True))
    whole = "\n".join([lead, lines[0] + "; " + lines[2].split(": ", 1)[1], lines[1]])
    bad = {**row, "rendered": out["text"].replace(out["addendum"], whole), "addendum": whole}
    assert any("주인이 없다" in p for p in e7c.integrity_problems(bad, s, PEOPLE))


def _res(made_by_family, accepted, checked, failed=0, detected=None):
    muts = {"render_integrity": {"checked": checked, "failed": failed, "examples": []}}
    for f, n in made_by_family.items():
        muts[f"{f}_x"] = {"made": n, "detected": n if detected is None else detected.get(f, n)}
    calls = [{"method": "graphrag", "set": "A"}] * accepted
    return {"mutations": muts, "calls": calls}


def test_g1_needs_every_family_above_the_floor_and_full_integrity(monkeypatch):
    monkeypatch.setattr(e7c, "_set_summary", lambda res, part: {})
    monkeypatch.setattr(e7c, "E7B_JSON", e7c.ROOT / "nonexistent.json.gz")
    full = {f: 400 for f in e7c.FAMILIES}
    assert e7c.summarize(_res(full, 600, 600))["G1"]["pass"]
    g = e7c.summarize(_res({**full, "H3": 299}, 600, 600))["G1"]                     # 하한 = max(100, 0.5 × 600) = 300
    assert not g["pass"] and g["families_below_floor"] == ["H3"] and g["detection_ok"]
    assert not e7c.summarize(_res({f: n for f, n in full.items() if f != "M7"}, 600, 600))["G1"]["pass"]   # 종류가 아예 없음
    assert not e7c.summarize(_res(full, 600, 599))["G1"]["pass"]                    # 채택 글 하나라도 무결성 검사를 안 거침
    assert not e7c.summarize(_res(full, 600, 600, failed=1))["G1"]["pass"]
    assert not e7c.summarize(_res(full, 200, 200))["G1"]["pass"]                    # 무결성 검사 300건 미만
    assert not e7c.summarize(_res(full, 600, 600, detected={"H1": 399}))["G1"]["pass"]
