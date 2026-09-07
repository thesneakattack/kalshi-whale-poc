# Economic Strategy Effectiveness & Execution Realism — Investigation Execution Plan

Companion: `docs/archive/lane-4-analytics-advisory-research/specs/2026-08-26-economic-strategy-effectiveness-investigation-design.md`
(read that first — constraints, evidence classes, methodology, deliverable map).

Task numbering: `E0`-`E12`. Status marked per task. A future session resuming this work
should re-ground against current `origin/main` and this file's status column, not against
conversational memory (per `.claude/rules/branching-and-ci.md`'s "Resuming work in a fresh
session" section) — re-run the read-only queries below fresh rather than trusting numbers
written here if meaningful time has passed, since the population these tasks measure grows
continuously.

| Task | Status | Summary |
|---|---|---|
| E0 | DONE | Re-ground: branch, worktree, active-work check, canonical docs read |
| E1 | DONE | Population staleness: reproduce/age-check the original 88.8%/394/58.3%/12 figures |
| E2 | DONE | Replay-gap analysis: reset history vs. data continuity |
| E3 | DONE (partial) | Capture-health tagging via signal-density time series |
| E4 | DONE | Gate marginal contribution: banded, cost-aware, sample-size-gated EV per gate |
| E5 | DONE | Adverse-selection root-cause: regime-segmented `reconcile()` |
| E6 | DONE | Advisory/calibration objective audit |
| E7 | DONE | Execution realism: book-context (spread/depth) at real entries |
| E8 | NOT DONE | Multi-gate interaction/cooldown decomposition |
| E9 | NOT DONE (assessed, not built) | Fill-probability/IOC/partial-fill modeling |
| E10 | CLOSED (infeasible) | Direct reconstruction of the original 12-trade sample |
| E11 | DONE | Adversarial self-review of E1-E7 |
| E12 | DONE (this pass) | Deliverables synthesis + status report |

## E0 — Re-ground and establish investigation boundaries

**Question:** what is the actual current repo state, and what must this investigation not
touch or duplicate?

**Method:** `git branch -a`, `git worktree list`, `gh pr list --state all`, read
`docs/kalshi-personal-production-execution-program-2026-08-26.md` in full (fetched via
`git show a478fea:...` — the doc lives on an open, unmerged PR branch
`docs/personal-production-execution-program`, not yet on `main`), confirm the current
branch (`worktree-agent-a919c0bdca5516dbe`, itself already a fresh worktree off
`origin/main` at `f784a64`) before branching.

**Result:** confirmed `feat/realtime-data-plane-remediation` is a separate, active, locked
worktree not to be read from or touched. Confirmed the execution program's own "Immediate
3" section names this exact branch name (`research/economic-strategy-effectiveness`),
constraints, and deliverable list — used directly as this investigation's specification.
Branched from a verified-synced `origin/main` (`git log origin/main..HEAD` empty before
branching).

## E1 — Population staleness of the original adverse-selection figures

**Question:** does "394 KXBTC15M whale signals, 88.8% correct; 12 selected trades, 58.3%
correct" still describe current reality, or was it measured against a population/config
regime that has since changed?

**Evidence class:** 1 (current persisted data), 2 (git history).

**Method:** `grep` `CLAUDE.md`/`ROADMAP.md`/`docs/roadmap-archive-2026-08-23.md` for the
figure's provenance (found: "Measured 2026-08-17 on KXBTC15M", `ROADMAP.md`). Query
`signal_log.db`'s `signals` table (copied read-only to scratchpad) for the current
resolved-count/accuracy for `series='KXBTC15M'`, unfiltered and split by `source`/
`excluded` to check whether any existing filter reproduces 394/88.8%.

**Result:** full-population count as of 2026-08-26 is 6,119 resolved KXBTC15M signals at
62.8% accuracy — no `source`/`excluded` filter reproduces 394 or 88.8%. The 394-signal
figure predates the 2026-08-23 whale-gate cutover (dollar-notional → contract-count, commit
`080a37b`) by 6 days and is roughly **6.4% of today's population**. Full detail:
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-population-and-replay-gaps.md`.

## E2 — Replay-gap analysis: what data actually survived

**Question:** can the original 12 traded signals be re-examined directly against current
data?

**Evidence class:** 1 (current persisted data).

**Method:** query `reset_log.db`'s `reset_events` table (read-only, live file — the table
itself is small and append-only, not worth copying) for `domain='paper'`/`signal_log`/
`candidate_log`; cross-reference against `paper_broker.db`'s (copied) `trades` table
`MIN(timestamp)`.

**Result:** `paper_broker.db`'s `trades` table was fully reset (`domain='paper', scope='all'`)
at 2026-08-24T22:07:17 (230 rows deleted) and again at 2026-08-24T22:22:44 — every trade row
currently in `paper_broker.db` postdates 2026-08-24T22:24. Two earlier full resets also
happened, 2026-08-17T20:31:52 and 2026-08-18T00:08:12. **The original 12-trade sample is not
reconstructable from current data under any circumstance** — this is a closed negative
result (E10), not an open question. `signal_log.db` was never fully reset (one partial
`between`-range purge on 2026-08-17T01:11:45, an explicit 28-minute experiment-window
exclusion, consistent with `signal_log.mark_excluded_range`'s documented purpose) — it is
the one continuously-available population series. `candidate_log.db`'s `rejection_events`
population table has exactly one reset event in its history, consistent with
ROADMAP.md's own "starts collecting from 2026-08-23 forward" note (the table's creation/
migration, not a mid-life data loss).

## E3 — Capture-health tagging

**Question:** does the signal population this investigation measures span any known or
suspected degraded-capture interval that should not be silently blended with healthy
samples?

**Evidence class:** 1 (current persisted data), 4 (prior investigation's live measurements,
reused per execution-program §10's "do not rerun" instruction), 6 (inference, labeled).

**Method:** hourly `COUNT(*)` of KXBTC15M signals from `signal_log.db` across its full
history (2026-08-12 to present), looking for troughs/regime shifts; cross-reference against
`docs/superpowers/research/2026-08-25-realtime-root-cause-report.md` §4's capture-
completeness figures.

**Result (full table + interpretation):**
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-population-and-replay-gaps.md`. Two distinct
regimes found:

1. A **~4-day near-zero trough, 2026-08-18 (afternoon) through 2026-08-22 (afternoon)**
   (density falls from a 2026-08-12–08-17 baseline of roughly 10-30/hour to mostly 1/hour,
   many hours with zero rows at all). This trough **predates** the 2026-08-23 whale-gate
   cutover, so it is not explained by the gate-definition change. It also predates every
   timestamped measurement window in the realtime investigation's own root-cause report (its
   "busy hour" sample and the I4 capture-completeness readings are all from 2026-08-25).
   **Tagged `completeness_unknown`** — real evidence a WS-capture problem existed in this
   window does not currently exist (the realtime investigation's own findings don't cover
   this period), but neither does evidence it didn't; genuinely low real market whale
   activity that week is an equally live hypothesis. Not resolved here — flagged for a
   follow-up task rather than guessed.
2. A **sharp, sustained step-up in density starting almost exactly at the 2026-08-23 05:45
   UTC whale-gate cutover** (from single digits/hour to 30-130/hour). This is confidently
   attributed to the gate-definition change itself (a much broader contract-count floor vs.
   the old, geometrically near-certainty-biased dollar floor — ROADMAP.md's own 2026-08-17
   measurement already showed this), **not** a capture-health change — labeled inference,
   but a well-supported one given the timing precision and the already-documented mechanism.

Everything from 2026-08-23 onward through this session's data pull sits in the same window
the realtime investigation's own 2026-08-25 busy-hour measurements describe (35% whale-sized
capture during saturation, 100% at idle, 0% during an active drop). The current investigation
does not have per-hour capture-completeness numbers for this exact stretch — E4/E5's
"post-cutover" analysis should be read as **not capture-health-controlled**: some fraction of
the KXBTC15M population in that window is real signal, and some unknown fraction of what
*should* have arrived did not, per the realtime investigation's own not-yet-remediated
findings (execution program §4.3: "the remediation itself is not implemented"). This is
recorded as a limitation on E4/E5's conclusions, not silently absorbed into them.

## E4 — Gate marginal contribution (banded, cost-aware, sample-size-gated)

**Question:** for each entry gate, what would its rejected candidates' hypothetical EV have
been, broken out by unit-cost band (not just an unbanded mean, which the HARD COMMANDMENT's
own near-certainty-band trap can hide)?

**Evidence class:** 1 (current persisted data), 3 (deterministic re-computation via
existing reviewed logic for the unbanded baseline).

**Method:** `population_gate_summary()` (existing, reviewed function) via
`GET /api/candidate-log/summary` for the unbanded baseline; a new ad hoc read-only script
(not shipped) against a `mode=ro` URI connection to the live `candidate_log.db` (not
copied — 1 GB), grouping `rejection_events` by `(strategy, gate_name, unit_cost band)` with
the same sample-size gate `population_gate_summary()` already uses (`n >= 30` per group).

**Result:** full table and interpretation in
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-gate-marginal-contribution.md`. Headline:
every gate with enough samples to band shows a **strongly cost-band-dependent EV**, not a
uniform one — the unbanded `population_gate_summary()` figures materially understate how
gate-tightening/loosening decisions should actually be made. The clearest pattern: rejected
candidates in the **0.60-0.95 unit-cost band show negative hypothetical EV per contract
across every gate with sufficient KXBTC15M samples** (`min_contracts`: -0.073 to -0.049;
`entry_threshold`: -0.039 to -0.030), while the 0.20-0.40 and 0.95-1.01 bands are usually
positive. This 0.60-0.95 band is exactly where a real whale print is "probably right but not
cheap enough to be worth it" — a genuinely different regime from both the cheap-longshot
band and the near-certainty band.

## E5 — Adverse-selection root cause: regime-segmented reconciliation

**Question:** under the *current* gate configuration, does the entry-gate pipeline still
select a worse subset than the population it draws from (the original finding's shape), or
has that reversed/changed?

**Evidence class:** 1 (current persisted data), 3 (deterministic re-computation via the
existing, reviewed `series_watcher.reconcile()`, not a reimplementation).

**Method:** ran the real `services.series_watcher.reconcile("KXBTC15M", ...)` function
(imported directly, `DB_PATH` monkeypatched to scratchpad copies of `signal_log.db`/
`paper_broker.db` — never the live files) over two disjoint windows split at the whale-gate
cutover commit (`080a37b`, 1787463902 epoch): `[data_start, cutover)` and
`[cutover, now]`.

**Result:** full output in
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-gate-marginal-contribution.md`. The
pre-cutover window shows **zero paper-broker entries** (consistent with E2's reset finding —
that window's real trades are gone). The post-cutover window (the only one with real trades
today, n=90) shows `selection_delta_pts: +12.2` (isolated to the post-cutover 76.7h window)
/ `+2.4` (over the full available window including the empty pre-cutover stretch) — **the
current gates are selecting a *better*-than-population accuracy subset, the opposite
direction from the original 2026-08-17 finding**, not a reproduction of it.
`traded_signal_accuracy_pct` (65.2%) sits close to but under `breakeven_accuracy_pct`
(65.8%), and `exit_delta_pts` is mildly positive (+1.5) — the net -$169.81 realized P&L over
90 trades traces to `edge_pts` (population accuracy vs. mean entry price), not to selection
or exit. **Conclusion: the specific adverse-selection direction named in CLAUDE.md/
ROADMAP.md does not currently reproduce.** It cannot be fully root-caused in its original
form either, because E2 already established the original 12-trade sample is gone — this is
reported as a closed, evidence-backed reframing, not a non-answer.

## E6 — Advisory/calibration objective audit

**Question:** classify `advisory_engine`, `confidence_calibration`, and
`config_performance` as economically aligned / partially economic /
accuracy-calibration-only / unknown.

**Evidence class:** 1 (current source), reconciled against each module's own CHEATSHEET.md
(already-existing audit findings from 2026-08-22/23, cited not re-derived).

**Result:** all three classified in
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-advisory-calibration-execution-audit.md`.
Headline: `advisory_engine.py`'s suggestion-generating functions compare win rate alone
(confirmed via the CHEATSHEET's own prior finding, re-verified by grep) —
**accuracy/win-rate-only, not economically aligned**, with the one partial exception of
`_exit_pct_recommendation` which does reference cost terms.
`confidence_calibration.py` has zero `cost_basis`/`realized_pnl`/`unit_cost`/EV references
anywhere — **pure accuracy-calibration-only** by construction, not a partial case.
`config_performance.py` is not itself a recommendation engine — it is the fingerprint/
applied-change persistence substrate both of the above write through — reclassified as
**infrastructure, not a scored mechanism**, rather than force-fit into the four-way
taxonomy.

## E7 — Execution realism

**Question:** what spread/depth/fillability evidence actually exists in retained data, and
what is honestly absent?

**Evidence class:** 1 (current persisted data) via the existing, reviewed
`book_context_at_entry()` function (not reimplemented).

**Result:** `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-advisory-calibration-execution-audit.md`.
74/90 (82%) of current KXBTC15M entries matched a book snapshot within the existing 30 s
window; mean spread 0.98 cents, mean depth ratio 8.44 (resting size at the crossed side ÷
position size), **12/74 matched entries (16%) had depth ratio < 1.0** — the position size
exceeded quoted resting depth, meaning real execution likely would have paid up for
additional depth (price impact) that paper-mode fills do not model at all (paper fills
unconditionally at the signal price). `services/series_watcher.py`'s raw book-snapshot
capture only starts 2026-08-19T13:53 UTC — **no spread/depth reconstruction is possible for
any KXBTC15M signal before that date**, a hard replay-gap boundary, not a soft one.
Fill-probability/IOC-no-fill/partial-fill data **does not exist and cannot be fabricated**:
`kalshi_account_client.create_order` has never been exercised (trading has never been
enabled), so there is no real fill-outcome history to calibrate a model against — recorded
as a genuine gap requiring new capture design (Program 2 territory), not estimated here.

## E8 — Multi-gate interaction/cooldown decomposition — NOT DONE

**Why not done this pass:** `record_rejection()` records the *first* gate a candidate
failed per evaluation tick, not a full per-candidate pass/fail vector across every gate —
`rejection_events` cannot currently answer "what would have happened to candidates that
failed gate A and would also have failed gate B," only "what would have happened to
candidates that failed gate A" (marginal, not interaction). Confirmed by reading
`services/strategy_engine.py`'s gate-evaluation order and `candidate_log.record_rejection`'s
call sites — this is a schema/instrumentation limitation, not a query I chose not to run.
**Recommendation, not built here:** Program 2 should consider whether `evaluate()` recording
a full gate-outcome vector per candidate (not just the first failure) is worth the added
write volume, given `rejection_events` is already at ~5.8M rows after 3 days.

## E9 — Fill-probability/IOC/partial-fill modeling — assessed, not built

**Why not done this pass:** per the design doc's §6, fabricating a fill-probability model
without real fill data would violate the execution program's own "do not fabricate
precision the historical data can't support" instruction. The honest assessment (E7): a
book-context-based simulation (compare position size against `book_snapshots`' resting
depth at the crossed side, the same join `book_context_at_entry()` already performs) is the
only currently-buildable proxy, and only from 2026-08-19 onward, and only as an estimate of
*price impact if executed against the visibly resting book*, not a true fill-probability
model (Kalshi's real matching/queue behavior is not observable from a REST/WS book
snapshot). Recorded as a Program 2 design candidate in
`docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-remediation.md`, not attempted here.

## E10 — Direct reconstruction of the original 12-trade sample — CLOSED (infeasible)

Closed by E2's finding: `paper_broker.db` has been fully reset twice since the original
2026-08-17 measurement (most recently 2026-08-24T22:22:44). No copy, backup, or archive of
the pre-reset `trades` table was located (`data/backups/` was not inspected in this pass —
see the status report's insufficient-sample list for whether that's worth checking in a
follow-up, since it was out of scope for a `data/*.db` non-interference pass to explore
without first confirming the backup mechanism's own read-safety).

## E11 — Adversarial self-review

**Result:** `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-strategy-effectiveness-adversarial-review.md`.
Attacks each E1-E7 conclusion directly (alternative explanations, what would falsify it,
what wasn't checked).

## E12 — Deliverables synthesis

**Result:** `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-strategy-effectiveness-status-report.md`
(root-cause status, guard disposition, insufficient-sample list) plus
`docs/archive/lane-3-strategy-risk-execution/specs/2026-08-26-economic-strategy-remediation-design.md` and
`docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-remediation.md` (Program 2 candidate
design/plan, not approved for execution — see that plan's own header).
