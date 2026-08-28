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
live session occupies — `guard_workflow.py` R6 denies it and `orient.sh` lists
the live sessions at session start.

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
- `gh pr create` once pushed; read the PR body before merging;
  `gh pr merge --merge` (keeps individual commits so `git log`/`blame` stay
  real history); delete the branch locally and remotely —
  `scripts/cleanup-worktrees.sh` does both for provably merged worktrees.
- Single-developer repo: no self-approval ceremony, just the mechanical
  guarantees — no routine work on `main`, no force-push, CI before
  integration, reviewable diffs, safe merges.

**GitHub-side enforcement (configured 2026-08-25):**
`gh api repos/thesneakattack/kalshi-whale-poc/branches/main/protection` —
`enforce_admins: true`, `allow_force_pushes: false`, `allow_deletions: false`,
`required_pull_request_reviews: null`, and `required_status_checks.contexts` =
`ci/woodpecker/pr/tests-pytest`, `.../tests-dependency-audit`,
`.../quality-architecture-audit`, `.../quality-browser-e2e`,
`.../kalshi-contract-fixtures`. Deliberately excludes
`ci/woodpecker/pr/quality-frontend-build`: it is path-filtered to `frontend/**`
and posts no status when skipped, so requiring it would block every
non-frontend PR. Update with `gh api -X PUT .../protection --input <file>`
when a required pipeline is added.

## Git history

Meaningful checkpoint commits — not one giant final commit, not trivial ones;
messages explain *why* in this repo's existing terse tone. No `--force` to
`main`, no `--no-verify`, never `--amend` pushed history, stage specific paths
(never `git add -A` — R8 denies it).

## Ending a session

Before `/clear`, a long `/compact`, or stopping for the day: `/checkpoint`
(commit verified units, push, confirm CI); name the branch and any open PR
in the closing message; state the last completed unit, the first incomplete
one, and anything uncommitted or unverified. The procedure below
reconstructs from the repository, so nothing that matters may live only in
chat.

## Resuming in a fresh session

On "resume" / "continue" / "pick up where we left off": do not rely on
conversational memory. Read `CLAUDE.md` and `.claude/rules/*.md`; inspect
`git status --short`, `git branch --show-current`, `git log --oneline -20`,
`git log origin/main..HEAD --oneline`, `ROADMAP.md`, the relevant module
`README.md` (`CHEATSHEET.md` for the Kalshi-boundary packages), and the active
plan under `docs/superpowers/plans/`. Determine the last completed step and the
next one from that evidence; resume the existing branch (`git checkout
<branch>`, or its worktree), never a replacement; state what was completed,
what remains, and what is being resumed, then continue.
