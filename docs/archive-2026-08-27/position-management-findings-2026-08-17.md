# Position management — findings, 2026-08-17

Follow-up to `docs/next-session-pickup-2026-08-17.md`. That one covers
signal *selection*; this one covers what happens to a position after it
opens, prompted by the direct report that "position management right now is
frankly broken" and that "candlestick or volatility data isn't being
factored in at all even though it suggests it does."

Both parts of that report were correct. Here is what was actually wrong.

---

## 1. The volatility factor was a constant, not a discriminator — FIXED

`strategy_engine._exit_confidence` scales the auto-exit's gain/loss
references by how volatile the market normally is:

```python
vol_ratio = max(0.25, min(4.0, vol / normal_vol))
gain_ref *= vol_ratio
loss_ref *= vol_ratio
```

Measured live across 183 well-sampled markets:

| | value |
|---|---|
| markets with volatility **exactly 0.0** | **142 / 183 (78%)** |
| median volatility | 0.000000 |
| p90 | 0.00216 |
| max | 0.18086 |
| configured `auto_exit_normal_volatility` | **0.1** |

Two compounding problems:

**The configured "normal" was ~50× too high.** At a median volatility of
0.00192 across the *well-sampled* subset, `vol / 0.1` never came close to
1.0, so the ratio pinned to its `0.25` floor for 97% of markets.

**No config value could fix it**, because a zero numerator clamps to the
floor regardless. `market_history.volatility()` returns `None` when there
aren't enough snapshots but a real `0.0` when there are and the price never
moved — and a price that hasn't ticked in 30 minutes usually means nobody
is trading it, not that the market is genuinely placid.

The effect: `gain_ref` and `loss_ref` were **permanently quartered** on
essentially every position. With `auto_exit_gain_reference_pct: 0.95` the
effective reference was 0.2375, so `pnl_factor` saturated at a ~24% move
instead of the configured ~95%. The auto-exit believed nearly every
position was sitting at a P&L extreme.

None of this was visible from config — every individual knob looked
reasonable. Only the *distribution of the resulting ratio* showed it.

**Fixed** by treating `vol == 0` as "no reading" and falling back to a
neutral `1.0`, which is exactly what that block's own comment already said
it intended. `auto_exit_normal_volatility` also moved `0.1 → 0.002` (the
measured median) in all three scopes, so the factor can actually
discriminate among markets that do move. Covered by two regression tests:
one asserting a flat market scores identically to one with no reading,
one asserting real volatility still changes the outcome.

### Still open: `position_netting.normal_volatility`

Left at `0.02` deliberately. It feeds a different formula
(`position_netting.py:246`) that was not traced, and the same measurement
suggests it is likely mis-scaled the same way — but changing it blind would
be guessing. **Trace that formula, then re-measure.**

---

## 2. Candlestick data is genuinely unused — OPEN

Confirmed by search: `candlesticks` appears only in
`kalshi_client.get_candlesticks` and a display-only
`GET /api/markets/{ticker}/candlesticks` route. **Nothing in position
management reads it.**

Meanwhile `_fetch_event_live_data` was found returning six live entries
whose crypto payloads carry per-interval OHLC candlesticks *and* an
underlying price timeseries — fetched every tick, and until today living
only in memory.

`services/game_state.py` now persists those payloads whole, so the history
starts accumulating immediately. **Nothing consumes it yet.** That is the
open work: the OHLC series is a far better volatility estimate than
`market_history.volatility()`'s snapshot-delta proxy, which is exactly the
thing measured above as returning 0.0 for 78% of markets.

**Concrete next step:** derive volatility from the captured candlesticks
(true range over the 15M buckets) for markets that have them, falling back
to the snapshot proxy only where they don't. That replaces a measure which
is structurally zero most of the time with one that is real.

---

## 3. Consolidation — the actual design problem

There are currently **four independent exit paths**, and they were built at
different times against different assumptions:

| path | trigger |
|---|---|
| `take_profit_pct` / `stop_loss_pct` | fixed % of cost basis |
| `auto_exit_*` (7 weights + 5 references) | composite confidence score |
| `exit_on_sentiment_reversal` | whale lean flip |
| `exit_min_seconds_to_close` | runway exhausted |

They do not share a scale, a definition of "decisive," or a common view of
the position. The auto-exit alone has twelve tunable knobs, and the finding
above is a direct consequence of that surface area: a single mis-scaled
constant silently disabled one factor for months and nothing surfaced it.

The direct request was that this be "consolidated and tuned to fit the
whale watching strategy." That is a design task, not a patch, and it should
follow the measurements rather than precede them — specifically it should
wait on `settlement_edge`'s verdict, because if the index projection turns
out to be predictive then the exit logic for crypto series changes shape
entirely (see the pickup doc's item 3).

**Suggested order:** finish the candlestick-based volatility (§2), let the
settlement-edge verdict land, then collapse the four paths into one scored
exit with a single reference scale.

---

## 4. Config UI decimal limits — could not reproduce

Reported: settings "insist on round numbers in .05 increments max and
anything 3 digits or more is counted as invalid."

Checked against the HTML the browser actually receives:

- 57 float inputs carry `step="any"` — no increment constraint
- 14 carry `step="1"`, and all are genuinely integer (counts of markets,
  signals, resolved trades, contracts)
- no `type="range"` inputs, no `pattern` attributes anywhere in `static/`
- the save path reads every float field with `parseFloat`, no rounding
- `cache-control: no-store, must-revalidate`, and the served file's
  `last-modified` is current — so this is not a stale cached page

`step="any"` shipped in `4469b6f` today, so a browser tab opened before
that would still hold the old markup. If it persists after a hard refresh,
**the specific field name would pin it down immediately** — the constraint
is not in the served markup as it stands.

---

## 5. Asked for during this session and NOT delivered

Recorded explicitly because several of these were requested directly and
then displaced by whatever was found next. Nothing here is blocked; it was
simply not reached.

### Never started

- **A backtesting arsenal.** Direct request: "create an arsenal of
  backtesting systems and diagnostic tools to make our efforts more
  efficient and effective." The *diagnostic* half was built extensively
  (`diagnostics`, `series_watcher`, `settlement_edge`, `trade_archive`,
  `/api/health/pipeline`). The *backtesting* half was not built at all.
  `services/backtest.py` exists and predates this session; it was never
  reviewed, extended, or verified. This is the largest outstanding request.
- **The ignored-vs-oversensitive trade comparison.** Direct request:
  "compare trades that ignored all this stuff and the trades that were way
  too sensitive," with the explicit caveat "only if the trades during that
  observation period were real whales." `selectivity_curve` covers part of
  the ground (accuracy vs threshold, normalised to ≥$2,500 prints) but is
  not that comparison — it never contrasts the two behavioural extremes
  against each other.
- **Danger Zone frontend UI.** Backend has been ready since `f4021fd`.
- **`market_lifecycle_v2` and `orderbook_delta` websocket channels.** Both
  recorded in `docs/kalshi/CHEATSHEET.md` as unused and relevant —
  lifecycle would replace REST polling for open/close/settlement (directly
  relevant to the stale-`close_time` bug class), orderbook_delta would give
  true depth instead of the sampled top-of-book `series_watcher` records.

### Investigated, then left unfixed

- **Live-status window bounds.** `_LIVE_STATUS_LOOKAHEAD_SEC` (1h) and
  `_LIVE_STATUS_LOOKBACK_SEC` (6h) were measured excluding 5 of 6 sports
  markets from live-status tracking. Root-caused, never fixed.
- **The four-entry gate bypass.** See the pickup doc, item 2. The
  `01b126c` invariant makes the class unreachable, but the path is unknown.
- **`position_netting.normal_volatility`** — §1 above.
- **Advisory suggestion validity.** `config_bounds.clamp()` was wired into
  `advisory_engine._exit_pct_recommendation` after the report that "the
  advisory gives suggestions for values that are apparently invalid," but
  the live advisory output was never re-checked afterwards to confirm the
  suggestions it now emits are actually accepted by the config UI.

### Known unread Kalshi data

All flagged during the 2026-08-17 API audit, none acted on:

- `fee_waiver_expiration_time` — fee-inclusive P&L drives stop-loss and
  take-profit triggering, so on a waived market the app overstates cost and
  **stops out early**. Smallest change with direct P&L correctness impact.
- `services/kalshi_fees.py` models only the trade fee. `fee_rounding.md`
  defines net fee as trade fee + rounding fee − rebate, so the function is
  a **lower bound**, not the net.
- `settlement_timer_seconds` — now read by `index_feed.settlement_spec`,
  but still not used by the runway gates, which reason about *close* rather
  than about when a position actually realises. Verified to vary widely: 1s
  on the 15-minute series, 60s on dailies, 1800s on KXXRPD/KXDOGED, 3600s
  on KXBTCMAXY.
- `early_close_condition` — the app applies one blanket grace period to
  every `can_close_early` market instead of reading the actual condition.
- `latest_expiration_time`, `expiration_value` — zero references.

### Process notes

- `static/status.html` phases 113–114 were hand-written rather than via the
  `/sync-status-docs` skill. The content matches the house format, but the
  skill exists and is the intended path.
- WebSocket **sharding** (`shard_factor`/`shard_key`, documented in
  `CHEATSHEET.md`) has never been exercised. Not needed at current volume —
  the reader/worker split in `kalshi_trade_ws.run()` handles today's
  exchange-wide load — but it is the next lever if one connection stops
  keeping up.
- The **`ticker` channel remains watchlist-scoped** by choice.
  `market-ticker.md` permits going exchange-wide, but that fires on every
  field change for every market. Revisit only with a concrete need.
