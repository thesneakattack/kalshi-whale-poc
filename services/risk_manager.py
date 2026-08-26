"""
Guardrails between "the strategy wants to trade" and "the trade actually
happens". Nothing in this file knows about signals or strategy logic —
it only ever asks: is this trade within limits, and has the bankroll
dropped past the daily loss cap.

day_start_bankroll and the halt state persist to data/risk_state.db.
Without this, a restart would keep services/paper_broker.py's recovered
bankroll but reset this file's loss baseline to config/settings.yaml's
starting_bankroll every time — either mismeasuring today's loss against a
stale number, or silently un-halting a kill switch that had actually
tripped. A plain restart resumes; reset_day() (called from POST
/api/reset) is the only thing that clears it - or, as of the automatic
rollover below, a genuinely new calendar day.

Real bug found live (2026-08-10): "daily loss limit" had never actually
been daily - reset_day() was only ever called manually (POST /api/reset,
or the two /api/risk/halt|resume-style routes), with nothing rolling the
baseline over at a real day boundary. A second independent kill switch
(the now-removed Market-Native strategy's own, 2026-08-22) tripped once,
then stayed permanently halted for 55+ hours with zero automatic recovery
path - the strategy looked "stalled" from the outside, but it was
actually just correctly, silently obeying a kill switch nothing had ever
cleared. check_daily_loss() now rolls the day over
automatically (once per real UTC calendar day, not once per tick) before
doing its own check - every existing call site gets this for free with no
new call needed.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "risk_state.db"


def _today(now: float | None = None) -> str:
    _ = now  # temporary, live CI cache-reuse verification for PR #25
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # Same idiom as services/signal_log.py/config_performance.py -
    # CREATE TABLE IF NOT EXISTS alone doesn't add a column to an existing
    # table with existing rows.
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
        CREATE TABLE IF NOT EXISTS risk_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            day_start_bankroll REAL NOT NULL,
            halted INTEGER NOT NULL,
            halt_reason TEXT
        )
        """
    )
    # Nullable on purpose - a pre-existing row from before this column
    # existed has no real "day" to compare against yet; _load() below seeds
    # it from today's date on first read rather than assuming a rollover is
    # due immediately (that decision belongs to a deliberate one-time
    # /api/risk/resume-style action, not an automatic migration side effect).
    _add_column_if_missing(conn, "risk_meta", "day_start_date", "TEXT")
    return conn


class RiskManager:
    def __init__(
        self, starting_bankroll: float, max_daily_loss_pct: float, kill_switch_enabled: bool,
        db_path: Path | None = None, max_total_exposure_pct: float | None = None,
    ):
        # Same per-instance db_path pattern as services/paper_broker.py's
        # PaperBroker - defaults to the module-level DB_PATH (resolved at
        # call time, so existing tests' monkeypatch.setattr(rm, "DB_PATH",
        # ...) keeps working), or pass an explicit path to run a second,
        # independent risk tracker without colliding with another instance's
        # risk_meta row.
        self.db_path = db_path or DB_PATH
        self.starting_bankroll = starting_bankroll
        self.max_daily_loss_pct = max_daily_loss_pct
        self.kill_switch_enabled = kill_switch_enabled
        # None (default) = no portfolio-wide exposure cap - same "ships
        # fully built, opt-in" precedent as max_open_positions_per_series/
        # kelly_fraction_of_cap elsewhere in this app. When set, caps total
        # dollar exposure across every currently-open position (see
        # check_total_exposure) - a gap max_trade_size alone can't close,
        # since several individually-small positions can still add up to
        # far more aggregate risk than one trade's own cap implies.
        self.max_total_exposure_pct = max_total_exposure_pct

        with self._connect() as conn:
            row = conn.execute(
                "SELECT day_start_bankroll, halted, halt_reason, day_start_date FROM risk_meta WHERE id = 1"
            ).fetchone()
            if row is None:
                self.day_start_bankroll = starting_bankroll
                self.halted = False
                self.halt_reason = None
                self.day_start_date = _today()
                conn.execute(
                    "INSERT INTO risk_meta (id, day_start_bankroll, halted, halt_reason, day_start_date) "
                    "VALUES (1, ?, 0, NULL, ?)",
                    (self.day_start_bankroll, self.day_start_date),
                )
            else:
                self.day_start_bankroll, halted, self.halt_reason, day_start_date = row
                self.halted = bool(halted)
                # A row from before day_start_date existed - seed it from
                # today rather than treating a pre-existing halt as due for
                # an immediate silent rollover (see the module docstring).
                self.day_start_date = day_start_date or _today()

    def _connect(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    def _persist(self):
        with self._connect() as conn:
            conn.execute(
                "UPDATE risk_meta SET day_start_bankroll = ?, halted = ?, halt_reason = ?, day_start_date = ? "
                "WHERE id = 1",
                (self.day_start_bankroll, int(self.halted), self.halt_reason, self.day_start_date),
            )

    def max_trade_size(self, bankroll: float, max_position_pct: float) -> float:
        return round(bankroll * max_position_pct, 2)

    def check_total_exposure(self, current_exposure: float, prospective_cost: float, bankroll: float) -> bool:
        """True if adding prospective_cost to current_exposure (the sum of
        every currently-open position's cost_basis - see
        PaperBroker.cost_basis) would stay within max_total_exposure_pct of
        bankroll. Always True when max_total_exposure_pct is unset (the
        opt-in default) - stateless and side-effect-free, same style as
        max_trade_size above, deliberately not a hard gate baked into
        open_position's own signature so a caller with no RiskManager
        wired in (or one that hasn't turned this on) sees zero behavior
        change."""
        if self.max_total_exposure_pct is None:
            return True
        return (current_exposure + prospective_cost) <= bankroll * self.max_total_exposure_pct

    def check_daily_loss(self, current_bankroll: float, now: float | None = None) -> bool:
        """Returns True if trading should continue; flips the kill switch if
        not. Runs the automatic daily rollover first (see module docstring)
        - a halt from a stale, day-old baseline gets cleared here the same
        way every other part of this state already self-manages, not left
        for a human to notice and clear by hand."""
        self._maybe_rollover_day(current_bankroll, now)
        if not self.kill_switch_enabled or self.halted:
            return not self.halted
        loss_pct = (self.day_start_bankroll - current_bankroll) / self.day_start_bankroll
        if loss_pct >= self.max_daily_loss_pct:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: -{loss_pct:.1%}"
            self._persist()
            return False
        return True

    def _maybe_rollover_day(self, current_bankroll: float, now: float | None = None) -> bool:
        """Rolls the baseline (and any halt) over exactly once per real UTC
        calendar-date change, not once per tick - direct fix for a real bug
        found live (2026-08-10): a kill switch tripped once and then stayed
        permanently halted for 55+ hours, since nothing had ever called
        reset_day() automatically. Returns True if a rollover happened."""
        if _today(now) != self.day_start_date:
            self.reset_day(current_bankroll, now)
            return True
        return False

    def reset_day(self, current_bankroll: float, now: float | None = None):
        self.day_start_bankroll = current_bankroll
        self.halted = False
        self.halt_reason = None
        self.day_start_date = _today(now)
        self._persist()

    def manual_halt(self, reason: str = "Manually halted"):
        self.halted = True
        self.halt_reason = reason
        self._persist()

    def resume(self):
        self.halted = False
        self.halt_reason = None
        self._persist()
