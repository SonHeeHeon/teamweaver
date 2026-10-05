"""K5 T1: review evidence index -- every evidence carries a source id, and "quote" means verbatim."""
import pytest

from api.rag.evidence import build_evidence_index, normalize
from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import Dataset, ParsedReview, PeerReview, ReviewSection


@pytest.fixture(scope="module")
def fixture_data():
    return load_fixtures(FIXTURES_DIR)


def _review(rv, re_, pos_text, neg_text, pos=("소통",), neg=("문서화",)):
    return PeerReview(reviewer_id=rv, reviewee_id=re_,
                      positive=ReviewSection(items=list(pos), text=pos_text),
                      negative=ReviewSection(items=list(neg), text=neg_text))


def _ds(reviews):
    base = generate_dataset(6, 1, seed=1)
    return Dataset(people=base.people, projects=base.projects, coworks=[], reviews=reviews), base


def test_every_quote_in_the_fixture_is_verbatim_and_sourced(fixture_data):
    ds, parsed = fixture_data
    idx = build_evidence_index(ds, parsed, reveal_text=True)
    quotes = summaries = 0
    for p in ds.people:
        for ev in idx.for_person(p.id, limit=None):
            src = idx.source(ev.source_id)
            assert src is not None and src.reviewee_id == p.id
            if ev.kind == "quote":
                quotes += 1
                assert normalize(ev.text) in normalize(src.text)
                assert idx.verify_quote(ev.source_id, ev.text)
            else:
                assert ev.kind == "summary"
                summaries += 1
                assert not idx.verify_quote(ev.source_id, ev.text)
    # 2026-10-05 measurement: 526 of the 532 LLM "quotes" are verbatim, 6 were reworded by the parser
    assert (quotes, summaries) == (526, 6)


def test_reworded_llm_quote_is_a_summary_not_a_quote():
    base = generate_dataset(6, 1, seed=1)
    rv, re_ = base.people[0].id, base.people[1].id
    ds, _ = _ds([_review(rv, re_, "지연을 크게 줄였습니다. 일정도 맞췄습니다.", "문서가 늦었습니다.")])
    parsed = [ParsedReview(reviewer_id=rv, reviewee_id=re_, text_polarity=0.2,
                           evidence=["지연을 크게 줄었습니다.", "문서가 늦었습니다."])]
    evs = build_evidence_index(ds, parsed, reveal_text=True).for_person(re_)
    assert [(e.kind, e.text) for e in evs] == [("summary", "지연을 크게 줄었습니다."),
                                               ("quote", "문서가 늦었습니다.")]
    assert evs[0].source_id.endswith(":pos") and evs[1].source_id.endswith(":neg")


def test_review_rounds_get_distinct_source_ids_and_newest_comes_first():
    base = generate_dataset(6, 1, seed=1)
    a, b = base.people[0].id, base.people[1].id
    reviews = [_review(a, b, "처음 평가 좋은 점.", "처음 평가 아쉬운 점."),
               _review(a, b, "두번째 평가 좋은 점.", "두번째 평가 아쉬운 점.")]
    ds = Dataset(people=base.people, projects=base.projects, coworks=[], reviews=reviews)
    idx = build_evidence_index(ds, parse_reviews_rule_based(reviews), reveal_text=True)
    evs = idx.for_person(b)
    assert [e.source_id for e in evs] == [f"rv:{a}>{b}#2:pos", f"rv:{a}>{b}#2:neg",
                                          f"rv:{a}>{b}#1:pos", f"rv:{a}>{b}#1:neg"]
    assert all(e.kind == "quote" for e in evs)
    assert idx.source(f"rv:{a}>{b}#1:pos").round_no == 1


def test_limit_keeps_the_newest_evidence():
    base = generate_dataset(6, 1, seed=1)
    a, b = base.people[0].id, base.people[1].id
    reviews = [_review(a, b, f"{n}회 좋은 점.", f"{n}회 아쉬운 점.") for n in range(1, 5)]
    ds = Dataset(people=base.people, projects=base.projects, coworks=[], reviews=reviews)
    evs = build_evidence_index(ds, parse_reviews_rule_based(reviews), reveal_text=True).for_person(b, limit=3)
    assert [e.text for e in evs] == ["4회 좋은 점.", "4회 아쉬운 점.", "3회 좋은 점."]


def test_misaligned_parsed_list_uses_first_sentences_of_the_source_text():
    base = generate_dataset(6, 1, seed=1)
    a, b = base.people[0].id, base.people[1].id
    reviews = [_review(a, b, "좋았습니다. 더 좋았습니다.", "아쉬웠습니다.")]
    ds = Dataset(people=base.people, projects=base.projects, coworks=[], reviews=reviews)
    evs = build_evidence_index(ds, [], reveal_text=True).for_person(b)
    assert [(e.kind, e.text, e.polarity) for e in evs] == [("quote", "좋았습니다.", None),
                                                          ("quote", "아쉬웠습니다.", None)]


def test_hidden_mode_keeps_no_review_text_at_all(fixture_data):
    ds, parsed = fixture_data
    idx = build_evidence_index(ds, parsed, reveal_text=False)
    texts = {r.positive.text for r in ds.reviews} | {r.negative.text for r in ds.reviews}
    for p in ds.people[:30]:
        for ev in idx.for_person(p.id, limit=None):
            assert ev.kind == "label"
            assert ev.text.startswith(("좋은 점: ", "아쉬운 점: "))
            assert all(t not in ev.text for t in texts)
            assert idx.source(ev.source_id).text == ""
            assert not idx.verify_quote(ev.source_id, ev.text)


def test_label_text_lists_the_review_items():
    base = generate_dataset(6, 1, seed=1)
    a, b = base.people[0].id, base.people[1].id
    reviews = [_review(a, b, "비밀 문장.", "비밀 단점.", pos=("적극성", "소통"), neg=("문서화",))]
    ds = Dataset(people=base.people, projects=base.projects, coworks=[], reviews=reviews)
    evs = build_evidence_index(ds, parse_reviews_rule_based(reviews), reveal_text=False).for_person(b)
    assert [e.text for e in evs] == ["좋은 점: 적극성·소통", "아쉬운 점: 문서화"]


@pytest.mark.parametrize("quote", ["", "  ", "."])
def test_trivial_quotes_never_verify(fixture_data, quote):
    ds, parsed = fixture_data
    idx = build_evidence_index(ds, parsed, reveal_text=True)
    sid = idx.for_person(ds.reviews[0].reviewee_id)[0].source_id
    assert not idx.verify_quote(sid, quote)


def test_unknown_source_never_verifies(fixture_data):
    ds, parsed = fixture_data
    idx = build_evidence_index(ds, parsed, reveal_text=True)
    assert idx.source("rv:nobody>p000#1:pos") is None
    assert not idx.verify_quote("rv:nobody>p000#1:pos", "아무 문장")


def test_whitespace_and_nfc_differences_still_verify():
    base = generate_dataset(6, 1, seed=1)
    a, b = base.people[0].id, base.people[1].id
    reviews = [_review(a, b, "회의를  잘\n이끌었습니다.", "없음.")]
    ds = Dataset(people=base.people, projects=base.projects, coworks=[], reviews=reviews)
    idx = build_evidence_index(ds, parse_reviews_rule_based(reviews), reveal_text=True)
    assert idx.verify_quote(f"rv:{a}>{b}#1:pos", "회의를 잘 이끌었습니다.")
    assert not idx.verify_quote(f"rv:{a}>{b}#1:pos", "회의를 잘 이끌었다.")
