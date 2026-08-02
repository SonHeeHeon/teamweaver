import json
from core.datagen.generator import generate_dataset
from core.datagen.llm_reviews import rewrite_reviews_with_llm

class _FakeCompletions:
    def create(self, **kw):
        req = json.loads(kw["messages"][-1]["content"])
        out = {"positive_text": f"{req['positive_items'][0]}이(가) 돋보였습니다. 일정 압박 속에서도 침착했습니다.",
               "negative_text": f"{req['negative_items'][0]} 부분은 보완이 필요합니다."}
        class Msg: content = json.dumps(out, ensure_ascii=False)
        class Choice: message = Msg()
        class Resp: choices = [Choice()]
        return Resp()

class FakeClient:
    class chat:
        completions = _FakeCompletions()

def test_rewrite_preserves_items_and_replaces_text():
    ds = generate_dataset(10, 3, seed=5)
    before = [r.model_copy(deep=True) for r in ds.reviews]
    out = rewrite_reviews_with_llm(ds, FakeClient(), model="test-model", seed=1)
    for old, new in zip(before, out.reviews):
        assert new.positive.items == old.positive.items
        assert new.negative.items == old.negative.items
        assert new.positive.text != old.positive.text
        assert new.positive.items[0] in new.positive.text
