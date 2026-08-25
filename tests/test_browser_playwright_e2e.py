"""
Browser tests for the things Selenium structurally cannot express.

This file deliberately does NOT duplicate tests/test_browser_e2e.py. That
file owns the Selenium smoke path (dashboard loads, tabs switch, the built
bundle is really the one running, System Health renders) and stays as-is.
This one owns cases that need the browser's *network layer* under test
control, which WebDriver has no protocol for:

  - forcing an API to fail on demand (status, body, or transport), so the
    "a displayed value must match its label" rule in CLAUDE.md becomes an
    executable guard instead of prose. Both bugs that rule documents shipped
    unnoticed, and neither was reachable from a test that can only observe
    whatever the backend happened to return.
  - asserting on the WebSocket at /api/ws.

Why this matters concretely: test_browser_e2e.py's own
test_failed_api_action_is_surfaced_as_failure_not_false_success can only
reach fetchJSON's 4xx path because /api/confidence-calibration/apply
*happens* to 400 in an isolated harness whose signal_log is empty. Its
docstring calls that deterministic, and today it is - but it is deterministic
by accident of fixture state, not by construction. Seed that DB and the same
test silently starts exercising the success path instead, against a config
it would then really mutate. Route interception removes that coupling: the
400 here is forced, so it holds regardless of what the harness DB contains,
and the request never reaches the backend at all.

Opt-in only (RUN_PLAYWRIGHT_E2E=1), same convention as RUN_BROWSER_E2E - an
ordinary `pytest` run must never try to launch a browser.

Environment:
  E2E_BASE_URL          base URL to drive the browser against (default
                        http://127.0.0.1:8765, same default and same
                        tests/support/e2e_server.py harness the Selenium
                        file uses)
  RUN_PLAYWRIGHT_E2E=1  required, or every test here skips

See .woodpecker/quality-browser-e2e.yml's playwright-e2e step for how CI
starts the server before this runs.
"""
import os
import re
from urllib.parse import urlparse

import pytest

E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PLAYWRIGHT_E2E", "").strip() != "1",
    reason="browser test - opt in with RUN_PLAYWRIGHT_E2E=1 (see this module's docstring)",
)

# frontend/src/js/main.js auto-opens the Help modal as a backdrop overlay the
# first time a browser has never set this key, and every fresh context
# qualifies. The Selenium fixture burns a throwaway navigation to prime it;
# an init script is the same fix one step earlier - it runs before page
# scripts on every navigation in the context, so no test here ever sees the
# modal.
_SUPPRESS_INTRO = "try { localStorage.setItem('whale-signal-seen-intro', '1'); } catch (e) {}"

# fmt() in frontend/src/js/shared-utils.js renders null/undefined as an em
# dash and anything real as a USD currency string, so "did a number reach
# this element" is exactly "does it contain a digit next to a dollar sign".
_CURRENCY = re.compile(r"\$\s*[\d,]")


@pytest.fixture()
def page(context):
    """A page with the first-run modal already suppressed.

    Function-scoped on purpose, unlike test_browser_e2e.py's module-scoped
    driver: every test here installs its own route interception, and route
    handlers registered on a shared page would leak across tests in file
    order - which is precisely the kind of order-dependent coupling this
    repo has already been bitten by.
    """
    context.add_init_script(_SUPPRESS_INTRO)
    pg = context.new_page()
    yield pg
    pg.close()


def _bankroll_text(page) -> str:
    return page.locator("#bankroll").inner_text()


# ---- the control, so the failure assertions below are not vacuous --------

def test_bankroll_really_does_populate_when_the_api_is_healthy(page):
    """Without this, every "must not show a number" test below would pass on
    a page that simply never renders anything at all."""
    page.goto(E2E_BASE_URL + "/")
    page.wait_for_function(
        "() => { const el = document.getElementById('bankroll');"
        " return el && el.textContent.trim() !== '' && el.textContent.trim() !== '\\u2014'; }",
        timeout=20000,
    )
    assert _CURRENCY.search(_bankroll_text(page)), (
        f"control failed: a healthy /api/state did not put a currency figure in #bankroll "
        f"(got {_bankroll_text(page)!r}) - the negative assertions in this file would be vacuous"
    )


# ---- forced transport/status/body failures ------------------------------

@pytest.mark.parametrize(
    "label,fulfil",
    [
        ("500", {"status": 500, "content_type": "application/json", "body": '{"detail":"forced failure"}'}),
        ("503 empty body", {"status": 503, "content_type": "application/json", "body": ""}),
        ("200 with malformed JSON", {"status": 200, "content_type": "application/json", "body": "{not json at all"}),
    ],
)
def test_a_failing_state_endpoint_never_renders_a_fabricated_bankroll(page, label, fulfil):
    """CLAUDE.md's "a displayed value must match its label" rule, as a test.

    An em dash, a blank, or an explicit error are all acceptable. A dollar
    figure is not: there is no bankroll to report when the endpoint that
    reports it failed, so any number on screen is invented.

    The malformed-JSON case matters separately from the status cases -
    fetchJSON only guards on res.ok, so a 200 whose body is not JSON takes a
    completely different path (res.json() rejects) than a 500 does.
    """
    page.route("**/api/state*", lambda route: route.fulfill(**fulfil))
    page.goto(E2E_BASE_URL + "/")
    page.wait_for_timeout(3000)

    text = _bankroll_text(page)
    assert not _CURRENCY.search(text), (
        f"/api/state failed ({label}) but #bankroll still showed a currency figure {text!r} - "
        f"a fabricated number is worse than no number"
    )


def test_an_aborted_state_request_never_renders_a_fabricated_bankroll(page):
    """Transport-level failure, not an HTTP status - the case fetchJSON's own
    comment says fetch() *does* reject on, and therefore the one path that
    was never broken. Worth pinning precisely because it is the branch the
    2026-08-17 bug did not affect: if a refactor ever unifies the two, this
    catches the regression from the other side."""
    page.route("**/api/state*", lambda route: route.abort())
    page.goto(E2E_BASE_URL + "/")
    page.wait_for_timeout(3000)

    text = _bankroll_text(page)
    assert not _CURRENCY.search(text), (
        f"/api/state aborted at the transport layer but #bankroll showed {text!r}"
    )


# ---- fetchJSON's 4xx path, forced rather than inferred -------------------

def _click_apply(page):
    """Drive applyCalibrationSuggestion() against a throwaway button.

    Same approach as the Selenium file: the real button only exists once a
    calibration report has rendered suggested weights, which needs seeded
    history this harness deliberately does not have.
    """
    page.evaluate(
        "() => { const b = document.createElement('button');"
        " b.id = 'pw-apply-btn'; document.body.appendChild(b);"
        " window.applyCalibrationSuggestion(b); }"
    )


def test_a_forced_4xx_apply_surfaces_as_failure(page):
    """Regression guard for the shipped fetchJSON bug (shared-utils.js's own
    comment, 2026-08-17): a 400 read as success, and a request that outright
    failed rendered "Applied ✓".

    Forced, so unlike the Selenium equivalent this does not depend on the
    harness DB being empty - and the request never reaches the backend, so
    no config is touched even in principle.
    """
    page.route(
        "**/api/confidence-calibration/apply",
        lambda route: route.fulfill(
            status=400, content_type="application/json", body='{"detail":"nothing to apply"}',
        ),
    )
    page.goto(E2E_BASE_URL + "/")
    _click_apply(page)

    page.wait_for_function(
        "() => { const b = document.getElementById('pw-apply-btn');"
        " return b && b.textContent.trim() !== ''; }",
        timeout=15000,
    )
    assert page.locator("#pw-apply-btn").inner_text() == "Apply failed — retry"


def test_a_forced_2xx_apply_reports_success(page):
    """The other half of the same guard, and the reason the 4xx test above
    means anything: prove the button can reach BOTH outcomes.

    Without this, "Apply failed — retry" could be what the button always
    says - a handler wired to fail unconditionally would pass the 4xx test
    perfectly. Interception keeps this safe: the POST is answered here and
    never reaches /api/confidence-calibration/apply, so the real config is
    not written even though this exercises the success path.
    """
    page.route(
        "**/api/confidence-calibration/apply",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"status":"ok"}',
        ),
    )
    page.goto(E2E_BASE_URL + "/")
    _click_apply(page)

    page.wait_for_function(
        "() => { const b = document.getElementById('pw-apply-btn');"
        " return b && b.textContent.trim() !== ''; }",
        timeout=15000,
    )
    assert page.locator("#pw-apply-btn").inner_text() == "Applied ✓"


# ---- the /api/ws websocket ----------------------------------------------

def test_the_dashboard_opens_and_holds_a_websocket_to_api_ws(page):
    """connectWebSocket() (frontend/src/js/polling-and-websocket.js) derives
    its URL from location.href and flips the scheme to ws/wss. A broken
    route, a wrong scheme, or a handshake the server rejects all show up the
    same way in the UI - as nothing, silently, because its close handler
    just schedules a reconnect with backoff.

    Asserting the socket both OPENS and is still open a moment later is what
    separates "the endpoint exists" from "the connection actually holds": a
    server that accepts and immediately drops would satisfy the first and
    fail the second, while reconnect backoff hides it from the user either
    way.

    Deliberately does not assert that frames arrive. tests/support/
    e2e_server.py sets state["running"] = False so the trading loop never
    runs, and /api/ws only ever emits what ws_manager broadcasts - so in
    this harness silence is correct, not a defect.
    """
    sockets = []
    page.on("websocket", lambda ws: sockets.append(ws))

    page.goto(E2E_BASE_URL + "/")
    page.wait_for_timeout(4000)

    # Exact path, not a substring. Found by fault injection while writing
    # this: pointing the client at /api/ws-BROKEN still satisfied
    # `"/api/ws" in url`, so the rename slipped past this assertion and was
    # only caught further down by the stay-open check. A wrong-but-prefixed
    # path is exactly the typo this test exists to catch, so it should fail
    # here, with a message naming the path.
    def _path(url: str) -> str:
        return urlparse(url).path

    ws_paths = [_path(ws.url) for ws in sockets]
    assert "/api/ws" in ws_paths, (
        f"dashboard never opened a websocket to /api/ws (opened {ws_paths!r})"
    )

    held = [ws for ws in sockets if _path(ws.url) == "/api/ws" and not ws.is_closed()]
    assert held, (
        f"a websocket to /api/ws was opened but did not stay open - all of {ws_paths!r} "
        f"had closed within the observation window, which the frontend hides behind "
        f"its own reconnect backoff"
    )
