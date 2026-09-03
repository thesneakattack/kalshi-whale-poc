# Edge/EV gate retrospective measurement (informing the `edge_gate_enabled` decision)

Research stage only — no code or `config/settings.yaml` change. Assignment
(autotrade-1d, 2026-09-03): the strategy edge/EV gate (PR #502) is fully
built and merged but `strategy.edge_gate_enabled: false`; 14 of 21 open
paper positions (14 of 18 on the yes side) sit in the 0.60-0.95 unit-cost
band CLAUDE.md flags as negative-EV, and 109 of the last 500 trades entered
there. The user needs a number before deciding whether to flip the gate on.
Verified every inherited claim from source rather than taking it on trust,
per this repo's "never guess" rule, then measured the four things asked
for.

All numbers below come from one script (read-only against live
`data/signal_log.db` and `data/paper_broker.db`, deleted after use, not
committed) that calls the real production functions unchanged
(`confidence_calibration.recompute_deltas`, `_edge_gate_check`'s own logic
inlined for the per-trade simulation) — never a re-derivation of the
gate's math from scratch. Config values used are the live
`config/settings.yaml` ones, confirmed identical to code defaults:
`edge_gate_min_edge=0.04`, `edge_gate_fee_buffer_usd=0.005`,
`edge_gate_pre_print_offset_sec=10.0`, `edge_gate_p_pre_max_age_sec=600.0`,
`edge_gate_min_bucket_n=50`.

## 1. How many (category, price_band) cells clear the 50-signal floor

Replicated `confidence_calibration.recompute_deltas()`'s own attachment
recipe (`services/whale_calibration/confidence_calibration.py:511-534`) —
category via `trade_category.categories_for_tickers`, `q_pre` via
`market_history.recent_price` at `seen_at - 10s` — against the same 30-day
window (`since_ts = now - 30*86400`) it uses, then called the shipped
`_bucket_delta_by_category_price_band` alongside my own instrumented
version of the same grouping so I could see pre-filter cell sizes, not just
the post-filter survivors the production function returns.

```
resolved signals in 30d window:                              173,801
  no p_pre found (market_history had nothing within
    600s of seen_at-10s) — excluded, can't attach q_pre:      123,886  (71.3%)
  q_pre attached (has both p_pre and a fee-derived
    unit cost) — the remaining rows, subdivides below:         49,915  (28.7%)
    of those, category=None (no trade_category row)
      — also excluded, same as production's own filter:        40,801  (23.5% of total, 81.8% of attached)
    of those, category present — these are what actually
      go into a (category, price_band) cell:                    9,114  ( 5.2% of total, 18.2% of attached)

distinct (category, price_band) cells with >=1 signal:   18
cells clearing min_bucket_n=50:                           18  (100%)
cells falling back to delta=0.0 for lack of sample size:   0
smallest surviving cell: (Crypto, 80-90%), n=152 — 3x the floor
```

(123,886 + 49,915 = 173,801 — the two mutually exclusive top-level buckets;
category-present/category-None are a further split of the 49,915, not a
third disjoint bucket, kept indented above to make that explicit.)

**Correction to the PM's own working assumption:** "~170k resolved signals
over roughly 55 cells" was the framing going in; the real grid is much
smaller — only 3 categories currently produce any edge-gate-attachable
signals at all (Crypto, Sports, Commodities; Politics/Economics/etc. either
have no resolved signals in-window or none that survive attachment), each
with up to 6 price bands, giving 18 possible cells, and **every one of
those 18 that has any data at all clears the floor by a wide margin.**
Zero cells are currently in the delta=0.0 fallback state. This is a
stronger, more specific answer than "most clear it" — it's all of them,
counted, not estimated. Worth noting separately: 81.8% of the rows that
survive the p_pre attachment step still get dropped for having no
`trade_category` row at all (40,801 of 49,915) — the real per-cell
population (9,114 rows across 18 cells, ~506 average) is meaningfully
smaller than "49,915 attached" reads at a glance, but every cell that
exists is still well clear of the 50-signal floor (smallest at 152, 3x
the minimum) even after that further narrowing.

**The bigger number here is the 71.3% `no p_pre` rate**, not the cell
count — confirmed as a real data characteristic, not a script bug, by
widening the lookback window to 7 days and re-checking a sample of the
failures: `market_history.recent_price` still returned `None` even at 7
days back, but returned a real value for the *same ticker* as of *right
now*. That means these aren't old signals whose price history rolled off
retention — they're on tickers that had **no price snapshot at all before
their first (and often only) trade**, concentrated in the highest-
rotation market types: 15-minute gold/silver strikes (`KXGOLD15M`,
`KXSILVER15M`) and hourly ETH strikes (`KXETHD-...`) that are created,
traded, and resolved faster than `market_history`'s own polling cadence
captures a baseline snapshot. This is a real, separate finding worth its
own follow-up (a coverage gap in how fast new short-dated market instances
get their first price snapshot), out of scope to fix here, but directly
relevant to item 3 below since it drives most of that section's fail-open
rate too.

## 2. The consequence of an uncalibrated cell

Confirmed directly from `_edge_gate_check` (`services/strategy_engine.py:
287-308`): when `delta_calibrated_for()` returns `0.0` (either `category is
None`, or the cell exists but never accumulated `min_bucket_n` — neither
condition is currently occurring for any real cell, per §1), `p_est_side =
clamp(q_pre_now + 0.0) = q_pre_now`. The gate's comparison is `edge =
p_est_side - ask_now - fee`, admitted when `edge >= min_edge`. Substituting:

```
q_pre_now - ask_now - fee >= min_edge
  =>  ask_now <= q_pre_now - fee - min_edge
```

**Confirmed, not refuted: an uncalibrated cell is a strict "the price must
have moved in your favor by at least ~0.045 since the pre-print snapshot"
filter, not a neutral pass-through.** `q_pre_now` is the unit cost 10
seconds *before* the whale print; `ask_now` is the unit cost the trade
would actually pay. Since `delta=0.0` means "no measured edge from being
right about direction," the gate falls back to demanding the price already
moved the trader's way before it will admit the trade — the opposite of
"chasing" a print. This reading holds regardless of §1's finding that no
real cell currently sits in this state: it's the correct characterization
of what would happen *if* one did (a brand-new category, or a cell that
later drops below 50 signals through retention pruning), and it's worth
carrying forward even though it isn't biting today.

## 3. Headline number: how many of the recent trades would the gate reject

Queried the last 500 entry trades from `data/paper_broker.db` (`reason NOT
LIKE 'closed:%'`, most recent first) — 319 actually exist in that
selection (paper trading hasn't accumulated 500 distinct entries yet).
Simulated `_edge_gate_check`'s exact math per trade using **today's**
calibration state (the real question a flip-the-switch-now decision needs:
would enabling the gate right now have rejected these), not a
point-in-time-historical recalibration.

```
total entry trades checked:                        319
fail-open (no p_pre / q_pre / ask_now — gate can't evaluate):  196  (61.4%)
evaluable:                                          123  (38.6%)
  -> would be REJECTED:                               92  (74.8% of evaluable, 28.8% of all)
  -> would be ADMITTED:                                31  (25.2% of evaluable,  9.7% of all)
  of evaluable, hit an uncalibrated (delta=0.0) cell:   0

in-band  (ask_now in [0.60, 0.95]): checked  54, rejected  39  (72.2%)
out-of-band:                        checked  69, rejected  53  (76.8%)
```

**Two things worth flagging plainly, not just the rejection count:**

- **The gate would only ever evaluate 38.6% of these entries at all.**
  61.4% fail open (admitted regardless of edge) purely because
  `market_history` has no price snapshot within 600s of the pre-print
  offset — the same mechanism as §1's 71.3% figure. A flip-the-switch
  decision should account for this: the gate is not a blanket filter on
  every entry, it's a filter on the roughly-two-in-five entries it can
  actually price.
- **Rejection rate is not concentrated in-band.** CLAUDE.md's own framing
  names the 0.60-0.95 band as the negative-EV concern, but in this sample
  the out-of-band rejection rate (76.8%) is slightly *higher* than the
  in-band rate (72.2%) — both are high, and the gate isn't selectively
  hitting the flagged band more than anywhere else. Read this as "the gate
  is a broad, aggressive filter on whatever it can evaluate," not as
  confirmation that it's precision-targeting the specific band of concern.

Zero uncalibrated-cell hits among the 123 evaluable trades — consistent
with §1's finding that no real cell is currently in fallback, so every
evaluable rejection reflects a genuine measured `delta`, not the strict
default-filter behavior described in §2.

## 4. Is an observe-only mode worth proposing?

**Yes, in the sense the PM's instinct pointed at — the compute step is
already unconditional-once-triggered — but it needs slightly more than
"stop enforcing," which is worth stating precisely rather than waving at.**

Confirmed: `_edge_gate_check` (`strategy_engine.py:233-308`) always returns
the full `{"p_est", "q_pre", "delta", "edge", "min_edge"}` detail dict
alongside its pass/fail `EntryValidation`, on **both** the admit and reject
paths (the reject path attaches it via `._replace(edge_gate_detail=...)`
at `:222`) — the computation itself doesn't distinguish observe from
enforce; only the caller's decision to act on `not edge_result.ok`
(`:221-222`) does. So "compute without enforcing" is genuinely a small
change: gate the *computation* on a broader condition (`edge_gate_enabled
or edge_gate_observe_only`) while gating the *early-return* strictly on
`edge_gate_enabled` alone.

**What it would additionally need, confirmed by tracing where the detail
actually goes today:** on the *admitted* path, `decision["edge_gate"]` is
already populated from the full detail dict (`strategy_engine.py:811-812,
836-837`) — that part transfers to observe-only unchanged. But on the
*rejected* path, the caller (`evaluate()`, `:731-736`) currently calls
`candidate_log.record_rejection(ticker, "whale_follow", gate_name,
observed_value, threshold_value, side=side, unit_cost=unit_cost)` —
`record_rejection`'s own signature
(`services/candidate_log.py:116-119`) has no field for the full detail
blob; only `observed_value`/`threshold_value` (which the edge gate's call
site maps to `edge`/`min_edge` alone) get persisted. **`p_est`, `q_pre`,
and `delta` are computed but never captured anywhere today for a rejected
signal** — not a new problem observe-only creates, but a real gap
observe-only would inherit unless addressed at the same time, since the
whole point of an observe-only pass is comparing what *would have*
happened across the full breakdown, not just edge-vs-threshold.

**Roughly what it would take, concretely:** (a) one new config key
(`edge_gate_observe_only` or similar); (b) a small conditional in
`_validate_entry_price` so observe-only still calls `_edge_gate_check` and
still attaches `edge_gate_detail`, but never turns a failing `edge_result`
into an early return; (c) extending `record_rejection`'s persisted fields
(or adding a parallel capture path) so `p_est`/`q_pre`/`delta` survive for
signals the gate *would* have rejected, not just `edge`/`min_edge`. (a)
and (b) are genuinely small; (c) is the part that turns "cheap" into
"small but real" — a schema/call-site change, not a one-line flag flip.
Not attempted here (out of scope — read-only research), but this is
concrete enough to size as a task, not a guess.

## Summary for the decision

- The edge-gate math and its calibration inputs are sound and well-
  covered where they apply: all 18 real (category, price_band) cells
  clear the 50-signal floor with 3x+ headroom, and the uncalibrated-cell
  fallback (a real, confirmed strict filter) isn't currently affecting any
  trade.
- The real headline number: **of trades the gate could actually evaluate,
  it would have rejected ~75% of them** (92/123) — a large, real effect,
  roughly evenly split between the CLAUDE.md-flagged 0.60-0.95 band and
  everything else, not concentrated where the concern was originally
  raised.
- The caveat that matters as much as the headline: **the gate can only
  evaluate 38.6% of entries at all** (61.4% fail open on missing
  price-history coverage, a data gap concentrated in fast-rotating 15min/
  hourly markets) — flipping it on would filter roughly three-quarters of
  a minority of trades, not three-quarters of everything.
- Observe-only is worth proposing and is cheap on the compute side, but
  needs one additional real piece of work (capturing the full detail
  breakdown on rejection, which nothing does today even with the gate
  enabled) to actually deliver what an observe-only comparison needs.
