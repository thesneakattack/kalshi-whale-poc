# Economic Strategy Remediation — Candidate Implementation Plan (Program 2)

**Status: NOT approved for execution.** Candidate task list only, per
`docs/archive/lane-3-strategy-risk-execution/specs/2026-08-26-economic-strategy-remediation-design.md`'s own status
line. Do not begin implementing any task below without: (1) explicit human review/approval
of the design doc, and (2) Program 1 (realtime data-plane remediation,
`feat/realtime-data-plane-remediation`) merged, since D2/D3/D4's ordering and several
findings' confidence level depend on it. Per the execution program §7, this is "Program 2,"
sequenced after "Program 1 — Realtime Foundation" and concurrent-research-only during it
("Program 2R," which is what this investigation itself was).

When this plan is picked up for real: re-ground against current `origin/main` first (per
`.claude/rules/branching-and-ci.md`'s "Resuming work in a fresh session"), re-run E1-E7's
underlying queries against then-current data rather than trusting the numbers cited below
(the population these tasks tune against will have grown substantially), and use
`superpowers:executing-plans` or `superpowers:subagent-driven-development` as the execution
mechanism — no bespoke orchestrator needed for *this* plan (unlike the investigation phase,
which has its own skill — see that skill's own note on why).

## Candidate tasks

### P2-1 — Banded, cost-aware gate diagnostic (D1)

TDD: `services/candidate_log.py`'s `population_gate_summary_banded()`, unit-tested against
a synthetic `rejection_events` fixture spanning multiple bands/sample sizes (mirroring the
existing `population_gate_summary()` test's fixture shape). Wire into
`GET /api/candidate-log/summary` (new `population_gates_banded` key, additive — do not
remove or reshape the existing `population_gates` key, live frontend/consumers depend on
it). Add `services/diagnostics/diagnostics.py`'s `check_gate_cost_bands`, folded into
`run_offline()`. CI/runtime guard disposition (per this investigation's status report §3):
permanent runtime diagnostic, same review bar as the existing `series_funnel`/
`selectivity_curve` checks — needs a genuine before/after false-positive check (a
synthetically-flat-EV fixture should produce no findings) before landing, not just a happy
path.

### P2-2 — Capture-health tagging helper (D2)

**Blocked on Program 1.** Do not start until the realtime remediation has landed a real
capture-completeness signal (per the design doc's own reasoning: without one, this can only
ever return `completeness_unknown`, which is honest but not useful enough to justify the
work now). When unblocked: TDD `services/diagnostics/capture_health.py`'s `tag_window()`
against both a synthetic-healthy fixture and a synthetic-degraded fixture (reusing whatever
telemetry Program 1 ships — e.g. dropped-message counts, queue-saturation duration — as the
`measurably_degraded`/`known_saturated_lossy` inputs). Reuse from both a new
`services/diagnostics` check and any future research script (this investigation's own E3
work is the first real caller/precedent).

### P2-3 — Price-impact estimate for thin-book entries (D3)

TDD: `series_watcher.py`'s `estimated_price_impact_pts` addition to `reconcile()`'s output,
tested against a synthetic `book_snapshots` fixture with known resting sizes at known
depth ratios (including a depth-ratio-exactly-1.0 boundary case and a no-matching-snapshot
case, both already-established conventions in this file's existing tests). Explicitly test
that it never mutates `realised_pnl`/`cost_basis` — additive reporting only, per the design
doc's "explicit non-goal."

### P2-4 — Multi-gate outcome vector (D4)

**Needs an explicit human sizing/retention decision before any code is written** — this is
not a green-lit implementation task yet, it is a design question this plan defers rather
than resolves: given `rejection_events` is already ~5.8M rows/3 days at one-gate-per-row,
what write-volume increase (proportional to gates-checked-per-candidate) is acceptable, and
does `data/candidate_log.db`'s retention/pruning story (if any exists — not audited in this
investigation) need to change first. Do not implement without that decision recorded.

### P2-5 — advisory_engine entry-side cost-awareness (D5)

TDD: thread E4's banded-EV data (via P2-1's new function, a real dependency — P2-5 should
follow P2-1, not run in parallel with it) through
`_entry_threshold_recommendation`/`_longshot_bonus_recommendation`/
`_cross_variant_recommendations`/`_rejected_candidate_recommendations`/
`_category_conditional_recommendations`/`_series_conditional_recommendations`, one function
at a time (six separate, independently-testable changes, not one large patch — matches this
repo's own "smaller well-bounded units" design guidance). Each function's test should
include a fixture where win-rate-favored and cost-favored options disagree, proving the
function's recommendation actually changes once cost-awareness is added (not just that it
runs without error).

## Explicit sequencing

```
Program 1 (realtime remediation) merges
        │
        ▼
Re-verify E3/E4/E5's provisional findings against a capture-health-controlled sample
        │
        ├── P2-1 (no Program-1 dependency, can start once re-verified) ──► P2-5
        ├── P2-2 (blocked on Program 1's capture-completeness signal)
        ├── P2-3 (no Program-1 dependency, independent of P2-1/P2-2)
        └── P2-4 (blocked on a human write-volume/retention decision, independent otherwise)
```

**Re-verification ran 2026-08-27** —
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-27-economic-e3-e5-reverification.md`. Outcome: E4
reproduces and strengthens under a genuinely capture-health-controlled sample (no design
change indicated for P2-1). E3's open questions (the 08-14 gap, the 08-18–08-22 trough)
remain unresolved, but a real capture-health signal (`loop_watchdog.stall_max_ms`) now
exists for the first time — for the post-merge population only, not retroactively — and it
surfaced a severe, sharply-bounded, already-independently-fixed stall incident
(`bb5806c`) inside the naive "post-merge" window; P2-2 should build on this signal rather
than assume Program 1 shipped the richer dropped-message/queue-saturation telemetry its
own text speculated about. E5 partially reproduces: the edge/pricing-gap finding
reproduces and worsens in every window measured; the "selection and exit are both
favorable" component reproduces in the larger (>90-entry) samples but not in the
smallest, cleanest capture-health-controlled sub-sample (n=32-34, too thin to be
conclusive either way) — flagged as an open tension for whoever next revisits E5, not
resolved by this pass. The underlying trade sample has grown from 90 to 142 post-cutover
entries, still short of the status report's own "several hundred entries" re-run trigger
(§4) — noted here for the first time in this plan, not independently corroborated by it.

## What this plan deliberately does not include

No task changes `config/settings.yaml`'s live values. No task enables real trading or
touches `kalshi_account.trading_enabled`. No task is an autonomous-remediation/auto-tuning
mechanism — every candidate here produces new *data surfaces* (a diagnostic, a reported
estimate, a recommendation a human still has to apply), consistent with
`.claude/rules/autonomous-quality-coordination-evidence.md`'s protected-domain list
(strategy/EV/fee/P&L semantics) and this repo's existing advisory/calibration auto-apply
convention (gated behind an explicit config flag + typed confirmation phrase, never silent).
