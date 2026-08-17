"""services/game_state.py — durable in-game state (score, period, clock).

The football payload here is the real one verified against live NFL
milestones in docs/kalshi/get-live-data.md, not invented. The baseball one
is the shape this module is designed to absorb without a schema change.
"""
import json
import sqlite3

import pytest

from services import game_state as gs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(gs, "DB_PATH", tmp_path / "game_state.db")
    monkeypatch.setattr(gs, "_buffer", [])
    monkeypatch.setattr(gs, "_last_fingerprint", {})
    # Also module-level, and added later than the others - without resetting
    # it, one test's rate-limit state suppresses the next test's first write.
    monkeypatch.setattr(gs, "_last_write_at", {})
    yield


# The real response `details` for a football_game milestone, from
# docs/kalshi/get-live-data.md's live verification.
_FOOTBALL = {
    "away_points": 24, "home_points": 20, "clock": "00:00", "quarter": 4,
    "status": "closed", "widget_status": "finished", "winner": "",
    "last_play": {"description": "End Game", "occurence_ts": 1786835667},
    "last_updated_ts": 1786837766,
    "situation": {"down": 3, "goal_to_go": False, "yardline": 2, "yfd": 2,
                  "possession_team_id": "abc", "side_team_id": "def"},
}

_BASEBALL = {
    "away_runs": 3, "home_runs": 5, "inning": 7, "status": "inprogress",
    "widget_status": "live", "winner": "",
    "last_play": {"description": "Single to left field", "occurence_ts": 1786835999},
    "last_updated_ts": 1786836000,
}


def test_extracts_the_real_football_payload():
    f = gs.extract(_FOOTBALL, sport="football")
    assert (f["home_score"], f["away_score"]) == (20, 24)
    assert f["period"] == 4 and f["period_label"] == "quarter"
    assert f["clock"] == "00:00"
    assert f["widget_status"] == "finished"
    assert f["last_play"] == "End Game"
    assert f["last_play_ts"] == 1786835667
    # Kalshi's own not-yet-decided value is "", not a team named "" - a
    # query for "has a winner" must not match every in-progress game.
    assert f["winner"] is None


def test_extracts_baseball_innings_without_a_schema_change():
    """The user's own example: a baseball game's score and innings. Different
    field names for the same ideas, absorbed by the generic mapping."""
    f = gs.extract(_BASEBALL, sport="baseball")
    assert (f["home_score"], f["away_score"]) == (5, 3)
    assert f["period"] == 7
    assert f["period_label"] == "inning"
    assert f["last_play"] == "Single to left field"


def test_period_label_never_invents_a_term_for_an_unknown_sport():
    assert gs.period_label_for("baseball") == "inning"
    assert gs.period_label_for("Hockey") == "period"
    assert gs.period_label_for("curling") == "period"   # generic, not guessed
    assert gs.period_label_for(None) == "period"


def test_stores_the_whole_payload_including_unmapped_fields():
    """`situation` (down/distance/possession) has no column, and must still
    survive - the ticker-channel mistake this app already had to fix."""
    gs.record("EVT-1", _FOOTBALL, sport="football", now=1000.0)
    gs.flush()
    with sqlite3.connect(gs.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM game_states").fetchone()
    assert row["home_score"] == 20
    raw = json.loads(row["raw_json"])
    assert raw["situation"]["down"] == 3
    assert raw["situation"]["yardline"] == 2


def test_unchanged_state_is_not_rewritten_every_poll():
    """A finished game keeps being polled for hours; without this it would
    dominate the table with identical rows."""
    assert gs.record("EVT-1", _FOOTBALL, sport="football", now=1000.0) is True
    assert gs.record("EVT-1", _FOOTBALL, sport="football", now=1060.0) is False
    assert gs.record("EVT-1", _FOOTBALL, sport="football", now=1120.0) is False


def test_a_real_state_change_does_write():
    gs.record("EVT-1", _BASEBALL, sport="baseball", now=1000.0)
    scored = {**_BASEBALL, "home_runs": 6,
              "last_play": {"description": "Home run", "occurence_ts": 1786836100}}
    assert gs.record("EVT-1", scored, sport="baseball", now=1060.0) is True
    gs.flush()
    rows = gs.timeline("EVT-1")
    assert [r["home_score"] for r in rows] == [5, 6]
    assert rows[-1]["last_play"] == "Home run"


def test_a_ticking_clock_alone_is_not_a_state_change():
    """source_updated_ts moves constantly; that is not news."""
    gs.record("EVT-1", _BASEBALL, sport="baseball", now=1000.0)
    same_but_later = {**_BASEBALL, "last_updated_ts": 1786899999}
    assert gs.record("EVT-1", same_but_later, sport="baseball", now=1060.0) is False


def test_record_never_raises_on_garbage():
    assert gs.record("", {"a": 1}) is False
    assert gs.record("EVT-1", {}) is False
    assert gs.record("EVT-1", {"last_play": "not-a-dict", "home_points": "x"}) is True


def test_timeline_and_stats_are_readable():
    gs.record("EVT-1", _FOOTBALL, sport="football", now=1000.0)
    gs.record("EVT-2", _BASEBALL, sport="baseball", now=1010.0)
    gs.flush()
    assert len(gs.timeline("EVT-1")) == 1
    s = gs.stats()
    assert s["observations"] == 2
    assert s["distinct_events"] == 2
    assert s["with_score"] == 2
    assert set(s["by_sport"]) == {"football", "baseball"}


# --- non-game live-data shapes (2026-08-17) ------------------------------
# The milestone path this module was first wired to was measured EMPTY while
# _fetch_event_live_data held six live entries carrying crypto payloads.
# Those are a completely different shape - OHLC candlesticks and an
# underlying price timeseries instead of a score - and are equally worth
# keeping.

_CRYPTO = {
    "coin": "BTC", "event_ticker": "KXBTCD-26AUG1717",
    "maturity_ts_ms": 1786982400000,
    "candlesticks": {"15M": [
        {"open_ts_ms": 1786941217555, "open": 63427.04, "high": 63464.46,
         "low": 63427.04, "close": 63464.20},
    ]},
    "timeseries": [{"ts_ms": 1786941217555, "value": 63427.04}],
}


def test_a_crypto_payload_is_stored_whole_even_with_no_sport_fields():
    assert gs.record("KXBTCD-26AUG1717", _CRYPTO, event_type="crypto", now=1000.0) is True
    gs.flush()
    with sqlite3.connect(gs.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM game_states").fetchone()
    assert row["event_type"] == "crypto"
    assert row["home_score"] is None and row["period"] is None
    raw = json.loads(row["raw_json"])
    assert raw["candlesticks"]["15M"][0]["close"] == 63464.20
    assert raw["timeseries"][0]["value"] == 63427.04


def test_continuously_changing_payloads_are_rate_limited_not_written_every_tick():
    """A score changes a handful of times an hour; a candlestick array
    changes on essentially every poll, so fingerprinting alone would not
    bound the volume."""
    assert gs.record("EVT-C", _CRYPTO, event_type="crypto", now=1000.0) is True
    changed = {**_CRYPTO, "timeseries": [{"ts_ms": 1786941217999, "value": 63500.0}]}
    assert gs.record("EVT-C", changed, event_type="crypto", now=1010.0) is False  # inside interval
    assert gs.record("EVT-C", changed, event_type="crypto", now=1100.0) is True   # past it


def test_identical_crypto_payload_is_still_deduplicated_after_the_interval():
    gs.record("EVT-C", _CRYPTO, event_type="crypto", now=1000.0)
    assert gs.record("EVT-C", _CRYPTO, event_type="crypto", now=2000.0) is False
