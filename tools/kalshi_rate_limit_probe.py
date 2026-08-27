"""Kalshi REST demand, endpoint-cost and batching probe (realtime data-plane
investigation task I8, docs/superpowers/plans/2026-08-25-realtime-data-
plane-investigation.md).

Read-only, manual, bounded. Four independent probes, each of which degrades
to an explicit "unavailable" rather than a guess:

- ``limits``  - GET /account/limits (docs/kalshi/get-account-api-limits.md):
  the connected account's usage tier and read/write token buckets.
- ``costs``   - GET /account/endpoint_costs (docs/kalshi/list-non-default-
  endpoint-costs.md): the default token cost and every endpoint priced
  differently, matched against the endpoints this app actually calls.
- ``batch``   - one GET /markets?tickers=... request per chunk size
  (docs/kalshi/get-markets.md documents ``tickers`` as a comma-separated
  filter with no per-call cap; the page ``limit`` maximum is 1000), timed
  through the shared limiter/telemetry stack so completeness, wall time,
  limiter wait, network time and 429s are reported per size.
- ``demand``  - caller-class and endpoint-family shares of REST demand
  over a window of persisted observability samples (kalshi_rest_class.* /
  kalshi_rest_endpoint.* from data/observability.db), plus a milestone/live-data
  duplicate-demand estimate: observed milestone-family calls per tracked
  event versus the minimum the repoll intervals require.
- ``anonymous-ceiling`` (realtime data-plane remediation P0 Task 4) - ramps
  request rate against a raw, UNAUTHENTICATED SDK client (bypassing this
  app's own local rate limiter entirely) until Kalshi returns a 429,
  reporting where the real server-side anonymous ceiling sits. Not part of
  ``--all`` - deliberately a separate, explicit, slower invocation, since
  it pushes real load against the exchange until it gets rate-limited.

Usage:
    python -m tools.kalshi_rate_limit_probe --all --json
    python -m tools.kalshi_rate_limit_probe --batch --sizes 50,100,200
    python -m tools.kalshi_rate_limit_probe --anonymous-ceiling
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Iterable

# Endpoint paths this app calls (services/kalshi/public.py + account.py),
# in the shape /account/endpoint_costs reports (method + path).
APP_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/markets"), ("GET", "/markets/{ticker}"), ("GET", "/markets/trades"),
    ("GET", "/markets/{ticker}/orderbook"), ("GET", "/series/{series_ticker}/markets/{ticker}/candlesticks"),
    ("GET", "/series"), ("GET", "/events"), ("GET", "/events/{event_ticker}"),
    ("GET", "/milestones"), ("GET", "/live_data/milestone/{milestone_id}"), ("GET", "/live_data/milestones"),
    ("GET", "/events/{event_ticker}/live_data"), ("GET", "/exchange/status"),
    ("GET", "/search/tags_by_categories"), ("GET", "/search/filters_by_sport"),
    ("GET", "/portfolio/balance"), ("GET", "/portfolio/positions"), ("GET", "/portfolio/fills"),
    ("GET", "/portfolio/orders"), ("POST", "/portfolio/orders"), ("DELETE", "/portfolio/orders/{order_id}"),
)

# Endpoint-family metric names (kalshi_rest_endpoint.<family>.calls) that hit the
# milestone / live-data surface, and the app's own repoll intervals for
# them (services/market_watch/catalog_scan.py _MILESTONE_REPOLL_SEC,
# live_status.py _LIVE_STATUS_REPOLL_SEC, event_metadata.py
# _EVENT_LIVE_DATA_REPOLL_SEC).
MILESTONE_FAMILIES: dict[str, int] = {"get_milestones": 60, "get_live_datas": 60, "get_live_data": 60,
                                      "get_event_live_data": 60}

DEFAULT_BATCH_SIZES = (50, 100, 200)
BATCH_PAUSE_SEC = 3.0  # let the local bucket refill between sizes so each is measured from a full pool


# --- limits / costs ------------------------------------------------------------

async def probe_limits(account) -> dict:
    if account is None:
        return {"available": False, "reason": "no authenticated account gateway (credentials not configured)"}
    try:
        raw = await account.get_api_limits()
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    read, write = raw.get("read") or {}, raw.get("write") or {}
    return {
        "available": True,
        "usage_tier": raw.get("usage_tier"),
        "read": {"refill_rate": read.get("refill_rate"), "bucket_capacity": read.get("bucket_capacity")},
        "write": {"refill_rate": write.get("refill_rate"), "bucket_capacity": write.get("bucket_capacity")},
        "grants": raw.get("grants") or [],
    }


async def probe_costs(account) -> dict:
    if account is None:
        return {"available": False, "reason": "no authenticated account gateway (credentials not configured)"}
    try:
        raw = await account.get_endpoint_costs()
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return summarize_costs(raw)


def summarize_costs(raw: dict) -> dict:
    """Pure: match the non-default list against APP_ENDPOINTS."""
    default_cost = raw.get("default_cost")
    non_default = [(e.get("method", "").upper(), e.get("path", ""), e.get("cost")) for e in (raw.get("endpoint_costs") or [])]
    priced = {(m, p): c for m, p, c in non_default}
    app = [{"method": m, "path": p, "cost": priced.get((m, p), default_cost),
            "non_default": (m, p) in priced} for m, p in APP_ENDPOINTS]
    return {
        "available": True,
        "default_cost": default_cost,
        "non_default_count": len(non_default),
        "app_endpoints": app,
        "app_endpoints_non_default": [a for a in app if a["non_default"]],
    }


def requests_per_second(limits: dict, cost: int | None) -> dict | None:
    """Pure: sustained and burst request rates implied by a bucket and cost."""
    if not limits.get("available") or not cost:
        return None
    read = limits["read"]
    if not read.get("refill_rate") or not read.get("bucket_capacity"):
        return None
    return {
        "sustained_per_sec": read["refill_rate"] / cost,
        "burst_requests": read["bucket_capacity"] // cost,
        "burst_seconds": read["bucket_capacity"] / read["refill_rate"],
    }


# --- batching ------------------------------------------------------------------

async def probe_batch_sizes(public, tickers: list[str], sizes: Iterable[int] = DEFAULT_BATCH_SIZES,
                            pause_sec: float = BATCH_PAUSE_SEC, sleep=asyncio.sleep, snapshot=None) -> list[dict]:
    """One request per size (get_markets_by_tickers with batch_size=size and
    exactly `size` tickers). `snapshot` is http_client.rest_latency_snapshot
    (injected for tests); its 'interactive' class deltas attribute limiter
    wait / network / 429s to each request."""
    from services import http_client
    snapshot = snapshot or http_client.rest_latency_snapshot
    results = []
    for i, size in enumerate(sizes):
        chunk = tickers[:size]
        before = _interactive_stats(snapshot())
        started = time.monotonic()
        row: dict = {"size": size, "requested": len(chunk), "short": len(chunk) < size}
        try:
            with http_client.caller_class("interactive"):
                fetched = await public.get_markets_by_tickers(chunk, batch_size=size)
            row["returned"] = len(fetched)
            row["completeness"] = (len(fetched) / len(chunk)) if chunk else None
            row["error"] = None
        except Exception as exc:
            row["returned"] = 0
            row["completeness"] = None
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["wall_ms"] = round((time.monotonic() - started) * 1000.0, 1)
        row.update(_delta(before, _interactive_stats(snapshot())))
        results.append(row)
        if i < len(list(sizes)) - 1 and pause_sec:
            await sleep(pause_sec)
    return results


def _interactive_stats(snap: dict) -> dict:
    s = (snap.get("by_class") or {}).get("interactive") or {}
    def total(comp):
        life = (s.get(comp) or {}).get("lifetime") or {}
        return (life.get("avg_ms") or 0.0) * (life.get("count") or 0)
    return {"attempts": s.get("attempts", 0), "rate_limited": s.get("rate_limited", 0),
            "limiter_wait_ms": total("limiter_wait"), "network_ms": total("network"), "backoff_ms": total("backoff")}


def _delta(before: dict, after: dict) -> dict:
    return {k: round(after[k] - before[k], 1) if isinstance(after[k], float) else after[k] - before[k] for k in before}


# --- anonymous REST ceiling (realtime data-plane remediation P0 Task 4) --------

_ANONYMOUS_CEILING_RAMP_FACTOR = 1.5
_ANONYMOUS_CEILING_WINDOW_SEC = 2.0
_ANONYMOUS_CEILING_START_RPS = 2.0


async def probe_anonymous_ceiling(
    client, *, duration_sec: float = 20.0, ticker: str = "KXBTCD-25AUG25-T1",
    window_sec: float = _ANONYMOUS_CEILING_WINDOW_SEC,
) -> dict:
    """Ramps request rate against an UNAUTHENTICATED market-data client
    until Kalshi returns a 429 or duration_sec elapses, to find the real
    server-side ceiling for anonymous reads - not this app's own locally
    configured rate limit. `client` must therefore be the raw SDK client
    (services.kalshi.transport.build_public_client), never
    KalshiPublicGateway: KalshiPublicGateway.get_market goes through
    call_with_backoff and this app's own token bucket, which would just
    measure our own configured rate back at us instead of Kalshi's.

    Ramps geometrically (x1.5 every window_sec, default 2s) starting from a
    low rate so a single run doesn't open by hammering at an arbitrary high
    guess. A 429 is detected the same way call_with_backoff does (the
    SDK's own exception exposes .status == 429); any other exception
    propagates - this probe's job is to characterize rate-limit behavior,
    not to silently swallow a real connectivity failure as "no ceiling
    found". `window_sec` is injectable (like probe_batch_sizes' `sleep`)
    so a test can ramp through many windows in well under a second instead
    of waiting out the real 2s default."""
    rps = _ANONYMOUS_CEILING_START_RPS
    first_429_at: float | None = None
    observed_max = 0.0
    deadline = time.monotonic() + duration_sec
    while time.monotonic() < deadline and first_429_at is None:
        interval = 1.0 / rps
        window_end = time.monotonic() + window_sec
        try:
            while time.monotonic() < window_end:
                await client.get_market(ticker)
                await asyncio.sleep(interval)
            observed_max = max(observed_max, rps)
            rps *= _ANONYMOUS_CEILING_RAMP_FACTOR
        except Exception as exc:
            if getattr(exc, "status", None) == 429:
                first_429_at = rps
            else:
                raise
    return {"observed_max_rps": observed_max, "first_429_at_rps": first_429_at, "sample_ticker": ticker}


# --- demand shares from observability --------------------------------------------

def demand_shares(summary: dict, hours: float) -> dict:
    """Pure: from observability.summary(hours) rows (per-metric count/avg of
    window samples), total calls per caller class and per endpoint family,
    with shares and rates. A window sample's value is that window's count,
    so the sum over samples is avg * count."""
    def total(metric):
        m = summary.get(metric)
        return (m["avg"] or 0.0) * m["count"] if m and m.get("avg") is not None else 0.0
    classes = {k.split(".")[1]: total(k) for k in summary if k.startswith("kalshi_rest_class.") and k.endswith(".calls")}
    endpoints = {k.split(".")[1]: total(k) for k in summary if k.startswith("kalshi_rest_endpoint.") and k.endswith(".calls")}
    rate_limited = {k.split(".")[1]: total(k) for k in summary if k.startswith("kalshi_rest_class.") and k.endswith(".rate_limited")}
    tot_c = sum(classes.values()) or 1.0
    seconds = hours * 3600.0
    return {
        "hours": hours,
        "by_class": {c: {"calls": round(n), "share": round(n / tot_c, 4), "per_sec": round(n / seconds, 4),
                         "rate_limited": round(rate_limited.get(c, 0.0))}
                     for c, n in sorted(classes.items(), key=lambda kv: (-kv[1], kv[0]))},
        "by_endpoint": {e: {"calls": round(n), "per_sec": round(n / seconds, 4)}
                        for e, n in sorted(endpoints.items(), key=lambda kv: (-kv[1], kv[0]))},
        "critical_share": round(sum(v for c, v in classes.items() if c.startswith("critical_")) / tot_c, 4),
        "background_share": round(sum(v for c, v in classes.items() if c.startswith("background_")) / tot_c, 4),
        "total_calls": round(tot_c),
    }


def milestone_duplicate_estimate(by_endpoint: dict, tracked_events: int, hours: float) -> dict:
    """Pure upper-bound estimate: observed milestone/live-data-family calls
    versus the minimum the app's own repoll intervals would need for the
    tracked events. A factor above ~1 means the same milestone/event is
    being polled by more than one subsystem (catalog scan, live status,
    event live data each keep their own cache)."""
    observed = {f: by_endpoint.get(f, {}).get("calls", 0) for f in MILESTONE_FAMILIES}
    total_observed = sum(observed.values())
    if not tracked_events or not hours:
        return {"observed_calls": observed, "total_observed": total_observed, "minimum_required": None, "duplicate_factor": None}
    minimum = tracked_events * (hours * 3600.0 / min(MILESTONE_FAMILIES.values()))
    return {
        "observed_calls": observed, "total_observed": total_observed,
        "tracked_events": tracked_events, "minimum_required": round(minimum),
        "duplicate_factor": round(total_observed / minimum, 3) if minimum else None,
    }


# --- CLI -------------------------------------------------------------------------

async def _run(args) -> dict:
    from dotenv import load_dotenv
    from services.config.config_store import config_store
    from services.kalshi.account_client import KalshiAccountClient
    from services.kalshi.public import KalshiPublicGateway
    load_dotenv()  # same optional .env the app reads (main.py) - credentials are never required
    cfg = config_store.get()
    public = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"].get("request_timeout_sec", 10))
    # trading_enabled=False by construction: this probe is read-only and must
    # be structurally unable to reach the order gateway's write path. Both
    # gateways are closed in this function's finally whatever happens.
    account = KalshiAccountClient(
        os.environ.get("KALSHI_ACCOUNT_BASE_URL", "").strip() or cfg["kalshi"]["base_url"],
        cfg["kalshi"].get("request_timeout_sec", 10), trading_enabled=False,
    )
    authenticated = account if getattr(account, "enabled", False) else None
    out: dict = {"generated_at": time.time(), "account_authenticated": authenticated is not None}
    try:
        if args.all or args.limits:
            out["limits"] = await probe_limits(authenticated)
        if args.all or args.costs:
            out["costs"] = await probe_costs(authenticated)
            if "limits" in out and out["costs"].get("available"):
                out["implied_read_rates"] = requests_per_second(out["limits"], out["costs"].get("default_cost"))
        if args.all or args.batch:
            tickers = await _sample_tickers(public, max(args.sizes))
            out["batch"] = await probe_batch_sizes(public, tickers, sizes=args.sizes)
        if args.all or args.demand:
            from services.observability import observability
            summary = observability.summary(args.demand_hours)
            shares = demand_shares(summary, args.demand_hours)
            out["demand"] = shares
            out["milestone_duplicates"] = milestone_duplicate_estimate(shares["by_endpoint"], args.tracked_events, args.demand_hours)
        if args.anonymous_ceiling:
            # Deliberately NOT KalshiPublicGateway (see probe_anonymous_ceiling's
            # own docstring) - a fresh raw SDK client, unauthenticated, closed
            # in this block only (public/account above stay closed in finally).
            # A real, currently-open ticker (via the already-open `public`
            # gateway) rather than a hardcoded guess - a settled/expired
            # ticker 404s instead of exercising the rate limiter at all.
            from services.kalshi import transport
            sample = await _sample_tickers(public, 1)
            anon_client = transport.build_public_client(cfg["kalshi"]["base_url"])
            try:
                kwargs = {"duration_sec": args.anonymous_ceiling_duration_sec}
                if sample:
                    kwargs["ticker"] = sample[0]
                out["anonymous_ceiling"] = await probe_anonymous_ceiling(anon_client, **kwargs)
            finally:
                await anon_client.close()
    finally:
        await public.close()
        await account.close()
    return out


async def _sample_tickers(public, n: int) -> list[str]:
    """Real open market tickers for the batch probe: one paged GET /markets
    (limit=1000 max per docs) is enough for 200."""
    markets = await public.get_markets(limit=min(1000, max(n, 1)), status="open")
    return [m["ticker"] for m in markets if m.get("ticker")][:n]


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m tools.kalshi_rate_limit_probe")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limits", action="store_true")
    parser.add_argument("--costs", action="store_true")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--demand", action="store_true")
    parser.add_argument("--anonymous-ceiling", action="store_true")
    parser.add_argument("--sizes", default=",".join(str(s) for s in DEFAULT_BATCH_SIZES))
    parser.add_argument("--demand-hours", type=float, default=1.0)
    parser.add_argument("--tracked-events", type=int, default=0)
    parser.add_argument("--anonymous-ceiling-duration-sec", type=float, default=20.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    args.sizes = tuple(int(s) for s in args.sizes.split(",") if s)
    if not (args.all or args.limits or args.costs or args.batch or args.demand or args.anonymous_ceiling):
        parser.error("choose --all or at least one of --limits/--costs/--batch/--demand/--anonymous-ceiling")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out = asyncio.run(_run(args))
    print(json.dumps(out, indent=1, default=str) if args.json else json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
