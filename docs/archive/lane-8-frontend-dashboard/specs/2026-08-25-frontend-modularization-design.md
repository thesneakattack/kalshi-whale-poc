# Frontend Modularization Design

## Status

Approved design (2026-08-25). Evidence:
`docs/archive/lane-8-frontend-dashboard/research/2026-08-25-frontend-modularization-research.md`
(moved there 2026-09-06, planning-lanes migration). Execution plan:
`docs/archive/lane-8-frontend-dashboard/plans/2026-08-25-frontend-modularization.md`
(moved there 2026-09-06, planning-lanes migration), orchestrated by
`superpowers:executing-plans` (corrected 2026-08-31 catch-up review — the bespoke
`frontend-modularization-task` skill this line named no longer exists; see the plan
doc's own header for the full chain of what replaced it and when).

## Goal

Give the dashboard frontend the same property the backend got in phases 115–119: cohesive
modules with one owner each, enforced dependency direction, testable in isolation, and
informative about their own behaviour — measured against CLAUDE.md's effectiveness /
efficiency / informativeness axes per module, not against a UI redesign.

## Non-goals

- No visual redesign; markup structure and ids the E2E tests rely on are preserved.
- No multi-page split (browser navigation replacing `showView()`); the design only
  **enables** it (§4.3).
- No changes to real-trading gates, the daily-loss kill switch, or `data/*.db` handling.
- `status.html`, `login.html`, `accounts.html` stay as they are.

## Decisions record (user-confirmed 2026-08-25)

| decision | choice |
|---|---|
| dependency stance | micro-framework allowed (phase 118's rejection of a *full SPA rewrite* stands) |
| framework | **Preact 10 + `@preact/signals` + `htm/preact`**, `.js` files only (JSX escape hatch documented, §10) |
| config panel | **backend-served schema** (`GET /api/config/schema`) drives rendering; `POST /api/config` validates against it and `config_bounds` |
| charts | **uPlot** behind one component for line/time-series charts; SVG sparkline/candlestick kept |
| tests | `node --test` + `preact-render-to-string`; `happy-dom` only when an interaction test needs it |
| CI | `quality-frontend-build` becomes always-run (no path filter) and a required branch-protection check |
| deliverable now | research + spec + plan; implementation in later PR groups on their own branches |

## 1. Architecture

### 1.1 Layers (all inside `frontend/src/js/`, so path filters, the scanner root, and ddev stay valid)

| layer | may import | contents |
|---|---|---|
| `main.js` | anything | bootstrap only: mount panels, start poll/WS, set `document.body.dataset.bundle = 'loaded'` |
| `core/` | `lib/` | `api.js`, `state.js`, `poll.js`, `ws.js`, `view.js`, `mount.js` |
| `lib/` | nothing | `format.js`, `side-adjusted-price.js`, `dom.js` (pure helpers) |
| `charts/` | `lib/` | `TimeSeriesChart.js`, `series.js` (pure data assembly), `theme.js`, `candlestick.js`, `sparkline.js` |
| `panels/<name>/` | `core/`, `lib/`, `charts/`, `panels/support/` | `index.js` (component + `OWNS`), `model.js` (pure state→view-model), `CHEATSHEET.md` |
| `legacy/` | `legacy/`, `core/`, `lib/`, `charts/` | the 13 current modules, moved unchanged; deleted file by file; gone at T9 |

Rule: **nothing outside `legacy/` may import `legacy/`.** Otherwise the strangler never
finishes. Enforced by the import-graph guard (§6).

### 1.2 Core primitives

```js
// core/state.js — the only writer of app-level signals
import { signal, computed, batch } from '@preact/signals';
export const appState      = signal(null);          // last non-304 /api/state body
export const pollTick      = signal(0);             // +1 per successful non-304 poll
export const currentView   = signal('portfolio');
export const pollIntervalMs = signal(5000);         // set from cfg.kalshi.poll_interval_sec
export const connectivity  = signal({ lastOk: Date.now(), failures: 0 });
export const accountMode   = signal(localStorage.getItem('whale-signal-account-mode') || 'paper');
export const whaleSource   = computed(() => appState.value?.whale_source ?? null);
export function publishState(state) { batch(() => { appState.value = state; pollTick.value++; }); }
```

```js
// core/poll.js — one fetch, two consumers during the migration
let legacyTick = null;
export function registerLegacyTick(fn) { legacyTick = fn; }
export async function refresh() {
  const res = await fetch('/api/state');            // literal URL: the contract scanner keys on it
  if (res.status === 304) { connectivity.value = { lastOk: Date.now(), failures: 0 }; return; }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const state = await res.json();
  connectivity.value = { lastOk: Date.now(), failures: 0 };
  publishState(state);                              // migrated panels react via signals
  legacyTick?.(state);                              // un-migrated panels: the old imperative chain
}
```

```js
// core/mount.js — the multi-page enabler
export function mountPanel(Component, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return false;                            // page without this container → no-op
  el.dataset.owner = 'preact';
  render(html`<${Component} />`, el);               // Preact replaces the static placeholder children
  return true;
}
```

`core/api.js` keeps the exact name `fetchJSON(url, opts)` and the literal-first-argument
convention (the scanner's contract), adds `class ApiError extends Error { status; body }`
so the config panel can read structured `errors` while every existing `catch (e) → e.message`
site keeps working, and preserves `shared-utils.js:27-53`'s `detail` surfacing.

### 1.3 Panel contract

```js
// panels/<name>/index.js
export const OWNS = ['container-id', ...];         // DOM ids this panel alone may write
export function Panel() { /* htm template; reads signals/computed; local state via useSignal */ }
```

- `model.js` is pure (`(state, …) → viewModel`) and unit-tested without a DOM.
- Per-tick vs tab-open refresh is declared, not dispatched:

```js
// panels/support/useTabLoader.js
export function useTabLoader(view, load, { everyTick = false } = {}) {
  useSignalEffect(() => {
    if (currentView.value !== view) return;
    if (everyTick) pollTick.value;                  // subscribe → re-run each poll while open
    load();
  });
}
```
`everyTick:false` reproduces `showView`'s load-once-on-open semantics (paginated tables keep
their page/scroll — phase 139); `everyTick:true` reproduces `refreshHistoryInsightsIfActive`.
- Panels never subscribe to `appState` wholesale; they read `computed` slices so unrelated
  changes cost nothing. Keyed lists (`key=${row.id}`) everywhere a list re-renders.
- Module-level `effect()` only in `core/`; panels use `useSignalEffect`/`useTabLoader`
  (disposed on unmount).
- Event handlers are properties (`onClick=${…}`), never strings; no `window.*` exposure.

### 1.4 Coexistence rules during the strangler migration (enforced)

1. **One container id ↔ one renderer.** A panel's migration commit deletes every legacy
   write to its `OWNS` ids; `frontend/test/guards/container-ownership.test.js` greps
   `src/js/legacy/**` for them. Belt-and-braces: `mountPanel` marks the container
   `data-owner="preact"` and legacy's `$()` logs `console.error` when asked for an owned id,
   which the E2E console-error tracker (empty allowlist) turns into a failed test.
2. **Bridge before inversion.** T2 adds `publishState(state)` after legacy's fetch and
   `currentView.value = name` inside legacy `showView` so the pilot can subscribe; T3 inverts
   ownership (core owns fetch/timer/WS; legacy becomes `legacyTick`).
3. Each task leaves `main.js` bootable with the remaining legacy modules, so any task is a
   single `git revert`.

### 1.5 Data flow

```text
/api/state (6 s)  ──► core/poll.js ──► publishState() ──► signals ──► panels (active view only)
                                   └─► legacyTick(state) ──► old render chain (shrinks to zero)
WebSocket ───────► core/ws.js ──► terminalFeeds / tradeStreamStatus signals
tab click ───────► core/view.js ──► currentView ──► useTabLoader effects + .active toggling
config save ─────► POST /api/config ──► loadConfig() ──► cfg signal ──► pollIntervalMs
```

## 2. Config schema contract

### 2.1 `GET /api/config/schema`

```json
{
  "version": 1,
  "sections": [{
    "id": "strategy", "title": "Strategy", "description": "what the whale-follow strategy trades on",
    "order": 20, "open_by_default": true, "visibility": "ui", "custom_widgets": [],
    "fields": [{
      "path": "strategy.take_profit_pct", "label": "Take-profit (fraction of cost basis)",
      "type": "number", "nullable": true, "min": 0, "max": 1.0, "step": "any", "placeholder": "off",
      "max_source": "take_profit_ceiling(min_unit_cost=0.5)", "partial_above": 0.25,
      "enum": null, "item_type": null, "help": "…", "badges": ["hist"],
      "overridable": true, "locked_by": null
    }]
  }],
  "locked": { "kalshi_account.trading_enabled": { "route": "POST /api/trading/enable", "reason": "…" } },
  "hidden_paths": { "index_feed.enabled": "no UI consumer yet — promote by adding a FIELDS entry" }
}
```

- `type ∈ number | int | bool | select | text | list | map | range`. `list` = comma-joined
  strings (`strategy.excluded_series`, `kalshi.categories`); `map` = `KEY:value` pairs
  (`whale_watcher_kalshi.min_contracts_by_series`); `range` = two inputs bound to a 2-list
  (`whale_signal.whale_size_range`). `nullable:true` encodes every `'' → null` save site.
- `overridable:true` replaces `OVERRIDE_FIELDS` in `config-panel.js:420-433`.
- `custom_widgets` marks slots the generic renderer delegates: `"watchlist"` (search/pin UI),
  `"overrides"` (by_category/by_series editor), `"reset"` (Danger Zone).
- Source of truth: hand-authored Python data in `services/config/schema_fields.py` (seeded
  once from `index.html` by `tools/extract_config_schema_seed.py`, hand-reviewed, then the
  script is deleted with the markup), assembled per request by
  `services/config/schema.py:build_schema(cfg)`.

### 2.2 Dynamic bounds

`build_schema(cfg)` merges from `services/config/config_bounds.py` (corrected 2026-08-31
catch-up review — moved from bare `services/config_bounds.py` by commit `6e4338f`,
2026-08-27, two days after this design was written) per request:
`strategy.take_profit_pct.max = take_profit_ceiling(strat)`, `partial_above =
take_profit_universal(strat)`; `strategy.stop_loss_pct.max = STOP_LOSS_CEILING` (new public
constant `= 1.0 + _FEE_HEADROOM`); `min/max_unit_cost` exclusive bounds with a hint naming
`MIN/MAX_TRADEABLE_UNIT_COST`; `kelly_fraction_of_cap` static `0..1`. This makes the
`services/config/config_bounds.py:43` docstring ("`/api/config` warns") true — same line
number at the new path, only the path itself moved.

### 2.3 Locked fields

The three hardcoded refusals in `services/config/routes.py:30-56` become `locked_by` data.
The route walks the patch and refuses any locked path **with the same `detail` strings as
today** (`tests/test_trading_gate.py:113,895` assert string containment). A fourth gated field
is a data change.

### 2.4 Coverage of the YAML surface

`config/settings.yaml` has 24 sections / 134 leaf keys; the tab curates ~72. (`ROADMAP.md` "Flatten the config surface" item is the adjacent, still-open audit.) Every leaf not in
`FIELDS` must be listed in `HIDDEN_PATHS = {path: reason}` (env-managed secrets, auto-apply-
owned weights, "no UI consumer yet", `strategy_overrides` → custom editor). A test asserts
`curated ∪ hidden == all leaves` and no phantom curated paths, forcing a decision for every
future key. The panel shows "N settings are file-only — edit `config/settings.yaml`".

### 2.5 Validation

`POST /api/config`, in order: (1) locked → 400 (unchanged messages); (2) unknown section/key
→ 400 `unknown_field`; (3) per-field type / nullable / enum / static bounds → 400 (`bool`
strict, int/float interchangeable); (4) build the candidate = deep-copied current cfg merged
the way `config_store.update` merges, run `config_bounds.check_all(candidate)`; **only
violations on fields present in the patch reject** (`unreachable` → 400) — pre-existing
violations are echoed as warnings so an unrelated save is never blocked; (5) the existing
merge + change-history logging (`routes.py:64-76`) unchanged.

Responses: 400 keeps `detail` as a **string** and adds `errors:[{path,code,message,bound}]`;
200 stays the cfg dict. New `POST /api/config/validate` `{patch}` → `{ok, errors, warnings}`,
never mutates or logs; the panel calls it debounced on change and before save.

### 2.6 Frontend behaviour

`panels/config/` renders sections/fields from the schema; `loadConfig`/save become generic
(`getPath`/`setPath` + per-type coercion); each field carries `data-config-path`, which
replaces the text-content matching in `advisory-calibration.js:63-76` (`jumpTo(path)`);
locked fields render disabled with the route link; whale sim/real sections derive
enabled/disabled from the `whaleSource` computed (replacing the poll loop's DOM reach-in at
`polling-and-websocket.js:197-233`); a **patch-parity test** reproduces today's save
semantics (nullable blanks, list split/upper, map parsing, range) field by field.

## 3. Charts

`charts/TimeSeriesChart.js`: uPlot instance in a `useRef`, created in `useEffect`,
`setData(data, false)` when props change (no `innerHTML` rebuild; canvas node identity
survives ticks), `ResizeObserver` for width, `scales.x.time` per prop (numeric for the
entry-threshold sweep), colours from `theme.js` reading the `:root` tokens. `charts/series.js`
is pure (`history → columns`, baseline series) and unit-tested. The six `renderEquityChart`
mount points migrate through a signature-preserving adapter in `legacy/equity-and-cards.js`
so every call site upgrades at once. uPlot's ~40-rule stylesheet is vendored into
`static/css/dashboard.css` under a versioned header (one stylesheet, no second generated
artifact). Sparkline and candlestick move to `charts/` unchanged (SVG).
ROADMAP's "Consider a dedicated charts/graphs module" item is answered: dedicated charts module yes; server-rendered no.

## 4. Shared library and multi-page enablement

### 4.1 `lib/format.js`
`fmt` (USD, null → em-dash, unchanged), `pct(x, digits)`, `num(x, digits)`, `ago(ts)` — the
replacements for 97 hand-rolled `toFixed` sites. `lib/side-adjusted-price.js` is the single
owner of the no-side inversion (moved, re-exported by legacy `shared-utils.js` until T8e).

### 4.2 Financial-display rule (CLAUDE.md "Bug pattern to watch for")
Panels display backend-named fields; `model.js` never re-derives P&L, cost basis, exposure or
payout from neighbouring fields. T8b (portfolio) runs `dimensional-analysis` per
`.claude/rules/tooling-plugins.md`'s routing table before completion.

### 4.3 Multi-page enablement
Every panel mounts by container presence; `core/view.js` is the only tab-aware module. A
future page = a new esbuild entry that mounts the panels whose containers it includes; no
panel changes. `localStorage['whale-signal-view']` semantics stay in `core/view.js`.

## 5. Testing

| kind | tool | what |
|---|---|---|
| model tests | `node --test` | pure `model.js`/`series.js`/`format.js` functions, fixtures per branch |
| render tests | `node --test` + `preact-render-to-string` | each panel's output for empty / error / normal states |
| interaction tests | `node --test` + `happy-dom` (added at T7b) | e.g. Apply button is the same node after a new report arrives |
| guards | `node --test` | container ownership; (Python) import-graph, scanner |
| browser E2E | Selenium + Playwright (existing) | real chain HTML → bundle → routes; console-error allowlist stays empty |

E2E dependencies on globals are replaced: `document.body.dataset.bundle === 'loaded'`
replaces the `window.showView` probe and the `clearTerminalFeedCaches` grep marker; the Apply
flow becomes a real-DOM Playwright test via route fixtures (`/status` enabled + `/report` with
suggestions → click → forced 400 → assert text), retiring the weaker Selenium duplicate
(fallback: one documented `window.__e2e` surface).

## 6. Guards and CI ownership (investigation-to-guard rule)

| failure class | guard | owner |
|---|---|---|
| scanner blind to nested/new files | `frontend_contract.py` → `rglob`; nested-file scanner test | `quality-architecture-audit` |
| import cycles / layer violations / `window.*` growth / inline handlers | new `tools/quality_audit/frontend_graph.py` (import-edge parser, Tarjan SCC, layer rules; findings `frontend-import-cycle-member:*`, `frontend-layer-violation:*`, `frontend-window-export:*`, `frontend-inline-handler:*`); baseline accepts today's 13 / 197 / 21+68 with a dated `notes` entry; ratchets down; T9 flips to zero | `quality-architecture-audit` |
| two renderers on one container | `test/guards/container-ownership.test.js` + `data-owner` console error | `quality-frontend-build`, `quality-browser-e2e` |
| behaviour regressions in pure logic | `npm test` | `quality-frontend-build` |
| bundle bloat | `scripts/check-bundle-size.mjs` vs `bundle-budget.json` — **100,000 B gz** during coexistence (measured: 58.8 KB today + 9.1 KB Preact stack + 24.2 KB uPlot), ratcheted to a measured target after `--minify` at T9 | `quality-frontend-build` |
| tick-render cost | `performance.measure('tick-render')` surfaced in System Health | runtime diagnostic |

`quality-frontend-build` loses its path filter and `ci/woodpecker/pr/quality-frontend-build`
is added to `main`'s required checks (`gh api -X PUT …/branches/main/protection`), with
`docs/woodpecker-ci.md` and `.claude/rules/branching-and-ci.md:105-117` updated in the same task.

## 7. Toolchain changes

`frontend/package.json`: `"type": "module"`; `dependencies` preact, @preact/signals, htm,
uplot; `devDependencies` + preact-render-to-string (happy-dom at T7b); scripts `build`
(+`--target=es2022 --sourcemap`), `watch`, `lint` (`src/js test`), `test`
(`node --test 'test/**/*.test.js'`), `size`, `check`. `eslint.config.js` → ESM, second
block for `test/**`, `no-unused-vars: warn` during migration (error at T9); header rewritten.
`.gitignore` + `dashboard.bundle.js.map`. ddev needs no change (`web` runs Node 24.15).
`--minify` only at T9, after the identifier-grep test marker is replaced.

## 8. Documentation

Created: `frontend/CHEATSHEET.md` (layers, API-helper rule, ownership rule, test kinds,
JSX escape hatch), one `CHEATSHEET.md` per `panels/<name>/`, `services/config/CHEATSHEET.md`
update for the schema. Corrected in T1a: `CLAUDE.md:292-294`, `shared-utils.js:5-18`,
`main.js:12-17`, `polling-and-websocket.js:43-47`, `.github/workflows/quality.yml:42-43`,
`tools/quality_audit/source.py:25-27`, frontend-verification/integration-audit/
final-verification "committed bundle" wording, `ROADMAP.md:206` (6 views). At T9:
`ROADMAP.md`/`static/status.html` via `/sync-status-docs`, CLAUDE.md file map.

## 9. Risks and rollback

| risk | mitigation |
|---|---|
| half-migrated frontend worse than either end | every task strangler-safe and revertible alone; five mergeable PR groups; `main` deployable at each |
| silently blinded CI guard | rglob fix + scanner test land in T1a *before* any file move; ownership guard in T2; console-error allowlist stays empty |
| validation blocks legitimate saves | patch-scoped bounds rejection; `/validate` surfaces warnings without changing the save contract; the plan's T4a/T4b spell out their 14 tests directly (corrected 2026-08-31 catch-up review — no separate numbered list exists elsewhere); existing `test_trading_gate.py` kept green |
| weakened trading/auto-apply refusals | locked paths are data with unchanged messages and unchanged tests |
| financial-display regression during rewrites | backend-named fields only; render tests with backend-shaped fixtures; dimensional-analysis on T8b |
| runtime template errors (htm has no build-time markup check) | render tests per branch; E2E on every push |
| bundle/perf regression | size budget in a required pipeline; tick-render measurement before adding per-row signals |
| cold sessions lose the thread | orchestrator skill reconstructs progress from `panels/`, `legacy/`, ratchet values and plan checkboxes |
| library maintenance | htm is ~500 lines and codemoddable to JSX; Preact/uPlot are actively maintained; all pinned with caret ranges and `npm ci` |

Rollback: `git revert <task>`; the two one-way-feeling steps (T5a markup removal, T9 legacy
deletion) are recoverable from git and gated on ratchets reading zero.

## 10. JSX escape hatch (documented, not used)

Append `--jsx=automatic --jsx-import-source=preact` to the esbuild scripts, add
`parserOptions.ecmaFeatures.jsx: true` and `src/js/**/*.{js,jsx}` to the lint block, extend
the scanner and manifest suffix lists, and pick a test transform (vitest or an esbuild
pre-bundle). ESLint ≥ 10 tracks JSX references natively — **no React lint plugin**
(`eslint-plugin-react` does not install against ESLint 10). Convert file by file; htm and
JSX coexist.

## 11. Success criteria (per-module axes)

- **Effectiveness**: every panel renders from backend-named data through a tested model;
  config saves are validated; the Apply-mid-click and scroll-reset classes are structurally
  impossible (keyed rendering, no wholesale `innerHTML`).
- **Efficiency**: one state write per tick; only the active view's panels do work; charts
  update in place; bundle within budget.
- **Informativeness**: schema-served help/bounds/locks in the UI; tick-render cost visible;
  import-graph and ownership guards report drift in CI; every panel has a CHEATSHEET.
