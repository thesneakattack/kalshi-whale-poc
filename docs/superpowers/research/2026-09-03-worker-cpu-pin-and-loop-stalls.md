# Research: the fastapi worker's ~107% CPU pin and loop_watchdog's stalls

2026-09-03, ~12:45–16:05 UTC. Requested by autotrade-1d (coordinator) as a genuine,
answer-unknown investigation of two standing live conditions: (1) the fastapi
container's uvicorn worker sits at a continuous, non-bursty ~107% of one CPU core;
(2) `loop_watchdog` logs 37–76 stalls/min with `last_tick_duration_sec` bouncing
5–19s against a 6s budget, and `GET /api/quality/summary` has never once returned
healthy. Scope explicitly excludes PR #414/#409/#420/#424 (already-fixed
event-loop-blocking bugs, read in full below to rule out recurrence) and issue
#510 (reset routes — real but separately tracked, ruled out as the cause of a
continuous ~50/min rate). No code, config, or live-app changes were made; every
number below comes from a read-only `/proc` sample, a live `GET`, a `git`
read, or a `docker`/host-level read-only command, cited inline with the exact
command.

**Top-line finding, stated up front:** both conditions share one mechanism.
`services/whale_stream/whale_stream_handlers.py`'s `_process_stream_ticker`
calls `strategy.check_exits(...)`, `broker.check_pending_fills(...)`, and
`position_netting.review(...)` synchronously (no `await`) on **every processed
WS ticker-channel message** — not once per 6-second poll tick, the cadence
these functions were designed and measured against. `check_exits`' own
`tick_cache` memoization (built 2026-08-27 specifically to stop this pattern)
is never passed at this call site — confirmed by the commit that added
`tick_cache` itself, which states in its own message: *"the two
`services/whale_stream/whale_stream_handlers.py` call sites are deliberately
left unwired, out of this task's scope."* With `auto_exit_enabled: true` and
`position_netting.enabled: true` both live in `config/settings.yaml` today
(`auto_exit_series_track_record_weight: 0` — confirmed live, so
`signal_log.series_stats` is gated off and not actually one of the live
calls), every open position on every processed ticker message re-executes
up to 3 uncached, per-call SQLite reads from `check_exits` alone
(`market_history.recent_price`, `market_history.volatility`,
`market_analyst_agent.analyst_lean`), plus a 4th, independent, also-uncached
call to `market_history.volatility` from `position_netting.review` for any
position in a netting group, plus real Python-level statistics computation
(`volatility()`'s population-stdev over a fetched row list) — entirely
synchronously, entirely on the event loop's main thread. This is a source-code state-transition proof (§4), corroborated by
live thread-level CPU sampling showing the main thread alone consistently
consuming 77–95% of a core (§2), by the WS ingest layer's own
`handler_time_by_class` showing the "ticker" message class at 46–113ms/call
against 2.7–5.2ms for "trade" and <1ms for "control"/"lifecycle" (§2), and by
the coordinator's own live-captured escalation event (§5, cited as reported)
showing CPU flat/unremarkable, worker state `R` not `D`, and only 2 of 27
threads active during a 31.25s tick — the exact signature of one thread
running a long synchronous stretch of Python, not disk I/O or thread-pool
contention.

## 1. Method

Per the task's known constraints (verified again here, not re-litigated):
`py-spy` cannot attach (`CapEff: 0000000000000000` inside the container blocks
ptrace-class access — confirmed directly in §1.1 below, extending to
`/proc/<pid>/fd` and `/proc/<pid>/io` too, not just external profilers).
Container-PID-space, not host-PID-space, is required for in-container
`/proc` reads; host-PID-space is required for `/proc/<pid>/fd`,
`/proc/<pid>/io`, and `lsof` (§1.1). All CPU-rate math is `utime+stime` deltas
from `/proc/<pid>/stat`, sampled twice over a fixed wall-clock window, divided
by `CLK_TCK`.

```
$ docker exec ddev-kalshi-whale-poc-fastapi getconf CLK_TCK
100
```

Confirmed, not assumed: 1 tick = 10ms.

### 1.1 Finding the worker, and the two permission domains

```
$ docker exec ddev-kalshi-whale-poc-fastapi sh /tmp/find_worker.sh   # walks /proc/[0-9]*/comm,cmdline
```
found container PID 1 = the uvicorn `--reload` supervisor
(`uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude
/app/.claude/worktrees --proxy-headers --forwarded-allow-ips=*`), container
PID 7 = `multiprocessing.resource_tracker`, and container PID **25541** =
`python3.13 -c "from multiprocessing.spawn import spawn_main; ..."` — the
actual `--reload` worker child that runs `main:app`. This is the "uvicorn
worker process" the task names.

`docker exec -u root ddev-kalshi-whale-poc-fastapi cat /proc/25541/fd/0` and
`.../io` both return `Permission denied`, **even as root inside the
container** — confirming the restriction is a dropped capability
(`CAP_SYS_PTRACE`), not a uid mismatch: reading another process's
`/proc/<pid>/fd/*` or `/proc/<pid>/io` requires `PTRACE_MODE_READ`
regardless of uid unless same-uid, and `docker exec -u root` is a *different*
uid (0) than the worker's owner (uid 1000, `DDEV_UID` per
`.ddev/docker-compose.fastapi.yaml`), so it still needs the capability. Basic
`/proc/<pid>/stat` and `/proc/<pid>/status` (no `fd`/`io`) are readable
regardless — that's why py-spy fails but plain CPU-tick sampling works.

Mapping to the *host* PID namespace, where the calling host user (`davidf`,
uid 1000 — the same uid `DDEV_UID` maps the container's process owner to)
**does** have same-uid access to `/proc/<pid>/fd` and `/proc/<pid>/io`
without needing the capability at all:
```
$ docker top ddev-kalshi-whale-poc-fastapi -eo pid,ppid,args | grep spawn_main
1803872  981356  /usr/local/bin/python3.13 -c "from multiprocessing.spawn import spawn_main; ..." --multiprocessing-fork
```
Host PID 1803872 = container PID 25541. `cat /proc/1803872/io` and
`ls /proc/1803872/fd` (run directly as `davidf` on the host, no `docker exec`,
no `sudo`) both succeed. This is the access path used for §6's fd census and
the `/proc/<pid>/io` cross-check in §5.2 — genuinely reproduced here, not
copied from a prior report.

## 2. Condition 1: the CPU pin is real, ~103–139% of one core, and it's the main thread

Worker start time, from `/proc/25541/stat` field 20 (`starttime`, in clock
ticks since boot) against `/proc/uptime`:
```
$ docker exec ddev-kalshi-whale-poc-fastapi sh /tmp/worker_uptime.sh
starttime_ticks=28025647   # / 100 Hz = 280256.47s since boot
283368.95 3784073.09       # /proc/uptime: system uptime, idle time
Thu Sep  3 12:51:01 UTC 2026
```
Worker age at that sample: 283368.95 − 280256.47 = **3112.48s (51.9 min)**,
implying a start time of ≈11:59:09 UTC — consistent with the task's stated
"11:56 UTC restart" (worst case a few minutes of clock/sample skew, not a
different event).

**Lifetime-average CPU utilization** (the `docker stats`/`ps %CPU`-equivalent
number, but computed correctly from `utime+stime` rather than trusted from a
tool that averages since an unstated start): at that same sample, `/proc/25541/stat`
showed `utime=224670` `stime=97971` ticks = 3226.41s of CPU time accumulated
over 3112.48s of wall-clock age = **103.7% average**. This already matches the
task's reported "~107%" to within measurement/rounding noise, using a method
independent of `docker stats`.

**Short-window sampling** (twice, 3 independent runs, this session,
`/tmp/thread_cpu.py` — a small script that reads `/proc/<pid>/task/*/stat`
before and after a fixed `time.sleep(window)`, per-thread):

| Window | Total (all threads) | Main thread (25541) | 2nd/3rd busiest thread |
|---|---|---|---|
| 10s | 105.1% | 76.7% (`R`) | 14.6%, 12.9% (both `S`) |
| 10s | 138.6% | 87.4% (`R`) | 37.8%, 12.1% (both `S`) |
| 12s | 99.1% | **95.4%** (`R`) | 1.5%, 1.3% (both `S`) |

The remaining 24 threads (27 total, minus main and the 2 columns above)
sampled at 0.0–0.4% each across all 3 runs — never a third meaningfully
active thread. The two
non-main threads that do show real (if smaller and more variable) usage are
consistent with `services/tick_executor.py`'s 2-worker pool — the pool
`asyncio.create_task(tick_executor.run(...))` now schedules the offloaded
work onto (§4's `record_snapshot_from_ticker`/`series_watcher.flush`, per
PR #500/Task 4, confirmed still wired that way in current source, §4.0).
**The main thread alone is, every time, the dominant and largely sufficient
explanation for the reported ~107%**; the total's 99–139% range (vs. a flat
107%) is explained by how much of that offloaded pool work happens to be
running concurrently in the same window, on other cores — real variance, not
inconsistency.

**The reloader (container PID 1) is a separate, already-disposed-of cost, not
part of this number.** A same-method sample of PID 1 (5s window) showed 25.6%
(`utime`Δ=31, `stime`Δ=98 ticks / 5.04s wall). This is now independently
explained by a different, already-merged investigation: **PR #515
("Merge pull request #515 from thesneakattack/chore/orient-worktree-cpu-warning",
merged onto `main` between this document's fetch and its worktree creation —
`git log --oneline -1 origin/main` before vs. after)**, whose own body states
`watchfiles` holds *zero* inotify watches in this container (WSL2 Docker bind
mounts don't propagate inotify) and therefore polls — `stat()`-ing the whole
watched tree every reload cycle — and measured, at their own two worktree
counts: 42.9% (34 worktrees, 47,401 files) vs. 20.8% (10 worktrees, 17,877
files) for the *watcher*, while **the worker's own CPU stayed flat at
~106–107.8% across both counts** (their own control column). This repo
currently sits at 12 worktrees (`git worktree list | wc -l`), between their
two reference points, and my own PID-1 sample (25.6%) falls between their
20.8%/42.9% bracket accordingly. This reconciles a real discrepancy: the
task's own framing and the coordinator's escalation report (§5) cite
container-level figures of 139–197% / 164%, meaningfully higher than this
document's worker-only 103–139% — the difference is PID 1's own
worktree-count-dependent cost, already found, already disposed of (a runtime
diagnostic added to `orient.sh`, per PR #515's own "Investigation-to-guard"
framing), and **not part of this document's mechanism** for the worker's own
pin, which their control measurement shows is independent of worktree count.

### 2.1 What `handler_time_by_class` shows, live, twice, ~20 minutes apart

`GET /api/health/pipeline`'s `ingest.queue_health.handler_time_by_class`
measures wall-clock `time.monotonic()` around `await self._handle_message(...)`
per message class (`services/kalshi/websocket.py:1189,1226` — read directly,
not inferred), i.e. genuinely how long the single consumer coroutine occupied
itself handling that one message, whatever it did internally:

| Pull | class=ticker window avg/max (n) | class=trade window avg/max (n) | class=lifecycle/control |
|---|---|---|---|
| 1 (12:49:10Z, `generated_at`) | 112.7ms / 542.5ms (541) | 2.57ms / 178.6ms (111) | 0.043ms / <1ms |
| 2 (12:59:34Z, `generated_at`, +10.4min) | 45.88ms / 76.67ms (638) | 2.69ms / 714.2ms (2186) | 0.18ms |

("trade" class's occasional high max, e.g. 714ms, is a separate, real
observation not chased further here — the *systematic* gap that repeats
identically in both independent pulls is ticker's average being 17–44×
trade's, not trade's own tail.) Ticker's *lifetime* average across both pulls
(52.4ms → 57.2ms, over 32,851 → 38,686 calls) stayed in the same band as the
window figures — this is not a one-off spike, it is the steady-state shape of
this one message class, consistently and by roughly an order of magnitude
above every other class on the same consumer.

## 3. Condition 2: the app's own stack-capture diagnostic cannot see the blocker (verified structurally, not inferred)

The 2026-09-02 architecture-audit-second-pass research doc (§4.3) named the
exact gap: `loop_watchdog` "records nothing about *what* the loop was doing,"
and proposed capturing `sys._current_frames()[main_thread_id]` on the stall
path as "the single most valuable diagnostic missing from the app." That
diagnostic **has since been built** — `services/loop_watchdog.py`, per its own
header, "Task 1 of `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`"
— and is live today. Reading it end to end:

```python
def start(*, sample_interval_sec: float = 0.1) -> asyncio.Task:
    async def _tick() -> None:
        ...
        while True:
            await asyncio.sleep(sample_interval_sec)
            now = time.monotonic()
            late = now - expected
            ...
            if late > _STALL_THRESHOLD_SEC:
                ...
                tb = _capture_stall_traceback()   # sys._current_frames()[main_thread_id]
                _record_stall_fault_background(tb)
            expected = now + sample_interval_sec
    return asyncio.ensure_future(_tick())
```
(`services/loop_watchdog.py:62-100`, elided for brevity — full function read
in place, nothing skipped that changes this analysis.)

`_tick()` is itself an `asyncio.Task` on the **same, single-threaded event
loop it monitors** — not a separate OS thread. This is a source-level
state-transition proof of why the resulting `first_traceback` can never show
the blocking code: when some other callback (this document's §4 mechanism,
concretely) occupies the loop synchronously for, say, 10 seconds, `_tick()`'s
own `await asyncio.sleep(0.1)` cannot resume until that occupying call
**returns control to the loop** — which on CPython's single-threaded
event-loop model means the blocking call has already **finished executing**
by the time `_tick()` gets its turn. `sys._current_frames()` at that instant
shows whatever frame is *currently* live on the main thread — which is now
`_tick()`'s own frame (`loop_watchdog.py:96` calling
`_capture_stall_traceback()` at `loop_watchdog.py:30`), because the blocker's
frames have already unwound. There is no mechanism by which a same-loop
coroutine can observe a frame that finished before it got scheduled.

**Verified empirically, not just derived**, against every stall row currently
held:
```
$ curl -sk 'https://kalshi-whale-poc.ddev.site:8443/api/health/faults?component=loop_watchdog&limit=400'
```
400 of 400 `loop_watchdog`/`stall` rows' `first_traceback` fields collapse to
exactly **2 distinct tails** (one populated, one empty/"main thread frame
unavailable"); the populated one is, byte-for-byte, always:
```
...
  File ".../asyncio/runners.py", line 119, in run
    return self._loop.run_until_complete(task)
  File "/app/services/loop_watchdog.py", line 96, in _tick
    tb = _capture_stall_traceback()
  File "/app/services/loop_watchdog.py", line 30, in _capture_stall_traceback
    return "".join(traceback.format_stack(frame))
```
Zero of 400 rows show any application frame (`main.py`,
`whale_stream_handlers.py`, `exit_engine.py`, anything under `services/`)
below the interpreter bootstrap. This is deterministic confirmation of the
structural argument above, not a coincidence of sample size: the mechanism
that would produce a *different* traceback (the blocker still executing when
sampled) cannot occur under this design, on this event-loop model, ever.

**This is why this document's mechanism for §4 rests on (b) live correlated
telemetry + (c) source-code proof, not (a) a captured stack of the blocker
itself** — the one diagnostic that could have delivered (a) turns out, on
direct reading, to be structurally incapable of it. §7 names the specific
fix.

## 4. The mechanism, read end to end

### 4.0 What's *not* the mechanism anymore (confirmed fixed, read directly)

`services/whale_stream/whale_stream_handlers.py:334-355`'s own comment
states the fix and cites its own task: *"2026-09-03, Task 4 of
`docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`
(§4.5 of the architecture-audit-second-pass research): `record_snapshot_
from_ticker`'s own `with _connect(DB_PATH)` SQLite write ran synchronously
on the event loop... the fix is here at the call site: schedule the WHOLE
call via `tick_executor.run()`."* Confirmed live in current source
(read directly, not assumed from the comment): the call is
`asyncio.create_task(tick_executor.run(lambda: market_history.
record_snapshot_from_ticker(...)))` — genuinely off the loop, onto the
2-worker pool. This *was* the architecture-audit-second-pass's own
best-named stall candidate (its §4.3); it is ruled out today, by direct
source read, as a current contributor. `series_watcher.record_book`'s own
buffer-full flush is likewise scheduled via `tick_executor.run` (line 289),
per PR #414's original fix, still holding.

### 4.1 What runs on every processed ticker message, unconditionally, on the loop

`_process_stream_ticker` (`services/whale_stream/whale_stream_handlers.py:251`),
after the (now-offloaded) snapshot write, at lines 356-387:
```python
if state["running"]:
    ...
    if state.get("signal_feed"):
        for close_decision in strategy.check_exits(
            state["latest_prices"], state["signal_feed"], cfg_now, state.get("market_results") or {},
            opened_since=now, category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
            latest_prices_updated_at=state["latest_prices_updated_at"],
        ):
            await _handle_close_decision(close_decision)
    for fill_decision in broker.check_pending_fills(...):
        await _handle_fill_decision(fill_decision, now)
    for close_decision in position_netting.review(
        broker, state["market_titles"], state["event_titles"], state["latest_prices"], cfg_now, now=now,
    ):
        await _handle_close_decision(close_decision)
```
None of the three calls (`check_exits`, `check_pending_fills`,
`position_netting.review`) is itself `await`ed — they are plain synchronous
function calls (confirmed: none of the three is declared `async def` at
their definitions, `services/strategy_engine.py:872`→delegates to
`services/exits/exit_engine.py:108`, `services/paper_broker.py:480`,
`services/exits/position_netting.py:374`). They run to completion, entirely
on the main thread, before the coroutine reaches its next `await`. This
wiring is deliberate, not accidental: `git log --oneline -- services/whale_stream/whale_stream_handlers.py`
shows `eaea541 feat: wire check_pending_fills/position_netting.review into
the WS ticker path (P8 Task 38)` as its own dedicated commit.

**Why only the ticker channel, not the trade channel, is analyzed below**
*(added 2026-09-03 per the adversarial review's F12)*: the tick_cache
commit's own scope note (§4.2) names "the **two**
`services/whale_stream/whale_stream_handlers.py` call sites," plural —
`_process_stream_trade` (same file, lines 170-249) also calls
`strategy.check_exits(...)` synchronously with no `tick_cache`, on the
trade-channel WS subscription. It is genuinely a second instance of the
same uncached-call defect, but it is reached only after `if not signals:
return` — i.e. only when the whale-scoring pipeline actually emitted a
signal for that specific trade, which §2.1's own `handler_time_by_class`
data shows is rare relative to raw trade volume ("trade" class averaging
2.57-5.27ms lifetime vs. "ticker" class's 46-113ms+, where the ticker call
is unconditional on `signal_feed` being non-empty, true most of the time in
normal operation). This document's §4 mechanism walkthrough is scoped to
the ticker-channel call site because that is where the volume is; the
trade-channel call site is the same defect at a rate too low to be the
dominant contributor to Condition 1/2, not a case this document overlooked.

### 4.2 `check_exits`: the memoization that exists, and is deliberately not used here

`services/exits/exit_engine.py:65-71`:
```python
def _cached(tick_cache: dict | None, key: tuple, fn, *args, **kwargs):
    if tick_cache is None:
        return fn(*args, **kwargs)
    if key not in tick_cache:
        tick_cache[key] = fn(*args, **kwargs)
    return tick_cache[key]
```
`check_exits` (`exit_engine.py:108`) iterates `for ticker, pos in
list(broker.positions.items())` — **every open position, not filtered to the
incoming ticker** — and, unconditionally, calls
`_cached(tick_cache, ("recent_price", ticker), market_history.recent_price, ticker, ...)`
(line 272). With `auto_exit_enabled` true (confirmed live,
`config/settings.yaml:83`), and none of the three hard rules (settlement /
take-profit / stop-loss / time-to-close) having already decided, it also
calls `_exit_confidence(...)`, which itself calls up to three more
`_cached(...)` sites: `("volatility", ticker)` → `market_history.volatility`
(line 523), `("analyst_lean", ...)` → `market_analyst_agent.analyst_lean`
(line 571), and `("series_stats", ticker)` → `signal_log.series_stats`
(line 586) — but the third is gated `if w_series > 0:` and
`auto_exit_series_track_record_weight` is `0` live (`config/settings.yaml:94`,
confirmed), so it does **not** actually fire today. **3 distinct DB-backed
reads actively fire per open position, per `check_exits` call, live today**
(`recent_price`, `volatility`, `analyst_lean`) — `series_stats` is real code
on this same uncached path but currently inert by config, named here so a
change to that one weight is understood to change this document's exact
read count, not silently assumed away.

*(Refined 2026-09-03 per the adversarial review's F11: `analyst_lean`
(`market_analyst_agent/per_market.py:168`) uses `_scoring_read_connection()`,
a **cached** scoring-read connection (Task 4, 2026-09-01) — a materially
different, cheaper connection strategy than `recent_price`/`volatility`'s
own fresh `with _connect(DB_PATH) as conn:` per call. All three are still
genuinely re-executed, uncached at the *result* level, on every qualifying
ticker message — `tick_cache` is `None` at this call site regardless of what
each function does internally for its own connection — but this document's
original "3 distinct DB-backed reads" framing flattened the three together
as equally costly, which they are not at the connection layer. Flagged here
so a future per-call cost estimate built on this document isn't built on
that flattened assumption.)*

`main.py:1288-1299`'s trading-loop call site (the *intended* per-tick caller,
at `poll_interval_sec: 6` — `config/settings.yaml:27`, `main.py:756-774`)
allocates `tick_cache: dict = {}` fresh each tick and passes it through —
confirmed live, current source, `grep -n tick_cache main.py`. The WS ticker
handler's call (§4.1 above) passes **no `tick_cache` argument at all** — the
parameter defaults to `None`, so every `_cached(...)` call inside
`check_exits`/`_exit_confidence` takes the `tick_cache is None` branch and
calls `fn()` directly, unconditionally, every single time.

**This is not an inferred gap — it is the exact, named, deliberate scope
boundary of the commit that built the cache**, `e787c8c` (2026-08-27,
`git show e787c8c`):
> "Wired only at main.py's real per-tick call site (trading_loop, right
> before `strategy.check_exits(...)`); the two
> `services/whale_stream/whale_stream_handlers.py` call sites are
> deliberately left unwired, out of this task's scope."
>
> "Task 17c's benchmark already showed the dominant cost is N *distinct*-
> ticker positions, which this cache can't help with — that needs a
> follow-up bulk-fetch or tick_executor offload (added as an unchecked item
> in the plan doc, right after this task)."

`exit_engine.check_exits`'s own docstring independently states the
magnitude that motivated building the cache at all: **"`market_history.
recent_price` alone costs ~103ms/tick at 500 open positions, called
unconditionally once per position with no caching"** (a pre-existing,
in-repo benchmark, `tests/test_check_exits_scale_benchmark.py`, commit
`c4fb61a`) — one read, of the 3-4 this document found actively firing, at
500 positions. At the currently-open-position count this session measured
live (`GET /api/health/pipeline` pull 1 → `price_staleness.open_position_
count: 27`), a naive linear scaling of that one benchmark alone gives
≈5.6ms; scaled up for 3-4 reads per position (not benchmarked directly by
this document — see §8 for what that would take) lands in the
tens-of-milliseconds range, the same order of magnitude as the live-measured
ticker-class handler cost in
§2.1 (46–113ms). This is offered as an **order-of-magnitude consistency
check against a pre-existing, independently-derived number**, not as a
substitute for directly benchmarking the WS-path call — flagged explicitly
in §8 as the next thing to measure if more precision is wanted.

### 4.3 `position_netting.review`: a second, independent uncached read, per position, per group

`services/exits/position_netting.py:374` has **no `tick_cache` parameter at
all** (confirmed: full function signature read, `def review(broker,
market_titles, event_titles, latest_prices, cfg, now=None)`). With
`position_netting.enabled: true` (confirmed live, `config/settings.yaml:220`),
`describe_groups(...)` (line 274) calls `_materiality_bar` (line 227) for
every group not already classified `locked_profit`/`locked_loss` — a
group already known to be a locked win or loss never triggers this read.
For every group that does reach it, `_materiality_bar` contains:
```python
vols = [market_history.volatility(ticker, vol_lookback, as_of=now) for ticker, _ in members]
```
(`position_netting.py:251`) — a second, entirely independent call to the same
`market_history.volatility` that `_exit_confidence` also calls (§4.2), with
**zero memoization of any kind**, on every WS ticker message, for every
member ticker of every open, variable-outcome netting group.

*(Corrected 2026-09-03 per the adversarial review's F10: the original text
attributed the `vols = [...]` line directly to `describe_groups` and called
it unconditional for every group. It is neither — it lives in the separate
`_materiality_bar` helper `describe_groups` calls, and only for groups not
already locked. This doesn't change the underlying point: `review()` still
reaches this uncached read for any variable-outcome group, on every ticker
message.)*

### 4.4 Why this reads as CPU-bound, not I/O-blocked, on `/proc`

`market_history.recent_price`/`volatility` (`services/market_history.py:343`,
`:309`, read in full) each do `with _connect(DB_PATH) as conn: conn.execute(...)`
— a fresh `sqlite3.connect()` + `PRAGMA`/schema setup + query + implicit
close-on-exit-of-`with`, per call (confirmed: `market_history.py` is one of
the 5 modules already fixed to close its connection properly, PR #499/#501,
per `docs/superpowers/research/2026-09-03-persistence-layer-db-migration.md`
— so this is real per-call connect/query/close overhead, not a leak, for
*this* module specifically). `volatility()` additionally does real
Python-level work after the fetch — `deltas = [prices[i+1]-prices[i] for
... ]`, `mean_delta = sum(deltas)/len(deltas)`, `variance = sum((d-mean_delta)**2
...)/len(deltas)` — a population-stdev computed in pure Python, holding the
GIL throughout. None of `check_exits`, `_exit_confidence`,
`position_netting.review`, or their SQLite calls `await` anything — so for
the duration of this whole chain, the event loop's one thread is doing real,
GIL-holding, mostly-CPU work (SQLite's index-seek queries are fast once
connected — `idx_snapshots_ticker_ts ON snapshots (ticker, timestamp)` exists,
confirmed — so most of the cost is Python-level `sqlite3.connect()`/pragma
overhead plus the pure-Python statistics, not disk-wait). This matches §2's
thread sampling exactly: the main thread in state `R` (running), not `D`
(uninterruptible/I/O-wait), for the overwhelming majority of every window
sampled.

## 5. The coordinator's live-captured escalation event (reported, not independently re-observed — the window passed)

The coordinator captured live diagnostics during a session-worst spike at
12:51Z: tick 31.25s (prior worst 19.39s), `loop_watchdog` 115.9/min (prior
worst 76.4/min), `/api/quality/summary` hit its 30s timeout, every endpoint
slow simultaneously, container CPU flat at 164% (within its normal 139–197%
band — i.e. **not** a CPU spike causing the stall), worker state `R` not `D`,
only 2 of 27 threads `R`, self-resolved within minutes. **This document
cannot re-observe that specific window — it already passed by the time this
investigation started** — but every element of its signature is independently
consistent with what this document verified on its own, live, at a different
moment:

- **CPU flat, not spiking, during a stall**: consistent with §4's mechanism
  being *always running* at some baseline rate (every processed ticker
  message, continuously) rather than triggered by a distinct rare event — a
  31.25s tick is the same mechanism compounding across an unusually long run
  of processed messages (or unusually many open positions/group members at
  that moment), not a different mechanism switching on.
- **`R` not `D`, 2/27 threads active**: exactly reproduced independently in
  §2's own three sampling runs, at a different time, using the same method.
- **Every endpoint slow together**: consistent with §4's mechanism being a
  single-threaded, un-awaited, loop-wide occupancy — while it runs, *nothing
  else* on the loop (any other route, any other WS message class) can be
  serviced, which is the defining signature of a genuinely blocking
  synchronous call inside an async handler, as the coordinator's own message
  named it.

### 5.1 `candidate_log.db`'s handle count: independently re-derived, not reproduced from the report

The coordinator flagged `candidate_log.db` at ~20 open handles (vs. 5 each
for `series_watcher.db`/`paper_broker.db`) as "notable and unexplained,"
asking whether 20 is genuine accumulation or ordinary
N-connections-×-3-files-per-connection multiplicity. Independently
re-measured, this session, via the host-PID access path (§1.1):
```
$ for f in /proc/1803872/fd/*; do readlink "$f"; done | grep '\.db' | sed 's#.*/##' | sort | uniq -c | sort -rn
     17 candidate_log.db
      1 candidate_log.db-wal
      1 candidate_log.db-shm
      6 signal_log.db
      5 series_watcher.db
      5 paper_broker.db
      5 market_history.db
      ...
```
**Not** N×3: every DB shown has ~5–17 *main*-file handles but only **1**
`-wal` and **1** `-shm` handle each, regardless of the main-file count — ruling
out "N independent connections, each holding all 3 files" as the shape.
Instead this is genuine per-call accumulation of un-closed main-file
descriptors, and `candidate_log.db`'s 17 (≈ the reported ~20, plausibly
one or two connections closed between the two reports) is proportionally
larger than the other DBs' 5–6 because **`candidate_log.py`'s own
`_connect()` is confirmed, by direct read, to leak** — `services/
candidate_log.py:76`, `def _connect() -> sqlite3.Connection: ...; conn =
sqlite3.connect(DB_PATH); ...; return conn` (no `@contextlib.contextmanager`),
called at 6 sites as `with _connect() as conn:`, which — per
`docs/superpowers/research/2026-09-03-persistence-layer-db-migration.md`'s
already-established finding for this exact code shape — only commits/rolls
back via `sqlite3.Connection`'s native context-manager protocol, never
closes. `candidate_log.db` sits on a far higher-volume path than
`series_watcher.db`/`paper_broker.db`: `GET /api/health/pipeline` shows
`ingest.gate_would_reject: 229819` (lifetime) — every one of those rejections
is a plausible `candidate_log` write. **This answers the coordinator's
question**: not N×3 multiplicity, and not something specific to
`candidate_log.db` beyond call volume — it is the same known, already-being-
remediated `_connect()`-leak class documented for 25 other modules (the
persistence-layer migration currently in its planning pipeline, PR #505),
directly confirmed here for `candidate_log.py` by this document's own read
(the persistence-layer doc's own self-review had flagged that only 2 of the
25 modules had been read in full, and `candidate_log.py` was not one of the
two — this document closes that specific gap).

### 5.2 `write_bytes`/`cancelled_write_bytes`: independently re-sampled, still unexplained as a mechanism, flagged as the coordinator did

Re-sampled live, same host-PID path:
```
$ cat /proc/1803872/io
...
write_bytes: 30139539456          # ≈30.14 GB (worker age at sample: ~3374s, ≈8.93 MB/s sustained)
cancelled_write_bytes: 23357345792 # ≈23.36 GB, 77.5% of write_bytes
```
This independently reproduces the coordinator's shape (28.8GB/22.2GB, 77%,
~8.7MB/s, sampled ~19 minutes earlier at the coordinator's report — both
numbers grew, ratio held within 1 point). **A plausible, source-grounded,
but not fully traced, contributing mechanism**: `candidate_log.py:76`'s
`_connect()` re-executes `conn.execute(capture_writer.REJECTED_CANDIDATES_DDL_SQL)`,
`conn.execute(capture_writer.REJECTION_EVENTS_DDL_SQL)`, and a `CREATE INDEX
IF NOT EXISTS` **on every single connect** — i.e. on every one of the
leaking connections identified in §5.1, at `gate_would_reject`'s 229,819-
lifetime volume. Even a no-op `CREATE TABLE`/`CREATE INDEX IF NOT EXISTS`
still touches SQLite's schema catalog inside a transaction and can journal
to WAL; whether *this specific* repeated-DDL pattern, vs. ordinary WAL
checkpoint behavior across the other 24 leaking `_connect()`-shaped modules,
vs. something else entirely, produces the 77% cancellation ratio was **not
established this session** — this document does not have a mechanism-level
trace connecting the two, only a plausible, source-verified candidate.
Flagged, per the coordinator's own framing, as a lead for a future session
with time to instrument WAL-checkpoint/temp-file behavior directly (e.g.
`PRAGMA wal_checkpoint` stats sampled around a connect burst), not claimed
as resolved here. It is a distinct finding from §4 (a write-side, disk-churn
question) and does not change §4's read-heavy, loop-blocking mechanism for
either of the task's two named conditions.

## 6. A related, real, but out-of-scope-for-mechanism finding: 1,311 zombie `git` processes

```
$ docker exec ddev-kalshi-whale-poc-fastapi sh /tmp/find_worker.sh | awk -F'\t' '{print $2}' | sort | uniq -c
   1311 git
```
All 1,311 confirmed `state=Z` (zombie), `ppid=1` (the uvicorn reloader,
which is also this container's de facto init and therefore inherits any
orphaned child), `utime=0 stime=0` (zombies report no further CPU; they
consume a process-table slot only). `starttime` range: 26,387,700–26,900,494
ticks → a burst spanning ≈85.5 minutes, ending ≈3.9 hours before this
document's sampling (current uptime 283,131.69s vs. max zombie starttime
269,004.94s). **Zero currently running, zero new ones observed forming during
this session's multiple samples** — this is a **closed historical event, not
an active contributor to either of the task's two conditions**, and this
document did not trace what specifically forked ~1,311 short-lived `git`
processes in that window (a `grep` for direct `subprocess`+`git` calls in
`services/`/`main.py` found none; `tools/project_manifest.py` does invoke
`git rev-parse HEAD` but is not called from the live app, ruled out as the
source by direct read). Named here so it is not silently lost, explicitly
**not** claimed to explain either symptom — PID 1 not reaping orphaned
children is itself a real, minor process-hygiene defect (unbounded zombie
accumulation over the container's lifetime), separate from this task's scope.

## 7. What this rules out, and on what basis

| Candidate | Verdict | Basis |
|---|---|---|
| PR #414's inline sync `flush()` (`record_cfbenchmarks`/`record_pyth`/`record_observation`/`record`/`record_book`) | Ruled out, still fixed | Read `services/series_watcher.py:record_book` (§4.0) — flush is scheduled via `tick_executor.run`, not inline |
| PR #409's per-trade fresh-connection whale-scoring / diagnostics-pool sharing | Ruled out as recurrence | Not touched by this document's mechanism (§4 is exit/netting logic, not whale-scoring or diagnostics); no evidence gathered contradicts PR #409 holding |
| PR #420/#424's `run_offline()`/`_aio_db` pool behavior | Ruled out as recurrence | `/api/quality/summary`'s slowness (§2.1, §5) is consistent with *this* document's mechanism (loop-wide stall blocks every route, including `/api/quality/summary`'s own handler from ever getting the loop) rather than a regression in the aiosqlite pool itself — not re-benchmarked directly, but no `_aio_db`/`diagnostics` code path appears anywhere in §4's call chain |
| Issue #510 (reset routes) | Confirmed not the cause, independently | §4's mechanism fires on *every processed ticker message* (~9.4/sec between this document's own two §2.1 pulls, ~6.5/sec average over a longer independent re-measurement during the adversarial review — corrected 2026-09-03 from an original overstated "10+/sec," per F9; either figure holds the conclusion) — orders of magnitude more frequent than an operator-triggered route; nothing in `services/reset/routes.py` appears anywhere in §4's call chain |
| `record_snapshot_from_ticker` (2026-09-02 audit's own candidate) | Ruled out, already fixed | §4.0 |
| Thread-pool/`tick_executor` starvation | Ruled out for this mechanism | §2's thread sampling: 24-25 of 27 threads at ~0% every time; the 2 `tick_executor` workers show real but modest, bounded usage, never saturated to the point of queueing (not independently re-benchmarked here, but not the shape §4's mechanism produces — that mechanism never touches `tick_executor` at all) |
| Disk I/O wait as the loop-stall mechanism | Ruled out for the specific escalation event | Coordinator's report: worker state `R` not `D` during the 31.25s tick (§5); this document's own §2 sampling: `R` state in all 3 runs |
| Worktree-count-driven reloader (`watchfiles` polling) | Confirmed real, but a *different* process (PID 1) and *not* the worker's own pin | §2, citing PR #515's own control measurement (worker CPU flat across worktree counts) |

## 8. What would be needed to go further

- **A direct benchmark of the WS-path call itself**, matching
  `tests/test_check_exits_scale_benchmark.py`'s existing method but at the
  *current* live open-position count and with `auto_exit_enabled`/
  `position_netting.enabled` both true (today's `test_check_exits_scale_
  benchmark.py` was not re-read for its exact parameterization in this
  session — that is the concrete next step, not assumed done here). This
  would replace §4.2's order-of-magnitude cross-check with an exact number.
- **A genuinely separate-thread stack sampler**, to actually deliver the
  `first_traceback` §3 shows the current design cannot: a dedicated
  `threading.Thread` (not an `asyncio.Task`) that wakes on a plain
  `time.sleep(N)` and calls `sys._current_frames()[main_thread_id]` **while
  the main thread is still inside its long call** — this works precisely
  because a real OS thread is not itself waiting on the blocked loop to
  schedule it. This is the same technique py-spy itself would use if it
  could attach; building a minimal in-process equivalent sidesteps the
  missing `CAP_SYS_PTRACE` entirely and would, unlike the current
  `loop_watchdog._tick()`, actually capture `exit_engine.py`/`market_history.py`
  frames mid-stall. Cost/safety per the data-plane HARD RULE: must be
  measured for hot-path overhead before shipping, same as `loop_watchdog`'s
  own existing 0.1s tick already was.
- ~~A live before/after measurement of threading `tick_cache` through the
  two WS call sites~~ — **done, 2026-09-03, PR #526** (`fix/whale-stream-
  ticker-handler-blocking`). See §10 below: the fix that shipped was a
  global min-interval throttle on the ticker-channel block (not per-message
  `tick_cache` alone, which turned out to be a no-op given the schema — see
  §10), and it was measured, not assumed. This document's own deferral was
  correct: implementation shape was genuinely a later-stage decision, made
  with information this research alone didn't have (the schema check that
  showed `tick_cache` wouldn't help by itself).
- **A WAL-checkpoint/temp-file trace** for §5.2's write/cancel-byte ratio —
  sampling `PRAGMA wal_checkpoint(PASSIVE)` stats or `strace -e trace=write,
  unlink` (itself blocked by the same missing `CAP_SYS_PTRACE` from inside
  the container — would need the same host-PID-namespace workaround this
  document used for `/proc/<pid>/io`, or a host-level `strace -p <hostpid>`)
  around a `candidate_log` connect burst, to move §5.2 from "plausible
  candidate" to a traced mechanism.

## 9. Dimensional analysis note

Per the HARD RULE, every quantity above carries its unit inline at first use
(ticks vs. seconds, converted via the confirmed `CLK_TCK=100`; bytes vs. GB
via ÷1e9; ms vs. s for handler timings, kept distinct from each other
throughout rather than mixed). The `dimensional-analysis` plugin itself was
not run as a separate pass over this document (it annotates *source code*
arithmetic, not a prose research document — consistent with how
`2026-09-02-architecture-audit-second-pass.md`'s own self-review handled the
same question for the same class of document, §Appendix "No dimensional-
analysis *plugin* pass was run on this document"). No code was written or
edited in this task, so there is no source-code arithmetic in scope for the
plugin to annotate.

## 10. Addendum (2026-09-03, post-review): the incident escalated further, then the mechanism was confirmed by a working fix

Two things happened after this document's own investigation window
(~12:45-16:05 UTC 2026-09-03) closed, neither of which changes the central
claim (§4) — both strengthen it.

**The condition got measurably worse, and a quantitative match appeared
that this document's own worst case never produced.** The adversarial
review's live re-check at 16:19 UTC (F13) found `last_tick_duration_sec:
1251.64` — a single tick took ~20.9 minutes, 40x this document's own
worst-recorded 31.25s. The coordinator (`autotrade-1d`) separately reported
the same escalation live: oldest stale position at 1,397.7s (23.3min) —
approximately the blocked tick's duration plus overhead. This is a
**quantitative** match, not merely directional: a generic "the app is
slow" story does not predict that the stalest position's age should
approximate the longest tick's duration; "one synchronous call blocked the
event loop wholesale, and everything else queues behind it until it
returns" predicts exactly that relationship.

**A trap in reading `last_tick_duration_sec` is the reason this went
undiagnosed for hours, and belongs in this document, not just in chat.**
Early in the incident, `last_tick_duration_sec` read 3.81s while every other
signal (stale positions climbing, `stores_probe_ms` climbing, `/api/health/
pipeline` itself taking 39.2s to answer) said something was badly wrong.
That field reports the duration of the last **completed** tick — a
healthy-looking value mid-incident means the current tick is still running
and simply hasn't finished yet, not that the tick loop is fine. The 1,251s
figure above is that same tick finally completing. Anyone reading this
field during a live incident should treat a suspiciously-healthy value as
"still running," not "healthy," and cross-check against oldest-stale-
position age or a fresh timestamp read before concluding the tick loop
itself is not the problem.

**The mechanism was independently confirmed by a working fix, not just by
review.** PR #526 (`fix/whale-stream-ticker-handler-blocking`, opened
2026-09-03, own review cycle in progress at the time this addendum was
written — not yet merged) implemented and measured a fix for exactly the
call chain §4 identifies. Two things from that PR are worth folding back
into this document's own findings regardless of its own merge status:

- **`tick_cache` alone would not have helped.** §4.2/§8 (before this
  addendum) treated wiring `tick_cache` through the WS call sites as *the*
  deferred fix. PR #526 checked the schema before implementing and found
  `positions` is `ticker TEXT PRIMARY KEY` — one position per ticker, always
  — and `_exit_confidence` is called exactly once per position per
  `check_exits` invocation (exit_engine.py:117,399). `tick_cache` only saves
  work when the *same* key is looked up more than once within one call;
  given those two facts, no key in the cache is ever read back, so wiring
  it changes nothing measurable. This document's own §4.2 commit quote
  already hinted at this — "Task 17c's benchmark already showed the
  dominant cost is N *distinct*-ticker positions, which this cache can't
  help with" — but neither this document nor its reviews connected that
  quote to "therefore tick_cache is a no-op here" before the fix's own
  implementation checked it directly. The actual fix that reduced cost was
  a **global min-interval throttle** (`kalshi.
  ticker_exit_check_min_interval_sec`, default 2.0s) capping how often the
  whole block runs — orthogonal to `tick_cache`, and the piece this
  document's own §8 correctly identified as needing a later-stage
  implementation decision.
- **The predicted magnitude held.** An isolated, deterministic benchmark
  (500 synthetic ticker messages, 30 open positions across 726 markets,
  before vs. after the throttle) measured **500/500 calls reaching the
  exit-check block before the fix, 1/500 after**, and aggregate blocking
  cost per simulated second of 10 msg/s traffic falling from **608.0ms/s to
  25.0ms/s** — a ~24x reduction. This is independent, measured
  corroboration of the mechanism §4 describes from source alone: the fix
  that removes the specific call chain this document identifies produces
  almost exactly the order-of-magnitude improvement the invocation-rate
  math predicts.
- **Live post-merge verification (Gate 2) is still owed, separately from
  this document.** The benchmark above proves the code is cheaper; it does
  not by itself prove the *incident's* symptoms (queue depth, ingest wait
  times, stale-position age) resolve in production — those are emergent
  properties of the live system under real load. PR #526 states this
  explicitly as an open item, not a claim of "incident closed."

**The `loop_watchdog` structural gap (§3) is filed as its own issue, #527**,
per the standing rule that a finding living only inside a merged research
document is a finding nobody acts on. Its fix (a genuinely separate OS
thread, not another same-loop `asyncio.Task`) is named there, not
re-litigated here.

## Appendix — evidence log

- `docker exec ddev-kalshi-whale-poc-fastapi getconf CLK_TCK` → `100`
- `docker exec ddev-kalshi-whale-poc-fastapi sh /tmp/find_worker.sh` (a
  `/proc/[0-9]*/comm,cmdline` walk) → container PID 25541 identified as the
  `spawn_main` worker
- `docker exec ddev-kalshi-whale-poc-fastapi sh /tmp/worker_uptime.sh` →
  worker `starttime_ticks=28025647`, sampled against `/proc/uptime`
- `docker exec ddev-kalshi-whale-poc-fastapi python3 /tmp/thread_cpu.py 25541 <10|10|12>`
  → 3 independent per-thread CPU-delta samples, §2
  (`/tmp/thread_cpu.py`: reads `/proc/<pid>/task/*/stat` before/after a fixed
  `time.sleep(window)`, computes ticks→seconds→percent per thread)
- `docker exec ddev-kalshi-whale-poc-fastapi python3 /tmp/thread_cpu.py 1 <window>`
  → PID 1 (reloader) sample, §2
- `docker top ddev-kalshi-whale-poc-fastapi -eo pid,ppid,args` → host-PID
  mapping (25541 → 1803872), §1.1
- `cat /proc/1803872/io` (host, uid 1000, no `docker exec`) → §5.2
- `for f in /proc/1803872/fd/*; do readlink "$f"; done | grep '\.db' | ...`
  (host, uid 1000) → §5.1 fd census
- `curl -sk https://kalshi-whale-poc.ddev.site:8443/api/health/pipeline` ×2,
  10.4 minutes apart (`generated_at` 12:49:10Z, 12:59:34Z) → §2.1's
  `handler_time_by_class` table, `open_position_count`, `gate_would_reject`
- `curl -sk 'https://kalshi-whale-poc.ddev.site:8443/api/health/faults?component=loop_watchdog&limit=400'`
  → §3's 400-row traceback-shape census
  (`python3 -c "... tb.count('File \"') ..."` grouping by tail)
  and 1-row spot-check for `count`/`first_seen`/`last_seen`
- `curl -sk https://kalshi-whale-poc.ddev.site:8443/api/quality/summary -o /dev/null -w '%{time_total}'`
  ×2 → 34.5s, 15.3s (both cited in the intro's "never once returned healthy"
  framing)
- `grep -n auto_exit_enabled\|exit_on_sentiment_reversal\|position_netting: -A5 config/settings.yaml`
  → `auto_exit_enabled: true` (line 83), `position_netting.enabled: true`
  (line 220), both read directly from the live config file, not assumed
- `git show e787c8c`, `git log --oneline --grep=tick_cache -i --all`,
  `git log --oneline -- services/whale_stream/whale_stream_handlers.py`
  → §4.2's commit-message evidence
- `gh pr view 414|409|420|424 --json title,body,mergedAt,number` → §7's
  rule-out basis; all four read in full before forming any hypothesis, per
  the task's own instruction
- `gh pr view 515 --json title,body` → §2's reloader/worktree-count
  reconciliation
- Direct `Read`/`grep -n` of: `services/whale_stream/whale_stream_handlers.py`,
  `services/exits/exit_engine.py`, `services/exits/position_netting.py`,
  `services/paper_broker.py`, `services/strategy_engine.py`,
  `services/market_history.py`, `services/candidate_log.py`,
  `services/loop_watchdog.py`, `main.py` — every source claim above is a
  read of the current file at this document's HEAD, not a recollection
