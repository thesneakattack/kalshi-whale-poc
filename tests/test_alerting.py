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
    from services.config.config_store import config_store
    monkeypatch.setattr(config_store, "get", lambda: {"alerting": {"webhook_url": None}})
    fake = _FakeHttpClient()
    monkeypatch.setattr(alerting, "get_client", lambda: fake)

    asyncio.run(alerting._dispatch_notification("kill_switch", "critical", "tripped", 1000.0))

    assert fake.posted == []


def test_dispatch_notification_posts_a_slack_compatible_payload_when_configured(monkeypatch):
    from services.config.config_store import config_store
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
    from services.config.config_store import config_store
    monkeypatch.setattr(config_store, "get", lambda: {"alerting": {"webhook_url": "https://example.invalid/hook"}})

    class _BoomClient:
        async def post(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(alerting, "get_client", lambda: _BoomClient())

    # Must not raise - a broken webhook endpoint can never be allowed to
    # propagate into the trading loop that (indirectly) triggered this.
    asyncio.run(alerting._dispatch_notification("kill_switch", "critical", "tripped", 1000.0))


# --- crash-alert resolution: expire_old_alerts / resolve_alert -------------
#
# "crash" alerts are recorded as discrete per-occurrence events (task_supervisor's
# exception handler, one row per crash - see alerting.py's own docstring), not as
# an edge-detected level like kill_switch/connectivity, so they need their own
# per-row aging rule rather than reusing resolve_category's whole-category clear.

def test_expire_old_alerts_resolves_rows_older_than_max_age():
    asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    expired = alerting.expire_old_alerts("crash", 1800, now=1000.0 + 1800.1)
    assert len(expired) == 1
    assert alerting.active_alerts() == []


def test_expire_old_alerts_leaves_fresh_rows_active():
    asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    expired = alerting.expire_old_alerts("crash", 1800, now=1000.0 + 1000.0)
    assert expired == []
    assert len(alerting.active_alerts()) == 1


def test_expire_old_alerts_ages_each_row_independently():
    asyncio.run(_record("crash", "critical", "first crash", now=1000.0))
    asyncio.run(_record("crash", "critical", "second crash", now=2500.0))
    # at t=2900: first is 1900s old (past the 1800s cutoff), second is 400s old (not)
    expired = alerting.expire_old_alerts("crash", 1800, now=2900.0)
    assert len(expired) == 1
    remaining = alerting.active_alerts()
    assert len(remaining) == 1
    assert remaining[0]["message"] == "second crash"


def test_expire_old_alerts_is_a_noop_when_nothing_qualifies():
    assert alerting.expire_old_alerts("crash", 1800) == []


def test_resolve_alert_resolves_an_unresolved_row():
    alert_id = asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    assert alerting.resolve_alert(alert_id, now=1010.0) is True
    assert alerting.active_alerts() == []
    assert alerting.recent(limit=1)[0]["resolved_at"] == 1010.0


def test_resolve_alert_is_a_noop_on_already_resolved():
    alert_id = asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    alerting.resolve_alert(alert_id, now=1010.0)
    assert alerting.resolve_alert(alert_id, now=1020.0) is False


def test_resolve_alert_is_a_noop_on_unknown_id():
    assert alerting.resolve_alert(999999) is False


# --- crash-alert resolution: check_and_alert wiring (_expire_stale_crash_alerts) --

def test_expire_stale_crash_alerts_resolves_and_notifies(monkeypatch):
    from services.config.config_store import config_store
    monkeypatch.setattr(config_store, "get", lambda: {"alerting": {"webhook_url": "https://example.invalid/hook"}})
    fake = _FakeHttpClient()
    monkeypatch.setattr(alerting, "get_client", lambda: fake)

    asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    asyncio.run(_expire_stale({"alerting": {"crash_auto_resolve_after_sec": 1800}}, now=1000.0 + 1800.1))

    assert alerting.active_alerts() == []
    # 2 posts total: record_alert's own "crash happened" notification, then
    # the auto-resolve notification - only the second is this test's subject.
    assert len(fake.posted) == 2
    resolved_post = fake.posted[-1]["json"]
    assert resolved_post["category"] == "crash"
    assert resolved_post["severity"] == "info"
    assert "auto-resolved" in resolved_post["message"]


def test_expire_stale_crash_alerts_leaves_fresh_alerts_alone(monkeypatch):
    fake = _FakeHttpClient()
    monkeypatch.setattr(alerting, "get_client", lambda: fake)

    asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    asyncio.run(_expire_stale({"alerting": {"crash_auto_resolve_after_sec": 1800}}, now=1000.0 + 5.0))

    assert len(alerting.active_alerts()) == 1
    assert fake.posted == []


def test_expire_stale_crash_alerts_defaults_to_1800s_when_unconfigured(monkeypatch):
    fake = _FakeHttpClient()
    monkeypatch.setattr(alerting, "get_client", lambda: fake)

    asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    asyncio.run(_expire_stale({}, now=1000.0 + 1800.1))

    assert alerting.active_alerts() == []


def test_expire_stale_crash_alerts_disabled_via_zero_never_expires(monkeypatch):
    fake = _FakeHttpClient()
    monkeypatch.setattr(alerting, "get_client", lambda: fake)

    asyncio.run(_record("crash", "critical", "trading_loop crashed", now=1000.0))
    asyncio.run(_expire_stale({"alerting": {"crash_auto_resolve_after_sec": 0}}, now=1000.0 + 999_999))

    assert len(alerting.active_alerts()) == 1


async def _record(*args, **kwargs):
    return alerting.record_alert(*args, **kwargs)


async def _transition(*args, **kwargs):
    return alerting._check_transition(*args, **kwargs)


async def _expire_stale(*args, **kwargs):
    return alerting._expire_stale_crash_alerts(*args, **kwargs)


# --- alert_findings: active alerts as QualityFindings (#71) ---------------

def test_alert_findings_maps_critical_to_error_and_every_other_severity_to_warning():
    crash_id = asyncio.run(_record("crash", "critical", "trading_loop.run crashed: RuntimeError: x", now=1000.0))
    conn_id = asyncio.run(_record("trade_stream_connectivity", "warning", "Trade stream disconnected", now=1001.0))
    odd_id = asyncio.run(_record("future_category", "unexpected", "some new caller", now=1002.0))

    findings = alerting.alert_findings(alerting.active_alerts())

    by_scope = {f.scope: f for f in findings}
    assert set(by_scope) == {"crash", "trade_stream_connectivity", "future_category"}
    crash = by_scope["crash"]
    assert crash.severity == "error"
    assert crash.check == "active-alert"
    assert crash.source == "runtime"
    assert crash.confidence == "high"
    assert crash.finding_id == f"alerting:active-alert:crash:{crash_id}"
    assert crash.evidence == {
        "alert_id": crash_id, "severity": "critical", "triggered_at": 1000.0, "context": None,
    }
    assert "trading_loop.run crashed" in crash.summary
    assert by_scope["trade_stream_connectivity"].severity == "warning"
    assert by_scope["trade_stream_connectivity"].finding_id == (
        f"alerting:active-alert:trade_stream_connectivity:{conn_id}"
    )
    # An active alert is by construction something a human must see, so
    # an unmapped severity floors at warning rather than vanishing to info.
    assert by_scope["future_category"].severity == "warning"
    assert by_scope["future_category"].evidence["alert_id"] == odd_id


def test_alert_findings_are_empty_once_alerts_resolve():
    asyncio.run(_record("kill_switch", "critical", "tripped", now=1000.0))
    assert len(alerting.alert_findings(alerting.active_alerts())) == 1
    alerting.resolve_category("kill_switch", now=1010.0)
    assert alerting.alert_findings(alerting.active_alerts()) == []
    assert alerting.alert_findings([]) == []
