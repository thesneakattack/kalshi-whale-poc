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


# ---------------------------------------------------------------------------
# political_race (Task 8, kalshi-category-data-completeness). Step 0's
# verification spike checked this app's OWN captured history FIRST
# (data/game_state.db's game_states table, same method as Task 7): ZERO
# political_race rows exist - `SELECT COUNT(*) FROM game_states WHERE
# event_type = 'political_race'` returns 0, and a `raw_json LIKE
# '%race_call_status%' OR ... '%tabulation_status%' OR ... '%candidates%'
# OR ... '%votehub%'` scan across ALL 9,072 rows (every event_type,
# including NULL) also returns 0 - this app has never actually captured a
# political_race live-data payload, unlike Task 7's esports_match which
# had four full lifecycles already on disk. So this task pulled real
# payloads live instead, via this app's own KalshiPublicGateway
# (unauthenticated per D4's own design note): `get_milestones_bulk(
# category="Elections")` (docs/kalshi/get-milestones.md: `category`
# filters by category string, NOT by milestone `type` - the wrapper only
# exposes `category`) then `get_live_datas()` on the political_race-typed
# ids (docs/kalshi/get-multiple-live-data.md). 500 Elections milestones
# fetched 2026-08-31, 410 carrying live data. Observed (race_call_status,
# tabulation_status, top-level `status`) combinations:
#   ("Called", {"Vote Certified"|"Gathering Certified Results"|
#    "Active Tabulation"|"Tabulation Paused"}, "created") -> 272/410.
#    `winner` is always a real candidate-id STRING keying into the
#    `candidates` dict (e.g. "a7d2a5af-72a1-46cb-a3b0-bf639f346fb9"), never
#    a name - id-to-name resolution is D4's own separate, not-yet-built
#    structured-target lookup, out of this task's scope; extract() just
#    passes the raw value through unchanged, same as every other type.
#   ("Runoff", ..., "created") -> 7/410, `winner` == "" (no single winner
#    - the race resolves to "goes to runoff" rather than a winning
#    candidate).
#   ("Too Early to Call", ..., "created") -> 2/410, `winner` == "" -
#    `candidates` populated with real, non-zero vote counts (ballots
#    actively being counted, no verdict yet - the genuine in-progress
#    state).
#   ("", "", "created") -> 116/410, `winner` == "" - pre-tabulation:
#    `candidates: {}`, `reporting_percentage: "0.0"`, `last_updated: ""`.
#   race_call_status key absent entirely -> 13/410 - "votehub-only"
#    payloads, matching the census's disjoint-36-of-810 population, though
#    the real shape is {"provider": "votehub", "status": "created",
#    "votehub": {...FEC campaign-finance filing data...}} (three keys, not
#    the task brief's simplified one-key {"provider": "votehub"} sketch -
#    kept as its own separate, still-valid test below since the sketch's
#    winner-is-None assertion holds either way) - no race-call field at
#    all, so no status signal exists this tick.
#
# Kalshi's top-level `details.status` field (present on all 410 payloads)
# is unconditionally "created" across every one of the above combinations
# - zero information, not used for anything here (named in the census's
# own key list but never claimed informative there either).
#
# Status mapping into this app's tri-state ("none"/"live"/"finished",
# matching live_status.py:216's schedule fallback - verified those exact
# three lowercase string values at that line before reusing them), plus
# None for genuine uncertainty:
#   "Called"            -> "finished" (a winner has been decided).
#   "Too Early to Call" -> "live" (ballots actively being counted right
#                           now - genuinely in-play).
#   "Runoff"            -> "none" (fix-round 2, task re-review - was
#                           originally "live", then fix-round 1 tried
#                           `None`, and re-review caught that doesn't
#                           actually work: live_status.py's own
#                           `_fetch_live_status` only routes a TRUTHY
#                           status into `confirmed[et]`; a falsy `None`
#                           silently falls through to the SCHEDULE
#                           fallback a few lines later, which re-derives
#                           "live" purely from occurrence_datetime for any
#                           event past its scheduled time - reproducing
#                           the original bug with zero net effect. `"none"`
#                           (the truthy string, same value the pre-race ""
#                           branch below already uses) flows straight into
#                           `confirmed[et]` and is used as-is, never
#                           reaching that fallback. It isn't in
#                           `_LIVE_STATUS_TERMINAL`, so polling continues
#                           on the normal cadence (no completeness loss),
#                           and nothing in this codebase checks `==
#                           "none"` as a distinct case - every real
#                           consumer (decision_bridge.py's `is_live`,
#                           whale_simulator.py's `live_only`, the
#                           frontend's `isLive()`) checks `== "live"`
#                           specifically, so `"none"` correctly reads as
#                           not-live everywhere `None` was meant to. See
#                           the full rationale in milestone_live_data.py's
#                           own _POLITICAL_RACE_STATUS comment block.
#   ""                  -> "none" (pre-race: `candidates` empty,
#                           `reporting_percentage` "0.0" - the same "not
#                           started yet" meaning every other type's "none"
#                           carries).
#   missing key          -> None (no live-data signal this tick at all -
#                           the votehub-only shape - the same "honest
#                           None, not a guess" precedent as esports_match's/
#                           golf_tournament's winner=None above).
def test_political_race_winner_is_pass_through():
    result = mld.extract("political_race", {"race_call_status": "Called", "winner": "Candidate A"})
    assert result["winner"] == "Candidate A"


def test_political_race_no_winner_when_only_votehub_provider_present():
    # Brief's own sketch (P3: "36 of 810 carry only `provider: votehub`");
    # the real payload shape carries two more keys (`status`, `votehub`)
    # but no race-call field either way, so the assertion holds unchanged.
    result = mld.extract("political_race", {"provider": "votehub"})
    assert result["winner"] is None


def test_political_race_status_is_finished_when_called():
    # Real shape (272/410 sampled payloads, condensed to the fields that
    # matter for this branch).
    result = mld.extract("political_race", {
        "race_call_status": "Called", "tabulation_status": "Vote Certified",
        "winner": "a7d2a5af-72a1-46cb-a3b0-bf639f346fb9",
        "winners": ["a7d2a5af-72a1-46cb-a3b0-bf639f346fb9"],
    })
    assert result == {"status": "finished", "winner": "a7d2a5af-72a1-46cb-a3b0-bf639f346fb9"}


def test_political_race_status_is_live_when_too_early_to_call():
    result = mld.extract("political_race", {
        "race_call_status": "Too Early to Call", "tabulation_status": "Active Tabulation",
        "winner": "",
    })
    assert result == {"status": "live", "winner": None}


def test_political_race_status_is_none_string_when_runoff_pending():
    # Deliberately the "none" STRING, not "live", "finished", or Python
    # None - see the comment block above for why (fix-round 2: "live"
    # bypassed real strategy_engine.py entry-risk gates via
    # decision_bridge.py's is_live for a race that isn't actually in-play;
    # fix-round 1's Python None was silently overridden back to "live" by
    # live_status.py's own schedule fallback since a falsy value never
    # reaches its confirmed[et] dict).
    result = mld.extract("political_race", {
        "race_call_status": "Runoff", "tabulation_status": "Vote Certified", "winner": "",
    })
    assert result == {"status": "none", "winner": None}


def test_political_race_status_is_none_when_pre_race():
    # Real pre-tabulation shape: empty-string race_call_status/
    # tabulation_status/winner, empty candidates dict.
    result = mld.extract("political_race", {
        "candidates": {}, "race_call_status": "", "tabulation_status": "",
        "reporting_percentage": "0.0", "last_updated": "", "winner": "",
    })
    assert result == {"status": "none", "winner": None}


def test_political_race_status_is_none_string_when_race_call_status_absent():
    # votehub-only payload (real shape: provider/status/votehub, no
    # race-call field at all). Final whole-branch review fix round: this
    # used to stay Python None ("honest no-signal, not a guess"), but a
    # falsy None here silently reproduced the same is_live entry-gate-
    # bypass the "Runoff"/unmapped-value fixes above close (see
    # _political_race's own comment for the full mechanism) - now the
    # same safe "none" STRING every other genuinely-uncertain case in
    # this extractor uses.
    result = mld.extract("political_race", {"provider": "votehub", "status": "created", "votehub": {}})
    assert result["status"] == "none"


def test_political_race_never_raises_on_empty_details():
    assert mld.extract("political_race", {}) == {"status": "none", "winner": None}


def test_political_race_unmapped_status_value_defaults_to_safe_none_not_python_none(monkeypatch):
    # Final whole-branch review finding: _POLITICAL_RACE_STATUS's 4 known
    # values came from one 500-milestone pull on one day - an unobserved
    # real value (e.g. Kalshi introducing "Contested") would otherwise fall
    # through .get()'s default to Python None, which is falsy and silently
    # reproduces the exact is_live entry-gate-bypass bug Task 8's two fix
    # rounds closed for "Runoff" specifically. The safe default is the
    # "none" STRING (same fix-round-2 reasoning as "Runoff"), not Python
    # None - distinct from the genuinely-missing-key case just above,
    # which correctly stays None (honest no-signal, not a guess).
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                         lambda *a, **k: faults.append(a) or True)
    result = mld.extract("political_race", {
        "race_call_status": "Contested", "winner": "",
    })
    assert result == {"status": "none", "winner": None}
    assert len(faults) == 1
    assert faults[0][:2] == ("milestone_live_data", "unmapped_political_race_status")


def test_political_race_unmapped_status_value_is_fault_logged_once_per_process(monkeypatch):
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                         lambda *a, **k: faults.append(a) or True)
    mld.extract("political_race", {"race_call_status": "Contested"})
    mld.extract("political_race", {"race_call_status": "Contested"})  # same process, same value
    assert len(faults) == 1


def test_political_race_missing_key_does_not_fault_log_unlike_the_unmapped_value_case(monkeypatch):
    # The genuinely-missing-key case (votehub-only payload, ~13/410
    # sampled - a known, designed-for shape) and the unmapped-value case
    # (a real value this module has never observed) now both map to the
    # same "none" string (final whole-branch review fix round - both
    # would otherwise reach the same is_live entry-gate bypass), but they
    # remain distinct in one way: the missing-key case is expected,
    # ordinary behavior and must NOT fault-log the way a genuinely
    # unexpected, unmapped value does.
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                         lambda *a, **k: faults.append(a) or True)
    result = mld.extract("political_race", {"provider": "votehub"})
    assert result["status"] == "none"
    assert faults == []


def test_has_no_live_status_false_for_political_race():
    # Its status IS real derived data (unlike company_report/truflation/
    # etc.'s structural always-None) - the schedule fallback still means
    # something for it, same shape as golf_tournament/esports_match above.
    assert mld.has_no_live_status("political_race") is False


def test_political_race_is_registered_not_left_on_the_default_path(monkeypatch):
    # Before this task political_race had no _EXTRACTORS entry, so every
    # call fell through to _pass_through (status always None, since
    # political_race payloads never carry `widget_status` - S3) AND
    # tripped _record_default_path_type's fault_log write. Its mapping is
    # now decided and verified (see comment block above), so it should no
    # longer be reported as an unmapped/default-path type.
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                         lambda *a, **k: faults.append(a) or True)
    mld.extract("political_race", {"race_call_status": "Called", "winner": "some-id"})
    assert faults == []
    assert "political_race" not in mld.default_path_types_snapshot().get("types", [])


# ---------------------------------------------------------------------------
# truflation and artist_streams (Task 13, kalshi-category-data-
# completeness). These two types are index/report milestones, not discrete
# events - they have no widget_status or winner fields (S3/S4). extract()
# correctly returns (None, None) for them via _no_live_outcome. This task
# adds a SEPARATE accessor, extract_index_series(), for their real
# domain-specific fields (indicator/timeseries for truflation, timeseries_daily/
# current_total for artist_streams, etc.). It is not a change to extract()'s
# status/winner contract - those remain (None, None) as originally designed.
#
# Step 0's verification (querying this app's own captured history in
# data/game_state.db): ZERO rows for either type -
#   SELECT event_type, COUNT(*) FROM game_states WHERE event_type IN
#   ('truflation', 'artist_streams') GROUP BY event_type -> []
#   SELECT COUNT(*) FROM game_states -> 9,139 total
#   SELECT DISTINCT event_type FROM game_states -> [None, 'commodity']
# This means these two types do NOT currently reach _fetch_live_status's
# watchlist/near-term-catalog scope in practice (they sit in Economics/Crypto/
# Entertainment categories, not Sports-heavy). Per the design spec §3.3:
# "if it doesn't [reach them], the fix is D2's discovery-scope question, not
# a new capture mechanism" - that's a separate, out-of-scope follow-up.
# The extractor still ships (it's correct and cheap regardless), and becomes
# useful the moment the discovery-scope gap is later closed.
def test_extract_index_series_returns_truflation_fields():
    result = mld.extract_index_series("truflation", {
        "indicator": "CPI", "latest_value": 3.2, "target_date": "2026-09-01",
        "series_key": "cpi-us", "timeseries": [{"t": 1, "v": 3.1}],
    })
    assert result == {"indicator": "CPI", "latest_value": 3.2, "target_date": "2026-09-01",
                       "series_key": "cpi-us", "timeseries": [{"t": 1, "v": 3.1}]}


def test_extract_index_series_returns_artist_streams_fields():
    result = mld.extract_index_series("artist_streams", {
        "current_total": 5000000, "timeseries_daily": [{"t": 1, "v": 100}],
        "timeseries_weekly": [{"t": 1, "v": 700}], "period_start": "2026-08-01",
        "period_end": "2026-08-07", "target_week_finalized": False,
    })
    assert result["current_total"] == 5000000
    assert result["target_week_finalized"] is False


def test_extract_index_series_returns_none_for_a_non_index_type():
    assert mld.extract_index_series("tennis_tournament_singles", {"widget_status": "live"}) is None
