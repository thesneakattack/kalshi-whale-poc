# autotrade / kalshi-whale-poc

Kalshi whale-signal paper trading terminal. Real Kalshi market data + a
simulated-or-live whale order-flow signal + a fake broker. Safety-first POC:
no real order ever gets placed unless `kalshi_account.trading_enabled` is
explicitly flipped in `config/settings.yaml`, and that flip is currently gated
behind unresolved P0 items — see `ROADMAP.md`.

## Git history + two supplementary docs

This became a git repository partway through the project's life (see the
first commit's message for the cutover point) — everything built before that
has no real commit-by-commit history, which is why two hand-maintained docs
exist and remain the primary source for the *why* behind pre-git work:

- **`ROADMAP.md`** — forward-looking, living to-do list. Check items off in
  place (`- [x]`), add new ones as they turn up. Organized P0 (safety/
  correctness) → P4 (nice-to-haves). Check the P0 section before touching
  anything safety-adjacent (kill switch, real trading, CORS, auth).
- **`static/status.html`** (served at `/status`) — backward-looking historical
  record. A manually maintained, chronological timeline of build phases, plus
  reference tables (components, API routes, config, known limitations). This
  is hand-written prose describing what was built and why, not generated —
  it can and does go stale if a change doesn't update it.

For anything committed going forward, prefer `git log` / `git blame` / `git
diff` as the primary source of "what changed and why" — that's real history,
not reconstructed prose. Keep using `ROADMAP.md` and `status.html` as the
living, human-readable layer on top: check the P0 section before
safety-adjacent work, and still update both when a roadmap item ships.

**When a roadmap item ships, update both.** Use the `/sync-status-docs` skill
for this — it checks the item off in `ROADMAP.md` and adds the matching
timeline phase + component-table rows to `status.html` in one pass, matching
the existing phases' tone and structure.

## Dev workflow — this is a ddev project, not bare uvicorn

- `ddev describe` — check whether it's running (it usually already is; don't
  assume you need `python -m venv` / `pip install`).
- Two services split frontend from API — `main.py` is API-only, it does not
  serve any HTML. `web` (ddev's default nginx container, `docroot: static`)
  is the one public entrypoint: it serves `static/*.html` directly and
  reverse-proxies `/api/` + `/auth/` to `fastapi`
  (`.ddev/nginx/kalshi-proxy.conf`). `fastapi` has **no public URL of its
  own** — no `HTTP_EXPOSE`/`HTTPS_EXPOSE`/`VIRTUAL_HOST` — it's reachable
  only inside the project's docker network as `fastapi:8000`. This is a
  deliberate fix for a real, recurring bug (see `ROADMAP.md`): when both
  containers had a public router registered for the same hostname,
  Traefik's tie-break between them wasn't stable across restarts.
- The `fastapi` service runs `uvicorn --reload` — edits to `.py` files take
  effect in ~1-2s automatically. No manual restart needed for normal
  iteration. Editing `.ddev/nginx/*.conf` or any `.ddev/*.yaml` does need a
  `ddev restart` to take effect, unlike `.py` files.
- `ddev logs -s fastapi` / `ddev logs -s web` — tail logs, e.g. to watch
  reload events, errors, or nginx's access/error log.
- `ddev exec -s fastapi <cmd>` — run one-off commands inside the container
  (working dir `/app`, same layout as the repo root). Prefer this over raw
  `docker exec`.
- App: `https://kalshi-whale-poc.ddev.site` (served by `web`).
  `GET /api/state` is the fastest way to check live state (bankroll,
  positions, risk halt status, etc.) without opening the dashboard — same
  hostname, nginx proxies it to `fastapi` transparently.
- To test something that depends on a **real process restart** (not just
  `--reload`'s in-process reimport) — e.g. verifying persistence survives a
  restart — use a full `ddev restart`. A file save alone won't exercise that
  path.
- See the `/run` skill for more detail on driving the live app.

## `data/*.db` files are live — don't delete/move them casually

`paper_broker.db`, `risk_state.db`, `signal_log.db`, `accounts.db` are SQLite
files the running dev server actively reads and writes while `ddev` is up.
Deleting or moving one out from under a live process desyncs its in-memory
state from disk until the next restart, and produces confusing results (a
`_connect()` mid-run will silently recreate an empty file and start writing
into a row that stale in-memory objects don't know is gone). Check
`ddev describe` before touching them. Prefer `POST /api/reset` (wipes the
paper account cleanly, in place, via `PaperBroker.reset()`) over deleting
`paper_broker.db` by hand.

## Persistence idiom

Every stateful module owns its own SQLite file under `data/` (gitignored via
`data/*.db`). Pattern used by `services/signal_log.py`,
`services/paper_broker.py`, `services/risk_manager.py`:

```python
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "X.db"

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS ... ")
    return conn
```

Follow this for any new persisted state rather than introducing a different
mechanism (a shared DB, an ORM, etc.) — it's deliberately one small file per
concern, consistent with how the rest of the app is factored.

## Safety invariants — don't regress these

- Paper mode by default (`mode: paper` in `config/settings.yaml`).
- Real order placement (`create_order`/`cancel_order` in
  `services/kalshi_account_client.py`) is fully implemented but gated by
  `kalshi_account.trading_enabled` — must stay `false` until the P0
  verification items in `ROADMAP.md` are resolved (order schema unverified
  against Kalshi's current docs, no in-app confirmation step yet).
- CORS is currently wide open (`allow_origins=["*"]` in `main.py`) — a known,
  tracked P0 item, not an accident. Don't "fix" it as a drive-by; it needs
  its own pass (see ROADMAP.md).
- The daily-loss kill switch (`services/risk_manager.py`) and the paper
  broker's bankroll/positions/trade log both persist across restarts now
  (`data/risk_state.db`, `data/paper_broker.db`). Keep them in sync if you
  touch either file — the risk manager's `day_start_bankroll` baseline must
  stay consistent with the broker's actual persisted bankroll, or the kill
  switch can mismeasure today's loss or silently un-halt after a restart.

## Quick file map

- `main.py` — FastAPI app, API/auth routes only (no HTML), the trading loop.
- `services/` — one module per concern (client, strategy, risk, broker,
  persistence, auth, accounts store, whale-watcher provider library).
- `static/` — dashboard + status page + login/accounts pages, served
  directly by ddev's `web` container, not by `main.py`. Plain inline
  HTML/CSS/JS per page, no build step, no bundler.
- `.ddev/nginx/kalshi-proxy.conf` — `web`'s reverse-proxy rules
  (`/api/`, `/auth/` → `fastapi:8000`) and the extension-less page aliases
  (`/status`, `/login`, `/accounts`).
- `config/settings.yaml` — non-secret, live-reloadable tuning (thresholds,
  position sizing, risk limits). Committed to git (once this becomes a git
  repo), editable live from the dashboard's Controls panel.
- `.env` (gitignored) — secrets and URLs. Every variable is optional; see
  `.env.example` and `README.md` for what each one unlocks.
