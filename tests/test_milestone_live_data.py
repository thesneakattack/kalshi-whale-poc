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


# ---------------------------------------------------------------------------
# esports_match (Task 7, kalshi-category-data-completeness). Step 0's
# verification spike queried this app's OWN captured production history
# (data/game_state.db's game_states table - NOT data/game_states.db, a
# stray empty file the task brief's filename didn't match; the real
# DB_PATH is services/game_state.py:58) for real esports_match live-data
# lifecycles, since game_state.record() persists every live-data poll's
# full raw `details` (services/market_watch/live_status.py:254). Four
# distinct event_tickers spanning a genuine live -> finished transition
# were found and read directly (KXCS2GAME-26AUG290900THEAE,
# KXDOTA2GAME-26AUG300400MOUZNAVI, KXLOLGAME-26AUG281315FNCVIT,
# KXVALORANTGAME-26AUG270400NSGENG - CS2/Dota2/LoL/Valorant, all under
# Kalshi's Esports category per the census cited in the module docstring),
# confirming ticker-prefix identity against sport-column values 'Esports'/
# 'Dota 2'/'Valorant' (game_state.record's own event_type column is None
# for these rows - live_status.py:254 never passes it - but the ticker
# prefix + sport label + exact field shape match the census's
# independently-derived esports_match key list, docs/superpowers/research/
# 2026-08-30-kalshi-category-data-shape-audit.md line 325).
#
# Q1 (status): widget_status is a reliable 3-state field for this type -
# "created" (pregame, home_score/away_score both 0) -> "live" (in
# progress) -> "complete" (finished) - verified across all four tickers'
# full lifecycles. is_live is perfectly redundant with it in every
# captured row (true iff widget_status == "live") and strictly less
# informative (is_live=false can't distinguish "not started" from
# "finished" the way widget_status's three-way value can), so it adds
# nothing worth threading through. This makes Task 5's default
# pass-through (details.get("widget_status")) already correct for status -
# no explicit branch is needed for that half.
#
# Q2 (winner): confirmed absent from every real captured payload, matching
# the census (S4/D2: 9,385/10,706 of the whole exchange's no-winner
# figure). Critically, NO team-identity field of any kind appears anywhere
# in the live-data `details` this function receives - no home_team/
# away_team name, and none surfaces in home_stats/away_stats either (those
# are numeric per-map stat blocks: kills, gold_earned, a per-map 0/1
# "winner" flag, etc., keyed by side, never by name). home_score/
# away_score (final map tally, e.g. CS2's 1-2) do let you derive WHICH
# SIDE won once widget_status == "complete" - but catalog_scan.py's own
# consumer (its only real caller, catalog_scan.py:154-167) matches
# `winner` as a substring against a related market's custom_strike values/
# yes_sub_title/no_sub_title - team/player NAME text - so a bare "home"/
# "away" side label would never match anything there; returning one would
# look resolved while being silently useless downstream, which is worse
# than the honest None. docs/kalshi/targets_and_milestones.md:28 confirms
# why no better answer exists in scope: `home_team_id`/`away_team_id`
# (when present) live on the MILESTONE object's own separate `details`
# field, never on the live-data `details` this function's signature
# receives (and Task 7's brief forbids a signature change to thread that
# through). So `winner: None` is not a placeholder pending more data - it
# is the correct, evidence-based answer for every payload this app has
# ever captured for this type, exactly mirroring golf_tournament's already
# -shipped shape (real status pass-through, explicit-None winner) for the
# same underlying reason: a real value exists on the exchange side, but
# not inside the one payload this module is given.
def test_esports_match_status_passes_through_widget_status_pregame_live_and_complete():
    # Real values from KXVALORANTGAME-26AUG270400NSGENG's full lifecycle
    # (pregame "created" -> "live" -> "complete"), not the task sketch's
    # guessed "finished" - Kalshi's actual finished value for this type is
    # "complete".
    assert mld.extract("esports_match", {"widget_status": "created", "home_score": 0, "away_score": 0,
                                          "is_live": False})["status"] == "created"
    assert mld.extract("esports_match", {"widget_status": "live", "home_score": 1, "away_score": 0,
                                          "is_live": True})["status"] == "live"
    assert mld.extract("esports_match", {"widget_status": "complete", "home_score": 2, "away_score": 1,
                                          "is_live": False})["status"] == "complete"


def test_esports_match_winner_is_always_none_even_when_complete():
    # Real captured shape (KXCS2GAME-26AUG290900THEAE's finished row):
    # home_score/away_score/home_periods/away_periods/home_stats/
    # away_stats/is_live all present, no `winner` key and no team-name
    # field anywhere to derive one from - never surface a guessed value.
    live = mld.extract("esports_match", {"widget_status": "live", "home_score": 1, "away_score": 0, "is_live": True})
    assert live["winner"] is None
    finished = mld.extract("esports_match", {
        "widget_status": "complete", "is_live": False, "home_score": 1, "away_score": 2,
        "home_periods": {"period_1": 13, "period_2": 7, "period_3": 8},
        "away_periods": {"period_1": 11, "period_2": 13, "period_3": 13},
    })
    assert finished["winner"] is None


def test_esports_match_never_raises_on_empty_details():
    assert mld.extract("esports_match", {}) == {"status": None, "winner": None}


def test_has_no_live_status_false_for_esports_match():
    # Unlike company_report/truflation/.../one_off_milestone (structurally
    # never a discrete live event), esports_match's status IS real
    # pass-through - only its winner is the always-None part, the same
    # shape as golf_tournament above.
    assert mld.has_no_live_status("esports_match") is False


def test_esports_match_is_registered_not_left_on_the_default_path(monkeypatch):
    # The one observable difference Task 7 makes (extract()'s return value
    # for esports_match is otherwise identical before/after - see the
    # comment block above): before this task esports_match had no
    # _EXTRACTORS entry, so every call fell through to _pass_through AND
    # tripped _record_default_path_type's once-per-process fault_log write
    # + default_path_types_snapshot() entry - correct behavior for a
    # genuinely undecided type, but esports_match's mapping is now decided
    # and verified (see comment block above), so it should no longer be
    # reported as an unmapped/default-path type going forward.
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                         lambda *a, **k: faults.append(a) or True)
    mld.extract("esports_match", {"widget_status": "live", "home_score": 1, "away_score": 0})
    assert faults == []
    assert "esports_match" not in mld.default_path_types_snapshot().get("types", [])
