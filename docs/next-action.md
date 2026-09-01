# Next action

**Root-cause why `capture_writer_health`/`exit_engine_faults` are still
actively recurring, not aging out as previously hoped.** The 2026-08-30
soak boundary assumed these would clear from the 24h fault window on their
own; the 2026-09-01 re-run still VERDICT FAIL, and `/api/health/faults`
confirms both are live (`capture_writer`'s "database is locked" — issue
#211, already tracked — last occurred 2026-09-01T06:06:23Z; `exit_engine`'s
`stale_price_uncorroborated` last occurred 2026-09-01T06:07:09Z, untracked
so far). See `docs/open-decisions.md`'s newest line for the full evidence.
Use `superpowers:systematic-debugging`. Only once both are actually clean
(checked by fault recency, not just verdict text) does the
`realtime_data_plane.two_consumer_mode` permanence decision become
answerable.

**Also open, lower priority:** a one-time `sudo chown`/`rm -rf` cleanup pass
is needed for pre-existing root-owned leftovers in other worktrees (see
below) — `ddev exec -s fastapi` can no longer force through them now that
it runs as the host user.

## Recently resolved (2026-09-01, this session)

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
