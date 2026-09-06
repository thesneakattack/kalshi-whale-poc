"""
GET /api/observability/history and GET /api/observability/summary
(services/observability/routes.py) - issue #629, same defect class already
fixed for GET /api/quality/summary (#552) and issue #585's two call sites
(#624/#625): these two `async def` routes called observability.py's
plain-sync, sqlite-touching history()/summary() directly in the handler
body with no thread dispatch, blocking the whole event loop for the call's
duration (measured live during #530's sweep: ~7s stall on a concurrent
GET /api/state while GET /api/observability/summary?hours=720 was in
flight - see services/observability/observability.py's _connect()
docstring for the original 0.79-1.02s/~7.66s measurement this issue is
closing out).

Relies on tests/conftest.py's global install_runtime_isolation() (redirects
services.observability.observability.DB_PATH to a tmp path and hard-blocks
real data/ sqlite3.connect calls) rather than a second per-file redirect -
same rationale as tests/test_quality_routes.py's own docstring.
"""
import asyncio
import time

import pytest  # noqa: E402

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services.observability import observability  # noqa: E402

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Same shape as tests/test_observability.py's own `_isolated` fixture -
    a fresh DB_PATH per test, since tests/conftest.py's global
    install_runtime_isolation() only redirects the module once per session
    (a shared tmp file across every test in this file otherwise)."""
    monkeypatch.setattr(observability, "DB_PATH", tmp_path / "observability.db")


def _spy(on_loop: dict, name: str, real):
    """Same idiom as tests/test_quality_routes.py's
    test_quality_summary_dispatches_its_blocking_calls_off_the_event_loop
    and tests/test_loop_watchdog.py's off-loop test: the spy checks whether
    asyncio.get_running_loop() succeeds *inside* the real call, proving the
    call actually left the event loop (ran on a worker thread) rather than
    merely that asyncio.to_thread was invoked somewhere."""
    def wrapper(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop[name] = True
        except RuntimeError:
            on_loop[name] = False
        return real(*args, **kwargs)
    return wrapper


def test_observability_history_dispatches_off_the_event_loop(monkeypatch):
    on_loop: dict = {}
    monkeypatch.setattr(observability, "history", _spy(on_loop, "history", observability.history))

    resp = client.get("/api/observability/history", params={"metric": "tick.duration_sec"})

    assert resp.status_code == 200
    assert on_loop == {"history": False}, f"observability.history ran on the event loop: {on_loop}"


def test_observability_summary_dispatches_off_the_event_loop(monkeypatch):
    on_loop: dict = {}
    monkeypatch.setattr(observability, "summary", _spy(on_loop, "summary", observability.summary))

    resp = client.get("/api/observability/summary", params={"hours": 24})

    assert resp.status_code == 200
    assert on_loop == {"summary": False}, f"observability.summary ran on the event loop: {on_loop}"


def test_observability_history_response_shape_unchanged():
    now = time.time()
    observability.record_sample("tick.duration_sec", 1.5, observed_at=now)

    resp = client.get(
        "/api/observability/history",
        params={"metric": "tick.duration_sec", "hours": 1, "limit": 10},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "tick.duration_sec"
    assert body["samples"] == [
        {"observed_at": now, "metric": "tick.duration_sec", "value": 1.5, "labels": {}}
    ]


def test_observability_summary_response_shape_unchanged():
    observability.record_sample("tick.duration_sec", 2.0)

    resp = client.get("/api/observability/summary", params={"hours": 24})

    assert resp.status_code == 200
    body = resp.json()
    assert "hours" in body
    assert body["metrics"]["tick.duration_sec"] == {
        "count": 1, "min": 2.0, "max": 2.0, "avg": 2.0,
    }


def test_observability_history_still_rejects_invalid_metric_names():
    resp = client.get("/api/observability/history", params={"metric": "not valid!"})
    assert resp.status_code == 400
