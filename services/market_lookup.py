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


def _close_time_by_ticker() -> dict:
    # ticker -> close_time, for check_exits' runway-exhausted gate
    # (strategy.exit_min_seconds_to_close, ROADMAP #1). Same
    # already-in-memory, zero-new-API-calls construction as
    # _category_by_ticker below, but sourced from state["markets"] rather
    # than market_titles: close_time is mutable upstream
    # (docs/kalshi/market_lifecycle.md's close_date_updated event), so this
    # deliberately reads the freshest per-tick markets list every call
    # instead of anything cached at entry time.
    return {
        m["ticker"]: m.get("close_time")
        for m in (state.get("markets") or [])
        if m.get("ticker") and m.get("close_time")
    }


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
