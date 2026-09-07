# Adversarial review: PR #409 (`fix/write-path-capacity-fix`)

Independent adversarial pass, per CLAUDE.md's "nothing advances on one pass" HARD
RULE. Fresh, memory-less review of the PR as pushed
(`fix/write-path-capacity-fix` @ `6a9d895` vs `origin/main` @ `911a1c8`). Every
claim below was re-derived from the actual source diff, an actual test run, or a
live empirical probe run in this session — not from the PR body's own summary,
the design spec's tables, or the implementation plan's prose. Where a claim could
not be independently re-verified this session, that is stated explicitly rather
than assumed true.

## Verdict: NO-GO (as pushed)

Not because of a functional defect — the mechanism is sound, every targeted and
full-suite test passes, and every load-bearing technical claim I could
empirically check held up. NO-GO because the PR's own Test Plan leaves an
explicitly self-declared **required** pre-merge gate unchecked (Task 7's
measured before/after validation — see "Confirmed correct" #8 below) and states
plainly that this is "a real, explicitly-called-out gate before merge, not a
formality." Taking that statement at face value (and independently agreeing with
it — see the data-plane HARD RULE's "a root-cause claim rests on... deterministic
reproduction, correlated runtime telemetry" bar), merging without that
measurement is merging on the PR's own admitted incomplete evidence. This review
also surfaces one real, material incompleteness in root-cause 2's fix (finding
B) that the PR body never mentions, plus two small, low-risk items (C, D) worth
folding in.

None of these findings require re-architecting anything. Recommended path: (1)
capture Task 7's baseline/post-change comparison per the plan's own steps, (2)
fix the stale comment (finding C) and correct or remove the market_history.py
circular-import rationale (finding A) — both trivial diffs, (3) either fold
`analytics/routes.py`/`whale_calibration/routes.py` into this same isolation
principle now or open a tracked follow-up issue explicitly (finding B) rather
than leaving the gap unstated, (4) optionally add a route-level regression test
for the diagnostics-pool wiring (finding D). None of 1-4 invalidate the
mechanism itself; they're the difference between "the mechanism works" (verified)
and "the mechanism is proven to fix the incident, completely, with nothing
adjacent left half-done" (not yet true).

## Confirmed real findings

### A. `market_history.py`'s circular-import claim does not reproduce

Commit `699f65c` and the PR body both assert `market_history.py` needs the same
deferred (call-time) `_scoring_pool` import as `signal_log.py`, "for the same
circular-import reason," and that this was "confirmed by direct `import main`
probes each time, not assumed."

I tested this directly: hoisted `market_history.py`'s
`from services.whalewatchers import _scoring_pool` to module level (right after
its existing `from services.signal_log import series_of` line), left
`signal_log.py` completely unchanged (still deferred, as merged), cleared
`__pycache__`, and ran, inside the fastapi container:

- `python -c "import main"` — succeeded, no error.
- `python -c "import services.market_history"` (fresh, first import, no prior
  warm sys.modules) — succeeded, no error.
- `python -m pytest tests/test_market_history.py --collect-only -q` — succeeded.

All three ran clean, twice (once before, once after a `__pycache__` wipe to rule
out a stale-bytecode fluke). The file was restored byte-for-byte afterward
(`git diff --stat` empty post-restore, confirmed).

Mechanism: `market_history.py` already imports `from services.signal_log import
series_of` *before* where the new import would sit, which forces `signal_log.py`
to fully finish loading first — so by the time the (hypothetical) module-level
`_scoring_pool` import runs and cascades back through
`whalewatchers.__init__ → base.py → confidence_scoring → kalshi_fees.py →
"from services.signal_log import series_of"`, `signal_log` is no longer
partially initialized; `series_of` is already bound. `signal_log.py` itself has
no such guard — its own `series_of` is defined ~300 lines below where its import
would go — which is exactly why *its* deferred import is genuinely required (see
"Confirmed correct" #4).

Not a functional bug (the deferred import is harmless, just unnecessary) and not
a merge blocker by itself, but it is a verified-false verification claim in a
codebase whose CLAUDE.md HARD RULE is "never guess; verify or falsify" — "a
retry that succeeds is not verification: establish why," and the inverse holds
too: a probe that wasn't actually run against the specific isolated change
(market_history.py's import alone, independent of signal_log.py's) isn't
verification either. Recommend either correcting the docstring/commit rationale
to state plainly it's defensive/precautionary rather than empirically forced, or
removing the unnecessary defer for exactness. Low priority, easy fix.

### B. Root-cause-2's fix is incomplete relative to its own stated principle — real, unmentioned gap

The PR states its governing principle as "nothing non-critical shares
`services.tick_executor`'s 2-worker pool ... with trading-critical work" and
fixes exactly one violator: `run_offline()` (`services/quality/routes.py`, now
on `_diagnostics_pool`). Two structurally identical siblings remain on
`tick_executor`'s shared pool, unmentioned anywhere in the PR body or diff, and
they are not hypothetical — they're already named as "the same shape" of bug in
a code comment this PR leaves untouched (`services/quality/routes.py:70-72`,
pre-existing, not part of this diff): *"Same shape as the two event-loop-stall
bugs fixed 2026-08-26 (candidate_log.population_gate_summary, whale_calibration
routes)"*.

Verified via `grep -rn "tick_executor.run("` across `services/` + `main.py`,
cross-checked against the frontend's actual poll cadence:

- `services/analytics/routes.py:103` — `candidate_log.population_gate_summary()`
  runs an unfiltered scan of `rejection_events` (6.2M+ rows), on
  `tick_executor.run(...)`, reached via `GET /api/candidate-log/summary`. The
  route's own comment records this was previously measured live (py-spy) at
  17-38s of continuous event-loop blocking before its 2026-08-26 fix moved it
  onto tick_executor (not off shared-pool contention, just off the event loop).
  Frontend: `loadCandidateLogSummary()` is called from
  `frontend/src/js/main.js`'s `refreshHistoryInsightsIfActive()`, itself called
  every ~5s from `polling-and-websocket.js:134` while the History tab is active
  — the same poll cadence the PR's own `run_offline()` fix was written to
  protect against.
- `services/whale_calibration/routes.py:117` and `:152` — `_build_report()`
  calls `signal_log.resolved_signals_with_factors()` (the *exact* unscoped-fetch
  cost `docs/open-decisions.md`'s Task 9 entry names, which this PR's own diff
  to that file updates for a different route) on `tick_executor.run(...)`,
  reached via `GET /api/confidence-calibration/report` and `POST
  /api/confidence-calibration/apply`. `loadCalibrationReport()` is on the same
  5s `refreshHistoryInsightsIfActive()` cadence, confirmed via
  `frontend/src/js/main.js:77` and the polling comment at
  `polling-and-websocket.js:134`.

Both still share `tick_executor`'s 2-worker pool with `capture_writer`'s flush
and `candidate_ledger.claim()/record_decision()`
(`services/whale_stream/decision_bridge.py:66,129`) — the exact trading-critical
writes root-cause 2 exists to protect. This doesn't make the `run_offline()` fix
wrong; it makes the "stop tick_executor starvation" framing in the PR's Summary
("nothing non-critical shares tick_executor's ... pool ... with trading-critical
work") incomplete as shipped. It should be either folded into this PR's scope or
explicitly named as a tracked follow-up — right now it's silently absent from
both the diff and the PR text.

Lower-severity, same family, noted for completeness: `services/diagnostics/
routes.py:53` (`GET /api/diagnostics`) calls `diagnostics.run_offline(...)`
directly and synchronously *on the event loop*, no offload at all (worse in kind
than sharing tick_executor) — but grep found no frontend or `.claude/hooks/`
caller, so it's manual/on-demand only, materially lower exposure.
`services/research/research.py`'s `build_report()` also calls the same
`run_offline()`, but is already routed through `asyncio.to_thread` (Python's
shared default executor — the same category root-cause 1 fixed for whale-scoring
elsewhere in this same PR) and is gated behind `research.enabled: false` by
default, so effectively dormant today.

### C. Stale comment left behind in the exact hunk this PR touched

`services/quality/routes.py` lines 68-72 (unchanged by this PR — a pre-existing
comment block) begin: `"# Offloaded via tick_executor (2026-08-27 fix,
subscription-churn investigation CH2 ...)"`, sitting directly above the line
this PR *did* change:

```python
"diagnostics": await _diagnostics_pool.run(lambda: diagnostics.run_offline(cfg)),
```

The comment still describes the pre-PR mechanism (tick_executor) one line above
code that no longer uses it. A reader who doesn't already know this PR's history
would be misled about which pool is actually in play. Trivial one-line fix
(update "Offloaded via tick_executor" → "Offloaded via `_diagnostics_pool`" or
similar), but it's a real, verified doc-accuracy defect introduced by omission in
this diff, and this repo's own "a displayed value must match its label" spirit
extends naturally to comments describing the mechanism a reader would trust.

### D. Minor test-coverage gap: no regression test pins the diagnostics-pool route wiring

`tests/test_quality_routes.py` (not modified by this PR) only asserts the
route's JSON shape and unrelated findings logic — nothing in it (or anywhere
else) asserts that `services/quality/routes.py:get_quality_summary()` actually
calls `_diagnostics_pool.run` rather than `tick_executor.run`.
`tests/test_diagnostics_pool.py`'s two new tests (confirmed passing, confirmed
non-vacuous — see "Confirmed correct" #9) only exercise `_diagnostics_pool.py`
in isolation, not its wiring into the route. A future refactor could silently
revert `services/quality/routes.py:81`'s call back onto `tick_executor.run` and
no test would catch it. Low priority — the diff itself is trivial to eyeball
today — but worth a small follow-up test (monkeypatch `_diagnostics_pool.run`
with a spy the way `tests/test_whalewatchers_kalshi_trade_tape.py`'s
`test_fetch_signals_runs_scoring_on_the_dedicated_scoring_pool` already does for
the whale-scoring side).

## Confirmed correct — no issue (independently re-verified)

1. **Root cause 1 diagnosis.** `kalshi_trade_tape.py`'s per-trade scoring path
   (`recent_sides_for_ticker`/`cluster_factor` in `signal_log.py`, `momentum()`
   in `market_history.py`, `analyst_lean()` in
   `market_analyst_agent/_db.py`+`per_market.py`) genuinely opened a fresh
   `sqlite3.connect()` per call before this PR — confirmed by reading the actual
   pre-PR `_connect()` bodies in the diff's `-` lines (each ran `CREATE TABLE IF
   NOT EXISTS` + WAL pragma on every call).

2. **`_scoring_pool.py`'s 4-worker pool correctly replaces `asyncio.to_thread`.**
   Confirmed in `services/whalewatchers/kalshi_trade_tape.py`: `fetch_signals()`
   now calls `await _scoring_pool.run(lambda: self._process_trades_timed(...))`
   (was `await asyncio.to_thread(...)`), and `score_recovered_trade()` (now
   `async def`) calls `await _scoring_pool.run(lambda:
   self._process_trades_sync(...))` (was a direct synchronous call on the event
   loop — confirmed strictly worse than the WS path pre-fix, as claimed).

3. **Root cause 2 diagnosis and fix.** `services/quality/routes.py`'s
   `run_offline(cfg)` call genuinely shared `services.tick_executor`'s 2-worker
   pool (confirmed: pre-PR diff line was
   `await tick_executor.run(lambda: diagnostics.run_offline(cfg))`) with
   `capture_writer`/`candidate_log` writes routed through the same pool
   (`main.py`'s `_flush_trade_capture_async`/`_flush_secondary_capture_stores_async`,
   `services/whale_stream/decision_bridge.py`'s `candidate_ledger.claim()`/
   `record_decision()`). The new `services/diagnostics/_diagnostics_pool.py`
   correctly isolates this onto its own dedicated 2-worker
   `ThreadPoolExecutor`, matching `services/tick_executor.py`'s own established
   module-level-singleton-executor convention.

4. **The circular-import claim for `signal_log.py` — genuinely required, and I
   reproduced the actual failure directly**, not just read the docstring.
   Reverted its deferred import to module-level (right after `from services
   import title_cache`), cleared `__pycache__`, ran `python -c "import main"`
   inside the fastapi container: got a real `ImportError: cannot import name
   'series_of' from partially initialized module 'services.signal_log'`. (Exact
   traceback path went through `advisory_engine → signal_log → whalewatchers →
   base.py → confidence_scoring → kalshi_fees.py → "from services.signal_log
   import series_of"` — a different concrete chain than the docstring's stated
   `whalewatchers.__init__ → kalshi_trade_tape → market_history → signal_log`
   path, but both are real edges in the same dependency graph and both produce
   the identical failure mode for the identical underlying reason: `series_of`
   is defined late in `signal_log.py`, after where a module-level
   `_scoring_pool` import would sit.) File was fully restored afterward
   (`git diff` empty). The deferred-import fix is correct and necessary.

5. **Type-consistency fix (`WhaleWatcherProvider.score_recovered_trade` →
   `async def`) is correct and complete.** Grepped every `WhaleWatcherProvider`
   subclass (`kalshi_trade_tape.py`, `generic_rest.py`, `template_provider.py`,
   plus `base.py` itself) and every `def score_recovered_trade` in the repo:
   `KalshiTradeTapeProvider` is the *only* override; `GenericRestProvider` and
   `TemplateProvider` don't define it at all, so they correctly inherit the
   base's new async default. `services/candidate_retry.py:167` is the *only*
   caller of `provider.score_recovered_trade(...)` in the entire repo (grepped
   `services/`, `tests/`, `main.py`), and it's correctly updated to `await` it.
   No other provider or caller is at risk.

6. **`busy_timeout` claim — empirically verified in this exact container.**
   `python3 -c "import sqlite3; print(sqlite3.connect(':memory:').execute('PRAGMA
   busy_timeout').fetchone())"` inside the fastapi container (Python 3.13.15)
   returned `5000` (ms) with no `timeout=` argument passed — matches the
   docstring's "Python's 5.0s default busy timeout" claim exactly. Cross-checked
   against `services/tick_executor.py:95-96`'s `connection_for()`, which
   explicitly passes `timeout=0.05` / `PRAGMA busy_timeout = 50` — confirms the
   claimed 100x contrast is real, not asserted.

7. **Thread-safety / lock-contention of the connection cache — reviewed, no new
   risk found.** The cache is strictly per-thread (`threading.local()`), so no
   `sqlite3.Connection` is ever touched from more than one thread — safe under
   Python sqlite3's default `check_same_thread=True`. Cached connections only
   ever execute bare `SELECT`s (no explicit `BEGIN`), and Python's sqlite3
   module's default legacy isolation mode does not open an implicit transaction
   for `SELECT` (only for INSERT/UPDATE/DELETE/REPLACE) — so there's no
   long-held read transaction that could see stale data under WAL. Both
   `test_scoring_read_connection_and_plain_connect_see_the_same_committed_data`
   tests (`test_signal_log.py`, `test_market_history.py`) directly exercise
   this: write via the plain per-call `_connect()` path, read immediately via
   the cached scoring connection, assert the write is visible — both pass. Net
   effect on schema-init contention at startup is a *reduction*, not an
   increase, versus the pre-PR per-call `_connect()` behavior (schema-init now
   runs at most once per thread per db file, capped at 4 scoring threads × 3 db
   files, versus once per trade before).

8. **"Task 7 not done" admission — honest, and if anything conservative, not
   understated.** Re-read `docs/superpowers/plans/2026-09-01-whale-scoring-
   connection-reuse.md`'s actual Task 7 (lines 600-639): Step 1 (pre-change
   baseline on `main`, ≥10 min real load, specific metrics named), Step 2
   (identical post-change reading), Step 3 (compare + optional burst test, honest
   disclosure if skipped), Step 4 (record the result in
   `docs/next-action.md`/`docs/open-decisions.md`). The PR body's
   characterization — "needs a pre-merge baseline... then the same reads
   post-merge... a single point-in-time snapshot... is not sufficient and is
   *not* being treated as this validation" — matches Steps 1-2 accurately and
   explicitly declines to treat weaker evidence as satisfying the gate, which is
   *more* conservative than the plan strictly requires, not less. It doesn't
   separately enumerate Step 3's burst-test option or Step 4's write-up
   requirement, but doesn't claim those are done either — no overstatement
   found.

9. **Test suite — actually run, not taken from the PR body.**
   - Exact 11-file list named in the PR's Test Plan:
     `python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py
     tests/test_whalewatchers_scoring_pool.py tests/test_candidate_retry.py
     tests/test_candidate_retry_integration.py tests/test_signal_log.py
     tests/test_market_history.py tests/test_market_analyst_agent_per_market.py
     tests/test_diagnostics_pool.py tests/test_quality_routes.py
     tests/test_strategy_engine.py tests/test_whale_stream_decision_bridge.py`
     → **331 passed**, exactly matching the PR body's claim, no discrepancy.
   - Full suite: `python -m pytest tests/ -q -m 'not slow'` →
     **2991 passed, 5 failed, 42 skipped, 3 deselected**. All 5 failures are in
     `tests/test_quality_coordination_cleanup_actions.py` (unrelated to every
     file this PR touches — it exercises `tools/quality_coordination.py`'s git
     branch-cleanup logic) and share one root cause: the fastapi container has
     no `git` binary at all (`which git` → exit 1; `/usr/bin/git`,
     `/usr/local/bin/git` both absent) — a pre-existing environment gap, not a
     regression from this diff. Confirmed unrelated by inspecting the failing
     tests' imports and the PR's actual changed-file list.

10. **Test quality spot-check (5 tests read critically, not just skimmed for
    assertions).**
    - `test_fetch_signals_runs_scoring_on_the_dedicated_scoring_pool` (kalshi
      trade tape): spies on `_scoring_pool.run` by wrapping the *real*
      implementation (`return await real_run(fn)`) rather than stubbing it out
      — proves the call actually goes through the real pool while still
      confirming it was routed there. Not vacuous.
    - `test_scoring_read_connection_and_plain_connect_see_the_same_committed_data`
      (both `signal_log.py`/`market_history.py` versions): real
      write-then-read-back integration test against a real tmp sqlite file, no
      mocking of the thing being tested. Not vacuous.
    - `test_run_pending_awaits_score_recovered_trade`: uses a real async
      provider stub whose `score_recovered_trade` appends to a `called` list —
      if the `await` in `candidate_retry.py:167` were missing/reverted,
      `asyncio.run(...)` would raise a real `TypeError` (iterating a coroutine),
      which this test would surface as a hard failure, not a silent pass. Real
      regression coverage.
    - `test_cached_read_connection_uses_default_busy_timeout`: queries `PRAGMA
      busy_timeout` on a real connection and asserts `5000` — a direct,
      non-mocked check of the exact claim in finding/confirmation #6 above.
    - `test_cached_read_connection_recovers_from_a_closed_connection`: closes a
      real connection out from under the cache, confirms the next call detects
      it (`sqlite3.ProgrammingError` on `SELECT 1`) and transparently reopens.
      Real behavior, not mocked away.

## Claims taken on faith (could not independently re-verify this session)

- **The live incident measurements cited in the design spec and commit messages**
  (tick_executor workers pinned 5h10m+ via `/proc`/py-spy, `capture_writer` lock
  faults recurring, `run_offline()` timing out on a direct curl while
  `/api/health/pipeline` responded normally, Task 8's own live isolation
  validation showing tick_executor probe latency 0.38-1.99ms with/without
  concurrent `run_offline()`) — these were point-in-time observations against a
  live running process earlier in the day and are not reproducible after the
  fact from a git checkout. I did not have (and was not asked to reconstruct) a
  live incident to re-measure against. Taken on faith as reported, consistent
  with this repo's own standing practice of trusting prior sessions' direct
  telemetry reads when the claim is specific and falsifiable in principle (which
  these are) rather than vague.
- **issues #145/#150** (a timed-out handler's OS thread not actually being
  freed) — cited in `_scoring_pool.py`'s own docstring as the reason for
  bounding the pool to 4 workers rather than eliminating the risk outright. I
  did not re-derive this from the original incident; treated as an
  already-tracked, already-cited fact per this repo's own "decide, don't
  over-investigate" standing guidance (chasing it further wouldn't change this
  PR's own correctness).
- **The two prior adversarial-review cycles' own findings** (the NO-GO → revise
  → NO-GO → revise history visible in the commit log for the spec) — I did not
  re-run those reviews from scratch; I verified the *current, final* code and
  spec state directly rather than trusting the intermediate review documents'
  own claims about what changed between revisions.
