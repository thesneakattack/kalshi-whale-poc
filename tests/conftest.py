"""
Bootstraps pytest-time isolation from live repository data before any test
module in this directory is collected (and therefore before any test can
`import main` or a services module that constructs an app_state singleton).
The actual mechanism - what gets redirected, the sqlite3.connect hard guard,
and the collection-order incident that made per-file redirects insufficient
on their own - lives in tests/support/runtime_isolation.py.
"""
from tests.support.runtime_isolation import install_runtime_isolation

install_runtime_isolation()


import pytest


@pytest.fixture(autouse=True)
def _fresh_whale_pipeline_perf(monkeypatch):
    """services/whale_pipeline_perf.py's module-level singleton is process-
    global mutable state the hot path records into; without this every
    test that exercises the whale provider or stream handler would leak
    lifetime counters into the next test (observability's "no evidence ->
    no rows" contract is the first thing that breaks). Same principle as
    the DB isolation above, applied to an in-memory aggregator."""
    from services import whale_pipeline_perf as wpp
    monkeypatch.setattr(wpp, "perf", wpp.WhalePipelinePerf())


@pytest.fixture(autouse=True)
def _fresh_rest_latency_stats(monkeypatch):
    """services/http_client.py's per-caller-class latency stats (I5) are
    module-global for the same reason and with the same leak: any test that
    drives call_with_backoff leaves lifetime counts behind for the next one.
    Fresh dict per test; the module's own reset_rest_latency_window() only
    rolls windows and is deliberately not a full reset.

    _endpoint_window and the two token buckets' waiters_high_water are the
    rest of what rest_latency_snapshot() reads (audited for #234): both
    module-lifetime, both only ever surfaced once by_class is non-empty -
    which is exactly why they leaked unnoticed behind the empty-by_class
    gate until a test that drives a real call asserts on them."""
    from services import http_client
    monkeypatch.setattr(http_client, "_rest_class_stats", {})
    monkeypatch.setattr(http_client, "_endpoint_window", {})
    for limiter in (http_client._kalshi_read_limiter, http_client._kalshi_write_limiter):
        monkeypatch.setattr(limiter, "waiters_high_water", 0)


@pytest.fixture(autouse=True)
def _fresh_candidate_retry_state(monkeypatch):
    """services/candidate_retry.py's pending queue + window counters (P2
    Task 12) are the same class of module-global mutable state as
    whale_pipeline_perf/http_client above, and just as easy to leak: any
    test anywhere in the suite that calls enqueue()/run_pending() (this
    module's own tests, plus the wiring test in
    test_whale_candidate_lifecycle.py) otherwise leaves _pending entries
    and non-zero window counters behind for the next test, which broke
    test_observability.py's "a fully quiet tick returns metrics == {}"
    contract the first time this module shipped (candidate_retry.retried
    leaked from test_candidate_retry.py into an unrelated observability
    test, alphabetically later in test discovery order - the exact same
    failure shape as the whale_pipeline_perf leak that motivated the
    fixture above it)."""
    from services import candidate_retry
    monkeypatch.setattr(candidate_retry, "_pending", {})
    monkeypatch.setattr(candidate_retry, "_window_retried", 0)
    monkeypatch.setattr(candidate_retry, "_window_recovered", 0)
    monkeypatch.setattr(candidate_retry, "_window_abandoned", 0)


@pytest.fixture(autouse=True)
def _fresh_loop_watchdog_window(monkeypatch):
    """services/loop_watchdog.py's stall window (P0 Task 1) is the same
    class again - three module globals capture_from_runtime folds into
    every capture - and the one that was still missing here (issue #234):
    any test that runs main's lifespan (`with TestClient(main.app)`)
    starts the real watchdog task, which records a late first wakeup
    under load as a stall and leaves it behind when its loop closes, so
    test_observability.py's quiet-tick `metrics == {}` failed on CI with
    `loop_watchdog.stall_max_ms: 185.593` and passed on re-run. Reproduced
    deterministically before this fixture existed by collecting a test
    that plants a sample ahead of it."""
    from services import loop_watchdog
    monkeypatch.setattr(loop_watchdog, "_stall_max_ms", 0.0)
    monkeypatch.setattr(loop_watchdog, "_stall_count", 0)
    monkeypatch.setattr(loop_watchdog, "_samples", 0)


@pytest.fixture(autouse=True)
def _capture_writer_not_left_running():
    """services/capture_writer.py's daemon thread is the last input
    capture_from_runtime reads (writer.* rows appear while is_alive()).
    Its own tests start()/stop() in try/finally and stop() already resets
    the module's thread handle, so nothing leaks today - this is the
    containment for the day a test fails between start() and its finally,
    or a new test forgets the stop(): a thread left alive would make every
    later test in the process report a live writer."""
    yield
    from services import capture_writer
    if capture_writer._thread is not None:
        capture_writer.stop()
