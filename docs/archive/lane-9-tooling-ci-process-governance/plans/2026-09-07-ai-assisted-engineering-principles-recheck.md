# Fix-list recheck — implementation plan revision (2026-09-07)

CLAUDE.md's recheck clause: a revision is checked against the fix list item by
item, never accepted on its own completion claim — a revision that silently
drops a requested fix is itself a defect. This checks the fifteen items in
`…-consolidation.md` against the revised plan and the amended spec. Scoped to
the fix list; not a second full cycle.

Each item was verified by locating the exact text in the file, not by
remembering having written it. Thirty string checks ran; twenty-nine matched
mechanically and item 15 matched on inspection (its sentence wraps across a
line, so the search string spanned the break).

| # | Fix | Verified how | Result |
|---|---|---|---|
| 1 | Anchored regex; eight false positives as negative fixtures; snapshot assertion 213/261; accepted false negative named | Four string checks in Task 4 (pattern body, `## Fix-list recheck (adversarial review returned NO-GO)`, `assert len(matched) == 213`, `## Review outcome (independent adversarial`) | **Done** |
| 2 | `count_review_artifacts(comments)` — comments only; file counting removed; committed-docs test replaces the two file tests | Signature, call site in Task 6, `test_committed_review_documents_do_not_satisfy_the_pr_gate`; `_ARTIFACT_FILENAME` confirmed **absent** | **Done** |
| 3 | `ast`-based scanner; bare-form test; non-vacuous floor | `_imported_dotted_names(tree: ast.Module)`, `test_lane3_direct_imports_finds_the_bare_from_services_form`, `assert len(targets) >= 25`; `_LANE3_IMPORT_RE` confirmed **absent** | **Done** |
| 4 | Six paths added with rationale | `services/fault_log.py` … `services/market_analyst_agent/` present in `_REVIEW_TIER_A_EXTRA_PATHS` with the per-path reasons in the comment | **Done** |
| 5 | Fixture is six PRs; #498 becomes a Tier A fixture; counts 70/14 and 24/6 | `test_tier_b_the_six_unreviewed_low_blast_radius_prs`, `test_pr_498_is_tier_a_through_the_lane_3_dependency_index_feed`, the verification note's "code-typed 70 / 14, unreviewed 24 / 6"; the old seven-PR test name confirmed **absent** | **Done** |
| 6 | Spec amended in §3.1, §3.2, §6.2 | Three dated correction blocks located in the spec | **Done** |
| 7 | Deviation 4 rewritten; 6 and 7 added | All three located; list re-ordered so it reads 1–7 in sequence | **Done** |
| 8 | Tier B template snippet in `branching-and-ci.md` | The five-field markdown block in Task 8 step 1 | **Done** |
| 9 | `#615` post-merge state check | `gh issue view 615 --json state,stateReason,closedAt` in Task 9 step 6 | **Done** |
| 10 | Task 1's grep claim corrected | "No test reads the hook's tuples", with the ten real `kalshi_client` hits named and marked leave-alone | **Done** |
| 11 | #663 has two triggers | "through both the pipeline-directory prose rule and the `labels.py` path rule" | **Done** |
| 12 | Task 7 step 15 names L38 as an expected survivor | "line 38's second sentence, which still reads … leave it" | **Done** |
| 13 | `review-tier` stated as the exception to CLAUDE.md L130 | The clause in the new merge bullet, with its retirement test dated 2026-10-05 | **Done** |
| 14 | Wrapped phrases flagged in Task 8 | "wraps across lines 77–78" and "wraps, across lines 92–94" | **Done** |
| 15 | #273 docstring no longer overstates | "Tier B on its *file list* and Tier A on its diff — it adds recovered `raw_trades` rows, which rule 3 catches" | **Done** |

## Beyond the fix list, checked because the revision touched them

- **The revision did not break the document.** A scripted re-ordering of the
  deviations list overshot and displaced the plan's closing section; caught by
  re-reading the structure, repaired, and re-verified: nine tasks in order,
  one `## Plan self-review` heading, at the end, where it belongs.
- **The test-stem example had to change.** `test_a_test_of_a_tier_a_module_is_tier_a…`
  used `tests/test_index_feed_backfill.py` as its Tier B case; adding
  `services/index_feed/` makes that file Tier A, so the assertion would have
  contradicted the new fixture two tests above it. Replaced with
  `tests/test_maintenance.py`, which makes the same underscore-boundary point
  (it must not match the stem `main`) and stays Tier B.
- **Stale test counts.** Two "Expected: PASS (N tests)" lines were left over
  from before tests were added and removed; replaced with descriptions rather
  than new numbers that would go stale again.
- **`import pytest`** is now needed in `tests/test_kanban_sync_review_tier.py`
  (the committed-docs test uses `pytest.raises`); noted in the file's import
  block.
- **The corrected counts are consistent across all three documents.** The
  recorded window (containing #663) gives 144/56; the spec's window (#254 in,
  #663 out) gives 143/57; code-typed 70/14 and unreviewed 24/6 are the same in
  both, because the swapped PR is docs-typed and reviewed. The plan states the
  recorded-window figures and the spec correction states its own.

## What this recheck did not do

- Execute the plan's Python. The adversarial pass did (33 of 34 tests passed
  against the pre-revision code, and the one failure is fix 1). The revision
  changes the regex and a signature; those changes were validated
  independently — the anchored pattern was measured against the 261-line
  snapshot and the eight named false positives before being written into the
  plan, and the `ast` scanner was run against the real repository to produce
  the 27 targets and the six uncovered paths. Task 1–4's own steps re-run all
  of it during execution.
- Re-verify the line anchors, the exists-on-disk claim, the hook subset, or
  the `gh` command shapes. The adversarial pass verified all of them and the
  revision did not touch them.

## Verdict

**Stage 3 verdict: GO.** All fifteen fixes are in, three defects the revision
itself introduced were caught and repaired, and the plan, the spec, and the
consolidation now agree on the same numbers.

The plan is ready to execute. Two things carry forward into the PR, and the
PR body must say both: **stage 3 corrected stage 2** in three places (the
spec's §3.1, §3.2 and §6.2 carry dated correction blocks), and this PR's own
Tier A cycle owes three distinct persisted **comments** — its committed
research and spec review documents deliberately no longer count toward it,
which is the point of fix 2.
