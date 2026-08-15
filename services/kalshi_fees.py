"""
Real Kalshi taker-fee formula. Verified 2026-08-09 directly against three
real fills on this app's own connected account (see
docs/prediction-markets-research-reference.md Part 2.4) rather than trusted
from scraped fee-schedule blogs, which converge on the same coefficient but
disagreed on rounding precision:

    taker_fee = ceil_to_$0.0001( 0.07 * contracts * price * (1 - price) )

Checked against real fee_cost values: 14.11 contracts @ $0.84 -> predicted
$0.1328, actual $0.1328. 9.13 @ $0.53 -> predicted/actual $0.1592. 21.75 @
$0.17 -> predicted/actual $0.2149. All three exact.

Before this, services/paper_broker.py modeled zero fees anywhere - every
paper-mode P&L figure (including shadow mode's own "would I trust this with
real money" read) was systematically more optimistic than real trading
would produce. See docs/prediction-market-strategy-alignment-plan.md
Part 2.2.

Deliberately price/(1-price)-symmetric, so a caller never needs to pick
"the yes price" vs "the no price" specially - the formula returns the same
number either way (0.84*(1-0.84) == 0.16*(1-0.16)), same as every other
place in this app that already treats price as "always the yes-side price
by convention" (see paper_broker.py's module docstring).

Maker fee (confirmed 2026-08-14 against docs/kalshi/kalshi-fee-schedule.pdf
- exactly 1/4 of the taker rate, 0.0175 vs 0.07, matching what was
previously only secondary-sourced) is still NOT implemented here - this
app has no maker/limit-order path yet (services/kalshi_account_client.py's
real order path defaults to "immediate_or_cancel", a taker order). Revisit
once that changes.

Per-series fee multiplier (2026-08-14, direct request to reconcile against
the real fee schedule PDF): the schedule's "Non-Standard Fees" table lists
a maker/taker multiplier per series, default 1 (i.e. the plain formula
above) for every series not listed - except ten series with multiplier 0,
a real full fee waiver this app had never modeled (taker_fee took no
ticker/series at all). Checked against real trade history before writing
this fix: zero trades in either strategy's trade log have ever touched any
of these ten series, so there was nothing to correct retroactively - this
is a forward-looking fix only. See _ZERO_FEE_SERIES below; every other
listed series in the real schedule has multiplier 1, identical to the
default, so only the actual exceptions are worth encoding.
"""
import math

from services.signal_log import series_of

_TAKER_RATE = 0.07

# docs/kalshi/kalshi-fee-schedule.pdf, "Non-Standard Fees" table, effective
# 2026-07-07 - the only series listed with a maker/taker multiplier of 0
# (every other listed series has multiplier 1, the same as the unlisted
# default, so isn't worth encoding separately).
_ZERO_FEE_SERIES = frozenset({
    "KXBTCY", "KXCITRINI", "KXDOED", "KXELECTIRAN", "KXGAMBLINGREPEAL",
    "KXGREENLAND", "KXIRANDEMOCRACY", "KXLAYOFFSYINFO", "KXPAHLAVIHEAD", "KXETHY",
})


def taker_fee(contracts: float, price: float, ticker: str | None = None) -> float:
    """Real Kalshi taker fee for one leg of a trade (open OR close - Kalshi
    charges per fill, not per round-trip), in dollars. contracts <= 0
    returns 0.0 rather than a negative/nonsensical fee.

    ticker: optional, same series_of() definition used everywhere else in
    this app (signal_log.series_of) - when given, applies the real
    zero-fee waiver for the ten series in _ZERO_FEE_SERIES. Omitting it
    (every pre-2026-08-14 call site, and every call site that genuinely
    has no ticker in scope) keeps today's behavior exactly - the default
    multiplier of 1 everywhere, same as this function's original,
    real-fill-verified formula."""
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    if ticker is not None and series_of(ticker) in _ZERO_FEE_SERIES:
        return 0.0
    raw = _TAKER_RATE * contracts * price * (1 - price)
    return math.ceil(raw * 10000) / 10000
