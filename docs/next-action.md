# Next action — live update 2026-09-04 ~07:35 UTC, resume here if interrupted

Actively-worked session, not a hard pause — David's directive is continuous
max-effort until the 3-hour data-plane-stalls goal (started ~05:20 UTC) is
met. This doc is being kept current as things land; verify with `gh`/`git`
before trusting anything that could have moved since.

## Standing goal (unchanged all session)

Decouple the trade stream entirely from history/diagnostics — no shared
consumers/pools/queues — and move History off fixed-interval polling to
event-driven push. The live incident that motivated urgency (#541/#542
queue drops, WS reconnect/keepalive-timeouts) is **already fixed and
live** (PR #555, PR #558). What's in flight now is the *architectural*
decoupling work the incident's postmortem surfaced.

**Review policy tonight**: skip staged/redundant review checkpoints and
exhaustive local testing — trust the one PR-level cycle (self-review +
independent adversarial review + consolidation) as the gate, merge the
instant it's GO + CI green. This does **not** relax the gate itself for
trading-adjacent code — see 6e's #410 call below, held deliberately.

## Merged and live tonight (chronological, don't re-verify each)

Full persistence-layer db.py migration (13 tasks) · live-incident fixes
(#555, #558) · `_scoring_pool`/`tick_executor`/History decoupling
research+design docs (#562, #566, #567, #568) · Woodpecker CI fully
repaired (4 stacked bugs: unwired GRPC secret, dead webhook token, stale
repo-private flag, stale Cloudflare tunnel connector — all verified fixed
end-to-end, pipeline 943+ succeeding).

## Open right now — check `gh pr list --state open` for current truth

- **#570** — dedicated 1-worker pool for `candidate_retry.score_recovered_trade()`
  (closes #563). Session `e4`. Code complete, self-review posted,
  adversarial review + CI both in flight as of this write (CI 5/6 green,
  `quality-browser-e2e` still running). e4 explicitly declined to rush
  this gate under time pressure ("the one thing that would actually risk
  the outcome on a change that touches the whale-signal scoring path") —
  correct call, don't override it. **Merges autonomously on GO+green, no
  action needed.**
- **#571** — issue #410 design: pool-vs-aiosqlite comparison, split
  recommendation (mechanical cache-fix now, implementation held for
  proper review). Session `6e`.
- **#569** — issue #410's cache-alignment bug fix (the mechanical half of
  #571's split). Session `6e`.
- **Issue #565** (provider-instance divergence after account
  reconnect/disconnect) — session `f8` implementing. Census found a
  *third* stale-binding site beyond the issue's original two
  (`services/diagnostics/routes.py`, a real displayed-value-mismatch bug,
  not just the concurrency risk). Design: `app_state` becomes the single
  source of truth via a private attribute + getter, so a stale import
  fails loudly rather than silently reintroducing the bug class. No PR
  number yet as of this write — check `gh pr list`.
- **History event-driven push implementation** — dispatched as a
  background subagent (not a peer session), building on the merged #568
  design. No status yet as of this write.
- **Full-scope crash-recovery plan** (this task, meta) — dispatched as a
  background subagent, writing now. Will supersede or extend
  `docs/SESSION_CRASH_RECOVERY.md` — check that file's own header once
  the PR lands for which it chose.

## Explicitly deferred, not forgotten

- **#410's actual implementation** (aiosqlite rewrite + `asyncio.to_thread`)
  — held for a properly-reviewed follow-up after tonight's window,
  6e's own call, accepted. Touches a read path sharing a pool with
  `candidate_ledger`'s live decision-path writes; the data-plane HARD
  RULE's before/after-measurement + competing-solutions requirement
  doesn't fit a compressed review.
- **Issue #532** (`rejection_events` unbounded growth, 25.8M rows,
  ~4.2x/week) — the actual reason #410's query costs keep climbing.
  Every fix tonight amortizes or relocates this cost; none stop the
  growth. **Retention policy is explicitly David's decision** per
  "accumulated history is a first-class asset" — not resolved, not
  gated on anything else finishing.

## Team roster (interactive sessions, not subagents)

`e4` (#570), `f8` (#565), `6e` (#569/#571, holding #410 implementation),
`F2` (monitoring, unchanged role all session). Two background subagents
in flight: History-push implementation, full-scope crash-recovery plan.

`config/settings.yaml` still carries David's own unpushed local commit
(`kelly_fraction_of_cap`/`KXBTC15M`) on the primary's `main` — confirmed
intentional, still his call when/whether to push it. Untouched all
session.
