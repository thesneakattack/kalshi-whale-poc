import time

import pytest

from services import paper_broker as pb
from services.kalshi_fees import maker_fee, taker_fee


def _broker(tmp_path, monkeypatch, starting_bankroll=1000.0):
    monkeypatch.setattr(pb, "DB_PATH", tmp_path / "paper_broker.db")
    return pb.PaperBroker(starting_bankroll=starting_bankroll)


def test_fresh_broker_uses_starting_bankroll(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    assert broker.bankroll == 1000.0
    assert broker.starting_bankroll == 1000.0
    assert broker.positions == {}
    assert broker.trade_log == []


def test_open_position_deducts_cost_and_logs_trade(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    trade = broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    # Real Kalshi taker fee (services/kalshi_fees.py) is now an additional
    # cash outflow on top of cost - see docs/prediction-market-strategy-
    # alignment-plan.md Part 2.2.
    fee = taker_fee(100, 0.5)
    assert fee > 0
    assert broker.bankroll == pytest.approx(1000.0 - 50.0 - fee)
    assert trade.fee == fee
    assert trade.size == 100
    assert trade.price == 0.5
    assert "TICK-A" in broker.positions
    assert broker.positions["TICK-A"].size == 100
    assert broker.positions["TICK-A"].entry_fee == fee
    assert len(broker.trade_log) == 1


def test_open_position_cost_capped_at_bankroll(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=10.0)
    # size*price = 1000*0.5 = 500, far more than the $10 available - the fill
    # should shrink to fit rather than overdraw the account. The fee is
    # deducted on top of the (already-capped) cost, not folded into the
    # sizing math itself - see paper_broker.py's open_position comment - so
    # a fill landing right at the bankroll cap can now go fractionally
    # negative by exactly the fee amount. Explicitly accepted, not a bug.
    trade = broker.open_position("TICK-A", "yes", size=1000, price=0.5, reason="test")
    fee = taker_fee(trade.size, 0.5)
    assert broker.bankroll == pytest.approx(0.0 - fee)
    assert trade.size == int(10.0 / 0.5)


def test_open_position_no_side_charges_inverted_price(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    # price is always the yes price - a NO contract at yes-price 0.3 really
    # costs (1-0.3)=0.7/contract, not 0.3/contract.
    trade = broker.open_position("TICK-A", "no", size=100, price=0.3, reason="test")
    fee = taker_fee(100, 0.3)
    assert broker.bankroll == pytest.approx(1000.0 - 100 * 0.7 - fee)
    assert trade.size == 100
    assert trade.price == 0.3  # still stored in yes-price terms


def test_open_position_no_side_cost_capped_at_bankroll(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=7.0)
    # unit cost = 1-0.3 = 0.7/contract; size*0.7 = 1000*0.7 = 700, far more
    # than the $7 available - the fill should shrink to fit at the true
    # NO-side unit cost, not the yes-price.
    trade = broker.open_position("TICK-A", "no", size=1000, price=0.3, reason="test")
    fee = taker_fee(trade.size, 0.3)
    assert broker.bankroll == pytest.approx(0.0 - fee)
    assert trade.size == int(7.0 / 0.7)


def test_cost_basis_yes_side(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    assert broker.cost_basis("TICK-A") == 50.0


def test_cost_basis_no_side(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "no", size=100, price=0.3, reason="test")
    assert broker.cost_basis("TICK-A") == 70.0


def test_cost_basis_no_open_position_is_zero(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    assert broker.cost_basis("NOPE") == 0.0


def test_state_positions_include_cost_basis(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "no", size=100, price=0.3, reason="test")
    state = broker.state({})
    assert state["positions"][0]["cost_basis"] == 70.0


def test_can_trade_respects_cooldown(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="test")
    assert broker.can_trade("TICK-A", cooldown_sec=300) is False
    assert broker.can_trade("TICK-B", cooldown_sec=300) is True  # different ticker, never traded


def test_can_trade_true_after_cooldown_elapses(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="test")
    broker.last_trade_time["TICK-A"] = time.time() - 301
    assert broker.can_trade("TICK-A", cooldown_sec=300) is True


def test_mark_to_market_yes_side_profit_and_loss(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    assert broker.mark_to_market("TICK-A", 0.6) == pytest.approx(10.0)
    assert broker.mark_to_market("TICK-A", 0.4) == pytest.approx(-10.0)


def test_mark_to_market_no_side_is_inverted(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.5, reason="test")
    # a "no" position profits when the price falls, not rises
    assert broker.mark_to_market("TICK-A", 0.4) == pytest.approx(10.0)
    assert broker.mark_to_market("TICK-A", 0.6) == pytest.approx(-10.0)


def test_mark_to_market_no_position_is_zero(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    assert broker.mark_to_market("NOPE", 0.5) == 0.0


def test_equity_combines_bankroll_and_total_position_value(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    # bankroll now 950-fee, position now genuinely worth 100*0.6=60 (cost
    # basis 50 + 10 unrealized gain) - equity is bankroll plus that full
    # current value, not just the +10 gain component on its own (audit
    # finding, 2026-08-09: equity() used to omit the cost-basis part
    # entirely, silently under-reporting true portfolio value by however
    # much capital was tied up in open positions - see
    # PaperBroker.total_position_value()'s docstring for the two
    # independent ways this was confirmed).
    fee = taker_fee(100, 0.5)
    assert broker.equity({"TICK-A": 0.6}) == pytest.approx(1010.0 - fee)


def test_state_exposes_unrealized_pnl_as_its_own_field(tmp_path, monkeypatch):
    # Not left for a caller to re-derive as equity - bankroll - that's
    # exactly the re-derivation that broke once already (the header-strip
    # "Unrealized P&L" bug, see CLAUDE.md) and would break again now that
    # equity() is no longer defined as bankroll + this exact number.
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    state = broker.state({"TICK-A": 0.6})
    assert state["unrealized_pnl"] == pytest.approx(10.0)
    assert state["equity"] != pytest.approx(state["bankroll"] + state["unrealized_pnl"])  # the old, broken relationship


def test_equity_is_continuous_across_closing_a_position_at_the_same_price(tmp_path, monkeypatch):
    # The concrete regression this bug caused: closing a position at
    # exactly the price it's already marked at should not itself change
    # total wealth (aside from the fee this close leg incurs) - equity()
    # used to jump by roughly the position's full cost basis at the
    # instant of closing, purely from an accounting gap, not any real P&L.
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    equity_before_close = broker.equity({"TICK-A": 0.6})
    broker.close_position("TICK-A", exit_price=0.6, reason="test")
    close_fee = taker_fee(100, 0.6)
    equity_after_close = broker.equity({})
    assert equity_after_close == pytest.approx(equity_before_close - close_fee)


def test_state_recent_trades_most_recent_first(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="first")
    broker.last_trade_time["TICK-A"] = 0  # bypass cooldown just for this test
    broker.open_position("TICK-B", "yes", size=10, price=0.5, reason="second")
    state = broker.state({})
    assert state["recent_trades"][0]["reason"] == "second"
    assert state["recent_trades"][1]["reason"] == "first"


def test_persistence_across_new_instance(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_broker.db"
    monkeypatch.setattr(pb, "DB_PATH", db_path)

    broker = pb.PaperBroker(starting_bankroll=1000.0)
    trade = broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")

    # Simulate a real restart: a brand-new instance, same DB_PATH, constructed
    # with a *different* starting_bankroll - the persisted state must win
    # over it, the way main.py always re-passes config's value on startup.
    resumed = pb.PaperBroker(starting_bankroll=999999.0)
    assert resumed.bankroll == broker.bankroll
    assert resumed.starting_bankroll == 1000.0
    assert "TICK-A" in resumed.positions
    assert resumed.positions["TICK-A"].size == trade.size
    assert len(resumed.trade_log) == 1
    assert resumed.trade_log[0].id == trade.id
    assert resumed.can_trade("TICK-A", cooldown_sec=300) is False  # cooldown survived too


def test_reset_wipes_state_and_persists_the_wipe(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_broker.db"
    monkeypatch.setattr(pb, "DB_PATH", db_path)

    broker = pb.PaperBroker(starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    broker.reset(starting_bankroll=500.0)

    assert broker.bankroll == 500.0
    assert broker.positions == {}
    assert broker.trade_log == []

    # A fresh instance after reset must not resurrect the wiped position.
    resumed = pb.PaperBroker(starting_bankroll=1.0)
    assert resumed.bankroll == 500.0
    assert resumed.positions == {}
    assert resumed.trade_log == []


def test_close_position_yes_side_realizes_profit(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # bankroll -> 950-entry_fee
    trade = broker.close_position("TICK-A", exit_price=0.75, reason="take-profit")
    assert trade is not None
    assert trade.side == "yes"
    assert trade.size == 100
    assert trade.price == 0.75
    assert "closed:" in trade.reason and "take-profit" in trade.reason
    # cash back = 100*0.75 - close_fee; bankroll = 950-entry_fee + cash_back
    # (net +25 profit before fees, both legs' real fees now included).
    entry_fee = taker_fee(100, 0.5)
    close_fee = taker_fee(100, 0.75)
    assert trade.fee == close_fee
    assert broker.bankroll == pytest.approx(1025.0 - entry_fee - close_fee)
    assert "TICK-A" not in broker.positions
    assert len(broker.trade_log) == 2  # the open, and the close


def test_close_position_yes_side_realizes_loss(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # bankroll -> 950-entry_fee
    broker.close_position("TICK-A", exit_price=0.3, reason="stop-loss")
    # cash back = 100 * 0.3 = 30; bankroll = 950-entry_fee + 30-close_fee (net -20 loss before fees)
    entry_fee = taker_fee(100, 0.5)
    close_fee = taker_fee(100, 0.3)
    assert broker.bankroll == pytest.approx(980.0 - entry_fee - close_fee)


def test_close_position_no_side_uses_inverted_price(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    # NO position entered when yes-price was 0.4, so the NO side really costs
    # (1-0.4)=0.6/contract - open_position charges that, even though
    # entry_price is still stored in yes-price terms (see mark_to_market).
    broker.open_position("TICK-A", "no", size=100, price=0.4, reason="entry")  # cost = 100*0.6 = 60, bankroll -> 940-entry_fee
    broker.close_position("TICK-A", exit_price=0.2, reason="whale sentiment reversed")
    # yes price dropped 0.4 -> 0.2, so the NO side gained: cash back = 100 * (1 - 0.2) = 80
    entry_fee = taker_fee(100, 0.4)
    close_fee = taker_fee(100, 0.2)
    assert broker.bankroll == pytest.approx(1020.0 - entry_fee - close_fee)  # 940 + 80, fee-adjusted


def test_close_position_returns_none_for_no_open_position(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    result = broker.close_position("TICK-NOPE", exit_price=0.5, reason="n/a")
    assert result is None
    assert broker.bankroll == 1000.0


def test_close_position_persists_across_restart(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_broker.db"
    monkeypatch.setattr(pb, "DB_PATH", db_path)
    broker = pb.PaperBroker(starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    broker.close_position("TICK-A", exit_price=0.75, reason="take-profit")

    resumed = pb.PaperBroker(starting_bankroll=999999.0)
    entry_fee = taker_fee(100, 0.5)
    close_fee = taker_fee(100, 0.75)
    assert resumed.bankroll == pytest.approx(1025.0 - entry_fee - close_fee)
    assert "TICK-A" not in resumed.positions
    assert len(resumed.trade_log) == 2


# --- config_fingerprint (docs/advisory-engine-plan.md) ----------------------

def test_open_position_stores_config_fingerprint(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    trade = broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry", config_fingerprint="fp1")
    assert trade.config_fingerprint == "fp1"
    assert broker.positions["TICK-A"].config_fingerprint == "fp1"


def test_open_position_config_fingerprint_defaults_to_none(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    trade = broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    assert trade.config_fingerprint is None


def test_close_position_inherits_entry_fingerprint_even_if_cfg_changed(tmp_path, monkeypatch):
    # A position stays open while the user tweaks config mid-hold - the
    # close trade must still carry the *entry-time* fingerprint, not
    # whatever fingerprint is "current" now, so a round-trip is always
    # attributed to one variant (see config_performance.py's module
    # docstring on this exact limitation).
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry", config_fingerprint="fp-old")
    trade = broker.close_position("TICK-A", exit_price=0.75, reason="take-profit")
    assert trade.config_fingerprint == "fp-old"


def test_config_fingerprint_persists_across_restart(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_broker.db"
    monkeypatch.setattr(pb, "DB_PATH", db_path)
    broker = pb.PaperBroker(starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry", config_fingerprint="fp1")
    broker.close_position("TICK-A", exit_price=0.75, reason="take-profit")

    resumed = pb.PaperBroker(starting_bankroll=999999.0)
    assert [t.config_fingerprint for t in resumed.trade_log] == ["fp1", "fp1"]


def test_migration_adds_config_fingerprint_column_to_pre_existing_db(tmp_path, monkeypatch):
    # data/paper_broker.db is a live file (CLAUDE.md) - config_fingerprint
    # was added to the trades/positions tables after both already had real
    # rows in production, so the migration must work against a db that
    # predates the column entirely, not just a fresh one.
    import sqlite3
    db_path = tmp_path / "paper_broker.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE broker_meta (id INTEGER PRIMARY KEY CHECK (id = 1), bankroll REAL NOT NULL, starting_bankroll REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE positions (ticker TEXT PRIMARY KEY, side TEXT NOT NULL, size INTEGER NOT NULL, "
        "entry_price REAL NOT NULL, opened_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE trades (id TEXT PRIMARY KEY, ticker TEXT NOT NULL, side TEXT NOT NULL, size INTEGER NOT NULL, "
        "price REAL NOT NULL, reason TEXT NOT NULL, timestamp REAL NOT NULL)"
    )
    conn.execute("INSERT INTO broker_meta (id, bankroll, starting_bankroll) VALUES (1, 900.0, 1000.0)")
    conn.execute(
        "INSERT INTO positions (ticker, side, size, entry_price, opened_at) VALUES ('TICK-A', 'yes', 100, 0.5, 123.0)"
    )
    conn.execute(
        "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp) "
        "VALUES ('t1', 'TICK-A', 'yes', 100, 0.5, 'whale print 5000 @ 0.5 (conf 0.7)', 123.0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(pb, "DB_PATH", db_path)
    broker = pb.PaperBroker(starting_bankroll=999999.0)
    # Pre-existing data survived the migration, untouched.
    assert broker.bankroll == 900.0
    assert "TICK-A" in broker.positions
    assert broker.positions["TICK-A"].config_fingerprint is None
    assert broker.trade_log[0].config_fingerprint is None
    # And the broker still works normally afterward.
    broker.open_position("TICK-B", "yes", size=10, price=0.5, reason="entry", config_fingerprint="fp2")
    assert broker.positions["TICK-B"].config_fingerprint == "fp2"


# --- per-instance db_path (services/market_strategy.py's own capital pool) --

def test_explicit_db_path_overrides_module_default(tmp_path, monkeypatch):
    # Module DB_PATH deliberately left pointed at something that would
    # error if ever touched, to prove the explicit db_path argument is what
    # actually gets used - not a fallback that silently still reads it.
    monkeypatch.setattr(pb, "DB_PATH", tmp_path / "should-not-be-used" / "paper_broker.db")
    explicit_path = tmp_path / "explicit" / "market_broker.db"
    broker = pb.PaperBroker(starting_bankroll=500.0, db_path=explicit_path)
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="entry")
    assert explicit_path.exists()
    assert not (tmp_path / "should-not-be-used").exists()


def test_two_broker_instances_with_different_db_paths_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "DB_PATH", tmp_path / "broker_a.db")
    broker_a = pb.PaperBroker(starting_bankroll=1000.0)
    broker_b = pb.PaperBroker(starting_bankroll=5000.0, db_path=tmp_path / "broker_b.db")

    broker_a.open_position("TICK-A", "yes", size=100, price=0.5, reason="a")
    broker_b.open_position("TICK-B", "yes", size=200, price=0.5, reason="b")

    assert broker_a.bankroll == pytest.approx(1000.0 - 50.0 - taker_fee(100, 0.5))
    assert broker_b.bankroll == pytest.approx(5000.0 - 100.0 - taker_fee(200, 0.5))
    assert list(broker_a.positions.keys()) == ["TICK-A"]
    assert list(broker_b.positions.keys()) == ["TICK-B"]

    # Reload both from disk - each must resume its own state, not the other's.
    resumed_a = pb.PaperBroker(starting_bankroll=999999.0)
    resumed_b = pb.PaperBroker(starting_bankroll=999999.0, db_path=tmp_path / "broker_b.db")
    assert resumed_a.bankroll == broker_a.bankroll
    assert list(resumed_a.positions.keys()) == ["TICK-A"]
    assert resumed_b.bankroll == broker_b.bankroll
    assert list(resumed_b.positions.keys()) == ["TICK-B"]


# ---- maker/limit-order path (2026-08-15) ----------------------------------

def test_place_limit_order_does_not_touch_bankroll(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    order = broker.place_limit_order(
        "TICK-A", "yes", size=100, limit_price=0.5, reason="test", expires_at=time.time() + 60,
    )
    assert order is not None
    assert broker.bankroll == 1000.0  # no cash/collateral reserved for a resting order
    assert "TICK-A" in broker.pending_orders
    assert broker.positions == {}
    assert broker.trade_log == []


def test_place_limit_order_refuses_a_second_order_on_the_same_ticker(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    first = broker.place_limit_order("TICK-A", "yes", 100, 0.5, "first", time.time() + 60)
    second = broker.place_limit_order("TICK-A", "yes", 50, 0.4, "second", time.time() + 60)
    assert first is not None
    assert second is None
    assert broker.pending_orders["TICK-A"].reason == "first"  # unchanged, not replaced


def test_check_pending_fills_yes_side_fills_when_ask_reaches_limit(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=time.time() + 60)
    # Ask still above the limit - order shouldn't fill yet.
    fills = broker.check_pending_fills(latest_bids={"TICK-A": 0.48}, latest_asks={"TICK-A": 0.52})
    assert fills == []
    assert "TICK-A" in broker.pending_orders
    # Ask has come down to the limit - fills now, at the real (maker) fee.
    fills = broker.check_pending_fills(latest_bids={"TICK-A": 0.49}, latest_asks={"TICK-A": 0.50})
    assert len(fills) == 1
    assert "TICK-A" not in broker.pending_orders
    assert "TICK-A" in broker.positions
    fee = maker_fee(100, 0.5)
    assert broker.bankroll == pytest.approx(1000.0 - 50.0 - fee)
    assert broker.positions["TICK-A"].entry_fee == fee
    assert fills[0]["trade"]["fee"] == fee


def test_check_pending_fills_fills_at_the_real_better_price_not_the_limit(tmp_path, monkeypatch):
    # A resting buy limit at 0.5 asks for "at most 0.5" - if the real ask is
    # already better (lower) than that when checked, it should fill at the
    # real, better price, same as a genuine resting limit order would.
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=time.time() + 60)
    fills = broker.check_pending_fills(latest_bids={"TICK-A": 0.40}, latest_asks={"TICK-A": 0.42})
    assert len(fills) == 1
    assert broker.positions["TICK-A"].entry_price == 0.42
    assert fills[0]["trade"]["price"] == 0.42


def test_check_pending_fills_no_side_uses_inverted_price(tmp_path, monkeypatch):
    # A "no" limit buy at limit_price=0.3 (yes-denominated) wants a no-side
    # unit cost of at most 0.7 - fills once the real yes-bid has risen to
    # (or above) 0.3, i.e. (1 - bid) <= 0.7.
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.place_limit_order("TICK-A", "no", size=100, limit_price=0.3, reason="r", expires_at=time.time() + 60)
    fills = broker.check_pending_fills(latest_bids={"TICK-A": 0.20}, latest_asks={"TICK-A": 0.22})
    assert fills == []  # 1 - 0.20 = 0.80 > 0.70, hasn't come to the order yet
    fills = broker.check_pending_fills(latest_bids={"TICK-A": 0.30}, latest_asks={"TICK-A": 0.32})
    assert len(fills) == 1
    assert broker.positions["TICK-A"].side == "no"
    assert broker.positions["TICK-A"].entry_price == pytest.approx(0.30)


def test_check_pending_fills_expires_unfilled_without_charging_anything(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.3, reason="r", expires_at=time.time() + 30)
    # Market never comes to the order's price, and time passes the expiry.
    fills = broker.check_pending_fills(
        latest_bids={"TICK-A": 0.48}, latest_asks={"TICK-A": 0.52}, now=time.time() + 31,
    )
    assert fills == []
    assert broker.pending_orders == {}
    assert broker.positions == {}
    assert broker.bankroll == 1000.0  # nothing ever charged for an unfilled, expired order


def test_check_pending_fills_leaves_order_pending_without_a_fresh_quote(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=time.time() + 60)
    fills = broker.check_pending_fills(latest_bids={}, latest_asks={})  # ticker not in this tick's quotes at all
    assert fills == []
    assert "TICK-A" in broker.pending_orders  # not guessed at, not dropped either


def test_pending_orders_persist_across_restart(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=1234567890.0)
    resumed = pb.PaperBroker(starting_bankroll=1000.0)
    assert "TICK-A" in resumed.pending_orders
    assert resumed.pending_orders["TICK-A"].limit_price == 0.5
    assert resumed.pending_orders["TICK-A"].expires_at == 1234567890.0


def test_reset_clears_pending_orders(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=time.time() + 60)
    broker.reset(starting_bankroll=500.0)
    assert broker.pending_orders == {}
    resumed = pb.PaperBroker(starting_bankroll=500.0)
    assert resumed.pending_orders == {}


def test_state_includes_pending_orders(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=time.time() + 60)
    state = broker.state(latest_prices={})
    assert len(state["pending_orders"]) == 1
    assert state["pending_orders"][0]["ticker"] == "TICK-A"
