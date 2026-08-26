# autotrade / kalshi-whale-poc

Kalshi whale-signal trading terminal: real Kalshi market data + a
simulated-or-live whale order-flow signal + a broker layer, currently
running in paper mode. Safety-first, incremental: no real order ever gets
placed unless `kalshi_account.trading_enabled` is explicitly flipped in
`config/settings.yaml` plus a typed in-app confirmation phrase. All P0
code-level safety **primitives** (the trading gate itself, the kill
switch, CORS) are shipped — but that is not the same claim as "real
capital is ready to depend on this." Realtime data-plane correctness,
economic/strategy validation, and canonical decision/execution semantics
are still open, substantive, code-level work, not merely operational
follow-up — see `docs/kalshi-personal-production-execution-program-
2026-08-26.md` for the current program-level sequencing, and ROADMAP.md's
"Path to production" section for the itemized checklist.

## Standing goal — personal-use, real-money production (2026-08-26)

Direct standing instruction (2026-08-26): this is no longer a proof of
concept kept in paper mode indefinitely — the purpose is a personal-use,
real-money trading system. Equally direct, same instruction: **"we still
need to progress safely until we reach the goal"** — this sets the
destination, it does not authorize shortcutting any safety gate, and it
does not by itself flip `kalshi_account.trading_enabled` or any config
default. Getting there runs through ROADMAP.md's "Path to production"
checklist — each open item there is a precondition, not a suggestion, and
several (real position-size/kill-switch numbers, the sports-category legal
exposure, the auth model, deployment target) are decisions only a human
makes, not something a commit can complete on its own.

This goal sits above, not in place of, the per-module quality objective
immediately below: a module has to actually be trustworthy before real
capital can depend on it, so that audit work is how this goal gets
reached, not a separate track from it.

Known specific gaps still open toward this goal (stubs — full detail in
ROADMAP.md's "Path to production" section and the relevant module
`CHEATSHEET.md`, not restated here):
- Entry-gate adverse selection — KXBTC15M whale signals resolved 88.8%
  correct across 394 settled signals, but the 12 the gates actually traded
  resolved only 58.3%. Not yet root-caused.
- `services/shadow_mode.py` has never produced a trade to review, not just
  "unreviewed" — `mode` has been switched to `shadow` only twice ever, both
  reverted within 24h, zero rows logged either time. No sustained real
  evaluation stretch has happened yet, and none is in progress today
  (`mode: paper`). That stretch, then a review of it, is the actual gate
  before ever flipping `trading_enabled` — see ROADMAP.md for the verified
  detail.
- No real deployment target yet (local `ddev` on one machine only); no
  human-set real position-size/kill-switch numbers (`risk.max_daily_loss_pct`
  is currently `0.85` — today's kill switch only halts after 85% of the
  day's bankroll is gone, i.e. not meaningfully protective as configured);
  and the single-operator auth model hasn't been explicitly confirmed as
  sufficient for real money.
- Sports-category contracts carry unresolved multi-state legal exposure
  (`docs/prediction-markets-research-reference.md` Part 3); this app has
  zero category-level legal-risk awareness today.
- `advisory`/`confidence_calibration` auto-apply has only ever tuned
  against paper-mode trade history.

## Current objective — per-module quality, not a P&L target

Direct standing instruction (2026-08-23), superseding the prior
"HARD COMMANDMENT — 70% whale accuracy AND 70% own win rate" objective
in full: **"forget the 70% thing. now that things are modular the goal is
to make sure each module works at peak effectiveness, efficiency, and
informativeness."** Do not chase, cite, or judge changes against the old
70%/70% target going forward — retired, not paused. (Its measured
data — the 593-signal unit-cost/breakeven table, the KXBTC15M
selection-vs-exit gap — is still real and still sitting in git history
`git log -S 'HARD COMMANDMENT' CLAUDE.md` / `docs/next-session-pickup-
2026-08-17.md` if a future session ever needs it again, but it is no
longer what work gets judged against.)

New objective: with the codebase now split into cohesive
`services/<name>/` packages (modularization phases 115-119, see
`static/status.html`), audit and improve each module against three axes:

- **Effectiveness** — does it actually do what it claims? Latent bugs,
  dead code paths, gaps between documented and real behavior.
- **Efficiency** — wasteful computation, redundant DB round-trips,
  blocking calls on a hot path, N+1 patterns, unnecessary API calls.
- **Informativeness** — does it surface enough of its own behavior
  (logging, diagnostics, historical capture, UI display) for a human to
  trust and reason about it, not just run it blind?

Each `services/<name>/` package's own `CHEATSHEET.md` (where one exists —
several were written "to an audit-oriented standard... how the module
currently behaves, what the API docs say it should do, and any gap already
visible while writing it," per `static/status.html` phase 119) is real,
pre-existing raw material for this — read it before assuming a module
needs a fresh audit from scratch.

## Start investigations here

Before writing an ad hoc script to check "is everything okay" or chase an
unexplained symptom, check these — in order — first. Together they're the
accumulated output of the Quality Control Plane initiative
(`docs/superpowers/plans/2026-08-24-quality-control-plane.md`, Tasks 1-20):
purpose-built so a session doesn't have to reconstruct "what does this app
already know about itself" from scratch every time.

1. `GET /api/quality/summary` — the single composite health read: overall
   status, active findings, alerts, faults, storage summary, latest
   research-run status. Start here for "is something wrong."
2. `GET /api/health/pipeline` — background-task/scheduler state (which
   `_maybe_*` schedulers are running, when each last fired).
3. `GET /api/health/faults` — accumulated exception history by
   component/operation (`services/fault_log.py`).
4. `GET /api/observability/summary` — historical metric trends
   (`data/observability.db`) for a specific metric name over a window,
   once `/api/quality/summary` has pointed at one worth digging into.
5. `GET /api/health/storage` — per-database file inventory (size, table
   row counts, last-modified) and, on demand,
   `POST /api/health/storage/scan` for a real integrity check.
6. `python -m tools.quality_audit` — static findings (router/persistence/
   config-usage/API-contract wiring gaps), baseline-ratcheted against
   `tools/quality_audit/baseline.json`.

An ad hoc script or a fresh `grep`/`sqlite3` session should be the
exception once these have been checked, not the default first move — they
already answer "is X wired up," "has X been failing," "is X growing
unexpectedly" for most of what this app does.

**Investigation-to-guard rule:** when a debugging/audit pass finds a real
bug class, decide before closing the work whether the measurement that
exposed it belongs in runtime diagnostics, CI, or an existing guard, and
record that disposition in the commit message or the relevant status
entry. The full decision tree (permanent runtime diagnostic / permanent
CI guard / shared logic / already covered / genuinely one-off) lives in
`.claude/rules/quality-capabilities.md`'s "Standing investigation-to-guard
rule" section — this is a pointer to that rule, not a second copy of it.

**Baseline-ratchet semantics (`tools/quality_audit/baseline.json`):**
`accepted_finding_ids` is a reviewed record of findings already judged to
be either a known, accepted scanner limitation (documented in that file's
own `notes` block — e.g. the config-usage scanner's inability to follow
reads through an intermediate variable) or a genuinely intentional state
(a route with no frontend caller yet, by design). **Adding an ID here is
a reviewed decision, not a mechanical "make the check green" move** — do
not silently add a new real error to the baseline just to clear a red CI
run. On a new finding: investigate whether it's real → fix it if so (or
explicitly accept it with a dated, reasoned note appended to `notes`,
matching the running "+N 2026-MM-DD: ..." addendum style already used
there) → only then add the finding_id to `accepted_finding_ids`. When a
baselined finding later resolves for real (e.g. a previously-unused route
gains a real caller), remove its ID and say why in the same commit rather
than leaving it as stale cruft — see QCP Task 18's removal of
`backend-route-unused:GET:/api/quality/summary` once
`frontend/src/js/system-health.js` became its first real consumer.

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
  record. A chronological timeline of build phases, plus reference tables
  (components, API routes, config, known limitations). The prose is
  hand-written describing what was built and why, and can and does go stale
  if a change doesn't update it — but **the file itself is generated**
  (2026-08-26 modularization, done for exactly this session's own
  efficiency: it had grown to 6358 lines/156 phases in one file). Never edit
  `static/status.html` directly — edit the small source fragments under
  **`docs/status-src/`** (one file per reference section, plus
  `timeline/`, chunked at a fixed 25 phases per file so no single file
  grows without bound as the timeline keeps extending) and regenerate with
  `python -m tools.build_status_page --write static/status.html`. A CI step
  (`quality-architecture-audit.yml`) fails the build if the two drift. See
  `tools/build_status_page.py`'s own docstring for the exact fragment
  layout and the `/sync-status-docs` skill for the edit workflow.
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
- App: `https://kalshi-whale-poc.ddev.site` (served by `web`; check
  `ddev describe`/the last `ddev start`/`restart` output for the actual
  port — it's not always the implicit HTTPS 443).
  `GET /api/state` is the fastest way to check live state (bankroll,
  positions, risk halt status, etc.) without opening the dashboard — same
  hostname, nginx proxies it to `fastapi` transparently.
- A separate Cloudflare Tunnel (outside this repo) exposes this app
  publicly at `autotrade.webfoundry.dev`, via a shared `traefik` container
  also fronting other projects. `.ddev/nginx/kalshi-proxy.conf` gates only
  that hostname behind HTTP Basic Auth (`$host`-conditional) — local access
  via `kalshi-whale-poc.ddev.site` (loopback-only per `ddev-router`'s own
  port bindings) is deliberately unaffected, so this never blocks local
  dev/verification. `.env`'s `SITE_BASIC_AUTH_USER`/`SITE_BASIC_AUTH_PASSWORD`
  are the durable source of truth for the password — a `ddev` post-start
  hook (`.ddev/config.yaml`) regenerates the gitignored
  `.ddev/nginx/.htpasswd` (nginx's actual `auth_basic_user_file`) from them
  on every `ddev start`/`restart`. To change the password: edit `.env`,
  `ddev restart`.
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
  (`POST /api/trading/enable`). The P0 code-level **gate/kill-switch
  primitives** are done; that does not mean the rest of the path to real
  capital is operational-only — realtime, economic/strategy, and
  execution-semantics work still stands between here and flipping this
  for real. See ROADMAP.md's "Path to production" section for the
  itemized checklist and `docs/kalshi-personal-production-execution-
  program-2026-08-26.md` for how that work is sequenced.
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
  `sync-status-docs`, `checkpoint`, `config-field-edit`).
- `.woodpecker/*.yml` — the authoritative CI pipelines (one file per named
  check), run by a shared Woodpecker instance defined outside this repo at
  `portfolio/ci-cd/`. See `docs/woodpecker-ci.md` for the full operational
  reference and `.claude/skills/ci-cd-guardrails/SKILL.md` for the local-
  vs-CI verification policy.
- `.github/workflows/` — `workflow_dispatch`-only manual fallbacks for the
  same checks (`tests.yml`, `quality.yml`), plus the still-automatic
  scheduled `docs-drift-check.yml`.

## Kalshi API documentation — treat `docs/kalshi/` as ground truth

**HARD RULE, not a guideline — check this proactively at the start of any
backend/API work in this repo, not only reactively when something breaks
or is missing.** `docs/kalshi/` mirrors Kalshi's own API docs locally,
fetched from the same `.md`-suffixed pages `docs.kalshi.com/llms.txt`
indexes. Before writing or editing ANY code that touches Kalshi
data — a call site, request/response parsing, rate-limit logic, AND
(broader than that) any code that classifies, derives, infers, or
groups data sourced from a Kalshi market/event/trade object —
`grep -rn` across `docs/kalshi/` for the relevant endpoint/object/field
name first. Don't rely on training-data assumptions about Kalshi's API,
which has already been caught drifting from what the code assumed, and
don't assume one live API response you happened to inspect is the whole
picture.

**This directive was already written down here once and still got missed**
(2026-08-16 direct correction, after a session built a "subcategory"
grouping by guessing that `category_tags` on a live event object was
per-event sport data — wrong, it's the same full facet-filter vocabulary
listed on every event in a category, carrying zero per-event signal; the
real answer, a documented `competition` field plus a `filters_by_sports`
sport→competition hierarchy, was sitting in `docs/kalshi/` the whole time).
The gap wasn't that this rule didn't exist — it was scoped too narrowly
("editing a call site") to register for *new feature* work that derives or
classifies Kalshi data without literally touching an existing call site.
Read it broadly: if the data in question originated from Kalshi, check
`docs/kalshi/` before writing code that infers anything about it, full
stop — this is a session-start checklist item for backend/API work, not
something to reach for only once you're already stuck. The 2026-08-15
full-audit session (see `docs/next-steps-2026-08-15-pt3.md`) found a stale
legacy base URL, a wrong live-data endpoint for sports, and three
unbatched-call opportunities — all by reading these docs and verifying
live against the real API, not by guessing from prose or memory.

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
- **`docs/kalshi/CHEATSHEET.md`** — living, append-only index of specific
  data questions already resolved by reading the 215-page mirror, kept so
  they don't get re-derived (or re-guessed) from scratch every session
  (2026-08-16 direct request, after the rule above still got missed once:
  "the api docs are expansive so maybe create a kind of shortcut sheet
  thst you update for things you know to look into across sessions").
  `session_orient.sh` prints every entry's title unconditionally at the
  start of every session and after every compaction (its `SessionStart`
  matcher is `*`, confirmed to fire on both) — check those titles before
  grepping the full mirror cold. **Add a new entry any time reading a
  `docs/kalshi/` page resolves a real data question**, especially one that
  took a wrong guess to get to — same discipline this file's own "Bug
  pattern to watch for" section already applies to code bugs, just for
  API-documentation lookups instead.

## Branching and CI — standing policy

Direct standing instruction (2026-08-25): `main` is the authoritative
integrated branch, protected both by policy and, since the same day, by
real GitHub branch protection (`enforce_admins` on, force-push/deletion
off, the five `ci/woodpecker/pr/*` status checks required — see
`.claude/rules/branching-and-ci.md`'s "Integration lifecycle" for the
exact configuration and how to change it). Normal implementation work
happens on a short-lived initiative branch
(`feat/`, `fix/`, `refactor/`, `chore/`, `docs/<name>`), not directly on
`main` — no permanent `development`/`staging`-style branches. Claude owns
targeted local verification; Woodpecker owns exhaustive verification, on
every branch push, not just `main`. Full lifecycle: `main` → initiative
branch → implementation → targeted local checks → commit → push →
Woodpecker → PR → merge → delete branch.

`.claude/rules/branching-and-ci.md` is the single authoritative detailed
rule for this — read it before starting implementation work, not just
this summary. `.claude/hooks/session_orient.sh` prints the active branch
every session start specifically so this doesn't depend on remembering
across a session; a report of `main` there is the cue to branch before
implementing, not a reason to proceed on it.

## Long-session workflow — commits, pushes, CI offload, compacting

Direct standing instruction (2026-08-16): during a long working session,
checkpoint proactively rather than batching everything to the end (on the
current initiative branch, per the branching policy above — this section
covers *when* to checkpoint within a session, not which branch it lands
on). Use `TodoWrite` for any multi-step task, and once a unit of work is
genuinely verified, commit it and push to `origin` rather than letting it
sit uncommitted.

**Default to offloading full-suite verification to Woodpecker CI rather
than re-running it locally before every commit** (direct instruction,
2026-08-16, updated 2026-08-24 when Woodpecker replaced GitHub Actions as
the authoritative executor — see `.claude/skills/ci-cd-guardrails/SKILL.md`'s
"Local vs CI verification policy" section for the full procedure; this is
a pointer, not a duplicate). The per-edit local hook
(`.claude/hooks/run_tests.py`, which fires `pytest` inside `ddev` after
every `main.py`/`services/*.py` edit — keep that as-is, it's the fast
in-the-loop feedback layer) already exercised every real code change as it
happened; a second full local run right before committing is usually just
repeating work Woodpecker (`.woodpecker/*.yml`, a shared instance defined
outside this repo at `portfolio/ci-cd/`) is about to do anyway, in a clean
environment, on push. Commit → push → check Woodpecker's result → interpret
it is the default path now. Still run locally first when there's a
concrete reason to want faster/richer feedback — actively debugging a
specific failure, a large/risky change, CI itself being unavailable, or a
change to CI configuration itself — that's a per-occasion judgment call,
not a rule against it. `.github/workflows/tests.yml`/`quality.yml` remain
as `workflow_dispatch`-only manual fallbacks (`gh workflow run tests.yml`,
`gh run view --log-failed`) for when Woodpecker is down or a GitHub-native
run is specifically wanted.

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
unilaterally. Concrete trigger, not just a vibe check (direct data,
2026-08-16 usage review — see below): right after `/sync-status-docs` or
any other full read of `ROADMAP.md` (still 1200+ lines; `static/status.html`
itself was the other large file this trigger originally named, until the
2026-08-26 modularization split it into small `docs/status-src/` fragments
— see that section above), and generally once a session has been open
8+ hours or is running noticeably slower to respond — both measured as the
real drivers of this project's heaviest usage sessions, not hypothetical.

**Session-efficiency review (2026-08-16, direct instruction: "use this to
inform improvements to session efficiency without losing effectiveness").**
`/usage`'s own attribution data showed 99% of usage from subagent-heavy
sessions, 94% from sessions open 8+ hours, 93% spent above 150k context,
and `/sync-status-docs` alone at 21% of a week's total. Root-caused the
last one directly, not guessed: that skill's own steps implied a full read
of both target files every invocation, and `static/status.html` alone is
4400+ lines with a single Component-Reference table cell running several
thousand tokens by itself — confirmed via `grep -c`/`wc -l`, not assumed.
Fixed at the skill itself (`.claude/skills/sync-status-docs/SKILL.md`) —
every step now scopes to `grep -n` + a targeted `Read(offset, limit)`
around just the relevant bullet/phase-block/table-row, never a whole-file
read; same output quality (still grounded in a real template block and the
real target text), a fraction of the context. Apply the same "grep first,
read a window, never the whole file" default to any other large file in
this repo (this one included, at 300+ lines) before reaching for a full
read — same lever, same payoff, whenever it applies.

When delegating to a subagent, match the model to the task (direct
instruction, 2026-08-16): `model: haiku` for mechanical, read-only,
low-judgment work (bulk fetches, log scans, file inventories) — reserve
the default/inherited model for anything needing real reasoning or code
changes. Don't restart an already-in-flight agent just to fix its model
tier; apply this to how agents get launched going forward. For a heavy,
genuinely self-contained agent task (a large multi-file investigation, a
full-suite verification run) that doesn't need to share this session's
accumulated context, prefer spawning it with `isolation: "worktree"` —
it starts cold (no inherited context bloat) and reports back a compact
result instead of its full working transcript landing in this session,
which is the other half of why subagent-heavy sessions run expensive:
not just model tier, but how much of the subagent's own context this
session ends up holding onto afterward.

Context-window limits are already auto-compacted by Claude Code itself, no
prompting needed. Session/usage-cap limits are handled the same way, by
design: don't pause mid-task to ask permission to keep going because usage
looks high — keep working. The checkpoint cadence above is what makes an
unannounced cutoff cheap (nothing valuable sitting uncommitted when it
happens), which is the actual mitigation here — there's no hook event for
"usage cap approaching" to wire up, so this is a behavioral commitment, not
a mechanical one.
