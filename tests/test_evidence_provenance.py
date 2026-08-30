"""services/quality/evidence_provenance.py - the "is a known data-
completeness defect currently active in this process" read that #214's
advisory/calibration auto-tuning loop was missing. Each source is
independently mocked here (never the real global counters) so this file's
result never depends on what other test files in the same pytest run did
to settlement_resolver/index_feed/capture_writer's shared module state."""
import pytest

from services import capture_writer, settlement_resolver
from services.index_feed import ingestion as index_feed_ingestion
from services.quality import evidence_provenance as ep


def _clean_settlement_snapshot():
    return {
        "pending": 0, "enqueued_total": 0, "resolved_total": 0, "dropped_total": 0,
        "dropped_after_max_attempts": 0, "skipped_non_binary_result": 0, "last_run_at": 0.0,
        "non_binary_by_result": {}, "non_binary_recent": [],
    }


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(settlement_resolver, "snapshot", _clean_settlement_snapshot)
    monkeypatch.setattr(index_feed_ingestion, "snapshot", lambda: {"indices": {}, "buffered_ticks": 0, "dropped_rows": 0})
    monkeypatch.setattr(capture_writer, "dropped_count", lambda: {"raw_trades": 0, "rejection_events": 0, "rejected_candidates": 0})
    monkeypatch.setattr(capture_writer, "overflow_dropped_count", lambda: {"raw_trades": 0, "rejection_events": 0, "rejected_candidates": 0})


def test_clean_state_is_not_degraded():
    result = ep.current_completeness_state()
    assert result["degraded"] is False
    assert result["defects"] == []
    assert isinstance(result["checked_at"], float)


def test_settlement_resolver_giving_up_after_retries_is_flagged(monkeypatch):
    monkeypatch.setattr(settlement_resolver, "snapshot", lambda: {**_clean_settlement_snapshot(), "dropped_after_max_attempts": 3})

    result = ep.current_completeness_state()

    assert result["degraded"] is True
    assert result["defects"] == [{
        "component": "settlement_resolver", "field": "dropped_after_max_attempts", "count": 3,
        "detail": "3 settlement(s) gave up after the retry budget - Kalshi never returned a result, "
                  "or every resolver attempt raised",
    }]


def test_settlement_resolver_dropped_total_alone_is_not_flagged(monkeypatch):
    # dropped_total is a conservation sum (enqueued == resolved + pending +
    # dropped_total), NOT a defect count - it is also incremented by the
    # expected non-binary-result skip branch. Only dropped_after_max_attempts
    # is a real defect signal (settlement_resolver.py's own comment).
    monkeypatch.setattr(settlement_resolver, "snapshot", lambda: {**_clean_settlement_snapshot(), "dropped_total": 64})

    result = ep.current_completeness_state()

    assert result["degraded"] is False
    assert result["defects"] == []


def test_index_feed_dropped_rows_is_flagged(monkeypatch):
    monkeypatch.setattr(index_feed_ingestion, "snapshot", lambda: {"indices": {}, "buffered_ticks": 0, "dropped_rows": 5})

    result = ep.current_completeness_state()

    assert result["degraded"] is True
    assert result["defects"] == [{
        "component": "index_feed", "field": "dropped_rows", "count": 5,
        "detail": "5 index tick(s) discarded - a reconnect gap the backfill path did not recover",
    }]


def test_capture_writer_dropped_count_per_store_is_flagged(monkeypatch):
    monkeypatch.setattr(capture_writer, "dropped_count", lambda: {"raw_trades": 2, "rejection_events": 0, "rejected_candidates": 0})

    result = ep.current_completeness_state()

    assert result["degraded"] is True
    assert result["defects"] == [{
        "component": "capture_writer", "field": "dropped_count[raw_trades]", "count": 2,
        "detail": "2 batch(es) dropped writing to 'raw_trades' after a flush failure",
    }]


def test_capture_writer_overflow_dropped_count_per_store_is_flagged(monkeypatch):
    monkeypatch.setattr(capture_writer, "overflow_dropped_count", lambda: {"raw_trades": 0, "rejection_events": 7, "rejected_candidates": 0})

    result = ep.current_completeness_state()

    assert result["degraded"] is True
    assert result["defects"] == [{
        "component": "capture_writer", "field": "overflow_dropped_count[rejection_events]", "count": 7,
        "detail": "7 row(s) discarded from 'rejection_events' past its retained-buffer cap",
    }]


def test_multiple_simultaneous_defects_all_appear(monkeypatch):
    monkeypatch.setattr(settlement_resolver, "snapshot", lambda: {**_clean_settlement_snapshot(), "dropped_after_max_attempts": 1})
    monkeypatch.setattr(index_feed_ingestion, "snapshot", lambda: {"indices": {}, "buffered_ticks": 0, "dropped_rows": 2})

    result = ep.current_completeness_state()

    assert result["degraded"] is True
    assert len(result["defects"]) == 2
    assert {d["component"] for d in result["defects"]} == {"settlement_resolver", "index_feed"}


def test_findings_wraps_each_defect_as_a_quality_finding(monkeypatch):
    monkeypatch.setattr(settlement_resolver, "snapshot", lambda: {**_clean_settlement_snapshot(), "dropped_after_max_attempts": 3})

    result = ep.findings()

    assert len(result) == 1
    finding = result[0]
    assert finding.finding_id == "evidence_provenance:settlement_resolver:dropped_after_max_attempts"
    assert finding.check == "evidence-completeness"
    assert finding.severity == "warning"
    assert finding.confidence == "high"
    assert finding.source == "runtime"
    assert finding.scope == "settlement_resolver"
    assert finding.evidence == {"count": 3, "field": "dropped_after_max_attempts"}


def test_findings_empty_when_clean():
    assert ep.findings() == []
