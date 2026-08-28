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
- Never trade one property for another silently; make the tradeoff explicit and measured (`.claude/rules/realtime-data-plane-evidence.md`).
- "Optimal" is measured against what `docs/kalshi/` says the exchange permits, not against today's behavior.
- This rule never justifies shortcutting a safety gate, weakening a kill switch, enabling real trading, or discarding accumulated history.

## Standing goal (2026-08-26) and current objective (2026-08-23)

- Destination: a personal-use, real-money trading system — reached only through ROADMAP.md "Path to production"; several items there are human decisions (position sizes, kill-switch numbers, sports-category legal exposure, auth model, deployment target), not commits.
- Current focus stays paper trading (Programs 1–2). Do not prioritize live execution, shadow qualification, or capital qualification (Program 3+) over realtime/economic correctness.
- The 70%/70% target is retired. Judge each `services/<name>/` module on effectiveness, efficiency, and informativeness, plus the six data-plane properties above.
- Read a module's `README.md` (`CHEATSHEET.md` for `services/kalshi/` and its raw-payload consumers `market_catalog`, `market_watch`, `market_events`, `whale_stream`) before auditing it; cross-post confirmed findings there with a date.
- Open gaps (detail in ROADMAP.md): pricing/edge gap at entry (0.60–0.95 unit-cost band negative-EV, designed not implemented); shadow mode has never produced a trade; no deployment target; `risk.max_daily_loss_pct` is 0.85 (not protective); zero category-level legal-risk awareness; auto-apply has only tuned on paper history.

## Start investigations here (in order, before any ad hoc script or `sqlite3`)

1. `GET /api/quality/summary` 2. `GET /api/health/pipeline` 3. `GET /api/health/faults` 4. `GET /api/observability/summary` 5. `GET /api/health/storage` (+ `POST /api/health/storage/scan`) 6. `python -m tools.quality_audit`.
- Investigation-to-guard: every real bug class gets a disposition (runtime diagnostic / CI guard / shared logic / already covered / one-off) recorded in the commit message (`.claude/rules/quality-capabilities.md`).
- `tools/quality_audit/baseline.json`: adding an ID is a reviewed decision with a dated note in `notes`, never a way to make CI green; remove an ID when its finding resolves and say why.
- Budget: one investigation pass, at most one session, before a code change lands; plans stay under 300 lines; the write-up comes after the fix and fits on one page.

## Docs and history

- `git log`/`blame`/`diff` are the only maintained history since the 2026-08-07 cutover. `ROADMAP.md` is the living to-do (check items off in place; `/close-roadmap-item`). `docs/open-decisions.md` is the single list of parked decisions, printed every session — act on a line or ask about it; never write a new plan for something already on it.
- Frozen, not maintained: `docs/status-archive-2026-08-26.html` (the pre-git narrative), `docs/roadmap-archive-2026-08-09.md`.

## Dev workflow — ddev, not bare uvicorn

- `ddev describe` first; it is usually already running. `web` (nginx, docroot `static`) is the only public entry; `fastapi` is reachable only as `fastapi:8000` inside the docker network (deliberate: Traefik tie-break bug).
- `.py` edits hot-reload in ~1–2 s; `.ddev/**` edits need `ddev restart`; anything that must survive a real process restart needs a real `ddev restart`.
- `ddev exec -s fastapi <cmd>` for in-container commands (runs as root; use it to delete root-owned leftovers). It refuses to run from a linked worktree directory: run it from the primary root and `cd /app/.claude/worktrees/<name>` inside.
- Hooks: `.claude/settings.json` is read from the current worktree but `$CLAUDE_PROJECT_DIR` is the primary checkout, so every hook command goes through `.claude/hooks/run_hook.py`, which runs the session's own checkout's copy (CI-enforced by `tests/test_workflow_budgets.py`).
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
- Parallel sessions share one checkout: `ListAgents` first; work in a worktree under `.claude/worktrees/`; never checkout/stash under another session's work; never edit a file another session names as in use.
- Suggest `/compact` at every phase boundary and `/clear` before unrelated work; keep working through usage limits.
- Subagents: `model: haiku` for mechanical read-only work; `isolation: "worktree"` for heavy self-contained tasks; Explore/Plan agents skip CLAUDE.md — pure lookup only.

## Toolchain

- Use installed plugins before writing anything of your own; `session_orient.sh` prints the list every session; routing detail in `.claude/rules/tooling-plugins.md`. Never build a project skill or tool that duplicates a plugin.
- Numbered plans run under the `plan-task` skill (+ `domains/<domain>.md`); bugs under `superpowers:systematic-debugging`; nothing is "done" without `superpowers:verification-before-completion`.

## Quick file map

- `main.py` FastAPI app + trading loop · `services/` one package per concern · `tools/` standalone tooling (`quality_audit/`, `quality_ratchet.py`, `quality_coordination.py`, `kanban_sync/`, replay/probe scripts) · `static/` + `frontend/` dashboard (esbuild bundle, no framework build) · `config/settings.yaml` live-reloadable tuning · `.env` secrets (all optional; see `.env.example`) · `docs/kalshi/` mirrored API docs · `.claude/` hooks, rules, skills · `.woodpecker/` authoritative CI · `.github/workflows/` `workflow_dispatch` fallbacks · `.ddev/nginx/kalshi-proxy.conf` proxy rules.
