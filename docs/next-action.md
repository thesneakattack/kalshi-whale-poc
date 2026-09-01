# Next action

**Run `python -m tools.soak_analyzer` after a real ~24h soak window (earliest
useful check ~2026-09-02T08:00Z, 24h past PR #394's merge), then confirm via
fault recency (not just verdict text) that `capture_writer_health`/
`exit_engine_faults` have genuinely stopped recurring before revisiting the
`realtime_data_plane.two_consumer_mode` permanence decision.** PR #394
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
a remaining defect, but that's not yet confirmed over a real 24h window.
Don't treat the immediate post-merge read as the soak boundary itself.

Full evidence and the 2 non-blocking follow-ups PR #394's own adversarial
review surfaced (a weaker-than-claimed regression test; `config_performance.
record_variant()` sharing the same unawaited-sync-write shape at lower risk)
are in `docs/open-decisions.md`'s newest lines.

**Also open, lower priority:** a one-time `sudo chown`/`rm -rf` cleanup pass
is needed for pre-existing root-owned leftovers in other worktrees (see
below) — `ddev exec -s fastapi` can no longer force through them now that
it runs as the host user.

## Recently resolved (2026-09-01, this session)

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
