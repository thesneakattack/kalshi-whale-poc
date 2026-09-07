# Economic Strategy Effectiveness — Population Staleness, Replay Gaps, Capture-Health Tagging (E1-E3)

Companion: `docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-effectiveness-investigation.md`
(tasks E1, E2, E3). Evidence classes per that plan's design doc §4.

All queries below ran read-only against a scratchpad copy of `signal_log.db` and
`paper_broker.db` (both small enough to `cp`), and a `mode=ro` URI connection to the live
`reset_log.db`/`candidate_log.db` (not copied — `candidate_log.db` is ~1 GB). No `data/*.db`
file was written to. Data pulled 2026-08-26, session timestamp range approx.
`1787735000`-`1787740000` epoch.

## E1 — The 88.8%/394/58.3%/12 figure is 9 days stale and describes a gate configuration
that no longer exists

`ROADMAP.md`'s own text: *"Measured 2026-08-17 on KXBTC15M: whale signals resolved 88.8%
correct across 394 settled signals, but the 12 the gates actually traded resolved only
58.3%."* (`git log -S` confirms this line first landed in commit `1fc2391`, "Record the
adverse-selection finding...".)

As of 2026-08-26, `signal_log.db`'s `signals` table for `series='KXBTC15M'` has **6,145
total rows, 6,119 resolved, 3,843 correct (62.8%)** — no filter on `source`
(`kalshi_trade_tape` vs `kalshi_trade_tape (websocket)`) or `excluded` reproduces 394 or
88.8%. 394 is roughly 6.4% of today's population.

Daily accuracy, `series='KXBTC15M'`, full history:

| Date | Signals | Resolved | Accuracy |
|---|---:|---:|---:|
| 2026-08-12 | 385 | 385 | 87.5% |
| 2026-08-13 | 319 | 319 | 92.5% |
| 2026-08-14 | 0 | 0 | — (no rows) |
| 2026-08-15 | 163 | 163 | 96.9% |
| 2026-08-16 | 331 | 331 | 91.5% |
| 2026-08-17 | 244 | 244 | 79.5% |
| 2026-08-18 | 80 | 80 | 83.8% |
| 2026-08-19 | 37 | 37 | 83.8% |
| 2026-08-20 | 31 | 31 | 80.6% |
| 2026-08-21 | 40 | 40 | 72.5% |
| 2026-08-22 | 143 | 143 | 62.9% |
| **2026-08-23** | **1,044** | 1,044 | **53.2%** |
| 2026-08-24 | 1,331 | 1,331 | 54.8% |
| 2026-08-25 | 1,418 | 1,418 | 54.2% |
| 2026-08-26 (partial) | 579 | 553 | 47.2% |

The regime break is exactly at 2026-08-23: daily accuracy sits at 62.9-96.9% every day
through 08-22, then drops to 47.2-54.8% every day from 08-23 onward, coinciding with commit
`080a37b` ("Switch the whale-detection gate from dollar notional to contract count",
2026-08-23 00:45:02 -0500 = 1787463902 epoch). This is not a coincidence requiring further
proof: ROADMAP.md's own 2026-08-17 measurement already established the mechanism — a
dollar-notional floor is geometrically biased toward near-certainty prints (44/58 clears at
unit cost >=0.95 under the old gate), while the replacement contract-count floor accepts a
much broader, closer-to-coinflip population. **88.8% accuracy was a property of the old
dollar-gated population, not a stable property of "whale signals" in general** — the
88.8%-vs-58.3% gap, whatever caused it, was measured entirely inside a regime that stopped
existing 6 days later.

**Self-review (execution program §9):** this is a verified fact (regime-break timing
matches the cutover commit to the day), not inference, for the *existence and timing* of
the break. The *mechanism* (dollar-gate near-certainty bias) is inference reused from an
already-published measurement (ROADMAP.md, 2026-08-17), not re-derived here — labeled as
such, not re-verified independently in this pass.

## E2 — Replay gap: the original 12-trade sample is unrecoverable, not merely stale

`reset_log.db`'s `reset_events` table (schema: `executed_at, domain, scope, range_start,
range_end, rows_before, rows_deleted, note`) records every `POST /api/reset`-style
domain purge. Filtering `domain IN ('paper', 'signal_log', 'candidate_log')`:

| When (UTC) | Domain | Scope | Rows deleted |
|---|---|---|---:|
| 2026-08-17T01:11:45 | signal_log | between (28 min range) | 19,995 |
| 2026-08-17T20:31:52 | paper | all | 133 |
| 2026-08-18T00:08:12 | paper | all | 26 |
| 2026-08-24T22:07:17 | paper | all | 230 |
| 2026-08-24T22:22:44 | paper | all | 0 |

`paper_broker.db`'s `trades` table (copied to scratchpad) currently has **460 rows,
`MIN(timestamp) = 2026-08-24T22:24:09`** — consistent with the last reset event 15 seconds
earlier. Every trade currently in `paper_broker.db`, including the 90 KXBTC15M entries used
in E5, postdates that reset by construction.

**Conclusion: the original 12 KXBTC15M trades from the 2026-08-17 measurement do not exist
in `paper_broker.db` in any form** — that data was on one side of the 2026-08-17T20:31:52
or 2026-08-18T00:08:12 reset (both predate or narrowly postdate the 2026-08-17 measurement
timestamp; either way, at least one full-table reset separates today's data from it) and,
regardless of which, the 2026-08-24 reset deleted whatever survived until then. This closes
E10 (direct reconstruction) as infeasible rather than merely difficult — not a data-mining
problem to solve harder, a genuine data-loss event. `data/backups/` (a directory visible
under the live `data/` tree, presumably `services/backup/backup.py`'s snapshot output) was
not inspected for a pre-reset `paper_broker.db` snapshot in this pass — flagged in the
status report's insufficient-sample list rather than assumed empty or assumed useful.

`signal_log.db`'s only purge was a targeted 28-minute exclusion window
(2026-08-17T01:11:45, 19,995 rows — almost certainly an experiment/test run purged via
`signal_log.mark_excluded_range`'s documented mechanism, not a reset of real data), so its
6,119-row KXBTC15M history is continuous back to 2026-08-12 and is the one trustworthy
long-run population series this investigation can rely on. `candidate_log.db` shows exactly
one reset event, consistent with `rejection_events`' documented 2026-08-23 creation rather
than a mid-life data-loss event.

## E3 — Capture-health tagging: two regimes, one attributable, one open

Hourly KXBTC15M signal counts (`CAST(seen_at/3600 AS INT)*3600` bucketing) across the full
237-hour history show two distinct anomalies beyond the 08-23 gate-cutover step already
covered in E1:

1. **A missing day** (2026-08-14: zero rows at all) sitting inside an otherwise
   normally-populated stretch (08-12/08-13 and 08-15/08-16 both show 150-400+ signals/day).
2. **A ~4-day near-zero trough, 2026-08-18 (afternoon) through 2026-08-22 (afternoon)**:
   hourly counts fall from a 10-30/hour baseline to mostly 1/hour, many hours entirely
   empty (e.g. 2026-08-20T13:00-14:00, 2026-08-21T10:00, 2026-08-21T13:00-16:00 all show
   zero rows). Daily totals over this stretch: 37, 31, 40 signals (08-19/08-20/08-21) against
   a 150-400/day baseline immediately before and a 143/day partial recovery on 08-22.

Per the execution program's capture-health rule, each interval used by E4/E5 must be tagged
rather than silently blended:

- **2026-08-12 through 2026-08-13, 2026-08-15 through 2026-08-17:** no known degraded-
  capture measurement covers this period (the realtime investigation's own measurements are
  all from 2026-08-25). Tagged **`completeness_unknown`** — plausible-looking volume is not
  the same as a verified-complete capture, but there is no contrary evidence either.
- **2026-08-14 (missing entirely) and 2026-08-18–08-22 (near-zero trough):** tagged
  **`completeness_unknown`**, specifically flagged as anomalous and worth a dedicated
  follow-up (not resolved here — see the status report). Two live hypotheses, neither
  eliminated in this pass: (a) a real WS-capture degradation predating the realtime
  investigation's own measurement window, consistent in *shape* with that investigation's
  later-measured failure modes (event-loop stalls, reconnect-discards — see
  `docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-realtime-root-cause-report.md` §2); (b) genuinely
  low real BTC-market whale print volume that week, which this app's own capture would
  faithfully reflect as low counts without any capture fault at all. Distinguishing these
  needs either a REST-based capture-completeness check run against that specific historical
  window (not currently possible — Kalshi's trade-history endpoints do not retroactively
  reconstruct arbitrary past windows the way a live reconciliation does) or a correlated
  independent BTC market-activity signal — neither pursued in this pass.
- **2026-08-23 onward:** the population used by E4 and E5. **Tagged
  `completeness_unknown`, leaning `measurably_degraded` during any saturated stretch**: this
  window sits inside the same unremediated realtime data-plane the 2026-08-25 root-cause
  report measured directly (execution program §4.3: "the remediation itself is not
  implemented" as of this session). That report's own numbers (idle-queue capture 100%;
  busy-hour whale-sized capture 35%, 0% during an active drop episode) were not re-measured
  against this investigation's exact window — E4/E5's post-cutover figures should be read as
  **drawn from an unknown, non-uniform capture-completeness population**, not a clean
  ground-truth sample. This is a real limitation on E4/E5, stated there and repeated in the
  status report rather than left implicit.

**Self-review:** the 08-23 step-up is confidently attributed to the gate-definition change
(E1), not capture health — the timing precision (to the hour, matching a commit's exact
deploy time) and the already-published mechanism make an alternative capture-health
explanation for *that specific transition* implausible. The 08-18–08-22 trough and the
08-14 gap are explicitly left open rather than folded into either explanation without
evidence — this is the one area of this document where "unknown" is the honest answer, not
a placeholder for more digging that would have been cheap to do. Attempting it would have
required either a live Kalshi REST call (this session deliberately avoided spending shared
REST-limiter budget the concurrent realtime-remediation worktree is actively measuring
against) or trusting an untested historical-reconstruction path — both correctly out of
scope for a research-only pass per the design doc §3/§6.
