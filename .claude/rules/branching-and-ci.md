# Branching and CI — standing repository policy

Direct standing instruction (2026-08-25). This is the single authoritative
source for git-branch and CI-verification behavior in this repository.
Other files (`CLAUDE.md`, skills, hooks) point here rather than restating
this policy — reconcile any conflicting example or assumption against
this file, not the other way around.

## Branch model

`main` is the authoritative integrated branch. Normal implementation work
does not happen directly on `main`.

This repo uses `main` + short-lived initiative branches only. Do not
create permanent `development`/`develop`/`staging`/environment branches
unless the user explicitly changes this policy later.

Before implementation:

1. `git branch --show-current` — check the active branch.
2. Already on an appropriate initiative branch for this work → continue
   using it.
3. On `main` → confirm it's synchronized with `origin/main`
   (`git fetch origin main --quiet && git log origin/main..HEAD --oneline`
   should be empty), then create a short-lived branch:
   `git checkout -b <prefix>/<name>`.

Prefixes: `feat/`, `fix/`, `refactor/`, `chore/`, `docs/`.

One coherent initiative gets one branch — not one branch per tiny edit.
Work that's tightly coupled and should review/merge atomically stays
together on one branch; work that's independently testable and
independently mergeable splits across branches. A numbered multi-task
initiative (Quality Control Plane tasks, Kalshi Integration Phase A/C
tasks) normally stays on one branch for the initiative, with each task's
usual one-task-one-commit discipline unchanged — the branch changes where
commits land, not the per-task commit/verify/stop rhythm those skills
already follow.

## CI ownership

Claude owns targeted local verification. Woodpecker owns exhaustive
verification. This applies on every branch, not just `main` —
`.woodpecker/*.yml`'s `when: event: [push, pull_request]` matches a push
to any branch.

Before pushing, run only what's cheap and targeted: syntax checks, scoped
lint/static analysis on changed files, the directly relevant unit/
regression test(s), inexpensive integration/wiring checks (router
mounted, scheduler called, persistence registered, config consumed). Do
not routinely run the full backend suite, frontend lint/build, browser
E2E, architecture audit, or dependency audit locally just to justify a
push — that's what Woodpecker is for.

`.claude/skills/ci-cd-guardrails/SKILL.md`'s "Exceptions" section owns
the full, detailed list of when exhaustive local verification is still
appropriate anyway (Woodpecker unavailable, reproducing a CI-only
failure, touching CI itself, a deliberate integration checkpoint, the
user asking for it, repository safety demanding it) — not duplicated
here. That same file owns the detail of *how* to
check Woodpecker's result (the token-free `gh api
repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status` check) and
*how* to design/scope a new deterministic guard — this file doesn't
duplicate that, it only establishes that the split exists and applies
per-branch.

## Integration lifecycle

```
main → initiative branch → implementation → targeted local checks →
commit → push → Woodpecker → PR/review → merge to main → delete branch
```

After pushing, confirm Woodpecker's actual result before treating a
branch as ready to merge or a change as fully verified — do not assume
green. Use the `gh api .../commits/<sha>/status` check referenced above.
On failure: inspect the failing job and its log, identify the actual
failure, fix it, verify narrowly, push again. Never weaken or bypass a
legitimate CI guard merely to force a green build.

Open a PR (`gh pr create`) once the branch is pushed and green (or to let
CI run against the PR — either order is fine). This is a single-developer
repository — skip unnecessary self-approval ceremony. Once CI is green
and the diff has been reviewed, merge
(`gh pr merge --merge`, which preserves the branch's individual commits
rather than squashing them, so `git log`/`git blame` retain real history
— see "Git history" below). Delete the branch after merge — locally
(`git branch -d <branch>`) and remotely (`gh pr merge --delete-branch`,
or `git push origin --delete <branch>` if the branch survives the merge)
— short-lived branches should not accumulate.

The important protections this policy exists for: no accidental routine
development directly on `main`; no destructive force-push; CI validation
before integration; reviewable diffs; safe merge behavior. Not process
ceremony for its own sake — a single-developer repo doesn't need a
second approver, just these mechanical guarantees.

## Git history

Meaningful checkpoint commits, not one giant final commit and not
excessive trivial ones. Commit messages explain *why* / architectural
intent when that context will matter later — this repo's existing
`git log` is the working example of the tone to match. `git log` /
`git show` / `git blame` / `git diff` should be enough on their own to
reconstruct how the system evolved, without depending on chat history.

Standard git safety already applies globally and is unchanged by this
policy: no `--force` push to `main`, no `--no-verify`/`--no-gpg-sign`,
never `--amend` already-pushed history, stage specific paths rather than
`git add -A`/`git add .`, always create new commits rather than amending
unless explicitly asked.

## Session-start behavior

`.claude/hooks/session_orient.sh` prints the active branch on every
session start and after every compaction. When it reports `main`, treat
that as the cue to create an initiative branch before starting
implementation work — not a green light to implement there. This is
deliberately mechanical (a hook reading repository state, not a rule that
has to be recalled from conversation) so it holds in a session that has
no memory of when or why this policy was adopted.

## Superseded instructions

This rule supersedes any earlier assumption — in `CLAUDE.md`, a
`.claude/skills/*/SKILL.md` file, or elsewhere — that implementation
commits land directly on `main`, or that Claude should routinely run the
full verification suite locally before an ordinary push. Where an older
doc's wording or example still shows a direct-to-`main` flow or a
"run everything before pushing" habit, this file is authoritative; treat
the older text as stale prose to be read in light of this policy, not as
a genuinely conflicting rule that needs case-by-case litigation.

Safety-critical rules this policy does not touch and does not weaken:
real Kalshi trading gates, `trading_enabled`, the daily-loss kill switch,
CORS restrictions, `data/*.db` test isolation and historical-data
preservation, and every rule in `CLAUDE.md`'s "Safety invariants" section
and `.claude/rules/kalshi-integration-authority.md`.
