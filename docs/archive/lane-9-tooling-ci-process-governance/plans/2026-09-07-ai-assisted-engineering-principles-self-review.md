# Self-review — AI-assisted engineering principles implementation plan (2026-09-07)

Same-session review of
`2026-09-07-ai-assisted-engineering-principles.md` (committed at `a1d2fc0`)
per CLAUDE.md's "nothing advances on one pass" HARD RULE. Lean form:
findings plus verdict. The plan carries its own short spec-coverage /
type-consistency / placeholder section, required by the `writing-plans`
skill; this document is the rule's separate artifact and does not repeat it —
it records what that section does **not** cover.

## Corrections already applied while writing

1. **The test-stem rule was wrong on the first pass.** I first designed
   `tests/test_<stem>*.py` as a token-subsequence match (any `_`-delimited
   run of the stem anywhere in the test name). Checked against the real
   corpus before writing it down: that reading makes
   `tests/test_index_feed_backfill.py` Tier A through the stem `backfill`
   (from `services/index_feed/backfill.py`), which reclassifies PR #498 —
   one of the seven PRs the spec's §3.2 names as Tier B. The spec's literal
   wording (`test_<stem>*`, a prefix) is both what it says and what
   reproduces its counts. Implemented as a prefix match at an underscore
   boundary, so `test_maintenance.py` cannot match the stem `main` either.
2. **The `outcomes` report and two client readers were cut mid-plan** on
   David's scope decision. Swept for leftovers afterwards: the file-structure
   table, the architecture paragraph, Task 5's method list and tests, Task
   5's in-code comment, the checkpoint edit, and the coverage line were all
   updated. Recorded as deviation 5 rather than dropped silently, with P4
   parked on a dated line.
3. **Simulated before writing, not after.** The rule set in Task 3 was run
   over the recorded 200-PR window first. It reproduces the spec's code-typed
   (68/16) and unreviewed (23/7) splits exactly; the 142/58-vs-141/59 overall
   difference was chased to its cause (the recorded window contains #663 and
   not #254; #254 is a docs-only PR that classifies Tier B) rather than left
   as an unexplained ±1.

## Findings — fix before the plan is executed

1. **The Tier B comment template has no copyable home.** The stage-2
   self-review flagged this as a plan item: "the plan should ship it as a
   snippet in `branching-and-ci.md` or the checkpoint skill so authors copy
   it rather than paraphrase it." Task 7's Scope-bullet text names the five
   fields in compressed form (`tier, change, evidence, falsifier, left
   undone`) and nothing else does. A field list inside a long rule sentence
   is not a template, and paraphrase is exactly how the body-narrative
   failure started. **Fix:** Task 8 adds the five-field snippet to
   `branching-and-ci.md`, next to the `review-tier` step.

2. **The PR body will name #615 repeatedly, and auto-close matches
   incidental phrasing.** Task 9 step 6 has the PR close #613 and discuss
   #615 (branch protection) in the same body, and Task 8 step 4's rule text
   also names #615. A "close"-family verb landing anywhere near a bare `#615`
   can close an issue that must stay open — this has happened in this repo.
   **Fix:** Task 9 step 6 says explicitly to write `#615` only in
   non-close-verb sentences, and to check `#615`'s `stateReason`/`closedAt`
   after the merge, not only `#613`'s.

3. **`_tier_a_prefix` returns the first match in sorted order, not the most
   specific one.** Only the *reason string* is affected — the tier is
   identical either way, since any match makes it Tier A — but a reason that
   names a less specific prefix than the one a reader expects will look like
   a bug during the Task 9 dry run. **Fix:** note it in Task 3's docstring so
   the dry run's output is read correctly. Not worth sorting by length.

## Findings — accept, but say so out loud

4. **A dotfile is treated as having an extension.** `_is_code_or_config`
   asks whether the basename contains a `.`; `.gitignore` therefore reads as
   "has a suffix, not in the list" and is skipped, while
   `scripts/woodpecker-status` reads as extensionless and counts as code.
   Both outcomes are the ones we want (PR #445, a `.gitignore` change, must
   stay Tier B), but they are reached by two different readings of the same
   rule. Worth one sentence in the code comment; not worth a third branch.

5. **Line 38's rewrite narrows what "carrying a claim" means.** Today the
   rule catches "a PR carrying a claim, design decision, new logic, or a
   process/rule change"; after the edit it catches "a Tier A PR", which is a
   path question. A claim-asserting document written outside the pipeline
   directories drops to Tier B. That is spec D9, decided deliberately, and
   escalation exists for the author who knows it matters — but the plan
   should not let it look like an accident of wording, and D9 is not restated
   anywhere in the plan. **Fix:** cite D9 in Task 7 step 2.

6. **`docs/open-decisions.md`'s existing section is replaced, not appended
   to.** Task 9 step 3 replaces "## Decided 2026-09-07 — spec in progress
   (remove when the spec's PR merges)" (line 26 on this branch) in full.
   Confirmed the section exists with that exact heading. The replacement
   drops the sentence recording *how* David decided (a direct three-option
   question, session `autotrade-d9`) — that provenance should survive, since
   it is the only record of the decision's origin outside this branch's
   documents. **Fix:** the replacement text keeps the provenance sentence.

## Unaddressed scope, deliberately

- The plan does not retroactively review the 30 unreviewed PRs the research
  found. Spec §1 says so; the research keeps the list.
- It does not touch the no-flake mandate or the skill-chain ceremony, the
  other two hour-plus contributors the 2026-09-03 memory names. Also spec §1.
- It does not narrow `MONEY_UI_PATHS` (spec D7) or decide #615.
- It does not build the outcomes report (deviation 5), so the article's first
  outcome question stays unanswered by tooling until someone needs it.

## What the adversarial pass should attack hardest

- Whether the plan's Python actually runs. It is complete code, written into
  a document, never executed as a unit.
- Whether every CLAUDE.md and `branching-and-ci.md` line anchor and every
  "replace this exact phrase" string is character-for-character correct at
  HEAD. A near-miss here produces a silently wrong edit.
- Whether the artifact regex's **false positives** are acceptable: a comment
  that merely mentions the word "adversarial" counts as an artifact and could
  let a PR merge unreviewed. The plan validated the true-positive side (224
  of 261) and asserts the misses are benign; it did not measure the other
  direction.
- Whether any PR the rules put in Tier B changed trading, risk, money, auth,
  or reset behaviour (spec §10's first NO-GO trigger).

## Verdict

Internally consistent, and the three fixes above are small and local. Ready
for the adversarial pass; the plan should not be executed until consolidation
says GO.
