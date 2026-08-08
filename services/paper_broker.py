"""
Fake broker: fills every order instantly at the signal's quoted price,
tracks positions and a running bankroll. No real money, no real orders —
this is what makes the whole POC safe to run unattended.

Bankroll, open positions, and the trade log persist to
data/paper_broker.db so a restart resumes the paper account instead of
silently resetting it to config/settings.yaml's starting_bankroll — same
pattern as services/signal_log.py. A plain restart loads what's there;
only an explicit reset() (POST /api/reset) wipes it.
"""
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "paper_broker.db"


@dataclass
class Position:
    ticker: str
    side: str
    size: int
    entry_price: float
    opened_at: float
    config_fingerprint: str | None = None


@dataclass
class Trade:
    id: str
    ticker: str
    side: str
    size: int
    price: float
    reason: str
    timestamp: float
    config_fingerprint: str | None = None

    def to_dict(self):
        return asdict(self)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # data/paper_broker.db is a live file the running dev server reads/writes
    # (CLAUDE.md) - CREATE TABLE IF NOT EXISTS alone doesn't add a column to
    # an existing table with existing rows, so new columns need an explicit,
    # idempotent ALTER TABLE guarded by a check, not just the CREATE above.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            bankroll REAL NOT NULL,
            starting_bankroll REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            ticker TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            opened_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            price REAL NOT NULL,
            reason TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
        """
    )
    # Config-variant fingerprinting (docs/advisory-engine-plan.md) - added
    # after both tables above already shipped and have live rows, hence the
    # guarded ALTER TABLE rather than a column in the CREATE statements.
    _add_column_if_missing(conn, "positions", "config_fingerprint", "TEXT")
    _add_column_if_missing(conn, "trades", "config_fingerprint", "TEXT")
    return conn


class PaperBroker:
    def __init__(self, starting_bankroll: float, db_path: Path | None = None):
        # db_path defaults to the module-level DB_PATH, resolved at call
        # time (not import time) so existing tests' `monkeypatch.setattr(pb,
        # "DB_PATH", ...)` pattern keeps working unchanged. Pass an explicit
        # db_path to run a second, fully independent paper account (e.g.
        # services/market_strategy.py's own capital pool) - each instance
        # gets its own file, so two brokers never share (and can't corrupt)
        # each other's broker_meta/positions/trades tables.
        self.db_path = db_path or DB_PATH
        self.positions: dict[str, Position] = {}   # keyed by ticker
        self.trade_log: list[Trade] = []
        self.last_trade_time: dict[str, float] = {}  # ticker -> timestamp

        with self._connect() as conn:
            row = conn.execute(
                "SELECT bankroll, starting_bankroll FROM broker_meta WHERE id = 1"
            ).fetchone()
            if row is None:
                # First run ever — seed from config and persist it.
                self.bankroll = starting_bankroll
                self.starting_bankroll = starting_bankroll
                conn.execute(
                    "INSERT INTO broker_meta (id, bankroll, starting_bankroll) VALUES (1, ?, ?)",
                    (self.bankroll, self.starting_bankroll),
                )
            else:
                # Resuming — the persisted account wins over whatever
                # config/settings.yaml's starting_bankroll says right now.
                self.bankroll, self.starting_bankroll = row
                for ticker, side, size, entry_price, opened_at, fp in conn.execute(
                    "SELECT ticker, side, size, entry_price, opened_at, config_fingerprint FROM positions"
                ):
                    self.positions[ticker] = Position(ticker, side, size, entry_price, opened_at, fp)
                for tid, ticker, side, size, price, reason, timestamp, fp in conn.execute(
                    "SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint FROM trades ORDER BY timestamp ASC"
                ):
                    self.trade_log.append(Trade(tid, ticker, side, size, price, reason, timestamp, fp))
                    self.last_trade_time[ticker] = max(self.last_trade_time.get(ticker, 0.0), timestamp)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    def can_trade(self, ticker: str, cooldown_sec: float) -> bool:
        last = self.last_trade_time.get(ticker)
        return last is None or (time.time() - last) >= cooldown_sec

    def open_position(
        self, ticker: str, side: str, size: int, price: float, reason: str,
        config_fingerprint: str | None = None,
    ) -> Trade:
        # price is always the YES price (see module docstring/mark_to_market) -
        # a NO contract's real per-unit cost is (1 - price), not price itself.
        # This used to charge `size * price` unconditionally, which silently
        # undercharged every NO entry (e.g. a NO position on a 0.1 YES price
        # should cost 0.9/contract, not 0.1) and manufactured phantom profit
        # on any NO position that never even moved - confirmed directly
        # against live trade history, not assumed.
        unit_cost = price if side == "yes" else (1 - price)
        cost = size * unit_cost
        cost = min(cost, self.bankroll)          # never go negative in the POC
        actual_size = int(cost / unit_cost) if unit_cost > 0 else 0

        self.bankroll -= cost
        self.positions[ticker] = Position(
            ticker=ticker, side=side, size=actual_size, entry_price=price, opened_at=time.time(),
            config_fingerprint=config_fingerprint,
        )
        trade = Trade(
            id=str(uuid.uuid4())[:8],
            ticker=ticker,
            side=side,
            size=actual_size,
            price=price,
            reason=reason,
            timestamp=time.time(),
            config_fingerprint=config_fingerprint,
        )
        self.trade_log.append(trade)
        self.last_trade_time[ticker] = trade.timestamp

        with self._connect() as conn:
            conn.execute("UPDATE broker_meta SET bankroll = ? WHERE id = 1", (self.bankroll,))
            conn.execute(
                "INSERT OR REPLACE INTO positions "
                "(ticker, side, size, entry_price, opened_at, config_fingerprint) VALUES (?, ?, ?, ?, ?, ?)",
                (ticker, side, actual_size, price, self.positions[ticker].opened_at, config_fingerprint),
            )
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, config_fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.id, trade.ticker, trade.side, trade.size, trade.price, trade.reason, trade.timestamp,
                 config_fingerprint),
            )
        return trade

    def close_position(self, ticker: str, exit_price: float, reason: str) -> Trade | None:
        """Sells an open position back at exit_price instead of holding it
        to settlement - direct request: this app had zero exit mechanism at
        all before this. A YES holder selling at the current market gets
        exit_price per contract back; a NO holder gets (1 - exit_price) per
        contract, since exit_price is always expressed in YES-price terms
        throughout this app (see mark_to_market/latest_prices). Returns
        None if there's no open position on this ticker - a no-op, not an
        error, since a poll tick's exit check racing a position that
        already closed this same tick shouldn't crash the loop."""
        pos = self.positions.get(ticker)
        if not pos:
            return None

        cash_back = pos.size * exit_price if pos.side == "yes" else pos.size * (1 - exit_price)
        realized_pnl = self.mark_to_market(ticker, exit_price)
        self.bankroll += cash_back

        trade = Trade(
            id=str(uuid.uuid4())[:8],
            ticker=ticker,
            side=pos.side,  # the position's side, not a new "close" side - keeps existing side-tag styling/logic working unchanged
            size=pos.size,
            price=exit_price,
            reason=f"closed: {reason} (realized {realized_pnl:+.2f})",
            timestamp=time.time(),
            # Inherited from the position being closed, not recomputed from
            # whatever config is active right now - a round-trip's entry and
            # close rows always carry the same fingerprint (the one active
            # at entry), even if strategy.* changed while the position was
            # held. See services/config_performance.py's module docstring.
            config_fingerprint=pos.config_fingerprint,
        )
        self.trade_log.append(trade)
        del self.positions[ticker]

        with self._connect() as conn:
            conn.execute("UPDATE broker_meta SET bankroll = ? WHERE id = 1", (self.bankroll,))
            conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, config_fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.id, trade.ticker, trade.side, trade.size, trade.price, trade.reason, trade.timestamp,
                 trade.config_fingerprint),
            )
        return trade

    def reset(self, starting_bankroll: float):
        """Wipes the persisted account and starts fresh — used by POST
        /api/reset. A plain process restart deliberately does NOT call this;
        it resumes whatever's in data/paper_broker.db instead."""
        self.bankroll = starting_bankroll
        self.starting_bankroll = starting_bankroll
        self.positions.clear()
        self.trade_log.clear()
        self.last_trade_time.clear()
        with self._connect() as conn:
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM trades")
            conn.execute(
                "INSERT OR REPLACE INTO broker_meta (id, bankroll, starting_bankroll) VALUES (1, ?, ?)",
                (starting_bankroll, starting_bankroll),
            )

    def mark_to_market(self, ticker: str, current_price: float) -> float:
        """Returns unrealized P&L for a given position, if any."""
        pos = self.positions.get(ticker)
        if not pos:
            return 0.0
        direction = 1 if pos.side == "yes" else -1
        return direction * (current_price - pos.entry_price) * pos.size

    def cost_basis(self, ticker: str) -> float:
        """The real dollar amount tied up in this position - size *
        entry_price for yes, size * (1 - entry_price) for no, same
        yes-price-always convention as entry_price/mark_to_market/
        open_position's unit_cost. Single source of truth so strategy_engine
        (pnl_pct for take-profit/stop-loss/auto-exit) and the dashboard
        (Cost/Payout columns, capital-at-risk) can't each reimplement this
        and drift out of sync with each other or with what open_position
        actually charged."""
        pos = self.positions.get(ticker)
        if not pos:
            return 0.0
        return pos.size * (pos.entry_price if pos.side == "yes" else (1 - pos.entry_price))

    def total_unrealized_pnl(self, latest_prices: dict[str, float]) -> float:
        return sum(
            self.mark_to_market(ticker, latest_prices.get(ticker, pos.entry_price))
            for ticker, pos in self.positions.items()
        )

    def equity(self, latest_prices: dict[str, float]) -> float:
        return round(self.bankroll + self.total_unrealized_pnl(latest_prices), 2)

    def state(self, latest_prices: dict[str, float]) -> dict:
        return {
            "bankroll": round(self.bankroll, 2),
            "equity": self.equity(latest_prices),
            "starting_bankroll": self.starting_bankroll,
            "positions": [
                {**asdict(p), "cost_basis": round(self.cost_basis(p.ticker), 2)} for p in self.positions.values()
            ],
            "recent_trades": [t.to_dict() for t in self.trade_log[-25:][::-1]],
        }
