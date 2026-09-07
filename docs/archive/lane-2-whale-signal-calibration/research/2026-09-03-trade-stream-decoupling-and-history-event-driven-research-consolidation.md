# Consolidation — trade-stream decoupling and History event-driven research

Reconciles the research document, its self-review, and an independent adversarial review (fresh
Agent-tool call, no memory of authoring either prior artifact) into one GO/no-go, per CLAUDE.md's
"nothing advances on one pass."

## What each layer found

**Self-review** (same author): re-verified every §1 citation directly, no errors found there.
Found and fixed one real gap: §1.3's original framing didn't account for the same-day 30s-cache
addition to the two diagnostic routes, which changes the sharing's frequency (bounded to
cache-miss windows) without changing its existence.

**Adversarial review** (independent, no memory of writing the document): confirmed the document's
core three-axis thesis is sound and its §3 route-by-route cross-check against the architecture
audit's Process B list is accurate — most citations checked out exactly, including several
specifically targeted for verification. Found three must-fix errors and two nice-to-have
refinements, verdict **NO-GO as originally written, GO once fixed**.

## Adjudication — each finding, verified independently before accepting

Per this repo's "receiving code review" discipline, no finding was applied without re-deriving it
myself first (the fixes below happened only after each of the following was independently
confirmed against primary source, not accepted on the adversarial reviewer's word alone):

1. **Semaphore re-blocking (§1.1, must-fix).** Re-read `_dispatch_trade_concurrent`'s own
   docstring directly: it explicitly states the semaphore `acquire()` before `create_task` is
   "deliberate: when all 4 slots are already in flight, this await is what makes a 5th trade
   item queue BEHIND the bound." **Confirmed true from the code's own stated design, not just
   the reviewer's inference.** Accepted and fixed — §1.1 now states the blocking mechanism is
   raised (1 → 4-plus-in-flight), not eliminated, and §4 no longer says PR #555 "fixed" the
   harm.
2. **Missing PR #409/issue #410 prior art (§1.3, must-fix — the largest finding).** Re-read
   `services/quality/routes.py:130-142`'s header comment directly and ran `gh issue view 410`
   myself: **confirmed** — issue #410 is real, `OPEN`, and precisely matches the reviewer's
   characterization (PR #409 already fixed two confirmed violations of "nothing non-critical
   shares tick_executor with trading-critical work," found these exact two routes as
   structurally identical but deliberately deferred them pending a query-cost measurement per
   CLAUDE.md's own HARD RULE). Accepted and fixed — §1.3 now correctly frames this as an
   already-tracked, already-reasoned-about gap rather than a fresh discovery, and §4 reflects
   that the design stage's job here is the measurement, not re-litigating whether the coupling
   matters.
3. **`ws_manager.broadcast()` call-site miscount (§1.6, must-fix).** Ran
   `grep -rn "ws_manager\.broadcast"` across `services/` myself: **confirmed** two call sites
   (`decision_bridge.py:20`, `whale_stream_handlers.py:674`), not one. Accepted and fixed.
4. **Merge-base imprecision (nice-to-have).** Ran `git merge-base HEAD origin/main` myself:
   **confirmed** `3d8c401`, not `46a0baa`. Accepted and fixed, with the gap's contents (PRs
   #552/#556/#555/#553) individually checked for anything else load-bearing — nothing beyond
   what's already folded into the §1.1 fix.
5. **PR #558 omission (nice-to-have).** Ran `gh pr view 558` myself: **confirmed** it merged 22
   seconds before this document's own first commit, touches the same file as §1.1's subject, and
   self-scopes as orthogonal in its own commit message. Accepted — added as an Appendix footnote
   and its one load-bearing detail (the correlated-burst observation) cited directly in §1.1's
   correction rather than the document silently missing a same-night, same-file, same-subsystem
   change.

**No finding was rejected.** All five were independently re-derived and confirmed true before
any edit was made — unlike the Task 8 code-PR adversarial review earlier today (where two of
that reviewer's findings were investigated and rejected with contrary evidence), this
adversarial pass's findings all held up under independent verification. That asymmetry is itself
worth naming: a review cycle that always agrees with its adversarial pass is as suspicious as one
that always defers to it, so the fact that this one's findings all survived scrutiny is reported
here as a checked outcome, not an assumed one.

## Fix-list status

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Semaphore can still hard-block the consumer at ≥5 in-flight trades | Must-fix | ✅ Fixed, §1.1 + §4 |
| 2 | tick_executor/diagnostic-route sharing already tracked (issue #410) | Must-fix | ✅ Fixed, §1.3 + §4 |
| 3 | `ws_manager.broadcast()` has 2 call sites, misattributed to 1 | Must-fix | ✅ Fixed, §1.6 |
| 4 | Cited baseline commit isn't the true merge-base | Nice-to-have | ✅ Fixed, header + Appendix |
| 5 | PR #558 (same-night, same-file) not mentioned | Nice-to-have | ✅ Fixed, §1.1 + Appendix |

## GO

The document's central three-axis thesis (WS consumer/queue, `_scoring_pool`, `tick_executor`)
and its §3 negative finding (the two-process split doesn't cover `_scoring_pool`) both survived
independent adversarial scrutiny intact. All five findings against the document's supporting
detail were individually re-verified and fixed. This document is ready to inform a design/spec
stage — with its most important corrected framing now being: **the two-process split (item 25)
remains valuable but its urgency and scope are narrower than either predecessor document
assumed** (the blocking harm it was partly justified by is now bounded, not eliminated, by PR
#555; the `tick_executor` axis it does cover is an already-tracked, already-deferred
measurement question, not a new one; and it does not cover the `_scoring_pool` axis at all,
which needs its own separate, smaller design pass).

This document does not authorize starting that design/spec stage — per CLAUDE.md, that is a
separate stage decision, made by whoever picks this up next, not by this consolidation.
