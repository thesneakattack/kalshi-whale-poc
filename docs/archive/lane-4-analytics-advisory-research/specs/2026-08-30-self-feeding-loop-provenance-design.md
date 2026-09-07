# Self-feeding loop: evidence provenance for advisory + calibration (#214 + #60)

Design doc. Brainstormed 2026-08-30, self-reviewed against current source
(not the issue text, which is already partly stale — see "Corrected framing"
below). Scope confirmed with the user: #214's provenance mechanism + #60's
narrow completeness fix. The win-rate-vs-cost algorithm change and #51
(whether to actually re-arm either `auto_apply_enabled` flag) stay parked —
this design makes that switch safe to consider later, it does not flip it.

## Problem

`advisory_engine` and `confidence_calibration` compute statistics from trade/
signal history, those statistics drive tuning recommendations, applied
recommendations change trading behaviour, and the resulting trades become the
next round of input. No step in that loop can currently observe that its
inputs were incomplete — `min_resolved_trades_per_variant`/
`auto_apply_min_n`-style gates count *surviving* rows and cannot distinguish
100 complete observations from 100 survivors of 130.

## Corrected framing (verified against current source, 2026-08-30)

Issue #214's own acceptance test cites `settlement_resolver.dropped_total > 0`
as the trigger. That field's source comment now reads *"Never read it as a
defect count — that was the bug"* (fixed by PR #231, merged the same day
#214 was filed — the issue text predates that fix). The real, current
per-defect counters are:

| Defect class (#) | Real counter | Accessor |
|---|---|---|
| Settlement gives up after retries (#208) | `dropped_after_max_attempts` | `services.settlement_resolver.snapshot()` |
| Index-feed reconnect discards a real gap (#209) | `dropped_rows` | `services.index_feed.ingestion.snapshot()` |
| Capture-writer flush/overflow failures (#211) | `dropped_count()` / `overflow_dropped_count()` (per store) | `services.capture_writer` |

All three are public, already-existing, lifetime-since-process-start
counters — no new instrumentation needed, only a new place that reads them
together and a new set of places that act on the combined read.

## Scope limitation (explicit, not silently accepted)

These are lifetime counters, not a time series. `current_completeness_state()`
(below) answers **"is a known defect currently active in this process,"**
not **"was every historical trade behind this specific statistic captured
cleanly."** The latter would require timestamping every defect occurrence and
joining it against each trade's capture window — nothing today stores that,
and it is more than the issue's own acceptance test asks for ("a
recommendation generated *while* a known completeness defect is open must
visibly say so"). Consequence, stated rather than hidden: if a defect fires,
gets fixed, and the process later restarts clean, older trades captured
during the defective window keep contributing to statistics with no flag.
Follow-on gap, not this design's job.

The opposite direction is also a scope limitation, not a bug: all three
counters are lifetime-since-process-start totals with no reset path except
a process restart, so `current_completeness_state()["degraded"]` latches
`True` for the rest of that process's uptime after a single occurrence —
even once the underlying defect is fixed and no new drops are happening.
A future improvement would compare each counter against the value captured
at the time of the last successful auto-apply (a delta), not the lifetime
total, so a resolved defect stops gating new applies without needing a
restart — that is a future decision, not built here.

## Architecture

### 1. `services/quality/evidence_provenance.py` (new)

Pure, read-only, no network I/O — same home and same shape as the rest of
`services/quality/` (which already composes read-only sources from
`observability`, `storage_health`, `alerting`, `fault_log` into one health
signal for `/api/quality/summary`).

```python
def current_completeness_state() -> dict:
    """{"degraded": bool, "defects": [...], "checked_at": float}"""

def findings() -> list[QualityFinding]:
    """Thin wrapper over current_completeness_state() for quality/routes.py,
    same pattern as alerting.alert_findings()."""
```

`defects` entries: `{"component", "field", "count", "detail"}` for each of
the three counters above that is nonzero.

Verified not a duplicate: `observability.py`'s `_capture_writer_dead_finding`
checks whether the writer thread is *alive*, not its drop counters;
`storage_health.py` and `alerting.py` have zero references to any of the
three fields above. This is a genuine gap, not double-reporting.

### 2. Surfacing — at the route boundary only

`evidence_provenance.current_completeness_state()` is added as an
`evidence_provenance` key to the **response dicts**, not to the pure engine
functions:

- `services/advisory/routes.py`: `get_advisory_status`,
  `get_advisory_recommendations` — add the key after calling
  `advisory_engine.generate_recommendations(...)`/building the status dict.
- `services/whale_calibration/routes.py`: `get_confidence_calibration_status`,
  `get_confidence_calibration_report` — same, after calling
  `confidence_calibration.generate_calibration_report(...)`.
- `services/quality/routes.py`: add `evidence_provenance.findings()` to the
  existing `findings +=` chain (same pattern as `alerting.alert_findings`).

`advisory_engine.generate_recommendations()` and
`confidence_calibration.generate_calibration_report()` themselves are
**untouched** — both have other callers
(`market_analyst_orchestrator`, `calibration_history`, `research.py`) that
have nothing to do with the HTTP surface; mutating their return contract
would ripple into those unrelated call sites. Routes compose, engines stay
pure — same split `services/quality/routes.py` already models.

One shared `evidence_provenance` block per response, not per-recommendation.
Today's three counters are process-global, not attributable to a specific
variant/trade/series — presenting fabricated per-recommendation granularity
would claim precision the instrumentation doesn't have.

### 3. Auto-apply refusal — `main.py`, both blocks, silent skip

Both auto-apply blocks (`confidence_calibration` ~L438, `advisory` ~L509)
gain a guard:

```python
if evidence_provenance.current_completeness_state()["degraded"]:
    # skip; do not advance the cooldown
else:
    ... existing apply logic unchanged ...
```

**No new logging call.** Checked `main.py` directly: it never calls
`logging.*` in the trading loop, and the two gates already sitting next to
this one (cooldown not elapsed, no qualifying recommendation) are already
silent skips with no log line. Adding a log call for only this one gate
would be a new, inconsistent convention. Visibility comes entirely from the
`evidence_provenance` block already surfaced on the read APIs (section 2),
not from a log line.

Since both `auto_apply_enabled` flags are `false` today (config/settings.yaml
:143, :152), this guard is currently unreachable in production — it is
mechanism-readiness for whenever #51 (a separate, still-parked human
decision) re-arms either flag. No change to today's runtime behaviour.

### 4. #60 — `series` blind spot in calibration

`services/signal_log.py:612`'s `resolved_signals_with_factors()` selects
`confidence, correct, factors_json, raw_notional_usd, raw_spread,
raw_volume_24h` — never `series`, despite it being a real, indexed,
`NOT NULL` column (`:59`, `:72`). Fix: add `series` to the SELECT and to the
returned row dict (mechanical, no derivation needed — `series` is already
its own stored column, unlike `regime_analytics.by_series()`'s
`signal_log.series_of(ticker)` derivation for the advisory side, which
doesn't apply here since `resolved_signals_with_factors()` doesn't even
select `ticker`).

Then add a categorical `by_series` breakdown to
`generate_calibration_report()`'s return, grouping by the new `series`
field (plain group-by — `series` is categorical, not a candidate for
`_bucket_win_rates`' numeric tertile split, and it isn't inside
`factors_json` so it isn't one of `_FACTOR_NAMES` either). Drop any series
bucket under `_MIN_BAND_SIZE` (3) — reusing the module's own existing
sample-size floor (already used by `_confidence_calibration_bands`) rather
than inventing a second "enough data" constant in the same file.

This is purely additive reporting. It does **not** touch `_suggested_weights`
or `blended_weights_for_auto_apply` — the win-rate-vs-cost weighting
question stays parked, per scope.

## Testing (TDD, per file)

- `tests/test_evidence_provenance.py` (new): each of the three defect
  sources independently, clean state, combined-degraded state.
- `main.py`'s two auto-apply blocks: a test forcing `auto_apply_enabled=True`
  + a monkeypatched degraded state asserts `config_store.update` is never
  called and the cooldown timestamp does not advance.
- `tests/test_signal_log.py`: `resolved_signals_with_factors()` returns
  `series` per row.
- `tests/test_confidence_calibration.py`: `by_series` groups correctly,
  drops buckets under 3, absent when no series clears the floor.
- Route tests for the three touched routes: `evidence_provenance` key
  present and matches `current_completeness_state()`'s shape.

## Out of scope (confirmed)

- Reworking `advisory_engine`'s suggestion functions to weigh win rate
  against cost/P&L (the older, separate open-decision line) — real
  algorithm change, its own judgment call, not touched here.
- Actually re-arming `advisory.auto_apply_enabled` or
  `confidence_calibration.auto_apply_enabled` (#51) — human decision,
  unaffected by this design either way.
- Per-trade/per-signal temporal provenance (see "Scope limitation" above).
