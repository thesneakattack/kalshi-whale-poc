# Branching and CI — standing repository policy

Direct standing instruction (2026-08-25). The single source for git-branch and
CI-verification behavior here; `CLAUDE.md` and the skills point at this file.

## Branch model

`main` is the authoritative integrated branch; implementation work never lands
on it directly. Short-lived initiative branches only — no permanent
`development`/`staging` branches.

1. `git branch --show-current`.
2. On an appropriate initiative branch → continue there.
3. On `main` → `git fetch origin main --quiet && git log origin/main..HEAD --oneline`
   must be empty, then `git checkout -b <feat|fix|refactor|chore|docs>/<name>`.

One coherent initiative gets one branch: tightly coupled work that should merge
atomically stays together; independently mergeable work splits. A numbered
multi-task plan normally stays on one branch with one commit per task.

Parallel sessions share one primary checkout. Work in a worktree
(`git worktree add .claude/worktrees/<name> -b <branch> origin/main`, then
`EnterWorktree`); never checkout/stash/reset/rebase/merge in a checkout another
live session occupies — convention only since 2026-08-30 (the guard that used
to deny this, `guard_workflow.py`'s R6, was retired after its no-staleness-check
liveness detection denied two real merges over dead sessions in one night, and
no installed replacement covers this specific job); `orient.sh` lists the live
sessions at session start, and that's now the only check.

## Integration lifecycle

```
main → initiative branch → implementation → targeted local checks →
commit → push → Woodpecker → PR → merge → delete branch
```

- A push triggers every `.woodpecker/*.yml` on any branch. Confirm the real
  result before calling anything verified:
  `gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status`, reading
  each `context`'s `state` independently. Never weaken or bypass a legitimate
  CI guard to go green.
- `gh pr create` once pushed. If this PR carries a stage of the "nothing
  advances on one pass" planning pipeline (investigation/research →
  design/spec → implementation plan) — a research doc, a design/spec doc,
  or a plan under `docs/superpowers/`, alone or bundled — apply the
  matching `phase:*` label(s) to the PR itself (`gh pr edit <n> --add-label
  phase:research`, `phase:spec`, and/or `phase:plan`; a PR bundling more
  than one stage in one commit gets more than one label, honestly
  reflecting what it actually contains). Reuse the exact vocabulary
  `tools/kanban_sync/labels.py` already defines for issues
  (`phase:research`/`phase:spec`/`phase:plan`/`phase:implementing`/
  `phase:verification`/`phase:done`) — do not invent new label names. This
  is a PR-level convention, not a `tools/kanban_sync` feature: that tool's
  `phase:*` labeling is deliberately Issues-only (see `labels.py`'s own
  header — GitHub Projects board grouping works off issue-linked fields,
  not PR labels), so a PR gets its label directly via `gh pr edit`, never
  through a kanban_sync source. The payoff: `gh pr list --label phase:plan`
  (or the GitHub UI) answers "which PRs are part of a planning track, and
  at which stage" without opening each one — before this convention, every
  PR touching `docs/superpowers/` merged on 2026-08-31 (7 of them) carried
  zero labels between them, confirmed via `gh pr view <n> --json labels`
  across the day's full merge history, not guessed.
  Decide the exemption question first, then run
  `python -m tools.kanban_sync review-tier --pr <n>` (`--exempt "<reason>"`
  for a mechanical change, `--tier A` to escalate) and paste its output into
  the final PR comment or the merge commit; merge only on `PASS` or `EXEMPT`.
  It decides Tier A/B from the changed paths, data-model lines, and
  `concern:hotpath`, and counts the persisted review artifacts (three for A,
  one for B), reading only each comment's first line. A `FAIL` is not a
  formality — supply the missing artifact (a fresh Agent for an adversarial
  pass, the author for a Tier B self-review) or do not merge.
  A counted artifact's first line begins with what it is: `## Self-review`,
  `## Adversarial review`, `## Consolidation`, `Tier B self-review`,
  optionally prefixed (`Independent`, `PR-stage`, `Tier B`, `stage 2 of 3`).
  A comment that discusses a review — a correction, a recheck, a response to
  a finding — is not one, however much it says about it. A Tier B PR's
  single comment:

  ```markdown
  Tier B self-review

  **Tier:** <changed paths>; none matches REVIEW_TIER_A_PATHS, no data-model
  line, no concern:hotpath.
  **What changed and why:** <the behaviour, not the diff>
  **Evidence:** <checks that ran and what they showed; CI status URL>
  **Falsifier:** <what observation after merge would show this was wrong>
  **Left undone:** <anything noticed and not done, or "nothing">
  ```

  Read the PR body before merging — run
  `gh pr view <n> --json body,commits --jq '.body, (.commits[].messageBody)' | grep -n '\[ \]\|\[x\]'`
  every time, plus the same grep over any `docs/superpowers/` document the
  body links to or was generated from, since a checklist there is otherwise
  invisible here. A "Test plan" checklist, a named human gate, or a
  post-merge follow-up is easy to skip silently if nobody greps for it.
  Check off what's genuinely done in the PR itself, leave a real gate
  unchecked rather than pre-checking it, and never assume an unchecked item
  (a scheduled re-verification, a "run X after merge" line) executes on its
  own by merging — either do it before merging, or say explicitly, before
  merging, what will do it and when. Before `gh pr merge`, if `ListAgents`
  shows a live peer session, send it a one-line "about to merge PR #N" ping
  via `SendMessage` — courtesy to avoid a merge race or duplicated review
  effort, not a blocking gate and not a substitute for CLAUDE.md's "nothing
  advances on one pass" requirement (a Tier A PR still needs
  its own fresh, memory-less Agent call regardless of what a peer says; a
  Tier B PR still needs its `Tier B self-review` comment): proceed
  on an ack or on no pushback within a few minutes, don't stall the merge
  indefinitely on a slow or unresponsive peer (2026-08-31, after two real
  near-misses in one session: a peer posted a redundant consolidation
  comment on a PR this session was independently reviewing, and separately
  asked, unprompted, whether it was safe to merge a PR this session had
  already merged). `gh pr merge --merge` (keeps individual commits
  so `git log`/`blame` stay real history); delete the branch locally and
  remotely — `scripts/cleanup-worktrees.sh` does both for provably merged
  worktrees.
- Single-developer repo: no self-approval ceremony, just the mechanical
  guarantees — no routine work on `main`, no force-push, CI before
  integration, reviewable diffs, safe merges. This does not exempt an
  in-scope PR from CLAUDE.md's "nothing advances on one pass" HARD RULE:
  no *human* approval step is needed here, but the AI-executed review its
  tier requires — the self-review/adversarial-review/consolidation cycle
  for Tier A, the one self-review comment for Tier B — still runs before
  `gh pr merge`, and `review-tier` records that it did — that is rigor,
  not approval ceremony.

**GitHub-side enforcement (configured 2026-08-25; lapsed 2026-09-05 when the
repo was private, #615; restored 2026-09-07 once it was public again):**
`gh api repos/thesneakattack/kalshi-whale-poc/branches/main/protection` reports
`enforce_admins: true`, `allow_force_pushes: false`, `allow_deletions: false`,
`required_pull_request_reviews: null`, `required_status_checks.strict: false`,
and `required_status_checks.contexts` = `ci/woodpecker/pr/tests-pytest-app`,
`.../tests-pytest-tooling`, `.../tests-dependency-audit`,
`.../quality-architecture-audit`, `.../quality-browser-e2e`,
`.../kalshi-contract-fixtures` — the 2026-09-03 list, restored unchanged.
Deliberately excludes `ci/woodpecker/pr/quality-frontend-build`: it is
path-filtered to `frontend/**` and posts no status when skipped, so requiring
it would block every non-frontend PR. Update with
`gh api -X PUT .../protection --input <file>` when a required pipeline is added.

**The gap this closes, and why the manual practice stays anyway.** Between
2026-09-05 and 2026-09-07 there was no server-side gate at all: the repo was
`private: true` on a Free plan, so the protection endpoint returned 403
*"Upgrade to GitHub Pro or make this repository public"*. That is why a CI
outage the night of 2026-09-05 left a PR at `mergeStateStatus: CLEAN` with zero
checks having run. Protection now exists again, but **`mergeStateStatus` is
still not evidence** and the merging session still reads
`commits/<sha>/status` itself and confirms each of the six contexts is
`success` for the PR head — the same instruction as the interim practice, kept
because the enforcement it depends on has now silently disappeared once.
Re-verify with the `.../branches/main/protection` call above rather than
assuming this paragraph is current; it was wrong for two days without anyone
noticing, and a session's own copy of this file is a snapshot from its start.

## Git history

Meaningful checkpoint commits — not one giant final commit, not trivial ones;
messages explain *why* in this repo's existing terse tone. No `--force` to
`main`, no `--no-verify`, never `--amend` pushed history, stage specific paths
(never `git add -A` — guard_workflow.py's GIT_ADD_ALL_BLOCKED rule denies it).

## Ending a session

Before `/clear`, a long `/compact`, or stopping for the day: `/checkpoint`
(commit verified units, push, confirm CI); name the branch and any open PR
in the closing message; state the last completed unit, the first incomplete
one, and anything uncommitted or unverified. The procedure below
reconstructs from the repository, so nothing that matters may live only in
chat.

## Resuming in a fresh session

On "resume" / "continue" / "pick up where we left off": read
`docs/next-action.md` first and do exactly what it says - it names the single
next action and is printed in every session banner by `orient.sh`. It is the
answer to "continue"; everything below is for reconstructing context around it,
not for choosing different work. Do not rely on conversational memory. Read `CLAUDE.md` and `.claude/rules/*.md`; inspect
`git status --short`, `git branch --show-current`, `git log --oneline -20`,
`git log origin/main..HEAD --oneline`, `ROADMAP.md`, the relevant module
`README.md` (`CHEATSHEET.md` for the Kalshi-boundary packages), and the active
plan under `docs/superpowers/plans/`. Determine the last completed step and the
next one from that evidence; resume the existing branch (`git checkout
<branch>`, or its worktree), never a replacement; state what was completed,
what remains, and what is being resumed, then continue.
