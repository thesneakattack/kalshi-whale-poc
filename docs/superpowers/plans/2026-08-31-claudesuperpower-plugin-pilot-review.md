# Self-review: claudesuperpower.com plugin pilot plan (2026-08-31)

Reviews `2026-08-31-claudesuperpower-plugin-pilot.md` for internal
consistency and unaddressed scope, per CLAUDE.md's "nothing advances on one
pass" HARD RULE.

## Checked

- **Every task in the design doc's per-plugin sections has a corresponding
  plan task**, in the same priority order, with no plugin dropped or
  reordered without explanation.
- **Task numbering follows `docs/superpowers/plans/README.md`'s parsed
  format** (`### Task N: <title>`) and this plan is not yet added to that
  README's index table — flagged below as a required follow-up, not done
  in this file (the README is a shared cross-plan index; editing it belongs
  with this plan's own commit, done as part of finalizing, not mid-review).
- **The wiring-test claim is verified, not assumed**: this plan directly
  read `tests/test_mcp_and_plugin_wiring.py` (not just cited the design
  doc) and confirmed `test_every_enabled_plugin_has_a_known_marketplace`
  already treats `@claude-plugins-official` as known without needing an
  `extraKnownMarketplaces` entry, and `test_enabled_plugins_are_all_actually_reachable`
  only blocks `second-opinion`/`42crunch-api-security-testing` — none of
  the 4 target plugins. This is primary-source verification done at the
  plan stage, not inherited unchecked from the design stage.
- **Task 1 and Task 5 are correctly independent gates**, not one gate
  covering all 4 plugins — matches the design's install-order rationale
  (codspeed's account requirement is a separate blocker from the other
  three's plain install go-ahead).
- **No task instructs installing/running anything** — every install step is
  still inside a task that has not been executed; this document is itself
  inert until Task 1/5's go-ahead is given, matching the design's
  non-goals.

## Unaddressed scope / weaknesses to flag for adversarial review

- Task 2's Tier A pilot depends on "one real, already-in-flight PR" existing
  at execution time — same caveat the design-stage self-review raised and
  the design-stage adversarial review left as acceptable to leave open;
  restated here rather than re-litigated, since nothing new changes that
  judgment.
- Task 7's results-doc filename uses a placeholder `<date>` rather than a
  fixed date, since the actual pilot could run any time after go-ahead —
  worth the adversarial pass confirming that's reasonable rather than a
  loose end.
- The plan doesn't specify what happens if a wiring test *fails*
  unexpectedly after an `enabledPlugins` edit (i.e., a rollback-on-failure
  step within a task, not just Task 7's end-of-pilot rollback) — worth
  flagging; a concrete "don't commit, don't proceed to the next task,
  investigate" instruction may be missing.
- Task 6 says the account "is a human action — this task does not create
  external accounts on the user's behalf," but doesn't say who runs
  `codspeed auth login` interactively or how that's confirmed done before
  the pilot benchmark step proceeds — a possible sequencing gap.

## Verdict

**GO.** The plan is a direct, task-by-task translation of the reviewed
design with no new unreviewed decisions introduced, and one of its own
claims (the wiring-test behavior) was independently re-verified against
source rather than trusted from the design stage. Proceed to adversarial
review.
