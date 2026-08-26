"""main.py's trading-tick synchronous SQLite phases, routed through
services/tick_executor.py instead of the calling event loop (realtime
data-plane remediation plan, P1 Tasks 7/8).

Follows the repo's established pattern of redirecting every DB_PATH before
`import main`, since main.py constructs PaperBroker/RiskManager/the
config_store singleton at import time (CLAUDE.md's "data/*.db files are
live" section) - see tests/test_active_terminal_refresh.py.
"""
import asyncio
import tempfile
from pathlib import Path

from services import paper_broker as pb_module
from services import risk_manager as rm_module
from services import config_performance as cp_module
from services import market_history as mh_module
from services.market_catalog import market_catalog as mc_module
from services import series_evaluator as se_module
from services import series_watcher as sw_module

_tmp_dir = Path(tempfile.mkdtemp(prefix="tick_executor_wiring_"))
pb_module.DB_PATH = _tmp_dir / "paper_broker.db"
rm_module.DB_PATH = _tmp_dir / "risk_state.db"
cp_module.DB_PATH = _tmp_dir / "config_performance.db"
mh_module.DB_PATH = _tmp_dir / "market_history.db"
mc_module.DB_PATH = _tmp_dir / "market_catalog.db"
se_module.DB_PATH = _tmp_dir / "series_evaluator.db"
sw_module.DB_PATH = _tmp_dir / "series_watcher.db"

import main  # noqa: E402

CFG = {
    "series_watcher": {"enabled": True, "series": ["KXBTC15M"], "book_snapshot_interval_sec": 5},
}


def _trade(trade_id, ticker="KXBTC15M-26AUG17-B1", outcome="yes", count="1000.00",
           yes_price="0.60", no_price="0.40"):
    return {
        "trade_id": trade_id, "ticker": ticker, "count_fp": count,
        "yes_price_dollars": yes_price, "no_price_dollars": no_price,
        "taker_outcome_side": outcome, "taker_book_side": "bid" if outcome == "yes" else "ask",
        "taker_side": outcome, "is_block_trade": False, "ts_ms": 1_755_000_000_000,
    }


def test_flush_trade_capture_runs_via_tick_executor(monkeypatch):
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(main.tick_executor, "run", _spy_run)
    asyncio.run(main._flush_trade_capture_async([], CFG))
    assert len(calls) == 1


def test_flush_trade_capture_writes_the_same_rows_as_before_extraction(monkeypatch, tmp_path):
    monkeypatch.setattr(sw_module, "DB_PATH", tmp_path / "series_watcher_isolated.db")
    monkeypatch.setattr(sw_module, "_trade_buffer", [])
    monkeypatch.setattr(sw_module, "_book_buffer", [])
    monkeypatch.setattr(sw_module, "_dropped_rows", 0)
    monkeypatch.setattr(sw_module, "_quarantine_cache", None)

    trade_tape = [_trade("t1"), _trade("t2")]
    result = asyncio.run(main._flush_trade_capture_async(trade_tape, CFG))

    assert result.get("trades") == 2
    stats = sw_module.capture_stats("KXBTC15M")
    assert stats.get("raw_trades") == 2
