"""
services/alerting/alerting.py - transition-based detection for kill-switch/
connectivity, plus best-effort webhook notification dispatch.

Deliberately does NOT import services.app_state or call check_and_alert()
directly - no test file in this suite imports app_state without first
redirecting every singleton's DB_PATH (test_trading_gate.py's own pattern,
done before `import main`), and this file has no reason to take on that
same heavyweight setup just to exercise alerting logic that doesn't
actually depend on app_state's own state shape. _check_transition is the
real edge-detection logic and takes plain values; check_and_alert itself
is a thin read-risk.halted/state[...]-then-call-_check_transition wrapper,
verified working correctly against real live traffic instead (real
trade_stream disconnect/reconnect cycles correctly created and resolved
alerts via GET /api/alerts/active during this feature's own development).
"""
import asyncio

import pytest

from services.alerting import alerting


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(alerting, "DB_PATH", tmp_path / "alert_log.db")
    monkeypatch.setattr(alerting, "_last_known_bad", {})
    yield


def test_record_alert_persists_and_shows_up_as_active():
    alert_id = asyncio.run(_record("kill_switch", "critical", "Kill switch tripped", now=1000.0))
    assert alert_id > 0
    active = alerting.active_alerts()
    assert len(active) == 1
    assert active[0]["category"] == "kill_switch"
    assert active[0]["resolved_at"] is None


def test_resolve_category_clears_it_from_active():
    asyncio.run(_record("kill_switch", "critical", "tripped", now=1000.0))
    resolved = alerting.resolve_category("kill_switch", now=1010.0)
    assert resolved == 1
    assert alerting.active_alerts() == []
    row = alerting.recent(limit=1)[0]
    assert row["resolved_at"] == 1010.0


def test_resolve_category_is_a_noop_when_nothing_is_active():
    assert alerting.resolve_category("kill_switch") == 0


def test_recent_returns_newest_first():
    asyncio.run(_record("kill_switch", "critical", "first", now=1000.0))
    asyncio.run(_record("trade_stream_connectivity", "warning", "second", now=2000.0))
    rows = alerting.recent(limit=10)
    assert [r["message"] for r in rows] == ["second", "first"]


# --- _check_transition: the real edge-detection logic --------------------

def test_transition_fires_once_on_going_bad():
    asyncio.run(_transition("kill_switch", True, "critical", lambda: "bad", "good"))
    active = alerting.active_alerts()
    assert len(active) == 1 and active[0]["message"] == "bad"


def test_transition_does_not_refire_while_still_bad():
    asyncio.run(_transition("kill_switch", True, "critical", lambda: "bad", "good"))
    asyncio.run(_transition("kill_switch", True, "critical", lambda: "bad again", "good"))
    assert len(alerting.active_alerts()) == 1  # still just the one


def test_transition_resolves_once_condition_clears():
    asyncio.run(_transition("kill_switch", True, "critical", lambda: "bad", "good"))
    asyncio.run(_transition("kill_switch", False, "critical", lambda: "bad", "good"))
    assert alerting.active_alerts() == []
    assert alerting.recent(limit=1)[0]["resolved_at"] is not None


def test_transition_does_not_reresolve_while_already_ok():
    asyncio.run(_transition("kill_switch", False, "critical", lambda: "bad", "good"))
    assert alerting.recent(limit=10) == []  # never went bad in the first place - nothing recorded at all


# --- webhook dispatch ------------------------------------------------------

class _FakeHttpClient:
    def __init__(self):
        self.posted: list[dict] = []

    async def post(self, url, json=None, timeout=None):
        self.posted.append({"url": url, "json": json})


def test_dispatch_notification_is_a_noop_when_webhook_url_unset(monkeypatch):
    from services.config_store import config_store
    monkeypatch.setattr(config_store, "get", lambda: {"alerting": {"webhook_url": None}})
    fake = _FakeHttpClient()
    monkeypatch.setattr(alerting, "get_client", lambda: fake)

    asyncio.run(alerting._dispatch_notification("kill_switch", "critical", "tripped", 1000.0))

    assert fake.posted == []


def test_dispatch_notification_posts_a_slack_compatible_payload_when_configured(monkeypatch):
    from services.config_store import config_store
    monkeypatch.setattr(config_store, "get", lambda: {"alerting": {"webhook_url": "https://example.invalid/hook"}})
    fake = _FakeHttpClient()
    monkeypatch.setattr(alerting, "get_client", lambda: fake)

    asyncio.run(alerting._dispatch_notification("kill_switch", "critical", "Kill switch tripped", 1000.0))

    assert len(fake.posted) == 1
    call = fake.posted[0]
    assert call["url"] == "https://example.invalid/hook"
    assert call["json"]["text"] == "[CRITICAL] kill_switch: Kill switch tripped"
    assert call["json"]["category"] == "kill_switch"
    assert call["json"]["triggered_at"] == 1000.0


def test_dispatch_notification_swallows_a_delivery_failure(monkeypatch):
    from services.config_store import config_store
    monkeypatch.setattr(config_store, "get", lambda: {"alerting": {"webhook_url": "https://example.invalid/hook"}})

    class _BoomClient:
        async def post(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(alerting, "get_client", lambda: _BoomClient())

    # Must not raise - a broken webhook endpoint can never be allowed to
    # propagate into the trading loop that (indirectly) triggered this.
    asyncio.run(alerting._dispatch_notification("kill_switch", "critical", "tripped", 1000.0))


async def _record(*args, **kwargs):
    return alerting.record_alert(*args, **kwargs)


async def _transition(*args, **kwargs):
    return alerting._check_transition(*args, **kwargs)
