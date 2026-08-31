"""
Milestone-based winner propagation, the series cache, and the background
task that incrementally builds services/market_catalog/market_catalog.py's
near-term catalog. Split out of market_watch.py (2026-08-22 modularization
Phase 9/9, the internal split of the 1,337-line file this package's own
Phase 7 extraction left as the largest file in the tree).
"""
import asyncio
import time
from datetime import datetime, timezone

from services import market_history, series_cache, task_supervisor
from services.app_state import bump_generation, state
from services.kalshi.public import KalshiPublicGateway
from services import http_client
from services.market_catalog import market_catalog

_MILESTONE_REPOLL_SEC = 60  # Repoll-cached (2026-08-15 tick_duration fix) -
# this used to call get_milestones_for_event() for every unique event on the
# watchlist, every tick, forever, unconditionally. Confirmed live as one of
# two per-event REST loops with zero caching, together accounting for the
# bulk of a ~27s tick_duration plateau that survived an earlier same-day
# rate-limit incident's own "no stone unturned" audit. Most events (crypto,
# politics, ...) never have a milestone at all, so this was 13+ wasted calls
# a tick for nothing. Once a winner is found for an event, it's cached
# permanently - a real-world outcome doesn't change, so there's never a
# reason to poll it again.


@http_client.classify("background_live_status")
async def propagate_milestone_winners(client: KalshiPublicGateway, markets: list[dict]) -> dict:
    """Best-effort: fetch first milestone per event, inspect its live-data
    for a declared `details.winner`, map that winner to a related market
    ticker when possible and set market_results for the related tickers
    (yes/no). Also records outcomes via market_history.record_outcome()
    so check_exits can close positions this tick. Returns the market_results
    mapping (ticker -> result) built from the provided markets plus any
    propagated winners. This is kept separate so it can be unit-tested.

    Repoll-cached in state["milestone_cache"] - see _MILESTONE_REPOLL_SEC
    above. A cache hit (event not due for repoll, or already resolved) costs
    zero API calls but still reapplies any already-known winner into this
    tick's market_results below, so callers see identical per-tick
    completeness to the pre-caching behavior - only the network cost was cut.

    Only includes a market's own `result` once `status` is `finalized`
    (2026-08-23 fix, services/exits/README.md's audit finding). Kalshi
    sets `result` the instant a market is `determined`, but the docs
    (docs/kalshi/market_lifecycle.md lines 21-24, 36-38, 68-72) are explicit
    that the result "may be disputed" during the settlement-timer window
    that follows, and can flip via `determined` -> `disputed` -> `amended`
    before finally reaching `finalized` ("Settlement complete... Terminal
    state"). Gating here means check_exits/close_if_settled (which consumes
    this dict) never closes a position - and market_analyst_agent/
    candidate_log's resolve_from_market_results (main.py, called with this
    same dict) never grade a call - on a result that could still reverse.
    An open position's own ticker keeps flowing through this function every
    tick regardless (main.py's extra_tickers), so it simply stays open
    through determined/disputed/amended and closes once finalized - slower
    to realize P&L, immune to the reversal this app previously had no
    correction path for. Milestone-winner results below are a separate,
    deliberately-earlier signal (a live-data winner declaration, not
    Kalshi's own market state machine) and are unaffected by this gate.
    """
    market_results = {
        m["ticker"]: m.get("result")
        for m in markets
        if m.get("ticker") and m.get("status") == "finalized"
    }
    try:
        event_tickers = list(dict.fromkeys(m.get("event_ticker") for m in markets if m.get("event_ticker")))
        cache = state["milestone_cache"]
        now = time.time()
        to_poll = [
            et for et in event_tickers
            if et not in cache or (
                not cache[et]["winner_found"] and (now - cache[et]["checked_at"]) >= _MILESTONE_REPOLL_SEC
            )
        ]
        if to_poll:
            milestone_tasks = await asyncio.gather(
                *(client.get_milestones_for_event(et) for et in to_poll), return_exceptions=True
            )
            # First pass: default every polled event to "no winner found
            # this tick" (recorded before any further fetch so a transient
            # failure below still throttles the retry to the next repoll
            # window), then collect the events that actually have a real
            # milestone id/type to check live-data for.
            milestone_by_event = {}
            for et, ms_result in zip(to_poll, milestone_tasks):
                cache[et] = {"checked_at": now, "winner_found": False, "related": None, "mapped_winner_ticker": None}
                if isinstance(ms_result, list) and ms_result:
                    ms = ms_result[0]
                    if ms.get("id") and ms.get("type"):
                        milestone_by_event[et] = ms

            # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
            # get-multiple-live-data.md) - was N individual get_live_data() calls,
            # one per event with a milestone, each inside this same loop.
            # One get_live_datas call now covers every event polled this
            # tick regardless of how many need it.
            live_datas = {}
            if milestone_by_event:
                live_datas = await client.get_live_datas([ms["id"] for ms in milestone_by_event.values()])

            # Pass 1: figure out which events actually have a declared
            # winner + related tickers to map (pure, no I/O), collecting
            # every related ticker across every such event into one flat,
            # deduped list.
            events_with_winner = []  # (et, ms, winner, related)
            all_related: list[str] = []
            seen_related = set()
            for et, ms in milestone_by_event.items():
                ld = live_datas.get(ms["id"])
                if not ld:
                    continue
                details = ld.get("details") or {}
                winner = details.get("winner")
                related = ms.get("related_event_tickers") or details.get("related_event_tickers") or []
                if not winner or not related:
                    continue
                events_with_winner.append((et, ms, winner, related))
                for t in related:
                    if t not in seen_related:
                        seen_related.add(t)
                        all_related.append(t)

            # Batched (2026-08-16, direct efficiency note: "a lot of
            # efficiency could be gained by using batch calls to the API vs
            # individual calls for specific markets") - was one gather of
            # individual get_market() calls PER event with a winner, inside
            # this same loop. One get_markets_by_tickers call now covers
            # every related ticker across every such event this tick,
            # regardless of how many events have a winner to map.
            related_market_by_ticker = await client.get_markets_by_tickers(all_related) if all_related else {}

            for et, ms, winner, related in events_with_winner:
                related_markets = [related_market_by_ticker[t] for t in related if t in related_market_by_ticker]
                mapped_winner_ticker = None
                for rm in related_markets:
                    if not isinstance(rm, dict):
                        continue
                    cs = rm.get("custom_strike") or {}
                    try:
                        if isinstance(cs, dict) and any(str(winner).lower() in str(v).lower() for v in cs.values()):
                            mapped_winner_ticker = rm.get("ticker")
                            break
                    except Exception:
                        pass
                    yst = (rm.get("yes_sub_title") or "")
                    nst = (rm.get("no_sub_title") or "")
                    if isinstance(winner, str) and winner:
                        wlow = winner.lower()
                        if yst and wlow in yst.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                        if nst and wlow in nst.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                        title = (rm.get("title") or "")
                        if title and wlow in title.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                if mapped_winner_ticker:
                    cache[et] = {
                        "checked_at": now, "winner_found": True,
                        "related": related, "mapped_winner_ticker": mapped_winner_ticker,
                    }
                    now_ts = time.time()
                    for rt in related:
                        res = "yes" if rt == mapped_winner_ticker else "no"
                        market_results[rt] = res
                        market_history.record_outcome(rt, res, resolved_at=now_ts)
                    bump_generation()
        # Reapply already-known winners from cache (no new API calls) so a
        # tick that skipped re-polling a resolved event still sees a
        # complete market_results, matching pre-caching per-tick behavior.
        for et in event_tickers:
            entry = cache.get(et)
            if entry and entry["winner_found"] and et not in to_poll:
                for rt in entry["related"]:
                    market_results[rt] = "yes" if rt == entry["mapped_winner_ticker"] else "no"
    except Exception:
        pass
    return market_results


_SERIES_CACHE_TTL_SEC = 3600  # series (a recurring-event template - "Pro Basketball Game") don't
# change often enough to justify get_series_list's ~1s cost (12,500+ entries) every 15s poll tick


def _utcnow() -> datetime:
    """Thin, monkeypatchable wrapper (kalshi-category-data-completeness Task
    2) so tests can fix "now" for the scheduled_ts comparison below without
    patching the stdlib clock. Not time.time(): SeriesFeeChange.scheduled_ts
    (docs/kalshi/get-series-fee-changes.md:126-129) is `type: string,
    format: date-time` - an ISO-8601 timestamp - not an epoch number, so the
    comparison needs a real datetime, not a float."""
    return datetime.now(timezone.utc)


async def _get_series_cache(client: KalshiPublicGateway) -> list[dict]:
    """All series with nonzero lifetime volume (~9,400 of Kalshi's ~12,500
    total, as of 2026-08-08), sorted by volume_fp descending, cached in
    state["series_cache"] and refreshed at most once per
    _SERIES_CACHE_TTL_SEC. See get_series_list's docstring for why this
    (series-level volume, then query real series directly) replaced
    browsing individual markets - a flat browse can be 100% dead combo
    markets even across tens of thousands of entries, confirmed directly,
    repeatedly. Shared by both the automatic watchlist (_get_top_series,
    just the top N) and market search (search_markets, which also needs
    the long tail to text-match against).

    Also folds in get_series_fee_changes' bulk call (kalshi-category-
    data-completeness Task 2) on every refresh: each series' raw fee_type/
    fee_multiplier (as returned by get_series_list) is only the fee it
    launched with, not necessarily its CURRENT fee if Kalshi has since
    scheduled a change - get-series-fee-changes.md's own SeriesFeeChange
    entries are the authoritative log of those changes. For each
    series_ticker, the most recently scheduled entry whose scheduled_ts is
    already <= now (ties broken by id, spec Sec1.8) overwrites that
    series' fee_type/fee_multiplier in place before series_cache.save() so
    Task 1's _series_metadata_row() persists the resolved current fee, not
    the stale launch-time one. A ticker absent from the fee-changes array
    never had a scheduled change (confirmed via changelog-index.md's
    2025-09-21 entry - see get_series_fee_changes' own docstring) and keeps
    whatever fee_type/fee_multiplier its raw Series object already
    carried."""
    cache = state["series_cache"]
    if time.time() - cache["fetched_at"] > _SERIES_CACHE_TTL_SEC or not cache["series"]:
        series = await client.get_series_list()
        series = [s for s in series if float(s.get("volume_fp") or 0) > 0]
        series.sort(key=lambda s: float(s.get("volume_fp") or 0), reverse=True)

        fee_changes = await client.get_series_fee_changes()
        now = _utcnow()
        effective_fee: dict[str, tuple[str, float, datetime, object]] = {}
        for fc in fee_changes:
            ticker = fc.get("series_ticker")
            if not ticker:
                continue
            scheduled_ts = fc.get("scheduled_ts")
            if not scheduled_ts:
                continue
            scheduled_at = datetime.fromisoformat(scheduled_ts.replace("Z", "+00:00"))
            if scheduled_at > now:
                continue  # not yet in effect
            fc_id = fc.get("id") or ""  # tie-break key only (spec Sec1.8) - a
            # missing id shouldn't crash the whole refresh on a
            # same-scheduled_ts tie (str/None aren't mutually orderable in a
            # tuple comparison); "" is a safe stand-in since which side wins
            # a tie is arbitrary either way, per the spec's own admission.
            current = effective_fee.get(ticker)
            if current is None or (scheduled_at, fc_id) >= (current[2], current[3]):
                effective_fee[ticker] = (fc.get("fee_type"), fc.get("fee_multiplier"), scheduled_at, fc_id)

        for s in series:
            resolved = effective_fee.get(s.get("ticker"))
            if resolved is not None:
                s["fee_type"], s["fee_multiplier"] = resolved[0], resolved[1]

        cache["series"] = series
        cache["fetched_at"] = time.time()
        series_cache.save(cache["fetched_at"], cache["series"])
    return cache["series"]


async def _get_top_series(client: KalshiPublicGateway, categories: list[str] | None = None, top_n_per_category: int = 12) -> list[str]:
    """Per-category discovery (2026-08-15 direct fix, real live report:
    "i see absolutely no signal or trade activity related to any markets
    other than sports or crypto... mentions... politics"). A flat global
    top-N by lifetime volume (the old behavior) systematically starves any
    category whose lifetime volume is small relative to Sports/Crypto's -
    confirmed live: Sports alone held 32 of the old top 40 series by
    lifetime volume, with Mentions/most of Politics/Entertainment/Climate
    holding zero. No amount of re-ranking *within* that narrow top-40 (see
    services/market_events/event_lifecycle.py's phase_ranked) can fix a
    category that was never even considered in the first place.

    categories: the same kalshi.categories list this app's own category
    filter already scopes to - top_n_per_category series from EACH one,
    guaranteeing every enabled category gets real, bounded coverage
    instead of zero. Total series considered is bounded by
    len(categories) * top_n_per_category (default config: 6 * 12 = 72), a
    moderate, predictable increase from the old flat 40 - deliberately NOT
    "query all ~9,757 volume-positive series every tick," which would mean
    ~9,757 concurrent API calls per 5s tick and risks recreating the exact
    shape of a real, already-documented incident (status.html phase 97 - a
    removed cap on trade-tape processing froze the app for several
    minutes). None/empty categories falls back to the old flat top-N
    behavior (top_n_per_category * 6, matching the default category
    count) rather than returning nothing."""
    series = await _get_series_cache(client)
    if not categories:
        return [s["ticker"] for s in series[:top_n_per_category * 6]]
    by_category: dict[str, list[str]] = {}
    for s in series:
        cat = s.get("category")
        if cat in categories:
            by_category.setdefault(cat, []).append(s["ticker"])
    result: list[str] = []
    for cat in categories:
        result.extend(by_category.get(cat, [])[:top_n_per_category])
    return result


# Series scanned per background batch to build services/market_catalog/market_catalog.py's
# near-term catalog. Cut from 40 to 10 (2026-08-15 tick_duration
# investigation) - confirmed live as the real remaining root cause after
# fixing three other uncached/uncapped call sites (propagate_
# milestone_winners, _fetch_event_live_data, _fetch_live_status's own
# per-tick cap) didn't meaningfully move tick_duration: this batch runs as
# an independent background task (_maybe_scan_catalog_batch), so it never
# blocks the tick's own await chain, but it draws from the exact same
# shared Kalshi rate limiter (services/http_client.py) the tick's own
# latency-sensitive reads need - backgrounding via asyncio.create_task
# decouples CONTROL FLOW, not RESOURCE CONTENTION. At 40 series/batch, with
# _CATALOG_SCAN_MIN_INTERVAL_SEC's overlap guard meaning a new batch starts
# again the moment the previous one finishes draining, this ran back-to-
# back continuously, at times consuming close to the entire 3.0 tokens/sec
# read budget by itself - starving _fetch_account_snapshot (measured
# stalling to 19-33s on affected ticks despite its own working 20s cache)
# and everything else sharing the bucket. Direct priority ordering already
# established this session ("maximum efficiency and maximum speed for
# position management and whale watching") argues for catalog-scan - a
# bulk, non-urgent backlog-clearing task, same category as signal-
# resolution's own already-throttled limit=10 batch - yielding budget to
# the tick-critical path, not competing with it head-on. Slower to fully
# re-cycle through every configured-category series as a result, but the
# catalog was already substantially warm before this change (thousands of
# series/markets from an earlier scanning period).
_CATALOG_SCAN_BATCH_SIZE = 10

# Root-cause report C3 / realtime data-plane remediation plan P1 Task 9:
# an unbounded asyncio.gather() of up to _CATALOG_SCAN_BATCH_SIZE
# concurrent get_markets calls, all in the same background REST caller
# class, delayed the tick-critical position/account fetch behind it in
# the shared token bucket. PACE_LIMIT bounds how many of this batch's own
# calls run concurrently - independent of _CATALOG_SCAN_BATCH_SIZE, which
# stays the same (still scans the same series per tick, just not all at
# once).
PACE_LIMIT = 4

# Deliberately NOT a module-level asyncio.Semaphore(PACE_LIMIT) singleton
# (code-review fix, finding #8 - /code-review high pass against PR #23,
# confirmed real and reproduced directly, not just plausible: two
# sequential asyncio.run() calls in the same process, each contending the
# same module-level Semaphore instance past PACE_LIMIT, deterministically
# raised "RuntimeError: ... is bound to a different event loop" on the
# second call - asyncio.Semaphore only binds to the running loop lazily,
# the first time acquire() actually has to wait, via its
# _LoopBoundMixin._get_loop(); an uncontended acquire() never touches it,
# which is exactly why the two pre-existing tests that happened to
# exercise this coexisted safely by accident - only one of them was ever
# contended enough to trigger the bind). Confirmed low risk in real
# production use (main.py's own _maybe_scan_catalog_batch already guards
# against overlapping batches via state["catalog_scan"]["scanning"], and
# uvicorn runs one event loop for the whole process lifetime - no
# cross-loop reuse ever happens there), but a genuine test-isolation
# landmine: any future test exercising this with >= PACE_LIMIT concurrent
# series, run in the same pytest worker process after another such test,
# would crash non-deterministically depending on xdist's scheduling that
# day. Fixed with the standard pattern for this class of bug - a fresh
# Semaphore per call, scoped to the one batch that's ever in flight at a
# time in production anyway - rather than a lazy-rebind-per-loop
# workaround, since there is no cross-invocation pacing to preserve here.
async def _paced_get_markets(client: KalshiPublicGateway, pace_sem: asyncio.Semaphore, **kwargs):
    async with pace_sem:
        return await client.get_markets(**kwargs)


async def _scan_catalog_batch(client: KalshiPublicGateway, cfg: dict):
    """Incrementally builds market_catalog's near-term market catalog, a
    bounded batch (least-recently-scanned series first, see market_catalog.
    next_series_to_scan) per call - see market_catalog.py's own module
    docstring for the full "why": volume-ranking the top 40 series
    systematically misses markets that are live right now but sit in a
    lower-volume series, confirmed directly against real Kalshi data (found
    ~0 of the real live markets the user could see on Kalshi's own site).

    No longer gated behind kalshi.live_markets_only (2026-08-15, direct
    "no stone unturned" API audit) - this catalog is now the primary
    source for the DEFAULT (non-live-only) discovery path too (see
    market_catalog.open_candidates/_refresh_discovery_cache), not just the
    live-only one, so it needs to stay warm regardless of that flag.
    Scoped to cfg["kalshi"]["categories"] rather than every one of
    Kalshi's ~9,400 series - this app only ever trades within its
    configured categories, so scanning outside them would be pure waste,
    exactly what this pass exists to eliminate. No categories configured
    falls back to scanning everything (matches _get_top_series' own
    no-categories fallback)."""
    all_series = await _get_series_cache(client)
    categories = cfg["kalshi"].get("categories")
    if categories:
        all_series = [s for s in all_series if s.get("category") in categories]
    batch = market_catalog.next_series_to_scan(all_series, _CATALOG_SCAN_BATCH_SIZE)
    if not batch:
        return
    # Fresh per call (finding #8 - see _paced_get_markets' own docstring):
    # asyncio.Semaphore binds lazily to whatever loop is running when it's
    # first actually contended, so a module-level singleton reused across
    # separate asyncio.run() calls (each its own loop) can raise once
    # contended a second time. Constructing it here, scoped to this one
    # batch, sidesteps that entirely - there is only ever one batch in
    # flight at a time in production anyway (_maybe_scan_catalog_batch's
    # own state["catalog_scan"]["scanning"] guard).
    pace_sem = asyncio.Semaphore(PACE_LIMIT)
    results = await asyncio.gather(
        *(
            _paced_get_markets(client, pace_sem, limit=100, status="open", series_ticker=s["ticker"])
            for s in batch
        ),
        return_exceptions=True,
    )
    now = time.time()
    # Data-robustness audit finding (2026-08-10): mark_scanned() used to be
    # called for the WHOLE batch unconditionally, regardless of whether each
    # series' fetch actually succeeded - return_exceptions=True above
    # swallows a failure with no logging at all, so a persistently-failing
    # series (rate limit, malformed/renamed ticker, transient API error)
    # would mark itself "freshly scanned" every ~59-minute rotation forever,
    # looking identical to a series that's simply quiet, while never
    # actually writing a row. Only the series that genuinely succeeded this
    # batch get marked scanned; a failing one stays at the front of the
    # least-recently-scanned queue and gets retried next tick instead of
    # silently going stale for good.
    succeeded = []
    for s, result in zip(batch, results):
        if isinstance(result, list):
            market_catalog.upsert_markets(s["ticker"], s.get("category"), result, updated_at=now)
            succeeded.append(s["ticker"])
        else:
            # No logging framework exists anywhere in this app yet (audit
            # finding) - stdout is captured by `ddev logs -s fastapi` per
            # this project's own documented workflow, so a failing series is
            # at least visible there instead of vanishing with zero trace.
            print(f"[market_catalog] scan failed for {s['ticker']!r}, will retry next tick: {result!r}")
    if succeeded:
        market_catalog.mark_scanned(succeeded, scanned_at=now)


_CATALOG_SCAN_MIN_INTERVAL_SEC = 15  # how often a new background batch may be KICKED OFF - the
# shared token-bucket rate limiter (services/http_client.py), not this interval, is what actually
# keeps the real aggregate call rate safe; this just avoids spawning pointless overlapping tasks.


def _maybe_scan_catalog_batch(cfg: dict) -> None:
    """Triggers _scan_catalog_batch as an independent background task on
    its own steady interval, decoupled from the main tick entirely - same
    pattern (and same 2026-08-15 "no stone unturned" API audit) as
    discovery's own _maybe_refresh_discovery_cache. Synchronous/non-
    blocking on purpose, exactly like that sibling function."""
    catalog_state = state["catalog_scan"]
    now_ts = time.time()
    due = now_ts - catalog_state["last_started_at"] > _CATALOG_SCAN_MIN_INTERVAL_SEC
    if due and not catalog_state["scanning"]:
        catalog_state["scanning"] = True
        catalog_state["last_started_at"] = now_ts
        catalog_state["task"] = task_supervisor.supervise(
            lambda: _scan_catalog_batch_background(cfg),
            component="market_catalog", operation="scan_batch",
        )


@http_client.classify("background_catalog")
async def _scan_catalog_batch_background(cfg: dict) -> None:
    """Owns its own KalshiPublicGateway (not the calling tick's, which closes at
    the end of that same tick - see discovery_cache._refresh_discovery_cache's
    identical reasoning) and delegates the real work to _scan_catalog_batch
    unchanged, so its existing behavior/tests keep working when called
    directly with an explicit client. Exceptions are caught and recorded
    by task_supervisor.supervise (the caller) - this only needs its own
    finally to release the "scanning" flag and close the client regardless
    of outcome."""
    catalog_state = state["catalog_scan"]
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _scan_catalog_batch(client, cfg)
    finally:
        catalog_state["scanning"] = False
        await client.close()
