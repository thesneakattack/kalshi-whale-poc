import time

import pytest

from services import candidate_log as cl_module
from services import capture_writer as cw_module
from services import market_analyst_agent as maa_module
from services.market_analyst_agent import _db as maa_db_module
from services import market_history as mh_module
from services import paper_broker as pb_module
from services import risk_manager as rm_module
from services import signal_log
from services.kalshi_fees import taker_fee
from services.strategy_engine import FollowTheWhaleStrategy, kelly_scaled_max_size
from services.confidence_scoring import WhaleSignal


def _signal(**overrides):
    defaults = dict(
        id="sig1", ticker="TICK-A", side="yes", size=10000,
        price=0.5, confidence=0.8, timestamp=time.time(),
    )
    defaults.update(overrides)
    return WhaleSignal(**defaults)


def _cfg(**overrides):
    strategy = dict(
        name="follow_the_whale", entry_threshold=0.65, max_position_pct=0.05,
        cooldown_sec=300, min_whale_winrate_pct=40, min_resolved_for_whale_filter=5,
        live_markets_only=False,
    )
    strategy.update(overrides)
    return {"strategy": strategy}


def _strategy(tmp_path, monkeypatch, bankroll=10000.0, kill_switch_enabled=True, max_daily_loss_pct=0.1):
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(rm_module, "DB_PATH", tmp_path / "risk_state.db")
    # _exit_confidence's new analyst_divergence factor (deep-scan finding
    # 2026-08-10) calls market_analyst_agent.analyst_lean() from
    # check_exits() now - redirect this too, same real-data-contamination
    # bug class this project has already found and fixed twice this
    # session (Item 6's audit, Item 1's trade-tape fixture gap). Without
    # this, every test in this file would read the real
    # data/market_analyst.db on every check_exits() call.
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "market_analyst.db")
    monkeypatch.setattr(cl_module, "DB_PATH", tmp_path / "candidate_log.db")
    # candidate_log.record_rejection() routes both its writes through
    # capture_writer now (P3 Task 16/17), not cl_module.DB_PATH directly -
    # redirect its stores too or a rejection call in these tests would hit
    # capture_writer's default real data/candidate_log.db path (caught,
    # not corrupted, by the sqlite3 guard - but fails the test).
    candidate_log_db_path = tmp_path / "candidate_log.db"
    monkeypatch.setattr(cw_module, "_STORE_PATHS", {
        "rejected_candidates": candidate_log_db_path, "rejection_events": candidate_log_db_path,
    })
    monkeypatch.setattr(cw_module, "_buffers", {"rejected_candidates": {}, "rejection_events": []})
    monkeypatch.setattr(cw_module, "_last_flush_at", {"rejected_candidates": 0.0, "rejection_events": 0.0})
    monkeypatch.setattr(cw_module, "_dropped_counts", {"rejected_candidates": 0, "rejection_events": 0})
    # _exit_confidence's new volatility-normalization (2026-08-14 auto-exit
    # deep-dive) calls market_history.volatility() whenever auto_exit_
    # enabled is on - redirect this too, same real-data-contamination bug
    # class this project has already found and fixed for market_analyst_
    # agent/candidate_log above.
    monkeypatch.setattr(mh_module, "DB_PATH", tmp_path / "market_history.db")
    broker = pb_module.PaperBroker(starting_bankroll=bankroll)
    risk = rm_module.RiskManager(bankroll, max_daily_loss_pct, kill_switch_enabled)
    # Default: no whale-filter opinion at all, so the filter branch is a
    # no-op unless a specific test overrides this.
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": ticker.split("-")[0], "window_days": days, "total_signals": 0,
        "resolved": 0, "correct": 0, "win_rate": None,
    })
    return FollowTheWhaleStrategy(broker, risk), broker, risk


def test_skip_below_confidence_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.5), _cfg(entry_threshold=0.65))
    assert decision["action"] == "skip"
    assert "confidence" in decision["reason"]
    assert broker.bankroll == 10000.0  # nothing traded


def test_skip_below_confidence_threshold_logs_a_rejected_candidate(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(ticker="TICK-Z", side="yes", price=0.5, confidence=0.5), _cfg(entry_threshold=0.65))
    gates = cl_module.gate_summary()
    assert len(gates) == 1
    assert gates[0]["strategy"] == "whale_follow"
    assert gates[0]["gate_name"] == "entry_threshold"
    assert gates[0]["rejected_count"] == 1
    # 2026-08-23, ROADMAP.md's "entry gates select a worse subset" item -
    # every candidate_log rejection in evaluate() now carries unit_cost
    # (signal.price, side-adjusted), not just observed_value/threshold_value.
    assert gates[0]["avg_unit_cost"] == pytest.approx(0.5)


def test_skip_below_min_whale_winrate_logs_a_rejected_candidate(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": ticker.split("-")[0], "window_days": days, "total_signals": 10,
        "resolved": 10, "correct": 3, "win_rate": 30.0,
    })
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.7), _cfg(entry_threshold=0.65, min_whale_winrate_pct=40, min_resolved_for_whale_filter=5))
    assert decision["action"] == "skip"
    gates = cl_module.gate_summary()
    assert len(gates) == 1
    assert gates[0]["gate_name"] == "min_whale_winrate_pct"
    assert gates[0]["avg_unit_cost"] == pytest.approx(0.7)


def test_skip_when_close_time_is_more_than_two_hours_away(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3 * 3600))
    decision = strategy.evaluate(_signal(confidence=0.9, close_time=close_time), _cfg(entry_threshold=0.65))
    assert decision["action"] == "skip"
    assert "close time is not within the trade window" in decision["reason"]
    gates = cl_module.gate_summary()
    assert any(g["gate_name"] == "close_window" for g in gates)


def test_trade_when_close_time_is_within_two_hours(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60 * 60))
    decision = strategy.evaluate(_signal(confidence=0.9, close_time=close_time), _cfg(entry_threshold=0.65))
    assert decision["action"] == "trade"


def test_close_window_is_configurable_not_hardcoded(tmp_path, monkeypatch):
    # 2026-08-14 direct report: the window used to be a hardcoded module
    # constant with no config knob. strategy.close_window_sec now drives it -
    # a signal 3h out is rejected under the 2h default (test above) but
    # accepted once the config widens the window past 3h.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3 * 3600))
    decision = strategy.evaluate(
        _signal(confidence=0.9, close_time=close_time),
        _cfg(entry_threshold=0.65, close_window_sec=4 * 3600),
    )
    assert decision["action"] == "trade"


# ---- effective_close_time fix (2026-08-24) - the close-window/runway gates
# must use the market's real resolution time, not signal.close_time's raw
# administrative close_time alone, when a fresher value is available on the
# market dict passed via markets=. See services/market_lookup.py's
# effective_close_time docstring for the live repro this closes.

def test_uses_market_expected_expiration_time_over_a_far_signal_close_time(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    far_close = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 30 * 86400))
    near_expected = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60 * 60))
    sig = _signal(confidence=0.9, close_time=far_close, ticker="TICK-A")
    markets = [{"ticker": "TICK-A", "close_time": far_close, "expected_expiration_time": near_expected}]
    decision = strategy.evaluate(sig, _cfg(entry_threshold=0.65), markets=markets)
    assert decision["action"] == "trade"


def test_uses_market_occurrence_datetime_over_a_far_signal_close_time(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    far_close = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 30 * 86400))
    near_occurrence = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60 * 60))
    sig = _signal(confidence=0.9, close_time=far_close, ticker="TICK-A")
    markets = [{"ticker": "TICK-A", "close_time": far_close, "occurrence_datetime": near_occurrence}]
    decision = strategy.evaluate(sig, _cfg(entry_threshold=0.65), markets=markets)
    assert decision["action"] == "trade"


def test_falls_back_to_signal_close_time_when_markets_is_none(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3 * 3600))
    decision = strategy.evaluate(
        _signal(confidence=0.9, close_time=close_time, ticker="TICK-A"),
        _cfg(entry_threshold=0.65), markets=None,
    )
    assert decision["action"] == "skip"
    assert "close time is not within the trade window" in decision["reason"]


def test_falls_back_to_signal_close_time_when_ticker_not_in_markets(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60 * 60))
    decision = strategy.evaluate(
        _signal(confidence=0.9, close_time=close_time, ticker="TICK-A"),
        _cfg(entry_threshold=0.65),
        markets=[{"ticker": "TICK-OTHER", "close_time": close_time}],
    )
    assert decision["action"] == "trade"


# ---- favorite-longshot-bias-aware entry threshold (docs/prediction-market-strategy-alignment-plan.md Part 2.3) ----

def test_longshot_price_requires_a_higher_confidence_bar(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    # 0.10 is inside the default 0.15 longshot zone (<=0.15 or >=0.85) - the
    # flat entry_threshold (0.65) alone wouldn't block a 0.70-confidence
    # signal, but the +0.15 longshot bonus raises the real bar to 0.80.
    decision = strategy.evaluate(_signal(confidence=0.70, price=0.10), _cfg(entry_threshold=0.65))
    assert decision["action"] == "skip"
    assert "longshot zone" in decision["reason"]


def test_longshot_price_still_trades_above_the_raised_bar(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.85, price=0.10), _cfg(entry_threshold=0.65))
    assert decision["action"] == "trade"


def test_longshot_price_trades_near_close_without_bonus(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 10 * 60))
    decision = strategy.evaluate(
        _signal(confidence=0.70, price=0.10, close_time=close_time),
        _cfg(entry_threshold=0.65),
    )
    assert decision["action"] == "trade"


def test_longshot_price_skips_when_far_from_close(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60 * 60))
    decision = strategy.evaluate(
        _signal(confidence=0.70, price=0.10, close_time=close_time),
        _cfg(entry_threshold=0.65),
    )
    assert decision["action"] == "skip"
    assert "longshot zone" in decision["reason"]


def test_longshot_zone_applies_symmetrically_to_high_prices(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.70, price=0.92), _cfg(entry_threshold=0.65))
    assert decision["action"] == "skip"
    assert "longshot zone" in decision["reason"]


def test_mid_range_price_uses_the_flat_threshold_unmodified(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    # 0.70 confidence clears a flat 0.65 threshold with no longshot bonus applied.
    decision = strategy.evaluate(_signal(confidence=0.70, price=0.50), _cfg(entry_threshold=0.65))
    assert decision["action"] == "trade"


def test_longshot_threshold_and_bonus_are_configurable(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    # Narrow the longshot zone to 0.05 and shrink the bonus to 0.05 - a 0.10
    # price is no longer inside the zone, so the flat threshold applies.
    decision = strategy.evaluate(
        _signal(confidence=0.68, price=0.10),
        _cfg(entry_threshold=0.65, longshot_price_threshold=0.05, longshot_entry_threshold_bonus=0.05),
    )
    assert decision["action"] == "trade"


# ---- category-conditional entry threshold ("web of expertise" audit, 2026-08-11;
# migrated 2026-08-15 to the generic services/config_overrides.py resolver) ----

def _with_category_override(cfg, category, field, value):
    cfg["strategy_overrides"] = {"by_category": {category: {field: value}}}
    return cfg


def test_category_override_replaces_the_flat_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _with_category_override(_cfg(entry_threshold=0.5), "Sports", "entry_threshold", 0.9)
    decision = strategy.evaluate(_signal(confidence=0.6, price=0.5), cfg, category="Sports")
    # 0.6 clears the flat 0.5 default but not the Sports-specific 0.9 override
    assert decision["action"] == "skip"
    assert "confidence" in decision["reason"]


def test_category_override_does_not_affect_other_categories(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _with_category_override(_cfg(entry_threshold=0.5), "Sports", "entry_threshold", 0.9)
    decision = strategy.evaluate(_signal(confidence=0.6, price=0.5), cfg, category="Politics")
    assert decision["action"] == "trade"  # Politics has no override - flat 0.5 applies


def test_category_override_ignored_when_category_not_passed(tmp_path, monkeypatch):
    # No caller before this feature passed category at all - must behave
    # exactly as before for anyone who still doesn't.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _with_category_override(_cfg(entry_threshold=0.5), "Sports", "entry_threshold", 0.9)
    decision = strategy.evaluate(_signal(confidence=0.6, price=0.5), cfg)
    assert decision["action"] == "trade"  # category=None -> flat threshold, no override lookup


def test_category_override_combines_with_the_longshot_bonus(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _with_category_override(
        _cfg(entry_threshold=0.5, longshot_price_threshold=0.15, longshot_entry_threshold_bonus=0.15),
        "Sports", "entry_threshold", 0.6,
    )
    # Sports base is 0.6, longshot zone adds +0.15 = 0.75 effective bar.
    decision = strategy.evaluate(_signal(confidence=0.70, price=0.10), cfg, category="Sports")
    assert decision["action"] == "skip"
    assert "longshot zone" in decision["reason"]


# ---- series-level overrides (services/config_overrides.py, direct request 2026-08-15:
# "these strategies need to be able to be tweaked for individual series") ----

def test_series_override_replaces_the_flat_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _cfg(entry_threshold=0.5)
    cfg["strategy_overrides"] = {"by_series": {"TICK": {"entry_threshold": 0.9}}}
    decision = strategy.evaluate(_signal(ticker="TICK-A", confidence=0.6, price=0.5), cfg)
    assert decision["action"] == "skip"
    assert "confidence" in decision["reason"]


def test_series_override_wins_over_category_override(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _cfg(entry_threshold=0.5)
    cfg["strategy_overrides"] = {
        "by_category": {"Sports": {"entry_threshold": 0.9}},
        "by_series": {"TICK": {"entry_threshold": 0.4}},
    }
    # Category alone would skip (0.6 < 0.9); the series override (0.4) wins and trades.
    decision = strategy.evaluate(_signal(ticker="TICK-A", confidence=0.6, price=0.5), cfg, category="Sports")
    assert decision["action"] == "trade"


def test_trade_when_conditions_met(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), _cfg())
    assert decision["action"] == "trade"
    assert broker.bankroll < 10000.0
    assert "TICK-A" in broker.positions


def test_trade_sizes_no_side_off_inverted_price(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    # price is always the yes price - a NO signal's real unit cost is
    # (1-price)=0.8/contract here, not 0.2/contract.
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.2, side="no"), _cfg(max_position_pct=0.05))
    assert decision["action"] == "trade"
    expected_contracts = int(10000.0 * 0.05 / 0.8)
    assert decision["trade"]["size"] == expected_contracts
    fee = taker_fee(expected_contracts, 0.2)
    assert broker.bankroll == pytest.approx(10000.0 - expected_contracts * 0.8 - fee)


# ---- kelly_scaled_max_size (deep-scan finding 1, 2026-08-10) -------------
# Pure-function tests for the shared sizing helper, independent of either
# strategy's full evaluate()/_evaluate_one() plumbing.

def test_kelly_scaled_max_size_off_by_default_returns_unchanged():
    assert kelly_scaled_max_size(500.0, confidence=0.61, effective_threshold=0.6, kelly_fraction=0.0) == 500.0


def test_kelly_scaled_max_size_full_fraction_scales_to_near_zero_at_threshold():
    result = kelly_scaled_max_size(500.0, confidence=0.6, effective_threshold=0.6, kelly_fraction=1.0)
    assert result == pytest.approx(0.0)


def test_kelly_scaled_max_size_full_fraction_returns_full_size_at_confidence_one():
    result = kelly_scaled_max_size(500.0, confidence=1.0, effective_threshold=0.6, kelly_fraction=1.0)
    assert result == pytest.approx(500.0)


def test_kelly_scaled_max_size_full_fraction_interpolates_linearly():
    # Halfway between threshold (0.6) and 1.0 confidence -> half the ceiling.
    result = kelly_scaled_max_size(500.0, confidence=0.8, effective_threshold=0.6, kelly_fraction=1.0)
    assert result == pytest.approx(250.0)


def test_kelly_scaled_max_size_blends_between_flat_and_scaled():
    # kelly_fraction=0.5 should land halfway between the flat-cap result
    # (500.0) and the fully-scaled result at this confidence (0.0 at the
    # threshold itself) - i.e. 250.0.
    result = kelly_scaled_max_size(500.0, confidence=0.6, effective_threshold=0.6, kelly_fraction=0.5)
    assert result == pytest.approx(250.0)


def test_kelly_scaled_max_size_never_exceeds_the_ceiling():
    # Confidence above 1.0 (shouldn't happen given clamping elsewhere, but
    # this function itself should never hand back more than max_size).
    result = kelly_scaled_max_size(500.0, confidence=1.5, effective_threshold=0.6, kelly_fraction=1.0)
    assert result <= 500.0


def test_kelly_scaled_max_size_handles_effective_threshold_of_one():
    # Degenerate case - a threshold of 1.0 would divide by zero in the raw
    # linear formula; this must return the unscaled ceiling instead of
    # raising.
    result = kelly_scaled_max_size(500.0, confidence=0.9, effective_threshold=1.0, kelly_fraction=1.0)
    assert result == 500.0


def test_kelly_scaled_max_size_never_returns_a_negative_ceiling():
    # kelly_fraction_of_cap is live-editable from the dashboard Controls
    # panel and documented as a 0-1 dial. Nothing bounded the upper end,
    # and `scale = 1.0 - kelly_fraction * (1.0 - raw_scale)` goes negative
    # as soon as kelly_fraction > 1 for any confidence short of the top of
    # the band - a fat-fingered `3` instead of `0.3` produced a NEGATIVE
    # position ceiling (measured: 500.0 -> -1000.0 at confidence 0.6).
    # A size ceiling is a magnitude; it can never be below zero.
    for kelly_fraction in (1.5, 3.0, 100.0):
        for confidence in (0.6, 0.7, 0.8, 0.9, 1.0):
            result = kelly_scaled_max_size(
                500.0, confidence=confidence, effective_threshold=0.6, kelly_fraction=kelly_fraction,
            )
            assert result >= 0.0, f"negative ceiling at kelly_fraction={kelly_fraction}, confidence={confidence}"
            assert result <= 500.0


def test_kelly_scaled_max_size_clamps_fraction_above_one_to_full_strength():
    # Out-of-domain values clamp to the strongest *documented* behavior
    # (1.0 = "full linear scaling"), rather than being clipped at zero.
    # Clipping at zero would silently refuse to trade every signal below a
    # confidence cutoff while reporting only "position size rounds to
    # zero" - the same silent-disable failure class services/config_bounds
    # .py was written to prevent. config_bounds.check() reports the bad
    # value separately so it gets fixed rather than just absorbed.
    for confidence in (0.6, 0.75, 0.9, 1.0):
        assert kelly_scaled_max_size(
            500.0, confidence=confidence, effective_threshold=0.6, kelly_fraction=5.0,
        ) == pytest.approx(kelly_scaled_max_size(
            500.0, confidence=confidence, effective_threshold=0.6, kelly_fraction=1.0,
        ))


def test_kelly_scaled_max_size_stays_monotonic_in_confidence():
    # Higher confidence must never buy a smaller position, at any dial
    # setting. This held even while the ceiling was going negative (the
    # curve stayed ordered, it just started below zero), so it is a
    # regression guard for the clamp rather than a reproduction of the
    # original bug - it must keep holding once the clamp is in.
    for kelly_fraction in (0.0, 0.3, 1.0, 3.0):
        sizes = [
            kelly_scaled_max_size(500.0, confidence=c, effective_threshold=0.6, kelly_fraction=kelly_fraction)
            for c in (0.6, 0.7, 0.8, 0.9, 1.0)
        ]
        assert sizes == sorted(sizes), f"non-monotonic at kelly_fraction={kelly_fraction}: {sizes}"


def test_kelly_scaled_max_size_treats_none_fraction_as_off():
    # Real live bug (2026-08-15): kelly_fraction_of_cap: null (Python None)
    # used to crash `None <= 0` here - null is this app's own established
    # "disabled" convention (take_profit_pct/stop_loss_pct/
    # max_open_positions_per_series all treat it that way), and was the
    # committed config default at the time. Must behave identically to
    # kelly_fraction=0.0, not raise.
    result = kelly_scaled_max_size(500.0, confidence=0.9, effective_threshold=0.6, kelly_fraction=None)
    assert result == 500.0


# ---- Position sizing scales with confidence when kelly_fraction_of_cap is set ----

def test_position_size_unaffected_by_confidence_when_kelly_fraction_unset(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.99, price=0.5), _cfg(entry_threshold=0.65))
    expected_contracts = int(10000.0 * 0.05 / 0.5)  # full max_position_pct cap, same as any other confidence
    assert decision["trade"]["size"] == expected_contracts


def test_position_size_scales_down_near_threshold_with_kelly_fraction_set(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    # confidence just barely clears entry_threshold (0.65) - full kelly_fraction
    # should size this close to (but not exactly) zero contracts.
    decision = strategy.evaluate(
        _signal(confidence=0.66, price=0.5), _cfg(entry_threshold=0.65, kelly_fraction_of_cap=1.0),
    )
    full_cap_contracts = int(10000.0 * 0.05 / 0.5)
    assert decision["action"] == "trade"
    assert 0 < decision["trade"]["size"] < full_cap_contracts


def test_position_size_at_full_confidence_matches_full_cap_with_kelly_fraction_set(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=1.0, price=0.5), _cfg(entry_threshold=0.65, kelly_fraction_of_cap=1.0),
    )
    full_cap_contracts = int(10000.0 * 0.05 / 0.5)
    assert decision["trade"]["size"] == full_cap_contracts


def test_skip_when_halted(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    risk.manual_halt("testing")
    decision = strategy.evaluate(_signal(confidence=0.9), _cfg())
    assert decision["action"] == "skip"
    assert "halted" in decision["reason"]


def test_kill_switch_trips_on_real_unrealized_drawdown_when_prices_passed(tmp_path, monkeypatch):
    # Audit finding (2026-08-09): the kill switch used to check
    # self.broker.equity({}) - an empty prices dict makes every position's
    # unrealized P&L silently compute as exactly 0 (mark_to_market falls
    # back to entry_price), so it was mathematically identical to checking
    # bankroll alone. A big real unrealized loss sitting in an open
    # position, with bankroll itself untouched, should now halt new trades
    # once latest_prices is actually passed through.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch, bankroll=10000.0, max_daily_loss_pct=0.1)
    # A large yes position that has since collapsed in price - realized
    # bankroll only reflects the entry cost/fee, not this unrealized loss.
    broker.open_position("TICK-A", "yes", size=5000, price=0.5, reason="entry")
    # Position now worth almost nothing - unrealized loss alone comfortably
    # exceeds 10% of the 10000 starting bankroll (cost basis was 2500).
    latest_prices = {"TICK-A": 0.01}
    equity_with_prices = broker.equity(latest_prices)
    assert equity_with_prices < 10000.0 * 0.9  # sanity: this really does cross the 10% daily-loss line

    decision = strategy.evaluate(_signal(ticker="TICK-B", confidence=0.9), _cfg(), latest_prices=latest_prices)
    assert decision["action"] == "skip"
    assert "halted" in decision["reason"]


def test_kill_switch_ignores_unrealized_drawdown_when_no_prices_passed(tmp_path, monkeypatch):
    # Backward-compatible default: a caller that doesn't pass latest_prices
    # at all (latest_prices=None) gets the old bankroll-only behavior, not
    # a crash or a silently different halt decision.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch, bankroll=10000.0, max_daily_loss_pct=0.1)
    broker.open_position("TICK-A", "yes", size=5000, price=0.5, reason="entry")
    decision = strategy.evaluate(_signal(ticker="TICK-B", confidence=0.9), _cfg())
    assert decision["action"] != "skip" or "halted" not in decision.get("reason", "")


def test_skip_when_market_already_resolved(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.9), _cfg(), market_results={"TICK-A": "yes"})
    assert decision["action"] == "skip"
    assert "already resolved" in decision["reason"]
    assert broker.bankroll == 10000.0


def test_trades_when_market_results_has_no_entry_for_this_ticker(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg(), market_results={"OTHER-TICKER": "yes"})
    assert decision["action"] == "trade"


def test_skip_when_cooldown_active(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    # Close it first so the "already open" check (evaluated before cooldown)
    # doesn't mask what this test is actually isolating.
    broker.close_position("TICK-A", exit_price=0.5, reason="test")
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    assert decision["action"] == "skip"
    assert "cooldown" in decision["reason"]


def test_skip_when_position_already_open_on_ticker(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    assert "TICK-A" in broker.positions
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    assert decision["action"] == "skip"
    assert "already open" in decision["reason"]
    assert len(broker.trade_log) == 1  # the second signal never touched the broker


# ---- me_complement (mutually-exclusive pair) check (2026-08-14) ----------

def test_skip_when_position_already_open_on_me_complement(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(ticker="TEAM-A", confidence=0.9, price=0.6), _cfg())
    assert "TEAM-A" in broker.positions
    # TEAM-B is a different ticker, confirmed as TEAM-A's 2-outcome
    # mutually-exclusive complement - holding both would be an offsetting
    # bet on the same underlying event, not two independent positions.
    decision = strategy.evaluate(
        _signal(ticker="TEAM-B", confidence=0.9, price=0.4), _cfg(), me_complement="TEAM-A",
    )
    assert decision["action"] == "skip"
    assert "mutually-exclusive complement" in decision["reason"]
    assert "TEAM-B" not in broker.positions


def test_trades_when_me_complement_has_no_open_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TEAM-B", confidence=0.9, price=0.4), _cfg(), me_complement="TEAM-A",
    )
    assert decision["action"] == "trade"


# ---- min_unit_cost/max_unit_cost price-band gate (2026-08-15, real
# trade-history finding: only unit_cost 0.5-0.8 was net profitable across
# 606 real settled trades) --------------------------------------------------

def test_skip_when_unit_cost_below_minimum(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    # yes side: unit_cost == signal.price directly
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.3, side="yes"), _cfg(min_unit_cost=0.5, max_unit_cost=0.8),
    )
    assert decision["action"] == "skip"
    assert "below the minimum unit cost" in decision["reason"]
    assert "TICK-A" not in broker.positions


def test_skip_when_unit_cost_above_maximum(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.9, side="yes"), _cfg(min_unit_cost=0.5, max_unit_cost=0.8),
    )
    assert decision["action"] == "skip"
    assert "above the maximum unit cost" in decision["reason"]


def test_trades_when_unit_cost_inside_the_band(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.6, side="yes"), _cfg(min_unit_cost=0.5, max_unit_cost=0.8),
    )
    assert decision["action"] == "trade"


def test_unit_cost_gate_is_side_aware_for_no(tmp_path, monkeypatch):
    # no side: unit_cost = 1 - signal.price, so a "cheap-looking" yes price
    # of 0.15 is actually an 0.85 unit_cost on the no side - well above a
    # 0.8 ceiling, should still be rejected on that basis, not let through
    # just because the raw yes-price looks low.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.15, side="no"), _cfg(min_unit_cost=0.5, max_unit_cost=0.8),
    )
    assert decision["action"] == "skip"
    assert "above the maximum unit cost" in decision["reason"]
    gates = cl_module.gate_summary()
    assert any(g["gate_name"] == "max_unit_cost" for g in gates)


def test_unit_cost_gate_is_a_noop_when_bounds_are_none(tmp_path, monkeypatch):
    # Default _cfg() sets neither bound - every existing test in this file
    # trades at a variety of prices with no min_unit_cost/max_unit_cost
    # passed, so the gate must be a true no-op (None means "no limit"),
    # not silently reject anything, when omitted.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.05, side="yes"), _cfg())
    assert decision["action"] == "trade"
    assert "TICK-A" in broker.positions


# ---- max_open_positions_per_series (deep-scan finding 2, 2026-08-10) -----
# Concentration risk across simultaneously-open positions on the same
# series - the "already open on ticker" check above only ever guards the
# exact same ticker, never a burst of correlated markets in one series.

def test_skip_when_series_already_at_max_open_positions(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(max_open_positions_per_series=1))
    assert "TICK-A" in broker.positions
    # TICK-B is a different ticker, same series ("TICK") - already at the
    # configured cap of 1, so this should be skipped even though the
    # per-ticker "already open" check alone would have let it through.
    decision = strategy.evaluate(_signal(ticker="TICK-B", confidence=0.9, price=0.5), _cfg(max_open_positions_per_series=1))
    assert decision["action"] == "skip"
    assert "TICK" in decision["reason"]
    assert "TICK-B" not in broker.positions


def test_trades_when_series_below_max_open_positions(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(max_open_positions_per_series=2))
    decision = strategy.evaluate(_signal(ticker="TICK-B", confidence=0.9, price=0.5), _cfg(max_open_positions_per_series=2))
    assert decision["action"] == "trade"
    assert "TICK-B" in broker.positions


def test_max_open_positions_per_series_does_not_block_a_different_series(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(max_open_positions_per_series=1))
    # OTHER-C's series ("OTHER") is unrelated to TICK's - the cap is
    # per-series, not a global open-position count.
    decision = strategy.evaluate(_signal(ticker="OTHER-C", confidence=0.9, price=0.5), _cfg(max_open_positions_per_series=1))
    assert decision["action"] == "trade"


def test_max_open_positions_per_series_unset_means_unlimited(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg())
    # No max_open_positions_per_series in _cfg() at all (None default) -
    # same "null/None means unlimited" convention as kalshi.
    # max_children_per_parent elsewhere in this app.
    decision = strategy.evaluate(_signal(ticker="TICK-B", confidence=0.9, price=0.5), _cfg())
    assert decision["action"] == "trade"


def test_skip_when_whale_winrate_below_minimum(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": "TICK", "window_days": days, "total_signals": 10,
        "resolved": 10, "correct": 2, "win_rate": 20.0,
    })
    decision = strategy.evaluate(
        _signal(confidence=0.9), _cfg(min_whale_winrate_pct=40, min_resolved_for_whale_filter=5)
    )
    assert decision["action"] == "skip"
    assert "win rate" in decision["reason"]


def test_trade_when_not_enough_resolved_to_trust_the_filter(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": "TICK", "window_days": days, "total_signals": 2,
        "resolved": 2, "correct": 0, "win_rate": 0.0,
    })
    # win rate is 0%, but only 2 resolved - below the 5-resolved minimum
    # needed before the filter is trusted, so this should still trade.
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(min_whale_winrate_pct=40, min_resolved_for_whale_filter=5)
    )
    assert decision["action"] == "trade"


def test_skip_when_position_size_rounds_to_zero(tmp_path, monkeypatch):
    # price 0.98 rather than 0.99: the tradeable-range invariant (2026-08-17)
    # now rejects anything outside 0.02-0.98 before sizing is reached, so a
    # 0.99 signal skips for that reason instead and no longer exercises this
    # test's actual subject. 0.98 sits exactly on the allowed boundary and
    # still rounds a $1 bankroll * 5% budget down to zero contracts.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch, bankroll=1.0)
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.98), _cfg(max_position_pct=0.05))
    assert decision["action"] == "skip"
    assert "zero" in decision["reason"]


def test_skip_when_live_markets_only_and_market_not_live(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(live_markets_only=True), is_live=False
    )
    assert decision["action"] == "skip"
    assert "live" in decision["reason"]
    assert broker.bankroll == 10000.0  # nothing traded


def test_trade_when_live_markets_only_and_market_is_live(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(live_markets_only=True), is_live=True
    )
    assert decision["action"] == "trade"


def test_live_markets_only_off_trades_regardless_of_is_live(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(live_markets_only=False), is_live=False
    )
    assert decision["action"] == "trade"


def test_skip_when_series_manually_excluded(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(excluded_series=["TICK"]),
    )
    assert decision["action"] == "skip"
    assert "TICK" in decision["reason"] and "excluded" in decision["reason"]
    assert broker.bankroll == 10000.0  # nothing traded


def test_trades_when_series_not_in_excluded_list(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(excluded_series=["OTHER"]),
    )
    assert decision["action"] == "trade"


def test_empty_excluded_series_list_trades_normally(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(excluded_series=[]),
    )
    assert decision["action"] == "trade"


# ---- watchlist entry gate (2026-09-03 off-watchlist entry bleed fix) ------
#
# Live incident: whale detection runs exchange-wide (services/whalewatchers/
# kalshi_trade_tape.py's _resolve_unknown_markets resolves ANY off-watchlist
# market whose print clears the whale threshold, deliberately, for signal
# completeness), but nothing downstream re-narrowed to the user's configured
# trading universe before opening a position - confirmed live: 17 of 29 open
# paper positions (KXMLBGAME, KXATPMATCH, KXLOLGAME, etc.) were on markets
# never in kalshi.markets_watchlist, despite that list holding only
# Crypto/Commodities series. category/markets_watchlist were never read
# anywhere in this file (grep confirmed) prior to this fix.

def test_skip_when_ticker_outside_watchlist_and_not_in_current_markets(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="KXMLBGAME-26SEP02-STLLAD", confidence=0.9, price=0.5),
        {**_cfg(), "kalshi": {"markets_watchlist": ["KXBTC15M"]}},
        markets=[{"ticker": "KXBTC15M-26SEP0317-T2389.99"}],
    )
    assert decision["action"] == "skip"
    assert "watchlist" in decision["reason"]
    assert broker.bankroll == 10000.0  # nothing traded


def test_trades_when_ticker_present_in_current_markets_snapshot(tmp_path, monkeypatch):
    # Merge-mode discovery: a series outside the pinned list that this
    # tick's own market_fetch already discovered and included in the
    # current watchlist snapshot is a legitimate trade, not a bleed.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5),
        {**_cfg(), "kalshi": {"markets_watchlist": ["OTHER"]}},
        markets=[{"ticker": "TICK-A"}],
    )
    assert decision["action"] == "trade"


def test_trades_when_series_pinned_even_if_missing_from_current_markets_snapshot(tmp_path, monkeypatch):
    # Staleness tolerance: a genuinely pinned series' brand-new market
    # instance may not have been fetched into state["markets"] yet this
    # tick - series membership in the pinned list is enough on its own.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5),
        {**_cfg(), "kalshi": {"markets_watchlist": ["TICK"]}},
        markets=[],
    )
    assert decision["action"] == "trade"


def test_markets_param_omitted_does_not_apply_watchlist_gate(tmp_path, monkeypatch):
    # Same backward-compatible "None means this caller didn't wire this
    # dimension in" convention category/me_complement already use (see
    # evaluate()'s own docstring) - a caller that never passes markets=
    # (every other test in this file) must see unchanged behavior.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5),
        {**_cfg(), "kalshi": {"markets_watchlist": ["OTHER"]}},
    )
    assert decision["action"] == "trade"


# ---- check_exits (active position management) ----------------------------

def _signal_dict(**overrides):
    return _signal(**overrides).to_dict()


def test_check_exits_does_nothing_when_unconfigured(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Price moved massively in the position's favor, but nothing is configured.
    decisions = strategy.check_exits({"TICK-A": 0.99}, [], _cfg())
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_take_profit_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # check_exits' pnl_pct is fee-inclusive (2026-08-09 audit finding: a
    # stop_loss_pct/take_profit_pct should mean "X% of what was actually put
    # in," not "X% of the raw price move before fees make it worse") - raw
    # gain is (0.75-0.5)*100/50 = 0.5 exactly, but entry_fee + the close fee
    # this exit itself incurs eat into that, so the real trigger threshold
    # (0.4) is set comfortably below the fee-inclusive ~0.439, not the raw 0.5.
    entry_fee = taker_fee(100, 0.5)
    close_fee = taker_fee(100, 0.75)
    expected_pnl_pct = ((0.75 - 0.5) * 100 - entry_fee - close_fee) / 50
    assert 0.4 <= expected_pnl_pct < 0.5  # fee-inclusive gain sits between the new and the old (raw) threshold
    decisions = strategy.check_exits({"TICK-A": 0.75}, [], _cfg(take_profit_pct=0.4))
    assert len(decisions) == 1
    assert decisions[0]["action"] == "close"
    assert decisions[0]["ticker"] == "TICK-A"
    assert "take-profit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions
    # started 10000, cost 50, cash back 75, minus both legs' real fees
    assert broker.bankroll == pytest.approx(1000.0 * 10 - 50 + 75 - entry_fee - close_fee)


def test_check_exits_take_profit_does_not_trigger_below_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # only +10% of cost basis, threshold is 50%
    decisions = strategy.check_exits({"TICK-A": 0.55}, [], _cfg(take_profit_pct=0.5))
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_stop_loss_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # -40% of cost basis: (0.3 - 0.5) * 100 / 50 = -0.4
    decisions = strategy.check_exits({"TICK-A": 0.3}, [], _cfg(stop_loss_pct=0.3))
    assert len(decisions) == 1
    assert "stop-loss" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_stop_loss_does_not_trigger_above_limit(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # only -10% loss, limit is -30%
    decisions = strategy.check_exits({"TICK-A": 0.45}, [], _cfg(stop_loss_pct=0.3))
    assert decisions == []


# --- price corroboration (2026-08-17, direct instruction "fix this
# immediately" after a real WTA position - Cirstea/Kalinskaya - was
# liquidated via stop-loss at exit_price 0.0 one tick after market_history's
# own REST-polled price had sat pinned at 0.99 for 13+ minutes) ----------

def test_check_exits_distrusts_a_ws_price_far_from_a_fresh_rest_snapshot(tmp_path, monkeypatch):
    """The exact failure reproduced: a single garbage WS tick (0.0) must
    not liquidate a position market_history's own fresh REST data says is
    worth 0.99. Corroboration should override current_price before the
    stop-loss check ever sees the garbage value."""
    from services import strategy_engine, market_history

    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.69, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: 0.99)

    decisions = strategy.check_exits({"TICK-A": 0.0}, [], _cfg(stop_loss_pct=0.4))
    assert decisions == [], "a 0.99 corroborated price must not stop-loss a yes position"
    assert "TICK-A" in broker.positions


def test_check_exits_still_fires_when_ws_and_rest_agree(tmp_path, monkeypatch):
    """Corroboration must not disable real stop-losses - only distrust an
    isolated outlier tick."""
    from services import strategy_engine, market_history

    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: 0.31)

    decisions = strategy.check_exits({"TICK-A": 0.3}, [], _cfg(stop_loss_pct=0.3))
    assert len(decisions) == 1
    assert "stop-loss" in decisions[0]["reason"]


def test_check_exits_fails_open_with_no_recent_snapshot(tmp_path, monkeypatch):
    """No market_history data (illiquid ticker, cold start) must not block
    a stop-loss that would otherwise correctly fire - same behaviour as
    before this fix existed."""
    from services import strategy_engine, market_history

    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: None)

    decisions = strategy.check_exits({"TICK-A": 0.3}, [], _cfg(stop_loss_pct=0.3))
    assert len(decisions) == 1


def test_check_exits_allows_a_genuine_large_move_within_the_deviation_band(tmp_path, monkeypatch):
    """A real, large, fast move that both sources already agree on
    (within the deviation band) must fire immediately, not be delayed."""
    from services import strategy_engine, market_history

    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.7, reason="entry")
    # WS and REST agree closely (within the 0.30 band) that price cratered.
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: 0.32)

    decisions = strategy.check_exits({"TICK-A": 0.3}, [], _cfg(stop_loss_pct=0.4))
    assert len(decisions) == 1


def test_check_exits_skips_position_opened_this_same_tick(tmp_path, monkeypatch):
    # Live bug, 2026-08-11: a position the signal loop just opened this
    # tick was immediately stop-lossed against latest_prices snapshotted
    # at the *top* of the same tick - stale relative to a real trade-tape
    # print used as this position's own entry_price on a fast-moving
    # market. opened_since (main.py passes tick_now) must make check_exits
    # skip anything opened at/after it, even though the raw price move
    # here would otherwise clear the stop-loss.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    tick_now = time.time()
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # -40% of cost basis - would trigger a 30% stop-loss if evaluated.
    decisions = strategy.check_exits(
        {"TICK-A": 0.3}, [], _cfg(stop_loss_pct=0.3), opened_since=tick_now,
    )
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_still_applies_to_positions_opened_before_this_tick(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    broker.positions["TICK-A"].opened_at = time.time() - 300  # opened 5 minutes ago, a prior tick
    tick_now = time.time()
    decisions = strategy.check_exits(
        {"TICK-A": 0.3}, [], _cfg(stop_loss_pct=0.3), opened_since=tick_now,
    )
    assert len(decisions) == 1
    assert "stop-loss" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_sentiment_reversal_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Position is YES, but recent signals lean heavily NO.
    feed = [
        _signal_dict(ticker="TICK-A", side="no", size=8000),
        _signal_dict(ticker="TICK-A", side="no", size=7000),
        _signal_dict(ticker="TICK-A", side="yes", size=1000),
    ]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(exit_on_sentiment_reversal=True, exit_sentiment_min_signals=3, exit_sentiment_lean_pct=65),
    )
    assert len(decisions) == 1
    assert "sentiment reversed" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_sentiment_reversal_needs_enough_signals(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Heavily against the position, but only 2 signals when 3 are required.
    feed = [
        _signal_dict(ticker="TICK-A", side="no", size=8000),
        _signal_dict(ticker="TICK-A", side="no", size=7000),
    ]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(exit_on_sentiment_reversal=True, exit_sentiment_min_signals=3, exit_sentiment_lean_pct=65),
    )
    assert decisions == []


def test_check_exits_sentiment_reversal_needs_strong_enough_lean(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Roughly balanced, well under the 65% reversal bar.
    feed = [
        _signal_dict(ticker="TICK-A", side="no", size=5100),
        _signal_dict(ticker="TICK-A", side="yes", size=4900),
        _signal_dict(ticker="TICK-A", side="no", size=100),
    ]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(exit_on_sentiment_reversal=True, exit_sentiment_min_signals=3, exit_sentiment_lean_pct=65),
    )
    assert decisions == []


def test_check_exits_sentiment_reversal_off_by_default(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    feed = [_signal_dict(ticker="TICK-A", side="no", size=9000) for _ in range(5)]
    decisions = strategy.check_exits({"TICK-A": 0.5}, feed, _cfg())  # exit_on_sentiment_reversal not set
    assert decisions == []


def test_check_exits_handles_multiple_positions_independently(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # will hit take-profit
    broker.open_position("TICK-B", "yes", size=100, price=0.5, reason="entry")  # stays flat, shouldn't close
    # take_profit_pct=0.4 - see test_check_exits_take_profit_closes_position
    # for why 0.4, not the raw-math 0.5, is the right threshold to use here.
    decisions = strategy.check_exits(
        {"TICK-A": 0.75, "TICK-B": 0.5}, [], _cfg(take_profit_pct=0.4),
    )
    assert len(decisions) == 1
    assert decisions[0]["ticker"] == "TICK-A"
    assert "TICK-A" not in broker.positions
    assert "TICK-B" in broker.positions


def test_check_exits_no_side_position_uses_correct_direction(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.4, reason="entry")  # cost = 100*(1-0.4) = 60
    # yes price drops 0.4 -> 0.1: NO side gains. Raw pnl = (0.4-0.1)*100 = 30,
    # raw pnl_pct = 30/60 = 0.5 - but fee-inclusive (see the take-profit test
    # above) it's ~0.46, so take_profit_pct is set to 0.4 to still clear it.
    entry_fee = taker_fee(100, 0.4)
    close_fee = taker_fee(100, 0.1)
    expected_pnl_pct = (30 - entry_fee - close_fee) / 60
    assert expected_pnl_pct >= 0.4
    # latest_asks added 2026-09-04: a NO position is sold into the NO bid
    # (1 - yes_ask), so an exit now needs an ask to price against at all.
    # Quoted at the same 0.1 the bid is, so this test's own arithmetic above
    # is untouched - it is still asserting NO-side P&L direction, nothing else.
    decisions = strategy.check_exits(
        {"TICK-A": 0.1}, [], _cfg(take_profit_pct=0.4), latest_asks={"TICK-A": 0.1},
    )
    assert len(decisions) == 1
    assert "TICK-A" not in broker.positions


# ---- check_exits: auto_exit (automated multi-factor exit confidence) -----

def test_check_exits_auto_exit_off_by_default(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # +30% unrealized gain - well past what auto_exit's default gain
    # reference would score as full pressure - but auto_exit_enabled isn't set.
    decisions = strategy.check_exits({"TICK-A": 0.65}, [], _cfg())
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_auto_exit_pnl_gain_triggers_at_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Fee-inclusive pnl_pct at exit 0.7: raw gain (0.7-0.5)*100=20, minus
    # entry_fee + this exit's own close_fee, over cost basis 50. Default
    # gain reference 0.5 -> pnl_factor = pnl_pct/0.5, comfortably clearing
    # the default 0.6 auto_exit_threshold on its own. No signal_feed at
    # all, so sentiment/staleness factors are entirely absent (not zero)
    # from the average - confidence is exactly this pnl factor. If a
    # missing factor were wrongly scored as 0 instead of omitted, this
    # would average down well below threshold and never trigger - this is
    # the case that proves it isn't.
    entry_fee = taker_fee(100, 0.5)
    close_fee = taker_fee(100, 0.7)
    expected_pnl_factor = min(1.0, (((0.7 - 0.5) * 100 - entry_fee - close_fee) / 50) / 0.5)
    assert expected_pnl_factor >= 0.6
    decisions = strategy.check_exits({"TICK-A": 0.7}, [], _cfg(auto_exit_enabled=True))
    assert len(decisions) == 1
    assert decisions[0]["action"] == "close"
    assert "auto-exit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_auto_exit_pnl_gain_below_threshold_no_trigger(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # pnl_pct = 0.2 -> pnl_factor = 0.2/0.5 = 0.4, below the 0.6 threshold.
    decisions = strategy.check_exits({"TICK-A": 0.6}, [], _cfg(auto_exit_enabled=True))
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_auto_exit_pnl_loss_triggers(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # pnl_pct = -0.3; default loss reference is 0.3 -> pnl_factor = 1.0.
    decisions = strategy.check_exits({"TICK-A": 0.35}, [], _cfg(auto_exit_enabled=True))
    assert len(decisions) == 1
    assert "auto-exit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_auto_exit_pnl_alone_insufficient_with_sentiment_present(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # pnl_factor = 0.1/0.5 = 0.2. Whale flow on this ticker has fully
    # reversed against the held side (sentiment_factor = 1.0), but its
    # weight is zeroed here to isolate: pnl alone shouldn't be enough.
    feed = [_signal_dict(ticker="TICK-A", side="no", size=1000) for _ in range(3)]
    decisions = strategy.check_exits(
        {"TICK-A": 0.55}, feed,
        _cfg(auto_exit_enabled=True, auto_exit_sentiment_weight=0, auto_exit_staleness_weight=0),
    )
    assert decisions == []


def test_check_exits_auto_exit_sentiment_factor_contributes_to_confidence(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # A small fee-inclusive pnl_factor alone (exit 0.6, comparable in shape
    # to test_check_exits_auto_exit_pnl_alone_insufficient_with_sentiment_
    # present's 0.55) isn't enough on its own - this time sentiment's
    # default weight (1.0) is left on, and the whale-lean fully reversed
    # (sentiment_factor=1.0) pulls the averaged confidence up past the
    # default 0.6 threshold where pnl alone would not have.
    entry_fee = taker_fee(100, 0.5)
    close_fee = taker_fee(100, 0.6)
    pnl_factor = min(1.0, (((0.6 - 0.5) * 100 - entry_fee - close_fee) / 50) / 0.5)
    assert (pnl_factor + 1.0) / 2 >= 0.6  # sanity: combined with full-reversal sentiment, this should clear threshold
    feed = [_signal_dict(ticker="TICK-A", side="no", size=1000) for _ in range(3)]
    decisions = strategy.check_exits(
        {"TICK-A": 0.6}, feed,
        _cfg(auto_exit_enabled=True, auto_exit_staleness_weight=0),
    )
    assert len(decisions) == 1
    assert "sentiment=100%" in decisions[0]["reason"]


def test_check_exits_auto_exit_still_runs_when_sentiment_reversal_also_enabled(tmp_path, monkeypatch):
    # Real bug found 2026-08-14: exit_on_sentiment_reversal and
    # auto_exit_enabled used to be two separate `elif` branches on the same
    # chain as take_profit/stop_loss. Those two are safe as elif because
    # their own condition tests both "enabled" and "actually triggered" -
    # exit_on_reversal's elif only tested the enabled flag, so whenever it
    # was simply True the branch was taken regardless of whether sentiment
    # actually reversed, and if it then found nothing (too few signals,
    # the common case), the chain had already used its one shot -
    # auto_exit was completely unreachable for as long as both flags were
    # on together, which config/settings.yaml's history shows was real
    # production config, not just a hypothetical. This reproduces exactly
    # that combination with a feed too thin to clear exit_sentiment_min_
    # signals, and confirms auto_exit still fires.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    feed = [_signal_dict(ticker="TICK-A", side="no", size=1000)]  # only 1, below min_signals=3 below
    decisions = strategy.check_exits(
        {"TICK-A": 0.35}, feed,
        _cfg(
            exit_on_sentiment_reversal=True, exit_sentiment_min_signals=3, exit_sentiment_lean_pct=65,
            auto_exit_enabled=True,
        ),
    )
    assert len(decisions) == 1
    assert "auto-exit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_auto_exit_volatility_dampens_loss_factor(tmp_path, monkeypatch):
    # 2026-08-14 direct request ("look at it from all angles... volatility"):
    # gain_ref/loss_ref used to be flat percentages applied identically
    # regardless of how much a ticker normally moves. Seed a genuinely
    # volatile recent price history for TICK-A (swings well past the
    # default auto_exit_normal_volatility=0.02 baseline), then confirm the
    # same -0.3-ish pnl_pct that maxes pnl_factor to 1.0 with no volatility
    # data (test_check_exits_auto_exit_pnl_loss_triggers) no longer clears
    # the default 0.6 threshold once loss_ref widens to reflect this
    # ticker's real recent noise.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    now = time.time()
    for i, price in enumerate([0.5, 0.75, 0.35, 0.8, 0.3]):
        mh_module.record_snapshots([{"ticker": "TICK-A", "yes_price": price}], timestamp=now - 1800 + i * 400)
    decisions = strategy.check_exits({"TICK-A": 0.35}, [], _cfg(auto_exit_enabled=True))
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_auto_exit_series_track_record_factor_contributes(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Override _strategy()'s default no-opinion series_stats with a real,
    # 0%-win-rate track record, well past min_resolved_for_whale_filter's
    # default floor (5) - off by default (weight 0.0), only contributes
    # once explicitly opted in via the cfg override below.
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": "TICK", "window_days": days, "total_signals": 20,
        "resolved": 20, "correct": 0, "win_rate": 0.0,
    })
    entry_fee = taker_fee(100, 0.5)
    close_fee = taker_fee(100, 0.6)
    pnl_factor = min(1.0, (((0.6 - 0.5) * 100 - entry_fee - close_fee) / 50) / 0.5)
    assert (pnl_factor + 1.0) / 2 >= 0.6  # sanity: combined with a maxed series_factor, this should clear threshold
    decisions = strategy.check_exits(
        {"TICK-A": 0.6}, [],
        _cfg(auto_exit_enabled=True, auto_exit_series_track_record_weight=1.0),
    )
    assert len(decisions) == 1
    assert "series_track_record=100%" in decisions[0]["reason"]


def test_check_exits_auto_exit_staleness_factor_triggers_close(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Price hasn't moved (pnl_factor weighted to 0) - the only thing arguing
    # for closing is that the last whale print on this ticker was an hour
    # ago, twice the default 1800s (30min) stale_after window, so
    # staleness_factor caps at 1.0.
    feed = [_signal_dict(ticker="TICK-A", side="yes", size=1000, timestamp=time.time() - 3600)]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(auto_exit_enabled=True, auto_exit_pnl_weight=0, auto_exit_sentiment_weight=0),
    )
    assert len(decisions) == 1
    assert "staleness=100%" in decisions[0]["reason"]


# analyst_divergence factor (deep-scan finding 2026-08-10) - analyst_lean()
# was already feeding entry confidence on both strategies but nothing ever
# consulted it on the exit side. Same "left out of the average entirely
# when absent" idiom as sentiment/staleness above.

def test_check_exits_auto_exit_analyst_divergence_absent_without_a_fresh_estimate(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # No analysis ever recorded for this ticker - analyst_lean() returns
    # None, so the factor is left out entirely, same as sentiment/staleness
    # when there's no whale data. With every other weight also zeroed,
    # total_weight is 0 and nothing should trigger.
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, [],
        _cfg(auto_exit_enabled=True, auto_exit_pnl_weight=0, auto_exit_sentiment_weight=0, auto_exit_staleness_weight=0),
    )
    assert decisions == []


def test_check_exits_auto_exit_analyst_divergence_factor_triggers_close_on_yes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # A fresh, maximally bearish analyst estimate (0.0 = the model thinks
    # this can't resolve YES) fully diverges from the held "yes" side -
    # price hasn't moved, no whale signals, but the analyst_divergence
    # factor alone should clear the default 0.6 threshold.
    maa_module.record_analysis(
        "TICK-A", "TICK", market_price=0.5, estimated_probability=0.0,
        llm_confidence=0.9, reasoning="r", model="m",
    )
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, [],
        _cfg(auto_exit_enabled=True, auto_exit_pnl_weight=0, auto_exit_sentiment_weight=0, auto_exit_staleness_weight=0),
    )
    assert len(decisions) == 1
    assert "analyst_divergence=100%" in decisions[0]["reason"]


def test_check_exits_auto_exit_analyst_divergence_direction_flips_for_no_side(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.5, reason="entry")
    # Same strongly bearish estimate now AGREES with a held "no" position -
    # zero divergence pressure, nothing should trigger.
    maa_module.record_analysis(
        "TICK-A", "TICK", market_price=0.5, estimated_probability=0.05,
        llm_confidence=0.9, reasoning="r", model="m",
    )
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, [],
        _cfg(auto_exit_enabled=True, auto_exit_pnl_weight=0, auto_exit_sentiment_weight=0, auto_exit_staleness_weight=0),
    )
    assert decisions == []


def test_check_exits_auto_exit_analyst_divergence_neutral_estimate_no_pressure(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # A neutral 0.5 estimate is neither agreement nor divergence - factor
    # should be exactly 0, same "50/50 = no pressure" language as sentiment.
    maa_module.record_analysis(
        "TICK-A", "TICK", market_price=0.5, estimated_probability=0.5,
        llm_confidence=0.5, reasoning="r", model="m",
    )
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, [],
        _cfg(auto_exit_enabled=True, auto_exit_pnl_weight=0, auto_exit_sentiment_weight=0, auto_exit_staleness_weight=0),
    )
    assert decisions == []


def test_check_exits_auto_exit_yields_to_hard_triggers_when_both_configured(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Both take_profit_pct and auto_exit_enabled would independently close
    # this position - take_profit_pct is a hard rail checked first, so the
    # reason should be the explicit take-profit one, not auto-exit's.
    decisions = strategy.check_exits(
        {"TICK-A": 0.75}, [], _cfg(take_profit_pct=0.1, auto_exit_enabled=True),
    )
    assert len(decisions) == 1
    assert "take-profit" in decisions[0]["reason"]
    assert "auto-exit" not in decisions[0]["reason"]


# ---- check_exits: market settlement (unconditional, not opt-in) ----------

def test_check_exits_settles_winning_position_at_full_dollar(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # cost 50, bankroll -> 9950
    decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg(), market_results={"TICK-A": "yes"})
    assert len(decisions) == 1
    assert "settled YES" in decisions[0]["reason"]
    assert "won" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions
    # full $1/contract payout, not the stale 0.5 latest_price. Settlement's
    # terminal price (1.0) means zero close-leg fee (services/kalshi_fees.py) -
    # only the entry leg's real fee applies.
    entry_fee = taker_fee(100, 0.5)
    assert broker.bankroll == pytest.approx(9950.0 + 100.0 - entry_fee)


def test_check_exits_settles_losing_position_at_zero(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # cost 50, bankroll -> 9950
    decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg(), market_results={"TICK-A": "no"})
    assert len(decisions) == 1
    assert "settled NO" in decisions[0]["reason"]
    assert "lost" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions
    entry_fee = taker_fee(100, 0.5)
    assert broker.bankroll == pytest.approx(9950.0 - entry_fee)  # nothing paid back, entry fee already spent


def test_check_exits_settles_no_side_position_correctly(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.4, reason="entry")  # cost 100*(1-0.4)=60, bankroll -> 9940
    decisions = strategy.check_exits({"TICK-A": 0.4}, [], _cfg(), market_results={"TICK-A": "no"})
    assert len(decisions) == 1
    assert "won" in decisions[0]["reason"]
    entry_fee = taker_fee(100, 0.4)
    assert broker.bankroll == pytest.approx(9940.0 + 100.0 - entry_fee)


def test_check_exits_ignores_unresolved_market_results(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    for result in ("", None, "unknown"):
        decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg(), market_results={"TICK-A": result})
        assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_settlement_takes_priority_over_opt_in_triggers(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # take_profit_pct would also fire on this price move, but the market has
    # actually settled - settlement should win, not the opt-in take-profit path.
    decisions = strategy.check_exits(
        {"TICK-A": 0.75}, [], _cfg(take_profit_pct=0.1), market_results={"TICK-A": "yes"},
    )
    assert len(decisions) == 1
    assert "settled YES" in decisions[0]["reason"]
    assert "take-profit" not in decisions[0]["reason"]


def test_check_exits_settlement_defaults_to_no_market_results(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # No market_results arg at all - existing call sites/tests shouldn't break.
    decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg())
    assert decisions == []
    assert "TICK-A" in broker.positions


# --- config_fingerprint passthrough (docs/advisory-engine-plan.md) ---------

def test_evaluate_threads_config_fingerprint_into_open_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), _cfg(), config_fingerprint="fp-abc")
    assert decision["action"] == "trade"
    assert broker.positions["TICK-A"].config_fingerprint == "fp-abc"
    assert broker.trade_log[0].config_fingerprint == "fp-abc"


def test_evaluate_config_fingerprint_defaults_to_none(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), _cfg())
    assert decision["action"] == "trade"
    assert broker.positions["TICK-A"].config_fingerprint is None


# ---- use_limit_orders (2026-08-15 maker-order path) -----------------------

def test_evaluate_places_a_limit_order_instead_of_a_market_trade_when_enabled(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.8, price=0.5), _cfg(use_limit_orders=True, limit_order_timeout_sec=30),
    )
    assert decision["action"] == "limit_order_placed"
    assert decision["order"]["ticker"] == "TICK-A"
    assert decision["order"]["limit_price"] == 0.5
    # No cash committed and no position opened yet - it's only resting.
    assert broker.positions == {}
    assert broker.trade_log == []
    assert "TICK-A" in broker.pending_orders


def test_evaluate_use_limit_orders_off_by_default_unchanged_behavior(tmp_path, monkeypatch):
    # No use_limit_orders key at all in cfg (not even explicitly False) -
    # existing callers/tests must be completely unaffected.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), _cfg())
    assert decision["action"] == "trade"
    assert broker.pending_orders == {}


def test_evaluate_skips_when_a_limit_order_is_already_pending_on_the_ticker(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _cfg(use_limit_orders=True, cooldown_sec=0)
    first = strategy.evaluate(_signal(confidence=0.8, price=0.5), cfg)
    assert first["action"] == "limit_order_placed"
    second = strategy.evaluate(_signal(confidence=0.9, price=0.4), cfg)
    assert second["action"] == "skip"
    assert "already resting" in second["reason"]
    assert len(broker.pending_orders) == 1  # the first order, untouched


def test_evaluate_carries_signal_confidence_onto_the_resting_order(tmp_path, monkeypatch):
    # The resting order needs its own signal's confidence available at fill
    # time (check_pending_fills' validate_fn) - the price it was PLACED at
    # doesn't tell you the confidence a fresh signal at the FILL price would
    # need to clear.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(confidence=0.77, price=0.5), _cfg(use_limit_orders=True))
    assert broker.pending_orders["TICK-A"].confidence == pytest.approx(0.77)


# ---- "four-entry gate bypass" fix: fill-time re-validation ----
# check_pending_fills previously called open_position() straight off
# whatever price the market happened to fill at, with none of
# entry_threshold/the price band re-checked against that price - only
# ever checked once, at placement time, against the price the order
# ASKED for. See strategy_engine._validate_entry_price's own docstring.

def test_validate_entry_price_rejects_confidence_below_threshold(tmp_path, monkeypatch):
    from services.strategy_engine import _validate_entry_price

    v = _validate_entry_price("yes", 0.5, confidence=0.5, effective_threshold=0.65, strat_cfg={})
    assert v.ok is False
    assert v.gate_name == "entry_threshold"


def test_validate_entry_price_rejects_the_hard_zero_or_one_floor(tmp_path, monkeypatch):
    from services.strategy_engine import _validate_entry_price

    v = _validate_entry_price("yes", 0.995, confidence=0.99, effective_threshold=0.65, strat_cfg={})
    assert v.ok is False
    assert v.gate_name == "tradeable_price_range"


def test_validate_entry_price_rejects_outside_configured_min_max_band(tmp_path, monkeypatch):
    from services.strategy_engine import _validate_entry_price

    strat_cfg = {"min_unit_cost": 0.6, "max_unit_cost": 0.8}
    below = _validate_entry_price("yes", 0.4, confidence=0.9, effective_threshold=0.65, strat_cfg=strat_cfg)
    assert below.ok is False and below.gate_name == "min_unit_cost"
    above = _validate_entry_price("yes", 0.9, confidence=0.9, effective_threshold=0.65, strat_cfg=strat_cfg)
    assert above.ok is False and above.gate_name == "max_unit_cost"


def test_validate_entry_price_side_aware_unit_cost(tmp_path, monkeypatch):
    # signal.price is always the YES price - a "no" order at price=0.9 has a
    # real unit cost of 0.1 (1 - 0.9), which the band below permits even
    # though 0.9 itself wouldn't.
    from services.strategy_engine import _validate_entry_price

    strat_cfg = {"min_unit_cost": 0.05, "max_unit_cost": 0.5}
    v = _validate_entry_price("no", 0.9, confidence=0.9, effective_threshold=0.65, strat_cfg=strat_cfg)
    assert v.ok is True


def test_validate_entry_price_passes_inside_every_band():
    from services.strategy_engine import _validate_entry_price

    v = _validate_entry_price(
        "yes", 0.7, confidence=0.9, effective_threshold=0.65,
        strat_cfg={"min_unit_cost": 0.6, "max_unit_cost": 0.8},
    )
    assert v.ok is True
    assert v.gate_name is None


# ---- Edge/EV gate (strategy-edge-gate-implementation, Task 8) ----
# _edge_gate_check is appended inside _validate_entry_price, after the
# existing price-band checks, so a resting limit order's fill-time
# re-check (validate_pending_fill) is held to the same bar as a fresh
# signal - same reasoning as every other check in this function (see its
# own docstring, the "four-entry gate bypass" fix). ticker/category/as_of
# all default to None, so every pre-existing call site/test above this
# comment keeps its exact prior behavior unchanged.

def test_edge_gate_off_by_default_changes_nothing(tmp_path, monkeypatch):
    """edge_gate_enabled defaults to false (Task 1) - confirms the gate
    truly is a no-op until deliberately turned on, even with a ticker
    passed through."""
    from services import strategy_engine as se

    result = se._validate_entry_price(
        "yes", 0.5, 0.9, 0.5, {}, ticker="TICK-A", category="Crypto", as_of=time.time(),
    )
    assert result.ok


def test_edge_gate_rejects_when_edge_below_min_edge(tmp_path, monkeypatch):
    from services import strategy_engine as se, market_history as mh, series_cache as sc

    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    now = time.time()
    # P_pre snapshot: yes_price 0.50 at (now - offset - 1s), so q_pre_now ~= 0.50
    with mh._connect(mh.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO snapshots (ticker, series, yes_price, timestamp) VALUES (?,?,?,?)",
            ("TICK-A", "TICK", 0.50, now - 11),
        )
    strat_cfg = {"edge_gate_enabled": True, "edge_gate_min_edge": 0.04,
                 "edge_gate_pre_print_offset_sec": 10.0, "edge_gate_p_pre_max_age_sec": 600.0}
    # No Δ_calibrated cache populated -> delta defaults to 0.0 -> p_est_side ~= q_pre_now ~= 0.50
    # ask_now = price = 0.55 (bought 5c above P_pre) -> edge is clearly negative
    result = se._validate_entry_price(
        "yes", 0.55, 0.9, 0.5, strat_cfg, ticker="TICK-A", category="Crypto", as_of=now,
    )
    assert not result.ok
    assert result.gate_name == "edge_gate"


def test_edge_gate_fails_open_when_p_pre_unavailable(tmp_path, monkeypatch):
    from services import strategy_engine as se, market_history as mh, series_cache as sc

    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    strat_cfg = {"edge_gate_enabled": True, "edge_gate_min_edge": 0.04}
    # No snapshot rows at all -> P_pre unavailable -> fails open, gate not evaluated
    result = se._validate_entry_price(
        "yes", 0.55, 0.9, 0.5, strat_cfg, ticker="TICK-A", category="Crypto", as_of=time.time(),
    )
    assert result.ok  # falls through to EntryValidation(True)


def test_edge_gate_fails_closed_on_flat_fee_type(tmp_path, monkeypatch):
    from services import strategy_engine as se, market_history as mh, series_cache as sc

    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    sc.save(time.time(), [{"ticker": "TICK", "fee_type": "flat"}])
    strat_cfg = {"edge_gate_enabled": True, "edge_gate_min_edge": 0.0}
    result = se._validate_entry_price(
        "yes", 0.55, 0.9, 0.5, strat_cfg, ticker="TICK-A", category="Crypto", as_of=time.time(),
    )
    assert result.ok  # not evaluated (flat), falls open the same way as missing P_pre


def test_evaluate_admitted_decision_includes_edge_gate_fields_when_computed(tmp_path, monkeypatch):
    """Task 9 (docs/superpowers/plans/2026-09-03-strategy-edge-gate-
    implementation.md): design §9's informativeness criterion - p_est/
    Δ_calibrated/edge must be inspectable per-signal, not only a boolean
    pass/fail. EntryValidation.observed/.threshold already carry
    edge/min_edge for the REJECT case (Task 8, see
    test_edge_gate_rejects_when_edge_below_min_edge above); this is the
    ADMITTED case - a signal that passed the gate - which carried no
    extra fields before this task. Same setup shape as Task 8's
    test_edge_gate_rejects_when_edge_below_min_edge (a real P_pre
    snapshot, edge_gate_enabled: true) but with the P_pre snapshot ABOVE
    the ask price (0.60 vs. 0.50) instead of below it, so
    p_est_side (~0.60, delta defaults to 0.0 with no calibration cache
    populated) clears ask_now (0.50) + fee + edge_gate_min_edge (0.04)
    comfortably instead of failing it - drives the strategy's real
    evaluate() end to end via this file's own _strategy/_signal/_cfg
    fixtures rather than calling _validate_entry_price directly, so the
    admitted decision dict this test asserts on is the one a real caller
    actually receives."""
    from services import series_cache as sc_module

    monkeypatch.setattr(sc_module, "DB_PATH", tmp_path / "series_cache.db")
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    # P_pre snapshot: yes_price 0.60 at (now - offset - 1s), so q_pre_now ~= 0.60
    with mh_module._connect(mh_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO snapshots (ticker, series, yes_price, timestamp) VALUES (?,?,?,?)",
            ("TICK-A", "TICK", 0.60, now - 11),
        )
    cfg = _cfg(
        edge_gate_enabled=True, edge_gate_min_edge=0.04,
        edge_gate_pre_print_offset_sec=10.0, edge_gate_p_pre_max_age_sec=600.0,
    )
    # ask_now = signal price = 0.50 (bought 10c below P_pre) -> edge is clearly positive
    decision = strategy.evaluate(_signal(timestamp=now, price=0.5, confidence=0.8), cfg, category="Crypto")
    assert decision["action"] == "trade"
    assert "edge_gate" in decision
    assert set(decision["edge_gate"].keys()) >= {"p_est", "delta", "edge", "q_pre"}
    assert decision["edge_gate"]["edge"] >= 0.04


def test_evaluate_admitted_decision_omits_edge_gate_when_not_computed(tmp_path, monkeypatch):
    """Counterpart to the test above - when the gate never ran (off by
    default, per Task 1), the admitted decision dict carries no
    fabricated edge_gate numbers, matching every pre-existing evaluate()
    test in this file (none of which expect an "edge_gate" key)."""
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(), _cfg(), category="Crypto")
    assert decision["action"] == "trade"
    assert "edge_gate" not in decision


def test_evaluate_and_validate_pending_fill_pass_ticker_category_as_of_through(tmp_path, monkeypatch):
    """Wiring test, not a re-test of the gate's own logic (covered above) -
    confirms both real call sites actually forward the new parameters,
    not just that _validate_entry_price itself accepts them."""
    from services import strategy_engine as se

    calls = []
    real = se._validate_entry_price

    def _spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(se, "_validate_entry_price", _spy)
    strategy = _strategy(tmp_path, monkeypatch)[0]
    strategy.evaluate(_signal(), _cfg(), category="Crypto")
    assert calls[-1].get("ticker") == "TICK-A" and calls[-1].get("category") == "Crypto"
    calls.clear()
    strategy.validate_pending_fill("TICK-A", "yes", 0.5, 0.9, _cfg(), category="Crypto")
    assert calls[-1].get("ticker") == "TICK-A" and calls[-1].get("category") == "Crypto"


def test_validate_pending_fill_records_a_rejection_in_candidate_log(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _cfg(min_unit_cost=0.6, max_unit_cost=0.8)
    ok, reason = strategy.validate_pending_fill("TICK-A", "yes", 0.9, confidence=0.9, cfg=cfg)
    assert ok is False
    assert "maximum unit cost" in reason
    gates = {g["gate_name"]: g for g in cl_module.gate_summary()}
    assert gates["max_unit_cost"]["rejected_count"] == 1
    assert gates["max_unit_cost"]["avg_unit_cost"] == pytest.approx(0.9)


def test_check_pending_fills_end_to_end_rejects_a_fill_that_moved_outside_the_band(tmp_path, monkeypatch):
    # Full path: evaluate() places a resting order at a price/confidence
    # that clears every gate, then the market moves before it fills - the
    # fill-time price now violates max_unit_cost. Before this fix,
    # check_pending_fills had no way to know that and would have opened
    # the position anyway.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _cfg(use_limit_orders=True, min_unit_cost=0.3, max_unit_cost=0.6)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), cfg)
    assert decision["action"] == "limit_order_placed"

    def _validate_fill(ticker, side, price, confidence):
        return strategy.validate_pending_fill(ticker, side, price, confidence, cfg)

    # Ask has dropped to 0.2 - a genuinely better fill price than the 0.5
    # limit asked for, but 0.2 is below max_unit_cost's floor of 0.3.
    fills = broker.check_pending_fills(
        latest_bids={"TICK-A": 0.18}, latest_asks={"TICK-A": 0.20}, validate_fn=_validate_fill,
    )
    assert len(fills) == 1
    assert fills[0]["action"] == "fill_rejected"
    assert broker.positions == {}
    assert broker.pending_orders == {}
    assert broker.bankroll == 10000.0


def test_check_pending_fills_end_to_end_still_fills_inside_the_band(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    cfg = _cfg(use_limit_orders=True, min_unit_cost=0.3, max_unit_cost=0.6)
    strategy.evaluate(_signal(confidence=0.8, price=0.5), cfg)

    def _validate_fill(ticker, side, price, confidence):
        return strategy.validate_pending_fill(ticker, side, price, confidence, cfg)

    fills = broker.check_pending_fills(
        latest_bids={"TICK-A": 0.44}, latest_asks={"TICK-A": 0.45}, validate_fn=_validate_fill,
    )
    assert len(fills) == 1
    assert fills[0]["action"] == "trade"
    assert "TICK-A" in broker.positions


# ---- ROADMAP #1: minimum-runway gates (entry + exit) ----
# 2026-08-16/17 direct report: "position management didn't reverse sentiment
# immediately" / positions riding to settlement unmanaged. close_window_sec
# was only an UPPER bound on time-to-close; nothing refused an entry, or
# forced an exit, once too little runway remained to manage the position.

def test_entry_rejected_when_runway_below_minimum(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 45))
    decision = strategy.evaluate(
        _signal(confidence=0.9, close_time=close_time),
        _cfg(entry_threshold=0.65, close_window_sec=4 * 3600, min_seconds_to_close=300),
    )
    assert decision["action"] == "skip"
    assert "runway" in decision["reason"]


def test_entry_allowed_when_runway_above_minimum(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 1800))
    decision = strategy.evaluate(
        _signal(confidence=0.9, close_time=close_time),
        _cfg(entry_threshold=0.65, close_window_sec=4 * 3600, min_seconds_to_close=300),
    )
    assert decision["action"] == "trade"


def test_min_seconds_to_close_unset_is_a_no_op(tmp_path, monkeypatch):
    # Backward compatibility: every existing config with no
    # min_seconds_to_close set must behave exactly as before.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 5))
    decision = strategy.evaluate(
        _signal(confidence=0.9, close_time=close_time),
        _cfg(entry_threshold=0.65, close_window_sec=4 * 3600),
    )
    assert decision["action"] == "trade"


def test_min_seconds_to_close_skipped_when_market_is_live(tmp_path, monkeypatch):
    # Same reasoning close_window already uses: for an in-play event the
    # scheduled close time isn't authoritative.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 5))
    decision = strategy.evaluate(
        _signal(confidence=0.9, close_time=close_time),
        _cfg(entry_threshold=0.65, close_window_sec=4 * 3600, min_seconds_to_close=300),
        is_live=True,
    )
    assert decision["action"] == "trade"


def test_exit_forced_when_runway_exhausted(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", 100, 0.50, "test entry")
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 20))
    # Price flat, so neither take-profit nor stop-loss would ever fire -
    # this position would otherwise ride straight to settlement.
    decisions = strategy.check_exits(
        {"TICK-A": 0.50}, [], _cfg(exit_min_seconds_to_close=60),
        close_times={"TICK-A": close_time},
    )
    assert len(decisions) == 1
    assert "runway exhausted" in decisions[0]["reason"]


def test_exit_not_forced_while_runway_remains(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", 100, 0.50, "test entry")
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))
    decisions = strategy.check_exits(
        {"TICK-A": 0.50}, [], _cfg(exit_min_seconds_to_close=60),
        close_times={"TICK-A": close_time},
    )
    assert decisions == []


def test_exit_runway_gate_is_a_no_op_without_close_time(tmp_path, monkeypatch):
    # No close_time known for the ticker - degrade honestly, never guess.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", 100, 0.50, "test entry")
    decisions = strategy.check_exits(
        {"TICK-A": 0.50}, [], _cfg(exit_min_seconds_to_close=60), close_times={},
    )
    assert decisions == []


# --- tradeable-price invariant (2026-08-17, direct instruction: "whale bets
# at cost 0 or 100c are just plain wrong... you shouldnt ever be seeing
# positions being made like this at all... not because of restrictions but
# because of practicality") --------------------------------------------

def test_rejects_a_full_dollar_unit_cost_regardless_of_config(tmp_path, monkeypatch):
    """The case actually found in live trade history: a no-side signal at a
    yes-price of 0.0 is a unit cost of 1.00 - paying the whole dollar for a
    contract that can pay at most a dollar."""
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.99, side="no", price=0.0),
        # Deliberately no band configured at all: this must not be reachable
        # by loosening config, because it isn't a preference.
        _cfg(min_unit_cost=None, max_unit_cost=None),
    )
    assert decision["action"] == "skip"
    assert "tradeable range" in decision["reason"]


def test_rejects_the_1c_and_99c_extremes_on_both_sides(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    for side, price in (("yes", 0.99), ("no", 0.01), ("yes", 0.01), ("no", 0.99)):
        decision = strategy.evaluate(
            _signal(confidence=0.99, side=side, price=price),
            _cfg(min_unit_cost=None, max_unit_cost=None),
        )
        assert decision["action"] == "skip", f"{side} @ {price} should be refused"
        assert "tradeable range" in decision["reason"]


def test_allows_the_boundary_prices_themselves(tmp_path, monkeypatch):
    """0.02 and 0.98 are the edges of what's allowed, not past them - an
    off-by-one here would silently narrow the strategy's whole universe."""
    from services.config import config_bounds

    assert config_bounds.is_tradeable_unit_cost(0.02) is True
    assert config_bounds.is_tradeable_unit_cost(0.98) is True
    assert config_bounds.is_tradeable_unit_cost(0.019) is False
    assert config_bounds.is_tradeable_unit_cost(0.981) is False
    assert config_bounds.is_tradeable_unit_cost(None) is False


# --- volatility scaling of the auto-exit references (2026-08-17) ---------

def test_zero_volatility_is_treated_as_no_reading_not_as_perfect_calm(tmp_path, monkeypatch):
    """Measured live: 142 of 183 well-sampled markets returned volatility
    exactly 0.0 - a price that hasn't ticked in 30 minutes usually means
    nobody is trading it. Feeding that through pinned vol_ratio to its 0.25
    floor for 78% of markets, permanently quartering gain_ref/loss_ref so
    the pnl factor saturated on a ~24% move instead of the configured ~95%.

    Asserted through the public behaviour: an identical position must score
    the same whether volatility reads 0.0 or is unavailable."""
    from services import market_history
    from services.exits import exit_engine

    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.50, reason="entry")
    cfg = _cfg(auto_exit_normal_volatility=0.002, auto_exit_volatility_lookback_sec=1800,
               auto_exit_gain_reference_pct=0.95, auto_exit_loss_reference_pct=0.85)["strategy"]

    pos = broker.positions["TICK-A"]
    monkeypatch.setattr(market_history, "volatility", lambda *a, **k: None)
    unavailable, _ = exit_engine._exit_confidence(pos, 0.20, "TICK-A", [], cfg)

    monkeypatch.setattr(market_history, "volatility", lambda *a, **k: 0.0)
    flat, _ = exit_engine._exit_confidence(pos, 0.20, "TICK-A", [], cfg)

    assert flat == pytest.approx(unavailable), (
        "a flat/untraded market must not be scored as if it were four times calmer than normal"
    )


def test_real_volatility_still_scales_the_references(tmp_path, monkeypatch):
    """The factor must still discriminate among markets that actually move -
    the fix is about zero, not about disabling the mechanism."""
    from services import market_history
    from services.exits import exit_engine

    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.50, reason="entry")
    cfg = _cfg(auto_exit_normal_volatility=0.002)["strategy"]

    pos = broker.positions["TICK-A"]
    monkeypatch.setattr(market_history, "volatility", lambda *a, **k: 0.0005)
    calm, _ = exit_engine._exit_confidence(pos, 0.20, "TICK-A", [], cfg)
    monkeypatch.setattr(market_history, "volatility", lambda *a, **k: 0.02)
    wild, _ = exit_engine._exit_confidence(pos, 0.20, "TICK-A", [], cfg)
    assert calm != wild, "volatility must still change the outcome when it is real"


# --- staleness-triggered corroboration (P8 Task 35, resolves R4) -----------
# The deviation gate above catches a WRONG price (a garbage tick). It never
# caught a STALE one: an in-memory price that drifted mildly while its WS
# ticker channel went quiet passed through indefinitely. Now, when the price
# check_exits is about to act on is older than
# strategy.price_staleness_corroborate_sec, the independent REST read is
# trusted even within the deviation band. Fail-open stays the rule; the
# uncorroborated-and-stale case becomes visible via fault_log, never silent.

def test_stale_ws_price_defers_to_rest_corroboration_even_within_the_deviation_band(tmp_path, monkeypatch):
    from services import market_history
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: 0.31)  # |0.5-0.31| = 0.19, inside the 0.30 band
    now = time.time()

    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, [], _cfg(stop_loss_pct=0.3, price_staleness_corroborate_sec=120.0),
        latest_prices_updated_at={"TICK-A": now - 200.0},
    )

    assert len(decisions) == 1 and "stop-loss" in decisions[0]["reason"]


def test_fresh_ws_price_keeps_the_deviation_only_rule(tmp_path, monkeypatch):
    from services import market_history
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: 0.31)
    now = time.time()

    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, [], _cfg(stop_loss_pct=0.3, price_staleness_corroborate_sec=120.0),
        latest_prices_updated_at={"TICK-A": now - 5.0},
    )

    assert decisions == []  # fresh + within band: the WS value stands, exactly as before


def test_stale_and_uncorroborated_fails_open_but_records_a_fault_once_per_window(tmp_path, monkeypatch):
    from services import market_history, fault_log
    from services.exits import exit_engine
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: None)
    recorded = []
    monkeypatch.setattr(fault_log, "record_fault", lambda *a, **k: recorded.append((a, k)) or True)
    exit_engine.reset_window()
    cfg = _cfg(stop_loss_pct=0.3, price_staleness_corroborate_sec=120.0)
    stamps = {"TICK-A": time.time() - 200.0}

    decisions = strategy.check_exits({"TICK-A": 0.3}, [], cfg, latest_prices_updated_at=stamps)
    assert len(decisions) == 1  # fail-open: the stale value is still acted on
    assert len(recorded) == 1
    assert recorded[0][0][0] == "exit_engine" and recorded[0][0][1] == "stale_price_uncorroborated"

    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    strategy.check_exits({"TICK-A": 0.3}, [], cfg, latest_prices_updated_at=stamps)
    assert len(recorded) == 1  # same ticker, same window: not re-logged

    exit_engine.reset_window()
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    strategy.check_exits({"TICK-A": 0.3}, [], cfg, latest_prices_updated_at=stamps)
    assert len(recorded) == 2  # new window: logged again


def test_zero_threshold_disables_the_staleness_trigger(tmp_path, monkeypatch):
    from services import market_history
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: 0.31)

    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, [], _cfg(stop_loss_pct=0.3, price_staleness_corroborate_sec=0),
        latest_prices_updated_at={"TICK-A": time.time() - 9999.0},
    )

    assert decisions == []


def test_callers_that_pass_no_stamps_get_the_pre_task35_behavior(tmp_path, monkeypatch):
    from services import market_history
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    monkeypatch.setattr(market_history, "recent_price", lambda *a, **k: 0.31)

    decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg(stop_loss_pct=0.3, price_staleness_corroborate_sec=120.0))

    assert decisions == []  # no stamps supplied: no staleness knowledge, deviation-only, additive guarantee


# --- Exit quotes must come from the side of the book the position is
# actually sold into (2026-09-04). `latest_prices` holds yes_bid; a NO
# position is sold by hitting the NO bid, which is (1 - yes_ask), NOT
# (1 - yes_bid) - that is the NO *ask*, the price to BUY no.
# docs/kalshi/get-market-orderbook.md: "a bid for yes at price X is
# equivalent to an ask for no at price (100-X)".

def _no_corroboration(monkeypatch):
    """Isolate these tests from the 2026-08-17 REST corroboration branch."""
    monkeypatch.setattr(mh_module, "recent_price", lambda *a, **k: None)


def test_check_exits_will_not_sell_a_no_position_into_an_empty_yes_book(tmp_path, monkeypatch):
    # The live bug: yes_bid 0.00 made a NO position worth (1 - 0.00) = $1.00
    # per contract, as if the market had already settled NO, and that
    # phantom mark maxed the auto-exit pnl factor so the exit fired.
    _no_corroboration(monkeypatch)
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.40, reason="entry")
    bankroll_before = broker.bankroll

    decisions = strategy.check_exits(
        {"TICK-A": 0.0}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
        latest_asks={"TICK-A": 1.0},
    )

    assert decisions == []
    assert "TICK-A" in broker.positions
    assert broker.bankroll == pytest.approx(bankroll_before)


def test_check_exits_sells_a_no_position_at_the_no_bid_not_the_no_ask(tmp_path, monkeypatch):
    _no_corroboration(monkeypatch)
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.40, reason="entry")
    bankroll_before = broker.bankroll

    decisions = strategy.check_exits(
        {"TICK-A": 0.20}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
        latest_asks={"TICK-A": 0.30},
    )

    assert len(decisions) == 1
    # NO bid = 1 - yes_ask = 0.70/contract. The bug paid 1 - yes_bid = 0.80.
    expected = bankroll_before + 100 * 0.70 - taker_fee(100, 0.30, ticker="TICK-A")
    assert broker.bankroll == pytest.approx(expected, abs=0.01)


def test_check_exits_still_sells_a_yes_position_at_the_yes_bid(tmp_path, monkeypatch):
    _no_corroboration(monkeypatch)
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.40, reason="entry")
    bankroll_before = broker.bankroll

    decisions = strategy.check_exits(
        {"TICK-A": 0.20}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
        latest_asks={"TICK-A": 0.30},
    )

    assert len(decisions) == 1
    # Selling YES hits the YES bid - unchanged behavior, the ask is ignored.
    expected = bankroll_before + 100 * 0.20 - taker_fee(100, 0.20, ticker="TICK-A")
    assert broker.bankroll == pytest.approx(expected, abs=0.01)


def test_check_exits_will_not_sell_a_no_position_with_no_ask_data_at_all(tmp_path, monkeypatch):
    # Fail CLOSED, deliberately unlike this module's usual fail-open rule for
    # missing data: with no ask there is no NO bid to sell into, and guessing
    # one is exactly how the phantom $1.00 payouts were manufactured. The
    # position rides to settlement, which is its true outcome.
    _no_corroboration(monkeypatch)
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.40, reason="entry")
    bankroll_before = broker.bankroll

    decisions = strategy.check_exits(
        {"TICK-A": 0.20}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
    )

    assert decisions == []
    assert "TICK-A" in broker.positions
    assert broker.bankroll == pytest.approx(bankroll_before)


def test_check_exits_still_settles_a_no_position_on_an_empty_book(tmp_path, monkeypatch):
    # The guard above must never block a SETTLEMENT close: once Kalshi has
    # published market.result the terminal $1/$0 payout is real, not a quote,
    # and an empty book at that moment is expected rather than suspicious.
    _no_corroboration(monkeypatch)
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.40, reason="entry")
    bankroll_before = broker.bankroll

    decisions = strategy.check_exits(
        {"TICK-A": 0.0}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
        market_results={"TICK-A": "no"}, latest_asks={"TICK-A": 1.0},
    )

    assert len(decisions) == 1
    assert "TICK-A" not in broker.positions
    # Held side won: $1.00/contract, no fee at a terminal price.
    assert broker.bankroll == pytest.approx(bankroll_before + 100.0, abs=0.01)


def test_check_exits_will_not_sell_a_yes_position_with_no_bid(tmp_path, monkeypatch):
    # Declared behavior CHANGE (2026-09-04 adversarial review, D3): a YES
    # position at yes_bid 0.00 previously stop-lossed at $0, booking a total
    # loss on a price nobody was actually bidding. It now holds to
    # settlement instead, where the real $1/$0 is published.
    _no_corroboration(monkeypatch)
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.40, reason="entry")
    bankroll_before = broker.bankroll

    decisions = strategy.check_exits(
        {"TICK-A": 0.0}, [], _cfg(stop_loss_pct=0.1), latest_asks={"TICK-A": 0.02},
    )

    assert decisions == []
    assert "TICK-A" in broker.positions
    assert broker.bankroll == pytest.approx(bankroll_before)


def test_check_exits_corroborated_bid_does_not_suppress_a_no_exit_as_crossed(tmp_path, monkeypatch):
    # 2026-09-04 adversarial review, D2. The corroboration branch may replace
    # the WS bid with a REST snapshot up to 120s old. Checking a live ask
    # against that substituted bid reads as "crossed" on a market that has
    # genuinely rallied, silently disabling every NO-side exit - inverting
    # the purpose of a branch that exists to ENABLE correct exits.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.90, reason="entry")
    # Raw WS bid 0.20; REST says 0.62, a 0.42 deviation, so it is substituted.
    monkeypatch.setattr(mh_module, "recent_price", lambda *a, **k: 0.62)

    decisions = strategy.check_exits(
        {"TICK-A": 0.20}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
        latest_asks={"TICK-A": 0.60},
    )

    # ask 0.60 < corroborated bid 0.62, but >= the raw WS bid it is checked
    # against, so the sale is priced and goes through at the NO bid (1-0.60).
    assert len(decisions) == 1
    assert "TICK-A" not in broker.positions


def test_check_exits_refuses_a_no_exit_on_an_ask_the_rest_read_contradicts(tmp_path, monkeypatch):
    # 2026-09-04 adversarial review, D7. Checking crossing against the raw WS
    # bid (the D2 fix) removed the NO side's only defense against a garbage
    # LOW ask. A tick quoting bid 0.00 / ask 0.05 while market_history's
    # independent REST read sits at 0.99 passes the crossed test, and would
    # sell a near-worthless NO position for $0.95/contract - the 2026-08-17
    # WTA incident relocated to the other side.
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.10, reason="entry")
    monkeypatch.setattr(mh_module, "recent_price", lambda *a, **k: 0.99)
    bankroll_before = broker.bankroll

    decisions = strategy.check_exits(
        {"TICK-A": 0.0}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
        latest_asks={"TICK-A": 0.05},
    )

    assert decisions == []
    assert "TICK-A" in broker.positions
    assert broker.bankroll == pytest.approx(bankroll_before)


def test_check_exits_crossed_check_is_skipped_when_there_is_no_live_bid(tmp_path, monkeypatch):
    # 2026-09-04 adversarial review, D8: yes_bid falls back to the position's
    # entry_price when the ticker has no live quote, and comparing a live ask
    # against a historical entry price false-refuses. With no live bid the
    # crossed check has nothing valid to compare and is skipped.
    _no_corroboration(monkeypatch)
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.90, reason="entry")

    # No entry in latest_prices at all; ask 0.40 is below the 0.90 entry price.
    decisions = strategy.check_exits(
        {}, [], _cfg(auto_exit_enabled=True, auto_exit_threshold=0.0),
        latest_asks={"TICK-A": 0.40},
    )

    assert len(decisions) == 1
    assert "TICK-A" not in broker.positions
