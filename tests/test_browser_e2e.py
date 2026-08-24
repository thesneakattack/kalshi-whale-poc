"""
Browser smoke tests (Quality Control Plane Task 8) - drives a real headless
Chrome against tests/support/e2e_server.py's uvicorn process to exercise the
true end-to-end path (built JS bundle -> fetchJSON -> FastAPI route) that
tests/test_e2e_terminal_static_and_api.py deliberately skips outside DDEV.

Opt-in only (RUN_BROWSER_E2E=1) - an ordinary `pytest` run must never try to
launch Chrome. See:
  - .woodpecker/quality-browser-e2e.yml (authoritative CI)
  - .github/workflows/quality.yml's browser-e2e job (manual fallback)
for how the server process and Chrome get started before this file runs.

Environment:
  E2E_BASE_URL       base URL to drive the browser against
                      (default http://127.0.0.1:8765, matches both CI jobs
                      and `ddev exec -s fastapi` local verification)
  SELENIUM_REMOTE_URL  optional Selenium server URL (e.g.
                      http://selenium-chrome:4444/wd/hub) - set this for
                      local verification via the ddev-selenium-standalone-
                      chrome add-on when the fastapi container itself has no
                      Chrome; leave unset in CI, where Chrome runs on the
                      same host as pytest
  CHROME_BIN          optional explicit Chrome/Chromium binary path
  CHROMEDRIVER_PATH   optional explicit chromedriver path - set both this
                      and CHROME_BIN in CI to avoid depending on Selenium
                      Manager's own network-fetched driver
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_E2E") != "1", reason="browser e2e opt-in (RUN_BROWSER_E2E=1)",
)

E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")

# Kept empty deliberately (design spec, "Browser E2E harness") - any
# captured console error is a real regression until proven otherwise, not
# something to allowlist away pre-emptively.
CONSOLE_ERROR_ALLOWLIST: tuple[str, ...] = ()

# Selenium's WebDriver-protocol get_log("browser") only exists on the
# Chrome-specific *local* driver class (selenium.webdriver.chrome.webdriver.
# WebDriver) - webdriver.Remote() returns the generic base class, which
# doesn't have it (confirmed live: AttributeError against the ddev
# selenium-chrome grid used for local verification here). It would also
# pick up browser-level noise unrelated to the app (e.g. a 404 favicon
# request surfaces as its own SEVERE entry). Tracking the app's actual
# error-signaling calls directly - console.error (shared-utils.js's
# fetchJSON catch blocks, polling-and-websocket.js's WS handlers) and
# uncaught exceptions - is both more portable (works identically local or
# remote) and more precise (only the app's own real error paths, not
# browser plumbing). Installed fresh after every navigation since window
# state doesn't survive a reload.
_CONSOLE_TRACKER_JS = """
window.__e2eConsoleErrors = [];
var __e2eOrigConsoleError = console.error.bind(console);
console.error = function() {
  window.__e2eConsoleErrors.push(Array.prototype.slice.call(arguments).map(String).join(' '));
  __e2eOrigConsoleError.apply(console, arguments);
};
window.addEventListener('error', function(e) {
  window.__e2eConsoleErrors.push('Uncaught: ' + (e.message || e));
});
"""


def _install_console_tracker(driver) -> None:
    driver.execute_script(_CONSOLE_TRACKER_JS)


def _console_errors(driver) -> list[str]:
    errors = driver.execute_script("return window.__e2eConsoleErrors || [];")
    return [e for e in errors if not any(allowed in e for allowed in CONSOLE_ERROR_ALLOWLIST)]


TAB_VIEW_IDS = (
    ("tab-btn-portfolio", "view-portfolio"),
    ("tab-btn-markets", "view-markets"),
    ("tab-btn-whale", "view-whale"),
    ("tab-btn-terminal", "view-terminal"),
    ("tab-btn-history", "view-history"),
    ("tab-btn-config", "view-config"),
)


@pytest.fixture(scope="module")
def driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,1024")
    # CI containers run Chrome as root with no user namespace, which trips
    # Chrome's sandbox and crashes the process before a session is ever
    # created (confirmed live: SessionNotCreatedException, "Chrome instance
    # exited", against a plain `apt-get install chromium chromium-driver`
    # python:3.13-slim container run as root) - standard, widely-documented
    # requirement for headless Chrome in an unprivileged container, harmless
    # against a real user account (local dev, the ddev selenium-chrome grid).
    options.add_argument("--no-sandbox")
    chrome_bin = os.environ.get("CHROME_BIN", "").strip()
    if chrome_bin:
        options.binary_location = chrome_bin

    remote_url = os.environ.get("SELENIUM_REMOTE_URL", "").strip()
    if remote_url:
        drv = webdriver.Remote(command_executor=remote_url, options=options)
    else:
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "").strip()
        service = Service(executable_path=chromedriver_path) if chromedriver_path else None
        drv = webdriver.Chrome(options=options, service=service)

    # First-run walkthrough (frontend/src/js/main.js) auto-opens the Help
    # modal as a backdrop overlay the first time a browser has never set
    # 'whale-signal-seen-intro' in localStorage - every fresh headless
    # profile qualifies. Prime the flag with one throwaway load (localStorage
    # is per-origin, so this must happen after a real navigation to the app)
    # so every actual test's own navigation starts with the modal already
    # suppressed.
    drv.get(E2E_BASE_URL + "/")
    drv.execute_script("window.localStorage.setItem('whale-signal-seen-intro', '1');")
    yield drv
    drv.quit()


def test_dashboard_loads_and_tabs_switch(driver):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(E2E_BASE_URL + "/")
    _install_console_tracker(driver)
    assert "Whale Signal" in driver.title

    # /api/state populates the page: the header bankroll figure starts as
    # the literal placeholder '—' (static/index.html) until refresh()'s
    # first successful fetch replaces it.
    WebDriverWait(driver, 10).until(
        lambda d: d.find_element(By.ID, "bankroll").text != "—"
    )

    for tab_id, view_id in TAB_VIEW_IDS:
        driver.find_element(By.ID, tab_id).click()
        WebDriverWait(driver, 5).until(
            lambda d, vid=view_id: "active" in d.find_element(By.ID, vid).get_attribute("class").split()
        )
        assert "active" in driver.find_element(By.ID, tab_id).get_attribute("class").split()

    errors = _console_errors(driver)
    assert not errors, f"unexpected browser console errors: {errors}"


def test_built_bundle_is_actually_loaded(driver):
    driver.get(E2E_BASE_URL + "/")
    assert 'src="js/dashboard.bundle.js"' in driver.page_source
    # showView is one of main.js's window-exposed globals (see its own
    # "Exposed for inline HTML event handlers" comment) - only present if
    # the built bundle actually executed, not merely referenced by <script>.
    assert driver.execute_script("return typeof window.showView;") == "function"


def test_failed_api_action_is_surfaced_as_failure_not_false_success(driver):
    """Regression coverage for the historical fetchJSON bug (shared-utils.js's
    own comment, 2026-08-17): fetch() only rejects on a network failure, so a
    4xx/5xx used to read as success everywhere fetchJSON was awaited.

    /api/confidence-calibration/apply always 400s in this harness's isolated,
    freshly-created signal_log DB (services/whale_calibration/routes.py:
    resolved_signals_with_factors() is empty, well under
    confidence_calibration.min_resolved_signals) - deterministic, and it
    never mutates config unless the (never-reached, in this state) success
    path runs, so nothing safety-relevant is touched.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(E2E_BASE_URL + "/")
    driver.execute_script(
        "window.__e2eApplyBtn = document.createElement('button');"
        "window.__e2eApplyBtn.id = 'e2e-calibration-apply-btn';"
        "document.body.appendChild(window.__e2eApplyBtn);"
        "window.applyCalibrationSuggestion(window.__e2eApplyBtn);"
    )
    WebDriverWait(driver, 10).until(
        lambda d: d.find_element(By.ID, "e2e-calibration-apply-btn").text != ""
    )
    btn_text = driver.find_element(By.ID, "e2e-calibration-apply-btn").text
    assert btn_text == "Apply failed — retry", (
        f"fetchJSON's 4xx path did not surface as a failure in the UI: button read {btn_text!r}"
    )


def test_system_health_panel_renders_a_status_with_no_console_error(driver):
    """QCP Task 18 - the Terminal tab's System Health panel
    (frontend/src/js/system-health.js), backed by GET /api/quality/summary.
    This harness's state["running"] = False means the trading loop never
    ticks, so the real content is minimal (no faults/alerts/findings
    accumulate) - this only asserts what the plan's own Step 5 asks for: a
    real status renders (not the "Loading…" placeholder, not the "system
    health unavailable" fetch-failure fallback) and no severe console error
    occurs, not any specific count."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(E2E_BASE_URL + "/")
    _install_console_tracker(driver)

    driver.find_element(By.ID, "tab-btn-terminal").click()
    WebDriverWait(driver, 10).until(
        lambda d: "active" in d.find_element(By.ID, "view-terminal").get_attribute("class").split()
    )
    WebDriverWait(driver, 10).until(
        lambda d: "Loading" not in d.find_element(By.ID, "system-health-summary").text
    )

    status_el = driver.find_element(By.CSS_SELECTOR, "#system-health-summary .sh-status")
    assert status_el.get_attribute("class").split()[-1] in ("ok", "warning", "error", "unknown")
    assert status_el.text.strip() != ""
    assert "unavailable" not in driver.find_element(By.ID, "system-health-summary").text

    errors = _console_errors(driver)
    assert not errors, f"unexpected browser console errors: {errors}"
