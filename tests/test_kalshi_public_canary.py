"""
tools/kalshi_public_canary.py (QCP Task 14) - a scheduled/manual canary
against Kalshi's live, unauthenticated public API. All HTTP-free here: every
check function takes an injectable fetch callable (same pattern as
tests/test_kalshi_docs_drift.py's compare_remote tests), so these tests
never touch the network and never depend on live exchange state.
"""
import json

from tools import kalshi_public_canary as canary


def _fetch_returning(status: int, body) -> canary.Fetcher:
    text = body if isinstance(body, str) else json.dumps(body)

    def fetch(url, params=None):
        return status, text

    return fetch


# --- check_exchange_status ---------------------------------------------


def test_exchange_status_ok_when_required_boolean_fields_present():
    fetch = _fetch_returning(200, {"exchange_active": True, "trading_active": False})
    result = canary.check_exchange_status(fetch)
    assert result["ok"] is True
    assert result["endpoint"] == "exchange_status"


def test_exchange_status_fails_on_non_200():
    fetch = _fetch_returning(503, "service unavailable")
    result = canary.check_exchange_status(fetch)
    assert result["ok"] is False
    assert "503" in result["error"]


def test_exchange_status_fails_on_invalid_json():
    fetch = _fetch_returning(200, "not json{")
    result = canary.check_exchange_status(fetch)
    assert result["ok"] is False
    assert "json" in result["error"].lower()


def test_exchange_status_fails_when_response_is_not_an_object():
    fetch = _fetch_returning(200, [1, 2, 3])
    result = canary.check_exchange_status(fetch)
    assert result["ok"] is False
    assert "object" in result["error"].lower()


def test_exchange_status_fails_when_required_field_missing():
    fetch = _fetch_returning(200, {"exchange_active": True})
    result = canary.check_exchange_status(fetch)
    assert result["ok"] is False
    assert "trading_active" in result["error"]


def test_exchange_status_fails_when_required_field_wrong_type():
    fetch = _fetch_returning(200, {"exchange_active": "yes", "trading_active": False})
    result = canary.check_exchange_status(fetch)
    assert result["ok"] is False
    assert "exchange_active" in result["error"]


# --- check_markets --------------------------------------------------------


def test_markets_ok_when_first_item_has_required_fields_and_valid_status():
    fetch = _fetch_returning(200, {"markets": [{"ticker": "KXFOO-26", "status": "active"}], "cursor": ""})
    result = canary.check_markets(fetch)
    assert result["ok"] is True
    assert result["endpoint"] == "markets"


def test_markets_fails_on_non_200():
    fetch = _fetch_returning(500, "internal error")
    result = canary.check_markets(fetch)
    assert result["ok"] is False
    assert "500" in result["error"]


def test_markets_fails_when_markets_key_is_not_a_list():
    fetch = _fetch_returning(200, {"markets": "oops", "cursor": ""})
    result = canary.check_markets(fetch)
    assert result["ok"] is False
    assert "list" in result["error"].lower()


def test_markets_fails_when_markets_list_is_empty():
    fetch = _fetch_returning(200, {"markets": [], "cursor": ""})
    result = canary.check_markets(fetch)
    assert result["ok"] is False
    assert "empty" in result["error"].lower()


def test_markets_fails_when_first_item_missing_required_field():
    fetch = _fetch_returning(200, {"markets": [{"ticker": "KXFOO-26"}], "cursor": ""})
    result = canary.check_markets(fetch)
    assert result["ok"] is False
    assert "status" in result["error"]


def test_markets_fails_when_status_value_is_not_a_documented_enum_member():
    fetch = _fetch_returning(200, {"markets": [{"ticker": "KXFOO-26", "status": "not-a-real-status"}], "cursor": ""})
    result = canary.check_markets(fetch)
    assert result["ok"] is False
    assert "status" in result["error"].lower()


# --- run_canary / main -----------------------------------------------------


def test_run_canary_ok_true_when_both_checks_pass():
    def fetch(url, params=None):
        if "exchange/status" in url:
            return 200, json.dumps({"exchange_active": True, "trading_active": True})
        return 200, json.dumps({"markets": [{"ticker": "KXFOO-26", "status": "active"}], "cursor": ""})

    report = canary.run_canary(fetch)
    assert report["ok"] is True
    assert len(report["checks"]) == 2
    assert "checked_at" in report


def test_run_canary_ok_false_when_one_check_fails():
    def fetch(url, params=None):
        if "exchange/status" in url:
            return 503, "down"
        return 200, json.dumps({"markets": [{"ticker": "KXFOO-26", "status": "active"}], "cursor": ""})

    report = canary.run_canary(fetch)
    assert report["ok"] is False


def test_main_exits_zero_when_canary_passes(monkeypatch, tmp_path):
    def fake_fetch(url, params=None, timeout=10.0):
        if "exchange/status" in url:
            return 200, json.dumps({"exchange_active": True, "trading_active": True})
        return 200, json.dumps({"markets": [{"ticker": "KXFOO-26", "status": "active"}], "cursor": ""})

    monkeypatch.setattr(canary, "_http_fetch", fake_fetch)
    json_out = tmp_path / "report.json"

    exit_code = canary.main(["--json-out", str(json_out)])

    assert exit_code == 0
    report = json.loads(json_out.read_text())
    assert report["ok"] is True


def test_main_exits_nonzero_when_canary_fails(monkeypatch):
    def fake_fetch(url, params=None, timeout=10.0):
        return 500, "down"

    monkeypatch.setattr(canary, "_http_fetch", fake_fetch)

    assert canary.main([]) == 1


def test_main_never_touches_account_or_order_endpoints(monkeypatch):
    """The canary must only ever request the two public URLs it declares -
    a regression here would mean a future edit accidentally pulled in an
    authenticated/account-shaped call."""
    seen_urls = []

    def fake_fetch(url, params=None, timeout=10.0):
        seen_urls.append(url)
        if "exchange/status" in url:
            return 200, json.dumps({"exchange_active": True, "trading_active": True})
        return 200, json.dumps({"markets": [{"ticker": "KXFOO-26", "status": "active"}], "cursor": ""})

    monkeypatch.setattr(canary, "_http_fetch", fake_fetch)
    canary.main([])

    assert seen_urls == [canary._EXCHANGE_STATUS_URL, canary._MARKETS_URL]
    for url in seen_urls:
        for forbidden in ("order", "position", "balance", "fill", "portfolio"):
            assert forbidden not in url.lower()
