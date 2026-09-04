# Next action — live update 2026-09-04 ~21:25 UTC, resume here if interrupted

## ⚠️ CRITICAL, READ FIRST: cross-session `SendMessage` is unreliable right now

As of this write, the coordinator sent 6 direct pings to 6 live peer
sessions (`autotrade-36/d2/8f/df/64/21`), each explicitly asking for an
"ack" reply. **`SendMessage` reported `"success": true` on every single
send. Zero replies arrived.** A peer separately reported the reverse
direction failing too (a message it sent using the coordinator's
`[ref]`-qualified address silently resolved differently than the bare
name — same symptom, success reported, nothing delivered).

**Do not trust `SendMessage`'s success return as proof of delivery right
now.** If you're coordinating multiple sessions:
- Verify actual coordination through `git`/`gh` (commits, pushed branches,
  PR comments, issue comments) — those are real, checkable state. A
  peer's *reported* status via chat is not, until this is confirmed fixed.
- If you need to know whether a peer got an instruction, look for its
  *effect* (did the PR get the label, did the branch get pushed), not a
  reply.
- Every peer session tonight was explicitly told: don't block waiting to
  hear back from the coordinator, keep working your assigned task and
  push as you go regardless. If you're a session resuming after this
  note, that instruction still stands until you have direct evidence
  (not a "success" send) that messaging works again.
- If David has direct visibility into a session's own terminal, that is
  more trustworthy right now than anything relayed through this channel.

## Standing goal (unchanged all session)

Decouple the trade stream entirely from history/diagnostics — no shared
consumers/pools/queues — and move History off fixed-interval polling to
event-driven push. The live incident that motivated urgency (#541/#542
queue drops, WS reconnect/keepalive-timeouts) was fixed hours ago (PR
#555, PR #558) and is live.

**Review policy tonight**: one full cycle (self-review + independent
adversarial review + consolidation) as the gate, merge on GO+CI-green
immediately. When an account-wide rate limit (see below) took out every
peer session mid-cycle on already-self-reviewed, CI-green PRs, the
coordinator completed the adversarial-review+consolidation step directly
and disclosed it explicitly in each merge comment — a legitimate
fallback when independent-agent capacity is genuinely unavailable, not a
license to skip the check silently.

**⚠️ Account-wide rate limit hit ~07:45 UTC** ("weekly limit, resets 7pm
America/Chicago") — every peer session and every dispatched subagent died
simultaneously. Capacity recovered by ~08:15 UTC the same session. If you
hit this again, don't keep spinning up new sessions/subagents expecting a
different result — verify the limit actually cleared first.

## Merged and live — all 3 decoupling axes now have BOTH design and a
## first implementation merged

Full persistence-layer migration (13 tasks) · both live-incident fixes
(#555, #558) · all 3 decoupling axes' research+design (#562, #566, #567,
#568) · Woodpecker CI fully repaired · `_scoring_pool` isolation (#570,
closes #563) · `#410`'s cache-alignment bug (#569) · `#410`'s
pool-vs-aiosqlite design (#571, split fix: aiosqlite for
`population_gate_summary()`, aiosqlite+`asyncio.to_thread` for
`whale_calibration._build_report()`, no third pool) · `#565`'s
provider-instance-divergence fix (#572, found a third stale-binding site
the issue never named) · **History event-driven push implementation
(#573)** — reuses the existing dashboard WS, thread-safety crux
(`candidate_ledger` writes run on a `tick_executor` worker thread, so
push dispatch needs `asyncio.run_coroutine_threadsafe`, not
`create_task`) independently proven via a live repro during review.

## Open right now — verify current state with `gh pr list`, don't trust this list blindly given the messaging outage above

1. **`#410`'s actual implementation** — design settled (#571). Was
   assigned to session `36`. **Cannot confirm status given the messaging
   outage — check `gh pr list`/`git branch -r` for real evidence of
   progress rather than trusting any relayed status.**
2. **Full-scope crash-recovery plan** (a *different*, more thorough
   document than this one — see `docs/SESSION_CRASH_RECOVERY.md` for the
   general procedure this is meant to extend) — assigned to session `d2`,
   real partial content existed at
   `.claude/worktrees/crash-recovery-plan/docs/MULTI_SESSION_CRASH_RECOVERY.md`
   as of ~08:15 UTC. **Check that worktree directly for progress.**
3. **PR #574** (`fix/no-side-exit-valuation`, "price exits off the side of
   the book they are sold into") — appeared without the coordinator
   assigning it; likely from an independent finding (see
   `paper-10x-run-is-no-side-exit-valuation-artifact.md` in the user's own
   memory notes — a NO-side exit valuation bug affecting reported P&L).
   **Not reviewed by the coordinator yet as of this write.** Verify its
   review-cycle status before merging, same as everything else.
4. **Issue #539** — assigned to session `64` to close out the decisive
   verification window (baseline already persisted to the issue itself:
   https://github.com/thesneakattack/kalshi-whale-poc/issues/539#issuecomment-5536293627).
   Status unconfirmed given the messaging outage.

## Explicitly deferred, not forgotten

**Issue #532** (`rejection_events` unbounded growth, 25.8M rows,
~4.2x/week) — confirmed by #571's design as the actual reason `#410`'s
query costs keep climbing regardless of which fix lands. Every fix
tonight amortizes or relocates this cost; none stop the growth.
**Retention policy is explicitly David's decision** — raise it directly
with him, not gated on anything else finishing.

## Team / capacity note

6 autotrade peer sessions were live as of ~21:00 UTC (`36`, `d2`, `8f`,
`df`, `64`, `21`), all told to stop waiting on coordinator replies and
work independently given the messaging outage above. A `portfolio-87`
session was also present at various points tonight — confirmed once
already to be working a *different* repo (`~/code/portfolio/`, not
autotrade); don't assume continuity or relevance without asking.

`config/settings.yaml` still carries David's own unpushed local commit
(`kelly_fraction_of_cap`/`KXBTC15M`) on the primary's `main` — confirmed
intentional, still his call when/whether to push it. Untouched all
session.
