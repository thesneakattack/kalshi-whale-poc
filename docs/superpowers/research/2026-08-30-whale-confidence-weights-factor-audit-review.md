# `whale_confidence_weights` Factor Audit — Stage 2 independent review

**Reviews:** `2026-08-30-whale-confidence-weights-factor-audit.md` (commit `f753bb8`).

**Verdict up front: revise before Stage 3 designs against it.** The audit's
*diagnosis* is correct and independently reproduced — every headline number in
§0–§3 re-derived here to the decimal. Its *forward-looking half* does not hold:
two of the three new-factor candidates it ranks highest are wrong (one is the
same bug class the document condemns, one isn't actually available), the factor
it singles out as clean is contaminated the same way as the ones it rejects, and
the guard it proposes would not catch most of the contamination it is written
for.

**Method.** Every claim below was re-derived from source and from the live DBs,
not read off Stage 1's tables. DBs opened `file:…?mode=ro` (`data/` is
gitignored and exists only in the primary checkout; reads only, no writes, no
git-state interaction). Row counts differ from Stage 1's by ~100 because
`signals` is still growing — the table was at 95,451–95,463 across my probes vs
Stage 1's 95,355.

---

## 1. Claims I independently verified

### 1.1 `depth_factor` zero-volume artifact — **CONFIRMED**

Re-derived from `signal_log.db`, replicating `_bucket_win_rates` exactly:

| quantity | Stage 1 | mine | verdict |
|---|---|---|---|
| `depth_factor` == 1.0 exactly | 22,833 (23.9%) | 22,822 (23.9%) | ✅ |
| …of those with `raw_volume_24h == 0` | 22,413 (98.2%) | 22,498 (98.6%) | ✅ |
| zero-volume win rate | 40.0% | 40.0% | ✅ |
| positive-volume win rate | 65.6% | 65.6% | ✅ |
| gap controlled to `volume > 0` | −28.6 → −17.4 | −28.6 → −17.4 | ✅ |
| saturated set series mix | 60% MVE + 36% BTC15M | 60.6% / 36.8% | ✅ |

`max(market_volume, 1.0)` (`confidence_scoring.py:194`) fabricating a 1-contract
denominator is real, load-bearing, and ~40% of the reported gap. No correction.

### 1.2 Units — **CONFIRMED, citation accurate**

`docs/kalshi/get-market.md` (the `volume_24h_fp` block, ~:233–236):

```yaml
volume_24h_fp:
  $ref: '#/components/schemas/FixedPointCount'
  description: String representation of the 24h market volume in contracts
```

`size` is contracts, so `depth_ratio = size / volume_24h` is contracts/contracts
— dimensionless, correct. There is no units error under the artifact argument.
`open_interest_fp` is likewise `FixedPointCount` (~:246–249) and is a **required**
field on the market object (:130).

### 1.3 `context_factor` / `depth_factor` shared artifact — **CONFIRMED, and Stage 1 understated it**

Stage 1 asserts the two tertiles "are the *same rows*" but evidences it with
**series composition**, which is a different quantity. I measured the row-level
intersection directly:

| measure | value |
|---|---|
| `depth_factor` high tertile ∩ `context_factor` low tertile | **27,217 rows** |
| share of `depth` high | **85.5%** |
| share of `context` low | **85.5%** |
| Jaccard | 74.7% |
| `raw_volume_24h == 0` share of each tertile | 70.7% / 70.3% |

Stage 1's "~74%/76%" is its series-composition figure (I reproduce 72.9%/76.2%),
which numerically coincides with the Jaccard by accident. The claim is right and
**stronger than argued** — but the stated evidence does not establish it. Fix the
evidence, keep the conclusion. Controlled gap +24.9 → +11.0 reproduces exactly.

### 1.4 `_bucket_win_rates` tie detection — **CONFIRMED at source, and worse than described**

`confidence_calibration.py:104–106`:

```python
distinct_values = {round(r["factors"][factor_name], 6) for r in sorted_rows}
if n < _BUCKET_COUNT or len(distinct_values) < _BUCKET_COUNT:
    return {}
```

The guard is `len(distinct) < 3`. A factor with a 64.2% tied mode and 64 distinct
values sails through. Confirmed. My own re-derivation of Stage 1's §2 table
matches every row:

| factor | low_edge | high_edge | valid? |
|---|---|---|---|
| `depth_factor` | 0.0188 | 0.1976 | valid |
| `unusualness_factor` | 0.2200 | 0.6800 | valid |
| `proximity_factor` | 0.0000 | 0.0000 | **INVALID** |
| `context_factor` | 0.5000 | 0.8981 | valid |
| `agreement_factor` | 0.5000 | 0.8400 | valid |
| `cluster_factor` | 0.3333 | 1.0000 | valid |
| `trend_factor` | 0.5000 | 0.5000 | **INVALID** |

**One addition Stage 1 missed, which strengthens its case:**
`resolved_signals_with_factors()` (`signal_log.py:611–613`) has **no `ORDER BY`
at all**. Row order is therefore unspecified by SQL semantics and rowid order in
practice. Stage 1 calls the tie-break "arrival order"; it is more accurately
*undefined order that happens to be insertion order today* — which is a stronger
argument for the guard, not a weaker one, because nothing pins it.

Tied-mass arrival-order drift reproduces exactly: `proximity` 71.5% → 40.1%,
`trend` 64.8% → 45.0%.

### 1.5 `raw_spread` "logged on all rows, never read" — **CONFIRMED as stated, but see §2.1**

`raw_spread` is non-NULL on all 95,454 rows; >0 on 40,655. `grep -rn raw_spread
services/ main.py frontend/ tools/` returns only `signal_log.py` (schema, INSERT,
SELECT, and packing into the report input dict). No consumer reads it. Confirmed.
The tertile gap −15.7 also reproduces exactly. **What that number means is
another matter — see §2.1, my most important finding.**

### 1.6 Everything else in §0–§3 — **CONFIRMED**

Ran the app's own `generate_calibration_report()` verbatim against live rows:

- Per-factor gaps: depth −28.6, unusualness −18.0, proximity −20.3, context
  +24.9, agreement +12.0, cluster +14.0, trend +13.3, analyst/block `None`. ✅
- `suggested_weights` → `context_factor: 0.34`. ✅ Live weights in
  `settings.yaml:131–140` match Stage 1's report exactly.
- §0.1 price-calibration table: all ten deciles reproduce within 0.1 pt.
- §0.2 Brier: market 0.1580, composite 0.2455, `corr = 0.264`. ✅
- §3 bands: all six reproduce exactly; max score ever 0.9100, 64 rows ≥0.90. ✅
- §1.3 proximity: 73.5% tied at 0.0, ≥0.99 group is 95.7% `KXBTC15M`, honest gap
  **−3.7**. ✅
- §1.7 trend: 9.9% / 64.2% / 25.9% at 53.8% / 51.6% / 81.7%; with-trend vs
  neutral **+30.1**. ✅

### 1.7 `block_trade_factor` structurally dead — **CONFIRMED, and upgraded from sample to census**

Stage 1's own falsification condition #3 notes it sampled 2M of 33.1M rows. I ran
the full table:

```
SELECT is_block_trade, COUNT(*) FROM raw_trades GROUP BY is_block_trade
-> [(0, 33177986)]   in 42.7s
```

**All 33,177,986 captured raw trades, no exceptions.** Falsification condition #3
is closed — "structurally dead" is now a census result, not an inference. Stage 3
can treat this as settled.

---

## 2. Claims that are overstated or wrong

### 2.1 `raw_spread` (N2) is the *same bug class* the document condemns — **the headline correction**

Stage 1 ranks N2 as a top candidate ("the cheapest of all: already logged on
every one of the 95,355 rows, and never read", gap −15.7) and D3 recommends it.
It never applies its own two tests to it. Both fail.

**It is a fabricated missing value.** `kalshi_trade_tape.py:746`:

```python
yes_ask = float(market.get("yes_ask_dollars") or price)
...
"spread": round(max(yes_ask - price, 0.0), 4),
```

When there is no ask data, `yes_ask` falls back to `price`, producing spread
**exactly 0.0** — the comment says so outright ("no ask data = assume no
spread"). This is precisely the `max(volume, 1.0)` failure Stage 1 correctly
condemns in §1.1 and D1, in a second location. `market_catalog` has no ask
column at all (`market_fetch.py:165–172` documents this as a previously-shipped
live bug), so catalog-sourced markets always take the fallback.

**Its tertile split is contaminated.** 54,799 rows (57.4%) are exactly 0.0. The
low tertile is 31,818 rows and `low_edge == 0.0` — the **entire low bucket** is
drawn from the fabricated-zero mass in rowid order. Within that mass the
arrival-order thirds run **71.8% → 44.9%**, the same time signal §2 identifies.

**The honest numbers:**

| comparison | gap |
|---|---|
| Stage 1's tertile gap | −15.7 |
| honest: spread == 0 (60.1%) vs spread > 0 (58.9%) | **−1.2** |
| controlled for `raw_volume_24h > 0` | **+1.1** (sign flips) |

The effect is ~13× overstated and does not survive the control. **N2 is not a
lead; it is the third instance of the bug the document is about.**

**It is also mislabeled.** Stage 1 calls it "Bid/ask spread". `yes_bid_dollars`
is not in `_MARKET_FIELDS` (`market_fetch.py:33–47`) and never reaches the
provider. The quantity is `max(best_ask − last_traded_yes_price, 0)` — a
one-sided distance between two different things, not a spread. Under CLAUDE.md's
"a displayed value must match its label", this needs renaming wherever it
survives.

### 2.2 N3 `open_interest_fp` is **not** already in the market dict — **WRONG**

Stage 1: "present in the same market dict already passed into
`composite_confidence_breakdown`", "requiring no new join or API call", and D3
ranks N3 above N1 on that basis.

`state["markets"]` is assigned at `main.py:795` from
`_resolve_and_record_settlements`, which (`main.py:299–356`) does
`slimmed = [_slim_market(m) for m in markets]` and returns `slimmed`.
`_slim_market` (`market_fetch.py:51–52`) projects onto a 10-field allowlist:

```
ticker, volume_24h_fp, event_ticker, close_time, strike_type,
occurrence_datetime, status, yes_ask_dollars, can_close_early,
expected_expiration_time
```

`open_interest_fp` is **not** in it and is discarded before scoring. It survives
only on the off-watchlist `_resolve_unknown_markets` path
(`kalshi_trade_tape.py:464`, `get_markets_by_tickers`), which does not slim.

So N3 is available on one path and stripped on the other — a **partial-coverage
field**, which is exactly the shape that produced the `trend_factor` and
`raw_spread` defects. The fix is genuinely cheap (one allowlist entry, pinned by
`tests/test_kalshi_contracts.py::test_slim_market_keeps_every_documented_field_it_declares`),
but it is a code change with a data-plane consequence, not a free read, and D3's
ranking rationale rests on a false premise.

### 2.3 §1.2's own table does not reconcile with its −18.1 headline — **conclusion right, mechanism wrong**

Stage 1's §1.2 table has n=20,861 per bucket at 53.5% / 50.7% — a gap of **−2.8**,
presented as the explanation of a **−18.1** headline. Those are different
populations. 20,861 ≈ 62,688/3: the table is tertiles of the **priced subset**
only (rows carrying `price`), not of the full 95k.

| population | n | unusualness tertile gap |
|---|---|---|
| full (the −18.1 headline) | 95,459 | **−18.1** |
| priced subset (Stage 1's table) | 62,688 | **−2.8** |
| unpriced only | 32,771 | **−43.2** |

**A third confound Stage 1 never names:** unpriced rows (34.3% of the
population, pre-`price`-column era) win **74.0%** vs the priced subset's
**52.1%**, and are unevenly distributed across the unusualness tertiles (38.9%
of the low bucket vs 33.2% of the high). The −18.1 is substantially an era-mix
artifact — the same undated-blending problem §2 flags for
`resolved_signals_with_factors()`, biting a second factor.

**The conclusion survives and I can evidence it better.** Win rate by
unusualness decile on the priced subset, split by which side of 0.5 the price is:

| unusualness | n | aggregate | favourites | longshots |
|---|---|---|---|---|
| 0.0–0.1 | 9,439 | 49.8% | 97.1% | 3.0% |
| 0.2–0.3 | 4,672 | 53.0% | 86.8% | 12.0% |
| 0.4–0.5 | 3,857 | 50.0% | 74.8% | 24.5% |
| 0.6–0.7 | 4,873 | 50.9% | 68.7% | 32.8% |
| 0.8–0.9 | 7,326 | 50.5% | 58.5% | 40.7% |
| 0.9–1.0 | 6,839 | 51.0% | 53.1% | 48.9% |

The aggregate is **flat at ~50%** across all ten deciles while the two
conditional curves converge monotonically. That is a clean demonstration that
`unusualness_factor` carries no accuracy information once you condition on side
— better evidence for Stage 1's thesis than the two-bucket table it used.

### 2.4 The Brier constant-benchmark uses the wrong population's base rate — **minor, and it favours Stage 1**

Stage 1 benchmarks a "constant 0.596 base rate" at Brier 0.2553 against the
62,584 priced rows. 0.596 is the **overall** win rate (I reproduce it: 59.6%);
the priced subset's own base rate is **52.1%**. The correct constant-predictor
Brier for that population is **0.2496**, not 0.2553. The composite scores 0.2455.
So the formula beats a constant by 0.004, not 0.010 — the "barely better than a
constant" conclusion is **stronger** than stated, not weaker.

### 2.5 Stale unit-cost band — **minor**

Stage 1 repeats "the 0.60–0.95 unit-cost band". Live config is
`min_unit_cost: 0.5` / `max_unit_cost: 0.9` (`settings.yaml:59–60`), and
`kalshi_fees.breakeven_unit_cost`'s docstring explicitly flags the older figure
as stale as of 2026-08-30. Worth correcting because the "do not invert" argument
turns on where that band sits.

---

## 3. What Stage 1 missed

### 3.1 Its proposed guard is insufficient — 5 of 7 factors still cut inside a tie

D1 proposes "a `low_edge != high_edge` assertion". That catches only the two
factors already known. The real hazard is a cut landing **inside** a tied value,
which happens at either boundary independently of whether the two edges are equal:

| factor | S1 guard | low/mid cut in tie | mid/high cut in tie | modal share |
|---|---|---|---|---|
| `depth_factor` | pass | no | no | 23.9% |
| `unusualness_factor` | pass | **yes** | **yes** | 3.5% |
| `proximity_factor` | **FAIL** | yes | yes | 73.5% |
| `context_factor` | pass | **yes** | **yes** | 9.5% |
| `agreement_factor` | pass | **yes** | **yes** | 25.5% |
| `cluster_factor` | pass | **yes** | **yes** | 50.7% |
| `trend_factor` | **FAIL** | yes | yes | 64.2% |

Stage 1's §2 table declares five of these "valid" that are not. The correct
predicate is *does the boundary value appear on both sides of the cut*, not
*are the two edges equal*.

### 3.2 `agreement_factor` is not the clean factor Stage 1 says it is — **most consequential miss**

Stage 1: "sound computation, real and artifact-robust discrimination… the only
factor whose gap is essentially unchanged by the artifact control… currently
under-weighted relative to its evidence." That recommendation does not survive.

25.5% of rows (24,341) sit at exactly 0.5, and the low/mid cut lands inside that
mass. Within it, arrival-order thirds run **67.7% → 33.3% — a 34.3-pt pure
arrival-order spread**, larger than `trend_factor`'s. Measured tie-safely by
value instead of by index tertile:

| bucket | n | share | win rate |
|---|---|---|---|
| < 0.5 (disagree) | 19,641 | 20.6% | 59.3% |
| **= 0.5 (default, no recent prints)** | **24,341** | **25.5%** | **44.8%** |
| > 0.5 (agree) | 51,483 | 53.9% | 66.7% |

Honest agree-vs-disagree gap: **+7.5** (**+8.9** controlled for `volume > 0`) —
not +12.0/+11.7. **Both are below `_MIN_DISCRIMINATION_GAP` = 10**, so under an
honest measurement `agreement_factor` would not qualify as discriminating at all.

Note also the 0.5 default wins **44.8%**, *below both* real buckets — it is not
neutral in outcome terms. That is the identical coverage defect Stage 1
diagnosed for `trend_factor` (§1.7) and did not apply here.

### 3.3 `_suggested_weights` cannot express the audit's own findings

`confidence_calibration.py:141–150` clamps every negative gap to 0.0:

- depth (−28.6), unusualness (−18.0) and proximity (−20.3) all receive the
  **same** floored weight. The instrument cannot distinguish actively
  anti-predictive from mildly anti-predictive, and can never propose removing or
  inverting anything — only shrinking toward a floor.
- `_MIN_SUGGESTED_WEIGHT = 0.05` is applied **before** renormalisation, so the
  realised floor is **0.04**, not the documented 0.05. Small, but a
  label-vs-value mismatch in the same class CLAUDE.md calls out.

### 3.4 Auto-apply's harm is double, not single

Stage 1 warns that applying `suggested_weights` would raise `context_factor` to
0.34. I ran `blended_weights_for_auto_apply` against live weights. It would also
cut **`trend_factor` from 0.3131 to 0.1818**:

| factor | live | after auto-apply |
|---|---|---|
| `context_factor` | 0.2121 | **0.3434** |
| `trend_factor` | 0.3131 | **0.1818** |
| `cluster_factor` | 0.1717 | 0.1919 |
| `agreement_factor` | 0.1818 | 0.1616 |

So auto-apply would harden the zero-volume artifact **and simultaneously halve
the weight of the factor Stage 1 believes is the strongest real signal in the
set**, both driven by the same broken measurement. This strengthens D1's
"prerequisite" framing; it should be stated.

---

## 4. Verdict on the edge reframe (incl. the coordinator's dual-score update)

The human's steer — score accuracy **and** edge independently, not one replacing
the other — is better supported by this app's data than Stage 1's D2, and I can
evidence it.

**Realized edge is computable per signal.** Verified by running it:
`kalshi_fees.unit_cost(side, price)` + `taker_fee_per_contract(uc, ticker)`, with
the outcome from `correct`. Mean realized edge across priced rows:
**−$0.0098/contract**.

**But the two scores cannot share a population — the key design constraint:**

| score | rows | share | base rate |
|---|---|---|---|
| accuracy (needs `correct`) | 95,458 | 100% | 59.6% |
| edge (needs `correct` + `price`) | 62,687 | 65.7% | **52.1%** |

32,771 rows (34.3%) have no `price` and never will. The populations differ by
7.5 pts of base rate, so the two scores are not measurable on common ground —
any Stage 3 design must treat that asymmetry explicitly rather than reporting
both as if commensurable.

**Inside the live tradeable band (0.5–0.9) only 24,707 rows survive (25.9% of
all), win 68.7%, and carry a mean realized edge of −$0.0149/contract.** That is
the ROADMAP's negative-EV band, now *measured* rather than designed — a
load-bearing number Stage 1 did not compute, and the strongest single argument
for the reframe.

**Five of seven factors flip sign between the two objectives** (in-band):

| factor | accuracy gap (pts) | edge gap ($/contract) |
|---|---|---|
| `depth_factor` | −4.5 | **+0.0102** |
| `unusualness_factor` | −27.4 | **+0.0155** |
| `proximity_factor` | −1.5 | −0.0022 |
| `context_factor` | +3.1 | **−0.0077** |
| `agreement_factor` | −0.6 | +0.0037 |
| `cluster_factor` | +2.0 | **−0.0021** |
| `trend_factor` | +1.6 | +0.0160 *(tied split — invalid)* |

This is direct evidence that **one blended score provably cannot serve both
objectives**: the factors that predict "you were right" are largely the factors
that predict "you overpaid". Two separate composites are required — not one
reweighted formula. The human's instinct is correct and the data supports it.

**Consequence Stage 1 did not anticipate: "remove `unusualness_factor`" does not
survive the dual framing.** Under an edge objective, in-band, unusualness has the
**largest positive edge gap in the set** (+$0.0155) — high unusualness = price
near 0.5 = cheaper contracts = better edge. Deleting it (Stage 1's §1.2
recommendation and D2 rationale) would remove the most edge-informative term.
Under a dual design it belongs in the **edge** score, not in the bin. Its
accuracy-side removal remains correct.

**"Do NOT invert `unusualness_factor`" — CONFIRMED, reasoning sound.** The
decile table in §2.3 is the direct test: the aggregate response curve is flat at
~50% across the entire range of the factor. A flat curve carries no exploitable
information in *either* direction, so inversion cannot help — and the specific
harm Stage 1 names is real, since inverting means preferring prices far from 0.5,
which on the favourite side is the measured −$0.0149 band. The one repair needed
is the stale band figure (§2.5). This is the judgment call the task flagged as
having real money consequences, and Stage 1 got it right.

**One caveat on the edge score Stage 3 must carry:** edge is mechanically a
function of `unit_cost`, and several factors are functions of `unit_cost` too, so
edge gaps are exposed to their own identity confound — the mirror of §0.1. It is
not a confound-free objective, just a differently-confounded one.

---

## 5. Recommendation

**Stage 1 must revise before Stage 3 designs against it.** The document is
~70% solid and the revisions are surgical, not structural — §0, §1.1, §1.3–1.4,
§1.7–1.9, §2 and §3 stand as written and can be designed against today.

Required edits, in priority order:

1. **Withdraw N2 (`raw_spread`) as a candidate** and re-file it under §1's
   defect list as the third instance of the fabricated-missing-value bug
   (§2.1). Its honest gap is −1.2, +1.1 controlled. Correct the "bid/ask
   spread" label.
2. **Correct N3's availability claim** — `open_interest_fp` is stripped by
   `_slim_market`'s allowlist on the watchlist path and present only on the
   off-watchlist path (§2.2). Re-rank N3 vs N1 on the corrected cost; N3 may
   still win, but not for the stated reason.
3. **Downgrade `agreement_factor` from "the one clean factor"** to "contaminated
   like the rest; honest gap +7.5/+8.9, below the discrimination threshold" and
   withdraw the up-weight recommendation (§3.2). Add its 0.5-default coverage
   defect alongside `trend_factor`'s.
4. **Strengthen D1's proposed guard** from `low_edge != high_edge` to a
   boundary-in-tie predicate, which is what 5 of 7 factors actually need (§3.1).
5. **Fix §1.2's population conflation** — state that the table is the priced
   subset (−2.8) and that the −18.1 headline is substantially the
   priced/unpriced era mix (§2.3). Swap in the decile table as the evidence.
6. **Record the dual-objective framing** from the human's steer, with the
   coverage asymmetry (100% vs 65.7%, 59.6% vs 52.1%), the in-band −$0.0149,
   and the five sign flips (§4) — and reverse "remove `unusualness_factor`" to
   "move it to the edge score".
7. Minor: block-trade census result (§1.7), Brier base-rate correction (§2.4),
   stale 0.60–0.95 band (§2.5), `_suggested_weights` clamp + 0.04 floor (§3.3),
   auto-apply's trend-factor cut (§3.4), context/depth row-overlap 85.5% (§1.3).

**Disposition (investigation-to-guard).** The tie-aware bucketing guard already
proposed in D1 covers §3.1 once strengthened. §2.1 and §2.2 argue for one shared
runtime diagnostic — *share of rows where a factor's input was fabricated or
absent* — since the same defect has now been found in three independent places
(`volume_24h`, `yes_ask_dollars`, `momentum()` coverage). That is a bug class,
not three bugs, and it is the finding with the most reuse in it.
