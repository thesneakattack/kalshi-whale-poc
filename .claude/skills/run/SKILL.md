---
name: run
description: This skill should be used when the user asks to "run the app", "start the app", "test this in the browser", "check if it's running", "restart the server", or wants to verify a change works in the live autotrade / kalshi-whale-poc app. Project-specific override of the generic run skill — this app runs under ddev (Docker), not bare uvicorn on the host.
---

# Run autotrade / kalshi-whale-poc

This project runs under `ddev`, building the FastAPI app from the repo's
`Dockerfile` as an additional service (see `.ddev/docker-compose.fastapi.yaml`).
Do not default to `python -m venv` / `pip install` / bare `uvicorn` — check
`ddev` first.

## Steps

1. Check whether it's already running: `ddev describe`. It usually already
   is — this is a long-running local dev environment, not something spun up
   fresh per task.

2. If not running: `ddev start`. First run builds the `fastapi` image from
   `Dockerfile`; later runs are fast.

3. For routine `.py` or `static/*.html` edits, no restart is needed. The
   `fastapi` service runs `uvicorn --reload`, which re-imports changed
   modules in-process within about 1-2 seconds of a save.

4. To see the app: open `https://kalshi-whale-poc.ddev.site` (or run
   `ddev launch`). For headless verification without a browser, hit
   `GET /api/state` directly — it returns bankroll, positions, the risk
   kill-switch state, and the signal/decision feeds as JSON, which is
   usually faster than screenshotting the dashboard to confirm a backend
   change worked.

5. To watch it work: `ddev logs -s fastapi` (add `-f` to follow). Look for
   `WatchFiles detected changes in '<file>'. Reloading...` to confirm a save
   was picked up, and for unhandled exceptions after a reload.

6. For one-off commands inside the container, use `ddev exec -s fastapi
   <cmd>` — working directory is `/app`, matching the repo root. Prefer this
   over raw `docker exec`, which requires guessing the container name.
   **Never write a scratch/diagnostic script into the repo tree itself**
   (including via `docker cp` to a container path — `/app` in `fastapi` is
   volume-mounted straight to the repo root). `uvicorn --reload` watches
   the *entire* repo, not just files under active editing, so dropping a
   file there triggers a full server restart (not just an in-process
   reimport), wiping in-memory state (`state["live_status_cache"]`,
   `latest_prices`, etc.) — directly disruptive if the dashboard is open in
   another tab. Prefer a stdin heredoc instead: `ddev exec -s fastapi
   python3 - <<EOF ... EOF`. If a file artifact is genuinely needed (e.g. a
   screenshot), write it to the container's `/tmp` or the scratchpad —
   never into `/app` first.

7. **Restart vs. reload — these are not the same thing.** `--reload` only
   re-imports Python modules inside the existing process; it does not
   exercise a real process restart. If verifying behavior that specifically
   depends on a full restart — most notably whether SQLite-backed state in
   `data/*.db` actually survives one (see `CLAUDE.md`'s persistence idiom) —
   use `ddev restart`. A file save alone will not exercise that path and
   will give a false sense of having tested it.

8. `data/*.db` files (`paper_broker.db`, `risk_state.db`, `signal_log.db`,
   `accounts.db`) are live while `ddev` is running — see `CLAUDE.md` before
   deleting or moving any of them to test something. Prefer `POST
   /api/reset` for clearing the paper account over touching
   `paper_broker.db` by hand.

## Browser-based UI verification

For real visual/DOM verification (not just reading `/api/state` JSON),
this project has a dedicated `selenium-chrome` ddev service
(`.ddev/docker-compose.selenium-chrome.yaml`, a real
`selenium/standalone-chromium` container, reachable in-network at
`selenium-chrome:4444`) — check `ddev describe` for it before concluding
no browser is available. `fastapi` already has the `selenium` Python
package installed, so drive it directly with a stdin heredoc (see step 6
above, not a script file):

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
options = Options()
options.add_argument('--headless=new')
options.add_argument('--ignore-certificate-errors')  # ddev's cert is self-signed
options.set_capability('acceptInsecureCerts', True)
driver = webdriver.Remote(command_executor='http://selenium-chrome:4444', options=options)
driver.get('https://kalshi-whale-poc.ddev.site/')
```

Known gotchas:
- The app auto-opens a first-run Help modal (`openHelp()`) unless
  `localStorage['whale-signal-seen-intro']` is already set — it intercepts
  clicks. Call `driver.execute_script("closeHelp();")` right after load.
- `driver.get_log('browser')` isn't available on this standalone setup —
  capture console errors manually via a `window.onerror`/`console.error`
  override injected with `execute_script` right after `driver.get()`.
- Give the page 5-6s after load and 2-3s after switching tabs before
  asserting on rendered content — `refresh()` is async, and checking too
  early looks like "nothing rendered" when it's just a timing fluke.
- `ui_samples/` in the repo root is the user's own reference material
  (real Kalshi screenshots for design comparison) — don't drop diagnostic
  screenshots there; use the scratchpad instead.

## Worked example

Verifying that paper-broker persistence survives a restart looked like:
`curl -sk https://kalshi-whale-poc.ddev.site/api/state` to capture the
bankroll/positions before, wait for the trading loop to place a trade,
`ddev restart`, then `curl` `/api/state` again and diff the two — confirming
the exact same position (ticker, entry price, timestamp) was still present
after a real process restart, not just a `--reload`.
