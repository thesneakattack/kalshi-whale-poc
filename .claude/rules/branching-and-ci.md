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
  advances on one pass" adversarial-review requirement (that still needs its
  own fresh, memory-less Agent call regardless of what a peer says): proceed
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
  no *human* approval step is needed here, but the AI-executed
  self-review/adversarial-review/consolidation cycle still runs before
  `gh pr merge` — that is rigor, not approval ceremony.

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
