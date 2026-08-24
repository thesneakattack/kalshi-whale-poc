"""
CI-only ASGI app for the browser E2E harness (Quality Control Plane Task 8).

`tests/test_e2e_terminal_static_and_api.py` already proves the API works in
isolation via `TestClient`, but it deliberately skips real static serving
outside DDEV because only DDEV's `web` container hostname serves
`static/*.html` - a bare CI runner has no such hostname. This module serves
both the real API and the real static frontend from a single process, driven
by an actual `uvicorn` (see the CI workflows that invoke this module as
`tests.support.e2e_server:app`), so a headless browser can exercise the true
end-to-end path: HTML -> built JS bundle -> fetchJSON -> FastAPI route.

Two things must happen before `main` is ever imported:
1. runtime isolation (same helper every other test file uses) so this can
   never touch a live repository data/*.db, and
2. nothing here may require real Kalshi credentials or real network access
   (Quality Control Plane design spec, "Browser E2E harness" section) - see
   the state["running"] note below for how that's satisfied.
"""
from pathlib import Path

from tests.support.runtime_isolation import install_runtime_isolation

install_runtime_isolation()

from fastapi.staticfiles import StaticFiles  # noqa: E402

from main import app, state  # noqa: E402

# main.py's lifespan() unconditionally schedules trading_loop() as a
# supervised background task, and trading_loop() polls real Kalshi market
# data on every tick whenever state["running"] is True - regardless of
# whether any credentials are configured, since market listings are public.
# trade_stream/index_stream's own websocket connections already stay off
# with no KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH in the CI environment
# (KalshiTradeWebSocketClient.enabled requires both), and every other
# background scan (catalog scan, discovery cache, backup, alerting, event
# schedule) is itself only ever triggered from inside trading_loop's own
# tick body - so this one flag, already the app's own documented pause
# toggle (POST /api/pause), is sufficient to guarantee zero real network
# calls for the whole harness without touching or mocking the Kalshi client.
state["running"] = False

# Mounted last, after `main` has finished importing and every production
# route (routers + the @app.get/@app.post/@app.websocket decorators further
# down in main.py) is already registered - so /api/* and /auth/* still
# resolve before this catch-all, exactly like DDEV's nginx (`web`) reverse-
# proxying /api/ + /auth/ to `fastapi` and serving everything else itself
# from the same `static/` docroot (.ddev/nginx/kalshi-proxy.conf).
_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="e2e-static")
