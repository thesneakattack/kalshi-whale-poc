# Next action — PAUSED 2026-09-04 ~05:20 UTC, resume here

Session paused for usage limits mid-workflow. Everything below is exact
state, not narrative — verify with `gh`/`git` before trusting anything
that could have changed since.

## Immediate next action

1. **Check `gh pr list --state open`.** As of pause: only **#567**
   (`docs/tick-executor-query-cost-measurement`) is open. Its CI shows
   `fail` on all 6 contexts from pipeline 763 — but that's a **stale
   status from before tonight's CI fix landed** (see below), not a real
   failure. Re-push a trivial commit or use `scripts/woodpecker-trigger`
   to get a fresh run, then merge if green — full review cycle already
   complete on this one (self-review + adversarial review + fix-list
   applied), don't redo it.
2. **Everything else that was open tonight is already merged**: #553,
   #555, #556, #557, #558, #559, #560, #561, #562, #564, #566, #568.
3. Pull any of the above into the primary if `git log origin/main..HEAD`
   isn't empty — check `git status`/`git log` first, this doc may already
   be stale by the time you read it.

## What was accomplished this session (verified merged, not claimed)

- **Full persistence-layer db.py migration: all 13 tasks done** (Gate 0
  through Task 13), plus Task 15's final Gate 2 validation (PR #564).
- **The live data-plane incident: fixed and deployed.** Queue-drop/
  staleness (#541/#542) fixed by PR #555 (bounded-concurrency trade
  dispatch). WS reconnect/keepalive-timeout fixed by PR #558
  (trade-path `check_exits` throttle, relates to but doesn't close #412).
- **Decoupling initiative (David's standing priority) — all 3 coupling
  axes have merged design/measurement docs**, no implementation started
  yet:
  - `_scoring_pool` isolation: PR #566 merged. Recommendation: dedicated
    1-worker pool for `candidate_retry.score_recovered_trade()`. Issue
    **#563** tracks it; **implementation-plan stage not started.**
  - `tick_executor`/issue #410: PR #567 (see "immediate next action"
    above, blocked only on stale CI status). Confirmed via deterministic
    reproduction that a trading-critical `candidate_ledger` call CAN
    queue behind diagnostics for the full 15-22s cost. Recommends
    aiosqlite rewrite (`population_gate_summary`) + `asyncio.to_thread`
    for the compute-bound part (`whale_calibration`). **Not implemented.**
  - History event-driven design: PR #568 merged. Recommends reusing the
    existing `ws_manager`/`/api/ws` connection, not a new one. Found and
    solved a real thread-safety bug before implementation could hit it
    (`candidate_ledger` writes run on a worker thread; naive
    `asyncio.create_task` from there would crash — use
    `asyncio.run_coroutine_threadsafe`). **Not implemented.**
  - **Issue #565** (found during #566's review, real and live): after an
    account reconnect/disconnect, the WS-trade and candidate-retry paths
    can end up on two different provider instances with two different
    #546 dedupe ledgers — silently defeats that fix's guarantee. Filed,
    **not fixed.**

## CI infrastructure — fixed tonight, verify it's still fixed

`~/code/portfolio/ci-cd/` (Woodpecker server, NOT part of the autotrade
repo — see `docs/woodpecker-ci.md`'s "Known limitations" section for the
full trail). Three real, stacked bugs, all fixed and verified end-to-end
(pipeline 943 succeeded, all 6 required contexts green):

1. `WOODPECKER_GRPC_SECRET` was never wired into `docker-compose.yml`
   (was in `.env` but not referenced) — fixed, now pinned.
2. The GitHub webhook's token was signed with a since-lost ephemeral
   secret — repaired via Woodpecker's own webhook-recreate API.
3. `repos.private` was stale (`0`) after the repo was intentionally made
   private at 04:36 UTC — every other supported API route failed to fix
   it; required a direct SQLite write (`UPDATE repos SET private=1`),
   done with the server stopped, approved directly by David.

**Open, not confirmed fixed**: a second, stale Cloudflare tunnel
connector for `webfoundry-devbox` may still be intermittently serving ~half
of inbound traffic (version endpoint alternates 3.17.0/3.18.0 at the
public edge, stable 3.17.0 direct). Documented recurring bug + fix in
`~/code/portfolio/traefik/wsl2-port-forward-staleness.md`. Dispatched to
session `portfolio-37` (`docker compose down/up -d cloudflared-devbox` in
`~/code/portfolio/cloudflare-tunnel/`) — **check whether that landed.**

**⚠️ Possible credential exposure, unresolved at pause**: a session
reported leaving scratch files with a live Woodpecker admin token, a JWT
signing secret, and a 256MB SQLite dump (containing the live GitHub OAuth
token) under
`/tmp/claude-1000/.../63a51636-99b7-4a66-a92b-4e49af81410b/scratchpad/`.
I searched for these files at pause time and found **nothing** at that
path or anywhere matching those filenames under `/tmp` — so either the
cleanup already succeeded, or they're elsewhere. **Do a fresh search
(`find / -iname "woodpecker.sqlite.bak" 2>/dev/null`, etc.) and confirm
one way or the other before treating this as closed.**

**Also flagged, not fixed**: both Woodpecker images pinned to floating
`:v3`, not an exact version — real latent risk on the next
`docker compose pull`.

## Not started

- Implementation-plan stage for any of the 3 decoupling designs (research/
  design done, code not started, per "nothing advances on one pass").
- Issue #565's fix.
- Issue #539: mechanism fix confirmed working (240x improvement), fault
  *count* not yet statistically distinguished from baseline — a 3-hour
  decisive measurement window was started by session `f8`, baseline
  persisted to the issue itself:
  https://github.com/thesneakattack/kalshi-whale-poc/issues/539#issuecomment-5536293627
  (count=364 @ epoch 1788498602.10, robust to worker reloads — resume any
  time, the comment has the exact command).

## Team roster at pause (all told to push + stop, verify still true)

F2 (monitoring, no code to push), e4 (clean, all pushed), f8 (clean, all
pushed, 3 PRs merged), 6e (clean, ci-cd fix complete and verified — no
autotrade-repo changes), portfolio-37 (dispatched on the Cloudflare
connector fix, status unknown at pause — check in first).

## Config note, unchanged all session

`config/settings.yaml` has one deliberate, human-made local change
(`kelly_fraction_of_cap`, `KXBTC15M` threshold) still sitting as an
unpushed local commit on the primary's `main` (`ef662c0`). Confirmed
intentional by David. Still not pushed — his call when/whether to.

`docs/SESSION_CRASH_RECOVERY.md` has the general per-role reconstruction
procedure if this doc is itself stale or missing context.
