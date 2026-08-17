"""
Permanent, append-only archive of paper-trading history, so a reset stops
costing anything.

Direct request (2026-08-17): "it would be immensely useful for me to do a
safe 'reset' of the paper trading mechanic while maintaining a log of
important data."

The problem is concrete and was hit the same day: an attempt to measure
performance against the ~70% target found `data/paper_broker.db` reaching
back only to 08/16 19:28, because a reset had wiped it. `signal_log`
survived and could still answer signal-level questions, but every
trade-level question - realised win rate, mean entry unit cost, exit
breakdown, P&L by category - was simply gone for everything before that
line. `services/reset_log.py` recorded that a reset *happened*; nothing
recorded what it destroyed.

This module closes that: before a paper reset, every trade and open
position is copied here under an "epoch" id, along with the summary
metrics that make epochs comparable. Resetting then becomes free - the
bankroll starts clean, the evidence does not disappear - which is what
makes it safe to reset often enough to actually run experiments.

CLAUDE.md is explicit that accumulated history in `data/*.db` is a
first-class asset. This is the mechanism that lets a reset honour that
rather than violate it.

APPEND-ONLY BY CONSTRUCTION: nothing in here deletes or updates. There is
no prune, no retention window, no clear_all - deliberately, because the
whole point is being the one store a reset cannot touch.
"""
import sqlite3
import time
from pathlib import Path

from services import signal_log, trade_analytics
from services import paper_broker as pb_module

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_archive.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS epochs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            reason TEXT,
            archived_at REAL NOT NULL,
            first_trade_at REAL,
            last_trade_at REAL,
            starting_bankroll REAL,
            ending_bankroll REAL,
            trades_archived INTEGER NOT NULL DEFAULT 0,
            positions_archived INTEGER NOT NULL DEFAULT 0,
            closed_positions INTEGER,
            wins INTEGER,
            win_rate_pct REAL,
            mean_entry_unit_cost REAL,
            breakeven_accuracy_pct REAL,
            edge_pts REAL,
            realised_pnl REAL,
            fees_paid REAL,
            config_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archived_trades (
            epoch_id INTEGER NOT NULL,
            id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            series TEXT,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            price REAL NOT NULL,
            reason TEXT NOT NULL,
            timestamp REAL NOT NULL,
            config_fingerprint TEXT,
            fee REAL,
            signal_seen_at REAL,
            PRIMARY KEY (epoch_id, id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_arch_trades_epoch ON archived_trades (epoch_id, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_arch_trades_series ON archived_trades (series, timestamp)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archived_positions (
            epoch_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            opened_at REAL NOT NULL,
            config_fingerprint TEXT,
            entry_fee REAL,
            PRIMARY KEY (epoch_id, ticker)
        )
        """
    )
    return conn


def _unit_cost(side: str, yes_price: float | None) -> float | None:
    """Side-aware, same inversion PaperBroker.cost_basis uses. A "no"
    position's real per-contract cost is (1 - price), and re-deriving it as
    `price` is the exact bug that inflated an entire era's P&L by a median
    factor of 49 (CLAUDE.md's hard commandment section)."""
    if yes_price is None:
        return None
    return yes_price if side == "yes" else 1.0 - yes_price


def _summarise(trade_rows: list[dict]) -> dict:
    """The metrics that make two epochs comparable — deliberately the same
    pairing CLAUDE.md's hard commandment requires: a win rate is never
    stored without the mean entry unit cost that makes it interpretable."""
    history = trade_analytics.build_trade_history(trade_rows)
    entries = [t for t in trade_rows if not t["reason"].startswith("closed:")]
    costs = [c for c in (_unit_cost(t["side"], t["price"]) for t in entries) if c is not None]
    mean_cost = sum(costs) / len(costs) if costs else None
    wins = sum(1 for r in history if r["won"])
    win_rate = round(100.0 * wins / len(history), 1) if history else None
    breakeven = round(mean_cost * 100, 1) if mean_cost is not None else None
    return {
        "closed_positions": len(history),
        "wins": wins,
        "win_rate_pct": win_rate,
        "mean_entry_unit_cost": round(mean_cost, 4) if mean_cost is not None else None,
        "breakeven_accuracy_pct": breakeven,
        "edge_pts": (round(win_rate - breakeven, 1)
                     if win_rate is not None and breakeven is not None else None),
        "realised_pnl": round(sum(r["realized_pnl"] or 0.0 for r in history), 2),
        "fees_paid": round(sum(r.get("fees_paid") or 0.0 for r in history), 2),
    }


def archive_epoch(label: str, reason: str | None = None, cfg: dict | None = None,
                  now: float | None = None) -> dict:
    """Copy the entire current paper-trading state into a new epoch and
    return its summary. Does NOT reset anything - archiving and resetting
    are separate steps on purpose, so this can also be called to take a
    checkpoint mid-run without disturbing the account.

    Reads paper_broker.db directly rather than through a live PaperBroker
    instance, so it captures what is actually on disk (the thing a reset is
    about to destroy) rather than one process's in-memory view of it."""
    import json

    now = now if now is not None else time.time()
    try:
        with sqlite3.connect(pb_module.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            trades = [dict(r) for r in conn.execute(
                "SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, "
                "fee, signal_seen_at FROM trades ORDER BY timestamp")]
            positions = [dict(r) for r in conn.execute(
                "SELECT ticker, side, size, entry_price, opened_at, config_fingerprint, entry_fee "
                "FROM positions")]
            meta = conn.execute(
                "SELECT bankroll, starting_bankroll FROM broker_meta WHERE id = 1").fetchone()
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"paper_broker unreadable: {exc}"}

    summary = _summarise(trades)
    with _connect() as arch:
        cur = arch.execute(
            "INSERT INTO epochs (label, reason, archived_at, first_trade_at, last_trade_at, "
            "starting_bankroll, ending_bankroll, trades_archived, positions_archived, "
            "closed_positions, wins, win_rate_pct, mean_entry_unit_cost, breakeven_accuracy_pct, "
            "edge_pts, realised_pnl, fees_paid, config_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                label, reason, now,
                trades[0]["timestamp"] if trades else None,
                trades[-1]["timestamp"] if trades else None,
                meta["starting_bankroll"] if meta else None,
                meta["bankroll"] if meta else None,
                len(trades), len(positions),
                summary["closed_positions"], summary["wins"], summary["win_rate_pct"],
                summary["mean_entry_unit_cost"], summary["breakeven_accuracy_pct"],
                summary["edge_pts"], summary["realised_pnl"], summary["fees_paid"],
                json.dumps(cfg.get("strategy")) if cfg else None,
            ),
        )
        epoch_id = cur.lastrowid
        arch.executemany(
            "INSERT OR IGNORE INTO archived_trades (epoch_id, id, ticker, series, side, size, "
            "price, reason, timestamp, config_fingerprint, fee, signal_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(epoch_id, t["id"], t["ticker"], signal_log.series_of(t["ticker"]), t["side"],
              t["size"], t["price"], t["reason"], t["timestamp"], t["config_fingerprint"],
              t["fee"], t["signal_seen_at"]) for t in trades],
        )
        arch.executemany(
            "INSERT OR IGNORE INTO archived_positions (epoch_id, ticker, side, size, entry_price, "
            "opened_at, config_fingerprint, entry_fee) VALUES (?,?,?,?,?,?,?,?)",
            [(epoch_id, p["ticker"], p["side"], p["size"], p["entry_price"], p["opened_at"],
              p["config_fingerprint"], p["entry_fee"]) for p in positions],
        )
    return {"ok": True, "epoch_id": epoch_id, "label": label, "archived_at": now,
            "trades_archived": len(trades), "positions_archived": len(positions), **summary}


def epochs(limit: int = 50) -> list[dict]:
    """Every archived epoch, newest first — the comparable record of what
    each configuration era actually produced."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT * FROM epochs ORDER BY archived_at DESC LIMIT ?", (limit,))]
    except sqlite3.Error:
        return []


def compare(limit: int = 10) -> dict:
    """Epochs side by side against the standing target (CLAUDE.md's hard
    commandment: ~70% win rate AND ~70% whale accuracy, at an entry price
    that makes 70% profitable).

    `edge_pts` is the column that matters — win rate minus the breakeven
    accuracy its own entry prices implied. Positive means the epoch made
    money for the right reason; a high win rate with negative edge is the
    ≥0.95-unit-cost trap, which wins 96% of the time and still bleeds."""
    rows = epochs(limit)
    if not rows:
        return {"epochs": [], "best": None,
                "note": "nothing archived yet — archive_epoch() runs on paper reset"}
    scored = [r for r in rows if r.get("edge_pts") is not None]
    best = max(scored, key=lambda r: r["edge_pts"]) if scored else None
    return {
        "epochs": rows,
        "best": ({"label": best["label"], "epoch_id": best["id"], "edge_pts": best["edge_pts"],
                  "win_rate_pct": best["win_rate_pct"],
                  "breakeven_accuracy_pct": best["breakeven_accuracy_pct"],
                  "realised_pnl": best["realised_pnl"]} if best else None),
        "target": {"win_rate_pct": 70.0,
                   "note": "70% only counts as progress if mean entry unit cost is below 0.70 — "
                           "see CLAUDE.md's hard commandment"},
    }


def epoch_trades(epoch_id: int) -> list[dict]:
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT * FROM archived_trades WHERE epoch_id = ? ORDER BY timestamp", (epoch_id,))]
    except sqlite3.Error:
        return []
