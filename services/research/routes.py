"""
Research status/latest/history/manual-trigger routes - the informativeness
half of services/research/research.py, same split as
services/backup/routes.py.
"""
import asyncio

from fastapi import APIRouter

from services.app_state import state
from services.config.config_store import config_store
from services.research import research

router = APIRouter()


def _effective_checkpoints(research_state: dict, last: dict | None) -> dict:
    if research_state["checkpoints"] is not None:
        return research_state["checkpoints"]
    if last is not None:
        return {"resolved_signals": last["resolved_signals_count"], "closed_trades": last["closed_trades_count"]}
    return {}


@router.get("/api/research/status")
async def get_research_status():
    cfg = config_store.get()
    research_cfg = cfg.get("research") or {}
    research_state = state.setdefault("research", {"running": False, "task": None, "checkpoints": None})
    last = research.latest()
    current = research.current_counts()
    checkpoints = _effective_checkpoints(research_state, last)
    return {
        "enabled": research_cfg.get("enabled", False),
        "running": research_state["running"],
        "min_new_resolved_signals": research_cfg.get(
            "min_new_resolved_signals", research._DEFAULT_MIN_NEW_RESOLVED_SIGNALS
        ),
        "min_new_closed_trades": research_cfg.get(
            "min_new_closed_trades", research._DEFAULT_MIN_NEW_CLOSED_TRADES
        ),
        "last_report_at": last["generated_at"] if last is not None else None,
        "checkpoints": checkpoints,
        "current_counts": current,
        "due": research.should_run(cfg, checkpoints, current),
    }


@router.get("/api/research/latest")
async def get_research_latest():
    return {"latest": research.latest()}


@router.get("/api/research/history")
async def get_research_history(limit: int = 20):
    return {"reports": research.recent(limit=limit)}


@router.post("/api/research/run")
async def trigger_research_run():
    """Manual, synchronous trigger - same convention as
    POST /api/backup/run: works regardless of research.enabled (an operator
    explicitly asking for a report is not the same as the periodic
    evidence-triggered scheduler being on), runs off the event loop via
    asyncio.to_thread so it can't stall the trading loop, and returns the
    real report immediately rather than a bare acknowledgement. Resets the
    in-memory checkpoint to this run's own watermark afterward so the
    periodic auto-trigger doesn't immediately re-fire against history this
    run just covered."""
    cfg = config_store.get()
    report = await asyncio.to_thread(research.run_and_store, cfg)
    research_state = state.setdefault("research", {"running": False, "task": None, "checkpoints": None})
    last = research.latest()
    if last is not None:
        research_state["checkpoints"] = {
            "resolved_signals": last["resolved_signals_count"],
            "closed_trades": last["closed_trades_count"],
        }
    return report
