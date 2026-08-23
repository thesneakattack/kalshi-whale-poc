"""
Monitoring/alerting for the three cases ROADMAP.md's "Path to production"
section named directly: "a kill-switch trip, crash, or connectivity loss
currently notifies no one." Direct instruction (2026-08-23): build
detection/logging now; the actual notification channel (email/Slack/etc.)
is a separate decision.

Same persisted-audit-trail idiom as reset_log.py/fault_log.py -
data/alert_log.db, one row per alert, resolved_at set once the underlying
condition clears so "still active" vs "happened once and recovered" is a
plain query, not something read off log timestamps by hand.

Detection is transition-based, not level-based: an alert fires once when a
condition goes from OK to bad, and resolves once when it goes back to OK -
never re-fires every tick while something stays broken (that would just be
fault_log's own per-occurrence counting again, under a different name).
check_and_alert() is the polling half (kill switch, WS connectivity, called
from main.py's tick loop); the crash half is a direct hook inside
services/task_supervisor.py's own exception handler, since that is the
exact moment a crash happens and already carries the component/operation/
exception this module needs - polling for it here would just be a slower,
less precise version of the same information.

Notification delivery is a single, deliberately generic mechanism: an
opt-in webhook POST (alerting.webhook_url, unset/None by default - the
same "ships fully built, opt-in" precedent as risk.max_total_exposure_pct
and friends). A plain JSON POST is compatible with Slack/Discord incoming
webhooks, ntfy.sh, PagerDuty's Events API, or a custom endpoint without
this module needing to know which - see CHEATSHEET.md for exact payload
shapes per destination. Never blocks the caller: fired as an independent
background task (task_supervisor.supervise, not restarted - a failed
notification isn't worth retrying forever) so a slow or unreachable
webhook endpoint can never stall the trading loop, and always
best-effort - a failed delivery still leaves the alert recorded and
visible via GET /api/alerts/*, so nothing is ever silently lost even if
notification delivery itself is broken.
"""
import sqlite3
import time
from pathlib import Path

from services import fault_log, task_supervisor
from services.http_client import get_client

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "alert_log.db"

# Module-level, not state[...] - this is internal bookkeeping for edge
# detection only (was the condition already bad last time we checked), not
# something any API consumer needs to read directly (GET /api/alerts/active
# answers "what's currently wrong" from the DB itself, which is the real
# source of truth). Deliberately resets on every process restart, same as
# every other _last_*/*_cache module-level dict in this app (discovery_
# cache's _last_book_write, game_state's _last_write_at, ...) - if a
# condition is still bad after a restart, re-alerting once is the safer
# default over silently assuming a human already knows.
_last_known_bad: dict[str, bool] = {}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            context TEXT,
            triggered_at REAL NOT NULL,
            resolved_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_category ON alerts (category, resolved_at)")
    return conn


def record_alert(category: str, severity: str, message: str, context: str | None = None,
                  now: float | None = None) -> int:
    now = now if now is not None else time.time()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO alerts (category, severity, message, context, triggered_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (category, severity, message, context, now),
        )
        alert_id = cur.lastrowid
    task_supervisor.supervise(
        lambda: _dispatch_notification(category, severity, message, now),
        component="alerting", operation="dispatch_notification",
    )
    return alert_id


def resolve_category(category: str, now: float | None = None) -> int:
    """Resolves every currently-unresolved alert in this category (in
    practice always 0 or 1, given check_and_alert only ever records a new
    one once the prior one is already resolved - see its own docstring).
    Returns how many rows were resolved."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE alerts SET resolved_at = ? WHERE category = ? AND resolved_at IS NULL",
            (now, category),
        )
        return cur.rowcount


def active_alerts() -> list[dict]:
    cols = ["id", "category", "severity", "message", "context", "triggered_at", "resolved_at"]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM alerts WHERE resolved_at IS NULL ORDER BY triggered_at DESC",
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def recent(limit: int = 50) -> list[dict]:
    cols = ["id", "category", "severity", "message", "context", "triggered_at", "resolved_at"]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM alerts ORDER BY triggered_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


async def _dispatch_notification(category: str, severity: str, message: str, triggered_at: float) -> None:
    """Best-effort webhook delivery - see this module's own docstring for
    why this never raises into the caller and never blocks the trading
    loop. No-op when alerting.webhook_url isn't configured (the opt-in
    default)."""
    from services.config_store import config_store

    webhook_url = ((config_store.get().get("alerting") or {}).get("webhook_url") or "").strip()
    if not webhook_url:
        return
    try:
        client = get_client()
        await client.post(webhook_url, json={
            "text": f"[{severity.upper()}] {category}: {message}",  # Slack/Discord-compatible field
            "category": category, "severity": severity, "message": message, "triggered_at": triggered_at,
        }, timeout=10.0)
    except Exception as exc:
        fault_log.record("alerting", "dispatch_notification", exc, context=category)


async def check_and_alert(cfg: dict) -> None:
    """Polls the two edge-detected conditions once per tick - kill switch
    and WS connectivity. Crash detection lives in task_supervisor.py
    itself (see this module's own docstring for why). Cheap: a handful of
    dict/attribute reads plus, at most, one DB write on an actual state
    transition - the overwhelmingly common case (nothing changed) costs a
    few comparisons and returns."""
    if not (cfg.get("alerting") or {}).get("enabled", True):
        return
    from services.app_state import risk, state

    halted = bool(risk.halted)
    _check_transition("kill_switch", halted, "critical",
                       lambda: f"Kill switch tripped: {risk.halt_reason or 'no reason recorded'}",
                       "Kill switch cleared")

    trade_status = state.get("trade_stream_status") or {}
    if trade_status.get("enabled"):
        _check_transition("trade_stream_connectivity", not trade_status.get("connected"), "warning",
                           lambda: f"Trade stream disconnected: {trade_status.get('error') or 'unknown reason'}",
                           "Trade stream reconnected")

    index_status = state.get("index_stream_status")
    if index_status is not None and index_status.get("enabled", True):
        # index_stream_status has no "enabled" key of its own (see
        # index_stream_handlers._handle_index_stream_status) - it simply
        # doesn't exist in state at all until the stream's first status
        # callback fires, which only happens when the stream is actually
        # configured/running (main.py's lifespan gates task creation on
        # index_stream.enabled). Its absence is "not running", not "down."
        _check_transition("index_stream_connectivity", not index_status.get("connected"), "warning",
                           lambda: f"Index stream disconnected: {index_status.get('error') or 'unknown reason'}",
                           "Index stream reconnected")


def _check_transition(category: str, is_bad: bool, severity: str, bad_message, resolved_message: str) -> None:
    was_bad = _last_known_bad.get(category, False)
    _last_known_bad[category] = is_bad
    if is_bad and not was_bad:
        record_alert(category, severity, bad_message())
    elif was_bad and not is_bad:
        # resolve_category closes out the existing row - a resolution isn't
        # a new alert state, so this deliberately does NOT go through
        # record_alert (which would insert a fresh unresolved row that
        # nothing would ever resolve). Still worth notifying about, though -
        # dispatched directly, same fire-and-forget mechanism.
        resolve_category(category)
        task_supervisor.supervise(
            lambda: _dispatch_notification(category, "info", resolved_message, time.time()),
            component="alerting", operation="dispatch_notification",
        )
