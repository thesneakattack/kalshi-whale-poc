# Adversarial review: `edge-gate-retro-measurement-2026-09-03.md`

Fresh, memory-less Agent dispatch (general-purpose, opus) against the doc,
re-deriving every load-bearing number from primary sources (the primary
checkout's `services/`, confirmed byte-identical to the worktree copy for
every cited module; read-only `mode=ro` URI queries against the live
`data/*.db`) rather than trusting the doc's own tables or the self-review's
"GO" verdict.

**Verdict: GO WITH REQUIRED FIXES.** The volume-level numbers the doc leads
with all reproduce independently — 18/18 calibrated cells, the delta=0.0
algebra, ~75% rejection among evaluable entries, ~38% evaluability,
rejection not concentrated in-band. But the doc's explanation of *why*
coverage is incomplete names the wrong mechanism and the wrong exemplar
markets, two claims overreach past what was actually established, and —
most consequential for the decision this doc exists to inform — nothing in
the doc asks whether the trades the gate would reject were actually
losers. Reproduced in full below, as the reviewing agent reported it.

---

## Findings, most severe first

### 1. CRITICAL — §1's explanation for the 71.3% `no p_pre` rate is affirmatively wrong. It rules out the actual dominant cause by name.

The doc states: *"these aren't old signals whose price history rolled off
retention — they're on tickers that had **no price snapshot at all before
their first (and often only) trade**."*

Retention is in fact the single largest cause, ~60% of all misses.

Primary evidence:
- `config/settings.yaml:308-309` — `market_history: retention_hours: 168`
  (7 days).
- `main.py:202-203` — `market_history.prune(retention_hours=mh_hours,
  now=now)`, hourly, 50k rows/call.
- `services/market_history.py:394-426` — `prune()` deletes `snapshots
  WHERE timestamp < cutoff`.
- Live table (at review time): 3,061,421 snapshots, **oldest 16.45 days
  old** (prune backlog still draining), against a `since_ts = now -
  30*86400` calibration window. 20.5% of in-window signals predate the
  oldest surviving snapshot outright.

Measured miss rate by signal age (175,117 resolved, non-excluded signals,
30d window):

| days ago | 0 | 1 | 2 | 3 | 4 | 5 | 6 | **7** | 8 | 9–22 |
|---|---|---|---|---|---|---|---|---|---|---|
| miss % | 35.7 | 61.5 | 39.8 | 48.1 | 61.3 | 57.3 | 67.4 | **99.4** | 100.0 | 100.0 |

A clean cliff exactly at the configured 168h boundary. 74,394 of 124,154
misses (59.9%) are signals ≥7 days old missing at 100%. Inside the
retained window the rate is **49.4%**, not 71%.

Per-family control (same family, inside vs outside retention) — this is
the falsifier:

| family | ≤7d n / miss% | >7d n / miss% |
|---|---|---|
| KXBTC15M | 13,997 / 31.8% | 7,619 / 99.6% |
| KXATPMATCH | 5,376 / 54.7% | 10,540 / 100.0% |
| KXMLBGAME | 2,542 / 59.4% | 10,686 / 100.0% |
| KXMVECROSSCATEGORY | 10,322 / 100.0% | 9,654 / 100.0% |

The doc's own falsification test cannot distinguish the hypotheses:
widening `max_age_sec` to 7 days cannot recover *deleted* rows, and "the
same ticker returns a real value as of right now" only samples tickers
still being snapshotted now — a sample biased entirely toward live, recent
tickers.

**Required fix:** delete the "not retention" sentence. Replace with: the
30-day `recompute_deltas` window is truncated to an effective ~7-day
window by `market_history.retention_hours: 168`; ~60% of the misses are
pruned history, and the genuine within-retention coverage gap is 49.4%.

### 2. CRITICAL — §1 names the wrong exemplar markets. The largest contributor is never mentioned.

The doc names `KXGOLD15M`, `KXSILVER15M`, `KXETHD-...` as where the gap
concentrates. Measured, these are among the **best**-covered families:

| family | missing rows | of which the ticker has **zero** snapshots ever |
|---|---|---|
| **KXMVECROSSCATEGORY** | **19,972 (16.1%)** | **19,972 (100.0%)** |
| KXGOLD15M | 15,080 | 3,545 (23.5%) |
| KXATPMATCH | 13,481 | 11,244 (83.4%) |
| KXMLBGAME | 12,195 | 11,005 (90.2%) |
| KXBTC15M | 12,040 | 8,417 (69.9%) |
| KXATPCHALLENGERMATCH | 9,553 | 8,753 (91.6%) |
| KXWTAMATCH | 8,849 | 8,126 (91.8%) |
| KXSILVER15M | 4,307 | 1,078 (25.0%) |
| KXETHD | 2,249 | 602 (26.8%) |

Sports match families (ATP/MLB/WTA/ITF/NFL/WNBA) are collectively ~47% of
all misses at 83–99% zero-snapshot rates. `KXMVECROSSCATEGORY` alone is
16% and is 100% uncovered at every age — a genuinely different failure
from either retention or market rotation, and the doc never mentions it.
The doc conflated "largest absolute count" with "worst coverage," and
then picked families that are neither.

**Required fix:** rewrite the exemplar list — `KXMVECROSSCATEGORY` (100%
uncovered) and the sports-match families (83–99%) are the real
within-retention coverage gap; `KXGOLD15M`/`KXSILVER15M` are 62–64%
*covered* and appear high only on volume.

### 3. HIGH — §2's "neither condition is currently occurring" is unsupported and contradicted by §1's own numbers.

§2 says delta=0.0 arises when "`category is None`, or the cell exists but
never accumulated `min_bucket_n` — **neither condition is currently
occurring for any real cell, per §1**."

§1 established only the second half (0 cells below the floor). It says
nothing about `category is None`; in fact §1's own table reports **81.9%
of attached signals have no category at all**. A third case is missing
entirely: a `(category, band)` pair with *zero* rows also returns 0.0 via
`_delta_cache.get(..., 0.0)` (`confidence_calibration.py:479`) —
`trade_category.db` holds `Climate and Weather` (14 tickers), `Economics`
(3), `Entertainment` (2) that produce no cells at all, so a live signal in
any of those hits the strict filter immediately.

The only real support is §3's "0 uncalibrated hits among 121 evaluable
trades" — a ~20-hour sample (see #5).

**Required fix:** scope the claim to "no *existing cell* is below the
floor"; state that `category is None` is common in the signal population
and is not ruled out at runtime; add the zero-row-cell case.

### 4. HIGH — §4 overstates what observe-only already gets for free. `decision["edge_gate"]` has no consumer anywhere.

§4: *"on the admitted path, `decision["edge_gate"]` is already populated …
that part transfers to observe-only unchanged."* Mechanically true,
materially misleading. `grep -rn edge_gate main.py services/ static/
frontend/` shows the only writes are `strategy_engine.py:812` and `:837`
and **zero reads**. The decision dict goes to `state["decision_feed"]`
capped at 50 entries (`services/whale_stream/decision_bridge.py:130-131`)
and a WebSocket broadcast (`:143`); the only persistence is
`candidate_ledger.record_decision(signal.id, decision.get("action",
"unknown"))` (`:129`), which stores the action string only. Nothing
durable, nothing queryable after the fact — on *either* path.

**Required fix:** §4 item (c) and Summary bullet 4 must say durable
capture is needed on **both** paths, not only rejection. The doc's "(a)
and (b) are genuinely small" survives; "that part transfers unchanged"
does not.

### 5. MEDIUM — §3's sample is ~20 hours of one trading session, and the doc's framing hides it.

`paper_broker.db` in full: 633 rows, oldest **0.82 days** old; 323
entries, all within that span. Bankroll 10,000 → 16,763. `data/
trade_archive.db` (mtime 2026-09-02 15:49) holds the prior history — the
broker was reset/archived ~20 h ago.

The doc's "(paper trading hasn't accumulated 500 distinct entries yet)"
reads as a lifetime shortfall. The real rate is ~400 entries/day; the
sample is one narrow, unusually profitable window. (Retention pruning does
**not** contaminate §3 — checked explicitly: 100% of the entries are <1
day old, so §3's fail-open rate is a genuine coverage measurement, not a
retention artifact. Worth stating, since #1 would otherwise cast doubt on
it.)

**Required fix:** state the span and the reset; note that the archive
isn't usable for this either, because retention has pruned its `p_pre`
inputs — which is itself the reason this measurement can never look back
more than ~7 days.

### 6. MEDIUM — the doc measures volume impact only and calls it "the real headline number" for what is an EV decision.

Nothing in the doc asks whether the trades the gate would reject were
*losers*. On the same population, matching each entry to the first close
on its ticker and parsing the broker's own `(realized ±X)`:

| bucket | entries | closed | sum realized | mean | win% |
|---|---|---|---|---|---|
| would-REJECT | 94 | 85 | **+$5,456.90** | +$64.20 | 77.6% |
| would-ADMIT | 29 | 27 | +$1,907.87 | +$70.66 | 74.1% |
| fail-open | 204 | 200 | +$5,976.08 | +$29.88 | 58.0% |

Indicative only — 20-hour sample, ticker-level close matching is
approximate under netting/partial closes, some rejected entries still
open, and the whole window was a strong run. But on the data available,
the gate would have removed a *profitable* cohort at roughly the same
mean as the cohort it keeps. That is the number the flip decision turns
on, and the doc neither computes it nor flags its absence.

**Required fix:** the Summary must say explicitly that no outcome/EV
measurement was made and that the rejection rate is a volume figure only.
Adding the P&L split (with caveats) would be better.

### 7. LOW — §4's "never captured anywhere" is too strong for `p_est`.

The reject-path reason string (`strategy_engine.py:305-306`) embeds
`p_est`, `ask` and `fee`, and reaches the decision dict via
`_skip(signal, validation.reason)` (`:736`). Correct for `q_pre` and
`delta` — those appear nowhere. Fix the sentence to name only
`q_pre`/`delta`, and note `p_est` survives only as text in a transient
50-entry ring.

### 8. LOW — §2's "the unit cost 10 seconds before the whale print" is imprecise.

`p_pre` is the most recent snapshot at or before `as_of − 10s`, accepted
up to `p_pre_max_age_sec = 600` stale (`strategy_engine.py:283-289`) — so
up to ~610 s before the print. Given §1's coverage sparsity, many `q_pre`
values sit near the stale end. This weakens §2's reading ("the price must
have moved in your favor *since the pre-print snapshot*") more than the
doc admits.

Also: "at least ~0.045" is the floor (`fee = 0.07·m·p(1−p) + 0.005`, min
0.005). In the flagged 0.60–0.95 band the actual requirement is
~0.056–0.062. Say "0.045 at the extremes, ~0.06 in the 0.60–0.95 band."

### 9. LOW — rounding errors the self-review's own consistency pass missed.

40,801/49,915 = **81.741%** — the doc prints 81.8% twice (should be
81.7%). 9,114/49,915 = **18.259%** — the doc prints 18.2% (should be
18.3%; it was derived by subtracting the already-rounded 81.8 rather than
from the counts). Cosmetic, but the self-review claims to have
spot-checked exactly this.

### 10. LOW — the "read-only" and "deleted after use" claims are not literally accurate, and artifacts remain on disk.

`recompute_deltas` / `recent_price` / `categories_for_tickers` open
ordinary read-write connections that run `PRAGMA journal_mode=WAL` and
`CREATE TABLE IF NOT EXISTS`. Proof: the shadowed first run left three
schema-only DBs still sitting in the worktree —
`.claude/worktrees/edge-gate-retro-measurement/data/{signal_log.db,
market_history.db, trade_category.db}` (28K/36K/12K, every table 0 rows).
Gitignored so not committed, but left in place at review time rather than
removed (since removing files from another session's worktree wasn't the
reviewer's call to make).

Also internally inconsistent: the header says the script "calls the real
production functions unchanged (`confidence_calibration.recompute_
deltas`…)" while §1 says it "replicated … recipe" and called only
`_bucket_delta_by_category_price_band`.

### 11. LOW — undisclosed methodological substitution in §3.

The retro sim feeds the gate a category from `trade_category.db`; at
runtime the gate's `category` comes from `market_lookup.
_category_by_ticker()` (in-memory `event_titles` map,
`decision_bridge.py:125`) — same vocabulary, materially denser coverage.
No practical divergence found here (0 of 121 evaluable trades hit
delta=0.0), but it should be named. Relatedly, "Politics/Economics/etc." —
`trade_category.db` has no Politics rows at all (Sports 1692, Crypto 380,
Commodities 161, Climate and Weather 14, Economics 3, Entertainment 2).

### 12. LOW — the opening's blanket verification claim has no evidence trail.

"Verified every inherited claim from source" — the PM's "21 open
positions / 14 in band / 109 of the last 500" numbers are time-varying and
unshown (live at review time: 15 positions, 9 yes-side, 7 in band; 127 of
327 entries in band). Either show the check with its timestamp or soften
the sentence.

---

## Claims independently confirmed correct (not taken from the doc)

- **Every `file:line` citation checks out.** `_validate_entry_price` at
  161; edge-gate block 218-230 with the reject `._replace(edge_gate_
  detail=…)` at 222 and the `not edge_result.ok` test at 221;
  `_edge_gate_check` 233-308 with core math 287-308; `decision["edge_
  gate"]` at 812 and 837 (guards at 811/836); reject-path caller 731-736
  with exactly the argument list quoted; `record_rejection` at
  `candidate_log.py:116-120` — `ticker, strategy, gate_name,
  observed_value, threshold_value, side, now, unit_cost`, **no**
  `p_est`/`q_pre`/`delta` field, so §4's core structural claim is right;
  `recompute_deltas` attachment loop 511-534; `_price_band_label` 303-313
  over six `_CONFIDENCE_BANDS`; `_bucket_delta_by_category_price_band`
  316-348; `delta_calibrated_for` 470-479. Config values in
  `config/settings.yaml:122-128` match the doc exactly and equal the code
  defaults.
- **§1's counts reproduce.** Independent re-run (window shifted a few
  hours): 174,453 resolved / 124,038 no-p_pre (71.1%) / 50,415 attached
  (28.9%) / 41,291 category-None (81.9% of attached) / 9,124 cell rows —
  against the doc's 173,801 / 123,886 (71.3%) / 49,915 (28.7%) / 40,801
  (81.8%) / 9,114. **18 cells with ≥1 signal, 18 clearing
  `min_bucket_n=50`, 0 below the floor**, categories exactly
  `{Commodities, Crypto, Sports}`, and the smallest cell is **(Crypto,
  80-90%) at n=152** — an exact match on the doc's most specific claim.
- **§2's algebra is correct**, re-derived from source rather than checked
  for plausibility: `delta=0.0` → `p_est_side = min(max(q_pre+0.0, 1e-6),
  1-1e-6) = q_pre` (the clamp only bites at exactly 0/1); admission is
  `edge >= min_edge` (code rejects on `edge < min_edge`, line 302); so
  `q_pre − ask − fee ≥ min_edge ⟺ ask ≤ q_pre − fee − min_edge`. The
  "strict filter, not a neutral pass-through" characterization is right.
- **§3's shape reproduces.** 323 entries (doc: 319), fail-open 200
  (62.3%) vs doc 196 (61.4%), evaluable 121 (37.7%) vs 123 (38.6%),
  rejected 92 (76.0% of evaluable) vs 92 (74.8%), 0 uncalibrated, **in-band
  checked 54 / rejected 39 / 72.2% — exact match**, out-of-band 67/53/79.1%
  vs doc 69/53/76.8%. Zero flat-`fee_type` tickers, validating the
  self-review's stated omission. Both arithmetics close.
- **Self-review's arithmetic:** all seven identities verified exactly.
  The subset-vs-partition fix it describes is correct and necessary.

---

## Verdict: GO WITH REQUIRED FIXES

The numbers the flip decision actually rests on — 18/18 calibrated cells,
the delta=0.0 algebra, ~75% rejection among evaluable entries, ~38%
evaluability, rejection not concentrated in the flagged band — all
reproduce independently, and §3 is provably *not* contaminated by the
retention defect. The doc's answer to "what would flipping the gate do to
trade volume" stands.

But four things must be corrected before this is final, because three of
them reach the Summary and one is stated in the Summary as fact:

1. **Findings #1 and #2** — the `no p_pre` root cause is 168h retention
   pruning plus a `KXMVECROSSCATEGORY`/sports-match coverage hole, not
   fast-rotating gold/silver/ETH strikes. The doc explicitly rules out the
   true cause. This is exactly the failure shape the sibling reload-
   watcher doc hit: correct citations, untraced mechanism. It also means
   the calibration window is effectively ~7 days, not 30 — worth
   surfacing as its own finding, since it caps what any future
   measurement can look back at.
2. **Finding #3** — §2's "neither condition is currently occurring" must
   be narrowed to what §1 actually established.
3. **Finding #4** — §4/Summary must stop implying the admitted path
   already captures anything durable.
4. **Finding #6** — the Summary must state that no EV/outcome was
   measured; the indicative split (would-reject cohort realized +$5,457,
   77.6% win rate) points the opposite way from "turn it on."

Findings #5 and #7–#12 are corrections to make in the same pass, not
blockers.
