# Multi-session crash recovery — coordination-specific gaps

This is a **companion** to `docs/SESSION_CRASH_RECOVERY.md`, not a replacement
or an extension of it. That file already answers, well, "how does a
replacement session reconstruct what shipped and resume it" — PR/branch/issue
state, from git and `gh`, deliberately with no roster because a roster goes
stale within the hour. Read it first; this document does not repeat it.

What that file does not cover, and what this one is for: the parts of running
several sessions/subagents at once that are not "what landed on `main`" —
subagent provenance, telling a live peer from an irrelevant one, telling a
renamed session from a dead one, the fact that `docs/next-action.md` is a
full-overwrite snapshot with no prompt to check its own history, work outside
this repo with no git-backed record at all, and credentials that end up on
disk mid-diagnosis. These six gaps were each hit for real in one session
(2026-09-03/04); the write-up below generalizes each into a standing
procedure, not a record of that night. Where a concrete example from that
night is used, it is marked as an example — the procedure is meant to
outlive it.

**Why a separate document instead of folding this into
`SESSION_CRASH_RECOVERY.md`:** that file's whole design philosophy is "derive
current state from sources that cannot go stale: git, the GitHub API, and the
live app" — a roster was tried once, went stale in an hour, and was removed
by name. Four of the six gaps below (1, 2, 3, 6) do not have a git/`gh`
source of truth to derive from at all today; the proposals here are about
*creating* one (marker commits, an explicit disclosure convention, a probe
protocol) without reintroducing a roster file that goes stale the same way.
Bolting "and also, here's a convention for inventing new stateless-derivable
signals" onto a file whose stated design is "we deliberately store nothing"
would blur what is a proven, load-bearing design decision and what is a
newer, less-proven one. Keeping them separate lets each document be
re-litigated independently later without touching the other.

**When to read which:** `SESSION_CRASH_RECOVERY.md` first, always — it is
the entry point and covers the common case (a session died, find its work,
resume it). Come to this document when that isn't enough: a subagent (not a
peer session) is unaccounted for, a listed `ListAgents` session's relevance
is unclear, a session's identity across a resume is in doubt, `next-action.md`
looks like it might be missing something a prior version had, out-of-repo
infrastructure was being touched, or credentials may be sitting on disk.

---

## 1. Subagent provenance: was one working on this, did it finish, where's the output

**The gap.** A background subagent (`Agent` tool call, especially
`isolation: "worktree"`) dies the instant the session that spawned it dies —
it has no independent process, no independent trace, beyond whatever it
already committed. If it was mid-task when the parent died, its reasoning,
partial findings, and anything it hadn't yet written to a committed file are
gone. `git worktree list` shows every worktree that exists, but not which
ones are subagent-owned, which are a peer session's own workspace, and which
are simply abandoned from hours or days earlier — a real listing checked
while writing this document had **38 worktree entries**, most stale from
earlier work, with no annotation distinguishing any of the three.

**A real, checkable distinguishing signal that already exists.** Verified
live against this repo's own worktrees while writing this document (not
assumed): an `Agent` tool dispatch with `isolation: "worktree"` creates its
worktree directory under `.claude/worktrees/agent-a<hex>` and starts it in
**detached HEAD** — e.g. `.claude/worktrees/agent-a433cd54d5b2c9ff3` at
`882bb2b (detached HEAD)`. If that subagent later creates and commits to a
named branch, `git worktree list` then shows that branch name instead (one
observed case: `agent-a1762732e0d9f6faf` shows
`docs/worker-cpu-pin-and-loop-stalls-investigation`, not detached HEAD, because
the dispatched agent had by then committed to that branch and it was checked
out). So:

- `.claude/worktrees/agent-a*` naming **and/or** a detached-HEAD state is
  today's signal for "this was an `Agent`-tool worktree dispatch," distinct
  from a human-chosen worktree name (`fix/...`, `docs/...`,
  `crash-recovery-plan`, etc.) that a coordinator or peer session created by
  hand for their own work.
- This is a naming *convention*, not an enforced invariant — nothing stops a
  differently-configured dispatch or a manually created worktree from not
  following it. Treat it as a strong prior, verify with the branch/commit
  content before relying on it for anything destructive (e.g. before
  deciding a worktree is safe to remove).

**The proposal: a first-action marker commit.** The naming convention answers
"was this an agent-tool worktree" but not "what was it told to do" or
"did it finish." Fix that at the source: **the first action inside any
dispatched subagent's own worktree, before any real work, is a commit** —

```
git commit --allow-empty -m "subagent: started — scope: <one-line task>, dispatched by: <coordinating session name/branch>, at: <ISO8601 UTC>"
```

(`--allow-empty` because the very first action, by definition, precedes any
file change.) This survives even if the subagent is killed one tool-call
later: `git -C <worktree> log --oneline` on a `agent-a*` worktree with only
that one marker commit and no branch checked out tells a resuming session,
with certainty, "a subagent was here, this is what it was told to do, and it
got no further" — the exact "did it finish" question the current setup
cannot answer today. A subagent that completes normally does not need a
matching "done" marker commit — its real commits and, for anything with a
review-cycle obligation, its own written artifact (self-review, findings
doc) already say that with more detail than a marker could. The marker's job
is only to cover the gap between "worktree exists" and "first real commit
exists," which is exactly the window a mid-task kill leaves empty today.

This is a convention, not a hook-enforced rule — there is no clean place to
enforce "first commit in a fresh worktree must match this shape" without
adding friction to every dispatch, and CLAUDE.md's standing preference is
prove-value-before-automating for new tooling. Adopt it by habit in the
`Agent` tool prompt for `isolation: "worktree"` dispatches; revisit whether
it needs enforcement only if it turns out to be skipped often enough to be
useless in practice.

**Recovery procedure for a resuming session:**

```bash
git worktree list                                   # every worktree, as today
# For each entry whose path matches .claude/worktrees/agent-a* (or that
# shows "(detached HEAD)"): read its own log to find the marker + any real
# work, without guessing from the directory name alone.
for w in .claude/worktrees/agent-a*; do
  echo "=== $w ==="
  git -C "$w" log --oneline -10
  git -C "$w" log -1 --format='%H %cI' 2>/dev/null   # last-commit timestamp: how stale
done
```

A worktree with only a marker commit (or no marker and no commits at all,
predating this convention) and nothing pushed anywhere is dead weight —
recover nothing, remove it per `SESSION_CRASH_RECOVERY.md` §3's worktree-
cleanup note (issue #513: every worktree costs the reload watcher a stat
call per file, every poll cycle). A worktree with a marker plus real commits
not yet on any pushed branch is exactly `SESSION_CRASH_RECOVERY.md` §2 case
2/3 (committed-but-unpushed, or uncommitted) — push or commit it before
touching anything else, same as a died peer session's work.

---

## 2. Peer relevance triage: is this listed session even mine

**The gap.** `ListAgents` is a machine-wide listing, not a repo-scoped one.
A real case: it returned a session (`portfolio-37`) that was working in a
sibling project (`~/code/portfolio/`, a different repo entirely, not
`autotrade`) — establishing that took a full message round-trip
("what are you working on?" / reply) before it could be ruled irrelevant.
At coordination scale (several peers, each potentially checking several
others), a full round-trip per listed session that turns out to be
unrelated is real, avoidable overhead.

**Honest answer: there is no confirmed zero-cost filter today.** This
document was written from inside a dispatched subagent, which does not have
`ListAgents` in its own tool set (only an interactive/main-loop session
holds peer visibility) — so the exact fields `ListAgents` returns for a main
session could not be directly re-verified while writing this. Do not assume
it carries a `cwd` or repo identifier field without checking live; if it
does, that becomes the free filter this section wants and this document
should be corrected to say so plainly, with an example, the next time
someone confirms it. Until confirmed:

- **The round-trip check stays the only reliable method** — this is a
  documented limitation, not a solved problem. Ask directly: "are you
  working in `<this repo>`? one line is enough." A session with nothing to
  do with `autotrade` can and should answer that in a single short message,
  which keeps the actual cost of the check low even without a zero-message
  filter.
- **Make the first message double as the filter for next time.** If a
  session's name or its first reply already states its repo/branch (many
  peer sessions in this repo announce their branch when they introduce
  themselves), record that pairing for the rest of *this* recovery episode
  only — not as a new persistent roster file (that is the exact failure
  mode `SESSION_CRASH_RECOVERY.md` was rewritten to remove), just as
  in-conversation memory for the current resume, since a second round-trip
  to the same peer in the same episode is pure waste.
- **A cheap pre-filter that does exist today:** a session name that matches
  this repo's convention (`autotrade-XX`, two hex/alnum chars) is a weak
  prior for relevance — not proof (a stale or coincidentally-named session
  could still exist), but worth checking before a same-named-but-differently-
  prefixed session (`portfolio-NN`) that is very likely a different repo's
  session entirely, going by the one confirmed real case above.

---

## 3. Renamed session vs. a dead one replaced by a stranger

**The gap.** A live session's `ListAgents` display name can change across a
resume while its conversational continuity (memory, in-flight task, context)
stays fully intact — observed at least twice in one night
(`autotrade-ce`→`autotrade-6c`, `autotrade-84`→`-96`→`-f2`). From outside,
watching only the name change, this is indistinguishable from "the old
session died and a different, unrelated session took its slot" — exactly
the ambiguity `SESSION_CRASH_RECOVERY.md` §5 assumes gets resolved before a
replacement coordinator self-appoints.

**The proposal: never infer from the name alone — probe, then corroborate.**
When a previously-known session name is gone and a new one has appeared
(or a known name's behavior suddenly seems unfamiliar), ask it directly, in
one message: *"What's the last unit of work you completed, and what are you
doing right now?"* Then check the answer against what is independently
knowable, not against what the session itself claims to be:

- Does the named "last completed unit" correspond to a real, findable
  artifact — a commit, a pushed branch, a merged PR, a doc under
  `docs/superpowers/`? (`git log`, `gh pr list`, `gh pr view <n>`.)
  A continuation answers with something that checks out; a stranger session
  either can't answer specifically or answers with something that doesn't
  match anything in the repo's actual recent history.
- Is it currently occupying the same worktree/branch the old name was last
  known to hold? Two genuinely distinct sessions independently ending up in
  the identical worktree at the identical branch is very unlikely — shared
  worktree+branch occupancy across the name change is strong (not certain)
  corroboration of continuity, on top of the content match above.
- Does its account of "what I'm doing right now" match a task that was
  actually in flight (from `docs/next-action.md`, an open PR with its
  branch, or a peer's own prior report)? A stranger session doing unrelated
  work in the same repo would name a different task or none at all.

None of these three alone is proof; agreement across at least two of them
(especially the artifact check plus one of the other two) is the bar for
"treat this as the same session, continued" rather than "treat this as an
unknown session and apply `SESSION_CRASH_RECOVERY.md` from scratch." When
genuinely unresolved after one probe, don't guess either way — say so to the
user rather than silently picking an assumption, same as any other
unresolved claim under the repo's "never guess" HARD RULE.

---

## 4. `docs/next-action.md` loses prior detail on every rewrite

**The gap.** The file is a full-overwrite snapshot by design (its own footer
says superseded snapshots are deliberately not kept in the file, because
`orient.sh` prints the whole thing into every session banner and an
ever-growing file would defeat that). It was fully rewritten at least twice
in one night — once for a mid-session policy/priority update, once at a hard
pause. `git log -p --follow docs/next-action.md` recovers every prior
version, but nothing prompts a resuming session to look at it; the natural
default is to read the current file and trust it as complete, because that
*is* the file's stated design.

**This document does not propose changing that design.** Making
`next-action.md` an append-only log or a task tracker was already
considered and rejected in the file's own history (see its footer and
`CLAUDE.md`'s "Do not read a task list out of this file" language) — a
growing status log is exactly the staleness failure mode the rest of this
repo's crash-recovery design avoids elsewhere. The fix here is procedural,
on the *reading* side, not the writing side.

**When to diff against history, and when not to.** A clean end-of-session
rewrite (the normal case: a session finished its work, wrote a fresh,
comprehensive snapshot, stopped) is comprehensive by design — reading only
the current file is correct and sufficient, and diffing it against history
adds cost for no signal.

Diff when either is true:

- **The current file's own content signals a mid-episode rewrite**, not an
  end-of-session one — phrasing like "second incident today," "supersedes
  the priority stated earlier this session," a standing-priority paragraph
  with a timestamp far more recent than the rest of the file's "reference
  state" section, or a live-incident narrative still in progress. These are
  the shape of a pause-and-rewrite mid-crisis, which is more likely to have
  dropped something the previous version had room to say.
- **The resuming session is specifically recovering from a crash that
  happened *during* a rewrite** — i.e., this document's own scenario. A
  session that died while composing the file may have left a genuinely
  incomplete final version (or, if the rewrite hadn't been committed yet,
  `git status --short docs/next-action.md` shows it dirty and the on-disk
  content is not even the last *committed* state — treat the last commit,
  via `git log -p -1 --follow docs/next-action.md`, as the trustworthy
  baseline in that case, and any uncommitted on-disk delta as an unverified,
  possibly-partial draft to read skeptically, not as fact).

The check itself is cheap regardless: `git log -p -2 --follow
docs/next-action.md` shows the current version and the one immediately
before it. **What's lost by skipping it in the normal case:** nothing,
by the file's own design — that's the entire point of full-overwrite over
append-log. **What's lost by skipping it in the two flagged cases above:**
whatever the previous version said that the rushed rewrite didn't have time
to restate — which, by definition, a resuming session has no way to know it
is missing unless it looks.

---

## 5. Out-of-repo infrastructure work has no crash-recovery coverage

**The gap.** Everything above — and all of `SESSION_CRASH_RECOVERY.md` —
assumes the work in question lives in *this* git repo, where a stranded or
partial state is at minimum discoverable through `git status`/`git log`/`gh`.
Infrastructure work outside this repo (the concrete case: a live CI server
fix in `~/code/portfolio/ci-cd/`, done via direct `docker-compose` edits and
a direct SQLite write against its database) has none of that scaffolding.
If a session doing that kind of work is interrupted mid-flight — a container
stopped to allow a direct DB write, then the session crashes before
restarting it — the live, production-adjacent service is left in a partial
state with **no document anywhere** describing how to detect that or how to
recover.

**A concrete illustration, found while writing this document, not
manufactured:** `~/code/portfolio/ci-cd/` is a git repository, tracking
`docker-compose.yml` and `.env` (no database files under version control, as
expected — they hold live/derived state). At the moment this was checked,
`git -C ~/code/portfolio/ci-cd status` showed **uncommitted changes to both
`.env` and `docker-compose.yml`** — this is exactly the ambiguous shape the
gap describes: on its own, this diff does not say whether it's someone's
completed-but-not-yet-committed change, a change mid-edit, or a leftover
from an interrupted session; nothing in that repo distinguishes those cases
today. This observation is not a claim about *whose* change it is or why —
only that the ambiguity is real and present right now, not hypothetical.

**Proposal — before starting a risky step outside this repo** (stopping or
restarting a live shared service, or mutating its database directly):

1. **State it, explicitly, before acting** — in the session's own report/
   chat output: which service, what specifically is about to change, and
   why. This alone doesn't survive a crash on its own, but it means a user
   reading a partial transcript has the "intent" half of the picture even
   if the "did it finish" half is missing.
2. **If mutating a database file directly, copy it first**, timestamped,
   before writing — the same shape this repo already uses for its own
   `data/quarantine/` convention (`cp live.db live.db.pre-mutation-<ISO8601>`
   before touching the original). This makes "what did it look like right
   before" recoverable regardless of what happens next, without needing any
   new tooling.
3. **Write a plain-text marker before starting**, in the target repo if it
   is one (confirmed true for `ci-cd/` — a small tracked or even untracked
   file works, since its mere presence/absence and mtime are the signal,
   not its git history) or in this repo's own scratch space otherwise. One
   line: what's about to change, since when, expected to finish by roughly
   when. Update or remove it on completion. An abandoned marker with a
   stale timestamp is precisely the "in flight" signal that's missing today.
4. **If the target has its own git repo (true for `ci-cd/`), commit the
   fix there too**, ordinary commit, once done — mirroring this repo's own
   "git is truth" default rather than leaving the change to sit
   uncommitted indefinitely the way the observed `.env`/`docker-compose.yml`
   diff currently does.
5. **Restart/verify the service and confirm it's actually back**, the same
   "verify a deploy, don't assume a merge/restart landed" discipline
   `docs/next-action.md` already applies to *this* repo's own ddev restarts
   — check the process/container is actually up and serving, not just that
   the restart command returned 0.

**Resuming session's first checks, for "is some out-of-repo shared
infrastructure mid-mutation right now":**

```bash
# 1. Marker file, once the convention above is adopted:
find ~/code/portfolio/ci-cd -maxdepth 2 -iname "*.inflight" -o -iname "*IN_PROGRESS*"

# 2. Uncommitted state in the target repo, whether or not a marker exists —
#    the only signal available today, before the convention is adopted:
git -C ~/code/portfolio/ci-cd status --short

# 3. Is the service actually running as expected right now?
docker compose -f ~/code/portfolio/ci-cd/docker-compose.yml ps

# 4. Any recent restore/backup file that implies a stopped-for-write window:
find ~/code/portfolio/ci-cd -maxdepth 2 -newermt '-6 hours' -type f
```

Until the marker convention is actually adopted, step 1 will find nothing —
that is not evidence of "nothing was in flight," only evidence the
convention hasn't been used yet. Steps 2-4 are the only real signal
available retroactively, and none of them is conclusive alone; treat an
uncommitted diff plus a service that isn't in its expected state as reason
to ask before assuming either "safe to leave" or "safe to touch."

---

## 6. Live credential exposure during infra work

**The gap.** During the same class of out-of-repo diagnostic work, a
session generated and locally cached live API tokens/secrets in scratchpad
files while diagnosing the CI outage. A cleanup step for those files was
denied by a permission prompt, and the coordinating session had no way,
from outside, to confirm whether the files still existed afterward.

**Standing rule: disclosure is not optional, and it's explicit, not
implied.** Any session that mints or derives a live credential into a local
file states that fact plainly in its own report — the exact path, and what
the file contains (a token, a full `.env`, a cookie jar — enough for the
next reader to know what they're looking for without opening it
unnecessarily). "I looked into the CI issue" is not sufficient disclosure if
a token got written to disk along the way; "wrote a temporary token to
`/tmp/claude-.../scratchpad/ci_token.txt` while diagnosing, not yet cleaned
up" is.

**Sweep for a resuming/crash-recovery session — concrete, not "check for
secrets":**

```bash
# This session's own scratchpad tree, and every sibling session's scratchpad
# for this project (each dispatched agent/session gets its own subdirectory
# under this same project-scoped prefix):
find /tmp/claude-*/-home-davidf-code-portfolio-showcase-projects-autotrade \
  -maxdepth 3 -type d -iname "scratchpad" 2>/dev/null

# Inside each, files whose name suggests a credential:
find /tmp/claude-*/-home-davidf-code-portfolio-showcase-projects-autotrade \
  -type f \( -iname "*token*" -o -iname "*secret*" -o -iname "*credential*" \
             -o -iname "*.pem" -o -iname "*cookie*" -o -iname "*.env" \
             -o -iname "*apikey*" -o -iname "*api_key*" \) 2>/dev/null

# Content-based check inside scratchpad/worktree text files, in case the
# filename doesn't give it away (skip binaries, cap match context):
grep -rlIE '(api[_-]?key|token|secret|password|Authorization: ?Bearer)[[:space:]]*[:=]' \
  /tmp/claude-*/-home-davidf-code-portfolio-showcase-projects-autotrade \
  .claude/worktrees 2>/dev/null

# Anything committed by accident (git catches this better than grep for
# tracked files — check staged/tracked content specifically, not just the
# working tree):
git -C ~/code/portfolio/showcase-projects/autotrade log --all -p \
  -- '*.env' '*token*' '*secret*' 2>/dev/null | head -100
```

A hit is not automatically a live incident — most will be legitimate
scratch use already covered by CLAUDE.local.md's normal handling. Treat a
hit as: confirm what it is, confirm whether it's still live (a token that's
already expired or been rotated is lower urgency than one that isn't),
and if it's a real live secret sitting in a readable file, revoke/rotate it
and remove the file — don't leave "found it" as the end state.

---

## Standard ground (brief — `SESSION_CRASH_RECOVERY.md` already covers the depth here)

- **Reconstructing PR/merge state from `gh`:** `SESSION_CRASH_RECOVERY.md`
  §1-§2 in full; not re-derived here.
- **Reconstructing the standing priority/goal when `docs/next-action.md`
  might be mid-rewrite or stale:** cross-check three sources, not one —
  `docs/next-action.md` (current, and per §4 above, its immediate git
  predecessor when the mid-rewrite signals apply), `CLAUDE.md`'s own dated
  "Standing goal"/"current objective" header (changes rarely, and is the
  right tie-breaker when `next-action.md` looks internally inconsistent),
  and `docs/open-decisions.md` (parked decisions — a standing priority that
  contradicts an open, undecided line there is itself a signal something is
  stale). Agreement across at least `next-action.md` and `CLAUDE.md` is the
  bar for treating a priority as current; a lone claim in only one of the
  three, especially `next-action.md` alone if it shows the mid-rewrite signs
  from §4, is treated as an unverified assumption until corroborated.
- **Role reassignment — the coordinator died:** `SESSION_CRASH_RECOVERY.md`
  §5's rule stands unchanged — stop, tell the user, do not self-appoint,
  forward peer check-ins rather than acting on them as approval. This
  document adds the other side of that same situation, which the existing
  file doesn't cover:

  **A worker session that outlives a crashed coordinator.** The standing
  personal protocol for a coordinator/dispatcher session going unresponsive
  is *pause and hand the user a full recap rather than improvising
  autonomously* — this is that protocol applied specifically to the
  crash-recovery case:

  1. **Keep working on the already-scoped, already-in-flight task** up to
     its next natural checkpoint (a verified unit: tests pass, the work is
     internally consistent) — abandoning genuinely in-flight, already-
     authorized work because a coordinator went quiet is wasteful, and nothing
     about a missing coordinator makes already-scoped implementation work
     newly risky. This is explicitly not new decision-making: no new task
     selection, no priority calls, no PR merges, no scope changes.
  2. **At that natural checkpoint, commit and push it** (the same
     `/checkpoint` shape as any other pause) — this is what makes the work
     recoverable by `SESSION_CRASH_RECOVERY.md` §2 regardless of what
     happens to this worker session next.
  3. **Attempt to reach the coordinator** (`ListAgents` + `SendMessage`,
     or, per §3 above, a probe if it's plausibly the same session under a
     new name). One unanswered ping is not evidence of a crash — sessions
     go idle for ordinary reasons. Try again at the *next* natural
     checkpoint rather than polling.
  4. **After a second consecutive unanswered ping spanning two genuine
     checkpoints (not two arbitrary clock ticks)**, stop opening new scope,
     and pause: hand the user a full recap — what shipped, what's
     mid-flight, what's blocked, exactly like a session ending itself would
     under `.claude/rules/branching-and-ci.md`'s "Ending a session" section
     — rather than continuing to invent next steps alone or self-appointing
     as the new coordinator. This mirrors, and does not relax, the standing
     rule that a worker never merges, signs off, or reassigns roles on its
     own judgment even once it suspects the coordinator is gone.

---

## Maintenance

This document is a companion procedure, not a plan with tasks to close out —
there's no "done" state to mark. Revise a section when its proposal turns
out wrong or insufficient in practice (the way `SESSION_CRASH_RECOVERY.md`
itself was rewritten once already, after its first version's roster
approach failed); add a new section only for a genuinely new class of gap,
not a restatement of one already here. If §1's marker-commit convention or
§5/§6's disclosure conventions accumulate real run history, that's the
trigger — per `CLAUDE.md`'s own toolchain standard — for deciding whether
either deserves actual enforcement (a hook, a CI check) instead of staying
convention-only.
