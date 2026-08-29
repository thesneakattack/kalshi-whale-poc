"""Deferred, batched settled-market resolver (P4 Tasks 19+24,
realtime-data-plane-remediation).

Why this exists: `_process_stream_lifecycle`'s settled branch used to await
`client.get_market(ticker)` inline, on the serial WS consumer, then run five
SQLite resolvers - one REST call + five writes per settlement, serially, on
the same task that drains the 20,000-message ingest queue. Settlements
cascade at boundary times (hourly/15-minute series settle together), so the
consumer stopped draining for tens of minutes: 71 of 81 recorded drop
episodes started at :00-:09, the lifecycle handler's window-max was 19x
elevated during them, and the inline read alone was 23% of all REST demand
(I8). The docstring that justified it ("~0.06/s measured live rate -
negligible") was averaging over exactly the burst that matters.

What replaces it: the handler calls `enqueue()` (dict insert, O(1)) and
returns; a supervised loop in main.py calls `run_pending()` which resolves
due tickers in one `GET /markets?tickers=` batch per run. The delay is not
politeness - docs/kalshi/market_lifecycle.md: the WS `settled` event fires
when settlement processing starts, and a REST read at that instant can
legitimately still see a not-yet-`finalized` market, so deferral + retry is
the contract, not a workaround. Grading stays gated on
status == "finalized" (`TERMINAL_REST_STATUS`) for the same
disputed-and-reversed-result reason as the inline path (2026-08-23
correction; services/kalshi/contracts/lifecycle.py).

Contract docs: docs/kalshi/get-markets.md, docs/kalshi/rate_limits.md,
docs/kalshi/market-and-event-lifecycle.md, docs/kalshi/market_lifecycle.md.

Single-mutator contract, same as services/candidate_retry.py: `enqueue` is
called only from the lifecycle handler, `run_pending` from exactly one
supervised loop (main._settlement_resolver_loop). Both touch `_pending`
only from the event loop; the five store resolvers run on the tick
executor's worker pool so a cascade's SQLite work never lands on the loop.
"""
import time

from services import candidate_log, market_analyst_agent, market_history, settlement_edge, signal_log
from services import http_client, tick_executor
from services.kalshi.contracts.lifecycle import TERMINAL_REST_STATUS

# ticker -> {"settled_ts": float, "enqueued_at": float, "attempts": int}.
# Keyed by ticker so a duplicate settled event (reconnect replay, dispute
# re-fire) collapses into one resolution; every resolver downstream is
# idempotent anyway (WHERE resolved = 0 / INSERT OR IGNORE).
_pending: dict[str, dict] = {}

# Lifetime counters for /api/health/pipeline's schedulers block - monotone,
# reset only by process restart, same idiom as the WS ingest counters.
_stats = {"enqueued_total": 0, "resolved_total": 0, "dropped_total": 0, "last_run_at": 0.0}


def enqueue(ticker: str, settled_ts: float | None = None, now: float | None = None) -> None:
    """Record a settled ticker for deferred resolution. O(1), no I/O -
    safe on the WS consumer's critical path."""
    if not ticker:
        return
    now = time.time() if now is None else now
    entry = _pending.get(ticker)
    if entry is None:
        _pending[ticker] = {
            "settled_ts": float(settled_ts) if settled_ts is not None else now,
            "enqueued_at": now,
            "attempts": 0,
        }
        _stats["enqueued_total"] += 1
    # A re-fired settled event for a ticker already pending changes nothing:
    # keep the original enqueue clock so the delay isn't restarted.


def pending() -> list[tuple[str, float]]:
    return [(t, e["settled_ts"]) for t, e in _pending.items()]


def snapshot() -> dict:
    return {"pending": len(_pending), **_stats}


def _resolve_one_sync(ticker: str, result: str, now: float) -> int:
    """The exact five stores the inline settled branch fed (P7 Task 30 made
    it five; feeding fewer here would silently narrow resolution coverage).
    Runs on the tick executor's worker thread - all five are synchronous
    SQLite."""
    market_history.record_outcome(ticker, result, resolved_at=now)
    resolved = settlement_edge.resolve_window(ticker, result == "yes")
    resolved += market_analyst_agent.resolve_from_market_results({ticker: result})
    resolved += candidate_log.resolve_from_market_results({ticker: result})
    resolved += signal_log.resolve_from_market_results(ticker, result)
    return resolved


async def run_pending(
    client, *, now: float | None = None, delay_sec: float = 60.0,
    batch_size: int = 50, max_attempts: int = 10,
) -> dict:
    """Resolve every due pending settlement in one batched REST read.

    Returns {"resolved": tickers resolved, "still_pending": tickers left,
    "resolved_rows": store rows resolved} - resolved_rows preserves the
    meaning of lifecycle_stream_stats["outcomes_resolved_via_lifecycle"],
    which always counted rows, not tickers.

    A not-yet-finalized market stays pending for the next run (settlement
    race, see module docstring). A ticker Kalshi stops returning is retried
    up to max_attempts then dropped - the REST-tick fallback path still
    exists for it. A non-yes/no result is dropped immediately: retrying a
    scalar market never changes the answer, matching the inline path's
    give-up. A failed batch read leaves everything pending untouched.
    """
    now = time.time() if now is None else now
    due = sorted(
        (t for t, e in _pending.items() if now - e["enqueued_at"] >= delay_sec),
        key=lambda t: _pending[t]["enqueued_at"],
    )[:batch_size]
    resolved_tickers = 0
    resolved_rows = 0
    if due:
        try:
            with http_client.caller_class("background_resolution"):
                markets = await client.get_markets_by_tickers(due)
        except Exception:
            # Transient REST failure: lose the attempt, never the tickers.
            _stats["last_run_at"] = now
            return {"resolved": 0, "still_pending": len(_pending), "resolved_rows": 0}
        for ticker in due:
            entry = _pending.get(ticker)
            if entry is None:
                continue
            market = markets.get(ticker)
            if market is None:
                # Renamed/deleted tickers just aren't in the result
                # (services/kalshi/public.py get_markets_by_tickers) -
                # bounded retries, then leave it to the REST-tick fallback.
                entry["attempts"] += 1
                if entry["attempts"] >= max_attempts:
                    del _pending[ticker]
                    _stats["dropped_total"] += 1
                continue
            if (market.get("status") or "") != TERMINAL_REST_STATUS:
                entry["attempts"] += 1
                if entry["attempts"] >= max_attempts:
                    del _pending[ticker]
                    _stats["dropped_total"] += 1
                continue
            result = (market.get("result") or "").strip().lower()
            if result not in ("yes", "no"):
                del _pending[ticker]
                _stats["dropped_total"] += 1
                continue
            resolved_rows += await tick_executor.run(
                lambda t=ticker, r=result: _resolve_one_sync(t, r, now))
            del _pending[ticker]
            resolved_tickers += 1
            _stats["resolved_total"] += 1
    _stats["last_run_at"] = now
    return {"resolved": resolved_tickers, "still_pending": len(_pending), "resolved_rows": resolved_rows}
