# Self-review: weather index ingestion plan (2026-08-31)

Reviews `2026-08-31-weather-index-ingestion.md` for internal consistency
and unaddressed scope, per CLAUDE.md's "nothing advances on one pass" HARD
RULE.

## Checked

- **Every fix-list item from the design-stage consolidation has a
  corresponding task**: refreshed ranking → Task 2; THOU/TDAL/AUS
  reconsideration → Task 2; re-run-once-cycled → Task 2's explicit
  instruction; the design's two already-flagged open items (city-ID
  spelling, REST cost/rate-limit measurement) → Tasks 1 and 6
  respectively, not silently dropped.
- **TDD ordering is real, not decorative**: every task that writes
  production code (3, 4, 5, 7) lists its tests before its implementation,
  matching this repo's `test-driven-development` skill.
- **Two citations were verified against source during this plan's own
  writing, not left as guesses**: `fault_log.record(component, context,
  exc)`'s real signature (`services/index_feed/ingestion.py:150`) and
  `main.py`'s real scheduler-tuple-list pattern (lines 566-567,
  `_maybe_run_backup`/`_maybe_run_large_backup`) — both read directly
  before being cited, not assumed from the design doc's looser mention of
  "same shape as every other scheduler."
- **Scope discipline**: no task touches `risk_manager.py`, `paper_broker.py`,
  strategy/entry code, or `tools/` — matches both the design's explicit
  out-of-scope list and CLAUDE.md's workflow/tooling-vs-application-code
  separation. Restated in the plan's own closing section so a future
  session doesn't scope-creep.
- **dimensional-analysis is flagged where it belongs**: Task 3 (value/
  precision) and Task 6 (REST-cost/rate-limit arithmetic) — the two places
  this plan does or measures real arithmetic — not treated as a one-time
  whole-plan checkbox.

## Unaddressed scope / weaknesses to flag for adversarial review

- Task 6's polling-interval decision depends on Task 2's finalized city
  count, which depends on a live re-query this plan can't run itself right
  now (catalog needs to finish cycling) — the plan sequences this
  correctly (Task 2 before Task 6) but doesn't say what happens if the
  catalog *still* hasn't finished cycling by the time someone picks this
  plan up days later. Worth the adversarial pass judging whether that's an
  acceptable execution-time judgment call or a real planning gap.
- Task 8's ddev-restart step assumes the app is running under ddev at
  execution time — reasonable per this repo's dev workflow, not verified
  here since this is a planning artifact, not an execution one.
- No task explicitly states what "done" means for the whole plan beyond
  the task checkboxes (e.g., is there a final consolidation step, a
  results doc, an update to ROADMAP.md?) — this repo's convention (seen in
  the just-completed claudesuperpower-plugin-pilot plan) sometimes adds an
  explicit final consolidation task; this plan doesn't have one. Worth
  flagging whether that's a real gap or unnecessary for an 8-task,
  single-package plan.

## Verdict

**GO.** Every design-stage fix-list item is represented as a task, TDD
structure is real, and two load-bearing citations were verified against
source rather than copied from the design doc's paraphrase. Proceed to
adversarial review.
