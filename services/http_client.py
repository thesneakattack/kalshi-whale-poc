"""
One shared, connection-pooled httpx.AsyncClient for every outbound HTTP call
in the app (Kalshi market data, Kalshi account, whale-watcher feeds, Google
OAuth). Every caller used to open its own `async with httpx.AsyncClient(...)`
per request, paying a full TCP+TLS handshake each time even when hitting the
same host seconds apart. One long-lived client reuses keep-alive connections
across calls instead.
"""
import asyncio
import random
import time

import httpx

_client: httpx.AsyncClient | None = None


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

    async def acquire(self):
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self._burst, self._tokens + (now - self._last_refill) * self._rate)
                self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self._rate)


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
# and still-valid fix this session: services/kalshi_client.py's own
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


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def call_with_backoff(
    coro_func, *args, max_retries: int = 4, base_delay: float = 0.5, is_write: bool = False, **kwargs
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
    read by default; services/kalshi_account_client.py's create_order/
    cancel_order are the only two real callers that pass is_write=True -
    grep for call_with_backoff before adding a new write-shaped call
    elsewhere and make sure it does too."""
    global _rate_limit_hits_since_reset
    limiter = _kalshi_write_limiter if is_write else _kalshi_read_limiter
    delay = base_delay
    for attempt in range(max_retries + 1):
        try:
            await limiter.acquire()
            return await coro_func(*args, **kwargs)
        except Exception as e:
            if getattr(e, "status", None) != 429 or attempt == max_retries:
                raise
            _rate_limit_hits_since_reset += 1
            await asyncio.sleep(delay + random.uniform(0, delay * 0.25))  # jitter
            delay *= 2
