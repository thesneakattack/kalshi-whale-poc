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

## Worked example

Verifying that paper-broker persistence survives a restart looked like:
`curl -sk https://kalshi-whale-poc.ddev.site/api/state` to capture the
bankroll/positions before, wait for the trading loop to place a trade,
`ddev restart`, then `curl` `/api/state` again and diff the two — confirming
the exact same position (ticker, entry price, timestamp) was still present
after a real process restart, not just a `--reload`.
