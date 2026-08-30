"""Reconnect-gap backfill for services/index_feed/ (issue #260).

FAULT THIS CLOSES: services/kalshi/websocket.py's KalshiStreamGateway (the
dedicated `index_stream` connection - see services/app_state.py's own
comment on why CF Benchmarks/Pyth gets a physically isolated socket) has no
resume/replay capability on reconnect - confirmed directly in that module's
own CONTRACT_DOCS comment on force_reconnect/ensure_consumer_progressing:
"there is no per-message gap-detection/resume capability on this API tier."
Every index tick during an outage window was therefore permanently lost,
degrading settlement_algebra.py's settlement-edge math for every crypto
market that settles against BRTI/ETHUSD_RTI, not just a display value.

MECHANISM REUSED, NOT INVENTED: KalshiStreamGateway already tracks
`reconnects` (a monotonic counter) and `last_disconnect`/`last_gap_sec`
(the wall-clock gap the *most recent* reconnect closed) in its own
ingest_metrics()["connection"] - see websocket.py's _begin_connection/
_record_disconnect. detect_gap() below compares `reconnects` against what
this module last handled, so it fires exactly once per physical reconnect,
using the SAME gap-duration figure the realtime data-plane's own
observability sampler already persists - no second gap-tracking mechanism.

BACKFILL SOURCE: docs/kalshi/rest-passthrough.md documents
`GET /trade-api/v2/cfbenchmarks/history/values?id=<index>&timespan=<span>&
timestamp=<ISO8601>`, forwarded verbatim to CF Benchmarks'
`/api/v1/history/values`. Confirmed live 2026-08-30 (CF Benchmarks' own
docs, docs.cfbenchmarks.com/api/rest/historical-values - NOT mirrored under
docs/kalshi/, fetched directly per the "never guess" rule since the Kalshi
mirror explicitly defers index/parameter details to it):

  * "The time range of the data is defined by the timespan and timestamp
    parameters" and "the timestamp must be truncated to the timespan
    granularity" - this is a bucketed lookup (e.g. the whole UTC hour
    containing `timestamp` when timespan=HOUR), NOT an arbitrary
    [start, end) range query. _hour_bucket_starts below walks every
    HOUR-aligned bucket a gap touches; fetch_cfbenchmarks_history fetches
    each one and filters the result client-side to the exact gap window
    before returning - only genuinely missing ticks are ever stored.
  * "the most recent values may not be immediately available, and could be
    delayed by up to 15 minutes" - a real completeness risk for a backfill
    that runs seconds after reconnect: the freshest slice of a gap can come
    back empty even though the request itself succeeded. This module does
    not retry on that (see ROADMAP-worthy follow-up in the module's own
    stats() - `rows_backfilled` and `last_result` make an incomplete
    backfill visible rather than silently declaring success).
  * requires "authorization for both the target index and the
    STREAM_HISTORICAL_VALUES data stream" - the same entitlement gate
    docs/kalshi/rest-passthrough.md's own "Access" section describes
    ("available only to accounts with the appropriate entitlement").
  * Rate limit: 50 tokens/request from the Read bucket vs this app's usual
    10 (docs/kalshi/rest-passthrough.md) - a real 5x-cost outlier, given
    its own caller class (background_index_backfill,
    services/http_client.py) purely for visibility; this app's shared
    limiter (services/http_client._kalshi_read_limiter) already treats
    every call as one slot regardless of Kalshi's own per-endpoint token
    cost (a pre-existing, deliberately conservative simplification - see
    that module's own long comment), so this call is not weighted any
    differently than any other read here, consistent with how every other
    REST caller in this codebase is already instrumented.

UNVERIFIED, FLAGGED RATHER THAN GUESSED: CF Benchmarks' history-endpoint
response *body* schema (the shape inside the `data.payload` envelope) was
not retrievable through this session's available fetch tooling - only the
*live* WS frame shape is confirmed (docs/kalshi/cfbenchmarks-value.md's
AsyncAPI example: `{"type":"value","id":"BRTI","time":<ms>,"value":"<str>"}`).
_extract_points/_point_ts_sec below assume the historical endpoint reuses
that same point shape (a reasonable inference - CF Benchmarks is one
vendor's one index-value concept - but NOT a confirmed contract) and are
written defensively: an unrecognized payload shape is logged via
fault_log rather than crashing or fabricating rows, and the FIRST real
response this code ever receives is logged in full (services/kalshi/
websocket.py's _logged_fill_shape/_logged_position_shape idiom - "verify
parsing against this real payload the first time one exists") specifically
so a human can confirm or correct this guess against a real response,
which additionally requires an account with the CF Benchmarks entitlement
this repo's own credentials may not have.

Also unverified: whether BRTI/ETHUSD_RTI specifically are among the
indices docs/kalshi/cfbenchmarks-value.md calls out as supporting
intra-second historical granularity ("some indices" - not enumerated).
Best available evidence, not a confirmation: CF Benchmarks' own docs name
a "Real-Time Indices (RTIs)" vs "Risk Ratings (RRs)" split with materially
different lookback windows (1 hour vs 1 year) on the adjacent `/values`
endpoint, and this app's own tracked indices are both named accordingly -
BRTI ("Bitcoin *Real-Time* Index") and ETHUSD_RTI (`_RTI` suffix;
services/index_feed/test fixtures also carry "Ethereum Real-Time Index
(ERTI)" and "Dogecoin Real-Time Index (DOGEUSD_RTI)" as the same family).
If an index turns out to have coarser historical granularity than its live
1Hz stream, backfilled rows for it will legitimately be sparser than live
ones for the same window - a real limitation of the upstream capability,
not a parsing bug here.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from urllib.parse import urlencode, urlparse

from services import fault_log
from services.http_client import call_with_backoff, caller_class, get_client
from services.index_feed import ingestion
from services.kalshi import signing

logger = logging.getLogger(__name__)

_CFBENCHMARKS_HISTORY_PATH = "/cfbenchmarks/history/values"
# The one value docs/kalshi/rest-passthrough.md's own example documents
# ("to retrieve one hour of BRTI values"). CF Benchmarks' full timespan enum
# is not mirrored under docs/kalshi/ and was not retrievable through this
# session's fetch tooling (see module docstring) - do not add another value
# here without confirming it first against CF Benchmarks' own docs.
_HISTORY_TIMESPAN = "HOUR"
_HISTORY_BUCKET_SECONDS = 3600.0
# Bounds one gap's REST fan-out (one request per UTC-hour bucket touched).
# Reconnect backoff caps at 30s/attempt (websocket.py's own run() loop), so
# an ordinary gap needs exactly one bucket; this only matters for a
# genuinely extended outage, and exists so a gap-math bug can never turn
# into an unbounded request burst against the shared read limiter at
# exactly the moment that limiter is also serving every other caller
# recovering from the same reconnect.
_MAX_HOUR_BUCKETS_PER_GAP = 6

_last_seen_reconnects = 0
_stats: dict = {
    "checks": 0,
    "gaps_detected": 0,
    "attempts": 0,
    "successes": 0,
    "failures": 0,
    "rows_backfilled": 0,
    "last_gap": None,
    "last_result": None,
}
_logged_history_response_shape = False


def detect_gap(connection_metrics: dict, now: float | None = None) -> dict | None:
    """Pure, synchronous gap detector. `connection_metrics` is
    KalshiStreamGateway.ingest_metrics()["connection"] - the reconnect
    tracking issue #260 asks to reuse rather than re-invent. Returns the
    gap a BRAND NEW reconnect just closed, or None when there is nothing
    new to backfill: no reconnect yet, the same reconnect already handled,
    or a skew-dropped gap (_begin_connection's own docstring: a negative
    gap - wall clock moving backwards between the two time.time() reads -
    is never recorded, so last_gap_sec stays None for it)."""
    global _last_seen_reconnects
    _stats["checks"] += 1
    reconnects = connection_metrics.get("reconnects") or 0
    if reconnects <= _last_seen_reconnects:
        return None
    _last_seen_reconnects = reconnects
    last_disconnect = connection_metrics.get("last_disconnect")
    gap_sec = connection_metrics.get("last_gap_sec")
    if not last_disconnect or gap_sec is None:
        return None
    reconnect_at = last_disconnect["at"] + gap_sec
    gap = {"reconnect_at": reconnect_at, "gap_sec": gap_sec, "reconnects": reconnects}
    _stats["gaps_detected"] += 1
    _stats["last_gap"] = gap
    return gap


async def backfill_index(index_id: str, gap: dict, fetch_history, now: float | None = None) -> dict:
    """Backfill one index's missed window. `fetch_history(index_id,
    start_ts, end_ts) -> list[dict]` is injected (see
    fetch_cfbenchmarks_history for the real, REST-calling implementation)
    so gap-window math stays testable without real signing/network.

    start_ts is the LAST recorded tick strictly before the reconnect
    (ingestion.last_tick_before) - the true start of the gap, not merely
    `reconnect_at - gap_sec` (the WS-level disconnect->reconnect duration,
    which can be a little wider than the actual data gap). Falls back to
    the WS-level figure only when this index has no recorded tick at all
    yet (e.g. backfill running before the very first live tick)."""
    now = now if now is not None else time.time()
    reconnect_at = gap["reconnect_at"]
    start_ts = ingestion.last_tick_before(index_id, reconnect_at)
    if start_ts is None:
        start_ts = reconnect_at - gap["gap_sec"]
    result = {"index_id": index_id, "start_ts": start_ts, "end_ts": reconnect_at, "rows": 0, "error": None}
    if start_ts >= reconnect_at:
        return result  # nothing missing - already covered, or a degenerate window
    _stats["attempts"] += 1
    try:
        points = await fetch_history(index_id, start_ts, reconnect_at)
    except Exception as exc:
        _stats["failures"] += 1
        result["error"] = str(exc)
        fault_log.record("index_feed", "backfill", exc, context=f"{index_id} {start_ts}-{reconnect_at}")
        result["at"] = now
        _stats["last_result"] = dict(result)
        return result
    stored = ingestion.record_cfbenchmarks_backfill(index_id, points, now=now)
    result["rows"] = stored
    _stats["successes"] += 1
    _stats["rows_backfilled"] += stored
    result["at"] = now
    _stats["last_result"] = dict(result)
    return result


async def check_and_backfill(
    connection_metrics: dict, fetch_history, index_ids: list[str] | None = None, now: float | None = None,
) -> list[dict]:
    """Call periodically (see main.py's _index_feed_backfill_loop, wired
    alongside the existing 10s _stream_consumer_liveness_loop for
    index_stream) with index_stream.ingest_metrics()["connection"]. A no-op
    dict-compare the overwhelmingly common case (no new reconnect) - no
    REST call, no allocation beyond the one comparison in detect_gap."""
    gap = detect_gap(connection_metrics, now=now)
    if gap is None:
        return []
    ids = index_ids if index_ids is not None else list(ingestion.DEFAULT_INDEX_IDS)
    return [await backfill_index(index_id, gap, fetch_history, now=now) for index_id in ids]


def stats() -> dict:
    """Pure read - backfill activity for /api/health/pipeline (issue #260's
    own "expose the backfill activity" requirement) and diagnostics."""
    return dict(_stats)


def _hour_bucket_starts(start_ts: float, end_ts: float) -> list[float]:
    """Every UTC-hour-aligned epoch whose [bucket, bucket+3600) span
    overlaps [start_ts, end_ts) - see module docstring for why a single
    request cannot span an arbitrary window (the history endpoint's own
    `timestamp` parameter must be truncated to the `timespan` granularity)."""
    if end_ts <= start_ts:
        return []
    first = (int(start_ts) // int(_HISTORY_BUCKET_SECONDS)) * int(_HISTORY_BUCKET_SECONDS)
    last_ts = end_ts - 1e-6  # end is exclusive - don't pull in an extra bucket for an exact boundary
    last = (int(last_ts) // int(_HISTORY_BUCKET_SECONDS)) * int(_HISTORY_BUCKET_SECONDS)
    buckets = []
    bucket = float(first)
    while bucket <= last:
        buckets.append(bucket)
        bucket += _HISTORY_BUCKET_SECONDS
    return buckets


def _iso_utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _point_ts_sec(point: dict) -> float | None:
    """The live WS frame's confirmed field is `time` (unix ms,
    docs/kalshi/cfbenchmarks-value.md). `timestamp`/`ts` are accepted as a
    soft fallback only because the history endpoint's exact field name is
    UNVERIFIED (see module docstring) - never guessed past that."""
    raw = point.get("time")
    if raw is None:
        raw = point.get("timestamp", point.get("ts"))
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    return raw / 1000.0 if raw > 1e12 else float(raw)


def _extract_points(payload) -> list[dict]:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for key in ("values", "history", "data", "points", "ticks"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [p for p in candidate if isinstance(p, dict)]
    fault_log.record_fault(
        "index_feed", "backfill_response_shape_unknown",
        f"unrecognized cfbenchmarks history payload shape: {type(payload).__name__}",
        severity="warn",
    )
    return []


async def fetch_cfbenchmarks_history(
    index_id: str, start_ts: float, end_ts: float, *, key_id: str, private_key, base_url: str,
) -> list[dict]:
    """One CF Benchmarks REST passthrough call per UTC-hour bucket
    [start_ts, end_ts) touches (docs/kalshi/rest-passthrough.md), filtered
    client-side to exactly that window before returning - every returned
    point is the untouched upstream object (fidelity: see
    ingestion.record_cfbenchmarks_backfill), only points a whole-hour
    bucket inevitably includes OUTSIDE the requested window are dropped
    here rather than stored.

    Signs each request itself (services/kalshi/signing.py) - too new an
    endpoint (Kalshi's 2026-08-27 changelog) to have a typed SDK method, so
    nothing signs it automatically the way services/kalshi/account.py's
    calls are. Routed through the same shared rate limiter/backoff/
    telemetry every other Kalshi REST call in this app uses
    (services/http_client.call_with_backoff) under its own caller class
    (background_index_backfill) so this real 5x-cost-per-request outlier
    is visible on its own rather than folded into an unrelated bucket."""
    global _logged_history_response_shape
    signed_path = urlparse(base_url.rstrip("/") + _CFBENCHMARKS_HISTORY_PATH).path
    buckets = _hour_bucket_starts(start_ts, end_ts)[:_MAX_HOUR_BUCKETS_PER_GAP]
    dropped_unplaceable = 0
    all_points: list[dict] = []
    for bucket_start in buckets:
        params = {"id": index_id, "timespan": _HISTORY_TIMESPAN, "timestamp": _iso_utc(bucket_start)}
        url = base_url.rstrip("/") + _CFBENCHMARKS_HISTORY_PATH + "?" + urlencode(params)
        headers = signing.sign_request(key_id, private_key, "GET", signed_path)

        async def do_get(url=url, headers=headers):
            resp = await get_client().get(url, headers=headers, timeout=10.0)
            resp.raise_for_status()
            return resp.json()

        with caller_class("background_index_backfill"):
            body = await call_with_backoff(do_get, endpoint="cfbenchmarks_history_backfill")

        if not _logged_history_response_shape:
            _logged_history_response_shape = True
            logger.info("first real cfbenchmarks history response shape (verify parsing against this): %r", body)

        payload = ((body or {}).get("data") or {}).get("payload")
        for point in _extract_points(payload):
            if _point_ts_sec(point) is None:
                dropped_unplaceable += 1
                continue
            all_points.append(point)

    if dropped_unplaceable:
        fault_log.record_fault(
            "index_feed", "backfill_point_unplaceable",
            f"{dropped_unplaceable} cfbenchmarks history point(s) for {index_id} had no usable "
            "timestamp field and were dropped rather than guessed into the window",
            severity="warn",
        )
    return [p for p in all_points if start_ts <= _point_ts_sec(p) < end_ts]
