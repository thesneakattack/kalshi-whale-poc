"""
Diagnostics, archive and index-feed routes.

First group extracted from main.py 2026-08-17 (direct request: "main.py is
now almost 6k lines long... more modular/less monolithic for better logic
targeting and also session efficiency"), and the template for the rest.

The pattern, so the next group is mechanical:
  1. `router = APIRouter()` here; the route decorators become `@router.*`
     rather than `@app.*`.
  2. Shared runtime state comes from `services.app_state`, never from
     main - that is what keeps the import acyclic and is why app_state was
     extracted first.
  3. main.py does `app.include_router(...)`, which preserves every path
     exactly, so no client or test changes.

These routes are grouped together because they answer one question between
them - "is this system doing what its settings say, and is the data behind
that answer real" - and they share a discipline: every one is read-only
with respect to trading, and each degrades to an explicit "unknown" rather
than a fabricated number.
"""
import time

from fastapi import APIRouter, HTTPException

from services import index_feed, series_watcher, settlement_edge, trade_archive
from services.diagnostics import diagnostics
from services.app_state import state, trade_stream, whale_provider
from services.whalewatchers.kalshi_trade_tape import _MAX_SEEN_TRADE_IDS
from services.config_store import config_store
from services.kalshi_client import KalshiClient

router = APIRouter()

@router.get("/api/diagnostics")
async def get_diagnostics(hours: float = 24.0):
    """Read-only performance/integrity report - services/diagnostics/diagnostics.py.
    Offline checks only (no API calls); see /api/diagnostics/coverage for
    the one check that needs real exchange data."""
    now = time.time()
    return diagnostics.run_offline(config_store.get(), since_ts=now - hours * 3600, now=now)


@router.get("/api/diagnostics/coverage")
async def get_diagnostics_coverage(pages: int = 2):
    """The one check the app cannot answer from its own stores: how much
    real exchange-wide whale flow it never sees. Makes 1-2 real API calls
    (GET /markets/trades, exchange-wide), so it's a separate route rather
    than part of /api/diagnostics' always-safe offline set."""
    cfg = config_store.get()
    watched = {m["ticker"] for m in (state.get("markets") or []) if m.get("ticker")}
    check = await diagnostics.check_coverage(cfg, watched, pages=pages)
    return check.to_dict()


@router.get("/api/diagnostics/series/{series}")
async def get_series_watcher(series: str, hours: float = 24.0):
    """End-to-end report for one series (services/series_watcher.py) - the
    funnel from raw exchange print to closed position, the
    accuracy-vs-realised-win-rate reconciliation, and the spread/depth
    context at each entry. Read-only, no API calls.

    Separate from /api/diagnostics' blended set because the question is
    per-series by construction: a win rate averaged across every series
    answers nobody's question about a specific one."""
    cfg = config_store.get()
    now = time.time()
    return {
        "funnel": series_watcher.funnel(series, hours=hours, cfg=cfg, now=now),
        "reconcile": series_watcher.reconcile(series, hours=hours, cfg=cfg, now=now),
        "book_context": series_watcher.book_context_at_entry(series, hours=hours, now=now),
        "capture": series_watcher.capture_stats(series),
    }


@router.get("/api/archive/epochs")
async def get_archive_epochs(limit: int = 50):
    """Every archived paper-trading epoch (services/trade_archive.py) -
    the permanent record a reset can't destroy."""
    return {"epochs": trade_archive.epochs(limit)}


@router.get("/api/archive/compare")
async def get_archive_compare(limit: int = 10):
    """Epochs side by side against the standing 70%/70% target. edge_pts
    (win rate minus the breakeven accuracy its own entry prices implied) is
    the ranking column - a high win rate with negative edge is the
    high-price trap, not progress."""
    return trade_archive.compare(limit)


@router.post("/api/archive/snapshot")
async def post_archive_snapshot(label: str, reason: str | None = None):
    """Take an archive checkpoint WITHOUT resetting anything - for marking
    the boundary of a config experiment while it's still running."""
    return trade_archive.archive_epoch(label=label, reason=reason, cfg=config_store.get())


@router.get("/api/diagnostics/settlement-edge")
async def get_settlement_edge(min_samples: int = 200):
    """Does the streaming partial settlement average beat the market's own
    price? Brier scores for both forecasts of the same event at the same
    instant (services/settlement_edge.py). Reports "insufficient" rather
    than a verdict until there's enough resolved data to mean anything."""
    return {"report": settlement_edge.edge_report(min_samples), "capture": settlement_edge.stats()}


@router.get("/api/health/pipeline")
async def get_pipeline_health():
    """One place to confirm the whole flow is actually alive between
    sessions - capture, signal generation, evaluation and every persisted
    store, with the age of the most recent write for each.

    Exists because "is it running" and "is it producing" are different
    questions (2026-08-17 direct request: make sure that between sessions
    the whole flow is working "at peak low latency and effectiveness, so
    even if we scrap things data gathered is still useful"). The app can
    look perfectly healthy - ticking, connected, no errors - while
    producing nothing, and a stale last-write timestamp is the only thing
    that shows it."""
    import sqlite3 as _sq

    from services import candidate_log, fault_log, game_state, signal_log

    now = time.time()

    def _age(db_path, table, col):
        try:
            with _sq.connect(db_path) as conn:
                n, last = conn.execute(f"SELECT COUNT(*), MAX({col}) FROM {table}").fetchone()
            return {"rows": n, "last_write_sec_ago": round(now - last, 1) if last else None}
        except Exception as exc:
            return {"error": str(exc)}

    return {
        "generated_at": now,
        "running": state.get("running"),
        "last_tick_duration_sec": state.get("last_tick_duration_sec"),
        "last_tick_rate_limit_hits": state.get("last_tick_rate_limit_hits"),
        "markets_watched": len(state.get("markets") or []),
        "trade_stream": state.get("trade_stream_status"),
        # Real ingest counters from the LIVE objects - the only place these
        # are readable. Measuring them from a separate process returns a
        # fresh object with zeroed counters, which is misleading rather than
        # merely useless (learned 2026-08-17). dropped_messages must stay 0:
        # a non-zero value means the reader outran the worker and prints
        # were lost, which is the one failure "no gaps" cannot tolerate.
        "ingest": {
            "messages_received": getattr(trade_stream, "messages_received", None),
            "dropped_messages": getattr(trade_stream, "dropped_messages", None),
            "exchange_wide": getattr(trade_stream, "exchange_wide_trades", None),
            "dedup_ids_held": len(getattr(whale_provider, "_seen_trade_ids", ())),
            "dedup_cap": _MAX_SEEN_TRADE_IDS,
            "provider_stats": getattr(whale_provider, "stats", None),
        },
        "index_stream": state.get("index_stream_status"),
        "stores": {
            "raw_trades": _age(series_watcher.DB_PATH, "raw_trades", "observed_at"),
            "book_snapshots": _age(series_watcher.DB_PATH, "book_snapshots", "observed_at"),
            "signals": _age(signal_log.DB_PATH, "signals", "seen_at"),
            "rejections": _age(candidate_log.DB_PATH, "rejected_candidates", "rejected_at"),
            "index_ticks": _age(index_feed.DB_PATH, "index_ticks", "observed_at"),
            "settlement_observations": _age(
                settlement_edge.DB_PATH, "window_observations", "observed_at"),
            "game_states": _age(game_state.DB_PATH, "game_states", "observed_at"),
        },
        # Anything that failed and was swallowed (services/fault_log.py).
        # A non-empty value here is the difference between "quiet market"
        # and "broken component" - the distinction that cost game_state
        # every row it should have written on 2026-08-17.
        "faults_last_24h": fault_log.summary(since_ts=now - 86400),
        "buffered_unwritten": {
            "series_watcher_trades": series_watcher.capture_stats().get("buffered_trades"),
            "index_feed_ticks": index_feed.snapshot().get("buffered_ticks"),
            "settlement_edge": settlement_edge.stats().get("buffered"),
            "game_state": game_state.stats().get("buffered"),
        },
    }


@router.get("/api/health/faults")
async def get_faults(limit: int = 50, component: str | None = None, hours: float = 24.0):
    """Every swallowed exception and edge case, deduplicated with a count
    (services/fault_log.py). Start a session here: a large `count` or a
    recent `last_seen` means something is failing right now, silently."""
    from services import fault_log as fl

    return {"summary": fl.summary(since_ts=time.time() - hours * 3600),
            "faults": fl.recent(limit=limit, component=component)}


@router.get("/api/index")
async def get_index_feed():
    """Live CF Benchmarks / Pyth index values (services/index_feed.py)."""
    return index_feed.snapshot()


@router.get("/api/index/settlement/{ticker}")
async def get_index_settlement(ticker: str):
    """Live settlement projection for one market, from the partial 60-second
    average currently streaming.

    For the crypto series this is exact arithmetic, not a forecast: the
    market settles on the mean of sixty one-second index observations, and
    `observations_known` of them are already in hand."""
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        market = await client.get_market(ticker)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not fetch {ticker}: {exc}")
    finally:
        # Real leak found 2026-08-23 auditing every KalshiClient() call site
        # for the same missing-close() shape that caused index_stream_
        # handlers._spec_for's live "Unclosed connector" incident - this
        # route had zero current callers (grepped, confirmed) so it wasn't
        # the source of that particular leak, but it's the same bug and
        # would fire on any real hit.
        await client.close()
    spec = index_feed.settlement_spec(market)
    if not spec.get("supported"):
        return spec
    return {
        **spec,
        "projection": index_feed.settlement_projection(
            spec["index_id"], spec["strike"], side="yes",
        ),
        "index_volatility_1s": index_feed.recent_volatility(spec["index_id"]),
    }

