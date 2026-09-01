"""
Persists bounded, low-frequency snapshots of runtime metrics the app
already computes in memory (tick timing/phase breakdown, rate-limit hits,
trade-stream throughput, WS drop counters, ...) so a later question like
"was it slow an hour ago" can be answered from data/observability.db
instead of only from live state or logs. docs/superpowers/plans/2026-08-24-
quality-control-plane.md Task 9; see this package's README.md.

Deliberately reuses existing counters rather than adding new instrumentation
(docs/superpowers/specs/2026-08-24-quality-control-plane-design.md section
8's "reuse existing state" scoping) - capture_from_runtime is a pure
metric-name mapping over whatever state/trade_stream/index_stream already
expose, with no new counters of its own.

capture_from_runtime/maybe_capture take `state`, `trade_stream`, and
`index_stream` as explicit arguments rather than importing
services.app_state directly, unlike services/backup/backup.py - that keeps
this module import-side-effect-free (no eager PaperBroker/RiskManager
construction) and makes the pure mapping function trivially testable with a
synthetic dict.
"""
import json
import sqlite3
import time
from pathlib import Path

from services import (
    candidate_retry, capture_writer, fault_log, http_client, loop_watchdog, strategy_engine, whale_pipeline_perf,
)
from services.exits import exit_engine
from services.quality.models import QualityFinding

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "observability.db"

_DEFAULT_SAMPLE_INTERVAL_SEC = 60
_DEFAULT_RETENTION_HOURS = 336  # 14 days
_RATE_LIMIT_WINDOW_HOURS = 1.0  # QCP Task 10 anomaly rule - see runtime_findings
_RATE_LIMIT_MIN_OCCURRENCES = 3  # "repeated," not one isolated hit


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metric_samples (
            observed_at REAL NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            labels_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_time "
        "ON metric_samples(metric, observed_at)"
    )
    return conn


def record_sample(
    metric: str, value: float | None, labels: dict | None = None, observed_at: float | None = None,
) -> None:
    observed_at = observed_at if observed_at is not None else time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO metric_samples (observed_at, metric, value, labels_json) VALUES (?, ?, ?, ?)",
            (observed_at, metric, value, json.dumps(labels or {})),
        )


def record_samples_bulk(samples: dict, observed_at: float, labels: dict | None = None) -> None:
    """One connection/commit for a whole capture cycle's worth of metrics -
    see this module's docstring; avoid a connection per metric row."""
    if not samples:
        return
    labels_json = json.dumps(labels or {})
    rows = [(observed_at, metric, value, labels_json) for metric, value in samples.items()]
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO metric_samples (observed_at, metric, value, labels_json) VALUES (?, ?, ?, ?)",
            rows,
        )


def history(metric: str, since_ts: float, limit: int = 5000) -> list[dict]:
    limit = max(1, min(limit, 5000))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT observed_at, metric, value, labels_json FROM metric_samples "
            "WHERE metric = ? AND observed_at >= ? ORDER BY observed_at ASC LIMIT ?",
            (metric, since_ts, limit),
        ).fetchall()
    return [
        {"observed_at": r[0], "metric": r[1], "value": r[2], "labels": json.loads(r[3])}
        for r in rows
    ]


def summary(hours: float, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600
    with _connect() as conn:
        rows = conn.execute(
            "SELECT metric, COUNT(*), MIN(value), MAX(value), AVG(value) "
            "FROM metric_samples WHERE observed_at >= ? GROUP BY metric",
            (since_ts,),
        ).fetchall()
    return {
        metric: {
            "count": count,
            "min": vmin,
            "max": vmax,
            "avg": round(vavg, 4) if vavg is not None else None,
        }
        for metric, count, vmin, vmax, vavg in rows
    }


def prune(retention_hours: float, now: float | None = None) -> int:
    now = now if now is not None else time.time()
    cutoff = now - retention_hours * 3600
    with _connect() as conn:
        cur = conn.execute("DELETE FROM metric_samples WHERE observed_at < ?", (cutoff,))
        return cur.rowcount


def _latest_sample_time() -> float | None:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(observed_at) FROM metric_samples").fetchone()
    return row[0] if row and row[0] is not None else None


def _flatten_position_ticker_cadence(state: dict, now: float) -> dict:
    """Per-open-position ticker-message cadence (P8 Task 34) - how long since
    each currently-open position's ticker last received a WS ticker update.
    This is the second of the two measured inputs the staleness benchmark
    (P8 Task 40) is explicitly gated behind, and the input Task 35's
    staleness-corroboration threshold should be tuned from rather than
    guessed. Bounded by open-position count by construction: the writer
    (_process_stream_ticker) only records tickers that are open positions,
    and this read intersects against the current open set so a closed
    position's leftover entry is excluded, never counted as a huge age.
    "Open but never seen" is counted separately, not fabricated as an age -
    same unknown-over-fabricated rule as everything else here."""
    open_tickers = state.get("open_position_tickers") or set()
    if not open_tickers:
        return {}
    seen_at = state.get("open_position_ticker_seen_at") or {}
    ages = [now - seen_at[t] for t in open_tickers if t in seen_at]
    out: dict = {
        "position_ticker.tracked_count": float(len(ages)),
        "position_ticker.never_seen_count": float(len(open_tickers) - len(ages)),
    }
    if ages:
        out["position_ticker.oldest_update_age_sec"] = round(max(ages), 3)
        out["position_ticker.newest_update_age_sec"] = round(min(ages), 3)
        out["position_ticker.median_update_age_sec"] = round(sorted(ages)[len(ages) // 2], 3)
    return out


def capture_from_runtime(cfg: dict, state: dict, trade_stream, index_stream, now: float | None = None) -> dict:
    """Pure mapping from already-computed in-memory counters to stable
    metric names - no I/O. A missing/None source is simply omitted rather
    than recorded as a fabricated 0 (CLAUDE.md / the design spec's "unknown
    is better than fabricated" rule)."""
    metrics: dict = {}

    if state.get("last_tick_duration_sec") is not None:
        metrics["tick.duration_sec"] = float(state["last_tick_duration_sec"])
    if state.get("last_tick_rate_limit_hits") is not None:
        metrics["tick.rate_limit_hits"] = float(state["last_tick_rate_limit_hits"])
    for phase, seconds in (state.get("tick_phase_timings") or {}).items():
        metrics[f"tick.phase.{phase}_sec"] = float(seconds)

    trade_perf = state.get("trade_stream_perf")
    if trade_perf:
        if trade_perf.get("messages_per_sec") is not None:
            metrics["trade_stream.messages_per_sec"] = float(trade_perf["messages_per_sec"])
        if trade_perf.get("avg_handler_ms") is not None:
            metrics["trade_stream.avg_handler_ms"] = float(trade_perf["avg_handler_ms"])

    if trade_stream is not None:
        metrics["trade_stream.dropped_messages"] = float(getattr(trade_stream, "dropped_messages", 0))
        metrics["trade_stream.messages_received"] = float(getattr(trade_stream, "messages_received", 0))
    if index_stream is not None:
        metrics["index_stream.dropped_messages"] = float(getattr(index_stream, "dropped_messages", 0))
        metrics["index_stream.messages_received"] = float(getattr(index_stream, "messages_received", 0))

    # kalshi_rest.<endpoint>.* (QCP Task 15) - reads state["last_tick_http_metrics"],
    # an already-computed since-last-tick snapshot main.py's trading_loop
    # stashes via http_client.http_metrics_snapshot(reset=True) right next to
    # get_and_reset_rate_limit_hits(). Deliberately never calls
    # http_metrics_snapshot itself here - this function must stay a pure read
    # (no I/O, no resets) since it's shared by both the periodic persisted
    # sampler and the on-demand GET /api/observability/current route; calling
    # reset=True from here would let an incidental /current request zero out
    # counters the periodic sampler was about to read. Only endpoints with at
    # least one attempt this tick appear at all (http_metrics_snapshot's own
    # "omit rather than fabricate" contract) and avg_latency_ms is skipped
    # per-endpoint when there were zero successes to average, same "unknown
    # over fabricated 0" rule as everything else in this function.
    for endpoint, m in (state.get("last_tick_http_metrics") or {}).items():
        metrics[f"kalshi_rest.{endpoint}.calls"] = float(m["calls"])
        metrics[f"kalshi_rest.{endpoint}.errors"] = float(m["errors"])
        metrics[f"kalshi_rest.{endpoint}.rate_limited"] = float(m["rate_limited"])
        if m["avg_latency_ms"] is not None:
            metrics[f"kalshi_rest.{endpoint}.avg_latency_ms"] = float(m["avg_latency_ms"])

    # <stream>.ingest.* (realtime data-plane investigation, I1) - the
    # gateway's own queue-health snapshot (services/kalshi/websocket.py's
    # ingest_metrics), flattened under bounded names. Same pure-read
    # contract as everything above: ingest_metrics() never resets anything;
    # maybe_capture is what rolls the gateway's window after persisting.
    for stream, prefix in ((trade_stream, "trade_stream"), (index_stream, "index_stream")):
        snapshot = _ingest_snapshot(stream)
        if snapshot is not None:
            metrics.update(_flatten_ingest_metrics(prefix, snapshot))

    # whale_pipeline.* (I2) - the whale-trade pipeline's own stage timers
    # and counters (services/whale_pipeline_perf.py, a pure module-level
    # singleton, not services.app_state). Same pure-read contract; the
    # window is rolled by maybe_capture after persisting.
    metrics.update(_flatten_whale_pipeline(whale_pipeline_perf.perf.snapshot()))

    # kalshi_rest_class.* / kalshi_rest_limiter.* (I5) - REST latency split
    # into limiter wait / network / backoff / total per caller class, plus
    # the token buckets' waiter-depth gauges (services/http_client.py's
    # rest_latency_snapshot, pure read; window rolled by maybe_capture).
    metrics.update(_flatten_rest_latency(http_client.rest_latency_snapshot()))

    # loop_watchdog.* (realtime data-plane remediation P0 Task 1) - whether
    # the asyncio loop itself is stalling, independent of any one
    # subsystem's own counters. Same "no evidence, no rows" contract as
    # whale_pipeline: omitted entirely until the watchdog has taken at
    # least one sample in this process.
    metrics.update(_flatten_loop_watchdog(loop_watchdog.snapshot()))

    # candidate_retry.* (realtime data-plane remediation P2 Task 12) - the
    # H4-recovery retry queue's depth (a live gauge) plus this window's
    # retried/recovered/abandoned counts. Same "no evidence, no rows"
    # contract as loop_watchdog/whale_pipeline - every other metric family
    # here follows it, and capture_from_runtime's own contract (asserted by
    # test_capture_from_runtime_omits_missing_sources_instead_of_fabricating_zero)
    # is that nothing having happened yields metrics == {}, not a page of
    # meaningful-looking zeros.
    metrics.update(_flatten_candidate_retry(candidate_retry.snapshot()))

    # writer.* (realtime data-plane remediation P3 Task 14) - the capture
    # writer daemon thread's per-store buffer depth and flush recency. Same
    # "no evidence, no rows" contract as everything above - depth()/
    # last_flush_age_ms() always return an entry per known store (populated
    # at module import, not at start()), so gate on is_alive() rather than
    # on those dicts being non-empty; otherwise a process that never called
    # capture_writer.start() would still emit a misleadingly "live-looking"
    # depth of 0.
    if capture_writer.is_alive():
        metrics.update(_flatten_capture_writer(capture_writer.depth(), capture_writer.last_flush_age_ms()))

    # position_ticker.* (P8 Task 34) - per-open-position ticker cadence.
    # Same pure-read contract; nothing here mutates the seen_at dict
    # (pruning of closed positions' leftover entries happens in
    # maybe_capture, post-persist, alongside the window rolls).
    metrics.update(_flatten_position_ticker_cadence(state, now if now is not None else time.time()))

    return metrics


def _flatten_loop_watchdog(snapshot: dict) -> dict:
    if not snapshot.get("samples"):
        return {}  # watchdog hasn't sampled yet in this process - no evidence, no rows
    out = {
        "loop_watchdog.samples": float(snapshot["samples"]),
        "loop_watchdog.stall_count": float(snapshot.get("stall_count") or 0),
    }
    if snapshot.get("stall_max_ms") is not None:
        out["loop_watchdog.stall_max_ms"] = float(snapshot["stall_max_ms"])
    return out


def _flatten_candidate_retry(snapshot: dict) -> dict:
    pending = snapshot.get("pending") or 0
    retried = snapshot.get("retried") or 0
    recovered = snapshot.get("recovered") or 0
    abandoned = snapshot.get("abandoned") or 0
    if not (pending or retried or recovered or abandoned):
        return {}  # nothing pending and nothing happened this window - no evidence, no rows
    return {
        "candidate_retry.pending": float(pending),
        "candidate_retry.retried": float(retried),
        "candidate_retry.recovered": float(recovered),
        "candidate_retry.abandoned": float(abandoned),
    }


def _flatten_capture_writer(depth: dict, last_flush_age_ms: dict) -> dict:
    out: dict = {}
    for store, n in depth.items():
        out[f"writer.depth.{store}"] = float(n)
    for store, age_ms in last_flush_age_ms.items():
        out[f"writer.last_flush_age_ms.{store}"] = float(age_ms)
    return out


def _flatten_rest_latency(snapshot: dict) -> dict:
    out: dict = {}
    by_class = snapshot.get("by_class") or {}
    if not by_class:
        return out  # nothing has called Kalshi yet in this process - no evidence, no rows
    for cls, stats in by_class.items():
        p = f"kalshi_rest_class.{cls}"
        window_counts = stats.get("window") or {}
        for key in ("calls", "attempts", "rate_limited", "errors"):
            # Per-window counts (summable across persisted samples) - never
            # the lifetime ones, which I8 found had been sampled by mistake.
            out[f"{p}.{key}"] = float(window_counts.get(key) or 0)
        for component in ("limiter_wait", "network", "backoff", "total"):
            window = (stats.get(component) or {}).get("window") or {}
            if window.get("count"):
                for key in ("avg_ms", "max_ms"):
                    if window.get(key) is not None:
                        out[f"{p}.{component}.window_{key}"] = float(window[key])
    for endpoint, counts in (snapshot.get("by_endpoint") or {}).items():
        for key in ("calls", "rate_limited", "errors"):
            out[f"kalshi_rest_endpoint.{endpoint}.{key}"] = float((counts or {}).get(key) or 0)
    for bucket, gauges in (snapshot.get("limiter") or {}).items():
        for key in ("waiters", "waiters_high_water"):
            if (gauges or {}).get(key) is not None:
                out[f"kalshi_rest_limiter.{bucket}.{key}"] = float(gauges[key])
    return out


def _flatten_whale_pipeline(snapshot: dict) -> dict:
    out: dict = {}
    stages = snapshot.get("stages") or {}
    lifetime_counters = (snapshot.get("counters") or {}).get("lifetime") or {}
    if not any((t.get("lifetime") or {}).get("count") for t in stages.values()) and not any(lifetime_counters.values()):
        return out  # the pipeline has never run in this process (poll mode, tests) - no evidence, no rows
    for stage, timing in stages.items():
        window = timing.get("window") or {}
        out[f"whale_pipeline.stage.{stage}.window_count"] = float(window.get("count") or 0)
        if window.get("count"):
            for key in ("avg_ms", "max_ms"):
                if window.get(key) is not None:
                    out[f"whale_pipeline.stage.{stage}.window_{key}"] = float(window[key])
    for counter, n in ((snapshot.get("counters") or {}).get("window") or {}).items():
        out[f"whale_pipeline.counter.{counter}"] = float(n)
    e2e = snapshot.get("receive_to_decision") or {}
    if e2e.get("window_p95_upper_bound_sec") is not None:
        out["whale_pipeline.receive_to_decision.window_p95_upper_bound_sec"] = float(e2e["window_p95_upper_bound_sec"])
    for name, n in (e2e.get("buckets") or {}).items():
        out[f"whale_pipeline.receive_to_decision.bucket.{name}"] = float(n)
    return out


def _ingest_snapshot(stream) -> dict | None:
    ingest = getattr(stream, "ingest_metrics", None) if stream is not None else None
    if not callable(ingest):
        return None
    try:
        return ingest()
    except Exception:
        return None  # unknown over fabricated - a broken snapshot is omitted, not zeroed


def _flatten_ingest_metrics(prefix: str, im: dict) -> dict:
    p = f"{prefix}.ingest"
    out: dict = {}
    # discarded_on_reconnect (#209): what _begin_connection threw away with
    # the previous connection - a distinct term from dropped (queue-full).
    for group in ("received", "processed", "dropped", "discarded_on_reconnect"):
        for cls, count in (im.get(f"{group}_by_class") or {}).items():
            if count:  # zero classes omitted, never fabricated
                out[f"{p}.{group}.{cls}"] = float(count)
    out[f"{p}.dropped_window"] = float(im.get("dropped_window") or 0)
    out[f"{p}.malformed_messages"] = float(im.get("malformed_messages") or 0)
    out[f"{p}.handler_exceptions"] = float(im.get("handler_exceptions_total") or 0)
    out[f"{p}.handler_timeouts"] = float(im.get("handler_timeouts_total") or 0)
    queue = im.get("queue") or {}
    out[f"{p}.queue_depth"] = float(queue.get("depth") or 0)
    out[f"{p}.queue_high_water"] = float(queue.get("high_water") or 0)
    if queue.get("oldest_message_age_sec") is not None:
        out[f"{p}.oldest_message_age_sec"] = float(queue["oldest_message_age_sec"])
    wait = im.get("queue_wait") or {}
    window = wait.get("window") or {}
    out[f"{p}.queue_wait.window_count"] = float(window.get("count") or 0)
    if window.get("count"):
        for key in ("max_sec", "avg_sec", "p95_upper_bound_sec"):
            if window.get(key) is not None:
                out[f"{p}.queue_wait.window_{key}"] = float(window[key])
    for name, count in (wait.get("buckets") or {}).items():
        out[f"{p}.queue_wait.bucket.{name}"] = float(count)
    for cls, timing in (im.get("handler_time_by_class") or {}).items():
        window = timing.get("window") or {}
        out[f"{p}.handler.{cls}.window_count"] = float(window.get("count") or 0)
        if window.get("count"):
            for key in ("avg_ms", "max_ms"):
                if window.get(key) is not None:
                    out[f"{p}.handler.{cls}.window_{key}"] = float(window[key])
    out[f"{p}.server_errors"] = float((im.get("server_errors") or {}).get("total") or 0)
    out[f"{p}.error_25_total"] = float(im.get("error_25_total") or 0)
    out[f"{p}.error_25_window"] = float(im.get("error_25_window") or 0)
    out[f"{p}.reconnects"] = float((im.get("connection") or {}).get("reconnects") or 0)
    # Reconnect gap duration (P8 Task 34) - emitted only in windows where a
    # reconnect actually completed (gap_sec_window is set by _begin_connection
    # and consumed by reset_ingest_window), so the persisted series is one
    # real outage-duration sample per reconnect event: the measured
    # distribution the staleness benchmark (P8 Task 40) is gated behind.
    gap_window = (im.get("connection") or {}).get("gap_sec_window")
    if gap_window is not None:
        out[f"{p}.last_gap_sec"] = float(gap_window)
    # subscription_churn (2026-08-26, investigating a direct report that
    # WS-subscription churn per market-discovery scan is "taxing everything
    # downstream and upstream") - how often _sync_subscriptions actually
    # sent an add_markets/delete_markets diff this window, and how many
    # tickers churned. Zero-window omitted like every other window metric
    # here, since "no churn this window" is a real, common, honest state.
    churn = im.get("subscription_churn") or {}
    if churn.get("syncs_window"):
        out[f"{p}.subscription_churn.syncs_window"] = float(churn["syncs_window"])
        out[f"{p}.subscription_churn.tickers_added_window"] = float(churn.get("tickers_added_window") or 0)
        out[f"{p}.subscription_churn.tickers_removed_window"] = float(churn.get("tickers_removed_window") or 0)
    return out


def maybe_capture(cfg: dict, state: dict, trade_stream, index_stream) -> None:
    """Cheap interval gate wired into the trading loop, next to backup's own
    _maybe_run_backup. Restart-safe the same way services/backup/backup.py's
    README.md documents fixing live (2026-08-23): last_sample_at is
    seeded from the most recently *persisted* sample the first time this
    runs in a given process, instead of trusting the in-memory
    state["observability"] default alone - otherwise every uvicorn --reload
    cycle would read "never sampled" and fire an immediate extra sample."""
    obs_cfg = cfg.get("observability") or {}
    if not obs_cfg.get("enabled", True):
        return
    obs_state = state.setdefault("observability", {"last_sample_at": 0.0})
    interval = obs_cfg.get("sample_interval_sec", _DEFAULT_SAMPLE_INTERVAL_SEC)
    now = time.time()
    if obs_state["last_sample_at"] == 0.0:
        obs_state["last_sample_at"] = _latest_sample_time() or 0.0
    if now - obs_state["last_sample_at"] < interval:
        return
    metrics = capture_from_runtime(cfg, state, trade_stream, index_stream)
    record_samples_bulk(metrics, observed_at=now)
    obs_state["last_sample_at"] = now
    # The gateways' "window" accumulators (queue wait, handler time by class,
    # dropped_window, error_25_window) cover exactly one persisted sample's
    # span - reset them only here, only after the sample is durably written,
    # never from the on-demand /current route (which must stay a pure read).
    for stream in (trade_stream, index_stream):
        reset = getattr(stream, "reset_ingest_window", None) if stream is not None else None
        if callable(reset):
            try:
                reset()
            except Exception:
                pass
    whale_pipeline_perf.perf.reset_window()
    http_client.reset_rest_latency_window()
    # loop_watchdog fault visibility (2026-09-01): the watchdog's own
    # snapshot was previously read-only - severe stalls (measured live, up
    # to 130s) were persisted to observability.db but never surfaced
    # anywhere a human or soak_analyzer would see without manually
    # querying /api/observability/summary. Read BEFORE reset_window()
    # clears the window; one fault_log row per persisted window here, not
    # per-stall inside loop_watchdog._tick() itself - that 0.1s hot loop
    # must never do blocking I/O, and this call site is already
    # synchronous and already inline in maybe_capture regardless.
    lw_snapshot = loop_watchdog.snapshot()
    if lw_snapshot.get("stall_count"):
        fault_log.record_fault(
            "loop_watchdog", "event_loop_stall",
            f"{lw_snapshot['stall_count']} stall(s) this window, worst "
            f"{lw_snapshot['stall_max_ms']:.0f}ms over {lw_snapshot['samples']} samples",
            severity="error" if lw_snapshot["stall_max_ms"] >= 1000.0 else "warn",
        )
    loop_watchdog.reset_window()
    candidate_retry.reset_window()
    exit_engine.reset_window()  # P8 Task 35: stale-uncorroborated once-per-ticker-per-window log
    strategy_engine.reset_window()  # issue #267: me_gate_unknown once-per-event-per-window fault log
    # Prune closed positions' leftover ticker-cadence entries (P8 Task 34),
    # here post-persist rather than on the WS message path, so the hot-path
    # writer stays a bare dict assignment and the dict stays bounded by the
    # open-position count instead of by every ticker ever held.
    seen_at = state.get("open_position_ticker_seen_at")
    if seen_at:
        open_tickers = state.get("open_position_tickers") or set()
        for ticker in [t for t in seen_at if t not in open_tickers]:
            del seen_at[ticker]


# --- runtime anomaly rules (QCP Task 10) ----------------------------------
#
# Each rule is a small, separately-testable pure function (no DB writes -
# the rate-limit-hits rule below is the one read, via history()). A rule
# that lacks enough evidence to judge - no sample yet, a feature that was
# never enabled, a single isolated blip - omits a finding rather than
# asserting health or failure, the same "unknown is better than fabricated"
# discipline services/diagnostics/diagnostics.py's Check.status already
# applies, just expressed as omission since QualityFinding has no fourth
# "unknown" severity of its own.


def _tick_duration_finding(cfg: dict, state: dict) -> QualityFinding | None:
    poll_interval = (cfg.get("kalshi") or {}).get("poll_interval_sec")
    duration = state.get("last_tick_duration_sec")
    if duration is None or not poll_interval or duration <= poll_interval:
        return None
    return QualityFinding(
        finding_id="observability:tick-duration-exceeded:trading_loop",
        check="tick-duration", severity="warning", confidence="high", source="runtime",
        scope="trading_loop",
        summary=f"last tick took {duration}s, exceeding the configured {poll_interval}s poll interval",
        evidence={"last_tick_duration_sec": duration, "poll_interval_sec": poll_interval},
    )


def _dropped_messages_findings(trade_stream, index_stream) -> list[QualityFinding]:
    """Local QueueFull drops (Kalshi-side loss is _server_error_25_findings
    below). Severity keys off the sample window - ingest_metrics()'s
    dropped_window, which reset_ingest_window zeroes right after every
    persisted sample, so "this sample window" spans at most
    observability.sample_interval_sec (60s default) - never off the lifetime
    counter: dropped_messages is set once in the gateway's __init__ and only
    ever incremented, so judging it pinned GET /api/quality/summary (step 1
    of CLAUDE.md's investigation ladder) to "error" for the rest of the
    process after a single drop (#72). The lifetime total stays in the
    finding, labelled as lifetime, at info once the window is clean; a
    stream with no ingest_metrics at all has no window evidence and is
    reported as unknown rather than judged either way. The lifetime counter
    itself is deliberately untouched - tools/soak_analyzer.py's
    ingest_no_drops reads it as the never-reset figure it is."""
    findings = []
    for stream, scope in ((trade_stream, "trade_stream"), (index_stream, "index_stream")):
        if stream is None:
            continue
        lifetime = getattr(stream, "dropped_messages", 0) or 0
        snapshot = _ingest_snapshot(stream)
        window = snapshot.get("dropped_window") if snapshot is not None else None
        if not lifetime and not window:
            continue
        if window is None:
            summary = (f"{scope} has dropped {lifetime} message(s) lifetime (never reset); "
                       "no sample-window count available")
        else:
            summary = (f"{scope} dropped {window} message(s) this sample window "
                       f"({lifetime} lifetime, never reset)")
        findings.append(QualityFinding(
            finding_id=f"observability:ws-dropped-messages:{scope}",
            check="ws-dropped-messages", severity="error" if window else "info",
            confidence="high", source="runtime", scope=scope, summary=summary,
            evidence={"dropped_window": window, "dropped_messages": lifetime},
        ))
    return findings


def _server_error_25_findings(trade_stream, index_stream) -> list[QualityFinding]:
    """Kalshi-side subscription buffer overflow (docs/kalshi/websocket-
    connection.md error 25) reported this window - a server-side loss
    point, deliberately distinct from the local QueueFull finding above
    (I1: the two used to be indistinguishable after the fact)."""
    findings = []
    for stream, scope in ((trade_stream, "trade_stream"), (index_stream, "index_stream")):
        snapshot = _ingest_snapshot(stream)
        if snapshot is None:
            continue
        window = snapshot.get("error_25_window") or 0
        if not window:
            continue
        findings.append(QualityFinding(
            finding_id=f"observability:ws-server-error-25:{scope}",
            check="ws-server-error-25", severity="warning", confidence="high", source="runtime",
            scope=scope,
            summary=(
                f"Kalshi reported subscription buffer overflow (error 25) {window} time(s) on "
                f"{scope} this window - server-side loss, separate from local queue drops"
            ),
            evidence={"error_25_window": window, "error_25_total": snapshot.get("error_25_total")},
        ))
    return findings


def _stream_disconnected_findings(state: dict) -> list[QualityFinding]:
    findings = []
    for key, scope in (("trade_stream_status", "trade_stream"), ("index_stream_status", "index_stream")):
        status = state.get(key)
        if not status or not status.get("enabled") or status.get("connected"):
            continue  # not enabled, no evidence yet, or actually connected - no anomaly
        findings.append(QualityFinding(
            finding_id=f"observability:stream-disconnected:{scope}",
            check="stream-connection", severity="warning", confidence="high", source="runtime",
            scope=scope,
            summary=f"{scope} is enabled but not currently connected",
            evidence={"status": status},
        ))
    return findings


def _repeated_rate_limit_hits_finding(now: float | None = None) -> QualityFinding | None:
    now = now if now is not None else time.time()
    since_ts = now - _RATE_LIMIT_WINDOW_HOURS * 3600
    samples = history("tick.rate_limit_hits", since_ts=since_ts, limit=1000)
    if not samples:
        return None
    nonzero_count = sum(1 for s in samples if (s["value"] or 0) > 0)
    if nonzero_count < _RATE_LIMIT_MIN_OCCURRENCES:
        return None
    return QualityFinding(
        finding_id="observability:repeated-rate-limit-hits:kalshi_client",
        check="rate-limit-hits", severity="warning", confidence="high", source="runtime",
        scope="kalshi_client",
        summary=(
            f"{nonzero_count} of {len(samples)} samples in the last "
            f"{_RATE_LIMIT_WINDOW_HOURS}h had nonzero rate-limit hits"
        ),
        evidence={
            "nonzero_count": nonzero_count, "sample_count": len(samples),
            "window_hours": _RATE_LIMIT_WINDOW_HOURS,
        },
    )


def _candidate_retry_abandoned_finding() -> QualityFinding | None:
    """P2 Task 13's own gate criterion: an abandoned retry must be counted,
    not silent (design spec). candidate_retry.snapshot()'s window counter
    already resets via maybe_capture like every other window metric here -
    this just turns a nonzero window into a visible finding instead of
    something only a deliberate metric query would ever surface."""
    abandoned = candidate_retry.snapshot().get("abandoned") or 0
    if not abandoned:
        return None
    return QualityFinding(
        finding_id="observability:candidate-retry-abandoned:kalshi_trade_tape",
        check="candidate-retry-abandoned", severity="warning", confidence="high", source="runtime",
        scope="kalshi_trade_tape",
        summary=(
            f"{abandoned} whale candidate(s) abandoned this window after exhausting the retry "
            "budget (~91.5s) - a market lookup never recovered in time"
        ),
        evidence={"abandoned": abandoned},
    )


def _capture_writer_dead_finding() -> QualityFinding | None:
    """P3 Task 14's own liveness contract: the writer thread is supervised
    (main.py restarts it within ~5s via capture_writer.ensure_alive()), but
    a finding here makes a dead stretch visible in /api/quality/summary
    too, not just recoverable - same "counted, not silent" bar P2's own
    retry-abandonment finding already set. Only fires once the writer has
    been started at least once in this process (a process that never
    called start() isn't "dead," it's simply not running this component
    yet - not an anomaly worth a finding)."""
    if not capture_writer.was_started():
        return None  # never started in this process
    if capture_writer.is_alive():
        return None
    return QualityFinding(
        finding_id="observability:capture-writer-dead:capture_writer",
        check="capture-writer-alive", severity="critical", confidence="high", source="runtime",
        scope="capture_writer",
        summary="capture_writer's daemon thread is not alive - capture writes are not landing",
        evidence={"depth": capture_writer.depth()},
    )


def runtime_findings(
    cfg: dict, state: dict, trade_stream, index_stream, now: float | None = None,
) -> list[QualityFinding]:
    """Composes every observability anomaly rule into one findings list -
    read-only, informational; this never remediates anything itself."""
    findings: list[QualityFinding] = []
    tick_finding = _tick_duration_finding(cfg, state)
    if tick_finding is not None:
        findings.append(tick_finding)
    findings.extend(_dropped_messages_findings(trade_stream, index_stream))
    findings.extend(_server_error_25_findings(trade_stream, index_stream))
    findings.extend(_stream_disconnected_findings(state))
    rate_limit_finding = _repeated_rate_limit_hits_finding(now=now)
    if rate_limit_finding is not None:
        findings.append(rate_limit_finding)
    abandoned_finding = _candidate_retry_abandoned_finding()
    if abandoned_finding is not None:
        findings.append(abandoned_finding)
    writer_finding = _capture_writer_dead_finding()
    if writer_finding is not None:
        findings.append(writer_finding)
    return findings
