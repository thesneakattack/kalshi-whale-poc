# Self-review: 2026-09-04-issue-410-tick-executor-measurement.md

Same-author/context pass per CLAUDE.md's "nothing advances on one pass" HARD RULE — checked for
internal consistency and unaddressed scope. This is the cheapest layer, run before deciding
whether the coordinating session needs to spend independent (adversarial) effort on this note.

## Issue found and fixed during this pass

**§3.4's original empirical test measured the wrong gap.** The first version slept 31s *after*
call 1's ~22s response had already fully completed, so the actual elapsed time between call 1's
request-receipt instant (when `cached_at` is really stamped) and call 2's request-receipt instant
was ~55.7s (22.39s query duration + 33.28s sleep-and-overhead), not ~31-33s. A ~55s gap missing
the cache is unsurprising and doesn't distinguish "the cache works as designed but the real poll
happens to land just outside it" from "the cache trivially can't survive a gap nearly double its
own TTL." Re-ran the test correctly: fired call 1 in the background (non-blocking), timestamped
the instant it was issued, then fired call 2 targeted at exactly that instant + 31s (landed at
+33.2s after accounting for Python startup overhead) — squarely inside the `[30, 36)s` window
§3.4's own derivation says the real frontend poll produces. Result: still a miss (16.89s, not a
sub-second hit). This is a materially stronger and more honest piece of evidence for the
document's central claim than the original test was, and the doc has been corrected in place to
describe the fixed methodology rather than silently keeping the weaker original numbers.

## Consistency check

- The verdict (top of doc), §4's severity framing, and §5's recommendation agree with each other:
  none claims run_offline()-scale severity (5h10m), all three describe a bounded-but-recurring
  latency tax, and the recommendation doesn't overreach into "implement fix X" — it stays at
  "which family, and why," consistent with the task's own instruction not to implement.
- The three solution families in §5 are genuinely competing (not a strawman set) — each has a
  named mechanism, a named precedent already in this codebase, and a named limitation. Option 1
  (dedicated pool) isn't dismissed, just ranked behind option 2 with a stated reason (doesn't
  reduce absolute cost, doesn't address unbounded table growth) — consistent with the user's
  standing "no strawman options" instruction (CLAUDE.local.md) since option 1 is #410's own
  originally-named fix and is presented as viable, just not the top recommendation.
- §2's frontend-poll-cadence correction (30s, not the PR #409 review's stale "~5s") is load-bearing
  for §3.4's entire argument — if that citation were wrong, the boundary-alignment finding would
  not hold. Verified directly against `frontend/src/js/main.js:79` and its surrounding comment
  (which itself states why and when it changed), not taken on the research doc's word.

## Unaddressed scope, named honestly rather than silently dropped

- **No direct proof of two `tick_executor` workers simultaneously occupied by diagnostics** (§4's
  central mechanism claim, and the doc's own §6 already names this as the largest evidence gap).
  Not closed in this pass — closing it would need a live thread-state probe during a real
  concurrent cache-miss window, which is a materially bigger and more invasive live-app
  measurement than this task's "a handful of read-only curl/docker exec calls, minimal load"
  budget was meant to cover. Left as the single most important thing an adversarial pass or a
  follow-up measurement should verify before a fix is scoped on top of this claim.
- **`tick.duration_sec`'s observed 1251.64s max**, seen in `/api/observability/summary` while
  gathering context, is not investigated beyond being named in §7 (now folded into this note's own
  §6/§4 framing) as likely a different, larger, already-elsewhere-owned investigation. This is a
  deliberate scope boundary, not an oversight — chasing it would violate the "decide, don't
  over-investigate" standing guidance when the answer doesn't change this note's own verdict.
- **Raw `raw_trades` row count** (the PR #409 incident's own table) was not independently
  re-measured in this session — the doc cites PR #409's/#410's own already-documented 38M-row,
  5h10m numbers as the reference point rather than re-deriving them, which is appropriate (they're
  a fixed historical incident, not a live quantity this task needed to re-verify) but is worth
  stating explicitly: that one number in the doc is inherited, not re-derived, unlike everything
  else.
- **No design work for the aiosqlite-rewrite option** was attempted, per the task's explicit
  instruction to recommend rather than implement. The doc is explicit that this comparison
  (mechanism/benchmark/correctness/failure-behavior/complexity) still needs its own pass before
  implementation — it does not present the ranking in §5 as sufficient to skip that step.

## Verdict on this artifact

Internally consistent, methodology now sound after the §3.4 correction, scope boundaries stated
rather than silently assumed. The one open evidence gap (simultaneous worker occupancy) is
already flagged in the main document's own §6/self-review-informed framing, so a reader isn't
misled into thinking the recommendation rests on stronger direct evidence than it does. Ready to
report back to the coordinating session; no further self-review pass needed. Whether an
adversarial (fresh, memory-less) review runs before this recommendation is acted on is the
coordinating session's call, per this task's explicit authorization to skip dispatching one here
given time pressure.
