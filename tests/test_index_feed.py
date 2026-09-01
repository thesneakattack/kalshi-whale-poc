"""services/index_feed/ - the settlement algebra and the guards that stop
it being applied to markets it doesn't describe, plus the tick-capture
layer underneath it.

Every fixture value here comes from a real market read live from the API on
2026-08-17, not invented - the whole point of these guards is that the eight
crypto series genuinely disagree with each other about strike_type, index
naming, and whether a 60-second average is involved at all.

Monkeypatches services.index_feed.ingestion directly, not the
services.index_feed package's own re-exported copies of DB_PATH/_latest/
_tick_buffer/_dropped_rows - see services/index_feed/__init__.py's
docstring for why the package-level names aren't the ones ingestion.py's
own functions actually read/write.
"""
import asyncio
import json
import sqlite3

import pytest

from services import index_feed as ifd
from services.index_feed import ingestion as _ingestion


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(_ingestion, "DB_PATH", tmp_path / "index_feed.db")
    monkeypatch.setattr(_ingestion, "_latest", {})
    monkeypatch.setattr(_ingestion, "_tick_buffer", [])
    monkeypatch.setattr(_ingestion, "_dropped_rows", 0)
    yield


# ------------------------------------------------------ settlement algebra

def test_required_remaining_average_inverts_the_settlement_mean():
    """Settlement is the mean of 60 one-second observations. After 30 of
    them average 100, the other 30 must average 100 for the total to be
    100 - and 110 for the total to reach 105."""
    assert ifd.required_remaining_average(100.0, 30, 100.0) == pytest.approx(100.0)
    assert ifd.required_remaining_average(100.0, 30, 105.0) == pytest.approx(110.0)
    # With 59 of 60 known, the single remaining observation carries the
    # whole correction - this is the "one second before close" case.
    assert ifd.required_remaining_average(100.0, 59, 101.0) == pytest.approx(160.0)


def test_required_remaining_average_refuses_a_complete_or_empty_window():
    assert ifd.required_remaining_average(100.0, 60, 100.0) is None   # nothing remaining
    assert ifd.required_remaining_average(100.0, 0, 100.0) is None    # nothing known
    assert ifd.required_remaining_average(None, 30, 100.0) is None
    assert ifd.required_remaining_average(100.0, 30, None) is None


def _cf_msg(index_id="BRTI", spot="63500.00", q15_value=None, q15_size=None):
    msg = {
        "index_id": index_id,
        "received_at": 1_755_000_000_123,
        # The raw upstream frame is a JSON STRING, not an object - it needs
        # a second parse (docs/kalshi/cfbenchmarks-value.md).
        "data": json.dumps({"type": "value", "id": index_id, "time": 1_755_000_000_123,
                            "value": spot}),
        "avg_60s_data": {"value": "63498.00000000", "window_size": 60,
                         "window_start_ts_ms": 1, "window_end_ts_exclusive": 2},
    }
    if q15_value is not None:
        msg["last_60s_windowed_average_15min"] = {
            "value": q15_value, "window_size": q15_size,
            "window_start_ts_ms": 1, "window_end_ts_exclusive": 2,
        }
    return msg


def test_projection_reports_outside_window_rather_than_extrapolating():
    """The q15 field is absent except in the final minute before a
    quarter-hour close. Absent means no part of the settlement average
    exists yet - the honest answer is "unknown", not an extrapolation from
    spot."""
    ifd.record_cfbenchmarks(_cf_msg(), now=1000.0)
    out = ifd.settlement_projection("BRTI", 63485.16)
    assert out["status"] == "outside_window"
    assert "not in the final minute" in out["reason"]
    assert "projected_probability" not in out


def test_projection_while_accumulating_gives_the_exact_required_average():
    # 30 of 60 observations known, averaging 63400; strike 63500.
    # Remaining 30 must average 63600 for the mean to land on the strike.
    ifd.record_cfbenchmarks(_cf_msg(spot="63450.00", q15_value="63400.00", q15_size=30), now=1000.0)
    out = ifd.settlement_projection("BRTI", 63500.0)
    assert out["status"] == "accumulating"
    assert out["observations_known"] == 30
    assert out["seconds_remaining"] == 30
    assert out["fraction_known"] == 0.5
    assert out["required_remaining"] == pytest.approx(63600.0)
    # Spot is 63450, so the index must average 150 HIGHER than it is now.
    assert out["gap_from_spot"] == pytest.approx(150.0)


def test_projection_is_determined_once_all_sixty_observations_are_in():
    ifd.record_cfbenchmarks(_cf_msg(q15_value="63490.00", q15_size=60), now=1000.0)
    out = ifd.settlement_projection("BRTI", 63485.16)
    assert out["status"] == "determined"
    assert out["settles_yes"] is True
    assert out["outcome_for_side"] is True
    # Same window read from the NO side flips the outcome, not the fact.
    assert ifd.settlement_projection("BRTI", 63485.16, side="no")["outcome_for_side"] is False


def test_projection_is_unknown_before_any_tick_arrives():
    assert ifd.settlement_projection("BRTI", 100.0)["status"] == "unknown"


# ---------------------------------------------------------------- capture

def test_cfbenchmarks_tick_parses_the_nested_raw_frame():
    accepted, _ = ifd.record_cfbenchmarks(_cf_msg(spot="63512.25"), now=1000.0)
    assert accepted is True
    latest = ifd.latest("BRTI")
    assert latest["value"] == pytest.approx(63512.25)
    assert latest["avg_60s_value"] == pytest.approx(63498.0)
    assert latest["q15_value"] is None      # absence preserved, not defaulted
    ifd.flush()
    with sqlite3.connect(_ingestion.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM index_ticks").fetchone()
    assert row["index_id"] == "BRTI" and row["source"] == "cfbenchmarks"
    assert json.loads(row["raw_json"])["index_id"] == "BRTI"


def test_pyth_tick_records_a_bare_price():
    accepted, _ = ifd.record_pyth({"underlying_ticker": "Metal.XAU/USD", "value_usd": "2365.12345000",
                                   "source_ts_ms": 1, "received_at": 2}, now=1000.0)
    assert accepted is True
    latest = ifd.latest("Metal.XAU/USD")
    assert latest["value"] == pytest.approx(2365.12345)
    assert latest["q15_window_size"] is None


def test_capture_never_raises_on_garbage():
    assert ifd.record_cfbenchmarks({})[0] is False
    assert ifd.record_pyth({})[0] is False
    assert ifd.record_cfbenchmarks({"index_id": "BRTI", "data": "not json"})[0] is True


def test_record_cfbenchmarks_returns_should_flush_without_flushing(monkeypatch):
    """Event-loop-blocking fix 1 (2026-09-01): record_cfbenchmarks used to
    call flush() inline once the buffer hit _FLUSH_BATCH - real synchronous
    disk I/O with no await point, blocking the whole asyncio event loop for
    the write's duration (confirmed live: a 13-minute app-wide stall). It
    now only reports that a flush is due; the caller (services/whale_stream/
    index_stream_handlers.py's _process_stream_index) schedules it off the
    loop via tick_executor."""
    flush_calls = []
    monkeypatch.setattr(_ingestion, "flush", lambda: flush_calls.append(1) or {"ticks": 0})
    for i in range(_ingestion._FLUSH_BATCH - 1):
        accepted, should_flush = ifd.record_cfbenchmarks(_cf_msg(), now=1000.0 + i)
        assert accepted is True
        assert should_flush is False
    accepted, should_flush = ifd.record_cfbenchmarks(_cf_msg(), now=2000.0)
    assert accepted is True
    assert should_flush is True  # crossed _FLUSH_BATCH
    assert flush_calls == []  # never called internally - the caller's job now


def test_record_pyth_returns_should_flush_without_flushing(monkeypatch):
    flush_calls = []
    monkeypatch.setattr(_ingestion, "flush", lambda: flush_calls.append(1) or {"ticks": 0})
    for i in range(_ingestion._FLUSH_BATCH - 1):
        accepted, should_flush = ifd.record_pyth(
            {"underlying_ticker": "BTC", "value_usd": "50000"}, now=1000.0 + i,
        )
        assert accepted is True
        assert should_flush is False
    accepted, should_flush = ifd.record_pyth(
        {"underlying_ticker": "BTC", "value_usd": "50000"}, now=2000.0,
    )
    assert accepted is True
    assert should_flush is True
    assert flush_calls == []


# --------------------------------------------- no-assumption spec guards
# Each of these is a real market read live on 2026-08-17. They disagree with
# each other, which is the entire reason settlement_spec exists.

_KXBTC15M = {
    "ticker": "KXBTC15M-26AUG170130-30", "strike_type": "greater_or_equal",
    "floor_strike": 63485.16, "settlement_timer_seconds": 1,
    "rules_primary": "If the simple average of the sixty seconds of CF Benchmarks' BRTI before "
                     "1:30 AM EDT on Aug 17, 2026 is at least the simple average of the sixty "
                     "seconds of CF Benchmarks' BRTI before 1:15 AM EDT on August 17, 2026, then "
                     "the market resolves to Yes.",
}
_KXETHD = {
    "ticker": "KXETHD-26AUG17", "strike_type": "greater", "floor_strike": 2594.99,
    "settlement_timer_seconds": 60,
    "rules_primary": "If the simple average of the sixty seconds of CF Benchmarks' Ethereum "
                     "Real-Time Index (ERTI) before 2 AM EDT is above 2594.99 at 2 AM EDT on "
                     "Aug 17, 2026, then the market resolves to Yes.",
}
_KXBTCMAXY = {
    "ticker": "KXBTCMAXY-26", "strike_type": "greater", "floor_strike": 109999.99,
    "settlement_timer_seconds": 3600,
    "rules_primary": "If the Bitcoin spot price according to the CF Bitcoin Real-Time Index is "
                     "above $109999.99 starting 01/02/2026 06:00 PM and before Dec 31, 2026 at "
                     "11:59 PM ET, then the market resolves to Yes",
}
_KXDOGED = {
    "ticker": "KXDOGED-26AUG17", "strike_type": "custom", "floor_strike": None,
    "settlement_timer_seconds": 1800,
    "rules_primary": "If there is a 60 second average of CF Benchmarks' Dogecoin Real-Time Index "
                     "(DOGEUSD_RTI) before 2 AM EDT is above 0.2449999 at 2 AM EDT.",
}


def test_spec_carries_the_greater_or_equal_operator_for_the_15_minute_series():
    spec = ifd.settlement_spec(_KXBTC15M)
    assert spec["supported"] is True
    assert spec["index_id"] == "BRTI"
    assert spec["strike"] == pytest.approx(63485.16)
    assert spec["comparison"] == ">="


def test_spec_carries_the_strictly_greater_operator_for_the_dailies():
    """The operator genuinely differs across series - 'is at least' on the
    15-minute markets, 'is above' on the dailies - so it is read from
    strike_type rather than assumed."""
    spec = ifd.settlement_spec(_KXETHD)
    assert spec["supported"] is True
    assert spec["comparison"] == ">"


def test_spec_resolves_the_third_spelling_of_the_ethereum_index():
    """KXETH15M's rules say 'ETHUSDRTI', KXETHD's say 'ERTI', and the docs
    example says 'ETHUSD_RTI'. All three are the same index."""
    assert ifd.settlement_spec(_KXETHD)["index_id"] == "ETHUSD_RTI"
    assert ifd.resolve_index_id("... CF Benchmarks' ETHUSDRTI before ...") == "ETHUSD_RTI"
    assert ifd.resolve_index_id("... Ethereum Real-Time Index (ERTI) ...") == "ETHUSD_RTI"


def test_spec_refuses_a_barrier_market_that_passes_every_other_check():
    """KXBTCMAXY has a real floor_strike AND a supported strike_type, so
    the first two guards let it through - only the 60-second-window check
    catches that it is a year-long barrier market with no averaging at
    all."""
    spec = ifd.settlement_spec(_KXBTCMAXY)
    assert spec["supported"] is False
    assert "60-second averaging window" in spec["reason"]


def test_spec_refuses_a_market_with_no_strike():
    spec = ifd.settlement_spec(_KXDOGED)
    assert spec["supported"] is False
    assert "floor_strike" in spec["reason"]


def test_spec_refuses_rather_than_guessing_an_unknown_index():
    spec = ifd.settlement_spec({
        "ticker": "KXNEW-1", "strike_type": "greater", "floor_strike": 10.0,
        "rules_primary": "If the simple average of the sixty seconds of Some Brand New Index "
                         "before 2 AM EDT is above 10.0, then the market resolves to Yes.",
    })
    assert spec["supported"] is False
    assert "refusing to guess" in spec["reason"]


def test_volatility_needs_real_history_before_it_reports_a_number():
    assert ifd.recent_volatility("BRTI") is None
    for i in range(40):
        ifd.record_cfbenchmarks(_cf_msg(spot=str(63500 + i)), now=1000.0 + i)
    ifd.flush()
    vol = ifd.recent_volatility("BRTI", lookback_sec=3600, now=1100.0)
    assert vol == pytest.approx(0.0, abs=1e-9)   # a perfectly steady $1/s ramp has zero variance


# ------------------------------------------------- reconnect backfill (#260)

def test_last_tick_before_ignores_ticks_at_or_after_the_cutoff():
    _ingestion.record_cfbenchmarks(_cf_msg(spot="1"), now=100.0)
    _ingestion.record_cfbenchmarks(_cf_msg(spot="2"), now=105.0)
    _ingestion.record_cfbenchmarks(_cf_msg(spot="3"), now=110.0)  # at/after cutoff - excluded

    assert asyncio.run(_ingestion.last_tick_before("BRTI", 110.0)) == pytest.approx(105.0)


def test_last_tick_before_flushes_the_buffer_first():
    """A tick can still be sitting in _tick_buffer (below _FLUSH_BATCH) when
    a gap check runs - last_tick_before must see it, not just what's
    already on disk."""
    _ingestion.record_cfbenchmarks(_cf_msg(), now=50.0)
    assert _ingestion._tick_buffer  # still buffered, not yet flushed

    assert asyncio.run(_ingestion.last_tick_before("BRTI", 60.0)) == pytest.approx(50.0)
    assert _ingestion._tick_buffer == []


def test_last_tick_before_returns_none_when_index_never_seen():
    assert asyncio.run(_ingestion.last_tick_before("BRTI", 100.0)) is None


def test_record_cfbenchmarks_backfill_stores_rows_distinguishable_by_source():
    points = [
        {"type": "value", "id": "BRTI", "time": 1_755_000_000_000, "value": "63500.00"},
        {"type": "value", "id": "BRTI", "time": 1_755_000_001_000, "value": "63501.50"},
    ]
    stored, should_flush = _ingestion.record_cfbenchmarks_backfill("BRTI", points, now=1000.0)
    assert stored == 2
    assert should_flush is True
    _ingestion.flush()  # caller's job now (event-loop-blocking elimination Fix 1) - do it explicitly here to check persistence

    with sqlite3.connect(_ingestion.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM index_ticks ORDER BY source_ts_ms").fetchall()
    assert [r["source"] for r in rows] == ["cfbenchmarks_backfill", "cfbenchmarks_backfill"]
    assert rows[0]["value"] == pytest.approx(63500.00)
    assert rows[1]["value"] == pytest.approx(63501.50)
    assert rows[0]["source_ts_ms"] == 1_755_000_000_000
    # Fidelity: the exact raw point Kalshi/CF Benchmarks sent, unmodified.
    assert json.loads(rows[0]["raw_json"]) == points[0]


def test_record_cfbenchmarks_backfill_never_regresses_latest_backward():
    """A live tick that already closed the gap must not be clobbered by the
    older historical data being backfilled behind it."""
    _ingestion.record_cfbenchmarks(_cf_msg(spot="70000.00"), now=2000.0)
    before = _ingestion.latest("BRTI")

    _ingestion.record_cfbenchmarks_backfill(
        "BRTI", [{"time": 1_000_000_000_000, "value": "1.00"}], now=2000.0,
    )

    assert _ingestion.latest("BRTI") == before


def test_record_cfbenchmarks_backfill_never_raises_on_garbage():
    assert _ingestion.record_cfbenchmarks_backfill("BRTI", [None, "not a dict", {}]) == (1, True)
    assert _ingestion.record_cfbenchmarks_backfill("BRTI", None) == (0, False)
