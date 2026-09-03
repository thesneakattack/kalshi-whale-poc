# Research: root cause of the `_seen_trade_ids`/`_seen_order` concurrency hazard (issue #546)

2026-09-03, ~22:00-22:30 UTC. Assigned directly by autotrade-ce (coordinator) after
adversarial review of a separate document (the trade-resolve solution comparison,
PR #547) surfaced this as a must-fix finding (F6) and it was filed as its own issue,
#546. The adversarial review's own finding was explicitly source-derived reasoning
from two contradicting docstrings plus a traced call graph, not an empirical
reproduction, and it flagged the exact interleaving as "narrow... inside `_mark_seen`'s
multi-statement eviction check." The coordinator's ask: trace the actual call graph
and locking (or lack of it) — not just the docstring contradiction — and determine
what a real fix looks like.

**No code, config, or live-app changes were made.** Every claim below is a direct
`grep`/`Read` of current source or a `git log`/`show`, cited inline.

**Top-line finding:** the hazard is real and confirmed — zero locking exists anywhere
in the file (`grep` for `Lock`/`threading.` returns nothing). But the precise
mechanism is narrower and different from what the adversarial review's hedge
suggested: it is **not** primarily the eviction loop's multi-statement sequence
(concurrent `_mark_seen` calls for *different* trade_ids do not structurally corrupt
the ring, traced in detail below) — it is a **check-then-act race on the "already
seen?" gate for the *same* trade_id**, and the code's own design explicitly creates
the exact condition that puts one trade_id into both racing paths: a trade whose
first resolve attempt failed is deliberately left eligible for *either* a later WS
re-presentation *or* the candidate-retry queue (`kalshi_trade_tape.py`'s own H4/Task-11
comment), and nothing prevents both from processing it at once. The git history shows
precisely how this gap was introduced: the safety claim was correct when written
(2026-08-11) and was invalidated by two later, independent, unrelated commits
(2026-08-28, 2026-09-01) neither of which revisited it. This codebase has already
established the correct fix pattern for exactly this shape of bug elsewhere
(`series_watcher.py`'s `_buffer_lock`, created via code review for the *same module
family*) — the fix here is applying an existing, proven convention, not inventing one.

## 1. No locking exists — confirmed directly, not inferred

```
$ grep -n "_mark_seen\|_seen_trade_ids\|_seen_order\|Lock\|threading\." services/whalewatchers/kalshi_trade_tape.py
```

Returns every reference to the dedupe ring and zero matches for `Lock` or
`threading.` anywhere in the file. `_mark_seen` (`kalshi_trade_tape.py:263-270`):

```python
def _mark_seen(self, trade_id: str, exchange_ts: float | None = None) -> None:
    if trade_id in self._seen_trade_ids:
        return
    self._seen_trade_ids.add(trade_id)
    self._seen_order.append((trade_id, float(exchange_ts) if exchange_ts is not None else time.time()))
    while len(self._seen_order) > _MAX_SEEN_TRADE_IDS:
        oldest, _ = self._seen_order.popleft()
        self._seen_trade_ids.discard(oldest)
```

No lock anywhere in the class (`__init__`, `kalshi_trade_tape.py:222-249`, declares
`_seen_trade_ids`, `_seen_order`, `_market_cache`, `_resolve_failed_tickers`, `stats`
— no synchronization primitive among them).

## 2. Where `_mark_seen` actually runs, and the real TOCTOU window

`_mark_seen` is called from exactly one place: `_process_trades_sync`
(`kalshi_trade_tape.py:564`), inside a per-trade loop over `trade_tape`. The relevant
span (`kalshi_trade_tape.py:539-564`):

```python
for trade in trade_tape:
    trade_id = trade.get("trade_id")
    if not trade_id or trade_id in self._seen_trade_ids:      # line 540-541: the gate
        continue
    ...                                                        # lines 542-561: series
                                                                 # bookkeeping, dict lookup
    if market is not None or ticker not in self._resolve_failed_tickers:
        self._mark_seen(trade_id, exchange_ts=...)              # line 564: the mark
```

The window between the gate (541) and the mark (564) is short — a counter bump, a
`signal_log.series_of()` call, a dict update, and `markets_by_ticker.get(ticker)`
(a plain dict lookup; `markets_by_ticker` was already fully populated by
`_resolve_unknown_markets` *before* `_process_trades_sync` starts, per `fetch_signals`'s
own sequencing at `kalshi_trade_tape.py:284-333` — no blocking I/O happens between
541 and 564). This is a genuine but **narrow** window, not a window spanning the
whole scoring pipeline — worth stating precisely rather than leaving as vague as
"somewhere in this function," since the width of the window is exactly what
determines how likely the race is to fire in practice.

**`_process_trades_sync` genuinely executes on a worker thread**, not the event
loop, in both call paths that reach it — confirmed by tracing both callers:
- `_process_trades_timed` (`kalshi_trade_tape.py:335-339`, called from `fetch_signals`,
  the WS-stream path's only route to this code) calls
  `_scoring_pool.run(lambda: self._process_trades_sync(...))`.
- `score_recovered_trade` (`kalshi_trade_tape.py:342-364`, the candidate-retry path's
  only route) calls the identical `_scoring_pool.run(lambda: self._process_trades_sync(...))`.

`_scoring_pool.run` (`_scoring_pool.py:39-41`): `return await
loop.run_in_executor(_executor, fn)` — `_executor` is a module-level 4-worker
`ThreadPoolExecutor` (`_scoring_pool.py:31`), shared process-wide by this module.
Both call paths dispatch to the same pool of OS threads.

## 3. The two paths are genuinely concurrent — traced, not assumed

`_candidate_retry_loop` (`main.py:777-806`) is its own independently-supervised
`asyncio` task (`task_supervisor.supervise(_candidate_retry_loop, ...,
restart=True)`, `main.py:1436`), running alongside — not serialized with — the WS
stream's own supervised task. Its body: `while True: await
asyncio.sleep(_SCHEDULER_TRIGGER_INTERVAL_SEC)` (5.0s), then, gated only on
`state["running"]`, `_streaming_trade_tape_enabled()` (true right now — the live
default), and `candidate_retry.snapshot().get("pending", 0) > 0`, it calls
`candidate_retry.run_pending(...)`, which (`candidate_retry.py:167`) does `for
trade... await provider.score_recovered_trade(trade, market, cfg, now)` — one at a
time internally, but with **zero check against, or coordination with, the WS
consumer's own activity**. Read the entire loop body (`main.py:777-806`) looking
specifically for a lock, a semaphore, a flag check against the WS path, or any
other synchronization: there is none. The gate `_streaming_trade_tape_enabled()`
being required for this loop to do real work actually *increases* the overlap
window rather than reducing it — candidate-retry only does meaningful work exactly
when the WS-streaming path is also live.

Since these are two independent `asyncio` tasks on the same event loop, and each
independently dispatches to the shared thread pool via `await
loop.run_in_executor(...)`, both can have an in-flight submission at the same
moment — the event loop freely interleaves at each task's own await points, and
nothing here prevents both from being mid-flight simultaneously. With 4 worker
threads available, both submissions can execute in true OS-thread parallelism, not
merely interleaved coroutine scheduling.

## 4. What does — and does not — actually break under concurrent access

Traced precisely rather than left as "a hazard exists":

**Concurrent `_mark_seen` calls for *different* trade_ids do not structurally
corrupt the ring.** Each of `set.add`, `set.discard`, `set.__contains__`,
`deque.append`, `deque.popleft`, and `len()` is an individual C-level operation that
completes atomically under CPython's GIL — the GIL does not release mid-call for
pure in-memory container operations, so two threads calling `.add()` on the same set
back-to-back cannot corrupt the set's internal structure. The eviction pair
(`oldest, _ = self._seen_order.popleft()` then `self._seen_trade_ids.discard(oldest)`)
is safe against cross-thread interleaving specifically because `oldest` is a
thread-local stack variable — each thread's own `popleft()` result is unique
(deque's C implementation guarantees no two concurrent `popleft()` calls return the
same logical element), and each thread's subsequent `discard()` always operates on
its *own* popped value, never a value read from shared state. This is a genuine
correction to the adversarial review's own hedge ("narrow interleaving inside
`_mark_seen`'s multi-statement eviction check") — the eviction loop, specifically,
is safe; the actual risk is elsewhere.

**The real risk is the check-then-act gate for the *same* trade_id (§2's window).**
Thread A (WS) and Thread B (candidate-retry), both processing trade_id X:

```
A: if X in self._seen_trade_ids: → False (not yet marked)
B: if X in self._seen_trade_ids: → False (A hasn't marked it yet)
A: ... proceeds to score X, eventually calls self._mark_seen(X)
B: ... proceeds to score X (again), eventually calls self._mark_seen(X)
```

Both threads pass the gate, both fully score the same real-world trade, both may
independently reach `_handle_signal` and emit a signal for it — a genuine duplicate,
not merely a bookkeeping inconsistency. `_seen_order` also ends up with two entries
for the same `trade_id` (a `deque` permits duplicates; `_seen_trade_ids`, a `set`,
does not — so the two structures diverge in count, though the eviction loop
self-corrects the *ring size* over subsequent calls even though the *semantic*
double-processing already happened and cannot be undone after the fact).

**How likely is this in practice?** It requires trade_id X to be simultaneously
`(a)` pending in `candidate_retry`'s queue (meaning its first resolve attempt failed
or was capacity-truncated — see `kalshi_trade_tape.py:434-448`,
`_resolve_unknown_markets`'s own `truncated`/exception handling, both of which call
`candidate_retry.enqueue`) and `(b)` re-presented on the live WS trade stream around
the same moment. The code's own H4/Task-11 design comment
(`kalshi_trade_tape.py:452-458`) explicitly names *both* paths as valid recovery
routes for the same once-failed trade_id — "a real chance on a later presentation
instead (this trade_id's next appearance in the trade tape, **or** Task 12's retry
queue)" — meaning the system's own design already anticipates a trade_id can
legitimately travel through either path, just not (as far as this investigation can
tell) through the possibility of *both at once*. Whether Kalshi's own stream
redelivers/replays a trade close in time to a resolve failure is outside this
document's scope to determine from source alone — genuinely worth checking against
`docs/kalshi/` per this repo's Kalshi-integration-authority rule before treating the
probability as settled, not done here.

## 5. Git-historical root cause: a documented invariant, correct when written, silently invalidated twice

```
$ git log -1 --format="%ci %s" a84d35c
2026-08-11 00:00:04 -0500 "Fix trade-tape caps silently dropping trades before whale
  detection; harden the async/SQLite hot path against a real live incident"
```

This is the commit that introduced the safety claim currently sitting in
`_process_trades_sync`'s docstring: "`self._seen_trade_ids`/`self._seen_order` are
only ever touched from within one in-flight `fetch_signals()` call at a time (the
trading loop awaits each tick's whale-provider call before starting the next)." At
this date, **this was true** — `candidate_retry.run_pending` was still called inline
from the trading loop's own tick body (P2 Task 13), so only one caller of
`fetch_signals`/`_process_trades_sync` existed, and the tick loop's own serial
structure genuinely enforced "one in-flight call at a time."

```
$ git log -1 --format="%ci %s" 7beb658
2026-08-28 03:25:56 -0500 "refactor: give candidate_retry.run_pending its own
  supervised loop (P8 Task 37)"
```

17 days later, this commit extracted `candidate_retry.run_pending` into its own
independently-supervised task — the exact serialization the 2026-08-11 docstring's
parenthetical relies on ("the trading loop awaits each tick's whale-provider call
before starting the next") no longer holds, since candidate-retry no longer runs
inside the trading loop's tick body at all.

```
$ git log -1 --format="%ci %s" 3ff41d4
2026-09-01 09:50:43 -0500 "feat: add dedicated worker pool + connection cache for
  whale-scoring reads"
```

4 days after that, this commit added `_scoring_pool.py` itself — its own module
docstring states explicitly it exists for "**both** the WS-message path,
`_process_stream_trade -> fetch_signals`, **and** the candidate-retry path,
`score_recovered_trade`," sized "headroom for legitimate brief overlap **plus the
candidate-retry path**." (Confirmed this specific commit only *added* `_scoring_pool.py`
and its test file, `git show 3ff41d4 --stat` — it did not touch
`kalshi_trade_tape.py`, so the docstring wiring these two paths onto the shared pool
happened in a still-later, unexamined commit — not needed to establish the point
here, since F2/F1 in the prior adversarial review already confirmed both call sites
use `_scoring_pool.run` in the *current* source.)

**Neither commit revisited `_process_trades_sync`'s own safety claim.** Two
independent, 17-and-4-days-apart, plausibly unrelated pieces of work (a scheduler
refactor, a capacity/connection-reuse feature) each removed one of the two
conditions the original claim depended on — first the tick-loop serialization,
then the caller isolation — and neither touched the docstring making the claim.
This is a textbook "documented invariant silently invalidated by later, unrelated
changes" pattern, not a design that was ever wrong when written.

## 6. This codebase already has the correct fix pattern, elsewhere, for the identical shape of bug

`services/series_watcher.py`'s `_buffer_lock` (`series_watcher.py:126-142`) was
added for **exactly this shape of hazard, in an adjacent part of the same module
family**:

> `_buffer_lock` (code-review fix, finding #2 - `/code-review` high pass against PR
> #23, originally guarding both buffers): a `tick_executor` worker thread
> (`main.py`'s `_flush_trade_capture_async`...) and the main asyncio event-loop
> thread (`services/whale_stream/whale_stream_handlers.py`'s
> `_process_stream_trade`/`_process_stream_ticker`) both touch `_book_buffer`, and
> `flush()`'s swap-and-clear was never synchronized against a concurrent `.append()`
> or a concurrent second `flush()`.

Same failure family (a worker thread and an event-loop-driven caller touching
shared mutable state with zero synchronization), same module family
(`whale_stream_handlers.py` is a direct participant in both cases), found and fixed
via code review previously in this exact codebase. `services/game_state.py:73`
states the governing rule explicitly: "`threading.Lock`, not `asyncio.Lock` - the
two real callers are on different OS threads" — directly applicable here, since
`_process_trades_sync` genuinely executes on `_scoring_pool`'s worker threads, not
the event loop. `grep` for `_buffer_lock` also surfaces the same pattern already
applied in `services/index_feed/ingestion.py` and `services/settlement_edge.py` —
this is an established, repeated convention in this codebase, not something to
invent from scratch.

## 7. What a real fix looks like

Two candidate shapes, not a full solution-family comparison (this problem's
solution space is much narrower than #542's — it is "restore a previously-true,
previously-relied-upon invariant using this codebase's own established primitive,"
not a genuine multi-way architectural tradeoff):

**A. `threading.Lock` around the point of access** (recommended, following the
`series_watcher.py`/`game_state.py` precedent directly): wrap the check-through-mark
span inside `_process_trades_sync`'s per-trade loop (§2's window) — and, for
consistency, `_mark_seen` itself — in a module- or instance-level `threading.Lock`.
Protects the actual shared state at every point it's touched, regardless of which
caller reaches it; robust even if a future third caller is added without knowing
about the dispatch-site discipline option B below would require.

**B. `asyncio.Lock` at the two dispatch sites** (weaker, not recommended as the
primary fix): wrap `await _scoring_pool.run(...)` at both call sites
(`_process_trades_timed` and `score_recovered_trade`) in a shared `asyncio.Lock`,
serializing the two paths' submissions to the pool entirely — mechanically restores
the exact "one in-flight call at a time" invariant the stale docstring already
claims. Works, but is fragile: it depends on every future caller of
`_process_trades_sync`-via-the-pool remembering to acquire the same lock; option A
protects the data itself rather than relying on caller discipline.

Neither was implemented or benchmarked here — this is root-cause and fix-direction
only, per the coordinator's ask, not an implementation. Lock-acquisition cost for a
`threading.Lock` around a handful of dict/set operations is well-established as
negligible (this codebase already accepts it in `series_watcher.py` on a comparably
hot path), but per CLAUDE.md's data-plane HARD RULE any change to this
exchange-wide-adjacent path still needs its own runtime-cost check before shipping —
named here, not measured.

## Not resolved here

- Whether Kalshi's own stream redelivery/replay semantics make the same-trade_id
  overlap (§4) a realistic, observed-frequency event or a purely theoretical edge
  case — flagged above as needing a `docs/kalshi/` check, not done in this pass.
- No live evidence search was repeated here beyond what the previous session already
  did (checked `signal_log.db`/`candidate_log.db` for a `trade_id` column — neither
  has one, so no persisted trail exists to check for an actual historical duplicate;
  checked 2,000 `fault_log` entries for any exception trace through this code path —
  zero hits, weak evidence given the likely failure mode is silent state corruption,
  not an exception).
- Exact lock scope/granularity (module-level vs. per-instance, whether it should also
  cover `_market_cache` even though the trade-resolve solution-comparison document's
  adversarial review (F6, PR #547) found that structure safe under the *current*
  call graph) is a design-stage decision, not settled here.

## Appendix — evidence log

- `grep -n "_mark_seen\|_seen_trade_ids\|_seen_order\|Lock\|threading\."
  services/whalewatchers/kalshi_trade_tape.py` → §1, confirms zero locking
- `Read` of `kalshi_trade_tape.py:210-290` (class init, `_mark_seen`,
  `seen_exchange_ts_by_id`, `seen_horizon_ts`) and `:335-364`
  (`score_recovered_trade`) and `:500-575` (`_process_trades_sync`'s docstring and
  per-trade loop) → §2, §4's exact code and line numbers
- `Read` of `services/whalewatchers/_scoring_pool.py` in full → §2's `run()`
  implementation, the 4-worker `ThreadPoolExecutor`
- `Read` of `main.py:777-806` (`_candidate_retry_loop`) and `:1414-1470`
  (`task_supervisor.supervise` call sites) → §3's independent-task confirmation
- `Read` of `services/candidate_retry.py:103-170` (`run_pending`) → §3's sequential
  internal dispatch, single `score_recovered_trade` call site
- `git log -1 --format="%ci %s" a84d35c` / `7beb658` / `3ff41d4` → §5's three-commit
  ordering
- `git merge-base --is-ancestor a84d35c 3ff41d4` → confirms ordering direction
- `git show 3ff41d4 --stat` → confirms this commit only added `_scoring_pool.py` +
  its test file, did not touch `kalshi_trade_tape.py`
- `grep -n "_buffer_lock" -B2 -A8 services/series_watcher.py` → §6's precedent, the
  exact PR #23 code-review-finding comment
- `Read` of `services/game_state.py:65-75` → §6's `threading.Lock, not asyncio.Lock`
  rule, stated explicitly in this codebase already
- `grep -n "asyncio.Lock" services/**/*.py services/*.py main.py` → §7's
  precedent survey (`http_client.py`, `websocket.py`, `ws_manager.py`,
  `_aio_db.py` all use `asyncio.Lock` for same-event-loop coordination — contrasted
  with `threading.Lock` for the actually-different-OS-thread case this issue is)
- Prior session's checks (relayed, not re-run here): `signal_log.db`/`candidate_log.db`
  schema (no `trade_id` column in either), `/api/health/faults?limit=2000` grep
  (zero hits) — cited in "Not resolved here," not re-verified in this pass
- Issue #546, the adversarial review finding F6 it was filed from (both read in full
  as the starting point for this investigation)
