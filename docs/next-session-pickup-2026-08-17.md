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

## CRITICAL, fixed 2026-08-17: fetchJSON never checked HTTP status - every action button's error handling was dead code

Direct report: "the whale calibration tool... doesn't auto apply. doesn't
update its values on the frontend, doesn't actually refine itself over
time. just does nothing." Investigating that one specific panel found a
bug in the single most central helper function in `static/index.html`,
used by essentially every fetch call in the file:

```js
async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  return res.json();       // BUG: never checked res.ok
}
```

`fetch()` only rejects on a genuine network failure - a 400/404/500
response is still a "successful" fetch as far as the Promise is concerned.
This function handed `res.json()` back regardless of status, so **every
`catch(e)` block anywhere in this file that calls `fetchJSON` - and there
are dozens, all written assuming a failed request throws - was silently
dead code for any error the backend reports via its own status code**,
which is how virtually every `raise HTTPException(...)` in `main.py`
communicates failure. A button whose action hit a 400 (disabled feature,
nothing to apply, a stale/not-found recommendation) would read the
response body, find no exception, and run its own success path anyway -
"Applied ✓" on a request that outright failed, the backend's own `detail`
message never even surfaced. Confirmed live and reproducibly via a real
`selenium-chrome` browser session: a `POST /api/confidence-calibration/
apply` call that correctly 400'd with "nothing to apply" still rendered
the button as "Applied ✓".

**Fixed at the root** (`static/index.html`'s `fetchJSON`): now checks
`res.ok` and throws an `Error` carrying the backend's own `detail` message
on any non-2xx response, so every existing `catch(e)` block starts working
exactly as its own code already implied it should.

**That fix has real blast radius - two other call sites were relying on
the old broken behavior and needed fixing in the same pass**, found via
`grep -n "\.detail"` across the file rather than assumed:
- `toggleAutoApply` (the advisory/calibration auto-apply confirmation-phrase
  flow) used to read `result.detail` off a "resolved" 400 body to `alert()`
  the real reason a wrong phrase failed. Under the new contract that 400
  throws instead of resolving, so this needed a `try/catch` wrapped around
  it, reading `e.message` instead - same alert text, now via the correct
  path. **Verified live**: a deliberately wrong confirmation phrase still
  shows the exact `alert("Confirmation phrase did not match...")` text.
- `confirmEnableTrading` - **the real-trading typed-confirmation gate**,
  the single most safety-critical UI flow in this app - had the identical
  pattern (`result.detail` read off a resolved 400 body). Without fixing
  this one, a wrong phrase would have failed *safely* (trading still never
  enables on the wrong phrase - the backend gate was never the broken
  part) but *silently*, with no error text shown and only an unhandled
  promise rejection in the console. Fixed the same way, verified the
  backend invariant itself was never at risk before or after.

**A second, independent bug found in the same investigation**: the
calibration panel (and the advisory panel, same code shape) re-renders its
entire section wholesale (`el.innerHTML = ...`) every 5s while the History
tab is active. A click's own async apply call could still be in flight
when that timer fired, replacing the just-clicked button with a fresh one
before the click's own `btn.textContent = 'Applied ✓'` feedback landed -
mutating a DOM node already detached from the page is not an error, just
silently invisible. Fixed with a simple in-flight guard
(`calibrationApplyInFlight`/`advisoryApplyInFlight`) that skips the
periodic re-render while an apply from that panel is pending. Both fixes
were necessary together - confirmed via a `window.fetch` wrapper logging
real request timing in a live browser session: before both fixes, a click
against real live data (a full `signal_log` table scan, several seconds)
showed neither success nor failure text, ever; after, it reliably shows
one or the other within a few seconds of the request actually resolving.

**Also shipped in the same pass, the actual feature that started this**:
whale-signal calibration had no way for a human to act on a good
suggestion except retyping every factor by hand into the Config tab - the
auto-apply toggle was the only path that ever wrote `suggested_weights`
into the live config, and it's gated behind a typed confirmation phrase
*and* only even checked once per `confidence_calibration.
snapshot_interval_sec` (6h default). Added `POST /api/confidence-
calibration/apply` (mirrors `apply_advisory_recommendation`'s existing
manual-apply shape exactly: always available regardless of
`auto_apply_enabled`, recomputes the report fresh, logs to the same
change-history audit trail as `calibration-manual`) plus an "Apply
suggested weights" button in the panel itself. Verified end to end against
real production data (34,530 resolved signals): applied live, config
updated, change history logged, verified via a real browser click - not
just curl.

Tests: `tests/test_config_store.py` (atomicity, see the settings.yaml
section below) and 5 new route-level tests in `tests/test_trading_gate.py`
covering the disabled/under-threshold/nothing-to-discriminate/success/
repeated-apply cases for the new endpoint, seeded through real
`signal_log` rows (not synthetic dicts) so the HTTP layer is actually
exercised. 1,144 tests passing.

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

**Fixed everywhere**, as of 2026-08-17 (`a44f41d`): the History table, the
main Open Positions table, the mutually-exclusive combo-legs table
(`static/index.html` ~line 3185), and the `market_strategy` panel's own
positions and recent-trades lists (~line 4893, ~line 4914) all now apply
the side-aware `side === 'yes' ? price : 1 - price` inversion.

**Verified in a real browser**, not just HTTP 200 + brace-balancing - a
selenium-chrome session against the live History tab found a real no-side
closed trade (`KXATPMATCH-26AUG17ZVEATM-ATM`, `entry_price=0.2`,
`exit_price=0.18`, `side=no`) and confirmed the rendered row reads "80¢ →
82¢" (matching `cost_basis=$377.60 = 472 × 0.80` exactly), not the raw
"20¢ → 18¢". Same row also confirms the `runway_exhausted`/
`position_netting` close-type fix on live data: it renders "Position
Netting", not "unknown".

## RESOLVED tonight: root cause fixed, live data remediated, verified

Everything below this line was still open as of the stopgap. All of it is
now fixed at the root, not just contained, and 1,132 tests pass.

**1. Root cause fixed.** `services/market_history.py::recent_price(ticker,
max_age_sec, as_of)` returns the most recent REST-polled snapshot within a
freshness window, or `None` (fail-open) if there isn't one.
`strategy_engine.check_exits` now calls it before trusting `current_price`
for a stop-loss/take-profit decision: if a fresh snapshot disagrees with
the websocket-sourced price by more than 0.30, the corroborated value
overrides it. `_PRICE_CORROBORATION_MAX_AGE_SEC = 120`,
`_PRICE_CORROBORATION_MAX_DEVIATION = 0.30` - wide enough to never delay a
genuine large real move (worst case, confirmed on the very next tick once
REST catches up), tight enough to catch the demonstrated 0.99-vs-0.0
failure with enormous margin. The `strategy_overrides.by_category.Sports.
stop_loss_pct: null` stopgap can be removed now that the mechanism itself
is fixed - left in place for now as defense in depth, but it's no longer
load-bearing.

**2. Corrupted data remediated, not just excluded.** Added
`trades.excluded` (same non-destructive idiom as `signal_log.excluded` -
flagged, never deleted) plus
`PaperBroker.correct_erroneous_close(trade_id, corrected_price=None)`,
which reverses a specific close's fabricated bankroll impact and,
optionally, credits what should have been paid at a corroborated price
instead - deliberately NOT a guessed "fair settlement" value, just
`market_history.recent_price` at the close's own timestamp, the same
trusted source the going-forward fix uses. `trade_analytics.
build_trade_history` skips excluded rows entirely (neither a loss nor a
phantom win); `diagnostics.py` and `series_watcher.py`'s close-row queries
now filter `excluded = 0` too, so win-rate/P&L stats stop counting them
everywhere, not just in the History table.

**3. Applied live, through the running server, not a detached script.**
Added `POST /api/admin/correct-trade` specifically so the correction
mutates the SAME in-memory `broker` object the live app is using (a
separate `ddev exec` script would update the DB but leave the live
process's memory stale until restart - the exact desync CLAUDE.md's
`data/*.db` warning describes, in reverse). Called for all three confirmed
trades, corroborated price 0.99 for each (computed independently per
trade, at its own close timestamp):

```
4e5fe02f  KXATPCHALLENGERMATCH-...SAKPOL-SAK   credited $554.01
e7d76da0  KXATPCHALLENGERMATCH-...MARMID-MID   credited $493.66
c6437ce7  KXWTAMATCH-...CIRKAL-CIR             credited $580.72
                                          total: $1,628.39
```

Verified both live (`GET /api/state` bankroll) and on disk (`excluded=1`
on all three rows) agree, and that `build_trade_history` no longer
produces a row for any of the three. A fourth trade sharing the same event
prefix (`KXWTAMATCH-...CIRKAL-KAL`, the *Kalinskaya*-wins contract, closed
at a moderate 0.50 with no 0.99-pinned signature) was checked and
deliberately left untouched - it doesn't show the fabricated-price shape,
and if Kalinskaya's real win probability was genuinely falling as
Cirstea's comeback developed, a moderate stop-loss there is plausibly
legitimate, not a bug.

**4. A second, related display bug found and fixed while auditing every
`close_position()` call site for the same shape:** trades closed via the
`exit_min_seconds_to_close` runway gate (shipped earlier this session) and
via `position_netting.py` had no matching pattern in `trade_analytics.
classify_close_type`, so they silently rendered as `"unknown"` - directly
matching a separate report ("certain trades being closed by 'unknown'").
Both patterns added (`runway_exhausted`, `position_netting`), plus
`runway_exhausted` joins `_EARLY_PROFIT_TYPES` (it's a deliberate early
exit ahead of settlement, same as take-profit/auto-exit/reversal). Verified
live: 0 unknown close types remain in the current account, down from 1.

## CRITICAL, confirmed and stopgapped: stop-loss can liquidate a WINNING position at a fabricated price

Direct instruction to double-check: a real Cirstea/Kalinskaya WTA position
was reported as a loss, and per the user's own real-world account Cirstea
won (comeback in set two, Kalinskaya retired injured in the final set).
Verified against this app's own independent data stores, not assumed:

```
market_history (REST-polled, independent of the exit path):
  22:22:54 -> 22:35:59  yes_price PINNED AT 0.99 for 13+ minutes straight
  22:36:15              yes_price 0.5   (one tick after the close)

paper_broker trade log:
  22:07:58  OPEN   yes  587 ct @ 0.69   "whale print 13743 @ 0.69 (conf 0.8)"
  22:36:14  CLOSE  yes  587 ct @ 0.00   "stop-loss hit: unrealized loss 102%
                                          of cost basis (limit 40%)
                                          (realized -413.82)"
```

The real market believed this position was a near-lock winner (0.99, i.e.
99% implied) for over thirteen minutes, and then, one tick later, the
app's own exit logic recorded the price as exactly **0.0** and liquidated
at a $413.82 loss. This is not a legitimate stop-loss catching a real
reversal - the real market never moved.

**Root cause, found in `services/strategy_engine.py::check_exits`:**

```python
current_price = latest_prices.get(ticker, pos.entry_price)
```

`latest_prices` is `state["latest_prices"]`, written in place by the
websocket `ticker` channel handler (`_process_stream_ticker` in
`main.py`) on every single tick update, with **no corroboration against
any other source** - not against `market_history`'s independently-polled
REST price sitting right there in the same process, not against a moving
average, not against a sanity bound on how far price can move in one
tick. A single garbage/outlier tick - almost certainly a thin, in-play
sports order book briefly presenting a near-zero top-of-book quote - gets
trusted completely and immediately triggers liquidation.

**This is not isolated.** Checked all stop-loss closes at <=5c across
every tennis series tonight: **3 for 3** show the identical shape -
`market_history` pinned near 0.99 for minutes, then an exit at exactly
0.0:

| time (UTC) | ticker | loss |
|---|---|---|
| 21:46:09 | `KXATPCHALLENGERMATCH-...SAKPOL-SAK` | (stop-loss, exit 0.0) |
| 22:01:17 | `KXATPCHALLENGERMATCH-...MARMID-MID` | (stop-loss, exit 0.0) |
| 22:36:14 | `KXWTAMATCH-...CIRKAL-CIR` | -$413.82 (confirmed against a real result) |

**Checked and ruled out as a wider problem, not assumed:** zero crypto
(`KXBTC*`/`KXETH*`) stop-loss closes at <=5c tonight, in the same window,
on far more actively-traded series. This looks like an illiquid in-play
sports order book producing a single degenerate quote, not a universal
parsing bug - consistent with the fix being scoped to Sports rather than
applied globally.

**Stopgap applied** (`config/settings.yaml`,
`strategy_overrides.by_category.Sports.stop_loss_pct: null`) - a one-line,
fully reversible config change, not a code change, chosen deliberately
over touching `check_exits`' core logic this late in an already
error-prone session. Verified live via `GET /api/config` immediately
after saving (also re-confirms tonight's config-reload fix is working).
Take-profit and settlement-based closing are untouched - only the
mechanism that was demonstrably destroying winning positions is disabled,
and only for the one category where it was observed.

**The real fix, for next session, with a clear head:** `current_price`
must be corroborated before it's allowed to trigger a stop-loss -
compare against `market_history`'s most recent REST snapshot for the same
ticker, and either require the two to roughly agree, or require the WS
price to persist across two consecutive ticks before acting on it. Given
`check_exits`' own docstring already documents a near-identical prior
incident (2026-08-11, a same-tick stale-quote fake -21% loss, fixed via
`opened_since`), this general class - "a single untrusted price read can
liquidate a position" - has now caused real damage twice and is worth
fixing at the root rather than patching each new shape of it.

**Also confirmed while investigating:** the unexplained `settings.yaml`
diff flagged earlier tonight is not just sitting there - it is **live and
was actively governing every trade** placed this session, including the
ones destroyed by this bug. `strategy_overrides.by_category` and
`by_series` were both empty (`{}`) before this stopgap, meaning every
position - Sports and Crypto alike - was running on the single global
`strategy.stop_loss_pct: 0.403` (~40%) from that diff, not the
category-tuned values from earlier tonight. Confirmed the "(limit 40%)"
text in the trade reasons above matches `0.403` exactly. This diff is
still uncommitted and still unexplained - see its own section below - but
it is not inert, and that changes its priority for next session from
"investigate when convenient" to "understand this before trusting any
further live trades."

## Answering directly: does the REST->websocket architecture change fix "slow whale stream, decision making, and management"?

Partially, and it's worth being precise about which part. Whale-follow
**signal generation and entry decisions already run off the websocket
trade stream in real time**, not gated by the 6-second tick -
`_process_stream_trade` calls `evaluate()` directly as each trade arrives.
The 6-second REST cadence mainly gates: full market-list rediscovery,
settlement/close detection (see `market_lifecycle_v2` above),
`market_history` snapshot resolution, and live sports game state. So the
proposed architecture change would speed up *discovery and settlement
detection*, not the moment-to-moment whale-signal path, which is already
fast when `trade_stream` is healthy (confirmed tonight:
`dropped_messages: 0`).

**Position management is a separate, more serious problem than latency** -
see the critical finding directly above. The stop-loss bug isn't slow, it's
wrong, and fixing "decision making and management" starts there, not with
websocket migration.

## ARCHITECTURE: most of the 6-second REST cadence has a websocket replacement

Direct point, correctly made: "I don't see why REST API polling should even
be 6 seconds if almost all of this data can be collected via websocket
stream. only position opening and closing." Verified against
`docs/kalshi/` rather than assumed, and the answer is more specific than
"mostly right":

**Already websocket, already working:** `trade` (exchange-wide) and
`ticker` (watchlist-scoped) - live trade flow and price updates. Confirmed
flowing all session (`raw_trades`/`index_ticks` writing every few seconds).

**Real, verified, currently-unused gap:** `market_lifecycle_v2` is a
documented channel (`docs/kalshi/market-and-event-lifecycle.md`) this app
**never subscribes to** - `grep -rn "market_lifecycle_v2" main.py
services/` returns zero hits. It pushes `created` / `activated` /
`deactivated` / `close_date_updated` / `determined` / `settled` /
`price_level_structure_updated` as they happen. Right now, open/close/
settlement detection (`main.py`'s outcome-checking pass) waits for the next
6-second REST poll to notice `result` populated on a market it's still
fetching wholesale - the exact mechanism behind the stale-`close_time` bug
class ROADMAP.md and CLAUDE.md already document, with a direct push-based
fix sitting entirely unused.

**Second concrete gap, ties back into tonight's volatility fix:**
`market_history.record_snapshots` (line ~2963 in `main.py`) is fed
**only** from the REST tick - never from the ticker WS stream already
flowing into `_process_stream_ticker` for `state["latest_prices"]`. That
store is the input to `market_history.volatility()`, measured earlier
tonight at exactly `0.0` for 78% of markets because samples were too
sparse (6s resolution). Wiring the same ticker updates into
`record_snapshots` would raise that resolution for free, using data
already in memory - no new subscription, no new API cost.

**Confirmed, not assumed, no equivalent exists:** there is no websocket
channel for live sports score/game state - `docs/kalshi/get-live-data.md`
and `get-milestone.md` are REST-only, and the full channel list
(`orderbook_delta`, `ticker`, `trade`, `fill`, `market_positions`,
`market_lifecycle_v2`, `multivariate_market_lifecycle`, `communications`,
`order_group_updates`, `user_orders`, `cfbenchmarks_value`, `pyth_value`)
has nothing else close. That piece genuinely has to stay REST-polled - but
there is no reason it needs the SAME 6-second cadence as the rest of the
loop; a score doesn't need sub-10-second freshness the way a trade signal
does.

**Order placement is correctly REST** - `create_order`/`cancel_order`,
no WS order-entry channel exists - and `fill`/`market_positions` already
push confirmations back once real trading is enabled. This part of the
architecture is already right and doesn't need to change.

### Recommended shape, not yet built

Four things currently share one `poll_interval_sec: 6` cadence that have
genuinely different freshness requirements:
1. Price/trade data - already WS, zero latency, done.
2. Open/close/settlement detection - could move to push via
   `market_lifecycle_v2` (new subscription, real work, real payoff).
3. `market_history` snapshot resolution - could move to push via the
   already-flowing ticker stream (smaller change, same payoff class as #2).
4. Live sports game state - genuinely REST-only, but decouple its polling
   interval from the other three; it doesn't need to run at 6s.

**Not started tonight, deliberately** - this touches `trading_loop`,
`kalshi_trade_ws.py` (a new channel subscription), and `market_history`'s
write path across several files, and this session already produced two
real incidents (a wrong config override that opened and lost money on a
real position, and a broken feed rehydration reverted twice) from moving
on partial verification under time pressure. The research above is real
and doc-verified; the implementation is not - do it with a clear head,
one piece at a time, suite green between each, starting with #3 (smallest,
lowest-risk, and directly reinforces tonight's volatility fix).

## RESOLVED 2026-08-17: per-phase tick timing instrumentation shipped

Direct request: "find the bottleneck in the whale stream (data
transmission vs analysis vs decision vs opening vs management vs
exiting)." Was measured once at `last_tick_duration_sec: 19.63` against a
6s `poll_interval_sec` with 3 rate-limit hits and 60 markets watched, but
**could not be attributed to a specific phase** - `trading_loop` had no
per-phase timing, only one number for the whole tick.

**Fixed** (`main.py::trading_loop`, `services/app_state.py`): 8
checkpoints now wrap the tick end to end - `market_fetch`,
`resolve_and_record`, `calibration_advisory`, `market_strategy`,
`event_and_tradetape_fetch`, `capture_flush_and_titles`,
`signal_and_entry`, `exit_management` - each storing its own elapsed
seconds into `state["tick_phase_timings"]`, exposed on `GET /api/state`
alongside the existing `last_tick_duration_sec`. Declared outside the
tick's `try/except` so a mid-tick exception still leaves whatever phases
completed visible instead of losing the whole breakdown.

**Verified live**, not just unit-tested: sampled 5 consecutive real ticks
via `curl /api/state`. Current watchlist (~31-33 markets, well below the
60-market state that produced the 19.63s tick) shows no rate-limit hits
and phase sums matching `last_tick_duration_sec` within ~0.2s (the
untimed remainder is `config_performance.record_variant` +
`series_evaluator.evaluate_pending` + `KalshiClient` construction at the
very top, plus `client.close()` in `finally` - all sub-millisecond,
deliberately left uninstrumented). `market_fetch` and `resolve_and_record`
dominate every sampled tick (2.2-3.1s and 0.4-1.3s respectively) with
`event_and_tradetape_fetch` consistently small (0.001-0.7s) - the opposite
of what the REST-vs-websocket architecture research below guessed would
be the bottleneck. **Next time the tick blows out again (watchlist back
up near 60+), read `tick_phase_timings` first** rather than re-guessing;
this is now a one-`curl` answer instead of a re-investigation.

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
once watchlist growth reproduces the blowout again and `tick_phase_timings`
(now shipped, see above) confirms `market_fetch` is still the dominant
phase at that size. **`_fetch_live_status` under the widened 12h lookahead
is already ruled out**, not just deprioritized - it lives inside the
`event_and_tradetape_fetch` phase, sampled at 0.001-0.7s across 5 live
ticks while `market_fetch` ran 2.2-3.1s on the same ticks. Whatever was
driving the 19.63s tick, it wasn't that.

## RESOLVED: the "unauthored" settings.yaml diff, plus a real race it exposed

Explained by `1eba8f5` (same night, committed before this pickup doc's
first draft was finished): `ConfigStore.reload()` ran once at construction
and nothing ever called it again, so the running process silently kept
serving whatever config it loaded at startup while every file edit -
dashboard save included, since `update()` writes the same file `get()`
never re-read - was invisible to it until a restart. The "unauthored"
values were real, intended config; the process just never picked them up,
so every diagnostic run that session was describing a configuration the
live app was not actually running. `get()` now stats the file and re-reads
on mtime change, closing the gap.

That fix widened who reads `settings.yaml` live, which surfaced a second,
smaller, genuinely new bug on 2026-08-17: `ConfigStore.update()` wrote via
a direct `open(path, "w")` - truncate-then-write, not atomic - so a
concurrent reader (this app's own `get()`, or a separate process touching
the same bind-mounted file, e.g. a `ddev exec` test run) could catch a
torn, partially-written file mid-flight. Caught live: a background task's
`cfg["kalshi"]["base_url"]` raised `KeyError` immediately after a dashboard
config save ran. Fixed the same way this class of bug is always fixed -
write to a temp file in the same directory, then atomically replace the
real path (`services/config_store.py`, tests in
`tests/test_config_store.py`) - so any reader now sees either the complete
old file or the complete new one, never a partial write in between.

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
