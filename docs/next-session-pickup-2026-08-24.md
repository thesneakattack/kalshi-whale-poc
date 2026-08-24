# Session pickup — 2026-08-24

Written mid-session, per direct instruction ("get the session to a good
committable pushable compactable point before continuing... leave clear
steps for the next session so after a /clear you can pick up right where
you left off"). Everything through the commit listed at the top of "What's
shipped" below is committed, pushed, and CI-green. One sub-unit of the
close-time fix is implemented and locally verified but **not yet
committed** as of this doc being written — see "In progress" below for the
exact boundary.

## What's shipped this session (all pushed, CI-green)

Three completed modularization passes (approved together earlier in the
session, "all three in that order"): `whale_simulator.py` →
`confidence_scoring.py`, `index_feed.py` → `services/index_feed/` package,
`market_analyst_agent.py` → `services/market_analyst_agent/` package.

Plus a frontend polling fix (`refreshActiveViewPanels()` removed — was
resetting scroll position and causing History-tab lag via a redundant
per-poll re-render).

Then a wave of direct live bug reports, each investigated and fixed:

| Commit | What |
|---|---|
| `22866c0` | Portfolio "no"-side position display: stale `positions-toggle` DOM leak from Real mode + Kalshi's `no_sub_title` "TBD" placeholder for the whole `KXBTC15M` series |
| `919a2d5` | Documented the TBD quirk in `docs/kalshi/CHEATSHEET.md` |
| `8b227fe` | Trade-channel CPU quantification instrumentation (`state["trade_stream_perf"]`) — **not yet read for a real sample, see below** |
| `485ea9c` | `series_evaluator.enabled=false` was still feeding real suggestions into Advisory, including through the **auto-apply** path |
| `dd6b1cb`/`e00e13a`/`bc6bcb7` | New `ROADMAP.md` items: use relevant collected data before trimming (with a discovery-visibility guardrail), and fresh evidence added to the existing retrospective-sweep item |

Full suite was at 1,207 tests passing after the module splits, growing with
each subsequent fix's own tests. Current count including the uncommitted
close-time sub-unit below: **1,222 passed**, confirmed green.

## In progress — the close-time/live-status fix

**The bug** (direct report, "MAJOR MAJOR"): every entry/exit gate that
reasons about "time until this market closes" used Kalshi's raw
`close_time` field only, even for event-style markets where `close_time`
can be a month+ out while the real event (and its outcome) is already
known. Live-reproduced: `KXVOTEPRIMARY-FLPRIMARY06R26ABAK-9` had
`close_time` 359.5 days out while its real primary election was 5.5 days in
the *past* — the exit-side runway floor (`exit_min_seconds_to_close`,
zero `is_live` awareness at all) never fires, so a position rides
unmanaged for up to a year past the real outcome.

**The full plan** is written out in detail — read it before continuing,
don't re-derive: `/home/davidf/.claude/plans/synchronous-honking-shamir.md`
(this session's Claude Code plan file — if it's gone by next session,
the design is also fully captured in this doc's own git history / the
conversation transcript, but the plan file is the primary source). Design
in one sentence: a new `effective_close_time()` resolver in
`services/market_lookup.py`, precedence `expected_expiration_time` (new
field, free) → `event_schedules[event_ticker].end_ts` (an existing,
previously-never-called 4-source resolver, `services/market_events/
event_schedule.py`) → `occurrence_datetime` (already fetched) →
`close_time` (current behavior, final fallback).

**Done and locally verified (targeted + broad test runs green), NOT YET
COMMITTED as of this doc:**
- `services/market_watch/market_fetch.py` — added `expected_expiration_time`
  to `_MARKET_FIELDS`.
- `services/market_lookup.py` — added `effective_close_time()`, rewrote
  `_close_time_by_ticker()` to use it. Zero call-site changes needed at
  either of its two consumers (`exit_engine.py`'s runway floor, `main.py`'s
  `_validate_fill`).
- `services/market_history.py` — widened `seconds_to_close()` to accept a
  raw float/int (tier 2's `end_ts` is a persisted float, not an ISO
  string) alongside the existing ISO-string parsing.
- New `tests/test_market_lookup.py` (11 tests, all precedence tiers +
  `_close_time_by_ticker` batch behavior).
- `tests/test_market_history.py` — 2 new tests for the float/int
  `seconds_to_close` acceptance.
- Verified: `tests/test_market_lookup.py` + `tests/test_market_history.py`
  (32 passed), `tests/test_strategy_engine.py` + `test_strategy_gate.py` +
  `test_trading_gate.py` (289 passed, confirms no regression from the
  `_close_time_by_ticker()` behavior change), `main.py` still imports
  cleanly, full suite (1,222 passed) green. **This sub-unit is verified
  and ready to commit** — if picking this up fresh, check `git status`
  first; it may already be committed if the session continued past this
  point before a `/clear`.

**Not started yet (the rest of the approved plan):**
1. `services/strategy_engine.py`'s `FollowTheWhaleStrategy.evaluate()` —
   replace the `signal.close_time`-only lookup with a fresh per-tick
   `effective_close_time()` call, falling back to `signal.close_time` when
   the market isn't found (load-bearing for `test_strategy_gate.py`'s two
   existing tests — see the plan file for exactly why). **Import
   `market_lookup` locally inside `evaluate()`, not at module top level** —
   `services/app_state.py` imports `strategy_engine` before its own `state`
   dict exists; a top-level import here crashes the whole process at
   startup. This is a real, verified trap, not a maybe.
2. `services/market_events/event_schedule.py` — build the missing
   `_maybe_resolve_event_schedules(cfg)` / `_resolve_event_schedules_background(cfg)`
   / `_resolve_event_schedules(...)` trio (pattern: `catalog_scan._maybe_scan_catalog_batch`).
   **Second, separate circular-import trap**: `app_state.py` imports
   `event_schedule` at module top level too (for `load_all()`) — same
   "import `state` locally inside the new functions only" fix, independently
   of trap #1 above.
3. `services/app_state.py` — add `"event_schedule_scan": {"running": False,
   "last_started_at": 0.0, "task": None}` to `state`.
4. `main.py` — one new line wiring `event_schedule._maybe_resolve_event_schedules(cfg)`
   into the tick loop's other `_maybe_*` triggers.
5. New `tests/test_event_schedule.py` (zero coverage exists for this module
   today — a genuine pre-existing gap, not just new-code coverage) and new
   `test_strategy_engine.py` tests modeling the live repro directly.
6. `services/market_events/CHEATSHEET.md` — currently has a **false**
   claim that `resolve_one`/`needs_resolution` are already wired into
   `main.py` — correct it once step 2 makes that true; explicitly leave
   noted that `trade_window_is_open`/`is_live` wiring is a separate,
   deliberately out-of-scope gap.
7. `static/status.html` phase entry, `ROADMAP.md` check, commit/push/CI.

The plan file also has 4 explicitly-flagged, non-blocking judgment calls
(a `48h` long-window threshold constant, no independent live-verification
of `expected_expiration_time` values yet, no open-position priority within
a resolution batch, web-search query construction) — read those before
tuning anything, they're deliberate defaults not oversights.

## Also approved this session, not started at all

Two more plans were designed and approved (`ExitPlanMode`) earlier in this
session but haven't been touched yet — same plan file
(`synchronous-honking-shamir.md`) was reused/overwritten for the close-time
plan above, so **the WS-primary-position-data plan's own detail is no
longer in that file** — it lives only in this conversation's transcript
now. Summarizing so it isn't lost:

1. **Trade-channel CPU quantification → real fix.** Instrumentation shipped
   (`8b227fe`) but not yet read for a real multi-minute sample. Direct
   instruction: "just quantify it first" before picking a fix. Once real
   `state["trade_stream_perf"]` numbers are in hand, the fix direction is
   already decided (two direct instructions, not to be re-litigated):
   **discovery-windowed funneling** (exchange-wide visibility only during
   discovery scan cycles, narrower the rest of the time) **combined with
   sharding** (Kalshi's own `shard_factor`/`shard_key`, multiple WS
   connections each taking a consistent-hash slice — full coverage
   preserved, load distributed across real cores). Explicit guardrail,
   stated twice: never scope down to watchlist-only permanently — that
   reintroduces a measured ~98% coverage-loss regression this app already
   fixed once (2026-08-17).
2. **Real-account WS fill/position bugs + fake-fill test harness.**
   Confirmed via direct code reading (not yet fixed): `kalshi_trade_ws.py`
   checks `msg_type == "market_positions"` (plural, the channel name) but
   the real documented `type` field is `"market_position"` (singular) — so
   `_process_stream_position` never fires on a real message.
   `_process_stream_fill` gates on `fill.get("fill_id")`, but the real WS
   `fill` message has no `fill_id` field at all, only `trade_id` — so it
   always no-ops. **The real-account WS path is currently 100% dead**
   despite looking wired. User's own proposed fix for verifying this
   without a real fill: build synthetic fill/position messages (matching
   `docs/kalshi/user-fills.md`/`market-positions.md`'s real documented
   shapes) constructed from real paper-trade data, run them through
   `_process_stream_fill`/`_process_stream_position` directly (pattern:
   `test_trading_gate.py`'s existing `test_process_stream_ticker_...`/
   `test_lifecycle_*` tests, which already do exactly this shape for other
   handlers). Then, once verified, flip both paper `latest_prices` and
   real `state["account"]` from wholesale-REST-every-tick to WS-primary
   with REST reserved for confirm-before-decision only (direct
   instruction, explicit: "i dont need it to be wholesale overwritten
   every 6 seconds... rest api should only be used to confirm decisions
   before theyre made").
3. **Confidence-calibration series-level scoping.** Confirmed real and
   well-evidenced (57,025 resolved signals, 36 series clear a 50-sample
   gate covering 97.5% of volume, real opposite-direction miscalibration
   across series e.g. KXWNBAGAME 92.3% observed vs. KXNFLGAME 51.3%
   observed in the same nominal 60-70% confidence band). Fix: `services/
   signal_log.py`'s `resolved_signals_with_factors()` silently drops the
   already-stored `series` column (services/signal_log.py:509) before
   `confidence_calibration.py` ever sees it — un-drop it, then add a
   grouping wrapper around `confidence_calibration.py`'s existing band/
   discrimination logic (that module's own functions need no redesign).
   Category-level calibration is a **harder, separate problem** — category
   is only captured at trade-open time (`trade_category.py`), giving only
   12.4% ticker coverage of the signal pool; needs new capture at
   signal-log time first, not just a grouping function. `advisory_engine.py`
   doesn't import `confidence_calibration` at all today — zero
   cross-connection between the two.

None of these three have an approved concrete implementation plan written
down yet (1 and 3 need one; 2 has enough detail above to start from
directly). Ask the user which to prioritize next relative to finishing the
close-time fix, rather than assuming.

## Standing reminders that still apply

- `docs/kalshi/` check before touching anything Kalshi-data-shaped (hard
  rule, CLAUDE.md) — already applied throughout this session's fixes.
- `data/*.db` files are live — this session did NOT reset/delete any of
  them; all fixes were verified via read-only live checks or isolated
  test DBs.
- Full suite takes ~130-265s depending on load — background it, don't
  block on it.
