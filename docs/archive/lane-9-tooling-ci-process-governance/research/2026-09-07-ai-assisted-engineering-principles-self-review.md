# Self-review — AI-assisted engineering principles research (2026-09-07)

Same-session review of
`2026-09-07-ai-assisted-engineering-principles.md` per CLAUDE.md's
"nothing advances on one pass" HARD RULE (self-review layer: internal
consistency and unaddressed scope, before the independent adversarial
pass). Lean form: findings plus verdict.

## Corrections applied to the artifact before the adversarial pass started

Found by re-reading my own numbers against the scratch data, not by the
adversarial agent:

1. Linked-issue falsifier: 32 issue references (not 34), not all unique.
2. Narrative-in-body count: at least 10 of 21 (not 12); the 3 `chore`
   bodies were never classified.
3. Reverts: 7 commits whose subject *is* a revert (9 mention the word);
   the artifact first said 9, then 6, now 7 — method stated.
4. Removed `POST /api/backtest` (written from memory — the routes are
   `GET /api/backtest/entry-threshold` and `.../min-whale-winrate`,
   `services/backtest/routes.py:16,36`); replaced with "`services/backtest/`
   routes".
5. Re-worded the "no path list behind the automation-never-edits
   sentence" grep to the command actually run.
6. Verified `HOT_PATHS` usage directly: `guard_workflow.py:397` (the
   dimensional nudge) is the only live use; `:351` is the disabled R4
   line, commented out. The artifact's "used only to fire the
   dimensional-analysis nudge" stands.

## Internal consistency

- 84 code-typed PRs = 66 fix + 10 feat + 7 chore + 1 refactor; 24 without
  artifact + 60 with = 84. "Three in ten" = 29%. Consistent.
- **Inconsistent:** §3.1 says "docs 104 (52%)" (prefix `docs:` exactly)
  while §3.4 says "docs PRs: 60 of 105" (`startswith('docs')`, which also
  catches the one `docs+feat:` title). Fix: use one definition; state it.
- **Inconsistent:** P1 describes Tier B as "self-review + CI before push;
  one adversarial pass + consolidation at the PR" (three artifacts at two
  moments) while P3 says Tier B needs "two" persisted artifacts. The two
  rows must agree on what Tier B persists. Proposed reconciliation for
  the spec: Tier B persists the adversarial review and the consolidation
  as PR comments; the self-review is the author's pre-push pass and
  whether it must be persisted is an explicit spec question, not an
  implied answer.
- §3.5's all-time follow-up rate (24/135 = 18%) and the last-200 split
  (15 + 2 = 17 of 84 = 20%) are from the same proxy on different windows;
  compatible.

## Falsifiers named in §8 that I then actually ran

1. **Docs-PR reclassification (§8 row 2) — the artifact's §3.1 reading
   is overstated at PR level.** Classifying the 105 docs-prefixed PR
   titles by first keyword match: audit/research/census/investigation 26;
   other 23 (mostly benchmarks, root-cause write-ups, fix-family
   comparisons — research-shaped); lanes migration/classification 20;
   next-action/status/decision records 20; plan/spec/design 13;
   **review-cycle artifact 3**. So review artifacts are not separate PRs;
   they ride inside the research/plan PRs they review. The file-level
   number (122 of 312 `docs/` markdown files are review artifacts, 39%)
   is the defensible one; the PR-level sentence "a large fraction of that
   documentation is the review process documenting itself" must be
   restated as "review artifacts are 39% of docs *files*; docs *PRs* are
   mostly requested audits, migration, and status records". Required fix.
2. **Size-banded follow-up split (§8 row 4) — the confound argument was
   incomplete.** By additions band, with-artifact vs without-artifact
   follow-up-fix rate: 0–100: 2/12 vs 0/8; 100–500: 5/30 vs 0/7; 500+:
   8/18 vs 2/9. The inversion persists inside every band, so size alone
   does not explain it. Two remaining explanations, neither testable
   from this proxy: reviews *generate* follow-ups (a found gap becomes a
   later PR citing "PR #N", i.e. the review working), and reviewed PRs
   carry more written context and are simply cited more. Either way the
   conclusion sharpens rather than weakens: the "cites PR #N" proxy
   measures discussion, not defects, and P4 must define a defect (live
   incident, revert, or a fix that changes behavior the original PR
   claimed) rather than count citations. Required fix to §3.5's table row
   and to P4's definition column.

## Unaddressed scope

- The article's third question ("are experienced people spending hours
  on judgment?") has no measure in the artifact and P4 does not propose
  one. Only David can supply it (minutes spent per task class). The
  artifact should say that plainly instead of leaving P4 to imply
  coverage. Required fix: one sentence in §3.7 and in P4's caution.
- 3 `chore` PRs (#389, #445, #531) were not swept for linked-issue
  reviews or body narrative. Small; stated in the artifact.
- The ChatGPT drafts propose twelve principles; the artifact keeps seven.
  The five not carried (start from the intended effect; find the limiting
  activity; make uncertainty actionable; preserve authority boundaries;
  five-question handoff) describe per-task assistant behavior that the
  harness's own instructions and CLAUDE.md's never-guess rule already
  cover; they are not repo rules and would duplicate. The artifact does
  not say this. Required fix: one sentence in §5's closing paragraph so
  the adversarial reviewer and the spec can contest the choice.
- The article page is not mirrored; quotes come from one WebFetch
  extract. Left to the adversarial pass (its item 8).
- Whether P1's "any Tier A file touched → Tier A PR" reading interacts
  badly with the lanes design's "PR labeled by its own primary purpose,
  not file count" rule (lanes §5). They answer different questions
  (which lane vs. how deep to review) but a reader could conflate them.
  Spec item, add to §7.

## Verdict

Artifact is internally sound on its central measurements (317/200 PR
counts, 24/84 unreviewed code PRs, latency, path lists, incident class)
and needs revision on four points before it can be trusted as a stage
artifact: (1) §3.1's PR-level over-attribution, (2) §3.5/P4 defect
definition after the banded split, (3) P1/P3 Tier B artifact-count
mismatch and the docs-count definition, (4) the two stated scope gaps
(human-hours measure; dropped-principle rationale). None changes the
candidate principle set or the decision on record. Consolidation should
merge these with the adversarial pass's list and apply them in one
revision.
