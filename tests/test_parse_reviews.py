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


def test_llm_mode_text_polarity_is_independent_of_item_score():
    """LLM 모드에서는 자유서술 감성이 항목 선택의 결정론적 함수가 아니어야 한다.

    TEMPLATE 모드에서는 두 값이 완전히 동일해(정보량 0) 하이브리드 파이프라인의
    비정형 축이 무의미했다. LLM 재동결 후 이 테스트가 그 해소를 고정한다.
    """
    from core.graph.memory_graph import _item_score
    meta = json.loads((FIXTURES_DIR / "meta.json").read_text("utf-8"))
    assert meta["review_mode"] == "llm", "LLM 모드 재동결이 선행되어야 함"
    ds, parsed = load_fixtures(FIXTURES_DIR)
    pol = {(p.reviewer_id, p.reviewee_id): p.text_polarity for p in parsed}
    diffs = [abs(pol[(r.reviewer_id, r.reviewee_id)] - _item_score(r)) for r in ds.reviews]
    assert max(diffs) > 0.05, "text_polarity 가 item_score 와 사실상 동일 — 비정형 축이 무의미"


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
