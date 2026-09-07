# Next action

**Before every `gh pr merge`, from now on:**

```bash
python -m tools.kanban_sync review-tier --pr <n>
```

Merge only on `PASS` or `EXEMPT`. It decides Tier A/B from the paths the PR
touches and counts the persisted review artifacts — three PR comments for
Tier A, one `Tier B self-review` comment for Tier B. A `FAIL` means an
artifact is genuinely missing: supply it (a fresh Agent for the adversarial
pass, the author for a Tier B self-review) or do not merge. `--exempt
"<reason>"` for a mechanical change, `--tier A` to escalate. The full rule is
CLAUDE.md's "nothing advances on one pass" Scope bullet; the merge step is in
`.claude/rules/branching-and-ci.md`.

Only the **first line** of a comment is read, and it must begin with what the
comment is: `## Self-review`, `## Adversarial review`, `## Consolidation`,
`Tier B self-review`. A comment that merely discusses a review does not count.

**No active task.** Review tiering shipped on 2026-09-07 (branch
`docs/lane9-ai-assisted-engineering-principles`). David has not given a new
instruction; don't self-assign — report status and let him decide.

**Dated follow-up, 2026-10-05** (`docs/open-decisions.md`): decide whether
`review-tier` has earned its place (has it ever caught a real missing
artifact?) and whether the cut outcomes report is worth building. Both are
answered from evidence, not impression.

**Known on day one:** the tool's first live run over the ten most recently
merged PRs returned nine `PASS` and one `FAIL` — PR #663, a Tier A change to
`tools/kanban_sync/labels.py` and the planning-pipeline directories, merged
with zero review comments. That is the failure the whole initiative is about,
found by the tool on its first run rather than by anybody reading back through
the history.
