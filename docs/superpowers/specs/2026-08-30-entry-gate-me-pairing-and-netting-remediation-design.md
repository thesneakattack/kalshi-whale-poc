# Entry-gate ME-pairing coverage gap, position_netting fee visibility, and milestone coverage — design

Status: drafted 2026-08-30, pending user review. Architectural path
(superpowers:brainstorming) — touches strategy/entry-gate code, which
CLAUDE.md requires explicit design approval for before implementation.

## Origin

Investigating `docs/open-decisions.md`'s "`position_netting` closes lose
money at scale" line (34+ closed trades, real dollar loss, not concentrated
in the previously-flagged 0.60-0.95 price band) surfaced a root cause
upstream of `position_netting.py` itself, plus two related, separately-
verified gaps. All three are documented here together per direct
instruction, since they surfaced from one investigation, but they are
independently shippable — see "Sequencing" below.

## Part 1 — Entry-gate ME-pairing coverage gap (the money-losing bug)

### Root cause, verified against live data

`services/mutual_exclusivity.py:find_me_pairs()` only registers a
mutually-exclusive pair when **both** sibling tickers are present in the
current tick's `markets` list — `main.py:838`,
`mutual_exclusivity.find_me_pairs(markets, state["event_titles"])`, where
`markets` comes from `_fetch_markets(client, cfg,
extra_tickers=open_position_tickers)` (main.py:752-753): the REST
watchlist plus whatever is *already* an open position. A freshly-arriving
whale-signal candidate ticker — by definition not yet a position, and with
no guarantee of being on the narrow watchlist (a documented, separate
coverage gap: "trade WS only subscribes to the watchlist, ~98% of whale
flow never arrives") — has no guarantee of appearing in `markets`. When it
doesn't, `find_me_pairs`'s own `if len(siblings) != 2: continue` (line 58)
skips the event, `state["me_pairs"]` never gets an entry for it, and
`decision_bridge.py:107`'s `(state.get("me_pairs") or
{}).get(signal.ticker)` silently returns `None` — the gate no-ops and the
entry proceeds.

**Live-verified example** (`data/paper_broker.db` trade history,
`KXATPMATCH-26AUG28BUSBON`, a real two-outcome tennis match with no
draw):

| time | ticker | action | price |
|---|---|---|---|
| t+0 | `-BON` | open YES | 0.57 |
| t+2h03m | `-BUS` | open YES | 0.76 (BON already open — gate should have fired) |
| t+2h08m | both | closed by `position_netting`, `locked_loss` | — |
| ... | `-BUS` | open YES | 0.89 |
| ... | `-BON` | open YES | 0.54 (BUS already open — gate should have fired, other direction) |
| ... | both | closed by `position_netting`, `locked_loss` | — |

Buying YES on both sides of a genuine two-outcome market at prices summing
to 1.33 (and, the second time, 1.43) is a guaranteed loss regardless of
who wins — the entry gate exists specifically to prevent this, and its
own docstring names this exact failure mode ("two independent whale
signals landing on opposite sides of one real event... taking BOTH sides
of exactly that kind of mispriced book"). It fired zero times across both
incidents on this one event within about an hour.

### Quantified impact (current DB, all `position_netting` closes)

| classify() outcome | n | total realized P&L | avg/trade |
|---|---|---|---|
| `locked_loss` | 36 | -$3,610.49 | -$100.29 |
| `variable` (genuine EV-improving trims/closes) | 23 | -$283.05 | -$12.31 |

`variable` (which already compares against a materiality bar before
acting) performs far better than `locked_loss` (which is pure downstream
cleanup of an already-doomed book). Entry prices behind these closes span
0.12-0.90; only 20/59 fall in the previously-flagged 0.60-0.95 band — this
is a distinct root cause from that band's negative-EV issue, not a
restatement of it.

### Fix

Add a new function to `services/mutual_exclusivity.py`:

```python
def find_open_confirmed_conflict(
    ticker: str, market_titles: dict, event_titles: dict, open_position_tickers: set[str],
) -> str | None:
    """The ticker of a currently-open position that is Kalshi-confirmed
    mutually-exclusive with `ticker` (same event_ticker,
    event_titles[...].mutually_exclusive is True), or None.

    Unlike find_me_pairs (which needs both siblings in the same tick's
    REST-fetched `markets` batch - narrow, watchlist-scoped), this reads
    market_titles/event_titles: the persisted, catalog-wide caches
    (services/title_cache.py) that decision_bridge.py already reads for
    every signal regardless of watchlist membership. Works for a candidate
    ticker that has never been on the watchlist.

    Scoped to open_position_tickers (small, already computed once per tick
    at main.py's open_position_tickers) rather than scanning the full
    market_titles catalog by event_ticker - same cost shape as
    position_netting.find_groups, which already does this safely on the
    hot path. O(open positions), not O(catalog)."""
    info = market_titles.get(ticker) or {}
    event_ticker = info.get("event_ticker")
    if not event_ticker:
        return None
    if (event_titles.get(event_ticker) or {}).get("mutually_exclusive") is not True:
        return None
    for open_ticker in open_position_tickers:
        if open_ticker == ticker:
            continue
        if (market_titles.get(open_ticker) or {}).get("event_ticker") == event_ticker:
            return open_ticker
    return None
```

`decision_bridge.py:107` changes from:

```python
me_complement = (state.get("me_pairs") or {}).get(signal.ticker)
```

to:

```python
me_complement = (state.get("me_pairs") or {}).get(signal.ticker) or \
    mutual_exclusivity.find_open_confirmed_conflict(
        signal.ticker, state["market_titles"], state["event_titles"], state["open_position_tickers"],
    )
```

`open_position_tickers` is already in `state` (main.py:748,
`state["open_position_tickers"] = set(open_position_tickers)`).

**Observability for the fail-open case:** per CLAUDE.md's data-plane rule
("these properties fail silently: measure them... never infer health from
absence of errors"), `find_open_confirmed_conflict` returning `None`
because `market_titles` has no cached entry yet for `ticker` (a brand-new
market the catalog scan hasn't reached) is a "checked, but the gate
defaulted" case, not a "checked, genuinely no conflict" case — the same
distinction `strategy_engine.py`'s `_record_me_gate_unknown` already
draws for a *different*, not-yet-implemented gate (PR #202's parked
event-scoped ME gate, `docs/superpowers/specs/2026-08-29-event-scoped-me-gate-design.md`
— a separate mechanism with its own `me_gate_unknown_total` counter; this
fix does not touch or reuse it). This fix adds its own small, analogous
counter in `services/mutual_exclusivity.py` (e.g.
`_me_pairing_unknown_total`, exposed read-only the same way
`me_gate_stats()` is) incremented only on the missing-`market_titles`-entry
path, so a real, currently-unmeasured "how often does this check fire
before the catalog has caught up" question becomes answerable instead of
silently absorbed into a generic `None`.

**No change needed to `strategy_engine.py`.** Its existing gate
(`services/strategy_engine.py:545`, `if me_complement and me_complement in
self.broker.positions:`) already does the real membership check itself —
it was already designed to accept "a candidate complement ticker" and
verify openness independently. This fix only improves what value
`me_complement` gets set to.

**No change needed to `main.py`'s existing `find_me_pairs(markets,
state["event_titles"])` call or `state["me_pairs"]`.** That mechanism
keeps serving its two existing purposes unchanged: the price-sum fallback
for events whose `mutually_exclusive` flag isn't cached yet (genuinely
needs both siblings' live prices, which this fix doesn't attempt to
replace), and the dashboard's ME-pair display
(`eventGroupCardHTML`/`GET /api/state`'s `me_pairs` field). The new
function is purely additive — checked second, only when the existing
lookup finds nothing.

**Scope boundary, deliberate:** this fixes the 2-outcome pairwise case
`mutual_exclusivity.py` already owns (matching its own docstring's
"deliberately conservative: only ever pairs an event with EXACTLY two...
siblings" scope) — the same scope as the verified ATP-match failure.
N-way concentration (the 51-position PGA case) is **out of scope** here;
`position_netting.py` already exists specifically to manage that
post-entry, and extending entry-side blocking to N-way events is a
separate, unscoped design question (e.g. "is a 6th position among 50
golfers a defect or ordinary correlated exposure?") that this fix does
not answer.

**Per direct decision:** a confirmed conflict blocks the entry (restores
the gate's originally-intended behavior). This is distinct from the
2026-08-30 decision that removed other risk/exposure guards for
paper-mode training coverage (see `docs/open-decisions.md`'s parked PR
#202 line) — that decision was about trading off protection for data
volume on genuinely uncertain bets; this is a structurally guaranteed
loss by construction (combined cost >100%), not a case where "more data
even if costly" applies.

### Testing

- `tests/test_mutual_exclusivity.py`: new cases for
  `find_open_confirmed_conflict` — confirmed pair with one leg open
  (returns the open ticker), confirmed pair with neither leg open (None),
  unconfirmed/`mutually_exclusive is False` event (None even with both
  legs open), ticker with no `market_titles` entry (None, fails open, and
  increments the new `_me_pairing_unknown_total` counter below).
- `tests/test_whale_stream_decision_bridge.py`: the fallback wiring (old
  `me_pairs` hit takes precedence; new check only consulted when it
  misses).
- Regression: `tests/test_strategy_engine.py`'s existing
  `test_skip_when_position_already_open_on_me_complement` /
  `test_trades_when_me_complement_has_no_open_position` need no changes —
  confirms the "no change needed" claim above rather than assuming it.
- New `_me_pairing_unknown_total` counter: asserts it increments on a
  missing-`market_titles`-entry call and stays flat on a genuine
  confirmed-False/no-conflict result.

## Part 2 — position_netting `locked_loss` exit-fee visibility

### The secondary, smaller defect

`services/exits/position_netting.py`'s `describe_groups` (lines 314-319)
recommends `close_all` for a `locked_loss` group unconditionally, with no
expected-value or materiality-bar comparison — unlike the `variable`
branch (which computes `_best_variable_action` and only acts if the
improvement clears `_materiality_bar`), and unlike the `locked_profit`
branch (which correctly recommends `hold`, citing the module's own
top-of-file docstring: "unwinding can only add fee drag for a payout that
can no longer change" since settlement itself is fee-free,
`kalshi_fees.taker_fee` returning exactly 0.0 at price 0/1). That
fee-drag argument is stated as applying to a "locked" position generally
(profit OR loss), but the code only acts on it for one of the two cases.

Measured: of the 36 `locked_loss` closes (-$3,610.49 total), $261.52
(7.2%) is exit-taker-fee that a fee-free settlement would not have
charged. The other 92.8% is the already-fixed loss baked in by Part 1's
entry-side accumulation — this fee is a real but secondary cost layered
on top.

### Why this spec does not change the close behavior

Whether to keep closing `locked_loss` groups immediately (current
behavior — frees bankroll/position headroom sooner, at the cost of
`~$261` in fees measured so far) or switch to holding to fee-free
settlement (saves the fee, ties up bankroll until settlement) is a
genuine, currently-unquantified tradeoff — "frees up bankroll/position
headroom" is a real benefit the module's own reasoning names, just never
weighed against the fee cost anywhere in code or in this investigation.
Deciding it here would be picking a new default for live paper-trading
money behavior without the evidence CLAUDE.md's "never guess" rule
requires (there is no measurement in this investigation of how often
bankroll headroom is actually the binding constraint on a missed entry).
That decision belongs to the user, not this spec.

### Fix: make the tradeoff visible, not resolved

Add the fee cost as a new field, computed the same way
`_unwind_now_value` already computes exit fees:

```python
exit_fee_cost = sum(
    kalshi_fees.taker_fee(pos.size, latest_prices.get(t, pos.entry_price), ticker=t)
    for t, pos in members
)
```

Surface it in `describe_groups`'s `locked_loss` recommendation dict as
`exit_fee_cost_usd` (rounded to 2dp, same convention as
`expected_value_improvement_usd`/`materiality_bar_usd`), and persist it
onto the trade row as a new additive column,
`netting_exit_fee_usd` (`services/paper_broker.py`,
`_add_column_if_missing`, mirrors issue #213's existing
`netting_improvement_usd`/`netting_bar_usd`/`netting_vol_ratio` columns
exactly — NULL for every non-`locked_loss` close and every pre-existing
row, never `0.0`).

Three-file wiring, identical shape to issue #213's existing columns (no
new pattern, just one more value riding the same path):
`position_netting.describe_groups` computes and returns
`exit_fee_cost_usd` on the recommendation dict → `position_netting.review`
reads `rec.get("exit_fee_cost_usd")` and passes it as a new
`netting_exit_fee_usd=` keyword into its existing `broker.close_position`
call → `PaperBroker.close_position` gains that keyword-only parameter,
threads it onto the `Trade` object and the `INSERT INTO trades` statement,
alongside the existing three `netting_*` keywords it already accepts.

This changes zero trading behavior. It converts a currently-invisible
cost into a tracked one, consistent with CLAUDE.md's "never trade one
property for another silently; make the tradeoff explicit and measured."

**New `docs/open-decisions.md` line** (added when this ships): "Should
`position_netting`'s `locked_loss` branch close immediately (current) or
hold to fee-free settlement, now that the fee cost is measured
(`netting_exit_fee_usd`)? · decide once a few weeks of the new column's
data shows the real fee-vs-headroom tradeoff · you · <ship date>."

### Testing

- `tests/test_position_netting.py`: new case asserting
  `exit_fee_cost_usd` on a `locked_loss` recommendation matches
  `kalshi_fees.taker_fee` summed over members; asserts it's absent/`None`
  on `locked_profit` and `variable` recommendations.
- Extend the existing
  `test_review_persists_netting_decision_inputs_that_agree_with_the_reason_prose`-style
  test to cover the new column on a `locked_loss` close.

## Part 3 — Milestone coverage for live game-state (score/clock)

### What already exists (verified, not assumed)

- `docs/kalshi/get-live-data-with-type.md`, `get-multiple-live-data.md`,
  `get-milestone.md`, `get-game-stats.md`: the documented live-data/
  milestone contract. `get-game-stats.md`'s own prior assessment (2026-08-15)
  already concluded full play-by-play is heavier than this app needs;
  `get_live_data`'s `details` block (score, period, clock) is the
  right-sized signal — unchanged by this spec.
- `services/kalshi/public.py`: `get_milestones_for_event` (per-event,
  used today), `get_live_data`/`get_live_datas` (used today), and
  `get_milestones_bulk` (category-scoped, batched, **implemented and
  tested but zero callers anywhere in the app** — verified by repo-wide
  grep, not assumed from its docstring). Its own docstring records a real
  2026-08-15 live verification: one call with `category="Sports"` and a
  6h watermark returned 200 milestones covering 1,483 distinct
  `related_event_ticker`s.
- `services/market_watch/live_status.py:_fetch_live_status`: the
  currently-wired path. It iterates the **same** watchlist-scoped
  `markets` list as Part 1's bug (confirmed by reading lines 96-119) to
  decide which events are worth a milestone lookup at all — so today,
  score/clock capture (`services/game_state.py`, already fully built:
  persists `home_score`/`away_score`/`period`/`clock`/`last_play` per
  event to `data/game_state.db`) only ever sees whatever's on the
  watchlist, not the wider catalog.

This is a **separate** gap from Part 1 — `mutually_exclusive` is a
market-structure fact from `event_titles`, unrelated to live score/clock
— surfaced by the same investigation, not a dependency of it.

**A second consumer shares the identical narrow pattern, found during
spec self-review:** `services/market_watch/catalog_scan.py`'s
`propagate_milestone_winners` (lines 70-94) *also* builds its own
per-tick `milestone_by_event` from `event_tickers` derived from the same
watchlist-scoped `markets` list, via its own `client.get_milestones_for_event`
calls — a structurally identical, independently-narrow lookup, for a
different purpose (early milestone-declared-winner propagation into
`market_results`, not score/clock display). Both consumers would benefit
from the same broad cache this fix adds. **Deliberately out of scope for
this fix**, though: `propagate_milestone_winners` feeds settlement-outcome
data that real trading decisions consume (`check_exits`/`close_if_settled`),
not just observability/display — rewiring it belongs in its own
follow-up, reviewed on its own terms, rather than riding in on a spec
whose stated purpose is score/clock coverage. Named here so it isn't
silently rediscovered later.

### Fix

New scheduler, `milestone_scan`, following the exact existing pattern
(`main.py`'s `_SCHEDULER_TRIGGERS` tuple, same shape as `catalog_scan`/
`mve_scan`):

- Calls `get_milestones_bulk(category, min_updated_ts=<last-run
  watermark>)` once per configured category (`kalshi.categories`, already
  widened to all 11 categories as of 2026-08-30), on its own interval —
  bounded, batched, same cost class as `catalog_scan`'s existing
  discipline ("batches N per scan, yields to the tick-critical path").
- Builds `state["milestone_by_event"]`: `event_ticker -> milestone_id`,
  broad and independent of `markets`.
- `_fetch_live_status` checks this cache **first** for a given
  `event_ticker`; only falls back to its existing per-event
  `get_milestones_for_event` REST call when the broad cache hasn't
  covered that event yet (e.g., newly created, or outside configured
  categories). Worst case (cold cache) is byte-identical to today's
  behavior; warm cache both broadens coverage and reduces redundant
  per-event calls for events already covered.
- No change to `_LIVE_STATUS_LOOKBACK_SEC` / `_LIVE_STATUS_LOOKAHEAD_SEC`
  / `_LIVE_STATUS_REPOLL_SEC` / `_LIVE_STATUS_MAX_POLL_PER_TICK` — those
  bound how often/how many events get a live-data *poll*, unrelated to
  how the milestone *ID* is discovered.

### Verification after shipping (per CLAUDE.md's data-plane rule — measure, don't assume)

- `game_state.stats()`'s `distinct_events`/`with_score`/`by_sport` before
  vs. after, over a comparable time window, to confirm broadened coverage
  actually materializes (not just "the code path exists").
- Real call volume from the new scheduler against this account's actual
  rate-limit headroom (`docs/kalshi/CHEATSHEET.md`'s already-recorded
  real tier: 200 refill/sec, 600 burst, 10 tokens/call) — expected
  negligible (11 categories x one batched call per interval) but stated
  here as a checked claim, not assumed.

### Testing

- New `tests/test_market_watch_milestone_scan.py` (or extend an existing
  market_watch test file): scheduler wiring, watermark advancement,
  `get_milestones_bulk` batching per category.
- `tests/test_market_watch_live_status.py` (or wherever
  `_fetch_live_status` is currently tested): cache-hit path skips the
  per-event REST call; cache-miss path is unchanged from current
  behavior (regression check).

## Sequencing

Three independently-shippable tasks, one branch (this investigation's
combined scope, per direct instruction), one commit per task — matches
this repo's established "numbered multi-task plan... one commit per
task" convention:

1. Part 1 (entry-gate fix) — highest priority, directly stops the
   verified money loss.
2. Part 2 (fee visibility) — small, additive, no behavior change.
3. Part 3 (milestone coverage) — largest of the three, genuinely
   independent; could ship separately if review prefers to land Part 1
   sooner.

## Out of scope

- N-way entry-side concentration limits (see Part 1's scope boundary).
- Deciding `locked_loss`'s close-immediately-vs-hold tradeoff (Part 2
  adds the measurement; the decision is explicitly deferred).
- Any change to `_LIVE_STATUS_*` polling cadence/limits (Part 3 changes
  discovery, not polling).
- Rewiring `catalog_scan.propagate_milestone_winners` onto the new broad
  milestone cache (Part 3's "second consumer" note) — settlement-outcome
  code, deferred to its own follow-up.
- Any change to real-trading gates, kill-switch, or `mode` — this is
  paper-mode strategy/entry-gate logic only.
