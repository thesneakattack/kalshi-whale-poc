"""
Shared per-milestone-type live-data extractor (kalshi-category-data-
completeness Task 5, D2). Normalizes the `details` dict Kalshi's
milestone/live-data system returns for ANY milestone `type` into
{"status": ..., "winner": ...} for `live_status.py`/`catalog_scan.py`'s
two call sites (Task 6).

WHY THE DEFAULT IS PASS-THROUGH, NOT NULL (read this before touching
_EXTRACTORS or the fallback in extract())

The design spec's §2.2 pseudocode for `extract()` returns `(None, None)`
for any milestone `type` not in its dispatch table. Cross-checking that
against the design's own cited evidence - the full-population live-data
census in docs/superpowers/research/2026-08-30-kalshi-category-data-
shape-audit.md, rows S3/S4 - shows this would silently regress live-data
coverage for roughly 21 of the census's 30 live-data-bearing milestone
types, including `basketball_game` (15,564 milestones) and
`soccer_tournament_multi_leg` (15,546), the 2nd/3rd-largest populations
after `tennis_tournament_singles` (49,764/49,764 live payloads carrying
both `widget_status` and `winner` - the census's control case) - because
those types already carry `widget_status`/`winner` today and are not
among the 9 types S3/S4 name as actually lacking one or both fields:
S3 (no `widget_status`) names `political_race`, `one_off_milestone`,
`company_report`, `truflation`, `artist_streams`, `kpis`, `tv_views`
(1,962 of 112,501 live milestones, 1.7%); S4 (no `winner`) names
`esports_match`, `one_off_milestone`, `company_report`, `truflation`,
`golf_tournament`, `artist_streams`, `kpis`, `tv_views` (10,706 of
112,501, 9.5%). `golf_tournament` is the one type in S4 but not S3: it
carries `widget_status` (via `leaderboard`/`current_round`) but never
`winner` (0 of 169 live).

So this module implements a **default pass-through** (matching today's
pre-existing behavior for anything not named as deviant - both call sites
already did a raw `details.get("widget_status")` / `details.get("winner")`
before this module existed) with the spec's dispatch table applied to all
9 named-deviant types: `esports_match` (Task 7, live-payload-verified
against four real captured match lifecycles - see _esports_match's own
docstring) and `political_race` (Task 8, live-payload-verified against a
real Elections-category pull - this app's own captured history had zero
political_race rows, so a live pull was required; see _political_race's
own docstring) both now have their own mapping. This
is not a redesign of D2's goal - every fix the
spec's §2.1-§2.3 describes still ships - it is a correction to how the
"unmapped type" default is implemented, grounded in the same evidence
document the spec itself cites, per CLAUDE.md's data-plane completeness
HARD RULE ("a dropped message, skipped candidate, or DB hole is a
defect") and the never-guess HARD RULE (this is a verified count from the
cited census, not a new assumption).

CALL-FREQUENCY NOTE: `extract()` itself is a pure dict lookup plus at
most two dict.get() calls - no I/O in the steady state. The only I/O is
`_record_default_path_type()`'s fault_log write, and that fires at most
once per distinct milestone `type` per process (see its own docstring)
specifically so a hot-path type doesn't turn into a SQLite connection
open per call/signal. Task 6's two call sites are both repoll-gated,
watchlist-scoped per-tick polls (`_LIVE_STATUS_REPOLL_SEC` /
`_MILESTONE_REPOLL_SEC`), not the per-trade whale hot path.
"""
from services import fault_log


def _pass_through(details: dict) -> dict:
    return {"status": details.get("widget_status"), "winner": details.get("winner")}


def _golf_tournament(details: dict) -> dict:
    return {"status": details.get("widget_status"), "winner": None}


def _esports_match(details: dict) -> dict:
    """Task 7 (kalshi-category-data-completeness), live-verified against
    four real captured lifecycles in this app's own data/game_state.db
    (game_states table, DB_PATH services/game_state.py:58 - NOT the
    non-existent data/game_states.db the task brief's SQL sketch named):
    KXCS2GAME-26AUG290900THEAE, KXDOTA2GAME-26AUG300400MOUZNAVI,
    KXLOLGAME-26AUG281315FNCVIT, KXVALORANTGAME-26AUG270400NSGENG - CS2/
    Dota2/LoL/Valorant, each spanning pregame -> live -> finished. See
    tests/test_milestone_live_data.py's esports_match comment block for
    the full evidence trail. Two-line summary:

    status: `widget_status` is real and reliable for this type - verified
    3-state ("created" pregame, "live", "complete" finished) across every
    captured row - so this is genuine pass-through, identical in shape to
    tennis/basketball's default path (`is_live` is redundant with it and
    less informative, so not threaded through separately).

    winner: verified permanently unavailable from THIS payload - no
    `winner` key (matches census S4/D2) and no team-name field anywhere
    (checked home_stats/away_stats too - numeric per-map stat blocks, not
    identity). home_score/away_score reveal which SIDE won once finished,
    but catalog_scan.py's only consumer matches `winner` as a NAME
    substring against a related market's custom_strike/yes_sub_title/
    no_sub_title (catalog_scan.py:154-167), so a bare "home"/"away" would
    silently never match anything - worse than the honest None, not
    better. docs/kalshi/targets_and_milestones.md:28 confirms why: a
    resolvable `home_team_id`/`away_team_id` lives on the MILESTONE
    object's own separate `details`, never on the live-data `details` this
    function receives (and this task's brief forbids a signature change to
    thread that through). Same shape as `_golf_tournament` above, for the
    same reason: a real answer exists exchange-side, just not in this
    payload."""
    return {"status": details.get("widget_status"), "winner": None}


def _no_live_outcome(details: dict) -> dict:
    return {"status": None, "winner": None}


# Task 8 (kalshi-category-data-completeness). This app's OWN captured
# history (data/game_state.db's game_states table) has ZERO political_race
# rows - `SELECT COUNT(*) FROM game_states WHERE event_type =
# 'political_race'` returns 0, and a raw_json scan for
# race_call_status/tabulation_status/candidates/votehub across all 9,072
# rows (every event_type, including NULL) also returns 0 - so this
# module's status-vocabulary mapping was built from a real LIVE pull
# instead of replayed history (this app's own unauthenticated
# KalshiPublicGateway.get_milestones_bulk(category="Elections") +
# get_live_datas(), docs/kalshi/get-milestones.md +
# get-multiple-live-data.md - `category` filters by category STRING, not
# by milestone `type`, since the wrapper only exposes `category`): 500
# Elections-category milestones fetched 2026-08-31, 410 carrying live
# data. See tests/test_milestone_live_data.py's political_race comment
# block for the full evidence trail (observed field-value distribution,
# every (race_call_status, tabulation_status) combination and count). Two
# -line summary:
#
# status: derived from `race_call_status`, the only field of the two
# named in the design spec's flagged assumption (§2.3) that actually
# varies - `tabulation_status` is perfectly correlated with it in every
# observed row (never independently empty/non-empty) and adds no extra
# signal for this tri-state mapping. "Called" -> "finished" (a winner has
# been decided). "Too Early to Call" -> "live" (ballots actively being
# counted right now - genuinely in-play, the plain meaning of "live").
#
# "Runoff" -> "none", NOT "live" and NOT "finished" (fix-round 2, task
# re-review - fix-round 1 tried None and that was NOT sufficient, see
# below). The original "live" choice was justified only against
# live_status.py's OWN polling completeness (a single live pull can't
# observe whether "Runoff" later transitions to "Called" once a separate
# future runoff election concludes, so mapping to "finished" would
# permanently stop polling under `_LIVE_STATUS_TERMINAL` - genuinely a
# completeness risk, CLAUDE.md's data-plane HARD RULE). What that
# justification missed: `status` isn't only consumed by live_status.py's
# own polling loop - it flows into `state["live_status"][event_ticker]`,
# which becomes `is_live` in decision_bridge.py:88 and then
# strategy_engine.py's evaluate(), where `is_live=True` BYPASSES real
# entry-risk gates: the close_window_sec check (strategy_engine.py:~405),
# the post-incident minimum-runway protection added for ROADMAP #1
# (strategy_engine.py:~428-437, after 755 KXBTC15M whale signals fired in
# the final 60 seconds of their market's life and 12/25 stop-losses fired
# only after price had already gapped past the configured limit), the
# special-market conservative gate (strategy_engine.py:~484), and the
# longshot entry-threshold bonus removal (strategy_engine.py:~135). A race
# called "Runoff" is the OPPOSITE of in-play: it's dead time between the
# initial election and a separately-scheduled future runoff, not ballots
# being actively counted (unlike "Too Early to Call", which genuinely is).
# "live" was the wrong direction for that risk - it's the choice that
# COULD silently relax real entry protections on a not-actually-live
# market.
#
# Fix-round 1 (this same reasoning) shipped `None` instead, and a
# re-review caught it doesn't actually work: `live_status.py`'s own
# `_fetch_live_status` only routes a status into its `confirmed[et]` dict
# when the returned value is TRUTHY (`if status: confirmed[et] = status`,
# live_status.py:~232) - Python `None` is falsy, so a `None` status from
# this extractor is silently treated as "no confirmation happened this
# tick" (the same bucket as a transient fetch miss), not as "confirmed
# not-live." That event then falls through to the SCHEDULE fallback a few
# lines below (live_status.py:~287-292), which re-derives status purely
# from `now` vs the market's scheduled `occurrence_datetime` - and for any
# event past its original election's occurrence time (true for essentially
# every real "Runoff" case), that fallback computes exactly "live" again,
# reproducing the original bug with zero net effect. `"none"` (the
# non-empty STRING, matching the pre-race "" branch below) is truthy, so
# it flows straight into `confirmed[et]` and is used as-is - it never
# reaches the schedule fallback at all. `"none"` is not in
# `_LIVE_STATUS_TERMINAL` (only {"finished", "closed"} are), so polling
# completeness is unaffected (the original justification's actual
# concern), and every real consumer of `live_status` in this codebase
# (`decision_bridge.py`'s `is_live`, `whale_simulator.py`'s `live_only`
# filter, `frontend/src/js/shared-utils.js`'s `isLive()`) checks `==
# "live"` specifically - nothing anywhere checks `== "none"` as a distinct
# case, confirmed by a repo-wide grep - so `"none"` and the originally
# intended `None` are functionally identical for every actual reader
# except the one that mattered: unlike `None`, `"none"` is truthy, so it
# doesn't silently fall through live_status.py's own confirmed-vs-fallback
# branch. ""
# (empty string, the real pre-race shape: `candidates: {}`,
# `reporting_percentage: "0.0"`) -> "none". A missing `race_call_status`
# key entirely (the real "votehub-only" shape - `{"provider": "votehub",
# "status": "created", "votehub": {...FEC data...}}`, no race-call field
# at all) -> None, honestly: no live-data signal exists this tick, not a
# guessed "none".
#
# winner: real pass-through, unchanged in shape from every other type's
# `winner` handling - `details.get("winner") or None` (the `or None`
# normalizes the real empty-string shape every non-"Called" row actually
# carries into this module's existing "no winner" convention, matching
# every other extractor's explicit `None` rather than a falsy ""). The
# value itself is a candidate-ID string (e.g.
# "a7d2a5af-72a1-46cb-a3b0-bf639f346fb9"), never a name - resolving that
# ID to a candidate name is D4's own separate, not-yet-built
# structured-target lookup, out of this task's scope; catalog_scan.py's
# `not winner` guard (catalog_scan.py:131) already treats an empty string
# the same as None, so this normalization changes no downstream behavior,
# it only keeps this module's own return-value convention consistent.
_POLITICAL_RACE_STATUS = {
    "": "none",
    "Called": "finished",
    "Runoff": "none",
    "Too Early to Call": "live",
}


_political_race_unmapped_status_seen: set[str] = set()


def _record_unmapped_political_race_status(race_call_status: str) -> None:
    # Mirrors _record_default_path_type's shape exactly (once per distinct
    # value per process - fault_log.py already dedupes by message but a
    # write still costs a connection open per call without this gate).
    # Final whole-branch review finding: _POLITICAL_RACE_STATUS's 4 known
    # values came from ONE 500-milestone pull on one day - an unobserved
    # value (e.g. "Contested"/"Recount") would otherwise fall through
    # .get()'s default to Python None, which is falsy and silently
    # reproduces the EXACT is_live entry-gate-bypass bug Task 8's two fix
    # rounds closed for "Runoff" specifically (live_status.py's
    # confirmed[et] check only routes a truthy value; a falsy None falls
    # to the schedule fallback, which re-derives "live" for any event past
    # its occurrence_datetime).
    if race_call_status in _political_race_unmapped_status_seen:
        return
    _political_race_unmapped_status_seen.add(race_call_status)
    fault_log.record_fault(
        "milestone_live_data", "unmapped_political_race_status",
        f"{race_call_status!r}: not in _POLITICAL_RACE_STATUS's observed vocabulary - "
        "defaulting to the safe 'none' string (truthy, non-terminal, is_live=False) "
        "rather than a guessed live/finished mapping",
        severity="warning",
    )


def _political_race(details: dict) -> dict:
    race_call_status = details.get("race_call_status")
    if race_call_status is None:
        # Genuinely missing key (the votehub-only shape) - honest None,
        # not a guess, matching this module's own documented convention.
        status = None
    elif race_call_status in _POLITICAL_RACE_STATUS:
        status = _POLITICAL_RACE_STATUS[race_call_status]
    else:
        # A real signal Kalshi sent, just not one this module's mapping
        # has observed yet - "none" is the safe default (see
        # _record_unmapped_political_race_status's own docstring for why
        # this specifically avoids reproducing the is_live bypass), not a
        # guessed live/finished value.
        _record_unmapped_political_race_status(race_call_status)
        status = "none"
    return {
        "status": status,
        "winner": details.get("winner") or None,
    }


_EXTRACTORS = {
    # Only the census's named-deviant types (S3 ∪ S4) get their own entry.
    # esports_match was added by Task 7 once its flagged assumptions (spec
    # §2.3) were live-verified against real captured payloads (see
    # _esports_match's own docstring) - registering it here changes NO
    # observable extract() output for this type (the default pass-through
    # it replaces already produced the identical {"status": widget_status,
    # "winner": None} shape, since a `winner` key never appears in a real
    # esports_match payload); what it does change is removing esports_match
    # from _record_default_path_type's fault-log/snapshot tracking, since
    # its mapping is now a verified, deliberate decision rather than an
    # unmapped type. political_race was added by Task 8 once its own
    # flagged assumption (§2.3's status-vocabulary question) was
    # live-verified (see _political_race's own comment above) - unlike
    # esports_match, this DOES change observable output for `status` (was
    # always None via the default pass-through's `widget_status` read,
    # since political_race payloads never carry that field - S3; now a
    # real derived tri-state value); `winner` behavior is unchanged
    # (already real pass-through via the same default, modulo the
    # empty-string-to-None normalization noted above, which no consumer
    # observes differently per catalog_scan.py:131's `not winner` guard).
    "esports_match": _esports_match,
    "political_race": _political_race,
    "golf_tournament": _golf_tournament,
    "company_report": _no_live_outcome,
    "truflation": _no_live_outcome,      # D3 §3.3 adds its OWN fields separately
    "artist_streams": _no_live_outcome,  # (indicator/timeseries etc.) - not status/winner
    "kpis": _no_live_outcome,
    "tv_views": _no_live_outcome,
    "one_off_milestone": _no_live_outcome,
}

_default_path_types_seen: set[str] = set()


def _record_default_path_type(milestone_type: str) -> None:
    # Fires once per distinct type per process (resets on restart, same
    # shape as the event-scoped-me-gate plan's _me_gate_unknown_logged) -
    # not once per call, so a hot-path type doesn't become a SQLite write
    # per signal (services/fault_log.py dedupes by message, but a write
    # still costs a connection open per call without this gate).
    if milestone_type in _default_path_types_seen:
        return
    _default_path_types_seen.add(milestone_type)
    fault_log.record_fault(
        "milestone_live_data", "default_path_milestone_type",
        f"{milestone_type}: no explicit extractor, using tennis-shaped pass-through "
        "by default - see module docstring for why default != null",
        severity="info",
    )


def default_path_types_snapshot() -> dict:
    """Observability read for every milestone `type` that has hit the
    default pass-through path this process (Task 5 own use only - the
    `state`/`GET /api/observability/summary` plumbing, if a future task
    wires it in, follows the `me_gate_snapshot()` precedent from
    docs/superpowers/plans/2026-08-29-event-scoped-me-gate.md Task 5; not
    required for D2 to ship correctly since fault_log already durably
    records every new type regardless)."""
    if not _default_path_types_seen:
        return {}
    return {"total_distinct_types": len(_default_path_types_seen),
            "types": sorted(_default_path_types_seen)}


def has_no_live_status(milestone_type: str) -> bool:
    """True only for a milestone `type` whose extractor unconditionally
    returns status=None (mapped to `_no_live_outcome` above - company_report/
    truflation/artist_streams/kpis/tv_views/one_off_milestone: an index
    series or report, structurally never a discrete clocked event with a
    genuine live/in-progress state) - as opposed to a type whose status
    merely wasn't confirmed on a given call (a transient miss, or a type
    that just hasn't started) but genuinely can carry one.

    Added for Task 6 (kalshi-category-data-completeness): live_status.py's
    schedule-fallback gate ("no confirmed status this tick -> guess live/
    none from the schedule instead") predates this module and doesn't know
    about these no-op types on its own - without this, it would guess
    "live" for e.g. a company_report milestone just because extract()
    correctly returned status=None for it, defeating the point of routing
    that type through the no-op extractor in the first place. golf_tournament
    is deliberately NOT included: its status is real pass-through (only its
    winner is the no-op part), so the schedule fallback still means
    something for it."""
    return _EXTRACTORS.get(milestone_type) is _no_live_outcome


def extract(milestone_type: str, details: dict) -> dict:
    """Normalizes ANY milestone type's live-data `details` into
    {"status": ..., "winner": ...}. Default is pass-through
    (details.get("widget_status")/.get("winner")), matching this app's
    pre-existing behavior for the ~21 of 30 live-data-bearing milestone
    types the census found carry both fields with tennis-shaped values -
    NOT a null default (see this module's own docstring / this plan's
    Global Constraints for why)."""
    details = details or {}
    extractor = _EXTRACTORS.get(milestone_type)
    if extractor is None:
        _record_default_path_type(milestone_type)
        return _pass_through(details)
    return extractor(details)


# Task 13 (kalshi-category-data-completeness): extract_index_series() is a
# separate accessor for truflation and artist_streams types' domain-specific
# fields (index series data), not a change to extract()'s status/winner
# contract. Per spec §3.3, this is capture-ready plumbing for a future
# consumer reading index_feed-style data (D3 work, not yet wired). The field
# names below are sourced from the design spec's own P3 census
# (docs/superpowers/specs/2026-08-30-kalshi-category-data-completeness-
# design.md:589-601) and cross-checked against the test fixtures in this
# module's tests (test_extract_index_series_returns_truflation_fields and
# test_extract_index_series_returns_artist_streams_fields).
_INDEX_SERIES_FIELDS = {
    "truflation": ["indicator", "latest_value", "target_date", "series_key", "timeseries"],
    "artist_streams": ["timeseries_daily", "timeseries_weekly", "current_total",
                       "period_start", "period_end", "target_week_finalized"],
}


def extract_index_series(milestone_type: str, details: dict) -> dict | None:
    """Extracts domain-specific fields for index/report milestone types
    (truflation, artist_streams) that carry no widget_status/winner but do
    carry their own structured time-series data. Returns a dict of those
    fields for truflation/artist_streams, None for any other type.

    This is separate from extract()'s status/winner contract - those remain
    (None, None) as originally designed for these types. No call site yet
    (spec §3.3 doesn't name a consumer - future D3 work reading index_feed
    -style data); this is capture-ready plumbing."""
    details = details or {}
    fields = _INDEX_SERIES_FIELDS.get(milestone_type)
    if fields is None:
        return None
    return {field: details.get(field) for field in fields}
