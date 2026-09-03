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
import asyncio
import time

from fastapi import APIRouter, HTTPException

from services import (
    capture_writer, index_feed, mutual_exclusivity, series_watcher, settlement_edge, settlement_resolver,
    strategy_engine,
)
from services.index_feed import backfill as index_feed_backfill
from services.reset import trade_archive
from services.diagnostics import diagnostics
from services.diagnostics import store_stats
from services.diagnostics import trade_capture_reconciliation
from services.app_state import account, state, trade_stream, whale_provider
from services import whale_pipeline_perf
from services import http_client
from services.whalewatchers.kalshi_trade_tape import _MAX_SEEN_TRADE_IDS, min_contracts_for
from services.config.config_store import config_store
from services.kalshi.account import classify_api_key_attestation, user_data_age_sec
from services.kalshi.public import KalshiPublicGateway

router = APIRouter()

# Task 1 of docs/superpowers/plans/2026-09-03-tier0-live-incident-
# remediation.md: no individual probe below had a timeout, so one store
# whose blocking sqlite3 call never returns hung the whole route forever
# (live incident, 2026-09-03 - GET /api/health/pipeline stopped responding
# while every other route kept serving). 10.0s is an estimate: Python's
# sqlite3 default busy-timeout is 5.0s, so an ordinary SQLITE_BUSY wait
# resolves (success or OperationalError) well inside 10s; a probe that
# still hasn't returned past that is not ordinary lock contention and
# this task's own live-validation step (Task 1 Step 4) is where that
# number gets checked against real behavior, not assumed correct.
STORE_PROBE_TIMEOUT_SEC = 10.0


async def _bounded(coro, *, timeout: float | None = None) -> dict:
    """Runs one probe coroutine with a hard wall-clock bound, converting a
    timeout into the same {"error": ...} shape store_stats.store_stats()
    already returns for every other failure - callers of this route never
    see a schema difference between "the store errored" and "the store
    never answered in time." Cancelling the wait_for() here unblocks this
    HTTP response; it cannot forcibly stop the underlying OS thread if the
    wrapped asyncio.to_thread() call is genuinely stuck (not just slow) -
    see this plan's Architecture section.

    `timeout` reads STORE_PROBE_TIMEOUT_SEC live, inside the function body,
    rather than capturing it as a default-parameter expression - a default
    argument's value is frozen once, at `async def` (module-import) time,
    so a test that reassigns the module attribute afterward (e.g.
    `monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC", 0.2)`) would
    silently have no effect on an already-bound default (caught by this
    plan's own adversarial review, deterministically reproduced - a bare
    `timeout: float = STORE_PROBE_TIMEOUT_SEC` default looks identical at
    every production call site but breaks exactly this kind of test)."""
    if timeout is None:
        timeout = STORE_PROBE_TIMEOUT_SEC
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"timed out after {timeout:.0f}s"}


@router.get("/api/diagnostics")
async def get_diagnostics(hours: float = 24.0):
    """Read-only performance/integrity report - services/diagnostics/diagnostics.py.
    Offline checks only (no API calls); see /api/diagnostics/coverage for
    the one check that needs real exchange data."""
    now = time.time()
    return await diagnostics.run_offline(config_store.get(), since_ts=now - hours * 3600, now=now)


@router.get("/api/diagnostics/coverage")
@http_client.classify("interactive")
async def get_diagnostics_coverage(pages: int = 2):
    """The one check the app cannot answer from its own stores: how much
    real exchange-wide whale flow it never sees. Makes 1-2 real API calls
    (GET /markets/trades, exchange-wide), so it's a separate route rather
    than part of /api/diagnostics' always-safe offline set."""
    cfg = config_store.get()
    watched = {m["ticker"] for m in (state.get("markets") or []) if m.get("ticker")}
    check = await diagnostics.check_coverage(cfg, watched, pages=pages)
    return check.to_dict()


@router.get("/api/diagnostics/trade-capture")
@http_client.classify("interactive")
async def get_trade_capture_reconciliation(minutes: float = 5.0, lag_sec: float = 60.0, max_pages: int = 10):
    """REST-vs-WebSocket capture completeness by trade_id over a bounded,
    recent exchange-time window (realtime data-plane task I4, hypothesis
    H5 - services/diagnostics/trade_capture_reconciliation.py).

    Manual only - never scheduled. Costs at most `max_pages` exchange-wide
    GET /markets/trades pages of 1000 (docs/kalshi/get-trades.md), read-only.
    The window ends `lag_sec` before now so a print still sitting in the
    ingest queue is not mistaken for a miss; the current oldest-message age
    is attached so a too-small lag is visible rather than silent."""
    cfg = config_store.get()
    now = time.time()
    window_end = now - max(lag_sec, 0.0)
    window_start = window_end - max(minutes, 0.1) * 60.0
    seen_by_id = getattr(whale_provider, "seen_exchange_ts_by_id", None)
    if not callable(seen_by_id):
        return {"error": f"active whale provider {getattr(whale_provider, 'name', '?')!r} keeps no seen-record; "
                         "reconciliation needs kalshi_trade_tape"}
    wwk_cfg = cfg.get("whale_watcher_kalshi") or {}
    ingest = trade_stream.ingest_metrics() if hasattr(trade_stream, "ingest_metrics") else {}
    evidence = {
        "dropped_messages": ingest.get("dropped_messages"),
        "dropped_window": ingest.get("dropped_window"),
        "error_25_total": ingest.get("error_25_total"),
        "reconnects": (ingest.get("connection") or {}).get("reconnects"),
        "oldest_message_age_sec": (ingest.get("queue") or {}).get("oldest_message_age_sec"),
        "queue_depth": (ingest.get("queue") or {}).get("depth"),
        "lag_sec": lag_sec,
    }
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"].get("request_timeout_sec", 10))
    try:
        return await trade_capture_reconciliation.reconcile_window(
            client, window_start=window_start, window_end=window_end,
            seen_exchange_ts_by_id=seen_by_id(),
            min_contracts_for=lambda ticker: min_contracts_for(ticker, wwk_cfg),
            max_pages=max(1, min(max_pages, 50)),
            ingest_evidence=evidence,
            seen_horizon_ts=whale_provider.seen_horizon_ts(),
        )
    finally:
        await client.close()


@router.get("/api/diagnostics/account")
@http_client.classify("interactive")
async def get_account_diagnostics():
    """Two Kalshi account reads with no existing caller anywhere in
    services/ before this route (grepped, confirmed 2026-08-30) - the
    exchange's own staleness signal (issue #266, GET /exchange/
    user_data_timestamp) and API-key location-attestation status (issue
    #261, GET /api_keys' api_key_region_expiration_ts).

    Deliberately its own route, not folded into /api/health/pipeline or
    /api/quality/summary: both stay network-I/O-free by design today
    (services/quality/routes.py's own module docstring, proven by
    tests/test_quality_routes.py monkeypatching KalshiClient construction
    to raise) and /api/health/pipeline just had a 504 traced to unmeasured
    per-request cost (issue #210) - the established pattern for "a
    diagnostic that needs a real Kalshi call" is already this file's own
    /api/diagnostics/coverage and /api/diagnostics/trade-capture: a
    separate, on-demand route, never an inline addition to either
    always-safe set. Both reads are single-token authenticated GETs (not
    exchange-wide), through the same account gateway/limiter every other
    account read already uses.

    pipeline_oldest_message_age_sec rides along so issue #266's actual ask
    - comparing the exchange's own reporting lag against this app's own
    ingest-pipeline staleness - is answerable from one response, without
    the two numbers ever merging into one (CLAUDE.md: "meant to be
    compared, not merged"). Each of the two live calls degrades to an
    explicit error rather than a fabricated value if the account isn't
    configured or the call fails - same ethos as every other source in
    this router."""
    now = time.time()
    ingest = trade_stream.ingest_metrics() if hasattr(trade_stream, "ingest_metrics") else {}
    pipeline_oldest_message_age_sec = (ingest.get("queue") or {}).get("oldest_message_age_sec")

    if not account.enabled:
        return {
            "generated_at": now,
            "configured": False,
            "user_data_timestamp": None,
            "api_key_attestation": None,
            "pipeline_oldest_message_age_sec": pipeline_oldest_message_age_sec,
        }

    async def _timestamp() -> dict:
        try:
            payload = await account.get_user_data_timestamp()
        except Exception as exc:
            from services import fault_log
            fault_log.record("kalshi_account", "get_user_data_timestamp", exc)
            return {"error": str(exc)}
        return {
            "as_of_time": payload.get("as_of_time"),
            "as_of_age_sec": user_data_age_sec(payload, now=now),
        }

    async def _attestation() -> dict:
        try:
            payload = await account.get_api_keys()
        except Exception as exc:
            from services import fault_log
            fault_log.record("kalshi_account", "get_api_keys", exc)
            return {"error": str(exc)}
        return {
            **classify_api_key_attestation(payload, now=now),
            "api_key_count": len(payload.get("api_keys") or []),
        }

    ts_result, attestation_result = await asyncio.gather(_timestamp(), _attestation())
    return {
        "generated_at": now,
        "configured": True,
        "user_data_timestamp": ts_result,
        "api_key_attestation": attestation_result,
        "pipeline_oldest_message_age_sec": pipeline_oldest_message_age_sec,
    }


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
        "funnel": await series_watcher.funnel(series, hours=hours, cfg=cfg, now=now),
        "reconcile": await series_watcher.reconcile(series, hours=hours, cfg=cfg, now=now),
        "book_context": await series_watcher.book_context_at_entry(series, hours=hours, now=now),
        "capture": await series_watcher.capture_stats(series),
    }


@router.get("/api/archive/epochs")
async def get_archive_epochs(limit: int = 50):
    """Every archived paper-trading epoch (services/reset/trade_archive.py) -
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


def _scheduler_status(now: float) -> dict:
    """P8 Task 36: with the background trigger checks relocated out of
    trading_loop into their own supervised loops (main._scheduler_loop),
    this is the one place to confirm each is still firing - last-started
    age and busy flag per scheduler. Unknown (never started, or a scheduler
    that keeps no timestamp of its own) is None, never a fabricated value."""
    from services.config import config_performance

    def _entry(key: str, started_key: str, busy_key: str) -> dict:
        s = state.get(key) or {}
        last = s.get(started_key) or 0.0
        return {"last_started_sec_ago": round(now - last, 1) if last else None, "busy": bool(s.get(busy_key))}

    def _applied(source: str) -> float | None:
        last = config_performance.last_applied_at(source)
        return round(now - last, 1) if last else None

    return {
        "signal_resolution": _entry("signal_resolution_check", "last_checked_at", "checking"),
        "backup": _entry("backup", "last_started_at", "running"),
        "research": {"last_started_sec_ago": None, "busy": bool((state.get("research") or {}).get("running"))},
        "event_schedule": _entry("event_schedule_scan", "last_started_at", "running"),
        "catalog_scan": _entry("catalog_scan", "last_started_at", "scanning"),
        # Multivariate (combo) event discovery (issue #268) - independent
        # scheduler from catalog_scan above, see services/market_watch/
        # mve_scan.py's own docstring for why.
        "mve_scan": _entry("mve_scan", "last_started_at", "scanning"),
        # Broad milestone discovery (entry-gate-me-pairing-and-netting-
        # remediation Part 3) - independent scheduler, see services/
        # market_watch/milestone_scan.py's own docstring for why.
        "milestone_scan": _entry("milestone_scan", "last_started_at", "scanning"),
        "candidate_retry": _entry("candidate_retry_loop", "last_started_at", "running"),
        # The resolver's own counters ride along (pending backlog, lifetime
        # enqueued/resolved/dropped). `dropped_after_max_attempts` growth is
        # the recurrence signal for a settlement that silently never resolved
        # - for a non-watchlist ticker, four of the five stores have no other
        # path. NOT `dropped_total`, which is the conservation sum and also
        # counts markets correctly skipped for a non-binary result (#208);
        # `non_binary_by_result` says which values those actually were.
        "settlement_resolver": {
            **_entry("settlement_resolver_loop", "last_started_at", "running"),
            **settlement_resolver.snapshot(),
        },
        "auto_apply": {
            "calibration_last_applied_sec_ago": _applied("calibration-auto-apply"),
            "advisory_last_applied_sec_ago": _applied("unified-advisory-auto"),
        },
    }


def _price_staleness(now: float) -> dict:
    """P7 Task 29 (redesigned) / R4 visibility: age of the price each open
    position would be exit-checked against, from the per-ticker write
    stamps. A position with no stamp is counted as unknown, not fabricated
    as fresh."""
    open_tickers = state.get("open_position_tickers") or set()
    stamps = state.get("latest_prices_updated_at") or {}
    ages = [now - stamps[t] for t in open_tickers if t in stamps]
    return {
        "open_position_count": len(open_tickers),
        "stamped_count": len(ages),
        "unstamped_count": len(open_tickers) - len(ages),
        "open_position_oldest_age_sec": round(max(ages), 1) if ages else None,
        "stale_over_300s_count": sum(1 for a in ages if a > 300.0),
    }


# Every persisted store the pipeline health read walks, as
# (db_path_owner, table, timestamp column). Named here rather than inline
# so the cost of the block is one list to reason about: each entry is one
# read-only connection, probed concurrently on worker threads.
def _store_specs() -> dict[str, tuple]:
    from services import candidate_log, game_state, signal_log

    return {
        "raw_trades": (series_watcher.DB_PATH, "raw_trades", "observed_at"),
        "book_snapshots": (series_watcher.DB_PATH, "book_snapshots", "observed_at"),
        "signals": (signal_log.DB_PATH, "signals", "seen_at"),
        "rejections": (candidate_log.DB_PATH, "rejected_candidates", "rejected_at"),
        "index_ticks": (index_feed.DB_PATH, "index_ticks", "observed_at"),
        "settlement_observations": (settlement_edge.DB_PATH, "window_observations", "observed_at"),
        "game_states": (game_state.DB_PATH, "game_states", "observed_at"),
    }


def _blocking_extras(now: float) -> dict:
    """The rest of the handler's synchronous SQLite work, in one place so
    it can ride the same thread hop as the store probes. Small stores, but
    "small" is exactly what raw_trades was in 2026-08."""
    from services import fault_log, game_state

    return {
        "schedulers": _scheduler_status(now),
        "faults_last_24h": fault_log.summary(since_ts=now - 86400),
        "settlement_edge_buffered": settlement_edge.stats().get("buffered"),
        "game_state_buffered": game_state.stats().get("buffered"),
    }


@router.get("/api/health/pipeline")
async def get_pipeline_health(exact_rows: bool = False):
    """One place to confirm the whole flow is actually alive between
    sessions - capture, signal generation, evaluation and every persisted
    store, with the age of the most recent write for each.

    Exists because "is it running" and "is it producing" are different
    questions (2026-08-17 direct request: make sure that between sessions
    the whole flow is working "at peak low latency and effectiveness, so
    even if we scrap things data gathered is still useful"). The app can
    look perfectly healthy - ticking, connected, no errors - while
    producing nothing, and a stale last-write timestamp is the only thing
    that shows it.

    Every SQLite read below runs on a worker thread and answers from the
    rowid index once a store outgrows services/diagnostics/store_stats.py's
    row limit; `stores.<name>.rows_exact` / `.rows_method` say which form
    each number came from. `?exact_rows=true` forces the exact COUNT(*)
    everywhere - the expensive answer moved off the default path, it was
    not removed, but it is opt-in for a reason: measured at 191s on
    raw_trades alone. Both properties exist because this endpoint reached a
    504 and dragged `last_tick_duration_sec` to 81.3s with it (issue #210,
    2026-08-30); `stores_probe_ms` is the recurrence detection - the block
    reports its own wall cost, measured 131.6s before / 0.28s after.
    """
    now = time.time()
    specs = _store_specs()

    probe_started = time.perf_counter()
    results = await asyncio.gather(
        _bounded(asyncio.to_thread(_blocking_extras, now)),
        *(
            _bounded(asyncio.to_thread(
                store_stats.store_stats, db_path, table, col, now, exact=exact_rows
            ))
            for db_path, table, col in specs.values()
        ),
    )
    extras, store_results = results[0], results[1:]
    stores_probe_ms = round((time.perf_counter() - probe_started) * 1000.0, 2)

    return {
        "generated_at": now,
        "running": state.get("running"),
        "last_tick_duration_sec": state.get("last_tick_duration_sec"),
        "last_tick_rate_limit_hits": state.get("last_tick_rate_limit_hits"),
        "markets_watched": len(state.get("markets") or []),
        # P7 Task 29 (redesigned) / R4: how stale the price each open
        # position would be exit-checked against actually is, from the
        # per-ticker write stamps. "No stamp" is counted as unknown, never
        # fabricated as fresh - a position with no entry at all is exactly
        # the case the age-aware overlay exists to catch.
        "price_staleness": _price_staleness(now),
        # P8 Task 36: per-scheduler liveness now that none of them are
        # called from the tick - see _scheduler_status.
        "schedulers": extras.get("schedulers"),
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
            # Live queue-health snapshot (I1, services/kalshi/websocket.py's
            # ingest_metrics): per-class counts, depth/high-water, oldest
            # message age, queue-wait and handler-time windows, server
            # error 25 vs local drops, reconnects. Pure read.
            "queue_health": trade_stream.ingest_metrics() if hasattr(trade_stream, "ingest_metrics") else None,
        },
        # index_feed's existing "is the stream connected" status, extended
        # (issue #260) with the reconnect-gap backfill activity for this
        # same connection - the completeness recovery path for exactly the
        # gaps index_stream_status alone can't show were ever recovered.
        "index_stream": {
            **(state.get("index_stream_status") or {}),
            "backfill": index_feed_backfill.stats(),
        },
        # Whale-pipeline stage timers/counters (I2, services/whale_pipeline_
        # perf.py): where a trade message's time goes, and how many messages
        # enter the thread hop versus how many are real candidates.
        "whale_pipeline": whale_pipeline_perf.perf.snapshot(),
        # Entry-gate lookup gaps that used to be silent (issue #267):
        # me_gate_unknown_total is a lifetime, monotone count of every
        # signal whose special-market conservative gate could not verify
        # the mutually_exclusive flag (no market_titles/event_titles entry,
        # or a genuine exception) - see services/strategy_engine.py's
        # _me_gate_stats docstring. Zero here is meaningful only alongside
        # markets_watched/candidates actually flowing; it does not mean
        # "the gate is being checked and always finds it False."
        "strategy_gates": strategy_engine.me_gate_stats(),
        # The entry-side ME-pairing fallback's own gap counter
        # (services/mutual_exclusivity.py's me_pairing_stats, 2026-08-30) -
        # how often find_open_confirmed_conflict returned None because
        # market_titles had no cached entry for the CANDIDATE ticker yet,
        # i.e. the gate defaulted rather than genuinely finding no
        # conflict. Deliberately a sibling key, not merged into
        # strategy_gates above: the two counters overlap in cause (a
        # missing title-cache entry) but belong to different gates measured
        # at different points, so summing or comparing them directly would
        # be wrong. Same "measure it or it fails silently" reason
        # strategy_gates is here at all.
        "me_pairing_gate": mutual_exclusivity.me_pairing_stats(),
        # REST latency decomposition by caller class + token-bucket waiter
        # gauges (I5, services/http_client.py's rest_latency_snapshot):
        # limiter wait vs network vs backoff, so a slow call is attributable.
        "rest_latency": http_client.rest_latency_snapshot(),
        "stores": dict(zip(specs, store_results)),
        # What the store block above cost, and whether it paid for exact
        # counts. Reported so the next time this endpoint slows down the
        # evidence is in the payload rather than in a stopwatch - the
        # measurement CLAUDE.md requires of anything on this path.
        "stores_probe_ms": stores_probe_ms,
        "stores_exact_rows": exact_rows,
        # Anything that failed and was swallowed (services/fault_log.py).
        # A non-empty value here is the difference between "quiet market"
        # and "broken component" - the distinction that cost game_state
        # every row it should have written on 2026-08-17.
        "faults_last_24h": extras.get("faults_last_24h"),
        # Visible rather than a silent row of nulls when _blocking_extras
        # itself timed out (Task 1) - None on the success path.
        "extras_error": extras.get("error"),
        "buffered_unwritten": {
            # capture_writer owns the raw_trades queue (series_watcher's own
            # capture_stats() only forwards this integer, and pays two
            # COUNT(*)s over 30M rows of raw_trades to do it - 4.5s measured
            # 2026-08-30). Read the owner directly.
            "series_watcher_trades": capture_writer.depth().get("raw_trades", 0),
            "index_feed_ticks": index_feed.snapshot().get("buffered_ticks"),
            "settlement_edge": extras.get("settlement_edge_buffered"),
            "game_state": extras.get("game_state_buffered"),
        },
        # Rows the capture daemon LOST, by cause and store, for the process
        # lifetime (services/capture_writer.loss_snapshot, issue #211):
        # dropped_rows (non-retryable flush failure), overflow_dropped_rows
        # (retained buffer hit its cap during lock collisions), lock_retries
        # (batches handed back for retry - churn, not loss). The
        # capture_writer entries in faults_last_24h above count collisions;
        # only these count missing history. tools/soak_analyzer.py gates on
        # them.
        "capture_writer": capture_writer.loss_snapshot(),
    }


@router.get("/api/health/faults")
async def get_faults(limit: int = 50, component: str | None = None, hours: float = 24.0):
    """Every swallowed exception and edge case, deduplicated with a count
    (services/fault_log.py). Start a session here: a large `count` or a
    recent `last_seen` means something is failing right now, silently."""
    from services import fault_log as fl

    summary, faults = await asyncio.gather(
        asyncio.to_thread(fl.summary, since_ts=time.time() - hours * 3600),
        asyncio.to_thread(fl.recent, limit=limit, component=component),
    )
    return {"summary": summary, "faults": faults}


@router.get("/api/index")
async def get_index_feed():
    """Live CF Benchmarks / Pyth index values (services/index_feed/)."""
    return index_feed.snapshot()


@router.get("/api/index/settlement/{ticker}")
async def get_index_settlement(ticker: str):
    """Live settlement projection for one market, from the partial 60-second
    average currently streaming.

    For the crypto series this is exact arithmetic, not a forecast: the
    market settles on the mean of sixty one-second index observations, and
    `observations_known` of them are already in hand."""
    cfg = config_store.get()
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        market = await client.get_market(ticker)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not fetch {ticker}: {exc}")
    finally:
        # Real leak found 2026-08-23 auditing every KalshiPublicGateway() call site
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

