"""
One shared, connection-pooled httpx.AsyncClient for every outbound HTTP call
in the app (Kalshi market data, Kalshi account, whale-watcher feeds, Google
OAuth). Every caller used to open its own `async with httpx.AsyncClient(...)`
per request, paying a full TCP+TLS handshake each time even when hitting the
same host seconds apart. One long-lived client reuses keep-alive connections
across calls instead.
"""
import asyncio
import contextvars
import functools
import random
import time
from contextlib import contextmanager

import httpx

from services.latency_agg import LatencyAgg

_client: httpx.AsyncClient | None = None

# Clock indirection so tests/test_http_client.py can drive the latency
# decomposition deterministically without patching time.monotonic itself
# (asyncio's own loop clock reads that).
_monotonic = time.monotonic

# --- caller classes (realtime data-plane investigation, I5) ------------------
#
# Every REST call is attributed to one bounded caller class so the shared
# read bucket's contention can be decomposed by WHO is spending it - the
# I0 baseline (docs/superpowers/research/2026-08-25-realtime-data-plane-
# baseline.md section 3) enumerates the callers; this is that taxonomy made
# measurable. Set with the caller_class() context manager or the classify()
# decorator; propagates into asyncio.gather()/create_task() children because
# tasks copy the current context at creation. Unset callers land in "other".
CALLER_CLASSES: tuple[str, ...] = (
    "critical_whale",           # whale-candidate market enrichment on the stream hot path
    "critical_position",        # open-position pricing, account snapshot, exchange status
    "interactive",              # dashboard/diagnostics routes a human is waiting on
    "background_discovery",     # discovery-cache refresh
    "background_catalog",       # catalog scan, event titles, category metadata
    "background_live_status",   # milestones/live data/event live data polling
    "background_resolution",    # signal/outcome resolution, event-schedule resolver
    "background_index_backfill",  # CF Benchmarks REST passthrough gap-backfill (issue #260) -
    # rare (only after an index_stream reconnect) but a 5x-cost outlier
    # (50 tokens/request vs this app's usual 10, docs/kalshi/
    # rest-passthrough.md's "Rate limit" section) - its own class so that
    # cost is visible on its own rather than folded into an unrelated
    # background bucket.
    "other",
)
_caller_class_var: contextvars.ContextVar[str] = contextvars.ContextVar("kalshi_rest_caller_class", default="other")


def current_caller_class() -> str:
    return _caller_class_var.get()


@contextmanager
def caller_class(name: str):
    if name not in CALLER_CLASSES:
        raise ValueError(f"unknown Kalshi REST caller class {name!r}; choose one of {CALLER_CLASSES}")
    token = _caller_class_var.set(name)
    try:
        yield
    finally:
        _caller_class_var.reset(token)


def classify(name: str):
    """Decorator form of caller_class() for whole async functions."""
    if name not in CALLER_CLASSES:
        raise ValueError(f"unknown Kalshi REST caller class {name!r}; choose one of {CALLER_CLASSES}")

    def decorate(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            token = _caller_class_var.set(name)
            try:
                return await fn(*args, **kwargs)
            finally:
                _caller_class_var.reset(token)
        return wrapper
    return decorate


class _RestClassStats:
    """Per-caller-class decomposition of every logical call_with_backoff
    call: limiter wait and network time per attempt, backoff sleep and total
    elapsed per logical call, plus counts. Window aggregates are reset by
    the observability sampler after each persisted sample (same ownership
    as the WebSocket ingest and whale-pipeline metrics); lifetime ones are
    monotone."""
    __slots__ = ("calls", "attempts", "rate_limited", "errors", "window_counts", "window", "lifetime")
    _COMPONENTS = ("limiter_wait", "network", "backoff", "total")
    _COUNTS = ("calls", "attempts", "rate_limited", "errors")

    def __init__(self) -> None:
        # Lifetime counts (monotone) and per-window counts (reset by the
        # observability sampler after each persisted sample, so persisted
        # samples are summable - I8 found summing samples of a lifetime
        # counter inflated demand ~30x).
        self.calls = 0
        self.attempts = 0
        self.rate_limited = 0
        self.errors = 0
        self.window_counts: dict[str, int] = dict.fromkeys(self._COUNTS, 0)
        self.window = {c: LatencyAgg() for c in self._COMPONENTS}
        self.lifetime = {c: LatencyAgg() for c in self._COMPONENTS}

    def count(self, name: str, n: int = 1) -> None:
        setattr(self, name, getattr(self, name) + n)
        self.window_counts[name] += n

    def add(self, component: str, seconds: float) -> None:
        self.window[component].add(seconds)
        self.lifetime[component].add(seconds)

    def reset_window(self) -> None:
        self.window_counts = dict.fromkeys(self._COUNTS, 0)
        self.window = {c: LatencyAgg() for c in self._COMPONENTS}

    def snapshot(self) -> dict:
        out: dict = {"calls": self.calls, "attempts": self.attempts,
                     "rate_limited": self.rate_limited, "errors": self.errors,
                     "window": dict(self.window_counts)}
        for c in self._COMPONENTS:
            out[c] = {"window": self.window[c].snapshot(1000.0, "ms"), "lifetime": self.lifetime[c].snapshot(1000.0, "ms")}
        return out


_rest_class_stats: dict[str, _RestClassStats] = {}
# Per-endpoint-family counts for the current observability window (I8):
# exact, summable per-minute demand by endpoint, unlike the per-tick
# _http_metrics snapshot main.py resets every tick. Bounded by the same
# endpoint-family label set as _http_metrics.
_endpoint_window: dict[str, dict[str, int]] = {}


def _class_stats(name: str) -> _RestClassStats:
    stats = _rest_class_stats.get(name)
    if stats is None:
        stats = _rest_class_stats[name] = _RestClassStats()
    return stats


def _limiter_gauges(limiter) -> dict:
    tokens = limiter.tokens_available() if hasattr(limiter, "tokens_available") else None
    return {
        "waiters": getattr(limiter, "waiters", 0),
        "waiters_high_water": getattr(limiter, "waiters_high_water", 0),
        "tokens": round(tokens, 3) if tokens is not None else None,
    }


def rest_latency_snapshot() -> dict:
    """Pure read (no resets): per-caller-class latency decomposition (with
    lifetime and per-window counts), per-endpoint-family window counts, and
    the two token buckets' waiter-depth gauges. Classes/endpoints never
    used are omitted, not reported as zeros."""
    return {
        "by_class": {name: stats.snapshot() for name, stats in _rest_class_stats.items()},
        "by_endpoint": {name: dict(counts) for name, counts in _endpoint_window.items()},
        "limiter": {"read": _limiter_gauges(_kalshi_read_limiter), "write": _limiter_gauges(_kalshi_write_limiter)},
    }


def reset_rest_latency_window() -> None:
    for stats in _rest_class_stats.values():
        stats.reset_window()
    _endpoint_window.clear()


def _count_endpoint_window(endpoint: str, *, rate_limited: bool, error: bool) -> None:
    counts = _endpoint_window.get(endpoint)
    if counts is None:
        counts = _endpoint_window[endpoint] = {"calls": 0, "rate_limited": 0, "errors": 0}
    counts["calls"] += 1
    if rate_limited:
        counts["rate_limited"] += 1
    if error:
        counts["errors"] += 1


class _TokenBucketRateLimiter:
    """A real throughput limiter, not a concurrency cap (2026-08-15, direct
    live incident - see the long comment below for the full story of why a
    concurrency Semaphore was tried first and confirmed NOT to work).
    Refills continuously at rate_per_sec, drains 1 "slot" per acquire() -
    when empty, callers wait exactly as long as it takes for the next slot
    to become available, no more. asyncio.Lock, not a semaphore's internal
    counter, because refill math (elapsed-time-based) needs to happen
    atomically with the drain check, and this app's trading loop is single-
    process/single-event-loop anyway (no cross-process contention to
    worry about)."""

    def __init__(self, rate_per_sec: float, burst: float | None = None):
        # burst (2026-08-15): the bucket's max saved-up size, separate from
        # its refill rate - defaults to rate_per_sec (1 second's worth,
        # the naive choice) but every real caller below passes a much
        # smaller explicit value. Confirmed live this distinction matters:
        # starting a fresh bucket already full at rate_per_sec permits an
        # instant burst of that many requests the moment a tick (or a
        # process restart, which happens often under uvicorn --reload)
        # begins, before the refill-throttling has any effect at all -
        # exactly when every cache is also cold and wants to fire the most
        # requests at once. A small burst cap means only the first couple
        # requests are ever "free"; everything after genuinely waits on
        # the sustained rate from the start.
        self._rate = rate_per_sec
        self._burst = burst if burst is not None else rate_per_sec
        self._tokens = self._burst
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        # Waiter-depth gauges (I5): how many callers are inside acquire()
        # right now (including the one holding the lock) and the most that
        # ever were. A high-water mark that climbs is the local-contention
        # signature that per-endpoint network latency can never show.
        self.waiters = 0
        self.waiters_high_water = 0

    def tokens_available(self) -> float:
        """Read-only refill-adjusted token estimate for diagnostics."""
        return min(self._burst, self._tokens + (time.monotonic() - self._last_refill) * self._rate)

    async def acquire(self):
        self.waiters += 1
        if self.waiters > self.waiters_high_water:
            self.waiters_high_water = self.waiters
        try:
            async with self._lock:
                while True:
                    now = time.monotonic()
                    self._tokens = min(self._burst, self._tokens + (now - self._last_refill) * self._rate)
                    self._last_refill = now
                    if self._tokens >= 1:
                        self._tokens -= 1
                        return
                    await asyncio.sleep((1 - self._tokens) / self._rate)
        finally:
            self.waiters -= 1


# Global throughput bound (2026-08-15, direct live incident: "why are there
# SO MANY unresolved signals" led to fixing services/signal_log.py's
# unresolved_batch head-of-line block, which then made
# main.py._check_signal_resolutions do real, sustained work for the first
# time - running concurrently with discovery's own fetch in the same
# asyncio.gather pushed real, live tick_duration/rate_limit_hits to
# ~40s/200+, repeatedly).
#
# First fix attempted here was a global asyncio.Semaphore bounding how many
# Kalshi calls could be in flight at once (mirroring the EARLIER, real,
# and still-valid fix this session: services/kalshi/public.py's own
# Semaphore(10) on get_candidate_markets's own internal fan-out) - dropping
# it all the way to Semaphore(5) still didn't fully stop the spikes.
# Confirmed why by actually reading Kalshi's rate-limit docs (docs.kalshi.
# com/getting_started/rate_limits, fetched live 2026-08-15, direct
# instruction to check docs/kalshi/ and the live docs site rather than keep
# guessing): it's a token-bucket THROUGHPUT system ("Every item in the
# batch is billed separately... The whole batch must fit in the bucket at
# once"), not concurrency-based at all - a concurrency cap bounds how many
# requests are in flight simultaneously but does nothing to slow the RATE
# new ones fire at once a slot frees up, so even Semaphore(5) still let
# requests fire far faster than the token bucket could refill.
#
# Two independent buckets, not one shared one (2026-08-15, direct
# instruction: "apply this knowledge universally to the application's
# current methods that use the API") - Kalshi's own docs describe read and
# write as genuinely separate budgets ("The split is by operation type...
# REST and FIX requests drain the same buckets" - i.e. same split, not
# shared with each other): Basic tier is 200 read-tokens/sec vs 100
# write-tokens/sec, most requests cost 10 tokens by default -> ~20 read
# req/sec vs ~10 write req/sec sustainable IF this app were authenticated
# at Basic tier. It isn't, for market data (only services/
# kalshi_account_client.py's real-trading path authenticates), so the real
# anonymous-access limit isn't in the documented tier table at all and had
# to be found empirically. 8 req/sec (this module's first real attempt,
# still confirmed live above the true anonymous ceiling: 60 rate-limit
# hits in a single tick even with it active) was still too fast - dropped
# further, with a small burst cap on top (see _TokenBucketRateLimiter's own
# burst param) so a cold restart's first requests can't spend a whole
# second's worth of budget in one instant. Deliberately erring slow rather
# than iterating rate numbers against Kalshi's real, live production API
# any further - this is a paper-trading POC with no latency requirement
# that justifies pushing a third party's rate limit to find its exact
# edge.
# Raised 2026-08-16 (API-doc audit finding B4, docs/kalshi/rate_limits.md) -
# re-fetched Kalshi's rate-limit docs fresh and called get_account_api_limits()
# / get_account_endpoint_costs() live against this app's own connected
# account: real confirmed Basic-tier budget is 200 read-tokens/sec (600-token,
# ~3s burst pool) at the documented flat default cost of 10 tokens/request for
# every endpoint this app calls - a real 20 read-req/sec sustainable rate, of
# which the old 3.0/sec (burst 2.0) used only ~15%.
#
# NOT raised all the way to that confirmed number, though, because it's an
# *authenticated-account* figure and this shared limiter's traffic is
# overwhelmingly *unauthenticated* market data (see the long comment above -
# only kalshi_account_client.py's calls authenticate; everything in
# kalshi_client.py, the bulk of real read volume, doesn't). Kalshi rate-limits
# unauthenticated traffic some other way (most likely per-IP) that was never
# empirically confirmed the way the account figure was - there's no live
# evidence the anonymous ceiling is the same 20/sec, only that it's plausibly
# in that neighborhood. 8.0/sec (burst 8.0) is a real, meaningful increase
# (2.7x the old rate) while keeping 2.5x headroom below the confirmed number
# even under the conservative assumption that anonymous traffic shares
# exactly the same ceiling as the authenticated account. Also, separately,
# the 2026-08-16 batching pass (get_events/get_live_datas/
# get_markets_by_tickers replacing what used to be N individual calls at
# several real call sites) already cut the *number* of requests this app
# makes per tick well below what motivated the original 3.0/sec figure, so
# this isn't asking the same request volume to move faster - it's a smaller
# request volume with more headroom per request.
#
# Write limiter deliberately left untouched: create_order/cancel_order are
# always authenticated (is_write=True only used in kalshi_account_client.py),
# so the confirmed 100 write-tokens/sec (~10 write-req/sec) figure applies
# without the anonymous-traffic ambiguity above - but there's no evidence
# write throughput is an actual bottleneck (this app places at most a
# handful of orders per tick, never a batch), and this is the single most
# real-money-adjacent number in the app. No concrete need to raise it, so it
# stays at its original, deliberately conservative value.
_KALSHI_READ_RATE_PER_SEC = 8.0
_KALSHI_WRITE_RATE_PER_SEC = 1.5
_KALSHI_READ_BURST = 8.0
_KALSHI_WRITE_BURST = 1.0
_kalshi_read_limiter = _TokenBucketRateLimiter(_KALSHI_READ_RATE_PER_SEC, burst=_KALSHI_READ_BURST)
_kalshi_write_limiter = _TokenBucketRateLimiter(_KALSHI_WRITE_RATE_PER_SEC, burst=_KALSHI_WRITE_BURST)

# Rolling rate-limit-hit visibility (2026-08-15, hardening-and-accuracy-
# roadmap-2026-08-11.md Part 3 Item 2 - "no tick-duration/rate-limit
# visibility," confirmed still open: real incident evidence exists, a
# genuine 502 was hit during the phase-97 cold-start incident, but nothing
# in `state` could show it coming next time). Single-threaded asyncio event
# loop, so a plain module-level int needs no lock. main.py's trading_loop
# reads and resets this once per tick via get_and_reset_rate_limit_hits() -
# a per-tick count, not an ever-growing lifetime total, so it stays a
# meaningful "is this happening right now" signal.
_rate_limit_hits_since_reset = 0


def get_and_reset_rate_limit_hits() -> int:
    global _rate_limit_hits_since_reset
    count = _rate_limit_hits_since_reset
    _rate_limit_hits_since_reset = 0
    return count


# Per-endpoint-family REST usage telemetry (2026-08-24, QCP Task 15 -
# docs/superpowers/plans/2026-08-24-quality-control-plane.md). Instrumented
# INSIDE call_with_backoff's own retry loop, not by wrapping
# call_with_backoff from the outside, so each real HTTP attempt (including
# every retried 429) is counted once - wrapping from outside would collapse
# N attempts into 1 "call" and undercount real request volume, exactly what
# this module's own docstring on call_with_backoff already warns readers not
# to double-count for the rate-limit-hits counter above.
#
# Bounded by construction, not by an eviction policy: keys are endpoint-
# family names (SDK method names like "get_markets", "create_order_v2" -
# see _default_endpoint_name below), a small fixed set (~25) determined by
# how many distinct Kalshi endpoints this app's clients call, never by
# per-request values like a ticker or query string.
_http_metrics: dict[str, dict] = {}


def _default_endpoint_name(coro_func) -> str:
    # The overwhelming majority of call_with_backoff's real call sites pass
    # a bound SDK method directly (self._client.get_markets, .create_order_v2,
    # ...) - its __name__ IS already the right low-cardinality endpoint-family
    # label, for free, with zero call-site changes. Only services/
    # kalshi_client.py's _get_json-routed calls (which all share one local
    # "do_get" closure name) need an explicit endpoint= override - see that
    # method's own call sites.
    return getattr(coro_func, "__name__", None) or "unknown"


def _record_http_attempt(endpoint: str, *, rate_limited: bool, success: bool, latency_ms: float | None) -> None:
    bucket = _http_metrics.setdefault(
        endpoint, {"calls": 0, "successes": 0, "errors": 0, "rate_limited": 0, "total_latency_ms": 0.0}
    )
    bucket["calls"] += 1
    if rate_limited:
        bucket["rate_limited"] += 1
    elif success:
        bucket["successes"] += 1
        bucket["total_latency_ms"] += latency_ms
    else:
        bucket["errors"] += 1


def http_metrics_snapshot(reset: bool = False) -> dict:
    """Per-endpoint-family counts/latency, keyed by endpoint (see
    _default_endpoint_name). avg_latency_ms is computed only over
    *successful* attempts - a 429 or a hard error still took real wall time,
    but folding those into the average would conflate "how long does a real
    round trip take" with "how long did we wait to get rate-limited," which
    isn't the question this number answers. None (not 0) when an endpoint
    has zero successes yet, matching this codebase's existing "unknown is
    better than fabricated" convention (see services/observability/
    observability.py's capture_from_runtime).

    reset=True atomically reads-and-clears, same semantics as this module's
    own get_and_reset_rate_limit_hits() above - main.py's trading_loop calls
    this with reset=True exactly once per tick (right next to that existing
    call) and stashes the result in state, so it becomes an already-computed,
    side-effect-free value by the time services/observability/
    observability.py's capture_from_runtime (a *pure* mapping, reused by both
    the periodic persisted sampler and the on-demand GET /api/observability/
    current route) reads it - capture_from_runtime itself must never call
    this with reset=True, or an incidental /current request would silently
    zero out the counters the periodic sampler was about to read."""
    snapshot = {}
    for endpoint, m in _http_metrics.items():
        avg_latency_ms = (m["total_latency_ms"] / m["successes"]) if m["successes"] else None
        snapshot[endpoint] = {
            "calls": m["calls"],
            "successes": m["successes"],
            "errors": m["errors"],
            "rate_limited": m["rate_limited"],
            "avg_latency_ms": avg_latency_ms,
        }
    if reset:
        _http_metrics.clear()
    return snapshot


# 2026-09-03, Task 8b of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md (moved there 2026-09-06, planning-lanes migration): pins this shared client's timeout/connection-pool ceiling
# explicitly rather than leaving it as an implicit, httpx-version-
# dependent default. Values are httpx 0.27.2's OWN measured defaults
# (docker exec ddev-kalshi-whale-poc-fastapi python3, confirmed live,
# 2026-09-03: Timeout(timeout=5.0), Limits(max_connections=100,
# max_keepalive_connections=20, keepalive_expiry=5.0)) - no bottleneck was
# measured at these values, so this changes no runtime behavior; it only
# stops a future httpx upgrade from silently changing this app's behavior
# by changing its own defaults out from under an implicit construction.
# max_connections/max_keepalive_connections are also an fd-budget concern
# now (this app's 2026-09-02 6.8h fd-exhaustion incident) - each open
# connection is a socket file descriptor, and this shared client serves
# every caller of get_client() (Kalshi public REST via KalshiPublicGateway,
# Google OAuth, event_schedule.py) through one pool.
_HTTP_CLIENT_TIMEOUT_SEC = 5.0
_HTTP_CLIENT_MAX_CONNECTIONS = 100
_HTTP_CLIENT_MAX_KEEPALIVE_CONNECTIONS = 20


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_CLIENT_TIMEOUT_SEC),
            limits=httpx.Limits(
                max_connections=_HTTP_CLIENT_MAX_CONNECTIONS,
                max_keepalive_connections=_HTTP_CLIENT_MAX_KEEPALIVE_CONNECTIONS,
            ),
        )
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def call_with_backoff(
    coro_func, *args, max_retries: int = 4, base_delay: float = 0.5, is_write: bool = False,
    endpoint: str | None = None, **kwargs
):
    """Kalshi's rate limiter returns 429 with no Retry-After header — their
    own docs (docs.kalshi.com/getting_started/rate_limits) say to apply
    exponential backoff on 429. Originally implemented as a raw-httpx request
    wrapper; rewritten 2026-08-08 to wrap an arbitrary async callable instead
    after migrating the Kalshi clients to the official SDK (kalshi_python_async) —
    the SDK's own retry support only covers 5xx/connection errors, not 429
    specifically, and there's no public way to add 429 to its retry list, so
    this still earns its place. Detects a 429 via the SDK's own exception
    shape (kalshi_python_async.exceptions.ApiException and subclasses all
    expose .status) rather than an HTTP response object. Only a 429-shaped
    exception triggers a retry; anything else propagates immediately,
    including on the final attempt.

    is_write (2026-08-15): routes to the write-operation token bucket
    instead of the read one - see the two _kalshi_*_limiter definitions
    above for why they're separate. Every caller of call_with_backoff is
    read by default; services/kalshi/orders.py's create_order/
    cancel_order are the only two real callers that pass is_write=True -
    grep for call_with_backoff before adding a new write-shaped call
    elsewhere and make sure it does too.

    endpoint (2026-08-24, QCP Task 15): the low-cardinality label REST
    telemetry (see http_metrics_snapshot above) is recorded under. Defaults
    to coro_func's own __name__, which is already correct for the common
    case (a bound SDK method); only overridden explicitly by services/
    kalshi_client.py's _get_json-routed calls."""
    global _rate_limit_hits_since_reset
    endpoint_name = endpoint or _default_endpoint_name(coro_func)
    limiter = _kalshi_write_limiter if is_write else _kalshi_read_limiter
    # Latency decomposition by caller class (I5): limiter wait and network
    # time per attempt, backoff sleep and total elapsed per logical call.
    # The pre-existing per-endpoint avg_latency_ms stays network-only and
    # success-only; these are the numbers that let a multi-second call be
    # attributed to local queueing, upstream time, or retry sleep.
    stats = _class_stats(_caller_class_var.get())
    stats.count("calls")
    _count_endpoint_window(endpoint_name, rate_limited=False, error=False)
    call_started = _monotonic()
    backoff_total = 0.0
    failed = False
    delay = base_delay
    try:
        for attempt in range(max_retries + 1):
            stats.count("attempts")
            wait_started = _monotonic()
            await limiter.acquire()
            started = _monotonic()
            stats.add("limiter_wait", started - wait_started)
            try:
                result = await coro_func(*args, **kwargs)
            except Exception as e:
                stats.add("network", _monotonic() - started)
                if getattr(e, "status", None) != 429:
                    _record_http_attempt(endpoint_name, rate_limited=False, success=False, latency_ms=None)
                    _endpoint_window[endpoint_name]["errors"] += 1
                    failed = True
                    raise
                _record_http_attempt(endpoint_name, rate_limited=True, success=False, latency_ms=None)
                _endpoint_window[endpoint_name]["rate_limited"] += 1
                stats.count("rate_limited")
                if attempt == max_retries:
                    failed = True
                    raise
                _rate_limit_hits_since_reset += 1
                sleep_started = _monotonic()
                await asyncio.sleep(delay + random.uniform(0, delay * 0.25))  # jitter
                backoff_total += _monotonic() - sleep_started
                delay *= 2
                continue
            network = _monotonic() - started
            stats.add("network", network)
            _record_http_attempt(endpoint_name, rate_limited=False, success=True, latency_ms=network * 1000)
            return result
    finally:
        stats.add("total", _monotonic() - call_started)
        if backoff_total > 0.0:
            stats.add("backoff", backoff_total)
        if failed:
            stats.count("errors")
