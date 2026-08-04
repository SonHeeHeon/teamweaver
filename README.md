# TeamWeaver

AI-powered staffing optimization platform for intelligent team composition and resource allocation.

## Quick Start

- **Setup environment**: `uv sync`
- **Run tests**: `uv run pytest -v`
- **Run E2E smoke** (slow, ~25s, needs the frozen fixture below): `uv run pytest -m slow -v`
- **Start Neo4j backend** (optional): `docker compose up -d`

## Demo fixture (`fixtures/*.json`)

`fixtures/{people,projects,coworks,reviews_ko,parsed_reviews,meta}.json` are the
frozen demo dataset (100 people / 20 projects / seed 42) that all experiments,
the E2E smoke test (`tests/test_e2e_smoke.py`), and demos consume as the single
source of truth. Generate/refresh them with `scripts/generate_fixtures.py`:

```bash
uv run python scripts/generate_fixtures.py --people 100 --projects 20 --seed 42 --review-mode template
```

**⚠️ The fixture currently committed to this repo was generated with
`--review-mode template`, not `--review-mode llm`.** No `OPENAI_API_KEY` /
`.env` was available at freeze time (Task 14), so:

- `reviews_ko.json` contains deterministic **template-generated** Korean text
  (`"{items} 측면이 뛰어나/아쉬워 ..."`), not natural LLM-written peer reviews.
- `parsed_reviews.json` was produced by the **rule-based** parser
  (`parse_reviews_rule_based`), not the LLM structured-output parser.
- The `--review-mode llm` code path in `scripts/generate_fixtures.py`
  (`rewrite_reviews_with_llm` + `parse_reviews_llm`, wired through
  `core.config.load_env`/`load_pricing`) is implemented and reviewed against
  its interfaces, but has **not been executed** — it is untested by actual
  execution.

### Known limitation: in template mode, `text_polarity` duplicates `item_score`

**The free-text sentiment in this committed fixture carries no information
beyond the structured checkbox items — this is expected, not a bug, and it
cannot be fixed by a better text parser.** `_template_text`
(`core/datagen/generator.py`) *builds* each review's narrative text from the
selected positive/negative items with a fixed closing clause per polarity
("...측면이 뛰어나 함께 일하기 좋았습니다." / "...아쉬워 협업에 어려움이 있었습니다.").
The text's sentiment is therefore a deterministic function of the item
counts by construction, so `parse_reviews_rule_based` derives `text_polarity`
directly from those counts
(`(n_pos - n_neg) / (n_pos + n_neg)`, identical to `_item_score` in
`core/graph/memory_graph.py`) rather than pretending a lexicon over the text
adds independent signal. (An earlier attempt replaced this with a small
Korean sentiment-cue lexicon scored over the text; because the cue words
don't vary with item count on this template-generated corpus, it came out
**constant** for every review, which *halved* `pair_review_score`'s standard
deviation — 0.325 → 0.163, measured on this fixture — making the synergy
score's dynamic range worse than the honest duplicate. Reverted; see
`.omc/reports/2026-08-04-final-review-fixes.md`.)

**Practical consequence**: in the committed fixture, the β term of the
synergy matrix `C` (`core/scoring/engine.py::synergy_matrix`,
`0.5*item_score + 0.5*text_polarity` inside `MemoryGraph.build`) is
**effectively item-only** — the "Hybrid Data Pipeline" architecture element's
free-text arm contributes **no independent signal** here. **Experiment 2's
premise (that LLM-parsed free text adds information structured checkboxes
don't) is unverified on this fixture and requires regenerating with
`--review-mode llm`** — real, independently-written review text is not a
deterministic function of the checkboxes — **before any claim about
LLM-parsed text adding information is published.**
`tests/test_parse_reviews.py::test_template_mode_text_polarity_duplicates_item_score`
pins the current (template-mode) duplication as a documented property and
will fail loudly once an LLM-mode fixture lands, forcing this disclosure to
be updated.

**Once an `OPENAI_API_KEY` is available**, regenerate the fixture for real
before relying on it for anything reviewer-facing:

```bash
echo "OPENAI_API_KEY=sk-..." > .env
uv run python scripts/generate_fixtures.py --people 100 --projects 20 --seed 42 --review-mode llm
uv run pytest -m slow -v   # re-verify headline numbers after LLM regeneration
```

**No datagen tuning was applied to hit any headline number.** `core/datagen/generator.py`
is unchanged from Task 3 — people/project skill difficulty is realistic (not
eased to make the optimizer's job trivial). See
`.omc/reports/2026-08-03-fixture-freeze-e2e.md` for why: the spec's headline
metric is **최적화율 (optimization ratio)** — `Σ S_ij·a_ij` of the solved plan
divided by the LP-relaxation upper bound of that same skill objective
(`core/optimize/metrics.py::optimization_ratio`) — not raw matching
fulfillment. On this fixture Plan A reaches optimization_ratio ≈ 0.93 with
zero unfilled required slots, while matching fulfillment (a stricter,
capacity-consuming secondary metric — the same person's allocation is split
across every skill slot they qualify for) reads ≈ 0.54 by construction, not
because staffing is short. Exact numbers, formulas, and the reasoning are in
the report above. `tests/test_e2e_smoke.py` also solves Greedy on the same
frozen fixture and asserts MILP's optimization_ratio beats it
(MILP ≈ 0.93 vs Greedy ≈ 0.71) as a regression guard on the metric itself.
