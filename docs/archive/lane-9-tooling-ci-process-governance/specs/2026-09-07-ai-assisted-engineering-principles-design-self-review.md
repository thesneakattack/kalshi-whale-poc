# Self-review — AI-assisted engineering principles design (2026-09-07)

Same-session review of
`2026-09-07-ai-assisted-engineering-principles-design.md` per CLAUDE.md's
"nothing advances on one pass" HARD RULE (self-review layer; the
adversarial pass follows as a fresh Agent). Lean form: findings plus
verdict. Also the brainstorming skill's spec self-review (placeholders,
contradictions, scope, ambiguity).

## Corrections applied before the adversarial pass

Found by re-running the simulation with the spec's own rules rather
than trusting the first run's first-match output:

1. **The 30 unreviewed PRs split 23 A / 7 B, not 22 / 8** (miscount).
   The seven Tier B are #301 #308 #415 #445 #498 #623 #273; the spec's
   §3.2 and §7 fixture counts corrected.
2. **#272 was not "A only because of a cheatsheet"**: it also changed
   code under `services/kalshi/`. Re-run with the code-suffix rule
   applied: the prose exemption moves **zero** PRs in the window. §3.1
   reworded to say so.
3. **The "coarse spots" examples were wrong**: #389 #396 #302 all touch
   other Tier A code besides `config/settings.yaml`. Marginal analysis
   (remove one entry, count PRs that flip to B): `settings.yaml` 0,
   `main.py` 3 (#625 #647 #649), `MONEY_UI_PATHS` 3 (#390 #422 #598),
   `LANES[1]+[2]` 1 (#545). §3.2, D1, D3 now cite these.
4. **Line anchors** re-derived by `grep -n` at `6a63309`: Safety
   invariants last bullet is line 103 (not 104); the displayed-value
   bullet is 107 (not 108); `branching-and-ci.md`'s protection paragraph
   is lines 96–116 (not 96–110). Header 3, Scope 40, Consolidation 46,
   "For an in-scope PR" 48, dimensional last bullet 60, `main` line 117,
   "Read the PR body" 63 — confirmed.
5. **`github_client` has no PR-reading methods** (`find_pr_state` is the
   only PR call; verified by listing its methods). §6.2 now names the
   five readers the plan must add. The `FakeGithubClient` in
   `tests/test_kanban_sync_sync.py` does exist, so §7's fixture claim
   stands.

## Placeholder / contradiction / ambiguity scan

- No TBD/TODO. Every rule edit in §5 is verbatim text, not a description
  of text.
- **Consistency of tier obligations across §2, §4, §5.1:** Tier A = three
  artifacts, Tier B = one comment, in all three places. The Scope-bullet
  replacement keeps every clause of the current bullet that is not about
  the tier (planning-stage boundaries, no commit/push gate, TDD covers
  implementation) — checked clause by clause against line 40.
- **`review-tier` artifact-count regex vs. this repo's real headings:**
  `# Self-review — …`, `# Adversarial review — …`, `# Consolidation —
  …` match `^#+\s*(self-review|adversarial review|consolidation)`
  case-insensitively. The stage-1 artifacts in this very branch are
  committed *documents*, not comments, so for a docs PR the count comes
  from the review-named files in the diff — covered by the second half
  of the count rule. Ambiguity found and resolved: a PR could carry
  three review *files* for a different initiative and be miscounted; the
  count rule should require the file to be new or modified in *this*
  PR's diff, which `files` already implies. Stated in §6.2 by "files in
  the diff".
- **Rule 3 (data-model) vs. the D1–D7 table:** rule 3 is not in the
  decision table although it widens Tier A into Lane 4; it is a design
  choice the adversarial pass should be able to contest. Added as a
  note in this review rather than a table row, since it follows directly
  from the article's "data model" boundary and the research's §7.1.
- **Scope check:** one implementation plan can carry this (labels
  constant + function + two subcommands + five client readers + tests +
  three prose files). It is not two initiatives.
- **Ambiguity:** "a later `fix`-prefixed PR that cites `PR #P`" in the
  defect predicate (§6.3 c) — does an issue-only citation count? No, and
  that is deliberate (the research's proxy used the same strictness); the
  report prints its predicate so this cannot be mistaken for a defect
  rate. Stated.

## Unaddressed scope

- The Tier B comment template's fields are prose in §4; the plan should
  ship it as a snippet in `branching-and-ci.md` or the checkpoint skill
  so authors copy it rather than paraphrase it. Plan item, not a spec
  change.
- `outcomes`' first window (2026-09-07 → 09-21) starts before the
  mechanism exists; the first report will be computed retroactively over
  that window, which is fine for a read-only measure and should be said
  in `docs/next-action.md` at rollout.
- Nothing in the spec changes `guard_workflow.py`; the subset test is the
  only new coupling. Confirmed against §6.1.

## Verdict

Internally consistent after the five corrections; ready for the
adversarial pass. The two things that pass should attack hardest: (a)
whether the Scope-bullet replacement in §5.1 silently drops any clause
of the current bullet, and (b) whether the Tier A path list misses a
consequential module (a Lane 4/5/6 file that changes trading, risk, or
money behaviour) — rules 3–5 exist for that and were not simulated.
