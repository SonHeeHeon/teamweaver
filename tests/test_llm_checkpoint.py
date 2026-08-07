import json
from core.datagen.generator import generate_dataset
from core.datagen.llm_checkpoint import (apply_checkpoint, generate_and_parse_checkpointed,
                                         load_checkpoint, usage_summary)


class _FakeCompletions:
    """Model-aware fake: returns generation-shaped JSON for the gen model,
    parse-shaped JSON for the parse model."""
    def __init__(self):
        self.n_calls = 0

    def create(self, **kw):
        self.n_calls += 1
        if kw["model"] == "gen-model":
            req = json.loads(kw["messages"][-1]["content"])
            out = {"positive_text": f"{req['positive_items'][0]}이(가) 좋았습니다.",
                   "negative_text": f"{req['negative_items'][0]}은 아쉬웠습니다."}
        else:
            out = {"text_polarity": 0.3, "evidence": ["근거 문장."]}

        class Usage:
            prompt_tokens = 10
            completion_tokens = 5

        class Msg:
            content = json.dumps(out, ensure_ascii=False)

        class Choice:
            message = Msg()

        class Resp:
            choices = [Choice()]
            usage = Usage()

        return Resp()


class FakeClient:
    def __init__(self):
        self.chat = type("chat", (), {"completions": _FakeCompletions()})()


def test_checkpointed_run_processes_only_pending_reviews(tmp_path):
    ds = generate_dataset(10, 3, seed=5)
    ckpt_path = tmp_path / "checkpoint.json"
    client = FakeClient()

    # first invocation: cap at 2 reviews
    checkpoint, done, n = generate_and_parse_checkpointed(
        ds, client, "gen-model", "parse-model", seed=1,
        checkpoint_path=ckpt_path, batch_size=2, log_every=25)
    assert n == 2
    assert not done
    assert len(checkpoint) == 2
    assert client.chat.completions.n_calls == 4  # 2 reviews * (gen + parse)

    # second invocation with a fresh dataset object (as happens across process
    # restarts) must skip the already-checkpointed reviews and only pay for
    # the rest
    ds2 = generate_dataset(10, 3, seed=5)
    client2 = FakeClient()
    checkpoint2, done2, n2 = generate_and_parse_checkpointed(
        ds2, client2, "gen-model", "parse-model", seed=1,
        checkpoint_path=ckpt_path, batch_size=None, log_every=25)
    assert done2
    assert len(checkpoint2) == len(ds2.reviews)
    assert n2 == len(ds2.reviews) - 2
    assert client2.chat.completions.n_calls == 2 * (len(ds2.reviews) - 2)


def test_checkpoint_survives_reload_from_disk(tmp_path):
    ds = generate_dataset(6, 2, seed=7)
    ckpt_path = tmp_path / "checkpoint.json"
    client = FakeClient()
    generate_and_parse_checkpointed(ds, client, "gen-model", "parse-model", seed=1,
                                    checkpoint_path=ckpt_path, batch_size=3, log_every=25)
    reloaded = load_checkpoint(ckpt_path)
    assert len(reloaded) == 3
    assert ckpt_path.exists()


def test_apply_checkpoint_writes_text_and_builds_parsed_reviews():
    ds = generate_dataset(6, 2, seed=7)
    checkpoint = {}
    for r in ds.reviews:
        checkpoint[f"{r.reviewer_id}:{r.reviewee_id}"] = {
            "positive_text": "pos", "negative_text": "neg",
            "text_polarity": 0.4, "evidence": ["ev1", "ev2", "ev3"]}
    parsed = apply_checkpoint(ds, checkpoint)
    assert len(parsed) == len(ds.reviews)
    for r, p in zip(ds.reviews, parsed):
        assert r.positive.text == "pos" and r.negative.text == "neg"
        assert p.text_polarity == 0.4
        assert p.reviewer_id == r.reviewer_id and p.reviewee_id == r.reviewee_id


def test_usage_summary_computes_cost_from_pricing():
    checkpoint = {
        "a:b": {"gen_usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
                "parse_usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}},
    }
    pricing = {"models": {"gen-model": {"input_per_1m": 1.0, "output_per_1m": 2.0},
                          "parse-model": {"input_per_1m": 0.5, "output_per_1m": 1.0}}}
    u = usage_summary(checkpoint, pricing, "gen-model", "parse-model")
    assert u["gen-model"]["cost_usd"] == 3.0  # 1*1.0 + 1*2.0
    assert u["parse-model"]["cost_usd"] == 1.5  # 1*0.5 + 1*1.0
    assert u["total_cost_usd"] == 4.5
