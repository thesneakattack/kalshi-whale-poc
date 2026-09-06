# Branch & PR audit — 2026-09-05/06

Saved for a fresh Fable-tier session to review and turn into an action plan.
Not yet acted on — this is the investigation output only, no cleanup has been
executed. Published artifact (same content, easier to read):
https://claude.ai/code/artifact/b6b27d62-7887-4be2-85f1-d363d84660c7

Scope: every one of the 82 local branches and 19 remote branches in this repo
was checked against `origin/main` and against what `docs/next-action.md` /
`docs/open-decisions.md` already say, as of 2026-09-05/06 (checked by
`autotrade-1d`). Requested by David; a permission/context request was sent to
the tonight coordinator (`autotrade-36`) but no reply had landed by the time
this was saved — **the actions below are not yet approved.**

## Headline

- 3 open PRs, all owned by live sessions (`autotrade-c4` on #585,
  `autotrade-49` on #601).
- 27 branches confirmed superseded — code already on `main`, verified by
  reading main's actual current files, not just commit ancestry.
- 2 items already decided in `open-decisions.md` but never executed.
- 2 orphaned docs/investigation branches that are genuinely undelivered and
  need an actual decision.
- ~55 branches (46 local + 9 remote) already merged into `main` — pure
  mechanical cleanup, no judgment needed.

## 1. Open pull requests

| PR | Branch | CI | Owner / state | Note |
|---|---|---|---|---|
| #624 | `fix/585-backtest-routes-async` | all green | autotrade-c4, live | Ready to merge — full checklist checked in the PR body, all 6 required contexts `success`. Not mine to merge. |
| #625 | `fix/585-auto-apply-async` | running | autotrade-c4, live | Opened minutes before this audit; several contexts still `pending`. |
| #603 | `docs/601-candidate-log-fix-families-benchmark` | 1 pending | autotrade-49, live | Own checklist still shows adversarial-review and consolidation unchecked, plus a "not merging this PR" note — the real #601 fix already shipped via PR #617. Benchmark record; merge-as-docs-or-close is 49's call. |

## 2. Decided in `open-decisions.md`, not yet executed

- **`feat/candlestick-volatility`** — 5 commits, local-only, never pushed to
  origin (confirmed via `git ls-remote origin`, no matching ref). Decision
  (#398 comment, coordinator): "gets pushed so it cannot be lost, stays
  unmerged." 13 commits ahead of an old `main` (774 behind current tip) — a
  rebase/merge pass will be needed once pushed, not a fast-forward.
- **`feat/frontend-realtime-push`** — plan-only branch, superseded; the real
  History-push design already landed on `main` under issue #490. Decision:
  "delete branch + close #398." Issue #398 confirmed still `OPEN`. Last
  commit on the branch is 2026-09-01 (4+ days stale).

## 3. Confirmed superseded — safe to delete

27 branches whose code already exists on `main`, shipped through a
differently-named branch or a squashed/rewritten commit. Verified by reading
the actual current file content — an ancestry-only check (`git log
origin/main..<branch>`) called 14 of these "undelivered" at first pass, which
was wrong for every one of them once the code itself was compared.

| Branch | Purpose | Evidence it's already on `main` |
|---|---|---|
| `feat/persistence-layer-unified-connect` | Unified `db.py` module + schema registry | `services/db.py` exists with same content (Task 1-2, shipped via PR #518) |
| `fix/db-foundation-must-fix-tests` | 3 must-fix tests for db.py foundation | `tests/test_db.py` and the functions it exercises are present |
| `feat/strategy-edge-gate-task4-markout-sweep` | Wire markout-capture sweep into tick loop (Task 4) | Markout-sweep logic present in `main.py`'s tick executor |
| `feat/strategy-edge-gate-task8` | Edge/EV gate inside `_validate_entry_price` (Task 8) | `_edge_gate_check` + all 8 `edge_gate_*` config fields present (still `edge_gate_enabled: false`, consistent with #616) |
| `feat/strategy-edge-gate-task9-reporting` | Thread edge-gate metrics through for inspection (Task 9) | `edge_gate_detail` field exists on `EntryValidation` dataclass |
| `worktree-agent-a40018bd0564965f3` | 8 inert `strategy.edge_gate_*` config fields (Task 1) | All 8 fields present in `config/settings.yaml` |
| `worktree-agent-a3fb821b62e7ad4dd` | Hourly edge-gate delta recompute-and-cache (Task 7) | `services/whale_calibration/confidence_calibration.py` already has the cache/lookup logic |
| `worktree-agent-a817b637af0ff6f53` | Δ_calibrated bucketed-mean estimator (Task 6) | Exact match: `confidence_calibration.py:319` docstring defines it verbatim, `delta_calibrated_for()` at line 470 |
| `worktree-agent-ae6e384c6666b5725` | `signal_log.resolved_signals_for_edge_calibration` query (Task 5) | Function exists at `services/signal_log.py:894` |
| `worktree-agent-ad59e6678d1de2346` | Stack-capture stall attribution in `loop_watchdog` (Task 1, tier1 hygiene) | `_capture_stall_traceback()` using `traceback.format_stack` is live |
| `worktree-agent-a56079bc24243ff43` | 30s TTL cache for two expensive population-gate routes (Task 6b) | `services/analytics/routes.py:131` names this exact fix by date/task number |
| `worktree-agent-a4bd15b0a53e21acf` | Markouts table + capture-eligible-entries query | `CREATE TABLE markouts` + index/insert/select all present in `market_history.py` |
| `worktree-agent-a569aaa24055d98e8` | One canonical DDL string per table, owned by `capture_writer.py` | Docstring: "Owns its own DDL per store," tables consolidated there |
| `worktree-agent-a6bbebc428f09e9d5` | `series_cache.get_fee_type()` per-series accessor | Function exists at `services/series_cache.py:122` |
| `worktree-agent-a81d2de9518ecabe4` | Throttle History-tab loaders and quality/summary polling | `refreshHistoryInsightsIfActive` defined, imported, wired in on main |
| `worktree-agent-a9c0ddb3c2d0e4cb0` | Schedule `record_snapshot_from_ticker` off the event loop | `whale_stream_handlers.py:424` already schedules it off-loop via lambda |
| `worktree-agent-a042095c3f11bc0b1` | Pin explicit `httpx.AsyncClient` timeout/limits | `services/http_client.py` has the pinned config |
| `worktree-agent-a1fdd030a2eee87b6` | `market_catalog.py`'s `_connect()` closes its connection | Docstring cites this exact fix (Task 4, tier0-live-incident-remediation); `finally: conn.close()` present |
| `worktree-agent-a43152a642ed14359` | `signal_log.py`'s `_connect()` closes its connection | Shipped as commit `eccf558` |
| `worktree-agent-a5452d98f700bed3f` | `market_history.py`'s `_connect()` closes its connection | Shipped as commit `6095325` |
| `worktree-agent-aecc014b13efbac18` | `title_cache.py`'s `_connect()` closes its connection | Shipped as commit `5d85523` |
| `worktree-agent-abbaeb6615c5b72f5` | `fault_log.py` close() the sqlite connection | Docstring: "closes it on exit (2026-09-03, Task 6, tier0-live-incident-remediation)" |
| `worktree-agent-a38044574c99e6462` | `ConfigStore.update()` recursive-merge fix | Logic present in `services/config/config_store.py` |
| `worktree-agent-a3d0869fd3c2d09a0` | fd-count visibility in `/api/health/pipeline` | `fd_count`/`fd_budget` present in `services/diagnostics/routes.py`, with matching test file |
| `worktree-agent-a6e7fbcb3e9349ab7` | Throttle `event_live_data` cadence, coarsen `bump_generation` | `bump_generation` live, called from 10+ sites — content confirmed present; throttle-cadence diff not line-verified (lower confidence than the rest of this list) |
| `docs/persistence-layer-db-migration-research` | Research doc for the 30-module db.py migration | PR #504 merged this content 2026-09-03 (squash/rewrite — branch tip isn't an ancestor of `main`, confirmed via `gh pr view 504` showing `MERGED`) |
| `fix/tier0-live-incident-remediation` | PR #441's Tasks 1-6, connection-leak fixes + health-probe timeout bound | Its PR (#618) was closed *unmerged* after adversarial review found main's own version "a strict superset"; the one real nugget salvaged separately as PR #623 (merged). `open-decisions.md`'s #448 line describing this as still needing a push is now stale. |

All from a single session on 2026-09-03 (~01:50-03:30) — one branch per
module/task, the shape of a batch of parallel subagent worktrees whose
individual branches were never cleaned up after their combined work landed
through consolidated PRs (#500, #501, #518, and the tier0/tier1 hygiene
plans).

## 4. Orphaned artifacts — need an actual decision

Two branches are genuinely distinct from `main` (not superseded), but also
never reached a PR. Both are docs/investigation branches, both 2+ days
stale, neither mentioned in tonight's `next-action.md` peer roster.

- **`fix/seen-trade-ids-concurrency-race-investigation`** — root-cause
  investigation doc for a concurrency race in `seen_trade_ids`; self-review
  done, no adversarial review, no PR. Last commit 2026-09-03 17:30. Per the
  standing "persist findings, never leave them only in a dead branch"
  lesson, this should either get its adversarial-review + consolidation
  pass and land as a docs PR, or be explicitly parked with a line in
  `open-decisions.md` — right now it's neither.
- **`review/pr-505-adversarial-review-autotrade-3b`** — adversarial review
  of PR #505 (persistence-layer design spec); #505 merged 2026-09-03, this
  review of it never did. Exactly the failure mode the "persist code-PR
  reviews as comments" memory names: the review exists only on a branch
  nobody merged, so PR #505 shows zero review evidence in the repo itself.
  Cheapest fix: post its content as a comment on the already-merged #505,
  then delete the branch.

## 5. Live right now — leave alone

Confirmed via `ListAgents` at audit time (2026-09-05/06, tonight's
coordination chain still active):

- `docs/530-sync-dispatch-sweep` — autotrade-c4, in-progress status doc for
  the #530 sweep.
- `fix/585-auto-apply-async`, `fix/585-backtest-routes-async` — PRs #625 and
  #624 above, autotrade-c4.
- `docs/601-candidate-log-fix-families-benchmark` — PR #603 above,
  autotrade-49.
- `fix/586-aio-db-shutdown-hang`, `fix/candidate-log-ticker-scoped-resolve`,
  `docs/150-fix-family-benchmark` — already merged (section 6) but their
  `.claude/worktrees/` checkouts are still locked, likely just not released
  yet by the sessions that finished them. Safe for
  `scripts/cleanup-worktrees.sh` once unlocked; not for manual removal now.

## 6. Bulk cleanup — already merged, just not deleted

46 local branches and 9 remote branches are already ancestors of
`origin/main` — a plain git fact, no further verification needed. Routine
housekeeping, not a decision.

9 remote branches still need an explicit delete (most local counterparts
already show `[gone]`, meaning origin was already cleaned up for those):

- `claude/remove-architecture-rules-a60jaa`
- `docs/coordination-status-checkpoint`, `docs/db-migration-gate1-preaudit-general-bucket`, `docs/order-dependent-test-failures-2026-09-03`, `docs/task5-observability-preflight`
- `feat/candidate-log-db-migration`, `fix/candidate-ledger-db-migration`, `fix/fault-log-null-exc-type-dedup`
- `worktree-agent-a20b8eaa6381b5d6d`

```
git for-each-ref --format='%(refname:short)' refs/heads | while read b; do
  git merge-base --is-ancestor "$b" origin/main && [ "$b" != "main" ] && echo "$b"
done | xargs -r git branch -d

git push origin --delete claude/remove-architecture-rules-a60jaa \
  docs/coordination-status-checkpoint docs/db-migration-gate1-preaudit-general-bucket \
  docs/order-dependent-test-failures-2026-09-03 docs/task5-observability-preflight \
  feat/candidate-log-db-migration fix/candidate-ledger-db-migration \
  fix/fault-log-null-exc-type-dedup worktree-agent-a20b8eaa6381b5d6d
```

Skip any branch a `git worktree list` still shows checked out and locked
(section 5) until that session releases it.

## For the Fable session picking this up

Nothing here needs a merge from outside the sessions already live on those
branches (section 1, section 5). The open questions worth turning into a
plan:

1. Sequencing and ownership for executing sections 2, 3, and 6 (who runs the
   git commands, whether it needs its own PR/review cycle per CLAUDE.md's
   "nothing advances on one pass" given it asserts branch-status claims, or
   qualifies for the mechanical/trivial exemption).
2. A disposition for the two section-4 branches (land vs. formally park in
   `open-decisions.md`).
3. Whether `autotrade-36` (or whichever session is coordinating by the time
   this is read) has already acted on any of this — check `git branch -a`
   and `open-decisions.md` fresh before planning, don't assume this
   snapshot is still accurate.
