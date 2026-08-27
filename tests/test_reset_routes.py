"""Confirms POST /api/reset, GET /api/reset/preview, and GET /api/reset/history
survived the main.py -> services/reset/routes.py extraction with identical
behavior - a wiring test, not new-behavior coverage (the route bodies
themselves are unchanged; tests/test_trading_gate.py already exercises
reset_broker's real domain-clearing behavior)."""
from fastapi.testclient import TestClient

import main


def test_reset_preview_route_is_wired():
    client = TestClient(main.app)
    resp = client.get("/api/reset/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert "counts" in body and "scope" in body


def test_reset_history_route_is_wired():
    client = TestClient(main.app)
    resp = client.get("/api/reset/history")
    assert resp.status_code == 200
    assert "events" in resp.json()
