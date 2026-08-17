# Session pickup — 2026-08-17

Written so the next session can start acting instead of re-deriving. Every
number here was measured against real data on this date and is reproducible
from the commands noted; nothing in this file is an estimate.

**Read this before `ROADMAP.md`** — the roadmap says what's open in general,
this says what was just learned and what to do with it first.

**Companion doc:** `docs/position-management-findings-2026-08-17.md` covers
everything *after* a position opens (the volatility factor that was a
constant, the unused candlestick data, the four un-consolidated exit paths)
plus a full list of what was asked for this session and not delivered —
most notably the **backtesting arsenal**, which was requested and never
started.

---

## The one finding that reframes everything

**A dollar-denominated whale threshold is geometrically biased toward
near-certain prices, and that bias is the dominant cause of poor
performance.**

$2,500 buys 125,000 contracts at 2c but only 2,505 at 99.8c. So a fixed
dollar gate is far easier to clear the more expensive the contract is.
Measured across 145,785 real captured prints:

| unit cost | prints | clear $2,500 | clear rate |
|---|---:|---:|---:|
| 0.00–0.02 | 4,924 | 0 | 0.00% |
| 0.02–0.20 | 27,371 | 0 | 0.00% |
| 0.20–0.50 | 46,774 | 1 | 0.00% |
| 0.50–0.80 | 40,187 | 7 | 0.02% |
| 0.80–0.95 | 15,504 | 6 | 0.04% |
| 0.95–0.98 | 6,554 | 6 | 0.09% |
| 0.98–1.01 | 4,471 | 38 | **0.85%** |

**Of the 58 prints that clear $2,500, 44 (75.9%) sit at unit cost ≥ 0.95** —
the band measured at −2.4% per dollar risked. Mean unit cost of everything
the dollar gate selects: **0.926**.

The whale filter was never finding informed traders. It finds whoever buys
near-certainties in size, because that is the only way to spend $2,500 on a
single print.

This single fact explains the chain of symptoms chased all session: why
40.7% of logged signals sat ≥0.95, why headline "whale accuracy" read 86.3%
when the tradeable truth was 77.5%, why realised win rate collapses to 47%,
and why `series_watcher.reconcile()` attributes −30pts to *selection*.

### The candidate fix, measured but NOT applied

| selector | prints | mean unit cost | ≥0.95 | in 0.65–0.80 |
|---|---:|---:|---:|---:|
| DOLLARS ≥ $2,500 *(current)* | 58 | 0.926 | 75.9% | 8.6% |
| CONTRACTS ≥ 2,000 | 218 | 0.342 | 23.4% | 4.1% |
| CONTRACTS ≥ 5,000 | 69 | 0.440 | 36.2% | 4.3% |
| **CONTRACTS ≥ 5,000 + tradeable range** | 11 | **0.759** | 27.3% | **27.3%** |

Deliberately left unshipped: this changes what the application *considers a
whale*, which is a strategy decision, not a bug fix. It was measured and
handed over rather than slipped in.

**If picking this up:** add `whale_watcher_kalshi.min_contracts` alongside
the existing `min_notional_usd` in
`services/whalewatchers/kalshi_trade_tape.py::_process_trades_sync` (the
gate lives right after `min_notional_for`). Keep both, applied with AND, so
the change is reversible by config and the old behaviour is one edit away.
Reproduce the table above first — the sample is small (11–218 prints) and
`data/series_watcher.db` has grown since.

---

## URGENT, fixed this session: no-side wins displayed as losses

Direct report: "wins are showing up as losses (0c exit when the result is
100c)." Confirmed against the 5 most recently closed positions, not
guessed — 4 of 5 were `yes`-side and rendered correctly by coincidence; the
5th exposed it exactly:

```
KXATPMATCH-26AUG17FERDE-FER   side=no   close_type=settled_win
  displayed (bug):    entry 22¢  ->  exit  0¢     looks like a wipeout
  actual (side-aware): entry 78¢  ->  exit 100¢    what really happened
  cash_back=$466.00   realized_pnl=+$96.92
```

`entry_price`/`exit_price` are correctly stored in the yes-price
convention (`WhaleSignal.price`'s documented meaning) - the DATA was never
wrong. `static/index.html`'s Trading History table rendered them raw
instead of applying the same side-aware inversion `cost_basis`/`cash_back`
already use two columns over, so a `no`-side win (yes-price settling to
0.0) displayed as "0¢", indistinguishable from a total loss. Same bug class
CLAUDE.md's "a displayed value must match its label" section already
documents once, in a different spot.

**Fixed**, in the History table and in the main Open Positions table (same
bug, same fix, found while checking for siblings). **NOT yet fixed** in two
lower-traffic spots found during the same sweep, left for next session
rather than rushed:
- Mutually-exclusive combo-legs table (`static/index.html` ~line 3174,
  `${(m.entry_price*100)}¢ → ${(m.current_price*100)}¢`)
- `market_strategy` panel (~line 4877) - lower priority, that strategy is
  `enabled: false` by default

**Not verified in a real browser** - the `selenium-chrome` container
crashed mid-session (Chrome binary crash, unrelated to this change) and
wasn't retried under time pressure. Confirmed instead: HTTP 200 on the
served page, brace-balance check on the edited region, and the fix mirrors
an already-proven pattern used elsewhere in this exact file. **Do a real
Selenium pass on the History and Positions tabs before trusting this
fully.**

## URGENT, not yet actioned: tick duration blew out, rate limiter tripping

Direct request: "find the bottleneck in the whale stream (data
transmission vs analysis vs decision vs opening vs management vs
exiting)." Measured, not guessed, in the last few minutes of this session:

```
last_tick_duration_sec:  19.63    (poll_interval_sec is configured at 6)
last_tick_rate_limit_hits: 3      (was 0 for essentially the entire session)
markets watched: 60               (was 179 a few hours earlier)
```

A tick running 3x+ its own configured interval, now actually tripping the
rate limiter, is real operational degradation - not a display artifact,
not a stale-feed illusion. **This could not be attributed to a specific
phase** (fetch/analysis/decision/open/manage/exit) because
`trading_loop`/`_fetch_markets` have no per-phase timing instrumentation -
`last_tick_duration_sec` is one number for the whole tick. Guessing which
`asyncio.gather()` block dominates would have been exactly the kind of
unverified claim this session got burned by twice already, so it wasn't
guessed.

**Concrete first step for next session:** wrap each major phase in
`trading_loop` (market fetch, event/live-status fetch, trade-tape/signal
generation, strategy evaluate, exit checks, account sync) in its own timer
and store them as `state["tick_phase_timings"]` alongside the existing
single number. That turns "the tick is slow" into "phase X is slow,"
which is the actual answer to "find the bottleneck."

### The other half of the request: make the watchlist size configurable

Direct request: "I don't need to be checking against 50 or 100 different
markets all at once, we need to scale down the market watchlist. but make
that highly configurable." `kalshi.watchlist_size` (currently 150) and
`kalshi.top_series_per_category` (currently 30) already exist and are
config-reloadable as of this session's `config_store` fix - lowering
`watchlist_size` is a one-line, already-available, zero-code-change
mitigation. What is genuinely missing, per "highly configurable": there is
no per-category watchlist cap (Sports and Crypto currently share one global
number) and no dashboard control for it - both real gaps, worth building
once the phase-timing data above says whether watchlist SIZE is actually
what's driving the 19.63s tick, versus something else entirely (a slow
`_fetch_live_status` under the widened 12h lookahead from earlier this
session is a real candidate worth ruling out first).

## Unexplained: an uncommitted settings.yaml diff, not authored by me

Found via `git status` at end of session - a working-tree diff to
`config/settings.yaml` that neither I nor, as far as the record shows, the
dashboard's Config tab produced. Values include
`entry_threshold: 0.4455`, `close_window_sec: 10599.6`,
`special_market_min_seconds_to_close: 218.7`,
`take_profit_pct: 0.459`/`stop_loss_pct: 0.403`, reworked
`whale_confidence_weights`, and both `strategy_overrides.by_category` and
`by_series` wiped to `{}` - the Sports and KXBTC15M overrides gone
entirely.

Ruled out, not assumed: `config_performance.applied_changes` has no
corresponding rows in the last 30 minutes (a `config_store.update()` call -
the dashboard's own save path - always logs there), and both
`auto_apply_enabled` flags (advisory, confidence_calibration) are `false`.
A `config_store.update()` call would be logged; a direct edit to the file
would not be, by design, regardless of who or what made it. The odd
fractional values read like optimizer/sweep output, not hand-typed numbers.

**Left uncommitted and untouched** - not reverted, not applied, not
guessed at. `git diff config/settings.yaml` shows the exact change before
deciding whether to keep it, and it's worth confirming who/what wrote it
before either committing or discarding.

## Left running unattended from 2026-08-17 — verified safe

Checked before walking away, and two things were found and fixed in the
checking rather than assumed:

- **`prune()` was written and never called from anywhere.** `data/` was at
  841MB (`market_history` 581MB, `series_watcher` 130MB after a few hours of
  exchange-wide capture, `game_state` 32MB within *minutes* of first
  writing, because a crypto live-data row carries a whole candlestick array
  plus a price timeseries). Now swept hourly by
  `main._maybe_prune_capture_stores`, honouring
  `series_watcher.retention_hours` (168).
- **All three prune paths then executed for real**, not just reviewed —
  `series_watcher`, `index_feed`, `game_state` each returned cleanly (0
  deletions, correct: nothing was 168h old yet).

**Disk: 923G free, 4% used.** At the observed rate a week is ~10–20GB, so
this is nowhere near a constraint — which also de-prioritises
`market_history.db` (581MB, unbounded, retention never traced). Look there
only if disk ever does get tight.

What the sweep deliberately never deletes: `raw_trades` (one row per real
exchange print, and the dataset any re-analysis of the whale threshold
depends on) and `index_feed`'s settlement-window rows
(`q15_window_size IS NOT NULL`, the paired observations `settlement_edge`
scores). Only high-churn sampled series are trimmed.

Safe to leave: paper mode, `kalshi_account.trading_enabled: false`.

### First three commands on return

1. `GET /api/health/faults` — a large `count` or recent `last_seen` means
   something is failing **silently, right now**
2. `GET /api/health/pipeline` — row counts and last-write age per store
3. `GET /api/diagnostics/settlement-edge` — a week should move it off
   `insufficient`, which decides whether the index projection is real

A week of accumulation should make both headline open questions answerable
on real data: the settlement-edge verdict, and whether a contract-count
whale threshold beats the dollar one.

## FIRST UI TASK: an empty feed must not look identical to a dead one

Direct report, twice in one session: "no whale signals are coming in! its
broken" and "I see nothing in the UI, no positions opened, no decisions
made, nothing." **The pipeline was healthy both times.** Measured while the
feed sat visibly empty: 1,096 signals in signal_log over 24h, the provider
reading 100/100 trade sides correctly, and 87 `min_notional` rejections in
ten minutes.

Three things compounded to make working look broken:

1. `state["signal_feed"]`, `state["decision_feed"]` and `state["stats"]` are
   **in-memory only** and start empty on every process start.
2. `uvicorn --reload` restarts on every `.py` edit, so they were wiped
   constantly during development.
3. The genuine whale-print rate is **~17/hour exchange-wide** — one every
   ~3.5 minutes — so a quiet ten minutes after a restart is normal.

**Do NOT fix this by rehydrating the feed from signal_log.** That was tried
on 2026-08-17 and reverted within minutes (`47b7fec`): it hand-rolled the
signal dict instead of matching `WhaleSignal.to_dict()` (dropping `id`,
`factors`, `raw_context`, `close_time`) and replayed historical signals
whose tickers had rotated out of the watchlist, so `market_titles` resolved
none of them and the feed showed **"unknown market" on everything**. If it
is attempted again, build the dict from a real `WhaleSignal` and only
replay tickers still in `state["markets"]`.

**The right fix is to show the rate, not the history.** An empty feed should
read as *"quiet — 17 signals/hr, last one 4 min ago, 87 prints rejected on
min_notional in the last 10 min"* rather than as silence. Every one of those
numbers is already available (`signal_log`, `candidate_log`,
`GET /api/health/pipeline`); nothing new needs collecting.

### Why KXBTC15M specifically looks dead — it isn't

Direct expectation: "at a MINIMUM I expect to see KXBTC15M whale signals
coming in." It produces **~10/hour, 470 in 24h**. Measured over one hour:

| KXBTC15M, 1 hour | |
|---|---|
| prints captured | **49,947** |
| median notional | **$5** |
| p90 notional | $72 |
| max notional | $9,699 |
| clearing $2,500 (current) | 20 — 0.04% |
| clearing $1,000 | 71 — 0.14% |
| clearing $500 | 282 — 0.56% |

This series is retail micro-flow: fifty thousand prints an hour at a median
of five dollars. `min_notional_usd: 2500` therefore keeps the top 0.04%.
That is a deliberate threshold, not a fault — but if visible signal flow on
this series matters more than selectivity, **$1,000 gives ~71/hr and $500
gives ~282/hr**, and both are one config edit.

Pair any such change with the dollar-vs-contract-count finding below: a
dollar threshold at *any* level is biased toward near-certain prices, so
lowering it admits more flow without removing that bias.

## Start here next session: `GET /api/health/faults`

`services/fault_log.py` now records every swallowed exception and edge
case, deduplicated with a count and the first traceback. A large `count` or
a recent `last_seen` means something is failing **right now, silently**.

It exists because that failure mode already cost real data the same day:
`game_state` shipped with `event_type` added to its CREATE TABLE but no
guarded ALTER, so every INSERT raised on a pre-existing table, `flush()`
caught it, and the store sat at 0 rows looking exactly like "no games are
on." Found only by manually calling `flush()` and reading the return value.
Fixed (guarded ALTER + the fault log + a printed message), but the lesson is
the point: **a capture layer that fails quietly is worse than one that fails
loudly.**

Also check `GET /api/health/pipeline` — row counts and last-write age for
every store, plus `faults_last_24h`. That one call answers "is it actually
producing," which is a different question from "is it running."

### Data capture verified working between sessions

| store | status |
|---|---|
| `raw_trades` | 179k+ rows, 8 series, writing every ~5s |
| `book_snapshots` | writing, 5s sampling |
| `index_ticks` | 13k+, 2.00/sec across BRTI + ETHUSD_RTI |
| `settlement_observations` | writes only in the final minute before a quarter-hour (expected gaps) |
| `game_states` | **was silently broken**, now writing |
| `signals` / `rejections` | writing; `min_notional_usd` dominates rejections at ~3.6k/hr |

Live-status lookahead widened **1h → 12h** (lookback 6h → 8h): 30 Sports
events were on the watchlist with only ONE live-status entry, because a
game scheduled for 13:35 is ~7.5h away at 06:00. No sports score/period was
being captured at all. Bounded by the existing 5-minute repoll cache and
10-per-tick cap, so this lengthens the rotation rather than multiplying API
calls.

**Note:** `_EVENT_LIVE_DATA_EXCLUDED_CATEGORIES = {"Sports"}` — sports game
state comes from the *milestone* path, not the crypto live-data endpoint.
Crypto events carry OHLC candlesticks + a price timeseries there instead.

## Shipped this session (all pushed, CI green, 1,118 tests)

| commit | what |
|---|---|
| `8690599` | Sports category override |
| `2974e42` | entry/exit runway gates (`min_seconds_to_close`, `exit_min_seconds_to_close`) |
| `e6913ee` | `services/diagnostics.py` |
| `737f535` | `services/data_quarantine.py` |
| `4469b6f` | `services/config_bounds.py` + config-UI step fix |
| `89334aa` | `taker_side` deprecation migration |
| `c081a9f` | `services/series_watcher.py` — capture + accuracy-vs-win-rate reconcile |
| `3c3ff67` | exchange-wide trade subscription + on-demand market resolution |
| `cf842b7` | `min_unit_cost` 0.50 → 0.65 |
| `3193843` | `services/index_feed.py` (CF Benchmarks settlement stream) + `services/trade_archive.py` |
| `2273b1e` | `services/settlement_edge.py` |
| `069376a` | `services/app_state.py` + `routers/diagnostics_routes.py` |
| `e621874` | two settlement-observation bugs |
| `01b126c` | strict price parsing + 0.02–0.98 tradeable invariant |

### Diagnostic surface now available

- `GET /api/diagnostics` — all offline checks, incl. `series_funnel:<SERIES>`
- `GET /api/diagnostics/series/{series}` — funnel, reconcile, book context
- `GET /api/diagnostics/settlement-edge` — Brier: projection vs market
- `GET /api/diagnostics/coverage` — exchange-wide coverage (makes API calls)
- `GET /api/index` · `GET /api/index/settlement/{ticker}`
- `GET /api/archive/epochs` · `/compare` · `POST /api/archive/snapshot`

---

## Do these next, in this order

### 1. Make the diagnostics epoch-aware — *blocking everything else*

`check_price_band_adherence` and `check_threshold_integrity`
(`services/diagnostics.py`) judge 24h of history against **today's** config.
Config changed at 08/17 01:33, so both report `FAIL` on trades that were
compliant when placed. Chasing that cost real time this session:
`price_band_adherence` reported 72% out-of-band; judged against the band
actually live at each trade's timestamp it was **4 of 39**.

`performance_by_epoch` already binds trades to config epochs via
`config_performance.applied_changes` — reuse that. Until this lands, every
number these two checks produce is untrustworthy, so do it first.

### 2. Find the 4-entry gate bypass

Four real entries at unit costs 0.97, 1.00, 0.20, 0.97 (08/16 21:26–22:25,
all KXBTC15M), one carrying `conf 0.25` against a 0.495 threshold — so they
bypassed the price band *and* the confidence gate.

Ruled out: prices in the reason string match the recorded prices (so the
gate saw the real input); `shadow_mode` (separate DB); `market_strategy`
(different reason format). `evaluate()` is the only producer of that reason
string and its gates precede the write. **Not yet explained.**

The window coincides exactly with the deliberate $1 `min_notional`
experiment (08/16 20:36–23:19). The `01b126c` invariant makes the class
unreachable going forward, but a gate that *can* be skipped is worse than no
gate, so the path still wants finding.

### 3. Decide the settlement-projection verdict

`services/settlement_edge.py` is recording. Check
`GET /api/diagnostics/settlement-edge`; it reports `insufficient` until
enough windows resolve.

- If **projection beats market**: the follow-on is real. `min_seconds_to_close: 300`
  currently refuses entries in the final five minutes — exactly the window
  where settlement is partly *known*. That gate was correct for a blind
  system and needs revisiting for one that isn't.
- If **market beats projection**: say so plainly, drop the trading ambition,
  keep the capture as a diagnostic. The negative result is worth as much.

### 4. Re-measure everything after 24h on the new config

The `min_unit_cost` 0.65 change and the tradeable invariant both landed
today. Every current statistic is dominated by pre-fix history. Take a
`POST /api/archive/snapshot` now to mark the boundary, then compare epochs
with `GET /api/archive/compare`.

### 5. Finish the `main.py` split

`services/app_state.py` + `routers/diagnostics_routes.py` established the
pattern (5,415 → 5,124 lines). The remaining ~69 routes are mechanical by
the same recipe. The hard half is `trading_loop` (605 lines),
`_fetch_markets` (258), `_fetch_live_status` (204) — real entanglement with
tick ordering and shared state. One at a time, suite green between each.

---

## Standing context worth not re-deriving

- **The 70%/70% target is a hard commandment** — see the top of `CLAUDE.md`.
  A win rate is meaningless without the mean entry unit cost beside it,
  because for a binary contract EV per contract is exactly `p − c`, so
  **breakeven accuracy IS the entry price**.
- **The remembered "70% era" was the no-side cost bug.** 96% win rate was
  real (buying 98c near-certainties); the P&L was a median **49×**
  understatement of cost. Reproduce the win rate, never those P&L figures.
- **Real tradeable whale accuracy is 77.5%**, not 86.3%. The difference was
  262 of 643 priced signals (40.7%) sitting outside 0.02–0.98 and resolving
  "correct" 99.2% of the time.
- **Only one price band has positive expectancy**: 0.65–0.80 at +2.2% per
  dollar risked. 0.50–0.65 is −35.2%; ≥0.95 is −2.4%.
- **This codebase has now paid three times for the same bug shape** — a
  missing value silently becoming a plausible one (`taker_side` → `"no"`,
  `cost = size*price` without the no-side inversion, `price or 0` → 0.0).
  Treat any `or 0` / `else <default>` on API-sourced data as suspect.
- **Kalshi genuinely sends extreme prints.** 145,152 captured: zero missing
  prices, zero at exactly 0.00/1.00, but 3.3% at ≤1c or ≥99c on the
  deci-cent grid. They are real; they are just untradeable.
- **The index feed is not a signal, it is partial knowledge of the outcome.**
  KXBTC15M settles on the mean of sixty one-second BRTI observations;
  `cfbenchmarks_value` streams that mean as it accumulates. Verified live: at
  29 seconds before close, 31 of the 60 were already known.
