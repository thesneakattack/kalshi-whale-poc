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

from services import kalshi_fees

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "paper_broker.db"


@dataclass
class Position:
    ticker: str
    side: str
    size: int
    entry_price: float
    opened_at: float
    config_fingerprint: str | None = None
    # Real Kalshi taker fee paid on entry (services/kalshi_fees.py),
    # carried on the position so close_position can report a true
    # round-trip-inclusive realized P&L without re-deriving it from a
    # trade-log scan. Defaults to 0.0 for positions opened before this
    # field existed (see the idempotent migration below).
    entry_fee: float = 0.0


@dataclass
class PendingOrder:
    """A resting limit order - the paper-mode maker-order simulation
    (services/kalshi_fees.py's maker_fee(), 2026-08-15 direct request).
    Unlike Position/Trade, limit_price is a target, not yet a fill - see
    PaperBroker.check_pending_fills for how/when this becomes a real
    Position. One pending order per ticker at a time, same constraint as
    positions itself."""
    ticker: str
    side: str
    size: int  # contracts, not a dollar budget - caller's responsibility, same convention as open_position
    limit_price: float  # always the YES price, same convention as Position.entry_price
    placed_at: float
    expires_at: float
    reason: str
    config_fingerprint: str | None = None
    signal_seen_at: float | None = None


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
    # Real Kalshi taker fee this specific fill paid - not a round-trip
    # total (see services/kalshi_fees.py). 0.0 for trades logged before
    # this field existed.
    fee: float = 0.0
    # The originating WhaleSignal's own .timestamp (signal_log's seen_at) -
    # 2026-08-16 direct request after an investigation that took cross-
    # referencing signal_log.db/candidate_log.db/config_performance.db by
    # hand to answer "how long from signal to open." None for a close-side
    # Trade (settlement/take-profit/stop-loss/auto-exit close a Position,
    # not a fresh signal) and for entries opened before this field existed.
    # trade_analytics.build_trade_history derives time_to_open_sec from
    # this directly rather than re-joining signal_log after the fact.
    signal_seen_at: float | None = None

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
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
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
    # Real fee modeling (docs/prediction-market-strategy-alignment-plan.md
    # Part 2.2) - same idempotent-migration pattern, added after both tables
    # already had live rows. NULL on pre-existing rows reads back as None,
    # handled explicitly wherever these are reconstructed from the DB below.
    _add_column_if_missing(conn, "positions", "entry_fee", "REAL")
    _add_column_if_missing(conn, "trades", "fee", "REAL")
    # Trade.signal_seen_at (2026-08-16 direct report - see that field's own
    # docstring) - same idempotent-migration pattern, added after this
    # table already had live rows.
    _add_column_if_missing(conn, "trades", "signal_seen_at", "REAL")
    # Maker/limit-order path (2026-08-15, docs/profit-maximization-
    # assessment-2026-08-15.md direct request) - own table, same
    # persistence idiom as positions/trades, so a resting order survives a
    # restart instead of silently vanishing (or worse, silently
    # "un-resting" into a market fill on resume).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_orders (
            ticker TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            limit_price REAL NOT NULL,
            placed_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            reason TEXT NOT NULL,
            config_fingerprint TEXT
        )
        """
    )
    _add_column_if_missing(conn, "pending_orders", "signal_seen_at", "REAL")
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
        self.pending_orders: dict[str, PendingOrder] = {}   # keyed by ticker, same as positions

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
                for ticker, side, size, entry_price, opened_at, fp, entry_fee in conn.execute(
                    "SELECT ticker, side, size, entry_price, opened_at, config_fingerprint, entry_fee FROM positions"
                ):
                    self.positions[ticker] = Position(ticker, side, size, entry_price, opened_at, fp, entry_fee or 0.0)
                for tid, ticker, side, size, price, reason, timestamp, fp, fee, signal_seen_at in conn.execute(
                    "SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, fee, signal_seen_at "
                    "FROM trades ORDER BY timestamp ASC"
                ):
                    self.trade_log.append(Trade(tid, ticker, side, size, price, reason, timestamp, fp, fee or 0.0, signal_seen_at))
                    self.last_trade_time[ticker] = max(self.last_trade_time.get(ticker, 0.0), timestamp)
                for ticker, side, size, limit_price, placed_at, expires_at, reason, fp, signal_seen_at in conn.execute(
                    "SELECT ticker, side, size, limit_price, placed_at, expires_at, reason, config_fingerprint, signal_seen_at "
                    "FROM pending_orders"
                ):
                    self.pending_orders[ticker] = PendingOrder(
                        ticker, side, size, limit_price, placed_at, expires_at, reason, fp, signal_seen_at,
                    )

    def _connect(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    def can_trade(self, ticker: str, cooldown_sec: float) -> bool:
        last = self.last_trade_time.get(ticker)
        return last is None or (time.time() - last) >= cooldown_sec

    def open_position(
        self, ticker: str, side: str, size: int, price: float, reason: str,
        config_fingerprint: str | None = None, fee_fn=kalshi_fees.taker_fee,
        signal_seen_at: float | None = None,
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

        # Real Kalshi taker fee (services/kalshi_fees.py) by default, deducted
        # as an additional cash outflow on top of cost - not folded into the
        # position-sizing math above, which stays exactly as already tested/
        # correct. This means a trade landing right at the bankroll limit
        # can push bankroll fractionally (cents) below zero once the fee is
        # added - an acceptable, explicitly-accepted approximation for a
        # paper POC (see the "never go negative in the POC" comment above,
        # already an approximation, not a hard invariant), not worth the
        # complexity of solving cost+fee<=bankroll simultaneously.
        #
        # fee_fn (2026-08-15, maker/limit-order path): a resting limit order
        # that fills (see check_pending_fills below) pays kalshi_fees.
        # maker_fee() instead - the entire point of resting an order rather
        # than taking the market. Defaults to taker_fee so every existing
        # caller (a plain market-order entry) is completely unaffected.
        fee = fee_fn(actual_size, price, ticker=ticker)

        self.bankroll -= (cost + fee)
        self.positions[ticker] = Position(
            ticker=ticker, side=side, size=actual_size, entry_price=price, opened_at=time.time(),
            config_fingerprint=config_fingerprint, entry_fee=fee,
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
            fee=fee,
            signal_seen_at=signal_seen_at,
        )
        self.trade_log.append(trade)
        self.last_trade_time[ticker] = trade.timestamp

        with self._connect() as conn:
            conn.execute("UPDATE broker_meta SET bankroll = ? WHERE id = 1", (self.bankroll,))
            conn.execute(
                "INSERT OR REPLACE INTO positions "
                "(ticker, side, size, entry_price, opened_at, config_fingerprint, entry_fee) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, side, actual_size, price, self.positions[ticker].opened_at, config_fingerprint, fee),
            )
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, config_fingerprint, fee, signal_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.id, trade.ticker, trade.side, trade.size, trade.price, trade.reason, trade.timestamp,
                 config_fingerprint, fee, signal_seen_at),
            )
        return trade

    def place_limit_order(
        self, ticker: str, side: str, size: int, limit_price: float, reason: str,
        expires_at: float, config_fingerprint: str | None = None, signal_seen_at: float | None = None,
    ) -> PendingOrder | None:
        """Rests a limit order instead of filling instantly at the quoted
        price - the paper-mode maker-order simulation (2026-08-15, docs/
        profit-maximization-assessment-2026-08-15.md direct request:
        fees were consuming ~60% of gross profit, and this app had no
        maker/limit-order path at all). Unlike open_position, this does
        NOT touch bankroll yet - no cash/collateral is reserved for a
        resting order, same "explicitly accepted approximation" tolerance
        as open_position's own "never go negative" cost clamp; a real
        exchange would reserve margin against a resting order, this paper
        POC doesn't model that.

        size is already in contracts (the caller's responsibility, same
        convention as open_position's own actual_size) - not a dollar
        budget, since the caller already knows the limit_price it's
        asking for and can size off that the same way evaluate() already
        sizes a market order.

        One resting order per ticker at a time, same constraint
        positions already has - returns None (no-op, not an error) if
        one's already pending on this ticker, rather than silently
        replacing/duplicating it."""
        if ticker in self.pending_orders or size <= 0:
            return None
        order = PendingOrder(
            ticker=ticker, side=side, size=size, limit_price=limit_price,
            placed_at=time.time(), expires_at=expires_at, reason=reason,
            config_fingerprint=config_fingerprint, signal_seen_at=signal_seen_at,
        )
        self.pending_orders[ticker] = order
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending_orders "
                "(ticker, side, size, limit_price, placed_at, expires_at, reason, config_fingerprint, signal_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order.ticker, order.side, order.size, order.limit_price, order.placed_at,
                 order.expires_at, order.reason, order.config_fingerprint, order.signal_seen_at),
            )
        return order

    def _cancel_pending(self, ticker: str) -> None:
        del self.pending_orders[ticker]
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_orders WHERE ticker = ?", (ticker,))

    def check_pending_fills(
        self, latest_bids: dict[str, float], latest_asks: dict[str, float], now: float | None = None,
    ) -> list[dict]:
        """Runs once per tick (main.py, right after the signal-evaluation
        loop) - resolves every resting limit order against this tick's
        real bid/ask. A yes-side buy fills once the real ask has come
        down to (or below) the limit price - someone's willing to sell at
        or better than what this order is bidding. A no-side buy fills
        once (1 - the real yes-bid) has come down to (or below) the
        limit's own no-side unit cost - the mirror-image condition, same
        side-aware convention as everywhere else in this app (price is
        always expressed in YES terms; unit_cost = price if side=='yes'
        else 1-price).

        Filled orders pay kalshi_fees.maker_fee() instead of taker_fee()
        via open_position's fee_fn param - the entire point of resting an
        order instead of taking the market. Fills at the real available
        price, which may be better than the limit (the order asked for
        "at most this," not "exactly this," same as a real resting limit
        order) - never worse.

        An order past its own expires_at is dropped unfilled (cancelled),
        not resubmitted as a market order - this never chases a price
        that's moved past what the order actually asked for; a signal
        that goes stale before the market comes to it is exactly the kind
        of trade this mechanism is supposed to skip, not force through at
        a worse (taker) price. No fresh quote this tick (ticker rotated
        off the watchlist, etc.) leaves the order pending untouched rather
        than guessing.

        Returns one decision dict per fill, in the same shape evaluate()'s
        caller already expects from a market-order trade."""
        now = now if now is not None else time.time()
        fills = []
        for ticker in list(self.pending_orders):
            order = self.pending_orders[ticker]
            if now >= order.expires_at:
                self._cancel_pending(ticker)
                continue
            if order.side == "yes":
                available_unit_cost = latest_asks.get(ticker)
            else:
                bid = latest_bids.get(ticker)
                available_unit_cost = (1 - bid) if bid is not None else None
            if available_unit_cost is None:
                continue  # no fresh quote this tick - wait, don't guess
            limit_unit_cost = order.limit_price if order.side == "yes" else (1 - order.limit_price)
            if available_unit_cost > limit_unit_cost:
                continue  # market hasn't come to this order's price yet
            fill_price = available_unit_cost if order.side == "yes" else (1 - available_unit_cost)
            self._cancel_pending(ticker)  # remove from pending before opening - a different dict than positions
            trade = self.open_position(
                ticker=order.ticker, side=order.side, size=order.size, price=fill_price,
                reason=order.reason, config_fingerprint=order.config_fingerprint, fee_fn=kalshi_fees.maker_fee,
                signal_seen_at=order.signal_seen_at,
            )
            fills.append({"action": "trade", "trade": trade.to_dict(), "reason": order.reason, "source": "limit_order"})
        return fills

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

        # Real Kalshi taker fee on this leg (services/kalshi_fees.py) -
        # naturally 0.0 at exit_price 0.0/1.0 (settlement's terminal payout,
        # see strategy_engine.close_if_settled), matching that settlement
        # isn't a fee-charged trade in the first place. "realized" here is
        # now the TRUE net P&L including both legs' fees: pos.entry_fee was
        # already deducted from bankroll back at open_position time, so
        # subtracting it again here (alongside this leg's own close_fee)
        # makes the reported number match bankroll's actual net change
        # across the full round trip, not just the raw price move.
        close_fee = kalshi_fees.taker_fee(pos.size, exit_price, ticker=ticker)
        gross_cash_back = pos.size * exit_price if pos.side == "yes" else pos.size * (1 - exit_price)
        cash_back = gross_cash_back - close_fee
        realized_pnl = self.mark_to_market(ticker, exit_price) - pos.entry_fee - close_fee
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
            fee=close_fee,
        )
        self.trade_log.append(trade)
        del self.positions[ticker]

        with self._connect() as conn:
            conn.execute("UPDATE broker_meta SET bankroll = ? WHERE id = 1", (self.bankroll,))
            conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, config_fingerprint, fee) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.id, trade.ticker, trade.side, trade.size, trade.price, trade.reason, trade.timestamp,
                 trade.config_fingerprint, close_fee),
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
        self.pending_orders.clear()
        with self._connect() as conn:
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM pending_orders")
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

    def total_position_value(self, latest_prices: dict[str, float]) -> float:
        """Current mark-to-market value of every open position - cost basis
        plus unrealized gain/loss, not just the gain/loss component alone.
        total_unrealized_pnl() is correct on its own terms for *its* job
        (the dashboard's own Unrealized P&L figure - see CLAUDE.md's
        documented bug pattern, and this file's own equity() history), but
        by itself it excludes the capital that's actually tied up in the
        position, which bankroll already had subtracted at entry. Needed
        as its own method (not just inlined into equity() below) so a
        caller that wants "what would I have if I liquidated everything
        right now" isn't tempted to reach for total_unrealized_pnl() alone,
        which looks equally plausible at the call site but answers a
        different question - the exact bug equity() itself had until
        2026-08-09 (audit finding: confirmed two ways - the project's own
        equity test's comment named a position's real value while the
        assertion it sat next to didn't include it, and equity() showed a
        real discontinuity, jumping by roughly a position's full cost basis
        at the instant it closed even at zero net price change)."""
        return sum(
            self.cost_basis(ticker) + self.mark_to_market(ticker, latest_prices.get(ticker, pos.entry_price))
            for ticker, pos in self.positions.items()
        )

    def equity(self, latest_prices: dict[str, float]) -> float:
        """True total portfolio value: cash on hand plus the current market
        value of everything currently held - not just bankroll plus the
        gain/loss on top of it (see total_position_value()'s docstring for
        why that distinction is real and was a genuine bug here until
        2026-08-09)."""
        return round(self.bankroll + self.total_position_value(latest_prices), 2)

    def state(self, latest_prices: dict[str, float]) -> dict:
        return {
            "bankroll": round(self.bankroll, 2),
            "equity": self.equity(latest_prices),
            # Its own explicit field, not left for a caller to re-derive as
            # equity - bankroll - that re-derivation is exactly how the
            # header strip's "Unrealized P&L" broke once already (see
            # CLAUDE.md) and would break again the moment equity() stopped
            # being defined as bankroll + this exact number (which, as of
            # the fix above, it no longer is).
            "unrealized_pnl": round(self.total_unrealized_pnl(latest_prices), 2),
            "starting_bankroll": self.starting_bankroll,
            "positions": [
                {**asdict(p), "cost_basis": round(self.cost_basis(p.ticker), 2)} for p in self.positions.values()
            ],
            "recent_trades": [t.to_dict() for t in self.trade_log[-25:][::-1]],
            # Resting limit orders (2026-08-15 maker-order path) - real
            # visibility into "what's waiting to fill," same reasoning as
            # exposing positions rather than leaving them invisible until a
            # fill/close event shows up in recent_trades.
            "pending_orders": [asdict(o) for o in self.pending_orders.values()],
        }
