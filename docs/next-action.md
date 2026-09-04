# Next action — live update 2026-09-04 ~08:10 UTC, resume here if interrupted

Actively-worked session. Continuous max-effort toward the 3-hour
data-plane-stalls goal (started ~05:20 UTC). Verify with `gh`/`git` before
trusting anything that could have moved since this was written.

**⚠️ Account-wide rate limit hit ~07:45 UTC** ("weekly limit, resets 7pm
America/Chicago") — every peer session and every dispatched subagent died
simultaneously. If you're resuming into the same wall, don't keep spinning
up new sessions/subagents expecting a different result; check whether the
limit has actually cleared before assuming more parallelism will help.

## Standing goal (unchanged all session)

Decouple the trade stream entirely from history/diagnostics — no shared
consumers/pools/queues — and move History off fixed-interval polling to
event-driven push. The live incident that motivated urgency (#541/#542
queue drops, WS reconnect/keepalive-timeouts) was fixed hours ago (PR
#555, PR #558) and is live.

**Review policy tonight**: one full cycle (self-review + independent
adversarial review + consolidation) as the gate, merge on GO+CI-green
immediately — no staged checkpoints, no redundant local testing. When the
rate limit took out every peer session mid-cycle on 4 already-self-
reviewed, CI-green PRs, the coordinator (this session) completed the
adversarial-review+consolidation step directly rather than leave them
stuck — disclosed explicitly as a process deviation in each PR's own
merge comment, not a silent skip of the gate. Do the same if you hit this
again: don't force-merge past a self-review-only PR without *some*
independent check, but don't let a capacity outage block real, verified
CI-green work either.

## Merged and live (all verified, not claimed)

Full persistence-layer migration (13 tasks) · both live-incident fixes
(#555, #558) · all 3 decoupling axes' research+design (#562, #566, #567,
#568) · Woodpecker CI fully repaired · **all 3 axes' first-round
implementation now merged too**: `_scoring_pool` isolation (PR #570,
closes #563) · `#410`'s cache-alignment bug (PR #569) · `#410`'s
pool-vs-aiosqlite design decision, split fix recommended (PR #571) ·
`#565`'s provider-instance-divergence fix (PR #572, found and fixed a
third stale-binding site the issue never named).

## Open — real work, not yet started

1. **`#410`'s actual implementation** — PR #571 settled the design
   (aiosqlite for `population_gate_summary()`, aiosqlite+`asyncio.to_thread`
   for `whale_calibration._build_report()`, no third pool). Code not
   written. Two binding requirements from the design doc, don't skip:
   carry the `_reset_aio_db_cache` fixture on any new aiosqlite test
   module (leaked-non-daemon-thread hang risk, already bit this codebase
   once), and cite connection *lifetime* not *loop-binding* in new
   comments (the old rationale is factually wrong for the pinned
   `aiosqlite==0.22.1`).
2. **History event-driven push implementation** — design merged (#568),
   was mid-implementation in a background subagent
   (`.claude/worktrees/history-push-impl`, branch
   `feat/history-event-driven-push`) when the rate limit killed it.
   **Check that worktree for partial progress before starting over** —
   it may have working tree state even without a commit. Crux
   requirement from the design, don't skip: `candidate_ledger`'s writes
   run on a `tick_executor` worker thread, so any push dispatch from
   there MUST use `asyncio.run_coroutine_threadsafe` against a loop
   captured at `lifespan()` startup — `asyncio.create_task()` from that
   thread crashes (`RuntimeError: no running event loop`), proven by the
   design's own adversarial review with a live repro.
3. **Full-scope crash-recovery plan** (this task, meta) — also killed
   mid-write by the same rate limit. Check
   `.claude/worktrees/crash-recovery-plan` (branch
   `docs/full-scope-crash-recovery-plan`) for partial content before
   restarting. Was writing real verified content (not guessing) when it
   died — worth salvaging rather than discarding.

## Explicitly deferred, not forgotten

**Issue #532** (`rejection_events` unbounded growth, 25.8M rows,
~4.2x/week) — confirmed by #571's design doc as the actual reason `#410`'s
query costs keep climbing regardless of which fix lands. Every fix
tonight amortizes or relocates this cost; none stop the growth.
**Retention policy is explicitly David's decision** — not gated on
anything else finishing, raise it directly with him when there's a
moment, don't let it ride indefinitely just because nothing else depends
on it.

## Team / capacity note

As of this write: no peer sessions reachable via `ListAgents` except
possibly a differently-repo'd `portfolio-*` session (verify it's even
working in this repo before assuming continuity — this happened once
already tonight and cost a round-trip to sort out). Coordinator is
working solo through the rate-limit window.

`config/settings.yaml` still carries David's own unpushed local commit
(`kelly_fraction_of_cap`/`KXBTC15M`) on the primary's `main` — confirmed
intentional, still his call when/whether to push it. Untouched all
session.
