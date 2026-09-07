# Economic Strategy Effectiveness & Execution Realism — Investigation Design

**Status:** design for the investigation itself (not the eventual remediation). Mirrors
`docs/superpowers/specs/2026-08-25-realtime-data-plane-investigation-design.md` in shape;
scoped by `docs/kalshi-personal-production-execution-program-2026-08-26.md` §6.1
("Immediate 3 — parallel research worktree").

## 1. Why this exists

`docs/kalshi-personal-production-execution-program-2026-08-26.md` §6.1 names this
investigation as **required** before real capital can depend on the strategy: the app has
never decomposed *where* economic edge is created or destroyed between a raw exchange
print and an actually executable, priced, fee-adjusted trade. The concrete motivating
symptom — KXBTC15M whale signals resolving 88.8% correct across 394 settled signals while
the 12 signals the entry gates actually traded resolved only 58.3% (ROADMAP.md, measured
2026-08-17) — has sat un-root-caused since it was first measured.

## 2. Core question

> Where is economic edge created or destroyed between the raw exchange event and an
> actually executable trade?

Chain under investigation: `raw event → capture health → candidate → signal →
confidence/features → each gate → selected trade → decision-time market → latency →
fillability → fees/slippage → exit/settlement → net result`.

## 3. Non-interference constraints (binding, not advisory)

Per the execution program's "Immediate 3" and this task's own dispatch instructions:

- Branch/worktree: `research/economic-strategy-effectiveness`, based on current
  `origin/main` at dispatch time (verified: `f784a64`, in sync).
- **Research only.** No edits to `main.py`, the realtime scheduler, the WS consumer, the
  Kalshi rate-limiter implementation, live strategy config (`config/settings.yaml`), or any
  live `data/*.db` file.
- `feat/realtime-data-plane-remediation` (branch and its worktree
  `.claude/worktrees/agent-a77b293d25b099924`) is active, locked, in-progress work — not
  read from as a dependency (unmerged), not touched.
- `data/*.db` files are read read-only: either a `cp` of a small file into scratchpad, or a
  direct `sqlite3`/`sqlite3 URI mode=ro` read against the live file for anything too large
  to duplicate reasonably (`series_watcher.db` is ~13.5 GB; `candidate_log.db` ~1 GB — both
  queried live, read-only, never copied).
- Deliverables are research/spec/plan documents. No runtime or CI code ships on this
  branch — where a finding implies a permanent guard, this investigation records the
  *disposition* (what kind of guard, roughly where it lands, which future task owns it),
  not the guard itself. Building it is Program 2 (economic remediation), gated behind
  Program 1 (realtime remediation) per the execution program's dependency sequence (§7).

## 4. Evidence classes (per `.claude/rules/autonomous-quality-coordination-evidence.md`'s
taxonomy, reused here since this investigation makes the same kind of policy-sensitive
claims)

1. Current source/persisted-data behavior — direct SQL against `signal_log.db`,
   `candidate_log.db`, `paper_broker.db`, `reset_log.db`, read live or from a
   point-in-time copy.
2. Current git/commit history — e.g. the exact commit and timestamp of the
   dollar-notional → contract-count whale-gate cutover, used as a regime boundary.
3. Deterministic re-computation using the app's own existing, already-reviewed functions
   (`services/series_watcher.py`'s `reconcile()`/`funnel()`, `services/candidate_log.py`'s
   `population_gate_summary()`) run against real data with different time windows — not a
   parallel reimplementation of their logic, to avoid a second, unaudited source of the
   same numbers.
4. Live runtime evidence via existing read-only QCP/diagnostics HTTP endpoints
   (`GET /api/quality/summary`, `GET /api/diagnostics/series/{series}`,
   `GET /api/candidate-log/summary`) — all confirmed side-effect-free reads (no Kalshi
   network calls; see `services/quality/CHEATSHEET.md`/`services/diagnostics/CHEATSHEET.md`
   own "no network" proofs). `GET /api/diagnostics/trade-capture` (the one diagnostic that
   makes real Kalshi REST calls) is **deliberately not invoked** in this pass: it would
   spend REST-limiter budget shared with the actively-running realtime-remediation
   worktree's own measurement work, which this investigation must not contend with.
5. Authoritative upstream documentation (`docs/kalshi/`) — consulted for any question about
   Kalshi market-lifecycle/result semantics that bears on how "correct"/"resolved" is
   defined (already resolved once, see `services/whale_calibration/CHEATSHEET.md`'s
   `determined`-vs-`finalized` finding — reused, not re-derived).
6. Inference/hypothesis — labeled explicitly wherever used (e.g. the unexplained
   2026-08-18–08-22 signal-density trough, where a specific cause is not established).

## 5. Methodology per required-evidence area (execution program §6.1)

- **Gate marginal contribution:** `services/candidate_log.py`'s `rejection_events` table
  (undeduped population of gate rejections, `unit_cost`-tagged since 2026-08-23) is the
  existing data source. `population_gate_summary()` already reports one hypothetical win
  rate per gate, but — per its own docstring and ROADMAP.md's own "still not done" note —
  never banded by `unit_cost`, which is exactly the trap CLAUDE.md's HARD COMMANDMENT
  table demonstrated (a near-certainty band can show a high win rate while losing money on
  every settlement). This investigation adds that banding as an ad hoc, read-only analysis
  script (not shipped code) against a **read-only URI connection to the live
  `candidate_log.db`** (1 GB; not copied) grouping by `(strategy, gate_name, unit_cost
  band)` with the same `min_samples` sample-size gate `population_gate_summary()` already
  uses, and reports EV-per-contract (`win_rate − mean_unit_cost`) per band, not just win
  rate.
- **Adverse-selection root cause:** `series_watcher.reconcile()` decomposes
  `realised_win_rate − signal_accuracy` into `selection_delta` + `exit_delta` already, plus
  a separate money-side `edge_pts` (`signal_accuracy − breakeven_accuracy`). Run across
  disjoint time windows split at the whale-gate cutover commit
  (`080a37b`, 2026-08-23 05:45 UTC) — the boundary the original 2026-08-17 measurement sits
  entirely before and the live gate configuration sits entirely after — rather than
  assuming today's `reconcile()` output describes the same regime the original 12-trade
  figure came from.
- **Advisory/calibration objective audit:** grep each candidate mechanism
  (`services/advisory/advisory_engine.py`, `services/whale_calibration/
  confidence_calibration.py`, `services/config_performance.py`) for any
  `cost_basis`/`realized_pnl`/`unit_cost`/EV term in the functions that actually decide a
  recommendation (not just in unrelated reporting functions), classify each as economically
  aligned / partially economic / accuracy-calibration-only / unknown, and reconcile against
  the audit findings each module's own CHEATSHEET.md already recorded (2026-08-22/23) rather
  than re-deriving from zero.
- **Execution realism:** `series_watcher.book_context_at_entry()` already joins each real
  paper entry to the nearest book snapshot within a 30 s window and reports spread and a
  depth ratio (resting size at the crossed side ÷ position size). Use its existing output
  rather than re-deriving spread/depth from raw `book_snapshots` rows. Explicitly do not
  attempt to fabricate a fill-probability/IOC/partial-fill model — paper mode has never
  placed a conditional order, so there is no real fill-outcome data to calibrate one
  against; report this as a design/data gap, not a guessed number.
- **Replay gap analysis:** cross-reference `reset_log.db`'s `reset_events` table (real,
  timestamped, per-domain reset/purge history) against `signal_log.db`, `paper_broker.db`,
  and `candidate_log.db`'s actual earliest-row timestamps, to determine precisely what
  survived which purge — rather than assuming continuity.
- **Capture-health rule:** build an hourly KXBTC15M signal-density time series from
  `signal_log.db` (already-captured, zero new instrumentation) and cross-reference visible
  troughs/regime-shifts against `docs/superpowers/research/2026-08-25-realtime-root-cause-
  report.md`'s already-measured busy-hour capture-completeness figures (WS capture 100% at
  idle, 35% whale-sized during a saturated busy hour, 0% during an active drop episode) —
  reusing that investigation's measurements per §10's "do not rerun" instruction, not
  re-measuring capture health from scratch. A density anomaly that predates any known
  degraded-capture measurement window is tagged `completeness_unknown`, not attributed to a
  guessed cause.

## 6. What this pass will not attempt

- A full historical strategy replay/backtest engine (execution program names this as
  Program 2 territory, not 2R).
- A canonical `TradeIntent`/live-execution design (§6.2 of the execution program — a
  separate, later investigation, explicitly sequenced after this one).
- Any change to `config/settings.yaml`'s live values, even ones this investigation's own
  evidence might suggest — that is a live strategy-tuning decision belonging to Program 2
  execution, not this research pass (same restraint `advisory_engine`'s existing
  4-unapplied-suggestions precedent already established, per ROADMAP.md).
- A GitHub-write-authority or auto-remediation mechanism for anything found here — out of
  scope for this investigation and, per
  `.claude/rules/autonomous-quality-coordination-evidence.md`, `strategy/EV/fee/P&L
  semantics` is one of the explicitly protected domains no autonomous actor may act on.

## 7. Deliverables (per execution program §6.1, mapped to files)

| Deliverable | File |
|---|---|
| Research report | `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-population-and-replay-gaps.md`, `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-gate-marginal-contribution.md`, `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-advisory-calibration-execution-audit.md` (all moved there 2026-09-06, planning-lanes migration) |
| Adversarial analysis | `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-strategy-effectiveness-adversarial-review.md` (moved there 2026-09-06, planning-lanes migration) |
| Design/spec | this file, plus `docs/archive/lane-3-strategy-risk-execution/specs/2026-08-26-economic-strategy-remediation-design.md` (moved there 2026-09-07, planning-lanes migration; Program 2 candidate design, not yet approved for execution) |
| Consolidated implementation plan | `docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-remediation.md` (moved there 2026-09-07, planning-lanes migration; candidate task list; execution gated behind Program 1 per execution program §7) |
| Permanent measurement/guard disposition | recorded in the status report (§9 below) and in the remediation plan's own task list — not shipped as code on this branch |
| Insufficient-sample list | `docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-strategy-effectiveness-status-report.md` (moved there 2026-09-06, planning-lanes migration) §"Insufficient / open" |

## 8. Self-review discipline

Every finding below states which evidence class (§4) supports it. Per the execution
program's §9 mandatory self-review gate, each major finding is run through: state the
result (fact vs. inference vs. target design vs. open question) → search for
contradiction → attack the conclusion → scope check → active-work/ownership check →
regression-guard disposition → correct before continuing. The adversarial-review document
is where the "attack the conclusion" pass is written out explicitly rather than done
silently.
