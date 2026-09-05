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
this repo with no git-backed record at all, credentials that end up on disk
mid-diagnosis, an inherited artifact confidently asserting something false
about you, an in-session subagent dying with no isolation to leave a trace
in, and a mass simultaneous restart rather than one session's death. The
first six were hit for real in one session (2026-09-03/04); the last three
were added after this document's own review cycle turned up real, undated
instances of each the very same night, including during a real restart that
happened while this document was still under review. The write-up below
generalizes each into a standing procedure, not a record of any one night.
Where a concrete example is used, it is marked as an example — the
procedure is meant to outlive it.

**Why a separate document instead of folding this into
`SESSION_CRASH_RECOVERY.md`:** that file's whole design philosophy is "derive
current state from sources that cannot go stale: git, the GitHub API, and the
live app" — a roster was tried once, went stale in an hour, and was removed
by name. Several of the gaps below (notably §§1-3 and §9) do not have a
git/`gh` source of truth to derive from at all today; the proposals here are
about *creating* one (marker commits/comments, an explicit disclosure
convention, a probe protocol, a live socket/cwd check) without
reintroducing a roster file that goes stale the same way.
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
infrastructure was being touched, credentials may be sitting on disk, an
inherited artifact claims something about you that doesn't match your own
state, an in-session (non-worktree) subagent went quiet mid-task, or
everything — every session, every container — went down at once rather than
one session dying among survivors.

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
while writing this document had **38 worktree entries** (a stale count even
one day later — 46, then 47 — so treat the number itself as a dated
snapshot, not a current fact), most stale from earlier work, with no
annotation distinguishing any of the three.

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

- `.claude/worktrees/agent-a*` naming is today's signal for "this was an
  `Agent`-tool worktree dispatch," distinct from a human-chosen worktree name
  (`fix/...`, `docs/...`, `crash-recovery-plan`, etc.) that a coordinator or
  peer session created by hand for their own work. Detached HEAD alone is
  **not** a reliable second signal: human-made worktrees can be detached too
  (`main-tools`, `rev569-scratch` both are, verified live) — go by the name.
- This is a naming *convention*, not an enforced invariant — nothing stops a
  differently-configured dispatch or a manually created worktree from not
  following it. Treat it as a strong prior, verify with the branch/commit
  content before relying on it for anything destructive (e.g. before
  deciding a worktree is safe to remove).

**The proposal: a first-action marker commit — but not a bare detached one.**
The naming convention answers "was this an agent-tool worktree" but not
"what was it told to do" or "did it finish." A marker commit on the
worktree's own detached HEAD does not actually fix this, though: once that
worktree is removed, the commit is unreachable from any ref and gone for
good — and `scripts/cleanup-worktrees.sh` explicitly refuses to auto-remove
a detached-HEAD worktree in the first place ("keeping: … detached HEAD - no
branch, so no PR state to check; remove it by hand once you know it is
finished"), so the marker's only real audience is a worktree nobody has a
prescribed reason to keep, and a stale one that just gets removed by hand
takes the marker with it. Put the marker somewhere that survives worktree
removal instead:

- **If the dispatch creates a branch at all** (most do, once real work
  starts): a first commit on that branch — `git commit --allow-empty -m
  "subagent: started — scope: <one-line task>, dispatched by: <coordinating
  session name/branch>, at: <ISO8601 UTC>"` — survives independently of the
  worktree via the branch ref.
- **If it's expected to stay detached throughout** (a short, read-only
  probe): post the same one-line "started" note as a comment on the PR or
  issue the dispatch is in service of, instead of a commit. A PR/issue
  comment survives worktree removal by construction and needs no ref at
  all.

A subagent that completes normally does not need a matching "done" marker —
its real commits and, for anything with a review-cycle obligation, its own
written artifact (self-review, findings doc) already say that with more
detail than a marker could. The marker's job is only to cover the gap
between "worktree exists" and "first real commit/comment exists," which is
exactly the window a mid-task kill leaves empty today.

This is a convention, not a hook-enforced rule — there is no clean place to
enforce "first commit in a fresh worktree must match this shape" without
adding friction to every dispatch, and CLAUDE.md's standing preference is
prove-value-before-automating for new tooling. Adopt it by habit in the
`Agent` tool prompt for `isolation: "worktree"` dispatches; revisit whether
it needs enforcement only if it turns out to be skipped often enough to be
useless in practice.

**Recovery procedure for a resuming session.** Derive paths from `git
worktree list --porcelain` rather than a relative glob — `.claude/worktrees/
agent-a*` does not exist at all from inside a linked worktree (only the
primary checkout holds them), and a bare `for w in .claude/worktrees/
agent-a*` hard-errors under this user's zsh when nothing matches, silently
finding zero worktrees instead of the real count:

```bash
git worktree list --porcelain | awk '/^worktree /{print $2}' | while IFS= read -r w; do
  case "$(basename "$w")" in
    agent-a*)
      echo "=== $w ==="
      git -C "$w" log --oneline -10
      git -C "$w" log -1 --format='%H %cI' 2>/dev/null   # last-commit timestamp: how stale
      ;;
  esac
done
```

(`git worktree list --porcelain` always prints absolute paths regardless of
which directory it's run from, so this works whether the resuming session
is in the primary or in some other linked worktree.)

A worktree with only a marker commit (or no marker and no commits at all,
predating this convention) and nothing pushed anywhere is dead weight —
recover nothing, remove it per `SESSION_CRASH_RECOVERY.md` §3's worktree-
cleanup note (issue #513: every worktree costs the reload watcher a stat
call per file, every poll cycle). A worktree with a marker plus real commits
not yet on any pushed branch is exactly `SESSION_CRASH_RECOVERY.md` §2 case
2/3 (committed-but-unpushed, or uncommitted) — before pushing, check it
isn't a stale duplicate of work already integrated under a different SHA
(`git cherry <upstream> <local>` or `git log --all --grep '<the same
message>'`; a real case existed where a subagent's only commit duplicated
one already merged days earlier) — then push or commit it, same as a died
peer session's work.

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

**Corrected: a zero-cost filter already exists in this repo, confirmed live.**
This section originally said no such filter existed, written from inside a
dispatched subagent that lacks `ListAgents`. That conclusion doesn't hold:
`ListAgents` itself still carries no `cwd`/repo field (confirmed live from a
main session — name, `[ref]`, mode, and start-time only), but the repo
already has an independent tool that answers the same question without a
round-trip: `.claude/hooks/guard_workflow.py --sessions` (wired into
`orient.sh`'s own banner since 2026-08-28) walks `/proc/<pid>/cwd` for every
live Claude session's socket in `/run/user/1000/cc-socks/` and prints each
one's working directory:

```
$ python3 .claude/hooks/guard_workflow.py --sessions
5800    other   /home/davidf/code/portfolio
6140    other   /home/davidf/code/portfolio
17771   other   /home/davidf/code/portfolio/showcase-projects/autotrade
19936   other   /home/davidf/code/portfolio/showcase-projects/autotrade/.claude/worktrees/issue-410-impl
```

Zero messages sent, and it gives worktree occupancy for free (useful for §3
below too). **What it doesn't give you:** a `pid` isn't a session *name* —
nothing in `/proc/<pid>/cmdline` carries the `autotrade-XX` label a session
introduces itself with — so this tool answers "how many sessions are in
this repo right now, and how many are foreign" without a round-trip, but
identifying *which listed name* maps to *which pid* still needs a probe (§3).
The two sources are complementary: `ListAgents` names sessions but not their
cwd, `guard_workflow.py --sessions` gives cwd but not names.

- **Use the socket/cwd check first**, before any round-trip, to rule out
  foreign-repo sessions in bulk. Only probe (per §3) the ones that land in
  this repo and whose name↔session mapping is actually in question.
- **A cheap secondary pre-filter:** a session name matching this repo's
  convention (`autotrade-XX`) is a weak prior for relevance even before
  running the tool above — not proof, but worth noting before a
  differently-prefixed session (`portfolio-NN`) that the socket check will
  likely confirm as a different repo's session entirely.
- **This does not reintroduce a persistent roster file.** The failure mode
  `SESSION_CRASH_RECOVERY.md` was rewritten to remove was a *stored,
  unverified* list that goes stale silently. `guard_workflow.py --sessions`
  is derived fresh from `/proc` on every call — it cannot go stale, because
  it isn't stored at all. (Worth noting: `docs/next-action.md` itself
  currently carries a time-boxed, explicitly-caveated session/assignment
  table for the current recovery episode — that's a legitimate, bounded use
  of "roster," not the stale-forever kind this document argues against;
  read this section as endorsing that shape, not contradicting it.)

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
  known to hold? **This rules out a stranger; it does not establish memory
  continuity** — a real restart falsified the stronger claim this section
  used to make. A relaunched session inherits its predecessor's cwd
  regardless of whether any conversational memory survived (`docs/
  next-action.md` records exactly this: a session came back in the same
  worktree it left, with zero memory of the work already done there). Use
  occupancy to narrow the field, not to conclude continuity by itself.
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

**A distinct failure this probe does not catch: a stale roster, not a
renamed session.** Everything above assumes a *known name disappeared* and
a *new name appeared* — the trigger to probe. A real case had the opposite
shape: a dead coordinator was still shown live in one session's `ListAgents`
snapshot, while the coordinator's actual live replacement was missing from
that same snapshot entirely — no name vanished from that vantage point, so
nothing above ever fires. The tell wasn't a name change; it was `ListAgents`
itself being a stale snapshot relative to a session that started after it
was taken. Detect this by comparing `ListAgents`'s count *and membership*
against the socket/cwd check in §2 (`guard_workflow.py --sessions`, or
`ls /run/user/1000/cc-socks/*.sock` mapped to live PIDs) — **matching counts
prove nothing** (a stale 8-session snapshot and a live 8-session roster can
both say "8" while disagreeing on who); only membership agreement does.

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
considered and rejected in the file's own history (see "Do not read a task
list out of this file" — that language lives in `docs/next-action.md`
itself, not `CLAUDE.md`; verify the quote by grepping the file it's actually
in, since a citation to the wrong document is exactly the kind of thing that
looks fine until someone checks) — a growing status log is exactly the
staleness failure mode the rest of this repo's crash-recovery design avoids
elsewhere. The fix here is procedural, on the *reading* side, not the
writing side. **Do not lean on a specific footer sentence still being
there, either** — `next-action.md`'s own footer language has already
changed shape at least once across rewrites; cite the *behavior* (full
overwrite, no retained history in-file) and confirm it against whatever the
current file actually says, not against a remembered exact sentence.

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
manufactured — but with one identity correction:** `~/code/portfolio/ci-cd/`
is **not its own git repository** — verified: `git -C ~/code/portfolio/ci-cd
rev-parse --show-toplevel` returns `/home/davidf/code/portfolio`, the parent
*portfolio* repo. `ci-cd/` is a tracked subdirectory of that repo (`docker-
compose.yml` and `.env` both show up as `ci-cd/docker-compose.yml`,
`ci-cd/.env` in `git ls-files`), not a repo of its own — and `ci-cd/.env` is
itself a **tracked, version-controlled credentials file**, which matters for
§6 below. At the moment this was checked, `git -C ~/code/portfolio/ci-cd
status` showed **uncommitted changes to both `.env` and `docker-
compose.yml`** (and, because the repo root is one level up, that same
status output also reports unrelated sibling-project changes —
`../traefik/*.md`, an untracked `../NEXT-SESSION.md` — that have nothing to
do with `ci-cd` itself) — this is exactly the ambiguous shape the gap
describes: on its own, this diff does not say whether it's someone's
completed-but-not-yet-committed change, a change mid-edit, or a leftover
from an interrupted session; nothing distinguishes those cases today. This
observation is not a claim about *whose* change it is or why — only that
the ambiguity is real and present right now, not hypothetical.

**Proposal — before starting a risky step outside this repo** (stopping or
restarting a live shared service, or mutating its database directly):

1. **State it, explicitly, before acting** — in the session's own report/
   chat output: which service, what specifically is about to change, and
   why. This alone doesn't survive a crash on its own, but it means a user
   reading a partial transcript has the "intent" half of the picture even
   if the "did it finish" half is missing.
2. **If mutating a database file directly, copy it first**, timestamped,
   before writing — the same shape this repo already uses for its own
   backup convention: `data/quarantine/<ISO8601>-<name>-<reason>/` (a real
   example: `data/quarantine/20260903T034119Z-market_history-corrupt/`).
   Follow that naming exactly rather than inventing a new one — this makes
   "what did it look like right before" recoverable regardless of what
   happens next, without needing any new tooling.
3. **Write a plain-text marker before starting**, in the target repo if it
   is one, or in a tracked subdirectory of a repo otherwise (`ci-cd/` is the
   latter, not its own repo — see the correction above) — a small tracked
   or even untracked file works, since its mere presence/absence and mtime
   are the signal, not its git history — or in this repo's own scratch
   space if there's no repo at all nearby. One line: what's about to
   change, since when, expected to finish by roughly when. Update or remove
   it on completion. An abandoned marker with a stale timestamp is
   precisely the "in flight" signal that's missing today.
4. **If mutating a repo tracking the target's config (true for `ci-cd/`,
   whose files live inside the parent portfolio repo, not a repo of their
   own), commit the fix there too, deliberately and narrowly — never a
   broad `add -A`.** Committing "the fix" in a subdirectory-of-a-repo case
   means staging exactly the paths that changed for the actual fix
   (`git add ci-cd/docker-compose.yml`, not a sweep), because the parent
   repo's working tree can hold unrelated sibling-project changes at the
   same time — confirmed live: modified `traefik/` docs and an untracked
   `NEXT-SESSION.md` sat alongside the `ci-cd/` diff during the exact check
   this section describes. A broad add here doesn't just bundle unrelated
   docs — `ci-cd/.env` is a tracked credentials file, so a careless
   `git add -A` in that repo can commit a secret alongside the intended
   fix. Mirror this repo's own "git is truth" default, but only for the
   paths the fix actually touched.
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

# 4. Any recent restore/backup file that implies a stopped-for-write window
#    (verified live: this shell's `find` resolves to `bfs`, which rejects
#    the relative `-newermt` syntax GNU find accepts — `-mmin` is portable
#    to both):
find ~/code/portfolio/ci-cd -maxdepth 2 -mmin -360 -type f
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

# Anything committed by accident, by CONTENT not just filename — a real
# tracked credentials file can have a generic name (verified live: this
# repo's own portfolio superproject tracks `ci-cd/.env` under an ordinary
# name that a filename-only check like `git ls-files -- '*.env' '*token*'
# '*secret*'` misses if it isn't literally named that). Search commit
# content across all history and all repos actually in play, not just
# filenames in the current tree — one `-G` with an alternation, NOT repeated
# `-S` flags: `git log`'s `-S` is last-wins when given more than once
# (verified live — an earlier term is silently dropped, no error), so
# `-S'token' -S'secret'` only ever searches for `secret`:
git -C ~/code/portfolio/showcase-projects/autotrade log --all -p \
  -G'(api[_-]?key|token|secret|password)' 2>/dev/null | head -100
git -C ~/code/portfolio grep -nE '(api[_-]?key|token|secret|password)[[:space:]]*[:=]' \
  $(git -C ~/code/portfolio rev-list --all -- ci-cd 2>/dev/null | head -20) \
  -- ci-cd 2>/dev/null | head -50
```

A hit is not automatically a live incident — most will be legitimate scratch
use. (One appeal this document originally made doesn't hold: `CLAUDE.local.md`
does not currently say anything about scratch/secret-file handling —
grepped, zero hits — so don't cite it as existing standing guidance; if that
guidance should exist, it needs to be written, not assumed already present.)
Treat a hit as: confirm what it is, confirm whether it's still live (a token
that's already expired or been rotated is lower urgency than one that
isn't), and if it's a real live secret sitting in a readable **or tracked**
file, revoke/rotate it and remove the file (or, if already committed,
purge it from history and rotate regardless of whether the local copy is
also gone) — don't leave "found it" as the end state.

---

## 7. An inherited coordination artifact confidently asserting a false fact about you

**The gap.** Everything above assumes the danger is *missing* information —
a subagent's output gone, a session's identity unclear. This one is the
opposite: an artifact you inherit (a handoff doc, `docs/next-action.md`, a
coordinator's message) can state something about *you specifically* —
"assigned to session X," "X is working on Y" — confidently and in good
faith, and be wrong, because it was written from a stale or incorrect
vantage point. A real case: a coordinator's handoff recorded that two named
sessions each owned a specific piece of work; neither session had actually
received that assignment, and three sessions nearly collided on the same PR
as a direct result before it was caught.

**The proposal: an assertion about you is a claim to verify, not a fact to
act on.** Before treating "you are assigned X" / "you already own Y" /
"session Z confirmed W" as true:

- If it names *you*, check it against your own actual state (do you recall
  this? does your own git branch, worktree, or open PR reflect it?) before
  proceeding as if it's settled — the artifact's confidence is not evidence,
  only your own verifiable state or the other party's live confirmation is.
- If it names *another* session's status ("X already reviewed this," "Y is
  handling Z"), verify against that artifact's actual trail — a PR comment,
  a commit, a direct reply — before building on it. A claim that was true
  when written and has since gone stale reads identically to one that was
  never true; the sentence doesn't carry its own age.
- When a written assignment cannot be confirmed as received by the session
  it names, that assignment doesn't exist yet from that session's side —
  treat it as unconfirmed and reachable-out-for, not as already delegated.

This generalizes §3's identity-probe discipline from "is this session who it
claims to be" to "is this *claim about* a session accurate" — the same
verify-don't-infer posture, aimed at a different kind of stale artifact.

---

## 8. In-session subagents (no worktree isolation) dying mid-review with nothing prescribed

**The gap.** §1 covers subagents dispatched with `isolation: "worktree"` —
they get their own directory and (per §1's revised proposal) a marker
commit or PR comment. A subagent dispatched **without** worktree isolation —
an in-session background `Agent` call doing a review or analysis task with
no filesystem footprint of its own — has no such trace at all. If it's
killed mid-task (a rate limit is a real, observed cause, not hypothetical:
it happened to two independent adversarial-review passes in one night), its
reasoning and partial findings vanish completely — no worktree, no branch,
no commit, nothing for a resuming session to find.

**What actually saved the work, both times it happened:** not any mechanism
this document prescribed, but the *parent* session noticing the death and
posting an "INCOMPLETE — terminated mid-pass" comment to the relevant PR
before doing anything else, explicitly stating that silence on the
un-reviewed items must not be read as "found nothing." That happened by
habit, not by procedure.

**The proposal: make it procedure.** Before dispatching an in-session
subagent for any task with a review-cycle obligation (an adversarial review,
a findings pass, anything whose absence would otherwise look like "clean"):
post a one-line "review started, dispatched at `<ISO8601>`" comment to the
relevant PR/issue *before* dispatching. If the subagent completes normally,
its own findings comment supersedes the placeholder and no further action is
needed. If it dies without reporting, the placeholder is already there to
be turned into an explicit "INCOMPLETE, terminated mid-pass — do not read
silence as a clean result" note — the parent doesn't have to remember to do
this from scratch under pressure, it just has to update a comment that
already exists.

---

## 9. Mass simultaneous restart, not a single session's death

**The gap.** Every recovery procedure above — in this document and in
`SESSION_CRASH_RECOVERY.md` — is framed around *one* session dying while
others keep running: find its work, resume it, verify against the survivors.
A real, deliberate event doesn't fit that shape at all: a full WSL restart
took down every session, every ddev container, and the live app
simultaneously, all at once. There was no survivor to ask, no "the other
sessions are still up" baseline to check the dead one's claims against —
every single vantage point was gone in the same instant, and every session
that came back afterward was equally new.

**Two things this scenario does that the single-death case doesn't:**

- **The coordinator role itself can churn identity multiple times in one
  evening for reasons that have nothing to do with a crash** — an
  accidental terminal close, a fresh resume — cycling through several
  `autotrade-XX` names in a few hours while remaining, in substance, the
  same continuing coordination thread. Don't treat a fast succession of
  coordinator identity changes as several different authorities to
  reconcile; verify continuity per §3 (now updated: occupancy narrows,
  doesn't prove; agreement across signals is the bar) and treat a confirmed
  continuation as the same authority under a new name, same as any other
  renamed session.
- **The coordinator's own authoritative working state can live only on the
  primary's local `main`, unpushed**, accumulating across a whole session
  (dated docs commits, in-flight corrections) with `origin/main` sitting
  materially behind it. A resuming session that reads `origin/main` alone
  gets a real answer, but a stale one — check `git log origin/main..HEAD`
  in the primary before trusting any "current state" document, the same way
  §4 already asks for `next-action.md` specifically.

**The check that actually resolves both, cheaply:** run the socket/cwd tool
from §2 (`guard_workflow.py --sessions`) fresh, right now — it can't be
carrying forward a stale assumption because it isn't stored anywhere.
Reconcile whatever a handoff document claims against that live output before
acting on the document, not after.

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
