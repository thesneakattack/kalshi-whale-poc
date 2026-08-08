"""
The only file that decides *whether* a whale signal becomes a trade.
Swap this out to change strategy without touching the broker, risk
manager, or data sources.
"""
from services import signal_log
from services.whale_simulator import WhaleSignal
from services.paper_broker import PaperBroker
from services.risk_manager import RiskManager


class FollowTheWhaleStrategy:
    def __init__(self, broker: PaperBroker, risk: RiskManager):
        self.broker = broker
        self.risk = risk

    def evaluate(self, signal: WhaleSignal, cfg: dict, is_live: bool | None = None) -> dict:
        """Returns a decision dict describing what happened (trade or skip + why).
        is_live comes from main.py's milestone/live-data lookup (see
        _fetch_live_status) - None/False means "not currently live" (either
        confirmed finished/scheduled, or no live-status data for this market
        at all, e.g. it's not a live-event-style market)."""
        strat_cfg = cfg["strategy"]

        if not self.risk.check_daily_loss(self.broker.equity({})):
            return self._skip(signal, f"halted: {self.risk.halt_reason}")

        if strat_cfg.get("live_markets_only") and not is_live:
            return self._skip(signal, "market is not currently live")

        if signal.confidence < strat_cfg["entry_threshold"]:
            return self._skip(signal, f"confidence {signal.confidence} below threshold")

        # Avoid this whale's picks on markets like this one once they've proven
        # unreliable here — but only once there's enough resolved history to
        # trust, so one unlucky result doesn't blacklist a whole category.
        min_resolved = strat_cfg.get("min_resolved_for_whale_filter", 5)
        min_winrate = strat_cfg.get("min_whale_winrate_pct", 40)
        record = signal_log.series_stats(signal.ticker, days=30)
        if record["resolved"] >= min_resolved and record["win_rate"] is not None and record["win_rate"] < min_winrate:
            return self._skip(
                signal,
                f'whale win rate for "{record["series"]}"-type markets is {record["win_rate"]:.0f}% '
                f'over {record["resolved"]} resolved signals (below {min_winrate}% minimum) — avoiding',
            )

        if not self.broker.can_trade(signal.ticker, strat_cfg["cooldown_sec"]):
            return self._skip(signal, "cooldown active for this market")

        max_size = self.risk.max_trade_size(self.broker.bankroll, strat_cfg["max_position_pct"])
        contracts = int(max_size / signal.price) if signal.price > 0 else 0
        if contracts <= 0:
            return self._skip(signal, "position size rounds to zero")

        trade = self.broker.open_position(
            ticker=signal.ticker,
            side=signal.side,
            size=contracts,
            price=signal.price,
            reason=f"whale print {signal.size} @ {signal.price} (conf {signal.confidence})",
        )
        return {
            "action": "trade",
            "signal": signal.to_dict(),
            "trade": trade.to_dict(),
        }

    def _skip(self, signal: WhaleSignal, reason: str) -> dict:
        return {"action": "skip", "signal": signal.to_dict(), "reason": reason}
