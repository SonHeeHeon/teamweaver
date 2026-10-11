import sqlite3

import pytest

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.sqlite_store import build_sqlite


@pytest.fixture
def conn(tmp_path):
    ds = generate_dataset(40, 8, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path / "rag.db"
    build_sqlite(ds, parsed, path)
    c = sqlite3.connect(path)
    yield c
    c.close()


def test_swap_context_groups_by_kind_and_person(conn):
    from api.rag.context import swap_context
    ctx = swap_context(conn, "p000", "p001")
    assert set(ctx.keys()) == {"p000", "p001"}
    for pid in ("p000", "p001"):
        assert set(ctx[pid].keys()) <= {"skills", "coworks", "evidence"}


def test_swap_context_evidence_carries_real_review_sentences(conn):
    """리뷰 근거 인용이 실제로 담기는지 -- PoC 설계 §6이 요구하는
    '리뷰 근거 문장(evidence) 인용'을 직접 확인한다."""
    from api.rag.context import swap_context
    ctx = swap_context(conn, "p000", "p001")
    has_evidence = any(ctx[pid].get("evidence") for pid in ("p000", "p001"))
    # 데이터셋이 우연히 p000/p001에 리뷰가 없을 수도 있으니, 없으면 다른 쌍으로 재확인.
    if not has_evidence:
        ctx = swap_context(conn, "p010", "p011")
        has_evidence = any(ctx[pid].get("evidence") for pid in ("p010", "p011"))
    assert has_evidence


@pytest.fixture
def data_and_conn(tmp_path):
    ds = generate_dataset(40, 8, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path / "rag.db"
    build_sqlite(ds, parsed, path)
    c = sqlite3.connect(path)
    yield ds, parsed, c
    c.close()


def _reviewed_pair(ds):
    reviewed = {r.reviewee_id for r in ds.reviews}
    ids = [p.id for p in ds.people if p.id in reviewed]
    return ids[0], ids[1]


def test_without_an_index_the_context_is_unchanged(data_and_conn):
    """K5 회귀: 색인을 넘기지 않으면 예전 문맥(sqlite_rag 행)과 같다."""
    from api.rag.context import swap_context
    ds, _, conn = data_and_conn
    out_id, in_id = _reviewed_pair(ds)
    ctx = swap_context(conn, out_id, in_id)
    assert all(set(e) == {"key", "value"} for pid in (out_id, in_id) for e in ctx[pid]["evidence"])
    assert swap_context(conn, out_id, in_id, evidence=None) == ctx


def test_with_an_index_evidence_is_sourced_sentences_not_numbers(data_and_conn):
    """K5: 근거 자리에 극성 숫자 대신 출처 ID가 붙은 원문 문장이 들어간다."""
    from api.rag.context import swap_context
    from api.rag.evidence import build_evidence_index
    ds, parsed, conn = data_and_conn
    idx = build_evidence_index(ds, parsed, reveal_text=True)
    out_id, in_id = _reviewed_pair(ds)
    plain = swap_context(conn, out_id, in_id)
    ctx = swap_context(conn, out_id, in_id, evidence=idx)
    for pid in (out_id, in_id):
        assert ctx[pid]["skills"] == plain[pid]["skills"] and ctx[pid]["coworks"] == plain[pid]["coworks"]
        assert ctx[pid]["evidence"], pid
        for e in ctx[pid]["evidence"]:
            assert set(e) == {"source_id", "reviewer_id", "kind", "text", "polarity"}
            assert e["kind"] == "quote" and idx.verify_quote(e["source_id"], e["text"])
        assert len(ctx[pid]["evidence"]) <= 6


def test_hidden_index_puts_no_review_text_in_the_context(data_and_conn):
    from api.rag.context import swap_context
    from api.rag.evidence import build_evidence_index
    ds, parsed, conn = data_and_conn
    out_id, in_id = _reviewed_pair(ds)
    ctx = swap_context(conn, out_id, in_id, evidence=build_evidence_index(ds, parsed, reveal_text=False))
    texts = [t for r in ds.reviews for t in (r.positive.text, r.negative.text)]
    blob = repr(ctx)
    assert all(e["kind"] == "label" for pid in (out_id, in_id) for e in ctx[pid]["evidence"])
    assert not any(t in blob for t in texts)


def test_project_id_adds_the_project_requirements(data_and_conn):
    from api.rag.context import swap_context
    ds, _, conn = data_and_conn
    out_id, in_id = _reviewed_pair(ds)
    j = ds.projects[0]
    ctx = swap_context(conn, out_id, in_id, project_id=j.id)
    assert ctx["project"]["id"] == j.id and ctx["project"]["name"] == j.name
    assert ctx["project"]["requirements"] == sorted(
        [{"skill": r.skill, "min_level": r.min_level, "headcount": r.headcount} for r in j.requirements],
        key=lambda r: r["skill"])
    assert "project" not in swap_context(conn, out_id, in_id)
    assert "project" not in swap_context(conn, out_id, in_id, project_id="no-such-project")


def test_credited_skills_are_marked_in_the_briefing_context(tmp_path):
    """하위 기술에서 인정한 레벨은 근거 색인의 별도 표에 남고, 교체 설명 문맥이 출처를 붙인다(2026-10-11). 인정 표가 없는 예전 색인은 예전 문맥."""
    from api.rag.context import swap_context
    from core.domain.models import SkillCredit
    ds = generate_dataset(40, 8, seed=5)
    p0 = ds.people[0]
    skill = next(iter(p0.skills))
    ds.people[0] = p0.model_copy(update={"skill_credit": {skill: SkillCredit(source="하위기술", months=14, depth=1, own_months=3)}})
    path = tmp_path / "credit.db"
    build_sqlite(ds, parse_reviews_rule_based(ds.reviews), path)
    c = sqlite3.connect(path)
    rows = c.execute("SELECT person_id, skill, source, months, depth, own_months FROM skill_credit").fetchall()
    assert rows == [(p0.id, skill, "하위기술", 14, 1, 3)]
    ctx = swap_context(c, p0.id, ds.people[1].id)
    marked = [s for s in ctx[p0.id]["skills"] if "credited_from" in s]
    assert marked == [{"key": skill, "value": str(p0.skills[skill]), "credited_from": "하위기술", "credited_months": 14, "own_months": 3}]
    assert all("credited_from" not in s for s in ctx[ds.people[1].id]["skills"])
    c.execute("DROP TABLE skill_credit")
    assert all("credited_from" not in s for s in swap_context(c, p0.id, ds.people[1].id)[p0.id]["skills"])
    c.close()


def test_briefing_prompt_tells_the_model_to_name_the_credit_source():
    from api.rag import briefing
    assert "credited_from" in briefing._BASE and "인정" in briefing._BASE
    assert "'Java" not in briefing._BASE and "따옴표로 감싸지 마라" in briefing._BASE   # 따옴표째 따라 쓰면 인용 검사가 글을 버린다(폴백 리뷰)
    assert "매번 레벨 뒤 괄호에 출처" in briefing._BASE      # "밝혀라"만으로는 실측 2/10만 괄호를 썼다(2026-10-12)


@pytest.mark.parametrize("reveal", [False, True])
def test_credit_source_in_parentheses_passes_the_quote_guard(reveal):
    """지시문이 권하는 표기(괄호, 따옴표 없음)는 두 모드의 인용 검사를 통과하고, 따옴표로 감싸면 걸린다."""
    from api.rag.briefing import BriefingRejected, _check_inline_quotes
    from api.rag.evidence import EvidenceIndex
    ev = EvidenceIndex({}, {}, reveal_text=reveal)
    _check_inline_quotes("들어올 사람은 Java(Spring 경력에서 인정) 레벨 2라 요구를 채운다.", {}, ev)
    with pytest.raises(BriefingRejected):
        _check_inline_quotes("들어올 사람은 'Java(Spring 경력에서 인정)' 레벨 2다.", {}, ev)
