# Next action

**Re-run `python -m tools.soak_analyzer` around 2026-08-31 16:11 UTC** (24h
past the first restart boundary) to confirm the `capture_writer_health`/
`exit_engine_faults` fault-log FAILs have aged out with zero new occurrences.
If clean, close `docs/open-decisions.md`'s `two_consumer_mode` permanence
item by updating the file comment in `config/settings.yaml`. (Not yet due —
current time is well before 16:11 UTC; nothing else blocking right now.)

**Leave alone — active peer-session work, not ready for anything:**
- `.claude/worktrees/candlestick-volatility` (`feat/candlestick-volatility`,
  13 commits ahead of `main`, no PR yet).
- `autotrade-79` is fixing the `data/fault_log.db` storage-growth warning
  (below): `services/fault_log.py` has no retention/prune, unlike every
  sibling capture-store module — confirmed root cause. Claimed
  `services/fault_log.py` and `main.py`'s `_maybe_prune_capture_stores`;
  working in a new worktree, will open its own PR. Don't touch either file.
- `autotrade-29` is progressing docs-only planning PRs to just-prior-to-
  implementation (David's task) and is closing PR #310 as superseded by
  #312 (see below) — no action needed from you on that.

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
- PR #308 merged (by another session — not this one): `POST /api/backup/run`'s manual trigger had zero
  concurrency guard against the periodic scheduler
  (`_maybe_run_backup`/`_maybe_run_large_backup`), unlike the scheduler's own
  `state["backup"/"backup_large"]["running"]` self-guard. Found live: a
  peer session's manual large-tier trigger (part of PR #302's Task 5
  verification, above) landed during a `uvicorn --reload` cold-start window
  and raced the scheduler's own cold-start reseed, producing two
  independent, fully redundant ~27GB `series_watcher.db`/`candidate_log.db`/
  `market_history.db` snapshots 26 seconds apart — 54GB on disk for one
  logical backup. Fixed with `backup.run_backup_now()`, an atomic
  check-and-set (no `await` between the `running` check and the set) now
  shared by the manual route for all three tiers, raising
  `BackupAlreadyRunningError` -> HTTP 409 on collision. Full review cycle
  run (self-review, independent adversarial-review Agent call against the
  diff, a second independent adversarial pass against the pushed PR itself)
  — the first adversarial pass caught a real gap (the `tier=all` path could
  silently drop a completed regular-tier result behind a bare 409 if only
  the large tier collided), fixed and re-verified before merge. CI green,
  merged, branch deleted. The duplicate 27GB snapshot
  (`data/backups_large/20260831T055419Z`) was deleted from disk separately
  (with explicit confirmation, since the classifier flags `rm -rf` as
  destructive) — the surviving snapshot (`20260831T055445Z`, `backup_runs`
  id 72) is intact and is what `/api/backup/status` already reported as
  `last_run`.
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
- **PR #311 merged**: added a pre-merge peer ping to
  `.claude/rules/branching-and-ci.md`/`CLAUDE.md` — a live peer per
  `ListAgents` gets a one-line "about to merge PR #N" `SendMessage` ping
  before `gh pr merge` (courtesy, not a blocking gate, not a substitute for
  the adversarial-review requirement). Adversarial review caught a real
  conflation risk (the ping/ack could be mistaken for the required
  independent review) and an undefined "reasonable wait" — both fixed
  before merge. Already used for its own merge and for PR #312 below.
- **PR #312 merged**: filed the claudesuperpower.com toolkit assessment
  (previously an untracked scratch file of uncertain provenance in the
  shared primary checkout, flagged last entry) into
  `docs/superpowers/research/2026-08-31-claudesuperpower-toolkit-assessment.md`
  verbatim, as a research input for a future planning sequence, plus a
  `docs/open-decisions.md` tracking line. You confirmed the file's content
  first. Turned out neither this session, `autotrade-79`, nor `autotrade-29`
  authored the original — `autotrade-29` is closing PR #310 (the file's
  other home) as superseded by this filed copy. The untracked scratch file
  itself was deleted after filing (content now permanent in git history).

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
  (221184 -> 761856 bytes over 23.8h, >=2.0x) — root cause confirmed and
  being fixed by `autotrade-79` (see "Leave alone" above); don't duplicate.
- **Needs your go-ahead, not a session's:** `docs/superpowers/research/2026-08-31-claudesuperpower-toolkit-assessment.md`'s
  FINAL VERDICT recommends piloting 4 official `claude-plugins-official`
  plugins in priority order (`pr-review-toolkit`, `claude-security`,
  `claude-md-management`, `codspeed`) — nothing installed yet. Run a
  `superpowers:brainstorming`/`writing-plans` sequence off that doc to
  decide which (if any) to pilot, or close the `docs/open-decisions.md`
  line explicitly.
- `/api/quality/summary`'s `series_funnel` checks show KXBTC15M/KXMLBGAME/
  KXATPMATCH all underwater at the price level after fees — this is the
  already-documented, already-open pricing/edge gap at entry (see CLAUDE.md's
  "Standing goal" section), not a new finding; no new action implied here.

Layer contract behind the tool: `docs/data-layer-analysis-layer-contract.md`.
Full audit history if picking this up cold:
`docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md`.
