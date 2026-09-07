# Consolidation — Strategy Edge Gate Implementation Plan (2026-09-03)

Reconciling `docs/archive/lane-3-strategy-risk-execution/plans/2026-09-03-strategy-edge-gate-implementation.md` (commit
`950ea12`), its embedded Plan self-review, and the independent adversarial review
(`docs/archive/lane-3-strategy-risk-execution/plans/2026-09-03-strategy-edge-gate-implementation-review.md`, commit
`2cebc34`, a fresh Agent call with no memory of the authoring session) per CLAUDE.md's
"nothing advances on one pass" HARD RULE.

## Verdict: **GO**

The adversarial review's independent verdict was GO-AFTER-FIXES: this is, in the reviewer's
own words, "an unusually well-verified plan on the file:line/signature axis — essentially
every citation checked came back exact." Task 8 (the one task that actually changes code on
the real entry-decision path) is independently confirmed provably inert with
`edge_gate_enabled: false`, and its fail-open/fail-closed semantics match the design exactly.
Task sequencing and cross-task dependencies were independently re-derived and hold. All 2
must-fix and all 4 should-fix items are now applied. Both must-fix items were in Task 4 — the
plan's one piece explicitly designed to run unconditionally, ahead of the gate, so real
markout data has time to accumulate — and both are corrected. Neither fix changes the design
this plan implements or any of its architectural decisions; both are implementation-detail
bugs in the plan document's own code sketches. This clears the plan for execution; per the
HARD RULE's own text, a fix-list recheck is sufficient here and a full second
self-review/adversarial-review cycle is not required, since no fix changed the artifact's
scope or introduced a claim neither original pass had seen.

## A note on the review's own falsification of its task brief

The adversarial review independently falsified a premise handed into it (via this session's
own review-dispatch instructions, not the plan document itself): that a separately-merged
watchlist-entry-gate PR predated this plan's authoring and might have made its citations
stale. The review found the actual PR (#482) was opened ~6 minutes *after* this plan's last
commit, remains open/unmerged, and touches no file this plan's citations depend on today. This
is recorded here for the record — it is not a defect in the plan, and required no plan-document
fix — but is exactly the kind of premise-checking this review stage exists to catch, and is
credited as a correct catch, not treated as noise.

## Disagreements between self-review and adversarial review

None. The plan's own self-review is explicit that Task 4 is "the one always-on piece" and
frames its correct placement (ahead of the gate) as deliberate — the adversarial review's
findings don't contradict that placement decision, they identify two bugs in how that already-
correctly-sequenced task's code was specified. The self-review did not claim to have executed
or dry-run any of the plan's own test code against real module state (`main.state["broker"]`
would need to actually be imported and run to surface F3; `trades_since()`'s missing filter
would need an open-then-close scenario, which no test in the plan's own list exercised, to
surface F4) — these are exactly the class of concrete, mechanical defect a same-context
self-review is least likely to catch, which is why the adversarial review exists as a
genuinely separate pass.

## Merged fix list and disposition

| # | Source | Finding | Disposition |
|---|---|---|---|
| 1 | Adversarial, must-fix 1 (F3) | Task 4's sketch and its own wiring test reference `state["broker"]`, which doesn't exist anywhere in this codebase (`broker` is a standalone module-level name) — `KeyError` in the test, silent permanent fault-logging in the real implementation | **Fixed** — every occurrence in Task 4 (the wiring test, its explanatory parenthetical, and the implementation) corrected to the bare `broker` name, with a note explaining the correct import and citing `main.py`'s own real usage |
| 2 | Adversarial, must-fix 2 (F4) | `PaperBroker.trades_since()` selects both entry and close rows with no discriminator, contaminating the markout population with close events that bear no relationship to "residual mispricing after a whale-follow entry" — no test in the plan's original list would catch this | **Fixed** — `trades_since()` now filters out rows whose `reason` starts with `"closed:"`, reusing the already-established convention two other modules depend on; a new test (`test_trades_since_excludes_close_rows`) opens and closes a position and asserts the close row is absent |
| 3 | Adversarial, should-fix 1 (F5) | The "honest gap" `t+close`/`close_ts` detection trigger exists only in prose, with no corresponding step in Task 10's checklist | **Fixed** — new Task 10 Step 4 queries `markouts` for `offset_label='close'` rows whose entry-to-target gap exceeds a stated 30-day threshold, records the finding either way (affected tickers, or an explicit "none found") |
| 4 | Adversarial, should-fix 2 (F6) | Task 4 Step 7's runtime-cost measurement (whole-tick `last_tick_duration_sec` before/after) is real but noisier than the ~50ms guideline it checks, since most ticks don't include the sweep's cost at all | **Fixed** — replaced with a direct `time.perf_counter()` bracket around `_maybe_capture_markouts`'s own body, matching Task 8 Step 6's technique, with a `capture_markouts_slow` fault_log entry if the measured delta exceeds 50ms |
| 5 | Adversarial, should-fix 3 | The "What changed" section's claim that this worktree's own `config/settings.yaml` is uncommitted-`M` against its own git HEAD doesn't hold (the worktree's copy is clean; the real divergence is against the primary checkout's live file) | **Fixed** — corrected to state the divergence is against the primary checkout's live file only, with an explicit note on what the earlier version got wrong |
| 6 | Adversarial, should-fix 4 | Forward-looking note: PR #482, if it merges before Task 8 runs, will shift line 579 inside `evaluate()` | **Fixed** — explicit note added to Task 8 Step 2, alongside the mechanical re-read step that already defends against it |

Every item in the adversarial review's must-fix and should-fix lists was applied; none were
deferred.

## Verification of the fix pass against the fix list

Checked item-by-item post-edit (not accepted on completion claim alone, per the HARD RULE's
"a revision that silently drops a requested fix is itself a defect" clause):

- `grep` for `state["broker"]` → the only remaining occurrence is inside the corrective note
  itself, explicitly describing what an earlier version said (for provenance), not a live
  reference anywhere in test code or implementation.
- `trades_since()`'s implementation now filters on `row[5]` (confirmed the `reason` column's
  position in the same `SELECT` statement) and a dedicated new test exercises the
  open-then-close scenario that no prior test in the plan covered.
- Code-fence count unchanged in kind (52, even/balanced) — every fix touched only prose and
  the specific code blocks the findings named, with fence pairs preserved throughout; one net
  new code block was added (Task 4 Step 7's `perf_counter` bracket) and one net new test
  function (`test_trades_since_excludes_close_rows`), both accounted for in the even count.
- Task heading count unchanged (10) — Task 10 gained a new Step 4 (with Steps 4-5 renumbered
  to 5-6), not a new top-level task; no task added, removed, or restructured.

## What GO means here

This plan has a design stage preceding it (already cleared GO in a separate consolidation),
so this consolidation clears the **implementation plan** stage, the pipeline's third stage.
Per CLAUDE.md's "nothing advances on one pass" HARD RULE, the next required review cycle is
the PR-stage one: after this plan (with its fixes) is pushed and a PR opened, one more full
self-review/adversarial-review/consolidation cycle runs against the PR as submitted, before
merge. Writing the actual code for the plan's 10 tasks is implementation-time work covered by
TDD/systematic-debugging/verification-before-completion, not by this cycle — but no code has
been written yet; this consolidation clears the plan document only.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
