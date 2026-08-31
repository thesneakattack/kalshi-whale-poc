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
# strategy_engine.py's own _me_gate_stats). This one counts how often
# find_open_confirmed_conflict couldn't determine an answer because
# market_titles had no cached entry yet for the candidate ticker (a
# brand-new market the catalog scan hasn't reached), as distinct from a
# genuine "checked, no conflict" result.
#
# Relationship to strategy_engine.py's me_gate_unknown_total (corrected
# 2026-08-30, final-review finding): that counter is real and LIVE today -
# _record_me_gate_unknown is called from strategy_engine.evaluate()'s own
# special-market conservative gate on every signal - not a placeholder for
# something unimplemented. It belongs to a DIFFERENT gate (the one PR
# #202's parked event-scoped ME design would extend), and it happens to
# count a similar underlying condition (no market_titles/event_titles
# entry, or a lookup exception) measured at a different point in that
# gate's own logic. The two counters therefore overlap in cause but not in
# meaning: this one is "the entry-side ME-pairing fallback had no catalog
# entry for the candidate", that one is "the special-market gate could not
# verify mutually_exclusive at all". Read them side by side at
# GET /api/health/pipeline (both are surfaced there), never as one number.
#
# Reading this number (self-review finding, 2026-08-30): decision_bridge.py
# calls find_open_confirmed_conflict unconditionally on EVERY whale signal,
# most of which are on ordinary markets that were never going to be part of
# a mutually-exclusive pair at all - they just haven't reached market_titles
# yet (an unrelated catalog-scan-lag question, not this gate's own blind
# spot). At full signal volume this counter is therefore dominated by that
# ordinary lag, not by ME-pairing-specific uncertainty - a rising count
# mostly says "catalog coverage is behind," not "the ME gate can't do its
# job." Real ME-pairing blindness is better read as a RATE against total
# confirmed-ME-event signal volume than as this counter's raw magnitude.
_me_pairing_stats = {"me_pairing_unknown_total": 0}


def me_pairing_stats() -> dict:
    """Pure read for GET /api/health/pipeline's me_pairing_gate block
    (services/diagnostics/routes.py) - see _me_pairing_stats above."""
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

    Bounded to the genuine 2-outcome case, the same scope find_me_pairs
    above restricts itself to (`if len(siblings) != 2: continue`): a
    conflict is only reported when `ticker` would become exactly the
    SECOND open position on its event. See the len() check below for why.

    Unlike find_me_pairs (which needs both siblings in the same tick's
    REST-fetched `markets` batch - narrow, watchlist-scoped), this reads
    market_titles/event_titles: the persisted, catalog-wide caches
    (services/title_cache.py) that decision_bridge.py already reads for
    every signal regardless of watchlist membership. Works for a candidate
    ticker that has never been on the watchlist - see docs/superpowers/
    specs/2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md.

    Scoped to open_position_tickers (small - pass a live view of currently-
    open positions, e.g. broker.positions.keys(), never a periodically-
    refreshed snapshot; see the caller's own docstring on why) rather than
    scanning the full market_titles catalog by event_ticker - same cost
    shape as position_netting.find_groups, which already does this safely
    on the hot path. O(open positions), not O(catalog)."""
    info = market_titles.get(ticker)
    event_ticker = info.get("event_ticker") if info else None
    me_flag = (event_titles.get(event_ticker) or {}).get("mutually_exclusive") if event_ticker else None
    if info is None or not event_ticker or me_flag is None:
        # Genuinely undetermined - counted, never silently treated as a
        # confirmed non-conflict (code-review finding, 2026-08-30): a
        # missing market_titles entry, a market_titles entry with no
        # event_ticker yet, and an event_titles entry whose
        # mutually_exclusive flag hasn't been backfilled are three
        # different "can't tell yet" states that all deserve the same
        # observability treatment - distinct from Kalshi's own confirmed
        # mutually_exclusive=False, a real, determined non-conflict that
        # must NOT inflate this counter.
        _me_pairing_stats["me_pairing_unknown_total"] += 1
        return None
    if me_flag is not True:
        return None
    same_event_open = [
        t for t in open_position_tickers
        if t != ticker and (market_titles.get(t) or {}).get("event_ticker") == event_ticker
    ]
    # Bounded to the genuine 2-outcome case, same proxy
    # position_netting.find_groups already uses for this identical problem
    # (len(members) != 2 there) - exactly one other open position on this
    # event means the candidate would make a real head-to-head pair; zero
    # means nothing to conflict with; two-or-more means this event is
    # already N-way in practice (a real subset of a larger field, e.g. a
    # golf tournament), which is out of scope for this entry-side gate -
    # position_netting.py already exists to manage N-way exposure
    # post-entry. See docs/superpowers/specs/2026-08-30-entry-gate-me-
    # pairing-and-netting-remediation-design.md's Part 1 scope boundary.
    if len(same_event_open) != 1:
        return None
    return same_event_open[0]
