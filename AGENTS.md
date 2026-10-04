# TeamWeaver — agent entry point

This repository is developed by two agents (Codex and Claude) in parallel.
Before starting any task:

1. Read `docs/work-split.md` — file ownership, task lanes, and the request log.
   Only edit files in your own lane; shared-contract files require an entry in the request log first.
2. Read `docs/project-context.md` — condensed project context (model formula, experiment results,
   input contract, roadmap). Read `outputs/eli5-project-history-roadmap.html` only if that is not enough.
3. Check `CLAUDE.md`, section "코드만 봐서는 모르는 함정" (gotchas). It applies to every agent.

Business validity stays `NOT_CALIBRATED`: all data is synthetic. Never present a computational
result as evidence of real staffing outcomes.

Baseline check: `uv run --group benchmark pytest -q` → 497 passed, 10 deselected (2026-10-03).
