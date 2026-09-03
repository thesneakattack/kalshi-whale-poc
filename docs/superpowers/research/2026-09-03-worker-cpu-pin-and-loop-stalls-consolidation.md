# Consolidation: `2026-09-03-worker-cpu-pin-and-loop-stalls.md`

Reconciles the research doc, its self-review, and its independent
adversarial review into a single GO/no-go verdict and fix-list recheck, per
CLAUDE.md's "nothing advances on one pass" HARD RULE. Authored by the same
session that wrote the original research doc — per that HARD RULE's own
text, the independence requirement attaches to the adversarial review, not
to consolidation; consolidation is the author's job, reconciling the
artifact and both reviews into a decision.

## Review history

1. **Research doc** (`2026-09-03-worker-cpu-pin-and-loop-stalls.md`) —
   investigates the fastapi worker's ~103-139% CPU pin and
   `loop_watchdog`'s 37-115/min stalls against a 6s budget. Central finding:
   `_process_stream_ticker` calls `check_exits`/`check_pending_fills`/
   `position_netting.review` synchronously on every processed WS ticker
   message, not once per 6s poll tick; `check_exits`' own `tick_cache`
   memoization (built 2026-08-27 for exactly this) is never passed at this
   call site, confirmed by that commit's own scope note. Also identifies
   `loop_watchdog`'s stall sampler as structurally unable to see a
   blocker's stack (same-loop `asyncio.Task`), confirmed against 400 live
   `fault_log` rows.
2. **Self-review** (`-self-review.md`) — found and fixed 3 real overclaims
   before this doc was ever reviewed externally: a stale read-count that
   included a config-gated-off function, an imprecise thread-percentage
   range, and two estimated timestamps presented without a hedge. Flagged
   two items explicitly for the adversarial pass: independently re-pulling
   PR #414/#409/#420/#424 rather than trusting this doc's characterization,
   and an `EXPLAIN QUERY PLAN` check on `idx_snapshots_ticker_ts` against
   `recent_price`'s exact query shape.
3. **Adversarial review** (`-adversarial-review.md`) — genuinely
   independent: fresh Agent call, no memory of the authoring session,
   re-derived every load-bearing claim from primary sources (current
   source at branch HEAD, `git show`, live `GET` calls, `docker exec`
   `/proc` reads, `gh pr/issue view`) rather than the doc's own tables.
   **Verdict: GO-AFTER-FIXES.** Confirmed the central mechanism claim
   (F1-F8) at the strongest evidence tier — direct source-code
   state-transition proof, byte-for-byte snippet matches, verbatim `git
   show` of the cited commit — including an independent, later, 400-row
   re-pull of the `loop_watchdog` fault-log claim that reproduced the
   identical shape. Found 2 must-fix inaccuracies (an inflated rate figure;
   a mischaracterized/overstated `position_netting` code attribution) and 4
   should-fix items (a connection-cost nuance for `analyst_lean`; the
   trade-channel call site quoted but not discussed; a new live
   observation that the condition had worsened substantially since the
   doc's own window closed; the self-review's `EXPLAIN QUERY PLAN` gap,
   still unrun after two passes).

## Adjudication

No disagreement between the two reviews to adjudicate — the adversarial
review's findings are corrections and completeness gaps layered on top of
claims the self-review had already checked and held; nothing in either
review contradicts the other. The self-review's own two flagged open items
were both explicitly addressed by the adversarial pass (the PR
re-derivation was done, including an independent source-level check of
#414 specifically; the `EXPLAIN QUERY PLAN` gap was checked and confirmed
still open, not silently dropped).

## Fix list — merged, and applied to the research doc in this pass

| # | Item | Source | Status |
|---|---|---|---|
| 1 | Correct "10+/sec sustained" to the figure the doc's own data supports (~9.35/sec between its own two pulls, ~6.5/sec over a longer independent re-measurement) | Adversarial F9, must-fix | **Applied** — §7 rule-out table corrected in place |
| 2 | Correct `position_netting.py:251`/`describe_groups` attribution: the `vols = [...]` line lives in `_materiality_bar`, a helper `describe_groups` calls only for groups not already locked-profit/locked-loss, not unconditional code inside `describe_groups` itself | Adversarial F10, must-fix | **Applied** — §4.3 corrected in place, with an inline note explaining the correction |
| 3 | Note `analyst_lean`'s cached scoring connection vs. `recent_price`/`volatility`'s fresh per-call connection — the "3 reads" framing shouldn't imply equal connection cost | Adversarial F11, should-fix | **Applied** — §4.2 refined in place |
| 4 | Explicitly address `_process_stream_trade`'s own uncached `check_exits` call and why the doc's mechanism analysis is scoped to the ticker channel | Adversarial F12, should-fix | **Applied** — new note added at the end of §4.1 |
| 5 | Add a dated addendum: a live re-check found the condition substantially worse (`last_tick_duration_sec: 1251.64`) than this doc's own worst case, on the confirmed-same worker process | Adversarial F13, should-fix | **Applied** — new §10, folded together with the coordinator's independently-reported 1,397.7s stale-position match and the "reports the last *completed* tick" reading trap that caused the coordinator's own earlier "tick loop is fine" misread |
| 6 | Carry forward the `EXPLAIN QUERY PLAN` gap — still open after two independent passes | Self-review + Adversarial F14, should-fix | **Carried forward, not closed** — remains open for whoever does the §8 direct-benchmark work next; not required to unblock this stage, since it affects only the precision of "why ~50-100ms," not the central claim, which both reviews independently confirmed holds regardless |
| 7 | File the `loop_watchdog` same-loop diagnostic gap as its own issue, not buried in a merged research doc | Direct instruction (coordinator, post-review) | **Applied** — filed as issue #527, cross-referenced from the new §10 |
| 8 | Cross-reference the mechanism's confirmation by PR #526's working fix and measured numbers (608.0ms/s → 25.0ms/s, ~24x) | Direct instruction (coordinator, post-review) | **Applied** — new §10, plus §8's `tick_cache` item struck through and corrected (the fix that shipped was a min-interval throttle, not `tick_cache` alone — `tick_cache` turned out to be a no-op given the schema, a fact this research alone didn't have) |

Every item was re-checked against the revised document after applying it,
per the HARD RULE's "never accepted on its own completion claim" —
confirmed each edit landed in the section the fix list names, not merely
claimed.

## Verdict: GO

The central mechanism claim — `_process_stream_ticker` synchronously
running an uncached, unthrottled `check_exits`/`check_pending_fills`/
`position_netting.review` block on every WS ticker message, with the
`tick_cache` gap being a deliberate, named, documented scope boundary — was
independently confirmed by the adversarial review at the strongest
available evidence tier and was **not weakened by anything found in this
pass**. It has since been additionally confirmed by a working, measured
fix (PR #526, ~24x reduction in aggregate blocking cost, in line with the
mechanism's own predicted magnitude) and by further live data
(the 1,251.64s tick / 1,397.7s stale-position quantitative match) that
neither the original doc nor its adversarial review had access to. All 2
must-fix and 4 should-fix items from the adversarial review are applied
or explicitly carried forward with a stated reason. This research stage is
complete: **GO** to proceed past it.

## What's still genuinely open (not blocking this stage)

- `EXPLAIN QUERY PLAN` on `idx_snapshots_ticker_ts` against `recent_price`'s
  query shape — affects precision of a secondary cost estimate, not the
  central claim.
- A direct benchmark of the WS-path `check_exits` call at live
  position/config values (§8) — largely superseded in practical terms by
  PR #526's own synthetic benchmark, though that benchmark measured the
  *fix's* effect, not a from-scratch characterization of the pre-fix cost
  the way §8 originally scoped it; still worth doing if more precision is
  ever wanted.
- A WAL-checkpoint/`strace`-level trace of §5.2's write/cancel-byte ratio —
  named in §8, not attempted by any review pass, genuinely unrelated to
  the ticker-message mechanism.
- `loop_watchdog`'s same-loop sampler defect (issue #527) — filed,
  unimplemented.
- Live post-merge Gate 2 verification of PR #526 itself (queue depth,
  ingest wait times, oldest-stale-position age falling in production, not
  just in the synthetic benchmark) — explicitly owed by that PR, not by
  this research stage, but the natural next confirmation of everything
  this document and its addendum describe.
