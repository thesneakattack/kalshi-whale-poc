# Economic Strategy Effectiveness — Gate Marginal Contribution & Adverse-Selection Root Cause (E4-E5)

Companion: `docs/superpowers/plans/2026-08-26-economic-strategy-effectiveness-investigation.md`
(tasks E4, E5). Read
`docs/superpowers/research/2026-08-26-economic-population-and-replay-gaps.md` first — E4/E5
below inherit its capture-health caveat (the post-2026-08-23 population this document
analyzes sits inside an unremediated, not-capture-health-controlled realtime data plane).

## E4 — Gate marginal contribution, banded by unit cost

`services/candidate_log.py`'s `rejection_events` table is the real population of every
gate rejection since 2026-08-23 (~5.8M rows as of this session, undeduped — see that
module's own "POPULATION STATISTICS" docstring). `population_gate_summary()` (existing,
reviewed function, exposed at `GET /api/candidate-log/summary`) already reports one
hypothetical win rate per `(strategy, gate_name)`, sample-size-gated at `n >= 30`
resolved+sided rows — but averaged across every unit-cost that gate ever rejected, which
ROADMAP.md's own note already flagged as the real remaining gap ("a banded, sample-size-
gated cost-aware win rate per gate... needs the new unit_cost data to accumulate first").
Enough time has now passed (3 days, ~5.8M rejection events) to do that banding.

**Method:** read-only `mode=ro` URI query against the live `candidate_log.db` (not copied —
1 GB), grouping `rejection_events` (`WHERE unit_cost IS NOT NULL AND resolved = 1 AND side
IN ('yes','no')`) by `(strategy, gate_name, unit_cost_band)` using the same six bands
CLAUDE.md's HARD COMMANDMENT table uses (`[0,.2) [.2,.4) [.4,.6) [.6,.8) [.8,.95) [.95,1.01)`),
computing `win_rate`, `mean_unit_cost`, and `EV_per_contract = win_rate − mean_unit_cost`
(dollars per $1-payout contract — dimensionally: a probability minus a dollar-price-per-
contract-share, both in the app's existing 0-1 "unit cost" convention, the same one
`series_watcher._unit_cost`/`PaperBroker.cost_basis` use), with the same `n >= 30`
sample-size gate.

### KXBTC15M-specific results (`ticker LIKE 'KXBTC15M-%'`)

| Gate | Band | n | Win rate | Mean unit cost | EV/contract |
|---|---|---:|---:|---:|---:|
| `whale_watcher.min_contracts` | 0.00-0.20 | 601,757 | 9.9% | 0.074 | **+0.0252** |
| `whale_watcher.min_contracts` | 0.20-0.40 | 461,397 | 36.9% | 0.300 | **+0.0692** |
| `whale_watcher.min_contracts` | 0.40-0.60 | 535,404 | 49.8% | 0.493 | +0.0048 |
| `whale_watcher.min_contracts` | 0.60-0.80 | 383,955 | 61.8% | 0.691 | **-0.0728** |
| `whale_watcher.min_contracts` | 0.80-0.95 | 299,264 | 83.1% | 0.880 | **-0.0491** |
| `whale_watcher.min_contracts` | 0.95-1.01 | 213,655 | 98.2% | 0.979 | +0.0029 |
| `whale_follow.entry_threshold` | 0.20-0.40 | 78 | 50.0% | 0.312 | **+0.1878** |
| `whale_follow.entry_threshold` | 0.40-0.60 | 156 | 48.1% | 0.482 | -0.0015 |
| `whale_follow.entry_threshold` | 0.60-0.80 | 53 | 62.3% | 0.662 | **-0.0392** |
| `whale_follow.min_unit_cost` | 0.00-0.20 | 39 | 33.3% | 0.141 | **+0.1921** |
| `whale_follow.min_unit_cost` | 0.20-0.40 | 124 | 58.9% | 0.304 | **+0.2847** |
| `whale_follow.min_unit_cost` | 0.40-0.60 | 105 | 48.6% | 0.452 | +0.0336 |

(Full output, all gates including sub-30-sample bands and the all-series comparison, in
the analysis script this table came from — see "Reproducibility" below.)

**Interpretation:**

- **`min_contracts` rejects show a striking non-monotonic EV pattern**: positive EV in the
  cheap bands (0.00-0.40, where the gate is correctly discarding low-probability-but-cheap
  longshots that would still lose money on net... except the data says otherwise: these
  bands are net *positive* EV, meaning the rejected candidates in this band would, on
  average, have been profitable had they been traded) and in the near-certainty band
  (0.95-1.01, where EV is roughly flat/slightly positive), but **strongly negative EV in
  the 0.60-0.95 middle-high band** (-0.073 to -0.049 per contract) — the exact "probably
  right but not cheap enough" zone CLAUDE.md's HARD COMMANDMENT table already named as the
  trap for *selected* trades. Here it shows up on the *rejected* side too: `min_contracts`
  is not mis-targeted in this band (it's correctly keeping out negative-EV candidates
  there), but its aggregate-mean framing in `population_gate_summary()` (unbanded
  `avg_unit_cost` around 0.48-0.49 across all its rejections) would never have surfaced
  that the 0.00-0.40 band it also rejects is *leaving positive EV on the table*.
- **`entry_threshold` and `min_unit_cost` rejects both show the same shape**: strongly
  positive EV in the low-to-mid bands, crossing to flat/negative above ~0.6. This is
  consistent across three independently-computed gates, which is stronger evidence than
  any single gate's pattern alone.
- **None of this is, by itself, proof any gate should be loosened.** `avg_unit_cost`/
  `hypothetical_win_rate` measures what a *rejected* candidate would have done if traded
  exactly as-is — it does not account for interaction effects with other gates (E8, not
  built this pass — see the plan's own note on why `rejection_events` can't currently
  answer combined-gate questions), nor for the capture-health caveat above (this whole
  population sits inside an unremediated, non-uniform-completeness capture window).

## E5 — Adverse-selection root cause: regime-segmented reconciliation

Ran the real `services.series_watcher.reconcile("KXBTC15M", ...)` (not reimplemented;
`DB_PATH` monkeypatched to scratchpad copies of `signal_log.db`/`paper_broker.db`) over two
windows split at the whale-gate cutover (`080a37b`, epoch 1787463902):

| Metric | Before cutover (2026-08-12 → 2026-08-23 05:45) | After cutover (2026-08-23 05:45 → now) |
|---|---:|---:|
| Signals (resolved) | 1,826 | 4,293 |
| Signal accuracy | 85.9% | 53.0% |
| Entries (paper trades) | **0** | 90 |
| Traded-signal accuracy | — | 65.2% |
| Realised win rate | — | 66.7% |
| Selection delta | — | **+12.2 pts** |
| Exit delta | — | +1.5 pts |
| Mean entry unit cost | — | 0.658 |
| Breakeven accuracy | — | 65.8% |
| Edge (accuracy − breakeven) | — | **-12.8 pts** |
| Realised P&L | — | -$169.81 |

The "before" window has zero paper entries — confirms E2's finding (`paper_broker.db` was
reset after this window and holds no rows from it); the before/after asymmetry here is a
*data-availability* fact, not a claim that no trading happened before the cutover.

Over the full available window (all resolved KXBTC15M signals, 400 h, effectively the same
90 trades since they're the only ones in `paper_broker.db`): `selection_delta_pts: +2.4`,
`exit_delta_pts: +1.5`, `edge_pts: -3.0` (the smaller magnitude vs. the post-cutover-only
row above is because the wider window's `signal_accuracy_pct` denominator, 62.8%, includes
the higher-accuracy pre-cutover population even though none of it has a matching trade).

**Conclusion:** under the current gate configuration, **the entry gates are selecting a
better-than-population-accuracy subset (`selection_delta_pts` positive in both windows),
the opposite direction from the original 2026-08-17 finding.** `exit_delta_pts` is also
mildly positive — exits are not the leak either. The realized net loss traces to `edge_pts`:
mean entry unit cost (0.658) sits above the population's own resolved accuracy (0.628-0.530
depending on window) even though the *traded* subset's accuracy (65.2%) sits almost exactly
at its own breakeven (65.8%) — a razor-thin, currently slightly negative pricing edge, not
an adverse-selection problem in the sense the original 88.8%/58.3% figure described.

**This is a reframing, not a non-answer.** The original question — "why did the 12 selected
trades resolve worse than the 394-signal pool" — cannot be answered for that specific
sample (E2, E10: the sample is gone). What this investigation *can* say, with current full-
population evidence: whatever mechanism produced that gap in the 2026-08-17-era
configuration, it is not currently reproducing under the 2026-08-23-era configuration, and
the current (much smaller, -$169.81 on 90 trades) shortfall has a different, identified
shape — a pricing/edge gap, not a selection gap. Sample size caveat: 90 trades over 3.2 days
is itself thin for a durable conclusion (see the status report's insufficient-sample list).

## Reproducibility

Both analyses ran from ad hoc, read-only scripts, not shipped code (per the design doc §3 —
this is a research-only branch). Scripts referenced by name here for anyone re-running this
investigation, not committed to this branch (scratchpad-local, gitignored working files):
`gate_cost_band_analysis.py` (E4) and `segmented_reconcile.py` (E5), both documented in the
plan file's methodology notes. Re-running either against current data will produce
different numbers than the tables above — the population these tables describe kept growing
throughout this session; a follow-up session should re-run rather than trust these numbers
verbatim once meaningful time has passed.
