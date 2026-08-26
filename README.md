# Whale Signal — Paper Trading Terminal

Real Kalshi market data + a real-time whale order-flow signal (real by
default, or a built-in simulator — your choice) + a paper broker, running
in paper mode by default. No real order is ever placed unless
`kalshi_account.trading_enabled` is explicitly turned on in
`config/settings.yaml`, plus a typed in-app confirmation phrase — `create_order`/
`cancel_order` are fully implemented already, not stubs waiting on a
future rewrite, they're just gated off until you decide otherwise. Your
real Kalshi account can optionally be connected read-only
(balance/positions) — see below.

This project is working toward personal-use, real-money trading, not
staying a proof of concept indefinitely — see `CLAUDE.md`'s standing goal
and `ROADMAP.md`'s "Path to production" section for exactly what's shipped
versus what's still open before that happens. Treat anything below as
describing a safety-first paper-trading system today, regardless of that
direction.

## Run it with DDEV

```bash
cd kalshi-whale-poc
cp .env.example .env      # optional — fill in real API keys later, blank is fine for now
ddev start
```

DDEV builds the API from the included `Dockerfile` as an additional,
internal-only `fastapi` service (see `.ddev/docker-compose.fastapi.yaml`).
The default webserver container isn't unused this time — it's the actual
public entrypoint: it serves the dashboard (`static/*.html`) directly and
reverse-proxies `/api/`+`/auth/` to `fastapi` (`.ddev/nginx/kalshi-proxy.conf`).
Once it's up:

```bash
ddev launch
```

or open **https://kalshi-whale-poc.ddev.site** directly — check `ddev describe`
for the actual port if that bare URL doesn't load; DDEV doesn't always bind
the implicit HTTPS 443 (currently `:8443` on this machine, but that's not
guaranteed to be stable across environments).

To stop: `ddev stop`. To rebuild after changing `requirements.txt` or the
`Dockerfile`: `ddev restart`.

## Run it without DDEV

`main.py` is API-only (no dashboard HTML) — DDEV's nginx container is what
serves the dashboard and proxies it to the API, see above, so this path is
mainly useful for hitting the API directly or for backend development
without the frontend:

```bash
cd kalshi-whale-poc
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

`GET http://localhost:8000/api/state` now works; **http://localhost:8000/**
does not serve the dashboard this way — the dashboard's JS calls `/api/...`
with relative paths that only resolve correctly when served from the same
origin as the API, which is exactly what DDEV's nginx proxy sets up. Use
DDEV for the full dashboard experience.

Or with plain Docker (same API-only caveat):

```bash
docker build -t whale-poc .
docker run -p 8000:8000 --env-file .env whale-poc
```

## How it works

Two things run concurrently, not one poll loop:

- **Whale-signal detection is real-time, not polled.** A Kalshi trade
  WebSocket stream (`services/whale_stream/`) feeds every print into
  whale-signal detection as it happens, via whichever provider
  `WHALE_WATCHER_PROVIDER` selects (real Kalshi trade-tape data by
  default — see "Whale-watcher provider library" below). A signal that
  passes the configured size/cooldown gates runs through
  `services/strategy_engine.py`, which checks confidence and
  `services/risk_manager.py`'s limits, then either opens a paper position
  via `services/paper_broker.py` or gets logged as a rejection.
- **A background loop** (`poll_interval_sec`, default 6s, in
  `config/settings.yaml`) handles periodic housekeeping unrelated to any
  single signal — market catalog scans, backups, signal-resolution
  checks, research — not whale detection itself.

The dashboard polls `/api/state` every 5 seconds and shows the signal tape,
strategy decisions, positions, and P&L live. Every threshold is editable from
the **Controls** panel and persists to `config/settings.yaml`.

## Config: two layers, on purpose

- **`config/settings.yaml`** — non-secret tuning (thresholds, position sizing, risk
  limits). Committed to git. Editable live from the dashboard's Controls panel.
- **`.env`** (copy from `.env.example`, gitignored) — API URLs and keys. Every
  variable is optional. By default (`WHALE_WATCHER_PROVIDER` unset) the app
  runs on real Kalshi trade-tape data via `kalshi_trade_tape` — no API key
  needed, it's public market data, streamed in real time over Kalshi's own
  WebSocket. Set `WHALE_WATCHER_PROVIDER=generic_rest` plus
  `WHALE_WATCHER_API_URL` to point at a third-party whale-watcher tool
  instead. There's no separate "simulator" setting to opt into — the
  built-in simulator (`services/whale_simulator.py`) only ever kicks in
  automatically as a fallback, when no real provider is currently enabled
  (e.g. `generic_rest` selected with no URL set). The dashboard header
  shows which source is currently active, including when it's fallen back
  to simulated.

### Whale-watcher provider library

`services/whalewatchers/` is a small plugin library, not a single file — exactly
one provider is active at a time, selected by `WHALE_WATCHER_PROVIDER` in `.env`
(defaults to `kalshi_trade_tape` — real Kalshi trade data, not a placeholder).

- `base.py` — the interface every provider implements (`enabled`, `fetch_signals()`).
- `kalshi_trade_tape.py` — the default: real Kalshi trade-tape prints over the
  exchange's own trade WebSocket channel, no third-party tool or API key needed.
- `generic_rest.py` — a configurable field-name-mapping REST client, for
  third-party whale-watcher tools that just return JSON. Point
  `WHALE_WATCHER_API_URL` at whichever tool you pick and adjust the
  `WHALE_WATCHER_*_FIELD` vars in `.env` if its JSON keys differ from
  `ticker` / `side` / `size` / `price`.
- `template_provider.py` — copy this to add a new named provider (its own file,
  its own credentials in `.env`, registered in `whalewatchers/__init__.py`) once
  you've picked a specific tool or want to tap into a specific account for it.

## Connecting your real Kalshi account (read-only)

`services/kalshi/account_client.py` is a second, separate authenticated client —
never mixed with the public market-data client above, on purpose. It uses
Kalshi's RSA-PSS request signing to read **your real account**: balance,
positions, fills, orders.

1. Generate an API key + RSA key pair from Kalshi's account settings
   (see [docs.kalshi.com](https://docs.kalshi.com) → API Keys). Save the private
   key file somewhere outside this repo.
2. In `.env`, set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` (pointing at
   that file). That's it — the dashboard's **Live Account** bar picks it up on
   the next poll. Nothing here can place, cancel, or modify anything; the read
   endpoints only ever read.

`create_order` / `cancel_order` are fully implemented in the same file — not
stubs — but every call checks `kalshi_account.trading_enabled` in
`config/settings.yaml` first and refuses while it's `false` (the default).
Turning real trading on is a config flip, not a coding task, once you're ready
for it.

## Optional: Google sign-in + Connected Accounts

By default there's no login — same as before. Setting `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, and `APP_SECRET_KEY` together in `.env` puts the whole
app (dashboard and every `/api/*` route) behind Google sign-in.
This is a single-operator gate, not multi-tenant accounts — there's no user
table, a successful login just proves it's you. Set `ALLOWED_GOOGLE_EMAIL` too
or anyone who completes Google's consent screen gets in.

1. Google Cloud Console → APIs & Services → Credentials → Create Credentials →
   OAuth client ID (type: Web application). Add `<whatever URL you use>/auth/callback`
   as an authorized redirect URI — exact match required.
2. Set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env`.
3. Generate `APP_SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`)
   — this also becomes the encryption key for the Connected Accounts store below.

Once signed in, **`/accounts`** replaces hand-editing `.env` for whale-watcher
provider credentials: fill in the form, it's encrypted (Fernet, key derived from
`APP_SECRET_KEY`) into `data/accounts.db` (gitignored), and takes effect
immediately, no restart. `.env` values still work as a fallback if nothing's
connected through the UI. Kalshi's own account deliberately stays out of this —
see "Connecting your real Kalshi account" above for why.

## Where real order execution plugs in later

| Piece | Now | Later |
|---|---|---|
| Whale signal | Real Kalshi trade-tape data by default (`kalshi_trade_tape`), or a named third-party provider — switches on `.env` (see above) | Already wired — no rewrite needed |
| Account read access | Real balance/positions/fills via `services/kalshi/account_client.py`, read-only | Already wired — no rewrite needed |
| Order execution | `services/paper_broker.py` (fake fills, fake bankroll). Real `create_order`/`cancel_order` exist but are gated off by `kalshi_account.trading_enabled: false` plus a typed in-app confirmation phrase | Flip `trading_enabled` to `true` (and confirm) once you trust it — after shadow mode (below) |
| Mode | `mode: paper` in `config/settings.yaml` | `services/shadow_mode.py` already exists (logs what it *would* trade, no execution) — run it for a real stretch and review the results before ever flipping to `live`. In this project's own history that stretch hasn't happened yet (`mode` has only ever been switched to `shadow` twice, both reverted within a day, zero trades logged either time) — treat that review as the real gate, not a formality. |

## Safety notes for when you go live

- Kalshi is a CFTC-regulated exchange. Read-only account access is wired up;
  real order placement is implemented but deliberately gated off by
  `kalshi_account.trading_enabled` — flipping it is a decision, not an accident.
- The `create_order` request shape has been verified against Kalshi's current
  docs and migrated to their official `kalshi_python_async` SDK. Kalshi's API
  has more than one order-placement surface (this isn't their perpetuals/
  margin product) and it has changed before — worth re-checking against
  current docs if it's been a while since this was last verified.
- Before connecting real trading: run in `shadow` mode against a live whale
  feed for a while and compare its decisions to what you'd have wanted, before
  trusting the kill switch and position limits with actual capital.
- Check `risk.max_daily_loss_pct` and every other risk/sizing number in
  `config/settings.yaml` before trusting them — the shipped defaults were
  picked to exercise paper-mode logic, not sized for real capital.
- This is not financial advice. It's a personal project working toward
  real-money use for one trusted operator — not a general-purpose or
  production trading system for anyone else to rely on as-is. See
  `ROADMAP.md`'s "Path to production" section for exactly what's still
  open before it should be trusted with real capital.
