# Research: issue #530 sync-dispatch sweep — status update, not a fresh census

2026-09-05. Issue #530 ("Systematic sweep: async route handlers calling synchronous DB
code with no dispatch"). Dispatched to enumerate every route file, find undispatched
sync-DB `async def` handlers, measure the real ones live, and specifically resolve
whether `GET /api/quality/summary`'s repeated session-long timeouts are this same shape.

## Headline finding: the sweep this issue asks for already happened

Before doing any grepping, `git log` was checked for prior art on this exact question,
per this repo's "never guess, verify or falsify" rule. It already exists, in full, with
its own completed review cycle:

- `docs/event-loop-blocking-routes-census-2026-09-03.md` (commit `7c32c2d`,
  2026-09-03) — the exact deliverable this issue describes. Four parallel sweeps
  covered all 17 `services/*/routes.py` files plus `main.py`'s 13 inline handlers (88
  handlers total), then an independent adversarial-review pass spot-checked 7 of those
  18 sources and found real errors in 3 of them, correcting the headline from
  "89 checked / 58 BLOCKING" to **88 checked / 63 BLOCKING / 25 SAFE / 0 UNCLEAR**.
  `docs/event-loop-blocking-routes-census-self-review-2026-09-03.md` is that document's
  self-review.
- `services/quality/routes.py`'s `GET /api/quality/summary` — the census's own
  headline CRITICAL finding (30.25s/33.35s/11.59s measured) — was fixed and merged:
  PR #552 (`edcf1d5`, `db35df0`, plus adversarial-review follow-up `0d5b0df`). Seven
  undispatched sync calls now go through `await asyncio.to_thread(...)`.
- Issue #530's own two named instances (`reset/routes.py`, `observability/routes.py`)
  are literally rows in that census's table — this issue's body is describing findings
  the census already absorbed and (for one of the two) already fixed.

**This document does not redo the 88-handler enumeration.** Re-grepping 17 files and
13 handlers that were checked twice two days ago (once mechanically, once
adversarially) two days ago would not improve on that answer and risks quietly
contradicting a document that already went through its own review cycle. Verified
instead that the enumeration itself hasn't gone stale — see "Enumeration is still
current" below — then spent the effort where it adds real information: **fresh live
measurements**, specifically re-answering this issue's own explicit ask about
`/api/quality/summary`, and confirming current fix/no-fix status for every item the
census flagged as belonging to this issue's track.

**A coordination gap worth surfacing, not resolving unilaterally**: as of this
session, `docs/next-action.md` lists peer session `c4` as "Now on `#585` → `#530`
(David-approved sequencing) ... Then `#530`'s broader sweep, via parallel dispatched
subagents" — phrased as if the sweep is still pending. Git history says otherwise
(census merged 2026-09-03, one fix already merged, two follow-up correction PRs
already landed through 2026-09-05). This session had no way to reach `c4` directly
(no `ListAgents`/`SendMessage` access from this isolated worktree agent) to confirm
whether `c4` already knows this or is about to duplicate it. Flagging for whichever
session reads this next, rather than guessing at `c4`'s current state.

## Enumeration is still current — verified, not assumed

Re-counted route-defining files/handlers live in this checkout and compared against
the census's own file list:

- `find services -maxdepth 2 -name routes.py` → 17 files, **identical names** to the
  census's table (`advisory`, `alerting`, `analytics`, `backtest`, `backup`, `config`,
  `diagnostics`, `exits`, `history`, `market_catalog`, `observability`, `position`,
  `quality`, `research`, `reset`, `storage_health`, `whale_calibration`).
- `grep -n '@app\.\(get\|post\|put\|delete\|patch\)' main.py` → 13 handlers, same 13
  routes by name/order as the census's `main.py` row.
- `grep -rl '@router\.\(get\|post\|put\|delete\|patch\)'` outside `*/routes.py` and
  `main.py` → no hits. No new route-defining file has appeared since the census.

Cross-checked which of the census's 18 sources have been touched by any commit since
`7c32c2d` (`git log 7c32c2d..HEAD --oneline -- <each file>`):

| File | Touched since census? | What changed |
|---|---|---|
| `services/quality/routes.py` | Yes | **Fixed** — PR #552, 7 calls now `asyncio.to_thread` |
| `services/analytics/routes.py` | Yes (`e530ff0`) | Issue #410 track only (`population_gate_summary`) — explicitly a *different* issue's scope per the census's own text; not a #530 dispatch fix |
| `services/whale_calibration/routes.py` | Yes (`6a0584c`, `2eed9a7`, `3ad2431`, `2617b17`) | Issue #410 track (calibration report). **Correction, post-adversarial-review**: this document's first draft misattributed these four commits to `services/analytics/routes.py` — checked with `git show --stat` on each SHA, all four actually touch `services/whale_calibration/routes.py` (`2617b17` touches both files). The census counted this file as 5 BLOCKING / 1 SAFE (`report` already dispatched); `apply` is now also dispatched, via `_build_report_async()` (`await signal_log.resolved_signals_with_factors_async()` + `await asyncio.to_thread(...)`) — same #410-track reclassification pattern as `population_gate_summary`, not a #530 dispatch fix either. `enable`, `disable`, `status`, `history` remain plain synchronous, undispatched — **4 of the file's original 5 BLOCKING routes still stand**. |
| all other 15 sources | No | Unchanged since the adversarially-reviewed census |

So **two** of the census's 63 BLOCKING rows (`services/quality/routes.py`'s one row
covering 7 calls, and `services/whale_calibration/routes.py`'s `apply` route) have
been resolved since 2026-09-03 — one under this issue's own track (PR #552), one
under issue #410's track (a different issue reclassifying an already-dispatched-shape
route, the same pattern the census itself already used for `population_gate_summary`).
The other ~61 are unchanged in code shape, per direct `git log` verification file by
file. This document re-measures the two the issue asks about by name
(`/api/quality/summary` explicitly, `services/observability/routes.py` as one of the
two originally-cited instances) rather than all 61, for the same reason the census
itself gave for not blanket-fixing everything: "assuming they're all costly would be
exactly the kind of guess CLAUDE.md's data-plane HARD RULE says not to make."

## Live measurement method — host curl is unreliable, in-container is authoritative

Per this issue's own warning ("plain curl from a bash sandbox has been flaky even when
the app is healthy") and a standing session memory
(`bash-sandbox-loopback-false-502.md`), host `curl` through
`https://kalshi-whale-poc.ddev.site:8443` was cross-checked against
`docker exec ddev-kalshi-whale-poc-fastapi python3 -c "urllib.request..."` hitting
`http://localhost:8000` directly (no nginx, no TLS terminator, no ddev router hop).

**The discrepancy was real and large, confirming the warning rather than just
citing it**: host curl reported `/api/observability/summary` (default `hours=24`) at
**10.27s**; the in-container measurement, run within the same minute, gave **0.82s /
0.89s** across two calls — matching the census's own 2026-09-03 figure (0.79-0.91s)
almost exactly. The in-container numbers below are the ones treated as authoritative;
host-curl numbers are noted only to document the discrepancy, not used for any
conclusion.

## `GET /api/quality/summary` — explicit verdict per this issue's own ask

**Same shape it was: confirmed by the existing census and its own PR. Now fixed:
confirmed here, live, on the current running app.**

In-container timings (3 sequential calls, `urllib.request`, `localhost:8000`):

| Run | Wall time |
|---|---|
| 1 | 3.432s |
| 2 | 3.754s |
| 3 | 3.423s |

Down from the census's pre-fix range (11.59s-33.35s across three independent
measurements). The remaining ~3.4-3.8s is expected and by design, not a residual
defect: it's dominated by `diagnostics.run_offline()`, which is `await`ed and runs on
aiosqlite (already fixed under a separate, older initiative — PR #409/#420/#424, per
the census's own "Prior history" section) — genuinely off the event loop, just not
zero wall-clock time for the calling client.

**Concurrency proof — the actual symptom CLAUDE.md's own "start investigations
here" list is about, tested directly rather than inferred from code shape**: fired
`GET /api/quality/summary` in one thread, then three `GET /api/state` calls 0.2s
apart while it was in flight (same in-container `urllib.request` harness):

| Request | Wall time |
|---|---|
| `quality_summary` | 4.902s |
| `state_0` | 0.568s |
| `state_1` | 1.155s |
| `state_2` | 0.958s |

`/api/state` (CLAUDE.md's "fastest live read," almost certainly the most-polled
endpoint in the app) stayed sub-1.2s while `/api/quality/summary` ran for nearly 5s
concurrently. Before the fix, the census cited PR #424's own finding that "5
concurrent `GET /api/quality/summary` requests [could stall] an unrelated
`GET /api/state` for minutes." That specific failure mode is confirmed resolved by
direct measurement, not by re-reading the PR's own claim.

**Verdict for this issue's explicit ask**: `/api/quality/summary`'s historical
30-45s+ timeouts were the sync-dispatch shape this issue is about, and it is **already
fixed and verified live** — this is not a new finding, it's independent confirmation
of PR #552's result on the actual current running app. If timeouts recur tonight on
this specific endpoint, the cause is something other than the seven calls PR #552
dispatched (candidates: `diagnostics.run_offline()` itself under heavier load,
`config_store.get()`'s per-call file stat, unrelated resource contention) — not
re-diagnosed here, flagged as a genuinely open question if it recurs.

## `services/observability/routes.py` — still open, confirmed live, unchanged

The census's other named instance. Verified unfixed by both `git log` (no commits
touching this file since `7c32c2d`) and live measurement:

| Call | In-container wall time | Census's 2026-09-03 figure |
|---|---|---|
| `GET /api/observability/summary` (default `hours=24`) | 0.816s, 0.892s | 0.79-0.91s |
| `GET /api/observability/summary?hours=720` | 7.014s | 7.66s |

Essentially unchanged (small variance, same order of magnitude) — confirms the code
path hasn't drifted, and `observability.db` growth (406MB currently) hasn't yet moved
the default-window cost meaningfully.

**Concurrency proof — the same test run against this still-undispatched route,
showing the defect is real, not just theoretical from code shape**: fired
`GET /api/observability/summary?hours=720` in one thread, then three `GET /api/state`
calls 0.2s apart while it was in flight:

| Request | Wall time |
|---|---|
| `obs_summary_720` | 7.694s |
| `state_0` | 7.453s |
| `state_1` | 7.253s |
| `state_2` | 7.053s |

Unlike the fixed `quality/summary` case, `/api/state` is **fully stalled for the
entire duration** of the concurrent `observability/summary?hours=720` call — a clean,
directly-measured, textbook full-event-loop-freeze, the same shape already fixed for
`quality/summary`. This is the strongest evidence in this document: not a code-shape
inference, a reproduced app-wide stall on the live app.

`observability.history()` (`services/observability/observability.py:110`) and
`observability.summary()` (`:124`) are both plain `def`, both open/query/fetch via
`_connect()`'s synchronous sqlite3 connection, called directly from `async def
get_observability_history`/`get_observability_summary`
(`services/observability/routes.py:32`, `:41`) with no `await
asyncio.to_thread(...)`/`tick_executor`/equivalent. Confirmed by direct read of both
files, current state (this session), not from the census's prior citation alone.

## Verdict on the census's remaining ~61 BLOCKING instances

Not re-measured in this pass, for the reason stated above (no code change since the
adversarially-reviewed census for these specific instances; re-measuring unchanged
code adds nothing per-instance that the census doesn't already have). The census's own
priority-ranking table (cost × call-frequency, using live nginx access-log frequency
data) remains the correct triage input — restated here for continuity rather than
re-derived:

1. `GET /api/quality/summary` — **now fixed** (this document's contribution).
2. `GET /api/candidate-log/summary` — reclassified to issue #410's track (dispatched
   via `tick_executor`, a capacity/starvation problem, not an undispatched-call
   defect); not this issue's scope.
3. `POST /api/reset` (candidate_log) — issue #510/#512, already tracked, separate PR.
4. `GET /api/observability/summary` — **this document's second contribution**: still
   open, now proven via concurrency test (not just code shape) to fully stall the app
   for its query duration. The highest-severity **confirmed, currently-open** instance
   left in the #530 track specifically (as opposed to #410's or #510's tracks).
5. `GET /api/state` — cheap today (0.06-0.52s) despite 3 undispatched calls; fix on
   principle per the census, not on current measured cost.
6. `services/whale_calibration/routes.py`'s `apply` route — also reclassified to
   issue #410's track since the census (see "Enumeration is still current" above);
   `enable`/`disable`/`status`/`history` on that same file remain BLOCKING and
   unaddressed by either track.
7. ~15 dashboard-batch endpoints and the remaining ~55+ instances — the census's own
   words stand: "nothing in this document measured them... assuming they're all
   costly would be exactly the kind of guess CLAUDE.md's data-plane HARD RULE says not
   to make." Unchanged by this pass.

**Provenance note**: item 6 above was missed in this document's first draft — a
commit-attribution error (four SHAs credited to `services/analytics/routes.py`
instead of `services/whale_calibration/routes.py`) caused it to fall silently inside
an "unchanged" bucket. Caught by independent adversarial review re-deriving the
attribution with `git show --stat` per SHA rather than trusting the first pass's
table; corrected here rather than left standing. Full detail:
`docs/superpowers/research/2026-09-05-issue-530-sync-dispatch-sweep-adversarial-review.md`.

## Scope notes — what is and isn't this issue's track

- **In scope for #530 (call-site dispatch defect)**: any `async def` FastAPI route
  handler calling a synchronous DB/file-I/O function directly, no
  `await`/`run_in_executor`/`asyncio.to_thread`/`tick_executor.run`. The census's 63
  BLOCKING rows, minus the one now fixed (`quality/routes.py`).
- **Explicitly NOT this issue's scope, already tracked elsewhere**:
  - `services/reset/routes.py` — issue #510, PR #512 (merged; separate PR, not
    re-touched here).
  - `candidate_log.population_gate_summary()` / calibration report — issue #410
    (dispatched via `tick_executor` already; the problem there is worker-thread
    starvation on a growing table, a different mechanism than "blocks the event
    loop directly").
  - `services/backtest/routes.py`, `main.py`'s `_maybe_run_auto_apply` — issue #585,
    explicitly called out as separately-being-fixed and out of this document's scope
    per this task's own brief.
  - `services/config/config_performance.py`'s `record_variant()`
    (`main.py:908`, inside the trading-loop tick's `try` block) — same defect
    *class* (an undispatched synchronous `INSERT OR IGNORE` on the event loop, called
    every tick) but **not a route handler** — it's called from the tick loop, not an
    `@app`/`@router` handler, so it falls outside this document's defined scope
    ("every route-defining file"). Already tracked: `docs/open-decisions.md` line 41
    says it "joins #530's sweep." Confirmed unfixed (`git log 7c32c2d..HEAD --
    services/config/config_performance.py` → no hits).
  - **Persistence-layer `db.py` connection-management migration** (PR #518+) — a
    different, related defect class (a `_connect()` that leaks/doesn't `.close()`),
    orthogonal to dispatch. `services/observability/observability.py`'s `_connect()`
    could be migrated to `db.py` (Task 5, already pre-flighted separately per the
    census) without touching `routes.py`'s dispatch problem at all, and vice versa.
    No file in this document's findings should be treated as "covered" by that
    migration.

## What this document does not do

Re-run the 88-handler mechanical enumeration (already done, twice, with adversarial
correction, 2 days ago — see "Headline finding" above for why redoing it would be
wasted/duplicative effort, not extra rigor). Measure any of the ~61 still-unfixed,
unchanged-since-census BLOCKING instances beyond the two this issue names by name.
Design or implement a fix for `services/observability/routes.py` — that is a
follow-on decision (dispatch off-loop vs. bound/paginate the `hours` window, per the
census's own note that severity here is window-dependent), not made here. Resolve the
apparent `c4`/`next-action.md` coordination gap — surfaced above for the next reader
with `ListAgents`/`SendMessage` access to act on, not resolved unilaterally by a
worktree-isolated research agent.

## Dimensional analysis

All timings above are wall-clock seconds from `time.perf_counter()` deltas around a
single HTTP request/response cycle, consistent units throughout (seconds, 3 decimal
places, no unit conversion performed). No money/probability/contract-count arithmetic
appears in this document. `observability.db`'s size (406368256 bytes, reported by
`ls -la`) is cited once, in bytes, not converted or used in any downstream
calculation — cited only to note relative growth, not as a load-bearing number.
