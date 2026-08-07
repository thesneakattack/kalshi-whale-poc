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

- [ ] Verify real Kalshi balance/position/fill field names against an actual
      connected account. `services/kalshi_account_client.py` and the
      Portfolio "Real Kalshi Account" mode are best-effort guesses at the
      response shape right now (raw JSON is always shown alongside as a
      safety net, but the formatted fields could be wrong).
- [ ] Verify the `create_order`/`cancel_order` request schema against
      Kalshi's *current* docs before `kalshi_account.trading_enabled` is
      ever flipped to `true` for real. Flagged with real uncertainty in
      `/status` Known Limitations — this is the one piece of the whole app
      with a genuine correctness question mark.
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
- [ ] Build shadow mode (logs intended real trades, executes nothing) — the
      README has called this a prerequisite to live trading since before
      any of this session's work; still not started.

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
