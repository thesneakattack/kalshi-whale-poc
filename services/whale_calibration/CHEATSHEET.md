# Whale calibration module — cheat sheet

Owns: `confidence_calibration.py` (rule-based bucket analysis of
`composite_confidence_breakdown`'s factor weights against real resolved
signals - per-factor discrimination gaps, suggested-weight computation,
confidence-vs-observed-accuracy calibration bands) + `calibration_history.py`
(point-in-time snapshots of the above, so calibration quality is a trend,
not just a single live report) + `routes.py`
(`/api/confidence-calibration/*`, 6 routes). Split out of
`services/analytics/` 2026-08-22 (modularization Phase 2/9), per the
already-queued ROADMAP.md item — checked directly before moving: this pair
has **zero cross-imports with any other analytics sibling**
(`regime_analytics`, `advisory_engine`, etc.), the cleanest split of this
whole pass.

## No direct Kalshi API surface

This module never calls Kalshi's API itself — it works entirely off
`services/signal_log.py`'s already-captured, already-resolved signal rows
(`resolved_signals_with_factors()`) and `services/confidence_scoring.py`'s
`DEFAULT_WEIGHTS`. But **whether a signal counts as resolved-correct at
all is decided upstream of this module**, and that upstream logic touches
Kalshi data directly — see the audit finding below.

## Audit finding: whale-accuracy "correct" is set at `determined`, not
`finalized` — the same gap as `services/exits/CHEATSHEET.md`, now on the
objective-function metric itself

Checked directly against `docs/kalshi/market_lifecycle.md` while writing
this cheat sheet, same as the Phase 1 exits finding. `main.py`'s
`_check_signal_resolutions` (the function that ultimately produces every
row this module's `generate_calibration_report` consumes) does:

```python
result = (market.get("result") or "").strip().lower()
if result in ("yes", "no"):
    signal_log.mark_resolved(item["id"], correct=(result == item["side"]))
```

Per `docs/kalshi/market_lifecycle.md` (lines 21–23, 51–52, 68–72):
`market.result` is set at the `closed` → `determined` transition, while
"Settlement timer is running" and the result "may be disputed... Settlement
timer restarts" if it is. The truly final state is `finalized`
(`determined`/`amended` → `finalized`, "positions paid out").

**Fixed 2026-08-23** (same "module quality" pass this cheat sheet's own
finding named as the top priority for a future audit): `_check_signal_
resolutions` now only trusts `market.result` once `market.status ==
"finalized"` — same fix shape, same day, as the sibling gaps this section
already named in `services/exits/CHEATSHEET.md` (phase 134) and
`services/market_catalog/CHEATSHEET.md`'s `propagate_milestone_winners`. A
ticker whose market is `determined` but not yet `finalized` simply stays in
`unresolved_batch`'s pool and gets rechecked on a later pass — the same
"not yet settled" degrade path this function already used for a market
Kalshi hasn't returned data for at all, now also covering "settled but
still inside the dispute window." `tests/test_signal_resolution.py` covers
the regression directly (a `determined`-but-not-`finalized` market must not
resolve). Not tracked further than this: no separate dispute-frequency
audit was run, since the fix removes the exposure at the source rather than
needing to first measure how often it happens.

## Handoff — who calls this module, who it calls

- **Upstream:** `main.py`'s `trading_loop` (`calibration_advisory` phase)
  calls `confidence_calibration.generate_calibration_report(...)` and
  `calibration_history.due()`/`record_snapshot(...)` directly (not just
  through `routes.py`) every tick, gated by `confidence_calibration.enabled`
  and `calibration_history`'s own rate-limited `due()` check.
- **Downstream (real write-back loop, same shape as
  `services/advisory/CHEATSHEET.md`'s):** when
  `confidence_calibration.auto_apply_enabled` is on (config-gated, typed-
  confirmation-phrase protected — `POST
  /api/confidence-calibration/auto-apply/enable`), `trading_loop` writes
  `blended_weights_for_auto_apply(...)`'s result straight into
  `config_store` (`whale_confidence_weights`) — this module computes the
  suggestion, `main.py` is the only place that ever calls
  `config_store.update()`, same "service module never writes its own
  config" convention `advisory_engine.py` also follows.
- **`confidence_scoring.DEFAULT_WEIGHTS`** is this module's one real
  cross-service import — the baseline `composite_confidence_breakdown`
  weights every suggestion is blended against.
