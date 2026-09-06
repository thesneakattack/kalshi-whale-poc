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
which tick_executor never touches). Not converted to an aiosqlite-native
path like issue #410/#585's signal_log.resolved_signals_with_factors_async:
that mechanism exists for a call with a real second-stage CPU cost
(post-query json.loads over every row) that must NOT also be pushed to a
thread; history()/summary() have no such split - they are a single bounded
SELECT each with no additional CPU-heavy pass over the result - so
to_thread is the fit here, not a new module-level aiosqlite connection.
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
