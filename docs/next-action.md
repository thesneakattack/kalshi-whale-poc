# Next action

**Start the brainstorming/design cycle from tonight's comprehensive
architecture audit** (`docs/superpowers/research/2026-09-02-architecture-
audit-and-rewrite-considerations.md`, PR #430 + follow-up PR #431 —
`phase:research`, full self-review + independent adversarial review +
consolidation cycle at both the artifact stage and the PR stage, both GO).
Direct request, overnight/autonomous session: audit DRY, hand-rolled-vs-
framework tradeoffs, frontend framework choice, SQLite fitness, trade-
critical/app-facing decoupling, polling load, comparison against real
auto-trading systems, an audit of predictionmarketspicks.com's tooling,
and whether the strategy should target edge/mispricing/confidence/size.
**Verdict: no full rewrite needed** — every measured defect is localized
and individually fixable. §13 gives a full prioritized action plan; Tier 1
(do first, days not weeks): (1) stop polling `/api/quality/summary` and
the two `tick_executor` routes on their fixed 6s dashboard timers — this
absorbs and supersedes issue #410's original next-action item below with
far deeper, corrected live measurement (§2, §4 of the audit); (2) fix 3
real safety-adjacent DRY bugs found: the unsupervised auto-apply path
silently skipping human-declined suggestions, `RiskManager` missing a
zero-bankroll guard `ShadowTrader` already has, and duplicated table DDL
against live multi-GB `.db` files; (3) a newly-found, previously-
unreported data-plane defect from the audit's own adversarial review: 237
`error`-severity `raw_trades` `flush` faults in the last 24h that **drop
rows outright**, not just contend for a lock — root-cause via
`superpowers:systematic-debugging` before any DuckDB migration. §14 lists
several other open design questions this audit deliberately left for a
human call, including one raised by a same-day, unrelated PR (#429, which
removed CLAUDE.md's "one SQLite file per concern" mandate): does that
change what §5's SQLite-fitness recommendations should be?

**Issue #410's original next-action text (superseded in measurement depth
by the audit above, not yet fixed in code)**: tick_executor pool
starvation (`analytics/routes.py`'s `get_candidate_log_summary` and
`whale_calibration/routes.py`'s `get_confidence_calibration_report`, both
`await tick_executor.run(...)`, confirmed against current source at
`analytics/routes.py:103` and `whale_calibration/routes.py:117`). During
PR #414's own required Task 5 live-validation window (2026-09-01, 16 min,
real WS traffic, no synthetic load), real browser traffic (nginx access
log, client `172.18.0.2` via
`autotrade.webfoundry.dev`) hit **9 upstream timeouts each** on
`/api/confidence-calibration/report` and `/api/candidate-log/summary` in a
~4-minute window, correlated with `last_tick_duration_sec` spiking to
96.56-138.5s (vs. a healthy 4-24s baseline seen earlier in the same run) -
consistent with these two routes' `tick_executor.run()` calls competing with
the tick loop's own trading-critical `tick_executor` usage for the same 2
workers. This likely explains the live symptom directly reported this
session ("as soon as i start clicking around in the app things start to
degrade... but they also degrade on their own without any action" - a
dashboard tab polling either endpoint saturates the pool on its own, user
interaction compounds it). Full evidence posted to issue #410
(https://github.com/thesneakattack/kalshi-whale-poc/issues/410#issuecomment-5500274545).
**Also found in the same window, separate and not yet root-caused:**
`/api/quality/summary` (services/quality/routes.py) hit 6 timeouts too,
despite using its own dedicated `_diagnostics_pool.py` (NOT tick_executor,
per PR #409 Task 8) - should be isolated from this specific mechanism, needs
its own look before assuming the same cause. **Mechanism note (2026-09-02):**
`_diagnostics_pool.py` no longer exists - PR #420 replaced it with
`services/diagnostics/_aio_db.py`, and PR #424 (a separate, later live
incident on the same route, post-#420) fixed one real unscoped-query cause
of `/api/quality/summary` slowness. This original 2026-09-01 finding (from
real WS traffic under `_diagnostics_pool.py`, before either PR existed) was
never independently re-investigated against the current code path - don't
assume PR #424 closed it without checking; it addressed a different
symptom (5-concurrent stalls found live post-#420) via a different
mechanism than whatever caused these 6 pre-#420 timeouts. Use
`superpowers:systematic-debugging`: measure real per-call query cost for
`population_gate_summary()`/`_build_report()` before choosing a fix shape
(pool isolation vs. query-cost reduction - issue #410's own note is that
isolation alone may just relocate the slowness if the underlying query is
also genuinely slow), per the data-plane HARD RULE. Two other follow-ups,
lower priority: issue #412 (WS reconnect churn - PR #414 already fixed the
inline-flush mechanism that caused *total* request-silence freezes; #412's
narrower remaining scope, if any, needs re-assessment against tonight's
evidence before further investigation, since tonight's residual stalls now
have a more specific, already-tracked explanation) and issue #411 (a CI
`push/tests-pytest` failure that didn't reproduce locally or on the
`pr/tests-pytest` context for the same commit - needs a valid Woodpecker
token to read the actual log, since the stored one was stale).

---

**Also open:** run `python -m tools.soak_analyzer` no earlier than ~2026-09-01T10:53Z
(~3h past PR #394's merge/deploy), then confirm via fault recency (not just
verdict text) that `capture_writer_health`/`exit_engine_faults` have
genuinely stopped recurring before revisiting the `realtime_data_plane.
two_consumer_mode` permanence decision.** The window isn't a round guess:
two direct corrections 2026-09-01 (see `soak-check-derive-window-from-logs`
memory) established that soak windows should be derived from the fault's
own measured pre-fix occurrence gaps, not assumed. Measured via
`/api/health/faults?component=exit_engine&limit=500&hours=24` (500 distinct-
ticker rows, 34.6h span, sorted+diffed): the worst gap between consecutive
pre-fix occurrences was 92.7 minutes - `capture_writer`'s own lock fault
collapses to one aggregate row (no per-instance timestamps to diff), but it
fires from the same tick-stall events, so the same cadence applies. ~3h
gives ~2x margin over that worst observed gap. PR #394
(merged 2026-09-01T07:53Z) root-caused and fixed the actual mechanism, not a
guess: `candidate_log.resolve_from_market_results`'s unbatched per-row UPDATE
loop held `candidate_log.db`'s write lock long enough to collide with
`capture_writer`'s daemon thread (measured: 91 lock faults in one window,
issue #211-adjacent but a *different* file/cause than #211's original
`raw_trades`/`series_watcher.db` diagnosis); `main.py`'s tick loop ran
`index_feed`/`settlement_edge`/`game_state`'s flush + the hourly prune sweep
(`series_watcher.prune()`'s full-scan DELETE — #211's actual named
mechanism — included) directly on the event loop, so a lock collision on any
of them froze the whole app, not just one tick (measured: 882.6s max tick
phase, 3.8h max open-position ticker staleness). Both fixed; an adversarial
review mid-development also caught and fixed a real concurrency regression
the fix itself introduced (unlocked buffers now touched from two threads).

**Live-verified post-fix (immediately after merge, not yet a real soak
window):** `capture_writer`'s `rejected_candidates` lock fault had zero
recurrence in the ~45 minutes since the fix deployed (one residual
occurrence right at the hot-reload boundary, matching the earlier live
monitoring window). `exit_engine`'s `stale_price_uncorroborated` is
**reduced but not zero** — individual "unstamped" occurrences (most recent
checked: ~9 min old) still fire for what look like genuinely thin/quiet
markets (ATP Challenger, LoL esports maps) rather than the previous
sustained, simultaneous mass staleness (`/api/health/pipeline`'s live
snapshot showed 0 stale-over-300s positions, versus 7 of 12 before the fix)
— this may be the expected baseline rate for illiquid tickers (the
corroboration mechanism is designed to fail open for exactly this case), not
a remaining defect, but that's not yet confirmed over the full ~3h window
derived above. Don't treat the immediate post-merge read as the soak
boundary itself — it's well short of the 92.7min worst pre-fix gap.

Full evidence and the 2 non-blocking follow-ups PR #394's own adversarial
review surfaced (a weaker-than-claimed regression test; `config_performance.
record_variant()` sharing the same unawaited-sync-write shape at lower risk)
are in `docs/open-decisions.md`'s newest lines.

**Also open, lower priority:** a one-time `sudo chown`/`rm -rf` cleanup pass
is needed for pre-existing root-owned leftovers in other worktrees (see
below) — `ddev exec -s fastapi` can no longer force through them now that
it runs as the host user.

## Recently resolved (2026-09-02, overnight autonomous audit session)

- **Comprehensive architecture audit completed and merged** (PR #430 +
  follow-up PR #431, `docs/superpowers/research/2026-09-02-architecture-
  audit-and-rewrite-considerations.md` + companion consolidation doc).
  Direct overnight request: audit whether the app has "gone off the
  rails" architecturally — see the "Next action" section above for the
  headline finding and top priorities. Built from a 15-minute live
  diagnostic monitor, four parallel research agents, and this session's
  own live probing; full self-review + independent adversarial review +
  consolidation cycle at both the artifact stage (caught a misattributed
  fault-file finding that also missed a real, previously-unreported
  `raw_trades` row-dropping defect, plus several other corrections) and
  the PR stage (GO, no fabricated fixes found). Unrelated to and not
  overlapping with the same-day PR #429 (a separate cloud session, real
  user-authored change removing 2 CLAUDE.md rules) — noted and accounted
  for in the audit doc's post-merge addendum, since a few of its findings
  cited those rules by name.

## Recently resolved (2026-09-02, permission-friction/guard-hook-cleanup session)

- **PR #436 merged** (`chore/workflow-guard-hook-cleanup`): `guard_workflow.py`'s
  R2 (data/*.db ad hoc access check) and R4 (GitNexus-impact-before-hot-edit
  check) disabled - commented out, not deleted, flagged for removal by direct
  instruction - and `guard_data_db.py` (the data/*.db-deletion guard)
  unregistered the same way. **Known, accepted consequence: no hook-enforced
  protection is left against `rm`/`mv` against a live trading database** - a
  deliberate departure from CLAUDE.local.md's own "data/*.db handling still
  gets confirmation" line, confirmed with the user before implementing, not an
  oversight (`services/backup/backup.py`'s periodic snapshots are a partial
  mitigant - bound worst-case loss to hours, don't prevent the desync
  incident). The three rules that stay active were cryptic single-letter
  labels (R8/R7/R3) - renamed to `GIT_ADD_ALL_BLOCKED`/`DDEV_EXEC_WRONG_WORKTREE`/
  `KALSHI_DOCS_REQUIRED`, same behavior. Separately, globally, the hand-rolled
  `~/.claude/hooks/sql_guard.py` (SQL-write confirmation gate) was deleted
  outright per the same direct instruction. Full "nothing advances on one
  pass" cycle ran at the PR stage (self-review, independent adversarial review
  - fresh Agent call, re-derived every claim from source rather than the PR
  body - consolidation; GO, one real gap found and fixed: an undisclosed,
  unrelated `permissions.allow` addition bundled into the same first commit).
  CI green on all 5 required contexts, both `push` and `pr` variants, on the
  actual merged commit. One real merge conflict with concurrent PR #437 (both
  appended a line to `docs/open-decisions.md`) resolved by keeping both lines
  in a detached-HEAD scratch worktree, so the shared primary checkout (other
  live sessions were relying on it staying put) was never disturbed.
  See `docs/open-decisions.md`'s architecture-audit-follow-up entry for what
  this change means for that audit's SQLite-fitness/DRY sections.
- Diagnosed, config/UX only, no code: why this user kept hitting "Allow once"
  permission prompts despite repeated in-chat assertions of blanket
  permission - root cause was `permissions.defaultMode: "auto"` sitting in
  the wrong settings-file scope (project-local, where Auto Mode is silently
  ignored; only user/managed scope activates it), and even once fixed, Auto
  Mode's classifier is a risk judgment, not a deterministic allow-all, so it
  can still prompt on unfamiliar-looking commands. Resolution adopted: the VS
  Code extension's "Allow dangerously skip permissions" toggle (a UI setting,
  not a CLI flag or settings.json key - confirmed the CLI-flag approach has
  zero effect on an already-running VS Code extension session) plus a session
  restart makes `bypassPermissions` available in the Shift+Tab mode cycle
  inline, on demand - confirmed PreToolUse hooks (the renamed R3/R7/R8 above)
  still fire and can still deny under that mode.

## Recently resolved (2026-09-02, this session)

- **PR #420 merged and deployed** (event-loop-blocking-fix2, diagnostics
  widening): converted `services/diagnostics/diagnostics.py` and
  `services/series_watcher.py`'s read-only functions from sync `sqlite3` to
  `aiosqlite`, via a new shared connection cache (`services/diagnostics/_aio_db.py`)
  and deleted the now-redundant `_diagnostics_pool.py`. Full plan
  (`docs/superpowers/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md`)
  now fully checked off, including Task 7's live smoke test — which is what
  surfaced the real live incident below.
- **PR #424 merged** (`fix/run-offline-cooperative-yield`, fast-follow to
  PR #420): PR #420's own required 5-concurrent burst test found a genuine
  live incident — 5 concurrent `GET /api/quality/summary` requests stalling
  an unrelated `GET /api/state` for minutes. Root cause: `check_confidence_input_coverage`'s
  previously-unscoped query against `signal_log.db` (127k+ rows and
  growing), now bound to a purpose-matched 24h window (closes
  `docs/open-decisions.md`'s Task 9 entry) — measured ~1.0s / ~30% saved
  per `run_offline()` call. An elastic per-file connection pool was also
  tried as a second fix, but a full-branch adversarial review measured
  every variant of it as *worse* than no pool at all on the same
  5-concurrent workload (26.5-28.0s wall / up to 1939ms worst co-resident
  stall with the pool vs. 15.0-16.4s / 168-216ms without it) — reverted
  `_aio_db.py` back to PR #420's original single-connection-per-file
  design rather than keep a "should help" guard rail that measurably made
  the incident worse. Combined final design (single connection + query
  bound), independently re-measured twice against real production data:
  single call ~2.7-2.8s warm, 5 concurrent calls ~13.2-14.3s wall — faster
  than both the pre-branch baseline and the reverted pool. Full "nothing
  advances on one pass" cycle at both branch stage (self-review →
  adversarial review → consolidation → a second full-branch adversarial
  review after 2 more commits landed mid-cycle, which returned NO-GO and
  drove the revert) and PR stage (self-review + independent adversarial
  review + consolidation, GO) —
  `docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-*.md`.
  Real lesson worth remembering: a well-evidenced, seemingly-fixed
  concurrency guard rail can still be proven by rigorous re-measurement to
  regress the exact metric it was built to protect — the fix was reverting
  it, not defending it further.
- **PR #417 merged** (`loop_watchdog` fault visibility) **+ PR #418** (a
  base-branch correction PR #417 itself needed). `services/loop_watchdog.py`
  has sampled the event loop for stalls every 0.1s since I13 P0 Task 1, but
  its snapshot was read-only - live investigation the same night found
  severe stalls (up to 130s over 24h, 56.6s in a 15-minute window) sitting
  in `observability.db` entirely unsurfaced, with no `fault_log` entry and
  no automated `soak_analyzer`/`quality_audit` check ever reading it.
  `maybe_capture` now logs one `fault_log` row per persisted window that saw
  a stall (`severity="error"` at `stall_max_ms >= 1000ms`, else `"warn"`);
  `tools/soak_analyzer.py` gains `check_event_loop_stalls` (DATA_PLANE
  layer). Full review cycle: self-review, then a fresh independent
  adversarial review (GO, 6 minor findings, none blocking -
  `docs/superpowers/specs/2026-09-01-loop-watchdog-fault-visibility-pr417-*.md`),
  consolidation, 3 mechanical fixes applied (exact-1000ms boundary test, a
  misleading docstring, a README update); 2 real design questions deferred
  to `docs/open-decisions.md` rather than reflexively patched (the check can
  never report PASS from a genuinely healthy window - a pre-existing SQL
  `GROUP BY` shape shared with `check_exit_engine_faults`; severity is keyed
  on peak stall magnitude only, never frequency). **Process mistake, caught
  and fixed same session:** PR #417 was opened against
  `feat/realtime-data-plane-remediation` instead of `main` - a wrong
  inference from reading that branch's own `git log` (it contained PR #414's
  merge commit only because it had separately merged `origin/main` into
  itself earlier; the local `main` ref was actually 176 commits stale,
  never pulled). PR #414/#409/#415 all correctly targeted `main` directly
  the whole time - only this session's own docs commit and PR #417 drifted.
  Fixed via PR #418 (`feat/realtime-data-plane-remediation` → `main`, clean
  fast-forward, all 5 stray commits now in `main`); primary checkout moved
  onto `main` directly and the now-fully-redundant
  `feat/realtime-data-plane-remediation` branch deleted (local + remote) to
  prevent recurrence. See the `never-infer-integration-branch-from-local-log`
  memory for the full lesson.
- **PR #414 merged and deployed** (event-loop-blocking elimination Fix 1,
  merge commit `9e26af7`): 6 functions across `services/index_feed/ingestion.py`,
  `services/settlement_edge.py`, `services/game_state.py`,
  `services/series_watcher.py` no longer call SQLite `flush()` inline,
  synchronously, unawaited, from `async def` functions on the event loop -
  each now returns `should_flush`/is offloaded via
  `asyncio.create_task(tick_executor.run(<module>.flush))` (fire-and-forget)
  or, for `last_tick_before` (needs its own flush-then-read atomicity), a
  direct `await tick_executor.run(...)`. Full "nothing advances on one pass"
  cycle at both spec/plan and PR stage (self-review + adversarial review +
  consolidation, fresh Agent calls each time;
  `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix1-pr-review.md`);
  adversarial review at PR stage found 2 more undisclosed sync-flush sites
  (`last_tick_before`/`record_cfbenchmarks_backfill`), fixed and re-reviewed
  before merge. Full suite: 3037 passed / 0 failed, confirmed independently
  twice. **Live validation (this plan's own required Task 5 Step 3) found a
  genuine complication worth recording honestly:** the *first* live-validation
  attempt was contaminated by this session's own mistake - a leftover
  `docker exec pytest` process from an earlier check was misdiagnosed as
  leaked test debris and killed (with explicit user approval after the auto-mode
  classifier twice blocked the attempt), but the killed process was actually
  uvicorn `--reload`'s own live server worker (cmdline
  `multiprocessing.spawn_main`, part of uvicorn's real reload mechanism, not
  pytest) - this caused a genuine ~4-minute outage, recovered via
  `ddev restart`. A second, truly clean 16-minute window (post-restart, zero
  intervention from this session) then confirmed: **PR #414's own narrow goal
  is met** - the *total* request-silence app freeze (45-90s+, zero requests
  served anywhere) that motivated this fix did not recur even once, including
  during periods of elevated `last_tick_duration_sec` (96-138s), where
  non-tick_executor-dependent endpoints kept serving normally throughout
  (qualitatively different, healthier failure mode than pre-fix). **What the
  clean window also found: a residual, smaller-magnitude stall pattern
  (~35-40s recurring full-silence gaps, and the elevated tick durations
  above) that PR #414 was never scoped to fix** - traced to issue #410's
  already-filed, previously-"unmeasured" tick_executor pool-sharing finding,
  now measured with real evidence (see "Next action" above and
  https://github.com/thesneakattack/kalshi-whale-poc/issues/410#issuecomment-5500274545).
  Not a PR #414 regression - a pre-existing, separate mechanism its fix was
  never meant to close.
- **PR #409 merged and deployed** (write-path capacity fix, milestone issue
  #400/8 tasks, all closed): two root causes fixed under one architectural
  principle - nothing non-critical shares `services.tick_executor`'s
  2-worker pool (or Python's shared default executor) with trading-critical
  writes. (1) `kalshi_trade_tape.py`'s per-trade whale-scoring pipeline
  opened a fresh SQLite connection per trade across 3 db files - fixed with
  a dedicated 4-worker pool + thread-local connection cache
  (`services/whalewatchers/_scoring_pool.py`), also bringing
  `candidate_retry.py`'s synchronous-on-the-event-loop
  `score_recovered_trade` path into scope (made async). (2)
  `GET /api/quality/summary`'s `diagnostics.run_offline()` shared
  `tick_executor`'s pool with `capture_writer`/`candidate_log` writes,
  confirmed live starving them (both workers pinned 5h10m+, recurring lock
  faults) - fixed with a dedicated 2-worker isolation pool
  (`services/diagnostics/_diagnostics_pool.py`). Full review cycle both at
  spec/plan stage (2 independent adversarial-review rounds, fresh Agent
  calls) and at PR stage (self-review + adversarial review + consolidation,
  `docs/superpowers/specs/2026-09-01-write-path-capacity-fix-pr-review*.md`)
  - the adversarial review found a wrong circular-import claim in
  `market_history.py`'s docstring (fixed, corrected to the real mechanism)
  and a stale comment, both fixed and rechecked before merge. Measured
  before/after on live production (issue #407): `handler_total`'s worst case
  267,468ms→8,709ms (30.7x), `provider` (whale-scoring) 11,488ms→5,026ms,
  `signals` 255,980ms→8,685ms (29.5x) - a clean, unambiguous win on every
  stage this fix targets. Tick duration improved but not fully healthy
  (228s→113s) - a real, separate residual mechanism (WS reconnect churn +
  slow REST calls) is now this file's top item (issue #412). Two other
  follow-ups filed, not folded into this fix per the data-plane HARD RULE
  (measure before touching capacity/isolation): issue #410 (two sibling
  routes with the same tick_executor-sharing shape, unmeasured), issue #411
  (an unreproduced CI push-context pytest failure, not a required check).
- **PR #394 merged** (2026-09-01T07:53Z): root-caused and fixed the
  `capture_writer_health`/`exit_engine_faults` recurrence via
  `superpowers:systematic-debugging`, not a guess. Started from a different
  angle — "guarantee open positions keep getting stream data even after
  rotating out of the watchlist" — and confirmed that mechanism (`services/
  market_watch/market_fetch.py`'s unconditional `extra_tickers` merge) was
  already sound (live-verified 0 unstamped open positions); the real live
  defect was a shared-resource throughput bottleneck: (1)
  `candidate_log.resolve_from_market_results`'s unbatched per-row UPDATE
  loop held `candidate_log.db`'s write lock long enough to collide with
  `capture_writer`'s daemon thread (91 lock faults measured in one window);
  (2) `main.py`'s tick loop ran `index_feed`/`settlement_edge`/`game_state`
  flush + the hourly prune sweep (`series_watcher.prune()`'s full-scan
  DELETE, #211's actual mechanism, included) directly on the event loop, so
  a lock collision anywhere in that phase froze the WHOLE app, not just one
  tick (882.6s max measured tick-phase duration; 3.8h max open-position
  ticker staleness). Both fixed by routing through the existing
  `tick_executor` pool/batching writes, no thread/queue/timeout capacity
  changed anywhere. A genuinely separate adversarial-review pass mid-
  development caught a real regression the fix itself introduced (moving
  3 modules' flush() onto a worker thread while their record functions
  still appended from the event loop raced their unlocked buffers - a real
  6.5%-row-loss repro before a `threading.Lock` fix, modeled on `services/
  series_watcher.py`'s own pre-existing lock for the identical race). A
  second full review pass ran against the PR as submitted before merge
  (both passes: fresh Agent calls, no memory of the session, GO verdict,
  CI green on all 5 required checks). 2 non-blocking follow-ups recorded
  in `docs/open-decisions.md`. See the "Next action" section above for the
  soak-check this now needs before `two_consumer_mode`'s permanence gate
  becomes answerable.
- **PR #387 merged**: `fastapi` ddev container now runs as the host user
  (`user: "${DDEV_UID}:${DDEV_GID}"` in `.ddev/docker-compose.fastapi.yaml`),
  not root. Root cause of `config/settings.yaml` and 2,163 other paths
  (including several live `data/*.db` files) silently going root-owned on
  every write — `services/config/config_store.py`'s `update()` writes
  straight onto the bind-mounted repo, and Docker/WSL2 doesn't remap
  container UIDs. Verified live before/after (`ddev exec -s fastapi id`,
  a real `POST /api/config` round-trip). Full review cycle run: independent
  adversarial review confirmed the mechanism against ddev's own internal
  compose templates (not just its docs page), and caught two comments
  (`scripts/cleanup-worktrees.sh`, `.claude/hooks/check_py_syntax.py`) left
  factually stale by the exact same change — fixed in the same PR before
  merge. One-time remediation chown was done by David (sudo, host-side,
  outside the diff).
  **New, expected side effect** (flagged live by `autotrade-d4`): pre-fix
  root-owned leftovers in *other* worktrees (e.g.
  `.claude/worktrees/impl-whale-confidence-scoring` after PR #388) can no
  longer be force-cleared by `ddev exec -s fastapi rm -rf` — it's no longer
  root either. `cleanup-worktrees.sh`'s comment already documents this
  (says "kept", never lies about success); the leftover directories
  themselves still need a one-time host-level `sudo rm -rf` pass whenever
  convenient. Not urgent, not blocking anything.
- **PR #389 merged**: `config/settings.yaml` updated directly by David via
  the dashboard — `markets_watchlist` widened from 1 ticker (`KXBTC15M`) to
  16 across BTC/ETH/gold/silver weekly/daily/hourly series (addresses the
  long-standing watchlist-coverage-bottleneck gap), `strategy.min_unit_cost`
  0.35→0.25, `whale_watcher_kalshi.min_contracts` 10000→3000. The same save
  also silently wiped `min_contracts_by_series` and
  `strategy_overrides.by_category` (Sports `stop_loss_pct` override) to
  `{}` — `config_store.py`'s `update()` shallow-merges, so a patch
  resending a top-level key as a bare `{}` overwrites rather than
  preserves. Flagged explicitly; David confirmed leaving both wiped. Only
  the historical `whale_confidence_weights` calibration-audit comment
  (pure documentation) was restored, since its deletion was pure collateral
  loss rather than an intended edit. Also added
  `docs/nothing-advances-diagram.png`.
- **PR #388 merged** (by `autotrade-d4`, whale-confidence-scoring-remediation
  Tasks 1-9): fixed 4 fabricated-default sites and a tie-blind bucketing bug
  in the whale-confidence-scoring formula
  (`services/confidence_scoring.py`, `services/whale_calibration/confidence_calibration.py`,
  `services/whalewatchers/kalshi_trade_tape.py`,
  `services/diagnostics/diagnostics.py`, `services/signal_log.py`,
  `services/market_analyst_agent/per_market.py`,
  `frontend/src/js/advisory-calibration.js`). No overlap confirmed with this
  session's work. **Tasks 10-16 blocked on a soak-time gate** — see
  `autotrade-d4` for status.
- Confirmed (not assumed) that git is genuinely absent from the `fastapi`
  container — already tracked at `docs/open-decisions.md` line 10, not a
  new finding; a stale comment in `tools/project_manifest.py`'s
  `_git_head()` claims otherwise (harmless — caught as `OSError`, not a
  live bug) but was left alone as out-of-scope for the container-user PR.
- Ran `docs/next-action.md`'s previously-pending soak_analyzer recheck (see
  "Next action" above for what it found) and `tools/quality_coordination`'s
  read-only scan — its `escalation_eligible` backlog (a dozen+ old plan
  docs, `feat/candlestick-volatility`) is pre-existing, already-investigated
  noise per `docs/open-decisions.md`'s 2026-08-30 entry (AQC's cleanup
  action for this was retired after proving 0-value); nothing new added.
  Skipped `kanban_sync`/board writes this session — `autotrade-d4` and
  `autotrade-1f` were both live and mid-merge (PRs #388, #390) at checkpoint
  time; confirmed with `autotrade-d4` the board was clear for them
  afterward.

## Leave alone — active peer-session work

- `.claude/worktrees/candlestick-volatility` (`feat/candlestick-volatility`).
- `.claude/worktrees/impl-whale-confidence-scoring` — PR #388 merged by
  `autotrade-d4`, git-level cleanup done, but an inert leftover directory
  with root-owned cache files remains (see "Also open" above).
- `feat/watchlist-event-grouping` (`autotrade-1f`, PR #390) — frontend-only
  (watchlist sidebar event-grouping), confirmed no file overlap with this
  session's work.

## whale-confidence-scoring-remediation open items (PR #388, autotrade-d4)

- **Task 10 (real-data re-measurement gate).** The official check is the
  app's own API: `GET https://kalshi-whale-poc.ddev.site:8443/api/confidence-calibration/report`
  (ddev running, local only, no auth) for the per-factor `data_status`
  breakdown; `GET .../api/quality/summary` also surfaces
  `check_confidence_input_coverage`'s absence-rate summary as a
  cross-check — that's what Step 2 of the plan's own Task 10 names. A
  direct read against `data/signal_log.db` (read-only) is equally fine for
  exploratory questions the API doesn't answer as a stat (e.g. resolution-
  lag distribution, counts by timestamp) — use whichever actually answers
  the question, don't default to one over the other on principle. As of
  the PR #388 merge (2026-09-01T06:02:21Z), 20 min post-merge: 77 new
  signals logged under the fixed formula, 0 yet resolved (median
  seen→resolved lag ~106min on recent history, so the first real post-fix
  resolved signals land within roughly an hour of merge, at ~5,150/day
  historical rate). Confirm no factor reads `data_status: "contaminated"`
  (`"insufficient_variance"` is fine/expected for `analyst_factor`/
  `block_trade_factor`), record the real observed per-factor gaps in the
  commit message per the plan's own Step 4-6, then Tasks 11-16 can start
  (plan: `docs/superpowers/plans/2026-08-30-whale-confidence-scoring-remediation-implementation.md`).
  A few thousand post-fix resolved signals (roughly half a day to a day at
  the measured rate) clears the materiality-floor blind band noted below;
  matching the original audit's own sample size for full confidence takes
  a few days — use judgment on how much rigor the moment calls for, design
  §10 sets no fixed duration.
- **Do not click "Apply suggested weights" on the whale-confidence-calibration
  dashboard panel right now.** The tie-safe re-measurement correctly flags
  5/9 factors unreliable on real data, which concentrates `suggested_weights`
  onto 2 factors (measured: `context_factor` 0.34→0.54 if applied).
  `auto_apply_enabled` stays `false` so nothing writes this automatically,
  but the manual route has no guard yet — Task 12 is the fix, blocked
  behind Task 10 by design. Full detail: `docs/open-decisions.md`.
- **Two algorithm-precision follow-ups from PR #388's own adversarial
  review**, not fixed in that PR on purpose (design work, not a same-PR
  patch): `_tied_run_size` flags contamination on a tied run's full length
  rather than the minority side actually crossing the cut (real example: a
  1,662-row run where only 1.1% is on the minority side trips the same flag
  as a fully-tied bucket); the materiality floor `max(30, 0.005*n)` has a
  blind band below n≈6000 (not reachable at today's real ~103k-row volume,
  latent at `min_resolved_signals: 50`). Worth a design call before Task 10
  treats today's algorithm as final. Full detail: `docs/open-decisions.md`.

## Also still open, unrelated

- **Parked, needs your read:** `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design.md`'s
  implementation plan is now ready (`docs/superpowers/plans/2026-08-31-weather-index-ingestion*.md`,
  full review cycle complete) — give the go-ahead on Task 1, or decline and
  close (`docs/open-decisions.md`).
- **Needs your go-ahead, not a session's:** the claudesuperpower.com
  plugin-pilot plan (`docs/superpowers/plans/2026-08-31-claudesuperpower-plugin-pilot*.md`)
  is ready — Task 1 (3 plain-install plugins) and Task 5 (codspeed, needs an
  external account) are separate go-aheads (`docs/open-decisions.md`).
- kalshi-category-data-completeness Task 12's 2-underlying Pyth Commodities
  scope (gold/silver only vs `["all"]`) was never an explicit go/no-go —
  confirm or widen (`docs/open-decisions.md`).
- `propagate_milestone_winners`'s `related_event_tickers`-vs-market-tickers
  mismatch (0 markets returned for 710 real event tickers, pre-existing,
  found 2026-08-31) — decide priority/approach (`docs/open-decisions.md`).
- `scripts/cleanup-worktrees.sh`'s `git branch -d` vs `-D` bug (spurious
  abort when the primary isn't on `main` and the remote branch is already
  gone) — small fix, needs its own review cycle (`docs/open-decisions.md`).
- `/api/quality/summary`'s `series_funnel` pricing/edge gap at entry
  (KXBTC15M/KXMLBGAME/KXATPMATCH underwater after fees) — already-documented,
  open (CLAUDE.md's "Standing goal" section), not a new finding.

Layer contract behind the tool: `docs/data-layer-analysis-layer-contract.md`.
Full audit history if picking this up cold:
`docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md`.
