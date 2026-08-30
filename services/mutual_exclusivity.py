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

# Lifetime counter (never reset except by process restart, same idiom as
# strategy_engine.py's own _me_gate_stats) - distinct from that module's
# me_gate_unknown_total, which tracks a different, not-yet-implemented
# gate (PR #202's parked event-scoped ME gate). This one counts how often
# find_open_confirmed_conflict couldn't determine an answer because
# market_titles had no cached entry yet for the candidate ticker (a
# brand-new market the catalog scan hasn't reached), as distinct from a
# genuine "checked, no conflict" result.
_me_pairing_stats = {"me_pairing_unknown_total": 0}


def me_pairing_stats() -> dict:
    """Pure read for observability - see _me_pairing_stats above."""
    return dict(_me_pairing_stats)


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


def find_open_confirmed_conflict(
    ticker: str, market_titles: dict, event_titles: dict, open_position_tickers: set[str],
) -> str | None:
    """The ticker of a currently-open position that is Kalshi-confirmed
    mutually-exclusive with `ticker` (same event_ticker,
    event_titles[...].mutually_exclusive is True), or None.

    Unlike find_me_pairs (which needs both siblings in the same tick's
    REST-fetched `markets` batch - narrow, watchlist-scoped), this reads
    market_titles/event_titles: the persisted, catalog-wide caches
    (services/title_cache.py) that decision_bridge.py already reads for
    every signal regardless of watchlist membership. Works for a candidate
    ticker that has never been on the watchlist - see docs/superpowers/
    specs/2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md.

    Scoped to open_position_tickers (small, already computed once per tick
    at main.py's open_position_tickers) rather than scanning the full
    market_titles catalog by event_ticker - same cost shape as
    position_netting.find_groups, which already does this safely on the
    hot path. O(open positions), not O(catalog)."""
    info = market_titles.get(ticker)
    if info is None:
        _me_pairing_stats["me_pairing_unknown_total"] += 1
        return None
    event_ticker = info.get("event_ticker")
    if not event_ticker:
        return None
    if (event_titles.get(event_ticker) or {}).get("mutually_exclusive") is not True:
        return None
    for open_ticker in open_position_tickers:
        if open_ticker == ticker:
            continue
        if (market_titles.get(open_ticker) or {}).get("event_ticker") == event_ticker:
            return open_ticker
    return None
