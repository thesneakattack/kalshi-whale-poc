"""Single-owner retry queue for whale candidates whose market lookup
failed transiently (realtime data-plane remediation plan, P2 Task 12;
root-cause report C5 / I12 review R7).

Task 11 (H4 fix) stopped a transient market-lookup failure from
permanently short-circuiting a trade via the seen dedupe ring, but left
the trade with no durable path back to evaluation beyond "wait for the
same trade_id to naturally reappear in a later trade tape poll" - not
guaranteed, and not bounded. This module is that durable path: an
in-memory pending queue with exponential backoff, run from exactly one
place (main.py's tick loop, Task 13) so nothing else can mutate it
concurrently (R7's "single mutator" requirement) - a retry must:

  (a) be the only mutator of pending state besides the reader,
  (b) inherit the critical_whale caller class so it doesn't silently
      fall into "other" in REST-latency accounting, and
  (c) abandon after a bounded budget (>=72s survives a 60s outage per
      I12's recovery-sim finding) rather than retry forever.

Recovery claims the trade_id in candidate_ledger (Task 10's gate) so a
recovered trade can never be double-evaluated if it also naturally
reappears in a later trade tape poll before this queue gets to it -
evaluate() itself is deliberately NOT this module's job; recovery only
proves the market now resolves, the same "candidate_pending" ->
"terminal_evaluated" transition Task 11's own lifecycle vocabulary
describes, and the actual evaluation still belongs to whichever normal
consumer path (P4's market queue) next sees this trade_id resolve.
"""
import time

from services import candidate_ledger, http_client

# Sums to >=72s (I12 R7's minimum survivable outage window): 0.5+1+2+4+8+16+30+30 = 91.5s.
_BACKOFF_SCHEDULE_SEC = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0)

_pending: dict[str, dict] = {}  # trade_id -> {"trade": ..., "attempts": int, "next_at": float}

# Window counters (services/observability/observability.py's
# _flatten_candidate_retry, reset by maybe_capture alongside
# loop_watchdog's own window - same "no evidence, no rows" shape). Not
# lifetime totals: a window resets to zero, _pending's own current
# length (pending_count()) is the one gauge read live instead.
_window_retried = 0
_window_recovered = 0
_window_abandoned = 0


def reset_window() -> None:
    global _window_retried, _window_recovered, _window_abandoned
    _window_retried = 0
    _window_recovered = 0
    _window_abandoned = 0


def snapshot() -> dict:
    return {
        "pending": len(_pending),
        "retried": _window_retried,
        "recovered": _window_recovered,
        "abandoned": _window_abandoned,
    }


def enqueue(trade: dict, *, failure: Exception, now: float | None = None) -> None:
    """Owns the sole insertion path into _pending. A trade_id already
    queued is left untouched (not reset/restarted) - the reader (Task 11's
    _resolve_failed_tickers path) is the only other thing that could ever
    call this for the same trade_id again before it resolves, and doing
    nothing preserves the original backoff schedule rather than letting a
    noisy failure window keep resetting it to attempt 0."""
    now = time.time() if now is None else now
    trade_id = trade.get("trade_id")
    if not trade_id or trade_id in _pending:
        return
    _pending[trade_id] = {"trade": trade, "attempts": 0, "next_at": now + _BACKOFF_SCHEDULE_SEC[0], "failure": str(failure)}


def pending_count() -> int:
    return len(_pending)


async def run_pending(client, *, now: float | None = None) -> dict:
    """The sole owner of retrying + removing entries from _pending. Call
    from exactly one place (main.py's tick loop) - a second concurrent
    caller would violate R7's single-mutator requirement (two callers
    could both retry the same trade_id in the same tick, double-counting
    attempts against the backoff budget)."""
    global _window_retried, _window_recovered, _window_abandoned
    now = time.time() if now is None else now
    retried = recovered = abandoned = 0
    for trade_id in list(_pending):
        entry = _pending[trade_id]
        if now < entry["next_at"]:
            continue
        retried += 1
        ticker = entry["trade"].get("ticker")
        try:
            with http_client.caller_class("critical_whale"):
                result = await client.get_markets_by_tickers([ticker])
            if not result.get(ticker):
                raise KeyError(f"market still missing for {ticker}")
            # Recovered: claim the trade_id so a natural re-presentation of
            # this same print elsewhere can never also evaluate it.
            candidate_ledger.claim(trade_id, ticker=ticker, now=now)
            recovered += 1
            del _pending[trade_id]
        except Exception:
            entry["attempts"] += 1
            if entry["attempts"] >= len(_BACKOFF_SCHEDULE_SEC):
                abandoned += 1
                del _pending[trade_id]
                continue
            entry["next_at"] = now + _BACKOFF_SCHEDULE_SEC[entry["attempts"]]
    _window_retried += retried
    _window_recovered += recovered
    _window_abandoned += abandoned
    return {"retried": retried, "recovered": recovered, "abandoned": abandoned}
