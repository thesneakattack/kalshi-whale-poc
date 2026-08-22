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
(`resolved_signals_with_factors()`) and `services/whale_simulator.py`'s
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

**Current behavior**: a signal is marked `correct`/incorrect the instant
`result` is set, before the dispute window closes. If a result is disputed
and reversed, `signal_log`'s row is never revisited — `mark_resolved` is a
one-way write, and `unresolved_batch`'s query only ever selects rows that
are still unresolved. **This directly feeds CLAUDE.md's HARD COMMANDMENT
metric**: whale accuracy and the calibration bands this module reports are
both computed straight off that same `correct` field, so a live-but-rare
dispute reversal would silently corrupt the exact number the whole project
is judged against, with no mechanism to notice or repair it. Not fixed
here (a behavior change — re-checking `determined` rows for a later
`amended`/`finalized` flip is real new work, not a refactor), but this is
the single most important thing a future audit of this module's numbers
should check first: how often does this actually happen, and is a
dispute-tracking pass worth building.

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
- **`whale_simulator.DEFAULT_WEIGHTS`** is this module's one real
  cross-service import — the baseline `composite_confidence_breakdown`
  weights every suggestion is blended against.
