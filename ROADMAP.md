# Roadmap: comprehensive, dummy-proof paper trading terminal

Guiding principle: someone who has never touched a prediction market or a
trading interface before should be able to open this app and understand
*what they're looking at*, *why it did what it did*, and that *no real money
is ever at risk* unless they deliberately configure it to be. Everything
below is measured against that bar, not against "does it technically work."

This is a living to-do list, not a snapshot — check items off in place and
add new ones as they turn up. **Keep this file short.** Only genuinely open
items live here; the moment something ships, it comes off this list
entirely (a one-line pointer at most) — the narrative belongs in `git log`,
not here. Other docs carry the "what happened and why":

- **`git log` / `git blame` / `git diff`** — the primary, actively
  maintained source for anything shipped from 2026-08-07 (the git cutover)
  onward. Real commit-by-commit history, not reconstructed prose.
- **`docs/status-archive-2026-08-26.html`** — a frozen, final snapshot of
  `static/status.html`, the hand-maintained build-timeline page this
  project kept before and after the git cutover, retired 2026-08-26 once
  everything it recorded going forward was already git-tracked (see
  `CLAUDE.md`'s "Git history + supplementary docs" section for the full
  reasoning). Not maintained going forward — consult it for the
  irreplaceable pre-git narrative, or anything shipped 2026-08-07 through
  2026-08-26 that a bare commit message doesn't fully explain.
- **`docs/roadmap-archive-2026-08-09.md`** — frozen snapshot #1 (pre-condense,
  837 lines).
- **`docs/roadmap-archive-2026-08-16.md`** — frozen snapshot #2, covering
  2026-08-09 → 2026-08-16.
- **`docs/roadmap-archive-2026-08-23.md`** — frozen snapshot #3, covering
  2026-08-16 → 2026-08-23 — the full evidence/numbers/fix-narrative behind
  every item condensed below lives here (or in the status archive/`git log`
  for anything it references by commit).

**When something ships, record the "why" in the commit message, not this
file's own prose.** Use the `/close-roadmap-item` skill to check the item
off here (a bare `[x]`, not a rewrite into a paragraph). Resist the urge to
leave a factual trail in this file itself; that's exactly how it got long
three times already (837 → 1300+ → 979 → this).

## Path to production

P0 — the code-level *safety-primitive* gates around real money (order
schema verified against the official SDK, typed in-app confirmation
phrase, restricted CORS, real account field names, kill switch + bankroll
persisting across restarts) — is **fully shipped**. That's necessary, not
sufficient, and it does not mean the remaining distance to real capital is
purely operational: most items below are (deployment target, auth model,
sizing/kill-switch numbers, category legal risk), but substantive
code-level work still sits ahead of this checklist too — realtime
data-plane correctness (P0-P2 of a 6-phase plan merged 2026-08-26; P3's
live-gate flip **authorized 2026-08-26** in a reprioritization pass
(ahead of Program 2R, since it's data-completeness work rather than a
trading-decision change) but not yet started; P4-P6 remain open),
economic/strategy validation (investigated 2026-08-26 — the originally-
reported entry-gate adverse-selection defect below turned out not to
reproduce in its original form, replaced by a different, still-open,
currently-unimplemented finding), and canonical decision/execution
semantics (fully open). See
`docs/kalshi-personal-production-execution-program-2026-08-26.md` for how
that work is sequenced, and
`docs/superpowers/plans/2026-08-26-active-tracks-board.md` for the
short, cross-session board tracking which of these tracks is next and
which can run in parallel right now.

- [x] **The event loop stalled for 17-38+ seconds at a stretch, live,
      post-P0-P2.** Found 2026-08-26 gathering Program 1's
      "runtime-measured" evidence (the dev instance was unreachable for
      4.5+ minute stretches). `loop_watchdog` (P0 Task 1's own diagnostic)
      confirmed it: `stall_max_ms` at 17,800-38,000ms, main thread pegged.
      Root-caused via a live `py-spy` stack trace (temporary `SYS_PTRACE`,
      since removed): `candidate_log.population_gate_summary()` fetched
      all 6.2M+ rows of the undeduped, unretained `rejection_events` table
      into Python on every dashboard poll of `GET /api/candidate-log/summary`,
      synchronously on the event loop. Fixed: rewrote the aggregation as a
      single SQL `GROUP BY` (6.2M rows → ~10 result rows) plus a
      `tick_executor` offload - a thread offload alone wasn't enough,
      since building millions of Python row-tuples holds the GIL
      regardless of which OS thread runs it. Live-verified: endpoint
      latency 17-38s+ (blocking everything) → ~6-7s (blocking nothing).
      Commits/PR #35 (`fix/candidate-log-population-summary-blocking-loop`).
      Whale-signal handling itself (`whale_provider.fetch_signals`) was
      confirmed a victim of the stall, not its cause. **Still open**:
      whether `rejection_events`' unbounded growth (undeduped, one row per
      gate check on every rejected candidate, by design) needs real
      retention or a pre-aggregated summary table - the SQL rewrite fixed
      the loop-blocking, not the underlying growth.
- [x] **A second, unrelated event-loop-blocking bug**, found the same day
      while investigating WS-subscription churn (below): three
      `services/whale_calibration/routes.py` routes ran synchronous work
      directly on the event loop on every dashboard poll, proven via
      another live `py-spy` trace. `GET /api/confidence-calibration/status`
      fetched and JSON-parsed every resolved-with-factors signal
      (~70K rows) just to `len()` the result - replaced with
      `signal_log.resolved_with_factors_count()`, a plain `COUNT(*)`.
      `GET .../report` and `POST .../apply` ran the full per-factor bucket
      analysis (`confidence_calibration._bucket_win_rates`) synchronously -
      offloaded via `tick_executor` (a plain thread offload was sufficient
      here, unlike the fix above, since the per-call cost at current scale
      is ~2s, not 18s+). Live-verified: 5 consecutive `/api/state` requests
      all succeeded in 1.5-2.5s each, sustained over 15s, where the app had
      previously been unable to complete a single request. PR #36
      (`fix/whale-calibration-blocking-loop`).

- [x] Runway/exit gates: a position could open with almost no time left
      before its market's close and ride unmanaged to settlement. Fixed via
      `strategy.min_seconds_to_close` (entry-side floor) and
      `strategy.exit_min_seconds_to_close` (force-close once runway drops
      below it, independent of P&L). Commit `2974e42` (2026-08-16).
- [ ] `services/shadow_mode.py` logs what the strategy *would* trade against
      real signal data, but hasn't been run for a real evaluation stretch
      and reviewed — that review, not the code existing, is the actual
      "trust it" gate before ever flipping `trading_enabled`. **Verified
      2026-08-26: `data/shadow_mode.db`'s `shadow_trades` table currently
      holds zero rows.** `mode` has only ever been switched to `shadow`
      twice in this repo's history — commit `715ba42` (2026-08-07,
      incidental to an SDK migration, reverted ~6h later) and `f3691a9`
      (2026-08-09 23:42, whose own message says "for a real evaluation
      stretch"), but that second attempt was reverted back to `paper`
      ~15.5h later (`d1b81e0`, 2026-08-10 15:20) with no shadow trades
      logged.
      Evaluation only runs at all when `mode` is `shadow`/`live`
      (`services/whale_stream/decision_bridge.py`'s `shadow.evaluate()`
      call is gated on exactly that) — this isn't "run it, then review
      it," it's that no sustained run has ever happened, and `mode: paper`
      today means none is in progress.
- [x] Risk enforcement lived only in strategy code, not the execution layer
      (a gate skipped at one call site had no backstop). Fixed 2026-08-22,
      commit `a8de034`: `PaperBroker.open_position()` and
      `KalshiAccountClient.create_order()` now independently refuse when
      `RiskManager` is halted or the exposure cap is exceeded (with an
      `is_closing_order` escape hatch for flatten orders during a halt).
- [x] No "flatten all positions" / emergency-close path existed. Fixed
      2026-08-22, commit `a8de034`: `PaperBroker.close_all_positions()` +
      `KalshiAccountClient.flatten_all()`, wired to
      `POST /api/trading/flatten-all` behind the same typed-confirmation
      mechanism as `/api/trading/enable`.
- [x] No portfolio-wide exposure cap (only per-trade/per-series ones). Fixed
      2026-08-22, commit `a8de034`: `RiskManager.check_total_exposure()`,
      opt-in via `risk.max_total_exposure_pct` (default `null` = no cap).
- [ ] This runs today only under local `ddev` on one machine — no real host,
      TLS domain, process supervisor, or uptime guarantee beyond ddev's dev
      containers. Decide and build a real deployment target before real
      capital depends on this process staying up.
- [x] `data/*.db` had no backup/retention policy. Shipped 2026-08-23:
      `services/backup/` snapshots every `data/*.db` file via
      `sqlite3.Connection.backup()` into `data/backups/`, retention-pruned
      (default 14 gens), runs both in-loop and as a standalone CLI.
      `GET /api/backup/status`/`history`, `POST /api/backup/run`. Sizing
      this against real data also found and fixed an unrelated 5.7GB
      `game_state.db` bug (candlestick payloads re-stored in full, never
      read back) — see `services/backup/README.md`.
- [x] No monitoring/alerting for a kill-switch trip, crash, or connectivity
      loss. Shipped 2026-08-23: `services/alerting/` (transition-based edge
      detection, `data/alert_log.db`, `GET /api/alerts/active`/`history`).
      Delivery is a generic opt-in webhook (`alerting.webhook_url`, unset by
      default) — **which channel to point it at is still an open decision**,
      only the detection/dispatch mechanism itself shipped.
- [ ] Auth is optional, single-operator Google OAuth (`services/auth.py`) —
      fine for "just me," but confirm that's still the model before real
      money sits behind it. No user table, no session-invalidation UI, no 2FA.
- [ ] `advisory.auto_apply_enabled`/`confidence_calibration.auto_apply_enabled`
      have only ever run against paper-mode trade history — confirm what
      should govern either flag (a stricter/zero floor, a manual-only
      posture) before real capital is ever behind the config they're
      tuning. Both currently read `false` in `config/settings.yaml` (they
      have flipped back and forth repeatedly over this repo's history, most
      recently landing off) — the open question is what should govern
      re-enabling them, not "stay on" as this item's original phrasing
      implied, since neither is on right now.
- [ ] Have a human, not a default, set real position-size/kill-switch
      numbers in `config/settings.yaml` before the first live dollar —
      today's defaults were picked for exercising paper-mode logic, not
      sized for real capital. **Concretely, verified 2026-08-26:
      `risk.max_daily_loss_pct` is `0.85`** —
      `RiskManager.check_daily_loss()` (`services/risk_manager.py:162`)
      only halts once **85% of the day's starting bankroll is gone**. As
      configured today this is not a meaningfully protective real-money
      kill switch, not merely an unconfirmed one.
- [ ] **Sports-category contracts are in genuinely live, multi-state legal
      dispute** (`docs/prediction-markets-research-reference.md` Part 3) —
      Nevada/Massachusetts geofencing, other states' suits unresolved.
      Election/economics contracts are practically settled as tradeable;
      sports is not. This app has zero category-level legal-risk awareness
      today (`kalshi.categories` is a volume/topic filter, not a risk one).
      Before real trading is ever enabled: a real answer on category
      selection is needed — see
      `docs/prediction-market-strategy-alignment-plan.md` Part 6.
- [x] `pip-audit` found 20 known vulnerabilities across 5 pinned deps.
      Shipped 2026-08-23: `fastapi` 0.115.0→0.134.0, `pydantic`→2.13.4,
      `cryptography` 43.0.3→50.0.0, `python-dotenv`→1.2.2, dev-only
      `pytest`→9.0.3/`requests`→2.33.0. Verified via a dry-run resolve +
      fresh `pip-audit` pass (zero known vulns) and a post-`ddev restart`
      full suite + live smoke check.
- [ ] **The entry gates select a worse subset than the pool they draw
      from.** Measured 2026-08-17 on KXBTC15M: whale signals resolved 88.8%
      correct across 394 settled signals, but the 12 the gates actually
      traded resolved only 58.3% — adverse selection, not a bad signal
      source. Progress since: `candidate_log`'s population-statistics
      blocker is fixed (new `rejection_events` table, undeduped —
      `GET /api/candidate-log/summary`'s `population_gates` key), and
      per-gate rejections now carry `unit_cost` (closing the "high win
      rate but loses money" blind spot partway — see
      `docs/roadmap-archive-2026-08-23.md` for the full mechanism).
      **Investigated 2026-08-26**
      (`docs/superpowers/research/2026-08-26-economic-strategy-
      effectiveness-status-report.md`): the original 394/88.8% sample and
      the gate configuration that produced it (dollar-notional, replaced
      by contract-count below) both no longer exist — not root-causable
      in its original form. Under the *current* gate, selection is
      currently *better* than population accuracy (opposite direction);
      the real current shortfall (-$169.81 over 90 trades in ~3.2 days)
      traces to a pricing/edge gap, not selection or exit. A banded,
      sample-size-gated cost-aware EV-per-gate pattern was found (the
      0.60-0.95 unit-cost band is negative-EV across every gate with
      enough samples) — real, still open. **Still not done**: the
      banded-EV diagnostic itself (designed, not implemented — Program 2,
      gated on human design-doc approval) and re-verifying all of the
      above against a larger post-realtime-fix sample.
- [x] **Whale threshold switched from dollars to contract count.** Measured
      2026-08-17: a dollar gate was geometrically biased toward
      near-certainty (mean unit cost 0.926, 75.9% of clears in the
      loss-bleeding >=0.95 band). Shipped 2026-08-23:
      `whale_watcher_kalshi.min_notional_usd*` replaced by `min_contracts`
      (default 5,000) end to end (diagnostics, series_watcher, market
      analyst, dashboard Controls panel) — mean unit cost of what clears
      dropped to 0.759. Single biggest measured lever on selection quality.
      (Supersedes, not fixes, the earlier "real `min_notional_usd`
      violations" investigation — that gate no longer exists.)
- [x] **Four-entry gate bypass**, root-caused 2026-08-22:
      `PaperBroker.check_pending_fills()` only ever checked entry gates at
      placement time, not at actual fill time. Fixed by extracting
      `_validate_entry_price()`, called from both `evaluate()` and a new
      `validate_pending_fill()` callback. Commit `6921543`.
- [x] **Settlement projection is a real edge, now acted on.** Verdict
      2026-08-23: `projection_beats_market` on 27,534 observations across
      478 windows (market Brier 0.1046 vs projection 0.0171). Shipped
      `services/settlement_edge_entry.py` (opt-in), a second entry path off
      the index feed's own tick, holding to settlement instead of managing
      via price (`Position.hold_to_settlement`). Commit `8ebd36f`.
- [x] **Every live `/api/config` write silently stripped every comment from
      `config/settings.yaml`.** Found + fixed 2026-08-23: `config_store.py`
      now round-trips through `ruamel.yaml` instead of PyYAML
      `safe_load`/`safe_dump`, preserving comments byte-for-byte. Also fixed
      a related latent gap: `get()` never caught a YAML parse error on a
      torn read (only `OSError`) — now also catches `YAMLError`.

## P4 — Nice-to-haves

- [x] **Recurring xdist-parallel test flakiness, `ci/woodpecker/push/tests-pytest`.**
      Root-caused and fixed 2026-08-27 across two PRs, both merged to `main`:
      **PR #119** (`fix/xdist-parallel-test-isolation`) added autouse reset
      fixtures for `main.state["discovery_cache"]`, `main.config_store`, and
      `services/http_client.py`'s `_rest_class_stats`/`_endpoint_window` -
      shared module-level singletons that xdist's work-stealing scheduler
      exposed by not preserving file-definition order within a worker.
      **PR #125** (`fix/xdist-trading-gate-isolation`) found and fixed a
      second instance the same day: `test_enable_trading_succeeds_with_
      correct_phrase_and_connected_account` flips `main.account.
      trading_enabled` True via the real `/api/trading/enable` route and
      never reset it - `monkeypatch`'s auto-restore covered `_client` but not
      a real-route mutation, leaking into whichever test xdist scheduled
      next. Fixed with the same autouse-fixture idiom PR #119 established.
      Both confirmed via real `pytest -n 4` full-suite runs (12 consecutive,
      zero recurrence) and via deterministic single-worker reproduction of
      the exact adversarial test pairs. General lesson worth keeping: a
      shared module-level singleton needs an autouse reset fixture the
      moment more than one test can mutate it, not just a manual reset at
      each mutating test's own setup - `_reset_trading_state()` had existed
      since 2026-08-22 and was still called at setup-only in the test that
      leaked.
- [ ] **Move analytics/advisory computation out of the live tick loop —
      dump the underlying data and let external tooling analyze it.**
      Direct instruction (2026-08-21). Queued behind the main.py
      modularization, which has since progressed significantly (see
      `static/status.html`) — worth re-evaluating whether this is still
      needed once that's fully settled, since the modularization itself may
      have addressed part of the original latency concern.
- [x] **Market-Native strategy removed entirely.** Was queued here as
      "consider removing" per direct instruction (2026-08-22, "over-
      complicating things") — **already shipped**, found stale during this
      2026-08-23 compression pass: commit `e2dcf33`, "Phase 1/9: Remove the
      Market-Native strategy entirely," is on `main`. `services/market_strategy.py`/
      `market_strategy_calibration.py` no longer exist. This item had not
      been checked off at the time; the backfill-a-phase-entry follow-up is
      now moot — `status.html` retired 2026-08-26 (`docs/status-archive-
      2026-08-26.html`), commit `e2dcf33` speaks for itself in `git log`.
- [x] **Split `services/analytics/` further** (advisory + whale calibration
      each their own module). Shipped 2026-08-22 as Phases 2-4/9 of the
      continued-modularization pass: `services/whale_calibration/`,
      `services/advisory/`, `services/backtest/`. Residual scope
      (regime/candidate-log/cross-strategy/market-analyst/series-evaluator)
      documented in `services/analytics/README.md`.
- [ ] **Retroactively move already-stable flat files into their concern's
      folder** — `paper_broker.py` → `services/position/`,
      `strategy_engine.py` → `services/position_management/`,
      `config_store.py` et al. → `services/config/`, etc. Queued
      2026-08-22 (`docs/next-session-pickup-2026-08-22.md`) but never
      actually added here until this 2026-08-23 review found the gap —
      still genuinely open, all three still sit flat in `services/` as of
      this check. Deliberately not done in-line with other modularization
      work: real import-site churn across the whole codebase for files
      that already work, queued once the new-file convention (package +
      `routes.py` + reference doc) had proven itself on enough new
      modules first.
      **Partially spec'd 2026-08-27** (independently arrived at, then found
      to converge with this item):
      `docs/superpowers/specs/2026-08-27-backend-services-modularization-
      design.md` covers `config_store.py` et al. → `services/config/`, plus
      `account_positions.py` → `services/position/`,
      `trade_analytics.py`/`regime_analytics.py`/`suggestion_decisions.py`
      → `services/history/`, and a new `services/reset/` — scoped to
      dashboard-facing files only, by explicit user choice that session.
      `paper_broker.py` → `services/position/` and `strategy_engine.py` →
      `services/position_management/` remain open, deferred as
      core-trading-engine work, not part of that spec.
- [ ] **Flatten the config surface** — too many independent knobs to track
      which are load-bearing. Direct instruction (2026-08-22); the 3-day
      config-drift incident that session is direct proof this is real, not
      hypothetical. `strategy.*` alone has ~30 fields, doubled by the
      (now-removed) `market_strategy.*` set, layered again by
      `strategy_overrides.by_category`/`by_series`. Needs an audit pass
      before deciding what to cut/consolidate — not started.
- [ ] **Separate the frontend from the backend completely.** Direct
      instruction (2026-08-22). Serving-level separation already exists
      (`main.py` is API-only; `web` serves `static/*.html` directly). What
      shipped toward this 2026-08-22: `index.html`'s inline
      `<style>`/`<script>` extracted into a real `frontend/` npm project
      (`esbuild` + `eslint`, real ES modules) bundled to
      `static/js/dashboard.bundle.js` — `index.html` 7,716 → 1,073 lines.
      Full detail (the handler-scope/reassignment/import-graph correctness
      issues this conversion surfaced, plus a genuine pre-existing bug it
      caught) is in `docs/roadmap-archive-2026-08-23.md`. **Still not
      started**: the actual multi-page split (browser nav replacing
      `showView()`'s 7-tab toggle) and the bigger question of whether a
      real framework/separate repo is ever warranted — planning only.
      **Planning shipped 2026-08-25:** the framework question is answered —
      Preact + `@preact/signals` + `htm` (a micro-framework, not the SPA
      rewrite phase 118 rejected), strangler-fig migration inside
      `frontend/src/js/` (`core/`, `lib/`, `charts/`, `panels/<name>/`,
      `legacy/`), a schema-driven Config tab backed by a new
      `GET /api/config/schema` + validated `POST /api/config`, uPlot charts,
      and CI-owned import-graph/ownership/bundle-budget guards. Research:
      `docs/superpowers/research/2026-08-25-frontend-modularization-research.md`;
      spec: `docs/superpowers/specs/2026-08-25-frontend-modularization-design.md`;
      plan (T1a–T9, five PR groups):
      `docs/superpowers/plans/2026-08-25-frontend-modularization.md`, executed
      via `.claude/skills/frontend-modularization-task/SKILL.md`. The
      multi-page split stays a separate follow-up the panel contract enables.
- [ ] **Per-module data-consumption audit + report.** Direct instruction
      (2026-08-22) — trace every module's data sources (REST/WS/SQLite/
      in-memory) and flag anywhere a cheaper/fresher source should be used.
      **The systematic, one-organized-report version is still not
      started.** Of the four originally-named findings, three shipped
      2026-08-23 (uncapped `asyncio.gather` in market hydration → one
      batched call; `series_evaluator.evaluate_pending()` per-series SQLite
      connections → one connection; `regime_analytics.by_category()` /
      `trade_analytics.compute_summary()` each computed twice on identical
      input → hoisted once). The fourth (`market_history.snapshots`'
      unread `spread`/`volume_24h`/`time_to_close_sec` columns) was
      investigated and deliberately left as-is — disclosed forward capture
      per `docs/advisory-engine-plan.md` §9, not dead-code waste; the real
      gap is no consumer was ever built for it.
- [ ] **Use all available *relevant* data before trimming the rest.**
      Direct instruction (2026-08-24, two-phase: read back what's already
      collected and relevant first, *then* cut what's genuinely unused —
      not "use every field regardless of relevance" either).
      `event_schedule.py`'s resolver is now wired in (resolved 2026-08-24,
      see `status.html` phase 139). Still open:
      `services/signal_log.py`'s `resolved_signals_with_factors()` still
      silently drops the already-stored `series` column before confidence
      calibration ever sees it. **Guardrail**: trimming must not close this
      app off from discovering new markets — an exchange-wide data layer can
      be "unused" today and still be the only way a not-yet-watchlisted
      market is ever discovered. Not started — planning item only.
- [x] **Real REST rate limiting** — history/position sections were making
      REST calls to monitor state every tick instead of relying on streams.
      Root-caused 2026-08-23 to the market-hydration path making an
      unconditional, uncached batched call every tick (the concurrency half
      of this had already been fixed; the caching half hadn't). Fixed by
      routing it through the same `_cached_market_fetch` (300s TTL) the
      other branches already used, plus a size cap on that cache.
- [ ] **Retrospective sweep: which targeted datapoints/logic were built on
      a wrong understanding of the Kalshi API or the streaming/REST
      split.** Direct instruction (2026-08-22) — distinct from the
      data-consumption audit above (source vs. meaning). This project has a
      real track record of exactly this mistake, always caught reactively
      (see CLAUDE.md's "Bug pattern to watch for" and `docs/kalshi/
      CHEATSHEET.md`). A 2026-08-24 ad hoc investigation session found a
      confirmed bug/gap in nearly every area poked at without deep
      digging — see `docs/roadmap-archive-2026-08-23.md` for the list —
      which is itself the evidence this sweep is overdue. Not started.
- [ ] Consider a dedicated charts/graphs module, possibly server-rendered
      via Plotly/Matplotlib. Direct instruction (2026-08-22). Queued behind
      higher-priority work; two separable questions (does chart-data
      assembly deserve its own module; does server-side rendering change
      anything for the better) not yet answered.
- [ ] Two deferred next-steps from
      `docs/todo-2026-08-14-heuristics-audit-and-exit-tuning.md`: a
      time-til-close exit factor, and folding mutually-exclusive-pair order
      flow into sentiment analysis (detection + entry-gating already ship;
      only the sentiment-merge half is deferred).
- [ ] Wash-trading detection (`docs/platform-deep-scan-findings-2026-08-10.md`
      Finding 5) — the one of 7 cited strategy/risk gaps never built.
- [ ] Regime-aware **live entry gating** — `services/regime_analytics.py`
      stays advisory-only by design (real plumbing complexity, deliberately
      not risking the live trading path twice in one session).
- [ ] Revisit the 5s dashboard polling model once any Advanced view needs
      sub-poll freshness — ETag/304 already makes an unchanged poll nearly
      free, so this is push-vs-poll latency, not payload waste. Backend
      signal latency is already better: `trade_stream` feeds whale-signal
      detection directly, no poll wait.
- [ ] Notifications (email/push) for real trades, kill-switch triggers, or a
      tracked whale crossing the avoidance threshold — see "Path to
      production" above.
- [ ] **`docs/kalshi/` mirror is stale relative to upstream — 111 of 197
      verbatim-mirrored pages drifted** since the 2026-08-16 full-index
      fetch, found live the moment QCP Task 12's new content-drift checker
      (`tools/kalshi_docs_drift.py --check`) was pointed at the real
      internet for the first time (2026-08-24). Several are schema-
      relevant, not cosmetic: a new `x-go-type-skip-optional-pointer`
      field and `exchange_shard_index` default/description changes across
      many order/portfolio endpoints, a new "Subaccounts" section added to
      `rfqs.md`, a formal `<Warning>`/`<Note>` deprecation/rate-limit
      callout added to several pages. Re-mirroring 111 pages is real,
      separate work (review each diff, not a bulk overwrite) — out of
      Task 12's own scope, which was building the *detector*, not doing
      the refresh. The weekly scheduled check will now catch future drift
      automatically; this item is the one-time backlog of what it already
      found on day one.
- [x] **`docs/kalshi/llms.txt`'s mirrored index is stale relative to the
      real upstream index — 11 pages exist upstream with no local mirror**
      — resolved 2026-08-25 during the Phase A completion gate (A17): all
      11 fetched from their exact upstream URLs, llms.txt refetched
      (220 → 231 entries), manifest/README regenerated, `--check` clean
      (commit 72622c7 on the initiative branch). Original finding:
      found live the moment Kalshi Integration Phase A Task A2's new index-
      drift checker (`tools/kalshi_docs_sync.py --check`) was pointed at
      the real internet for the first time (2026-08-24): `Get Weather
      Index`, `Get`/`Set Target Balance Allocation`, and 8 margin
      isolated/cross exit-trigger endpoints
      (`margin-rest/exit-triggers/*`). None are currently called by any
      production code path (confirmed via `docs/kalshi/used-contracts.json`
      before treating this as backlog rather than a blocker — Finding A's
      own "global mirror completeness is an initiative goal, not a reason
      to block unrelated migration" scope note applies). Mirroring them is
      real, separate work (fetch, verify, commit) — out of Task A2's own
      scope, which was building the *detector*. The weekly scheduled check
      now catches future index drift automatically alongside existing
      content drift; this item is the day-one backlog of what it found.
- [ ] Finalize the app name — "Nessie" vs. "Operation Deepscan" still open.
- [ ] Clicking a logged position/signal/decision should show whether that
      specific position ultimately won/lost, not just current market state —
      needs new signal→trade→outcome correlation that doesn't exist today.
- [x] **close_time-mutability gap** — a permanent fix via Kalshi's
      `market_lifecycle_v2` WS channel. Half shipped 2026-08-17 (in-memory
      overlay). Second half shipped 2026-08-23:
      `market_catalog.apply_lifecycle_update()` applies `close_ts`/`status`
      straight to the persisted SQLite row on `close_date_updated`/
      `determined`/`settled` events, and `determined` now also triggers all
      four settlement-resolution functions (idempotent, safe alongside the
      existing REST-tick path) — closing the gap where a ticker rotating
      off the live watchlist before settling (routine for KXBTC15M-shaped
      series) never got resolved at all.
- [x] **`services/alerting/alerting.py`'s "crash" category had no
      resolution path at all.** Found live 2026-08-24 while building QCP
      Task 18's System Health UI (`_check_transition`, the only caller of
      `resolve_category`, only covers the two continuously-monitored
      conditions - `kill_switch`, `trade_stream_connectivity`/
      `index_stream_connectivity`; `task_supervisor.py`'s
      `record_alert("crash", ...)` had no matching resolution trigger
      anywhere, and `routes.py` exposed no manual route either - so
      `active_alerts()` included every crash forever). Fixed same day: two
      independent mechanisms, both in `alerting.py` - `expire_old_alerts()`
      ages each unresolved "crash" row out on its own once older than
      `alerting.crash_auto_resolve_after_sec` (default 1800s, wired into
      `check_and_alert`'s existing per-tick cadence via
      `_expire_stale_crash_alerts`), plus `resolve_alert(alert_id)` +
      `POST /api/alerts/{id}/resolve` for manual acknowledgment. See
      `services/alerting/README.md`'s "Crash-alert resolution" section.
      **Correction to the original write-up:** it claimed this pinned
      `/api/quality/summary`'s overall `status` to `"error"` - checked
      against the actual code while designing the fix, and that's false:
      `status`/`counts` are composed only from `observability.
      runtime_findings()` + `storage_health.storage_findings()`,
      `alerting.active_alerts()` is exposed as a separate field that never
      feeds into `status`. The real effect was `GET /api/alerts/active`/the
      dashboard's "Alerts: N active" line staying wrong forever, which is
      what this fix actually corrects. Surfaced two new, still-open
      findings while investigating the mistaken claim - filed as their own
      items directly below rather than fixed here (out of this item's
      approved scope).
- [ ] Whether a critical active alert (`kill_switch`, `crash`) should be
      able to drive `/api/quality/summary`'s `overall_status()` to
      `"error"` at all — today it can't: `status`/`counts` are computed
      only from `observability.runtime_findings()` +
      `storage_health.storage_findings()`; `alerting.active_alerts()` is
      exposed as a separate, uncombined `alerts` field. Surfaced
      2026-08-24 while fixing the crash-alert resolution item above; not
      decided — needs its own pass on severity-mapping semantics before
      changing a field other code/tests may already read as "ok".
- [ ] **`services/kalshi_trade_ws.py`'s `dropped_messages` counter is set
      once (`__init__`) and only ever incremented (one call site) — nothing
      in the codebase ever resets it.** Found live 2026-08-24 investigating
      the item above: `/api/quality/summary` was observed returning
      `status: "error"` with zero active alerts, traced to this counter
      (5,985 dropped messages on `trade_stream`) via `observability.
      _dropped_messages_findings` (severity `"error"`, no expiry/decay).
      Once a single message ever drops for the lifetime of a
      `trade_stream`/`index_stream` object, `overall_status()` stays
      `"error"` until the next full process restart — almost certainly the
      real mechanism behind the "105 minutes stuck red" observation the
      original crash-alert item above mis-attributed to alerting. This
      directly undermines CLAUDE.md's own "start every investigation at
      `/api/quality/summary`" guidance once a session has been up long
      enough for a single drop to occur. Not investigated further or
      fixed — needs its own root-cause pass (does a reconnect recreate the
      underlying stream object or just resume it? should the finding decay/
      window instead of being permanent?).
- [x] **`min_seconds_to_close`/`exit_min_seconds_to_close` were configurable
      only by hand-editing `config/settings.yaml` — never wired into the
      dashboard Controls panel**, unlike their immediate siblings
      (`close_window_sec`/`special_market_min_seconds_to_close`/
      `take_profit_pct`/`stop_loss_pct`). Found live 2026-08-24 (direct
      report: "most of my trade log says 'runway exhausted' i dont seem to
      be able to configure that runway") — confirmed empirically first:
      73.5% (83/113) of closed trades in the live `paper_broker.db` close
      via `runway_exhausted`, real not exaggerated, and a structural
      consequence of `take_profit_pct`/`stop_loss_pct` both being `null`
      plus `auto_exit` firing only twice. Fixed same day: both fields added
      to `frontend/src/js/config-panel.js` + `static/index.html`'s Strategy/
      Position-Management sections, mirroring the exact existing pattern;
      live-verified via the selenium-chrome grid (correct values render, a
      direct `POST /api/config` round-trip confirmed the save path, zero
      console errors) — the backend already fully supported both fields,
      only the UI was missing.
- [ ] **Whether the runway-exhausted-dominant exit mix is actually costing
      real wins.** Direct follow-up report, same session: "many of these
      exit managed positions ended up winning if i had just held to
      settlement." Investigation in progress - not yet quantified against
      real settlement outcomes.
- [x] **`.woodpecker/quality-frontend-build.yml`'s bundle-sync check was a
      silent no-op — fixed 2026-08-25 by removing it.** Found live
      2026-08-24: `static/js/dashboard.bundle.js` is gitignored/untracked,
      so `git diff --exit-code` on it always reported "no difference"
      regardless of real corruption. Resolved by removing the check rather
      than re-committing the bundle: `quality-browser-e2e.yml`'s own
      `build-frontend` step (and this workflow's own `npm run build`)
      already rebuild the bundle from current source on every push, so a
      broken build fails loudly on its own and a stale-vs-source drift
      can't reach either check undetected.
- [x] **Persisted static-finding observation series** (I11 spec, I12
      plan, implemented 2026-08-26; renamed 2026-08-27) — a read-only
      `tools/quality_ratchet.py` module (standalone workflow tooling, not
      application code) that tracks `tools.quality_audit` static findings'
      identity/persistence/suppression state over time, with zero GitHub
      writes and zero new credentials. Invoked externally only
      (`python -m tools.quality_ratchet`) — corrected mid-implementation,
      same day (2026-08-26), after the module was originally built wired
      into the trading app (`main.py`'s tick loop, `config/settings.yaml`,
      app-owned API routes): that coupling was a real misunderstanding of
      the feature's own purpose (workflow/tooling quality control, not
      application behavior) and was fully reversed — see
      `docs/superpowers/plans/2026-08-26-autonomous-quality-coordination.md`'s
      Task 15 and `CLAUDE.md`'s "Workflow/tooling and application code must
      never overlap" standing rule for the full account. No API surface, no
      dashboard view; inspect `tools/quality_ratchet_data/quality_
      ratchet.db` directly or run the module's own CLI. **Renamed
      2026-08-27, module and behavior otherwise unchanged:** a further
      direct correction clarified that "Autonomous Quality Coordination"
      was never supposed to mean this — it names a *different* tool
      (project-manager/janitor over this repo's own engineering workflow,
      not an observer of the trading app's own static findings). This
      module kept its implementation under the new `quality_ratchet` name;
      see the next item for what "AQC" now refers to.
- [ ] **Autonomous Quality Coordination (workflow-health)** — spec'd and
      implemented 2026-08-27:
      `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-
      workflow-design.md`,
      `docs/superpowers/plans/2026-08-27-autonomous-quality-coordination-
      workflow.md`. An automated project-manager/janitor over this repo's
      own engineering workflow — branch/PR/CI lifecycle, `superpowers`
      plan/ledger execution health, standing-rule/baseline hygiene, and
      (as context, not an audited target) the trading app's own
      self-reported diagnostics — plus a narrow, three-action deterministic
      cleanup layer (`git worktree prune`, deleting merged-and-remote-
      deleted local branches, deleting a finished plan's SDD scratch
      workspace). 10 of the plan's 11 tasks are done and functional
      (`tools/quality_coordination.py` + `tools/coordination_engine.py`);
      the sole remaining item is Task 11 — one small application-side
      change (`services/auth.py`'s `PUBLIC_PATHS` gains the three read-only
      diagnostic routes AQC reads as context) — deliberately gated behind
      live, in-the-moment human approval per the plan's own Task 11 text,
      not routine follow-up work. Everything else already touches only
      `tools/`, git, and the filesystem.

## Shipped

Everything else has shipped — P0 (safety gates), the full dashboard/UX
overhaul, active position management, whale-tracking maturity, reliability/
engineering hygiene (test suite + CI, official SDK migration, rate-limit
correctness), the full main.py modularization (5,450 → 1,716 lines across 7
phases) plus a further continued-modularization pass on top of that
(1,716 → 1,519 lines, including the analytics/advisory/whale-calibration
splits and the Market-Native strategy's full build-then-removal arc), and
dozens of live-reported bugs found and fixed session by session.
`static/status.html` (`/status`) is the complete, phase-by-phase record —
140+ phases and counting. For the detailed prose version of this file as it
stood before each condensing pass, see `docs/roadmap-archive-2026-08-09.md`,
`docs/roadmap-archive-2026-08-16.md`, and `docs/roadmap-archive-2026-08-23.md`.
