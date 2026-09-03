# Adversarial review, round 2: `edge-gate-retro-measurement-2026-09-03.md`

Fresh, memory-less Agent dispatch (general-purpose, opus) against the doc
as revised after round 1 (`-adversarial-review.md`, GO WITH REQUIRED
FIXES — 2 CRITICAL, 2 HIGH, 8 MEDIUM/LOW). Explicitly instructed not to
rubber-stamp the round-1 fixes and to independently reproduce the §3.6
P&L finding on its own, since two prior sibling documents in this same
research effort (the reload-watcher doc, and this doc's own round 1) had
each found a case of accurate citations attached to a misread mechanism.

**Verdict: GO WITH REQUIRED FIXES.** No load-bearing conclusion inverts —
the retention root cause, the 18/18 calibration cells, the §2 algebra,
the ~75-78% rejection rate among evaluable entries, the ~38% evaluability,
the not-concentrated-in-band result, and the §3.6 outcome finding all
reproduced independently, and §3.6 actually *strengthened* under a
capital-normalization check the doc hadn't run. But two findings (HIGH)
repeat the exact class of error round 1 was fixing — accurate individual
numbers assembled into a self-contradictory or mislabeled claim — plus
five MEDIUM findings including a real arithmetic bug in the §3.6 table,
and four LOW precision fixes. Reproduced in full below, as the reviewing
agent reported it.

---

## HIGH — must fix

### 1. Summary bullet 3 contradicts itself and contradicts §3.5.

It states the 61.4% fail-open figure, then says *"The root cause of
**that coverage gap** … is `market_history.retention_hours: 168` (7
days) pruning price history"*, then three sentences later says *"§3
itself (all entries <1 day old) is **not** contaminated by the retention
mechanism."* Both cannot be true. Worse, it then prescribes *"the fix
would be different: retention tuning or accepting the ~7-day effective
window, **not** speeding up snapshot capture for specific market
types"* — the exact opposite of what §3.5 concluded for that population
(*"they genuinely don't exist yet at trade time"*; widening the window
recovers ~nothing, so faster/broader capture is the only lever). §1's
71.3% and §3's 61.4% are two different gaps over two different
populations; the Summary fuses §1's root cause onto §3's number.
**Required fix:** split the bullet — §1's 30-day gap is ~60% retention;
§3's <1-day gap is 100% a genuine capture hole, and the fix for it is
capture coverage, not retention tuning.

### 2. §1's replacement exemplar paragraph repeats the failure class it was written to fix — all-window statistics presented as a within-retention finding.

The paragraph's headline is *"The real **within-retention** coverage gap
concentrates in…"* but every number in it is the 30-day all-window
figure, and the all-window figures are dominated by the retention-pruned
rows the paragraph says it is excluding. Re-derivation (n=175,920
resolved signals):

| family | share of **all** misses | share of **<7d** misses | zero-snapshot, all-window | zero-snapshot, **<7d** |
|---|---|---|---|---|
| KXGOLD15M | 12.2% | **30.1% (largest)** | 23.4% | 22.8% |
| KXMVECROSSCATEGORY | 16.1% | 20.6% | 100.0% | 100.0% |
| KXATPMATCH | 10.8% | 5.9% | 83.4% | **23.9%** |
| KXMLBGAME | 9.8% | 3.0% | 90.2% | **23.1%** |
| KXATPCHALLENGERMATCH | 7.7% | 3.6% | 91.7% | **55.7%** |
| KXWTAMATCH | 7.1% | 2.6% | 91.8% | **44.6%** |

Three concrete errors:
- *"the sports-match families … collectively **~47% of all misses**"* —
  the four named families are **35.4%**. The ~47% figure (inherited from
  the prior review) covers a broader set including ITF/NFL/MLBTOTAL/
  MLBSPREAD that the doc doesn't name.
- *"83-99% zero-snapshot … a distinct failure from either retention or
  fast rotation"* — false for the sports families. "Has this ticker any
  snapshot ever" is *itself* a retention measurement; restricted to
  within-retention signals their zero-snapshot rate collapses to
  **23.9–55.7%**. The claim holds only for `KXMVECROSSCATEGORY` (100% at
  every age — that part is solid and independently confirmed).
- The doc reclassifies `KXGOLD15M`/`KXSILVER15M` as "reasonably well
  covered" (correct: 62.4%/64.5% covered, 23.4%/24.9% zero-snapshot —
  matches the doc's numbers exactly) and drops them from the exemplar
  list — but `KXGOLD15M` is the **single largest contributor to the
  within-retention gap at 30.1%**. The first draft conflated volume with
  coverage; the fix over-corrects to rate-only and then attaches volume
  shares from the wrong population.

---

## MEDIUM

### 3. §2's "many real `q_pre` values sit near that stale end" is falsified by measurement.

The ~610s *bound* is correct and confirmed (max observed 606.9s). But the
frequency claim is not: over the §1 attached population (n=51,892) median
staleness is **17.4s**, p90 43.6s, p99 199.9s; **0.6% exceed 300s, 0.2%
exceed 500s, 0% exceed 600s**. Over the §3 evaluable entries (n=124):
median 17.6s, 89.5% within 60s, max 446s. The doc uses this unmeasured
claim to soften §2's own conclusion — in practice it is very close to the
clean framing. Adopted verbatim from the prior review's speculation
without checking. **Fix:** keep the 610s bound, replace the frequency
clause with the measured distribution.

### 4. §2 and the Summary use the 81.7%-no-category figure as a *runtime* condition; it is an artifact of the offline table.

Row-weighted over the 30-day signal population: `trade_category.db`
supplies a category for **28.3%** of signals, the live `event_titles`
chain for **73.3%**. So the live no-category rate is ~27%, not ~82%. The
qualitative claim ("a live signal with no category hits the strict
filter today") is **true** — just at a third of the stated rate, and
from a different source than the number quoted beside it.

### 5. §3.6's table does not reconcile with itself, and the Summary promotes it to the doc's headline.

In all three rows `entries ≠ closed + still_open` (94≠97, 29≠13,
204≠199), and the would-REJECT row fails its own sum÷closed=mean
identity: 7,159.49/85 = **84.23**, not the stated **68.84** (the implied
denominator is 104). The would-ADMIT row leaves **16 of 29 entries
(55%) unaccounted for**, and that row supplies the +$91.54 the doc leans
on for its "comparable to, not clearly worse than" framing. No caveat
mentions dropped/unmatched entries. Independent rerun reconciles exactly
(reject 97 = 88+9, 6021.16/88 = 68.42; admit 27 = 25+2, 1549.93/25 =
62.00; fail-open 207 = 203+4, 6342.62/203 = 31.24).

### 6. §3's category-source attribution names the wrong function and the wrong line.

The doc says the live gate's category comes from *"`market_lookup.
_category_by_ticker()` (`decision_bridge.py:125`)"*. It does not — the
whale-signal path sets `category = event_info.get("category")` from
`state["event_titles"]` at **`decision_bridge.py:104-105`**, and passes
it at :125. `_category_by_ticker()` is used on the *fill* paths (:177,
:200). Both derive from `event_titles`, so the substance survives, but
this is a citation-accurate/mechanism-misread pattern imported verbatim
from the prior review without re-derivation — the exact failure the doc
says it is guarding against.

### 7. `services/candidate_log.py:116-119` resolves only against this worktree's stale copy.

`origin/main` merged PR #522 (`feat/candidate-log-db-migration`); there
`record_rejection` is at **92-95**. The argument-list claim (no
`p_est`/`q_pre`/`delta` field) is correct in both versions, so only the
line number needs updating — but it will be wrong the moment this doc
merges.

---

## LOW

### 8. §4: *"has zero consumers anywhere in the codebase"* is too broad as literally grepped.

`grep -rn edge_gate main.py services/ static/ frontend/` returns ~40
lines (config keys, comments, the `EntryValidation` field). And
`tests/test_strategy_engine.py:1653-1654` does read `decision["edge_
gate"]`. The load-bearing claim (no production consumer, nothing
durable) is correct: two writes at :812/:837, zero non-test reads. Scope
the sentence to production code.

### 9. §2: *"closer to ~0.06 within the … 0.60-0.95 band"* is only true for part of the band.

The required margin is 0.0618 at unit cost 0.60 but **0.0483 at 0.95**,
effectively the 0.045 floor. True only for the 0.60–0.75 part of the
band.

### 10. §1 lists `KXETHD` among the "reasonably well covered" families but gives figures only for GOLD/SILVER.

ETHD is **51.2% covered / 26.8% zero-snapshot** — materially worse than
the 62-64% quoted for gold/silver.

### 11. "bankroll 10,000 → 16,763" appears as bare fact; live value now 16,296.

Timestamp it like the doc already does for the PM's position counts.

---

## Independently confirmed correct (re-derived, not taken from the doc)

- **Every `file:line` citation checked individually and correct** except
  #7: `_edge_gate_check` 233-308, core math 287-308, admit/reject branch
  218-222 with `._replace` at :222, reason string 305-306,
  `decision["edge_gate"]` writes 811-812/836-837, reject caller 731-736,
  `decision_bridge.py` :129/:130-131, `confidence_calibration.py` 479 and
  511-534, `paper_broker.py:620`, `config/settings.yaml:308-309` and
  122-128. All five config values match code defaults; `edge_gate_
  enabled: false` still live.
- **§1 reproduces:** 175,920 resolved / 70.7% no-p_pre / 29.3% attached /
  80.5% of attached category-None; **18 cells, 18 clearing
  min_bucket_n=50, 0 in fallback**, categories exactly {Commodities,
  Crypto, Sports}, smallest cell **(Crypto, 80-90%) n=153** vs the doc's
  152. `trade_category.db` category list matches, no Politics. §1's own
  percentage arithmetic (including the round-1 rounding fixes) all
  checks out.
- **The corrected retention root cause is right.** Oldest surviving
  snapshot **7.01 days**; miss rate 35.4/64.2/36.7/48.5/61.0/57.4/67.2%
  for 0-6d, then **99.8% at 7d, 100% beyond** — a clean cliff at the
  168h boundary. **59.9%** of misses are ≥7d; within-retention gap
  **49.2%** (doc: ~60% / 49.4%). The "30-day window is effectively ~7
  days" consequence is correct and well-supported.
- **§2's algebra** re-derived from source: `delta=0.0` → `p_est = q_pre`;
  admission `ask ≤ q_pre − fee − min_edge`. "Strict filter, not neutral
  pass-through" is right.
- **§3 reproduces:** 331 entries, 62.5% fail-open, 37.5% evaluable, 78.2%
  rejected (doc 74.8%), **0 uncalibrated hits — exact match**. In-band
  rejected 72.7% vs out-of-band 82.6% (doc 72.2%/76.8%) — "not
  concentrated in-band" holds and is stronger. Fail-open 56.7% in-band vs
  66.2% out (doc 56.5%/64.6%). 0 flat-fee-type tickers, as the doc
  states.
- **§3.5 reproduces:** max_age 600s→7d recovers 3.3 points (doc 3.4), and
  1 day → 7 days recovers nothing.
- **§3's disclosed category substitution has no practical divergence —
  verified.** Running the identical sim with the live `event_titles`
  category gives identical rejection/admission counts and **0 verdict
  flips**.
- **§3.6's finding reproduces and survives a confound the doc never
  tested.** Reject 78.4% win / +$68.42 mean vs admit 72.0% / +$62.00 vs
  fail-open 58.1% / +$31.24. Normalized for capital deployed (size ×
  unit cost), since a high-unit-cost cohort can post a high win rate on
  poor return: **return on capital reject 20.7%, admit 18.2%, fail-open
  11.1%**. The would-reject cohort is best on all three measures — the
  doc's headline is if anything *understated*, and its cautious framing
  is fair.
- The three schema-only leftover `.db` files from the earlier shadowing
  bug are gone from the worktree's `data/`.

## Verdict: GO WITH REQUIRED FIXES

No load-bearing conclusion inverts. The retention root cause, the 18/18
calibration cells, the §2 algebra, the ~75-78% rejection among evaluable
entries, the ~38% evaluability, the not-concentrated-in-band result, and
the §3.6 outcome finding all reproduce independently — and §3.6
strengthens under capital normalization. The doc's answer to "what would
flipping the gate do" stands, and its central message (volume rejected
is not evidence of quality improved) is correct.

But findings **1** and **2** must be fixed before this is final: both are
the same class of error the doc was revised to eliminate — a correct
citation attached to a misread mechanism — and finding 1 sits in the
Summary, self-contradicting inside a single bullet. Findings 3-7 are
corrections to make in the same pass; 3 and 4 are stated-as-fact claims
that measurement contradicts, and 5 is an arithmetic defect in the table
the Summary promotes above everything else.
