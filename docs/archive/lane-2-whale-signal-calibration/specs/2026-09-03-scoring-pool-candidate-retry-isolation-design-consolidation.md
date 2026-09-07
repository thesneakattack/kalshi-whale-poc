# Consolidation — `_scoring_pool`/candidate-retry isolation design

Reconciles the design document, its self-review, and an independent adversarial review (fresh
Agent-tool call, no memory of authoring either prior artifact) into one GO/no-go, per CLAUDE.md's
"nothing advances on one pass."

## What each layer found

**Self-review** (same author): re-verified every citation, found only a ±5-line range
imprecision in one `main.py` citation (disclosed, not a substantive error). No fix needed beyond
what was already disclosed.

**Adversarial review** (independent, no memory of authoring): specifically tried to break the
document's central, load-bearing claim (that PR #555 changed the WS path's real concurrency need
from ~1 to up to 4, making the shared-pool sizing stale) via the two most plausible failure
modes — the #546 lock serializing more of the work than claimed, and `_candidate_retry_loop`'s
own structure allowing concurrent `run_pending()` re-entry. **Neither broke; the central premise
holds.** Also independently verified the `_diagnostics_pool.py` precedent's git history, the
call-site completeness for `_scoring_pool.run(...)`, and both rejected options' reasoning.
Verdict: **NO-GO as written**, one must-fix, two should-fix, otherwise sound.

## Adjudication — the must-fix, verified independently before accepting

**Finding**: the design's correctness-precondition claim ("splitting the pool does not reopen
#546 — verified, not assumed") actually verified only that the #546 lock is instance-scoped
(true), while silently assuming the WS-trade path and candidate-retry path always reference the
*same* provider instance — an assumption the reviewer found is false in a real, live, reachable
path: `main.py`'s account reconnect/disconnect handlers re-instantiate the provider by rebinding
only `main.py`'s own module-level name, never `services/app_state.py`'s original attribute or
`services/whale_stream/whale_stream_handlers.py`'s separately-imported copy.

**Re-derived myself, not accepted on the reviewer's word**: read `services/app_state.py:109`,
`services/whale_stream/whale_stream_handlers.py:28`, `main.py:133-137`, `services/whalewatchers/
__init__.py:27-30` (`get_active_provider()`, confirmed no caching — a fresh instance every call),
and `main.py:1934-1953` (the two account-connect/disconnect handlers) directly. **Confirmed
exactly as the reviewer described**: after a reconnect/disconnect of the active provider, the
WS-trade path and the candidate-retry path genuinely end up on two different provider instances,
each with its own separate `#546` dedupe state. Real bug, orthogonal to pool topology, present
today regardless of this design's outcome.

**Disposition**: fixed in place — the design document's correctness-precondition wording now
covers only what was actually verified (lock scope), with the instance-divergence gap disclosed
explicitly rather than folded into an overclaimed "verified." Filed as its own issue (**#565**),
not silently absorbed into this design's scope, per this repo's standing practice that a finding
living only inside a document goes unactioned. **Does not change this design's recommendation**:
the bug doesn't favor growing the shared pool or using the default executor over the dedicated-
pool option — it's a separate defect that exists identically under all three options compared
here.

## Should-fix items — verified and applied

- Third `fetch_signals()` caller (`main.py:1221`, the tick-loop's non-streaming fallback) added
  to the call-site census. Verified mutually exclusive with candidate-retry (same
  `_streaming_trade_tape_enabled()` gate `_candidate_retry_loop` itself checks at `main.py:796`)
  — doesn't change the 1-worker sizing rationale, just closes a genuine completeness gap.
- Minor citation-range imprecision (`services/quality/routes.py:130-142`) — confirmed trivial,
  same category as the self-review's own disclosed imprecision; not worth a separate fix given
  it doesn't misstate what the citation is illustrating.

## Fix-list status

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Correctness precondition overclaimed instance-identity, which is false on a real reconnect path | Must-fix | ✅ Fixed; new bug filed as issue #565, orthogonal to the recommendation |
| 2 | Missing third `fetch_signals()` caller in the call-site census | Should-fix | ✅ Fixed, confirmed mutually exclusive with candidate-retry |
| 3 | Minor citation-range imprecision | Nice-to-have | Confirmed trivial, no fix needed |

## GO

The design's central architectural claim — PR #555 raised the WS path's real concurrency need
against `_scoring_pool` from ~1 to up to 4, candidate-retry is genuinely capped at 1 concurrent
submission, and a dedicated 1-worker pool is the correctly-sized, lowest-risk option — survived
an adversarial pass that specifically tried to break it via the two most plausible mechanisms and
could not. The one real defect found (an overclaimed correctness precondition) traced back to a
genuine, independently-verified, previously-unknown bug — now filed, not lost, and confirmed not
to change which option this document recommends.

**Recommendation stands: Option 1** (dedicated 1-worker `ThreadPoolExecutor` for
`score_recovered_trade`, mirroring the `_scoring_pool.py`/`_diagnostics_pool.py` precedent
already proven twice in this codebase). Options 2 (grow the shared pool) and 3 (default executor)
remain rejected on the reasoning already given, re-confirmed sound by the adversarial pass.

This document is ready to inform an implementation-plan stage. It does not authorize starting
one — per CLAUDE.md, that is a separate stage decision. Issue #565 (the provider-instance
divergence bug) is a separate, real finding that should not wait on this design's own
implementation to be triaged — it's a live correctness gap independent of pool topology.
