"""services/index_feed/backfill.py (issue #260) - reconnect-gap backfill
via the CF Benchmarks REST passthrough (docs/kalshi/rest-passthrough.md).

Three layers, tested separately:
  * detect_gap - pure, synchronous, reuses KalshiStreamGateway's own
    reconnects/last_gap_sec tracking (services/kalshi/websocket.py).
  * backfill_index/check_and_backfill - orchestration, with fetch_history
    injected so gap-window math is testable without real signing/network.
  * fetch_cfbenchmarks_history - the real REST-calling layer, tested with a
    fake httpx-shaped client (same idiom services/kalshi/public.py's
    _get_json follows) and a fake rate limiter/no-sleep (same idiom
    tests/test_http_client.py's own _install_clock/_install_limiter/
    _no_sleep use, so this never contends the real shared module-level
    limiter across the test session).
"""
import asyncio
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services import fault_log, http_client
from services.index_feed import backfill, ingestion


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ingestion, "DB_PATH", tmp_path / "index_feed.db")
    monkeypatch.setattr(ingestion, "_latest", {})
    monkeypatch.setattr(ingestion, "_tick_buffer", [])
    monkeypatch.setattr(ingestion, "_dropped_rows", 0)
    monkeypatch.setattr(backfill, "_last_seen_reconnects", 0)
    monkeypatch.setattr(backfill, "_stats", {
        "checks": 0, "gaps_detected": 0, "attempts": 0, "successes": 0,
        "failures": 0, "rows_backfilled": 0, "last_gap": None, "last_result": None,
    })
    monkeypatch.setattr(backfill, "_logged_history_response_shape", False)
    yield


# ------------------------------------------------------------- detect_gap

def _metrics(reconnects=0, last_disconnect=None, last_gap_sec=None):
    return {"reconnects": reconnects, "last_disconnect": last_disconnect, "last_gap_sec": last_gap_sec}


def test_detect_gap_is_none_before_any_reconnect():
    assert backfill.detect_gap(_metrics(reconnects=0)) is None
    assert backfill.stats()["checks"] == 1
    assert backfill.stats()["gaps_detected"] == 0


def test_detect_gap_fires_once_for_a_new_reconnect():
    metrics = _metrics(reconnects=1, last_disconnect={"reason": "x", "at": 100.0}, last_gap_sec=7.5)

    gap = backfill.detect_gap(metrics)

    assert gap == {"reconnect_at": 107.5, "gap_sec": 7.5, "reconnects": 1}
    assert backfill.stats()["gaps_detected"] == 1
    assert backfill.stats()["last_gap"] == gap


def test_detect_gap_does_not_refire_for_the_same_reconnect_count():
    metrics = _metrics(reconnects=1, last_disconnect={"reason": "x", "at": 100.0}, last_gap_sec=7.5)
    assert backfill.detect_gap(metrics) is not None

    assert backfill.detect_gap(metrics) is None  # same reconnects value - already handled
    assert backfill.stats()["gaps_detected"] == 1


def test_detect_gap_fires_again_on_a_second_distinct_reconnect():
    first = _metrics(reconnects=1, last_disconnect={"reason": "x", "at": 100.0}, last_gap_sec=5.0)
    second = _metrics(reconnects=2, last_disconnect={"reason": "y", "at": 200.0}, last_gap_sec=3.0)
    assert backfill.detect_gap(first) is not None

    gap = backfill.detect_gap(second)

    assert gap == {"reconnect_at": 203.0, "gap_sec": 3.0, "reconnects": 2}
    assert backfill.stats()["gaps_detected"] == 2


def test_detect_gap_is_none_when_the_gap_was_skew_dropped():
    """_begin_connection never records a negative gap (wall-clock skew) -
    last_gap_sec stays None for that reconnect. Nothing to backfill from a
    duration that was never measured."""
    metrics = _metrics(reconnects=1, last_disconnect={"reason": "x", "at": 100.0}, last_gap_sec=None)

    assert backfill.detect_gap(metrics) is None
    assert backfill.stats()["gaps_detected"] == 0
    # The reconnect count is still consumed - a later real gap on the same
    # counter value must not refire.
    assert backfill.detect_gap(metrics) is None


# --------------------------------------------------------- backfill_index

def test_backfill_index_uses_the_last_recorded_tick_as_the_window_start():
    ingestion.record_cfbenchmarks({"index_id": "BRTI", "data": json.dumps({"value": "1"})}, now=95.0)
    gap = {"reconnect_at": 110.0, "gap_sec": 15.0, "reconnects": 1}
    seen_windows = []

    async def fetch_history(index_id, start_ts, end_ts):
        seen_windows.append((index_id, start_ts, end_ts))
        return []

    result = asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    assert seen_windows == [("BRTI", 95.0, 110.0)]
    assert result == {"index_id": "BRTI", "start_ts": 95.0, "end_ts": 110.0, "rows": 0,
                       "error": None, "at": result["at"]}


def test_backfill_index_falls_back_to_the_ws_level_gap_when_no_prior_tick_exists():
    """Cold start: index_stream reconnects before this index has ever
    produced a live tick. Nothing in ingestion to anchor the window to, so
    the WS-level disconnect->reconnect duration is the best known bound."""
    gap = {"reconnect_at": 200.0, "gap_sec": 12.0, "reconnects": 1}
    seen_windows = []

    async def fetch_history(index_id, start_ts, end_ts):
        seen_windows.append((start_ts, end_ts))
        return []

    asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    assert seen_windows == [(188.0, 200.0)]


def test_backfill_index_skips_the_fetch_for_a_degenerate_window():
    """gap_sec == 0 (a reconnect gap so small it rounded to zero) with no
    prior recorded tick to anchor to collapses the fallback start_ts to
    exactly reconnect_at - nothing missing, so no REST call should fire."""
    gap = {"reconnect_at": 110.0, "gap_sec": 0.0, "reconnects": 1}
    called = []

    async def fetch_history(index_id, start_ts, end_ts):
        called.append(True)
        return []

    result = asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    assert called == []
    assert result["rows"] == 0


def test_backfill_index_stores_the_returned_points_and_updates_stats():
    gap = {"reconnect_at": 110.0, "gap_sec": 20.0, "reconnects": 1}
    points = [
        {"time": 91_000, "value": "63500.00"},
        {"time": 92_000, "value": "63501.00"},
    ]

    async def fetch_history(index_id, start_ts, end_ts):
        return points

    result = asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    assert result["rows"] == 2
    assert result["error"] is None
    s = backfill.stats()
    assert s["attempts"] == 1 and s["successes"] == 1 and s["failures"] == 0
    assert s["rows_backfilled"] == 2
    assert s["last_result"]["rows"] == 2

    # The flush is scheduled via asyncio.create_task (event-loop-blocking
    # elimination Fix 1) rather than run inline - asyncio.run() above
    # doesn't wait for outstanding tasks once backfill_index itself
    # returns, so call flush() explicitly here to check real persistence
    # (harmless/idempotent if the scheduled task already ran first).
    ingestion.flush()

    with __import__("sqlite3").connect(ingestion.DB_PATH) as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute("SELECT * FROM index_ticks ORDER BY source_ts_ms").fetchall()
    assert [r["source"] for r in rows] == ["cfbenchmarks_backfill", "cfbenchmarks_backfill"]


def test_backfill_index_schedules_flush_via_tick_executor_when_points_are_stored(monkeypatch):
    """event-loop-blocking elimination Fix 1 (2026-09-01) - a second,
    lower-frequency instance found by adversarial review of the PR that
    fixed the original four sites: record_cfbenchmarks_backfill() no
    longer calls flush() itself; backfill_index() must schedule it via
    create_task(tick_executor.run(...)) instead of blocking on it.
    Real tick_executor.run/asyncio.create_task run underneath (not fully
    mocked) - ingestion.last_tick_before also legitimately calls
    tick_executor.run internally (its own flush-then-read fix), so this
    spies rather than replaces, and only asserts on the second call
    (backfill_index's own scheduled flush), not the first."""
    from services import tick_executor

    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []
    real_tick_executor_run = tick_executor.run

    async def spy_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return await real_tick_executor_run(fn)

    monkeypatch.setattr(tick_executor, "run", spy_tick_executor_run)

    gap = {"reconnect_at": 110.0, "gap_sec": 20.0, "reconnects": 1}
    points = [{"time": 91_000, "value": "63500.00"}]

    async def fetch_history(index_id, start_ts, end_ts):
        return points

    asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    # Two tick_executor.run calls total: last_tick_before's internal
    # flush-then-read, then backfill_index's own scheduled flush.
    assert len(tick_executor_calls) == 2
    assert tick_executor_calls[1] is ingestion.flush
    # Only the second is scheduled via create_task (fire-and-forget) - the
    # first is awaited directly inside last_tick_before, never wrapped in
    # create_task, so exactly one create_task call is expected here.
    assert len(scheduled) == 1


def test_backfill_index_schedules_no_flush_when_nothing_was_stored(monkeypatch):
    """An empty fetch_history result reaches record_cfbenchmarks_backfill
    but stores nothing (should_flush=False) - backfill_index must not
    schedule a flush in that case. last_tick_before's own internal
    tick_executor.run call (for its flush-then-read) still happens - this
    only asserts nothing extra gets scheduled via create_task on top of
    that expected one."""
    from services import tick_executor

    scheduled = []
    real_create_task = asyncio.create_task
    monkeypatch.setattr(asyncio, "create_task", lambda coro: scheduled.append(coro) or real_create_task(coro))

    gap = {"reconnect_at": 110.0, "gap_sec": 20.0, "reconnects": 1}

    async def fetch_history(index_id, start_ts, end_ts):
        return []  # nothing to store

    asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    assert scheduled == []


def test_backfill_index_never_raises_when_fetch_history_fails():
    gap = {"reconnect_at": 110.0, "gap_sec": 20.0, "reconnects": 1}

    async def fetch_history(index_id, start_ts, end_ts):
        raise RuntimeError("upstream boom")

    result = asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    assert result["rows"] == 0
    assert result["error"] == "upstream boom"
    s = backfill.stats()
    assert s["attempts"] == 1 and s["failures"] == 1 and s["successes"] == 0


def test_backfill_index_records_a_fault_on_a_fetch_failure(monkeypatch):
    recorded = []
    monkeypatch.setattr(fault_log, "record", lambda *a, **k: recorded.append((a, k)))
    monkeypatch.setattr(backfill.fault_log, "record", lambda *a, **k: recorded.append((a, k)))
    gap = {"reconnect_at": 110.0, "gap_sec": 20.0, "reconnects": 1}

    async def fetch_history(index_id, start_ts, end_ts):
        raise RuntimeError("upstream boom")

    asyncio.run(backfill.backfill_index("BRTI", gap, fetch_history))

    assert recorded  # the failure is visible, not silently swallowed


# ------------------------------------------------------ check_and_backfill

def test_check_and_backfill_is_a_noop_without_a_new_gap():
    calls = []

    async def fetch_history(index_id, start_ts, end_ts):
        calls.append(index_id)
        return []

    results = asyncio.run(backfill.check_and_backfill(_metrics(reconnects=0), fetch_history, ["BRTI"]))

    assert results == []
    assert calls == []


def test_check_and_backfill_backfills_every_configured_index_on_a_new_gap():
    metrics = _metrics(reconnects=1, last_disconnect={"reason": "x", "at": 100.0}, last_gap_sec=5.0)
    calls = []

    async def fetch_history(index_id, start_ts, end_ts):
        calls.append(index_id)
        return []

    results = asyncio.run(backfill.check_and_backfill(metrics, fetch_history, ["BRTI", "ETHUSD_RTI"]))

    assert calls == ["BRTI", "ETHUSD_RTI"]
    assert [r["index_id"] for r in results] == ["BRTI", "ETHUSD_RTI"]


def test_check_and_backfill_defaults_to_default_index_ids():
    metrics = _metrics(reconnects=1, last_disconnect={"reason": "x", "at": 100.0}, last_gap_sec=5.0)
    calls = []

    async def fetch_history(index_id, start_ts, end_ts):
        calls.append(index_id)
        return []

    asyncio.run(backfill.check_and_backfill(metrics, fetch_history))

    assert calls == list(ingestion.DEFAULT_INDEX_IDS)


# -------------------------------------------------------- _hour_bucket_starts

def test_hour_bucket_starts_covers_a_window_within_one_hour():
    start = 1_800_000_600.0  # 10 minutes past some hour boundary
    end = start + 30.0
    buckets = backfill._hour_bucket_starts(start, end)
    assert len(buckets) == 1
    assert buckets[0] == pytest.approx((int(start) // 3600) * 3600)


def test_hour_bucket_starts_covers_a_window_spanning_an_hour_boundary():
    hour = 1_800_000_000.0
    start = hour - 5.0
    end = hour + 5.0
    buckets = backfill._hour_bucket_starts(start, end)
    assert buckets == [hour - 3600.0, hour]


def test_hour_bucket_starts_is_capped_for_a_pathological_window():
    start = 0.0
    end = 3600.0 * 100
    buckets = backfill._hour_bucket_starts(start, end)
    assert len(buckets) == 100  # uncapped here - the cap is applied by the caller


# ---------------------------------------------------- fetch_cfbenchmarks_history

def _keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHTTPXClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    async def get(self, url, headers=None, timeout=None):
        self.requests.append({"url": url, "headers": headers})
        return self._responses.pop(0)


def _no_sleep(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)


def _fake_limiter(monkeypatch):
    class _Limiter:
        waiters = 0
        waiters_high_water = 0

        async def acquire(self):
            return None

        def tokens_available(self):
            return 1.0

    monkeypatch.setattr(http_client, "_kalshi_read_limiter", _Limiter())


def test_fetch_cfbenchmarks_history_signs_and_scopes_the_request(monkeypatch):
    _no_sleep(monkeypatch)
    _fake_limiter(monkeypatch)
    private_key, public_key = _keypair()
    fake_client = _FakeHTTPXClient([_FakeResponse({"data": {"payload": []}})])
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)

    asyncio.run(backfill.fetch_cfbenchmarks_history(
        "BRTI", 1_800_000_600.0, 1_800_000_630.0,
        key_id="key-1", private_key=private_key, base_url="https://external-api.kalshi.com/trade-api/v2",
    ))

    assert len(fake_client.requests) == 1
    req = fake_client.requests[0]
    assert req["url"].startswith("https://external-api.kalshi.com/trade-api/v2/cfbenchmarks/history/values?")
    assert "id=BRTI" in req["url"] and "timespan=HOUR" in req["url"]
    assert req["headers"]["KALSHI-ACCESS-KEY"] == "key-1"
    # Signature verifies against the documented message shape: timestamp +
    # GET + path WITHOUT the query string (docs/kalshi/api_keys.md).
    timestamp = req["headers"]["KALSHI-ACCESS-TIMESTAMP"]
    signature = __import__("base64").b64decode(req["headers"]["KALSHI-ACCESS-SIGNATURE"])
    message = f"{timestamp}GET/trade-api/v2/cfbenchmarks/history/values".encode()
    public_key.verify(
        signature, message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_fetch_cfbenchmarks_history_filters_to_the_exact_requested_window(monkeypatch):
    """A whole-hour bucket legitimately returns points outside the actual
    gap - only genuinely missing ticks may be stored."""
    _no_sleep(monkeypatch)
    _fake_limiter(monkeypatch)
    private_key, _ = _keypair()
    hour = 1_800_000_000.0
    payload = {"data": {"payload": [
        {"time": int((hour + 5) * 1000), "value": "1"},    # before the window - excluded
        {"time": int((hour + 15) * 1000), "value": "2"},   # inside the window
        {"time": int((hour + 25) * 1000), "value": "3"},   # inside the window
        {"time": int((hour + 55) * 1000), "value": "4"},   # after the window - excluded
    ]}}
    fake_client = _FakeHTTPXClient([_FakeResponse(payload)])
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)

    points = asyncio.run(backfill.fetch_cfbenchmarks_history(
        "BRTI", hour + 10, hour + 30,
        key_id="key-1", private_key=private_key, base_url="https://external-api.kalshi.com/trade-api/v2",
    ))

    assert [p["value"] for p in points] == ["2", "3"]


def test_fetch_cfbenchmarks_history_fetches_one_bucket_per_hour_the_gap_touches(monkeypatch):
    _no_sleep(monkeypatch)
    _fake_limiter(monkeypatch)
    private_key, _ = _keypair()
    hour = 1_800_000_000.0
    fake_client = _FakeHTTPXClient([
        _FakeResponse({"data": {"payload": [{"time": int((hour - 5) * 1000), "value": "a"}]}}),
        _FakeResponse({"data": {"payload": [{"time": int((hour + 3) * 1000), "value": "b"}]}}),
    ])
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)

    points = asyncio.run(backfill.fetch_cfbenchmarks_history(
        "BRTI", hour - 5, hour + 5,
        key_id="key-1", private_key=private_key, base_url="https://external-api.kalshi.com/trade-api/v2",
    ))

    assert len(fake_client.requests) == 2
    assert [p["value"] for p in points] == ["a", "b"]


def test_fetch_cfbenchmarks_history_never_stores_a_point_with_no_usable_timestamp(monkeypatch):
    _no_sleep(monkeypatch)
    _fake_limiter(monkeypatch)
    private_key, _ = _keypair()
    hour = 1_800_000_000.0
    payload = {"data": {"payload": [{"value": "no time field at all"}]}}
    fake_client = _FakeHTTPXClient([_FakeResponse(payload)])
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)

    points = asyncio.run(backfill.fetch_cfbenchmarks_history(
        "BRTI", hour, hour + 60,
        key_id="key-1", private_key=private_key, base_url="https://external-api.kalshi.com/trade-api/v2",
    ))

    assert points == []


def test_fetch_cfbenchmarks_history_logs_a_fault_on_an_unrecognized_payload_shape(monkeypatch):
    _no_sleep(monkeypatch)
    _fake_limiter(monkeypatch)
    private_key, _ = _keypair()
    hour = 1_800_000_000.0
    recorded = []
    monkeypatch.setattr(backfill.fault_log, "record_fault", lambda *a, **k: recorded.append((a, k)))
    fake_client = _FakeHTTPXClient([_FakeResponse({"data": {"payload": "not a list or dict"}})])
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)

    points = asyncio.run(backfill.fetch_cfbenchmarks_history(
        "BRTI", hour, hour + 60,
        key_id="key-1", private_key=private_key, base_url="https://external-api.kalshi.com/trade-api/v2",
    ))

    assert points == []
    assert recorded  # visible, not silently swallowed


def test_fetch_cfbenchmarks_history_extracts_a_list_nested_under_a_known_key(monkeypatch):
    """Defends the case where the real (unverified) history response
    wraps its points under a key like "values" instead of being a bare
    list - see the module's own UNVERIFIED note."""
    _no_sleep(monkeypatch)
    _fake_limiter(monkeypatch)
    private_key, _ = _keypair()
    hour = 1_800_000_000.0
    payload = {"data": {"payload": {"values": [{"time": int((hour + 5) * 1000), "value": "x"}]}}}
    fake_client = _FakeHTTPXClient([_FakeResponse(payload)])
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)

    points = asyncio.run(backfill.fetch_cfbenchmarks_history(
        "BRTI", hour, hour + 60,
        key_id="key-1", private_key=private_key, base_url="https://external-api.kalshi.com/trade-api/v2",
    ))

    assert [p["value"] for p in points] == ["x"]
