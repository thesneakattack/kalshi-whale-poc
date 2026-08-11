from services import cross_strategy as cs


def _row(ticker, side, won, realized_pnl=10.0):
    return {
        "ticker": ticker, "side": side, "won": won, "realized_pnl": realized_pnl,
        "hold_sec": 100.0, "left_on_table": None, "cost_basis": 50.0,
        "close_type": "settled_win" if won else "settled_loss",
    }


# --- ticker_overlap -----------------------------------------------------

def test_ticker_overlap_empty_when_no_shared_tickers():
    whale_rows = [_row("TICK-A", "yes", True)]
    market_rows = [_row("TICK-B", "yes", True)]
    assert cs.ticker_overlap(whale_rows, market_rows) == []


def test_ticker_overlap_finds_a_shared_ticker():
    whale_rows = [_row("TICK-A", "yes", True, realized_pnl=5.0)]
    market_rows = [_row("TICK-A", "no", False, realized_pnl=-3.0)]
    overlap = cs.ticker_overlap(whale_rows, market_rows)
    assert len(overlap) == 1
    assert overlap[0] == {
        "ticker": "TICK-A",
        "whale_side": "yes", "whale_won": True, "whale_realized_pnl": 5.0,
        "market_side": "no", "market_won": False, "market_realized_pnl": -3.0,
        "agreed": False,
    }


def test_ticker_overlap_agreed_true_when_same_side():
    whale_rows = [_row("TICK-A", "yes", True)]
    market_rows = [_row("TICK-A", "yes", True)]
    overlap = cs.ticker_overlap(whale_rows, market_rows)
    assert overlap[0]["agreed"] is True


def test_ticker_overlap_handles_multiple_whale_trades_on_the_same_ticker():
    # e.g. re-entered after an earlier close - both should pair with the
    # market strategy's single trade on that ticker.
    whale_rows = [_row("TICK-A", "yes", True), _row("TICK-A", "no", False)]
    market_rows = [_row("TICK-A", "yes", True)]
    overlap = cs.ticker_overlap(whale_rows, market_rows)
    assert len(overlap) == 2


def test_ticker_overlap_ignores_tickers_only_one_strategy_traded():
    whale_rows = [_row("TICK-A", "yes", True), _row("TICK-B", "yes", True)]
    market_rows = [_row("TICK-A", "no", False)]
    overlap = cs.ticker_overlap(whale_rows, market_rows)
    assert len(overlap) == 1
    assert overlap[0]["ticker"] == "TICK-A"


# --- aggregate_comparison -------------------------------------------------

def test_aggregate_comparison_returns_both_strategies_summaries():
    whale_rows = [_row("TICK-A", "yes", True), _row("TICK-B", "yes", False, realized_pnl=-5.0)]
    market_rows = [_row("TICK-C", "no", True, realized_pnl=8.0)]
    result = cs.aggregate_comparison(whale_rows, market_rows)
    assert result["whale_follow"]["total_closed"] == 2
    assert result["market_native"]["total_closed"] == 1
    assert result["market_native"]["wins"] == 1


def test_aggregate_comparison_empty_rows_produce_zero_summary():
    result = cs.aggregate_comparison([], [])
    assert result["whale_follow"]["total_closed"] == 0
    assert result["market_native"]["total_closed"] == 0
