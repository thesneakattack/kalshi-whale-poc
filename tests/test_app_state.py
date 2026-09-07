"""
services/app_state.py's bump_generation() coarsening (Task 7 of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md, moved there 2026-09-06, planning-lanes migration). No bespoke DB-
redirection needed here - tests/conftest.py's module-level
install_runtime_isolation() (confirmed by direct read, line 11) already
runs before this file is collected, same as every other test file in this
suite.
"""
from services import app_state


def test_bump_generation_coarsens_to_at_most_once_per_second(monkeypatch):
    """whale_stream_handlers.py's _process_stream_trade calls
    bump_generation() unconditionally on every processed trade message
    (confirmed live, all 3 of its exit paths) - this made state["generation"]
    (the literal ETag value) change far more often than /api/state's own
    body actually did, defeating the ETag's whole purpose (a 6-30s poll
    landing between real changes should get a 304, and almost never could).
    """
    start_gen = app_state.state["generation"]
    fake_now = [1000.0]
    monkeypatch.setattr(app_state.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(app_state, "_last_bump_ts", 0.0)

    app_state.bump_generation()
    app_state.bump_generation()  # same instant - must be suppressed
    assert app_state.state["generation"] == start_gen + 1

    fake_now[0] += 1.1  # past the 1.0s coarsening window
    app_state.bump_generation()
    assert app_state.state["generation"] == start_gen + 2
