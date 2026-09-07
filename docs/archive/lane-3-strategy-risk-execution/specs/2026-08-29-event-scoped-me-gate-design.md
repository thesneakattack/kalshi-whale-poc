# Event-Scoped Mutual-Exclusivity Entry Gate — Design

Date: 2026-08-29. Status: **revision 2**, after two independent reviews
(implementation-readiness + design-soundness). Strictness DECIDED by the
user: strict — block any second position on an ME-true event. The design
review's verdict on revision 1 was "do not implement as written": the
gate could be rendered inert by three independent conditions AND every
stated success criterion was satisfied by that inert state. Sections 4.1,
4.2, 4.5, 4.6 and 7 are rewritten to close that. Ready for implementation
review again. Origin: direct question ("why when positions enter
netting/hedge mode, almost 100% of the time, conflicting positions are
made, and BOTH lose") answered with a full mechanism investigation the
same session; this spec is the fix's design. Evidence and reconstruction:
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-29-trade-performance-analysis.md`
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

### 3.1 Strictness DECIDED (2026-08-29, user's call): block any second
position on an ME-true event, regardless of side

The user chose strict after reviewing the evidence and the counter-case
below. Both are retained because they define what must be measured.

The naive counterfactual looked ambiguous — across the 22 reconstructed
pairs, second legs alone summed only -$97.92, and same-side second legs
actually won +$519.51 — so the decision does NOT rest on it. It rests on
four things that are not ambiguous:

1. **Arb is arithmetically impossible under the current floor.** Stated
   as arithmetic, not as data: two legs at unit costs c1, c2 >= 0.50 cost
   c1 + c2 >= 1.00 **plus two taker fees**, against a maximum payout of
   exactly $1.00 on a 2-outcome event. Strictly negative, always. (The
   observed minimum of 1.06 is corroboration, not the argument.) The one
   legitimate reason to hold both sides — guaranteed profit — therefore
   cannot reach this gate.

   **This is NOT a global-config guarantee, and revision 1 wrongly treated
   it as one.** `_validate_entry_price` reads `min_unit_cost` off
   `config_overrides.resolve(...)` (strategy_engine.py:187, :259-260), so a
   single `strategy_overrides.by_series.<X>.min_unit_cost: 0.4` — exactly
   the shape the advisory engine tunes, and overrides are already live in
   this config — silently kills the argument. The revisit trigger is
   therefore MECHANICAL, not a note: `services/config/config_bounds.py`
   already validates "across the global strategy config and every
   category/series" (:239); it gains a check that flags any RESOLVED
   `min_unit_cost < 0.5` while the strict gate is on. Spec §3-C is the
   successor design for that world.
2. **The hedge is strictly dominated by exiting leg 1.** If new whale
   flow says the held side collapsed, buying the other side at >= 0.50
   locks in (combined_cost - 1) + fees — observed mean 0.33 of overround
   paid — while simply closing leg 1 captures the same information
   without paying it. The correct instrument exists and is the book's
   best performer (auto_exit: 97.3% win, +$5,475.47).
3. **The gate forecloses nothing profitable — sequencing stays legal.**
   Exit-then-enter is allowed: once leg 1 closes, the event is no longer
   held and the flip side can be entered. Every observed profitable
   second leg (best: +$205.46) was reachable under the gate via the
   strictly better exit-first sequence.
4. **Mixed-side entries are directly negative anyway.** The
   disguised-double shape (yes-A + no-B = same bet twice) lost -$617.43
   on second legs alone; blocking it needs no subtlety.

Same-side pairs' combined book: -$2,052.48; mixed: -$1,352.14.

**The argument against, now VERIFIED and stronger than revision 1 said.**
Point 3 assumes exit-then-enter actually happens. It is legal, but nothing
causes it — and the design review confirmed the mechanism is not merely
absent, it is structurally impossible today:

- `_whale_lean(ticker, signal_feed)` filters the signal feed by **exact
  ticker** (`services/exits/exit_engine.py:418-432`), and both consumers
  (the `exit_on_sentiment_reversal` branch :390-397 and the auto-exit
  `sentiment` factor :558-562) use it. A whale print on sibling leg B
  contributes **zero** weight against held position A.
- `exit_on_sentiment_reversal` is `false` (config/settings.yaml:65).
- The blocked signal is then destroyed: `candidate_ledger.claim()`
  consumed its trade_id at `whale_stream/decision_bridge.py:66`, before
  `evaluate()` ever ran.

So under today's code the strict gate does not *risk* converting a hedge
into a lingering single loss — it **always** does. Strictness is still the
right call (the pair was a guaranteed loss, and `position_netting` was
closing the worse leg at a fixed loss anyway), but the consequence is real
and unmeasured.

**Therefore the measurement ships in THIS spec, not the follow-up**
(§4.4): every gate block records the held ticker alongside the blocked
event, so the held leg's realized P&L can be reported separately. Without
it, the stated follow-up trigger ("if the soak shows blocked-flip losers
lingering") has no data to fire on — the same defect §3.1 correctly
identifies in its own counterfactual. The softer variant (block + emit an
exit-review signal into `check_exits`) remains out of scope, but it is now
a decision the soak can actually inform.

## 4. Design

### 4.1 Gate (services/strategy_engine.py, inside `evaluate()`)

Replaces the existing `me_complement` parameter/check in its exact
current slot — immediately after the same-ticker duplicate check
(strategy_engine.py:438), BEFORE the cooldown check at :498 (the spec's
first draft said "after cooldown"; verified wrong during self-review).
Not a second check beside it. Logic:

```
# Committed exposure = open positions AND resting limit orders. A resting
# order is exposure the gate must see: revision 1 read only positions, so
# two opposite legs could both pass at placement and both fill (F2b).
held_events = {p.event_ticker for p in broker.positions.values() if p.event_ticker}
held_events |= {o.event_ticker for o in broker.pending_orders.values() if o.event_ticker}
if signal_event and signal_event in held_events:
    me_flag = event ME flag for signal_event    # see 4.3
    if me_flag is True:
        record_rejection(..., gate_name="me_event_gate", ...)
        skip("already holding a position on this one-winner event")
    elif me_flag is False:
        allow                                    # independent props
    else:                                        # unknown
        allow, increment counter; fault_log once per event per PROCESS
```

- ME-true blocks ANY second position on the event regardless of side:
  both observed loss shapes (opposite bets and disguised doubles) share
  the same trigger.
- Unknown fails OPEN (the codebase's uniform rule; the data-plane rule
  forbids silently trading completeness for protection) but is never
  silent: the monotone counter increments on EVERY occurrence, while
  `fault_log` records once per event **per window** — a broken metadata
  path at signal rate must not become a SQLite fault write per signal on
  the hot path. Revision 1 said "per process," which is strictly weaker
  than the precedent it cited: websocket.py's
  `_fault_logged_classes_this_window` is CLEARED every window
  (`reset_ingest_window`, :1162), so a persistent problem keeps
  re-announcing itself; "once per process" would emit one row and then go
  permanently silent, the opposite of what a live health signal needs.
  The seen-set is window-scoped (reset alongside the counter snapshot) and
  bounded, matching this module family's `_MAX_SEEN_TRADE_IDS` /
  `_MAX_MARKET_CACHE` convention.
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
  column via `_add_column_if_missing`, stamped at `open_position`.
  **`open_position` has THREE callers, not one** (revision 1 plumbed
  only the first): `strategy_engine.py:555` (whale-follow),
  `paper_broker.py:499` (`check_pending_fills`, the limit-order fill
  path — see §4.5), and `settlement_edge_entry.py:139` (see §4.6).
- `PendingOrder` (paper_broker.py) also gains `event_ticker`, stamped at
  `place_limit_order`, so §4.1's `held_events` can see resting exposure.
- Pre-existing position rows get NO backfill (YAGNI: median hold ~51 min,
  so unstamped rows age out). **Revision 1 claimed "the unknown counter
  measures the window" — that was false and is retracted.** There are two
  distinct unknowns and revision 1 instrumented only one: "event unknown"
  (position never stamped) produces no collision, so it never reaches the
  counter at all; only "ME flag unknown" (collision found, event absent
  from `event_titles`) does. The real thing is instrumented directly
  instead — see §7's `positions_without_event_ticker`. The caveat that
  matters: the untagged tail is exactly the multi-hour live-sports events
  that form pairs (186 of 257 closes were settlements; max observed
  inter-leg gap 8.7h), so coverage must be measured, not assumed.
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

### 4.4 Blocked-flip measurement (ships now, per §3.1)

Every gate block records, alongside the rejection: the blocked event, and
the ticker(s) of the held position(s) that caused the block. That makes
"what happened to the leg we kept" answerable directly from trade history
— the P&L of held legs whose event produced a blocked flip, versus the
book. This is the data the softer-variant decision needs, and §3.1's
verified finding (nothing exits the held leg on a flip signal) is why it
cannot wait for a follow-up.

### 4.5 The limit-order path (was a structural bypass)

With `strategy.use_limit_orders: true`, whale-follow positions are created
at `paper_broker.py:499` inside `check_pending_fills`, not at
`strategy_engine.py:555`. Revision 1 therefore had two holes: fills wrote
`event_ticker=None` (gate goes permanently inert and silent), and resting
orders were invisible to `held_events` (both legs pass at placement, both
fill). The flag is `false` today (config/settings.yaml:86) but the maker
path exists precisely because fees are eating the book, so it is a live
prospect, and a gate that silently dies when a config flag flips is not
acceptable.

Three changes: `PendingOrder` carries `event_ticker` (§4.2);
`check_pending_fills` forwards it to `open_position`; and the fill-time
`validate_fn` (`FollowTheWhaleStrategy.validate_pending_fill`) re-checks
the event gate — an order validated only at placement is the exact bug
shape `strategy_engine.py:155-165` already documents.

### 4.6 `settlement_edge_entry` — deliberately out of scope

`settlement_edge_entry.py:139` opens positions without going through
`evaluate()` at all (reached from `whale_stream/index_stream_handlers.py`).
It is `enabled: false` (config/settings.yaml:176) and is a different
strategy with its own entry logic. Its positions are therefore NOT stamped
and NOT gated: an explicit, named exclusion rather than silence. If that
engine is ever enabled, this gate does not protect it — recorded here so
enabling it is a decision that sees this consequence.

### 4.7 What stays

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

## 7. Success criteria — positive coverage first

Revision 1's criteria were **all satisfiable by a completely dead gate**
(the design review's disqualifying finding): the unknown counter only
increments inside the collision branch, so an unpopulated `held_events`
reads as zero unknowns — perfect health, zero function. CLAUDE.md's own
rule ("never infer health from the absence of errors") was violated by the
measurement plan itself. Coverage is now proven positively:

1. **Coverage (the gate is alive).** `positions_with_event_ticker /
   positions_total` ≈ 1.0, and `me_gate_evaluated_total` (signals arriving
   with a non-empty event) grows with the signal stream. A zero here means
   the gate is inert regardless of every other number.
2. **Function.** `me_gate_blocked_total` > 0 whenever same-event second
   signals occur (they occurred 22 times in the analyzed book), read from
   `rejection_events` — NOT `gate_summary()`, whose
   `PRIMARY KEY (ticker, strategy, gate_name)` counts distinct tickers,
   not blocks.
3. **Residual risk, measured not assumed.** `positions_without_event_ticker`
   (the §4.2 retraction) and the ME-flag unknown counter both near zero,
   each meaning a different thing.
4. **Outcome.** New same-event locked_loss pairs → ~0, and (per §4.4) the
   held legs of blocked flips are not systematically worse than the book.
5. **Cost.** Hot-path latency, not call counts: the `resolve` stage
   (`perf.record_stage("resolve", …)`, kalshi_trade_tape.py:317-319) shows
   no material delta. Revision 1 measured REST call counts, which is not
   what the data-plane rule is about.

Note on 1 and 3: these counters are in-memory and reset on every process
restart, and `.py` edits hot-reload in 1-2s under ddev — so a "near zero
over a soak" reading must be taken against a known-stable process, not a
repeatedly-zeroed one.
