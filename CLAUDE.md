# autotrade / kalshi-whale-poc

Kalshi whale-signal trading terminal: real Kalshi market data + a simulated-or-live whale order-flow signal + a broker layer, running in paper mode. No real order is placed unless `kalshi_account.trading_enabled` is flipped in `config/settings.yaml` plus a typed in-app confirmation. Program sequencing: `docs/kalshi-personal-production-execution-program-2026-08-26.md`; itemized checklist: ROADMAP.md "Path to production". This file is a rulebook: one line per rule. The incident behind any rule is in `git log -S'<phrase>' -- CLAUDE.md`, not here.

## HARD RULE — the data plane is the product (permanent, 2026-08-27)

End to end, every piece of this app depends on optimal data completeness, accuracy, flow rate, timeliness, fidelity, and speed of execution. Permanent: not superseded by any later objective, not traded for convenience; an instruction that appears to relax it is a misunderstanding to raise.

- Completeness: every whale print, market update, and lifecycle event reaches the code that decides on it; a dropped message, skipped candidate, or DB hole is a defect (sample-size-gated heuristics fail silently on missing data).
- Accuracy: a value means exactly what its label says (cents vs dollars, yes vs no side, contracts vs notional, unrealized vs cumulative).
- Flow rate: sustained throughput at full subscription scope; a backlog that "catches up later" is already a timeliness failure.
- Timeliness: stale-but-correct data is the wrong input to a decision.
- Fidelity: store and replay what the exchange sent; no lossy normalization on the way in (`.claude/rules/kalshi-integration-authority.md`).
- Speed of execution: signal-to-order latency is part of the edge; the trading/WebSocket hot path stays hot.
- These properties fail silently: measure them (see "Start investigations here"); never infer health from the absence of errors, green tests, or unremarkable P&L.
- Never trade one property for another silently; make the tradeoff explicit and measured.
- Never change queue capacity, consumer/connection/thread counts, subscription scope, REST rate, batch size, cache TTL, retry budget, or polling frequency because it "should help": identify the measured bottleneck and its mechanism first.
- A root-cause claim rests on, in order of preference: deterministic reproduction, correlated runtime telemetry, source state-transition proof, protocol/library documentation. "Queue depth was high when latency was high" is correlation, not causation.
- Any diagnostic or abstraction on the exchange-wide hot path is measured for runtime cost before it ships.
- A confirmed bottleneck gets competing solution families compared on mechanism, benchmark, correctness, failure behavior, and complexity, and permanent detection for recurrence — not the first fix that works.
- "Optimal" is measured against what `docs/kalshi/` says the exchange permits, not against today's behavior.
- This rule never justifies shortcutting a safety gate, weakening a kill switch, enabling real trading, or discarding accumulated history.

## HARD RULE — never guess; verify or falsify (permanent, 2026-08-29)

Not knowing is a research task, not a probability estimate. Permanent: applies equally to app code, tooling, MCP/plugin calls, shell flags, and any claim made to the user.

- A field, parameter, signature, endpoint, flag, config key, or schema comes from reading the authoritative source — `docs/kalshi/` for Kalshi, the tool's own loaded schema for a tool, the module for a symbol, the DB for a value — never from recall or plausibility.
- Read before writing, every time: the call site, the function, the doc page, the tool definition. "It is probably called X" is the failure mode, and one wrong guess at a parameter name is indistinguishable from broken wiring.
- When a check is cheap, run it instead of reasoning about it; one probe beats a paragraph of inference.
- A claim ships with its evidence and with what would falsify it. Anything unverified is labeled an assumption, in the same sentence.
- A retry that succeeds is not verification: establish why the first attempt failed before changing the input, or the next failure is unexplained too.
- Correlation is not a mechanism, and one passing observation is not a property (see the data-plane rule).
- If the authoritative source cannot answer, say so and stop; a gap filled with a guess is read as fact by the next session.

## Standing goal (2026-08-26) and current objective (2026-08-23)

- Destination: a personal-use, real-money trading system — reached only through ROADMAP.md "Path to production"; several items there are human decisions (position sizes, kill-switch numbers, sports-category legal exposure, auth model, deployment target), not commits.
- Current focus stays paper trading (Programs 1–2). Do not prioritize live execution, shadow qualification, or capital qualification (Program 3+) over realtime/economic correctness.
- The 70%/70% target is retired. Judge each `services/<name>/` module on effectiveness, efficiency, and informativeness, plus the six data-plane properties above.
- Read a module's `README.md` (`CHEATSHEET.md` for `services/kalshi/` and its raw-payload consumers `market_catalog`, `market_watch`, `market_events`, `whale_stream`) before auditing it; cross-post confirmed findings there with a date.
- Open gaps (detail in ROADMAP.md): pricing/edge gap at entry (0.60–0.95 unit-cost band negative-EV, designed not implemented); shadow mode has never produced a trade; no deployment target; `risk.max_daily_loss_pct` is 0.8, deliberately non-protective while in paper-mode training (decided 2026-08-30 — see git log for that date); zero category-level legal-risk awareness; auto-apply has only tuned on paper history.

## Start investigations here (in order, before any ad hoc script or `sqlite3`)

1. `GET /api/quality/summary` 2. `GET /api/health/pipeline` 3. `GET /api/health/faults` 4. `GET /api/observability/summary` 5. `GET /api/health/storage` (+ `POST /api/health/storage/scan`) 6. `python -m tools.quality_audit`.
- Investigation-to-guard: every real bug class gets a disposition (runtime diagnostic / CI guard / shared logic / already covered / one-off) recorded in the commit message.
- `tools/quality_audit/baseline.json`: adding an ID is a reviewed decision with a dated note in `notes`, never a way to make CI green; remove an ID when its finding resolves and say why.

## Docs and history

- `docs/next-action.md` holds the single next action and is printed in every session banner; "continue" means do that one thing. Rewrite it at the end of a session; never leave it describing finished work.
- `git log`/`blame`/`diff` are the only maintained history since the 2026-08-07 cutover. `ROADMAP.md` is the living to-do (check items off in place; `/close-roadmap-item`). `docs/open-decisions.md` is the single list of parked decisions, printed every session — act on a line or ask about it; never write a new plan for something already on it.
- Frozen, not maintained: `docs/status-archive-2026-08-26.html` (the pre-git narrative), `docs/roadmap-archive-2026-08-09.md`.

## Dev workflow — ddev, not bare uvicorn

- `ddev describe` first; it is usually already running. `web` (nginx, docroot `static`) is the only public entry; `fastapi` is reachable only as `fastapi:8000` inside the docker network (deliberate: Traefik tie-break bug).
- `.py` edits hot-reload in ~1–2 s; `.ddev/**` edits need `ddev restart`; anything that must survive a real process restart needs a real `ddev restart`.
- `ddev exec -s fastapi <cmd>` for in-container commands (runs as root; use it to delete root-owned leftovers). It refuses to run from a linked worktree directory: run it from the primary root and `cd /app/.claude/worktrees/<name>` inside.
- Hooks: `.claude/settings.json` is read from the session's current checkout (worktree or primary); every entry resolves that checkout from the hook payload's `cwd` and runs its `.claude/hooks/run_hook.py` — never via `$CLAUDE_PROJECT_DIR`, which is always the primary (a primary parked on a branch without the launcher silently disabled every hook in every worktree session, 2026-08-28; CI-enforced by `tests/test_hooks_wiring.py`). Never reference a hook script directly. A session that starts without the `=== autotrade orientation ===` banner has no hooks at all — its checkout predates the launcher; merge `origin/main` into it first.
- App: `https://kalshi-whale-poc.ddev.site:8443`; `GET /api/state` is the fastest live read; `ddev logs -s fastapi|web` for logs; `/run` skill for detail.
- Public tunnel `autotrade.webfoundry.dev` is Basic-Auth gated in `.ddev/nginx/kalshi-proxy.conf`; `.env` `SITE_BASIC_AUTH_*` is the source of truth, regenerated on `ddev start`.
- CORS stays restricted to the ddev host + `localhost:8000` (`ALLOWED_ORIGINS` in `.env`).

## `data/*.db` files are live and are a first-class asset

- Never rm/mv a `data/*.db` while ddev is up; prefer `POST /api/reset` over deleting `paper_broker.db`.
- Accumulated history is the dataset every sample-size-gated heuristic depends on: schema changes are additive only (`CREATE TABLE IF NOT EXISTS` + `_add_column_if_missing`); tests always `monkeypatch` `DB_PATH` to a tmp path; manual verification is read-only or a set-confirm-revert round trip.
- Persistence idiom — one SQLite file per concern, no shared DB, no ORM:

```python
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "X.db"

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS ... ")
    return conn
```

- Moving a module one directory deeper changes `DB_PATH`'s `.parent` chain; fix it in the same edit and check `git status` for a stray `services/data/`.

## Safety invariants — never regress

- `mode: paper` by default. Real orders (`services/kalshi_account_client.py`) are gated by `kalshi_account.trading_enabled` (default `false`) plus a typed confirmation (`POST /api/trading/enable`).
- The daily-loss kill switch (`services/risk_manager.py`) and paper-broker state persist (`risk_state.db`, `paper_broker.db`); keep `day_start_bankroll` consistent with the broker's persisted bankroll.
- Nothing in diagnostics, verification, refactoring, or quality work enables real trading, weakens a gate, or resets live data.
- Automation — hooks, CI, anything under `tools/` — never edits trading, risk, sizing, calibration, strategy, settlement, auth, or CI-credential code, and never weakens, baselines, or bypasses a guard to go green.

## A displayed value must match its label

- Trace every displayed financial figure to its backend definition (`PaperBroker.equity()` / `cost_basis()` / `mark_to_market()`); expose backend-computed fields rather than re-deriving client-side. Two shipped bugs of this shape: the no-side `1 - price` inversion and `equity - starting_bankroll` labeled as unrealized P&L. Run `dimensional-analysis` after any money/probability math.

## Workflow/tooling and application code never overlap

- Tooling lives in `tools/`, never `services/` or `main.py`; its config is its own, never `config/settings.yaml`; it runs externally (human, cron, CI), never from the app's tick loop or state; the app never imports, configures, or schedules a tool (test isolation registries included); a tool may read the app through a real API.

## Kalshi API — `docs/kalshi/` is ground truth (HARD RULE)

- Before writing or editing any code that reads, parses, classifies, derives, infers, or groups Kalshi-sourced data, `grep -rn` `docs/kalshi/` (index `llms.txt`, provenance `README.md`) — not memory, not one live response. This applies to new feature work, not only call-site edits.
- Check `docs/kalshi/CHEATSHEET.md` titles first (printed every session); add an entry whenever a page resolves a real question.
- Full rule: `.claude/rules/kalshi-integration-authority.md`; skill: `kalshi-contract-review`.

## Branching, CI, sessions

- `main` is protected; work on `feat/|fix/|refactor/|chore/|docs/` branches; PR → `gh pr merge --merge` → delete the branch (`.claude/rules/branching-and-ci.md`). Read a PR body before merging.
- CI (Woodpecker, `.woodpecker/*.yml`) is the only full-suite owner. Locally run only the targeted test files; the per-edit hook already does this. Confirm CI via `gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status`.
- Checkpoint often (`/checkpoint`): commit verified units, stage specific paths, never `git add -A`, never commit a failing state.
- Parallel sessions share one primary checkout: `orient.sh` lists the live ones and `ListAgents` names them; work in a worktree under `.claude/worktrees/` (`git worktree add … origin/main`, then `EnterWorktree`); never checkout/stash/reset/rebase/merge under another session's work (R6 denies it); never edit a file another session names as in use.
- Suggest `/compact` at every phase boundary and `/clear` before unrelated work; keep working through usage limits.
- Subagents: `model: haiku` for mechanical read-only work; `isolation: "worktree"` for heavy self-contained tasks; Explore/Plan agents skip CLAUDE.md — pure lookup only.

## Toolchain

- Process is `superpowers:*` — brainstorming, writing-plans, executing-plans (numbered plans under `docs/superpowers/plans/` included), test-driven-development, systematic-debugging for any bug, verification-before-completion before "done", requesting-code-review, using-git-worktrees. Project skills are only `/run`, `/checkpoint`, `/kalshi-contract-review`, `/kanban-board-sync`, `/close-roadmap-item`, `/config-field-edit`; never add one that duplicates a plugin.
- GitNexus is pinned: `npx gitnexus@1.6.10`, never `@latest` — an in-place upgrade under a running MCP server breaks every query until sessions restart (2026-08-28). `impact`/`context`/`trace` before multi-file edits to strategy, risk, advisory, calibration, kalshi client, or shared state (R4 asks once per session). Its Claude Code hooks stay installed (`npx gitnexus@1.6.10 setup -c claude`, user-run) and their staleness nudge advertises `@latest` — ignore that string, it contradicts the pin above; `orient.sh` reports index staleness; `/checkpoint` re-analyzes after a merge. It never walks dot-directories (`dot: false` in its walker; no `.gitnexusignore` rule can reach them), so `.claude/hooks/` is covered by `tests/`, not by the graph. If two unrelated symbols return the same impact set, the index is corrupt — `npx gitnexus@1.6.10 clean` then `GITNEXUS_WAL_CHECKPOINT_THRESHOLD=67108864 npx gitnexus@1.6.10 analyze --force --skip-agents-md` (the raised WAL threshold is what got the 2026-08-25 rebuild past the checkpoint error that corrupted it); never reason from it.
- MCP servers: `gitnexus` is declared in the repo's `.mcp.json` at the pinned version (that file is the pin the harness actually launches; `tests/test_mcp_and_plugin_wiring.py` fails if it drifts from the version stated here). `github` stays in user config — it is cross-project and carries a personal token, which a committed file must never hold. Plugins are enabled/disabled in `.claude/settings.json`'s `enabledPlugins`.
- `dimensional-analysis` after any cents/dollars/probability/contracts/P&L math; chrome-devtools MCP for browser evidence (`https://kalshi-whale-poc.ddev.site:8443`); context7 for FastAPI/Pydantic/asyncio docs, never for Kalshi; 42crunch and second-opinion are blocked (no account) and therefore disabled in `enabledPlugins` — re-enable there if an account ever exists; an enabled-but-unreachable plugin advertises skills that always fail.
- The user's own tools under `tools/` (`kanban_sync`, `quality_coordination`, `quality_ratchet`, `quality_audit`) are wired into `/checkpoint`; an unused tool is a workflow gap to wire in, never cruft to retire.

## Quick file map

- `main.py` FastAPI app + trading loop · `services/` one package per concern · `tools/` standalone tooling (`quality_audit/`, `quality_ratchet.py`, `quality_coordination.py`, `kanban_sync/`, replay/probe scripts) · `static/` + `frontend/` dashboard (esbuild bundle, no framework build) · `config/settings.yaml` live-reloadable tuning · `.env` secrets (all optional; see `.env.example`) · `docs/kalshi/` mirrored API docs · `.claude/` hooks, rules, skills · `.woodpecker/` authoritative CI · `.github/workflows/` `workflow_dispatch` fallbacks · `.ddev/nginx/kalshi-proxy.conf` proxy rules.
