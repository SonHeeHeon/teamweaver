import json
from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_llm, parse_reviews_rule_based


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


def test_rule_based_polarity_sign():
    ds = generate_dataset(10, 3, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    for r, p in zip(ds.reviews, parsed):
        expected = (len(r.positive.items) - len(r.negative.items)) / (
            len(r.positive.items) + len(r.negative.items))
        assert abs(p.text_polarity - expected) < 1e-9 and len(p.evidence) >= 1


def test_template_mode_text_polarity_duplicates_item_score():
    """Known, DOCUMENTED property of the committed (template-mode) fixture --
    not an accident and not something a better lexicon can fix.

    `_template_text` (core/datagen/generator.py) builds each review's free
    text FROM the selected checkbox items with a fixed closing clause per
    polarity ("...측면이 뛰어나 함께 일하기 좋았습니다." / "...아쉬워 협업에
    어려움이 있었습니다."). The text's sentiment is therefore a deterministic
    function of len(items) by construction -- no text parser, rule-based or
    otherwise, can extract signal from that text independent of the item
    counts, because the text contains none. (A prior fix attempt replaced the
    item-count formula with a small Korean sentiment-cue lexicon over the
    text; it produced a CONSTANT text_polarity across the whole corpus since
    the cue words don't vary with item count, which halved
    pair_review_score's standard deviation -- 0.325 -> 0.163 measured on this
    fixture -- making the synergy score's dynamic range worse, not better.
    Reverted; see README's "Hybrid Data Pipeline" disclosure and
    .omc/reports/2026-08-04-final-review-fixes.md.)

    parse_reviews_rule_based therefore derives text_polarity directly from
    item counts (matching _item_score in core/graph/memory_graph.py), and
    this test pins that as expected for the committed template-mode fixture.

    This WILL legitimately change once the fixture is regenerated with
    --review-mode llm (real, independently-written review text is not a
    deterministic function of the checkboxes). If this test starts failing
    after such a regeneration, that's expected -- update the README
    disclosure and this test (or remove it) together.
    """
    assert (FIXTURES_DIR / "people.json").exists(), "fixture 동결이 선행되어야 함"
    ds, parsed = load_fixtures(FIXTURES_DIR)
    diffs = []
    for r, p in zip(ds.reviews, parsed):
        item_score = (len(r.positive.items) - len(r.negative.items)) / (
            len(r.positive.items) + len(r.negative.items))
        diffs.append(abs(p.text_polarity - item_score))
    assert max(diffs) < 1e-9, (
        "template-mode text_polarity no longer duplicates item_score -- either "
        "the fixture was regenerated with real review text (update the README "
        "disclosure and this test) or parse_reviews_rule_based changed "
        "unexpectedly")


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
