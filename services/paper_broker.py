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
import contextlib
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

from services import db, history_push, kalshi_fees
from services.risk_manager import RiskManager

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
    # Set by services/settlement_edge_entry.py (2026-08-23) - this position
    # was deliberately opened in a settlement window's final seconds, with
    # no intent to manage it via price-driven exits (there's no runway
    # left to). services/exits/exit_engine.py's runway-floor forced exit
    # (exit_min_seconds_to_close) skips positions with this set, since that
    # rule exists specifically to stop OTHER positions from riding to
    # settlement unmanaged - the opposite of what this one is for. False
    # (default) for every position opened by the whale-follow strategy,
    # unchanged behavior for all of them.
    hold_to_settlement: bool = False


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
    # Carried from the signal that placed this order so check_pending_fills
    # can re-validate the fill-time price against the same confidence gate
    # a fresh signal at that price would have to clear - see
    # strategy_engine.py's _validate_entry_price docstring for the bug
    # this closes.
    confidence: float | None = None


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
    # Flagged by correct_erroneous_close (2026-08-17) - a CLOSE trade whose
    # exit_price was confirmed fabricated (a stop-loss fired on a price
    # market_history's own independent data said was wrong). False for
    # every trade before this existed and for every entry - only ever set
    # on a specific, individually-confirmed close row. trade_analytics.
    # build_trade_history skips these entirely rather than counting them as
    # a loss or a phantom win.
    excluded: bool = False
    # The three structured inputs behind a position-netting close (services/
    # exits/position_netting.py review(), issue #213, 2026-08-30): the
    # expected-value improvement the action was estimated to deliver, the
    # materiality bar it had to clear, and the volatility ratio that scaled
    # that bar. None for every entry, every non-netting close, a locked_loss
    # close_all (no bar is computed there), and every row written before
    # these existed. The reason sentence keeps carrying the first two in
    # prose; these exist so an analysis reads them as columns instead of
    # regex-parsing `bar \$([0-9.]+)` out of it (docs/data-layer-analysis-
    # layer-contract.md: prose is for the reader, columns are for the
    # analysis).
    netting_improvement_usd: float | None = None
    netting_bar_usd: float | None = None
    netting_vol_ratio: float | None = None
    # Real Kalshi exit-taker-fee cost of a locked_loss position_netting
    # close (services/exits/position_netting.py, docs/superpowers/specs/
    # 2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md
    # Part 2) - the module's own docstring argues unwinding a locked
    # position early only adds fee drag versus Kalshi's fee-free
    # settlement; this makes that cost measurable instead of buried in
    # realized P&L. None for every entry, every non-netting close, every
    # locked_profit/variable netting close, and every row written before
    # this existed.
    #
    # !! GROUP TOTAL, REPEATED PER ROW - NEVER SUM THIS COLUMN. A netting
    # close_all writes one trades row per member ticker, and each of those
    # rows carries the SAME whole-group fee figure (the convention the
    # three sibling netting_* columns above already established - they are
    # non-additive by nature, so repeating them is harmless; a USD amount
    # is not, and SUM(netting_exit_fee_usd) overcounts by exactly the group
    # size). It stays a group total deliberately: the per-leg number is
    # already in this same row's own `fee` column, computed from the same
    # taker_fee(size, price, ticker) at the same price, so a per-leg
    # netting_exit_fee_usd would be a pure duplicate and this column would
    # carry no information at all.
    #   Total netting-driven fee drag, correctly:
    #     SELECT SUM(fee) FROM trades WHERE netting_exit_fee_usd IS NOT NULL
    #   (the column is the flag for "this row was a locked_loss netting
    #   leg"; `fee` is that leg's own real cost). Per-group total: read any
    #   ONE member row's netting_exit_fee_usd, or GROUP BY the close's
    #   event via `reason`.
    netting_exit_fee_usd: float | None = None

    def to_dict(self):
        return asdict(self)


def _init_broker_meta(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            bankroll REAL NOT NULL,
            starting_bankroll REAL NOT NULL
        )
        """
    )


def _init_positions(conn: sqlite3.Connection) -> None:
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


def _init_trades(conn: sqlite3.Connection) -> None:
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


def _init_pending_orders(conn: sqlite3.Connection) -> None:
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


db.register_schema("broker_meta", _init_broker_meta)
db.register_schema("positions", _init_positions)
db.register_schema("trades", _init_trades)
db.register_schema("pending_orders", _init_pending_orders)


@contextlib.contextmanager
def _connect(db_path: Path):
    """Every existing `with _connect() as conn:`/`with self._connect() as
    conn:` call site keeps working unchanged - now backed by services/db.py's
    closing connect(). WAL mode and the busy_timeout pragma are set by
    db.connect() itself, same as every other migrated module.

    The 13 add_column_if_missing calls and the one index below keep their
    original relative order (undisturbed since each column/table shipped) -
    in particular, the idx_trades_excluded index runs immediately after the
    `excluded` column it indexes (call #7), not after all 13 calls; SQLite's
    CREATE INDEX doesn't actually care when the column was added as long as
    it exists first, but preserving the original ordering removes any
    question of whether reordering is truly inert."""
    with db.connect(
        db_path, tables=("broker_meta", "positions", "trades", "pending_orders")
    ) as conn:
        # Config-variant fingerprinting (docs/advisory-engine-plan.md) - added
        # after both tables above already shipped and have live rows, hence
        # the guarded ALTER TABLE rather than a column in the CREATE
        # statements.
        db.add_column_if_missing(conn, "positions", "config_fingerprint", "TEXT")
        db.add_column_if_missing(conn, "trades", "config_fingerprint", "TEXT")
        # Real fee modeling (docs/prediction-market-strategy-alignment-plan.md
        # Part 2.2) - same idempotent-migration pattern, added after both
        # tables already had live rows. NULL on pre-existing rows reads back
        # as None, handled explicitly wherever these are reconstructed from
        # the DB below.
        db.add_column_if_missing(conn, "positions", "entry_fee", "REAL")
        db.add_column_if_missing(conn, "trades", "fee", "REAL")
        # Position.hold_to_settlement (2026-08-23, services/settlement_edge_entry.py) -
        # same idempotent-migration pattern, added after this table already
        # had live rows. 0/NULL on every pre-existing row reads back as False
        # via the `or 0` below, which is correct: no position opened before
        # this field existed was ever a settlement-edge entry.
        db.add_column_if_missing(conn, "positions", "hold_to_settlement", "INTEGER")
        # Trade.signal_seen_at (2026-08-16 direct report - see that field's
        # own docstring) - same idempotent-migration pattern, added after
        # this table already had live rows.
        db.add_column_if_missing(conn, "trades", "signal_seen_at", "REAL")
        # excluded (2026-08-17 direct request/incident: a real WTA position -
        # Cirstea/Kalinskaya - was closed by check_exits at a fabricated
        # exit_price of 0.0 one tick after market_history's own REST-polled
        # price had sat pinned at 0.99 for 13+ minutes - real damage to real
        # (paper) bankroll, and real contamination of every downstream
        # win-rate/P&L statistic that reads this table. Same non-destructive
        # idiom signal_log.excluded already established: a bad CLOSE row is
        # flagged, never deleted, so history stays a first-class asset
        # (CLAUDE.md) while ceasing to count as evidence. See
        # PaperBroker.correct_erroneous_close.
        db.add_column_if_missing(conn, "trades", "excluded", "INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_excluded ON trades (excluded)")
        # Netting decision inputs (issue #213, 2026-08-30; see Trade) - same
        # idempotent-migration pattern, the live table already had rows. NULL
        # on every pre-existing row and every non-netting row IS the meaning
        # ("no bar was computed"), not a gap to backfill.
        db.add_column_if_missing(conn, "trades", "netting_improvement_usd", "REAL")
        db.add_column_if_missing(conn, "trades", "netting_bar_usd", "REAL")
        db.add_column_if_missing(conn, "trades", "netting_vol_ratio", "REAL")
        db.add_column_if_missing(conn, "trades", "netting_exit_fee_usd", "REAL")
        db.add_column_if_missing(conn, "pending_orders", "signal_seen_at", "REAL")
        db.add_column_if_missing(conn, "pending_orders", "confidence", "REAL")
        yield conn


class PaperBroker:
    def __init__(self, starting_bankroll: float, db_path: Path | None = None, risk: RiskManager | None = None):
        # db_path defaults to the module-level DB_PATH, resolved at call
        # time (not import time) so existing tests' `monkeypatch.setattr(pb,
        # "DB_PATH", ...)` pattern keeps working unchanged. Pass an explicit
        # db_path to run a second, fully independent paper account - each
        # instance gets its own file, so two brokers never share (and can't
        # corrupt) each other's broker_meta/positions/trades tables.
        self.db_path = db_path or DB_PATH
        # Execution-layer risk enforcement (2026-08-23 gap-check finding):
        # before this, RiskManager was only ever consulted from inside
        # strategy_engine.evaluate(), never at the actual execution choke
        # point both a market-order entry and a filled resting limit order
        # pass through - the same general shape as the four-entry gate
        # bypass this session already fixed once (a gate that only runs at
        # one call site is a gate that can be skipped). None (default)
        # means no risk instance is wired in, preserving every existing
        # caller/test's exact prior behavior.
        self.risk = risk
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
                for ticker, side, size, entry_price, opened_at, fp, entry_fee, hold_to_settlement in conn.execute(
                    "SELECT ticker, side, size, entry_price, opened_at, config_fingerprint, entry_fee, "
                    "hold_to_settlement FROM positions"
                ):
                    self.positions[ticker] = Position(
                        ticker, side, size, entry_price, opened_at, fp, entry_fee or 0.0, bool(hold_to_settlement),
                    )
                for (tid, ticker, side, size, price, reason, timestamp, fp, fee, signal_seen_at, excluded,
                     net_improvement, net_bar, net_vol_ratio, net_exit_fee) in conn.execute(
                    "SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, fee, "
                    "signal_seen_at, excluded, netting_improvement_usd, netting_bar_usd, netting_vol_ratio, "
                    "netting_exit_fee_usd "
                    "FROM trades ORDER BY timestamp ASC"
                ):
                    self.trade_log.append(Trade(tid, ticker, side, size, price, reason, timestamp, fp, fee or 0.0,
                                                signal_seen_at, bool(excluded),
                                                netting_improvement_usd=net_improvement, netting_bar_usd=net_bar,
                                                netting_vol_ratio=net_vol_ratio, netting_exit_fee_usd=net_exit_fee))
                    self.last_trade_time[ticker] = max(self.last_trade_time.get(ticker, 0.0), timestamp)
                for ticker, side, size, limit_price, placed_at, expires_at, reason, fp, signal_seen_at, confidence in conn.execute(
                    "SELECT ticker, side, size, limit_price, placed_at, expires_at, reason, config_fingerprint, "
                    "signal_seen_at, confidence FROM pending_orders"
                ):
                    self.pending_orders[ticker] = PendingOrder(
                        ticker, side, size, limit_price, placed_at, expires_at, reason, fp, signal_seen_at, confidence,
                    )

    def _connect(self):
        return _connect(self.db_path)

    def can_trade(self, ticker: str, cooldown_sec: float) -> bool:
        last = self.last_trade_time.get(ticker)
        return last is None or (time.time() - last) >= cooldown_sec

    def open_position(
        self, ticker: str, side: str, size: int, price: float, reason: str,
        config_fingerprint: str | None = None, fee_fn=kalshi_fees.taker_fee,
        signal_seen_at: float | None = None, hold_to_settlement: bool = False,
    ) -> Trade | None:
        # Execution-layer risk guard (2026-08-23) - read-only checks of
        # self.risk's already-computed state, never re-invoking
        # check_daily_loss() itself here: that mutates day-rollover state
        # and strategy_engine.evaluate() already calls it once per decision
        # against the right bankroll snapshot, so re-running it again here
        # against a possibly-stale bankroll would double-mutate. None
        # (no risk instance wired in) is a no-op, same as every other
        # opt-in gate in this app.
        if self.risk is not None and self.risk.halted:
            return None

        # price is always the YES price (see module docstring/mark_to_market) -
        # a NO contract's real per-unit cost is (1 - price), not price itself.
        # This used to charge `size * price` unconditionally, which silently
        # undercharged every NO entry (e.g. a NO position on a 0.1 YES price
        # should cost 0.9/contract, not 0.1) and manufactured phantom profit
        # on any NO position that never even moved - confirmed directly
        # against live trade history, not assumed.
        unit_cost = kalshi_fees.unit_cost(side, price)
        cost = size * unit_cost
        cost = min(cost, self.bankroll)          # never go negative in the POC
        actual_size = int(cost / unit_cost) if unit_cost > 0 else 0

        if self.risk is not None:
            current_exposure = sum(self.cost_basis(t) for t in self.positions)
            if not self.risk.check_total_exposure(current_exposure, cost, self.bankroll):
                return None

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
            config_fingerprint=config_fingerprint, entry_fee=fee, hold_to_settlement=hold_to_settlement,
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
                "(ticker, side, size, entry_price, opened_at, config_fingerprint, entry_fee, hold_to_settlement) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, side, actual_size, price, self.positions[ticker].opened_at, config_fingerprint, fee,
                 int(hold_to_settlement)),
            )
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, config_fingerprint, fee, signal_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.id, trade.ticker, trade.side, trade.size, trade.price, trade.reason, trade.timestamp,
                 config_fingerprint, fee, signal_seen_at),
            )
        # History-push hook (design §4.3/§2 - loadTradingHistory/
        # loadAdvisory/loadRegimeSegmentation are all trade close/open-
        # driven). Placed after the two early `return None` guards above
        # (halted / exposure-limit rejection), which are no-ops, not real
        # writes.
        history_push.mark_history_changed()
        return trade

    def place_limit_order(
        self, ticker: str, side: str, size: int, limit_price: float, reason: str,
        expires_at: float, config_fingerprint: str | None = None, signal_seen_at: float | None = None,
        confidence: float | None = None,
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
            config_fingerprint=config_fingerprint, signal_seen_at=signal_seen_at, confidence=confidence,
        )
        self.pending_orders[ticker] = order
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending_orders "
                "(ticker, side, size, limit_price, placed_at, expires_at, reason, config_fingerprint, "
                "signal_seen_at, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order.ticker, order.side, order.size, order.limit_price, order.placed_at,
                 order.expires_at, order.reason, order.config_fingerprint, order.signal_seen_at, order.confidence),
            )
        return order

    def _cancel_pending(self, ticker: str) -> None:
        del self.pending_orders[ticker]
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_orders WHERE ticker = ?", (ticker,))

    def check_pending_fills(
        self, latest_bids: dict[str, float], latest_asks: dict[str, float], now: float | None = None,
        validate_fn=None,
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

        validate_fn(ticker, side, fill_price, confidence) -> (ok, reason),
        when given, re-checks the fill-time price/confidence against the
        same gates a fresh signal at that price would have to clear
        (services/strategy_engine.py's FollowTheWhaleStrategy.
        validate_pending_fill) - the order was only ever validated once,
        at PLACEMENT time, against the price it asked for; nothing
        previously re-checked the price it actually filled at, which is
        the confirmed root cause of the "four-entry gate bypass" (real
        entries at unit costs 0.97, 1.00, 0.20, 0.97 that should never have
        cleared entry_threshold/the price band). None (the default) skips
        re-validation entirely, preserving this method's exact prior
        behavior for every existing caller/test.

        Returns one decision dict per fill, in the same shape evaluate()'s
        caller already expects from a market-order trade."""
        now = now if now is not None else time.time()
        fills = []
        for ticker in list(self.pending_orders):
            order = self.pending_orders[ticker]
            if now >= order.expires_at:
                self._cancel_pending(ticker)
                continue
            # The yes price this order would fill at right now: a YES buyer
            # lifts the yes ask; a NO buyer lifts the no ask, which IS the
            # yes bid (docs/kalshi/get-market-orderbook.md: "a bid for yes
            # at price X is equivalent to an ask for no at price (100-X)").
            # Read once as a yes price and side-adjusted once through
            # kalshi_fees.unit_cost - this used to invert the bid into a
            # no-side cost and then invert that back into fill_price
            # (1 - (1 - bid)), the only place the inversion ran in reverse
            # (issue #212); same number to within one ulp.
            fill_price = latest_asks.get(ticker) if order.side == "yes" else latest_bids.get(ticker)
            if fill_price is None:
                continue  # no fresh quote this tick - wait, don't guess
            available_unit_cost = kalshi_fees.unit_cost(order.side, fill_price)
            limit_unit_cost = kalshi_fees.unit_cost(order.side, order.limit_price)
            if available_unit_cost > limit_unit_cost:
                continue  # market hasn't come to this order's price yet
            if validate_fn is not None:
                ok, reason = validate_fn(order.ticker, order.side, fill_price, order.confidence)
                if not ok:
                    self._cancel_pending(ticker)
                    fills.append({
                        "action": "fill_rejected", "ticker": order.ticker, "side": order.side,
                        "price": fill_price, "reason": reason, "source": "limit_order",
                    })
                    continue
            self._cancel_pending(ticker)  # remove from pending before opening - a different dict than positions
            trade = self.open_position(
                ticker=order.ticker, side=order.side, size=order.size, price=fill_price,
                reason=order.reason, config_fingerprint=order.config_fingerprint, fee_fn=kalshi_fees.maker_fee,
                signal_seen_at=order.signal_seen_at,
            )
            if trade is None:
                # Execution-layer risk guard fired (self.risk.halted, or the
                # portfolio exposure cap) - same "cancel, don't force
                # through" outcome as a validate_fn rejection above, just a
                # different gate.
                fills.append({
                    "action": "fill_rejected", "ticker": order.ticker, "side": order.side,
                    "price": fill_price, "reason": "risk halted or exposure cap", "source": "limit_order",
                })
                continue
            fills.append({"action": "trade", "trade": trade.to_dict(), "reason": order.reason, "source": "limit_order"})
        return fills

    def close_position(
        self, ticker: str, exit_price: float, reason: str, *,
        netting_improvement_usd: float | None = None, netting_bar_usd: float | None = None,
        netting_vol_ratio: float | None = None, netting_exit_fee_usd: float | None = None,
    ) -> Trade | None:
        """Sells an open position back at exit_price instead of holding it
        to settlement - direct request: this app had zero exit mechanism at
        all before this. A YES holder selling at the current market gets
        exit_price per contract back; a NO holder gets (1 - exit_price) per
        contract, since exit_price is always expressed in YES-price terms
        throughout this app (see mark_to_market/latest_prices). Returns
        None if there's no open position on this ticker - a no-op, not an
        error, since a poll tick's exit check racing a position that
        already closed this same tick shouldn't crash the loop.

        The keyword-only netting_* values are position_netting.review's
        structured decision inputs (see Trade, issue #213); every other
        caller leaves them None and the row's columns NULL."""
        pos = self.positions.get(ticker)
        if not pos:
            return None

        # Real Kalshi taker fee on this leg (services/kalshi_fees.py) -
        # naturally 0.0 at exit_price 0.0/1.0 (settlement's terminal payout,
        # see exit_engine.close_if_settled), matching that settlement
        # isn't a fee-charged trade in the first place. "realized" here is
        # now the TRUE net P&L including both legs' fees: pos.entry_fee was
        # already deducted from bankroll back at open_position time, so
        # subtracting it again here (alongside this leg's own close_fee)
        # makes the reported number match bankroll's actual net change
        # across the full round trip, not just the raw price move.
        close_fee = kalshi_fees.taker_fee(pos.size, exit_price, ticker=ticker)
        gross_cash_back = pos.size * kalshi_fees.unit_cost(pos.side, exit_price)
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
            netting_improvement_usd=netting_improvement_usd,
            netting_bar_usd=netting_bar_usd,
            netting_vol_ratio=netting_vol_ratio,
            netting_exit_fee_usd=netting_exit_fee_usd,
        )
        self.trade_log.append(trade)
        del self.positions[ticker]

        with self._connect() as conn:
            conn.execute("UPDATE broker_meta SET bankroll = ? WHERE id = 1", (self.bankroll,))
            conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, config_fingerprint, fee, "
                "netting_improvement_usd, netting_bar_usd, netting_vol_ratio, netting_exit_fee_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.id, trade.ticker, trade.side, trade.size, trade.price, trade.reason, trade.timestamp,
                 trade.config_fingerprint, close_fee,
                 trade.netting_improvement_usd, trade.netting_bar_usd, trade.netting_vol_ratio,
                 trade.netting_exit_fee_usd),
            )
        # History-push hook - see open_position's own comment above. Placed
        # after the `if not pos: return None` no-op guard at the top of
        # this function.
        history_push.mark_history_changed()
        return trade

    def close_all_positions(
        self, latest_prices: dict[str, float], reason: str,
        latest_asks: dict[str, float] | None = None,
    ) -> list[Trade]:
        """Flattens every currently-open position at once - the paper-mode
        half of POST /api/trading/flatten-all (2026-08-23 gap-check
        finding: no "get flat immediately" path existed at all). Loops a
        snapshot of the ticker list (not self.positions directly, since
        close_position mutates it mid-iteration) and closes each at its
        latest known price, falling back to the position's own entry_price
        when this tick has no fresh quote for it - same "don't guess, but
        don't refuse to flatten either" tradeoff check_pending_fills makes
        elsewhere, except a manual flatten-everything action should never
        silently skip a position just because a quote is momentarily
        missing. Direct precedent for the loop shape:
        services/exits/position_netting.py's own review().

        latest_asks (2026-09-04): a sale is struck on the side of the book
        the position is sold INTO - yes_bid for a YES position, yes_ask for a
        NO one, since the NO bid is (1 - yes_ask). Pricing both sides off
        latest_prices (yes_bid) valued a NO position at the NO *ask* and, on
        an empty yes book, paid $1.00/contract as if the market had settled
        NO. kalshi_fees.forced_exit_quote, not sellable_quote, because this
        path must never refuse to flatten: an unsellable book resolves to
        zero proceeds rather than a fabricated payout. Omitting it keeps the
        old both-sides-off-the-bid behavior only for a caller that genuinely
        has no ask dict, which no production caller does."""
        closed = []
        for ticker in list(self.positions):
            pos = self.positions[ticker]
            price = latest_prices.get(ticker, pos.entry_price)
            if latest_asks is not None:
                price = kalshi_fees.forced_exit_quote(
                    pos.side, price, latest_asks.get(ticker), unknown_fallback=pos.entry_price,
                )
            trade = self.close_position(ticker, price, reason)
            if trade is not None:
                closed.append(trade)
        return closed

    def correct_erroneous_close(self, trade_id: str, corrected_price: float | None = None) -> dict | None:
        """Reverse a specific CLOSE trade's fabricated bankroll impact,
        optionally re-crediting a corrected value, and flag it `excluded` -
        the remediation half of the 2026-08-17 stop-loss price-
        corroboration fix (see strategy_engine.check_exits and
        market_history.recent_price). A confirmed-bad close (a stop-loss
        that fired on a fabricated exit_price - see the `excluded` column's
        own comment above) doesn't just leave one wrong row: it left real
        (paper) bankroll wrong, and every trade-level win-rate/P&L
        statistic downstream of `trades` counted it.

        Two steps, both optional-but-composable:

        1. ALWAYS: reverse exactly the fabricated close's own `cash_back` -
           undoes what the bad exit_price actually did to bankroll, no
           assumption involved, since that number is read straight off the
           row itself.
        2. IF `corrected_price` is given: credit what SHOULD have been paid
           at that price instead, using the same side-aware cash math and a
           freshly-computed real fee (not the stale one from the bad
           close). The intended input is
           `market_history.recent_price(ticker, ..., as_of=<the close's own
           timestamp>)` - the same independent, already-trusted corroboration
           source the going-forward fix uses, so the correction and the
           prevention share one definition of "what the price actually
           was." Deliberately NOT "what did the market eventually settle
           at" (that requires external confirmation this function has no
           way to verify on its own) - just "what was the last price this
           app's own trusted data actually recorded," which is directly
           computable and requires no assumption about the eventual
           outcome.

        Guarded to ONLY ever touch a `closed:` trade (never an entry) and
        only a trade that hasn't already been corrected, so this is safe to
        re-run. Returns None (no-op) if the trade doesn't exist, isn't a
        close, or is already excluded."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ticker, side, size, price, fee, reason, excluded FROM trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            if row is None:
                return None
            ticker, side, size, bad_price, fee, reason, already_excluded = row
            if already_excluded or not reason.startswith("closed:"):
                return None
            fee = fee or 0.0
            bad_gross = size * kalshi_fees.unit_cost(side, bad_price)
            reversed_cash_back = bad_gross - fee
            self.bankroll -= reversed_cash_back

            corrected_credit = 0.0
            if corrected_price is not None:
                good_gross = size * kalshi_fees.unit_cost(side, corrected_price)
                good_fee = kalshi_fees.taker_fee(size, corrected_price, ticker=ticker)
                corrected_credit = good_gross - good_fee
                self.bankroll += corrected_credit

            conn.execute("UPDATE broker_meta SET bankroll = ? WHERE id = 1", (self.bankroll,))
            conn.execute("UPDATE trades SET excluded = 1 WHERE id = ?", (trade_id,))
        # Also flip the in-memory copy - trade_analytics.build_trade_history
        # (and therefore the dashboard's History table, /api/state's
        # recent_trades, and every P&L summary main.py computes) reads
        # self.trade_log directly, not a fresh SQL query, so without this
        # the correction would be invisible until the next process restart.
        for t in self.trade_log:
            if t.id == trade_id:
                t.excluded = True
                break
        return {
            "trade_id": trade_id, "ticker": ticker,
            "reversed_cash_back": round(reversed_cash_back, 2),
            "corrected_credit": round(corrected_credit, 2),
            "bankroll_after": round(self.bankroll, 2),
        }

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

    def trades_since(self, after: float | None) -> list[Trade]:
        """Every real ENTRY (a PaperBroker.open_position row) with
        timestamp > after - the markout-capture sweep's own read of 'what
        entries exist to capture markouts for' (Task 4,
        strategy-edge-gate-implementation.md). open_position and
        close_position write into this exact same trades table with no
        type/action discriminator column, so close rows are filtered out
        here via the reason column's own established convention -
        close_position always prefixes reason with "closed: "
        (services/history/trade_analytics.py's build_trade_history and
        this module's own correct_erroneous_close both already depend on
        the identical convention). Without this filter a close row's own
        exit price/timestamp would be fed into the markout sweep as a
        phantom entry (adversarial review Finding F4). Unlike
        count_trade_range/clear_trade_range, this returns full rows, not
        just a count."""
        where, params = self._trade_range_where(before=None, after=after)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, "
                f"fee, signal_seen_at FROM trades {where} ORDER BY timestamp ASC", params,
            ).fetchall()
        return [Trade(*row) for row in rows if not row[5].startswith("closed:")]

    def count_trade_range(self, before: float | None = None, after: float | None = None) -> int:
        """Danger Zone preview support (2026-08-16 direct request: purge a
        noisy tuning stretch without losing valid history on either side of
        it). Scoped to the trades table only - never positions/bankroll/
        pending_orders, which are CURRENT live state, not history; a range
        purge must never orphan an open position's own accounting."""
        where, params = self._trade_range_where(before, after)
        with self._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM trades {where}", params).fetchone()[0]

    def clear_trade_range(self, before: float | None = None, after: float | None = None) -> int:
        """Deletes trade-log rows (closed history) in (after, before] from
        both the DB and the in-memory trade_log - never touches positions/
        bankroll/pending_orders/last_trade_time, so an in-range purge can't
        silently break a currently-open position's own state."""
        where, params = self._trade_range_where(before, after)
        with self._connect() as conn:
            cur = conn.execute(f"DELETE FROM trades {where}", params)
            deleted = cur.rowcount
        self.trade_log = [
            t for t in self.trade_log
            if not ((after is None or t.timestamp > after) and (before is None or t.timestamp <= before))
        ]
        return deleted

    @staticmethod
    def _trade_range_where(before: float | None, after: float | None) -> tuple[str, list]:
        clauses, params = [], []
        if after is not None:
            clauses.append("timestamp > ?")
            params.append(after)
        if before is not None:
            clauses.append("timestamp <= ?")
            params.append(before)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

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
        return pos.size * kalshi_fees.unit_cost(pos.side, pos.entry_price)

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
