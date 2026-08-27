"""
services/diagnostics/diagnostics.py - performance/integrity checks.

Every test redirects each module's DB_PATH into tmp_path (the established
convention throughout tests/*.py, and a hard requirement per CLAUDE.md:
accumulated history in data/*.db is a first-class asset and must never be
touched by a test run).
"""
import sqlite3
import time
from pathlib import Path

import pytest

from services.config import config_performance as cp_module
from services.diagnostics import diagnostics
from services.market_catalog import market_catalog as mc_module
from services import paper_broker as pb_module
from services import series_watcher as sw_module
from services import signal_log as sl_module
from services import trade_category as tc_module


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(sl_module, "DB_PATH", tmp_path / "signal_log.db")
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(cp_module, "DB_PATH", tmp_path / "config_performance.db")
    monkeypatch.setattr(mc_module, "DB_PATH", tmp_path / "market_catalog.db")
    monkeypatch.setattr(tc_module, "DB_PATH", tmp_path / "trade_category.db")
    # run_offline now includes one series_funnel check per watched series
    # (services/series_watcher.py), which opens its own store.
    monkeypatch.setattr(sw_module, "DB_PATH", tmp_path / "series_watcher.db")
    return tmp_path


def _cfg(**over):
    cfg = {
        "strategy": {"min_unit_cost": 0.5, "max_unit_cost": 0.8},
        "whale_watcher_kalshi": {"min_contracts": 5000, "min_contracts_by_series": {}},
        "strategy_overrides": {"by_category": {}, "by_series": {}},
    }
    cfg.update(over)
    return cfg


def _seed_signals(rows):
    """rows: [(ticker, series, size, seen_at)]. factors_json is set to a
    real (non-null) value so these rows pass check_threshold_integrity's
    "is this a real whale_watcher row" filter - the same factors_json IS
    NOT NULL idiom services/signal_log.py's resolved_signals_with_factors
    already uses."""
    sl_module._connect().close()  # ensure schema
    with sqlite3.connect(sl_module.DB_PATH) as conn:
        for ticker, series, size, seen_at in rows:
            conn.execute(
                "INSERT INTO signals (ticker, series, side, size, confidence, source, seen_at, "
                "resolved, factors_json) VALUES (?,?,?,?,?,?,?,0,?)",
                (ticker, series, "yes", size, 0.5, "test", seen_at, "{}"),
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
    # A 2,600-contract print is a violation under the global 5000 floor but
    # fine for a series whose own override is 2500 - the check must resolve
    # the same per-series floor the provider itself uses.
    now = time.time()
    _seed_signals([("B-1", "KXBTC15M", 2600.0, now - 50)])
    cfg = _cfg(whale_watcher_kalshi={
        "min_contracts": 5000, "min_contracts_by_series": {"KXBTC15M": 2500},
    })
    c = diagnostics.check_threshold_integrity(cfg, since_ts=now - 3600, now=now)
    assert c.status == "ok"
    assert c.detail["violations"] == 0


def test_threshold_integrity_unknown_when_no_data(dbs):
    now = time.time()
    sl_module._connect().close()
    c = diagnostics.check_threshold_integrity(_cfg(), since_ts=now - 3600, now=now)
    assert c.status == "unknown"


def test_threshold_integrity_is_epoch_aware_not_judged_against_todays_config(dbs):
    # A signal recorded 2 hours ago when the floor was only 100 - compliant
    # at the time. The floor was then raised to 5000 an hour ago. Judging
    # this signal against TODAY's 5000 floor (the pre-fix behavior) would
    # manufacture a false violation; judged against the floor actually live
    # at seen_at, it's clean. This is the exact bug ROADMAP.md's "Make the
    # diagnostics epoch-aware" item measured live (72% -> 4/39).
    now = time.time()
    _seed_signals([("L-1", "KXA", 150.0, now - 7200)])
    cp_module._connect().close()
    with sqlite3.connect(cp_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO applied_changes (applied_at, config_path, old_value, new_value, rationale, "
            "trade_count, fingerprint_before, fingerprint_after) VALUES (?,?,?,?,?,?,?,?)",
            (now - 3600, "whale_watcher_kalshi.min_contracts", "100", "5000", "test", 0, "fp0", "fp1"),
        )
    cfg = _cfg()  # today's live min_contracts is 5000
    c = diagnostics.check_threshold_integrity(cfg, since_ts=now - 10800, now=now)
    assert c.status == "ok"
    assert c.detail["violations"] == 0
    assert c.detail["epoch_aware"] is True


def test_threshold_integrity_still_flags_a_real_violation_from_before_a_later_raise(dbs):
    # Same setup, but the signal's own size (50) was already below the
    # floor that was live when it was recorded (100) - a real violation,
    # not a stale-config artifact, and the epoch-aware fix must not paper
    # over it.
    now = time.time()
    _seed_signals([("M-1", "KXA", 50.0, now - 7200)])
    cp_module._connect().close()
    with sqlite3.connect(cp_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO applied_changes (applied_at, config_path, old_value, new_value, rationale, "
            "trade_count, fingerprint_before, fingerprint_after) VALUES (?,?,?,?,?,?,?,?)",
            (now - 3600, "whale_watcher_kalshi.min_contracts", "100", "5000", "test", 0, "fp0", "fp1"),
        )
    c = diagnostics.check_threshold_integrity(_cfg(), since_ts=now - 10800, now=now)
    assert c.status == "fail"
    assert c.detail["violations"] == 1
    assert c.evidence[0]["floor_at_the_time"] == 100.0


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


def test_price_band_is_epoch_aware_not_judged_against_todays_band(dbs):
    # Entered at unit_cost 0.9 two hours ago, when max_unit_cost was still
    # 0.95 - inside the band at the time. max_unit_cost was then tightened
    # to 0.8 an hour ago. Judging this entry against today's 0.8 band (the
    # pre-fix behavior) would manufacture a false "above max" violation.
    now = time.time()
    _seed_trades([("N-1", "yes", 0.9, "whale print 9000 @ 0.9 (conf 0.6)", now - 7200)])
    cp_module._connect().close()
    with sqlite3.connect(cp_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO applied_changes (applied_at, config_path, old_value, new_value, rationale, "
            "trade_count, fingerprint_before, fingerprint_after) VALUES (?,?,?,?,?,?,?,?)",
            (now - 3600, "strategy.max_unit_cost", "0.95", "0.8", "test", 0, "fp0", "fp1"),
        )
    cfg = _cfg()  # today's live max_unit_cost is 0.8
    c = diagnostics.check_price_band_adherence(cfg, since_ts=now - 10800, now=now)
    assert c.status == "ok"
    assert c.detail["above"] == 0
    assert c.detail["inside"] == 1
    assert c.detail["epoch_aware"] is True


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
        "threshold_integrity", "price_band_adherence", "runway_at_entry",
        "config_bounds", "performance_by_epoch", "selectivity_curve",
        # One per services/series_watcher.watched_series entry - the
        # accuracy-vs-realised-win-rate reconciliation, per-series by
        # construction (a blended figure across every series answers
        # nobody's question about a specific one).
        "series_funnel:KXBTC15M",
    }


def test_read_paths_close_their_sqlite_connections(dbs):
    """Regression guard for a real flake root-caused 2026-08-25 (CI-only at
    first, then reproducible): every read helper in diagnostics.py/
    series_watcher.py used `with sqlite3.connect(...)`, which COMMITS on
    exit but never CLOSES. On a WAL database (signal_log/paper_broker) the
    connection then lingered until garbage collection, and its close-time
    WAL checkpoint rewrote the main .db file - bumping mtime at a
    nondeterministic moment and making
    test_run_offline_never_writes_to_any_db pass or fail purely on GC
    timing (adding unrelated modules to the import graph was enough to
    flip it).

    Asserting on the source rather than on timing: a timing-based test for
    this would be exactly as flaky as the bug it guards. `closing(...)` is
    the required idiom for these read paths - it also drops the pointless
    implicit commit, which is what made a documented never-writes module
    write at all."""
    import re

    for module_path in (
        Path(diagnostics.__file__),
        Path(sw_module.__file__),
    ):
        source = module_path.read_text()
        bare = re.findall(r"with sqlite3\.connect\(", source)
        assert not bare, (
            f"{module_path.name} has {len(bare)} bare `with sqlite3.connect(...)` read site(s) - "
            "wrap in contextlib.closing() so the connection is actually closed and no "
            "implicit commit fires on a read path"
        )


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
