# Edge/EV gate retrospective measurement (informing the `edge_gate_enabled` decision)

Research stage only — no code or `config/settings.yaml` change. Assignment
(autotrade-1d, 2026-09-03): the strategy edge/EV gate (PR #502) is fully
built and merged but `strategy.edge_gate_enabled: false`; the PM's own
working numbers going in were 14 of 21 open paper positions (14 of 18 on
the yes side) sitting in the 0.60-0.95 unit-cost band CLAUDE.md flags as
negative-EV, and 109 of the last 500 trades entered there — both are
time-varying counts, not independently re-verified against a specific
timestamp in this doc (a live re-check while writing this: 15 open
positions, 9 yes-side, 7 in-band). The user needs a number before deciding
whether to flip the gate on. Measured the four things asked for, tracing
every derived claim to source rather than taking it on trust, per this
repo's "never guess" rule — though as the adversarial review that
followed found (see this doc's `-adversarial-review.md` sibling), tracing
a citation correctly is not the same as tracing what it *computes*
correctly, and this doc's first draft got that distinction wrong twice.

All numbers below come from scripts (read-only-intent SQLite queries
against live `data/signal_log.db` and `data/paper_broker.db` — though the
underlying `services.*` functions called still open ordinary read-write
connections that run `PRAGMA journal_mode=WAL`/`CREATE TABLE IF NOT
EXISTS` internally, so "read-only" describes intent and the queries
written for this research, not a guarantee about every function called;
scratch scripts deleted after use, not committed) that call the real
production functions for the actual gate math — `confidence_calibration.
recompute_deltas`/`delta_calibrated_for`/`_bucket_delta_by_category_
price_band`, and `kalshi_fees.unit_cost`/`taker_fee_per_contract` — with
`_edge_gate_check`'s own formula inlined for the per-trade simulation in
§3 (the function itself needs a live `ticker`/`series_cache` lookup for
its flat-fee-type early-out that a standalone script doesn't replicate;
confirmed none of the sampled tickers are on a flat-fee series, so the
omission doesn't affect the numbers). Config values used are the live
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
      — also excluded, same as production's own filter:        40,801  (23.5% of total, 81.7% of attached)
    of those, category present — these are what actually
      go into a (category, price_band) cell:                    9,114  ( 5.2% of total, 18.3% of attached)

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
signals at all (Crypto, Sports, Commodities; `trade_category.db` itself
only holds Sports/Crypto/Commodities/Climate and Weather/Economics/
Entertainment rows — no Politics category exists in this table at all —
and the latter three categories' signals either don't survive attachment
or don't reach `min_bucket_n`), each
with up to 6 price bands, giving 18 possible cells, and **every one of
those 18 that has any data at all clears the floor by a wide margin.**
Zero cells are currently in the delta=0.0 fallback state. This is a
stronger, more specific answer than "most clear it" — it's all of them,
counted, not estimated. Worth noting separately: 81.7% of the rows that
survive the p_pre attachment step still get dropped for having no
`trade_category` row at all (40,801 of 49,915) — the real per-cell
population (9,114 rows across 18 cells, ~506 average) is meaningfully
smaller than "49,915 attached" reads at a glance, but every cell that
exists is still well clear of the 50-signal floor (smallest at 152, 3x
the minimum) even after that further narrowing.

**The bigger number here is the 71.3% `no p_pre` rate**, not the cell
count — but the explanation for it in this doc's first draft was wrong,
caught by adversarial review, and worth walking through since the error is
instructive: the doc originally widened the lookback window to 7 days,
found `recent_price` still returned `None`, and concluded these were
tickers with no price history at all rather than old signals whose history
had rolled off retention. **That test cannot actually distinguish the two
hypotheses** — widening how far back you *search* can't recover a snapshot
that's already been physically *deleted* by the time you search, and
`market_history.retention_hours: 168` (`config/settings.yaml:308-309`, a 7-
day retention window, enforced hourly by `market_history.prune()` deleting
`snapshots WHERE timestamp < cutoff`) means exactly that: a signal older
than ~7 days has almost certainly had its own contemporaneous price
snapshots physically removed by now, regardless of how wide a window a
later query searches.

Re-measured directly, binning the same 30-day signal population by age:
miss rate holds around 32-67% for signals 0-6 days old, then jumps to
**99.4% at exactly 7 days and 100% beyond** — a clean cliff at the
configured retention boundary, not a gradual falloff. **~60% of all
misses are signals older than the retention window whose history has
simply been deleted; the genuine within-retention coverage gap is 49.4%,
not 71.3%.** This also means `recompute_deltas`'s nominal 30-day
calibration window is, in practice, capped at ~7 days by retention — worth
flagging on its own, since it bounds how far back *any* future
calibration measurement can ever look, not just this one.

The exemplar markets named in the original draft (`KXGOLD15M`,
`KXSILVER15M`, `KXETHD-...`) were also wrong — re-checked per-family, they
are actually reasonably well covered (`KXGOLD15M` and `KXSILVER15M` both
~62-64% covered, only 23-25% genuinely zero-snapshot). The real
within-retention coverage gap concentrates in `KXMVECROSSCATEGORY`
(16.1% of all misses, **100% zero-snapshot at every age** — a distinct
failure from either retention or fast rotation) and the sports-match
families (`KXATPMATCH`, `KXMLBGAME`, `KXWTAMATCH`, `KXATPCHALLENGERMATCH`
— collectively ~47% of all misses, 83-99% zero-snapshot). This is a real,
separate finding worth its own follow-up (why these specific families
never get a first `market_history` snapshot at all, as distinct from the
general retention-boundary effect), out of scope to fix here. It is *not*
what drives §3's fail-open rate, though — see §3's own note on why that
section isn't contaminated by the retention mechanism.

## 2. The consequence of an uncalibrated cell

Confirmed directly from `_edge_gate_check` (`services/strategy_engine.py:
287-308`): when `delta_calibrated_for()` returns `0.0`, `p_est_side =
clamp(q_pre_now + 0.0) = q_pre_now`. The gate's comparison is `edge =
p_est_side - ask_now - fee`, admitted when `edge >= min_edge`. Substituting:

```
q_pre_now - ask_now - fee >= min_edge
  =>  ask_now <= q_pre_now - fee - min_edge
```

**Confirmed, not refuted: an uncalibrated cell is a strict "the price must
have moved in your favor since the pre-print snapshot" filter, not a
neutral pass-through.** `q_pre_now` is `unit_cost` at the most recent
`market_history` snapshot at or before `as_of - edge_gate_pre_print_
offset_sec` (10s), itself accepted up to `edge_gate_p_pre_max_age_sec`
(600s) stale — so in practice `q_pre_now` can reflect a price up to ~610s
before the print, not a clean "10 seconds before," and given §1's coverage
sparsity many real `q_pre` values sit near that stale end; `ask_now` is
the unit cost the trade would actually pay. Since `delta=0.0` means "no
measured edge from being right about direction," the gate falls back to
demanding the price already moved the trader's way before it will admit
the trade — the opposite of "chasing" a print, though a looser reading of
"since the pre-print snapshot" than the clean 10-second framing suggests.
The margin required is `fee + min_edge`, not a flat number: `fee =
taker_fee_per_contract(price, ticker) + 0.005`, so the floor is ~0.045 at
the cheapest/priciest contracts and closer to ~0.06 within the CLAUDE.md-
flagged 0.60-0.95 band specifically, where the per-contract fee itself is
larger.

**Correction, caught by adversarial review: this doesn't only matter as a
hypothetical.** The doc's first draft claimed "neither condition [`category
is None`, or an under-sized cell] is currently occurring for any real
cell, per §1" — §1 only established the second half (0 existing cells
below the floor). It said nothing about `category is None`, which §1's own
numbers show is common: **81.7% of p_pre-attached signals have no
`trade_category` row at all.** A third case also exists that neither draft
named: a `(category, price_band)` combination that simply has zero rows
attached returns the same 0.0 fallback (`confidence_calibration.py:479`'s
`_delta_cache.get(..., 0.0)`) — `trade_category.db` has categories
(`Climate and Weather`, `Economics`, `Entertainment`) that never produce a
cell at all. **Correct, narrower statement: no *existing* (category,
price_band) cell is under-sized, but a live signal with no category, or
one in a category that's never formed a cell, hits this strict filter
right now** — not a hypothetical confined to "a brand-new category." §3's
own "0 uncalibrated hits among 121 evaluable trades" is the actual
empirical check that this isn't currently biting the trades that make it
past the earlier fail-open filters, and that finding stands.

## 3. Headline number: how many of the recent trades would the gate reject

Queried the last 500 entry trades from `data/paper_broker.db` (`reason NOT
LIKE 'closed:%'`, most recent first) — 319 actually exist in that
selection. **Correction, caught by adversarial review: this is not a
lifetime shortfall.** `paper_broker.db` in full holds 633 rows, the oldest
0.82 days old — the broker was reset (`data/trade_archive.db`'s mtime
confirms the prior history was archived ~20 hours before this
measurement), so the entire 319-entry sample is one ~20-hour trading
session at roughly 400 entries/day, not "hasn't accumulated 500 entries
yet" in a general sense. Worth stating plainly since it bears on how much
weight to put on the specific numbers below: this is one narrow window,
not a long-run average, and the archived prior history isn't a usable
substitute for extending it — `market_history`'s own retention (§1) has
already pruned the price snapshots that history's own `p_pre` values would
need, which is itself the reason this kind of measurement can never look
back further than ~7 days regardless of how much trade history exists.

Simulated `_edge_gate_check`'s exact math per trade using **today's**
calibration state (the real question a flip-the-switch-now decision needs:
would enabling the gate right now have rejected these), not a
point-in-time-historical recalibration. One disclosed methodological
substitution, caught by adversarial review: this simulation attaches
category via `trade_category.categories_for_tickers` (the same source §1
uses), while the live gate's runtime category actually comes from
`market_lookup._category_by_ticker()` (`decision_bridge.py:125`, an
in-memory map built from `event_titles`) — same vocabulary, but with
materially denser real-time coverage than the DB table. No practical
divergence found in this run (0 of the evaluable trades hit a delta=0.0
fallback either way), but a live-enabled gate would see more categorized
signals than this offline simulation does.

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

## 3.5. Three follow-up questions (PM, after reading the above)

Three additional, cheap measurements requested before the user briefing,
using the same script and methodology as §3 (same live data, same
`_edge_gate_check` math, run immediately after §3 — no meaningful time gap
for the underlying data to have shifted).

### The decision-critical number: is the un-evaluable 61.4% disproportionately in-band?

This is the number the PM flagged as the one the decision actually turns
on: if the trades the gate *can't* see are concentrated in the 0.60-0.95
band, flipping the gate on would reject three-quarters of what it can see
while leaving the actual concern untouched — worse than useless for the
stated purpose.

```
                    evaluable    fail-open    total    fail-open rate
in-band (0.60-0.95)     54           70        124         56.5%
out-of-band              69          126        195         64.6%
```

**Answer: no, the un-evaluable set is not disproportionately in-band — if
anything, coverage is slightly *better* in-band.** 43.5% of in-band trades
are evaluable versus 35.4% out-of-band, an 8-point gap in the reassuring
direction. This doesn't eliminate the coverage gap (56.5% of in-band
trades still can't be evaluated at all), but it rules out the worse
failure mode the PM was checking for: the gate is not selectively blind to
the specific trades CLAUDE.md's negative-EV concern is about.

### Is the 61.4% fail-open rate a config limit or a data limit?

Tested by widening `edge_gate_p_pre_max_age_sec` far beyond its live 600s
value and re-running the same 319 trades' `market_history.recent_price`
lookups at each width:

```
max_age_sec=     600 (live default): p_pre found for 123/319 (38.6%)
max_age_sec=    1800:                p_pre found for 128/319 (40.1%)
max_age_sec=    3600:                p_pre found for 130/319 (40.8%)
max_age_sec=   86400 (1 day):        p_pre found for 134/319 (42.0%)
max_age_sec=  604800 (7 days):       p_pre found for 134/319 (42.0%)
```

**Answer: it's overwhelmingly a data limit, not a config limit.** Widening
the window 1,008x (600s → 7 days) recovers only 3.4 percentage points of
coverage (38.6% → 42.0%), and the last 6x of that widening (1 day → 7
days) recovers nothing at all — the curve has already flattened. This
confirms the missing snapshots aren't just outside a too-narrow lookback
window — for this specific population (all entries <1 day old, so §1's
retention-pruning mechanism cannot be the explanation here, per §3's own
correction above) they genuinely don't exist yet at trade time. **Which
markets drive this particular 20-hour window's gap wasn't separately
broken down** — §1's corrected root-cause analysis (retention pruning
plus a genuine `KXMVECROSSCATEGORY`/sports-match zero-coverage hole)
covers the full 30-day population, not specifically this recent slice, so
citing those same exemplar families here without checking would repeat
the shape of the original mistake rather than fix it. What's established
for this slice specifically: raising `edge_gate_p_pre_max_age_sec` is not
a viable fix for it, whatever markets are driving it; closing the gap
would require `market_history` capturing new-market snapshots faster (if
it's rotation-driven) or extending coverage to whichever families are
missing here (if it's the same zero-coverage-hole shape as §1's), neither
of which this pass separately confirmed for the §3 population.

### Rejection-rate sensitivity to `min_edge`

```
min_edge=+0.04 (live default): rejected  92/123 (74.8%)
min_edge=+0.03:                rejected  90/123 (73.2%)
min_edge=+0.02:                rejected  86/123 (69.9%)
min_edge=+0.01:                rejected  81/123 (65.9%)
min_edge=+0.00:                rejected  72/123 (58.5%)
min_edge=-0.01:                rejected  71/123 (57.7%)
```

**Answer: not flat — genuinely threshold-sensitive, but with a floor.**
The rejection rate falls steadily as `min_edge` is lowered (74.8% → 57.7%
across the tested range), so the 0.04 setting is doing real, tunable work,
not just crossing a wall that's fixed regardless of the threshold. But
**even at `min_edge=0.00`** — admitting anything with non-negative
estimated edge after fees, no profit cushion required at all — **58.5% of
evaluable trades are still rejected.** That's a separate, important
reading: a substantial majority of these trades are estimated to have
negative edge outright, not merely positive-but-below-threshold edge. The
rejection isn't primarily a borderline-tuning artifact; most of what gets
rejected is rejected by a wide margin.

## 3.6. What was missing from all of the above: did the gate actually reject losers?

**Caught by adversarial review, and this is the finding that most directly
bears on the flip decision: nothing above asks whether the trades the gate
would reject were actually losing trades.** Every number so far is a
volume/count measurement — how many trades the gate touches, not whether
touching them would have helped. That's a real gap in a doc meant to
inform an EV decision.

Matched each of the 319 entries to the first subsequent close on the same
ticker and parsed the broker's own recorded realized P&L
(`paper_broker.py:620`'s `reason=f"closed: {reason} (realized
{realized_pnl:+.2f})"`). **Confirmed net of fees, not gross — checked
directly from source rather than assumed, since the gate's own rejection
criterion is fee-inclusive and a gross-vs-net mismatch would make the
comparison meaningless:** `realized_pnl = self.mark_to_market(ticker,
exit_price) - pos.entry_fee - close_fee` (`paper_broker.py:611`), and
`mark_to_market()` itself (`:829-835`) is pure gross price-delta —
`direction * (current_price - entry_price) * size`, no fee term at all.
So `realized_pnl` subtracts both the entry fee and the exit fee from that
gross figure; every number below is **net of both trading fees**,
commensurable with the gate's own fee-inclusive `edge` criterion.

Independently reproduced (not just taken from the adversarial review) on a
slightly later snapshot of the same live data:

```
              entries  closed  still_open   sum realized    mean     win%
would-REJECT      94      85      12          +$7,159.49   +$68.84   78.8%
would-ADMIT       29      12       1          +$1,098.45   +$91.54   66.7%  (n=12, small)
fail-open        204     196       3          +$5,082.91   +$25.93   57.1%
```

(Counts differ slightly from §3's 92/31/196 split — this was run at a
later moment with a few more trades in the window and a different,
independently-written matching script; the qualitative shape is what
matters, not exact parity between the two runs.)

**Leading with the number that actually matters for an EV decision: the
would-REJECT cohort's net-of-fees realized P&L was +$7,159.49 over 85
closed positions — positive, and larger in total than would-ADMIT's
+$1,098.45 over 12 (though on far more trades, so not directly comparable
per-trade without more data).** The 78.8% win rate is supporting color,
not the headline — a high win rate alone is not evidence of positive EV
(a strategy can win often and still lose money on large losses), and this
repo's own retired 70%/70% win-rate target is a standing reminder of that
exact confusion. **On the dollar figure, which is the correct one: the
would-REJECT cohort was straightforwardly profitable, not a cohort of
losers.** A gate that had been on would have removed a profitable cohort,
in exchange for filtering out the (much larger) rejected volume.

**This is explicitly indicative, not a conclusion**, and every caveat that
applies to §3 applies doubly here: one ~20-hour session, ticker-level
"first close after entry" matching that doesn't correctly attribute P&L
under position-netting or partial closes, several rejected entries still
open (their eventual P&L unknown), and a session that happened to be
strongly profitable overall (bankroll 10,000 → 16,763), which could
compress or exaggerate any real difference between cohorts. **Stated
plainly rather than left implicit: this window can support "profitable in
this specific ~20-hour stretch," and nothing stronger — it cannot support
"profitable" as a general claim.** This app trades crypto and commodities
series whose character changes on short timescales; one session's regime
(direction, volatility, which whale signals fired) is not evidence about
any other session's. It does not show the gate is bad, and it does not
show the gate is good — it shows that **volume rejected is not evidence of
quality improved**, and this doc otherwise never checked the difference. A
real answer needs a longer window spanning multiple regimes, correct
netting-aware P&L attribution per entry, and ideally
several different market regimes, none of which this research pass
attempted.

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
actually goes today — and this doc's first draft overstated how much of
that already works, caught by adversarial review:** the original draft
claimed the *admitted* path's `decision["edge_gate"]` "already
transfers to observe-only unchanged," implying that side of the picture
was solved. Traced further: `decision["edge_gate"]` (populated at
`strategy_engine.py:811-812, 836-837`) has **zero consumers anywhere** in
the codebase (`grep -rn edge_gate main.py services/ static/ frontend/`
finds only those two writes) — it flows into `state["decision_feed"]`, a
transient ring buffer capped at 50 entries
(`services/whale_stream/decision_bridge.py:130-131`) plus a live
WebSocket broadcast, and the only thing that persists durably is
`candidate_ledger.record_decision(signal.id, decision.get("action",
"unknown"))` (`:129`), which stores the bare action string, not the detail
dict. **Nothing durable captures the admitted-path detail today either** —
the admit side isn't a solved problem observe-only inherits unchanged,
it's the same open problem the reject side has.

On the *rejected* path specifically, the caller (`evaluate()`, `:731-736`)
currently calls `candidate_log.record_rejection(ticker, "whale_follow",
gate_name, observed_value, threshold_value, side=side,
unit_cost=unit_cost)` — `record_rejection`'s own signature
(`services/candidate_log.py:116-119`) has no field for the full detail
blob; only `observed_value`/`threshold_value` (which the edge gate's call
site maps to `edge`/`min_edge` alone) get persisted. `q_pre` and `delta`
are computed but never captured anywhere today for a rejected signal —
`p_est` survives partially, embedded as text inside the human-readable
`reason` string (`strategy_engine.py:305-306`, reaching the decision dict
via `_skip(signal, validation.reason)` at `:736`), but only as an
unparsed sentence in a 50-entry transient ring, not a queryable field.

**Roughly what it would take, concretely:** (a) one new config key
(`edge_gate_observe_only` or similar); (b) a small conditional in
`_validate_entry_price` so observe-only still calls `_edge_gate_check` and
still attaches `edge_gate_detail`, but never turns a failing `edge_result`
into an early return; (c) real, durable capture of the full detail dict
(`p_est`/`q_pre`/`delta`/`edge`) on **both** the admit and reject paths —
extending `record_rejection`'s persisted fields (or a parallel capture
path) for rejections, and adding durable storage for admits too, since
`decision["edge_gate"]` today is not that despite superficially looking
like it. (a) and (b) are genuinely small; (c) is the part that turns
"cheap" into "small but real" — a schema/call-site change on *both* paths,
not a one-line flag flip. Not attempted here (out of scope — read-only
research), but this is concrete enough to size as a task, not a guess.

## Summary for the decision

**The single most important thing this doc can tell the decision-maker:
every number below is a volume/count measurement — how many trades the
gate touches — not an outcome measurement. §3.6 found (indicatively, on
one ~20-hour session, net of trading fees so it's commensurable with the
gate's own fee-inclusive rejection criterion) that the trades the gate
would have rejected were net **+$7,159.49**, not a losing cohort — the
dollar figure is the one that matters for an EV decision; its 78.8% win
rate is supporting color, not itself evidence of profitability, and
leaning on win rate alone is the same mistake behind this repo's own
retired 70%/70% target. That window can support "profitable in this
specific stretch" and nothing stronger — one session says nothing about
another in markets whose regime shifts this fast. Nothing here shows
flipping the gate on would improve results, and nothing here shows it
wouldn't — that question was never actually answered, and it's the one
that matters most.**

- The edge-gate math and its calibration inputs are sound and well-
  covered where they currently apply: all 18 real (category, price_band)
  cells clear the 50-signal floor with 3x+ headroom. But this doesn't mean
  the strict uncalibrated-cell filter (§2) never bites in practice —
  81.7% of signals that clear the earlier price-history filter still have
  no category at all, and a live signal with no category, or one in a
  category that's never formed a cell (Climate and Weather, Economics,
  Entertainment all exist in `trade_category.db` with zero cells), hits
  the strict filter today. §3's own empirical check (0 of 123 evaluable
  trades hit it) is the only reason to believe this isn't currently
  costing real admits — a narrower, more defensible claim than "isn't
  occurring for any real cell."
- The volume headline: **of trades the gate could actually evaluate, it
  would have rejected ~75% of them** (92/123) — a large, real effect,
  roughly evenly split between the CLAUDE.md-flagged 0.60-0.95 band and
  everything else, not concentrated where the concern was originally
  raised. This is a single ~20-hour trading session's worth of data (§3's
  own correction — not a lifetime-average sample), during an unusually
  profitable run (bankroll 10,000 → 16,763).
- **The gate can only evaluate 38.6% of entries at all** (61.4% fail
  open on missing price-history coverage) — flipping it on would filter
  roughly three-quarters of a minority of trades, not three-quarters of
  everything. **The root cause of that coverage gap was misdiagnosed in
  this doc's first draft** (caught by adversarial review): it isn't
  primarily fast-rotating 15-minute/hourly markets never getting a first
  snapshot — it's `market_history.retention_hours: 168` (7 days) pruning
  price history faster than the nominal 30-day calibration window assumes,
  plus a genuine, separate zero-coverage hole in `KXMVECROSSCATEGORY` and
  the sports-match families specifically. The practical consequence is the
  same either way (the gate can't evaluate a majority of entries), but the
  fix would be different: retention tuning or accepting the ~7-day
  effective window, not speeding up snapshot capture for specific market
  types. §3 itself (all entries <1 day old) is *not* contaminated by the
  retention mechanism, so its own 61.4% figure stands as measured.
- The un-evaluable 61.4% is not disproportionately in the 0.60-0.95 band
  (in-band coverage is actually 8 points *better* than out-of-band, §3.5)
  — the gate isn't blind precisely where the original concern lives, it's
  just incomplete everywhere. Widening `edge_gate_p_pre_max_age_sec`
  1,008x recovers only 3.4 points of coverage — this specific gap won't
  close via that config knob.
- Rejection rate is genuinely threshold-sensitive (not a flat wall), but
  even at `min_edge=0.00` — no profit cushion required at all — 58.5% of
  evaluable trades are still rejected, meaning most rejections are
  estimated to have outright negative edge, not borderline cases the
  threshold happens to catch. **This number describes the gate's own
  estimate of edge, not measured outcomes — §3.6 is the check of whether
  that estimate lined up with what actually happened, and on this sample
  it's genuinely unclear that it did.**
- Observe-only is worth proposing and is cheap on the compute side (the
  `_edge_gate_check` computation itself is symmetric), but needs more real
  work than this doc's first draft implied: **neither the admit path nor
  the reject path durably captures the full `p_est`/`q_pre`/`delta`
  breakdown today** — `decision["edge_gate"]` (the admit-path detail) has
  zero consumers and lives only in a 50-entry transient ring buffer;
  `record_rejection` (the reject-path capture) persists only `edge`/
  `min_edge`, not the full breakdown. Both sides need the same kind of
  durable-capture work, not just the reject side.
