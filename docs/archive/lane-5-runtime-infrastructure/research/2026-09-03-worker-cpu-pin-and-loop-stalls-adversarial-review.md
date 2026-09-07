# Adversarial review: 2026-09-03 worker-CPU-pin-and-loop-stalls research doc

Independent pass per CLAUDE.md's "nothing advances on one pass" HARD RULE —
fresh Agent call, no memory of the authoring session. Every load-bearing claim
below was re-derived from primary sources (current source at this branch's
HEAD `f063551`, `git log`/`git show`, live `GET` calls against
`https://kalshi-whale-poc.ddev.site:8443`, `docker exec` `/proc` reads, and
`gh pr/issue view`), never taken from the artifact's own tables, prose
summary, or citations. Read-only throughout — no code, config, or `data/*.db`
writes; no restart/reload.

## Method

1. Checked out the PR branch (`docs/worker-cpu-pin-and-loop-stalls-investigation`,
   commit `f063551`) detached in an isolated worktree — another live session
   already held that branch name checked out elsewhere, so a named branch
   checkout would have collided; this review's commit sits on top of `f063551`
   and is meant to be fetched/merged onto the real branch by the orchestrating
   session, not pushed by this one.
2. Read both documents under review in full
   (`docs/archive/lane-5-runtime-infrastructure/research/2026-09-03-worker-cpu-pin-and-loop-stalls.md (moved there 2026-09-06, planning-lanes migration)`,
   its `-self-review.md`).
3. Re-read, from current source, every file/line the research doc cites as
   evidence for its central mechanism: `services/whale_stream/whale_stream_handlers.py`,
   `services/exits/exit_engine.py`, `services/exits/position_netting.py`,
   `services/paper_broker.py`, `services/strategy_engine.py`,
   `services/market_history.py`, `services/market_analyst_agent/per_market.py`,
   `services/candidate_log.py`, `services/loop_watchdog.py`, `main.py`,
   `config/settings.yaml`.
4. Re-ran the cited `git show`/`git log` commands against the actual repo
   history (`e787c8c`, `eaea541`) rather than trusting the doc's quoted text.
5. Independently re-pulled `gh pr view` for #414/#409/#420/#424/#515 and
   `gh issue view` for #510.
6. Made two live `GET` calls of my own — `/api/health/pipeline` and
   `/api/health/faults?component=loop_watchdog&limit=400` — at 16:19 UTC
   2026-09-03, roughly 14 minutes after the research doc's own investigation
   window closed (~16:05 UTC same day) — and cross-checked the worker process
   identity via `docker exec .../proc/25541/stat` to confirm this was the same
   long-running process the doc profiled, not a restarted one.

## Findings

### F1 — CONFIRMED (source, strongest tier): the central call chain is real, exactly as described

Read `services/whale_stream/whale_stream_handlers.py` in full. `_process_stream_ticker`
lines 356–387, current source, byte-for-byte matches the doc's §4.1 quoted
snippet: under `if state["running"]:`, `strategy.check_exits(...)` (366–370),
`broker.check_pending_fills(...)` (380–382), and `position_netting.review(...)`
(384–386) are called as plain synchronous function calls — none of the three
calls itself is `await`ed (only the resulting `_handle_close_decision`/
`_handle_fill_decision` calls are). This runs on every processed ticker
message that reaches this point (gated only by `state["running"]` and, for
`check_exits` specifically, `state.get("signal_feed")`), not once per 6-second
poll tick. This is a direct source-code state-transition proof, the strongest
evidence tier under the data-plane HARD RULE.

### F2 — CONFIRMED: `tick_cache` is genuinely never passed at this call site

`services/exits/exit_engine.py`'s `_cached()` helper (lines 65–78) takes the
`tick_cache is None` branch and calls `fn()` directly whenever `tick_cache` is
`None`. `check_exits`'s signature (line 108) defaults `tick_cache: dict | None
= None`. The WS ticker call site (`whale_stream_handlers.py:366–370`) passes
no `tick_cache=` keyword at all, so this branch is taken unconditionally,
every call. Verified by direct read, not inferred.

### F3 — CONFIRMED verbatim: the tick_cache commit's own scope note

`git show e787c8c` (2026-08-27 19:18:52 -0500, confirmed timestamp) contains,
verbatim: *"Wired only at main.py's real per-tick call site (trading_loop,
right before `strategy.check_exits(...)`); the two
`services/whale_stream/whale_stream_handlers.py` call sites are deliberately
left unwired, out of this task's scope."* This exactly matches the doc's §4.2
quote, word for word.

### F4 — CONFIRMED: every cited config value, exact line numbers

Direct `grep -n` of the live `config/settings.yaml` at this branch's HEAD:
`poll_interval_sec: 6` (line 27), `auto_exit_enabled: true` (line 83),
`auto_exit_series_track_record_weight: 0` (line 94), `position_netting:`
(line 219) / `enabled: true` (line 220). All four match the doc's citations
exactly, including line numbers.

### F5 — CONFIRMED: `main.py`'s per-tick `tick_cache` wiring is real and is the only wired site

`grep -n tick_cache main.py` → lines 1288–1299, a fresh `tick_cache: dict = {}`
allocated once per tick and threaded through `strategy.check_exits(...,
tick_cache=tick_cache, ...)`. Matches the doc's §4.2 claim.

### F6 — CONFIRMED, both structurally and empirically (live, independently reproduced): `loop_watchdog` cannot see the blocker

Read `services/loop_watchdog.py` in full. `start()` (line 62) defines `_tick`
as `async def` and schedules it via `asyncio.ensure_future(_tick())` (line
100) — a plain `asyncio.Task` on the same single-threaded event loop it
monitors, not a `threading.Thread`. The stack-capture path is
`_capture_stall_traceback()` (line 20–30, `sys._current_frames()` +
`traceback.format_stack`), invoked from `_tick()` at line 96 — both line
numbers (96, 30) match the doc's citation exactly. The doc's structural
argument (a same-loop coroutine cannot resume, and therefore cannot sample a
frame, until the blocking call has already returned control — by which point
the blocker's own frames have unwound) is airtight given this code.

Independently re-pulled `GET /api/health/faults?component=loop_watchdog&limit=400`
live at 16:19 UTC 2026-09-03 (a different, later 400-row sample than the
doc's own capture): 399/400 rows collapse to the identical shallow tail
(`loop_watchdog.py:96` in `_tick` → `loop_watchdog.py:30` in
`_capture_stall_traceback`), 1/400 has an empty `first_traceback`, and 0/400
contain any application frame (checked for `whale_stream_handlers.py`,
`exit_engine.py`, `main.py`, `market_history.py` anywhere in the traceback
text). This independently reproduces the doc's claimed shape exactly, on a
live re-pull the doc's own authoring session never saw.

### F7 — CONFIRMED: `candidate_log.py`'s `_connect()` leak, and the PR #499/#501 fix on `market_history.py`

`services/candidate_log.py:76`'s `_connect()` returns a plain
`sqlite3.Connection` (no `@contextlib.contextmanager`), runs 2 DDL statements
plus 2 `CREATE INDEX IF NOT EXISTS` on every connect, and is called at exactly
6 sites (`grep -n "_connect()" services/candidate_log.py` → lines 207, 255,
355, 407, 429, 449) as `with _connect() as conn:` — which, per
`sqlite3.Connection`'s own documented context-manager protocol, only
commits/rolls back, never closes. This is a genuine, source-confirmed leak,
matching the doc's §5.1 claim exactly.

By contrast, `services/market_history.py`'s `_connect()` (line 106) **is** a
proper `@contextlib.contextmanager` with `finally: conn.close()` (line 130),
per its own docstring citing "2026-09-03, Task 2 of
docs/archive/lane-6-observability-quality-safety/plans/2026-09-03-tier0-live-incident-remediation.md, moved there 2026-09-06, planning-lanes migration)..." (path
updated by the planning-lanes migration's own citation-fix sweep; the
docstring's own text continues past this point). I
independently re-pulled `gh pr view 499` ("fix: Tier 0 live-incident
remediation — close connection leaks, bound probes, unblock faults route,"
touches `market_history.py` among others) and `gh pr view 501` ("fix: close
residual setup-failure fd leak in **5** tier0 `_connect()` fixes," touching
exactly 5 service files including `market_history.py`) — both confirm the
doc's "5 modules... PR #499/#501" citation in §4.4 precisely, including the
count.

### F8 — CONFIRMED: the ruled-out PRs/issue are titled and shaped consistently with the doc's characterization

Re-pulled `gh pr view` for #414 ("fix: eliminate inline synchronous flush
blocking the event loop (4 modules)"), #409 ("fix: isolate write-critical
work from tick_executor's shared pool"), #420 ("fix: convert diagnostics.py +
series_watcher.py to aiosqlite"), #424 ("fix: revert regressive elastic pool,
keep+correct query-bound fast-follow"), #515 ("chore(orient): warn that
worktrees cost live-app CPU, above 12"), and `gh issue view 510` ("reset/
routes.py runs every DB call on the event loop," closed). All titles are
consistent with the doc's §7 rule-out table. For #414 specifically — the one
most load-bearing for this doc's own mechanism area — I independently
re-derived the fix from current source rather than trusting the PR title:
`series_watcher.record_book`'s flush is genuinely scheduled via
`asyncio.create_task(tick_executor.run(series_watcher.flush))`
(`whale_stream_handlers.py:289`), and `record_snapshot_from_ticker` is
genuinely scheduled via `tick_executor.run(...)`
(`whale_stream_handlers.py:349–355`) — both off the loop, matching §4.0's
claim.

### F9 — OVERSTATED (must-fix): "10+/sec sustained" ticker rate is not supported by the doc's own cited data

§7's rule-out table for issue #510 states the mechanism fires "10+/sec
sustained, per §2.1's call counts over the worker's ~1h life." Recomputing
directly from the doc's own two `handler_time_by_class` pulls (§2.1: ticker
lifetime count 32,851 at 12:49:10Z → 38,686 at 12:59:34Z, 10.4 minutes apart):
(38686 − 32851) / (10.4 × 60) ≈ **9.35/sec**, not 10+/sec. I independently
re-pulled `GET /api/health/pipeline` live at 16:19:11 UTC (ticker lifetime
count now 116,915): over the ~3.33-hour span from the doc's own second pull
to my live pull, (116915 − 38686) / 11977.66s ≈ **6.53/sec** average. Neither
figure clears "10+/sec." This doesn't change the rule-out's conclusion
(either number is still orders of magnitude above an operator-triggered
route), but the specific number should be corrected — "~9/sec" or
"high-single-digits/sec," not "10+/sec."

### F10 — OVERSTATED (must-fix): §4.3's `position_netting` citation misattributes the code and overstates its conditionality

§4.3 states: *"`describe_groups(...)` (line 274) contains, unconditionally
for every group: `vols = [market_history.volatility(ticker, vol_lookback,
as_of=now) for ticker, _ in members]` (`position_netting.py:251`)."* Reading
the actual file: line 251 is inside a **separate helper function**,
`_materiality_bar` (definition at line 227), which `describe_groups` *calls*
— it is not code that lives directly inside `describe_groups`'s own body.
More importantly, the call is **not unconditional for every group**:
`describe_groups`'s per-group loop (lines 305–349) only calls
`_materiality_bar` in the `else` branch (line ~340), for groups classified
neither `locked_profit` nor `locked_loss` — i.e., only "variable-outcome"
groups. A group that's already `locked_profit`/`locked_loss` never triggers
this volatility read at all.

This doesn't invalidate the underlying point — `review()` (line 374) does
call `describe_groups()` (line 390), which does call `_materiality_bar` for
any variable-outcome group, producing a second, independently uncached
`market_history.volatility()` call per member ticker, with zero
memoization — but "unconditionally for every group" and the direct
line-274 attribution both overstate/mischaracterize what the source actually
does. Should be corrected to something like: "`describe_groups` (line 274)
calls `_materiality_bar` (line 227) for every group not already classified
locked-profit/locked-loss; `_materiality_bar` (`position_netting.py:251`)
then computes `vols = [...]` unconditionally for that group's members."

### F11 — GAP (should-fix): `analyst_lean` does not do a fresh per-call `sqlite3.connect()` the way `recent_price`/`volatility` do

`services/market_analyst_agent/per_market.py:168`'s `analyst_lean()` uses
`_scoring_read_connection()` — its own docstring states explicitly: "Uses the
cached scoring-read connection (Task 4, 2026-09-01 write-path capacity fix),
not a per-call `_connect()`." This is a genuinely different connection
strategy from `market_history.recent_price`/`volatility`, which do
`with _connect(DB_PATH) as conn:` (a fresh `sqlite3.connect()` + PRAGMA +
schema-init) on every call — confirmed directly at `market_history.py:366`
and `:328`.

The doc's §4.4 "fresh `sqlite3.connect()`... per call" framing is textually
scoped only to `recent_price`/`volatility` (it never explicitly makes this
claim about `analyst_lean`), so this isn't a false statement — but §4.2 lists
all three (`recent_price`, `volatility`, `analyst_lean`) together as "3
distinct DB-backed reads" without flagging that one of the three carries
materially lower per-call connection overhead. This doesn't affect the
central claim (all three are still genuinely re-executed, uncached, on every
qualifying ticker message, since `tick_cache` is `None` at this call site
regardless of what each function does internally) — but it does affect the
precision of any future per-call cost estimate built on top of this document,
and should be noted explicitly rather than left implicit.

### F12 — GAP (should-fix): the doc doesn't address `_process_stream_trade`'s own uncached `check_exits` call, despite quoting a commit message that names it

The tick_cache commit's own text, which the doc quotes verbatim (F3 above),
says "the **two** `services/whale_stream/whale_stream_handlers.py` call
sites" — plural. Reading the file confirms there genuinely are two:
`_process_stream_trade` (same file, lines 170–249) also calls
`strategy.check_exits(...)` synchronously with no `tick_cache` argument
(lines 231–236), on the trade-channel WS subscription. The doc's entire §4
mechanism walkthrough (4.1–4.4) analyzes only the ticker-channel call site
and never explicitly discusses the trade-channel one, despite quoting the
very sentence that names both.

This appears to be empirically well-justified, not an oversight that changes
the conclusion: `_process_stream_trade`'s `check_exits` call is reached only
after `if not signals: return` (line 223) — i.e., only when the whale-scoring
pipeline actually emitted a signal for that trade, which is rare relative to
raw trade volume — consistent with the doc's own §2.1 data showing "trade"
class `handler_time_by_class` averaging only 2.57–5.27ms lifetime, versus
"ticker" class's 46–113ms+ (ticker's call is unconditional on `signal_feed`
being non-empty, which is true most of the time in normal operation). Still,
the doc should say this explicitly — right now a reader has to reconstruct
the "why ticker and not trade" reasoning themselves from a quote the doc
itself provides but doesn't unpack.

### F13 — New live observation, not in either document (should-fix as an addendum, not a correction): the condition has visibly worsened since the doc's own window closed

Live re-check at 16:19:11 UTC 2026-09-03 — about 14 minutes after the
research doc's own stated investigation window (~12:45–16:05 UTC same day)
closed:

- `GET /api/health/pipeline` → `last_tick_duration_sec: 1251.64` (nearly 21
  minutes), `ingest.queue_health.handler_time_by_class.ticker.window`:
  `avg_ms: 1682.99, max_ms: 5020.9` (n=4), `queue.high_water: 20000` (equal
  to `capacity`, i.e. the queue has been completely full at some point),
  `queue_wait.lifetime.max_sec: 465.44` (worst-case wait ≈7.75 minutes).
  All of these are dramatically worse than anything reported in the research
  doc (worst tick 31.25s per the coordinator's escalation; ticker
  `handler_time_by_class` window avg 46–113ms in the doc's own §2.1 pulls).
- I confirmed this is the **same** long-running worker process the doc
  profiled, not a restarted one: `docker exec ... cat /proc/25541/stat` shows
  `starttime` field (28,025,647 ticks) identical to the doc's own §2 reading,
  state `R`. Computing lifetime-average CPU from this same live read
  (`utime+stime` = 18,924.16s over a worker age of 15,150.52s) gives
  ≈124.9% — still broadly consistent with the doc's Condition-1 range
  (103–139%), so the CPU-pin condition doesn't appear to have qualitatively
  changed. Condition 2 (tick duration / stall severity) has.

This is not a falsification of anything in either document — it's simply a
fact that postdates both. But it means the doc's own numeric ranges (5–19s
tick duration, 37–115/min stalls) are now stale as a description of "current
state," and whoever acts on this research next should re-pull
`/api/health/pipeline` rather than treat this document's numbers as current.
I did not attempt to root-cause this specific escalation — that would be a
new investigation, out of this review's scope, and risks exactly the
"decide, don't over-investigate" failure mode this repo's own standing
guidance warns against for a secondary curiosity that doesn't change the
primary answer (which remains F1/F2's central claim, independently confirmed
above regardless of this new data point).

### F14 — Arithmetic/dimensional checks: all internally consistent where checkable

Independently recomputed every arithmetic claim I could check against its
stated inputs:
- §2's lifetime CPU average: `utime=224670, stime=97971` ticks at 100Hz =
  3226.41s of CPU time over 3112.48s wall-clock age = 103.66% ≈ "103.7%" —
  correct.
- Worker age: `starttime_ticks=28025647` / 100Hz = 280256.47s;
  `/proc/uptime` 283368.95s → age 3112.48s = 51.87 min ≈ "51.9 min" —
  correct.
- §5.2: `cancelled_write_bytes / write_bytes` = 23,357,345,792 /
  30,139,539,456 = 0.7750 = "77.5%" — correct. `write_bytes / age` =
  30,139,539,456 / 3374s ≈ 8.93 MB/s — correct given the stated 3374s, though
  I did not independently re-derive that specific elapsed-time figure live
  (the doc doesn't show its derivation inline either — a minor traceability
  gap, not an error I could find).
- §9's dimensional-analysis note (deferring the plugin pass since this is a
  prose document with no source-code arithmetic in scope) is a reasonable
  reading of the HARD RULE, consistent with how the cited prior document
  handled the same question.

Every other headline figure I spot-checked (CLK_TCK=100, the two
`handler_time_by_class` pulls, the config line numbers) matched their cited
source exactly — see F1–F8 above.

## Self-review cross-check

The companion self-review's three corrected overclaims (read-count, thread
percentage, estimated timestamps) all check out against current source/data
and don't need revisiting. Its two explicitly-flagged open items for this
pass:

- **"Re-pull PR #414/#409/#420/#424 independently rather than trust this
  document's characterization"** — done (F8 above), including an independent
  source-level (not just title-level) re-derivation for #414, the one most
  load-bearing for this doc's own mechanism.
- **"`EXPLAIN QUERY PLAN` on `idx_snapshots_ticker_ts` against `recent_price`'s
  query shape, not run this session"** — still not run by this review either.
  This is a real, still-open gap, correctly scoped by the self-review as
  affecting only the precision of "why ~50–100ms" (not the core claim, which
  holds either way given F1/F2). Flagged again here rather than closed.

This review did not find anything the self-review's "checked and held" list
got wrong.

## Verdict: GO-AFTER-FIXES

The central, load-bearing claim — that `_process_stream_ticker` synchronously
calls `check_exits`/`check_pending_fills`/`position_netting.review` on every
processed ticker message, that `check_exits`' `tick_cache` memoization exists
but is genuinely never wired at this call site, and that this is a deliberate,
named, documented scope gap in the commit that built the cache — **fully
holds** under independent re-derivation from primary sources: direct current-
source reads (exact line numbers, byte-for-byte snippet matches), a verbatim
`git show` of the cited commit, and live config values, none of which
required trusting the document's own citations. This is the strongest
evidence tier under the data-plane HARD RULE (source-code state-transition
proof), corroborated by a live, independently-reproduced 400-row empirical
check for the companion `loop_watchdog` claim. Nothing in this review
falsifies or meaningfully weakens either finding.

What keeps this from a clean GO is a small number of concrete, fixable
inaccuracies — an inflated rate figure, a mischaracterized/overstated
conditional in the `position_netting` citation — plus two completeness gaps
(the second, trade-channel call site quoted but not discussed; a connection-
caching nuance for `analyst_lean`) that a subsequent design/spec stage could
otherwise inherit uncritically. None of these require new investigation to
fix — they're corrections to claims already re-derivable from evidence this
review (and the original doc) already gathered.

### Must-fix

1. Correct "10+/sec sustained" in §7's rule-out table to the figure the
   doc's own cited data actually supports (~9.35/sec between its two pulls;
   ~6.5/sec average measured over a longer window in this review) — F9.
2. Correct §4.3's `position_netting.py:251`/`describe_groups` citation:
   attribute the `vols = [...]` line to `_materiality_bar` (the helper
   `describe_groups` calls, not code inside `describe_groups` itself), and
   correct "unconditionally for every group" to "for every group not already
   classified locked-profit/locked-loss" — F10.

### Should-fix

3. Note that `market_analyst_agent.analyst_lean()` uses a cached scoring
   connection, not a fresh per-call `sqlite3.connect()` like
   `recent_price`/`volatility` — refine the "3 reads" framing in §4.2 to
   distinguish connection-cost from query-cost so a future per-call estimate
   isn't built on a flattened assumption — F11.
4. Explicitly address `_process_stream_trade`'s own uncached `check_exits`
   call (same commit-message quote the doc already cites names it) and state
   why the doc's mechanism analysis is scoped to the ticker channel only
   (the trade-channel call is gated behind `if not signals: return` and
   empirically rare, per the doc's own `handler_time_by_class` data) — F12.
5. Add a dated addendum noting that a live re-check ~14 minutes after this
   document's own investigation window closed found the tick-duration/stall
   severity substantially worse (`last_tick_duration_sec: 1251.64` vs. the
   document's own worst-case 31.25s), on the confirmed-same worker process —
   so a reader doesn't treat this document's numeric ranges as current state
   without re-pulling `/api/health/pipeline` first — F13.
6. Carry forward the self-review's still-open `EXPLAIN QUERY PLAN` gap into
   whatever stage reads this document next; it remains unclosed after two
   independent passes now.

None of the above require touching the central mechanism claim itself, which
this review treats as independently confirmed.
