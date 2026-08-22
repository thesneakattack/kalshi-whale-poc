from datetime import datetime, timezone

import pytest

from services.market_events import event_lifecycle as el


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- is_tournament_style ----------------------------------------------------

def test_is_tournament_style_true_for_mutually_exclusive_with_enough_siblings():
    assert el.is_tournament_style(sibling_count=69, mutually_exclusive=True) is True


def test_is_tournament_style_false_for_two_way_head_to_head():
    assert el.is_tournament_style(sibling_count=2, mutually_exclusive=True) is False


def test_is_tournament_style_false_when_not_mutually_exclusive():
    # High sibling count alone isn't enough - many unrelated markets could
    # share an event ticker without being a real mutually-exclusive field.
    assert el.is_tournament_style(sibling_count=50, mutually_exclusive=False) is False


def test_is_tournament_style_none_mutually_exclusive_treated_as_false():
    # None = not yet known (main.py's own convention for this field before
    # an event is fully cached) - not guessed True.
    assert el.is_tournament_style(sibling_count=50, mutually_exclusive=None) is False


def test_is_tournament_style_respects_custom_min_siblings():
    assert el.is_tournament_style(sibling_count=4, mutually_exclusive=True, min_siblings=5) is False
    assert el.is_tournament_style(sibling_count=5, mutually_exclusive=True, min_siblings=5) is True


# --- classify_phase: the real live incident that motivated this module -----

def test_classify_phase_real_pga_tournament_day_3_is_mid_series():
    # The exact real case: FedEx St. Jude Championship, real dates Aug
    # 13-16 2026, occurrence_datetime=Aug 16 (the tournament's own final
    # day, confirmed identical across all 69 real sibling markets), 69
    # golfers, mutually_exclusive=True. "Now" is Aug 15 - the tournament's
    # own day 3 of 4 - which the pre-existing single-game-shaped window
    # would have called pre_tail (~24h before occurrence_datetime, outside
    # even a same-day lookahead).
    occurrence = "2026-08-16T00:00:00Z"
    now = _ts(datetime(2026, 8, 15, 12, 0, 0))
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=69, mutually_exclusive=True,
        close_time="2026-08-30T00:00:00Z",
    )
    assert phase == el.MID_SERIES


def test_classify_phase_same_tournament_would_be_pre_tail_11_days_out():
    occurrence = "2026-08-16T00:00:00Z"
    now = _ts(datetime(2026, 8, 5, 0, 0, 0))  # 11 days before
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=69, mutually_exclusive=True,
    )
    assert phase == el.PRE_TAIL


def test_classify_phase_same_tournament_first_day_is_mid_series():
    occurrence = "2026-08-16T00:00:00Z"
    now = _ts(datetime(2026, 8, 13, 8, 0, 0))  # tournament's real day 1
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=69, mutually_exclusive=True,
    )
    assert phase == el.MID_SERIES


# --- classify_phase: single-game shape unaffected (existing window kept) ---

def test_classify_phase_single_game_24h_before_kickoff_is_pre_tail():
    # A real single game (2 sibling markets) 24h before kickoff must NOT
    # get the wide tournament lookahead - the existing 1h-before window
    # for single games is deliberately untouched.
    occurrence = "2026-08-16T03:00:00Z"
    now = _ts(datetime(2026, 8, 15, 3, 0, 0))  # exactly 24h before
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=2, mutually_exclusive=True,
    )
    assert phase == el.PRE_TAIL


def test_classify_phase_single_game_within_1h_before_kickoff_is_mid_series():
    occurrence = "2026-08-16T03:00:00Z"
    now = _ts(datetime(2026, 8, 16, 2, 30, 0))  # 30 min before
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=2, mutually_exclusive=True,
    )
    assert phase == el.MID_SERIES


def test_classify_phase_single_game_within_6h_after_kickoff_is_mid_series():
    occurrence = "2026-08-16T03:00:00Z"
    now = _ts(datetime(2026, 8, 16, 8, 0, 0))  # 5h after
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=2, mutually_exclusive=True,
    )
    assert phase == el.MID_SERIES


def test_classify_phase_single_game_well_after_kickoff_is_post_tail():
    occurrence = "2026-08-16T03:00:00Z"
    now = _ts(datetime(2026, 8, 16, 12, 0, 0))  # 9h after, past the 6h lookback
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=2, mutually_exclusive=True,
    )
    assert phase == el.POST_TAIL


# --- classify_phase: edge cases ---------------------------------------------

def test_classify_phase_no_occurrence_datetime_returns_no_occurrence_bucket():
    now = _ts(datetime(2026, 8, 15, 12, 0, 0))
    assert el.classify_phase(occurrence_datetime=None, now=now) == el.NO_OCCURRENCE


def test_classify_phase_unparseable_occurrence_datetime_returns_no_occurrence():
    now = _ts(datetime(2026, 8, 15, 12, 0, 0))
    assert el.classify_phase(occurrence_datetime="not-a-date", now=now) == el.NO_OCCURRENCE


def test_classify_phase_past_close_time_is_post_tail_regardless_of_occurrence():
    # Same "trading has already stopped there regardless" shortcut
    # main.py._fetch_live_status already uses.
    occurrence = "2026-08-16T00:00:00Z"
    now = _ts(datetime(2026, 8, 31, 0, 0, 0))  # after close_time, still within a naive mid-series read of occurrence
    phase = el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=69, mutually_exclusive=True,
        close_time="2026-08-30T00:00:00Z",
    )
    assert phase == el.POST_TAIL


def test_classify_phase_respects_custom_tournament_pretail_days():
    occurrence = "2026-08-16T00:00:00Z"
    now = _ts(datetime(2026, 8, 12, 0, 0, 0))  # 4 days before
    # Default (5 days) -> mid_series; a tighter 2-day config -> pre_tail.
    assert el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=69, mutually_exclusive=True,
    ) == el.MID_SERIES
    assert el.classify_phase(
        occurrence_datetime=occurrence, now=now, sibling_count=69, mutually_exclusive=True,
        tournament_pretail_days=2,
    ) == el.PRE_TAIL


# --- phase_ranked ------------------------------------------------------------

def _market(ticker, event_ticker, volume, occurrence_datetime=None, close_time=None):
    return {
        "ticker": ticker, "event_ticker": event_ticker, "volume_24h_fp": volume,
        "occurrence_datetime": occurrence_datetime, "close_time": close_time,
    }


def test_phase_ranked_no_occurrence_markets_keep_pure_volume_order():
    # No occurrence_datetime at all (political/economic futures) - must
    # never be phase-weighted, per the doc's own explicit carve-out.
    markets = [
        _market("A", "EV-A", volume=100),
        _market("B", "EV-B", volume=500),
        _market("C", "EV-C", volume=50),
    ]
    ranked = el.phase_ranked(markets, event_titles={}, now=1000.0)
    assert [m["ticker"] for m in ranked] == ["B", "A", "C"]


def test_phase_ranked_mid_series_outranks_pre_tail_at_similar_volume():
    now = 1_800_000_000.0
    far_future = now + 20 * 86400  # 20 days out - pre_tail under default 5-day window
    soon = now + 3600  # 1h out - mid_series (within the 6h lookback either direction is fine here)
    markets = [
        _market("PRETAIL", "EV-1", volume=1000, occurrence_datetime=_iso_from_ts(far_future)),
        _market("MIDSERIES", "EV-2", volume=900, occurrence_datetime=_iso_from_ts(soon)),
    ]
    event_titles = {"EV-1": {"mutually_exclusive": True}, "EV-2": {"mutually_exclusive": True}}
    # Bump sibling count past the tournament threshold for both so phase
    # actually applies (mutually_exclusive alone isn't enough - see
    # is_tournament_style).
    for _ in range(4):
        markets.append(_market("PRETAIL-SIB", "EV-1", volume=1, occurrence_datetime=_iso_from_ts(far_future)))
        markets.append(_market("MIDSERIES-SIB", "EV-2", volume=1, occurrence_datetime=_iso_from_ts(soon)))
    ranked = el.phase_ranked(markets, event_titles, now=now)
    # MIDSERIES (900 * 1.0 = 900) must outrank PRETAIL (1000 * 0.4 = 400)
    # despite PRETAIL's higher raw volume - the exact "outrank similar
    # volume" behavior the hardening doc asked for.
    assert ranked[0]["ticker"] == "MIDSERIES"


def test_phase_ranked_does_not_bury_a_much_larger_pre_tail_market():
    # A genuinely huge pre-tail market should still be able to outrank a
    # trivial mid-series one - multiplicative weighting, not a hard
    # phase-first sort.
    now = 1_800_000_000.0
    far_future = now + 20 * 86400
    soon = now + 3600
    markets = [_market("HUGE-PRETAIL", "EV-1", volume=1_000_000, occurrence_datetime=_iso_from_ts(far_future))]
    markets += [_market(f"PRETAIL-SIB{i}", "EV-1", volume=1, occurrence_datetime=_iso_from_ts(far_future)) for i in range(4)]
    markets.append(_market("TINY-MIDSERIES", "EV-2", volume=5, occurrence_datetime=_iso_from_ts(soon)))
    markets += [_market(f"MID-SIB{i}", "EV-2", volume=1, occurrence_datetime=_iso_from_ts(soon)) for i in range(4)]
    event_titles = {"EV-1": {"mutually_exclusive": True}, "EV-2": {"mutually_exclusive": True}}
    ranked = el.phase_ranked(markets, event_titles, now=now)
    assert ranked[0]["ticker"] == "HUGE-PRETAIL"


def test_phase_ranked_is_a_stable_sort_preserving_original_order_on_ties():
    markets = [_market("A", "EV-A", volume=100), _market("B", "EV-B", volume=100)]
    ranked = el.phase_ranked(markets, event_titles={}, now=1000.0)
    assert [m["ticker"] for m in ranked] == ["A", "B"]
