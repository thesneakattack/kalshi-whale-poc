# Economic Strategy Effectiveness & Execution Realism — Status Report (E12)

**Status:** investigation phase substantially complete (E1-E7, E11 done with real evidence;
E8-E10 explicitly scoped-not-executed or closed-infeasible — see the plan file's status
table). This is the synthesis document the execution program's §6.1 deliverable list calls
for; read the four documents it draws from for full evidence
(all moved to `docs/archive/lane-4-analytics-advisory-research/research/` on
2026-09-06, planning-lanes migration:
`2026-08-26-economic-population-and-replay-gaps.md`,
`2026-08-26-economic-gate-marginal-contribution.md`,
`2026-08-26-economic-advisory-calibration-execution-audit.md`,
`2026-08-26-economic-strategy-effectiveness-adversarial-review.md`).

**Addendum (2026-08-26, resumed after this branch was stopped mid-flight):** Program 1
(realtime data-plane remediation) merged into `main`@`22d1a79` at 2026-08-26T12:01:39-05:00
— the dependency §5's conflict check named as still-open. This branch has been merged
forward onto current `main` and carries that code. **The re-verification §5 called for
cannot be done yet, and that is a calendar-time fact, not unfinished work:** Program 1's
capture-side fixes (candidate dedup, WAL journaling, the `series_watcher` cross-thread
race fix, batch-capacity-truncation retry) affect data captured going forward from the
merge, not retroactively, and as of this addendum only ~5.4 hours have elapsed since
merge — nowhere near the "several hundred `entries`" re-run trigger §4 already specifies
for even the pre-existing 90-trades/3.2-days post-cutover sample. E4/E5's post-cutover
findings therefore remain exactly as provisional as this document originally said; nothing
here should be read as having resolved that caveat. Re-run against `origin/main` when this
is next picked up, per the remediation plan's own "when this plan is picked up for real"
instruction, rather than trusting either this addendum's or the original findings' numbers
as still-current.

## 1. Answer to the core question, as far as current evidence supports it

> Where is economic edge created or destroyed between the raw exchange event and an
> actually executable trade?

**For the specific, named symptom (88.8%/394 → 58.3%/12 on KXBTC15M): the original gap
cannot be root-caused in its original form, because the underlying trade sample no longer
exists (E2) and the gate configuration that produced it no longer exists either (E1) — both
verified facts, not inference.** What this investigation establishes instead, with current
full-population evidence:

- The 88.8% figure was measured on a population skewed 75.5% into the 0.80-1.0 unit-cost
  band (mean unit cost 0.858) under a dollar-notional whale gate — a population where high
  "accuracy" is substantially a mechanical consequence of price concentration near
  certainty, not proof of directional signal quality (E1, strengthened by the adversarial
  review's own falsification attempt).
- Under the **current** gate configuration (contract-count based, live since 2026-08-23),
  the entry pipeline is currently selecting a **better**-than-population-accuracy subset
  (`selection_delta_pts` +2.4 to +12.2 depending on window) — the opposite of the original
  finding's direction (E5).
- The current (much smaller) shortfall, -$169.81 realized over 90 trades in ~3.2 days,
  traces to a **pricing/edge gap** (mean entry unit cost 0.658 vs. breakeven 65.8%, traded
  accuracy 65.2% — a razor-thin, currently negative edge), not a selection or exit
  problem — both of those components are currently net favorable (E5).
- A genuine, previously-unmeasured gate-level pattern exists underneath that pipeline-level
  number: rejected candidates in the **0.60-0.95 unit-cost band show negative hypothetical
  EV per contract across every gate with sufficient samples**, both KXBTC15M-specific and
  all-series (E4) — a real, actionable signal about *where* future gate tuning should focus,
  independent of whether the pipeline-level selection effect is currently positive or
  negative.

**This is a legitimate, evidence-backed answer, not a non-answer** — it reframes "why were
the 12 selected trades bad" (unanswerable) into "is the currently-live gate configuration
adversely selecting, and if not, where does its shortfall actually come from" (answered:
no, and pricing).

## 2. What is a verified fact vs. inference (execution program §9.A)

**Verified fact** (evidence class 1-3, directly queried or re-run against real
data/existing reviewed code): the 394/88.8% population size and accuracy no longer match
current data; the regime break's timing and unit-cost-distribution mechanism; the
`paper_broker.db` reset history and its consequence for E10; the current banded EV pattern;
the current `selection_delta`/`exit_delta`/`edge_pts` decomposition; the advisory/
calibration cost-awareness split; the book-context depth-ratio findings.

**Inference, explicitly labeled as such in the source documents:** the 08-18–08-22
signal-density trough's cause (capture-health problem vs. low real market activity — left
open); whether `rejection_events`' rejection-time `unit_cost` is a close enough proxy for a
real hypothetical entry price (E4's adversarial-review caveat); whether the current
positive `selection_delta_pts` will hold up on a larger post-cutover sample (E5's
adversarial-review caveat, given the regime is only 3 days old).

**No target-design claim is made in this document** — this is an investigation, not a
remediation. `docs/superpowers/plans/2026-08-26-economic-strategy-remediation.md` names
candidate remediation directions; none is approved for execution by this document.

## 3. Permanent measurement/guard disposition

Per `.claude/rules/quality-capabilities.md`'s investigation-to-guard decision tree, applied
to each real finding class this investigation produced:

| Finding class | Disposition | Where it lands |
|---|---|---|
| Banded, cost-aware, sample-size-gated EV per gate (E4) | **Permanent runtime diagnostic** — this is exactly the shape of `services/diagnostics/diagnostics.py`'s existing `check_series_funnel`/`selectivity_curve` checks (offline, local-data-only, already-reviewed pattern) | Program 2, as an extension to `services/candidate_log.py`'s `population_gate_summary()` (add unit-cost banding as a parameter/second function) plus a new `services/diagnostics` check surfacing it in `GET /api/quality/summary`. Not shipped on this research-only branch. |
| Capture-health tagging for a given analysis window (E3) | **Shared logic, used by runtime and future research alike** — the hourly-density-anomaly detection this investigation hand-rolled should not be re-hand-rolled every time a future investigation needs it | Program 2 candidate: a small `capture_health.tag_window(series, since, until)` helper reusable by both a runtime diagnostic and any future research script, per the design doc's methodology section. Not built here (research-only branch). |
| Price-impact estimate for entries with insufficient depth (E7) | **Genuinely needs new capture/design work first** — not yet a guard, because the underlying computation (book-walk price-impact estimate) doesn't exist yet | Program 2 design candidate, detailed in `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md` |
| `data/backups/`'s retention not reaching back far enough to have helped E2 (adversarial-review finding) | **One-off observation, not a new guard** — this investigation is not the owner of backup retention policy; recorded for `services/backup/`'s own future audit, not acted on here | Noted for `services/backup/CHEATSHEET.md`'s next audit pass, not this investigation's responsibility to fix |
| Multi-gate interaction schema gap (E8) | **Existing guard/instrumentation limitation, already correctly scoped as future work by ROADMAP.md's own prior note** — not a new finding needing a new disposition, restated for completeness | Program 2, contingent on a cost/benefit call about `rejection_events`' write volume (already ~5.8M rows/3 days) |
| advisory_engine's entry-side win-rate-only recommendation functions (E6) | **Already covered** — `services/advisory/CHEATSHEET.md` recorded this exact finding 2026-08-22/23; this investigation re-verified it, not re-discovered it. No new disposition needed; the existing CHEATSHEET entry already names it as the top audit priority for a future advisory-module pass. | N/A — already tracked |

## 4. Insufficient / genuinely open — do not treat as settled

- **The 2026-08-18–08-22 signal-density trough's cause** (E3): capture-health problem vs.
  low real market activity, unresolved. Needs either a live REST-based historical
  reconstruction (not attempted, and not clearly possible retroactively) or an independent
  BTC-market-activity cross-reference (not attempted).
- **90 post-cutover trades over 3.2 days** (E5): the entire "selection is currently
  favorable, edge is currently the problem" conclusion rests on a regime only 3 days old.
  Needs re-verification once meaningfully more post-cutover history accumulates (a natural
  re-run point: once `entries` in `series_watcher.reconcile("KXBTC15M", hours=<large>)`
  reaches several hundred).
- **Rejection-time `unit_cost` as a proxy for hypothetical real entry price** (E4): not
  validated against real signal-to-entry price drift; the existing 27.17 s mean lag (E5)
  bounds how large this gap plausibly is, but it was not directly measured.
- **Full depth-ratio distribution, not just mean + a <1.0 count** (E7, adversarial review):
  cheap follow-up, not executed this pass.
- **Fill-probability/IOC/partial-fill modeling** (E9): assessed as currently unbuildable
  without either real order data (doesn't exist) or a book-walk price-impact proxy (design
  candidate only, not built).
- **Multi-gate interaction/cooldown decomposition** (E8): blocked on a schema change
  (`rejection_events` records only the first-failing gate per candidate), not attempted.
- **Whether the 12 original 2026-08-17 trades are recoverable from any source this
  investigation didn't check** (E2/adversarial review): `data/backups/` checked and doesn't
  reach back far enough; no other backup/archive location was searched (e.g. any
  operator-side manual export, outside this repo's own `data/` tree, is unknown and
  unknowable from inside this investigation).
- **Whether the KXBTC15M-specific banded-EV pattern (E4) generalizes to other actively-
  traded series** (KXBTCD, KXATPMATCH, etc. — the live `series_funnel` diagnostic already
  shows some of them in a `fail` state per this session's `/api/quality/summary` pull, but
  this investigation did not run the banded-EV analysis for any series but KXBTC15M).

## 5. Conflict/overlap check with concurrently active work (execution program §9.E)

- `feat/realtime-data-plane-remediation`: not read from, not touched, per the design doc's
  constraints. This investigation's findings (E3's capture-health caveat, specifically)
  *depend on* that work eventually landing — E3/E4/E5's post-cutover numbers should be
  treated as provisional until Program 1 (realtime remediation) ships and capture
  completeness for the KXBTC15M analysis window can be re-verified. This is stated as a
  dependency, not resolved by this investigation.
- No other open PR/branch (`gh pr list --state all` at E0) overlaps this investigation's
  file surface (`docs/superpowers/{specs,plans,research}/2026-08-26-economic-*`,
  `.claude/skills/economic-strategy-effectiveness-investigation/`) — the two open docs PRs
  (#20 personal-production-execution-program, #21 doctrine-final-truth) are both pure
  `CLAUDE.md`/`ROADMAP.md`/program-doc edits with no file-path collision.
