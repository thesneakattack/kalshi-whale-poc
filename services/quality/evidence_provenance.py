"""Evidence-completeness signal for the advisory/calibration auto-tuning
loop (#214). Composes three already-existing, already-public defect
counters into one "is a known data-completeness defect currently active in
this process" read - no new instrumentation, no persistence, no network
I/O. See docs/archive/lane-4-analytics-advisory-research/specs/2026-08-30-self-feeding-loop-provenance-
design.md (moved there 2026-09-06, planning-lanes migration) for why these three fields specifically (settlement_resolver's
dropped_total is a conservation sum, not a defect count - its own comment
says so) and why this reads current process state rather than a historical
per-trade window.
"""
import time

from services import capture_writer, settlement_resolver
from services.index_feed import ingestion as index_feed_ingestion
from services.quality.models import QualityFinding


def current_completeness_state() -> dict:
    defects = []

    dropped_after_max_attempts = settlement_resolver.snapshot()["dropped_after_max_attempts"]
    if dropped_after_max_attempts:
        defects.append({
            "component": "settlement_resolver", "field": "dropped_after_max_attempts",
            "count": dropped_after_max_attempts,
            "detail": (
                f"{dropped_after_max_attempts} settlement(s) gave up after the retry budget - "
                "Kalshi never returned a result, or every resolver attempt raised"
            ),
        })

    dropped_rows = index_feed_ingestion.snapshot()["dropped_rows"]
    if dropped_rows:
        defects.append({
            "component": "index_feed", "field": "dropped_rows", "count": dropped_rows,
            "detail": f"{dropped_rows} index tick(s) discarded - a reconnect gap the backfill path did not recover",
        })

    for store, count in capture_writer.dropped_count().items():
        if count:
            defects.append({
                "component": "capture_writer", "field": f"dropped_count[{store}]", "count": count,
                "detail": f"{count} batch(es) dropped writing to '{store}' after a flush failure",
            })
    for store, count in capture_writer.overflow_dropped_count().items():
        if count:
            defects.append({
                "component": "capture_writer", "field": f"overflow_dropped_count[{store}]", "count": count,
                "detail": f"{count} row(s) discarded from '{store}' past its retained-buffer cap",
            })

    return {"degraded": bool(defects), "defects": defects, "checked_at": time.time()}


def findings() -> list[QualityFinding]:
    """Wraps current_completeness_state()'s defects as QualityFindings for
    GET /api/quality/summary - same composition pattern as
    services/alerting/alerting.py's alert_findings()."""
    return [
        QualityFinding(
            finding_id=f"evidence_provenance:{d['component']}:{d['field']}",
            check="evidence-completeness", severity="warning", confidence="high",
            source="runtime", scope=d["component"], summary=d["detail"],
            evidence={"count": d["count"], "field": d["field"]},
            remediation=(
                "This defect degrades advisory/calibration recommendations computed while it is "
                "open - see docs/archive/lane-4-analytics-advisory-research/specs/"
                "2026-08-30-self-feeding-loop-provenance-design.md (moved there 2026-09-06, "
                "planning-lanes migration)"
            ),
        )
        for d in current_completeness_state()["defects"]
    ]
