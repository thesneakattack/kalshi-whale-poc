"""
Read-only HTTP surface for services/observability/observability.py's
persisted runtime metrics - the informativeness half, same split as
services/backup/routes.py.

Event-loop dispatch (issue #629, 2026-09-06): history()/summary() are
plain sqlite3 reads (services/observability/observability.py's _connect(),
not aiosqlite) that were called directly from these `async def` route
bodies with no dispatch - _connect()'s own docstring already recorded this
as item 2 of issue #530, measured live at 0.79-1.02s at hours=24 and
~7.66s at hours=720, and #530's sweep separately reproduced a ~7s stall on
a concurrent GET /api/state while GET /api/observability/summary?hours=720
was in flight. Both calls are now wrapped in asyncio.to_thread, the same
mechanism PR #552 used for this exact class of problem in
services/quality/routes.py (a diagnostic sqlite3 read that must leave the
event loop but must not share services/tick_executor.py's dedicated
2-worker pool - issue #510 rejected sharing that pool for a slow
diagnostic call once already, PR #409, because it starved trading-critical
writes for 5+ hours; asyncio.to_thread uses the loop's default executor,
which tick_executor never touches).

Correction (adversarial review of this PR, 2026-09-06): an earlier draft
of this note inverted its own cited precedent. signal_log.py's
resolved_signals_with_factors_async (issue #410/#585) keeps BOTH halves
of its work off the event loop - aiosqlite for the SQL AND
asyncio.to_thread for its own per-row json.loads pass
(materialize_signals_with_factors, signal_log.py:891) - because
materialize_signals_with_factors' own docstring measured that second half
at 75.3% of total cost and calls converting only the SQL to aiosqlite "a
REGRESSION rather than a fix": it would move 25% of the work off a worker
thread and leave the CPU-heavy 75% on the loop. The rule is "must not run
ON the event loop," not "must not be pushed TO a thread" - this module's
first draft stated the opposite. history() has exactly the same second
stage signal_log's does: its own per-row json.loads over up to
_MAX_HISTORY_LIMIT (5000) rows (observability.py:118-121, ~7ms measured
at the cap against empty labels). summary()'s only post-query step is a
single round() per metric row, not per sample - materially smaller, with
no comparable split. Given that, a single asyncio.to_thread around each
whole call is not merely adequate but the STRONGER choice for history():
it moves both the SQL and the json.loads pass off the loop in one hop,
where a hypothetical aiosqlite-only conversion of just the SELECT would
leave history()'s json.loads pass on the loop - exactly the regression
signal_log's docstring warns against. Not converted to an aiosqlite-based
module-level connection for that reason, not because no second stage
existed.

Tradeoff stated explicitly, not left implicit (data-plane HARD RULE):
asyncio.to_thread uses the loop's shared default executor (confirmed
20-worker ceiling in this container, cpu_count 16, min(32, n+4) - same
figure signal_log.py's own docstring recorded for the same executor). A
cancelled/disconnected request does not stop the dispatched call once
started; its worker thread runs to completion holding one of those 20
slots regardless. _MAX_WINDOW_HOURS caps a single call's window at 90
days but nothing throttles how many concurrent history/summary calls are
in flight. This executor is shared with diagnostics/quality/research/
backup route work and loop_watchdog's fault write (signal_log.py's own
enumeration) - acceptable for the same reason PR #552/#624/#625 already
accepted it: no sustained trading-hot-path work uses this executor (that
work has its own dedicated pools - tick_executor, _scoring_pool.py,
_candidate_retry_pool.py), so a burst of slow diagnostic calls degrades
other diagnostic-route latency, never the trading path.
"""
import asyncio
import re
import time

from fastapi import APIRouter, HTTPException

from services.app_state import index_stream, state, trade_stream
from services.config.config_store import config_store
from services.observability import observability

router = APIRouter()

_METRIC_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.]*$")
_MAX_HISTORY_LIMIT = 5000
_MAX_WINDOW_HOURS = 24 * 90  # 90 days - generous upper bound, well past any realistic retention_hours


@router.get("/api/observability/current")
async def get_observability_current():
    cfg = config_store.get()
    return {
        "observed_at": time.time(),
        "metrics": observability.capture_from_runtime(cfg, state, trade_stream, index_stream),
    }


@router.get("/api/observability/history")
async def get_observability_history(metric: str, hours: float = 24.0, limit: int = 1000):
    if not _METRIC_NAME_RE.match(metric):
        raise HTTPException(status_code=400, detail=f"invalid metric name: {metric!r}")
    hours = max(0.01, min(hours, _MAX_WINDOW_HOURS))
    limit = max(1, min(limit, _MAX_HISTORY_LIMIT))
    since_ts = time.time() - hours * 3600
    samples = await asyncio.to_thread(observability.history, metric, since_ts=since_ts, limit=limit)
    return {"metric": metric, "samples": samples}


@router.get("/api/observability/summary")
async def get_observability_summary(hours: float = 24.0):
    hours = max(0.01, min(hours, _MAX_WINDOW_HOURS))
    metrics = await asyncio.to_thread(observability.summary, hours=hours)
    return {"hours": hours, "metrics": metrics}
