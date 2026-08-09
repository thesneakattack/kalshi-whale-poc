from services import trade_analytics as ta

# Reason strings mirror exactly what services/paper_broker.py and
# services/strategy_engine.py actually produce (see their docstrings) -
# these tests are the contract that the classifier/parser keeps matching
# those formats.


def _open(ticker="TICK-A", side="yes", size=100, price=0.5, conf=0.7, ts=1000.0, tid="o1"):
    return {
        "id": tid, "ticker": ticker, "side": side, "size": size, "price": price,
        "reason": f"whale print 5000 @ {price} (conf {conf})", "timestamp": ts,
    }


def _closed(ticker="TICK-A", side="yes", size=100, price=0.75, inner="take-profit hit: unrealized gain 50% of cost basis (target 50%)",
            realized=25.0, ts=2000.0, tid="c1"):
    return {
        "id": tid, "ticker": ticker, "side": side, "size": size, "price": price,
        "reason": f"closed: {inner} (realized {realized:+.2f})", "timestamp": ts,
    }


# ---- classify_close_type ---------------------------------------------------

def test_classify_close_type_recognizes_every_known_prefix():
    cases = {
        "closed: take-profit hit: unrealized gain 50% of cost basis (target 50%) (realized +25.00)": "take_profit",
        "closed: stop-loss hit: unrealized loss 40% of cost basis (limit 30%) (realized -20.00)": "stop_loss",
        "closed: whale sentiment reversed: 78% of 5 recent prints now lean against this yes position (realized +5.00)": "sentiment_reversal",
        "closed: auto-exit: composite confidence 65% >= 60% threshold (factors: pnl=60%, sentiment=100%) (realized +10.00)": "auto_exit",
        "closed: market settled YES - position won (realized +50.00)": "settled_win",
        "closed: market settled NO - position lost (realized -50.00)": "settled_loss",
    }
    for reason, expected in cases.items():
        assert ta.classify_close_type(reason) == expected


def test_classify_close_type_returns_none_for_open_or_unknown_reasons():
    assert ta.classify_close_type("whale print 5000 @ 0.5 (conf 0.7)") is None
    assert ta.classify_close_type("closed: something we've never written before (realized +1.00)") is None


# ---- build_trade_history ----------------------------------------------------

def test_build_trade_history_pairs_entry_and_close():
    log = [_open(conf=0.82, ts=1000.0), _closed(ts=1300.0)]
    rows = ta.build_trade_history(log)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "TICK-A"
    assert r["entry_price"] == 0.5
    assert r["exit_price"] == 0.75
    assert r["hold_sec"] == 300.0
    assert r["close_type"] == "take_profit"
    assert r["realized_pnl"] == 25.0
    assert r["won"] is True
    assert r["entry_confidence"] == 0.82
    assert r["cost_basis"] == 50.0  # 100 * 0.5
    assert r["cash_back"] == 75.0  # 100 * 0.75


def test_build_trade_history_defaults_fees_paid_to_zero_without_fee_data():
    # _open/_closed (this file's own helpers) don't carry a "fee" key at
    # all - the same shape a trade logged before fee modeling existed
    # would have. "no data" and "zero fee" look identical here on purpose
    # (see build_trade_history's comment) - the honest default is 0.0, not
    # a crash or a None that'd need special-casing downstream.
    log = [_open(ts=1000.0), _closed(ts=1300.0)]
    rows = ta.build_trade_history(log)
    assert rows[0]["fees_paid"] == 0.0


def test_build_trade_history_sums_entry_and_close_fees():
    entry = _open(ts=1000.0)
    entry["fee"] = 1.75
    close = _closed(ts=1300.0)
    close["fee"] = 1.31
    rows = ta.build_trade_history([entry, close])
    assert rows[0]["fees_paid"] == 3.06


def test_build_trade_history_dollar_amounts_account_for_no_side():
    log = [
        _open(ticker="TICK-A", side="no", price=0.4, size=100, ts=1000.0),
        _closed(ticker="TICK-A", side="no", price=0.2, size=100, ts=1100.0,
                inner="whale sentiment reversed: 80% of 4 recent prints now lean against this no position", realized=20.0),
    ]
    rows = ta.build_trade_history(log)
    assert rows[0]["cost_basis"] == 60.0  # 100 * (1 - 0.4)
    assert rows[0]["cash_back"] == 80.0  # 100 * (1 - 0.2)


def test_build_trade_history_cost_basis_is_none_without_a_paired_entry():
    log = [_closed(ts=1100.0)]  # a close with no preceding open in this log
    rows = ta.build_trade_history(log)
    assert rows[0]["entry_price"] is None
    assert rows[0]["cost_basis"] is None
    assert rows[0]["cash_back"] is not None  # still computable from the close trade alone


def test_build_trade_history_ignores_still_open_positions():
    log = [_open()]
    assert ta.build_trade_history(log) == []


def test_build_trade_history_pairs_each_close_with_its_own_reentry():
    log = [
        _open(ts=1000.0, tid="o1", price=0.5),
        _closed(ts=1100.0, tid="c1", price=0.6, inner="take-profit hit: unrealized gain 20% of cost basis (target 20%)", realized=10.0),
        _open(ts=1200.0, tid="o2", price=0.4),
        _closed(ts=1250.0, tid="c2", price=0.2, inner="stop-loss hit: unrealized loss 50% of cost basis (limit 50%)", realized=-20.0),
    ]
    rows = ta.build_trade_history(log)
    assert len(rows) == 2
    assert rows[0]["entry_price"] == 0.5 and rows[0]["exit_price"] == 0.6
    assert rows[1]["entry_price"] == 0.4 and rows[1]["exit_price"] == 0.2
    assert rows[1]["close_type"] == "stop_loss"
    assert rows[1]["won"] is False


def test_build_trade_history_left_on_table_only_for_early_profitable_exits():
    log = [
        _open(ticker="TP", ts=1000.0, tid="o1"),
        _closed(ticker="TP", ts=1100.0, tid="c1", price=0.75, inner="take-profit hit: unrealized gain 50% of cost basis (target 50%)", realized=25.0),
        _open(ticker="SL", ts=1000.0, tid="o2"),
        _closed(ticker="SL", ts=1100.0, tid="c2", price=0.3, inner="stop-loss hit: unrealized loss 40% of cost basis (limit 30%)", realized=-20.0),
        _open(ticker="SW", ts=1000.0, tid="o3"),
        _closed(ticker="SW", ts=1100.0, tid="c3", price=1.0, inner="market settled YES - position won", realized=50.0),
    ]
    rows = {r["ticker"]: r for r in ta.build_trade_history(log)}
    assert rows["TP"]["left_on_table"] == 25.0  # 100 * (1 - 0.75)
    assert rows["SL"]["left_on_table"] is None  # a loss, not a profit-take
    assert rows["SW"]["left_on_table"] is None  # settlement, not an early exit


def test_build_trade_history_left_on_table_accounts_for_no_side():
    log = [
        _open(ticker="TICK-A", side="no", price=0.4, ts=1000.0),
        _closed(ticker="TICK-A", side="no", price=0.2, ts=1100.0,
                inner="take-profit hit: unrealized gain 50% of cost basis (target 50%)", realized=20.0),
    ]
    rows = ta.build_trade_history(log)
    # NO side: cash back = size*(1-price) = 100*0.8 = 80; full win = 100;
    # left on table = 100 - 80 = 20 = size * exit_price.
    assert rows[0]["left_on_table"] == 20.0


# ---- compute_summary --------------------------------------------------------

def test_compute_summary_handles_empty_rows():
    summary = ta.compute_summary([])
    assert summary["total_closed"] == 0
    assert summary["win_rate_pct"] is None
    assert summary["total_realized_pnl"] == 0.0
    assert summary["total_capital_deployed"] == 0.0
    assert summary["by_close_type"] == {}


def test_compute_summary_totals_capital_deployed():
    log = [
        _open(ticker="A", side="yes", price=0.5, size=100, ts=1000.0, tid="o1"),  # cost 50
        _closed(ticker="A", ts=1100.0, tid="c1", inner="take-profit hit: unrealized gain 50% of cost basis (target 50%)", realized=25.0),
        _open(ticker="B", side="no", price=0.4, size=100, ts=1000.0, tid="o2"),  # cost 60
        _closed(ticker="B", ts=1100.0, tid="c2", inner="stop-loss hit: unrealized loss 30% of cost basis (limit 30%)", realized=-15.0),
    ]
    rows = ta.build_trade_history(log)
    summary = ta.compute_summary(rows)
    assert summary["total_capital_deployed"] == 110.0


def test_compute_summary_totals_fees_paid():
    entry = _open(ticker="A", ts=1000.0, tid="o1")
    entry["fee"] = 1.75
    close = _closed(ticker="A", ts=1100.0, tid="c1")
    close["fee"] = 1.31
    rows = ta.build_trade_history([entry, close])
    summary = ta.compute_summary(rows)
    assert summary["total_fees_paid"] == 3.06


def test_compute_summary_aggregates_wins_losses_and_by_close_type():
    log = [
        _open(ticker="A", ts=1000.0, tid="o1"),
        _closed(ticker="A", ts=1100.0, tid="c1", inner="take-profit hit: unrealized gain 50% of cost basis (target 50%)", realized=25.0),
        _open(ticker="B", ts=1000.0, tid="o2"),
        _closed(ticker="B", ts=1100.0, tid="c2", inner="stop-loss hit: unrealized loss 30% of cost basis (limit 30%)", realized=-15.0),
        _open(ticker="C", ts=1000.0, tid="o3"),
        _closed(ticker="C", ts=1100.0, tid="c3", inner="take-profit hit: unrealized gain 60% of cost basis (target 50%)", realized=30.0),
    ]
    rows = ta.build_trade_history(log)
    summary = ta.compute_summary(rows)
    assert summary["total_closed"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["win_rate_pct"] == round(2 / 3 * 100, 1)
    assert summary["total_realized_pnl"] == 40.0
    assert summary["by_close_type"]["take_profit"]["count"] == 2
    assert summary["by_close_type"]["take_profit"]["avg_pnl"] == 27.5
    assert summary["by_close_type"]["stop_loss"]["count"] == 1


# ---- compute_insights --------------------------------------------------------

def _rows_with_confidence_split(n_low, low_win, n_high, high_win):
    """n_low trades entered at low confidence (win the first `low_win` of
    them), n_high at high confidence (win the first `high_win`)."""
    log = []
    for i in range(n_low):
        t = f"LOW{i}"
        log.append(_open(ticker=t, conf=0.3, ts=1000.0, tid=f"lo{i}"))
        realized = 10.0 if i < low_win else -10.0
        inner = "take-profit hit: unrealized gain 50% of cost basis (target 50%)" if realized > 0 else "stop-loss hit: unrealized loss 30% of cost basis (limit 30%)"
        log.append(_closed(ticker=t, ts=1100.0, tid=f"lc{i}", inner=inner, realized=realized))
    for i in range(n_high):
        t = f"HIGH{i}"
        log.append(_open(ticker=t, conf=0.9, ts=1000.0, tid=f"ho{i}"))
        realized = 10.0 if i < high_win else -10.0
        inner = "take-profit hit: unrealized gain 50% of cost basis (target 50%)" if realized > 0 else "stop-loss hit: unrealized loss 30% of cost basis (limit 30%)"
        log.append(_closed(ticker=t, ts=1100.0, tid=f"hc{i}", inner=inner, realized=realized))
    return ta.build_trade_history(log)


def test_compute_insights_entry_threshold_hint_fires_on_meaningful_win_rate_gap():
    rows = _rows_with_confidence_split(n_low=4, low_win=0, n_high=4, high_win=4)  # 0% vs 100%
    insights = ta.compute_insights(rows)
    topics = [i["topic"] for i in insights]
    assert "entry_threshold" in topics


def test_compute_insights_no_entry_threshold_hint_when_gap_is_small():
    rows = _rows_with_confidence_split(n_low=4, low_win=2, n_high=4, high_win=2)  # 50% vs 50%
    insights = ta.compute_insights(rows)
    topics = [i["topic"] for i in insights]
    assert "entry_threshold" not in topics


def test_compute_insights_respects_minimum_sample_size_for_close_type_hints():
    # Only 2 stop_loss trades - below the minimum of 3, should not fire.
    log = [
        _open(ticker="A", ts=1000.0, tid="o1"),
        _closed(ticker="A", ts=1100.0, tid="c1", inner="stop-loss hit: unrealized loss 30% of cost basis (limit 30%)", realized=-15.0),
        _open(ticker="B", ts=1000.0, tid="o2"),
        _closed(ticker="B", ts=1100.0, tid="c2", inner="stop-loss hit: unrealized loss 30% of cost basis (limit 30%)", realized=-15.0),
    ]
    rows = ta.build_trade_history(log)
    insights = ta.compute_insights(rows)
    assert not any(i["topic"] == "stop_loss_pct" for i in insights)


def test_compute_insights_fires_stop_loss_hint_once_minimum_sample_reached():
    log = []
    for i in range(3):
        t = f"T{i}"
        log.append(_open(ticker=t, ts=1000.0, tid=f"o{i}"))
        log.append(_closed(ticker=t, ts=1100.0, tid=f"c{i}", inner="stop-loss hit: unrealized loss 30% of cost basis (limit 30%)", realized=-15.0))
    rows = ta.build_trade_history(log)
    insights = ta.compute_insights(rows)
    hit = next(i for i in insights if i["topic"] == "stop_loss_pct")
    assert hit["n"] == 3
    assert hit["confidence"] == "low"  # n=3 < 5


def test_compute_insights_confidence_label_scales_with_sample_size():
    log = []
    for i in range(15):
        t = f"T{i}"
        log.append(_open(ticker=t, ts=1000.0, tid=f"o{i}"))
        log.append(_closed(ticker=t, ts=1100.0, tid=f"c{i}", inner="take-profit hit: unrealized gain 50% of cost basis (target 50%)", realized=10.0))
    rows = ta.build_trade_history(log)
    insights = ta.compute_insights(rows)
    hit = next(i for i in insights if i["topic"] == "take_profit_pct")
    assert hit["n"] == 15
    assert hit["confidence"] == "higher"


def test_compute_insights_never_mutates_input_rows():
    rows = _rows_with_confidence_split(n_low=4, low_win=0, n_high=4, high_win=4)
    snapshot = [dict(r) for r in rows]
    ta.compute_insights(rows)
    assert rows == snapshot


# sentiment_reversal/momentum_reversal - direct report: "the config tunings
# hints section... doesn't seem to give me actual advice at all." Confirmed
# live against real trade history: sentiment_reversal was 81% of all real
# closed trades, but had no heuristic here at all before this, unlike
# stop_loss/take_profit/auto_exit above - every other insight happened to
# need a close type or confidence spread this app's real data didn't
# produce, so the panel was correctly silent, just silent on the one close
# type that actually mattered.

def test_compute_insights_respects_minimum_sample_size_for_sentiment_reversal():
    log = [
        _open(ticker="A", ts=1000.0, tid="o1"),
        _closed(ticker="A", ts=1100.0, tid="c1", inner="whale sentiment reversed: 70% of 5 recent prints now lean against this yes position", realized=-5.0),
        _open(ticker="B", ts=1000.0, tid="o2"),
        _closed(ticker="B", ts=1100.0, tid="c2", inner="whale sentiment reversed: 70% of 5 recent prints now lean against this yes position", realized=-5.0),
    ]
    rows = ta.build_trade_history(log)
    insights = ta.compute_insights(rows)
    assert not any(i["topic"] == "exit_sentiment_lean_pct" for i in insights)


def test_compute_insights_fires_sentiment_reversal_hint_once_minimum_sample_reached():
    log = []
    for i in range(3):
        t = f"T{i}"
        log.append(_open(ticker=t, ts=1000.0, tid=f"o{i}"))
        log.append(_closed(ticker=t, ts=1100.0, tid=f"c{i}", inner="whale sentiment reversed: 70% of 5 recent prints now lean against this yes position", realized=-5.0))
    rows = ta.build_trade_history(log)
    insights = ta.compute_insights(rows)
    hit = next(i for i in insights if i["topic"] == "exit_sentiment_lean_pct")
    assert hit["n"] == 3
    assert "raising exit_sentiment_lean_pct" in hit["text"]  # avg_pnl <= 0 -> "reversing out on noise" framing


def test_compute_insights_sentiment_reversal_hint_flips_advice_when_net_positive():
    log = []
    for i in range(3):
        t = f"T{i}"
        log.append(_open(ticker=t, ts=1000.0, tid=f"o{i}"))
        log.append(_closed(ticker=t, ts=1100.0, tid=f"c{i}", inner="whale sentiment reversed: 70% of 5 recent prints now lean against this yes position", realized=5.0))
    rows = ta.build_trade_history(log)
    insights = ta.compute_insights(rows)
    hit = next(i for i in insights if i["topic"] == "exit_sentiment_lean_pct")
    assert "lowering exit_sentiment_lean_pct" in hit["text"]


def test_compute_insights_fires_momentum_reversal_hint_once_minimum_sample_reached():
    log = []
    for i in range(3):
        t = f"T{i}"
        log.append(_open(ticker=t, ts=1000.0, tid=f"o{i}"))
        log.append(_closed(ticker=t, ts=1100.0, tid=f"c{i}", inner="momentum reversed: price moved 4% against this yes position over 30m", realized=-3.0))
    rows = ta.build_trade_history(log)
    insights = ta.compute_insights(rows)
    hit = next(i for i in insights if i["topic"] == "min_momentum_delta")
    assert hit["n"] == 3
    assert "raising min_momentum_delta" in hit["text"]
