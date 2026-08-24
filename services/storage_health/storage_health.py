"""
Read-only inventory, growth detection, and integrity diagnostics for every
data/*.db file this app owns - the "informativeness" half of CLAUDE.md's
"accumulated history is a first-class asset" rule. The reference failure
this module exists to make visible before a human notices disk usage by
hand: game_state.db grew to 5.7GB from repeated full crypto payload
persistence (git log, static/status.html phase 126), unnoticed until a
direct 2026-08-23 investigation found it. docs/superpowers/plans/2026-08-24-
quality-control-plane.md Task 11; see this package's CHEATSHEET.md.

Three cost tiers, deliberately kept separate:
- inventory_data_dir()/database_health(include_table_counts=False) - file
  stat + lightweight PRAGMAs only (page_count/page_size/freelist_count,
  table names). Safe to call on every GET /api/health/storage and every
  GET /api/quality/summary.
- database_health(include_table_counts=True) - adds a per-table COUNT(*).
  Only ever invoked by the explicit deep scan (routes.py's
  POST /api/health/storage/scan, run as a task_supervisor background task),
  never the default GET.
- quick_check() - PRAGMA quick_check, bounded but still a real read of the
  whole file. Only ever invoked by the explicit
  POST /api/health/storage/integrity-check route - never automatic, never
  on a hot path, never run as a side effect of anything else in this
  module.

Never vacuums, prunes, deletes, migrates, or repairs a DB - CLAUDE.md's
"data/*.db files are live" rule and this plan's own explicit constraint.
Every connection this module opens is read-only (sqlite3's own `mode=ro`
URI), which also makes that constraint mechanically true rather than just
disciplined: a read-only connection cannot write even if a caller tried to.

Deliberately imports only services.observability.observability (already
side-effect-free at import time, see that module's own docstring) and
services.quality.models - never services.app_state or services.backup,
so this module stays importable and unit-testable with synthetic
dicts/tmp-path fixtures alone, the same discipline
services/observability/observability.py already established for Task 9.
Backup-recency data (services.backup.backup.latest()) is fetched by the
routes layer and passed in as a plain argument instead.
"""
import sqlite3
import time
from pathlib import Path

from services.observability import observability
from services.quality.models import QualityFinding

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_GROWTH_WINDOW_HOURS = 24.0  # how far back storage_growth_finding looks for a baseline sample
_GROWTH_MIN_SPAN_HOURS = 1.0  # ignore a baseline sample newer than this - too close in time to be a rate, not a point
_GROWTH_DOUBLING_RATIO = 2.0  # conservative, history-relative criterion (plan Task 11 Step 6: no
# threshold "from intuition") - only a db that has at least doubled against its OWN prior observed
# size counts as anomalous growth, never a fixed byte cutoff.
_BACKUP_OVERDUE_MULTIPLIER = 2.0  # generous on purpose - a single missed cycle from a slow tick
# shouldn't page anyone; only "backup stopped running" should.

_SIZE_SAMPLE_INTERVAL_SEC = 900  # 15 min - see maybe_capture_sizes


def _ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def quick_check(path: Path) -> dict:
    """PRAGMA quick_check - bounded, but still reads the whole file, so
    this is only ever called explicitly (POST /api/health/storage/
    integrity-check), never automatically. Never raises: a missing file or
    one that isn't a valid SQLite database is reported as an explicit
    error dict instead of an exception reaching the caller."""
    if not path.exists():
        return {"ok": False, "error": "file not found"}
    try:
        conn = _ro_connect(path)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}
    detail = row[0] if row else None
    return {"ok": detail == "ok", "detail": detail}


def _pragma_info(conn: sqlite3.Connection) -> dict:
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return {"page_count": page_count, "page_size": page_size, "freelist_count": freelist_count}


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def database_health(path: Path, include_table_counts: bool = False) -> dict:
    """Fast by default (file stat + lightweight PRAGMAs + a table-name
    listing, no COUNT(*)) - include_table_counts=True adds a per-table row
    count and is only ever used by the explicit deep scan
    (POST /api/health/storage/scan), never the default GET. Never raises:
    a missing file, or a file that fails even the lightweight PRAGMA open
    (i.e. isn't a valid SQLite database), is reported via the `error`
    field rather than an exception - see storage_integrity_finding, which
    treats that same field as free, incidental corruption evidence."""
    name = path.name
    result = {
        "name": name, "path": str(path), "exists": False, "size_bytes": None,
        "modified_at": None, "page_count": None, "page_size": None,
        "freelist_count": None, "tables": None, "table_row_counts": None,
        "error": None,
    }
    if not path.exists():
        result["error"] = "file not found"
        return result
    stat = path.stat()
    result["exists"] = True
    result["size_bytes"] = stat.st_size
    result["modified_at"] = stat.st_mtime
    try:
        conn = _ro_connect(path)
        try:
            result.update(_pragma_info(conn))
            tables = _table_names(conn)
            result["tables"] = tables
            if include_table_counts:
                result["table_row_counts"] = {
                    table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in tables
                }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    return result


def inventory_data_dir(data_dir: Path) -> list[dict]:
    """The GET /api/health/storage / GET /api/quality/summary default:
    every data/*.db file, fast path only (include_table_counts=False) -
    see this module's own docstring for the three cost tiers."""
    if not data_dir.exists():
        return []
    return [database_health(p, include_table_counts=False) for p in sorted(data_dir.glob("*.db"))]


def resolve_db_path(data_dir: Path, name: str) -> Path | None:
    """Path-traversal guard for the integrity-check route, which takes a
    caller-supplied db name over HTTP. Only a bare `*.db` filename directly
    inside data_dir is accepted - no path separators, no `..`, nothing that
    could resolve outside data_dir once joined."""
    if not name or not name.endswith(".db") or "/" in name or "\\" in name:
        return None
    candidate = data_dir / name
    if candidate.resolve().parent != data_dir.resolve():
        return None
    return candidate


def capture_size_samples(sizes: dict[str, int | None], now: float | None = None) -> None:
    """Persists each db's current size into observability's shared
    metric_samples store as db.<name>.size_bytes - the same
    record_samples_bulk sink Task 9's capture_from_runtime uses. This is
    what lets storage_growth_finding below compare "now" against "however
    long ago" instead of only ever seeing a single point in time. Entries
    with no size (a missing/errored file) are skipped rather than
    recording a fabricated 0."""
    now = now if now is not None else time.time()
    samples = {
        f"db.{name}.size_bytes": float(size) for name, size in sizes.items() if size is not None
    }
    observability.record_samples_bulk(samples, observed_at=now)


def maybe_capture_sizes(state: dict, data_dir: Path, now: float | None = None) -> None:
    """Cheap interval-gated periodic size sampler wired into the trading
    loop next to backup/observability's own _maybe_* siblings - without
    this, storage_growth_finding would never have real history to compare
    against outside of tests. Deliberately file-stat only (no sqlite
    connection at all), unlike inventory_data_dir - this runs on a timer
    regardless of whether anyone is looking at a dashboard, so it stays as
    close to free as possible.

    Unlike backup.py's/observability.py's own restart-safe cold-start
    seeding (which exists because their guarded operations are genuinely
    expensive - a full db copy, an extra metric row), this uses a plain
    in-memory gate that resets to "never" on every restart. A stat() over
    every data/*.db file costs microseconds, so firing once extra after
    every uvicorn --reload is not worth the same seeding complexity - see
    this package's CHEATSHEET.md for the full reasoning."""
    now = now if now is not None else time.time()
    sh_state = state.setdefault("storage_health", {"last_sampled_at": 0.0})
    if now - sh_state.get("last_sampled_at", 0.0) < _SIZE_SAMPLE_INTERVAL_SEC:
        return
    sizes = {p.name: p.stat().st_size for p in sorted(data_dir.glob("*.db"))} if data_dir.exists() else {}
    capture_size_samples(sizes, now=now)
    sh_state["last_sampled_at"] = now


# --- storage anomaly findings (QCP Task 11) ---------------------------------
#
# Same "unknown is better than fabricated" discipline as
# services/observability/observability.py's runtime_findings: a rule that
# lacks enough evidence to judge omits a finding rather than asserting
# health or failure.


def storage_growth_finding(name: str, current_size: int, now: float | None = None) -> QualityFinding | None:
    now = now if now is not None else time.time()
    since_ts = now - _GROWTH_WINDOW_HOURS * 3600
    samples = observability.history(f"db.{name}.size_bytes", since_ts=since_ts, limit=1000)
    if not samples:
        return None
    oldest = samples[0]
    oldest_size = oldest["value"]
    if not oldest_size:
        return None
    elapsed_hours = (now - oldest["observed_at"]) / 3600
    if elapsed_hours < _GROWTH_MIN_SPAN_HOURS:
        return None
    if current_size < oldest_size * _GROWTH_DOUBLING_RATIO:
        return None
    return QualityFinding(
        finding_id=f"storage-growth:data/{name}",
        check="storage-growth", severity="warning", confidence="high", source="runtime",
        scope=f"data/{name}",
        summary=(
            f"data/{name} grew from {int(oldest_size)} to {int(current_size)} bytes "
            f"(>={_GROWTH_DOUBLING_RATIO}x) over {elapsed_hours:.1f}h"
        ),
        evidence={
            "oldest_size_bytes": oldest_size, "current_size_bytes": current_size,
            "elapsed_hours": round(elapsed_hours, 2), "window_hours": _GROWTH_WINDOW_HOURS,
        },
    )


def storage_integrity_finding(entry: dict) -> QualityFinding | None:
    """Free, incidental corruption evidence from database_health()'s own
    lightweight PRAGMA open - NOT a proxy for quick_check. A file that
    fails to even open for a page-count PRAGMA is real evidence of
    corruption; a file that opens fine here can still fail a full
    quick_check, which this rule does not attempt (that scan is expensive
    and only ever runs on explicit request - see this module's own
    docstring)."""
    if not entry.get("exists") or not entry.get("error"):
        return None
    name = entry["name"]
    return QualityFinding(
        finding_id=f"storage-integrity:data/{name}",
        check="storage-integrity", severity="error", confidence="medium", source="runtime",
        scope=f"data/{name}",
        summary=f"data/{name} failed a basic PRAGMA read: {entry['error']}",
        evidence={"error": entry["error"]},
    )


def backup_overdue_finding(
    last_run: dict | None, interval_sec: float, now: float | None = None,
) -> QualityFinding | None:
    """last_run is services.backup.backup.latest()'s own return shape
    ({"finished_at": ..., ...} | None), passed in by the routes layer
    rather than fetched here - see this module's docstring for why. No
    backup ever having run is not itself a finding here; that is backup's
    own concern (services/backup/CHEATSHEET.md) - this only flags a backup
    that WAS running and then stopped."""
    if last_run is None:
        return None
    now = now if now is not None else time.time()
    age_sec = now - last_run["finished_at"]
    overdue_after = interval_sec * _BACKUP_OVERDUE_MULTIPLIER
    if age_sec <= overdue_after:
        return None
    return QualityFinding(
        finding_id="backup-overdue",
        check="backup-overdue", severity="warning", confidence="high", source="runtime",
        scope="backup",
        summary=(
            f"last backup finished {age_sec / 3600:.1f}h ago, more than "
            f"{_BACKUP_OVERDUE_MULTIPLIER}x the configured {interval_sec / 3600:.1f}h interval"
        ),
        evidence={"last_finished_at": last_run["finished_at"], "age_sec": age_sec, "interval_sec": interval_sec},
    )


def storage_findings(
    entries: list[dict], last_backup_run: dict | None, backup_interval_sec: float, now: float | None = None,
) -> list[QualityFinding]:
    """Composes every storage anomaly rule into one findings list -
    read-only, informational; this never remediates anything itself.
    `entries` is inventory_data_dir()'s own return shape."""
    findings: list[QualityFinding] = []
    for entry in entries:
        integrity = storage_integrity_finding(entry)
        if integrity is not None:
            findings.append(integrity)
        if entry.get("size_bytes") is not None:
            growth = storage_growth_finding(entry["name"], entry["size_bytes"], now=now)
            if growth is not None:
                findings.append(growth)
    overdue = backup_overdue_finding(last_backup_run, backup_interval_sec, now=now)
    if overdue is not None:
        findings.append(overdue)
    return findings
