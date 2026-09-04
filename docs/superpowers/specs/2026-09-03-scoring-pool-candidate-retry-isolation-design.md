# Design: isolate `_candidate_retry_loop` from the WS-trade-scoring pool

2026-09-03/04, ~23:20-00:10 CDT/UTC. Design/spec stage per CLAUDE.md's "nothing advances on one
pass" — builds directly on the just-consolidated research
(`docs/superpowers/research/2026-09-03-trade-stream-decoupling-and-history-event-driven-research.md`,
PR #562) and issue #563, which that research filed for exactly this gap: the one coupling axis
of David's standing decoupling priority with **no existing fix and no prior tracked issue**
(unlike `tick_executor`/issue #410, already scoped and deliberately deferred). Not re-deriving
that research's findings; extending them into a design with a recommendation, per CLAUDE.md's
data-plane HARD RULE ("a confirmed bottleneck gets competing solution families compared on
mechanism, benchmark, correctness, failure behavior, and complexity").

**No code, config, or live-app changes were made writing this document.**

## 1. The problem, and a fact this document adds that neither the research doc nor the original
   pool design could have known

`services/whalewatchers/_scoring_pool.py` (`ThreadPoolExecutor(max_workers=4,
thread_name_prefix="whale-scoring")`, unchanged since its creation — `git log --oneline --
services/whalewatchers/_scoring_pool.py` shows exactly one commit) is shared between two
independently-triggered callers:

- **WS-trade path**: `services/whale_stream/whale_stream_handlers.py`'s `_process_stream_trade`
  → `kalshi_trade_tape.py`'s `fetch_signals()` (line 364, `await _scoring_pool.run(...)`).
- **Candidate-retry path**: `main.py`'s `_candidate_retry_loop` (wakes every
  `_SCHEDULER_TRIGGER_INTERVAL_SEC`, only acts when something is pending) →
  `services/candidate_retry.py`'s `run_pending()` → `kalshi_trade_tape.py`'s
  `score_recovered_trade()` (line 415, `await _scoring_pool.run(...)`).

The pool's own module docstring states its sizing rationale explicitly: **"4 workers: this call
path normally needs ~1 concurrently (the WS consumer drains one queue item at a time) - headroom
for legitimate brief overlap plus the candidate-retry path, not a load-bearing capacity guess."**
The original design doc (`docs/superpowers/specs/2026-09-01-whale-scoring-connection-reuse-design.md`
§4) repeats the same reasoning and made a deliberate, considered choice to share this pool
between both callers — this was not an oversight, and its own review doc
(`...-design-review.md`) confirms the sharing was examined, not missed.

**That reasoning is now stale, and neither this document's predecessors had reason to notice —
PR #555 (Option B, merged after the pool's own design was written) changed the WS path's own
concurrency profile.** Verified directly: `services/kalshi/websocket.py`'s `_dispatch_trade_concurrent`
now dispatches each trade item as its own concurrent task, bounded by a 4-slot semaphore
(`_TRADE_DISPATCH_CONCURRENCY = 4`) — so up to 4 trades can be concurrently inside
`_process_stream_trade` → `fetch_signals` → `_scoring_pool.run(...)` at once, not "~1... the WS
consumer drains one queue item at a time" as the pool's own docstring still says (unchanged by
PR #555 — `_scoring_pool.py` was not touched by that PR). **The WS path alone can now
legitimately want all 4 of `_scoring_pool`'s workers**, before the candidate-retry path submits
anything.

The candidate-retry path's own submission pattern, by contrast, is confirmed strictly serial:
`candidate_retry.py:126,167`'s `run_pending()` iterates `for trade_id in list(_pending): ...
await provider.score_recovered_trade(...)` — one `await` per trade, no `asyncio.gather`/
`create_task` fan-out. At most 1 concurrent `_scoring_pool` submission from this path, ever.

**The concrete failure mode this produces**: during a WS burst that saturates all 4 semaphore
slots (the research doc's §1.1 correction already established this is plausible, not purely
theoretical, citing PR #558's "a burst of correlated whale signals can stack two+ long-tail
draws back to back" observation in the same subsystem), a candidate-retry submission arriving
in that window queues behind all 4 WS-path scoring calls on the shared pool — the retry loop's
own catch-up work for signals that were *already missed once* gets delayed further by exactly
the kind of burst that caused them to be missed in the first place. Not measured directly here
(no synthetic benchmark was run — see §5), but the mechanism is real and now more probable than
when the original design assumed ~1 concurrent WS-path caller.

## 2. Constraints

- **Completeness (CLAUDE.md's data-plane HARD RULE)**: no design option below drops, delays
  indefinitely, or silently merges a trade or a recovered candidate. Both paths keep scoring
  every item they're given; only *which pool's thread* runs that scoring changes.
- **No capacity/thread-count change "because it should help"**: the WS path's own worker need
  (up to 4, established in §1) is not itself in question here — that bound was already
  established by PR #555 and is not this document's job to re-tune. What this document scopes
  is *whether the retry path shares that specific pool*, not whether 4 is the right number for
  the WS path alone.
- **Correctness precondition, verified before any option below is viable**: the #546 fix (PR
  #555 — `self._seen_lock` guarding `_seen_trade_ids`/`_seen_order`) must keep working
  regardless of which pool runs `_process_trades_sync`. Confirmed directly:
  `kalshi_trade_tape.py:273`'s `self._seen_lock = threading.Lock()` is an **instance** attribute
  on the provider object — a real OS-level mutex, not scoped to a particular
  `ThreadPoolExecutor`. Any thread from any pool that holds a reference to the same provider
  instance correctly contends for the same lock. **Splitting the pool does not reopen #546** —
  verified, not assumed.
- **Exchange-wide hot path measurement**: the WS-path side of `_scoring_pool` remains
  unmeasured-here per CLAUDE.md's requirement for hot-path changes; this document's own
  benchmark section (§5) is explicit about what is and isn't measured.

## 3. Options compared

### Option 1 (recommended): give `_candidate_retry_loop` its own dedicated pool

**Mechanism.** New module, same shape as `_scoring_pool.py` itself and its own retired sibling
`services/diagnostics/_diagnostics_pool.py` (deleted once `run_offline()` moved to aiosqlite —
`services/quality/routes.py:130-142`'s comment; the *pattern* of "give an isolable workload its
own small pool" survives that specific instance's retirement, it's the precedent this option
reuses, not a contradiction of it). `services/candidate_retry.py` (or a new
`services/whalewatchers/_candidate_retry_pool.py`, mirroring the existing module-per-pool
convention) gets `ThreadPoolExecutor(max_workers=1, thread_name_prefix="candidate-retry-scoring")`.
`score_recovered_trade()`'s call site (`kalshi_trade_tape.py:415`) is parameterized (or a second
`run()` wrapper is added) to route through the new pool instead of `_scoring_pool`; `fetch_signals()`
(line 364) is untouched, keeps using `_scoring_pool` exclusively.

**Sizing: 1 worker, not the existing pool's 4** — this is the one number this design actually
sets, and it's justified from §1's own already-established fact, not a fresh guess:
`run_pending()`'s strictly serial submission pattern means at most 1 concurrent call from this
path ever occurs; a 2nd worker would sit permanently idle. If `run_pending()`'s own concurrency
model ever changes (e.g., a future fan-out via `asyncio.gather`), that's a documented assumption
this sizing depends on, not a silent one — worth a one-line comment in the new module pointing
back here, matching this codebase's own convention of citing the reasoning inline
(`_scoring_pool.py`'s and `tick_executor.py`'s own docstrings both do this).

**Correctness.** Lowest-risk option: `#546`'s lock precondition holds (§2). Connection-cache
consequence, worth naming explicitly rather than treating as free: `_scoring_pool.py`'s
`cached_read_connection` uses `threading.local()`, so a second, separate pool means a second,
separate thread-local connection cache — the retry path's one worker thread opens its own cached
read connections to `signal_log.db`/`market_history.db`/`services/market_analyst_agent`'s DB
(the same three modules `cached_read_connection` already serves), independent of the WS path's 4
threads' own cached connections. **Net new cost: up to 3 additional persistent read connections**
(one per cached DB, on the retry pool's single thread) that don't exist today, since today the
retry path's calls land on whichever of `_scoring_pool`'s 4 threads happens to be free and reuse
*that* thread's already-cached connections. This is bounded, observable, and small — the same
"bounded and observable, not eliminated" property `_scoring_pool.py`'s own docstring names as its
actual design goal — but it is a real, non-zero fd-budget line item, not literally free, and
should be named in the implementation plan's own accounting rather than discovered at review
time.

**Failure behavior.** No new failure mode. A stuck/leaked thread on the new 1-worker pool (the
same `#145/#150` class of risk `_scoring_pool.py`'s own docstring already names as a known,
separately-tracked issue) now stalls only candidate-retry work, never WS-path scoring — this is
the isolation property David's priority asks for, stated concretely: today, a stuck retry-path
thread can occupy 1 of `_scoring_pool`'s 4 workers indefinitely, reducing the WS path's own
effective concurrency from 4 to 3 without anything in the WS path itself being at fault. Splitting
removes that specific cross-contamination in both directions.

**Complexity.** Small. One new module (or one new executor instance + one new `run()`-style
wrapper function) mirroring an already-proven, already-reviewed pattern in this exact codebase
twice over (`_scoring_pool.py` itself, and the retired `_diagnostics_pool.py`). One call-site
change (`score_recovered_trade`'s internal `_scoring_pool.run(...)` call becomes a call to the
new pool's `run(...)`). No change to `fetch_signals()`, no change to `_process_trades_sync`'s
logic, no change to the #546 lock.

### Option 2 (considered, rejected): grow `_scoring_pool` to accommodate both paths' peak concurrency

**Mechanism.** Raise `max_workers` from 4 to, e.g., 5 (4 for the WS path's own established need
plus 1 for candidate-retry's single concurrent caller), keep both paths sharing one pool.

**Why rejected.** Directly conflicts with CLAUDE.md's data-plane HARD RULE: "Never change...
thread counts... because it 'should help': identify the measured bottleneck and its mechanism
first." No measurement here establishes that 5 (vs. 4, vs. some other number) is the right
figure — this option's only justification would be "should help," the exact reasoning the HARD
RULE forbids acting on without measurement. It also does not deliver the actual property David's
priority names ("not sharing... thread pools") — it makes the shared pool bigger, not un-shared;
the cross-contamination failure mode named in Option 1's "Failure behavior" paragraph is
unchanged by this option (a stuck retry-path thread still occupies a WS-path-usable slot, just
one of 5 instead of one of 4).

### Option 3 (considered, rejected): route candidate-retry through Python's default `asyncio.to_thread` executor

**Mechanism.** Simplest possible code change — remove `score_recovered_trade`'s dependency on
any dedicated pool, let it fall onto the process-wide default executor like any other
`asyncio.to_thread` call.

**Why rejected.** `_scoring_pool.py`'s own module docstring already rejected this exact approach
for the WS path, for reasons that apply identically to the retry path: the default executor is
"shared process-wide with unrelated work... and unbounded up to 20 threads on this container" —
using it here would reintroduce the precise anti-pattern (`_scoring_pool.py`'s revision-1
mistake, per the original design doc's §4: "Revision 1 only changed connection lifecycle and
left the work running on Python's shared default executor... issue #145/#150... turn this fix's
own cache into a compounding leak") this whole subsystem was built to avoid, just relocated to
the other caller instead of removed. Rejected on the same evidence the original design doc
already established, not re-derived from scratch.

## 4. Comparison

| | Option 1: dedicated 1-worker pool | Option 2: grow shared pool | Option 3: default executor |
|---|---|---|---|
| **Removes the sharing (David's stated priority)?** | **Yes** | No — bigger, still shared | Yes, but reintroduces a worse problem |
| **Violates the "no capacity change without measurement" HARD RULE?** | No — sizing derived from an already-established fact (strict serial submission), not a guess | **Yes** | N/A (removes the dedicated-pool concept entirely) |
| **New correctness burden** | None (#546's lock verified pool-agnostic, §2) | None | Reopens the exact leak-blast-radius problem `_scoring_pool.py` was built to close (§3, Option 3) |
| **Resource cost** | +≤3 read connections, one thread, bounded and observable | None beyond +1 thread | Unbounded (shared with all other `to_thread` work) |
| **Complexity** | Small — mirrors an already-twice-proven pattern in this codebase | Trivial (one constant change) — but not a real fix | Trivial — but the rejected kind |

## 5. What this document does not do

- **Does not benchmark or measure** the WS path's own actual peak concurrent demand against
  `_scoring_pool` post-PR-#555 — §1's "up to 4" is a structural ceiling (the semaphore bound),
  not a measured steady-state rate. An implementation-stage task should capture this the same
  way the research doc's PR #558 citation did — via existing telemetry, not a new synthetic
  benchmark, unless none exists.
- **Does not touch `services/kalshi/websocket.py`'s dispatch semaphore, `_TickerDispatchGate`,
  or any WS ingestion code** — this design is scoped entirely to where `score_recovered_trade`'s
  work runs, nothing about how trades are dequeued or dispatched.
- **Does not touch `_process_trades_sync`'s scoring logic, gates, or the #546 lock's own
  implementation** — only which executor runs the function that acquires it.
- **Does not decide the exact module name/location** for the new pool (`_candidate_retry_pool.py`
  vs. folding the executor directly into `candidate_retry.py`) — a genuinely minor implementation
  detail, not a design-level decision, left for the implementation-plan stage to pick using
  whichever reads more consistently with this codebase's existing per-workload-pool convention.
- **Does not authorize implementation** — per CLAUDE.md, this design/spec stage still needs its
  own self-review, adversarial review, and consolidation before an implementation-plan stage can
  build on it, and that stage is separate from this one regardless of how this cycle resolves.

## Appendix — evidence log

- `git log --oneline -- services/whalewatchers/_scoring_pool.py` → exactly one commit, confirming
  it was never touched by PR #555 — the direct evidence the pool's own sizing docstring is stale
  relative to the WS path's post-#555 concurrency.
- `services/kalshi/websocket.py`: `_dispatch_trade_concurrent`, `_TRADE_DISPATCH_CONCURRENCY`,
  `_run_trade_item` (re-read directly, not assumed carried over from the research doc) — §1's
  "up to 4 concurrent" claim.
- `services/whale_stream/whale_stream_handlers.py:201-235` (`_process_stream_trade`'s body, read
  directly) → confirms it calls `whale_provider.fetch_signals(...)` directly, no intermediate
  queueing between WS dispatch and the `_scoring_pool.run(...)` call inside `fetch_signals`.
- `services/whalewatchers/kalshi_trade_tape.py:322-420` (`fetch_signals`, `score_recovered_trade`)
  → both call sites into `_scoring_pool.run(...)`, confirmed at lines 364 and 415.
- `services/candidate_retry.py:103-168` (`run_pending`, full body) → confirmed strictly serial
  `for trade_id in list(_pending): ... await provider.score_recovered_trade(...)`, no
  `asyncio.gather`/`create_task` — the basis for this design's 1-worker sizing.
- `main.py:777-806` (`_candidate_retry_loop`, full body) → confirmed the loop's own trigger
  cadence and single-caller contract ("call from exactly one place").
- `services/whalewatchers/kalshi_trade_tape.py:273` (`self._seen_lock = threading.Lock()`) →
  §2's correctness-precondition verification that the #546 fix is pool-agnostic.
- `docs/superpowers/specs/2026-09-01-whale-scoring-connection-reuse-design.md` §4, §4a (read in
  full) → the original, deliberate decision to share this pool, and the directly analogous
  `_diagnostics_pool.py` precedent for splitting a workload out when isolation matters. Its own
  review doc (`...-design-review.md`, `grep -n "split\|separate pool\|candidate.retry"`) shows no
  prior debate over splitting WS-path from candidate-retry — confirmed this is new ground, not a
  re-litigation.
- `services/quality/routes.py:130-142` → confirms `_diagnostics_pool.py` was later deleted once
  its one workload (`run_offline()`) moved to aiosqlite — cited as evidence the "dedicated pool
  per isolable workload" *pattern* is proven in this codebase, not that every instance of it is
  permanent; this design's own workload (synchronous DB+scoring work) has no analogous
  async-native replacement path today, so this instance is not expected to retire the same way.
- `docs/superpowers/research/2026-09-03-trade-stream-decoupling-and-history-event-driven-research.md`
  §1.2, §3, §5 and its own consolidation → the research this design directly extends; not
  re-derived, cited.
- Issue #563 (filed from that research) → the tracking record this design closes out.
- `gh issue list --search "_scoring_pool" --state all` → confirmed no other open issue on this
  axis besides #546 (the now-fixed race, a different concern).
