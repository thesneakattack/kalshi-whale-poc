"""
services/diagnostics.py - performance/integrity checks.

Every test redirects each module's DB_PATH into tmp_path (the established
convention throughout tests/*.py, and a hard requirement per CLAUDE.md:
accumulated history in data/*.db is a first-class asset and must never be
touched by a test run).
"""
import sqlite3
import time

import pytest

from services import config_performance as cp_module
from services import diagnostics
from services import market_catalog as mc_module
from services import paper_broker as pb_module
from services import signal_log as sl_module
from services import trade_category as tc_module


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(sl_module, "DB_PATH", tmp_path / "signal_log.db")
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(cp_module, "DB_PATH", tmp_path / "config_performance.db")
    monkeypatch.setattr(mc_module, "DB_PATH", tmp_path / "market_catalog.db")
    monkeypatch.setattr(tc_module, "DB_PATH", tmp_path / "trade_category.db")
    return tmp_path


def _cfg(**over):
    cfg = {
        "strategy": {"min_unit_cost": 0.5, "max_unit_cost": 0.8},
        "whale_watcher_kalshi": {"min_notional_usd": 5000, "min_notional_usd_by_series": {}},
        "strategy_overrides": {"by_category": {}, "by_series": {}},
    }
    cfg.update(over)
    return cfg


def _seed_signals(rows):
    """rows: [(ticker, series, notional, seen_at)]"""
    sl_module._connect().close()  # ensure schema
    with sqlite3.connect(sl_module.DB_PATH) as conn:
        for ticker, series, notional, seen_at in rows:
            conn.execute(
                "INSERT INTO signals (ticker, series, side, size, confidence, source, seen_at, "
                "resolved, raw_notional_usd) VALUES (?,?,?,?,?,?,?,0,?)",
                (ticker, series, "yes", 100, 0.5, "test", seen_at, notional),
            )


def _seed_trades(rows):
    """rows: [(ticker, side, price, reason, timestamp)]"""
    pb_module.PaperBroker(1000.0)  # ensure schema
    with sqlite3.connect(pb_module.DB_PATH) as conn:
        for ticker, side, price, reason, ts in rows:
            conn.execute(
                "INSERT INTO trades (ticker, side, size, price, reason, timestamp) VALUES (?,?,?,?,?,?)",
                (ticker, side, 100, price, reason, ts),
            )


# ---- threshold integrity ----

def test_threshold_integrity_flags_signals_below_the_configured_floor(dbs):
    now = time.time()
    _seed_signals([
        ("A-1", "KXA", 6000.0, now - 100),   # clears the 5000 floor
        ("A-2", "KXA", 12.0, now - 90),      # residue from a looser epoch
        ("A-3", "KXA", 3.0, now - 80),
    ])
    c = diagnostics.check_threshold_integrity(_cfg(), since_ts=now - 3600, now=now)
    assert c.status == "fail"
    assert c.detail["violations"] == 2
    assert c.detail["total"] == 3
    assert {e["ticker"] for e in c.evidence} == {"A-2", "A-3"}


def test_threshold_integrity_respects_per_series_overrides(dbs):
    # A $2,500 print is a violation under the global 5000 floor but fine
    # for a series whose own override is 2500 - the check must resolve the
    # same per-series floor the provider itself uses.
    now = time.time()
    _seed_signals([("B-1", "KXBTC15M", 2600.0, now - 50)])
    cfg = _cfg(whale_watcher_kalshi={
        "min_notional_usd": 5000, "min_notional_usd_by_series": {"KXBTC15M": 2500},
    })
    c = diagnostics.check_threshold_integrity(cfg, since_ts=now - 3600, now=now)
    assert c.status == "ok"
    assert c.detail["violations"] == 0


def test_threshold_integrity_unknown_when_no_data(dbs):
    now = time.time()
    sl_module._connect().close()
    c = diagnostics.check_threshold_integrity(_cfg(), since_ts=now - 3600, now=now)
    assert c.status == "unknown"


# ---- price band ----

def test_price_band_flags_entries_above_max_unit_cost(dbs):
    now = time.time()
    _seed_trades([
        ("C-1", "yes", 0.95, "whale print 9000 @ 0.95 (conf 0.6)", now - 100),  # uc 0.95 > 0.8
        ("C-2", "yes", 0.65, "whale print 5000 @ 0.65 (conf 0.6)", now - 90),   # inside
        ("C-3", "no", 0.05, "whale print 6000 @ 0.05 (conf 0.6)", now - 80),    # uc 0.95 > 0.8
    ])
    c = diagnostics.check_price_band_adherence(_cfg(), since_ts=now - 3600, now=now)
    assert c.status == "fail"
    assert c.detail["above"] == 2
    assert c.detail["inside"] == 1


def test_price_band_uses_side_aware_unit_cost_not_raw_price(dbs):
    # A no-side entry at yes_price 0.05 really costs 0.95/contract. Judging
    # it on the raw 0.05 would call it a "cheap" trade and pass it - the
    # exact no-side dollar-math bug class CLAUDE.md documents.
    now = time.time()
    _seed_trades([("D-1", "no", 0.05, "whale print 6000 @ 0.05 (conf 0.6)", now - 10)])
    c = diagnostics.check_price_band_adherence(_cfg(), since_ts=now - 3600, now=now)
    assert c.detail["above"] == 1
    assert c.evidence[0]["unit_cost"] == pytest.approx(0.95)
    assert c.evidence[0]["max_gain_per_contract"] == pytest.approx(0.05)


# ---- runway ----

def test_runway_buckets_entries_by_time_to_close(dbs):
    now = time.time()
    _seed_trades([
        ("E-1", "yes", 0.6, "whale print 5000 @ 0.6 (conf 0.6)", now - 100),
        ("E-2", "yes", 0.6, "whale print 5000 @ 0.6 (conf 0.6)", now - 100),
    ])
    mc_module._connect(mc_module.DB_PATH).close()
    with sqlite3.connect(mc_module.DB_PATH) as conn:
        # E-1 had 30s of runway at entry; E-2 had an hour.
        conn.execute("INSERT INTO markets (ticker, close_ts, updated_at) VALUES (?,?,?)", ("E-1", now - 70, now))
        conn.execute("INSERT INTO markets (ticker, close_ts, updated_at) VALUES (?,?,?)", ("E-2", now + 3500, now))
    c = diagnostics.check_runway_at_entry(_cfg(), since_ts=now - 3600, now=now)
    assert c.detail["buckets"]["<60s"] == 1
    assert c.detail["buckets"][">900s"] == 1


def test_runway_unknown_when_no_close_time_is_recorded(dbs):
    # Degrade honestly rather than reconstructing close time from the
    # ticker string - that convention is a per-series habit, not an API
    # guarantee, and close_time is mutable upstream anyway.
    now = time.time()
    _seed_trades([("F-1", "yes", 0.6, "whale print 5000 @ 0.6 (conf 0.6)", now - 100)])
    mc_module._connect(mc_module.DB_PATH).close()
    c = diagnostics.check_runway_at_entry(_cfg(), since_ts=now - 3600, now=now)
    assert c.status == "unknown"
    assert c.detail["buckets"]["unknown"] == 1


# ---- epoch attribution ----

def test_performance_by_epoch_splits_trades_at_config_change_boundaries(dbs):
    now = time.time()
    cp_module._connect().close()
    with sqlite3.connect(cp_module.DB_PATH) as conn:
        for ts, path in [(now - 3000, "strategy.entry_threshold"), (now - 1000, "strategy.max_unit_cost")]:
            conn.execute(
                "INSERT INTO applied_changes (applied_at, config_path, old_value, new_value, rationale, "
                "trade_count, fingerprint_before, fingerprint_after) VALUES (?,?,?,?,?,?,?,?)",
                (ts, path, "1", "2", "test", 0, "fp0", "fp1"),
            )
    # epoch 1 (now-3000 .. now-1000): 3 wins; epoch 2 (now-1000 ..): 3 losses
    rows = []
    for i in range(3):
        rows.append((f"G-{i}", "yes", 0.6, f"closed: settled YES - position won (realized +10.0)", now - 2500 + i))
    for i in range(3):
        rows.append((f"H-{i}", "yes", 0.6, f"closed: stop-loss hit (realized -20.0)", now - 500 + i))
    _seed_trades(rows)
    c = diagnostics.performance_by_epoch(since_ts=now - 7200, now=now, min_trades=3)
    assert c.status == "ok"
    epochs = c.detail["epochs"]
    assert len(epochs) == 2
    assert epochs[0]["win_rate_pct"] == 100.0
    assert epochs[1]["win_rate_pct"] == 0.0
    assert epochs[1]["total_pnl"] == pytest.approx(-60.0)


def test_performance_by_epoch_unknown_without_enough_trades(dbs):
    now = time.time()
    cp_module._connect().close()
    with sqlite3.connect(cp_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO applied_changes (applied_at, config_path, old_value, new_value, rationale, "
            "trade_count, fingerprint_before, fingerprint_after) VALUES (?,?,?,?,?,?,?,?)",
            (now - 1000, "strategy.entry_threshold", "1", "2", "test", 0, "fp0", "fp1"),
        )
    _seed_trades([("I-1", "yes", 0.6, "closed: settled YES - position won (realized +5.0)", now - 500)])
    c = diagnostics.performance_by_epoch(since_ts=now - 7200, now=now, min_trades=3)
    assert c.status == "unknown"


# ---- runner ----

def test_run_offline_reports_worst_status_across_checks(dbs):
    now = time.time()
    _seed_signals([("J-1", "KXA", 5.0, now - 10)])  # a clear violation -> fail
    report = diagnostics.run_offline(_cfg(), since_ts=now - 3600, now=now)
    assert report["overall"] == "fail"
    assert {c["name"] for c in report["checks"]} == {
        "threshold_integrity", "price_band_adherence", "runway_at_entry", "performance_by_epoch",
    }


def test_run_offline_never_writes_to_any_db(dbs):
    # This module must be safe to call on a live system at any time.
    now = time.time()
    _seed_signals([("K-1", "KXA", 6000.0, now - 10)])
    # Touch every store first so schema creation (CREATE TABLE IF NOT
    # EXISTS on connect) isn't mistaken for a write by the comparison.
    diagnostics.run_offline(_cfg(), since_ts=now - 3600, now=now)
    before = {p.name: p.stat().st_mtime_ns for p in dbs.glob("*.db")}
    diagnostics.run_offline(_cfg(), since_ts=now - 3600, now=now)
    after = {p.name: p.stat().st_mtime_ns for p in dbs.glob("*.db")}
    assert before == after
