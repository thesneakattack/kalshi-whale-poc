"""services/alerting/routes.py - GET /api/alerts/history's paginate()
wiring (Task 6a of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md, moved there 2026-09-06, planning-lanes migration).

New file: no tests/test_alerting_routes.py existed before this task (only
tests/test_alerting.py, which covers services/alerting/alerting.py's own
functions directly, not the routes module) - matches the per-package
`test_<package>_routes.py` convention already established by
tests/test_analytics_routes.py, tests/test_diagnostics_routes.py,
tests/test_backup_routes.py, and tests/test_whale_calibration_routes.py.

Uses TestClient(main.app) (tests/test_quality_routes.py's convention, per
this repo's own conftest.py global runtime-isolation fixture) rather than
calling get_alert_history() directly via asyncio.run: a direct call would
bypass FastAPI's own Depends() resolution entirely (the `limit` parameter
would just receive whatever value is passed as a plain kwarg, never routed
through paginate()'s clamp), so only a real HTTP request through the ASGI
app actually proves the dependency is wired in, not just defined.
"""
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services import alerting  # noqa: E402

client = TestClient(main.app)


def test_get_alert_history_route_clamps_an_oversized_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(alerting, "recent", lambda limit: calls.append(limit) or [])

    resp = client.get("/api/alerts/history?limit=999999")

    assert resp.status_code == 200
    assert calls == [200], "paginate(max_limit=200) should have clamped the oversized limit before alerting.recent() saw it"


def test_get_alert_history_route_defaults_to_50_with_no_query_string(monkeypatch):
    calls = []
    monkeypatch.setattr(alerting, "recent", lambda limit: calls.append(limit) or [])

    resp = client.get("/api/alerts/history")

    assert resp.status_code == 200
    assert calls == [50]
