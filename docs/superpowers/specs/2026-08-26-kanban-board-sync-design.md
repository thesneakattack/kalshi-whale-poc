# Kanban Board Sync — Design

**Repository:** `thesneakattack/kalshi-whale-poc`
**Date:** 2026-08-26
**Status:** Brainstormed and approved in chat, section by section. Not yet
implemented. This document is the spec; implementation follows via
`superpowers:writing-plans`, not this document itself.

---

## 1. Purpose

Give worktrees, plan docs, `ROADMAP.md`, and `active-tracks-board.md` a
single live reflection on a real GitHub Issues + Projects V2 board, using
the newly-installed `github-issues-kanban` skill as the board substrate —
so a human (or, later, the already-designed-but-unimplemented
`autonomous-engineering-mode` claim loop) has one place to see what's
active, what stage it's in, and what's next, instead of reconstructing
that from `git log`, `git worktree list`, and five hand-maintained
documents every session.

This is the concrete answer to a question `autonomous-engineering-mode`'s
design deliberately left open: its §11 named "which sources feed the
issue queue" as "a separate, later decision," listing worktrees,
`ROADMAP.md`, and `active-tracks-board.md`'s tracks as candidates. This
spec is that decision.

## 2. Non-goals

- Does not turn on `autonomous-engineering-mode`'s claim loop. That
  mechanism is separately specified and planned
  (`docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md`,
  `docs/superpowers/plans/2026-08-26-autonomous-engineering-mode.md`) and
  has not been implemented. This spec only makes the issue queue real;
  starting a background agent to claim from it is a separate action.
- Does not write anything back into a repo file. `ROADMAP.md`, every plan
  doc, and `active-tracks-board.md` remain exactly as hand-maintained
  today — see §9.
- Does not attempt to auto-detect completion of individual tasks inside a
  numbered plan doc from that doc's own checkboxes. §5 measures why that
  signal is unreliable in this repo and why the design routes around it
  instead of trying to fix it.
- Does not add a privileged GitHub-write credential to Woodpecker CI or
  any other always-on/cloud-scheduled process. §10 explains why and what
  it does instead.
- Does not decide whether `active-tracks-board.md` should eventually be
  retired in favor of the live board it will now largely duplicate — a
  real question, explicitly left for later (§14).

## 3. Prior art and constraints this design must respect

- `.claude/rules/branching-and-ci.md` — `main` is protected; this is
  process tooling, built on an initiative branch like any other change.
- `.claude/rules/autonomous-quality-coordination-evidence.md` — the
  standing precedent for "automation that touches GitHub in this repo."
  Its core principle ("automation may observe broadly, but it earns
  authority to act narrowly") and its credential-minimization posture are
  reused directly in §9/§10 below, generalized beyond AQC. This spec's
  sync is observation-and-reflection, not remediation, but the same
  caution about *where a write credential lives* applies regardless of
  what the write does.
- `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md`
  — this repo's only prior design for issue-driven work. §5's `type:*`
  label extension, §6's protected-domain list, and the `## Scope`
  fail-closed body requirement (that spec's Task 7 note) are reused
  as-is (§7 below) rather than re-invented, so issues this sync creates
  are already compatible with that mechanism if it's ever turned on.
- The installed `github-issues-kanban` skill's actual schema — read in
  full for this spec, not assumed: `assets/label-scheme.json` (the exact
  `status:*` values), `references/issue-as-task-contract.md` (mandatory
  `## Acceptance criteria`, the claimability rules), and
  `references/dependency-chain.md` (`depends-on:#N` is AND-only,
  resolved by direct-issue-number label, not by body text).
- `CLAUDE.md`'s "Git history + supplementary docs" section and
  `.claude/rules/branching-and-ci.md`'s "Resuming work" section — this
  repo's existing, explicit position that hand-maintained progress
  ledgers drift and that git/current-code, not a checkbox in a doc, is
  implementation truth. §5 extends that same reasoning to plan-doc
  checkboxes specifically.
- `tools/quality_audit/` — this repo's existing precedent for "a Python
  package under `tools/` with its own `__main__.py`, invoked by a skill
  and independently testable." §10 follows the same shape for the
  deterministic parts of this sync rather than inventing a new
  packaging convention.

## 4. Architecture overview

```
 sources (read-only)
   - git worktree list, branch/PR/CI state
   - ROADMAP.md                      (checkbox state IS maintained truth)
   - active-tracks-board.md          (hand-maintained Status/entry-gate text)
   - docs/superpowers/plans/*.md     (NOT already represented on the board)
         |
         v
 classify
   - worktrees, ROADMAP, tracks: mechanical (parse the maintained signal)
   - plan docs not on the board: judgment-assisted — done / in-progress /
     not-started, from git log + CLAUDE.md/ROADMAP cross-reference, done
     once at sync time, never re-derived from the plan's own checkboxes
         |
         v
 tools/kanban_sync/  (deterministic core, independently testable)
   - find-or-create by <!-- autotrade-sync: <kind>:<key> --> marker
   - apply status:*/type:*/depends-on:#N per the kanban skill's own
     label-scheme.json — no invented label vocabulary
   - close issues whose source item is now done; never reopen a
     manually-closed issue (post a flag comment instead)
   - never edits a repo file
         |
         v
 GitHub Issues + Projects V2 board
   (github-issues-kanban skill's own Dispatch/Claim/Report/Audit/Triage
    take over from here — this design stops at "the queue is real")
```

Two entry points call the same `tools/kanban_sync` core (§10): an
on-demand skill, and a new step in the existing `/checkpoint` skill.
Neither is a new standing process.

## 5. Source inventory, granularity, and the checkbox-staleness problem

**Measured, not assumed** (2026-08-26): `docs/superpowers/plans/*.md`
contains 883 raw `- [ ]` checkboxes across 14 files. Spot-checking the
largest, `2026-08-24-quality-control-plane.md` (180 checkboxes across 23
`## Task N` headers), every single checkbox still reads unchecked — yet
`CLAUDE.md` and `.claude/rules/quality-capabilities.md` both confirm that
entire initiative (Tasks 1-20) shipped weeks ago. Same shape for
`2026-08-24-kalshi-integration-phase-a.md`/`-phase-c.md` (138 + 87
checkboxes, both merged 2026-08-25 per
`.claude/rules/kalshi-integration-authority.md`). This is not a bug in
those files — it's this repo's deliberate convention
(`.claude/rules/branching-and-ci.md`'s "Resuming work" section): progress
is reconstructed from git log and current code, not hand-checked off in
the plan doc, specifically so there's no second ledger to keep in sync.
`ROADMAP.md` is the opposite and reliable: its `- [x]`/`- [ ]` state is
actively maintained per `CLAUDE.md`'s explicit instruction to check items
off in place.

This means a sync can't use one parsing rule for both files. It uses:

| Source | Unit synced | Signal used |
|---|---|---|
| `git worktree list` + branch/PR/CI state | one tracking issue per active worktree | fully mechanical (git/`gh` state) |
| `ROADMAP.md` | one issue per open `- [ ]` (every section — all ~27 today, per the "everything named" scope decision) | fully mechanical (the checkbox itself) |
| `active-tracks-board.md` | one issue per track (3 today: A, B, C) | mechanical parse of that file's own hand-maintained Status/entry-gate text |
| Every other `docs/superpowers/plans/*.md` not already referenced by a track | one issue **per plan**, not per task | judgment-assisted classification (done / in-progress / not-started) at sync time, via git log + `CLAUDE.md`/`ROADMAP.md` cross-reference — never the plan's own checkboxes |

A plan classified **done** gets no issue at all (skip, not
create-then-close — a done initiative adds nothing by appearing and
immediately disappearing from the board). Measured today: of the 9 plan
docs not already represented by a track, 7 classify as done
(`kalshi-integration-dual-phase`, `-phase-a`, `-phase-c`,
`quality-control-plane`, `autonomous-quality-coordination-investigation`,
`realtime-data-plane-investigation`, and `realtime-data-plane-remediation`'s
P0-P2 slice — the latter's still-open P3/P4-P6 slices are already
represented via Track A and are not double-counted here) and 2 classify
as open (`frontend-modularization`, `autonomous-engineering-mode` itself).
These counts will change as work ships; the rule, not the current
snapshot, is what the implementation follows.

Because this classification step requires judgment (reading a doc and
cross-referencing git history, not just parsing a fixed format), it is
the one part of this sync that runs as a Claude-driven skill step rather
than pure deterministic code — same boundary `tools/quality_audit`
already draws between its deterministic scanners and the judgment calls
left to whoever reviews a finding.

## 6. Identity and idempotent sync

Every issue this sync creates carries a stable marker in its body:

```
<!-- autotrade-sync: <kind>:<key> -->
```

`<kind>` is one of `worktree` / `roadmap` / `track` / `plan`. `<key>` is a
stable identifier that survives re-syncs without depending on line
numbers (which shift as docs are edited): the worktree's branch name; a
short stable slug derived from the ROADMAP bullet's own bold lead-in text
(not its line number); the track's letter (`A`/`B`/`C`); the plan doc's
filename.

A sync run always searches first (`gh issue list --search "<marker>"
--state all`) before creating — find-and-update, never blind-create. This
is the same identity problem
`.claude/rules/autonomous-quality-coordination-evidence.md` already
flags for `QualityFinding.finding_id` ("do not assume automatically a
durable escalation key... prove identity behavior under inserted
lines/rename"), applied here to sync sources instead of scanner findings:
the marker is deliberately decoupled from anything that shifts under
ordinary editing.

## 7. Labels and the claimable-work contract

Per the brainstorm decision ("feed the real queue"), every ROADMAP/
track/plan issue is built to the kanban skill's real contract, not a
lookalike:

- Exactly one `status:*` from the skill's own const list
  (`status:claimable` for anything genuinely available now — the
  depends-on DAG in §8, not a separate status, is what gates a
  not-yet-its-turn item; `status:done` when the sync closes it).
- A repo-local `type:*` label reused from `autonomous-engineering-mode`'s
  own extension (`type:bug`, `type:investigation`, `type:plan-task`,
  `type:feature`/`type:design`) — that spec already documents `type:*` as
  a repo-local addition to the skill's canonical scheme (its Task 15
  note), so this isn't a second, conflicting extension.
- Worktree tracking issues instead get a new repo-local `type:tracking`
  label and a `status:*` that reflects real branch state
  (`status:in-progress` while local/pushed, `status:ready-for-review`
  once a PR is open, `status:done` once merged) but **never**
  `status:claimable` — a worktree isn't work to claim, and withholding
  that one label is what keeps the dispatch/claim mechanism from ever
  offering it, with no extra logic needed on the conductor side.
- `## Acceptance criteria` is always populated (the skill's own hard
  requirement — without it the conductor refuses to dispatch regardless
  of labels), derived from the source item's own stated definition of
  done: the ROADMAP bullet's text, the track's own "what's next" line, or
  the plan's own stated goal for plan-level issues.
- `## Scope` (the `autonomous-engineering-mode` extension, listing
  repo-relative paths/globs) is populated **only** when the sync can
  state it with real confidence (e.g. a ROADMAP bullet that already names
  specific files/services). Otherwise it's left absent on purpose — that
  spec's own fail-closed rule ("an issue with no declared scope is not
  claimable") already handles the gap safely; this sync does not attempt
  scope inference as a substitute for that judgment call.

## 8. Dependency-chain derivation

`depends-on:#N` requires a real issue number, which doesn't exist until
the referenced issue is created — so sync runs in two passes: (1)
find-or-create every issue from every source with no dependency labels
yet; (2) resolve each source's declared ordering into real issue numbers
and reconcile `depends-on:#N` labels (`gh issue edit --add-label`/
`--remove-label`, per `references/dependency-chain.md`).

Ordering comes directly from already-maintained text, not invented:
`active-tracks-board.md`'s own stated sequence (Track A: CH1 → CH2 → CH3
→ conditional CH4/CH5 → Tasks 14-17; Track C sequential and gated behind
both A and B) becomes real depends-on edges between the corresponding
track issues. A plan-level issue with an explicit entry gate in its own
doc (e.g. a plan that states it can't start until another plan merges)
gets the same treatment; a plan with no stated gate gets none.

## 9. Sync direction and closure semantics

One-way, repo-authoritative, confirmed in brainstorming: `ROADMAP.md`,
every plan doc, and `active-tracks-board.md` remain exactly as
hand-maintained today. The sync only ever reads them and writes to
GitHub — never the reverse.

Closure follows the same direction: a ROADMAP item checked off, a track
whose status line says done, or a plan-level issue explicitly closed
(matching the plan's own completion) causes the sync to close the
corresponding GitHub issue. The reverse case — an issue closed on GitHub
while its repo source still shows open — is **not** auto-reopened. Silently
reopening a closed issue could clobber a legitimate manual closure (e.g.
someone closed it as "not going to do this"); instead the sync posts an
`<!-- event: progress -->`-style comment flagging the mismatch and leaves
the issue closed, so a human reconciles it explicitly. This mirrors
`references/dependency-chain.md`'s own existing behavior for "closed but
not `status:done`" dependencies (§8's citation) rather than inventing a
new rule shape.

## 10. Trigger mechanism

Two entry points, both calling the same `tools/kanban_sync/` core so
there is exactly one implementation of the deterministic logic:

1. **On-demand skill** (new — name TBD, e.g. `kanban-board-sync`),
   invoked anytime, running the full flow including the judgment-assisted
   plan classification step (§5).
2. **A new step in the existing `/checkpoint` skill**
   (`.claude/skills/checkpoint/SKILL.md`), placed after its existing step
   7 ("Roadmap sync check") — checkpoint already fires "at natural
   breakpoints in a long working session" per `CLAUDE.md`'s long-session
   workflow discipline, so this gives automatic coverage without a new
   standing process, a new git hook, or a new cron. It's a call into the
   same `tools/kanban_sync` core the standalone skill uses, run in its
   deterministic (fast, mechanical-sources-only) mode — worktrees,
   ROADMAP, tracks — not the slower judgment-assisted plan classification,
   which stays a deliberate, on-demand action via the standalone skill.

A CI-based (Woodpecker) or cloud-scheduled trigger was considered and
rejected for v1: it would require a GitHub-write credential to live
inside CI, which is exactly the class of decision
`.claude/rules/autonomous-quality-coordination-evidence.md` says needs
its own threat-model/fault-injection pass before it exists — the same
reasoning that already paused `autonomous-quality-coordination`'s Program
7 pending a write-lane design. Nothing here is time-sensitive enough to
justify opening that question just for this. Left as an explicitly
deferred option (§14), not ruled out permanently.

## 11. Credential and environment prerequisite

Measured directly, not assumed: `gh auth status` in this environment
shows token scopes `gist, read:org, repo` — no `project` scope at all.
Projects V2 (the actual board with columns, not just Issues) needs
`project` scope. This is a one-time, human-only action (`gh auth refresh
-s project`, an interactive OAuth device-flow prompt) — no commit or
config change can grant it. The implementation plan should check for this
scope early and fail with a clear, actionable message rather than a
confusing `gh` API error if it's missing.

## 12. Data flow summary

```
git worktree list / gh pr-status / gh api commit-status
ROADMAP.md, active-tracks-board.md, docs/superpowers/plans/*.md
      |
      v  (read-only)
tools/kanban_sync (+ judgment-assisted plan classification, skill-driven)
      |
      v  (find-or-create by marker; apply labels; close; never reopen)
GitHub Issues (+ Projects V2 board)
      |
      v
github-issues-kanban skill's own Dispatch/Claim/Report/Audit/Triage
(out of scope here — this design stops once the queue is real)
```

## 13. Verification approach

Process/tooling, not application/trading code — verification proves the
sync's own claims, not trading logic.

1. **Deterministic core is unit-testable in isolation** (`tools/
   kanban_sync/`, same shape as `tools/quality_audit`): marker-based
   find-vs-create resolution, label application against the real
   `label-scheme.json` values, depends-on two-pass resolution, and the
   "don't reopen a manually-closed issue" rule — all provable with fixture
   data and a faked `gh` layer, no live GitHub calls required for the
   test suite itself.
2. **Fault injection**, one scenario per safety claim in §9-§11:
   - Re-running sync twice against the same source state produces zero
     duplicate issues (marker-based idempotency actually holds).
   - An issue manually closed on GitHub, with its repo source still open,
     is not reopened by the next sync — a flag comment appears instead.
   - A dependency cycle in a track's stated ordering is detected and
     refused the same way `references/dependency-chain.md` already
     specifies, rather than silently created.
   - Sync run against a `gh` token missing `project` scope fails with a
     clear message identifying the missing scope, not a raw API error.
3. **Dry-run mode** for the judgment-assisted plan-classification step:
   report what it would create/classify without writing anything, so a
   first real run can be sanity-checked against the numbers in §5 before
   it touches the live board.

## 14. Explicitly deferred (separate decisions, not part of this design)

- **Turning on `autonomous-engineering-mode`'s claim loop.** This spec
  only makes the queue real; starting the loop against it is that other
  spec's own launch step, a separate explicit action.
- **A CI/cloud-scheduled privileged trigger**, per §10 — not ruled out,
  just not part of v1, and gated behind its own credential/threat-model
  review if it's ever proposed.
- **Whether `active-tracks-board.md` should eventually be retired** once
  the live board reliably covers what it covers. A real question once
  this has run for a while; premature to decide before the board exists.
- **Widening synced sources beyond worktrees/ROADMAP/tracks/plans** (e.g.
  individual AQC coordinator findings, once that mechanism has a write
  lane) — each new source is its own scoping decision, not a default
  this design grants.

## 15. Self-review

**Placeholder scan:** no TODO/TBD left unresolved except the sync skill's
own name (§10, item 1) — explicitly marked TBD, the same pattern
`autonomous-engineering-mode`'s spec used for its own launcher-skill name,
not a substantive gap.

**Internal consistency:** §5's source table, §7's label mapping, §8's
dependency derivation, and §9's closure rule all reference the same
primitives (the sync marker, the kanban skill's real label constants, the
two-pass depends-on resolution) without renaming a concept between
sections.

**Scope check:** deliberately bounded to "make the queue real" — §2 and
§14 both say explicitly what's out (the claim loop itself, a CI trigger,
retiring `active-tracks-board.md`, new sources beyond these four) so a
future implementer isn't left guessing whether an omission was
deliberate.

**Ambiguity check:** the one place two readings were possible — whether
"everything named" (the broadest scope answer) meant literally every
plan-doc task checkbox too — is resolved explicitly in §5: it was scoped,
in the clarifying question itself, to ROADMAP breadth specifically; plan
granularity was already going to be plan-level with a done/in-progress
filter regardless of that answer, and §5's measurement is the evidence
for why.
