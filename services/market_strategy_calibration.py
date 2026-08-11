"""
Calibration tooling for MarketNativeStrategy's own composite confidence
score (services/market_strategy.py's _entry_confidence) - "web of expertise"
audit (2026-08-11) gap #5: services/confidence_calibration.py has done this
for the whale-follow side since 2026-08-08, but market_strategy's own
confidence factors (momentum/liquidity/spread, plus analyst when fresh) -
an explicitly parallel dataset - had zero calibration tooling of its own.

Deliberately scoped smaller than confidence_calibration.py, disclosed here
rather than silently assumed equivalent: whale-follow signals persist their
full per-factor breakdown at signal time (signal_log.py's factors_json),
resolved or not, which is what lets confidence_calibration.py ask "does
THIS SPECIFIC FACTOR discriminate winners from losers." market_strategy has
no equivalent per-candidate factor log - only a trade that actually got
PLACED carries its confidence forward at all (embedded in Trade.reason,
extracted by trade_analytics.build_trade_history()'s entry_confidence
field), and only the *blended* score, not momentum/liquidity/spread
individually. This module answers the question that data CAN honestly
answer - "does market_strategy's own composite confidence score actually
predict its own outcomes" (the confidence-calibration-bands half of
confidence_calibration.py) - not "which factor drives that" (the
per-factor-discrimination half), which would need a new persistence layer
capturing every evaluated candidate's raw factors, not just executed
trades' blended score. That remains open, not silently dropped - see
docs/hardening-and-accuracy-roadmap-2026-08-11.md.
"""
from services import trade_analytics

# Same fixed-width bands and minimum-band-size idiom as
# confidence_calibration.py's own _CONFIDENCE_BANDS/_MIN_BAND_SIZE - one
# definition would be nice, but the two modules read from genuinely
# different row shapes (signal_log's resolved-signal rows vs.
# trade_analytics' closed-trade rows), so this stays its own small
# constant rather than an awkward shared import between two independently
# gated features.
_CONFIDENCE_BANDS = [
    (0.0, 0.5, "<50%"),
    (0.5, 0.6, "50-60%"),
    (0.6, 0.7, "60-70%"),
    (0.7, 0.8, "70-80%"),
    (0.8, 0.9, "80-90%"),
    (0.9, 1.01, "90-100%"),
]
_MIN_BAND_SIZE = 3


def _confidence_calibration_bands(rows: list[dict]) -> list[dict]:
    """rows: trade_analytics.build_trade_history()'s output, already
    carrying entry_confidence (parsed from Trade.reason) and won. Same
    "predicted band midpoint vs. observed win rate" comparison
    confidence_calibration.py's own function of the same name uses for
    whale signals, applied to closed market_strategy trades instead."""
    bands = []
    for lo, hi, label in _CONFIDENCE_BANDS:
        group = [r for r in rows if r.get("entry_confidence") is not None and lo <= r["entry_confidence"] < hi]
        if len(group) < _MIN_BAND_SIZE:
            continue
        observed = round(sum(1 for r in group if r["won"]) / len(group) * 100, 1)
        predicted_mid = round((lo + min(hi, 1.0)) / 2 * 100, 1)
        bands.append({
            "band": label,
            "n": len(group),
            "predicted_pct": predicted_mid,
            "observed_win_rate_pct": observed,
            "gap_pts": round(observed - predicted_mid, 1),
        })
    return bands


def generate_calibration_report(rows: list[dict], min_resolved_trades: int) -> dict:
    """rows: trade_analytics.build_trade_history()'s output over
    market_broker.trade_log - already scoped to closed trades. Gated
    entrypoint, same "the threshold lives inside this function, not the
    caller" precedent confidence_calibration.py/advisory_engine.py both
    already follow."""
    resolved_count = len(rows)
    if resolved_count < min_resolved_trades:
        return {
            "report": None,
            "gated_reason": (
                f"{resolved_count}/{min_resolved_trades} closed market-native trades - "
                "calibration activates once that's reached"
            ),
            "resolved_count": resolved_count,
        }
    with_confidence = [r for r in rows if r.get("entry_confidence") is not None]
    overall_win_rate = (
        round(sum(1 for r in with_confidence if r["won"]) / len(with_confidence) * 100, 1)
        if with_confidence else None
    )
    return {
        "report": {
            "resolved_count": resolved_count,
            "with_confidence_count": len(with_confidence),
            "overall_win_rate": overall_win_rate,
            "confidence_label": trade_analytics.confidence_label(resolved_count),
            "confidence_calibration": _confidence_calibration_bands(rows),
        },
        "gated_reason": None,
        "resolved_count": resolved_count,
    }
