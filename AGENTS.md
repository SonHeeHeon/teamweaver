# TeamWeaver — agent entry point

This repository is developed by two agents in parallel: **Codex and Claude (Claude Code)**.
Before starting any task:

1. Read `docs/handoff-log.md` (newest entries first). It records what each agent did, which branches
   were merged into `main`, and what changed that affects the other agent.
2. Read `docs/work-split.md`. It defines file ownership, task lanes (C* = Codex, K* = Claude), the
   "in progress" list, and the request log. Only edit files in your own lane. Shared-contract files
   require an entry in the request log first.
3. Read `docs/project-context.md` for condensed project context (model formula, experiment results,
   input contract, roadmap). Read `outputs/eli5-project-history-roadmap.html` only if that is not enough.
4. Check `CLAUDE.md`, section "코드만 봐서는 모르는 함정" (gotchas). It applies to every agent.

Before you start and before you merge, bring `main` into your branch (merge or rebase).
When you finish a task, add an entry at the top of `docs/handoff-log.md`. Merges into `main`
require the user's approval.

Business validity stays `NOT_CALIBRATED`: all data is synthetic. Never present a computational
result as evidence of real staffing outcomes.

Baseline check: `uv run --group benchmark pytest -q` → 521 passed, 10 deselected (2026-10-04).
