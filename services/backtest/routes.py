"""
Backtest routes - moved out of services/analytics/routes.py (2026-08-22
modularization pass, Phase 4/9). backtest.py is a leaf module (zero
cross-imports with any analytics sibling), the second-cleanest split of
this whole pass after whale_calibration.
"""
from fastapi import APIRouter

from services import signal_log
from services.backtest import backtest
from services.config.config_store import config_store

router = APIRouter()


@router.get("/api/backtest/entry-threshold")
async def get_backtest_entry_threshold():
    # services/backtest/backtest.py - Gap 2 of docs/config-tuning-data-gaps-
    # 2026-08-10.md, stateless replay against every already-logged resolved
    # signal. Always safe to call - pure read, no enable flag.
    rows = signal_log.resolved_signals_with_factors()
    current_threshold = config_store.get()["strategy"]["entry_threshold"]
    return {"current_threshold": current_threshold, "sweep": backtest.entry_threshold_sweep(rows)}


@router.get("/api/backtest/min-whale-winrate")
async def get_backtest_min_whale_winrate():
    strat_cfg = config_store.get()["strategy"]
    series_stats = signal_log.all_series_stats(days=30)
    signal_rows = signal_log.resolved_signals_with_series(days=30)
    sweep = backtest.min_whale_winrate_pct_sweep(
        series_stats, signal_rows, min_resolved_for_filter=strat_cfg.get("min_resolved_for_whale_filter", 10),
    )
    return {"current_floor": strat_cfg.get("min_whale_winrate_pct", 40), "sweep": sweep}
