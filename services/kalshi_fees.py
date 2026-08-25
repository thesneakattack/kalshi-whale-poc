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
app has no maker/limit-order path yet (services/kalshi/orders.py's
real order path defaults to "immediate_or_cancel", a taker order). Revisit
once that changes.

Per-series fee multiplier (2026-08-14, direct request to reconcile against
the real fee schedule PDF; corrected 2026-08-15 against Kalshi's own live
GET /series/{ticker} and GET /series endpoints - see docs.kalshi.com's
"Get Series"/"Get Series List" pages, each series' own `fee_multiplier`
field). The static PDF-sourced list this app shipped with (2026-08-14) only
found ten multiplier-0 series and missed real deviations entirely - a
direct live sample of every one of the 13,029 real series on Kalshi
(2026-08-15) found 33 total: 14 at multiplier 0 (four more than the PDF
list had - KXEXPAND, KXNEXTIRANLEADER, KXTRUMPOUT, KXGDPYEAR - either
missed by that pass or added since) and, previously entirely unmodeled, 19
at multiplier 0.5 - the ENTIRE MLB proposition-market family (spread,
total, outs, HR, hits, F5/F3/F7 innings, RBI, stolen bases, strikeouts,
total bases, team total, ...), one of this app's most actively-traded
series families in real trade history. Every MLB trade before this fix was
therefore charged double the real fee. See _FEE_MULTIPLIER_BY_SERIES below
- every series not listed defaults to multiplier 1, unchanged. Like the
PDF this replaces, this is still a point-in-time snapshot, not a live
fetch - Kalshi's own schedule can change again; revisit the same way if a
future real-fill fee_cost stops matching this formula.
"""
import math

from services.signal_log import series_of

_TAKER_RATE = 0.07
# Confirmed 2026-08-14 against docs/kalshi/kalshi-fee-schedule.pdf - exactly
# 1/4 the taker rate (0.0175 vs 0.07). See maker_fee()'s own docstring for
# why nothing called this until 2026-08-15.
_MAKER_RATE = _TAKER_RATE / 4

# Live-verified 2026-08-15 via Kalshi's real GET /series (single) and
# GET /series (list) endpoints' fee_multiplier field, sampled across all
# 13,029 real series - see this module's own docstring. Every series not
# listed here uses the default multiplier of 1 (today's existing formula,
# unchanged).
_FEE_MULTIPLIER_BY_SERIES = {
    # Full waiver (multiplier 0)
    "KXBTCY": 0.0, "KXCITRINI": 0.0, "KXDOED": 0.0, "KXELECTIRAN": 0.0,
    "KXETHY": 0.0, "KXEXPAND": 0.0, "KXGAMBLINGREPEAL": 0.0, "KXGDPYEAR": 0.0,
    "KXGREENLAND": 0.0, "KXIRANDEMOCRACY": 0.0, "KXLAYOFFSYINFO": 0.0,
    "KXNEXTIRANLEADER": 0.0, "KXPAHLAVIHEAD": 0.0, "KXTRUMPOUT": 0.0,
    # Half rate (multiplier 0.5) - the entire MLB proposition-market family.
    "KXMLBEXTRAS": 0.5, "KXMLBF3": 0.5, "KXMLBF5": 0.5, "KXMLBF5SPREAD": 0.5,
    "KXMLBF5TOTAL": 0.5, "KXMLBF7": 0.5, "KXMLBGAME": 0.5, "KXMLBHIT": 0.5,
    "KXMLBHR": 0.5, "KXMLBHRR": 0.5, "KXMLBKS": 0.5, "KXMLBOUTS": 0.5,
    "KXMLBRBI": 0.5, "KXMLBRFI": 0.5, "KXMLBSB": 0.5, "KXMLBSPREAD": 0.5,
    "KXMLBTB": 0.5, "KXMLBTEAMTOTAL": 0.5, "KXMLBTOTAL": 0.5,
}


def taker_fee(contracts: float, price: float, ticker: str | None = None) -> float:
    """Real Kalshi taker fee for one leg of a trade (open OR close - Kalshi
    charges per fill, not per round-trip), in dollars. contracts <= 0
    returns 0.0 rather than a negative/nonsensical fee.

    ticker: optional, same series_of() definition used everywhere else in
    this app (signal_log.series_of) - when given, applies the real
    per-series multiplier in _FEE_MULTIPLIER_BY_SERIES (1.0, i.e. no
    change, for every series not listed there). Omitting it (any call site
    that genuinely has no ticker in scope) keeps the default multiplier of
    1 everywhere, same as this function's original, real-fill-verified
    formula."""
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    multiplier = _FEE_MULTIPLIER_BY_SERIES.get(series_of(ticker), 1.0) if ticker is not None else 1.0
    if multiplier == 0.0:
        return 0.0
    raw = _TAKER_RATE * multiplier * contracts * price * (1 - price)
    return math.ceil(raw * 10000) / 10000


def maker_fee(contracts: float, price: float, ticker: str | None = None) -> float:
    """Real Kalshi maker fee for one leg of a trade - same formula/rounding/
    per-series-multiplier convention as taker_fee(), just at _MAKER_RATE
    instead of _TAKER_RATE. Not implemented until 2026-08-15 (docs/profit-
    maximization-assessment-2026-08-15.md, direct request) because this app
    had no maker/limit-order path at all before then - every real order
    (services/kalshi/orders.py) defaulted to immediate_or_cancel (a
    taker order), and the paper broker filled everything instantly at the
    quoted price, which is also inherently a taker fill. See services/
    paper_broker.py's PendingOrder/check_pending_fills for the paper-mode
    limit-order simulation this now feeds."""
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    multiplier = _FEE_MULTIPLIER_BY_SERIES.get(series_of(ticker), 1.0) if ticker is not None else 1.0
    if multiplier == 0.0:
        return 0.0
    raw = _MAKER_RATE * multiplier * contracts * price * (1 - price)
    return math.ceil(raw * 10000) / 10000
