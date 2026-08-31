"""
Tests for services/market_watch/milestone_live_data.py - the shared
per-milestone-type live-data extractor (kalshi-category-data-completeness
Task 5). See the module's own docstring for the default-pass-through-not-
null design decision and its evidence (census S3/S4,
docs/superpowers/research/2026-08-30-kalshi-category-data-shape-audit.md).
"""
from services.market_watch import milestone_live_data as mld


def test_tennis_tournament_singles_is_pure_pass_through():
    # The census's control case (docs/superpowers/research/2026-08-30-kalshi-
    # category-data-shape-audit.md S3/S4): 49,764/49,764 payloads carry both
    # fields. Not in _EXTRACTORS - reaches this result via the default path.
    result = mld.extract("tennis_tournament_singles",
                          {"widget_status": "live", "winner": "Player A"})
    assert result == {"status": "live", "winner": "Player A"}


def test_an_unnamed_type_also_gets_pass_through_not_null():
    # basketball_game/soccer_tournament_multi_leg (15,564/15,546 milestones,
    # the census's 2nd/3rd-largest populations) are NOT individually named
    # anywhere in this module and must still resolve their real fields - the
    # regression this task exists to prevent (see Global Constraints).
    result = mld.extract("basketball_game", {"widget_status": "finished", "winner": "Lakers"})
    assert result == {"status": "finished", "winner": "Lakers"}


def test_golf_tournament_passes_through_status_but_not_winner():
    # S4: 0/169 golf_tournament live payloads carry `winner` - explicit None
    # until a real `leaderboard` item shape is read (spec §2.3).
    result = mld.extract("golf_tournament", {"widget_status": "live", "winner": "should not surface"})
    assert result == {"status": "live", "winner": None}


def test_settlement_input_types_are_explicit_no_ops():
    for t in ("company_report", "truflation", "artist_streams", "kpis", "tv_views", "one_off_milestone"):
        assert mld.extract(t, {"status": "reported", "winner": "should not surface"}) == \
            {"status": None, "winner": None}, t


def test_extract_never_raises_on_empty_or_none_details():
    assert mld.extract("tennis_tournament_singles", {}) == {"status": None, "winner": None}
    assert mld.extract("tennis_tournament_singles", None) == {"status": None, "winner": None}


def test_a_new_default_path_type_is_fault_logged_once_per_process(monkeypatch):
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                         lambda *a, **k: faults.append(a) or True)
    mld.extract("some_type_never_seen_before", {"widget_status": "live"})
    mld.extract("some_type_never_seen_before", {"widget_status": "live"})  # same process, same type
    assert len(faults) == 1


def test_default_path_types_snapshot_is_empty_when_nothing_seen():
    assert mld.default_path_types_snapshot() == {}


def test_default_path_types_snapshot_lists_distinct_types_seen():
    mld.extract("a_seen_type", {})
    mld.extract("another_seen_type", {})
    snap = mld.default_path_types_snapshot()
    assert snap["total_distinct_types"] == 2
    assert set(snap["types"]) == {"a_seen_type", "another_seen_type"}


def test_has_no_live_status_true_for_every_no_live_outcome_type():
    # Added for Task 6 (kalshi-category-data-completeness): live_status.py's
    # schedule-fallback gate needs to distinguish "this type structurally
    # never has a live status" from "not confirmed this particular tick" -
    # sourced from the same dispatch table extract() itself uses, not a
    # second, independently-maintained list.
    for t in ("company_report", "truflation", "artist_streams", "kpis", "tv_views", "one_off_milestone"):
        assert mld.has_no_live_status(t) is True, t


def test_has_no_live_status_false_for_golf_tournament():
    # golf_tournament's status IS real pass-through (only its winner is the
    # no-op part) - the schedule fallback still means something for it, so
    # it must NOT be treated the same as the always-None types above.
    assert mld.has_no_live_status("golf_tournament") is False


def test_has_no_live_status_false_for_pass_through_and_unknown_types():
    assert mld.has_no_live_status("tennis_tournament_singles") is False  # default pass-through
    assert mld.has_no_live_status("basketball_game") is False  # unnamed, also default pass-through
    assert mld.has_no_live_status(None) is False  # no confirmed type at all - not a no-op type either


def test_extract_and_snapshot_are_re_exported_from_the_package():
    # services/market_watch/__init__.py re-exports every submodule's public
    # names at the package top level (see its own module docstring) - Task
    # 6's call sites import the submodule directly, but this is the one
    # place that would catch a typo'd export name or a circular import
    # before Task 6 (or any other future caller using the package-level
    # form) hits it.
    import services.market_watch as market_watch
    assert market_watch.extract is mld.extract
    assert market_watch.default_path_types_snapshot is mld.default_path_types_snapshot
