# Consolidation — Tier 1 Backend Hygiene Implementation Plan (2026-09-03)

Reconciling `docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md (moved there 2026-09-06, planning-lanes migration)` (commit `128caf2`),
its embedded Plan self-review, and the independent adversarial review
(`docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene-review.md (moved there 2026-09-06, planning-lanes migration)`, commit `bb0cba2`, a
fresh Agent call with no memory of the authoring session) per CLAUDE.md's "nothing advances
on one pass" HARD RULE. This plan was drafted directly from the second-pass audit's Tier 1
items (7-14) without a separate design stage — a deliberate judgment call for this initiative
(narrow, mechanical fixes, no architectural decision requiring its own design document),
distinct from the persistence-layer and strategy-edge-gate initiatives, which did get a
design stage.

## Verdict: **GO**

The adversarial review's independent verdict was GO-AFTER-FIXES with **zero must-fix code
defects** — every proposed diff the review spot-checked or fully reproduced (the
`config_store` merge fix, the `RiskManager`/`ShadowTrader` guard mirror, the `alerting.py`
task-handle wrapper, the httpx pin, the pagination census, the `event_live_data`/
`bump_generation` throttles) was confirmed correct against current source and safe with
respect to CLAUDE.md's trading/risk safety invariants. The plan's single most load-bearing
technical claim — the `config_store.update()` comment-wipe mechanism and its fix — was
independently, experimentally reproduced from scratch inside the live container, not merely
re-asserted. All 6 should-fix items and both nice-to-have items are now applied to the plan
document. One of the should-fix items (F13) surfaced a genuine, previously-unraised
correctness concern in Task 1 (a self-inflicted phantom-stall risk from an inline blocking
write) and has been fixed with a stronger mechanism than the review's own literal code
suggestion — see below. This clears the plan for execution; per the HARD RULE's own text, a
fix-list recheck is sufficient here and a full second self-review/adversarial-review cycle is
not required, since no fix changed the artifact's scope or introduced a claim neither
original pass had seen.

## Disagreements between self-review and adversarial review

None outright. Two items are worth noting as the self-review and adversarial review
reinforcing each other rather than conflicting:

- The self-review's own "Placeholder scan" claimed every code block is either a verbatim
  transcription or a complete, disclosed-exception implementation. The adversarial review's
  F14 independently spot-checked dozens of the plan's line-number/signature/call-site
  citations and found them exact — direct, independent support for that self-review claim.
- Where the adversarial review found the plan's own verification narrative overstated (F9,
  F10, F11 — a parameter presented as hypothetical when it already exists; a call-site count
  off by one; a historical cost figure cited as current), none of these affected the plan's
  actual design conclusions, which the review explicitly confirms hold up on independent
  re-derivation in each case. This is citation-precision drift, not a design defect, and the
  self-review's own "Research coverage" section already flagged (accurately) that citation
  corrections were made during this plan's own drafting — the adversarial review found two
  more instances of the same failure mode (an earlier correction pass not going far enough)
  rather than a new category of problem.

One judgment call, not a disagreement: F13's own suggested code snippet
(`asyncio.create_task(asyncio.to_thread(...))`, unretained) would reintroduce the exact
weak-reference garbage-collection hazard Task 8a elsewhere in this same plan exists to fix —
the review's own prose says the fix should be "consistent with... Task 8a's... 'retain a
reference, don't block on it' idiom," but its literal code sample doesn't retain a reference.
The fix applied below follows the review's stated *intent* (fire-and-forget, no delay to the
watchdog's own next sample) using a small locally-scoped retain-and-discard pattern matching
Task 8a's actual mechanism, rather than the review's literal one-line suggestion, since the
literal suggestion would have been a regression against this same plan's own Task 8a
reasoning.

## Merged fix list and disposition

| # | Source | Finding | Disposition |
|---|---|---|---|
| 1 | Adversarial, should-fix 1 (F9) | Task 6 presents `since_ts` as a hypothetical capability when `resolved_signals_with_factors()` already has this parameter (added 2026-09-01) | **Fixed** — both occurrences (Task 6's intro summary and its detailed section) corrected to state the parameter already exists and explain why the gate-computing callers correctly leave it unset |
| 2 | Adversarial, should-fix 2 (F10) | "1 call site" of `population_gate_summary()` should be 2 (`services/research/research.py:221` also calls it, inside an evidence-gated infrequent sweep) | **Fixed** — both occurrences corrected, with the second call site's evidence-gated nature noted as the reason it doesn't change Task 6b's design |
| 3 | Adversarial, should-fix 3 (F11) | "17-38s" cited as current cost is actually the pre-2026-08-26-fix figure; current cost is ~4.8s | **Fixed** — all load-bearing citations of this figure (Task 6's rationale, Task 6a's caching target, Task 10's live-validation comparison, the self-review) corrected to cite ~4.8s as current and 17-38s as historical context for the earlier fix |
| 4 | Adversarial, should-fix 4 (F12) | PR #424 citation dismissed as "unreliable" based on a shallow `git log` branch-name check; `gh pr view` shows it's a real, analogous precedent for a different sibling function | **Fixed** — both occurrences (Task 6's research-correction section and the self-review) replaced with the accurate `gh pr view`-derived citation |
| 5 | Adversarial, should-fix 5 (F13) | Task 1's inline `await asyncio.to_thread(...)` write can delay the watchdog's own next sample, risking a self-inflicted phantom stall count | **Fixed, using a stronger mechanism than the review's own literal suggestion** — new `_record_stall_fault_background()` helper dispatches via a *retained* `asyncio.create_task(...)` (a module-level `_pending_fault_writes` set + `add_done_callback` discard), fire-and-forget with respect to the loop's own timing but not leaking the weak-reference GC hazard Task 8a elsewhere in this plan exists to fix. Documented in Task 1's Interfaces section and the stall-branch code comment. |
| 6 | Adversarial, should-fix 6 (F7) | `services/task_supervisor.py:89` citation should be `:73` | **Fixed** |
| 7 | Adversarial, nice-to-have 1 (F2) | Task 5 Step 3's "to:" code block doesn't show the atomic-write block that follows it, which could confuse an implementer | **Fixed** — explicit note added immediately after the code block confirming the atomic-write block is unchanged and correctly stays in place |
| 8 | Adversarial, nice-to-have 2 (F13, second finding) | `services/loop_watchdog.py` is 45 lines, not 46 | **Fixed** |

Every item in the adversarial review's should-fix and nice-to-have lists was applied; there
were no must-fix items to apply.

## Verification of the fix pass against the fix list

Checked item-by-item post-edit (not accepted on completion claim alone, per the HARD RULE's
"a revision that silently drops a requested fix is itself a defect" clause):

- `grep` for "task_supervisor.py:89"/"46 lines"/"the 1 call site"/the old shallow `git log`
  PR #424 phrasing → zero hits.
- `grep` for "17-38" → the only remaining occurrences are (a) this file's own Global-
  Constraints-level summary line correctly describing 17-38s as "the pre-fix range" (line 95,
  written that way from the start of this fix pass), and (b) two Step-1 "confirm the route's
  own comment still says X" instructions, which accurately describe what the *source code's
  own comment text* says (a target for implementers to locate and re-confirm, not a claim
  about current cost) — left unedited deliberately, since editing them would misrepresent
  what's actually written in the source file they're asking the implementer to find.
- Code-fence count unchanged (160, even/balanced) — every fix touched only prose and one
  code block's surrounding commentary (Task 1's helper + stall-branch edit), with fence pairs
  preserved.
- Task heading count unchanged (9) — no task added, removed, or restructured by any fix.
- Task 1's fix specifically re-verified for the GC-hazard concern noted above: the applied
  `_record_stall_fault_background()` helper retains a reference in `_pending_fault_writes`
  and removes it via `add_done_callback`, the same pattern Task 8a's own
  `_supervise_background()` uses — confirmed by direct comparison, not assumed equivalent.

## What GO means here

This plan has no separate design stage preceding it (documented at the top of this
consolidation), so GO here clears the **implementation plan** stage directly — the final
stage before code is written. Per CLAUDE.md's "nothing advances on one pass" HARD RULE, the
next required review cycle is the PR-stage one: after this plan (with its fixes) is pushed
and a PR opened, one more full self-review/adversarial-review/consolidation cycle runs
against the PR as submitted, before merge. Writing the actual code for the plan's 9 tasks is
implementation-time work covered by TDD/systematic-debugging/verification-before-completion,
not by this cycle (per the HARD RULE's own Scope note) — but the code has not been written
yet; this consolidation clears the plan document only.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
