"""
Scaffolding-only module (services/ml_feed.py, docs/advisory-engine-plan.md) -
nothing in the app calls it yet, so this is the only thing keeping it from
silently drifting out of sync with the shapes it wraps as those evolve.
"""
from services import ml_feed


def test_build_context_snapshot_wraps_every_input_unchanged():
    cfg = {"strategy": {"entry_threshold": 0.5}}
    portfolio = {"bankroll": 1000.0, "positions": []}
    market_snapshot = {"markets": [{"ticker": "TICK-A"}], "latest_prices": {"TICK-A": 0.5}}
    trade_history_rows = [{"ticker": "TICK-A", "won": True}]
    whale_track_record = {"win_rate": 55.0}
    advisory = {"recommendations": [], "variant_summaries": {}}

    snapshot = ml_feed.build_context_snapshot(
        cfg, portfolio, market_snapshot, trade_history_rows, whale_track_record, advisory,
    )

    assert snapshot["config"] == cfg
    assert snapshot["portfolio"] == portfolio
    assert snapshot["market_snapshot"] == market_snapshot
    assert snapshot["trade_history"] == trade_history_rows
    assert snapshot["whale_track_record"] == whale_track_record
    assert snapshot["advisory"] == advisory
    assert snapshot["schema_version"] == 1
    assert isinstance(snapshot["generated_at"], float)


def test_build_context_snapshot_is_json_serializable():
    import json
    snapshot = ml_feed.build_context_snapshot({}, {}, {}, [], {}, {})
    json.dumps(snapshot)  # must not raise


def test_build_context_snapshot_includes_candlestick_volatility_when_provided():
    snapshot = ml_feed.build_context_snapshot(
        cfg={}, portfolio={}, market_snapshot={}, trade_history_rows=[],
        whale_track_record={}, advisory={},
        candlestick_volatility={"TICK-A": 0.0123},
    )
    assert snapshot["candlestick_volatility"] == {"TICK-A": 0.0123}


def test_build_context_snapshot_defaults_candlestick_volatility_to_empty_dict_when_omitted():
    snapshot = ml_feed.build_context_snapshot(
        cfg={}, portfolio={}, market_snapshot={}, trade_history_rows=[],
        whale_track_record={}, advisory={},
    )
    assert snapshot["candlestick_volatility"] == {}
