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
/api/reset) is the only thing that clears it.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "risk_state.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
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
    return conn


class RiskManager:
    def __init__(
        self, starting_bankroll: float, max_daily_loss_pct: float, kill_switch_enabled: bool,
        db_path: Path | None = None,
    ):
        # Same per-instance db_path pattern as services/paper_broker.py's
        # PaperBroker - defaults to the module-level DB_PATH (resolved at
        # call time, so existing tests' monkeypatch.setattr(rm, "DB_PATH",
        # ...) keeps working), or pass an explicit path to run a second,
        # independent risk tracker (e.g. services/market_strategy.py's own
        # kill switch) without colliding with another instance's risk_meta row.
        self.db_path = db_path or DB_PATH
        self.starting_bankroll = starting_bankroll
        self.max_daily_loss_pct = max_daily_loss_pct
        self.kill_switch_enabled = kill_switch_enabled

        with self._connect() as conn:
            row = conn.execute(
                "SELECT day_start_bankroll, halted, halt_reason FROM risk_meta WHERE id = 1"
            ).fetchone()
            if row is None:
                self.day_start_bankroll = starting_bankroll
                self.halted = False
                self.halt_reason = None
                conn.execute(
                    "INSERT INTO risk_meta (id, day_start_bankroll, halted, halt_reason) VALUES (1, ?, 0, NULL)",
                    (self.day_start_bankroll,),
                )
            else:
                self.day_start_bankroll, halted, self.halt_reason = row
                self.halted = bool(halted)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    def _persist(self):
        with self._connect() as conn:
            conn.execute(
                "UPDATE risk_meta SET day_start_bankroll = ?, halted = ?, halt_reason = ? WHERE id = 1",
                (self.day_start_bankroll, int(self.halted), self.halt_reason),
            )

    def max_trade_size(self, bankroll: float, max_position_pct: float) -> float:
        return round(bankroll * max_position_pct, 2)

    def check_daily_loss(self, current_bankroll: float) -> bool:
        """Returns True if trading should continue; flips the kill switch if not."""
        if not self.kill_switch_enabled or self.halted:
            return not self.halted
        loss_pct = (self.day_start_bankroll - current_bankroll) / self.day_start_bankroll
        if loss_pct >= self.max_daily_loss_pct:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: -{loss_pct:.1%}"
            self._persist()
            return False
        return True

    def reset_day(self, current_bankroll: float):
        self.day_start_bankroll = current_bankroll
        self.halted = False
        self.halt_reason = None
        self._persist()

    def manual_halt(self, reason: str = "Manually halted"):
        self.halted = True
        self.halt_reason = reason
        self._persist()

    def resume(self):
        self.halted = False
        self.halt_reason = None
        self._persist()
