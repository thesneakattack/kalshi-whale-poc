import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()  # reads .env if present; every var is optional, see .env.example

from services import accounts_store
from services import auth as auth_service
from services import signal_log
from services.config_store import config_store
from services.http_client import close_client
from services.kalshi_client import KalshiClient
from services.kalshi_account_client import KalshiAccountClient
from services.whale_simulator import WhaleSimulator
from services.whalewatchers import PROVIDERS, get_active_provider
from services.paper_broker import PaperBroker
from services.risk_manager import RiskManager
from services.strategy_engine import FollowTheWhaleStrategy

# ---- shared runtime state -------------------------------------------------

cfg = config_store.get()
broker = PaperBroker(starting_bankroll=cfg["risk"]["starting_bankroll"])
risk = RiskManager(
    starting_bankroll=cfg["risk"]["starting_bankroll"],
    max_daily_loss_pct=cfg["risk"]["max_daily_loss_pct"],
    kill_switch_enabled=cfg["risk"]["kill_switch_enabled"],
)
strategy = FollowTheWhaleStrategy(broker, risk)
whale_sim = WhaleSimulator(
    size_range=tuple(cfg["whale_signal"]["whale_size_range"]),
    bias=cfg["whale_signal"]["bias"],
)
whale_provider = get_active_provider()  # only active if its own env vars are set — see services/whalewatchers/
account = KalshiAccountClient(
    cfg["kalshi"]["base_url"],
    cfg["kalshi"]["request_timeout_sec"],
    cfg["kalshi_account"]["trading_enabled"],
)  # real account — only active if KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH are set in .env

state = {
    "running": True,
    "markets": [],
    "latest_prices": {},
    "market_titles": {},
    "series_track_record": {},
    "signal_feed": [],   # most recent first
    "decision_feed": [],
    "stats": {"signals_seen": 0, "trades_placed": 0, "skipped": 0},
    "equity_history": [],  # [{"t": unix_ts, "equity": float}, ...], capped, for the Portfolio view's chart
    "real_balance_history": [],  # same shape, for the real-account toggle — only grows if a real account is connected
    "last_poll": None,
    "error": None,
    "whale_source": whale_provider.name if whale_provider.enabled else "simulated",
    "account": {
        "connected": account.enabled, "balance": None, "positions": None, "fills": None,
        "error": None, "trading_enabled": account.trading_enabled,
    },
}


# The dashboard only ever reads m.ticker and m.volume_24h_fp off a raw market
# object — title and price are already looked up separately via market_titles/
# latest_prices. Kalshi's full market object carries 40+ fields (rules text,
# combo-leg lists, ...); trimming to what's actually used cuts the /api/state
# payload for 8 markets from ~34KB to well under 1KB.
_MARKET_FIELDS = ("ticker", "volume_24h_fp")


def _slim_market(m: dict) -> dict:
    return {k: m.get(k) for k in _MARKET_FIELDS}


async def _fetch_markets(client: KalshiClient, cfg: dict) -> list[dict]:
    watchlist = cfg["kalshi"]["markets_watchlist"]
    if watchlist:
        # One ticker at a time, sequentially, meant 8 round trips paid back-to-back —
        # they don't depend on each other, so fetch them concurrently instead.
        results = await asyncio.gather(
            *(client.get_market(ticker) for ticker in watchlist), return_exceptions=True
        )
        return [m for m in results if isinstance(m, dict)]
    return await client.get_top_volume_markets(cfg["kalshi"]["watchlist_size"])


async def _fetch_account_snapshot(cfg: dict) -> dict:
    account.trading_enabled = cfg["kalshi_account"]["trading_enabled"]
    if not account.enabled:
        return {
            "connected": False, "balance": None, "positions": None, "fills": None,
            "error": account.status["error"], "trading_enabled": account.trading_enabled,
        }
    try:
        # balance, positions, and fills are independent reads — fetch all three
        # at once instead of one after another.
        balance, positions, fills = await asyncio.gather(
            account.get_balance(), account.get_positions(), account.get_fills(limit=25)
        )
        return {
            "connected": True, "balance": balance, "positions": positions, "fills": fills,
            "error": None, "trading_enabled": account.trading_enabled,
        }
    except Exception as e:
        return {
            "connected": True, "balance": None, "positions": None, "fills": None,
            "error": str(e), "trading_enabled": account.trading_enabled,
        }


async def _check_signal_resolutions(client: KalshiClient):
    """Pick a small batch of old-enough unresolved logged signals and see if
    their markets have settled yet. Small batch + shared connection-pooled
    client keeps this cheap even though it runs every poll tick."""
    for item in signal_log.unresolved_batch(limit=3, older_than_sec=600):
        try:
            market = await client.get_market(item["ticker"])
            result = (market.get("result") or "").strip().lower()
            if result in ("yes", "no"):
                signal_log.mark_resolved(item["id"], correct=(result == item["side"]))
        except Exception:
            continue  # market may be gone/renamed — leave unresolved, retry next time


async def trading_loop():
    while True:
        cfg = config_store.get()
        if not state["running"]:
            await asyncio.sleep(1)
            continue
        try:
            client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])

            # Market data, account data, and resolution-checking don't depend on
            # each other — fetch/run all three concurrently.
            markets, account_snapshot, _ = await asyncio.gather(
                _fetch_markets(client, cfg), _fetch_account_snapshot(cfg), _check_signal_resolutions(client)
            )
            state["account"] = account_snapshot

            state["markets"] = [_slim_market(m) for m in markets]
            # yes_bid_dollars is Kalshi's real field (already a 0-1 probability) —
            # "yes_bid" (cents) doesn't exist on the live API and silently
            # defaulted every price to 0.5.
            state["latest_prices"] = {
                m["ticker"]: float(m.get("yes_bid_dollars") or 0.5) for m in markets if m.get("ticker")
            }
            # Human-readable label for a ticker — whale signals/decisions only carry
            # the raw ticker string, so the dashboard looks this up to show something
            # a person can actually read instead of e.g. "KXMVESPORTS...-FC34E0243A1".
            # Accumulates (doesn't overwrite) so a signal from a market that has since
            # rotated out of the top-volume watchlist still resolves to its title.
            state["market_titles"].update({
                m["ticker"]: (m.get("title") or m.get("yes_sub_title") or m["ticker"])
                for m in markets if m.get("ticker")
            })
            if len(state["market_titles"]) > 300:  # bound unbounded growth over a long-running process
                state["market_titles"] = dict(list(state["market_titles"].items())[-300:])
            # Computed once per poll tick (not per /api/state request, which is polled
            # more often) since it's the same until the next tick anyway.
            state["series_track_record"] = {
                m["ticker"]: signal_log.series_stats(m["ticker"], days=30) for m in markets if m.get("ticker")
            }
            state["last_poll"] = time.time()
            state["error"] = None
            state["equity_history"].append({"t": state["last_poll"], "equity": broker.equity(state["latest_prices"])})
            state["equity_history"] = state["equity_history"][-200:]

            # Real balance's exact field name is unverified against Kalshi's current
            # docs (see /status Known Limitations) — try the common shape, skip the
            # sample entirely rather than guess wrong if it doesn't match.
            real_balance = (account_snapshot.get("balance") or {}) if account_snapshot.get("connected") else {}
            real_balance_value = real_balance.get("balance") if isinstance(real_balance, dict) else None
            if real_balance_value is not None:
                try:
                    state["real_balance_history"].append({"t": state["last_poll"], "balance": float(real_balance_value)})
                    state["real_balance_history"] = state["real_balance_history"][-200:]
                except (TypeError, ValueError):
                    pass

            new_signals = []
            if whale_provider.enabled:
                try:
                    new_signals = await whale_provider.fetch_signals()
                    state["whale_source"] = whale_provider.name
                except Exception as e:
                    # Provider hiccuped — fall back to the simulator for this
                    # tick rather than stalling the whole loop.
                    state["error"] = f"whale-watcher fetch failed, using simulator: {e}"
                    whale_sim.size_range = tuple(cfg["whale_signal"]["whale_size_range"])
                    whale_sim.bias = cfg["whale_signal"]["bias"]
                    sig = whale_sim.maybe_generate(markets, cfg["whale_signal"]["signal_frequency_sec"])
                    new_signals = [sig] if sig else []
                    state["whale_source"] = f"simulated ({whale_provider.name} fallback)"
            else:
                whale_sim.size_range = tuple(cfg["whale_signal"]["whale_size_range"])
                whale_sim.bias = cfg["whale_signal"]["bias"]
                sig = whale_sim.maybe_generate(markets, cfg["whale_signal"]["signal_frequency_sec"])
                new_signals = [sig] if sig else []
                state["whale_source"] = "simulated"

            for signal in new_signals:
                state["signal_feed"].insert(0, signal.to_dict())
                state["signal_feed"] = state["signal_feed"][:50]
                state["stats"]["signals_seen"] += 1
                signal_log.log_signal(
                    signal.ticker, signal.side, signal.size, signal.confidence,
                    state["whale_source"], signal.timestamp,
                )

                decision = strategy.evaluate(signal, cfg)
                state["decision_feed"].insert(0, decision)
                state["decision_feed"] = state["decision_feed"][:50]
                state["stats"]["trades_placed" if decision["action"] == "trade" else "skipped"] += 1

        except Exception as e:
            state["error"] = str(e)

        await asyncio.sleep(cfg["kalshi"]["poll_interval_sec"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(trading_loop())
    yield
    task.cancel()
    await close_client()


app = FastAPI(title="Kalshi Whale-Signal Paper Trader", lifespan=lifespan)

# allow_origins=["*"] was fine while this only ever ran on localhost/DDEV, but
# doesn't hold once it's reachable from anywhere else — a same-origin browser
# tab never needs CORS at all (the frontend and API are served from this same
# app), so this only matters for cross-origin callers, which by default means
# just the DDEV hostname and common local dev ports. Override with a
# comma-separated ALLOWED_ORIGINS in .env for any other real deployment.
_default_origins = [
    "https://kalshi-whale-poc.ddev.site",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
allowed_origins = (
    [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
    if _allowed_origins_env
    else _default_origins
)
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["*"], allow_headers=["*"])

# AuthMiddleware added first (inner) so SessionMiddleware — added second, thus
# outermost — populates request.session before AuthMiddleware ever reads it.
# Both are no-ops end-to-end until GOOGLE_CLIENT_ID/SECRET + APP_SECRET_KEY
# are set in .env; see services/auth.py.
app.add_middleware(auth_service.AuthMiddleware)
if auth_service.auth_configured():
    app.add_middleware(SessionMiddleware, secret_key=auth_service.session_secret_key())


# ---- auth --------------------------------------------------------------

@app.get("/api/session")
async def get_session(request: Request):
    return {
        "auth_configured": auth_service.auth_configured(),
        "user": request.session.get("user") if auth_service.auth_configured() else None,
    }


@app.get("/auth/login")
async def auth_login(request: Request):
    if not auth_service.auth_configured():
        return RedirectResponse(url="/")
    redirect_uri = str(request.url_for("auth_callback"))
    state_token = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state_token
    return RedirectResponse(url=auth_service.build_login_url(redirect_uri, state_token))


@app.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(url=f"/login?error={error}")
    if not code or state != request.session.get("oauth_state"):
        return RedirectResponse(url="/login?error=state_mismatch")
    redirect_uri = str(request.url_for("auth_callback"))
    try:
        tokens = await auth_service.exchange_code(code, redirect_uri)
        claims = await auth_service.verify_id_token(tokens["id_token"])
    except Exception as e:
        return RedirectResponse(url=f"/login?error={type(e).__name__}")
    request.session["user"] = {
        "email": claims.get("email"),
        "name": claims.get("name"),
        "picture": claims.get("picture"),
    }
    return RedirectResponse(url="/")


@app.post("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---- API -------------------------------------------------------------------

class ConfigPatch(BaseModel):
    patch: dict


# kalshi_account.trading_enabled is the one config value that turns on real
# order placement — it doesn't go through the generic config patch endpoint
# below at all, on purpose. See EnableTradingBody/enable_trading for the only
# path that can flip it on, which requires a real connected account and an
# exact-match typed confirmation phrase, not just a checkbox.
TRADING_CONFIRMATION_PHRASE = "ENABLE REAL TRADING"


class EnableTradingBody(BaseModel):
    confirmation_phrase: str


@app.get("/api/state")
async def get_state():
    return {
        "running": state["running"],
        "markets": state["markets"],
        "market_titles": state["market_titles"],
        "latest_prices": state["latest_prices"],
        "signal_feed": state["signal_feed"],
        "decision_feed": state["decision_feed"],
        "stats": state["stats"],
        "equity_history": state["equity_history"],
        "real_balance_history": state["real_balance_history"],
        "whale_track_record": signal_log.stats(days=30),
        "series_track_record": state["series_track_record"],
        "last_poll": state["last_poll"],
        "error": state["error"],
        "whale_source": state["whale_source"],
        "risk": {"halted": risk.halted, "halt_reason": risk.halt_reason},
        "broker": broker.state(state["latest_prices"]),
        "account": state["account"],
    }


@app.get("/api/config")
async def get_config():
    return config_store.get()


@app.post("/api/config")
async def update_config(body: ConfigPatch):
    if "kalshi_account" in body.patch and "trading_enabled" in (body.patch.get("kalshi_account") or {}):
        raise HTTPException(
            status_code=400,
            detail=(
                "kalshi_account.trading_enabled can't be changed through /api/config — "
                "use POST /api/trading/enable (requires a connected account and a typed "
                "confirmation phrase) or POST /api/trading/disable."
            ),
        )
    new_cfg = config_store.update(body.patch)
    return new_cfg


@app.post("/api/trading/enable")
async def enable_trading(body: EnableTradingBody):
    if not account.enabled:
        raise HTTPException(
            status_code=400,
            detail="No real Kalshi account is connected — set KALSHI_API_KEY_ID and "
                   "KALSHI_PRIVATE_KEY_PATH in .env first.",
        )
    if body.confirmation_phrase != TRADING_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{TRADING_CONFIRMATION_PHRASE}"',
        )
    config_store.update({"kalshi_account": {"trading_enabled": True}})
    account.trading_enabled = True  # take effect immediately, not on the next poll tick
    return {"trading_enabled": True}


@app.post("/api/trading/disable")
async def disable_trading():
    # Always allowed, no confirmation needed — turning real trading back off
    # is never the dangerous direction.
    config_store.update({"kalshi_account": {"trading_enabled": False}})
    account.trading_enabled = False
    return {"trading_enabled": False}


@app.post("/api/toggle")
async def toggle_running():
    state["running"] = not state["running"]
    return {"running": state["running"]}


@app.post("/api/risk/halt")
async def halt_trading():
    risk.manual_halt("Manually halted from dashboard")
    return {"halted": risk.halted, "halt_reason": risk.halt_reason}


@app.post("/api/risk/resume")
async def resume_trading():
    risk.resume()
    return {"halted": risk.halted, "halt_reason": risk.halt_reason}


@app.post("/api/reset")
async def reset_broker():
    cfg = config_store.get()
    # In-place reset (not reassigning `broker`) so this also wipes the
    # persisted account in data/paper_broker.db — see PaperBroker.reset().
    broker.reset(cfg["risk"]["starting_bankroll"])
    risk.reset_day(cfg["risk"]["starting_bankroll"])
    state["signal_feed"] = []
    state["decision_feed"] = []
    state["stats"] = {"signals_seen": 0, "trades_placed": 0, "skipped": 0}
    state["equity_history"] = []
    return {"ok": True}


# ---- connected accounts -----------------------------------------------
# Kalshi's own account (services/kalshi_account_client.py) stays .env/file-path
# based on purpose — an RSA private key shouldn't ever pass through a browser
# form. This is for the whale-watcher provider library instead: connecting a
# provider here beats hand-editing .env, and it's encrypted at rest.

class ConnectAccountBody(BaseModel):
    provider: str
    credentials: dict


@app.get("/api/accounts")
async def list_accounts():
    return {
        "storage_enabled": accounts_store.enabled(),
        "connected": accounts_store.status(),
        "available_providers": list(PROVIDERS.keys()),
        "active_provider": whale_provider.name,
    }


@app.post("/api/accounts/connect")
async def connect_account(body: ConnectAccountBody):
    global whale_provider
    try:
        accounts_store.save(body.provider, body.credentials)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.provider == whale_provider.name:
        whale_provider = get_active_provider()  # re-instantiate so it picks up the new creds now
    return {"ok": True}


@app.post("/api/accounts/{provider}/disconnect")
async def disconnect_account(provider: str):
    global whale_provider
    accounts_store.delete(provider)
    if provider == whale_provider.name:
        whale_provider = get_active_provider()
    return {"ok": True}


# ---- static dashboard --------------------------------------------------
# Every page here is a thin, entirely inline HTML/CSS/JS shell that re-fetches
# its own data every few seconds — there's no separate .js/.css bundle to
# version, and no benefit to caching the shell itself. FileResponse's default
# (ETag + Last-Modified, no Cache-Control) lets browsers heuristically cache
# without even revalidating, which is exactly what caused an already-shipped
# panel to silently not appear after an edit. no-store makes that impossible.
NO_CACHE_HEADERS = {"Cache-Control": "no-store, must-revalidate"}

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def dashboard():
    return FileResponse("static/index.html", headers=NO_CACHE_HEADERS)


@app.get("/status")
async def build_status():
    return FileResponse("static/status.html", headers=NO_CACHE_HEADERS)


@app.get("/login")
async def login_page():
    return FileResponse("static/login.html", headers=NO_CACHE_HEADERS)


@app.get("/accounts")
async def accounts_page():
    return FileResponse("static/accounts.html", headers=NO_CACHE_HEADERS)
