"""Dedicated 1-worker pool for candidate_retry's scoring path
(kalshi_trade_tape.score_recovered_trade -> _process_trades_sync), split
out of services/whalewatchers/_scoring_pool.py per docs/superpowers/specs/
2026-09-03-scoring-pool-candidate-retry-isolation-design.md (issue #563).

Why a separate pool rather than more workers on the shared one: the two
callers are independently scheduled and have very different latency
requirements. The WS-trade path (_process_stream_trade -> fetch_signals)
is the exchange-wide real-time path, and since PR #555 it can have up to
_TRADE_DISPATCH_CONCURRENCY (4, services/kalshi/websocket.py) trades
dispatched concurrently - it can legitimately want all 4 of
_scoring_pool's workers by itself. The candidate-retry path is a catch-up
sweep on main.py's _candidate_retry_loop, waking on an interval and only
acting when something is pending. Sharing meant a retry submission
arriving during a WS burst queued behind up to 4 real-time scoring calls,
and - in the other direction - a stuck retry thread (issue #145/#150's
known, separately-tracked thread-leak shape) permanently cost the WS path
one of its 4 workers.

1 worker, not a guess: candidate_retry.run_pending() submits strictly
serially - one `await provider.score_recovered_trade(...)` per pending
trade in a plain `for` loop, no asyncio.gather/create_task fan-out - and
main.py's _candidate_retry_loop is a single supervised task that awaits
run_pending() to completion before its next iteration, so two run_pending()
calls can never overlap. A second worker would sit permanently idle. This
sizing depends on that serial-submission property: if run_pending() ever
fans out, revisit this number and the design doc above together, don't
just raise it (tests/test_whalewatchers_candidate_retry_pool.py's
single-worker test fails loudly if it's raised without that).

No cached_read_connection here, deliberately: the three modules that cache
read connections for this scoring path (signal_log.py, market_history.py,
market_analyst_agent/_db.py) call _scoring_pool.cached_read_connection()
by name, and that cache is keyed on threading.local() - thread-keyed, not
pool-keyed - so this pool's worker thread transparently gets its own cache
slot with no change in those modules. Duplicating the helper here would
create a second cache for the same three databases on the same thread."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="candidate-retry-scoring")


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)
