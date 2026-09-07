# `whale_confidence_weights` Factor Audit — revision review (Stage 2b)

**Reviews:** the revision at commit `ca8ffca` against Stage 2's review (`d0a7b50`)
and the live DBs directly — not by reading what either prior agent wrote and
picking a side. Every number below was re-derived from `data/signal_log.db`
(`file:…?mode=ro`, primary checkout, read-only) and `data/series_watcher.db`,
and against the actual shipped code (`services/confidence_scoring.py`,
`services/whale_calibration/confidence_calibration.py`,
`services/whalewatchers/kalshi_trade_tape.py`, `services/kalshi_fees.py`,
`services/market_watch/market_fetch.py`, `main.py`), including running
`generate_calibration_report()` and `blended_weights_for_auto_apply()` live
against current data and current `config/settings.yaml` weights. `signals` was
at n = 95,773–95,882 across my probes (vs. the revision's 95,526) — the table
is still growing; per the task's own instruction this is expected and is not
by itself a discrepancy.

**Verdict up front: GO for Stage 3 on the core findings, with one bounded
follow-up patch (four items, all specified below) that does not block design
work from starting.** The disputed number is resolved — both figures in
dispute are correct, and they are genuinely different claims, not competing
versions of one number. But re-deriving *around* that dispute surfaced that
the revision's own commit message describes correcting Stage 2's findings, and
did — for four of them — while silently dropping five smaller items from Stage
2's own "required edits" list, one of which (§1.2's population conflation) is
not cosmetic.

---

## 1. The disputed number, independently re-derived

Two different "N of 7" claims exist in this pipeline and the task description
correctly treats them as possibly-conflated. I derived both from scratch.

### 1a. §2's tie-boundary claim: **6 of 7 confirmed**, with a caveat on precision

The document's predicate: "does the boundary value appear on both sides of the
cut," tested at both the low/mid and mid/high tertile cuts, replicating
`_bucket_win_rates`' exact index-tertile logic. I computed this directly, and
additionally measured *how many rows* sit at the tied value straddling each
cut (materiality), which neither prior document reports:

| factor | low/mid tie | mid/high tie | tie size at each boundary |
|---|---|---|---|
| `depth_factor` | tie (flickers run-to-run) | tie (flickers run-to-run) | **2 rows / 6 rows** (<0.01% each) |
| `unusualness_factor` | yes | yes | 1,538 / 1,323 (1.6% / 1.4%) |
| `proximity_factor` | yes | yes | 70,285 / 70,285 (73.4% both) |
| `context_factor` | yes | technically yes | 1,675 (1.75%) / **32 rows (0.03%)** |
| `agreement_factor` | yes | technically yes | 24,411 (25.5%) / **31 rows (0.03%)** |
| `cluster_factor` | yes | yes | 9,599 / 48,670 (10.0% / 50.8%) |
| `trend_factor` | yes | yes | 61,646 / 61,646 (64.3% both) |

Read as a **materiality-weighted** predicate (does the tied mass at the cut
represent a meaningful fraction of the bucket, i.e. large enough to plausibly
move a win-rate gap by more than noise), exactly **six factors — every one
except `depth_factor` — have at least one materially-sized tied boundary**,
matching the revised document's table and Stage 2's own table (not Stage 2's
"5 of 7" *prose* in its §3.1 header, which undercounts its own adjacent
table by one — a drafting slip, not a re-measurement; the revision's "6 of 7"
is the correct fix of that slip, using Stage 2's table as ground truth rather
than its header text).

**Caveat, new here, worth carrying into D1's implementation, not into this
document's numbers:** read *literally* (any tie at all, no materiality floor),
the predicate is unstable. I ran the exact same query five times in quick
succession as the live table grew by a handful of rows between calls, and
`depth_factor`'s boundary-tie status flickered between 0, 1, and 2 tied
boundaries — because ~50% of `depth_factor`'s values are exact float
duplicates (integer contract sizes over a shared `volume_24h` snapshot
produce identical ratios), so *some* boundary landing on a 2–6-row duplicate
cluster by chance is close to guaranteed, not a real contamination signal.
`context_factor` and `agreement_factor` show the same knife-edge behavior at
their *second* boundary (32 and 31 rows respectively) — immaterial, but
technically "tied." **This means "6 of 7" is correct in substance and matches
what actually explains the reported gaps, but the document's predicate as
literally worded has no materiality floor and will flap on live data exactly
as I observed.** D1's CI guard needs a minimum-tied-fraction threshold (e.g.
≥1% of the bucket), not a bare boundary-value-equality test, or it will
alternately pass and fail `depth_factor` from one CI run to the next on inert
noise. This doesn't change any number in the document — it's a spec gap for
the guard's implementation, which is Stage 3/D1 territory, not a defect to
send back for another revision pass.

Stage 1's proposed `low_edge != high_edge` guard: confirmed it fails
(catches) exactly **`proximity_factor`** (0.0/0.0) and **`trend_factor`**
(0.5/0.5) — 2 of the 6 materially-tied factors, as both documents state.

### 1b. §4.1's sign-flip claim: **5 of 7 confirmed**, independently reproduced end to end

In-band (`0.5 ≤ unit_cost ≤ 0.9`), index tertiles, accuracy gap (pts) vs. mean
realized edge gap ($/contract):

| factor | accuracy gap | edge gap | flip? |
|---|---|---|---|
| `depth_factor` | −4.6 | **+0.0081** | **FLIP** |
| `unusualness_factor` | −27.5 | **+0.0141** | **FLIP** |
| `proximity_factor` | −1.4 | −0.0021 | no (both neg) |
| `context_factor` | +3.3 | **−0.0062** | **FLIP** |
| `agreement_factor` | −0.7 | **+0.0025** | **FLIP** |
| `cluster_factor` | +1.9 | **−0.0035** | **FLIP** |
| `trend_factor` | +1.5 | +0.0140 | no (both pos; 86.9% of in-band rows tied at 0.5 — matches the document's 86.8% tied-split caveat) |

**Exactly 5 of 7 flip, the same 5 factors the document names**
(`depth`, `unusualness`, `context`, `agreement`, `cluster`). In-band population:
n = 24,820 (doc: 24,727), win 68.7% (**exact match** to the doc), mean edge
**−$0.0156/contract** (doc: −$0.0150 — 0.4pp population drift explains the
small gap, not an error). Overall priced-population mean edge: **−$0.0098**
(doc: −$0.0097). Both numbers reproduce.

**This is a different claim from §2's "6 of 7"** — one is about whether a
factor's tertile *cut* lands inside a tied value (a measurement-instrument
defect), the other is about whether a factor's *effect* on accuracy and on
realized edge point the same direction (a design constraint). The revision's
own text treats them as separate throughout and never conflates them; my
independent re-derivation confirms both numbers stand on their own and there
is no actual disagreement to resolve between "5" and "6" — they were never
the same measurement.

---

## 2. Other claims independently re-derived

| claim | doc's number | my re-derivation | verdict |
|---|---|---|---|
| `raw_spread` honest gap, uncontrolled | −1.2 | **−1.4** (zero 57.4% win 60.1%, pos win 58.8%) | **CONFIRMED** (sign, order of magnitude, ~13× overstatement vs. Stage 1's −15.7 all match; small drift from live growth) |
| `raw_spread` honest gap, volume-controlled — sign flip | +1.1 | **+1.1** | **CONFIRMED exactly** |
| `kalshi_trade_tape.py:746` citation | `yes_ask = float(market.get("yes_ask_dollars") or price)` | read at source: **exact match**, line 746; `"spread": round(max(yes_ask - price, 0.0), 4)` at line 755 | **CONFIRMED** |
| `agreement_factor` honest gap, uncontrolled | +7.4 | **+7.4** (disagree 59.2%, agree 66.6%) | **CONFIRMED exactly** |
| `agreement_factor` honest gap, volume-controlled | +8.9 | **+8.8** | **CONFIRMED** (within live-data noise) |
| `agreement_factor` 0.5-mass arrival-order spread | 34.3 pts (67.7%→33.3%) | **34.2 pts** (67.6%→33.5%) | **CONFIRMED** |
| `context`/`depth` row-level overlap | 85.5% | **85.7%** | **CONFIRMED** |
| Jaccard | 74.7% | **74.9%** | **CONFIRMED** |
| Full block-trade census | 33,221,747 rows, single group | **33,472,164 rows, single group** `(0, N)` | **CONFIRMED** (grew ~250k rows between the revision's snapshot and mine — expected, the table is live; zero exceptions either time, so "structurally dead" stands) |
| N3 `open_interest_fp` stripped by `_slim_market` | not in the 10-field allowlist | read `market_fetch.py:33–52`: `_MARKET_FIELDS` is exactly the 10 fields quoted, `open_interest_fp` absent; `main.py:313` (`slimmed = [_slim_market(m) for m in markets]`) and `:795` (`state["markets"] = ...`) confirmed at cited lines | **CONFIRMED** |
| §4.1's factor-assignment table vs. its own sign-flip table | internally consistent | checked row by row — consistent; `trend_factor`'s nominally-larger edge gap is correctly excluded from "largest credible" on the stated tied-split caveat, which I independently confirm is real (86.9% tied at 0.5 in-band) | **CONFIRMED, internally consistent and usable as a Stage 3 input** |
| Live `generate_calibration_report()` run against current weights | gaps −28.6/−18.1/−20.3/+24.9/+12.0/+14.1/+13.3 | ran the actual function: **−28.7/−18.0/−20.4/+24.8/+12.1/+13.8/+13.3** | **CONFIRMED**, all within 0.3 pt |

Nothing checked here turned up a wrong number. Every claim I traced to source
code matched the cited line exactly.

---

## 3. What the revision dropped — real gaps, not re-openings

Stage 2 issued a 7-item, priority-ordered "required edits" list. The revision
executed items 1–4 (raw_spread withdrawal, N3 repricing, agreement_factor
downgrade, tie-guard predicate) and item 6 (dual-objective §4.1) faithfully —
all independently confirmed above. It also silently skipped one medium item
and three of six minor items bundled in item 7. None of these change any
verdict Stage 3 needs to act on, but they are real, checkable, and Stage 2
already did the work — leaving them out means that work gets lost rather than
carried forward.

**3.1 — §1.2's population conflation (Stage 2 §2.3, priority item 5) — not
fixed, and the document's own text still shows it.** The current §1.2 table
is byte-for-byte Stage 1's original (n=20,861 per bucket, 53.5%/50.7%), which
Stage 2 demonstrated is the *priced-subset* tertile split (62,688-ish rows),
not the full population the −18.1 headline is computed over. I reproduced
Stage 2's three-population breakdown fresh:

| population | n | unusualness tertile gap |
|---|---|---|
| full (the −18.1 headline) | 95,882 | **−18.0** |
| priced subset (§1.2's actual table) | 63,111 | **−2.8** |
| unpriced only | 32,771 | **−43.2** |

All three numbers reproduce exactly against Stage 2's figures. §1.2's verdict
("mathematical identity, not a finding") is correct regardless of which table
backs it, so this is not a wrong conclusion — but the table currently shown is
the misleading one Stage 2 flagged, presented without the population caveat,
and the stronger, cleaner evidence Stage 2 produced (the flat-aggregate/
diverging-conditionals decile table, which Stage 2 called "better evidence for
Stage 1's thesis than the two-bucket table it used") never made it in.

**3.2 — auto-apply's trend-factor cut (Stage 2 §3.4) — missing from D4, and
still live.** I ran the actual `blended_weights_for_auto_apply()` against
current `config/settings.yaml` weights and current data, not a hand
recomputation:

```
current: context_factor 0.2121 → trend_factor 0.3131
after:   context_factor 0.3434 → trend_factor 0.1818
```

Confirmed exactly, live, today. D4 in the current document cites only the
`context_factor` → 0.34 risk as its evidence for "no weight should move before
D1 lands." It omits that the same auto-apply pass would simultaneously nearly
halve `trend_factor` — the **largest weight in the formula** and, per the
document's own §1.7 verdict, "plausibly the strongest real factor in the
set." This is the stronger half of Stage 2's warning and the one most likely
to change how urgently Stage 3 treats gating `auto_apply_enabled`; leaving it
out weakens D4's own argument.

**3.3 — two purely minor items also missing:** the Brier constant-benchmark
in §0.2 still reads `0.2553` (the overall 59.6% base rate) rather than Stage
2's corrected `0.2496` (the priced subset's actual 52.1% base rate) — the
correction *strengthens* §0.2's "barely better than a constant" conclusion, so
leaving it out isn't misleading, just incomplete. And `_MIN_SUGGESTED_WEIGHT`'s
documented 0.05 floor vs. its actual pre-renormalization 0.04 (Stage 2 §3.3)
isn't mentioned anywhere in the current document.

None of §3.1–§3.3 contradicts anything the revision did land, and none
requires re-deriving anything further — I already have the settled numbers
above for all four. This is a carry-through gap, not a re-opened dispute.

---

## 4. Recommendation

**GO for Stage 3.** The load-bearing content — the dual accuracy/edge
framing (§4.1), the tie-detection predicate correction, the raw_spread and
open_interest_fp corrections, the agreement_factor downgrade, and every
number I independently re-derived in §1–2 above — is solid, reproducible from
live data and live code today, and internally consistent. Stage 3 can design
against it now.

**Bounded follow-up, not a blocking revision pass** (all numbers already
settled above, no further investigation needed):

1. Fix §1.2 to state its table is the priced subset (−2.8), name the full-
   population headline (−18.0/−18.1) as substantially a priced/unpriced era
   mix (−43.2 on the unpriced-only slice), and swap in the decile table —
   Stage 2 already wrote both, they just weren't carried over.
2. Add the trend_factor 0.3131→0.1818 auto-apply cut to D4 alongside the
   context_factor 0.34 raise — the stronger half of that warning.
3. Two one-line minor fixes: Brier benchmark 0.2553→0.2496 in §0.2; note
   `_MIN_SUGGESTED_WEIGHT`'s realized floor is 0.04, not the documented 0.05.
4. Carry the materiality-threshold caveat on the boundary-in-tie predicate
   (§1a above) into D1's guard spec, so the CI implementation doesn't flap on
   `depth_factor`'s incidental float-duplicate ties.
