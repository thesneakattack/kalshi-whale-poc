# Frontend Modularization Plan — Freshness Check (2026-09-03)

**Subject:** `docs/superpowers/plans/2026-08-25-frontend-modularization.md`
(spec: `docs/superpowers/specs/2026-08-25-frontend-modularization-design.md`;
prior review: `docs/superpowers/plans/2026-08-25-frontend-modularization-catchup-consolidation.md`,
2026-08-31).

**Why:** the plan was last touched 2026-08-31. It has never been decomposed
into sub-issues or started (verified below: no `frontend/src/js/{legacy,core,
lib,charts,panels}/` directories exist; `frontend/package.json` has none of
the three runtime deps the plan adds). In the 9 days since, 10 commits
touched `frontend/src/js/` or `static/index.html`, plus an unrelated but
larger volume of churn in `CLAUDE.md` and `ROADMAP.md`. This document checks
whether the plan's own cited excerpts (file:line pairs quoted as "before"
states for its tasks) still match current `HEAD`, before handing the plan to
implementation subagents.

**Method:** `git log --oneline --since=2026-08-25 -- frontend/src/js/
static/index.html` for the 10 commits; every task in the plan carrying a
`file:line` or `file:line-range` citation was checked by reading the cited
range at current `HEAD` and comparing it against what the task's own prose
says should be there (a function name, a specific string, a semantic
boundary) — never by assuming a citation is still valid because its commit
count "looks small." Where a citation was wrong, the actual current location
was found by `grep -n` for the same anchor content the plan itself names
(a function name, a literal string, a comment), then diffed against the
commit(s) that shifted it, to establish a mechanism (not just "line numbers
don't match").

## Verdict: GO, with 5 corrections applied directly to the plan

The plan is safe to hand to implementation subagents **as corrected** — no
task is invalidated in substance, no task is already done or partially done,
and the dependency/tooling status the plan assumes is unchanged. Five stale
`file:line` citations were found; all five are footnote-level ("this is
where the old code the new code replaces currently lives"), none change a
task's Create/Modify/Delete file list, acceptance criteria, or commit
message. All five have been corrected directly in
`docs/superpowers/plans/2026-08-25-frontend-modularization.md` with a dated
changelog note at its top, per this session's own investigation (this is a
mechanical citation fix — verified line-for-line against current `HEAD`, not
a design change), consistent with how the 2026-08-31 catch-up review already
corrected this same document once before.

## Corrections applied (old citation → verified current citation)

| Task | Old (stale) | New (verified) | Root cause |
|---|---|---|---|
| T1a | `CLAUDE.md:148` | `CLAUDE.md:132` | `CLAUDE.md` was rewritten again after the 2026-08-31 review that set `:148` — 15+ commits touched it since (`git log --since=2026-08-31 -- CLAUDE.md`), continuing the same "file rewritten smaller" pattern the 08-31 note already flagged once. Current `HEAD` is 132 lines total; the "no UI framework installed yet — Preact migration designed... not started" sentence this citation means now lives in the "Quick file map" line, `:132`. |
| T1a | `ROADMAP.md:206` (6 views) | `ROADMAP.md:414` (6 views) | Current line 206 is the unrelated `data/*.db` backup/retention-policy bullet — not about views/tabs at all. The actual stale claim this task means to fix is the "browser nav replacing `showView()`'s **7**-tab toggle" phrase (current `VIEWS` array in `main.js` has 6 entries: portfolio/markets/whale/terminal/history/config) at line 414. This mismatch predates this freshness check (no `ROADMAP.md` commit in the 2026-08-25→now window touches line ~414's content, confirmed via `git log -S"7-tab" -- ROADMAP.md` returning nothing since 2026-08-25) — it is not new drift from the 10 frontend commits, but the line-number `:206` was already wrong at 08-31 review time (ROADMAP.md has had 20+ unrelated commits since 08-25 that shifted line numbers throughout the file) and needs fixing regardless of cause. |
| T3 | `tests/test_e2e_terminal_static_and_api.py:65` | `tests/test_e2e_terminal_static_and_api.py:85` | Commits `8d8edac`/`aa1cbf9` (2026-08-30, Docker-Desktop-DNS-sentinel skip-check fixes) added ~20 lines earlier in the same file, above the actual anchor. The "grep marker" the task means is `assert 'clearTerminalFeedCaches' in js_resp.text` (spot-checking the built bundle contains a known helper), currently at line 85, not 65. |
| T5a | `legacy/config-panel.js:125-229` | `legacy/config-panel.js:126-230` | Commit `8b5f7ab` (2026-08-27, opt-in `close_positions_first` paper-reset flag) inserted one line into the `config-reset-btn` click handler, above this range. The cited range is the `$('save-config-btn').addEventListener(...)` handler (the patch-building logic T5a's `panels/config/model.js` must reproduce for patch-parity) — currently lines 126-230, confirmed by direct `grep -n` for both the opening `addEventListener` line and its closing `});`. |
| T6 | `legacy/equity-and-cards.js:97-138` | `legacy/equity-and-cards.js:117-158` | Two 2026-09-02 commits (`8e203f5` extracting shared `pnlRowTint`/`marketResultBadgeHTML`, `2624d1e` extending the trade-log P&L gradient/market-result column to the Simple view and History tab) added a net ~20 lines above this function in the same file. The cited range is `renderEquityChart(...)` — the plain-SVG chart function T6 replaces with a uPlot-backed, signature-preserving adapter — confirmed by `awk`-bounded read of the function from its `function renderEquityChart(...)` line to its matching closing `}`, currently 117-158. |

## Citations checked and confirmed still accurate (no change needed)

- T1a: `tools/quality_audit/frontend_contract.py:141` (`glob("*.js")` — present verbatim).
- T1a: `frontend/src/js/shared-utils.js:5-18` (header comment block — verbatim match).
- T1a: `frontend/src/js/main.js:12-17` (header comment block — verbatim match).
- T1a: `.github/workflows/quality.yml:42-43` ("Verify committed bundle matches source" step —
  present; a *different* step in the same file, "Check status.html is current," was removed
  by commit `1961761` on 2026-08-26, but it sits after the cited lines and doesn't shift them).
- T1a: `tools/quality_audit/source.py:25-27` (comment block — verbatim match).
- T2: `system-health.js:58-75` (the `droppedTotal`/`activeAlerts`/`faults`/`dbs`/`storageFindings`/
  `research` derivation block the panel's model must reproduce — verbatim match; a backend-only
  commit, `6c61356`, touched the metrics this block *reads* but not this file or its shape).
- T2: `polling-and-websocket.js`'s `loadSystemHealth` import (line 5) and call (line 108) —
  present as described.
- T2: `static/index.html`'s `system-health-summary`/`system-health-findings` ids (lines 279-280) —
  present as described.
- T3: `tests/test_browser_e2e.py:164` (`window.showView` typeof probe — verbatim match).
- T4a: `services/config/config_bounds.py` (path already corrected by the 08-31 review after
  commit `6e4338f`'s move) and its `:43` docstring line — verbatim match, unaffected by anything
  since.
- T5a: `legacy/advisory-calibration.js:63-76` (`jumpToConfigSetting(path)` — confirmed by
  `awk`-bounded read, function spans exactly lines 63-76; the file's only recent commit,
  `5008348`, edits line ~230, well after this range).
- T5a/T5b: `static/index.html:566-912` and `:913-968` (config-fields vs. watchlist/reset section
  boundaries) — both verbatim matches against current `HEAD`; the one-line insertion from
  `8b5f7ab` lands at line ~955, inside the already-correct `913-968` range, so it doesn't
  invalidate the boundary.
- T7b: `tests/test_browser_e2e.py:167-193` (`test_failed_api_action_is_surfaced_as_failure_not_
  false_success` — verbatim match; file untouched by any of the 10 commits).
- T8c: `renderFeedListSmooth`/`renderSignals` — still both defined in `signals-feed.js` (lines 13
  and 50), which none of the 10 commits touch.
- T8e: `__backdropMouseDownOnSelf` — still present, `screener-and-header.js:198`.

## No task is already done or partially done

Confirmed by directory listing: `frontend/src/js/` is still completely flat
(13 files, no `legacy/`, `core/`, `lib/`, `charts/`, or `panels/`
subdirectories exist anywhere under `frontend/src/`). None of the 10 commits
since 2026-08-25 introduce any of those directories, `preact`/`@preact/
signals`/`htm` imports, or ES-module `import`/`export` restructuring beyond
what already existed pre-plan. The plan is at "not started" exactly as
`CLAUDE.md`'s own file map already states.

## Dependency check

`frontend/package.json` (read directly, current `HEAD`):

```json
"devDependencies": {
  "esbuild": "^0.24.0",
  "eslint": "^10.0.0"
}
```

No `dependencies` block exists at all — zero runtime deps, confirming
`preact`, `@preact/signals`, and `htm` are all still absent. Matches the
plan's own assumed starting state; no drift.

## Non-blocking notes for implementers (not citation fixes — no line numbers to correct)

These don't invalidate any task's file list or acceptance criteria, since the
plan's own Create/Modify/Delete bullets for the affected files are already
written at file-level, not line-level, granularity for these spots — but an
implementer should know the legacy files carry more logic than they did when
the plan was researched:

- **T5b** (`panels/config/reset.js`): the paper-reset Danger Zone gained a
  new opt-in `close_positions_first` checkbox (`static/index.html:957`,
  `config-panel.js` inside the `config-reset-btn` handler) via commit
  `8b5f7ab` (2026-08-27) — one more field to port when the reset panel is
  built. Already inside T5b's cited `static/index.html:913-968` delete range,
  so no range fix needed, just a heads-up it's there.
- **T8d/T8e** (`panels/markets/`, `panels/whale-watch/`, modal panels):
  `screener-and-header.js` and `shared-utils.js` gained a Watchlist-sidebar
  "group by event, not just series" feature across three commits
  (`70b2e83`/`502bd32`/`cf43c41`, PR #390) — up to ~140 changed lines in
  `shared-utils.js` in one of those commits alone. T8e's task already deletes
  both files generically ("remaining inline handlers ... gone"; delete
  `legacy/screener-and-header.js`, `legacy/shared-utils.js`) without
  itemizing functions, so this doesn't break a citation, but there is
  genuinely more logic in both files now than existed when T6/T8d/T8e were
  scoped.
- **T1a** baseline counts ("13 `frontend-import-cycle-member:*`, 197
  `frontend-window-export:*`") are pre-computed estimates from before the
  scanner (`tools/quality_audit/frontend_graph.py`) exists; T1a's own
  instructions already say to accept "today's findings" when the scanner is
  actually run, so these numbers are expected to be recomputed at execution
  time rather than trusted as-is — flagging only so nobody treats "197" as a
  citation to defend if the real run finds a different number (the six
  commits touching `shared-utils.js`/`screener-and-header.js`/`equity-and-
  cards.js`/`trade-log-and-real.js`/`advisory-calibration.js`/`trading-gate-
  and-connectivity.js` plausibly changed the real window-export count from
  whatever it was on 2026-08-25).

## Out of scope for this check

No live-app verification was performed (not needed — this is a git-history
and static-file check). No code was written. `config/settings.yaml` and
`data/*.db` were not touched. This check does not re-derive whether the
plan's *design* (Preact/signals/htm, the panel contract, the five PR groups)
is still the right call — only whether its citations of current source match
current source. That design question was already settled by the 08-25
spec + 08-31 catch-up review and is not reopened here.
