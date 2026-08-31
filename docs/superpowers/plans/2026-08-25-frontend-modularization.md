# Frontend Modularization Execution Plan

> **Agentic execution — corrected 2026-08-31 catch-up review:** `.claude/skills/plan-task/`
> (which this line previously pointed at, itself the successor to the bespoke
> `frontend-modularization-task` skill folded into it 2026-08-28) no longer exists — it was
> deleted the same day (commit `05faa3b`) as redundant with `superpowers:executing-plans`.
> Use `superpowers:executing-plans` per CLAUDE.md's current Toolchain section. Reconstruct
> progress from current HEAD, execute exactly one numbered task, verify, commit, report,
> and stop.

**Goal:** Turn the 13-module, single-import-cycle dashboard frontend into owned, testable
panels on Preact + signals + htm behind a schema-driven Config tab and a real charts module —
one strangler-safe commit at a time, with every guard CI-owned.

**Spec:** `docs/superpowers/specs/2026-08-25-frontend-modularization-design.md`
**Research:** `docs/superpowers/research/2026-08-25-frontend-modularization-research.md`

## Global constraints

- Current HEAD is implementation truth; if HEAD already does a step, mark it done, don't redo it.
- Every task leaves `main.js` bootable with the remaining `legacy/` modules → one `git revert` rolls it back.
- One container id ↔ one renderer. Nothing outside `legacy/` imports `legacy/`.
- `fetchJSON('/literal/path', …)` stays the only API-call shape (the contract scanner keys on it).
- Displayed financial figures come from backend-named fields, never re-derived (CLAUDE.md).
- Real trading stays disabled; `POST /api/config`'s three refusals keep their exact `detail` strings.
- No tests against live `data/*.db`; browser tests use the isolated E2E server.
- Targeted local verification only (`.claude/rules/branching-and-ci.md`); Woodpecker is exhaustive. Tasks that touch `.woodpecker/` run `cd frontend && npm run check` locally (guardrails exception).
- One task = one independently reviewable commit; PR groups below merge to `main` in order.

## PR groups

| PR | tasks | branch prefix |
|---|---|---|
| A | T0 (this plan) | `docs/frontend-modularization-design` |
| B | T1a → T1b → T1c → T2 → T3 | `refactor/frontend-foundation` |
| C | T4a → T4b → T5a → T5b | `feat/config-schema` |
| D | T6 → T7a → T7b → T7c | `refactor/frontend-charts-history` |
| E | T8a → T8b → T8c → T8d → T8e → T9 | `refactor/frontend-strangler-finish` |

---

## T0 — Research, spec, plan, orchestrator skill

- [x] `docs/superpowers/research/2026-08-25-frontend-modularization-research.md`
- [x] `docs/superpowers/specs/2026-08-25-frontend-modularization-design.md`
- [x] this plan
- [x] `.claude/skills/frontend-modularization-task/SKILL.md` + router entry in `.claude/rules/quality-capabilities.md`
- [x] `ROADMAP.md` pointer under "Separate the frontend from the backend completely"

---

## T1a — Guards first, stale docs (Python/CI only; zero JS change)

**Modify**
- `tools/quality_audit/frontend_contract.py:141` — `glob("*.js")` → `rglob("*.js")`.
- `tests/test_frontend_api_contract.py` — add a nested `frontend/src/js/panels/x/index.js` case.
- `tools/quality_audit/__main__.py` — register the new scanner.
- `tools/quality_audit/baseline.json` — accept today's findings with a dated `notes` entry
  (13 `frontend-import-cycle-member:*`, 197 `frontend-window-export:*`, inline/string handler
  names); these are **ratchets**, removals are expected every task.
- Stale docs — **corrected 2026-08-31 adversarial review, two rounds** (first pass fixed
  the `config_bounds.py` path and dead orchestration pointer elsewhere in this doc but
  missed this specific list; second pass caught the remainder): `CLAUDE.md:148` (not
  `:292-294` — the file was rewritten smaller since this plan was written, "no framework
  build" language now lives there); `frontend/src/js/shared-utils.js:5-18`;
  `frontend/src/js/main.js:12-17`; `.github/workflows/quality.yml:42-43` (delete the
  no-op diff step); `tools/quality_audit/source.py:25-27`; `ROADMAP.md:206` (6 views).
  **Dropped, not replaced**: the original `polling-and-websocket.js:43-47` citation (no
  "committed bundle" claim exists anywhere in that file today — re-grep for the actual
  location at execution time if this specific staleness still needs fixing) and all
  three `.claude/skills/{frontend-verification,integration-audit,final-verification}/
  SKILL.md` citations — none of those three skill directories exist anymore; nothing
  there to correct.

**Create**
- `tools/quality_audit/frontend_graph.py` — parse `import … from './x.js'` edges under
  `frontend/src/js/**`; Tarjan SCC; layer rules (`panels → core|lib|charts|panels/support`,
  `core → lib`, `charts → lib`, `legacy → legacy|core|lib|charts`, `main.js → *`); count
  `^window\.\w+\s*=` per file; count inline `on*=` in `static/index.html` and `on*="` in JS
  strings. Findings: `frontend-layer-violation:<from>-><to>`,
  `frontend-import-cycle-member:<module>`, `frontend-window-export:<name>`,
  `frontend-inline-handler:<fn>`, `frontend-string-handler:<module>:<fn>` (all error/high).
- `tests/test_frontend_graph.py` — synthetic repos (a cycle, a layer violation, a clean tree).

- [ ] TDD the scanner; register; baseline.
- [ ] Fix the stale docs.
- [ ] Verify: `python3 -m pytest tests/test_frontend_graph.py tests/test_frontend_api_contract.py -q`;
  `python3 -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json` → exit 0;
  `python3 -m pyflakes tools/quality_audit`.
- [ ] Commit: `quality: guard the frontend import graph and fix scanner/doc blind spots before restructuring`

**Acceptance** — a nested JS file is seen by the contract scanner; a synthetic cycle fails
the new scanner; CI is green with the baseline; no doc still claims "no bundler" or a
"committed bundle".

---

## T1b — Runtime foundation (no behaviour change)

**Modify**
- `frontend/package.json` — `"type": "module"`; `dependencies`: `preact ^10.29`, `@preact/signals ^2.11`, `htm ^3.1`, `uplot ^1.6`; `devDependencies` + `preact-render-to-string ^6.7`; scripts:
  `build` = `esbuild src/js/main.js --bundle --format=iife --target=es2022 --sourcemap --outfile=../static/js/dashboard.bundle.js`,
  `watch` = `npm run build -- --watch`, `lint` = `eslint src/js test`,
  `test` = `node --test 'test/**/*.test.js'`, `size` = `node scripts/check-bundle-size.mjs`,
  `check` = `npm run lint && npm test && npm run build && npm run size`; `description` rewritten.
- `frontend/package-lock.json` (via `npm install`).
- `frontend/eslint.config.js` — `export default`; second block for `test/**` + `scripts/**`; `no-unused-vars: warn`; header rewritten (there *are* runtime deps now).
- `.gitignore` — `static/js/dashboard.bundle.js.map`.
- `.woodpecker/quality-frontend-build.yml` — remove the path filter; steps `npm ci`, `npm run lint`, `npm test`, `npm run build`, `npm run size`; rewrite the header.
- Branch protection: add `ci/woodpecker/pr/quality-frontend-build` to required contexts (`gh api -X PUT repos/thesneakattack/kalshi-whale-poc/branches/main/protection --input <file>`); update `docs/woodpecker-ci.md` and `.claude/rules/branching-and-ci.md:105-117`.
- `static/project-manifest.json` — regenerate on the host.

**Create**
- `frontend/src/js/core/api.js` (`fetchJSON` + `ApiError`; legacy `shared-utils.js` re-exports it),
  `core/state.js`, `core/mount.js`; `frontend/src/js/lib/format.js`, `lib/side-adjusted-price.js`
  (legacy `shared-utils.js` re-exports `sideAdjustedPrice` — single owner preserved).
- `frontend/test/support/render.js`; `frontend/test/lib/format.test.js`; `frontend/test/core/state.test.js`.
- `frontend/scripts/check-bundle-size.mjs` + `frontend/bundle-budget.json` (`{"max_gzip_bytes": 100000}`).
- `frontend/CHEATSHEET.md`.

- [ ] Implement; `cd frontend && npm install && npm run check`.
- [ ] `ddev exec -s web node --version` (≥ 22) and confirm the watch daemon still rebuilds.
- [ ] `python3 -m tools.project_manifest --write static/project-manifest.json --repo-root .` then `--check`.
- [ ] Apply the branch-protection change and verify with `gh api …/protection | jq .required_status_checks.contexts`.
- [ ] Commit: `build(frontend): add Preact/signals/htm/uPlot, node --test harness, bundle budget; make frontend-build a required check`

**Acceptance** — bundle still boots the unchanged dashboard (`legacy` untouched);
`npm test` runs ≥ 2 test files; size check prints raw/gz/budget; the pipeline runs on a
backend-only push and appears in required contexts.

---

## T1c — Mechanical move to `legacy/`

- [ ] `git mv frontend/src/js/*.js frontend/src/js/legacy/` (old `main.js` → `legacy/bootstrap.js`); new 1-line `src/js/main.js` importing it; rewrite relative import paths.
- [ ] `tools/quality_audit/baseline.json` — path-based finding ids updated (same findings, new paths).
- [ ] Prove zero behaviour change: `diff <(sed 's#src/js/legacy/#src/js/#' new-bundle) old-bundle` is empty apart from the entry comment.
- [ ] `npm run check`; `python3 -m tools.quality_audit …` exit 0; manifest regenerated.
- [ ] Commit: `refactor(frontend): move the 13 modules under src/js/legacy/ unchanged`

---

## T2 — Bridge + pilot panel: System Health

**Modify** `legacy/polling-and-websocket.js` (+`publishState(state)` after the JSON parse; delete the `loadSystemHealth` call + import), `legacy/bootstrap.js` (`showView` also sets `currentView.value`), `legacy/config-panel.js` (`pollIntervalMs.value = …` next to `scheduleRefreshTimer`), `static/index.html` (wrap `system-health-summary`/`-findings` in `<div id="system-health">`; inner ids kept for `tests/test_browser_e2e.py:220-224`), `src/js/main.js` (mount).
**Create** `panels/system-health/{index.js, model.js, CHEATSHEET.md}` (`OWNS`, `useTabLoader('terminal', load, {everyTick:true})`, `performance.measure('tick-render')` line), `panels/support/useTabLoader.js`, `test/panels/system-health/{model,render}.test.js`, `test/guards/container-ownership.test.js`.
**Delete** `legacy/system-health.js`.

- [ ] TDD model (derive-from-findings rules from `system-health.js:58-75`, never fabricate) and render tests; implement; delete legacy file; baseline −2 exports, −1 cycle member.
- [ ] `npm run check`; `python3 -m pytest tests/test_frontend_graph.py -q`; browser smoke via `/run`.
- [ ] Commit: `refactor(frontend): migrate System Health to a Preact panel over signals (pilot)`

**Acceptance** — `quality-browser-e2e`'s System Health test passes with no console error;
the legacy poll loop no longer references the panel; ownership guard green.

---

## T3 — Tabs, poll loop, WebSocket into `core/`

**Create** `core/view.js` (VIEWS, `showView`, `.active` effect on `view-*`/`tab-btn-*`; `window.showView` kept as a documented surface until T9), `core/poll.js` (304/failure handling → `connectivity`; `startPolling` effect on `pollIntervalMs`; `registerLegacyTick`), `core/ws.js` (`terminalFeeds`, `tradeStreamStatus` signals; backoff), `panels/tab-bar/index.js` (renders the same `tab-btn-*` ids; replaces 6 inline handlers), `test/core/{poll,view}.test.js`.
**Modify** `src/js/main.js` (bootstrap order; `document.body.dataset.bundle = 'loaded'`), `legacy/polling-and-websocket.js` (body → `legacyTick(state)`), `legacy/trading-gate-and-connectivity.js` (timer removed; `renderConnectivity` reads `connectivity.peek()`), `legacy/bootstrap.js` (shrinks), `tests/test_browser_e2e.py:164` (probe → `data-bundle`), `tests/test_e2e_terminal_static_and_api.py:65` (grep marker → `data-bundle`).

- [ ] Tests: 304 leaves `appState` untouched; failure increments; success publishes then calls the legacy tick exactly once; `showView` persists to localStorage.
- [ ] `npm run check`; `/run` browser smoke (tabs, live dot); baseline −6 inline handlers.
- [ ] Commit: `refactor(frontend): core owns polling, websocket and view state; legacy panels consume a tick`

---

## T4a — Backend: `GET /api/config/schema` (TDD)

**Create** `services/config/schema_fields.py` (seeded), `services/config/schema.py` (`build_schema(cfg)`, `HIDDEN_PATHS`, YAML leaf walker), `tools/extract_config_schema_seed.py` (stdlib `html.parser` state machine over `#view-config`; cross-checks `config-panel.js` id→path assignments; emits `schema_fields.py`), `tests/test_config_schema.py`.
**Modify** `services/config/routes.py` (+GET), `services/config/config_bounds.py` (moved here from bare `services/config_bounds.py` by commit `6e4338f`, 2026-08-27 — corrected 2026-08-31 catch-up review) (+`STOP_LOSS_CEILING`; docstring `:43`, same line number at the new path), `services/config/CHEATSHEET.md`, `baseline.json` (+`backend-route-unused:GET:/api/config/schema`, note "consumer lands in T5a").

- [ ] Write these 5 tests first (corrected 2026-08-31 catch-up review — no separate
  numbered "spec's TDD list" exists anywhere in the design or plan; these are this
  task's own tests, written out in full rather than referenced from a phantom list):
  (1) `GET /api/config/schema` returns 200 with the documented shape; (2) every `ui`
  field in the response has `label`/`type`/`help`; (3) coverage: `curated ∪ hidden ==
  leaves` (every real config leaf is either a `ui` field or explicitly hidden, none
  missing); (4) dynamic `take_profit_pct.max` reflects `min_unit_cost` correctly for
  both `0.5` and `0.8`; (5) the 3 locked paths are marked locked in the response.
- [ ] Run the seed script, hand-review `schema_fields.py` (≈72 fields, ~8 need hand-written help).
- [ ] `ddev exec -s fastapi python3 -m pytest tests/test_config_schema.py tests/test_config_bounds.py -q`; pyflakes.
- [ ] Commit: `feat(config): serve a field schema with dynamic bounds, locks and hidden-path coverage`

---

## T4b — Backend: schema-driven `POST /api/config` validation + `/validate` (TDD)

**Create** `services/config/validation.py` (`validate_patch(schema, cfg, patch) → (errors, warnings)`).
**Modify** `services/config/routes.py` (validate before merge; `POST /api/config/validate`), `tests/test_config_schema.py` (9 more tests, continuing T4a's numbering as tests 6–14 — corrected 2026-08-31 catch-up review, same phantom-list fix as T4a above: type mismatch rejected; static bounds enforced; enum values enforced; nullable fields accept `null`; patch-scoped `check_all` rejects vs. warns correctly; unknown field rejected; `/validate` is side-effect-free (no write); a `strategy_overrides` patch on a field that isn't `overridable: true` per the schema is rejected — pinned 2026-08-31 adversarial review, the prior "override scope respected" wording was ambiguous with `config_overrides._TIERS`'s `by_category`/`by_series` scoping, a separate concept; existing logging behavior unchanged), `baseline.json` (+validate route).

- [ ] `ddev exec -s fastapi python3 -m pytest tests/test_config_schema.py tests/test_trading_gate.py -q`.
- [ ] Commit: `feat(config): validate /api/config patches against the schema and physical bounds`

**Acceptance** — the three refusal messages are byte-identical; a pre-existing out-of-range
value does not block an unrelated save; 400 bodies carry string `detail` + `errors`.

---

## T5a — Config panel: schema-driven fields

**Create** `panels/config/{index.js, model.js, field.js, CHEATSHEET.md}`, `test/panels/config/model.test.js` (**patch-parity** against `legacy/config-panel.js:125-229` semantics), render test with a 2-field fixture schema, a Playwright test (fixture schema → inputs; forced 400 with `errors` → inline message).
**Modify** `static/index.html:566-912` → `<div id="config-panel">` (legend kept; reset/session/watchlist stay legacy until T5b), `legacy/config-panel.js` (delete `loadConfig` field lines `:14-96`), delete `legacy/polling-and-websocket.js:197-233` (provider status → computed), `legacy/advisory-calibration.js:63-76` → `jumpTo(path)` via `data-config-path`.

- [ ] `npm run check`; **manual browser check required**: save a field → Change History row; jump chip from History lands on the field; whale sim/real sections follow the provider.
- [ ] Commit: `feat(frontend): render the Config tab from /api/config/schema with inline validation`

---

## T5b — Config panel: overrides, watchlist/search, reset

**Create** `panels/config/{overrides.js, watchlist.js, reset.js}` (+ pure `mergeOverride`/`removeOverride` tests).
**Delete** `legacy/config-panel.js`, `static/index.html:913-968`, `tools/extract_config_schema_seed.py`.

- [ ] `npm run check`; browser check add/remove override, pin/unpin; baseline −21 exports.
- [ ] Commit: `refactor(frontend): finish the Config tab migration; retire legacy/config-panel.js`

---

## T6 — Charts module

**Create** `charts/{TimeSeriesChart.js, series.js, theme.js, candlestick.js, sparkline.js}`, `test/charts/series.test.js`, Playwright test (10-point `equity_history` fixture → `#equity-chart canvas`).
**Modify** `legacy/equity-and-cards.js:97-138` → signature-preserving adapter (all six call sites upgrade), `legacy/screener-and-header.js` (imports moved renderers), `static/css/dashboard.css` (+vendored uPlot block), `bundle-budget.json` unchanged (100 KB covers it).

- [ ] `npm run check`; chrome-devtools check that the canvas node identity survives 3 ticks (no flicker, no scroll reset).
- [ ] Commit: `feat(frontend): uPlot-backed charts module replacing the SVG polyline rebuilds`

---

## T7a — History core

`panels/history/{index.js, model.js, trade-table.js, suggestion-card.js}` from `legacy/history-core.js`; paging/sort as `useSignal` state; `history-pnl-chart` via `TimeSeriesChart`.
- [ ] Render tests per branch; `npm run check`.
- [ ] Commit: `refactor(frontend): History core as a Preact panel`

## T7b — Advisory + calibration

`panels/advisory/`, `panels/calibration/`; delete `advisoryApplyInFlight`/`calibrationApplyInFlight`; add `happy-dom`; node-identity interaction test; Playwright Apply flow via route fixtures; retire the Selenium duplicate (`tests/test_browser_e2e.py:167-193`) or add `window.__e2e`.
- [ ] `npm run check`; `pytest tests/test_browser_playwright_e2e.py --collect-only`.
- [ ] Commit: `refactor(frontend): Advisory and Calibration panels with keyed rendering; drop the in-flight guards`

## T7c — Regime, candidate log, backtest sweeps, series evaluator, market analyst

One `panels/<name>/` each (`useTabLoader('history', …, {everyTick:true})`); delete `refreshHistoryInsightsIfActive` and `legacy/advisory-calibration.js`; the `feedTheAnalyst` inline handler goes.
- [ ] `npm run check`; baseline −19 exports.
- [ ] Commit: `refactor(frontend): remaining History-tab panels; retire legacy/advisory-calibration.js`

---

## T8a — Header, account toggle, banners, status badges
`panels/header/`; `accountMode` signal (+localStorage effect); badge renderers from `legacy/trading-gate-and-connectivity.js`.
- [ ] `#bankroll` E2E stays green. Commit: `refactor(frontend): header/account/status panels`

## T8b — Portfolio
`panels/portfolio/` — equity chart, **one** `PositionsPanel` replacing both `positions-list` writers, netting groups, dummies/shadow, trading-gate confirm flow; delete `legacy/trade-log-and-real.js`.
- [ ] Run `dimensional-analysis` on displayed P&L/cost/exposure; browser check paper↔real. Commit: `refactor(frontend): Portfolio panels; unify paper/real positions`
- [ ] **Open question carried over from the realtime remediation plan's P3.5 (2026-08-27):** a live report ("having a large amount of open positions causes things to lag or crash") was traced backend-side to `exit_engine.check_exits` (see
  `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`'s Task 20 and
  Task 17c's benchmark) - whether `PositionsPanel`'s own render cost *also* scales
  materially with open-position count is untested and was deliberately left for this
  task rather than pinned to the current pre-modularization file, which this task
  deletes anyway. Same measurement T8d already does for markets - measure tick-render
  with a large (e.g. 200+) synthetic position fixture; add a per-row keyed store only
  if it's actually slow, not preemptively.

## T8c — Terminal
`panels/terminal/` — keyed signal/decision feed (delete `renderFeedListSmooth` and dead `renderSignals`), funnel, watchlist table, control buttons.
- [ ] Commit: `refactor(frontend): Terminal panels; remove the dead signals-only feed`

## T8d — Markets + Whale Watch
`panels/markets/` (shared `MarketTable`, filters as signals), `panels/whale-watch/`; `core/market-meta.js` replaces the `Object.assign` caches; delete `legacy/whale-watch.js`, `legacy/equity-and-cards.js`.
- [ ] Measure tick-render with a 500-row fixture; add the per-row keyed store **only if** > 16 ms. Commit: `refactor(frontend): Markets and Whale Watch panels`

## T8e — Market-detail + Help modals
`panels/market-detail/`, `panels/help/`; remaining inline handlers and `__backdropMouseDownOnSelf` gone; delete `legacy/screener-and-header.js`, `legacy/shared-utils.js`.
- [ ] Browser check ESC/backdrop close. Commit: `refactor(frontend): modals as panels; legacy/ is empty`

---

## T9 — Cleanup, strict guards, docs sync

- [ ] Delete `src/js/legacy/`; `window.*` → 0 (or exactly `__e2e`); graph guard → no cycles anywhere; `no-unused-vars: error`.
- [ ] `--minify`; measure; ratchet `bundle-budget.json` to the measured size + 10 %.
- [ ] Decide the 4 uncalled `services/research/routes.py` routes (surface in System Health or baseline with a note).
- [ ] `/sync-status-docs` for the ROADMAP "Separate the frontend…" and "dedicated charts/graphs module" items and `static/status.html`; CLAUDE.md file map; manifest regenerated.
- [ ] `npm run check`; `python3 -m tools.quality_audit …` exit 0 with a much smaller baseline.
- [ ] Commit: `refactor(frontend): remove legacy/, flip guards strict, sync docs — modularization complete`

**Acceptance (initiative)** — every panel has `OWNS`, a model test, a render test and a
CHEATSHEET; import graph acyclic with layer rules; zero `window.*`; config validated from a
served schema; charts update in place; five pipelines green; `main` deployable.
