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

Recovery scores the trade through the active whale-watcher provider's own
scoring pipeline (provider.score_recovered_trade - see
services/whalewatchers/base.py) and, when that produces a signal, routes
it through the exact same evaluation path a first-try signal uses
(handle_signal, i.e. services/whale_stream/decision_bridge._handle_signal
in production) - not a separate, drifting copy of that logic living here.

Originally (P2 Task 12) this module called candidate_ledger.claim()
itself on every successful resolution and reported it as "recovered"
without ever evaluating anything - a real bug (code-review finding #1,
/code-review high pass against PR #23): the trade_id was permanently
claimed, blocking any later natural re-presentation, while the recovery
was reported as a success and no trading decision was ever made. Fixed by
never claiming here at all - handle_signal (_handle_signal) already
performs its own candidate_ledger.claim() as an atomic dedup gate
immediately before evaluating, so the claim and the evaluation now happen
inside one call, never one without the other. A resolution that produces
no signal (the market resolved, but the trade fails some other gate - see
score_recovered_trade's own docstring) still counts as "recovered" here
(the market DID resolve) but claims nothing, exactly as if the trade had
never been queued in the first place.

provider and handle_signal are accepted as parameters rather than
imported directly: importing services.app_state (or decision_bridge,
which itself imports app_state) here would create a circular import back
through services.whalewatchers.kalshi_trade_tape, which is this module's
own caller (Task 12's enqueue()). main.py's tick loop already holds both
(whale_provider, decision_bridge._handle_signal) and passes them straight
through.
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


async def run_pending(
    client, provider, handle_signal, cfg: dict, market_results: dict,
    config_fp: str, tick_now: float, *, now: float | None = None,
) -> dict:
    """The sole owner of retrying + removing entries from _pending. Call
    from exactly one place (main.py's tick loop) - a second concurrent
    caller would violate R7's single-mutator requirement (two callers
    could both retry the same trade_id in the same tick, double-counting
    attempts against the backoff budget).

    provider: the active whale-watcher provider (services.app_state.
    whale_provider in production) - provides score_recovered_trade (see
    services/whalewatchers/base.py). handle_signal: decision_bridge.
    _handle_signal in production - given a WhaleSignal, performs its own
    candidate_ledger.claim() and, if newly claimed, strategy.evaluate() and
    every side effect a first-try signal gets (signal feed, logging,
    decision feed, broadcast). cfg/market_results/config_fp/tick_now are
    forwarded to handle_signal unchanged - the same four values main.py's
    tick loop already passes to its own direct _handle_signal calls."""
    global _window_retried, _window_recovered, _window_abandoned
    now = time.time() if now is None else now
    retried = recovered = abandoned = 0
    for trade_id in list(_pending):
        entry = _pending[trade_id]
        if now < entry["next_at"]:
            continue
        retried += 1
        trade = entry["trade"]
        ticker = trade.get("ticker")
        try:
            with http_client.caller_class("critical_whale"):
                result = await client.get_markets_by_tickers([ticker])
            market = result.get(ticker)
            if not market:
                raise KeyError(f"market still missing for {ticker}")
        except Exception:
            # Only a market-lookup failure is retried against the backoff
            # budget - the market genuinely still isn't resolvable yet (or
            # the request itself failed), the same transient condition this
            # queue exists to survive.
            entry["attempts"] += 1
            if entry["attempts"] >= len(_BACKOFF_SCHEDULE_SEC):
                abandoned += 1
                del _pending[trade_id]
                continue
            entry["next_at"] = now + _BACKOFF_SCHEDULE_SEC[entry["attempts"]]
            continue
        # Recovered: the market now resolves. Score this trade through the
        # provider's own pipeline - the same one a first-try trade goes
        # through - and, if it still produces a signal, evaluate it through
        # the exact same path a first-try signal uses. handle_signal
        # performs its own candidate_ledger.claim() immediately before
        # evaluating, so the claim and the evaluation happen atomically
        # inside that one call - a trade can never end up claimed (blocking
        # a future natural re-presentation) without also having been
        # evaluated. Deliberately outside the try/except above: a bug in
        # scoring or evaluation is a real defect to surface (it will raise
        # and propagate, same as any other tick-loop exception), not a
        # transient market-lookup failure to silently reschedule - treating
        # it as one would misattribute the failure and hide it in the
        # retry/abandon counters instead of the fault log.
        recovered += 1
        del _pending[trade_id]
        for signal in provider.score_recovered_trade(trade, market, cfg, now):
            await handle_signal(signal, cfg, market_results, config_fp, tick_now)
    _window_retried += retried
    _window_recovered += recovered
    _window_abandoned += abandoned
    return {"retried": retried, "recovered": recovered, "abandoned": abandoned}
