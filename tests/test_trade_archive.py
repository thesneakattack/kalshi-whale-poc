"""services/reset/trade_archive.py - reset without losing the evidence.

Motivating incident, 2026-08-17: measuring performance against the ~70%
target found data/paper_broker.db reaching back only to 08/16 19:28 because
a reset had wiped it, so every trade-level question about anything earlier
was unanswerable. reset_log recorded that a reset happened; nothing
recorded what it destroyed.
"""
import sqlite3

import pytest

from services.reset import trade_archive as ta


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from services import paper_broker as pb_module

    monkeypatch.setattr(ta, "DB_PATH", tmp_path / "trade_archive.db")
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    yield


def _seed_broker(trades, bankroll=9000.0, starting=10000.0, positions=()):
    from services import paper_broker as pb_module

    pb_module.PaperBroker(starting)  # creates the schema
    with sqlite3.connect(pb_module.DB_PATH) as conn:
        # Each call stands for a fresh era, so the previous one's rows go -
        # otherwise a second seed archives the union of both and the epochs
        # stop being independent.
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM broker_meta")
        conn.execute("INSERT INTO broker_meta (id, bankroll, starting_bankroll) VALUES (1,?,?)",
                     (bankroll, starting))
        for t in trades:
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, "
                "config_fingerprint, fee, signal_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)", t)
        for p in positions:
            conn.execute(
                "INSERT INTO positions (ticker, side, size, entry_price, opened_at, "
                "config_fingerprint, entry_fee) VALUES (?,?,?,?,?,?,?)", p)


_TRADES = [
    ("e1", "KXBTC15M-A", "yes", 100, 0.70, "whale print (conf 0.80)", 1000.0, "fp", 1.0, 999.0),
    ("x1", "KXBTC15M-A", "yes", 100, 1.00, "closed: market settled yes - position won (realized +29.00)",
     1100.0, "fp", 0.0, None),
    ("e2", "KXBTC15M-B", "no", 100, 0.30, "whale print (conf 0.80)", 1200.0, "fp", 1.0, 1199.0),
    ("x2", "KXBTC15M-B", "no", 100, 0.00, "closed: stop-loss hit at 0.00 (realized -70.00)",
     1300.0, "fp", 0.0, None),
]


def test_archive_captures_every_trade_and_position_before_a_reset():
    _seed_broker(_TRADES, positions=[("KXETH15M-C", "yes", 50, 0.6, 1400.0, "fp", 0.5)])
    result = ta.archive_epoch("pre-reset test", reason="unit test")

    assert result["ok"] is True
    assert result["trades_archived"] == 4
    assert result["positions_archived"] == 1
    rows = ta.epoch_trades(result["epoch_id"])
    assert len(rows) == 4
    # series is derived at archive time so archived rows stay queryable by
    # series without re-deriving it from the ticker later.
    assert {r["series"] for r in rows} == {"KXBTC15M"}


def test_archive_pairs_win_rate_with_the_entry_price_that_makes_it_mean_something():
    """CLAUDE.md's hard commandment: a win rate is meaningless without the
    mean unit cost beside it. One win at 0.70 and one loss at 0.70 is a 50%
    win rate against a 71.47% fee-inclusive breakeven - stored as a pair so
    the two can never drift apart."""
    _seed_broker(_TRADES)
    r = ta.archive_epoch("pairing test")

    assert r["closed_positions"] == 2
    assert r["wins"] == 1
    assert r["win_rate_pct"] == 50.0
    # Both entries cost $0.70/contract: the yes at price 0.70, and the no at
    # yes-price 0.30 (side-aware inversion, not 0.30).
    assert r["mean_entry_unit_cost"] == pytest.approx(0.70)
    # Breakeven is the entry price PLUS the taker fee that fill really pays
    # (issue #205): 0.70 + 0.07*0.70*0.30 = 0.7147. The fee-free 70.0 this
    # used to assert pinned the wrong bar.
    assert r["breakeven_accuracy_pct"] == 71.47
    assert r["edge_pts"] == -21.5
    assert r["realised_pnl"] == pytest.approx(-41.0)


def test_archive_survives_a_reset_of_the_broker_it_copied_from():
    """The actual point: wipe paper_broker afterwards and the record is
    still here."""
    from services import paper_broker as pb_module

    _seed_broker(_TRADES)
    epoch = ta.archive_epoch("before wipe")["epoch_id"]

    pb_module.PaperBroker(10000.0).reset(10000.0)
    with sqlite3.connect(pb_module.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert len(ta.epoch_trades(epoch)) == 4


def test_compare_ranks_by_edge_not_by_win_rate():
    """A 96% win rate at a 98% breakeven loses money; a 60% win rate at a
    55% breakeven makes it. Ranking by win rate would pick the wrong one,
    which is the exact trap the 2026-08-17 measurement found."""
    _seed_broker([
        ("e1", "KXA-1", "yes", 100, 0.98, "whale print", 1000.0, "fp", 0.0, None),
        ("x1", "KXA-1", "yes", 100, 1.00, "closed: market settled yes - position won (realized +2.00)",
         1100.0, "fp", 0.0, None),
    ])
    ta.archive_epoch("high win rate, thin edge")

    _seed_broker([
        ("e2", "KXB-1", "yes", 100, 0.55, "whale print", 2000.0, "fp", 0.0, None),
        ("x2", "KXB-1", "yes", 100, 1.00, "closed: market settled yes - position won (realized +45.00)",
         2100.0, "fp", 0.0, None),
    ])
    ta.archive_epoch("lower win rate, real edge")

    out = ta.compare()
    assert len(out["epochs"]) == 2
    # Both are 100% win rate here; edge is what separates them.
    assert out["best"]["label"] == "lower win rate, real edge"
    # 100% win rate against a 0.55 + 0.07*0.55*0.45 = 56.73% breakeven.
    assert out["best"]["edge_pts"] == pytest.approx(43.3)


def test_epochs_puts_pre_fee_rows_on_the_current_breakeven_convention():
    """An epoch row written before issue #205 stored a fee-free breakeven.
    compare() ranks by edge_pts with max(), which reads numbers and not the
    prose caveat beside them, so a legacy row would win on a bar 1.68pts
    too low. epochs() recomputes both from the epoch's own archived trades
    - which are complete and immutable - so one convention is ranked."""
    _seed_broker([
        ("e1", "KXA-1", "yes", 100, 0.60, "whale print", 1000.0, "fp", 0.0, None),
        ("x1", "KXA-1", "yes", 100, 1.00, "closed: market settled yes - position won (realized +40.00)",
         1100.0, "fp", 0.0, None),
    ])
    epoch_id = ta.archive_epoch("legacy row")["epoch_id"]
    # Rewrite the stored pair the way the pre-fix code wrote it: fee-free.
    with sqlite3.connect(ta.DB_PATH) as conn:
        conn.execute("UPDATE epochs SET breakeven_accuracy_pct = 60.0, edge_pts = 40.0 "
                     "WHERE id = ?", (epoch_id,))

    row = ta.epochs()[0]
    # 0.60 + 0.07*0.60*0.40 = 0.6168, and 100.0 - 61.68 = 38.32 -> 38.3.
    assert row["breakeven_accuracy_pct"] == 61.68
    assert row["edge_pts"] == pytest.approx(38.3)
    assert ta.compare()["best"]["edge_pts"] == pytest.approx(38.3)


def test_compare_says_so_when_nothing_is_archived():
    out = ta.compare()
    assert out["epochs"] == []
    assert "nothing archived yet" in out["note"]


def test_archive_reports_an_unreadable_broker_rather_than_a_fake_epoch():
    from services import paper_broker as pb_module

    pb_module.DB_PATH.parent.mkdir(exist_ok=True)
    pb_module.DB_PATH.write_text("this is not a sqlite database")
    result = ta.archive_epoch("broken")
    assert result["ok"] is False
    assert "unreadable" in result["error"]
