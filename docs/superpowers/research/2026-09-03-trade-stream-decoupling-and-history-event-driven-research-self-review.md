# Self-review — trade-stream decoupling and History event-driven research

Same author, own artifact, per CLAUDE.md's "nothing advances on one pass." Checked the research
document's citations against current source directly (not re-trusted from when they were first
written a few minutes earlier), looked for internal inconsistency and unaddressed scope, and
fixed what was found rather than only reporting it — matching the precedent this repo already
set (`2026-09-03-trade-resolve-consumer-blocking-solution-comparison-self-review.md`, "fix 4
citation errors").

## Citations re-verified against current source, one by one

Re-ran every line-number/file citation in §1 against the actual file content (not against my own
earlier read of it), via direct `grep -n`/`sed -n` of: `services/whale_stream/decision_bridge.py`
(`_broadcast_signal_decision` + its 4 `create_task` call sites, the two `tick_executor.run`
`candidate_ledger` calls), `services/analytics/routes.py:127`, `services/whale_calibration/
routes.py:131,168`, `services/tick_executor.py:80`, `main.py:1507,1408`,
`.ddev/docker-compose.fastapi.yaml:70`, `services/ws_manager.py`'s header comment. All matched
exactly as cited — no correction needed on any of these.

## One real gap found and fixed: the `tick_executor` sharing finding (§1.3) was missing a material fact

While re-checking `services/analytics/routes.py:127` and `services/whale_calibration/
routes.py:131,168`, noticed both call sites are guarded by an `if`/`else` around a cache check
that the original draft's grep-only citation didn't surface. Read the surrounding code
(`analytics/routes.py:42-49`, `whale_calibration/routes.py:24-30`) directly: both routes gained a
30-second result cache the same day, commented "2026-09-03, Task 6b of `docs/superpowers/
plans/2026-09-03-tier1-backend-hygiene.md`," deliberately coordinated with the frontend's own
30s History-tab throttle interval (not an independent coincidence — the comment says so).

This changes the finding's severity, not its existence: `tick_executor.run(...)` on these routes
now fires at most once per 30s per route (on a cache miss), not on every request as the original
draft implied by citing the first audit's uncached 48.6s/13.6s/4.5s wall-time numbers without
noting they no longer reflect steady-state cost. **Fixed in place** (§1.3 of the main document
now has a "Correction from self-review" paragraph stating this precisely, with its own citations).
Did not touch §3's or §4's conclusions, which called the coupling "real and current" without
claiming a specific frequency — that framing survives the correction; only the implied severity
in §1.3's original wording needed adjusting.

**Why this matters enough to fix rather than leave for the adversarial pass**: a design-stage
reader deciding whether the `tick_executor` sharing is worth fixing on its own (independent of
the full two-process split) needs the real frequency, not an implied "every request" rate that's
3 weeks-old-audit-vintage and no longer true. Leaving it uncorrected risked the next stage
over-weighting this specific finding's urgency relative to §1.2's (`_scoring_pool`, which has no
such mitigation and remains fully live on every trade/retry, not just a cache-miss).

## Scope check — did this cover what was asked?

The task named two things: (1) trade-stream decoupling from history/diagnostics, no shared
consumers/pools/queues, and (2) History's modules moving off fixed-interval polling to
on-demand/event-driven. Re-read the document section by section against both:

- (1) is covered by §1.1-§1.4 (three distinct coupling axes individually verified: WS
  consumer/queue, `_scoring_pool`, `tick_executor`) plus §3 (whether the process split actually
  addresses each one — the one section that stops treating "the split" as a single monolithic
  fix and checks it item-by-item against the actual route list).
- (2) is covered by §2, which correctly identified (by reading `services/history/`'s actual
  contents, not assuming) that "History's modules" poll on a fixed interval is a frontend
  concern, not a backend scheduler — a finding that could have been gotten wrong by assuming the
  priority meant a backend loop and going looking for one that doesn't exist.

Nothing found unaddressed. The document does not make a build recommendation (correctly, for a
research-stage document) — it stops at "here's what's true now, here's what item 25/26 do and
don't cover, here's what the design stage needs to weigh," per CLAUDE.md's own scoping for this
stage.

## Internal consistency check

Cross-read §1's five subsections against §3's "does the split fix this" table and §4's "gets
right/wrong" summary for contradiction. None found: §1.1 says PR #555 mitigated the blocking
harm without removing the physical sharing; §3 doesn't re-claim §1.1 as something the process
split would additionally need to fix (correctly silent on it, since §1.1 is about WS internals
the split never touches); §4's "urgency argument... weaker than either predecessor document
assumed" is consistent with, not contradicted by, §1.1's own more careful "downgraded from
blocking to contention, not eliminated" wording — §4 doesn't overclaim "no longer matters," only
"less urgent than assumed," which §1.1 supports.

## What this self-review did not re-verify

Did not re-run the historical wall-time/queue-depth numbers cited from the two predecessor
documents (they're attributed and not re-derived, consistent with §0's own statement that this
document builds on them rather than re-deriving their claims) — an adversarial pass re-deriving
those from scratch would be redundant with what already happened in those documents' own review
cycles, not a gap in this one. Did not independently re-run PR #555's own falsification tests
(also out of scope — this document treats PR #555's merge and its own stated review status as
given, the same way §0 treats the two predecessor research docs' conclusions as given, and cites
the PR body directly rather than the code's behavior under a scenario this document didn't
itself construct).

## Verdict

One material correction made and verified in place. No other errors found. Ready for adversarial
review.
