# Roadmap: comprehensive, dummy-proof paper trading terminal

Guiding principle: someone who has never touched a prediction market or a
trading interface before should be able to open this app and understand
*what they're looking at*, *why it did what it did*, and that *no real money
is ever at risk* unless they deliberately configure it to be. Everything
below is measured against that bar, not against "does it technically work."

This is a living to-do list, not a snapshot — check items off in place and
add new ones as they turn up. For what's already built and in what order,
see `static/status.html` (`/status`) — this file is forward-looking, that
one is the historical record.

## P0 — Safety & correctness (before anything else)

- [x] Verify real Kalshi balance/position/fill field names against an actual
      connected account. **Also found and fixed a real bug in the process:
      every signed request was returning 401 Unauthorized**, on both
      production and demo hosts — the signed message omitted the
      `/trade-api/v2` prefix that Kalshi's own docs example
      (`path='/trade-api/v2/portfolio/balance'`) includes. This had never
      been tested against a real key before; nothing was wrong with the
      account/credentials, the signing code itself was wrong. Fixed in
      `services/kalshi_account_client.py` (`_base_path`, derived from
      `base_url` rather than hardcoded, so it's correct on production,
      demo, or any other host). Also added `KALSHI_ACCOUNT_BASE_URL` so the
      account client can point at a different Kalshi environment than
      public market data does — demo and production use separate
      credentials entirely. Once auth worked, real field names turned out
      to differ from the guesses in several places: `market_positions`
      entries use `position_fp`/`market_exposure_dollars`/
      `total_traded_dollars`, not `position`/`market_exposure`/
      `total_traded`; fills use `count_fp`/`yes_price_dollars`/
      `no_price_dollars`, not `count`/`size`/`yes_price`/`price` — the old
      guesses didn't exist on any real response and silently rendered
      "—"/blank for every row. Balance turned out to have two genuinely
      different real numbers (`balance` = uninvested cash, `portfolio_value`
      = cash + open positions) that the old code picked between as if one
      were a fallback for the other; the dashboard now shows both labeled
      explicitly. Raw JSON stays visible everywhere regardless, since
      Kalshi's docs can still drift again. 2 new regression tests
      (`test_signed_message_includes_the_trade_api_v2_prefix`,
      `test_base_path_derived_from_base_url_not_hardcoded`); the field-name
      fixes were verified live against the real connected account (visually
      confirmed in a real Chrome session, not just curl).
- [x] Verify the `create_order`/`cancel_order` request schema against
      Kalshi's *current* docs. **The concern was justified — the original
      implementation was wrong.** It targeted Kalshi's legacy order shape
      (`POST /portfolio/orders`, `action`+`side(yes/no)`+`count`+
      `{yes,no}_price` in cents). Current docs (verified 2026-08-07 by
      fetching docs.kalshi.com directly) show the endpoint moved to
      `POST /portfolio/events/orders` / `DELETE
      /portfolio/events/orders/{order_id}`, `action`+`side` collapsed into
      one `side` field (`"bid"`/`"ask"`, no `"no"` value — selling YES and
      buying NO are the same order-book trade), `count`/`price` are now
      *strings* (contracts and dollars, not integer cents), and
      `time_in_force`/`self_trade_prevention_type` are newly *required*
      fields with no old equivalent. Kalshi's docs note migration off the
      legacy endpoint "no earlier than May 6, 2026" — today is past that,
      so the old code could already have been rejected outright, silently,
      the first time it was ever used. `services/kalshi_account_client.py`
      is rewritten to the current shape; read endpoints (balance/positions/
      fills) were also checked and are unchanged. 4 new tests
      (`tests/test_kalshi_account_client.py`) verify the exact request
      shape sent (path, method, body fields) against a faked HTTP client —
      no network call, no real credentials. The balance/position/fill
      *response* field-name item right below this one is still open — that
      one needs a real connected account to verify, which is out of scope
      for this pass.
- [x] Add an in-app confirmation step before real trading can be enabled.
      `POST /api/config` now structurally refuses to touch
      `kalshi_account.trading_enabled` at all — the only path is
      `POST /api/trading/enable`, which requires both a connected real
      account and an exact-match typed confirmation phrase ("ENABLE REAL
      TRADING", not a checkbox). `POST /api/trading/disable` always works,
      no confirmation needed. Dashboard gets a matching control on the
      account bar. Covered by 8 backend tests (`tests/test_trading_gate.py`,
      isolated from the real config/settings.yaml and data/*.db) and
      verified live in a real browser via the project's ddev selenium-chrome
      service, including that the confirmation input survives the
      dashboard's periodic 5s refresh instead of getting wiped mid-typing.
- [x] Persist paper broker state (bankroll, open positions, trade log)
      across restarts. `services/paper_broker.py` now persists to
      `data/paper_broker.db`, same SQLite pattern as `signal_log.py`.
      `services/risk_manager.py`'s daily-loss baseline and kill-switch halt
      state were persisted alongside it (`data/risk_state.db`) — without
      that, a restart would keep the recovered bankroll but reset the loss
      baseline to `config/settings.yaml`'s `starting_bankroll`, either
      mismeasuring today's loss or silently un-halting a tripped kill
      switch. A plain restart now resumes; `POST /api/reset` is the only
      thing that wipes it. Verified end-to-end with a real `ddev restart`.
- [x] Tighten CORS (`allow_origins=["*"]` in `main.py`). Now defaults to the
      DDEV hostname + `localhost:8000`, overridable via a comma-separated
      `ALLOWED_ORIGINS` in `.env` for any other deployment. Verified live:
      preflight from the DDEV origin gets `access-control-allow-origin`
      back, a random origin gets nothing.
- [x] Build shadow mode (logs intended real trades, executes nothing).
      `services/shadow_mode.py`'s `ShadowTrader` runs the exact same
      follow-the-whale gates as `strategy_engine.py` (confidence, whale
      win-rate filter, cooldown, position-size-rounds-to-zero, its own
      independent daily-loss kill switch) but sized against a *real*
      reference bankroll — the connected account's actual balance if one
      exists, or `risk.starting_bankroll` as a clearly-labeled fallback
      when it doesn't — and only ever logs the result to
      `data/shadow_mode.db`; `create_order` is never called. Active when
      `mode: shadow` or `mode: live` in `config/settings.yaml` (a Controls
      panel dropdown now exposes this — previously `mode` was set in the
      YAML but read by nothing). Portfolio view gets a Shadow Trades panel.
      10 tests cover every gate in isolation; live-verified end-to-end
      against the running app (flipped to `shadow`, watched a real
      intended-trade row appear with the correct fallback-bankroll label,
      confirmed the dashboard renders it, reverted back to `paper`
      afterward).

## P1 — Actually dummy-proof (a first-timer understands what's happening)

- [ ] First-run walkthrough. There is currently zero onboarding — a new
      user lands directly on a 4-tab dashboard with no explanation of what
      Portfolio/Markets/Whale Watch/Terminal even mean.
- [ ] A persistent glossary/help layer (tooltips or a dedicated Help panel)
      for every piece of jargon still in use: confidence, cooldown, kill
      switch, series, combo/parlay market, implied probability, basis
      points, whale "lean," etc.
- [ ] Extend the plain-English pattern from the Portfolio "Betting vs.
      Likelihood vs. Risk vs. Whales" panel to Strategy Decisions too —
      skip reasons are still raw strings like "confidence 0.34 below
      threshold" instead of a sentence a beginner would understand.
- [ ] A basic "how prediction markets work" explainer: what a price means
      as a probability, why Yes + No ≈ 100¢, what "settlement" means. The
      whole app currently assumes this prior knowledge.
- [ ] A global, impossible-to-miss "this is not real money" indicator.
      Today that's the header's PAPER tag plus a separate red account bar
      — clear once you know to look, but not loud enough for a total
      beginner, especially after a real account is connected.

## P2 — Whale-tracking maturity

- [ ] Add at least one more real named whale-watcher provider (beyond the
      `generic_rest` default and the unused `template_provider.py`) with
      actual, tested setup steps.
- [ ] Improve series/category grouping. `services/signal_log.py` currently
      groups "market type" by ticker-prefix-before-first-hyphen, a
      reasonable proxy but not a real category taxonomy — revisit once
      better Kalshi series metadata is available.
- [ ] Let a user manually exclude a specific whale/source from the
      strategy, not just the automatic win-rate cutoff
      (`min_whale_winrate_pct` / `min_resolved_for_whale_filter`).

## P3 — Reliability & engineering hygiene

- [x] Add an automated test suite. 35 tests in `tests/` cover
      `paper_broker.py` (fill/cost-capping/P&L math, cooldowns, persistence
      across a simulated restart, reset), `risk_manager.py` (daily-loss
      kill-switch trip/stay-halted/resume, persistence), `strategy_engine.py`
      (every skip/trade branch), and `signal_log.py` (series grouping,
      win-rate math, time-window filtering). Each test isolates its own
      SQLite file via `monkeypatch`-ing the module's `DB_PATH` — none of them
      touch the real, live `data/*.db` files. Spot-verified the suite isn't
      vacuous by deliberately breaking the "no"-side P&L direction math and
      confirming the right test failed, then reverting.
- [x] Basic CI. `.github/workflows/tests.yml` runs the suite via GitHub
      Actions on every push to `main` and every PR.
- [x] Migrated fully to Kalshi's official `kalshi_python_async` SDK —
      **supersedes the "decided against it" finding below the line from
      earlier the same day.** That finding was real but was an artifact of
      `pip index versions` silently resolving to a stale, Python-3.11-
      compatible release (3.2.0) without warning that newer ones existed;
      every release past 3.2.0 requires Python ≥3.13 and the real latest
      (3.27.0) matches Kalshi's live API exactly — `get_positions`/
      `get_fills`/`get_balance`/`get_orders`/`get_exchange_status` and order
      placement (`create_order_v2`/`cancel_order_v2`) all verified working
      against the real connected account with zero `ValidationError`s.
      `Dockerfile` bumped to `python:3.13-slim`; `requirements.txt` pins the
      exact SDK version and `urllib3==2.7.0` (an undeclared dependency the
      SDK needs but doesn't list). `services/kalshi_client.py` and
      `services/kalshi_account_client.py` were rewritten to hand signing,
      endpoint paths, and request/response schemas entirely to the SDK,
      keeping the same public method signatures and dict-shaped returns so
      `main.py` didn't need to change. `_request_timeout` is accepted by
      `create_order_v2` but rejected outright by every read endpoint and by
      `cancel_order_v2` — verified by reading each method's generated
      source, not assumed; get it wrong and every read call throws instead
      of just being slow. Old note, still true: its own auth code hardcodes
      the exact same `/trade-api/v2` signed-path prefix this session's
      earlier hand-rolled-auth fix added — outside confirmation that fix was
      correct.
      <details><summary>Original "decided against it" finding (2026-08-07, superseded above)</summary>
      Investigated migrating to Kalshi's official `kalshi_python_async` SDK
      (async-native, would eliminate hand-rolled request-signing as a bug
      surface) — decided against it, and verified why empirically rather
      than from docs alone. Installed it, pointed it at the real connected
      account, and `get_positions()`/`get_fills()` both threw Pydantic
      `ValidationError`: the SDK's models require integer fields
      (`position`, `market_exposure`, `count`, `price`, ...) that Kalshi's
      live API no longer returns, only the `_fp`/`_dollars` string variants
      this project's own hand-rolled client already handles correctly
      (confirmed by reading the raw HTTP response through the SDK's own
      `_without_preload_content` escape hatch). `get_balance` and
      `get_exchange_status` work fine through the SDK; positions and fills
      don't, at the latest available version (3.2.0) as of 2026-08-07.
      Kalshi's own docs warn "SDKs are updated periodically and may lag the
      API" — this is that, hit directly. Worth re-evaluating once Kalshi
      patches it, but adopting it today would have been a regression, not
      an improvement.
      </details>
- [x] Exponential backoff on `429 Too Many Requests`, per Kalshi's own rate
      limit guidance (`docs.kalshi.com/getting_started/rate_limits` — no
      `Retry-After` header is provided, backoff is the documented
      expectation). Originally implemented as `services/http_client.py`'s
      `request_with_backoff` wrapping raw `httpx` calls; became dead code
      the moment the SDK migration above landed (Kalshi calls no longer go
      through raw `httpx` — the SDK owns the request), so it was replaced
      with `call_with_backoff`, which wraps arbitrary async SDK client
      methods instead and detects a 429 via the SDK's own exception shape
      (`.status`). The SDK's own built-in retry support doesn't cover 429 at
      all (only 5xx/connection errors), so this is still load-bearing, not
      redundant with the SDK. Only 429 triggers a retry; every other
      exception re-raises immediately. 5 tests, no real network calls or
      real sleeping.
- [x] Surface Kalshi's real exchange open/closed status
      (`GET /exchange/status`, public/unauthenticated) in the dashboard, so
      a quiet signal feed reads as "the market's closed," not "the strategy
      is stuck." Small badge in the header, only visually loud when closed.
- [x] Fixed the intermittent 403/404 errors on
      `https://kalshi-whale-poc.ddev.site/` that recurred repeatedly during
      development, and, in the same pass, actually separated the frontend
      from the API rather than papering over the symptom. Root cause:
      `ddev`'s default (unused, empty-docroot) `web` service and the custom
      `fastapi` service both auto-register a Traefik router for the exact
      same hostname (confirmed by reading ddev-router's generated
      `<project>_merged.yaml` directly: two routers, identical
      `HostRegexp`, same `https` entrypoint); Traefik's tie-break between
      two equal-priority routers isn't stable across reloads, so the site
      would randomly route to `web`'s empty docroot (→ 403) instead of
      `fastapi`. Clearing `router_http_port`/`router_https_port` in
      `.ddev/config.yaml` (an earlier fix attempt, that config's own
      comment described the symptom accurately) didn't actually fix this —
      those only control the ports ddev-router itself listens on, not which
      services get routers generated for them.
      <details><summary>First real fix (superseded below): blank out web's exposure</summary>
      Blanked out `web`'s own `HTTP_EXPOSE`/`HTTPS_EXPOSE`/`VIRTUAL_HOST`
      via `.ddev/docker-compose.web-override.yaml` so ddev's router-config
      generator never creates a `web` router for this hostname at all —
      verified by re-reading the generated config after a restart (`web`
      routers: 0, was 3) and 10/10 real external requests through the
      actual router path returning 200. Worked, but left `web` sitting
      there unused and the "fix" was a container-level workaround rather
      than addressing why `main.py` was serving the dashboard AND the API
      out of one process in the first place.
      </details>
      Real fix: `main.py` is API-only now — the `/`, `/status`, `/login`,
      `/accounts` FileResponse routes and the `/static` mount were removed
      entirely. `web` (nginx, `docroot: static` in `.ddev/config.yaml`) is
      now the one and only public entrypoint, serving `static/*.html`
      directly and reverse-proxying `/api/` + `/auth/` back to `fastapi`
      (`.ddev/nginx/kalshi-proxy.conf`); `fastapi` dropped its
      `HTTP_EXPOSE`/`HTTPS_EXPOSE`/`VIRTUAL_HOST` entirely and is reachable
      only inside the project's docker network as `fastapi:8000` — it has
      no public URL of its own anymore. This doesn't just avoid the router
      collision, it makes it structurally impossible: there is now exactly
      one service ddev-router can register for this hostname. Also means
      a separate frontend could genuinely be built against this API later,
      hitting `/api/*` directly, without touching `main.py`. Uvicorn picked
      up `--proxy-headers --forwarded-allow-ips=*` since it now sits behind
      two hops (ddev-router → nginx → fastapi) instead of one, so
      `request.url_for()` (the OAuth `redirect_uri`) still resolves the
      right scheme/host — safe to trust from any IP here specifically
      because `fastapi` has no public exposure at all, only other
      containers on this project's network can reach it. Verified: every
      page (`/`, `/status`, `/login`, `/accounts`), `/api/*` and `/auth/*`
      proxying, the `Cache-Control: no-store` behavior the old
      `NO_CACHE_HEADERS` used to provide (a real regression risk — nginx's
      `try_files <file> =404` serves a matched file within its own
      location block rather than re-entering location matching, so the
      header had to be set directly on each page's location block, not
      inherited from a shared regex block), and the full test suite, all
      against the real running ddev project, not just reasoned about.
- [ ] Mobile/responsive pass — the Terminal view's 3-column grid is
      desktop-only right now.
- [ ] Accessibility pass — keyboard navigation, aria labels, and a check
      that the heavy green/red (yes/no) coding has a non-color fallback
      for colorblind users.

## P4 — Nice-to-haves

- [ ] Notifications (email/push) for real trades, kill-switch triggers, or
      a tracked whale's win rate crossing the avoidance threshold.
- [ ] Finalize the app name — "Nessie" vs. "Operation Deepscan" is still
      an open decision; once picked, it needs to flow through page titles,
      headers, and the README.
