"""
Persists bounded, low-frequency snapshots of runtime metrics the app
already computes in memory (tick timing/phase breakdown, rate-limit hits,
trade-stream throughput, WS drop counters, ...) so a later question like
"was it slow an hour ago" can be answered from data/observability.db
instead of only from live state or logs. docs/superpowers/plans/2026-08-24-
quality-control-plane.md Task 9; see this package's CHEATSHEET.md.

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

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "observability.db"

_DEFAULT_SAMPLE_INTERVAL_SEC = 60
_DEFAULT_RETENTION_HOURS = 336  # 14 days


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


def capture_from_runtime(cfg: dict, state: dict, trade_stream, index_stream) -> dict:
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

    return metrics


def maybe_capture(cfg: dict, state: dict, trade_stream, index_stream) -> None:
    """Cheap interval gate wired into the trading loop, next to backup's own
    _maybe_run_backup. Restart-safe the same way services/backup/backup.py's
    CHEATSHEET.md documents fixing live (2026-08-23): last_sample_at is
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
