# Economic Strategy Remediation — Candidate Design (Program 2)

**Status: NOT approved for execution.** This is the design-candidate deliverable the
execution program's §6.1 asks for ("design/spec"), written at the end of the
research-only investigation on `research/economic-strategy-effectiveness`. Per the
execution program §7's dependency sequence, **Program 2 (this design's implementation)
is gated behind Program 1 (realtime data-plane remediation) landing** — several of this
investigation's own findings (E3, E4, E5's post-cutover figures) are explicitly flagged as
provisional pending that work, and building on them before Program 1 lands risks tuning
against an uncontrolled capture-completeness population. Do not begin implementing any
task below until: (a) a human has reviewed and approved this design, and (b) Program 1 has
merged and this investigation's provisional findings have been re-verified against a
capture-health-controlled sample.

Source evidence: `docs/superpowers/research/2026-08-26-economic-*.md` (four documents) and
`docs/superpowers/plans/2026-08-26-economic-strategy-effectiveness-investigation.md`.

## Design candidates, one per finding class

### D1 — Banded, cost-aware gate diagnostic (from E4)

**Problem:** `services/candidate_log.py`'s `population_gate_summary()` reports one
hypothetical win rate per gate, averaged across every unit cost — hiding the real
0.60-0.95-band negative-EV pattern this investigation found underneath the aggregate.

**Candidate:** add `population_gate_summary_banded(bands=DEFAULT_BANDS, min_samples=30)` to
`services/candidate_log.py`, same sample-size-gating convention as the existing function,
grouping by `(strategy, gate_name, unit_cost_band)` and reporting `win_rate`,
`mean_unit_cost`, and `ev_per_contract` per group (exact logic already prototyped, read-only,
in this investigation's E4 analysis). Surface it as a new field on
`GET /api/candidate-log/summary` alongside the existing `population_gates` key (e.g.
`population_gates_banded`), and add a `services/diagnostics` check
(`check_gate_cost_bands`) that flags any `(gate, band)` with `n >= min_samples` and
`ev_per_contract` meaningfully negative, folded into `run_offline()`/
`GET /api/quality/summary` alongside the existing `series_funnel`/`selectivity_curve`
checks it would sit next to.

**Not in scope for D1:** using this to actually retune `entry_threshold`/`min_contracts`/
etc. — that is a live strategy-tuning decision (same restraint the existing 4 unapplied
advisory suggestions already established), a separate, later, human-reviewed step.

### D2 — Reusable capture-health tagging helper (from E3)

**Problem:** this investigation hand-rolled an hourly-density-anomaly check to decide
whether a given time window's data is safe to treat as representative. A future
investigation (or a runtime diagnostic) needing the same judgment would otherwise re-derive
it from scratch.

**Candidate:** a small, series-agnostic helper — tentatively
`services/diagnostics/capture_health.py`'s `tag_window(series, since_ts, until_ts, cfg=None)`
— computing hourly signal density from `signal_log.db` (or, once Program 1's remediation
lands, from whatever capture-completeness metric it produces) and returning one of
`healthy` / `measurably_degraded` / `completeness_unknown` / `known_saturated_lossy` per
hour, reusable by both a runtime diagnostic and any future ad hoc research script. Depends
on Program 1 landing a real capture-completeness signal to fold in — until then this can
only ever produce `completeness_unknown` for any window without an already-published
realtime-investigation measurement covering it, which is honest but of limited value; **this
is the strongest reason D2 should wait for Program 1**, not merely a scheduling
convenience.

### D3 — Price-impact estimate for thin-book entries (from E7/E9)

**Problem:** paper-mode fills unconditionally at the signal price; E7 found 16% of
book-matched entries had insufficient resting depth for their size, meaning real execution
would likely have paid a worse price paper P&L never reflects.

**Candidate:** NOT a fill-probability model (explicitly rejected in E9 as unbuildable
without real fill data). Instead: for any entry with a matched book snapshot and depth ratio
< 1.0, walk the snapshot's resting-size field at the crossed side to estimate the
volume-weighted price needed to fill the full position size (a standard book-walk price-
impact estimate, bounded by what a single snapshot's aggregate size fields can actually
support — note `book_snapshots` stores aggregate bid/ask size, not a full depth ladder, so
this is a one-level approximation, not a real order-book walk; state that limitation in the
output, not silently). Surface as a new `series_watcher.reconcile()` field
(`estimated_price_impact_pts`) rather than a new module, reusing the existing entry/book
join.

**Explicit non-goal:** do not use this estimate to adjust `realised_pnl` in place — report
it alongside the real (paper-fill) P&L as a separate "what a book-aware fill might have
cost" figure, so existing P&L reporting is never silently redefined.

### D4 — Multi-gate outcome vector (from E8)

**Problem:** `rejection_events` records only the first gate a candidate failed per
evaluation, so no query can currently answer "what would have happened to candidates that
would have failed gate A even if gate B were loosened."

**Candidate, explicitly flagged as the highest-cost item here:** change
`strategy_engine.evaluate()`'s gate-checking loop to continue evaluating (without acting on)
every gate rather than short-circuiting at the first failure, and have
`candidate_log.record_rejection()` accept a full pass/fail vector instead of one
`gate_name`. **Cost concern, not hypothetical:** `rejection_events` is already ~5.8M rows
after 3 days at the current single-gate-per-row schema; a full-vector schema multiplies
write volume by the number of gates checked per candidate (currently up to ~7-9 depending on
strategy path). This needs an explicit human sizing/retention decision before being built,
not just an engineering green light — flagged here, not decided.

### D5 — advisory_engine entry-side cost-awareness (from E6)

**Problem:** `_entry_threshold_recommendation`/`_longshot_bonus_recommendation`/
`_cross_variant_recommendations`/`_rejected_candidate_recommendations`/
`_category_conditional_recommendations`/`_series_conditional_recommendations` are all
win-rate-only; `services/advisory/CHEATSHEET.md` already named this as its top audit
priority (2026-08-22/23), unchanged as of this investigation.

**Candidate:** thread a cost-aware second check through each of the six functions above —
the same "does the win-rate-favored option also carry a higher mean unit cost" check this
investigation's own E4 banding makes newly possible with real KXBTC15M/all-series evidence
behind it. Not designed in further detail here — this is squarely `services/advisory/`'s
own module-quality work (the "per-module effectiveness/efficiency/informativeness" standing
objective in `CLAUDE.md`), not new investigation scope; D5 exists in this document mainly
to record that E4's banded data is the missing ingredient that makes this fix newly
tractable, not to design the fix itself.

## Explicit non-candidates (considered, rejected)

- **Auto-retuning `entry_threshold`/`min_contracts` directly from D1's output.** Rejected:
  this is a live strategy-tuning decision on protected economic parameters — out of scope
  for any automated actor per
  `.claude/rules/autonomous-quality-coordination-evidence.md`'s "Remediation authority
  rule" (strategy/EV/fee/P&L semantics is an explicitly protected domain). D1 surfaces
  data; a human decides what to do with it, the same restraint already established for the
  4 existing unapplied advisory suggestions.
- **A full historical strategy replay/backtest engine.** Rejected for this design: the
  execution program's own §14 "Final operating model" scopes that to a later program, not
  this one — building it here would be scope creep past what E1-E9's findings actually
  require to act on.
- **Retroactively estimating fill probability from book-context data alone.** Rejected in
  E9 and reaffirmed here: would fabricate precision the historical data cannot support.

## Sequencing

D1 and D5 do not depend on Program 1 (they operate on already-captured, already-complete
`candidate_log`/`signal_log` data whose capture-health status doesn't change what the banded
math itself computes — only how much weight to put on any given window's conclusions, a
D2 concern). D2, D3, D4 either directly depend on Program 1's capture-completeness signal
(D2) or are large enough (D3's book-walk limitation, D4's write-volume decision) to warrant
their own human sign-off regardless of Program 1's status. See
`docs/superpowers/plans/2026-08-26-economic-strategy-remediation.md` for the task-level
sequencing this maps to.
