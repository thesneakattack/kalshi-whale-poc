"""
Shadow mode: runs the exact same follow-the-whale decision gates as
strategy_engine.py, but scoped to what a REAL account's bankroll would
allow instead of the paper broker's, and only ever logs an intended trade —
never calls kalshi_account_client.create_order. This is the step the README
has called a prerequisite to live trading since before any of this
project's git history: it answers "what would this strategy actually have
done with real capital" without a single real dollar at risk.

Active whenever config/settings.yaml's `mode` is "shadow" or "live" (once
you're past pure paper testing, shadow logging keeps running alongside
everything else - including once mode is "live" too, so shadow-vs-actual
stays comparable). A no-op otherwise.

Uses the connected real account's balance as the reference bankroll when one
is connected; falls back to risk.starting_bankroll from config, clearly
flagged as such on every logged row, so this is still meaningfully testable
without a real Kalshi account connected.

Deliberately its own small module rather than reusing PaperBroker/
RiskManager: shadow mode's cooldown and daily-loss-halt state need to be
independent of paper trading's (a different reference bankroll would
otherwise silently share or corrupt paper's own kill-switch state), and
persisting is a single-row/single-table concern each keeps to itself
elsewhere in this codebase too (see services/risk_manager.py,
services/paper_broker.py).
"""
import sqlite3
import time
import uuid
from pathlib import Path

from services import signal_log

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "shadow_mode.db"


def _today(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_trades (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            price REAL NOT NULL,
            reason TEXT NOT NULL,
            reference_bankroll REAL NOT NULL,
            bankroll_source TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_risk (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            day_start_bankroll REAL NOT NULL,
            halted INTEGER NOT NULL,
            halt_reason TEXT
        )
        """
    )
    # Real bug found live (2026-08-10, same class as services/risk_manager.py's
    # own fix, same session): shadow mode's "daily loss limit" was never
    # actually daily either - reset_day() was only ever called from a
    # manual clear(), so once tripped it stayed halted forever (confirmed
    # live: halted=1, "-99.7%", dormant only because mode was "paper" at
    # the time - the instant mode flips to "shadow"/"live" this would have
    # silently blocked every shadow evaluation with no recovery path).
    _add_column_if_missing(conn, "shadow_risk", "day_start_date", "TEXT")
    return conn


class ShadowTrader:
    def __init__(self, default_bankroll: float):
        self.last_trade_time: dict[str, float] = {}
        with _connect() as conn:
            row = conn.execute(
                "SELECT day_start_bankroll, halted, halt_reason, day_start_date FROM shadow_risk WHERE id = 1"
            ).fetchone()
            if row is None:
                self.day_start_bankroll = default_bankroll
                self.halted = False
                self.halt_reason = None
                self.day_start_date = _today()
                conn.execute(
                    "INSERT INTO shadow_risk (id, day_start_bankroll, halted, halt_reason, day_start_date) "
                    "VALUES (1, ?, 0, NULL, ?)",
                    (self.day_start_bankroll, self.day_start_date),
                )
            else:
                self.day_start_bankroll, halted, self.halt_reason, day_start_date = row
                self.halted = bool(halted)
                # Seed from today rather than treating a pre-existing halt
                # as due for an immediate silent rollover - same migration
                # safety as risk_manager.py's own fix.
                self.day_start_date = day_start_date or _today()

    def _persist_risk(self):
        with _connect() as conn:
            conn.execute(
                "UPDATE shadow_risk SET day_start_bankroll = ?, halted = ?, halt_reason = ?, day_start_date = ? "
                "WHERE id = 1",
                (self.day_start_bankroll, int(self.halted), self.halt_reason, self.day_start_date),
            )

    def can_trade(self, ticker: str, cooldown_sec: float) -> bool:
        last = self.last_trade_time.get(ticker)
        return last is None or (time.time() - last) >= cooldown_sec

    def check_daily_loss(
        self, current_bankroll: float, max_daily_loss_pct: float, kill_switch_enabled: bool, now: float | None = None,
    ) -> bool:
        self._maybe_rollover_day(current_bankroll, now)
        if not kill_switch_enabled or self.halted:
            return not self.halted
        if not self.day_start_bankroll:
            return True
        loss_pct = (self.day_start_bankroll - current_bankroll) / self.day_start_bankroll
        if loss_pct >= max_daily_loss_pct:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: -{loss_pct:.1%}"
            self._persist_risk()
            return False
        return True

    def _maybe_rollover_day(self, current_bankroll: float, now: float | None = None) -> bool:
        """Same automatic-rollover fix as services/risk_manager.py's own -
        rolls the baseline (and any halt) over exactly once per real UTC
        calendar-date change, not once per tick."""
        if _today(now) != self.day_start_date:
            self.reset_day(current_bankroll, now)
            return True
        return False

    def reset_day(self, current_bankroll: float, now: float | None = None):
        self.day_start_bankroll = current_bankroll
        self.halted = False
        self.halt_reason = None
        self.day_start_date = _today(now)
        self._persist_risk()

    def resume(self):
        """Un-halt without rebasing day_start_bankroll or touching logged
        shadow trades - the lighter-weight action, mirroring services/
        risk_manager.RiskManager.resume(). clear() above stays the
        destructive full-wipe action; this is for "the halt itself was
        stale/wrong, the trade history and baseline are still fine.\""""
        self.halted = False
        self.halt_reason = None
        self._persist_risk()

    def clear(self, current_bankroll: float):
        """Full wipe: deletes every logged shadow trade and resets the daily-loss
        baseline/halt, same as a fresh ShadowTrader. Unlike reset_day (called
        automatically alongside the paper broker's own reset), this is only ever
        triggered deliberately - it destroys the long-run shadow track record,
        which normally survives paper resets on purpose."""
        with _connect() as conn:
            conn.execute("DELETE FROM shadow_trades")
        self.last_trade_time = {}
        self.reset_day(current_bankroll)

    def evaluate(
        self, signal, cfg: dict, reference_bankroll: float, bankroll_source: str,
        is_live: bool | None = None, market_results: dict | None = None,
    ) -> dict | None:
        """Mirrors FollowTheWhaleStrategy.evaluate's gates (same order, same
        thresholds) but against reference_bankroll instead of the paper
        broker's, and only ever logs - never executes. Returns the logged
        row, or None if the signal didn't clear the bar - only intended
        trades are logged, not every skip, matching the roadmap's own
        wording ("logs intended real trades")."""
        strat_cfg = cfg["strategy"]
        risk_cfg = cfg["risk"]

        if not self.check_daily_loss(reference_bankroll, risk_cfg["max_daily_loss_pct"], risk_cfg["kill_switch_enabled"]):
            return None
        result = ((market_results or {}).get(signal.ticker) or "").strip().lower()
        if result in ("yes", "no"):
            return None
        if strat_cfg.get("live_markets_only") and not is_live:
            return None
        excluded_series = strat_cfg.get("excluded_series") or []
        if signal_log.series_of(signal.ticker) in excluded_series:
            return None
        if signal.confidence < strat_cfg["entry_threshold"]:
            return None

        min_resolved = strat_cfg.get("min_resolved_for_whale_filter", 5)
        min_winrate = strat_cfg.get("min_whale_winrate_pct", 40)
        record = signal_log.series_stats(signal.ticker, days=30)
        if record["resolved"] >= min_resolved and record["win_rate"] is not None and record["win_rate"] < min_winrate:
            return None

        if not self.can_trade(signal.ticker, strat_cfg["cooldown_sec"]):
            return None

        max_size = reference_bankroll * strat_cfg["max_position_pct"]
        # signal.price is always the YES price - a NO print's real
        # per-contract cost is (1 - price), same fix as
        # strategy_engine.evaluate(); shadow mode should size trades the
        # same way the real paper broker would.
        unit_cost = signal.price if signal.side == "yes" else (1 - signal.price)
        contracts = int(max_size / unit_cost) if unit_cost > 0 else 0
        if contracts <= 0:
            return None

        row = {
            "id": str(uuid.uuid4())[:8],
            "ticker": signal.ticker,
            "side": signal.side,
            "size": contracts,
            "price": signal.price,
            "reason": f"whale print {signal.size} @ {signal.price} (conf {signal.confidence})",
            "reference_bankroll": round(reference_bankroll, 2),
            "bankroll_source": bankroll_source,
            "timestamp": time.time(),
        }
        with _connect() as conn:
            conn.execute(
                "INSERT INTO shadow_trades "
                "(id, ticker, side, size, price, reason, reference_bankroll, bankroll_source, timestamp) "
                "VALUES (:id, :ticker, :side, :size, :price, :reason, :reference_bankroll, :bankroll_source, :timestamp)",
                row,
            )
        self.last_trade_time[signal.ticker] = row["timestamp"]
        return row

    def recent(self, limit: int = 25) -> list[dict]:
        cols = ["id", "ticker", "side", "size", "price", "reason", "reference_bankroll", "bankroll_source", "timestamp"]
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM shadow_trades ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def stats(self) -> dict:
        with _connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM shadow_trades").fetchone()[0]
        return {"total_shadow_trades": count, "halted": self.halted, "halt_reason": self.halt_reason}
