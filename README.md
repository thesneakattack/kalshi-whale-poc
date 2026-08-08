# Whale Signal — Paper Trading Terminal (POC)

Real Kalshi market data + a simulated whale-signal feed + a fake broker.
No real orders are ever placed. Your real Kalshi account can optionally be
connected read-only (balance/positions) — see below. Every piece is wired so
live order execution comes in later without a rewrite.

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

or open **https://kalshi-whale-poc.ddev.site** directly.

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

Every `poll_interval_sec` (default 15s), the backend:

1. Pulls the top-volume open markets from Kalshi's public API (no auth needed for market data).
2. Maybe generates a fake "whale" order-flow signal via `services/whale_simulator.py`.
3. Runs that signal through `services/strategy_engine.py`, which checks confidence
   threshold, cooldown, and risk limits, then either opens a paper position or skips.
4. Updates the in-memory bankroll/positions in `services/paper_broker.py`.

The dashboard polls `/api/state` every 5 seconds and shows the signal tape,
strategy decisions, positions, and P&L live. Every threshold is editable from
the **Controls** panel and persists to `config/settings.yaml`.

## Config: two layers, on purpose

- **`config/settings.yaml`** — non-secret tuning (thresholds, position sizing, risk
  limits). Committed to git. Editable live from the dashboard's Controls panel.
- **`.env`** (copy from `.env.example`, gitignored) — API URLs and keys. Every
  variable is optional. Leave `WHALE_WATCHER_API_URL` blank and the app runs
  entirely on the built-in simulator; fill it in (and set `WHALE_WATCHER_PROVIDER`
  if you're not using the default generic one) and it switches over automatically,
  no settings.yaml changes needed. The dashboard header shows which source is
  currently active.

### Whale-watcher provider library

`services/whalewatchers/` is a small plugin library, not a single file — exactly
one provider is active at a time, selected by `WHALE_WATCHER_PROVIDER` in `.env`
(defaults to `generic_rest`).

- `base.py` — the interface every provider implements (`enabled`, `fetch_signals()`).
- `generic_rest.py` — the default: a configurable field-name-mapping REST client,
  for whale-watcher tools that just return JSON. Point `WHALE_WATCHER_API_URL` at
  whichever provider you pick and adjust the `WHALE_WATCHER_*_FIELD` vars in `.env`
  if its JSON keys differ from `ticker` / `side` / `size` / `price`.
- `template_provider.py` — copy this to add a new named provider (its own file,
  its own credentials in `.env`, registered in `whalewatchers/__init__.py`) once
  you've picked a specific tool or want to tap into a specific account for it.

## Connecting your real Kalshi account (read-only)

`services/kalshi_account_client.py` is a second, separate authenticated client —
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
app (dashboard, build status, every `/api/*` route) behind Google sign-in.
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
| Whale signal | Simulated or a named provider, switches on `.env` (see above) | Already wired — no rewrite needed |
| Account read access | Real balance/positions/fills via `services/kalshi_account_client.py`, read-only | Already wired — no rewrite needed |
| Order execution | `services/paper_broker.py` (fake fills, fake bankroll). Real `create_order`/`cancel_order` exist but are gated off by `kalshi_account.trading_enabled: false` | Flip `trading_enabled` to `true` once you trust it — after shadow mode (below) |
| Mode | `mode: paper` in `config/settings.yaml` | Add a `shadow` mode first (logs what it *would* trade, no execution) before ever flipping to `live` |

## Safety notes for when you go live

- Kalshi is a CFTC-regulated exchange. Read-only account access is wired up;
  real order placement is implemented but deliberately gated off by
  `kalshi_account.trading_enabled` — flipping it is a decision, not an accident.
- The `create_order` request shape targets Kalshi's classic prediction-markets
  order endpoint. Kalshi's API has more than one order-placement surface (this
  isn't their perpetuals/margin product) and it has changed before — re-verify
  the request schema against current Kalshi docs before ever enabling it.
- Before connecting real trading: run in `shadow` mode against a live whale
  feed for a while and compare its decisions to what you'd have wanted, before
  trusting the kill switch and position limits with actual capital.
- This is a POC, not financial advice or a production trading system.
