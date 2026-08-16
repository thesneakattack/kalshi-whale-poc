# autotrade / kalshi-whale-poc

Kalshi whale-signal paper trading terminal. Real Kalshi market data + a
simulated-or-live whale order-flow signal + a fake broker. Safety-first POC:
no real order ever gets placed unless `kalshi_account.trading_enabled` is
explicitly flipped in `config/settings.yaml` plus a typed in-app confirmation
phrase. All P0 code-level safety gates are done; what's still open before
real capital should depend on this is operational — see ROADMAP.md's "Path
to production" section.

## Git history + supplementary docs

This became a git repository partway through the project's life (see the
first commit's message for the cutover point) — everything built before that
has no real commit-by-commit history, which is why hand-maintained docs
exist and remain the primary source for the *why* behind pre-git work:

- **`ROADMAP.md`** — forward-looking, living to-do list, kept short. Check
  items off in place (`- [x]`), add new ones as they turn up. Shipped work
  gets folded into the "Shipped (condensed)" section as a one-line pointer,
  not a narrative — the real detail belongs in `status.html`. Check the
  **"Path to production"** section before touching anything safety-adjacent
  (kill switch, real trading, CORS, auth) — P0 itself is fully shipped, so
  that's where the remaining open safety/correctness-adjacent questions
  (shadow-mode review, deployment target, auth model, real position sizing,
  category-level legal risk) actually live now.
- **`static/status.html`** (served at `/status`) — backward-looking historical
  record. A manually maintained, chronological timeline of build phases, plus
  reference tables (components, API routes, config, known limitations). This
  is hand-written prose describing what was built and why, not generated —
  it can and does go stale if a change doesn't update it.
- **`docs/roadmap-archive-2026-08-09.md`** — a frozen, one-time snapshot of
  `ROADMAP.md`'s full pre-condensing detail (it had grown to 837 lines of
  mostly-shipped narrative). Not maintained going forward; consult it (or
  `git log`/`git show` on `ROADMAP.md`) for the full story behind anything
  checked off before 2026-08-09 that `status.html` doesn't already cover.

For anything committed going forward, prefer `git log` / `git blame` / `git
diff` as the primary source of "what changed and why" — that's real history,
not reconstructed prose. Keep using `ROADMAP.md` and `status.html` as the
living, human-readable layer on top: check "Path to production" before
safety-adjacent work, and still update both when a roadmap item ships.

**When a roadmap item ships, update both.** Use the `/sync-status-docs` skill
for this — it checks the item off in `ROADMAP.md` and adds the matching
timeline phase + component-table rows to `status.html` in one pass, matching
the existing phases' tone and structure.

## Dev workflow — this is a ddev project, not bare uvicorn

- `ddev describe` — check whether it's running (it usually already is; don't
  assume you need `python -m venv` / `pip install`).
- Two services split frontend from API — `main.py` is API-only, it does not
  serve any HTML. `web` (ddev's default nginx container, `docroot: static`)
  is the one public entrypoint: it serves `static/*.html` directly and
  reverse-proxies `/api/` + `/auth/` to `fastapi`
  (`.ddev/nginx/kalshi-proxy.conf`). `fastapi` has **no public URL of its
  own** — no `HTTP_EXPOSE`/`HTTPS_EXPOSE`/`VIRTUAL_HOST` — it's reachable
  only inside the project's docker network as `fastapi:8000`. This is a
  deliberate fix for a real, recurring bug (see `ROADMAP.md`): when both
  containers had a public router registered for the same hostname,
  Traefik's tie-break between them wasn't stable across restarts.
- The `fastapi` service runs `uvicorn --reload` — edits to `.py` files take
  effect in ~1-2s automatically. No manual restart needed for normal
  iteration. Editing `.ddev/nginx/*.conf` or any `.ddev/*.yaml` does need a
  `ddev restart` to take effect, unlike `.py` files.
- `ddev logs -s fastapi` / `ddev logs -s web` — tail logs, e.g. to watch
  reload events, errors, or nginx's access/error log.
- `ddev exec -s fastapi <cmd>` — run one-off commands inside the container
  (working dir `/app`, same layout as the repo root). Prefer this over raw
  `docker exec`.
- App: `https://kalshi-whale-poc.ddev.site` (served by `web`).
  `GET /api/state` is the fastest way to check live state (bankroll,
  positions, risk halt status, etc.) without opening the dashboard — same
  hostname, nginx proxies it to `fastapi` transparently.
- To test something that depends on a **real process restart** (not just
  `--reload`'s in-process reimport) — e.g. verifying persistence survives a
  restart — use a full `ddev restart`. A file save alone won't exercise that
  path.
- See the `/run` skill for more detail on driving the live app.

## `data/*.db` files are live — don't delete/move them casually

`paper_broker.db`, `risk_state.db`, `signal_log.db`, `accounts.db` are SQLite
files the running dev server actively reads and writes while `ddev` is up.
Deleting or moving one out from under a live process desyncs its in-memory
state from disk until the next restart, and produces confusing results (a
`_connect()` mid-run will silently recreate an empty file and start writing
into a row that stale in-memory objects don't know is gone). Check
`ddev describe` before touching them. Prefer `POST /api/reset` (wipes the
paper account cleanly, in place, via `PaperBroker.reset()`) over deleting
`paper_broker.db` by hand.

**Accumulated history in these files is a first-class asset, not disposable
state** — direct instruction. `market_history.db`, `signal_log.db`,
`market_catalog.db`, `market_analyst.db`, `config_performance.db` are the
dataset every rule-based heuristic (`confidence_calibration`,
`advisory_engine`, `trade_analytics.compute_insights`), the whale-tracking
filters, and the market analyst agent all depend on — most of them are
explicitly sample-size-gated, so losing history doesn't just lose data, it
silently resets those gates back to zero. This must survive every future
refactor, rewrite, and test run:
- Schema changes are always additive (`CREATE TABLE IF NOT EXISTS` +
  `_add_column_if_missing`-style `ALTER TABLE` — see the idiom below),
  never a drop-and-recreate.
- Tests always redirect `DB_PATH` via `monkeypatch` to an isolated tmp
  path — never touch a real `data/*.db` file (the established convention
  throughout `tests/*.py`).
- Manual/live verification should not call `POST /api/reset` or otherwise
  truncate real data unless a reset is specifically what's being verified —
  prefer read-only checks, or a disposable round-trip (set a value, confirm,
  set it back) for anything that needs to touch live config/state.

## Persistence idiom

Every stateful module owns its own SQLite file under `data/` (gitignored via
`data/*.db`). Pattern used by `services/signal_log.py`,
`services/paper_broker.py`, `services/risk_manager.py`:

```python
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "X.db"

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS ... ")
    return conn
```

Follow this for any new persisted state rather than introducing a different
mechanism (a shared DB, an ORM, etc.) — it's deliberately one small file per
concern, consistent with how the rest of the app is factored.

## Safety invariants — don't regress these

- Paper mode by default (`mode: paper` in `config/settings.yaml`).
- Real order placement (`create_order`/`cancel_order` in
  `services/kalshi_account_client.py`) is fully implemented — schema
  verified against Kalshi's current docs and migrated to the official
  `kalshi_python_async` SDK — and gated by `kalshi_account.trading_enabled`
  (default `false`) plus a typed in-app confirmation phrase
  (`POST /api/trading/enable`). All P0 code-level gates are done; what's
  left before ever flipping it for real is operational, not code — see
  ROADMAP.md's "Path to production" section.
- CORS is restricted to the DDEV hostname + `localhost:8000` (overridable
  via `ALLOWED_ORIGINS` in `.env`), not wide open — don't reopen it as a
  drive-by.
- The daily-loss kill switch (`services/risk_manager.py`) and the paper
  broker's bankroll/positions/trade log both persist across restarts now
  (`data/risk_state.db`, `data/paper_broker.db`). Keep them in sync if you
  touch either file — the risk manager's `day_start_bankroll` baseline must
  stay consistent with the broker's actual persisted bankroll, or the kill
  switch can mismeasure today's loss or silently un-halt after a restart.

## Bug pattern to watch for — a displayed value must match its label, not just look plausible

Found live 2026-08-09 (`ROADMAP.md`/`status.html` phase 54, direct report:
"I get values for bankroll, equity, and unrealized P&L, but the open
positions themselves aren't shown, what a lie"). The Portfolio header's
"Unrealized P&L" was computed as `equity - starting_bankroll` — cumulative
all-time P&L, including every past realized gain — instead of
`equity - bankroll`, the actual unrealized P&L on currently-open positions
per `PaperBroker.equity()`'s own definition (`bankroll +
total_unrealized_pnl(open_positions)`). With zero open positions this showed
a large nonzero figure next to an empty positions list. Both formulas read
as equally plausible from the call site — `starting_bankroll` and `bankroll`
are both real, nearby, correctly-spelled fields — which is exactly why it
shipped unnoticed.

Same root shape as the earlier, independently-found "no-side dollar math"
bug class (`ROADMAP.md`, Active Position Management section): four separate
frontend spots reimplemented `cost = size * price` without the `1 - price`
no-side inversion, each looking locally reasonable in isolation. Before
adding or editing any displayed financial figure (P&L, cost basis, exposure,
payout, ...), trace it back to its backend definition
(`PaperBroker.equity()` / `cost_basis()` / `mark_to_market()`) rather than
deriving it from whichever fields already happen to be in scope at the call
site. When a backend-computed value already exists, prefer exposing it as
its own named field over re-deriving it client-side at all — re-derivation
is exactly where both of these bug classes happened.

## Quick file map

- `main.py` — FastAPI app, API/auth routes only (no HTML), the trading loop.
- `services/` — one module per concern (client, strategy, risk, broker,
  persistence, auth, accounts store, whale-watcher provider library).
- `static/` — dashboard + status page + login/accounts pages, served
  directly by ddev's `web` container, not by `main.py`. Plain inline
  HTML/CSS/JS per page, no build step, no bundler.
- `.ddev/nginx/kalshi-proxy.conf` — `web`'s reverse-proxy rules
  (`/api/`, `/auth/` → `fastapi:8000`) and the extension-less page aliases
  (`/status`, `/login`, `/accounts`).
- `config/settings.yaml` — non-secret, live-reloadable tuning (thresholds,
  position sizing, risk limits). Committed to git (once this becomes a git
  repo), editable live from the dashboard's Controls panel.
- `.env` (gitignored) — secrets and URLs. Every variable is optional; see
  `.env.example` and `README.md` for what each one unlocks.
- `docs/kalshi/` — locally-mirrored, LLM-formatted copy of Kalshi's own API
  docs. `llms.txt` is the maintained index (source URLs), `README.md` is
  per-page provenance/fetch dates. Authoritative over training-data
  assumptions about Kalshi's API — see "Kalshi API documentation" below.
- `.claude/` — Claude Code project config: hooks (`hooks/` — test-on-edit,
  syntax check, `data/*.db` write guard, session orientation, pre-compact
  and checkpoint reminders) and project skills (`skills/` — `run`,
  `sync-status-docs`, `checkpoint`).
- `.github/workflows/tests.yml` — CI: full pytest suite on push to `main`,
  every PR, and on demand (`workflow_dispatch`).

## Kalshi API documentation — treat `docs/kalshi/` as ground truth

`docs/kalshi/` mirrors Kalshi's own API docs locally, fetched from the same
`.md`-suffixed pages `docs.kalshi.com/llms.txt` indexes. Read the relevant
page(s) there before writing or editing any Kalshi API call site,
request/response parsing, or rate-limit logic — don't rely on training-data
assumptions about Kalshi's API, which has already been caught drifting from
what the code assumed. The 2026-08-15 full-audit session (see
`docs/next-steps-2026-08-15-pt3.md`) found a stale legacy base URL, a wrong
live-data endpoint for sports, and three unbatched-call opportunities — all
by reading these docs and verifying live against the real API, not by
guessing from prose or memory.

- `docs/kalshi/llms.txt` — the maintained index: source URLs + one-line
  descriptions. A complete mirror of Kalshi's real remote index as of
  2026-08-16 (215 pages, everything from the standard trading API through
  margin/perps, FIX, RFQ, order-groups, and historical data — see
  `README.md`'s "full-index gap-fill" entry for how the margin/FIX
  endpoint-name collisions against the standard API were resolved). If a
  future page 404s or a new endpoint appears upstream, re-fetch
  `llms.txt` and pull the new/changed page into `docs/kalshi/` — don't let
  it silently drift back into a partial snapshot. This surface doesn't
  change often, so there's no automated re-sync — instead,
  `session_orient.sh` prints an age note (based on `llms.txt`'s last git
  commit, not file mtime — mtime resets on every fresh clone) once it's
  been 90+ days since the last refresh, as a periodic nudge to spot-check
  for drift rather than trust the mirror indefinitely.
- `docs/kalshi/README.md` — per-page provenance (source URL + fetch date).
  Check the date before trusting a page for anything rate-limit- or
  schema-sensitive; re-fetch if it looks stale.
- Individual pages (`get-market.md`, `rate_limits.md`,
  `websocket-connection.md`, ...) — the actual reference detail. Read the
  specific page for the endpoint in question rather than guessing field
  names or limits.

## Long-session workflow — commits, pushes, CI offload, compacting

Direct standing instruction (2026-08-16): during a long working session,
checkpoint proactively rather than batching everything to the end. Use
`TodoWrite` for any multi-step task, and once a unit of work is genuinely
verified, commit it and push to `origin` rather than letting it sit
uncommitted.

**Default to offloading full-suite verification to GitHub rather than
re-running it locally before every commit** (direct instruction,
2026-08-16 — "very worthwhile to offload routine operations to github like
full suite testing"). The per-edit local hook
(`.claude/hooks/run_tests.py`, which fires `pytest` inside `ddev` after
every `main.py`/`services/*.py` edit — keep that as-is, it's the fast
in-the-loop feedback layer) already exercised every real code change as it
happened; a second full local run right before committing is usually just
repeating work `.github/workflows/tests.yml` is about to do anyway, in a
clean environment, on push. Commit → push → `gh run watch --exit-status` →
interpret the result is the default path now. Still run locally first
when there's a concrete reason to want faster/richer feedback than a ~40s
CI round-trip — actively debugging a specific failure, a large/risky
change, or CI/`gh` itself being unavailable — that's a per-occasion
judgment call, not a rule against it. Use `workflow_dispatch` (`gh
workflow run tests.yml`) to trigger CI on demand without waiting for a
push, and `gh run view --log-failed` to pull failing output back into the
session when CI is red.

The `/checkpoint` skill runs this sequence end to end (verify tests green →
review diff scope → commit → push → report CI status → flag whether a
`ROADMAP.md` item just shipped, in which case run `/sync-status-docs`
too). Two hooks back this so it doesn't depend purely on remembering
across a long session — both verified against the primary Claude Code
hooks docs first, since `PreCompact`/`Stop` hooks' stdout is only
debug-logged, never seen by the model, which rules them out for this:
- `SessionStart` with a `compact` matcher
  (`.claude/hooks/post_compact_reorient.sh`) fires immediately after any
  compaction (manual `/compact` or automatic) and re-injects
  uncommitted-change state right after summarization could have blurred
  it.
- `UserPromptSubmit` (`.claude/hooks/midsession_checkpoint_nudge.sh`)
  covers the gap between session start and the first compaction — which
  could be arbitrarily long — by checking uncommitted diff size at most
  once every 10 minutes (time-throttled, not a check on every prompt) and
  nudging toward `/checkpoint` once it's grown past 5 files / 150 lines.

Both only print a reminder; neither commits anything on its own.

Never commit a failing or half-finished state — this instruction covers
*when* to commit during a session, not a license to commit broken code.
Stage specific paths, never a blind `git add -A`/`git add .` (see the
global git-safety rules) — this matters more than usual here given
`data/*.db`, `.env`, and session scratch files all live in this tree.

Also proactively suggest — don't silently assume — a good moment for the
*user* to run `/compact` (session has done substantial work and context is
getting heavy) or `/clear` (the next thing is materially unrelated to what
was just finished). This is a suggestion to surface, not a decision to make
unilaterally.

When delegating to a subagent, match the model to the task (direct
instruction, 2026-08-16): `model: haiku` for mechanical, read-only,
low-judgment work (bulk fetches, log scans, file inventories) — reserve
the default/inherited model for anything needing real reasoning or code
changes. Don't restart an already-in-flight agent just to fix its model
tier; apply this to how agents get launched going forward.

Context-window limits are already auto-compacted by Claude Code itself, no
prompting needed. Session/usage-cap limits are handled the same way, by
design: don't pause mid-task to ask permission to keep going because usage
looks high — keep working. The checkpoint cadence above is what makes an
unannounced cutoff cheap (nothing valuable sitting uncommitted when it
happens), which is the actual mitigation here — there's no hook event for
"usage cap approaching" to wire up, so this is a behavioral commitment, not
a mechanical one.
