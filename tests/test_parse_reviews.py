import json
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_llm, parse_reviews_rule_based, _text_polarity


class _FakeCompletions:
    def create(self, **kw):
        out = {"text_polarity": 0.4, "evidence": ["일정 압박 속에서도 침착했습니다."]}
        class Msg: content = json.dumps(out, ensure_ascii=False)
        class Choice: message = Msg()
        class Resp: choices = [Choice()]
        return Resp()


class FakeClient:
    class chat:
        completions = _FakeCompletions()


def test_llm_parse_shape():
    ds = generate_dataset(10, 3, seed=5)
    parsed = parse_reviews_llm(ds.reviews, FakeClient(), model="m")
    assert len(parsed) == len(ds.reviews)
    p = parsed[0]
    assert -1 <= p.text_polarity <= 1 and p.reviewer_id == ds.reviews[0].reviewer_id


def test_rule_based_polarity_bounds_and_evidence():
    """text_polarity must stay in [-1, 1] and evidence must be present -- it no
    longer needs to equal the item-count formula (see
    test_rule_based_polarity_not_degenerate_vs_item_score below: that used to
    be an exact identity, which was the bug)."""
    ds = generate_dataset(10, 3, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    for p in parsed:
        assert -1.0 <= p.text_polarity <= 1.0 and len(p.evidence) >= 1


def test_rule_based_polarity_matches_text_lexicon():
    """text_polarity is exactly `_text_polarity` applied to the review's own
    positive+negative text -- i.e. it is computed from the narrative, not from
    len(items)."""
    ds = generate_dataset(10, 3, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    for r, p in zip(ds.reviews, parsed):
        expected = _text_polarity(r.positive.text + " " + r.negative.text)
        assert abs(p.text_polarity - expected) < 1e-9


def test_rule_based_polarity_not_degenerate_vs_item_score():
    """Regression guard for the fixed bug: text_polarity must differ from the
    pure item-count formula `(n_pos - n_neg) / (n_pos + n_neg)` (`_item_score`
    in core/graph/memory_graph.py) for at least some reviews. Before the fix,
    parse_reviews_rule_based computed text_polarity with that exact formula,
    making `0.5*_item_score(r) + 0.5*text_polarity` (memory_graph.py) a 5:5
    blend of a value with itself -- i.e. the "hybrid" text-parsing arm
    contributed exactly zero information."""
    ds = generate_dataset(30, 6, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    diffs = []
    for r, p in zip(ds.reviews, parsed):
        item_score = (len(r.positive.items) - len(r.negative.items)) / (
            len(r.positive.items) + len(r.negative.items))
        diffs.append(abs(p.text_polarity - item_score))
    assert max(diffs) > 0, "text_polarity must not be a re-derivation of item counts"


def test_llm_parse_malformed_response():
    """Test that malformed LLM response raises ValueError with reviewer_id."""

    class MalformedCompletions:
        def create(self, **kw):
            # Return invalid JSON
            class Msg: content = "not valid json"
            class Choice: message = Msg()
            class Resp: choices = [Choice()]
            return Resp()

    class MalformedClient:
        class chat:
            completions = MalformedCompletions()

    ds = generate_dataset(5, 3, seed=5)
    try:
        parse_reviews_llm(ds.reviews, MalformedClient(), model="m")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        error_msg = str(e)
        # Check that the error message contains reviewer_id
        assert "reviewer=" in error_msg
        assert ds.reviews[0].reviewer_id in error_msg


def test_rule_based_evidence_covers_both_texts():
    """Test that rule-based evidence extracts from both positive and negative texts."""
    ds = generate_dataset(10, 3, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    r, p = ds.reviews[0], parsed[0]
    assert len(p.evidence) == 2
    assert p.evidence[0].startswith(r.positive.text.split(".")[0][:10])
    assert p.evidence[1].startswith(r.negative.text.split(".")[0][:10])


def test_llm_evidence_truncated_to_two_items():
    """Test that LLM evidence with 3+ items is truncated to 2."""

    class TruncateCompletions:
        def create(self, **kw):
            out = {
                "text_polarity": 0.5,
                "evidence": ["첫번째 문장입니다.", "두번째 문장입니다.", "세번째 문장입니다.", "네번째 문장입니다."]
            }
            class Msg: content = json.dumps(out, ensure_ascii=False)
            class Choice: message = Msg()
            class Resp: choices = [Choice()]
            return Resp()

    class TruncateClient:
        class chat:
            completions = TruncateCompletions()

    ds = generate_dataset(5, 3, seed=5)
    parsed = parse_reviews_llm(ds.reviews, TruncateClient(), model="m")
    for p in parsed:
        assert len(p.evidence) == 2


def test_llm_evidence_must_be_list():
    """Test that LLM response with non-list evidence raises ValueError."""

    class NonListCompletions:
        def create(self, **kw):
            out = {
                "text_polarity": 0.5,
                "evidence": "this is a string, not a list"
            }
            class Msg: content = json.dumps(out, ensure_ascii=False)
            class Choice: message = Msg()
            class Resp: choices = [Choice()]
            return Resp()

    class NonListClient:
        class chat:
            completions = NonListCompletions()

    ds = generate_dataset(5, 3, seed=5)
    try:
        parse_reviews_llm(ds.reviews, NonListClient(), model="m")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        error_msg = str(e)
        assert "리뷰 파싱 실패" in error_msg
        assert "reviewer=" in error_msg
