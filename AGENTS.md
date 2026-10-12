# TeamWeaver — agent entry point

This repository is developed by three agents in parallel: **Codex and two Claude Code accounts (`claude-a`, `claude-b`)**.
Before starting any task:

> **Project scope (user notice, 2026-10-12):** this is a proof of concept built for an assignment submission.
> Real HR data cannot be loaded and will not be loaded. All development, validation, demos and reports use synthetic data.
> Real-data features (input contract, hidden-text mode, in-house judge host) stay as design only. Do not start work that
> assumes real data (first real load, calibration on real outcomes, shadow mode or pilot and its operations setup);
> describe such items only as "what a real deployment would need".


1. Read `docs/handoff-log.md` (newest entries first). It records what each agent did, which branches
   were merged into `main`, and what changed that affects the other agent.
2. Read `docs/work-split.md`. It defines file ownership, task lanes (C* = Codex, K* = Claude), the
   "in progress" list, and the request log. Only edit files in your own lane. Shared-contract files
   require an entry in the request log first.
3. Read `docs/project-context.md` for condensed project context (model formula, experiment results,
   input contract, roadmap). Read `outputs/eli5-project-history-roadmap.html` only if that is not enough.
4. Check `CLAUDE.md`, section "코드만 봐서는 모르는 함정" (gotchas). It applies to every agent.
5. If `/Users/honey/Dev/teamweaver/private/schema-intake.json` exists, it is the user's description of
   the real HR skill-history and peer-review schema. Read `docs/data-schema/README.md` first.
   It shaped the input contract only -- no real data will ever be loaded (see the scope notice above).
   Never commit, push, or publish anything under `private/`.

Before you start and before you merge, bring `main` into your branch (merge or rebase).
Work only in your own folder: Codex in `.worktrees/`, claude-b in `.worktrees/claude-b-<slug>`, claude-a in the repository root.
Never edit another agent's branch or worktree. Agent IDs and lanes are in `docs/work-split.md`.
When you finish a task, add an entry at the top of `docs/handoff-log.md`. Merges into `main`
require the user's approval.

Business validity stays `NOT_CALIBRATED`: all data is synthetic. Never present a computational
result as evidence of real staffing outcomes.

Baseline check: `uv run --group benchmark pytest -q` -- the current numbers are in `CLAUDE.md`, section "명령과 검증 기준".
