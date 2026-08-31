# Next action

**Review PR #299 and PR #300** — two full research→review→design→review→plan
pipelines (subagent-delegated, each stage independently re-verified before
the next), both docs-only, both "ready for execution," neither pushed toward
implementation yet:

- **#299** — Kalshi category data-shape audit: what category-specific
  Kalshi data this app is missing across all 11 traded categories.
  Investigation went through 2 review/revision rounds (a milestone-sampling
  bug — 1 page of 275 — inflated a false "Sports-only" conclusion into
  durable guidance until the full 137,200-milestone census corrected it);
  design went through 3 rounds, including the plan-writing stage itself
  catching a real completeness regression in the design's own extractor
  pseudocode (would have silently broken `basketball_game`/
  `soccer_tournament_multi_leg`, the 2nd/3rd-largest milestone types).
  14-task implementation plan at the end.
- **#300** — whale-confidence-weights scoring remediation: most of tonight's
  earlier live discrimination numbers turned out to be measurement
  artifacts, not real findings (already corrected in `config/settings.yaml`'s
  comment on this branch). Design review caught a safety-critical gap in the
  `measurement_valid` auto-apply gate (missed one of two real write paths;
  couldn't distinguish real contamination from permanent data-sparsity) —
  fixed and re-verified. Encodes the standing directive to score accuracy
  and edge as two independent scores, never blended. 16-task implementation
  plan at the end.

Both branches (`worktree-agent-a72f71384c869f628`, `worktree-agent-a5110e2d3016b26a8`)
still exist as `.claude/worktrees/` checkouts if execution is approved —
resume there rather than starting fresh worktrees.

**Also still open, unrelated to the above:**

- Re-run `python -m tools.soak_analyzer` around 2026-08-31 16:11 UTC (24h
  past the first restart boundary) to confirm the `capture_writer_health`/
  `exit_engine_faults` fault-log FAILs have aged out with zero new
  occurrences. If clean, close `docs/open-decisions.md`'s `two_consumer_mode`
  permanence item by updating the file comment in `config/settings.yaml`.
- **Parked, needs your read:** `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design.md`
  (PR #263, merged docs-only) — temperature-market settlement-edge ingestion
  via Kalshi's `GET /live_data/weather/{city}`. Stops at the spec per the
  brainstorming skill's own gate until reviewed; opens a new market category.
- **R6 retired** (`fix/realtime-data-plane-remediation`, merged to `main` via
  #296): the peer-session git-merge guard is gone outright — no installed
  replacement, convention-only now. `tools/kanban_sync`'s `find_by_marker`
  bug and AQC's branch-cleanup action are also fixed/retired the same PR.
  `CLAUDE.md`'s Toolchain section carries the new "installed over handspun"
  standard this all came from.
- PR #298 (entry-gate ME-pairing fix, another session) narrows but does not
  moot PR #202's event-scoped ME gate plan — #202 retains live scope
  (N-way blocking decision, `event_ticker` as a first-class field, the
  limit-order path fix, blocked-flip P&L measurement). Both open, see
  `docs/open-decisions.md`.

Layer contract behind the tool: `docs/data-layer-analysis-layer-contract.md`.
Full audit history if picking this up cold:
`docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md`.
