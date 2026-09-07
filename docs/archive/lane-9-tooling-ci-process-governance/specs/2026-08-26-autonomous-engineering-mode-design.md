# Autonomous Engineering Mode — Design

**Repository:** `thesneakattack/kalshi-whale-poc`
**Date:** 2026-08-26
**Status:** Brainstormed and approved in chat, section by section. Not yet
implemented. This document is the spec; implementation follows via
`superpowers:writing-plans`, not this document itself.

---

## 1. Purpose

Give the user a way to "switch on" autonomous engineering work, scoped to a
specific git worktree/debug session: once on, Claude investigates, plans,
debugs, and executes work **on its own**, using GitHub Issues (via the
newly-installed `github-issues-kanban` Claude Code skill) as the task
substrate — while remaining fully bound by every workflow rule this
repository already has (branching policy, TDD, protected-domain
restrictions, safety invariants). Autonomous mode changes *when* work
happens; it does not relax *what's allowed*.

This originated as a narrower request — "factor GitHub Issues Kanban into
the AQC implementation" — but was explicitly broadened during brainstorming
(2026-08-26) into this general-purpose mechanism. AQC's own
`quality_coordination.py` (Program 7, paused after Task 3 — see
`docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-autonomous-quality-coordination.md` and
its ledger) is one *possible* future source of work items for this
mechanism, not what it's built around. Wiring a specific source (AQC
findings, `ROADMAP.md` items, the `active-tracks-board.md` tracks) into
this mechanism is an explicitly deferred, separate decision — see §11.

## 2. Non-goals

- This spec does not modify `services/quality_coordination.py` or resume
  its paused Tasks 4-9. That plan stays exactly as scoped (zero write
  lane) and can resume independently of this work.
- This spec does not decide which GitHub-Issues-Kanban archetype/board
  layout to use, nor build any specific board. Board generation is a
  runtime *capability* the mechanism can invoke (§5), not a
  decision this design makes on the user's behalf.
- This spec does not implement or expand `services/quality_coordination.py`,
  `.claude/rules/autonomous-quality-coordination-evidence.md`'s governance,
  or any AQC-specific write lane. It is a new, separate mechanism.
- No change to real-money trading gates, `kalshi_account.trading_enabled`,
  the kill switch, or CORS. Nothing here touches those files unless a
  claimed issue explicitly does, and protected-domain issues are refused
  at claim time (§6) specifically to prevent that.

## 3. Prior art and constraints this design must respect

- `.claude/rules/branching-and-ci.md` — `main` is protected; work happens
  on initiative branches; Claude owns targeted local verification,
  Woodpecker owns exhaustive verification; PRs merge only after CI is
  green.
- `.claude/rules/autonomous-quality-coordination-evidence.md` — the
  precedent for exactly this class of question. Its "Remediation
  authority rule" protected-domain list and "No auto-merge assumption"
  are reused directly in §6/§7 below, generalized beyond AQC.
- `superpowers:brainstorming`'s hard gate: no implementation without a
  human-approved design. §6 makes this bind inside autonomous mode too.
- The installed `github-issues-kanban` Claude Code skill (SKILL.md read in
  full 2026-08-26): auth via `gh` CLI (already authenticated in this
  environment); state via issue labels + structured comments (event bus,
  polling not webhooks); optimistic-concurrency claim locks with a 30min
  default TTL, explicitly **not** truly atomic (v0.1.0 scope; "true atomic
  lock via external service" is deferred to v0.2+ by the skill's own
  authors); YOLO mode (bypasses per-dispatch confirmation) with an
  auto-disable safety net (two consecutive blocked events, any error
  during a YOLO dispatch, explicit user override, or session end).
- This project's own long-session workflow discipline (`CLAUDE.md`):
  checkpoint proactively, one task per commit, offload full verification
  to CI rather than re-running everything locally.
- This is a personal-use, eventually-real-money trading system currently
  in paper mode (`CLAUDE.md`'s standing goal). Safety-first, incremental —
  this mechanism's whole design posture follows from that.

## 4. Architecture overview

Autonomous mode is a background Agent (this environment's `Agent` tool,
`run_in_background: true`), pinned to one worktree, spawned explicitly by
the user, running a continuous claim → work → report loop against GitHub
Issues until stopped.

```
 EnterWorktree (existing)
       |
       v
 /autonomous-mode on   (new skill, §5)
       |
       v
 spawn background Agent, self-contained prompt:
   - loop logic (this section)
   - CLAUDE.md + .claude/rules/*.md (fresh load - agent has no inherited
     context from the launching session)
   - protected-domain list (§6)
   - merge-allowlist definition (§7)
       |
       v
 LOOP (repeats until stopped):
   1. List claimable issues (kanban skill Dispatch primitive -
      respects depends-on:#N DAGs, skips claimed/locked issues)
   2. Claim one (TTL optimistic-concurrency lock; re-verify
      immediately after acquiring - see §8)
   3. Protected-domain check (§6) - refuse and release if it fails,
      BEFORE any work starts
   4. Route by label to the existing skill that owns this kind of
      work (§5's routing table)
   5. Run that skill's normal workflow end to end (TDD, one-task-
      one-commit, branching policy) - INCLUDING brainstorming's
      human-approval gate if a new design decision is required (§6)
   6. Report progress as event-bus comments on the issue
   7. Push; wait for Woodpecker
   8. Diff-check against the merge-allowlist (§7):
        - inside allowlist + CI green -> merge, close issue
        - outside allowlist -> open PR, post "needs human merge"
          event, stop here for this issue
   9. Release claim
  10. Reschedule (dynamic pacing, same pattern as /loop's dynamic
      mode) and return to step 1
```

"On" and "off" are literally "is this Agent currently running for this
worktree" (§8) — no separate abstract state machine.

## 5. Skill routing

The mechanism does not invent a new workflow for any category of work. It
decides *when* to run the workflows this repository already has, based on
an issue's labels:

| Issue label | Routed to |
|---|---|
| `type:bug` | `superpowers:systematic-debugging` / `root-cause-debugging` |
| `type:investigation` | The domain's existing investigation skill (e.g. `realtime-data-plane-investigation`, `economic-strategy-effectiveness-investigation`) if the issue names one; otherwise a general investigation pass using `root-cause-debugging`'s discipline |
| `type:plan-task` | Whichever numbered-task orchestrator the issue names (`quality-plan-task`, `frontend-modularization-task`, `kalshi-integration-refactor`, or a `subagent-driven-development`/`executing-plans` pass over a named plan file — same pattern used for the AQC plan earlier this session) |
| `type:feature` / `type:design` | `superpowers:brainstorming` — subject to the human-approval gate in §6; this is the category most likely to stop and wait for the user rather than complete unattended |
| (unlabeled / unrecognized) | Refuse to claim; post an event asking for a `type:*` label before this issue is claimable |

Board generation/maintenance (the skill's own Generate/Audit/Triage/List
modes) are available to the agent as ordinary tool use within a loop
iteration — e.g. it can run Audit to check board health before Dispatch,
or Triage newly-filed issues into the label scheme — but these are
capabilities the loop *uses*, not a separate code path this design has to
build.

## 6. Safety principle: empowers, never relaxes

This is the load-bearing constraint of the whole design, confirmed
explicitly during brainstorming:

1. **`superpowers:brainstorming`'s human-approval hard gate still applies
   inside autonomous mode.** If a claimed issue requires a genuinely new
   architectural decision — not just executing an already-approved plan —
   the agent runs brainstorming's normal process, proposes a design, and
   **stops, posting the design as an event-bus comment on the issue**,
   exactly as the hard gate already requires outside autonomous mode. It
   does not self-approve. Practically, this means autonomous mode is
   naturally best suited to already-bounded work (bug fixes,
   already-approved plan tasks, investigations with a clear question) —
   the same shape of work the user actually described ("investigations/
   planning/debugging/plan executions"), not open-ended unsupervised
   architecture decisions.
2. **Protected-domain issues are refused at claim time, not merely blocked
   at merge time.** Reusing `.claude/rules/autonomous-quality-coordination-
   evidence.md`'s "Remediation authority rule" list, generalized beyond
   AQC: real trading/order execution; risk limits/kill switches;
   sizing/bankroll/exposure; whale/advisory/confidence/calibration logic;
   strategy/EV/fee/P&L/settlement semantics; security/auth policy;
   CI/branch-protection/credential policy; and this mechanism's own
   policy/guard implementation (the loop, the allowlist config, the
   protected-domain list itself). An issue whose target scope/paths touch
   any of these is refused before any investigation or code change starts
   — stricter than "won't auto-merge," because an unattended agent should
   not even attempt changes here.
3. **Standard git safety is unchanged**: no `--force`, no `--no-verify`,
   no commits directly to `main`, CI must be green before any merge
   attempt regardless of allowlist status.

## 7. Merge-allowlist

Checked against the **actual diff**, immediately before any `gh pr merge`
call — never against the issue's label, which can be stale or wrong about
what a fix actually touched.

Initial allowlist (deliberately narrow; can be extended later, each
extension its own explicit decision):
- `docs/**` — documentation-only changes.
- `tests/**` — test-only changes (no production code in the same diff).
- Explicitly pre-approved mechanical-refactor categories, added by the
  user one at a time as they're proven safe — none are pre-approved by
  this document.

Anything outside the allowlist: the PR opens, CI must still be green, but
the agent stops there and posts a "needs human merge" event. This mirrors
exactly how every PR in this session has been handled with the user
present — the difference autonomous mode introduces is *unattended*
operation, so anything not on the allowlist gets the same human-in-the-
loop merge step, just asynchronously.

## 8. State, launch, and concurrency

**Launch:** a new skill (name TBD — `/autonomous-mode` used as a
placeholder throughout this document) invoked from inside a worktree.
Refuses to run against `main` directly (same check the branching policy
already requires elsewhere); confirms an initiative branch is checked out;
spawns the background Agent with a self-contained prompt per §4's diagram.

**State:** a small marker file in the worktree, e.g.
`.claude/autonomous-mode.json`:
```json
{"status": "running", "started_at": "<iso-ts>", "agent_id": "<id>", "worktree": "<path>"}
```
Stopping = `TaskStop` on `agent_id` + marker updated to `"status": "stopped"`.
No separate abstract on/off state exists anywhere else — this file and the
agent's actual running/not-running status are the only source of truth.

**Concurrency across worktrees:** the kanban skill's own TTL claim-lock is
the primary defense against two autonomous agents (or an agent and the
user, working manually) claiming the same issue — but per §3, it is
explicitly optimistic-concurrency, not atomic (a known, stated limitation
of the skill's current version, not something this design can fix
upstream). Mitigation: immediately re-read the claim label right after
acquiring it, before starting any real work, to catch the most common
race window. This is a partial mitigation, not a guarantee — accepted as a
residual risk given the skill's own stated maturity level, rather than
over-promising atomicity that doesn't exist.

**Second kill switch, inherited from the skill itself:** the kanban
skill's YOLO auto-disable triggers (two consecutive blocked events, any
error during a YOLO dispatch, explicit user override) stop the **outer
loop**, not just the current issue's dispatch — a degraded run halts
itself rather than continuing to claim more issues in a bad state.

## 9. Data flow summary

```
GitHub Issue (labels: type:*, status:*, claimed-by:*, claim-expires:*,
              depends-on:#N, yolo:*)
      <-> github-issues-kanban skill primitives
          (Claim / Report / Dispatch / Audit / Triage / Generate)
      <-> autonomous-mode loop (this design)
      <-> existing repo skills (systematic-debugging, brainstorming ->
          writing-plans, numbered-task orchestrators)
      <-> normal git/branch/PR/CI/merge flow
      <-> event-bus comments posted back on the issue
```

## 10. Verification approach

This is process/tooling, not application code — verification means
proving the safety claims above, not a unit-test suite over trading logic.

1. **Dry-run mode.** The agent claims and investigates/plans normally but
   stops before any push/merge, reporting only what it *would* do. Lets
   the user sanity-check routing and judgment against real issues with
   zero write risk, before autonomous mode is ever allowed to act for
   real.
2. **Fault injection**, one scenario per safety claim in §6-§8:
   - A protected-domain-labeled issue is refused at claim time, not merely
     at merge time.
   - A stale claim (TTL expired) recovers on the next dispatch cycle.
   - A red Woodpecker run blocks merge unconditionally, allowlist or not.
   - Two simulated concurrent claim attempts on one issue resolve to
     exactly one worker proceeding (proving the re-verify-after-acquire
     mitigation actually catches the common race, not just in theory).
   - A simulated YOLO error event halts the **outer loop**, not just the
     one issue's dispatch.
   - An issue requiring a new design decision produces a posted design
     proposal and then genuinely stops — no implementation follows
     without a separate, later human "proceed" signal.

## 11. Explicitly deferred (separate decisions, not part of this design)

- **Which sources feed the issue queue.** This design assumes issues
  already exist (filed by the user via the kanban skill's Triage mode, or
  by some future adapter). Whether/how `services/quality_coordination.py`'s
  `escalation_eligible` items, `ROADMAP.md`'s open items, or
  `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-active-tracks-board.md`'s tracks ever
  get exported into this queue is a separate, later decision — each with
  its own eligibility-filter question, same shape as protected-domain
  filtering but potentially source-specific.
- **Widening the merge-allowlist** beyond docs/tests-only — each addition
  is its own explicit decision per §7, not a default this document grants.
- **Unconditional auto-merge** (no allowlist at all) was explicitly
  considered and rejected during brainstorming in favor of the narrower
  allowlisted version — see the chat record for the reasoning (this
  session found two real production bugs, PRs #35/#36, that had already
  passed CI and merged; an unattended loop with unconditional merge
  authority would have shipped them the same way).
- **Multi-worktree fleet management** (running several autonomous-mode
  agents across several worktrees simultaneously, with cross-worktree
  coordination beyond the per-issue claim lock) is not designed here —
  this spec covers one worktree, one agent, at a time.

## 12. Self-review

**Placeholder scan:** no TODO/TBD left unresolved. `/autonomous-mode` is
explicitly marked "name TBD" in §8 — the only intentionally-open naming
detail, not a substantive gap.

**Internal consistency:** §4's loop diagram, §5's routing table, §6's
safety principle, §7's allowlist, and §8's state/concurrency section all
reference the same primitives (claim/report/dispatch, event-bus comments,
TTL lock) consistently — no renamed concept between sections.

**Scope check:** deliberately narrow — one worktree, one agent, issues
already existing. §11 names exactly what's out of scope and why, so a
future implementer or reviewer doesn't have to guess whether an omission
was deliberate.

**Ambiguity check:** the one place two readings were possible — whether
brainstorming's gate could be skipped for "obviously small" design
decisions inside autonomous mode — is resolved explicitly in §6 item 1:
the gate always applies; a design proposal always stops for human review,
with no size-based exception. This was a direct choice, not left
ambiguous, precisely because "obviously small" is exactly the kind of
judgment call that erodes under unattended operation.
