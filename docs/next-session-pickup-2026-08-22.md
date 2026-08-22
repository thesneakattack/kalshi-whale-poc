# Session pickup — 2026-08-22

Written at 93% session usage, per direct standing instruction this session
("when you hit 93% start winding down... so that when you hit 97% you've
committed and pushed and can write documentation for the next session").
Everything through Phase 6 is committed, pushed, and verified live-healthy.
Phase 7 has not been started — only read-only exploration (grep/read, no
edits) happened before this doc was written, so there is nothing
half-finished to untangle.

**Read this before `ROADMAP.md`.** The full modularization plan (context,
ground rules, module boundaries, all 7 phases in detail) lives at
`/home/davidf/.claude/plans/dapper-cuddling-penguin.md` — read that first,
this doc is just the "where exactly things stand" pointer on top of it.

## What shipped this session (all pushed, live-verified)

The whole session started from a direct instruction to establish that
**system performance** (latency/responsiveness/reliability - NOT signal
accuracy, which has its own separate 70%/70% objective in CLAUDE.md) is
the real problem, and to modularize `main.py` (was ~5,450 lines) so each
performance issue becomes fixable inside a named, bounded file instead of
one giant file. Findings that motivated this: message drops under
exchange-wide trade load, tick-duration budget overruns, and a websocket
stream that silently wedged for 43+ hours while self-reporting
`connected: true`.

Also fixed earlier the same session (before the modularization work
started, separate commits): reverted 3 days of config drift that had
violated CLAUDE.md's 70%/70% hard commandment (`b6a58a9`'s parent), and
fixed a live bug where Kalshi's undocumented `quadratic_with_combo_maker_fees`
fee type was crashing `market_catalog`'s background scan every cycle
(`b6a58a9`).

| Commit | Phase | What |
|---|---|---|
| `5a96c64` | 1 | Removed `strategy_engine.evaluate()`'s `import main` reach-around |
| `4b0858b` | 2 | `services/state_view.py`, `market_lookup.py`, `account_positions.py`, `ws_manager.py` |
| `d4e7484` | 2b | `services/config/` (added mid-execution - direct instruction) |
| `45ad494` | 3 | `services/position/routes.py` |
| `a843d59` | — | Cheat sheets for config + position |
| `e4c2e82` | 4+5 | `services/history/`, `services/analytics/` (incl. `market_analyst_orchestrator.py`) |
| `8a344ea` | 6 | `services/whale_stream/` (`decision_bridge.py`, `whale_stream_handlers.py`, `index_stream_handlers.py`) |

`main.py`: 5,450 → **3,014 lines**.

**Folder-per-concern structure** (added mid-execution, direct instruction:
"restructure the folders and files so i could scope these modules
individually... in their own git repos or very least claude sessions") -
every module from Phase 2b onward lives in `services/<concern>/`, and each
concern's router lives inside its own `services/<concern>/routes.py`
rather than the old flat `routers/<concern>_routes.py` pattern. Phase 2's
four files (`state_view.py` etc.) stay flat - they predate the
instruction and are cross-cutting helpers, not one of the six concerns.

**Per-module `CHEATSHEET.md`** (added mid-execution, direct instruction) -
`services/config/`, `services/position/`, `services/history/`,
`services/analytics/`, `services/whale_stream/` each have one: what the
module owns, relevant `docs/kalshi/` pages (or "none, internal"), and the
handoff to/from adjacent modules in the real data flow. **Phase 7's module
needs one too** (`services/market_watch/CHEATSHEET.md`) - not written yet.

**Verification discipline this session**: fast static checks between
phases (`py_compile`/`ast.parse` + an AST scan for orphaned top-level defs
that should've been removed), a **live health check via curl before every
commit** (not just syntax - `/api/state`, `/api/health/pipeline`,
`/api/health/faults`, confirming both websocket streams still connect and
real messages still flow), full `pytest` suite deferred to one
comprehensive pass at the end (direct instruction: "focus on modularization
completion first over everything else, skip expensive testing until the
end"). **That final full-suite pass has not happened yet** - do it before
declaring the whole plan done, not just per-phase live checks.

## Start here: Phase 7 (the last one) - market watch / discovery

Not started. This is the **largest and highest-risk remaining phase**
(~1,300 contiguous lines, and the current tick-duration bottleneck per
this session's own performance investigation). Exact current boundaries in
`main.py` (verified via `grep`/`awk` right before this doc was written -
re-verify with a fresh grep before trusting these numbers, since nothing
guarantees main.py is byte-identical if anything else touched it):

**The contiguous block to move: main.py lines 127–1434.**
- Line 127 (`_MILESTONE_REPOLL_SEC = 60`) is the first line to move - lines
  113–125 just before it (`_maybe_prune_capture_stores` +
  `_last_capture_prune_at`) **stay in `main.py`** (trading_loop's own
  hourly-prune bookkeeping, not a market-watch concern).
- Line 1434 (blank line right before `_SIGNAL_RESOLUTION_CHECK_INTERVAL_SEC`
  at 1435) is the last line to move - `_maybe_check_signal_resolutions`
  and everything after it is history/signal-resolution territory, stays in
  `main.py`.

Everything in between, in file order: `_MILESTONE_REPOLL_SEC`,
`propagate_milestone_winners`, `_MARKET_FIELDS`, `_slim_market`,
`_SERIES_CACHE_TTL_SEC`, `_PINNED_MARKET_REFRESH_SEC`,
`_DISCOVERY_REFRESH_SEC`, `_get_series_cache`, `_get_top_series`,
`_CATALOG_SCAN_BATCH_SIZE`, `_scan_catalog_batch`,
`_CATALOG_SCAN_MIN_INTERVAL_SEC`, `_maybe_scan_catalog_batch`,
`_scan_catalog_batch_background`, `_fetch_category_metadata`,
`_cached_market_fetch`, `_maybe_refresh_discovery_cache`,
`_DISCOVERY_TERMINAL_STATUSES`, `_refresh_discovery_cache`,
`_refresh_discovery_cache_background`, `_fetch_markets`,
`_LIVE_STATUS_LOOKBACK_SEC`, `_LIVE_STATUS_LOOKAHEAD_SEC`,
`_LIVE_STATUS_REPOLL_SEC`, `_LIVE_STATUS_TERMINAL`,
`_LIVE_STATUS_MAX_POLL_PER_TICK`, `_fetch_live_status`,
`_fetch_exchange_status`, `_fetch_event_titles`,
`_EVENT_LIVE_DATA_REPOLL_SEC`, `_EVENT_LIVE_DATA_EXCLUDED_CATEGORIES`,
`_fetch_event_live_data`.

**Given the size, the safest mechanical approach** (planned but not yet
executed): use `sed -n '127,1434p' main.py > /tmp/market_watch_body.py` to
extract verbatim (avoids manual retyping/transcription risk on 1,300
lines), prepend a hand-written import block to a new
`services/market_watch/market_watch.py`, then `sed -i '127,1434d' main.py`
to remove it from the source - mirroring exactly how Phase 2's smaller
extractions were verified (AST orphan-check + live curl check), just with
`sed` doing the bulk copy instead of Read/Write.

**Known external call sites to fix in `main.py` after the move** (from
this session's earlier investigation, re-confirm live since line numbers
will have shifted): `_fetch_live_status` has 3 callers - two inside
`trading_loop` itself, one inside the `/api/state` view-builder path
(likely now inside `services/state_view.py` or `main.py`'s
`_build_state_body`, re-check) - all three become
`market_watch._fetch_live_status(...)`. `trading_loop`'s `market_fetch`/
`resolve_and_record` phase bodies will shrink to calls into
`market_watch._fetch_markets`/`_fetch_exchange_status`/
`propagate_milestone_winners`.

**Imports the new module will need** (from `services.app_state`): `state`
only, per the plan doc - verify this holds once the block is actually
extracted, since a 1,300-line block is exactly the kind of thing that
might have one more dependency than expected.

**`tests/test_trading_gate.py` references many of these functions
directly** (`main._fetch_markets`, `main._fetch_live_status`,
`main._get_top_series`, `main._refresh_discovery_cache`,
`main._refresh_discovery_cache_background`, `main._scan_catalog_batch`,
and likely more - re-grep rather than trust this list) - per this
session's established pattern, importing each name into `main.py`'s own
namespace (`from services.market_watch.market_watch import _fetch_markets,
...`) makes `main._fetch_markets(...)` keep working automatically, no test
edits needed, **except** for any test that *reassigns* a module-level
mutable directly (like the `_full_spectrum_analyzing` gotcha below) -
scan for that pattern specifically in this block too (e.g. does anything
reassign `_settlement_spec_cache`-style module state directly via `main.`).

## Known test fix-ups needed at the final verification pass (not yet done)

1. `test_trading_gate.py:2600` (approx - re-grep) does
   `main._full_spectrum_analyzing = False`. Importing that name into
   `main.py` does NOT make this work - it's an immutable bool, so the
   test's reassignment only rebinds `main.py`'s own local name, not the
   real module-level flag `_run_full_spectrum_analysis` reads via `global
   _full_spectrum_analyzing` inside
   `services/analytics/market_analyst_orchestrator.py`. Fix: change that
   test line to reassign the real module attribute directly (`import
   services.analytics.market_analyst_orchestrator as mao;
   mao._full_spectrum_analyzing = False`), not a `main.py` import tweak.
   (`_analyzing_tickers`/`_analyzing_series` don't have this problem -
   they're sets, and `.clear()` mutates the same object regardless of
   which name it's accessed through.)
2. Run the **full `pytest` suite** for the first time since Phase 2b
   (`ddev exec -s fastapi python3 -m pytest -q`) - every phase after that
   was verified via static checks + live curl checks only, per the
   "skip expensive testing until the end" instruction. Expect some
   mechanical `main._foo` reference fixes in `test_trading_gate.py` for
   names that moved but were never imported back into `main.py` because
   `main.py`'s own code didn't need them (same shape as the
   `_slim_event_position` fix in Phase 2 - grep for the actual failures
   rather than guessing which ones).

## Queued follow-ups (in `ROADMAP.md`, not started)

All added as direct instructions mid-session, deliberately deferred rather
than folded into the modularization phases:

1. **Move analytics/advisory computation out of the live tick loop** -
   data-dump-and-analyze-externally instead of `trading_loop`'s
   `calibration_advisory` phase computing it in-process every tick.
2. **Consider removing the Market-Native strategy entirely** - "over-
   complicating things," already off by default
   (`market_strategy.enabled: false`), but the code duplication is real
   and now touches several already-modularized files
   (`services/position/routes.py`, `services/analytics/routes.py`) - do
   this as its own pass once modularization ships, not mid-phase.
3. **Consider a dedicated charts/graphs module**, possibly server-rendered
   via Plotly/Matplotlib, for `cumulative_pnl_curve`/`equity_history`-shaped
   data - a rendering-technology question, separate from pure
   modularization.
4. **Retroactively move already-stable flat files** into their concern's
   folder (`paper_broker.py` → `services/position/`, `strategy_engine.py`
   → `services/position_management/`, `config_store.py` et al. →
   `services/config/`, etc.) - explicitly NOT done this session (real
   import-site churn across the whole codebase for files that already
   work), queued once the new-file convention has proven itself.

## After Phase 7 ships

Per the plan doc's "Wrap-up state" section: `trading_loop`'s 8 phase
bodies should be mostly 1-3 line delegations into the new modules at that
point. The loop's control flow and phase ordering stay in `main.py`
deliberately (CLAUDE.md's own "Quick file map" describes `main.py` as
including "the trading loop"). Run the full test suite, fix the known
issues above, do a final live smoke pass across every moved endpoint, then
this modularization effort is done - update `ROADMAP.md`'s "Finish
breaking up `main.py`" line (`## Path to production` section) to checked
off, and use `/sync-status-docs` to add the `static/status.html` timeline
entry.
