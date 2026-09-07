# Consolidation: `2026-09-03-trade-resolve-consumer-blocking-solution-comparison.md`

Reconciles the solution-comparison document, its self-review, and its independent
adversarial review into a single GO/no-go verdict and merged fix list, per
CLAUDE.md's "nothing advances on one pass" HARD RULE. Authored by the same session
that wrote the original document — per that HARD RULE's own text, the independence
requirement attaches to the adversarial review, not to consolidation; consolidation
is the author's job, reconciling the artifact and both reviews into a decision.

## Review history

1. **Comparison doc** (`2026-09-03-trade-resolve-consumer-blocking-solution-
   comparison.md`) — compares three fix directions for issue #542's confirmed
   consumer-blocking mechanism (a REST resolve call awaited inline in the sole
   trade-queue consumer): coalescing/batching resolve calls (Option A), bounded-
   concurrency resolve (Option B), and a reserved rate-limiter budget (Option C).
   Central finding: only Option B addresses the root mechanism; Option A only
   delivers its benefit once layered on B's decoupling (not a standalone
   alternative); Option C is a valid, low-risk complement that leaves the measured
   44.6-second network-latency tail untouched. Also surfaces a new fact neither
   #526 nor Gate 2 caught: `two_consumer_mode` is live, so trade and ticker
   messages share one consumer today — a resolve stall on a trade message also
   delays ticker/price-freshness processing.
2. **Self-review** (`-self-review.md`) — found and fixed 3 real citation errors
   before external review (misattributing analysis from issue #541's comment
   thread to #542's, 3 instances — later found to have been miscounted as 4 in its
   own bullet, see F7 below), 1 wrong section cross-reference, 1 benchmark figure
   cross-referenced to the wrong evidence source with 2 missing appendix citations,
   and 1 claim strengthened after re-checking raw JSON showed a fuller match than
   first written. Explicitly flagged 3 items it could not fully verify itself for
   the adversarial pass — including, presciently, the `_seen_trade_ids`/
   `_market_cache` "safe by inspection" claim that turned out to matter most (F6).
3. **Adversarial review** (`-adversarial-review.md`) — genuinely independent: fresh
   Agent call in its own isolated worktree, no memory of the authoring session,
   re-derived every load-bearing claim from primary sources (current source at
   branch HEAD, live `GET` calls made fresh on a **different process generation**
   than the one the original doc measured, re-run `git log`/`merge-base`, and the
   actual GitHub issue threads via `gh issue view --json`) rather than the
   document's own tables. **Verdict: GO-AFTER-FIXES.** Confirmed the central
   mechanism (F1), the live 1:1 no-batching fact reproduced independently on a
   fresh process generation (F2 — matching the original to 4 significant figures
   on the historical max figures), the shared trade/ticker consumer finding (F3),
   the mutually-exclusive tick-loop-vs-streaming call shape (F4), and the git
   commit-ordering proof (F5) — all at the strongest evidence tier, source
   state-transition proof corroborated by fresh live telemetry. Found 1 must-fix
   (F6, described below) and 4 should-fix items (F7-F10), plus 1 nice-to-have
   (F11, an arithmetic slip).

## The one finding worth stating plainly

F6 is not a citation nitpick — it is a real, independently-confirmed, currently-
live correctness gap the original comparison document's self-review explicitly
flagged as unaudited and the adversarial review then audited and confirmed:
`self._seen_trade_ids`/`self._seen_order` (the trade-tape dedupe ring in
`kalshi_trade_tape.py`) are already exposed to concurrent mutation *today*, via two
independent, currently-supervised, currently-running tasks (the WS stream consumer
and `_candidate_retry_loop`) sharing the same 4-worker `_scoring_pool` — a fact one
module's own docstring states as deliberate and a different module's own docstring
directly denies. This predates and is independent of whether Option B is ever
built. Filed as its own issue (#546) rather than left to live only inside this
research document, consistent with this repo's existing practice for exactly this
shape of discovery (see the `worker-cpu-pin-and-loop-stalls` research doc's own
`#527` precedent).

## Adjudication

No disagreement between the two reviews needed adjudicating — the self-review's own
"not fixed, flagged for adversarial review" section named the two areas that turned
out to matter (F6's shared-state audit, F10's ordering-guard citation) and the
adversarial review confirmed and deepened both rather than contradicting either.
The one place the adversarial review found the self-review's *own* work (not the
underlying document) to be wrong — F7, the "four instances" overclaim — is a
genuine, if minor, defect in the self-review's audit trail, corrected in place with
an explicit correction note rather than silently rewritten, so the record of what
actually happened stays intact.

## Fix-list recheck (all six items applied and reverified against the revision)

| # | Severity | Finding | Fix applied | Reverified |
|---|---|---|---|---|
| 1 | Must-fix | F6 — pre-existing `_seen_trade_ids`/`_seen_order` concurrency hazard, independent of Option B | Expanded §3's correctness section; filed issue #546; updated the comparison table and §8's recommendation to reference it separately from the Option-B-specific `_resolve_failed_tickers` fix | ✅ `grep` confirms all 3 locations |
| 2 | Should-fix | F7 — self-review's own "four instances" overclaim | Corrected to "three" with an explicit correction note (not silently rewritten) | ✅ |
| 3 | Should-fix | F8 — 2 residual `#541/#542` dual-citations for #541-only analysis | Both changed to `#541` alone | ✅ 0 remaining `#541/#542` joint citations in the doc |
| 4 | Should-fix | F9 — `CALLER_CLASSES` undercounted (6/5-other vs. actual 9/8-other) | Corrected in both §5 (mechanism) and §3 (benchmark) | ✅ |
| 5 | Should-fix | F10 — `opened_since` guard mis-cited to `strategy_engine.py` | Re-cited to `services/exits/exit_engine.py:232-233`; characterization narrowed to match its actual documented scope | ✅ |
| 6 | Nice-to-have | F11 — "5-15x" arithmetic slip | Corrected to "~5-18x" with the arithmetic shown (verified independently: 1421.35/290.02≈4.9, 1421.35/78.79≈18.0) | ✅ |

Per CLAUDE.md's no-go recheck discipline, this table was built by re-grepping the
actual revised file for each fix, not by trusting my own memory of having made the
edits — the tool output backing each ✅ is in this session's own record.

## Verdict: GO

The document's central, load-bearing claims survived independent re-derivation on
a different process generation without weakening — this is unusually strong
corroboration for a comparison document (most of the adversarial review's findings
are precision/citation issues, not challenges to the mechanism or the
recommendation). The one must-fix finding (F6) was real and is now both reflected
in the document and filed as its own actionable issue, not buried. All should-fix
and nice-to-have items are applied and reverified.

**This document's recommendation stands**: pursue Option B (bounded-concurrency
resolve) as the shape for the eventual design/spec stage, with Option A's batching
folded into B's hand-off path rather than built as a separate mechanism, optionally
paired with Option C for the median-latency win. Two correctness preconditions ride
with that recommendation, both now explicit rather than implicit: the
`_resolve_failed_tickers` per-call-reset fix (Option B's own precondition) and
issue #546's `_seen_trade_ids`/`_seen_order` fix (pre-existing, real regardless of
Option B, needs its own scoped fix).

**This consolidation authorizes the next planning stage to begin** (a design/spec
document, per CLAUDE.md's pipeline) — it does not authorize implementation. The
design/spec stage gets its own full self-review/adversarial-review/consolidation
cycle before any code is written, same as this one.

## What's next

1. Report this GO to the coordinator (autotrade-ce) — theirs is the call on whether
   this recommendation stands, per this document's own §8.
2. Open a PR for this branch (`docs/trade-resolve-blocking-solution-comparison`).
   Per `.claude/rules/branching-and-ci.md`, apply `phase:research` — this is a
   research-stage artifact (a solution-family comparison), not yet a design/spec or
   implementation plan.
3. Per CLAUDE.md, the PR itself gets one more full review cycle (self-review,
   adversarial review, consolidation) once opened, before merge — this
   consolidation covers the document-stage cycle, not the PR-stage one.
4. Issue #546 stands on its own regardless of this document's fate — it is a real,
   live gap independent of whether Option B is ever pursued.
