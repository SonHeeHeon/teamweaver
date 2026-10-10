"""E7c 판정 도구(rehearsal/justify_scale_e7c.py): 덧붙임 무결성 확인과 G1 하한(Codex 사후 리뷰 2026-10-10) -- 측정 전에 도구가 실제로 잡는지 확인한다."""
import json

from rehearsal import justify_scale_e7c as e7c
from tests.test_kg_justify import GOOD, _Client, _small_adverse

PEOPLE = {"P1", "P2", "P9"}


def _row(raw):
    from api.rag.justification import generate_justification
    out = generate_justification(_Client(json.dumps({"text": raw}, ensure_ascii=False)), "gpt-6-luna", _small_adverse())
    assert out["method"] == "graphrag"
    return {"llm_text": raw, "rendered": out["text"], "addendum": out["addendum"], "appended_adverse": out["appended_adverse"]}


def test_integrity_passes_service_output_and_catches_addendum_faults():
    s = _small_adverse()
    raw = GOOD.replace("이며, {F5}", "").replace(" 동료 평가는 {F8}입니다.", "")
    row = _row(raw)
    assert e7c.integrity_problems(row, s, PEOPLE) == []
    assert e7c.integrity_problems(_row(GOOD), s, PEOPLE) == []                     # 빠진 것이 없으면 덧붙임도 없다
    no_add = {**row, "rendered": row["rendered"].split("\n")[0], "addendum": None, "appended_adverse": []}
    assert any("덧붙임 문단이 없" in p for p in e7c.integrity_problems(no_add, s, PEOPLE))
    assert any("불리한 사실 칩 없음" in p for p in e7c.integrity_problems(no_add, s, PEOPLE))
    wrong = {**row, "rendered": row["rendered"].replace("[F8]", "[F10]")}
    assert any("≠ 빠진 불리한 사실" in p for p in e7c.integrity_problems(wrong, s, PEOPLE))
    short = {**row, "rendered": row["rendered"].replace("P1의 Spark", "Spark", 1)}  # 덧붙임에 주인 ID 없는 문구
    assert e7c.integrity_problems(short, s, PEOPLE)
    extra = {**_row(GOOD), "rendered": _row(GOOD)["rendered"] + "\n다만 다음 사항도 함께 확인이 필요합니다: x[F5]."}
    assert any("덧붙였다" in p for p in e7c.integrity_problems(extra, s, PEOPLE))


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
