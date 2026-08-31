# Next action

**Re-run `python -m tools.soak_analyzer` around 2026-08-31 16:11 UTC** (24h
past the first restart boundary) to confirm the `capture_writer_health`/
`exit_engine_faults` fault-log FAILs have aged out with zero new occurrences.
If clean, close `docs/open-decisions.md`'s `two_consumer_mode` permanence
item by updating the file comment in `config/settings.yaml`. (Not yet due —
current time is well before 16:11 UTC; nothing else blocking right now.)

**Coordination procedure agreed with `autotrade-79`** on a shared commit/PR/
merge coordination discussion (triggered by two near-misses this session: a
redundant consolidation comment on PR #303, and PR #310 turning out to be a
*third*, unidentified session/worktree's work — not autotrade-79's, as first
assumed). Outcome: (1) `ListAgents`+`gh pr list` before opening/merging a PR
is already the documented convention (`.claude/rules/branching-and-ci.md`,
since 2026-08-30) — the miss was practice, not policy, no rule-text change
needed. (2) A one-line "about to merge #N" ping before `gh pr merge` when
ListAgents shows a live peer is worth formalizing — this session is drafting
it as a small addition to branching-and-ci.md's PR-merge section, through
the full self-review/adversarial-review/consolidation cycle (it's a
process/rule change, in scope regardless of size).

**Leave alone — active peer-session work, not ready for anything:**
- `.claude/worktrees/candlestick-volatility` (`feat/candlestick-volatility`,
  13 commits ahead of `main`, no PR yet).
- PR #310 (`docs/claudesuperpower-toolkit-assessment`, an exhaustive
  claudesuperpower.com toolkit scan) — author is a third session/worktree
  neither this session nor `autotrade-79` has identified; needs your read on
  its FINAL VERDICT (4 candidate `claude-plugins-official` plugins) before
  anyone merges it.
- **Needs your confirmation, not a session's:** an untracked scratch file
  `docs/claudesuperpower-toolkit-assessment-2026-08-31.md` sits in the
  shared primary checkout (not any worktree) — a near-identical leftover
  (differs by 2 trailing blank lines, mtime 2026-08-31 01:52 local/06:52 UTC,
  predating this session's activity) of what's committed on PR #310's
  branch. Neither this session nor `autotrade-79` created it; provenance
  unconfirmed, so nobody's touched it — don't let it get swept into an
  unrelated `git add`.

## Recently resolved (2026-08-31, this session)

- **PR #303 merged** (watermark boundary gap, stale docstring, misleading
  metric comment — PR #298 follow-up). Full self-review → independent
  adversarial-review Agent call → consolidation cycle run first; the
  adversarial pass caught a real defect (the PR's own new comment in
  `services/mutual_exclusivity.py` claimed a fallback runs "unconditionally
  on EVERY whale signal" — false, it's short-circuited by the `me_pairs`
  fast path; fixed in commit `e209c94` before merging, CI re-confirmed
  green). Remote branch deleted; the local worktree
  `.claude/worktrees/entry-gate-netting-remediation` was left untouched
  (a live peer session was using it) — still on the pre-merge commit,
  needs `scripts/cleanup-worktrees.sh` or a manual sync once that session
  is done with it.
- **PR #307 merged**: broadened CLAUDE.md's `dimensional-analysis` HARD RULE
  from money/probability math only to any arithmetic/unit conversion/numeric
  derivation; confirmed (not from memory) that `dimensional-analysis` is a
  separate `trailofbits` plugin, not part of `superpowers`; noted Wolfram MCP
  as an optional (not required) numeric-verification complement, since
  dimensional-analysis itself has no computation engine. Self-review caught
  that `.claude/hooks/guard_workflow.py`'s automated nudge is still scoped
  narrower than the new rule (money/probability files only) — said so
  explicitly in the rule text and tracked broadening the hook as a separate
  item in `docs/open-decisions.md` rather than silently leaving it out of
  sync.
- Two new permanent CLAUDE.md HARD RULEs merged: "nothing advances on one
  pass" (PR #304 — self-review/adversarial-review/consolidation gates every
  planning-stage handoff and PR merge) and the PR/commit task-list-grep
  requirement in `.claude/rules/branching-and-ci.md` (PR #305). Both went
  through their own review cycle, including an independent adversarial pass
  that caught real defects each time (see the memory files
  `nothing-advances-on-one-pass.md` and `read-pr-body-before-merging.md` for
  the verified evidence behind adopting this — 11/12 real catches across
  PR #299 and #300's review checkpoints, not adopted on faith).
- PR #299, #300, #301, #302 all merged. Primary checkout (`main` working
  directory, currently on `feat/realtime-data-plane-remediation`) was stale
  relative to `origin/main` after those merges — fixed by merging
  `origin/main` into it. **Lesson for next time:** that merge touches many
  tracked `.py` files under ddev's bind mount and triggers a live
  `uvicorn --reload` restart — don't fire a request at the running app in
  the same breath as a file-changing git operation on the primary checkout,
  or an in-flight request can die as a client-side 504 even though the
  backend completes fine (confirm via the relevant status endpoint, not the
  POST response, if this happens).
- PR #302's deferred Task 5 (live ddev verification of the two-tier backup
  split) completed clean once the primary was synced: regular-tier backup
  correctly excludes series_watcher.db/candidate_log.db/market_history.db;
  large-tier backup correctly includes exactly those three (confirmed via
  `/api/backup/status`, not just the POST response, because of the 504
  above). **Still open:** market_history.db's row-cap is only confirmed
  reachable, not confirmed effective yet — current size (1.16GB) matches
  the documented pre-fix baseline, which is expected since the fix caps
  growth going forward rather than shrinking existing rows. Re-check its
  size/row count after an hour or so of normal operation to confirm the
  cap is actually holding.
- Two provably-merged worktrees cleaned up via `scripts/cleanup-worktrees.sh`
  (`agent-a5110e2d3016b26a8`/PR #300, `web-skip-test-tighten`/PR #301).

## Also still open, unrelated

- Re-run `python -m tools.soak_analyzer` around 2026-08-31 16:11 UTC (24h
  past the first restart boundary) to confirm the `capture_writer_health`/
  `exit_engine_faults` fault-log FAILs have aged out with zero new
  occurrences. If clean, close `docs/open-decisions.md`'s `two_consumer_mode`
  permanence item by updating the file comment in `config/settings.yaml`.
- **Parked, needs your read:** `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design.md`
  (PR #263, merged docs-only) — temperature-market settlement-edge ingestion
  via Kalshi's `GET /live_data/weather/{city}`. Stops at the spec per the
  brainstorming skill's own gate until reviewed; opens a new market category.
- `data/fault_log.db` storage-growth warning in `/api/quality/summary`
  (221184 -> 761856 bytes over 23.8h, >=2.0x) — not yet triaged this session.
- `/api/quality/summary`'s `series_funnel` checks show KXBTC15M/KXMLBGAME/
  KXATPMATCH all underwater at the price level after fees — this is the
  already-documented, already-open pricing/edge gap at entry (see CLAUDE.md's
  "Standing goal" section), not a new finding; no new action implied here.

Layer contract behind the tool: `docs/data-layer-analysis-layer-contract.md`.
Full audit history if picking this up cold:
`docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md`.
