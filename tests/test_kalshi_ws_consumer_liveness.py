"""Backstop for a stuck consumer that services/kalshi/websocket.py's
per-message asyncio.wait_for (_HANDLER_TIMEOUT_SEC) doesn't structurally
cover - e.g. a hang inside _sync_subscriptions, which runs on the reader's
own task, not the consumer's. Live-observed incident: issue #145,
docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md's
"Live incident (2026-08-27...)" section.

ensure_consumer_progressing() and force_reconnect() are tested directly
(no real socket) - force_reconnect is exercised via a fake connection
object so its "close the current connection without touching self._stop"
contract (distinct from close(), which stops the stream permanently) is
checked without a live websocket."""
import asyncio

from services.kalshi.websocket import KalshiStreamGateway


def _gateway() -> KalshiStreamGateway:
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")
    gw._begin_connection()
    return gw


class _FakeConnection:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


# --- force_reconnect: closes the connection, never stops the stream --------

def test_force_reconnect_closes_the_current_connection():
    gw = _gateway()
    fake = _FakeConnection()
    gw._ws = fake

    asyncio.run(gw.force_reconnect("test"))

    assert fake.closed is True


def test_force_reconnect_does_not_set_stop_unlike_close():
    # self._stop = True would end run()'s outer while loop permanently -
    # force_reconnect must let run() reconnect, not shut the stream down.
    gw = _gateway()
    gw._ws = _FakeConnection()

    asyncio.run(gw.force_reconnect("test"))

    assert gw._stop is False


def test_force_reconnect_is_a_noop_when_there_is_no_current_connection():
    gw = _gateway()
    gw._ws = None

    asyncio.run(gw.force_reconnect("test"))  # must not raise


# --- ensure_consumer_progressing: detection --------------------------------

def _seed(gw, *, processed: int, received: int, queue_depth: int):
    gw._processed_by_class = {"trade": processed} if processed else {}
    gw.messages_received = received
    for i in range(queue_depth):
        gw._queue.put_nowait((0.0, "trade", {"type": "trade", "msg": {}}))


def test_first_sample_never_triggers_a_reconnect(monkeypatch):
    gw = _gateway()
    calls = []
    monkeypatch.setattr(gw, "force_reconnect", lambda reason: calls.append(reason) or _noop_coro())
    _seed(gw, processed=0, received=5, queue_depth=3)

    triggered = asyncio.run(gw.ensure_consumer_progressing())

    assert triggered is False and calls == []


async def _noop_coro():
    return None


def _drive_samples(gw, n: int, *, processed: int, received: int, queue_depth: int):
    result = False
    for _ in range(n):
        _seed(gw, processed=processed, received=received, queue_depth=queue_depth)
        result = asyncio.run(gw.ensure_consumer_progressing())
    return result


def _drive_stuck_samples(gw, n: int, *, processed: int, received_start: int, queue_depth: int):
    # Models the real incident: messages_received keeps climbing every
    # sample (the reader is still alive and counting arrivals, even ones it
    # has to drop) while processed_by_class stays frozen (the consumer
    # never comes back to queue.get()).
    result = False
    for i in range(n):
        _seed(gw, processed=processed, received=received_start + i, queue_depth=queue_depth)
        result = asyncio.run(gw.ensure_consumer_progressing())
    return result


def test_stuck_for_fewer_than_the_threshold_does_not_trigger(monkeypatch):
    gw = _gateway()
    calls = []
    monkeypatch.setattr(gw, "force_reconnect", lambda reason: calls.append(reason) or _noop_coro())

    _drive_samples(gw, 1, processed=1, received=10, queue_depth=5)  # baseline
    triggered = _drive_stuck_samples(gw, 2, processed=1, received_start=11, queue_depth=5)  # threshold is 3

    assert triggered is False and calls == []


def test_stuck_for_the_full_threshold_forces_a_reconnect_and_resets(monkeypatch):
    gw = _gateway()
    calls = []
    monkeypatch.setattr(gw, "force_reconnect", lambda reason: calls.append(reason) or _noop_coro())

    _drive_samples(gw, 1, processed=1, received=10, queue_depth=5)  # baseline
    triggered = _drive_stuck_samples(gw, 3, processed=1, received_start=11, queue_depth=5)  # 3 stuck samples

    assert triggered is True
    assert len(calls) == 1 and "3" in calls[0]
    assert gw._liveness_stuck_samples == 0  # doesn't keep firing every sample after


def test_progress_between_samples_never_triggers(monkeypatch):
    gw = _gateway()
    calls = []
    monkeypatch.setattr(gw, "force_reconnect", lambda reason: calls.append(reason) or _noop_coro())

    for processed in (1, 2, 3, 4, 5):
        _seed(gw, processed=processed, received=processed * 2, queue_depth=1)
        asyncio.run(gw.ensure_consumer_progressing())

    assert calls == []  # steadily advancing - never stuck


def test_a_genuinely_quiet_market_never_triggers(monkeypatch):
    # messages_received not climbing means nothing new arrived - a flat
    # processed count here is a healthy idle consumer, not a stuck one.
    gw = _gateway()
    calls = []
    monkeypatch.setattr(gw, "force_reconnect", lambda reason: calls.append(reason) or _noop_coro())

    _drive_samples(gw, 5, processed=3, received=7, queue_depth=0)

    assert calls == []


def test_never_connected_is_a_safe_noop():
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")  # no _begin_connection()

    triggered = asyncio.run(gw.ensure_consumer_progressing())

    assert triggered is False
