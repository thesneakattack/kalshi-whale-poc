# Kalshi Autotrader Quality Control Plane — Design Specification

**Repository:** `thesneakattack/kalshi-whale-poc`  
**Design date:** 2026-08-24  
**Baseline reviewed while writing this spec:** `main` at/around `5eaed2771eb50b03c71b551ecccb147ccddf3810`  
**Status:** Approved design direction from the preceding project review; implementation must re-ground against current HEAD before changing code.

---

## 1. Purpose

This project has repeatedly paid the same investigative cost: a symptom appears, an agent writes or runs temporary diagnostics, live data and logs are inspected, a root cause is measured, the bug is fixed, a regression test is added, and the investigative machinery disappears. The repository history shows that the most valuable of those investigations eventually became durable capabilities (`services/diagnostics/`, `series_watcher.py`, `settlement_edge.py`, `services/backup/`, `services/alerting/`, config performance history, tick-phase timings, trade-stream performance counters, etc.).

The purpose of this design is to make that conversion systematic.

> **Standing rule:** whenever an investigation finds a real bug, performance pathology, integration gap, stale assumption, or data-quality failure, the fix is not considered complete until the team decides whether the measurement that exposed it should become a permanent runtime diagnostic, a CI/CD guardrail, or both.

The result is a **Quality Control Plane**: a set of small, modular runtime services and CI/CD checks that continuously answer whether the application is effective, efficient, and informative without forcing a future agent to reconstruct the same evidence from scratch.

This is not a single giant `quality.py` module. It is a shared set of conventions, result types, audit tooling, runtime metrics, and CI workflows layered onto the existing module-per-concern architecture.

---

## 2. Evidence from the repository history

The design is grounded in repeated patterns already present in commits and `static/status.html`, including:

- Phase 52 / commit `91561fb`: full API/data-usage review discovered fields fetched but not used and implemented missing UI/data paths.
- Phase 57 / commit `12323cc`: a second-pass audit found a real race in on-demand market analysis.
- Phases 66–67 / commits `c1ab30b`, `3e3252c`: persistence audit found a test touching live data, a scan health bug, missing UI error visibility, and reset/logging gaps.
- Commit `e6913ee`: manual investigation was promoted into `services/diagnostics.py` with threshold, price-band, runway, epoch, and coverage checks.
- Commit `c081a9f`: a one-off end-to-end whale-versus-realized-performance investigation became persistent `series_watcher` capture and reconciliation.
- Commit `2273b1e`: settlement-projection research became `settlement_edge`, explicitly measuring before trading on the idea.
- Commit `972dfd`: tick-phase timing instrumentation was added because aggregate tick duration was insufficient to identify a bottleneck.
- Commit `8b227fe`: trade-channel CPU investigation added `trade_stream_perf` counters rather than guessing.
- Commit `979f0fc`: a five-hour live resource leak was found from logs; a follow-up audit found another identical `KalshiClient` leak at a zero-caller route.
- Commits `4193f24`, `801ea3c`, `52e9dba`: data-consumption/performance audits found uncapped REST calls, N+1 SQLite connections, and duplicated O(n) analytics work.
- Commit `8820cbc`: storage sizing exposed a 5.7 GB `game_state.db` growth pathology from repeated full crypto payload persistence.
- Commit `16b68e4`: recurring manual monitoring needs were promoted into `services/alerting/`.
- Commit `46c8498`: durability concerns were promoted into `services/backup/`.
- Commit `0f548b5`: dependency auditing and Kalshi docs URL checking were promoted into GitHub Actions.
- Commit `b5a01b6`: `event_schedule` was fully built but had zero callers until a later audit found and wired it; this is the canonical “implemented but functionally dead” failure class.
- Commits `89334aa`, `83f878b`, `a415fff`: docs/API semantic audits found dangerous assumptions around deprecated fields and `determined` vs. `finalized` lifecycle semantics.
- `static/status.html` itself currently demonstrates documentation drift in its old current-state header even though its later timeline documents a much more advanced system.

These are not isolated accidents. They define recurring bug classes that should become product capabilities or pipeline checks.

---

## 3. Current project constraints that this design must respect

These are non-negotiable unless the project owner explicitly changes them:

1. **Current code wins over this spec.** The implementation agent must re-read `CLAUDE.md`, `ROADMAP.md`, relevant `CHEATSHEET.md` files, recent commits, and current HEAD before each workstream.
2. **DDEV is the development runtime.** Normal Python verification runs through `ddev exec -s fastapi ...`; do not invent a parallel venv-first workflow.
3. **`data/*.db` is a first-class asset.** Tests must never read or write live project DBs. No task may delete, reset, vacuum, move, or mutate live historical stores merely to verify code.
4. **Persistence remains one SQLite file per stateful concern.** Do not introduce an ORM or shared mega-database as part of this work.
5. **Kalshi API work is docs-first.** `docs/kalshi/` and the current official docs are authoritative over memory or assumptions.
6. **Paper mode and real-trading gates remain unchanged.** This project must not enable real trading, relax confirmation gates, or make quality tooling capable of placing orders.
7. **Runtime diagnostics are read-only by default.** A quality finding may inform a human or the existing advisory system, but this project does not auto-remediate trading/configuration behavior.
8. **Preserve existing module boundaries.** Extend `diagnostics`, `alerting`, `task_supervisor`, `backup`, `frontend`, and existing workflows before creating duplicate systems.
9. **Avoid config explosion.** Add only user-meaningful quality-control settings. Internal scanner rules and stable engineering thresholds should live in code or committed audit contracts, not as dozens of dashboard knobs.
10. **Unknown is better than fabricated.** Every new runtime health check follows the diagnostics module’s existing discipline: insufficient or unavailable evidence returns `unknown`/`insufficient`, never a fake OK.
11. **Measure before optimizing.** Runtime efficiency changes must be justified by recorded metrics, not intuition.
12. **Use baseline-ratchet CI for heuristic audits.** Existing debt may be recorded as a baseline; CI prevents new debt while old findings are burned down deliberately.

---

## 4. Primary success criteria

The initiative is successful when all of the following are true:

- A fresh agent can answer “is the system healthy?” from stable API/CLI reports rather than ad hoc shell queries.
- The application retains enough historical performance telemetry to explain why it was slow or unhealthy during an earlier period, not only right now.
- A new unmounted router, built-but-unwired background service, test access to `data/*.db`, stale generated frontend bundle, new high-confidence resource leak pattern, or frontend-to-backend route mismatch is caught automatically in CI.
- The Kalshi docs mirror can detect content drift, not only dead URLs.
- Critical Kalshi parser/request assumptions have deterministic fixtures and contract tests.
- The frontend build and lint are real CI gates, and a browser smoke test runs in CI without requiring the DDEV-only `web` hostname.
- Database size/growth, write freshness, backup age, and integrity are visible from the application.
- Existing ephemeral metrics such as tick-phase timing and trade-stream performance are persisted at a bounded sampling rate and can be queried historically.
- Repeated research sweeps can be run by the application itself when enough new evidence has accumulated.
- `status.html`’s current-state numbers are generated or verified automatically so the historical timeline can remain hand-authored without its header going stale.
- CI jobs clearly distinguish behavior failures, architecture failures, frontend failures, dependency vulnerabilities, docs/API drift, contract drift, and performance regressions.

---

## 5. Runtime vs. CI/CD decision rule

Every candidate check must be classified using this matrix:

| Question | Runtime/In-App | CI/CD | Shared implementation |
|---|---:|---:|---:|
| Depends on live market traffic, DB history, current stream state, or elapsed time | Yes | Fixture/synthetic only | Usually |
| Deterministic from source code/repo structure | Optional report | Yes | Often |
| Can corrupt live data if wrong | Guard at runtime/test boundary | Yes | Yes |
| External API schema/semantics | Live read-only canary when safe | Fixtures + scheduled verification | Yes |
| Frontend rendering/interactions | Optional runtime health | Yes, browser smoke | Mostly no |
| Performance based on live data distribution | Yes | Synthetic regression budget | Yes |
| Dependency/security metadata | No | Yes | No |
| Historical strategy/research analysis | Yes | Unit tests only | Yes |
| Documentation current-state counts | Generated at build/check time | Yes | Yes |

The implementation must resist putting everything in both places. Runtime checks are for facts that require runtime evidence. CI checks are for facts that can be reproduced from code, fixtures, or a safe canary.

---

## 6. High-level architecture

```text
                           ┌──────────────────────┐
                           │ Existing application │
                           │ streams / loop / DBs │
                           └──────────┬───────────┘
                                      │
               ┌──────────────────────┼──────────────────────┐
               │                      │                      │
               ▼                      ▼                      ▼
      services/observability/  services/storage_health/  existing diagnostics
      bounded metric capture   DB growth/integrity       + alerting/fault log
               │                      │                      │
               └──────────────┬───────┴──────────────┬───────┘
                              ▼                      ▼
                     services/quality/         services/research/
                     common findings +        evidence-triggered
                     summary routes           read-only sweeps
                              │
                              ▼
                      /api/quality/*

Repository / CI side:

 source tree ──► tools/quality_audit/* ──► quality-audit.json
                         │
                         ├─ router/wiring checks
                         ├─ config-path checks
                         ├─ DB/test safety checks
                         ├─ API/client resource checks
                         ├─ frontend/API contract checks
                         └─ baseline ratchet

 frontend ──► npm lint/build ──► generated bundle sync check
            └─ browser E2E harness

 docs/kalshi + fixtures ──► docs content drift + Kalshi contract workflow

 all outputs ──► GitHub Actions jobs + uploaded artifacts
```

---

## 7. Shared quality finding contract

Create a lightweight pure-Python package `services/quality/` whose core types have no imports from `main.py`, `app_state.py`, databases, or FastAPI.

### `QualityFinding`

```python
@dataclass(frozen=True)
class QualityFinding:
    finding_id: str
    check: str
    severity: Literal["info", "warning", "error"]
    confidence: Literal["high", "medium", "low"]
    source: Literal["runtime", "ci"]
    scope: str
    summary: str
    evidence: dict[str, Any]
    remediation: str | None = None
```

`finding_id` must be deterministic for the same underlying issue. CI baseline comparison depends on stable IDs.

### Severity semantics

- `error`: known correctness/safety/integration violation. A new high-confidence CI `error` fails the job.
- `warning`: suspicious, inefficient, stale, or incomplete behavior requiring review but not necessarily a correctness failure.
- `info`: inventory, resolved debt, measurement, or intentionally unsupported behavior.

### Confidence semantics

- `high`: deterministic evidence; safe for CI gating.
- `medium`: strong heuristic; warn and record, do not fail by default.
- `low`: exploratory clue; visible in reports only.

This distinction prevents heuristic static analysis from becoming noisy, brittle CI.

---

## 8. Runtime observability service

Create `services/observability/` rather than expanding the already broad diagnostics module.

### Responsibility

Persist bounded, low-frequency snapshots of metrics the app already computes so they can answer historical questions.

Initial metric sources should reuse existing state rather than instrument every function immediately:

- `state["last_tick_duration_sec"]`
- `state["tick_phase_timings"]`
- `state["last_tick_rate_limit_hits"]`
- `state["trade_stream_perf"]` when present
- `trade_stream.messages_received`
- `trade_stream.dropped_messages`
- trade and index stream connection status
- background task running/restart/fault state where already available
- selected pipeline last-write ages from the same stores currently exposed by `/api/health/pipeline`
- process-visible DB file sizes
- backup age/status

### Persistence

Use `data/observability.db` with an additive schema:

```sql
CREATE TABLE IF NOT EXISTS metric_samples (
    observed_at REAL NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    labels_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_time
    ON metric_samples(metric, observed_at);
```

Sampling default: 60 seconds. Retention default: 336 hours (14 days). Those are the only initial user-facing knobs.

Do not persist every WS message. Existing per-message counters are aggregated into periodic samples.

### Routes

- `GET /api/observability/current`
- `GET /api/observability/history?metric=...&hours=...`
- `GET /api/observability/summary?hours=...`

### Initial anomaly rules

Rules are read-only findings, not remediation:

- tick duration greater than configured poll interval
- non-zero dropped WS messages
- repeated recent rate-limit hits
- stale critical persisted store relative to expected activity
- backup older than its configured interval plus a tolerance
- stream disconnected while application is otherwise running

Rules that depend on market activity must return `unknown` instead of warning when there is insufficient evidence that writes/messages should have occurred.

---

## 9. Storage health service

Create `services/storage_health/`.

### Responsibilities

For each `data/*.db` owned by the application:

- file size
- size change over time using observability history
- SQLite page count/page size/freelist count
- table inventory
- row counts where inexpensive enough for on-demand/scheduled scans
- oldest/newest timestamp for known time-series tables where a module exposes that information
- backup recency
- bounded `PRAGMA quick_check` / explicit manual integrity check

### Routes

- `GET /api/health/storage`
- `POST /api/health/storage/scan` — manual background scan, read-only
- `POST /api/health/storage/integrity-check` — explicit read-only check, never automatic every tick

The service must never vacuum, prune, delete, migrate, or repair a DB automatically.

The `game_state.db` 5.7 GB incident is the reference failure this service should make obvious before a human notices disk usage.

---

## 10. Static repository audit framework

Create `tools/quality_audit/` as a standard-library-heavy package with a CLI. It must not import `main.py` or construct app singletons.

### CLI

```bash
python -m tools.quality_audit \
  --repo-root . \
  --baseline tools/quality_audit/baseline.json \
  --json-out build/quality-audit.json
```

### Required scanner classes

1. **Router registration scanner**
   - discover `services/**/routes.py` modules defining `router = APIRouter()`
   - parse `main.py` imports and `app.include_router(...)`
   - high-confidence error when a router exists but is not mounted

2. **Background wiring scanner**
   - discover `_maybe_*` functions that schedule/hold background tasks (`task_supervisor.supervise`, task state, or `asyncio.create_task` patterns)
   - find external call sites
   - high-confidence error when a scheduler-style `_maybe_*` function has zero non-test callers
   - this is designed specifically to catch the `event_schedule` “fully built but zero callers” class

3. **Resource ownership scanner**
   - find local `KalshiClient(...)` constructions
   - high-confidence warning/error if the same function contains no `.close()` call for the variable at all
   - do not attempt full control-flow proof in v1; baseline heuristic findings rather than pretending certainty

4. **Persistence/test-safety scanner**
   - inventory `DB_PATH` definitions
   - flag new persistence modules that are not represented in the centralized test isolation map
   - flag tests containing obvious literal connections to repo `data/`

5. **Config usage scanner**
   - parse `config/settings.yaml` leaf paths
   - extract common literal `cfg[...]` / `.get(...)` path chains from Python
   - report likely defined-but-unread config paths as warnings
   - do not fail CI on heuristic config findings in v1

6. **Service/dead-code inventory scanner**
   - inventory public functions and modules with zero obvious callers
   - treat `routes.py`, `__init__.py`, CLI entry points, and functions marked with `# quality-audit: standalone` as intentional
   - warnings only

7. **Kalshi API usage inventory scanner**
   - list `KalshiClient` method calls and their source files
   - list direct `sqlite3.connect` call sites
   - produce inventory for REST-vs-WS reviews
   - no failure solely because a call exists

### Baseline ratchet

Commit `tools/quality_audit/baseline.json`. The first run records existing medium/low-confidence debt. CI behavior:

- new high-confidence `error` → fail
- new `warning` → GitHub annotation + artifact, do not fail initially
- existing baseline finding → report as existing debt
- resolved baseline finding → report as resolved and require deliberate baseline update in a cleanup commit

This avoids turning the first audit into a mandate to refactor unrelated old code.

---

## 11. Test-data isolation hardening

The existing `tests/conftest.py` is already a critical safety mechanism, but commit history shows the list has repeatedly had to grow after newly persisted modules were added.

Create reusable `tests/support/runtime_isolation.py` and make `conftest.py` delegate to it.

The helper must:

- redirect every known stateful module `DB_PATH` to one temporary root before `main` can be imported
- redirect the global `ConfigStore` path to a copied temp `settings.yaml`
- install a global wrapper around `sqlite3.connect` that raises immediately if any test attempts to open a path under the repository’s real `data/` directory
- expose the registered persistence-module list so the static quality audit can compare new `DB_PATH` owners against it

This turns “remember to add a monkeypatch” into a hard invariant.

A new test must attempt `sqlite3.connect(repo_root / "data" / "forbidden-test.db")`, assert a failure, and assert no file was created.

---

## 12. Frontend build, API contract, and browser CI

The frontend is now a real esbuild project, so CI must treat it as one.

### Frontend build job

Every push/PR:

```bash
cd frontend
npm ci
npm run lint
npm run build
cd ..
git diff --exit-code -- static/js/dashboard.bundle.js
```

This catches source/bundle drift.

### Frontend/API contract audit

Add a static scanner that:

- extracts backend route method/path pairs from Python route decorators
- extracts literal/deterministically normalizable `/api/...` and `/auth/...` paths from frontend JS
- normalizes template literals such as `/api/markets/${ticker}` to FastAPI-style dynamic path patterns
- reports frontend calls with no matching backend route as high-confidence errors when the path is statically resolvable
- reports backend routes with no frontend caller as informational only because many are diagnostic/API-only by design

### Browser E2E harness

The current `test_e2e_terminal_static_and_api.py` deliberately skips static serving on bare CI because `web` exists only inside DDEV. Add a CI-only ASGI harness:

1. install temp DB/config isolation before importing `main`
2. import the production `main.app`
3. mount `static/` at `/` **after** existing routes so `/api/*` remains handled by the real app
4. run it on localhost under uvicorn
5. drive Chrome headless with the already-pinned Selenium dependency

Browser smoke must cover at minimum:

- dashboard loads
- no fatal console errors on initial load
- `/api/state` populates the page
- Portfolio, Markets, Whale Watch, Terminal, History, Config tabs can be activated by their existing `tab-btn-*` IDs
- an intentionally failing API action is surfaced as an error rather than falsely shown as success (regression class from the old `fetchJSON` bug)
- built JS bundle is actually loaded

Do not require real Kalshi credentials or real network access for PR browser tests.

---

## 13. Kalshi documentation drift and contract tests

The current weekly workflow only checks whether source documentation URLs still return 200. Upgrade it in two independent layers.

### Documentation content drift

Commit `docs/kalshi/upstream-manifest.json` with, for each mirrored source:

```json
{
  "source_url": "...",
  "local_path": "docs/kalshi/...",
  "sha256": "..."
}
```

A scheduled workflow downloads each upstream source, normalizes line endings only, hashes it, and compares it with the committed manifest/local mirror.

A changed page must:

- produce a concise changed-files report
- upload the diff/hash report as a workflow artifact
- fail the scheduled check so drift is visible
- **not** auto-commit documentation updates

URL availability remains part of the same check.

### Contract fixtures

Create canonical fixtures under `tests/fixtures/kalshi/` for message/request shapes the application depends on most heavily:

- public trade
- ticker
- lifecycle
- market position WS message
- fill WS message
- market REST object
- event REST object
- create-order request
- cancel-order behavior

Fixtures must be based on `docs/kalshi/`, not memory.

Tests validate current normalization/handler behavior against those fixtures. This is intended to prevent repeats of `taker_side`, `market_position(s)`, `fill_id`/`trade_id`, and lifecycle-status semantic mistakes.

### Scheduled live canary

A separate scheduled/manual job may call **only** endpoints documented as public/read-only without secrets. It should validate basic response shape, never place/cancel orders, never enable trading, and never require production credentials. Authenticated canaries are out of scope unless the owner later explicitly provides CI secrets and approves them.

---

## 14. Runtime Kalshi REST/WS usage visibility

Do not attempt an invasive purpose-argument rewrite across every client call initially.

First add low-overhead counters at the centralized HTTP/client layer:

- request method/endpoint family
- success/failure
- latency
- rate-limit hit
- caller category where it can be supplied cheaply without stack inspection

Expose aggregate counts through observability, e.g.:

```json
{
  "kalshi_rest": {
    "get_markets": {"calls_5m": 12, "avg_ms": 83.2},
    "get_positions": {"calls_5m": 15, "avg_ms": 102.1}
  }
}
```

Do not inspect Python stacks per request in the hot path.

The static audit’s call-site inventory plus runtime endpoint counters together provide the evidence for future REST-vs-WS redesigns. CI should not blindly ban REST calls.

---

## 15. Automated research sweep service

Create `services/research/` only after safety/observability work is stable.

This service does **not** invent new algorithms. It orchestrates existing read-only analytics and stores a coherent snapshot so a human or agent does not have to rerun them manually.

Initial report inputs should include what is already available and sufficiently stable:

- `diagnostics.run_offline(...)`
- trade analytics summary/insights
- confidence calibration report
- advisory recommendations/status without applying them
- candidate population gate summary
- series-evaluator status/quality summaries where available
- settlement-edge report
- strategy/control performance summaries
- config epoch information

### Trigger policy

Prefer evidence growth to a fixed daily timer:

- at least 100 newly resolved signals since last report, or
- at least 50 newly closed trades since last report, or
- manual `POST /api/research/run`

Numbers are defaults and may be adjusted only if existing sample-gating conventions in the current code make a stronger choice obvious at implementation time.

### Persistence

`data/research_reports.db` with report timestamp, source counts/watermarks, and JSON report. The service must never apply config changes.

### Routes

- `GET /api/research/status`
- `GET /api/research/latest`
- `GET /api/research/history?limit=N`
- `POST /api/research/run`

---

## 16. Project manifest and `status.html` drift prevention

Do not generate the historical timeline; it remains deliberately hand-authored.

Generate only the facts that go stale mechanically:

- git HEAD when available
- Python/JS/HTML file counts and lines
- test count/collection count
- FastAPI route count
- service package count
- frontend module count
- workflow/job inventory

Create `tools/project_manifest.py` and committed `static/project-manifest.json`.

`static/status.html` should load those values into its current-state header rather than hardcoding old counts.

CI runs:

```bash
python -m tools.project_manifest --check static/project-manifest.json
```

and fails when the committed manifest is stale.

Capability claims that are semantic rather than mechanical (for example, “real order placement exists but is gated”) remain human-authored, but the immediate stale claim that no order-execution path exists must be corrected during this initiative.

---

## 17. CI/CD target topology

Keep jobs separate so failure meaning is obvious.

### Existing `tests.yml`

Retain:

- `pytest`
- `dependency-audit`

Test-data isolation becomes part of every pytest run automatically through `conftest.py`.

### New `quality.yml`

Jobs:

- `architecture-audit`
- `frontend-build`
- `frontend-api-contract`
- `browser-e2e`
- `project-manifest`

All run on PRs and pushes to `main`. Upload `quality-audit.json` even on failure.

### Kalshi drift/contract workflow

Rename or extend the existing docs workflow; keep it separate from code tests.

Jobs:

- `docs-content-drift` — scheduled + manual
- `kalshi-contract-fixtures` — PR/push when relevant files change, plus manual
- `public-api-canary` — scheduled + manual

### Performance regression workflow

Start as scheduled/manual until baselines are stable. Use generated datasets and broad thresholds, not microsecond assertions. Once stable, promote high-signal benchmarks to PR gates.

---

## 18. Performance regression philosophy

Do not build a generic benchmarking framework first. Target code paths that have already produced real regressions:

- trade analytics over large history
- advisory recommendation generation
- series evaluator batch processing
- candidate population summaries
- market-history calculations
- config resolution/override application

Use deterministic generated datasets (10k/50k/100k rows depending on the function). Record wall time and, where useful, query/connect counts.

Initial CI should fail only for major regressions (for example >2x an established baseline and above a minimum absolute duration) to avoid noise from shared runners.

---

## 19. Quality summary API

Create `services/quality/routes.py` after observability and storage-health services exist.

`GET /api/quality/summary` composes, without mutating anything:

- existing diagnostics offline status
- active alerts
- recent faults
- current observability anomalies
- storage-health summary
- latest research report metadata if available

It should not call live Kalshi APIs. Coverage and live canaries remain separate opt-in/on-demand routes.

This endpoint becomes the first read for a future coding agent beginning an investigation.

---

## 20. UI scope

Do not build a new dashboard application for quality tooling.

After APIs are stable, add one read-only **System Health** section in an existing operationally appropriate view (Terminal or a dedicated diagnostics subsection if current frontend organization already has one). It should show:

- overall status: healthy / warning / error / unknown
- tick duration vs poll interval
- dropped stream messages
- active alerts/fault count
- critical store freshness
- database/backup health
- link/button to fetch deeper details

Keep raw research and static CI reports out of the normal trading UI unless they are already represented there.

---

## 21. Non-goals

This initiative explicitly does **not**:

- enable real trading
- change strategy thresholds or alpha logic
- auto-fix config based on quality findings
- migrate SQLite to PostgreSQL/Redis/etc.
- introduce Prometheus/Grafana/Sentry/Datadog or another external observability platform
- rewrite every module to use a new dependency-injection framework
- force every Kalshi field to be consumed
- remove deliberately forward-captured data merely because it is not read today
- replace `fault_log`, `alerting`, `diagnostics`, `backup`, or `task_supervisor`
- make scheduled CI depend on private Kalshi account credentials
- turn heuristic dead-code/static-analysis warnings into immediate hard failures
- regenerate the historical prose in `status.html`

---

## 22. Rollout order

The rollout order is deliberate:

1. test-data isolation hardening
2. shared finding schema + static audit framework
3. high-confidence wiring/registration checks
4. frontend build/API/browser CI
5. runtime observability persistence
6. storage health
7. quality summary + alert integration
8. Kalshi docs content drift + contract fixtures/canary
9. REST/WS usage telemetry
10. research sweep orchestration
11. status/project manifest automation
12. performance regression promotion
13. baseline debt burn-down

Safety and reproducibility come before new analytics.

---

## 23. Definition of done

The overall initiative is done only when:

- every workstream has targeted tests and the full existing suite remains green
- frontend lint/build is green and the committed bundle matches source
- browser E2E is green in GitHub Actions
- the static audit runs in CI with a committed baseline and fails on a deliberately injected unmounted-router fixture
- a deliberately injected test connection to repo `data/` fails before SQLite opens the file
- observability data survives a real DDEV restart and obeys retention
- storage health correctly reports current DB sizes and can detect a synthetic fast-growth fixture in tests
- docs drift can detect a changed fixture hash
- Kalshi contract tests cover the message/request shapes listed above
- project manifest check catches a deliberately stale fixture
- `/api/quality/summary` returns a coherent read-only report with no live external call
- no task mutates real historical DBs during verification
- `CLAUDE.md`, relevant `CHEATSHEET.md` files, `ROADMAP.md`, and `static/status.html` are synchronized to the shipped architecture
- the implementation is split into reviewable commits, one independently testable deliverable per task
