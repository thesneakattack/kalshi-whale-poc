# Event-Scoped Mutual-Exclusivity Entry Gate — Design

Date: 2026-08-29. Status: spec written, awaiting user review before
implementation. Origin: direct question ("why when positions enter
netting/hedge mode, almost 100% of the time, conflicting positions are
made, and BOTH lose") answered with a full mechanism investigation the
same session; this spec is the fix's design. Evidence and reconstruction:
`docs/superpowers/research/2026-08-29-trade-performance-analysis.md`
(§3, §13) plus the pair-reconstruction run recorded below.

## 1. The problem, mechanically (verified, not hypothesized)

Three findings, each independently verified this session:

1. **Formation**: whale-follow chases momentum, and live sports seesaw.
   Reconstructed from real trade history: 22 same-event overlapping pairs
   involving a `position_netting` close; the second leg entered a median
   **30 minutes** after the first (min 0s, max 8.7h). Whales print on the
   current favorite; the match swings; whales print on the new favorite;
   the strategy follows both. The cross-ticker version of the
   "betting against myself" whipsaw `services/mutual_exclusivity.py`'s
   own docstring names as its motivation.
2. **The existing gate is structurally blind to the entry stream.**
   `state["me_pairs"] = find_me_pairs(state["markets"], event_titles)`
   (main.py:828) is computed per tick from the **watchlist** (~15–22
   markets; live check at investigation time: 22 markets, exactly **one**
   paired event). Entries flow from the **exchange-wide** WS stream via
   `_resolve_unknown_markets`, which fetches only the printed ticker —
   never its event siblings — so `find_me_pairs` (which requires both
   siblings present, and exactly two) can almost never cover an entry.
   The observed sequencing confirms it: after BOTH legs are open,
   `extra_tickers=open_position_tickers` pulls their markets into
   `state["markets"]`, the pair finally appears, and `position_netting`
   groups it — the gate is blind at formation, the netting module sees it
   after, which is why "netting appears ⇔ conflict exists" reads as
   ~100% correlation.
3. **Every formed pair is doomed by construction.** `min_unit_cost: 0.5`
   forces each leg ≥ $0.50, so any same-event pair costs ≥ $1.00 combined
   against a $1.00 maximum payout, before fees. Observed: all 22 pairs
   sum over 1.00 — mean **1.33**, worst 1.78; **12 of 22 were
   mathematically locked losses at the moment the second leg opened**
   (sizes and fees included, payout-profile computation). Two loss
   shapes: 13 same-side pairs (genuine opposite bets, overround-doomed)
   and 9 mixed-side pairs (yes-A + no-B on a 2-way event = the same bet
   twice; both rows lose together when wrong).

The 2026-08-29 applied config change-set (series caps, exposure cap,
netting bar $10) mitigates N-way pileups and trims still-variable pairs
earlier, but none of it prevents pair FORMATION — a series cap of 2
still admits one pair, and the coverage hole is code, not config.

## 2. Kalshi contract facts this design stands on (docs read, not memory)

- `event_ticker` is a documented field on every market object
  (`docs/kalshi/get-market.md:141`, `docs/kalshi/get-markets.md:148`).
  The resolve path's fetched market objects already carry it. **The event
  is NEVER derived from the market ticker string** — no doc guarantees
  the `SERIES-EVENT-MARKET` prefix structure.
- `mutually_exclusive` is a documented boolean on event objects
  (`docs/kalshi/get-events.md:236`): "If true, only one market in this
  event can resolve to 'yes'. If false, multiple markets can resolve to
  'yes'." Authoritative one-winner semantics; no inference needed.
- Event metadata is already batch-fetched (`_fetch_event_titles` →
  `get_events`) and cached in `state["event_titles"]` with persistence
  via `title_cache`.

## 3. Approaches considered

- **A — extend `find_me_pairs` coverage** (fetch siblings during
  resolve). Rejected: adds a sibling fetch per candidate, still fails
  N-way events (`len(siblings) != 2`), and keeps the racy
  periodically-computed-map shape.
- **B — event-scoped entry gate** (CHOSEN): gate on "does any open
  position share this signal's `event_ticker`," verdict decided by the
  event's own `mutually_exclusive` flag. No sibling fetch, no pair map
  on the entry path, uniform for 2-way and N-way events.
- **C — EV-aware combined-book gate** (allow a second leg only when the
  combined payout profile is positive). Rejected for now: with
  `min_unit_cost: 0.5` a positive same-side combined book is
  mathematically impossible, so C degenerates to B plus complexity.
  Revisit only if `min_unit_cost` ever drops below 0.5.

## 4. Design

### 4.1 Gate (services/strategy_engine.py, inside `evaluate()`)

Replaces the existing `me_complement` parameter/check (same slot in the
gate order — after cooldown, before sizing), not a second check beside
it. Logic:

```
held_events = {pos.event_ticker for pos in broker.positions.values()
               if pos.event_ticker}
if signal_event and signal_event in held_events:
    me_flag = event ME flag for signal_event    # see 4.3
    if me_flag is True:
        record_rejection(..., gate_name="me_event_gate", ...)
        skip("already holding a position on this one-winner event")
    elif me_flag is False:
        allow                                    # independent props
    else:                                        # unknown
        allow, fault_log once per event per window, increment counter
```

- ME-true blocks ANY second position on the event regardless of side:
  both observed loss shapes (opposite bets and disguised doubles) share
  the same trigger.
- Unknown fails OPEN (the codebase's uniform rule; the data-plane rule
  forbids silently trading completeness for protection) but is never
  silent: `fault_log` + a monotone counter, so the residual risk is
  measured, not invisible.
- Rejections flow through `candidate_log.record_rejection` under
  `me_event_gate` with `unit_cost`, so the advisory counterfactual
  pipeline sees this gate from day one.

### 4.2 Event identity plumbing

- `WhaleSignal` (services/confidence_scoring.py) gains
  `event_ticker: str | None = None`. Populated by
  `services/whalewatchers/kalshi_trade_tape.py` from the resolved market
  object's own `event_ticker` field (both the watchlist path and
  `_resolve_unknown_markets`' on-demand path hold that object at scoring
  time). The simulator leaves it None — same convention as `factors`.
- `Position` (services/paper_broker.py) gains `event_ticker` — additive
  column via the existing `_add_column_if_missing` idiom, stamped at
  `open_position` from the signal/market. Pre-existing rows deliberately
  get NO backfill (YAGNI, resolved during plan review): positions are
  short-lived (median hold ~51 min in the analyzed book), so unstamped
  legacy rows age out within hours of deployment; until then a position
  whose event cannot be determined participates in `held_events` as
  nothing - fail-open, and the unknown counter measures the window.
- `candidate_retry`'s recovered-candidate path re-scores through the
  same provider, so recovered signals carry `event_ticker` with no extra
  work — verify in the plan, don't assume.

### 4.3 ME-flag availability (the only new I/O, bounded)

Lookup order: `state["event_titles"][event_ticker]["mutually_exclusive"]`
(tick-fetched + `title_cache`-persisted) → on miss, the whale resolve
path's enrichment ensures the event via the existing `get_events` batch
(`critical_whale` caller class - verified, not assumed:
`_resolve_unknown_markets` is decorated `@http_client.classify("critical_whale")`
and `classify` propagates through the whole coroutine via contextvar, so the
`get_events` call inside inherits it), one call per previously-unseen event,
cached in `event_titles`/`title_cache` thereafter. Per the data-plane
rule this addition is measured: the plan includes capturing per-event
fetch counts through the existing REST class metrics before/after, and
the expected steady-state rate is ~one call per new event entering the
candidate stream (strictly less than the existing per-candidate market
enrichment that already runs).

### 4.4 What stays

- `find_me_pairs` remains for netting/UI display; `state["me_pairs"]`
  keeps its consumers. Only `evaluate()`'s me_complement parameter is
  retired in favor of the event gate.
- `position_netting` remains the backstop for anything that still forms
  (e.g. two legs racing in before either position registers — the gate
  reads `broker.positions`, which updates synchronously at open, so the
  race window is a single consumer iteration, but the backstop stays).
- Netting-side visibility heals automatically: positions now carry
  `event_ticker`, removing `find_groups`' dependence on watchlist
  metadata freshness for grouping (follow-up, not in this spec's scope:
  `find_groups` could read `pos.event_ticker` directly).

## 5. Safety / invariants

- Paper mode only, no trading-enablement surface touched.
- The gate can only REDUCE entries; it cannot open anything.
- Fail-open on unknown preserves current behavior exactly for every
  signal the new plumbing can't classify — the failure mode of this
  feature not working is today's behavior, measured.
- Schema change is additive-only (CLAUDE.md persistence rule).

## 6. Testing (TDD, per gate branch)

1. ME-true event + open position on a sibling → skipped, rejection
   recorded under `me_event_gate` with unit_cost.
2. ME-false event (independent props) → allowed.
3. Unknown flag → allowed + fault-logged + counter incremented (and NOT
   re-logged within the window).
4. N-way ME event (3+ markets) → second entry skipped (the shape
   `find_me_pairs` could never catch).
5. Simulator signal (event_ticker None) → passes through untouched.
6. Position stamping: open_position persists event_ticker; reload from
   DB restores it; legacy row without it backfills from market_catalog.
7. Regression from real data: reconstruct the KXATPMATCH-26AUG29FERBUS
   pair shape (second leg 1,816s later, combined cost 1.41) and assert
   the second leg is refused when the event is ME-true.
8. Counterfactual visibility: gate_summary() shows the new gate.

## 7. Success criteria

- New same-event ME-true pairs stop forming (netting's locked_loss
  close count for NEW pairs → ~0 over a soak; existing backstop
  untouched).
- The unknown-flag counter stays near zero in steady state (proves the
  metadata plumbing actually covers the entry stream; a climbing counter
  is the signal the fetch path has a hole).
- No measurable REST-rate increase beyond ~one get_events call per new
  event (verified via existing kalshi_rest_class metrics).
