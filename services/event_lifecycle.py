"""
Pre-tail / mid-series / post-tail activity-phase classification.

docs/hardening-and-accuracy-roadmap-2026-08-11.md Part 1, direct request:
"i want my system to account for things like this. pre-tail, mid-series,
post-tail type analysis" - triggered by a real screenshot of a months-out
tournament-champion futures market ranking on stale cumulative volume
despite the event not having started. Never implemented until now - a
second, concrete live report pointed straight at the same root cause from a
different symptom: real whale signals on `KXPGATOUR-FESJC26` (the FedEx St.
Jude Championship, real tournament dates Aug 13-16) were being skipped
"close time is not within the trade window" on Aug 15 - the tournament's
own THIRD day, not "pre-tail" by any reasonable definition.

Root cause, confirmed against Kalshi's real API before writing this (not
assumed): `occurrence_datetime` does not mean the same thing for every
market shape.
- Single-game markets (e.g. KXNFLGAME, 2 sibling markets, one per team)
  reliably use it as the actual kickoff/start moment - a well-defined
  single point in time. The existing `main.py._fetch_live_status` window
  (1h before through 6h after) is correct for this shape and is left
  untouched here.
- Tournament/field "outright winner" markets (e.g. KXPGATOUR-FESJC26, 69
  sibling markets, one per golfer) share one identical event-level
  `occurrence_datetime` across every sibling - confirmed directly (all 69
  markets in the event return the exact same value). Empirically this is
  the tournament's final/decisive day, not its start, so a narrow window
  around it misses the multi-day event entirely: on Aug 15 (day 3 of a
  4-day tournament that runs Aug 13-16), `occurrence_datetime` (Aug 16) is
  still ~24h in the future - outside even a same-day lookahead, let alone
  the 1h one built for single games.

Detecting which shape a market belongs to doesn't require guessing at
sport-specific tournament lengths: `mutually_exclusive=True` combined with
a real sibling-market count (`tournament_min_siblings`, default 4) is a
structural signal already present in this app's own event metadata
(`state["event_titles"][...]["mutually_exclusive"]`) - a genuine head-to-
head game has 2 sibling markets; an outright-winner-across-a-field market
has however many real competitors are in the field, reliably more than a
handful. `tournament_pretail_days` (default 5) is a reasoned starting
value, not a measured one - wide enough to cover a standard 4-day event
(the confirmed real case) plus a day of margin, same "ships with a
reasoned value, gets recalibrated once real data exists" precedent as
services/whale_simulator.py's DEFAULT_WEIGHTS. Config-tunable
(`event_lifecycle.tournament_min_siblings`/`tournament_pretail_days`), not
hardcoded, so it can be corrected per real data without a code change.

Markets with no single occurrence at all (political/economic futures,
season-long futures, mention markets - the doc's own explicit carve-out)
get their own "no_occurrence" bucket rather than being forced into a phase
that doesn't apply - callers should leave these on today's pure-volume
ranking untouched, exactly as the doc specifies.
"""
from datetime import datetime

PRE_TAIL = "pre_tail"
MID_SERIES = "mid_series"
POST_TAIL = "post_tail"
NO_OCCURRENCE = "no_occurrence"

_SINGLE_LOOKAHEAD_SEC = 3600       # matches main.py's _LIVE_STATUS_LOOKAHEAD_SEC
_LOOKBACK_SEC = 6 * 3600           # matches main.py's _LIVE_STATUS_LOOKBACK_SEC


def _parse_ts(value: str | None) -> float | None:
    # Same parsing idiom as market_history.seconds_to_close - no value (or
    # an unparseable one) returns None rather than guessing.
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def is_tournament_style(sibling_count: int, mutually_exclusive: bool | None, min_siblings: int = 4) -> bool:
    """A real structural signal, not a sport-specific guess - see this
    module's own docstring. mutually_exclusive=None (unknown, e.g. an
    event not yet fully cached - see main.py's own None-means-not-yet-
    known convention for this exact field) is treated as False, not
    guessed True."""
    return bool(mutually_exclusive) and sibling_count >= min_siblings


def classify_phase(
    occurrence_datetime: str | None,
    now: float,
    sibling_count: int = 1,
    mutually_exclusive: bool | None = False,
    close_time: str | None = None,
    tournament_min_siblings: int = 4,
    tournament_pretail_days: float = 5.0,
) -> str:
    """Returns one of PRE_TAIL / MID_SERIES / POST_TAIL / NO_OCCURRENCE.

    A market past its own close_time is always POST_TAIL regardless of
    occurrence_datetime - same "trading has already stopped there
    regardless of what else would say" shortcut main.py._fetch_live_status
    already uses for exactly this case."""
    close_ts = _parse_ts(close_time)
    if close_ts is not None and now > close_ts:
        return POST_TAIL

    occ_ts = _parse_ts(occurrence_datetime)
    if occ_ts is None:
        return NO_OCCURRENCE

    lookahead_sec = (
        tournament_pretail_days * 86400
        if is_tournament_style(sibling_count, mutually_exclusive, tournament_min_siblings)
        else _SINGLE_LOOKAHEAD_SEC
    )
    delta = now - occ_ts  # negative = occurrence is still in the future
    if delta < -lookahead_sec:
        return PRE_TAIL
    if delta <= _LOOKBACK_SEC:
        return MID_SERIES
    return POST_TAIL


_DEFAULT_PRE_TAIL_VOLUME_WEIGHT = 0.4
_DEFAULT_POST_TAIL_VOLUME_WEIGHT = 0.2


def phase_ranked(
    markets: list[dict],
    event_titles: dict,
    now: float,
    tournament_min_siblings: int = 4,
    tournament_pretail_days: float = 5.0,
    pre_tail_volume_weight: float = _DEFAULT_PRE_TAIL_VOLUME_WEIGHT,
    post_tail_volume_weight: float = _DEFAULT_POST_TAIL_VOLUME_WEIGHT,
) -> list[dict]:
    """Re-sorts markets (input already volume-sorted, same convention as
    KalshiClient.get_candidate_markets) so mid_series/no_occurrence
    candidates outrank pre_tail/post_tail ones of comparable real volume -
    docs/hardening-and-accuracy-roadmap-2026-08-11.md Part 1, integration
    point 1 ("mid-series candidates should outrank pre-tail/post-tail ones
    of similar raw volume, freeing watchlist slots for markets where whale
    signals actually mean something right now").

    Multiplicative down-weight on a per-market ranking score, not a hard
    phase-first sort - the doc's own wording asks for outranking *similar*
    volume, not unconditionally regardless of scale, so a genuinely huge
    pre-tail futures market can still occasionally surface rather than
    being buried behind every vol-5 mid-series market that exists. The
    weights are reasoned starting values (not measured), same "ships with
    a reasoned value, gets recalibrated once real data exists" precedent
    as this module's tournament_pretail_days.

    no_occurrence markets (political/economic futures, mention markets -
    no single occurrence to judge phase against) are always weight 1.0 -
    explicitly left on pure volume, per the doc's own carve-out. Stable
    sort - ties (e.g. two no_occurrence markets) keep their input order."""
    sibling_counts: dict[str, int] = {}
    for m in markets:
        et = m.get("event_ticker")
        if et:
            sibling_counts[et] = sibling_counts.get(et, 0) + 1
    weight = {
        MID_SERIES: 1.0, NO_OCCURRENCE: 1.0,
        PRE_TAIL: pre_tail_volume_weight, POST_TAIL: post_tail_volume_weight,
    }

    def _rank_score(m: dict) -> float:
        et = m.get("event_ticker")
        phase = classify_phase(
            occurrence_datetime=m.get("occurrence_datetime"),
            now=now,
            sibling_count=sibling_counts.get(et, 1) if et else 1,
            mutually_exclusive=(event_titles.get(et) or {}).get("mutually_exclusive") if et else False,
            close_time=m.get("close_time"),
            tournament_min_siblings=tournament_min_siblings,
            tournament_pretail_days=tournament_pretail_days,
        )
        return float(m.get("volume_24h_fp") or 0) * weight[phase]

    return sorted(markets, key=_rank_score, reverse=True)
