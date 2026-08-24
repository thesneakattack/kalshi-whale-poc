"""
Small, pure ticker/event -> category/subcategory/close_time lookups built
from the already-in-memory services.app_state.state (zero new API calls).
Shared by position management (strategy_engine.check_exits' runway gate),
history/analytics (trade_category, whale-confidence segmentation), and
main.py's own _handle_signal - extracted into its own module (2026-08-21,
part of main.py's modularization pass) rather than owned by any one of
those, since all three already depended on it.
"""
from services.app_state import state


def effective_close_time(m: dict | None) -> str | float | None:
    """The real time THIS market's own outcome should be treated as
    resolved/closing - not Kalshi's raw administrative close_time alone.
    2026-08-24 fix for a direct bug report: "trading windows... will close
    at the conclusion of that event vs. the scheduled market close time."
    Every gate that reasoned about "time until this market closes" used
    raw close_time only - for an event-style market Kalshi leaves open a
    month or more past when the real-world event (and its outcome) is
    already known, that gate was computing against a number with no
    relationship to when the position actually needs managing. Confirmed
    live, 2026-08-23: KXVOTEPRIMARY-FLPRIMARY06R26ABAK-9's close_time was
    359.5 days out; its real primary-election date (occurrence_datetime)
    was 5.5 days in the PAST.

    Precedence, most authoritative first:
      1. expected_expiration_time - Kalshi's own per-market forecast of
         when the outcome will be known (docs/kalshi/market_lifecycle.md:
         "the time the event is likely to resolve... close_time may be set
         well into the future to allow for rescheduling"). See
         services/market_watch/market_fetch.py's _MARKET_FIELDS. SKIPPED
         when it's identical to close_time - live-confirmed 2026-08-24 on
         a real runoff-eligible race (KXMAYORLA-26): Kalshi sets both
         fields to the exact same far-out placeholder (2027-06-02, ~7
         months past the real Nov 2026 election) rather than a real
         per-market forecast, presumably because a possible runoff means
         Kalshi itself doesn't know the real date either. Trusting it
         unconditionally in that case would silently shadow tier 2 below,
         which (via the milestone API) resolves this exact event
         correctly. A genuinely-differing expected_expiration_time is
         unaffected by this check and still wins outright.
      2. state["event_schedules"][event_ticker]["end_ts"] - the real-world
         event-schedule resolver (services/market_events/event_schedule.py's
         4-source waterfall), when resolved AND it actually found an
         end_ts (its own docstring: milestone end_date is "almost always
         null," so this tier is often skipped even when start_ts IS known
         - that's fine, the next tier decides).
      3. occurrence_datetime - already fetched, already used for
         is_live/event-phase classification. event_schedule.py's own
         docstring: "occurrence_datetime already does a reasonable job of
         [the end of an event's window]."
      4. close_time - Kalshi's raw administrative field, current/original
         behavior, final fallback.

    Returns whatever the winning tier's own native type is - an ISO-8601
    string for tiers 1/3/4, a float unix timestamp for tier 2 (event_
    schedule.py persists start_ts/end_ts as floats) -
    market_history.seconds_to_close accepts either. None when every tier
    is empty, exactly like close_time alone did before this existed -
    never guessed."""
    if not m:
        return None
    expected = m.get("expected_expiration_time")
    if expected and expected != m.get("close_time"):
        return expected
    event_ticker = m.get("event_ticker")
    if event_ticker:
        schedule = (state.get("event_schedules") or {}).get(event_ticker)
        if schedule and schedule.get("end_ts"):
            return schedule["end_ts"]
    occurrence = m.get("occurrence_datetime")
    if occurrence:
        return occurrence
    return m.get("close_time")


def _close_time_by_ticker() -> dict:
    # ticker -> effective close/resolution time (see effective_close_time
    # above), for check_exits' runway-exhausted gate
    # (strategy.exit_min_seconds_to_close, ROADMAP #1) and main.py's
    # _validate_fill fill-time re-check - both already treat this as an
    # opaque "close time" value fed straight into
    # market_history.seconds_to_close, so fixing the SOURCE here fixes
    # both call sites with zero changes needed at either one. Same
    # already-in-memory, zero-new-API-calls construction as
    # _category_by_ticker below, but sourced from state["markets"] rather
    # than market_titles: close_time/expected_expiration_time are mutable
    # upstream (docs/kalshi/market_lifecycle.md's close_date_updated
    # event; event_schedules updates via _maybe_resolve_event_schedules),
    # so this deliberately reads the freshest per-tick markets list every
    # call instead of anything cached at entry time.
    result = {}
    for m in (state.get("markets") or []):
        ticker = m.get("ticker")
        if not ticker:
            continue
        ect = effective_close_time(m)
        if ect:
            result[ticker] = ect
    return result


def _category_by_ticker() -> dict:
    # Per-series/category config overrides (services/config_overrides.py,
    # 2026-08-15 direct request) - built from the already-in-memory
    # state["market_titles"]/state["event_titles"] (pure dict comprehension,
    # zero new API calls), covering every KNOWN ticker rather than just
    # this tick's markets list, so an open position that's rotated off the
    # watchlist still resolves a category for check_exits.
    return {
        ticker: (state["event_titles"].get(info.get("event_ticker")) or {}).get("category")
        for ticker, info in state["market_titles"].items()
        if info.get("event_ticker")
    }


def _sport_for_event(event_info: dict) -> str | None:
    # SPORT ("Baseball"), not the finer per-competition string
    # ("Pro Baseball") - reverse-mapped through category_metadata's
    # sport_by_competition (see _fetch_category_metadata's own comment:
    # get-filters-for-sports.md documents competitions as nested WITHIN a
    # sport, e.g. filters_by_sports["Baseball"]["competitions"] contains
    # "Pro Baseball"/"Japan NPB"/"Korea KBO"/"Mexico LMB" - several
    # competitions, one sport). Falls back to the raw competition string
    # only if the reverse map hasn't been built yet (category_metadata's
    # first fetch hasn't completed) or doesn't recognize it - a real
    # subcategory late is better than none, even if slightly coarser than
    # intended for one refresh cycle.
    competition = event_info.get("competition")
    if not competition:
        return None
    sport_by_competition = state["category_metadata"].get("sport_by_competition") or {}
    return sport_by_competition.get(competition, competition)


def _subcategory_by_ticker() -> dict:
    # Mirrors _category_by_ticker exactly, one field over - 2026-08-16
    # direct request for a series -> subcategory -> category fallback
    # chain in whale-confidence win-rate segmentation (see
    # services/trade_category.py's own subcategory docstring for why this
    # isn't category_tags - that field is the same full facet-filter
    # vocabulary on every event in a category, not per-event data).
    return {
        ticker: _sport_for_event(state["event_titles"].get(info.get("event_ticker")) or {})
        for ticker, info in state["market_titles"].items()
        if info.get("event_ticker")
    }
