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
  (`parse_reviews_rule_based`), not the LLM structured-output parser. Its
  `text_polarity` comes from `core.datagen.parse_reviews._text_polarity`, a
  small deterministic **Korean sentiment-cue lexicon** (weighted substring
  matches over words like `뛰어나`/`좋았습니다` vs `아쉬워`/`어려움`/`부족`,
  normalized to `[-1, 1]`) applied to each review's own text — a stand-in for
  the LLM path, not a reproduction of it. It is intentionally simple, so on
  this template-generated corpus (whose closing clause is fixed per polarity)
  it comes out to the same value for every review; see
  `.omc/reports/2026-08-04-final-review-fixes.md` (FIX 3) for the measured
  numbers and why that's an honest limitation of a template-only fallback
  rather than the parser ignoring the text.
- The `--review-mode llm` code path in `scripts/generate_fixtures.py`
  (`rewrite_reviews_with_llm` + `parse_reviews_llm`, wired through
  `core.config.load_env`/`load_pricing`) is implemented and reviewed against
  its interfaces, but has **not been executed** — it is untested by actual
  execution. The real "Hybrid Data Pipeline" signal (LLM-parsed free text
  contributing information structured checkboxes don't) only materializes
  once this path is actually run against real natural-language reviews.

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
across every skill slot they qualify for) reads ≈ 0.55 by construction, not
because staffing is short. Exact numbers, formulas, and the reasoning are in
the report above. `tests/test_e2e_smoke.py` also solves Greedy on the same
frozen fixture and asserts MILP's optimization_ratio beats it
(MILP ≈ 0.93 vs Greedy ≈ 0.71) as a regression guard on the metric itself.
