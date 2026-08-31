# Self-Feeding Loop Evidence Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the advisory/calibration auto-tuning loop a way to see that its own inputs were incomplete (#214), and fix the concrete `series`-blindness gap in calibration reporting (#60).

**Architecture:** One new pure module (`services/quality/evidence_provenance.py`) composes three already-existing, already-public defect counters into a single `current_completeness_state()` read. It is surfaced at the route boundary on three existing endpoints and folded into `/api/quality/summary`'s findings, and it silently gates the two real config-write paths in `main.py`'s `_maybe_run_auto_apply`. Separately, `signal_log.resolved_signals_with_factors()` gains the `series` column it already stores but never selects, and `confidence_calibration.generate_calibration_report()` gains a categorical `by_series` breakdown.

**Tech Stack:** Python 3, FastAPI, pytest, sqlite3 (existing patterns only — no new dependencies).

**Spec:** `docs/superpowers/specs/2026-08-30-self-feeding-loop-provenance-design.md`

## Global Constraints

- `evidence_provenance.current_completeness_state()`/`findings()` are pure, read-only, no network I/O, no new persistence.
- Use the *current* per-defect counters, not the issue's stale reference: `settlement_resolver.snapshot()["dropped_after_max_attempts"]` (never `dropped_total` — that field's own comment says it is a conservation sum, not a defect count), `index_feed.ingestion.snapshot()["dropped_rows"]`, `capture_writer.dropped_count()` / `capture_writer.overflow_dropped_count()` (per-store dicts).
- Provenance is attached at the **route boundary only** — `advisory_engine.generate_recommendations()` and `confidence_calibration.generate_calibration_report()` themselves are never modified to know about `evidence_provenance` (they have other callers: `market_analyst_orchestrator`, `calibration_history`, `research.py`).
- The `main.py` auto-apply refusal is a **silent skip** — no new logging call. Fold `not evidence_provenance.current_completeness_state()["degraded"]` into the existing qualifying condition in each block; do not restructure into a new if/else, do not touch the cooldown timestamp on a refusal.
- The `#60` `by_series` breakdown reuses `confidence_calibration.py`'s existing `_MIN_BAND_SIZE = 3` floor — no new sample-size constant in that file.
- Every task is TDD: failing test first, minimal implementation, passing test, commit.
- Every touched route module (`services/advisory/routes.py`, `services/whale_calibration/routes.py`, `services/quality/routes.py`) is already imported into `main.py` under an alias (`advisory_routes`, `whale_calibration_routes`, `quality_routes`) — tests monkeypatch `main.<alias>.evidence_provenance` (or, in `tests/test_whale_calibration_routes.py`, its own direct `calibration_routes` import) rather than adding new imports.

---

### Task 1: `services/quality/evidence_provenance.py` — the completeness signal

**Files:**
- Create: `services/quality/evidence_provenance.py`
- Test: `tests/test_evidence_provenance.py`

**Interfaces:**
- Produces: `current_completeness_state() -> dict` returning `{"degraded": bool, "defects": list[dict], "checked_at": float}`, where each defect is `{"component": str, "field": str, "count": int, "detail": str}`.
- Produces: `findings() -> list[QualityFinding]` (from `services.quality.models`), one per entry in `current_completeness_state()["defects"]`.
- Consumes: `services.settlement_resolver.snapshot()`, `services.index_feed.ingestion.snapshot()`, `services.capture_writer.dropped_count()`, `services.capture_writer.overflow_dropped_count()` — all already exist, unmodified.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_provenance.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_evidence_provenance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.quality.evidence_provenance'`

- [ ] **Step 3: Write the implementation**

```python
# services/quality/evidence_provenance.py
"""Evidence-completeness signal for the advisory/calibration auto-tuning
loop (#214). Composes three already-existing, already-public defect
counters into one "is a known data-completeness defect currently active in
this process" read - no new instrumentation, no persistence, no network
I/O. See docs/superpowers/specs/2026-08-30-self-feeding-loop-provenance-
design.md for why these three fields specifically (settlement_resolver's
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
                "open - see docs/superpowers/specs/2026-08-30-self-feeding-loop-provenance-design.md"
            ),
        )
        for d in current_completeness_state()["defects"]
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_evidence_provenance.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add services/quality/evidence_provenance.py tests/test_evidence_provenance.py
git commit -m "feat: add evidence-completeness signal for the advisory/calibration loop (#214)"
```

---

### Task 2: Wire into `GET /api/quality/summary`

**Files:**
- Modify: `services/quality/routes.py`
- Test: `tests/test_quality_routes.py`

**Interfaces:**
- Consumes: `evidence_provenance.findings()` from Task 1.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_quality_routes.py`:

```python
def test_quality_summary_surfaces_a_completeness_defect_as_a_warning_finding(monkeypatch):
    monkeypatch.setattr(
        main.quality_routes.evidence_provenance, "current_completeness_state",
        lambda: {
            "degraded": True,
            "defects": [{
                "component": "settlement_resolver", "field": "dropped_after_max_attempts",
                "count": 3, "detail": "3 settlement(s) gave up after the retry budget",
            }],
            "checked_at": 0.0,
        },
    )

    resp = client.get("/api/quality/summary")

    assert resp.status_code == 200
    body = resp.json()
    ids = [f["finding_id"] for f in body["findings"]]
    assert "evidence_provenance:settlement_resolver:dropped_after_max_attempts" in ids
    assert body["status"] in ("warning", "error")


def test_quality_summary_has_no_evidence_provenance_findings_when_clean(monkeypatch):
    monkeypatch.setattr(
        main.quality_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )

    resp = client.get("/api/quality/summary")

    ids = [f["finding_id"] for f in resp.json()["findings"]]
    assert not any(i.startswith("evidence_provenance:") for i in ids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_quality_routes.py -v -k evidence_provenance`
Expected: FAIL with `AttributeError: module 'services.quality.routes' has no attribute 'evidence_provenance'`

- [ ] **Step 3: Write the implementation**

In `services/quality/routes.py`, add the import alongside the existing ones:

```python
from services.quality import evidence_provenance
```

And add one line to the `findings +=` chain, right after the alerting line:

```python
    active_alerts = alerting.active_alerts()
    findings += alerting.alert_findings(active_alerts)
    findings += evidence_provenance.findings()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_quality_routes.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add services/quality/routes.py tests/test_quality_routes.py
git commit -m "feat: fold evidence-completeness defects into GET /api/quality/summary"
```

---

### Task 3: Wire into `services/advisory/routes.py`

**Files:**
- Modify: `services/advisory/routes.py`
- Test: `tests/test_trading_gate.py`

**Interfaces:**
- Consumes: `evidence_provenance.current_completeness_state()` from Task 1.
- Produces: `evidence_provenance` key on `GET /api/advisory/status` and `GET /api/advisory/recommendations` responses.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trading_gate.py`, near the existing `test_advisory_status_reports_disabled_by_default` test:

```python
def test_advisory_status_includes_evidence_provenance_block(monkeypatch):
    _reset_advisory_state()
    monkeypatch.setattr(
        main.advisory_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )

    resp = client.get("/api/advisory/status")

    assert resp.status_code == 200
    assert resp.json()["evidence_provenance"] == {"degraded": False, "defects": [], "checked_at": 0.0}


def test_advisory_recommendations_includes_evidence_provenance_when_disabled(monkeypatch):
    _reset_advisory_state()
    monkeypatch.setattr(
        main.advisory_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": True, "defects": [{"component": "index_feed"}], "checked_at": 1.0},
    )

    resp = client.get("/api/advisory/recommendations")

    assert resp.json()["evidence_provenance"]["degraded"] is True


def test_advisory_recommendations_includes_evidence_provenance_when_enabled(monkeypatch):
    _reset_advisory_state()
    main.config_store.update({"advisory": {"enabled": True}})
    monkeypatch.setattr(
        main.advisory_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": True, "defects": [], "checked_at": 2.0},
    )

    resp = client.get("/api/advisory/recommendations")

    assert resp.status_code == 200
    assert resp.json()["evidence_provenance"]["checked_at"] == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trading_gate.py -v -k evidence_provenance`
Expected: FAIL with `AttributeError: module 'services.advisory.routes' has no attribute 'evidence_provenance'`

- [ ] **Step 3: Write the implementation**

In `services/advisory/routes.py`, add the import:

```python
from services.quality import evidence_provenance
```

Modify `get_advisory_status`'s return (end of the function):

```python
    return {
        "enabled": adv_cfg["enabled"],
        "min_resolved_trades_per_variant": min_resolved,
        "auto_apply_enabled": adv_cfg["auto_apply_enabled"],
        "current_fingerprint": current_fp,
        "variants": variants_out,
        "evidence_provenance": evidence_provenance.current_completeness_state(),
    }
```

Modify `get_advisory_recommendations` — both the disabled early-return and the final return:

```python
@router.get("/api/advisory/recommendations")
async def get_advisory_recommendations():
    adv_cfg = config_store.get()["advisory"]
    if not adv_cfg["enabled"]:
        return {
            "recommendations": [], "gated_reason": "advisory engine is disabled", "resolved_count": None,
            "evidence_provenance": evidence_provenance.current_completeness_state(),
        }

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
        gate_summaries=candidate_log.gate_summary(),
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
        category_rows=regime_analytics.by_category(all_rows),
        declined_ids=suggestion_decisions.declined_ids(),
    )
    result["evidence_provenance"] = evidence_provenance.current_completeness_state()
    return result
```

(`apply_advisory_recommendation` is unchanged — it is a write action, not a read surface; its own recommendation set is recomputed fresh and discarded per-request, so there is nothing durable for a provenance block to attach to there.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trading_gate.py -v -k "advisory"`
Expected: PASS (all advisory tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add services/advisory/routes.py tests/test_trading_gate.py
git commit -m "feat: surface evidence-completeness on GET /api/advisory/status and /recommendations"
```

---

### Task 4: Wire into `services/whale_calibration/routes.py`

**Files:**
- Modify: `services/whale_calibration/routes.py`
- Test: `tests/test_whale_calibration_routes.py`

**Interfaces:**
- Consumes: `evidence_provenance.current_completeness_state()` from Task 1.
- Produces: `evidence_provenance` key on `GET /api/confidence-calibration/status` and `GET /api/confidence-calibration/report` responses.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_whale_calibration_routes.py`:

```python
def test_status_includes_evidence_provenance_block(monkeypatch):
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(calibration_routes.signal_log, "resolved_with_factors_count", lambda: 0)
    monkeypatch.setattr(
        calibration_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )

    result = asyncio.run(calibration_routes.get_confidence_calibration_status())

    assert result["evidence_provenance"] == {"degraded": False, "defects": [], "checked_at": 0.0}


def test_report_includes_evidence_provenance_block(monkeypatch):
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(calibration_routes.signal_log, "resolved_signals_with_factors", lambda: [])
    monkeypatch.setattr(
        calibration_routes.confidence_calibration, "generate_calibration_report",
        lambda rows, min_n, weights: {"report": None, "gated_reason": "not enough data", "resolved_count": 0},
    )
    monkeypatch.setattr(
        calibration_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": True, "defects": [{"component": "capture_writer"}], "checked_at": 5.0},
    )

    result = asyncio.run(calibration_routes.get_confidence_calibration_report())

    assert result["evidence_provenance"]["degraded"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_whale_calibration_routes.py -v -k evidence_provenance`
Expected: FAIL with `AttributeError: module 'services.whale_calibration.routes' has no attribute 'evidence_provenance'`

- [ ] **Step 3: Write the implementation**

In `services/whale_calibration/routes.py`, add the import:

```python
from services.quality import evidence_provenance
```

Modify `get_confidence_calibration_status`'s return:

```python
    return {
        "enabled": cc_cfg["enabled"],
        "min_resolved_signals": cc_cfg["min_resolved_signals"],
        "resolved_count": resolved_count,
        "ready": resolved_count >= cc_cfg["min_resolved_signals"],
        "auto_apply_enabled": cc_cfg.get("auto_apply_enabled", False),
        "evidence_provenance": evidence_provenance.current_completeness_state(),
    }
```

Modify `get_confidence_calibration_report`:

```python
@router.get("/api/confidence-calibration/report")
async def get_confidence_calibration_report():
    cc_cfg = config_store.get()["confidence_calibration"]
    if not cc_cfg["enabled"]:
        return {
            "report": None, "gated_reason": "confidence calibration is disabled", "resolved_count": None,
            "evidence_provenance": evidence_provenance.current_completeness_state(),
        }

    current_weights = config_store.get().get("whale_confidence_weights")

    def _build_report():
        rows = signal_log.resolved_signals_with_factors()
        return confidence_calibration.generate_calibration_report(
            rows, cc_cfg["min_resolved_signals"], current_weights
        )

    result = await tick_executor.run(_build_report)
    result["evidence_provenance"] = evidence_provenance.current_completeness_state()
    return result
```

(`apply_confidence_calibration_suggestion` is unchanged, same reasoning as advisory's apply route in Task 3.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_whale_calibration_routes.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add services/whale_calibration/routes.py tests/test_whale_calibration_routes.py
git commit -m "feat: surface evidence-completeness on GET /api/confidence-calibration/status and /report"
```

---

### Task 5: Auto-apply refuses to write on degraded evidence (`main.py`)

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_scheduler_loops.py`

**Interfaces:**
- Consumes: `evidence_provenance.current_completeness_state()` from Task 1, called as `main.evidence_provenance.current_completeness_state()`.

This is the mechanism-readiness half of the design: both `auto_apply_enabled` flags are `false` today, so this guard is currently unreachable in production. It must still be correct and tested for whenever #51 (a separate, parked human decision) re-arms either flag.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main_scheduler_loops.py`:

```python
# --- #214: auto-apply refuses to write on a known completeness defect -----

def _wire_calibration_auto_apply(monkeypatch, degraded: bool):
    monkeypatch.setattr(main.calibration_history, "due", lambda *a, **k: True)
    monkeypatch.setattr(main.calibration_history, "record_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(main.signal_log, "resolved_signals_with_factors", lambda: [])
    monkeypatch.setattr(main.confidence_calibration, "generate_calibration_report", lambda rows, min_n, weights: {
        "report": {
            "resolved_count": 200, "suggested_weights": {"depth_factor": 0.6},
            "ranked_by_discrimination": ["depth_factor"],
            "per_factor": [{"factor": "depth_factor", "gap_pts": 12.0}],
        },
    })
    monkeypatch.setattr(main.confidence_calibration, "blended_weights_for_auto_apply",
                         lambda current, suggested: {"depth_factor": 0.6})
    monkeypatch.setattr(main.config_performance, "last_applied_at", lambda source: None)
    monkeypatch.setattr(main.config_performance, "fingerprint", lambda cfg: "fp")
    logged = []
    monkeypatch.setattr(main.config_performance, "log_applied_change", lambda **kw: logged.append(kw))
    updates = []
    monkeypatch.setattr(main.config_store, "update", lambda patch: updates.append(patch))
    monkeypatch.setattr(main.config_store, "get", lambda: {})
    bumps = []
    monkeypatch.setattr(main, "bump_generation", lambda: bumps.append(True))
    monkeypatch.setattr(main.evidence_provenance, "current_completeness_state",
                         lambda: {"degraded": degraded, "defects": [], "checked_at": 0.0})
    return updates, logged, bumps


_CALIBRATION_CFG = {
    "confidence_calibration": {
        "enabled": True, "auto_apply_enabled": True, "min_resolved_signals": 7,
        "auto_apply_min_resolved_signals": 150, "auto_apply_cooldown_sec": 86400,
    },
    "advisory": {"enabled": False},
    "whale_confidence_weights": {"depth_factor": 0.5},
}


def test_calibration_auto_apply_writes_new_weights_when_evidence_is_clean(monkeypatch):
    updates, logged, bumps = _wire_calibration_auto_apply(monkeypatch, degraded=False)

    main._maybe_run_auto_apply(_CALIBRATION_CFG)

    assert updates == [{"whale_confidence_weights": {"depth_factor": 0.6}}]
    assert len(logged) == 1 and logged[0]["auto_applied"] is True
    assert bumps == [True]


def test_calibration_auto_apply_refuses_to_write_when_evidence_is_degraded(monkeypatch):
    updates, logged, bumps = _wire_calibration_auto_apply(monkeypatch, degraded=True)

    main._maybe_run_auto_apply(_CALIBRATION_CFG)

    assert updates == []  # known completeness defect open - refuse the automatic write
    assert logged == []
    assert bumps == []


def _wire_advisory_auto_apply(monkeypatch, degraded: bool):
    qualifying_rec = {
        "id": "rec-1", "config_path": "strategy.entry_threshold",
        "current_value": 0.5, "suggested_value": 0.6, "rationale": "test recommendation",
        "confidence_label": "higher", "n": 100,
    }
    monkeypatch.setattr(main.advisory_engine, "generate_recommendations",
                         lambda *a, **k: {"recommendations": [qualifying_rec]})
    monkeypatch.setattr(main, "_series_evaluator_rows_for_advisory", lambda cfg: [])
    monkeypatch.setattr(main.regime_analytics, "by_category", lambda rows: [])
    monkeypatch.setattr(main.candidate_log, "gate_summary", lambda: {})
    monkeypatch.setattr(main.config_performance, "last_applied_at", lambda source: None)
    monkeypatch.setattr(main.config_performance, "fingerprint", lambda cfg: "fp")
    monkeypatch.setattr(main.config_performance, "all_last_applied_by_path", lambda: {})
    # all_variants() would otherwise do a real (if test-isolated) DB read
    # this test has no reason to depend on - generate_recommendations
    # itself is mocked below and never inspects its `variants` argument.
    monkeypatch.setattr(main.config_performance, "all_variants", lambda: [])
    monkeypatch.setattr(main.trade_analytics, "build_trade_history", lambda rows: [])
    logged = []
    monkeypatch.setattr(main.config_performance, "log_applied_change", lambda **kw: logged.append(kw))
    updates = []
    monkeypatch.setattr(main.config_store, "update", lambda patch: updates.append(patch))
    monkeypatch.setattr(main.config_store, "get", lambda: {"strategy": {"entry_threshold": 0.5}})
    bumps = []
    monkeypatch.setattr(main, "bump_generation", lambda: bumps.append(True))
    monkeypatch.setattr(main.evidence_provenance, "current_completeness_state",
                         lambda: {"degraded": degraded, "defects": [], "checked_at": 0.0})
    return updates, logged, bumps


_ADVISORY_CFG = {
    "advisory": {
        "enabled": True, "auto_apply_enabled": True,
        # min_resolved_trades_per_variant is read via adv_cfg["..."] (bracket
        # indexing, not .get()) when building the generate_recommendations
        # call - omitting it here raises KeyError before the mocked
        # generate_recommendations ever runs.
        "min_resolved_trades_per_variant": 10,
        "auto_apply_min_confidence": "higher", "auto_apply_min_n": 25, "auto_apply_cooldown_sec": 86400,
    },
    "confidence_calibration": {"enabled": False},
}


def test_advisory_auto_apply_writes_when_evidence_is_clean(monkeypatch):
    updates, logged, bumps = _wire_advisory_auto_apply(monkeypatch, degraded=False)

    main._maybe_run_auto_apply(_ADVISORY_CFG)

    assert updates == [{"strategy": {"entry_threshold": 0.6}}]
    assert len(logged) == 1 and logged[0]["auto_applied"] is True
    assert bumps == [True]


def test_advisory_auto_apply_refuses_to_write_when_evidence_is_degraded(monkeypatch):
    updates, logged, bumps = _wire_advisory_auto_apply(monkeypatch, degraded=True)

    main._maybe_run_auto_apply(_ADVISORY_CFG)

    assert updates == []  # known completeness defect open - refuse the automatic write
    assert logged == []
    assert bumps == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_scheduler_loops.py -v -k "auto_apply_writes or auto_apply_refuses"`
Expected: FAIL — `test_..._writes_when_evidence_is_clean` fails because `main` has no `evidence_provenance` attribute yet (`AttributeError`); `test_..._refuses_...` fails the same way.

- [ ] **Step 3: Write the implementation**

In `main.py`, add the import alongside the other `services.quality` import (near line 126):

```python
from services.quality import evidence_provenance
```

Modify the confidence-calibration auto-apply condition inside `_maybe_run_auto_apply` (currently `main.py:450-452`) by adding one clause:

```python
                if (
                    (last_auto is None or (tick_now - last_auto) >= cooldown)
                    and cc_result["report"]["resolved_count"] >= auto_apply_floor
                    and not evidence_provenance.current_completeness_state()["degraded"]
                ):
```

Modify the advisory auto-apply qualifying check (currently `main.py:543`, `if qualifying:`) to:

```python
            if qualifying and not evidence_provenance.current_completeness_state()["degraded"]:
```

No other lines in either block change — the cooldown timestamp is only ever advanced inside the guarded body via `config_performance.log_applied_change`, so a refusal leaves it untouched and the next tick retries once the defect clears.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_scheduler_loops.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_scheduler_loops.py
git commit -m "fix: refuse advisory/calibration auto-apply while a completeness defect is open (#214)"
```

---

### Task 6: `series` blind spot in calibration (#60)

**Files:**
- Modify: `services/signal_log.py:600-630` (`resolved_signals_with_factors`)
- Modify: `services/whale_calibration/confidence_calibration.py` (new `_series_win_rates`, wired into `generate_calibration_report`)
- Test: `tests/test_signal_log.py`, `tests/test_confidence_calibration.py`

**Interfaces:**
- Produces: each row from `signal_log.resolved_signals_with_factors()` now includes `"series": str`.
- Produces: `confidence_calibration._series_win_rates(rows: list[dict]) -> list[dict]`, each entry `{"series": str, "n": int, "win_rate_pct": float}`, sorted by `n` descending, dropping any series under `_MIN_BAND_SIZE` (3).
- Produces: `generate_calibration_report()`'s `report` dict gains a `"by_series"` key holding that list.

- [ ] **Step 1: Write the failing test for `signal_log`**

Add to `tests/test_signal_log.py`:

```python
def test_resolved_signals_with_factors_includes_the_stored_series(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("KXBTC15M-26AUG161645-45", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.5})
    log.mark_resolved(1, correct=True)

    rows = log.resolved_signals_with_factors()

    assert rows[0]["series"] == "KXBTC15M"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_signal_log.py -v -k includes_the_stored_series`
Expected: FAIL with `KeyError: 'series'`

- [ ] **Step 3: Write the minimal implementation**

In `services/signal_log.py`, modify `resolved_signals_with_factors()` (currently lines 610-630):

```python
def resolved_signals_with_factors() -> list[dict]:
    """services/whale_calibration/confidence_calibration.py's entire input: resolved signals
    that carry a real per-factor confidence breakdown. factors_json IS NOT
    NULL is the filter, not a source string match - only real providers
    (services/whalewatchers/kalshi_trade_tape.py) ever populate it, so this
    naturally excludes every simulator-sourced row without needing a second,
    possibly-drifting definition of "real" to maintain. No date/limit
    scoping - the calibration gate cares about total resolved count, not
    recency, and this table is small enough (one row per signal, not per
    tick) that a full scan is cheap. `series` is already a stored, indexed
    column (issue #60 - it existed but was never selected here, so every
    consumer of this function was blind to it)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT confidence, correct, factors_json, raw_notional_usd, raw_spread, raw_volume_24h, series "
            "FROM signals WHERE resolved = 1 AND excluded = 0 AND factors_json IS NOT NULL",
        ).fetchall()
    results = []
    for confidence, correct, factors_json, raw_notional_usd, raw_spread, raw_volume_24h, series in rows:
        try:
            factors = json.loads(factors_json)
        except (TypeError, ValueError):
            continue  # malformed row - skip rather than crash the whole report
        results.append({
            "confidence": confidence, "correct": bool(correct), "factors": factors,
            "raw_notional_usd": raw_notional_usd, "raw_spread": raw_spread, "raw_volume_24h": raw_volume_24h,
            "series": series,
        })
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_signal_log.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Write the failing tests for `confidence_calibration`**

Add to `tests/test_confidence_calibration.py`. First extend the existing `_row` helper with an optional `series` parameter (default keeps every existing call site unchanged):

```python
def _row(depth, unusualness, proximity, context, agreement, correct, confidence=0.5, cluster=0.0, trend=0.5, analyst=0.5, series="KXTEST"):
    return {
        "confidence": confidence,
        "correct": correct,
        "series": series,
        "factors": {
            "depth_factor": depth, "unusualness_factor": unusualness,
            "proximity_factor": proximity, "context_factor": context,
            "agreement_factor": agreement, "cluster_factor": cluster,
            "trend_factor": trend, "analyst_factor": analyst, "score": confidence,
        },
    }
```

Then add:

```python
def test_series_win_rates_groups_by_series_and_computes_win_rate():
    rows = (
        [_row(0.5, 0.5, 0.5, 0.5, 0.5, True, series="KXBTC15M") for _ in range(2)]
        + [_row(0.5, 0.5, 0.5, 0.5, 0.5, False, series="KXBTC15M")]
        + [_row(0.5, 0.5, 0.5, 0.5, 0.5, True, series="KXMLB") for _ in range(3)]
    )

    result = cc._series_win_rates(rows)

    assert result == [
        {"series": "KXBTC15M", "n": 3, "win_rate_pct": 66.7},
        {"series": "KXMLB", "n": 3, "win_rate_pct": 100.0},
    ]


def test_series_win_rates_drops_buckets_under_the_min_band_size():
    rows = [_row(0.5, 0.5, 0.5, 0.5, 0.5, True, series="KXTINY"),
            _row(0.5, 0.5, 0.5, 0.5, 0.5, False, series="KXTINY")]  # n=2 < _MIN_BAND_SIZE(3)

    assert cc._series_win_rates(rows) == []


def test_series_win_rates_empty_for_no_rows():
    assert cc._series_win_rates([]) == []


def test_generate_calibration_report_includes_by_series_breakdown():
    rows = _discriminating_dataset(n_per_bucket=3)  # 9 rows, all series="KXTEST" by the _row default

    result = cc.generate_calibration_report(rows, min_resolved_signals=5)

    assert result["report"]["by_series"] == [
        {"series": "KXTEST", "n": 9, "win_rate_pct": round(3 / 9 * 100, 1)},
    ]
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_confidence_calibration.py -v -k series`
Expected: FAIL with `AttributeError: module 'services.whale_calibration.confidence_calibration' has no attribute '_series_win_rates'`

- [ ] **Step 7: Write the minimal implementation**

In `services/whale_calibration/confidence_calibration.py`, add a new function near `_factor_report`:

```python
def _series_win_rates(rows: list[dict]) -> list[dict]:
    """Categorical group-by-series breakdown (#60 - `series` is a real,
    indexed, stored column that signal_log.resolved_signals_with_factors()
    never selected, so this module never saw it). Plain group-by, not
    _bucket_win_rates' numeric tertile split - `series` is a category
    label, not a sortable factor value, and it does not live inside
    factors_json so it is not one of _FACTOR_NAMES either. Drops any
    series under _MIN_BAND_SIZE, reusing the same floor
    _confidence_calibration_bands already uses rather than adding a second
    "enough data" constant to this file."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["series"], []).append(r)
    out = [
        {
            "series": series, "n": len(group),
            "win_rate_pct": round(sum(1 for r in group if r["correct"]) / len(group) * 100, 1),
        }
        for series, group in buckets.items() if len(group) >= _MIN_BAND_SIZE
    ]
    out.sort(key=lambda b: -b["n"])
    return out
```

Then add `"by_series": _series_win_rates(rows),` to `generate_calibration_report()`'s returned `"report"` dict, alongside the existing `"per_factor"`/`"ranked_by_discrimination"` keys.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_confidence_calibration.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 9: Commit**

```bash
git add services/signal_log.py services/whale_calibration/confidence_calibration.py tests/test_signal_log.py tests/test_confidence_calibration.py
git commit -m "fix: expose the series dimension confidence_calibration was blind to (#60)"
```

---

### Task 7: Full local verification and CHEATSHEET/README cross-posting

**Files:**
- Modify: `services/quality/README.md`, `services/advisory/README.md`, `services/whale_calibration/README.md` (one dated finding each, per CLAUDE.md's "cross-post confirmed findings there with a date")

**Interfaces:** none new — this task verifies the whole branch together and records the finding in the three modules' own reference docs, per CLAUDE.md's module-README cross-posting rule.

- [ ] **Step 1: Run the full targeted test suite for every file this plan touched**

Run:
```bash
pytest tests/test_evidence_provenance.py tests/test_quality_routes.py tests/test_trading_gate.py \
       tests/test_whale_calibration_routes.py tests/test_main_scheduler_loops.py \
       tests/test_signal_log.py tests/test_confidence_calibration.py -v
```
Expected: PASS, no failures, no unexpected skips.

- [ ] **Step 2: Add a dated note to `services/quality/README.md`**

Insert this section after the existing "What `/api/quality/summary` actually does" table:

```markdown
## Evidence-completeness signal (2026-08-30, #214)

`evidence_provenance.py` composes three already-existing, already-public
defect counters into `current_completeness_state()`: `settlement_resolver.
snapshot()["dropped_after_max_attempts"]`, `index_feed.ingestion.
snapshot()["dropped_rows"]`, and `capture_writer.dropped_count()`/
`overflow_dropped_count()` (per store). Its `findings()` wrapper feeds
`GET /api/quality/summary` the same way `alerting.alert_findings()` does.
Deliberately NOT `settlement_resolver.dropped_total` - that field is a
conservation sum (also incremented by the expected non-binary-result skip
branch), not a defect count; its own source comment says so.
`services/advisory/routes.py` and `services/whale_calibration/routes.py`
also call `current_completeness_state()` directly, and `main.py`'s
`_maybe_run_auto_apply` uses it to refuse an automatic config write while
a defect is open - see `docs/superpowers/specs/2026-08-30-self-feeding-
loop-provenance-design.md`.
```

- [ ] **Step 3: Add a dated note to `services/advisory/README.md`**

Insert this section after the existing "Handoff" section:

```markdown
## Evidence-completeness provenance (2026-08-30, #214)

`GET /api/advisory/status` and `GET /api/advisory/recommendations` both
carry an `evidence_provenance` block (`services.quality.evidence_
provenance.current_completeness_state()`), attached at the route boundary
only - `advisory_engine.generate_recommendations()` itself is unchanged,
since `market_analyst_orchestrator` also calls it directly and has nothing
to do with the HTTP surface. `main.py`'s `_maybe_run_auto_apply` folds
`not evidence_provenance.current_completeness_state()["degraded"]` into
its existing qualifying-recommendation check, so an automatic config write
is silently skipped (cooldown untouched) while a known data-completeness
defect is open - currently unreachable in production since
`advisory.auto_apply_enabled` is `false`, but tested and ready for
whenever #51 re-arms it.
```

- [ ] **Step 4: Add a dated note to `services/whale_calibration/README.md`**

Insert this section after the existing content:

```markdown
## Evidence-completeness provenance + the series blind spot (2026-08-30, #214 + #60)

`GET /api/confidence-calibration/status` and `.../report` both carry an
`evidence_provenance` block, same mechanism and same route-boundary-only
placement as `services/advisory/routes.py`'s own addition - `confidence_
calibration.generate_calibration_report()` itself is unchanged.
`main.py`'s calibration auto-apply block gets the same silent-skip-on-
degraded-evidence guard as advisory's.

Separately (#60): `signal_log.resolved_signals_with_factors()` now selects
`series` - a real, indexed, `NOT NULL` column it stored but never
returned, so this module never saw it. `generate_calibration_report()`'s
`report` dict now carries a `by_series` breakdown
(`confidence_calibration._series_win_rates()`), a plain categorical
group-by (not `_bucket_win_rates`' numeric tertile split - `series` isn't
inside `factors_json`), dropping any series under the module's existing
`_MIN_BAND_SIZE` (3) floor.
```

- [ ] **Step 5: Commit**

```bash
git add services/quality/README.md services/advisory/README.md services/whale_calibration/README.md
git commit -m "docs: cross-post the evidence-provenance mechanism and #60 series fix to module READMEs"
```
