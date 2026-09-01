"""
Broad, watchlist-independent event_ticker -> milestone_id discovery
(docs/superpowers/specs/2026-08-30-entry-gate-me-pairing-and-netting-
remediation-design.md, Part 3). services/kalshi/public.py's
get_milestones_bulk (category-scoped, batched - live-verified 2026-08-15:
one call covered 1,483 distinct related_event_tickers) has been
implemented and tested since before this module existed, but had zero
callers anywhere in the app - confirmed by repo-wide grep, not assumed.
The only wired milestone lookup (services/market_watch/live_status.py's
_fetch_live_status) iterates the same per-tick watchlist-scoped `markets`
list the entry-gate ME-pairing bug (services/mutual_exclusivity.py) does,
so today score/clock capture (services/game_state.py) only ever sees
whatever's on the watchlist.

This module does not fetch live data itself - only WHICH events have a
milestone and what its id is, independent of markets. live_status.py's
_fetch_live_status checks state["milestone_by_event"] first and only
falls back to a batched get_events(needs_fetch, with_milestones=True)
call (kalshi-category-data-completeness Task 10 - was a per-event
get_milestones_for_event call, one REST round trip per uncached event,
until then) when this broad cache hasn't covered an event yet, so a
cold cache reproduces the same logical fallback this module always
had, just via fewer real REST calls now rather than byte-identically
the same call.

WHAT THIS DOES NOT DO YET (corrected 2026-08-30, final-review finding -
the first version of this docstring, the design spec's Part 3, and the
implementation plan all read as if the watchlist gap described above were
now closed; it is not). _fetch_live_status still builds its `to_poll`
list entirely from the per-tick, watchlist-scoped `markets` argument, and
only THEN consults this cache, after `to_poll` has already been truncated
to _LIVE_STATUS_MAX_POLL_PER_TICK. So the measured effect of this module
today is narrower than "broader coverage": it removes redundant per-event
get_milestones_for_event REST calls for events that were going to be
polled anyway. An event this scan knows about but that never appears in
`markets` is still never polled, so game_state.py's score/clock capture
sees exactly the same event set it saw before. Real broadening - having
_fetch_live_status also poll milestone-known events outside `markets` -
is an unimplemented follow-up (docs/open-decisions.md, 2026-08-30): it
changes what the live-status poll budget is spent on, so it needs its own
design and review rather than riding in on a REST-call-reduction change.

Deliberately NOT rewiring catalog_scan.propagate_milestone_winners onto
this same cache (it shares the identical narrow pattern, found during
this module's own design spec's self-review) - that function feeds
settlement-outcome data real trading decisions consume, so touching it is
its own, separately-reviewed follow-up.
"""
import asyncio
import time

from services import http_client, task_supervisor
from services.app_state import state
from services.kalshi.public import KalshiPublicGateway

_MILESTONE_SCAN_MIN_INTERVAL_SEC = 300  # Discovery only (which events have
# a milestone + its id), not the live score/clock read itself - that stays
# on live_status.py's own, identically-paced _LIVE_STATUS_REPOLL_SEC (5 min,
# same 300s - not "more frequent", the two just happen to share a cadence
# today) cadence. A starting point, not a measured optimum - see the design
# spec's Part 3 "Verification after shipping" section.

_WATERMARK_OVERLAP_SEC = 5  # docs/kalshi/get-milestones.md documents
# min_updated_ts as "updated AFTER this Unix timestamp" - read as exclusive
# (strictly greater than), not "at or after" (self-review finding,
# 2026-08-30). A milestone updated in the exact same integer second as a
# cycle's own scan_started_at, but not yet reflected in that cycle's
# response (still in flight when it changed), would never be asked for
# again once the watermark advances to that exact second - a real, if
# narrow, boundary the code didn't previously guard. Re-asking for a few
# seconds of overlap every cycle is free (state["milestone_by_event"]'s
# writes are last-write-wins and idempotent) and removes the gap entirely;
# 5s comfortably covers request latency plus this repeated-fetch cost, not
# a measured optimum.


async def _scan_milestone_batch(client: KalshiPublicGateway, cfg: dict) -> None:
    """One get_milestones_bulk call per configured category
    (cfg["kalshi"]["categories"]), each watermarked since ITS OWN last
    successful run (state["milestone_scan"]["watermarks"], category ->
    Unix seconds; a category with no entry yet asks for everything). Every
    related_event_ticker on every returned milestone maps to that
    milestone's id in state["milestone_by_event"] - last-write-wins on a
    collision, the same plain-dict-overwrite convention every other cache
    in this package uses (mve_scan's new_event_titles, event_metadata's
    _fetch_event_titles). A category call that raises is logged and
    skipped for this cycle, same posture as mve_scan._scan_mve_batch's own
    per-series failure handling - no logging framework exists yet (ddev
    logs -s fastapi is the visibility path), and the rest of the batch
    still lands.

    Per-category, not one shared scalar (corrected 2026-08-30,
    final-review finding): the first version advanced a single watermark
    unconditionally at the end of every cycle, including a cycle where
    every category failed. A category that stayed down would have had its
    window marched forward anyway and would permanently skip whatever
    changed while it was down, the moment it recovered - a silent
    completeness hole of exactly the kind CLAUDE.md's data-plane rule
    forbids, and the opposite of what mve_scan/catalog_scan do (a failure
    means don't mark it scanned)."""
    milestone_state = state["milestone_scan"]
    watermarks = milestone_state["watermarks"]
    # One timestamp for the whole cycle, taken BEFORE the calls go out, so
    # anything created while this cycle is in flight falls inside the next
    # cycle's window rather than between the two.
    scan_started_at = int(time.time())
    categories = cfg["kalshi"]["categories"]
    results = await asyncio.gather(
        *(client.get_milestones_bulk(category, min_updated_ts=watermarks.get(category)) for category in categories),
        return_exceptions=True,
    )
    for category, result in zip(categories, results):
        if not isinstance(result, list):
            # Watermark deliberately left where it was - this category
            # re-asks for the same window next cycle instead of skipping it.
            print(f"[milestone_scan] scan failed for category {category!r}, will retry next cycle: {result!r}")
            continue
        for ms in result:
            ms_id = ms.get("id")
            if not ms_id:
                continue
            for event_ticker in ms.get("related_event_tickers") or []:
                state["milestone_by_event"][event_ticker] = ms_id
        # int, not the raw float time.time() returns: docs/kalshi/
        # get-milestones.md documents min_updated_ts as `integer, format:
        # int64`, and the vendored SDK's stricter get_milestones_with_http_info
        # sibling types the same field StrictInt (get_milestones itself is
        # `Any`-typed, so nothing rejects a float client-side). Live-verified
        # 2026-08-30: a float returns HTTP 400 "Invalid format for parameter
        # min_updated_ts: ... strconv.ParseInt ... invalid syntax", which the
        # isinstance(result, list) skip above would swallow, so the cache
        # would never grow past its cold-start run (review finding, task-5
        # fix pass; see docs/kalshi/CHEATSHEET.md). Overlap-buffered (see
        # _WATERMARK_OVERLAP_SEC) against min_updated_ts's exclusive
        # boundary - never advances past a point that could still exclude a
        # genuinely-new-this-cycle update.
        watermarks[category] = max(watermarks.get(category) or 0, scan_started_at - _WATERMARK_OVERLAP_SEC)


def _maybe_scan_milestone_batch(cfg: dict) -> None:
    """Triggers _scan_milestone_batch as an independent background task on
    its own steady interval - mirrors mve_scan._maybe_scan_mve_batch
    exactly (same overlap guard shape, same task_supervisor wiring)."""
    milestone_state = state["milestone_scan"]
    now_ts = time.time()
    due = now_ts - milestone_state["last_started_at"] > _MILESTONE_SCAN_MIN_INTERVAL_SEC
    if due and not milestone_state["scanning"]:
        milestone_state["scanning"] = True
        milestone_state["last_started_at"] = now_ts
        milestone_state["task"] = task_supervisor.supervise(
            lambda: _scan_milestone_batch_background(cfg),
            component="milestone_scan", operation="scan_batch",
        )


@http_client.classify("background_catalog")
async def _scan_milestone_batch_background(cfg: dict) -> None:
    """Owns its own KalshiPublicGateway - same reasoning as
    mve_scan._scan_mve_batch_background (the calling tick's own client
    closes at the end of that same tick)."""
    milestone_state = state["milestone_scan"]
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _scan_milestone_batch(client, cfg)
    finally:
        milestone_state["scanning"] = False
        await client.close()
