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
before this module existed) with the spec's dispatch table applied only
to the 9 named-deviant types (minus `esports_match`/`political_race`,
deferred to Tasks 7/8 pending live-payload verification - see the comment
on _EXTRACTORS below). This is not a redesign of D2's goal - every fix the
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


def _no_live_outcome(details: dict) -> dict:
    return {"status": None, "winner": None}


_EXTRACTORS = {
    # Only the census's named-deviant types (S3 ∪ S4) get their own entry.
    # esports_match and political_race are added by Tasks 7/8 once their
    # flagged assumptions (spec §2.3) are live-verified - until then they
    # take the _pass_through default below, which is IDENTICAL to today's
    # behavior for them (both currently read details.get("winner") raw), so
    # this task changes nothing observable for those two types yet.
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
