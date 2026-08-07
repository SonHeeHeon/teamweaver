"""Resumable, per-review checkpointed LLM generate+parse pass.

Long sequential runs (~266 reviews x 2 calls) can be killed mid-flight by the
execution environment. The plain `rewrite_reviews_with_llm` / `parse_reviews_llm`
functions in `llm_reviews.py` / `parse_reviews.py` buffer all results in memory
and only become visible once their whole pass finishes, so a kill loses
everything. This module does the same two API calls per review (reusing the
exact same prompts, imported from those modules so there is one source of
truth) but persists each review's result to a JSON checkpoint file immediately
after it completes, and can be invoked repeatedly -- each call only processes
reviews missing from the checkpoint (optionally capped at `batch_size`), so a
kill loses at most one in-flight review, and callers never pay twice for a
review that already succeeded.
"""
import json
import time
from pathlib import Path

from core.datagen.llm_reviews import _SYSTEM as _GEN_SYSTEM
from core.datagen.parse_reviews import _SYSTEM as _PARSE_SYSTEM
from core.domain.models import Dataset, ParsedReview


def _key(reviewer_id: str, reviewee_id: str) -> str:
    return f"{reviewer_id}:{reviewee_id}"


def _usage(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens}


def _call_with_retry(client, retries: int = 3, backoff: float = 2.0, **kwargs):
    """Retry transport-level failures only; JSON/validation errors are real
    bugs and must propagate immediately, not be retried."""
    for attempt in range(retries):
        try:
            return client.chat.completions.create(**kwargs)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            raise
        except Exception:  # noqa: BLE001 -- transport/rate-limit errors from the SDK
            if attempt == retries - 1:
                raise
            time.sleep(backoff * (attempt + 1))


def load_checkpoint(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    return {}


def generate_and_parse_checkpointed(
    ds: Dataset, client, gen_model: str, parse_model: str, seed: int,
    checkpoint_path: Path, batch_size: int | None = None, log_every: int = 25,
) -> tuple[dict, bool, int]:
    """Process reviews missing from the checkpoint (up to `batch_size` of them),
    persisting each result immediately. Returns (checkpoint, done, n_processed)."""
    checkpoint = load_checkpoint(checkpoint_path)
    pending = [(idx, r) for idx, r in enumerate(ds.reviews) if _key(r.reviewer_id, r.reviewee_id) not in checkpoint]
    todo = pending if batch_size is None else pending[:batch_size]

    t0 = time.time()
    for n, (idx, r) in enumerate(todo, 1):
        payload = {"reviewer": r.reviewer_id, "reviewee": r.reviewee_id,
                   "positive_items": r.positive.items, "negative_items": r.negative.items,
                   "variation": (seed + idx) % 97}
        gen_resp = _call_with_retry(
            client, model=gen_model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _GEN_SYSTEM},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
        try:
            gen_out = json.loads(gen_resp.choices[0].message.content)
            pos_text, neg_text = gen_out["positive_text"], gen_out["negative_text"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(
                f"리뷰 재작성 실패 (index={idx}, reviewer={r.reviewer_id}, reviewee={r.reviewee_id}): {exc}"
            ) from exc
        gen_usage = _usage(gen_resp)

        parse_resp = _call_with_retry(
            client, model=parse_model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _PARSE_SYSTEM},
                      {"role": "user", "content": json.dumps(
                          {"좋은점": pos_text, "나쁜점": neg_text}, ensure_ascii=False)}])
        try:
            parse_out = json.loads(parse_resp.choices[0].message.content)
            ev = parse_out.get("evidence", [])
            if not isinstance(ev, list):
                raise ValueError("evidence must be a list")
            polarity = max(-1.0, min(1.0, float(parse_out["text_polarity"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"리뷰 파싱 실패 (reviewer={r.reviewer_id}, reviewee={r.reviewee_id}): {exc}"
            ) from exc
        parse_usage = _usage(parse_resp)

        checkpoint[_key(r.reviewer_id, r.reviewee_id)] = {
            "positive_text": pos_text, "negative_text": neg_text,
            "text_polarity": polarity, "evidence": list(ev)[:2],
            "gen_usage": gen_usage, "parse_usage": parse_usage,
        }
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False), "utf-8")

        if n % log_every == 0 or n == len(todo):
            elapsed = time.time() - t0
            print(f"[llm-checkpoint] this run: {n}/{len(todo)}  "
                  f"total: {len(checkpoint)}/{len(ds.reviews)}  elapsed={elapsed:.1f}s")

    done = len(checkpoint) == len(ds.reviews)
    return checkpoint, done, len(todo)


def apply_checkpoint(ds: Dataset, checkpoint: dict) -> list[ParsedReview]:
    """Write checkpointed text back onto ds.reviews (fresh objects each run
    since generate_dataset() is called anew every invocation) and build the
    parsed-review list. Raises KeyError if a review is missing -- callers
    should only call this once `done` is True."""
    parsed = []
    for r in ds.reviews:
        c = checkpoint[_key(r.reviewer_id, r.reviewee_id)]
        r.positive.text = c["positive_text"]
        r.negative.text = c["negative_text"]
        parsed.append(ParsedReview(reviewer_id=r.reviewer_id, reviewee_id=r.reviewee_id,
                                    text_polarity=c["text_polarity"], evidence=c["evidence"]))
    return parsed


def usage_summary(checkpoint: dict, pricing: dict, gen_model: str, parse_model: str) -> dict:
    totals = {gen_model: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
              parse_model: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}}
    for c in checkpoint.values():
        for model, field in ((gen_model, "gen_usage"), (parse_model, "parse_usage")):
            u = c[field]
            totals[model]["calls"] += 1
            totals[model]["prompt_tokens"] += u["prompt_tokens"]
            totals[model]["completion_tokens"] += u["completion_tokens"]
    total_cost = 0.0
    for model, u in totals.items():
        rates = pricing["models"].get(model, {})
        cost = (u["prompt_tokens"] / 1_000_000) * rates.get("input_per_1m", 0.0) + \
               (u["completion_tokens"] / 1_000_000) * rates.get("output_per_1m", 0.0)
        u["cost_usd"] = round(cost, 4)
        total_cost += cost
    totals["total_cost_usd"] = round(total_cost, 4)
    return totals
