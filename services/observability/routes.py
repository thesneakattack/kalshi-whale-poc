"""
Read-only HTTP surface for services/observability/observability.py's
persisted runtime metrics - the informativeness half, same split as
services/backup/routes.py.
"""
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
    return {"metric": metric, "samples": observability.history(metric, since_ts=since_ts, limit=limit)}


@router.get("/api/observability/summary")
async def get_observability_summary(hours: float = 24.0):
    hours = max(0.01, min(hours, _MAX_WINDOW_HOURS))
    return {"hours": hours, "metrics": observability.summary(hours=hours)}
