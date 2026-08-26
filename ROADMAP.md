# Roadmap: comprehensive, dummy-proof paper trading terminal

Guiding principle: someone who has never touched a prediction market or a
trading interface before should be able to open this app and understand
*what they're looking at*, *why it did what it did*, and that *no real money
is ever at risk* unless they deliberately configure it to be. Everything
below is measured against that bar, not against "does it technically work."

This is a living to-do list, not a snapshot — check items off in place and
add new ones as they turn up. **Keep this file short.** Only genuinely open
items live here; the moment something ships, it comes off this list
entirely (a one-line pointer at most) — the narrative belongs in
`static/status.html`, not here. Four other docs carry the "what happened
and why":

- **`static/status.html`** (`/status`) — the actively-maintained
  backward-looking record, phase by phase, with the full "why," verification
  steps, and bugs found along the way. The primary source for "what
  happened and why" for anything shipped from now on.
- **`docs/roadmap-archive-2026-08-09.md`** — frozen snapshot #1 (pre-condense,
  837 lines).
- **`docs/roadmap-archive-2026-08-16.md`** — frozen snapshot #2, covering
  2026-08-09 → 2026-08-16.
- **`docs/roadmap-archive-2026-08-23.md`** — frozen snapshot #3, covering
  2026-08-16 → 2026-08-23 — the full evidence/numbers/fix-narrative behind
  every item condensed below lives here (or in `status.html`/`git log` for
  anything it references by commit).

**When something ships, update `status.html`, not this file's own prose.**
Use the `/sync-status-docs` skill — it checks the item off here (a bare
`[x]`, not a rewrite into a paragraph) and adds the matching timeline
phase to `status.html` in one pass. Resist the urge to leave a factual
trail in this file itself; that's exactly how it got long three times
already (837 → 1300+ → 979 → this).

## Path to production

P0 — the code-level gates around real money (order schema verified against
the official SDK, typed in-app confirmation phrase, restricted CORS, real
account field names, kill switch + bankroll persisting across restarts) —
is **fully shipped**. That's necessary, not sufficient: going from "the
gate works" to "flip it for real" still has these open operational
questions.

- [x] Runway/exit gates: a position could open with almost no time left
      before its market's close and ride unmanaged to settlement. Fixed via
      `strategy.min_seconds_to_close` (entry-side floor) and
      `strategy.exit_min_seconds_to_close` (force-close once runway drops
      below it, independent of P&L). Commit `2974e42` (2026-08-16).
- [ ] `services/shadow_mode.py` logs what the strategy *would* trade against
      real signal data, but hasn't been run for a real evaluation stretch
      and reviewed — that review, not the code existing, is the actual
      "trust it" gate before ever flipping `trading_enabled`.
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
      read back) — see `services/backup/CHEATSHEET.md`.
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
      have only ever run against paper-mode trade history — confirm they
      should stay on (or get a stricter/zero floor) before real capital is
      ever behind the config they're tuning.
- [ ] Have a human, not a default, set real position-size/kill-switch
      numbers in `config/settings.yaml` before the first live dollar —
      today's defaults were picked for exercising paper-mode logic, not
      sized for real capital.
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
      source. Not yet root-caused to a specific gate. Progress since:
      `candidate_log`'s population-statistics blocker is fixed (new
      `rejection_events` table, undeduped — `GET /api/candidate-log/summary`'s
      `population_gates` key), and per-gate rejections now carry `unit_cost`
      (closing the "high win rate but loses money" blind spot partway — see
      `docs/roadmap-archive-2026-08-23.md` for the full mechanism). **Still
      not done**: a banded, sample-size-gated cost-aware win rate per gate
      (the real next step, needs the new `unit_cost` data to accumulate
      first) and the root-cause itself.
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
      been checked off or logged in `status.html` — worth a `/sync-status-docs`
      pass to backfill the phase entry.
- [x] **Split `services/analytics/` further** (advisory + whale calibration
      each their own module). Shipped 2026-08-22 as Phases 2-4/9 of the
      continued-modularization pass: `services/whale_calibration/`,
      `services/advisory/`, `services/backtest/`. Residual scope
      (regime/candidate-log/cross-strategy/market-analyst/series-evaluator)
      documented in `services/analytics/CHEATSHEET.md`.
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
      `routes.py` + `CHEATSHEET.md`) had proven itself on enough new
      modules first.
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
      `services/alerting/CHEATSHEET.md`'s "Crash-alert resolution" section.
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
- [ ] **Persisted quality-coordination observation series** (I11 spec, I12
      plan) — a read-only `services/quality_coordination.py` module that
      tracks `tools.quality_audit` static findings' identity/persistence/
      suppression state over time and exposes it via
      `GET /api/quality/coordination`, with zero GitHub writes and zero new
      credentials. Fully specified and planned as 9 bite-sized TDD tasks but
      **not implemented** — the 2026-08-25/26 autonomous-quality-
      coordination investigation (`docs/superpowers/research/2026-08-25-
      autonomous-quality-architecture-decision.md`) measured **zero**
      durable `main`-level findings needing escalation across its 41-hour
      sample and concluded report-only stays correct until new evidence
      says otherwise — building even this report-only observation series is
      queued nice-to-have, not urgent, and a future write-lane (GitHub
      issues/draft PRs) is explicitly a separate, later decision requiring
      its own re-verification, not a default next step. Start here:
      `docs/superpowers/plans/2026-08-26-autonomous-quality-coordination.md`.

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
