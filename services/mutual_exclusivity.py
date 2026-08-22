"""
Detects real 2-outcome mutually-exclusive market pairs - two tickers under
the same event where exactly one can resolve YES (e.g. "Team A to win" /
"Team B to win" in a head-to-head matchup with no draw: a 2x2 yes/no
matrix where one market's row is the exact inversion of the other's).
Direct request (2026-08-14): this relationship needs detecting
algorithmically and accounted for in trading decisions, not just detected
by a one-off manual inspection script (services/market_events/event_inspector.py already
had a version of this heuristic, but nothing in the live app ever called
it) or shown for display only (main.py's own event-title cache has carried
Kalshi's real mutually_exclusive flag since before this module existed,
used only by eventGroupCardHTML in static/index.html and one conservative
settlement-timing gate in strategy_engine.py - never to detect or prevent
an offsetting position across the pair). Motivated by the same session's
trade-history review finding a real "betting against myself" pattern
(whale-follow re-entering the opposite side of the SAME ticker after a
loss) - a genuine ME pair is the sharper, more directly preventable version
of that same risk: two DIFFERENT tickers that are economically the same
bet.

Kalshi's own event.mutually_exclusive flag (fetched every tick by main.py's
_fetch_event_titles, cached in state["event_titles"]) is the authoritative
signal - a real True/False Kalshi itself asserts about how the event's
markets settle, not an inference. A price-sum-to-~1.0 fallback (the same
heuristic services/market_events/event_inspector.py already used for manual inspection)
covers the transient case where an event's flag hasn't been backfilled yet
(main.py's own _fetch_event_titles docstring: "real Kalshi events always
return a real True/False... a cached None uniquely means this entry
predates that field being extracted").
"""
import math

_PRICE_SUM_TOLERANCE = 0.02


def find_me_pairs(markets: list[dict], event_titles: dict) -> dict[str, str]:
    """markets: state["markets"]-shaped list (needs ticker, event_ticker,
    yes_bid_dollars). event_titles: state["event_titles"]-shaped dict
    (event_ticker -> {"mutually_exclusive": bool | None, ...}).

    Returns {ticker: complement_ticker} for every market confidently
    identified as one half of a genuine 2-outcome mutually-exclusive pair -
    symmetric, so both tickers map to each other. Deliberately
    conservative: only ever pairs an event with EXACTLY two currently-
    fetched sibling markets. A real N-way mutually-exclusive event (e.g. a
    60-golfer tournament-winner market) is mutually_exclusive too but has
    no simple pairwise complement - main.py's own _fetch_event_titles
    comment makes the same distinction, this isn't a new judgment call."""
    by_event: dict[str, list[dict]] = {}
    for m in markets:
        event_ticker = m.get("event_ticker")
        ticker = m.get("ticker")
        if event_ticker and ticker:
            by_event.setdefault(event_ticker, []).append(m)

    pairs: dict[str, str] = {}
    for event_ticker, siblings in by_event.items():
        if len(siblings) != 2:
            continue
        me_flag = (event_titles.get(event_ticker) or {}).get("mutually_exclusive")
        if me_flag is True:
            confirmed = True
        elif me_flag is None:
            prices = [float(s.get("yes_bid_dollars") or 0) for s in siblings]
            confirmed = math.isclose(sum(prices), 1.0, rel_tol=_PRICE_SUM_TOLERANCE, abs_tol=_PRICE_SUM_TOLERANCE)
        else:
            confirmed = False  # Kalshi explicitly says False - independent props sharing an event, not a pair
        if not confirmed:
            continue
        ticker_a, ticker_b = siblings[0]["ticker"], siblings[1]["ticker"]
        pairs[ticker_a] = ticker_b
        pairs[ticker_b] = ticker_a
    return pairs
