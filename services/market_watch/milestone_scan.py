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
falls back to its existing per-event get_milestones_for_event call when
this broad cache hasn't covered an event yet, so a cold cache is
byte-identical to pre-existing behavior.

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
# on live_status.py's own, more frequent _LIVE_STATUS_REPOLL_SEC (5 min)
# cadence. A starting point, not a measured optimum - see the design
# spec's Part 3 "Verification after shipping" section.


async def _scan_milestone_batch(client: KalshiPublicGateway, cfg: dict) -> None:
    """One get_milestones_bulk call per configured category
    (cfg["kalshi"]["categories"]), watermarked since this function's own
    last successful run (a single scalar shared across every category in
    one cycle - the cycle's own start time, so nothing created mid-cycle
    is missed). Every related_event_ticker on every returned milestone
    maps to that milestone's id in state["milestone_by_event"] -
    last-write-wins on a collision, the same plain-dict-overwrite
    convention every other cache in this package uses (mve_scan's
    new_event_titles, event_metadata's _fetch_event_titles). A category
    call that raises is logged and skipped for this cycle, same posture as
    mve_scan._scan_mve_batch's own per-series failure handling - no
    logging framework exists yet (ddev logs -s fastapi is the visibility
    path), and the rest of the batch still lands."""
    milestone_state = state["milestone_scan"]
    watermark = milestone_state["watermark"] or None
    scan_started_at = time.time()
    categories = cfg["kalshi"]["categories"]
    results = await asyncio.gather(
        *(client.get_milestones_bulk(category, min_updated_ts=watermark) for category in categories),
        return_exceptions=True,
    )
    for category, result in zip(categories, results):
        if not isinstance(result, list):
            print(f"[milestone_scan] scan failed for category {category!r}, will retry next cycle: {result!r}")
            continue
        for ms in result:
            ms_id = ms.get("id")
            if not ms_id:
                continue
            for event_ticker in ms.get("related_event_tickers") or []:
                state["milestone_by_event"][event_ticker] = ms_id
    milestone_state["watermark"] = scan_started_at


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
