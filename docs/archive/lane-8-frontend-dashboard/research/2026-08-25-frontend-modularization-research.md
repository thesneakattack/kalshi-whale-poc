# Frontend Modularization — Research Baseline and Solution-Family Comparison

**Date:** 2026-08-25.
**Status:** research record backing
`docs/archive/lane-8-frontend-dashboard/specs/2026-08-25-frontend-modularization-design.md`
(moved there 2026-09-06, planning-lanes migration). Every number below was
measured against `a0c1569` (`origin/main` at the time) with the commands in Appendix A/B;
library sizes come from real scratch esbuild builds, not vendor prose. Nothing in this
document is a design decision — the spec is authoritative for those; this file records the
evidence they rest on so a later session never has to re-derive it.

## 1. Re-grounding record

- **Branch:** `docs/frontend-modularization-design`, cut from `origin/main` at `a0c1569` in
  an isolated worktree (the primary checkout was on `chore/realtime-dp-investigation` with
  live, uncommitted work from a parallel session — never switch branches under it).
- **Objective served:** CLAUDE.md's per-module effectiveness / efficiency / informativeness
  objective (2026-08-23), applied to the one part of the app that never got the
  `services/<name>/`-style module split the backend got in phases 115–119.
- **Frontend toolchain (verified):** esbuild 0.24.2 installed (`^0.24.0`), ESLint `^10.0.0`
  with exactly one rule (`no-undef`), **zero runtime npm dependencies**. Node 24.16 on the
  host, 24.15 inside the ddev `web` container, `node:22-slim` in Woodpecker.
- **Build:** `esbuild src/js/main.js --bundle --format=iife --outfile=../static/js/dashboard.bundle.js`
  (`frontend/package.json:7`) — **unminified**, bundle gitignored (`.gitignore`), rebuilt by
  a ddev `web_extra_daemons` watch (`.ddev/config.yaml:23-25`) and by two CI pipelines.
- **Current bundle:** 253,294 B raw / 58,791 B gz as built. A `--minify --target=es2022`
  rebuild of the same source: 171,621 B raw / 48,866 B gz. One test greps the *unminified*
  bundle for the identifier `clearTerminalFeedCaches`
  (`tests/test_e2e_terminal_static_and_api.py:65`), so minification is not a free flag today.
- **Served page:** `static/index.html` loads exactly one `<script src="js/dashboard.bundle.js">`
  (`:974`) and one stylesheet (`css/dashboard.css`, `:7`). The three other pages
  (`status.html`, `login.html`, `accounts.html`) are still self-contained inline pages and
  are out of scope.

## 2. Method

- Three read-only inventory passes (architecture/build; per-panel code map; prior decisions,
  CI and tooling) plus one independent design review, all against source and cited by
  `file:line`.
- Library facts from current documentation (Context7 mirrors of the official docs, `npm view`
  on 2026-08-25) and from scratch esbuild builds in a throwaway directory (§8.1) — never from
  memory.
- The adversarial-analysis MCP (`Devil's Advocate`) is subscription-gated → BLOCKED_EXTERNAL
  under `.claude/rules/tooling-plugins.md`; the premortem was done as an internal pass and
  its four findings are folded into the spec's risk section.

## 3. Measured inventory

### 3.1 Modules — `frontend/src/js/`, 6,201 lines in 13 files

| module | lines | responsibility (from its own header comment) |
|---|---:|---|
| `advisory-calibration.js` | 823 | History tab: advisory engine, suggestion cards, calibration report/history, regime segmentation, candidate log, backtest sweeps, market analyst, series evaluator |
| `screener-and-header.js` | 769 | Markets screener table, market-detail modal (info/trades/orderbook/candles/analyst/help), Portfolio header strip, real-money banner |
| `signals-feed.js` | 738 | Feed-list keyed re-render helper, Terminal signal+decision feed & filters, Portfolio open-positions (paper) |
| `shared-utils.js` | 699 | `$`, `fmt`, `esc`, `fetchJSON`, market-metadata caches, series/market label helpers, Markets-tab table renderer |
| `config-panel.js` | 611 | Config tab `loadConfig()`/save, `loadSession()`, poll-interval source, Markets search/watchlist, strategy-overrides editor |
| `trade-log-and-real.js` | 535 | Portfolio trade log; real-money positions/fills/orders; trading funnel |
| `equity-and-cards.js` | 513 | SVG equity/P&L chart, divergence/whale-lean helpers, market & event card renderers |
| `history-core.js` | 445 | History tab core (narrative, summary, P&L curve, close-type breakdown), suggestion card, decline/undecline, plain-language phrase maps |
| `trading-gate-and-connectivity.js` | 331 | Real-trading confirmation gate, exchange/connectivity/tick-health/halt indicators, the single `setInterval` |
| `whale-watch.js` | 267 | Whale Watch tab: trade tape, track record, signal history/clusters |
| `polling-and-websocket.js` | 247 | The `refresh()` poll loop over `/api/state`, live WebSocket, poll-owned state |
| `main.js` | 119 | Entry: `showView`, per-view refresh dispatch, first-run walkthrough, bootstrap |
| `system-health.js` | 104 | Terminal "System Health" panel over `GET /api/quality/summary` |

### 3.2 `static/index.html` — 977 lines, one page, six tab views

- Views `portfolio, markets, whale, terminal, history, config` (`main.js:18`; ROADMAP still
  says "7-tab" — Market-Native was removed in `e2dcf33`). 36 `class="panel…"` blocks,
  **240 `id=` attributes**, **21 inline `on*=` handlers**, no `<section>` elements.
- `#view-config` = lines 549–977: **429 lines = 43 % of the file**. 16
  `<details class="config-section">`, **72 `cfg-*` field ids**, 79 `config-path` spans,
  **64 `info-icon` tooltips** (the only place the settings' help text exists), 87
  `<input>/<select>` elements, 0 `data-*` binding attributes.

### 3.3 Per-file counts

| file | `window.X = X` | `innerHTML =` | `esc(` | `toFixed(` | `fmt(` | `fetchJSON(` | raw `fetch(` | `$(` | `on*=` in JS strings | `addEventListener` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| advisory-calibration.js | 19 | 49 | 40 | 23 | 2 | 25 | 0 | 17 | 4 | 3 |
| config-panel.js | 21 | 16 | 19 | 0 | 0 | 20 | 1 | 209 | 7 | 11 |
| equity-and-cards.js | 9 | 6 | 36 | 8 | 7 | 0 | 0 | 2 | 5 | 0 |
| history-core.js | 20 | 14 | 18 | 6 | 8 | 6 | 0 | 7 | 5 | 0 |
| main.js | 3 | 1 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| polling-and-websocket.js | 5 | 3 | 0 | 0 | 0 | 0 | 1 | 10 | 0 | 4 |
| screener-and-header.js | 22 | 14 | 29 | 24 | 8 | 4 | 1 | 26 | 5 | 0 |
| shared-utils.js | 43 | 4 | 30 | 3 | 2 | 1 | 2 | 3 | 10 | 0 |
| signals-feed.js | 16 | 15 | 36 | 17 | 11 | 1 | 0 | 11 | 18 | 0 |
| system-health.js | 2 | 4 | 4 | 1 | 0 | 1 | 0 | 3 | 0 | 0 |
| trade-log-and-real.js | 14 | 24 | 55 | 9 | 13 | 1 | 0 | 19 | 6 | 0 |
| trading-gate-and-connectivity.js | 14 | 6 | 5 | 2 | 2 | 2 | 0 | 20 | 3 | 0 |
| whale-watch.js | 9 | 11 | 12 | 4 | 0 | 2 | 0 | 10 | 5 | 0 |
| **total** | **197** | **167** | **284** | **97** | **53** | **63** | 5 | 339 | **68** | 18 |

`CustomEvent`/`dispatchEvent`: **0** in every file. `setInterval`: exactly one
(`trading-gate-and-connectivity.js:92`).

Readings:
- **`window.*` is the event bus.** 197 exports exist so that 21 inline handlers in
  `index.html` plus 68 handlers built inside JS template strings can resolve by global name.
- **Rendering is string-concatenated `innerHTML`** (167 sites) with 284 manual `esc()` calls
  compensating — XSS-by-omission is one missed call away, and every re-render replaces DOM
  nodes wholesale (the root of the "Apply button replaced mid-click" and scroll-reset bug
  classes, see §5).
- **Formatting is not consolidated**: 97 `toFixed(` vs 53 `fmt(`; no percent or plain-number
  formatter exists, so every `x.toFixed(1) + '%'` is hand-rolled at the call site.
- **`fetch` is consolidated** (63 `fetchJSON` vs 5 raw), which is what makes the
  frontend↔backend contract scanner possible (§7.3).

### 3.4 Shared infrastructure

| concern | exists? | where |
|---|---|---|
| API wrapper | yes | `fetchJSON` — `shared-utils.js:27-53` (checks `res.ok`, surfaces `detail`) |
| poll scheduler | yes | `refresh()` `polling-and-websocket.js:42`; timer in `trading-gate-and-connectivity.js:86-92` |
| WebSocket client | yes | `polling-and-websocket.js:142-185`, 2 message types, 1 s→30 s backoff |
| DOM helper | yes | `$ = id => document.getElementById(id)` `shared-utils.js:24` (339 uses) |
| money formatter | yes | `fmt` `shared-utils.js:25` |
| percent / number / time-ago formatter | **no** | — |
| HTML escaper | yes | `esc` `shared-utils.js:76` |
| central state store | **no** | ~40 module-level `let`s owned by whichever module mutates them; cross-file writes via setter shims (`_setAccountMode` `shared-utils.js:72`, `_setTradeTapeMinSize`, `_rerenderTerminalFeed` `polling-and-websocket.js:38`) |
| no-side price inversion | yes, single owner | `sideAdjustedPrice()` in `shared-utils.js` (phase 118) — must stay single-owner |
| unit tests | **none** | no test script, no runner, no `*.test.js` |
| frontend docs | **none** | no `frontend/README.md` or `CHEATSHEET.md`; the designated "read this first" header (`shared-utils.js:5-18`) describes the pre-ESM layout (§7.4) |

### 3.5 Tests and CI touching the frontend

| file | what it proves | coupling to today's layout |
|---|---|---|
| `tests/test_browser_e2e.py` (Selenium, 4 tests) | dashboard loads, tabs switch, bundle is loaded, failed API action isn't false success, System Health renders with **no console error (allowlist deliberately empty)** | `assert typeof window.showView === 'function'` (`:164`); calls `window.applyCalibrationSuggestion(btn)` on a fabricated button (`:187`); `tab-btn-*`/`view-*` ids (`:79-84`) |
| `tests/test_browser_playwright_e2e.py` (6 tests) | bankroll never fabricated on failing `/api/state`, forced 4xx/2xx apply, WS held to `/api/ws` | calls `window.applyCalibrationSuggestion` (`:165`) |
| `tests/test_e2e_terminal_static_and_api.py` | static index served by `web`, `/api/state` shape | asserts the exact `<script src="js/dashboard.bundle.js">` tag and greps the bundle for `clearTerminalFeedCaches` (`:62-64`) |
| `tests/test_frontend_api_contract.py` | the contract scanner, on **synthetic** JS files | would keep passing if the real scan went blind |
| `tests/test_project_manifest.py` | `static/project-manifest.json` counts (`frontend_modules` = 13) within tolerance | regenerate on the host, not in ddev |
| `.woodpecker/quality-frontend-build.yml` | `npm ci && npm run lint && npm run build` | **path-filtered to `frontend/**`; not a required check** (posts nothing when skipped) |
| `.woodpecker/quality-browser-e2e.yml` | rebuilds the bundle, then Selenium + Playwright | required; no path filter |
| `.woodpecker/quality-architecture-audit.yml` | `tools.quality_audit` incl. the contract scanner, manifest check, mypy | required; no path filter |

## 4. Import graph — one strongly connected component

54 import edges (Appendix A prints them). Tarjan's algorithm yields **a single SCC of size
13**: every module transitively depends on every other, so no module can be loaded, tested,
or reasoned about alone and the file boundaries carry no dependency-direction meaning.

Direct cycles: `main.js ↔ polling-and-websocket.js`; `config-panel.js ↔ advisory-calibration.js`;
`shared-utils.js → {equity-and-cards, polling-and-websocket, screener-and-header}` which all
import `shared-utils.js` back. The only **leaves** (no imports from other panels) are
`system-health.js` and `whale-watch.js` — which is why System Health is the pilot panel.

## 5. Coupling catalogue — what a modularization must cut

1. **One central poll loop hard-codes every panel.** `refresh()`
   (`polling-and-websocket.js:42-140`) raw-fetches `/api/state`, then calls 20+ named render
   functions from 7 modules behind a tab `if/else if` chain (`:97-128`); `main.js:72-84`'s
   `refreshHistoryInsightsIfActive()` fires 10 loaders every tick while History is open.
   Adding a panel = editing `refresh()`. The tab-open-vs-poll refresh contract (phase 139)
   is enforced by comments, not structure.
2. **`window.*` bus** (§3.3) — 197 exports, 21 + 68 string-built handlers, plus one bare
   global `window.__backdropMouseDownOnSelf` (`screener-and-header.js:175`, read by
   `index.html:53,81`).
3. **Shared mutable caches** in `shared-utils.js:55-65` (`marketTitles`, `eventTitles`,
   `eventLiveData`, `marketPanelState`, `marketPanelFilters`) `Object.assign`ed from five
   modules; poll-owned state in `polling-and-websocket.js:22-31` imported read-only by four.
4. **Shared DOM ids written by more than one module**: `positions-list`/`position-count`/
   `positions-toggle` written by both the paper renderer (`signals-feed.js:536-660`) and the
   real renderer (`trade-log-and-real.js:160-295`); `toggle-btn` text set by the poll loop
   (`:70`) while its click handler lives in `config-panel.js:99`; the poll loop mutates
   Config-tab DOM every tick (`updateWhaleProviderStatus`, `polling-and-websocket.js:197-233`);
   Advisory's `jumpToConfigSetting` (`advisory-calibration.js:63-76`) finds Config fields by
   **text-content match** on `.config-path` spans; `market-analyst-summary-cards` children
   are indexed positionally (`advisory-calibration.js:533-537`).
5. **Cross-feature direct calls**: `applyAdvisoryRecommendation → loadConfig()`;
   `loadTradingHistory()` fires 10 loaders from another file; `toggleAutoApply` reloads
   *both* advisory and calibration regardless of which toggle was clicked
   (`history-core.js:394-395`).
6. **Wholesale re-render bug class**: `advisoryApplyInFlight`/`calibrationApplyInFlight`
   (`advisory-calibration.js:13,202`) exist because the every-tick `innerHTML` rebuild
   replaced the Apply button mid-click; phase 139 removed a duplicate per-poll refresh that
   reset the History scrollbar. `renderFeedListSmooth` (`signals-feed.js:13-38`) is a
   hand-rolled keyed diff written to work around the same thing for the Terminal feed.
7. **`localStorage` as cross-panel state** (`whale-signal-view`, `whale-signal-account-mode`,
   `whale-signal-seen-intro`, per-panel `adv-*` flags).

Dead code found: `renderSignals()` (`signals-feed.js:50-150`, ~100 lines) targets
`signal-feed-list`/`signal-feed-filter`, ids that do not exist in `index.html`; it is called
only by itself. `services/research/routes.py` exposes 4 routes with no frontend caller.

## 6. Feature-level map

### 6.1 Config tab (the largest single target)

- Markup `index.html:549-968`; JS `config-panel.js`. Each of the 72 fields is wired **three
  times by hand**: the markup (`<span class="config-path">strategy.entry_threshold</span>` +
  `info-icon title="…"` + `<input id="cfg-threshold" …>`), `loadConfig()` (`:14-96`, one
  assignment per field), and the save handler (`:125-229`, one `parseFloat`/`.checked` per
  field building a nested patch). Adding a field = three edits, and the id→path mapping
  exists nowhere as data.
- The one manifest-driven piece: `OVERRIDE_FIELDS` (`:420-433`, 12 entries `{label, type}`)
  driving the overrides editor.
- Backend: `services/config/routes.py` — `GET /api/config` → `config_store.get()`;
  `POST /api/config` → three hardcoded refusals (`kalshi_account.trading_enabled`,
  `advisory.auto_apply_enabled`, `confidence_calibration.auto_apply_enabled`, `:30-56`, and
  `tests/test_trading_gate.py:113` and `:895` assert the field name appears in the string `detail`) →
  `config_store.update(patch)` = shallow merge + atomic YAML write (`config_store.py:154-184`)
  → change-history logging. **No type, range, or unknown-key validation.**
- `services/config_bounds.py` (276 lines) encodes the physically-achievable ranges
  (`take_profit_ceiling`, `take_profit_universal`, `check`, `check_all`, `clamp`,
  `MIN/MAX_TRADEABLE_UNIT_COST`) with a real 2026-08-17 incident behind it, and its
  docstring (`:43`) says "`/api/config` warns" — the route never imports it. Only
  `advisory_engine.py:328`, `kalshi_trade_tape.py:520` and
  `diagnostics.check_config_bounds` (`:600-616`) consume it. Client-side, `min`/`max`
  attributes exist on inputs but nothing reads them.
- Coverage: `config/settings.yaml` has 24 top-level sections / 134 leaf keys; the tab covers
  ~13 sections / 72 fields. `index_feed`, `settlement_edge_entry`, `series_watcher`,
  `logging`, `backup`, `alerting`, `observability`, `research`, `event_lifecycle`,
  `event_schedule`, `whale_confidence_weights` have no UI at all.
- Reverse coupling: the poll loop rewrites `#whale-provider-status`, toggles
  `#whale-sim-section`/`#whale-real-section` and disables their inputs every tick.

### 6.2 Advisory panel

Markup `index.html:324-347` (9 real lines); JS split across `advisory-calibration.js:15-100,
138-188` and `history-core.js:232-317, 356-405` (the shared `renderSuggestionCard()`,
decline/undecline, auto-apply toggle, plain-language phrase maps). Re-fetched and wholesale
re-rendered every tick while History is open (§5.1, §5.6). Uses `prompt()`/`alert()`/
`confirm()` gates. Endpoints: `/api/advisory/{status,recommendations,recommendations/apply,
applied-changes,auto-apply/*}`, `/api/suggestions/{decline,declined,undecline}`.

### 6.3 Charts

No charting library, nothing vendored, no CDN. Four hand-rolled kinds:
1. **SVG polyline line chart** — `renderEquityChart(history, baseline, valueKey,
   baselineLabel, elId, opts)` (`equity-and-cards.js:97-138`, 42 lines, viewBox 900×180, no
   axes/tooltips/zoom), reused at **six mount points**: `equity-chart` (paper equity and
   real balance), `history-pnl-chart`, `calibration-history-chart`, `regime-hour-chart`,
   `backtest-entry-threshold-chart` — the last has a **numeric** x-axis (threshold), not time.
2. **SVG sparkline** — `screener-and-header.js:553-576` (24 lines).
3. **SVG candlestick + volume + bid/ask bands** — `renderCandlestickHTML`
   (`screener-and-header.js:514-652`, 139 lines).
4. **CSS div bar meters** (`.prob-bar`, `.whale-bar`, `.conf-bar`, `.funnel-bar`).
Every chart is regenerated as a whole `innerHTML` string on its owner's cadence (equity
chart every tick while Portfolio is open).

### 6.4 Other panels (condensed)

| panel | JS | refresh |
|---|---|---|
| Positions (paper / real) | `signals-feed.js:536-660` / `trade-log-and-real.js:160-295` — **same three DOM ids** | every tick |
| Signal/decision feed | `signals-feed.js:212-469` + keyed helper `:13-38` | tick **and** WS push |
| Whale tape / track record / signal history / clusters | `whale-watch.js` | tape+record every tick; history/clusters on tab open |
| System Health | `system-health.js` (leaf module) | every tick, Terminal only |
| Market analyst, calibration, regime, candidates, backtest, series evaluator | `advisory-calibration.js:204-800` | every tick while History open (10 loaders) |
| Market-detail modal | `screener-and-header.js:157-357` + 4 render fns | every tick regardless of tab while open |
| Help/glossary modal | `screener-and-header.js:257-291` | on demand |
| Trade log, real orders, position netting, funnel | `trade-log-and-real.js`, `signals-feed.js:661-738` | tick / on demand |

CSS: one stylesheet (`static/css/dashboard.css`, 912 lines) with a single `:root` token
block (`--bg --panel --panel-border --text --muted --yes --no --whale --danger --mono --sans`),
dark-only, one breakpoint; 119 inline `style=` in `index.html` and 225 more inside JS strings.

## 7. Prior decisions and standing constraints

### 7.1 Timeline (from `static/status.html` and git)
- **Phase 17** — serving-level split: `main.py` API-only, `web` serves `static/`.
- **Phase 42** — Config tab rebuilt as a collapsible accordion.
- **Phase 117 (2026-08-22, `37fb43a`)** — the inline 5,758-line script became 13 esbuild-
  bundled ES modules. A first byte-range split into classic scripts was **corrected
  mid-session**: "no build step, no bundler" was never an instruction, and "we can use
  libraries and plugins and tools to help achieve our goals." It surfaced three correctness
  classes that recur on any file move: inline handlers resolve against `window`; wholesale-
  reassigned values go stale behind a one-time `window.x = x`; an importer only gets a
  read-only live binding. `eslint no-undef` was adopted as the safety net and caught a real
  pre-existing bug.
- **Phase 118 (same day)** — framework-alternatives audit: a **full SPA rewrite was rejected**
  in favour of the bundler + narrow in-house patterns; `sideAdjustedPrice()` centralised.
- **Phase 139 (2026-08-23)** — removed the duplicate per-poll refresh (scroll reset, laggy
  Apply); established the tab-open-vs-poll contract.
- **Phase 144/145/149/150 (2026-08-24)** — contract scanner, Selenium E2E, System Health
  panel (the 13th module), CI topology; **`dea22a7`, `bbb0b56`, `8bc3914`** are the only
  frontend commits since — content, not structure.

### 7.2 Rules a modularization must keep (`.claude/skills/frontend-verification/SKILL.md` and friends)
Source is `frontend/src/js`, the bundle is generated (never the sole edit); lint + build
after source edits; verify API method/path against backend routes; browser E2E for
user-visible behaviour with an empty console-error allowlist; no DDEV-only DNS in CI; pytest
alone is not proof for user-visible changes. From CLAUDE.md: a displayed financial figure
must trace to its backend definition rather than be re-derived at the call site;
`sideAdjustedPrice()` stays the single owner of the no-side inversion. From
`.claude/rules/branching-and-ci.md`: Claude does targeted local checks, Woodpecker does the
rest; required PR contexts are listed there and `quality-frontend-build` is deliberately
excluded because it is path-filtered.

### 7.3 Tooling that goes silently blind under a restructure
- `tools/quality_audit/frontend_contract.py:136-141`: `js_dir = frontend/src/js`,
  **`glob("*.js")` non-recursive**, regex `\bfetch(?:JSON)?\(` with a literal first argument
  starting with `/`. A subdirectory, a `.jsx` file, or a URL-building helper makes calls
  invisible with no error; the resulting `backend-route-unused` findings are *info*, not
  gated. Its test uses synthetic files, so it would keep passing.
- `tools/project_manifest.py`: `frontend_modules` is recursive (count changes, tolerance
  check); `_GENERATED_JS_PATHS` hardcodes the bundle path.
- `.woodpecker/quality-frontend-build.yml`: path filter `frontend/**`; unit tests or a size
  budget added there would not gate merges today.
- `.ddev/config.yaml:23-36`: watch daemon and post-start `npm install` are directory-bound
  to `frontend/`.

### 7.4 Documentation drift found (all real, all to be fixed in T1a)
1. `CLAUDE.md:292-294` — "Plain inline HTML/CSS/JS per page, no build step, no bundler":
   true for 3 of 4 pages, false for the dashboard, and causally implicated in phase 117's
   wrong first pass.
2. `frontend/src/js/shared-utils.js:5-18` — the designated conventions header says the files
   are classic scripts loaded by 12 `<script>` tags with no import/export; its own first
   three lines are `import` statements.
3. `frontend/src/js/main.js:17` — "the other 11 files" (there are 12).
4. `frontend/src/js/polling-and-websocket.js:43-47` — justifies bypassing `fetchJSON` with
   "it doesn't check `res.ok`"; it has since 2026-08-17 (the 304 handling is the real reason).
5. `.github/workflows/quality.yml:42-43` — still runs the no-op
   `git diff --exit-code -- static/js/dashboard.bundle.js` that `8bc3914` removed from
   Woodpecker; `tools/quality_audit/source.py:25-27` cites it.
6. `.claude/skills/frontend-verification/SKILL.md` rule 5, `integration-audit/SKILL.md:21`,
   `final-verification/SKILL.md:27` — "committed bundle … synced": there is no committed bundle.
7. `static/status.html:6140` — component row lists 12 concern files (missing
   `system-health.js`), stale LOC.
8. `ROADMAP.md:206` — "7-tab toggle" (6 views).

## 8. Solution-family research

### 8.1 Component / state model — measured

Scratch builds (`esbuild --bundle --minify --format=iife --target=es2022`, `gzip -9`;
Appendix B), versions: preact 10.29.8, @preact/signals 2.11.1 (core 1.14.4), htm 3.1.1,
lit / lit-html 3.3.3, alpinejs 3.16.3, uplot 1.6.32:

| bundle | raw B | gz B |
|---|---:|---:|
| preact (`render`, `h`) | 10,761 | 4,585 |
| preact + @preact/signals | 20,474 | 8,016 |
| **preact + hooks + @preact/signals + htm/preact** | 23,275 | **9,128** |
| lit (`LitElement`, `html`, `render`) | 15,619 | 6,075 |
| lit-html alone | 7,459 | 3,359 |
| alpinejs (`Alpine.start()`) | 54,813 | 19,744 |
| uplot | 52,602 | 23,460 (+772 for `uPlot.min.css`) |
| current dashboard bundle, unminified / minified | 253,294 / 171,621 | 58,791 / 48,866 |

Decision matrix (the spec carries the decision; this is the evidence):

| | gz | toolchain change | migration of 167 template-literal sites | fine-grained updates | DOM-free unit tests | `no-undef` coverage | familiarity | multi-page |
|---|---|---|---|---|---|---|---|---|
| **Preact + signals + htm** | 9.1 KB | `npm i`, `"type":"module"` | **transform** — same backticks/`${}`; drop `esc()`, wire handlers | signal as text child updates the text node only; `computed` slices; keyed lists | `node --test` + `preact-render-to-string`, zero transform | full, zero config (`${Comp}` is a JS identifier) | React-like; htm is Preact's documented JSX alternative, ~500 lines, codemoddable to JSX | mount-by-container |
| Preact + signals + JSX | 6.5 KB + 0 | esbuild `--jsx=automatic --jsx-import-source=preact`, `.jsx` files, `ecmaFeatures.jsx` | **rewrite** of every site | same | needs vitest (pulls vite) or an esbuild pre-bundle | full — ESLint ≥10 tracks JSX natively; `eslint-plugin-react` **cannot install** against ESLint 10 (peer `^3…^9.7`) and is unnecessary | widest | same |
| Lit (light DOM) | 6.1 KB + signals-core + `@lit-labs/preact-signals` | none | transform (closest syntax) but component = custom element ceremony | only via a **labs** adapter; otherwise whole-template re-render | needs a DOM (`@lit-labs/ssr` is heavy) | full | web-components model has no payoff for one single-team page | good |
| Alpine.js | 19.7 KB | none | rewrite into attribute expressions | `Alpine.store`; `x-for` on 500-row tables slower than keyed VDOM | poor | **none** — logic in attribute strings is invisible to ESLint and to the contract scanner | fights the lint-as-safety-net culture | fine |
| hand-rolled (phase-118 answer) | 0 | none | keep `innerHTML`; write a store + keyed diff | must be built (`renderFeedListSmooth` is 25 lines of exactly this) | trivial | full | reinvents ~9 KB of tested code; the re-render-mid-click class stays hand-guarded | fine |

Facts that decided the syntax question: `node --test` has no transform hook (`.jsx` cannot be
imported directly); `.jsx` would touch the scanner glob, the manifest suffix, and the lint
`files` list; ESLint 10 tracks JSX references natively so JSX buys no lint advantage; htm
templates are parsed once per template literal and cached.

### 8.2 Charts

| library | gz | x-axis | streaming API | notes |
|---|---|---|---|---|
| **uPlot 1.6.32** | 23.5 KB + 0.8 KB CSS | **time or numeric** (`scales.x.time:false`) | `setData(data, false)` preserves the view | canvas, no animation; multi-series/axes, cursor sync; the threshold-sweep chart needs the numeric axis |
| Lightweight Charts 5.2.1 | ~45 KB | **time only** | `series.update()` | financial-native candlesticks, but cannot draw the entry-threshold sweep |
| Chart.js | larger, animated | both | `update()` | slower past ~50k points; more than this dashboard needs |
| hand-rolled SVG (today) | 0 | both | full rebuild | no axes/tooltips/zoom; DOM churn every tick |
| server-rendered Plotly/Matplotlib (ROADMAP "Consider a dedicated charts/graphs module" item) | 0 client | — | image per request | adds Python deps and per-tick latency, loses interactivity — rejected |

### 8.3 Schema-driven config forms
Generic JSON-Schema form libraries (JSFE, XO-JS, SurveyJS…) all assume a standard JSON
Schema and their own widget sets; this app's needs are narrower (numbers/bools/selects/
comma-lists/`SERIES:amount` maps/2-ranges, dynamic ceilings from `config_bounds`, locked
fields, sim/hist/new badges, custom widgets for watchlist and overrides). A backend-served
manifest (`GET /api/config/schema`) rendered by ~150 lines of Preact is smaller than any
library and makes the backend the single owner of field metadata, which is what also lets
`POST /api/config` validate. The 64 existing tooltips are the seed corpus for `help`.

### 8.4 Build tooling
esbuild stays. Vite would add a dev server/HMR and a Rollup production pipeline this project
does not need (ddev's watch daemon already rebuilds in ~50 ms); esbuild handles htm (plain
JS) and, if ever wanted, JSX natively. esbuild 0.28.2 is current; 0.24.2 works (nothing here
needs newer).

### 8.5 Test runner
`node --test` (stable in Node 22, glob args supported) + `preact-render-to-string` for
DOM-free component rendering; `happy-dom` only when an interaction test needs a DOM. vitest
was rejected for now (drags in vite; watch/coverage features unused); revisit past ~50 test
files.

### 8.6 Rendering cost on a 6 s tick
`/api/state` is ~40 KB with a markets list that can reach hundreds of rows. One `batch()`
write per tick, `computed` slices per panel, keyed lists, and only the active view's panels
subscribed reproduce the old `if/else` intent declaratively. A per-row keyed-signal store is
a further lever but is **gated on measurement** (`performance.measure` around the tick,
surfaced in System Health) rather than adopted up front.

## 9. Decisions (summary — the spec is authoritative)

Preact 10 + `@preact/signals` + `htm/preact`, `.js` only; strangler-fig migration inside
`frontend/src/js/` (`core/`, `lib/`, `charts/`, `panels/<name>/`, `legacy/`); poll loop →
signals; backend `GET /api/config/schema` + schema-driven validation; uPlot behind one
chart component; CI-owned guards (recursive scanner, import-graph SCC/layer/`window.*`
ratchets, container ownership, unit tests, bundle budget) with `quality-frontend-build`
becoming always-run and required; multi-page split enabled but out of scope.

## 10. Open ROADMAP questions this answers

- `ROADMAP.md` "Separate the frontend from the backend completely" item — "whether a real framework … is ever warranted": **yes, a micro-
  framework (Preact + signals) — not a SPA rewrite**; the phase-118 rejection of a full
  rewrite stands. Separate repo: no — the `frontend/` npm project inside this repo with its
  own CHEATSHEET and CI is the module boundary.
- `ROADMAP.md` "Consider a dedicated charts/graphs module" item: **dedicated `charts/` module, yes;
  server-rendered, no** (§8.2).
- `ROADMAP.md` "Revisit the 5s dashboard polling model" item: unchanged; the signal-based state layer makes a later
  push-vs-poll change a `core/poll.js`-only edit.

## Appendix A — reproducible inventory script

Run against any checkout: `python3 frontend_counts.py <repo_root>`. (T1a productises the
import-graph part as `tools/quality_audit/frontend_graph.py`; the rest is one-off.)

```python
#!/usr/bin/env python3
import re, sys
from pathlib import Path
root = Path(sys.argv[1]); js = root / "frontend/src/js"; html = (root / "static/index.html").read_text()
files = sorted(js.glob("*.js")); texts = {f.name: f.read_text() for f in files}
print("## Modules"); total = 0
for f in files:
    n = texts[f.name].count("\n"); total += n; print(f"- {f.name}: {n}")
print(f"- TOTAL: {total} lines in {len(files)} modules")
edges = {n: sorted(set(re.findall(r"from '\./([a-z-]+\.js)'", t))) for n, t in texts.items()}
print(f"\n## Import edges: {sum(len(v) for v in edges.values())}")
for n, v in edges.items(): print(f"- {n} -> {', '.join(v) or '(none)'}")
idx, low, on, st, sccs, c = {}, {}, set(), [], [], [0]
def strong(v):
    idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
    for w in edges[v]:
        if w not in idx: strong(w); low[v] = min(low[v], low[w])
        elif w in on: low[v] = min(low[v], idx[w])
    if low[v] == idx[v]:
        comp = []
        while True:
            w = st.pop(); on.discard(w); comp.append(w)
            if w == v: break
        sccs.append(sorted(comp))
for v in edges:
    if v not in idx: strong(v)
print("\n## Strongly connected components (Tarjan)")
for s in sorted(sccs, key=len, reverse=True): print(f"- size {len(s)}: {', '.join(s)}")
def count(pat, t): return len(re.findall(pat, t))
rows = [("window.X = X", r"^window\.\w+\s*=", re.M), ("innerHTML =", r"\.innerHTML\s*=", 0), ("esc(", r"\besc\(", 0),
        ("toFixed(", r"\.toFixed\(", 0), ("fmt(", r"\bfmt\(", 0), ("fetchJSON(", r"\bfetchJSON\(", 0), ("raw fetch(", r"(?<![\w.])fetch\(", 0),
        ("$( calls", r"\$\('", 0), ("on*= in JS strings", r"\bon(click|change|input|mousedown|keydown)=\"", 0),
        ("addEventListener", r"addEventListener\(", 0), ("CustomEvent/dispatchEvent", r"CustomEvent|dispatchEvent", 0), ("setInterval", r"setInterval\(", 0)]
print("\n## Per-file counts")
print("| file | " + " | ".join(r[0] for r in rows) + " |"); print("|---|" + "---|" * len(rows))
tot = [0] * len(rows)
for n, t in texts.items():
    vals = [len(re.findall(p, t, fl)) for _, p, fl in rows]; tot = [a + b for a, b in zip(tot, vals)]
    print(f"| {n} | " + " | ".join(map(str, vals)) + " |")
print("| **total** | " + " | ".join(map(str, tot)) + " |")
print("\n## static/index.html")
print(f"- lines: {html.count(chr(10))}")
print(f"- inline on*= handlers: {count(r'\son(click|change|input|mousedown|keydown)=', html)}")
print(f"- id= attributes: {count(r'\sid=\"', html)}")
print(f"- <details class=\"config-section\">: {count(r'<details class=\"config-section\"', html)}")
print(f"- cfg-* field ids (excluding cfg-section-*): {len(set(re.findall(r'id=\"(cfg-(?!section-)[a-z0-9-]+)\"', html)))}")
print(f"- config-path spans: {count(r'class=\"config-path\"', html)}")
print(f"- info-icon tooltips: {count(r'class=\"info-icon\"', html)}")
cfg = html[html.index('id="view-config"'):]
print(f"- inputs/selects inside #view-config: {count(r'<(input|select)\b', cfg)}")
vc_start = html[:html.index('id="view-config"')].count("\n") + 1
print(f"- #view-config starts at line {vc_start}, spans {html.count(chr(10)) - vc_start + 1} lines")
print(f"- <script> tags: {count(r'<script', html)}; <link rel=stylesheet>: {count(r'rel=\"stylesheet\"', html)}")
```

## Appendix B — bundle-size measurement

In a throwaway directory (never inside `frontend/`):

```bash
npm init -y && npm i preact@10 @preact/signals htm uplot esbuild lit lit-html alpinejs
# one entry per candidate, e.g.
cat > a.js <<'EOF'
import { render, h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { signal, computed, effect, batch, useSignal, useComputed, useSignalEffect } from '@preact/signals';
import { html } from 'htm/preact';
window.__x = { render, h, useEffect, useRef, useState, signal, computed, effect, batch, useSignal, useComputed, useSignalEffect, html };
EOF
./node_modules/.bin/esbuild a.js --bundle --minify --format=iife --target=es2022 --outfile=a.min.js
printf "raw=%s gz=%s\n" "$(wc -c < a.min.js)" "$(gzip -9c a.min.js | wc -c)"
# current bundle
printf "raw=%s gz=%s\n" "$(wc -c < static/js/dashboard.bundle.js)" "$(gzip -9c static/js/dashboard.bundle.js | wc -c)"
```
