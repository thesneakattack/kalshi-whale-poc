"""Pure, allocation-free classification of a raw trade print as whale-sized
or not - the gate the realtime data-plane investigation demanded exist
before any reader-side filtering ships (design spec Sec 2). Must stay well
under 20us/call; this module's own test pins that budget so a future
change that regresses it fails CI, not a production incident.

Realtime data-plane remediation plan, P0 Task 3. Deliberately shadow-mode
only where it's wired in today (services/kalshi/websocket.py's reader) -
Task 17 flips it to actually filtering the market queue."""
from services.kalshi.contracts import trade as trade_contract
from services.whalewatchers.kalshi_trade_tape import min_contracts_for as _series_min_contracts_for


def passes(trade: dict, *, min_contracts: int) -> bool:
    """True if this raw trade print's contract count clears min_contracts.
    A trade with an unparseable/missing count fails open toward the caller
    (returns False here, but callers must fall open on an *exception*, not
    on a legitimate False - see services/kalshi/websocket.py's reader for
    the fall-open wrapper around this call)."""
    count = trade_contract.trade_contract_count(trade)
    return count is not None and count >= min_contracts


def min_contracts_for(ticker: str, cfg: dict) -> float:
    """This series' whale threshold, or the global default - a thin
    wrapper around kalshi_trade_tape's own per-series threshold function so
    the reader-side gate and the whale-watcher provider's own prescan/real
    gate can never disagree about what counts as whale-sized for a given
    ticker (one definition, reused, not re-derived)."""
    wwk_cfg = cfg.get("whale_watcher_kalshi") or {}
    return _series_min_contracts_for(ticker, wwk_cfg)
