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

Maker fee (reported secondhand as exactly 1/4 of the taker rate, only
charged if a resting order fills) is NOT implemented here - it's secondary-
sourced only, never independently verified against a real fill, and this
app has no maker/limit-order path yet (services/kalshi_account_client.py's
real order path defaults to "immediate_or_cancel", a taker order). Revisit
once that changes.
"""
import math

_TAKER_RATE = 0.07


def taker_fee(contracts: float, price: float) -> float:
    """Real Kalshi taker fee for one leg of a trade (open OR close - Kalshi
    charges per fill, not per round-trip), in dollars. contracts <= 0
    returns 0.0 rather than a negative/nonsensical fee."""
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw = _TAKER_RATE * contracts * price * (1 - price)
    return math.ceil(raw * 10000) / 10000
