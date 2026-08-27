"""
Evidence-triggered read-only research sweep - Quality Control Plane Task 16
(docs/superpowers/plans/2026-08-24-quality-control-plane.md). Orchestrates
existing read-only analytics into one coherent, persisted snapshot so a
human or agent doesn't have to re-run each one by hand - invents no new
algorithm or statistic of its own. See this package's README.md for the
full per-section provenance and the trigger-policy design notes.

Never applies anything: build_report/run_and_store only ever call read
functions (compute_summary, generate_calibration_report,
generate_recommendations, ...) - never config_store.update or any
*.log_applied_change. tests/test_research.py's
test_build_report_never_touches_config_store_or_logs_an_applied_change
guards this directly, the same way advisory_engine's own read-only routes
(GET /api/advisory/recommendations) already prove the pattern is safe to
call on every request.
"""
import json
import sqlite3
import time
from pathlib import Path

from services import candidate_log, config_performance, regime_analytics, series_evaluator
from services import settlement_edge, signal_log, suggestion_decisions, trade_analytics
from services.advisory import advisory_engine
from services.analytics.market_analyst_orchestrator import _series_evaluator_rows_for_advisory
from services.diagnostics import diagnostics
from services.whale_calibration import confidence_calibration

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "research_reports.db"

_DEFAULT_MIN_NEW_RESOLVED_SIGNALS = 100
_DEFAULT_MIN_NEW_CLOSED_TRADES = 50


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # WAL mode (2026-08-11, real live incident - see services/candidate_log.py's
    # own comment on this exact line): rollback-journal mode serializes ALL
    # writers and readers against each other for the whole transaction; WAL
    # lets readers proceed concurrently with a writer. Same standard
    # hardening every other writable persistence module in this repo
    # applies - found missing here during QCP Task 22's final-verification
    # review, not something this module needed a live incident of its own
    # to justify.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at REAL NOT NULL,
            resolved_signals_count INTEGER NOT NULL,
            closed_trades_count INTEGER NOT NULL,
            report_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_reports_generated_at ON research_reports(generated_at)"
    )
    return conn


def _row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "generated_at": row[1],
        "resolved_signals_count": row[2],
        "closed_trades_count": row[3],
        "report": json.loads(row[4]),
    }


def recent(limit: int = 20) -> list[dict]:
    limit = max(1, min(limit, 200))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, generated_at, resolved_signals_count, closed_trades_count, report_json "
            "FROM research_reports ORDER BY generated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def latest() -> dict | None:
    rows = recent(limit=1)
    return rows[0] if rows else None


def current_counts() -> dict:
    """Cheap - one indexed COUNT() plus an in-memory list scan over
    broker.trade_log (already loaded in memory, no DB read) - safe to call
    on every trading-loop tick, same "cheap gate before the expensive path"
    shape as calibration_history.due(). "closed trades" mirrors
    trade_analytics.build_trade_history's own definition (a CLOSE trade,
    not excluded by correct_erroneous_close) without paying for the full
    enrichment/pairing pass build_trade_history does - should_run only
    needs the count, not the rows themselves."""
    from services.app_state import broker  # local import - see this module's own README "Why this imports app_state lazily"

    closed = sum(
        1 for t in broker.trade_log
        if not getattr(t, "excluded", False) and t.reason.startswith("closed:")
    )
    return {
        "resolved_signals": signal_log.total_count(resolved_only=True),
        "closed_trades": closed,
    }


def should_run(cfg: dict, checkpoints: dict, current_counts: dict) -> bool:
    """OR semantics: due once EITHER threshold has newly accumulated since
    the last checkpoint. checkpoints={} (no prior watermark) treats every
    current count as entirely new - a deliberate choice, not an oversight:
    on a genuinely fresh install/first-ever-enable, if there's already
    enough real history sitting in signal_log/trade_log to clear a
    threshold, that's a legitimate first run, not noise. The DIFFERENT
    failure mode this must not reproduce - a lost in-memory checkpoint on
    every uvicorn --reload firing an immediate re-run regardless of how
    recently one actually completed (services/backup/backup.py's
    documented 2026-08-23 incident, same "_maybe_*" scheduler shape) - is
    handled one layer up, by _maybe_run_research seeding checkpoints from
    the last *persisted* report's own recorded watermark on cold start,
    not by this pure function defaulting to a lost/{} checkpoint on every
    restart."""
    research_cfg = cfg.get("research") or {}
    checkpoints = checkpoints or {}
    new_resolved = current_counts.get("resolved_signals", 0) - checkpoints.get("resolved_signals", 0)
    new_trades = current_counts.get("closed_trades", 0) - checkpoints.get("closed_trades", 0)
    return (
        new_resolved >= research_cfg.get("min_new_resolved_signals", _DEFAULT_MIN_NEW_RESOLVED_SIGNALS)
        or new_trades >= research_cfg.get("min_new_closed_trades", _DEFAULT_MIN_NEW_CLOSED_TRADES)
    )


def build_report(cfg: dict, now: float | None = None) -> dict:
    """Pure orchestration over EXISTING read-only analyzers - no new
    statistic, no I/O beyond what each analyzer already does on its own
    (mostly SQLite reads; nothing here makes a live Kalshi API call,
    matching diagnostics.run_offline's own "no network" contract, which
    this reuses directly). Every section here is exactly what a human
    would otherwise have to call by hand across seven different modules -
    see this package's README.md for the one-line reason each section
    was chosen."""
    now = now if now is not None else time.time()
    from services.app_state import broker  # local import, same reason as current_counts above

    trade_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    signal_rows = signal_log.resolved_signals_with_factors()
    current_fp = config_performance.fingerprint(cfg)
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}

    cc_cfg = cfg.get("confidence_calibration") or {}
    calibration = confidence_calibration.generate_calibration_report(
        signal_rows, cc_cfg.get("min_resolved_signals", 50), cfg.get("whale_confidence_weights"),
    )

    # Same read-only assembly as GET /api/advisory/recommendations
    # (services/advisory/routes.py) - deliberately never the auto-apply
    # path (main.py's trading_loop), which would also call config_store.
    # update/log_applied_change. advisory.enabled gates this the same way
    # that route does, for the same reason (docs/advisory-engine-plan.md
    # §3's per-variant data-threshold gate lives inside
    # generate_recommendations itself either way).
    adv_cfg = cfg.get("advisory") or {}
    if adv_cfg.get("enabled"):
        advisory = advisory_engine.generate_recommendations(
            trade_rows, cfg, current_fp, variants, adv_cfg.get("min_resolved_trades_per_variant", 30),
            gate_summaries=candidate_log.gate_summary(),
            last_applied_by_path=config_performance.all_last_applied_by_path(),
            series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
            category_rows=regime_analytics.by_category(trade_rows),
            declined_ids=suggestion_decisions.declined_ids(),
        )
    else:
        advisory = {"recommendations": [], "gated_reason": "advisory engine is disabled", "resolved_count": None}

    return {
        "generated_at": now,
        "diagnostics": diagnostics.run_offline(cfg, now=now),
        "trade_analytics": trade_analytics.compute_summary(trade_rows),
        "confidence_calibration": calibration,
        "advisory": advisory,
        "candidate_population_gate": candidate_log.population_gate_summary(),
        "series_evaluator": series_evaluator.overview(),
        "settlement_edge": settlement_edge.edge_report(),
        "config_epoch": {
            "current_fingerprint": current_fp,
            "variant_count": len(variants),
            "applied_changes_count": config_performance.applied_changes_count(),
            "recent_applied_changes": config_performance.recent_applied_changes(limit=20),
        },
    }


def run_and_store(cfg: dict) -> dict:
    now = time.time()
    report = build_report(cfg, now=now)
    counts = current_counts()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO research_reports "
            "(generated_at, resolved_signals_count, closed_trades_count, report_json) VALUES (?, ?, ?, ?)",
            (now, counts["resolved_signals"], counts["closed_trades"], json.dumps(report)),
        )
    return report


async def _run_research_background(cfg: dict) -> None:
    """Background-task wrapper, same split/shape as services/backup/backup.py's
    _run_backup_background: owns releasing the "running" flag regardless of
    outcome via finally, and asyncio.to_thread keeps run_and_store's several
    blocking SQLite reads/writes off the event loop so building this report
    can't stall the trading loop's own tick timing."""
    import asyncio

    from services.app_state import state

    research_state = state["research"]
    try:
        await asyncio.to_thread(run_and_store, cfg)
        last = latest()
        if last is not None:
            research_state["checkpoints"] = {
                "resolved_signals": last["resolved_signals_count"],
                "closed_trades": last["closed_trades_count"],
            }
    finally:
        research_state["running"] = False


def _maybe_run_research(cfg: dict) -> None:
    """Kicks off _run_research_background as an independent background task
    if evidence has accumulated and none is already running - never awaited
    by the calling tick, same fire-and-forget shape as _maybe_run_backup/
    _maybe_scan_catalog_batch. Disabled by default (config/settings.yaml's
    research.enabled: false) until manually reviewed, per this task's own
    design spec.

    Cold-start-safe the same way _maybe_run_backup fixed live 2026-08-23
    (see that function's own docstring for the full incident): checkpoints
    is seeded from the most recently *persisted* report's own recorded
    watermark the first time this runs in a process, not trusted at its
    None in-memory default - a restart means "go check what was last
    recorded," not "assume zero and let should_run's own {}-checkpoint
    fresh-install behavior fire an unwarranted immediate run against
    history a report has already covered."""
    from services.app_state import state

    research_cfg = cfg.get("research") or {}
    if not research_cfg.get("enabled", False):
        return
    research_state = state.setdefault("research", {"running": False, "task": None, "checkpoints": None})
    if research_state["running"]:
        return
    if research_state["checkpoints"] is None:
        last = latest()
        research_state["checkpoints"] = (
            {"resolved_signals": last["resolved_signals_count"], "closed_trades": last["closed_trades_count"]}
            if last is not None else {}
        )
    if not should_run(cfg, research_state["checkpoints"], current_counts()):
        return

    import services.task_supervisor as task_supervisor

    research_state["running"] = True
    research_state["task"] = task_supervisor.supervise(
        lambda: _run_research_background(cfg), component="research", operation="run",
    )
