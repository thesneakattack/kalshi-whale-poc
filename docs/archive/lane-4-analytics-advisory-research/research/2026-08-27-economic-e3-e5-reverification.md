# Economic Strategy Effectiveness — E3/E4/E5 Re-Verification Against Post-Program-1 Data

Precondition run for `docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-remediation.md`'s
(Program 2) "Explicit sequencing" section: *"Program 1 (realtime remediation) merges →
Re-verify E3/E4/E5's provisional findings against a capture-health-controlled sample."*
Program 1 P0-P2 merged into `main`@`22d1a79` on 2026-08-26T17:01:39Z (`main`'s
`22d1a79db796d565d523b4b7f0994107f81cd901`, confirmed by `git log`/`git show`) —
candidate-duplication (candidate_ledger `claim()`, gated on `_handle_signal` via Task 10),
WAL on `candidate_ledger.db`, and a real cross-thread lock on `series_watcher`'s capture
buffers. This document re-runs E3/E4/E5's original queries (methodology unchanged from
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-population-and-replay-gaps.md` and
`docs/archive/lane-4-analytics-advisory-research/research/2026-08-26-economic-gate-marginal-contribution.md`
(both moved there 2026-09-06, planning-lanes migration) against
current data, pulled 2026-08-27 ~06:00 UTC (`NOW` epoch `1787810000`), all read-only
(`data/signal_log.db`, `data/paper_broker.db`, `data/reset_log.db`, `data/candidate_ledger.db`,
`data/observability.db` copied to scratchpad; `data/candidate_log.db` — 1.2 GB — read via a
live `mode=ro` URI connection, never copied or written to). No `data/*.db` file was written,
no `config/settings.yaml` value changed, no application code changed.

**Bottom line: E4 reproduces and strengthens. E3's open questions are unchanged, but a
real capture-health signal now exists for part of the population for the first time — and
it reveals a distinct, already-diagnosed-and-fixed severe-degradation incident sitting
inside the "post-merge" window naively used for E4/E5's original capture-health caveat. E5
partially reproduces: the edge/pricing-gap component (the original headline finding)
reproduces and gets worse; the selection/exit-favorable component reproduces in the larger,
less-clean samples but does not clearly reproduce in the smallest, most capture-health-
controlled sub-sample — though that sub-sample (n=32-34) is too thin to overturn anything.**

## Method note: how "capture-health-controlled" was actually defined here

E3's original pass (2026-08-26) could only tag the post-cutover population
`completeness_unknown` — no direct capture-completeness telemetry existed yet. Program 1
shipped `services/loop_watchdog.py` (100 ms sampling, `_STALL_THRESHOLD_SEC = 0.05`,
persisted into `observability.db` via `capture_from_runtime`), which is new, real,
class-1 evidence that did not exist for the original E3 pass. Querying it for the
post-merge window surfaced something the original design doc's "wait for Program 1 to
ship a capture-completeness signal" language did not anticipate: **a severe, sharply
bounded stall incident immediately after the merge deploy, already root-caused and fixed
by a separate, independent commit** (`bb5806c`, "fix: stop candidate_log.
population_gate_summary from blocking the event loop", 2026-08-26T14:11:29-05:00 /
2026-08-26T19:11:29Z — 2h10m after the Program 1 merge (2026-08-26T17:01:39Z), itself
found via a live py-spy trace per its own commit message and `ROADMAP.md`). This is
unrelated to Program 1's own changes; it is a
same-day, independently-discovered-and-fixed blocking-call bug that happened to surface
under the increased dashboard-poll/`rejection_events` volume shortly after Program 1
deployed.

`loop_watchdog.stall_max_ms` per capture window (612 windows, merge → now):

| Sub-window | Windows | Avg stall_max_ms | Max stall | Windows >5s stall |
|---|---:|---:|---:|---:|
| Deploy-transient (2026-08-26T17:01:39Z → T20:35:00Z, 3.6h) | 110 | 9,928 ms | 43,003 ms | 58/110 (53%) |
| Stabilized (2026-08-26T20:35:00Z → now, 9.3h) | 502 | 248 ms | 6,518 ms | 1/502 (0.2%) |

The deploy-transient window's 58 stall events of 5-43 seconds each cluster almost
exactly between `bb5806c`'s parent state and ~90 minutes after its commit — consistent
with the fix needing a `--reload` pickup plus draining already-queued blocking calls, not
an ongoing problem. **During the deploy-transient window, `signal_log` recorded only 10
KXBTC15M signals in 3.6h (vs. a ~55-73/h contemporary baseline) and zero paper-broker
entries** — real, measured evidence of severe capture loss during that specific incident,
not inference. This confirms the *mechanism* the original E3 pass could only gesture at
("an unremediated realtime data plane... not capture-health-controlled") but had no direct
measurement for.

**Revised capture-health tag for this session, reusing but extending E3's convention:**
`measurably_healthy` (2026-08-26T20:35:00Z onward — the "stabilized" window below) vs.
`measurably_degraded` (2026-08-26T17:01:39Z–T20:35:00Z, the deploy-transient window,
excluded from the tables below) vs. `completeness_unknown` (everything before Program 1's
telemetry existed, i.e., the entire pre-merge population — unchanged from E3's original
tag; loop_watchdog has zero samples before the merge by construction, so pre-merge
capture health remains exactly as uncharacterizable as E3 originally found it). The
2026-08-14 gap and 2026-08-18–08-22 trough E3 flagged as open remain **unresolved** —
no new evidence bears on them; they predate any capture-health telemetry this repo has
ever shipped.

## E3 — Capture-health tagging: reproduces (open questions unchanged), method upgraded

- The 2026-08-14 missing-day gap and 2026-08-18–08-22 near-zero trough: **unchanged,
  still open**. No new evidence.
- The 2026-08-23 gate-cutover density step-up: still attributed to the gate-definition
  change (E1), unchanged.
- **New finding, not in the original E3 pass:** post-merge KXBTC15M signal density
  averages 72.9/h (938 signals / 12.9h) vs. 54.4/h pre-merge (4,528 signals / 83.3h) — a
  +34% density increase. Entirely attributable to the deploy-transient/stabilized split
  above, not a uniform post-merge effect: the deploy-transient window's 10 signals/3.6h
  (~2.8/h) is far below baseline (severe loss during the incident), while the stabilized
  window alone runs 928 signals/9.3h ≈ **99.8/h**, meaningfully above the pre-merge
  baseline. Two live hypotheses, neither fully eliminated here (same honesty standard as
  the original E3's open trough): (a) Program 1's candidate-duplication/cross-thread-race
  fixes genuinely recovered previously-lost captures (mechanistically plausible — a fixed
  race in `series_watcher`'s capture buffers would show up as exactly this kind of density
  increase); (b) real market whale-print volume was simply higher in this specific
  9.3-hour window (BTC market activity is not independently cross-referenced here, same
  gap E3's original pass named for the 08-18–08-22 trough). Labeled **inference** for the
  mechanism, **measured fact** for the density numbers themselves.

## E4 — Gate marginal contribution, banded: reproduces and strengthens

Re-ran the identical banded query (`rejection_events`, `WHERE unit_cost IS NOT NULL AND
resolved=1 AND side IN ('yes','no')`, `n>=30`, same six unit-cost bands) against
`KXBTC15M-%` tickers, for (a) the full current post-cutover population and (b) restricted
to `rejected_at >= 2026-08-26T20:35:00Z` — the `measurably_healthy` sub-sample.

| Gate | Band | Original (2026-08-26) EV/ctr | Current full-population EV/ctr | Current healthy-sub-sample EV/ctr |
|---|---|---:|---:|---:|
| `min_contracts` | 0.00-0.20 | +0.0252 | +0.0254 | +0.0334 |
| `min_contracts` | 0.20-0.40 | +0.0692 | +0.0663 | +0.0662 |
| `min_contracts` | 0.40-0.60 | +0.0048 | +0.0088 | +0.0202 |
| `min_contracts` | 0.60-0.80 | **-0.0728** | **-0.0726** | **-0.0853** |
| `min_contracts` | 0.80-0.95 | **-0.0491** | **-0.0545** | **-0.0935** |
| `min_contracts` | 0.95-1.01 | +0.0029 | +0.0020 | +0.0029 |
| `entry_threshold` | 0.60-0.80 | -0.0392 | -0.0785 | -0.0324 |

**Reproduces cleanly, and the central finding (a strongly negative-EV 0.60-0.95 unit-cost
band for `min_contracts` rejects, positive on both sides of it) is if anything *more*
pronounced in the capture-health-controlled sub-sample** (-0.0853/-0.0935 vs. the original
-0.0728/-0.0491). All sample sizes remain large (n in the hundreds of thousands to low
millions even restricted to the 9.3h healthy window — `rejection_events`' write volume is
high enough that the `n>=30` gate is never the binding constraint here, unlike E5).
`entry_threshold`'s 0.60-0.80 band is noisier across the three measurements (n=53-107,
much smaller than `min_contracts`) but stays negative in all three. **Evidence class 1
(current persisted data) + 3 (deterministic re-computation via the same SQL shape as the
original, reviewed `population_gate_summary()` query).** This is the strongest, most
sample-rich re-verification result of the three — Program 2's P2-1 (the banded-EV
diagnostic) rests on solid, reproducing ground.

## E5 — Adverse-selection root cause: partially reproduces, thinnest evidence of the three

Re-ran `services.series_watcher.reconcile("KXBTC15M", ...)` (the actual function, not
reimplemented — `DB_PATH` monkeypatched to scratchpad copies of `signal_log.db`/
`paper_broker.db`, confirmed no live file touched) over several windows:

| Window | Hours | Entries | Closed | Selection Δ | Exit Δ | Edge (pts) | Realised P&L | $/trade |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original (2026-08-26 pass, post-cutover) | 76.7 | 90 | 90 | +12.2 | +1.5 | -12.8 | -$169.81 | -$1.89 |
| Current full post-cutover (cutover → now) | 96.1 | 142 | 141 | +8.3 | +0.3 | -13.4 | -$3,685.62 | -$26.14 |
| Post-reset, pre-merge (2026-08-24T22:24Z → merge) | 42.6 | 108 | 108 | +11.7 | +0.9 | -13.0 | -$1,148.50 | -$10.63 |
| Post-merge, unfiltered (merge → now) | 12.9 | 34 | 33 | -1.3 | -1.4 | -16.4 | -$2,537.12 | -$76.88 |
| **`measurably_healthy` sub-sample (20:35Z → now)** | 9.3 | 34 | 33 | **-1.1** | **-1.4** | **-16.6** | **-$2,537.12** | **-$76.88** |

(The post-merge and `measurably_healthy` rows are nearly identical because the
deploy-transient sub-window contributed zero paper-broker entries — the severe capture
loss during that incident meant no trades were placed at all in that period, so it drops
out of the reconciliation on its own.)

**What reproduces:** the edge/pricing-gap component — the original finding's actual
headline ("the shortfall is a pricing gap, not a selection or exit problem") — reproduces
in every window measured, and gets *worse*, not better: `edge_pts` is -12.8 in the
original, and -13.0 to -16.6 across every current window, with the capture-health-
controlled sub-sample showing the most negative edge of all (-16.6). Realised $/trade also
degrades sharply across every current window vs. the original (-$1.89 → -$10.63 to
-$76.88/trade) — the strategy is currently losing more per trade, not less, than when it
was first measured.

**What does not clearly reproduce:** the original and larger current samples (both >90
entries) show `selection_delta_pts` confidently positive (+8.3 to +12.2) and `exit_delta`
mildly positive (+0.3 to +1.5) — "selection and exit are both currently favorable, only
pricing is the problem." The smallest, most capture-health-controlled sample (n=32-34)
shows both **near zero to mildly negative** (-1.1, -1.4) instead — not a confident reversal
back toward the original 2026-08-17 adverse-selection direction (a -1.1 selection delta on
n=32 traded-resolved signals is well within noise of zero), but it does not confidently
reproduce "selection is favorable" either. **This is a genuine, labeled tension, not
resolved here**: the two possible readings are (a) selection/exit really are favorable and
n=32-34 is simply too thin to see it cleanly, consistent with the design doc's own
"several hundred entries" re-run trigger not yet being met even now (142 total entries
post-cutover, 34 in the one genuinely clean sub-window), or (b) the favorable
selection/exit numbers in the larger samples are themselves partly an artifact of blending
in the `completeness_unknown`-tagged pre-stabilization period, and the true current
picture is closer to flat-to-slightly-unfavorable. **Evidence class 1 (current persisted
data) + 3 (deterministic re-computation via the real, reviewed `reconcile()` function) for
every number in the table; the choice between (a)/(b) above is explicitly evidence class
6 (labeled inference) — not settled by this pass.**

## Evidence class summary (per the investigation's own design doc §4, `docs/archive/lane-4-analytics-advisory-research/specs/2026-08-26-economic-strategy-effectiveness-investigation-design.md` — moved there 2026-09-06, planning-lanes migration — the numbering `.claude/rules/autonomous-quality-coordination-evidence.md`'s evidence-classes section inspired, same convention the sibling E1-E3 doc cites)

**Class 1 (current persisted data) + 3 (deterministic re-computation via existing,
reviewed code):** every numeric table above — the reset-log/trade-count sanity check, the
loop_watchdog stall statistics, the E4 banded EV table, the E5 reconcile table, the
signal-density comparison's raw counts.

**Class 2 (git/commit history):** the Program 1 merge timestamp, `bb5806c`'s timestamp and
its ancestry relative to the merge and to the stall cluster.

**Class 6 (inference, labeled):** the mechanism behind the post-merge density increase
(capture-loss recovery vs. genuine higher market activity — unresolved, same shape as
E3's original open trough question); which of the two readings of E5's selection/exit
tension is correct.

## What this means for Program 2

Not a redesign of `docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-remediation.md`'s
task list — flagged in prose per this document's own scope:

- **P2-1** (banded, cost-aware, sample-size-gated EV diagnostic): re-verification gives it
  *more* confidence, not less — the finding reproduces and strengthens under a genuinely
  cleaner sample. No design-assumption change indicated.
- **P2-2** (capture-health tagging helper, `tag_window()`): the blocking condition in the
  plan text ("do not start until the realtime remediation has landed a real
  capture-completeness signal") is now **partially satisfied** — `loop_watchdog` is a real,
  usable stall signal, though it is a *loop-responsiveness* proxy, not the REST-vs-WS
  trade-level completeness `services/diagnostics/trade_capture_reconciliation.py` measures
  live and cannot reconstruct retroactively. A future implementation of `tag_window()`
  should be aware it has exactly one new class-1 signal to build on
  (`loop_watchdog.stall_max_ms`/`stall_count`, available only from
  2026-08-26T17:01:39Z forward) — not the richer telemetry (dropped-message counts,
  queue-saturation duration) the plan's P2-2 text speculated Program 1 might ship. This is
  a flag for whoever picks up P2-2, not a task edit.
- **E5's underlying data has grown from 90 to 142 post-cutover entries** (34 in the
  cleanest sub-window) — still short of the "several hundred entries" re-run trigger the
  status report and remediation plan both name. The selection/exit tension identified
  above is a concrete reason to keep watching this rather than treat E5 as closed once
  that trigger is finally met.
