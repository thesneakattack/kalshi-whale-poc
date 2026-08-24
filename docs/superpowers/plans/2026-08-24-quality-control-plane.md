# Kalshi Autotrader Quality Control Plane Implementation Plan

> **Execution-method override:** The technical tasks, ordering, architecture,
> acceptance criteria, safety constraints, and verification requirements in
> this plan remain authoritative. The original recommendation to use
> `superpowers:subagent-driven-development`, `superpowers:executing-plans`,
> isolated worktrees, per-task implementer/reviewer subagents, or a separate
> progress ledger is superseded by the repository's current Claude workflow.
> `.claude/skills/quality-plan-task/SKILL.md` is the execution orchestrator for
> this initiative. Superpowers skills remain available selectively as supporting
> engineering capabilities—especially TDD, systematic debugging,
> verification-before-completion, brainstorming, and code review—but they do
> not replace the repository-specific task/commit/checkpoint/CI workflow unless
> the user explicitly requests a different execution model. Steps use checkbox
> (`- [ ]`) syntax for task acceptance tracking, not as a separate progress
> ledger.

**Goal:** Convert the repository’s recurring manual audits, diagnostics, live investigations, and verification rituals into durable runtime services and CI/CD guardrails without changing trading strategy behavior or risking live historical data.

**Architecture:** Add a lightweight shared quality-finding contract, a baseline-ratcheted static audit CLI, hardened test-data isolation, persistent runtime observability, storage health, Kalshi contract/drift checks, frontend/API/browser CI, an evidence-triggered read-only research runner, and generated current-state project metadata. Reuse and compose the existing diagnostics, alerting, task-supervisor, backup, fault-log, frontend, and GitHub Actions systems rather than replacing them.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest 9, Selenium 4.47, JavaScript ES modules, esbuild, ESLint, GitHub Actions, DDEV, Python stdlib AST/JSON/hashlib/sqlite3/subprocess utilities, existing `ruamel.yaml` dependency.

**Spec:** `docs/superpowers/specs/2026-08-24-quality-control-plane-design.md`

## Global Constraints

- Read `CLAUDE.md` before changing anything. If it conflicts with this plan, `CLAUDE.md` wins.
- Re-check current `main`/HEAD and recent commits before every workstream; this plan was grounded around HEAD `5eaed277...` but the repository is active.
- Use DDEV for normal backend verification: `ddev exec -s fastapi python3 -m pytest ...`.
- Never delete, reset, move, vacuum, or mutate live `data/*.db` files for testing or verification.
- Tests must never read or write repository live DBs. A connection attempt itself becomes a test failure after Task 2.
- Use additive SQLite schemas only. No drop-and-recreate migrations.
- Do not enable real trading or weaken `kalshi_account.trading_enabled`, typed confirmation, CORS, kill-switch, or execution-layer risk gates.
- Any Kalshi API/schema work must begin by reading the relevant current file(s) in `docs/kalshi/`.
- Runtime quality checks are read-only. They may emit findings/alerts; they may not alter strategy or config.
- Preserve the repo’s one-stateful-module/one-SQLite-file convention.
- Avoid adding third-party dependencies unless a task explicitly requires one; the intended audit framework uses the standard library plus already-installed dependencies.
- A new heuristic static-analysis finding is a warning until proven deterministic. Do not make speculative checks hard CI failures.
- Existing quality debt is baselined and ratcheted; this initiative must not become an unrelated whole-repo refactor.
- Every task follows TDD: failing targeted test → minimal implementation → targeted green → broader green → commit.
- Each independently testable task gets its own commit. Do not lump multiple workstreams into one “quality improvements” commit.
- When a roadmap item ships, update `ROADMAP.md` and `static/status.html` using the project’s established `/sync-status-docs` workflow.

---

# Execution Directive for Claude

Before Task 0, do all of the following and record the results in the working session:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log -12 --oneline
ddev describe
```

Then read, in this order:

1. `CLAUDE.md`
2. this plan and its spec
3. `ROADMAP.md`
4. `services/diagnostics/CHEATSHEET.md`
5. `services/alerting/CHEATSHEET.md`
6. `services/backup/CHEATSHEET.md`
7. current `tests/conftest.py`
8. `.github/workflows/tests.yml`
9. `.github/workflows/docs-drift-check.yml`
10. `frontend/package.json`
11. `static/status.html` phases related to diagnostics/audits, modularization, monitoring, storage, and CI
12. the latest 15–25 commits whose messages contain `audit`, `diagnostic`, `instrument`, `test`, `CI`, `performance`, `rate`, `storage`, `unwired`, or `zero callers`

**Re-grounding rule:** if a task’s proposed file/function already exists at current HEAD, do not create a duplicate. Verify the task’s acceptance criteria against the existing implementation, add only what is missing, and document that the task was partially/fully pre-shipped.

**Investigation-to-guard rule:** every bug or recurring failure class discovered while implementing this plan must receive an explicit final disposition in the commit message or status update:

- permanent runtime diagnostic/observability added
- permanent CI/CD guard added
- shared runtime + CI checking logic added
- existing permanent guard already covers it
- intentionally one-off; reason documented

**CI ownership rule:** a deterministic recurring checker is not considered permanently integrated merely because Claude can run it manually. If a check does not require live application state, is deterministic enough for automation, can run safely in a clean checkout, and protects against a recurring failure class, CI/CD is the default permanent owner. Wire the checker into the appropriate GitHub Actions workflow in the same logical task unless there is a documented reason not to. Runtime-only conditions belong in application diagnostics/observability; network-dependent, upstream, or expensive checks normally belong in scheduled/manual CI.

---

# Planned File Structure

The intended end state is:

```text
services/
  quality/
    __init__.py
    models.py
    routes.py
    CHEATSHEET.md
  observability/
    __init__.py
    observability.py
    routes.py
    CHEATSHEET.md
  storage_health/
    __init__.py
    storage_health.py
    routes.py
    CHEATSHEET.md
  research/
    __init__.py
    research.py
    routes.py
    CHEATSHEET.md

tools/
  __init__.py
  project_manifest.py
  kalshi_docs_drift.py
  quality_audit/
    __init__.py
    __main__.py
    models.py
    baseline.py
    source.py
    routers.py
    background.py
    persistence.py
    config_usage.py
    resources.py
    api_usage.py
    frontend_contract.py
    baseline.json

tests/
  support/
    __init__.py
    runtime_isolation.py
  fixtures/
    kalshi/
      trade.json
      ticker.json
      lifecycle.json
      market_position.json
      fill.json
      market.json
      event.json
      create_order_request.json
  test_quality_models.py
  test_quality_routes.py
  test_quality_audit.py
  test_runtime_isolation.py
  test_observability.py
  test_storage_health.py
  test_frontend_api_contract.py
  test_browser_e2e.py
  test_kalshi_docs_drift.py
  test_kalshi_contracts.py
  test_kalshi_public_canary.py
  test_research.py
  test_project_manifest.py
  perf/

.github/workflows/
  tests.yml                         # existing; mostly retained
  quality.yml                       # new
  docs-drift-check.yml              # upgraded or replaced deliberately
  kalshi-contract.yml               # new if separation is clearer
  performance.yml                   # scheduled/manual synthetic regressions

static/
  project-manifest.json
  status.html                       # historical prose retained; current facts wired to manifest

frontend/src/js/
  system-health.js                  # create only after backend quality API is stable
  main.js                           # imports/exports health renderer as needed
```

If current HEAD has already reorganized any of these concerns, adapt paths to the established package structure while preserving responsibilities and interfaces.

---

## Task 0: Capture the current implementation baseline

**Files:** No product files yet.

**Interfaces:**
- Consumes: current repository, current branch/HEAD, DDEV environment, and current CI definitions.
- Produces: recorded baseline test/build/audit counts and a confirmed clean starting state.

- [ ] **Step 1: Confirm the repository's current execution state**

Do not create an isolated worktree solely because the older version of this
plan required one. The repository's current `quality-plan-task` workflow is
the execution authority.

Run:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log -12 --oneline
ddev describe
```

If `CLAUDE.md` or the user's current instructions establish direct work on
`main`, continue on `main`. Do not introduce a feature worktree, per-task
subagent branch, or separate progress ledger for this initiative unless the
user explicitly changes the execution model.

If the working tree contains unrelated uncommitted changes, stop and resolve
that ambiguity before implementing Task 1.

- [ ] **Step 2: Capture current test collection and full-suite result**

Run:

```bash
ddev exec -s fastapi python3 -m pytest --collect-only -q
ddev exec -s fastapi python3 -m pytest -q
```

Record the collected/passing counts in the working session. Do not hardcode
1,265; current HEAD may be newer.

- [ ] **Step 3: Capture frontend baseline**

Run:

```bash
cd frontend
npm ci
npm run lint
npm run build
cd ..
git status --short
```

If `npm run build` changes `static/js/dashboard.bundle.js` on a clean checkout,
stop and treat that as a pre-existing source/bundle drift finding before
continuing.

- [ ] **Step 4: Capture current CI/workflow baseline**

Read all current workflow files under `.github/workflows/`, including
`tests.yml`, `docs-drift-check.yml`, and `quality.yml` when present.

Record each workflow's jobs/triggers and identify which Quality Control Plane
checks are already permanently owned by CI. Do not duplicate an existing
check merely because this plan originally described it as future work.

- [ ] **Step 5: Commit nothing**

Task 0 is a baseline checkpoint, not a product-code change. If Task 0 exposes
a genuine pre-existing defect that must be fixed before Task 1, handle that
defect as a focused prerequisite using the repository's normal
investigation-to-guard and commit workflow.

---

## Task 1: Add the shared quality finding model

**Files:**
- Create: `services/quality/__init__.py`
- Create: `services/quality/models.py`
- Create: `tests/test_quality_models.py`

**Interfaces:**
- Produces: `QualityFinding`, `QualityReport`, `severity_rank()`, deterministic serialization used by runtime and CI tooling.
- Consumes: Python stdlib only.

- [ ] **Step 1: Write failing model tests**

Create tests equivalent to:

```python
from services.quality.models import QualityFinding, QualityReport, severity_rank


def test_quality_finding_serializes_stably():
    finding = QualityFinding(
        finding_id="router-unmounted:services.foo.routes",
        check="router-registration",
        severity="error",
        confidence="high",
        source="ci",
        scope="services.foo.routes",
        summary="router exists but is not mounted",
        evidence={"path": "services/foo/routes.py"},
        remediation="include foo_routes.router in main.py",
    )
    assert finding.to_dict()["finding_id"] == "router-unmounted:services.foo.routes"
    assert finding.to_dict()["severity"] == "error"


def test_quality_report_counts_by_severity():
    report = QualityReport(findings=[
        QualityFinding("a", "x", "warning", "high", "ci", "a", "A", {}),
        QualityFinding("b", "x", "error", "high", "ci", "b", "B", {}),
    ])
    assert report.counts() == {"info": 0, "warning": 1, "error": 1}
    assert report.overall_status() == "error"


def test_severity_rank_orders_error_highest():
    assert severity_rank("error") > severity_rank("warning") > severity_rank("info")
```

- [ ] **Step 2: Run targeted test and verify failure**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_quality_models.py -q
```

Expected: import failure because `services.quality.models` does not exist.

- [ ] **Step 3: Implement the model**

Use dataclasses and exact literals:

```python
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warning", "error"]
Confidence = Literal["high", "medium", "low"]
Source = Literal["runtime", "ci"]

@dataclass(frozen=True)
class QualityFinding:
    finding_id: str
    check: str
    severity: Severity
    confidence: Confidence
    source: Source
    scope: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class QualityReport:
    findings: list[QualityFinding]

    def counts(self) -> dict[str, int]:
        return {
            "info": sum(f.severity == "info" for f in self.findings),
            "warning": sum(f.severity == "warning" for f in self.findings),
            "error": sum(f.severity == "error" for f in self.findings),
        }

    def overall_status(self) -> str:
        counts = self.counts()
        if counts["error"]:
            return "error"
        if counts["warning"]:
            return "warning"
        return "ok"

def severity_rank(value: Severity) -> int:
    return {"info": 0, "warning": 1, "error": 2}[value]
```

- [ ] **Step 4: Run targeted test**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_quality_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Run a small import regression set**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_diagnostics.py tests/test_alerting.py -q
```

- [ ] **Step 6: Commit**

```bash
git add services/quality tests/test_quality_models.py
git commit -m "Add shared quality finding model"
```

---

## Task 2: Turn test/live-data isolation into a hard invariant

**Files:**
- Create: `tests/support/__init__.py`
- Create: `tests/support/runtime_isolation.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_runtime_isolation.py`

**Interfaces:**
- Produces: `install_runtime_isolation(temp_root: Path) -> IsolationContext`
- Produces: `PERSISTENCE_MODULE_PATHS: tuple[str, ...]`
- Produces: global pytest-time guard that rejects any `sqlite3.connect()` under repo `data/`.
- Consumes: existing DB_PATH-owning modules and global `config_store`.

- [ ] **Step 1: Enumerate current persistence owners before coding**

Search current source for `DB_PATH =` and compare the result to `tests/conftest.py`. The list in the new helper must be based on current HEAD, not copied blindly from this plan.

At minimum, the baseline reviewed for this plan includes:

```text
services.paper_broker
services.risk_manager
services.config_performance
services.market_history
services.market_catalog.market_catalog
services.series_evaluator
services.series_watcher
services.candidate_log
services.settlement_edge
services.market_analyst_agent._db
services.market_events.event_schedule
```

Also include every newer persistence owner found at current HEAD such as alert/backup/fault/index/game-state stores when their module-level paths are test-reachable.

- [ ] **Step 2: Write failing isolation tests**

Tests must prove both redirection and hard blocking:

```python
def test_repo_data_sqlite_connection_is_blocked(repo_root):
    forbidden = repo_root / "data" / "forbidden-test.db"
    assert not forbidden.exists()
    with pytest.raises(AssertionError, match="live repository data"):
        sqlite3.connect(forbidden)
    assert not forbidden.exists()


def test_registered_persistence_modules_are_redirected_outside_repo_data():
    for module in loaded_registered_modules():
        path = Path(module.DB_PATH).resolve()
        assert repo_data_dir() not in path.parents
```

- [ ] **Step 3: Run and verify the hard-block test fails before implementation**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_runtime_isolation.py -q
```

- [ ] **Step 4: Extract conftest path setup into `tests/support/runtime_isolation.py`**

Implement one source of truth. `conftest.py` should become a small bootstrap that creates a temp root and calls `install_runtime_isolation()` before any test module imports `main`.

The helper must copy the real config file to temp and repoint/reload the module-level `config_store`, exactly preserving current behavior.

- [ ] **Step 5: Install the SQLite guard**

Wrap `sqlite3.connect` after persistence paths are redirected:

```python
_original_connect = sqlite3.connect

def guarded_connect(database, *args, **kwargs):
    if is_repo_data_path(database):
        raise AssertionError(f"pytest attempted to open live repository data: {database}")
    return _original_connect(database, *args, **kwargs)
```

Handle `Path`, plain strings, and SQLite `file:` URIs. Allow `:memory:`.

Do **not** allow a blanket escape hatch marker in CI. If a legitimate test truly needs a production-shaped DB, copy it into the temp root first.

- [ ] **Step 6: Run the new tests**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_runtime_isolation.py -q
```

- [ ] **Step 7: Run DB-heavy regression tests**

```bash
ddev exec -s fastapi python3 -m pytest \
  tests/test_paper_broker.py \
  tests/test_market_catalog.py \
  tests/test_market_history.py \
  tests/test_series_watcher.py \
  tests/test_event_schedule.py \
  tests/test_task_supervisor.py -q
```

- [ ] **Step 8: Run the full suite**

```bash
ddev exec -s fastapi python3 -m pytest -q
```

- [ ] **Step 9: Verify live DB mtimes/checksums did not change because of pytest**

Use read-only `stat`/hash comparisons before and after one extra targeted test run. Do not open the DBs with SQLite for this verification.

- [ ] **Step 10: Commit**

```bash
git add tests/conftest.py tests/support tests/test_runtime_isolation.py
git commit -m "Harden pytest isolation from live data stores"
```

---

## Task 3: Build the static quality-audit framework and baseline ratchet

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/quality_audit/__init__.py`
- Create: `tools/quality_audit/__main__.py`
- Create: `tools/quality_audit/models.py`
- Create: `tools/quality_audit/source.py`
- Create: `tools/quality_audit/baseline.py`
- Create: `tools/quality_audit/baseline.json`
- Create: `tests/test_quality_audit.py`

**Interfaces:**
- `run_audit(repo_root: Path) -> QualityReport`
- `load_baseline(path: Path) -> set[str]`
- `compare_to_baseline(report, baseline_ids) -> BaselineComparison`
- CLI exit: nonzero only for **new high-confidence errors** in default mode.

- [ ] **Step 1: Write tests for deterministic finding IDs and baseline behavior**

Cover:

```python
def test_existing_error_in_baseline_does_not_fail_default_gate(): ...
def test_new_high_confidence_error_fails_default_gate(): ...
def test_new_warning_is_reported_but_does_not_fail_default_gate(): ...
def test_resolved_baseline_id_is_reported_as_resolved(): ...
```

- [ ] **Step 2: Run and verify failure**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_quality_audit.py -q
```

- [ ] **Step 3: Implement source helpers**

`source.py` should provide:

```python
iter_python_files(repo_root, include_tests=False)
parse_python(path) -> ast.Module
read_text(path) -> str
relative_path(repo_root, path) -> str
```

Ignore `.git`, `.ddev`, `data`, `node_modules`, generated JS bundle, and Python caches.

- [ ] **Step 4: Implement baseline comparison**

Baseline stores stable finding IDs, plus optional notes, not full mutable evidence blobs.

Example:

```json
{
  "version": 1,
  "accepted_finding_ids": [
    "unused-public:services.example:legacy_helper"
  ]
}
```

- [ ] **Step 5: Implement the CLI shell**

Support:

```bash
python -m tools.quality_audit --repo-root .
python -m tools.quality_audit --repo-root . --json-out build/quality-audit.json
python -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json
python -m tools.quality_audit --repo-root . --strict
```

Default output must show counts for new/existing/resolved findings and each new error/warning.

- [ ] **Step 6: Run the empty-scanner framework**

At this stage `run_audit()` may return no findings. Verify CLI plumbing and JSON output.

- [ ] **Step 7: Commit**

```bash
git add tools tests/test_quality_audit.py
git commit -m "Add baseline-ratcheted quality audit framework"
```

---

## Task 4: Add high-confidence router and background-task wiring checks

**Files:**
- Create: `tools/quality_audit/routers.py`
- Create: `tools/quality_audit/background.py`
- Modify: `tools/quality_audit/__main__.py`
- Modify: `tests/test_quality_audit.py`

**Interfaces:**
- `scan_router_registration(repo_root) -> list[QualityFinding]`
- `scan_background_wiring(repo_root) -> list[QualityFinding]`

- [ ] **Step 1: Write fixture-based router tests**

Use temporary fake repos in tests. Prove:

- mounted router → no error
- `services/foo/routes.py` with `router = APIRouter()` but no `include_router` → `error/high`
- route module documented with `# quality-audit: standalone-router` → no error

Expected stable ID:

```text
router-unmounted:services.foo.routes
```

- [ ] **Step 2: Implement router discovery with AST**

Do not grep for raw strings alone. Parse:

- route module assignment `router = APIRouter(...)`
- `main.py` imports such as `from services.foo import routes as foo_routes`
- calls such as `app.include_router(foo_routes.router)`

- [ ] **Step 3: Write background-wiring tests**

A function is considered scheduler-style in v1 when its name begins `_maybe_` and its body contains at least one of:

- `task_supervisor.supervise(...)`
- `asyncio.create_task(...)`
- assignment to a subscript/key named `task`

Test:

- scheduler function with a call in `main.py` → no error
- scheduler function with zero non-test external references → high-confidence error

Stable ID:

```text
background-unwired:services.foo:_maybe_do_work
```

- [ ] **Step 4: Implement cross-file symbol reference scanning**

Count calls/references outside the defining module and outside `tests/`. Avoid counting the function definition itself.

- [ ] **Step 5: Run targeted tests**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_quality_audit.py -q
```

- [ ] **Step 6: Run the scanner on the real repo**

```bash
ddev exec -s fastapi python3 -m tools.quality_audit --repo-root . --json-out /tmp/quality-audit.json
```

Investigate every **new high-confidence** error before baselining it. Do not blindly accept real unmounted/unwired code into baseline.

- [ ] **Step 7: Commit**

```bash
git add tools/quality_audit tests/test_quality_audit.py
git commit -m "Audit router and background-service wiring"
```

---

## Task 5: Add persistence, resource-lifecycle, config-usage, and API-usage scanners

**Files:**
- Create: `tools/quality_audit/persistence.py`
- Create: `tools/quality_audit/resources.py`
- Create: `tools/quality_audit/config_usage.py`
- Create: `tools/quality_audit/api_usage.py`
- Modify: `tools/quality_audit/__main__.py`
- Modify: `tests/test_quality_audit.py`

**Interfaces:** scanner functions each return `list[QualityFinding]`.

- [ ] **Step 1: Persistence scanner test — new DB_PATH owner not registered for test isolation**

The scanner must compare source-defined `DB_PATH` owners with `PERSISTENCE_MODULE_PATHS` from `tests/support/runtime_isolation.py` without importing application singletons.

A fake source module with `DB_PATH` absent from the registry produces:

```text
persistence-unisolated:services.new_store
```

severity `error`, confidence `high`.

- [ ] **Step 2: Implement persistence inventory**

Use AST to detect module-level assignments named `DB_PATH`. The registry should be read as source/AST or imported from the pure support module only if that import has no application side effects.

- [ ] **Step 3: Resource lifecycle test**

Fixture:

```python
async def leak():
    client = KalshiClient(...)
    return await client.get_market("X")
```

must produce a warning/error because `client.close()` is absent.

A function containing:

```python
client = KalshiClient(...)
try:
    ...
finally:
    await client.close()
```

must not produce the finding.

V1 only proves presence/absence of a close call; do not claim full control-flow correctness.

- [ ] **Step 4: Config usage scanner tests**

Parse a synthetic settings YAML and code containing common forms:

```python
cfg["strategy"]["entry_threshold"]
cfg.get("strategy", {}).get("take_profit_pct")
```

Known unread leaf → `warning/medium`, never default CI failure.

- [ ] **Step 5: API usage inventory tests**

Inventory calls such as:

```python
client.get_market(...)
client.get_markets_by_tickers(...)
account.get_positions(...)
```

Output informational findings/counters with source file and function. This inventory is for future REST/WS reviews, not a blanket ban.

- [ ] **Step 6: Run real-repo audit and review all high-confidence findings**

Do not baseline a real missing close/resource or unisolated DB owner without fixing or explicitly justifying it.

- [ ] **Step 7: Write initial baseline**

After real errors are fixed or consciously classified, save only remaining accepted warnings/debt to `tools/quality_audit/baseline.json`.

- [ ] **Step 8: Add a test that the real repo audit has no **new** high-confidence errors against baseline**

This gives local pytest a small architecture smoke independent of GitHub Actions.

- [ ] **Step 9: Commit**

```bash
git add tools/quality_audit tests/test_quality_audit.py tests/support/runtime_isolation.py
git commit -m "Expand static audit for persistence and API usage"
```

---

## Task 6: Add frontend lint/build/bundle-sync CI

**Files:**
- Create: `.github/workflows/quality.yml`
- No source changes required unless current lint/build exposes real defects.

**Interfaces:** GitHub Actions job `frontend-build`.

- [ ] **Step 1: Verify local commands from a clean tree**

```bash
cd frontend
npm ci
npm run lint
npm run build
cd ..
git diff --exit-code -- static/js/dashboard.bundle.js
```

If any command fails, fix the actual source issue in a separate commit before wiring CI.

- [ ] **Step 2: Add `frontend-build` job**

Use `actions/checkout@v4` and `actions/setup-node@v4` with the Node version compatible with the existing lockfile. Enable npm cache using `frontend/package-lock.json`.

Commands exactly:

```yaml
- working-directory: frontend
  run: npm ci
- working-directory: frontend
  run: npm run lint
- working-directory: frontend
  run: npm run build
- run: git diff --exit-code -- static/js/dashboard.bundle.js
```

- [ ] **Step 3: Add `architecture-audit` job skeleton in the same workflow**

Set up Python 3.13, install `requirements-dev.txt`, run:

```bash
python -m tools.quality_audit --repo-root . \
  --baseline tools/quality_audit/baseline.json \
  --json-out build/quality-audit.json
```

Upload `build/quality-audit.json` with `actions/upload-artifact@v4` using `if: always()`.

- [ ] **Step 4: Validate workflow syntax locally by inspection and, if available, `actionlint`; do not add actionlint as a repo dependency solely for this task**

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/quality.yml
git commit -m "Add frontend and architecture quality CI"
```

---

## Task 7: Add frontend-to-backend API contract audit

**Files:**
- Create: `tools/quality_audit/frontend_contract.py`
- Create: `tests/test_frontend_api_contract.py`
- Modify: `.github/workflows/quality.yml`
- Modify: `tools/quality_audit/__main__.py`

**Interfaces:**
- `extract_backend_routes(repo_root) -> set[RouteSpec]`
- `extract_frontend_calls(repo_root) -> list[FrontendCall]`
- `scan_frontend_contract(repo_root) -> list[QualityFinding]`

- [ ] **Step 1: Write backend route-extraction tests**

Support decorators:

```python
@router.get("/api/foo/{ticker}")
@app.post("/api/bar")
```

Capture HTTP method and path template.

- [ ] **Step 2: Write frontend call-extraction tests**

Support at minimum:

```javascript
fetchJSON('/api/config')
fetch('/api/state')
fetchJSON(`/api/markets/${ticker}/detail`)
```

Normalize `${...}` segments to dynamic placeholders.

- [ ] **Step 3: Write mismatch test**

A frontend literal call with no compatible backend route emits high-confidence error:

```text
frontend-route-missing:GET:/api/does-not-exist
```

Backend-only routes are informational, not failures.

- [ ] **Step 4: Implement the scanner**

Keep parsing deliberately narrow/high-confidence. If a JS URL is built through opaque concatenation or helper indirection, report `unknown`/info rather than guessing.

- [ ] **Step 5: Run against current frontend**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_frontend_api_contract.py -q
ddev exec -s fastapi python3 -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json
```

Investigate real mismatches rather than baselining obvious bugs.

- [ ] **Step 6: Add a dedicated CI step/job**

Either keep it inside `architecture-audit` or create `frontend-api-contract`; prefer a separate job if failures are easier to interpret that way.

- [ ] **Step 7: Commit**

```bash
git add tools/quality_audit tests/test_frontend_api_contract.py .github/workflows/quality.yml
git commit -m "Audit frontend and backend API contract"
```

---

## Task 8: Add a real browser E2E job that does not depend on DDEV-only hostnames

**Files:**
- Create: `tests/support/e2e_server.py`
- Create: `tests/test_browser_e2e.py`
- Modify: `.github/workflows/quality.yml`
- Optionally modify: `tests/test_e2e_terminal_static_and_api.py` only to share helpers, not to remove its existing DDEV coverage.

**Interfaces:**
- CI env flag: `RUN_BROWSER_E2E=1`
- local server: `http://127.0.0.1:8765`

- [ ] **Step 1: Create the isolated ASGI test server**

`tests/support/e2e_server.py` must call the same runtime isolation helper from Task 2 **before importing `main`**, then:

```python
from fastapi.staticfiles import StaticFiles
from main import app

app.mount("/", StaticFiles(directory="static", html=True), name="e2e-static")
```

Mounting occurs after production routes are already registered, so `/api/*` resolves before the catch-all static mount.

- [ ] **Step 2: Write one failing browser smoke test**

Use existing stable IDs from `static/index.html`:

```python
@pytest.mark.skipif(os.getenv("RUN_BROWSER_E2E") != "1", reason="browser e2e opt-in")
def test_dashboard_loads_and_tabs_switch(driver):
    driver.get("http://127.0.0.1:8765/")
    assert "Whale Signal" in driver.title
    for tab_id in (
        "tab-btn-portfolio", "tab-btn-markets", "tab-btn-whale",
        "tab-btn-terminal", "tab-btn-history", "tab-btn-config",
    ):
        driver.find_element(By.ID, tab_id).click()
        # Assert the corresponding view becomes active/visible using the
        # current frontend's real class/display convention discovered from source.
```

The implementer must inspect `showView()` and use the real active/visibility contract, not invent test-only behavior.

- [ ] **Step 3: Add console error capture**

Configure Chrome logging preferences and fail on `SEVERE` browser console entries except an explicit, documented allowlist of known benign browser messages. Keep the allowlist empty initially.

- [ ] **Step 4: Add regression coverage for the historical `fetchJSON` failure class**

Use a safe endpoint/action that is expected to return 4xx in the isolated test state. Assert the UI displays/propagates failure rather than success. Do not call real-trading enable/disable or mutate safety-critical fields.

- [ ] **Step 5: Run locally with a background uvicorn process**

Example:

```bash
RUN_BROWSER_E2E=1 ddev exec -s fastapi sh -lc '
  python3 -m uvicorn tests.support.e2e_server:app --host 0.0.0.0 --port 8765 >/tmp/e2e-uvicorn.log 2>&1 &
  pid=$!;
  trap "kill $pid" EXIT;
  python3 -m pytest tests/test_browser_e2e.py -q
'
```

If the DDEV fastapi container does not contain Chrome, run the browser side using the existing `selenium-chrome` service for local verification; CI will install Chrome directly.

- [ ] **Step 6: Add GitHub Actions browser job**

Use `browser-actions/setup-chrome@v1`, Python 3.13, `pip install -r requirements-dev.txt`, start uvicorn, wait for health with bounded retries, run the browser test with `RUN_BROWSER_E2E=1`, and upload `/tmp/e2e-uvicorn.log` on failure.

- [ ] **Step 7: Commit**

```bash
git add tests/support/e2e_server.py tests/test_browser_e2e.py .github/workflows/quality.yml
git commit -m "Run browser smoke tests in CI"
```

---

## Task 9: Persist existing runtime performance/health instrumentation

**Files:**
- Create: `services/observability/__init__.py`
- Create: `services/observability/observability.py`
- Create: `services/observability/routes.py`
- Create: `services/observability/CHEATSHEET.md`
- Create: `tests/test_observability.py`
- Modify: `services/app_state.py`
- Modify: `main.py`
- Modify: `config/settings.yaml`
- Modify: `tests/conftest.py` / isolation registry for `observability.DB_PATH`

**Interfaces:**
- `record_sample(metric: str, value: float | None, labels: dict | None = None, observed_at: float | None = None)`
- `capture_from_runtime(cfg: dict, state: dict, trade_stream, index_stream) -> dict[str, float | None]`
- `maybe_capture(cfg, state, trade_stream, index_stream) -> None`
- `history(metric: str, since_ts: float, limit: int = 5000) -> list[dict]`
- Routes under `/api/observability/*`.

- [ ] **Step 1: Add config with only the minimal knobs**

```yaml
observability:
  enabled: true
  sample_interval_sec: 60
  retention_hours: 336
```

Do not expose per-metric thresholds as config in this task.

- [ ] **Step 2: Write DB-path and schema tests**

Use temp DB. Test additive creation and sample retrieval order.

- [ ] **Step 3: Write capture tests with a synthetic state dict**

Synthetic state must cover:

```python
{
    "last_tick_duration_sec": 1.25,
    "last_tick_rate_limit_hits": 0,
    "tick_phase_timings": {"market_fetch": 0.4, "exit_management": 0.1},
    "trade_stream_perf": {
        "messages_per_sec": 180.0,
        "avg_handler_ms": 1.7,
    },
}
```

Assert emitted metric names are stable, e.g.:

```text
tick.duration_sec
tick.rate_limit_hits
tick.phase.market_fetch_sec
trade_stream.messages_per_sec
trade_stream.avg_handler_ms
trade_stream.dropped_messages
```

- [ ] **Step 4: Implement SQLite persistence and bounded pruning**

Use `data/observability.db`, one connection per public operation or an established batched pattern; do not create a connection per metric row. `record_samples_bulk()` should write one batch/commit per capture cycle.

- [ ] **Step 5: Implement `maybe_capture()` as a cheap interval gate**

Store the last sample time in `state["observability"] = {"last_sample_at": 0.0}` or infer from DB if restart persistence is important. Prefer restart-safe due logic: read latest persisted sample at startup or on the first due check so uvicorn reload does not force a special extra sample storm.

- [ ] **Step 6: Wire one line into the trading loop near other `_maybe_*` operational checks**

Do not place SQLite/aggregation work on the critical path if it becomes measurable. If capture takes non-trivial time, schedule it with `task_supervisor.supervise` and keep overlap state in `app_state`.

- [ ] **Step 7: Add routes**

Implement:

```text
GET /api/observability/current
GET /api/observability/history?metric=...&hours=...
GET /api/observability/summary?hours=...
```

Validate metric names and cap query limits.

- [ ] **Step 8: Register router in `main.py`**

This should immediately be protected by Task 4’s router-registration audit.

- [ ] **Step 9: Add observability DB to test isolation**

The static audit must fail if this is forgotten.

- [ ] **Step 10: Run targeted tests and quality audit**

```bash
ddev exec -s fastapi python3 -m pytest tests/test_observability.py tests/test_quality_audit.py -q
ddev exec -s fastapi python3 -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json
```

- [ ] **Step 11: Live-verify without mutating historical trading data**

Use DDEV read-only checks:

```bash
curl -s https://<actual-ddev-host>/api/observability/current
```

Wait for one sample interval only if the app is already running; do not reset anything. Confirm a real process restart does not corrupt or explosively duplicate samples.

- [ ] **Step 12: Commit**

```bash
git add services/observability services/app_state.py main.py config/settings.yaml tests
git commit -m "Persist runtime observability metrics"
```

---

## Task 10: Add runtime anomaly findings and the unified quality summary API

**Files:**
- Modify: `services/quality/models.py`
- Create: `services/quality/routes.py`
- Create: `services/quality/CHEATSHEET.md`
- Modify: `services/observability/observability.py`
- Create/modify: `tests/test_quality_routes.py`
- Modify: `main.py`

**Interfaces:**
- `observability.runtime_findings(cfg, state, ...) -> list[QualityFinding]`
- `GET /api/quality/summary`

- [ ] **Step 1: Write anomaly rule tests**

Cover exact behavior:

1. `last_tick_duration_sec > kalshi.poll_interval_sec` → warning/high.
2. `trade_stream.dropped_messages > 0` → error/high.
3. stream disconnected while app `running=True` → warning/high.
4. no sample/data needed to judge a quiet store → `unknown`/no false error.
5. recent rate-limit hits repeatedly above zero → warning, using a bounded history window rather than one isolated hit.

- [ ] **Step 2: Implement pure rule functions**

Keep each rule separately testable. Do not perform DB writes inside rule evaluation.

- [ ] **Step 3: Build `/api/quality/summary`**

Compose only local/read-only sources:

- `diagnostics.run_offline(...)`
- current observability findings
- `alerting` active alerts through its query API
- recent `fault_log` summary
- storage health later, when Task 11 lands
- latest research metadata later, when Task 16 lands

Return:

```json
{
  "generated_at": 0,
  "status": "ok|warning|error|unknown",
  "counts": {"info": 0, "warning": 0, "error": 0},
  "findings": [],
  "diagnostics": {},
  "alerts": {},
  "faults": {}
}
```

- [ ] **Step 4: Explicitly prove the route makes no Kalshi network calls**

In a route test, monkeypatch `KalshiClient` construction/network methods to raise if touched; `GET /api/quality/summary` must still return 200.

- [ ] **Step 5: Register route and run architecture audit**

- [ ] **Step 6: Commit**

```bash
git add services/quality services/observability main.py tests/test_quality_routes.py
git commit -m "Expose unified runtime quality summary"
```

---

## Task 11: Add storage-health reporting and integrity checks

**Files:**
- Create: `services/storage_health/__init__.py`
- Create: `services/storage_health/storage_health.py`
- Create: `services/storage_health/routes.py`
- Create: `services/storage_health/CHEATSHEET.md`
- Create: `tests/test_storage_health.py`
- Modify: `services/quality/routes.py`
- Modify: `main.py`

**Interfaces:**
- `inventory_data_dir(data_dir: Path) -> list[DatabaseHealth]`
- `database_health(path: Path, include_table_counts: bool = False) -> dict`
- `quick_check(path: Path) -> dict`
- routes `/api/health/storage`, `/api/health/storage/scan`, `/api/health/storage/integrity-check`.

- [ ] **Step 1: Write tests against disposable SQLite fixtures**

Create temp DBs with known page/row counts. Assert:

- size bytes reported
- page count/page size reported
- table names reported
- quick check returns `ok`
- nonexistent/corrupt fixture returns explicit error without raising through the route

- [ ] **Step 2: Implement fast inventory**

Default `GET /api/health/storage` should avoid full `COUNT(*)` scans of huge tables. It may use file stat + lightweight pragmas. Deep counts belong to explicit scan.

- [ ] **Step 3: Implement deep scan as background task**

`POST /api/health/storage/scan` schedules read-only work through `task_supervisor`; protect against overlap.

- [ ] **Step 4: Implement explicit integrity check**

Use `PRAGMA quick_check`, not `integrity_check` by default. Never repair automatically.

- [ ] **Step 5: Add size-growth calculation using observability history**

At minimum, compute current size minus the oldest available `db.size_bytes` sample in the requested window. If no historical sample exists, report growth as `None`/unknown.

- [ ] **Step 6: Fold storage summary into `/api/quality/summary`**

Storage errors/warnings become `QualityFinding`s with deterministic IDs like:

```text
storage-integrity:data/game_state.db
storage-growth:data/game_state.db
backup-overdue
```

Do not create a hard “too big” threshold from intuition. Growth findings require an explicit, conservative criterion based on rate/history and should start as warnings.

- [ ] **Step 7: Run targeted + full tests**

- [ ] **Step 8: Live read-only verify real DB inventory**

Do not run deep row counts or integrity checks against every multi-GB DB automatically. Use the fast route first; manually invoke one bounded quick check if appropriate.

- [ ] **Step 9: Commit**

```bash
git add services/storage_health services/quality main.py tests/test_storage_health.py
git commit -m "Add storage health and integrity diagnostics"
```

---

## Task 12: Upgrade Kalshi docs checking from URL availability to content drift

**Files:**
- Create: `tools/kalshi_docs_drift.py`
- Create: `docs/kalshi/upstream-manifest.json`
- Create: `tests/test_kalshi_docs_drift.py`
- Modify or replace deliberately: `.github/workflows/docs-drift-check.yml`

**Interfaces:**
- `build_manifest(docs_root: Path) -> dict`
- `compare_remote(manifest) -> DriftReport`
- CLI `--check`, `--write-manifest`, `--json-out`.

- [ ] **Step 1: Read `docs/kalshi/README.md` and `llms.txt` to confirm how local paths map to source URLs**

Do not invent filename mapping if the repo already records provenance.

- [ ] **Step 2: Write unit tests using local HTTP-free fixtures**

Test:

- unchanged normalized content → no drift
- changed body with HTTP 200 → drift detected
- HTTP status non-200 in mocked fetch layer → availability failure
- CRLF vs LF only → no content drift after newline normalization

- [ ] **Step 3: Implement SHA256 manifest generation**

Manifest entry:

```json
{
  "source_url": "https://docs.kalshi.com/...md",
  "local_path": "docs/kalshi/get-market.md",
  "sha256": "..."
}
```

Hash normalized UTF-8 content. Do not strip semantic whitespace or markdown text.

- [ ] **Step 4: Generate initial manifest from current mirrored docs**

```bash
ddev exec -s fastapi python3 tools/kalshi_docs_drift.py --write-manifest docs/kalshi/upstream-manifest.json
```

Review unexpected missing provenance before committing.

- [ ] **Step 5: Upgrade scheduled workflow**

Weekly + workflow_dispatch. On drift:

- print changed URLs/files
- write JSON report
- upload artifact even on failure
- exit nonzero
- do not auto-commit

- [ ] **Step 6: Commit**

```bash
git add tools/kalshi_docs_drift.py docs/kalshi/upstream-manifest.json tests/test_kalshi_docs_drift.py .github/workflows/docs-drift-check.yml
git commit -m "Detect Kalshi documentation content drift"
```

---

## Task 13: Add fixture-based Kalshi contract tests for the highest-risk schemas

**Files:**
- Create: `tests/fixtures/kalshi/*.json` listed in the spec
- Create: `tests/test_kalshi_contracts.py`
- Modify implementation only if a contract test exposes a current bug
- Create/modify: `.github/workflows/kalshi-contract.yml`

**Interfaces:** deterministic tests over existing parser/normalization/request code.

- [ ] **Step 1: Read the exact authoritative docs before creating each fixture**

At minimum inspect current docs for:

- trade messages / trade REST fields
- market ticker WS
- market lifecycle v2
- user fills WS
- market positions WS
- get market
- get event
- create order v2
- cancel order

Record each fixture’s source doc in a small `_meta` field or adjacent comment/documentation file; do not put undocumented invented fields into fixtures.

- [ ] **Step 2: Create fixtures for the historical bug classes**

The fixtures must specifically encode:

- canonical taker direction field so deprecated `taker_side` absence does not flip every trade to NO
- `type: "market_position"` singular
- fill identity using the documented field, including absence of the previously-assumed `fill_id` if current docs still say so
- lifecycle states sufficient to distinguish `determined` from `finalized`
- current create-order shape used by the installed SDK/client

- [ ] **Step 3: Write tests through real handler/normalizer entry points**

Do not merely assert fixture keys. Feed fixtures into the same functions production uses (`_process_stream_fill`, `_process_stream_position`, trade normalizer/provider parsing, lifecycle handler, account request builder or mocked SDK call).

- [ ] **Step 4: Add contract CI job**

Run these tests on PR/push whenever relevant paths change, and on workflow_dispatch. A simple always-on small test subset is acceptable if fast.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/kalshi tests/test_kalshi_contracts.py .github/workflows/kalshi-contract.yml
git commit -m "Add Kalshi API contract fixtures"
```

---

## Task 14: Add a safe scheduled public Kalshi API canary

**Files:**
- Create: `tools/kalshi_public_canary.py`
- Create: `tests/test_kalshi_public_canary.py`
- Modify: `.github/workflows/kalshi-contract.yml`

**Interfaces:** CLI exits nonzero on documented public-read response-shape failure; no account secrets.

- [ ] **Step 1: Verify from docs which endpoints are unauthenticated/public at current HEAD**

Do not rely on historical behavior. Only include endpoints explicitly confirmed public/read-only.

- [ ] **Step 2: Implement a tiny client with strict timeouts**

Use existing HTTP utilities if they can be imported without app singleton side effects; otherwise use Python stdlib/requests already in dev deps. No order, position, balance, or account calls.

- [ ] **Step 3: Validate only stable structural invariants**

Examples, if docs support them:

- response is JSON object
- markets/trades list exists and is a list
- ticker/market fields production parser requires are present in at least one real item

Do not assert volatile values, categories, counts, prices, or specific live tickers.

- [ ] **Step 4: Add scheduled/manual workflow job**

Run daily or weekly; choose weekly initially to minimize external flakiness. Upload response-shape summary on failure, redacting any headers.

- [ ] **Step 5: Commit**

```bash
git add tools/kalshi_public_canary.py tests/test_kalshi_public_canary.py .github/workflows/kalshi-contract.yml
git commit -m "Add read-only Kalshi public API canary"
```

---

## Task 15: Add centralized Kalshi REST usage telemetry without rewriting callers

**Files:**
- Modify: `services/http_client.py` and/or the narrowest shared Kalshi request layer current HEAD actually uses
- Modify: `services/observability/observability.py`
- Create/modify: `tests/test_http_client.py` or the current HTTP client test file

**Interfaces:**
- `http_metrics_snapshot(reset: bool = False) -> dict`
- per-endpoint family counts/latency/errors retained in memory and sampled by observability.

- [ ] **Step 1: Inspect the current HTTP call path**

Map which KalshiClient/account-client methods ultimately pass through `services/http_client.py`. Do not instrument above a layer that would double-count retries.

- [ ] **Step 2: Write tests for counters**

A fake successful request should increment:

- calls
- successes
- total latency / average latency

A 429/retry should increment rate-limit/retry metrics exactly once per actual HTTP attempt according to the chosen semantics. Document semantics in the test names.

- [ ] **Step 3: Implement bounded in-memory counters**

Key by normalized endpoint family, not full URL with ticker/query values, to avoid unbounded cardinality.

Good:

```text
GET /markets
GET /markets/{ticker}
GET /portfolio/positions
```

Bad:

```text
GET /markets/KX...specific...
```

- [ ] **Step 4: Sample the counters into observability**

Record calls/5m or cumulative deltas, average ms, errors, 429s. Do not persist every request.

- [ ] **Step 5: Expose in observability summary**

This is the durable replacement for future “how many REST calls are happening here?” temporary wrappers.

- [ ] **Step 6: Live-verify counter growth read-only**

Observe normal app traffic for a few ticks; do not change rate limits or strategy behavior in this task.

- [ ] **Step 7: Commit**

```bash
git add services/http_client.py services/observability tests
git commit -m "Track Kalshi REST usage and latency"
```

---

## Task 16: Build the evidence-triggered read-only research sweep service

**Files:**
- Create: `services/research/__init__.py`
- Create: `services/research/research.py`
- Create: `services/research/routes.py`
- Create: `services/research/CHEATSHEET.md`
- Create: `tests/test_research.py`
- Modify: `services/app_state.py`
- Modify: `main.py`
- Modify: `config/settings.yaml`
- Modify test isolation for `research.DB_PATH`

**Interfaces:**
- `build_report(cfg: dict, now: float | None = None) -> dict`
- `should_run(cfg: dict, checkpoints: dict, current_counts: dict) -> bool`
- `run_and_store(cfg: dict) -> dict`
- `_maybe_run_research(cfg) -> None`
- routes `/api/research/status|latest|history|run`.

- [ ] **Step 1: Add minimal config**

```yaml
research:
  enabled: false
  min_new_resolved_signals: 100
  min_new_closed_trades: 50
```

Default disabled until manually reviewed. Manual route still works while disabled if consistent with project conventions; if not, document exact behavior.

- [ ] **Step 2: Write `should_run` tests**

Prove OR semantics:

- +100 resolved signals → true
- +50 closed trades → true
- below both → false
- no prior checkpoint → report `insufficient` or run once only if explicitly designed/documented; choose one and test it

Recommended: first automatic run establishes a baseline only after one threshold has actually accumulated, avoiding an immediate heavy scan on every restart.

- [ ] **Step 3: Write report-composition tests using monkeypatched existing analyzers**

Assert `build_report` includes named sections and **does not call apply/update methods**.

- [ ] **Step 4: Implement report from existing read-only functions**

Use current HEAD APIs; do not duplicate their statistics. Include at minimum diagnostics, trade analytics, confidence calibration status/report, advisory recommendation snapshot, candidate population gate summary, settlement edge, and config epoch metadata where available.

- [ ] **Step 5: Persist report/checkpoints additively**

`data/research_reports.db`. Store JSON report plus source counts/watermarks.

- [ ] **Step 6: Schedule through `task_supervisor`**

Heavy report generation must not block the trading loop. Use overlap guard state and `asyncio.to_thread` for synchronous DB-heavy work if needed.

- [ ] **Step 7: Add routes and quality-summary metadata**

`/api/quality/summary` should expose only last research timestamp/status, not the full heavy report.

- [ ] **Step 8: Add test isolation registration and run quality audit**

Task 5 should fail if the new DB owner is missing from isolation.

- [ ] **Step 9: Commit**

```bash
git add services/research services/app_state.py main.py config/settings.yaml tests
git commit -m "Add evidence-triggered research snapshots"
```

---

## Task 17: Generate current project metadata and make `status.html` stop lying about mechanical facts

**Files:**
- Create: `tools/project_manifest.py`
- Create: `tests/test_project_manifest.py`
- Create: `static/project-manifest.json`
- Modify: `static/status.html`
- Optionally create: `static/js/status.js` if cleaner than inline script
- Modify: `.github/workflows/quality.yml`

**Interfaces:**
- `build_manifest(repo_root: Path) -> dict`
- CLI `--write PATH` and `--check PATH`.

- [ ] **Step 1: Write manifest tests against a temporary mini-repo**

Test deterministic counts for:

- Python/JS/HTML files
- lines
- service packages
- route decorators
- workflow files/jobs where parsed reliably

Do not require `.git` in tests; HEAD may be `None` if unavailable.

- [ ] **Step 2: Implement manifest builder**

Manifest structure:

```json
{
  "schema_version": 1,
  "generated_from_head": "...",
  "files": {"python": 0, "javascript": 0, "html": 0},
  "lines": {"python": 0, "javascript": 0, "html": 0},
  "services": 0,
  "api_routes": 0,
  "frontend_modules": 0,
  "workflow_files": 0
}
```

Exclude `node_modules`, `.git`, `data`, generated bundle from **source** line counts unless the label explicitly says generated output.

- [ ] **Step 3: Generate `static/project-manifest.json`**

```bash
ddev exec -s fastapi python3 tools/project_manifest.py --write static/project-manifest.json
```

- [ ] **Step 4: Replace stale mechanical header values in `status.html` with manifest-driven placeholders**

Do not rewrite timeline prose. Add IDs and a small script that fetches `/project-manifest.json` and fills counts.

- [ ] **Step 5: Correct the stale semantic claim that no order-execution path exists**

Use wording consistent with current `CLAUDE.md`: real order placement exists but is gated and paper mode remains default. This is a human-authored correction, not generated metadata.

- [ ] **Step 6: Add CI manifest check**

```bash
python tools/project_manifest.py --check static/project-manifest.json
```

If HEAD causes unavoidable churn, exclude `generated_from_head` from equality or support `--ignore-head`; the check should gate structural facts, not require a manifest commit on every unrelated code commit merely because SHA changed.

- [ ] **Step 7: Browser/smoke verify `/status`**

Ensure current facts populate and historical timeline still renders.

- [ ] **Step 8: Commit**

```bash
git add tools/project_manifest.py tests/test_project_manifest.py static/project-manifest.json static/status.html .github/workflows/quality.yml
git commit -m "Generate current build metadata for status page"
```

---

## Task 18: Add a minimal System Health UI backed by `/api/quality/summary`

**Files:**
- Create: `frontend/src/js/system-health.js`
- Modify: `frontend/src/js/main.js`
- Modify: `static/index.html`
- Modify: `static/css/dashboard.css` only if existing utility classes are insufficient
- Regenerate: `static/js/dashboard.bundle.js`
- Modify: `tests/test_browser_e2e.py`

**Interfaces:** read-only frontend call `GET /api/quality/summary`.

- [ ] **Step 1: Choose the existing operational view after inspecting current layout**

Preferred location: Terminal, because it already surfaces connectivity/tick health and operational controls. Do not add a seventh top-level tab unless the current frontend has already moved diagnostics into its own view.

- [ ] **Step 2: Add stable DOM targets**

Example IDs:

```html
<div id="system-health-summary"></div>
<div id="system-health-findings"></div>
```

- [ ] **Step 3: Implement renderer**

Display:

- overall status
- tick duration vs poll interval
- dropped messages
- active alert count
- recent fault count
- storage/backup summary
- top 5 warning/error findings

Do not dump raw JSON by default.

- [ ] **Step 4: Reuse the project’s existing polling/refresh conventions**

Do not add a second independent 5s timer if `refresh()` or active-view refresh hooks already provide a place to fetch the panel. Fetch only while the containing view is active if that is the existing density pattern.

- [ ] **Step 5: Add browser test**

In isolated E2E state, assert the System Health panel renders a status and no severe console error occurs.

- [ ] **Step 6: Lint/build/diff**

```bash
cd frontend
npm run lint
npm run build
cd ..
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/js static/index.html static/css/dashboard.css static/js/dashboard.bundle.js tests/test_browser_e2e.py
git commit -m "Surface runtime quality health in the terminal"
```

---

## Task 19: Add synthetic performance regression checks for historically problematic paths

**Files:**
- Create: `tests/perf/` package or a clearly named `tests/test_performance_regressions.py`
- Create: `tools/performance_smoke.py` only if pytest is not a clean fit
- Create: `.github/workflows/performance.yml`

**Interfaces:** scheduled/manual initially; later promotable to PR gate.

- [ ] **Step 1: Select only real historical bottleneck candidates**

At current HEAD, inspect and choose deterministic functions among:

- `trade_analytics.compute_summary` / insights
- advisory recommendation generation
- `series_evaluator.evaluate_pending`
- candidate population summaries
- market-history computations
- config override resolution

Do not benchmark network or live DBs.

- [ ] **Step 2: Build deterministic generated datasets**

Use fixed random seed if randomness is needed. Dataset sizes should be large enough to reveal accidental O(n²)/N+1 behavior without making CI excessive.

- [ ] **Step 3: Count expensive operations where possible**

For known historical classes, operation counts are often more stable than wall time:

- number of SQLite connects
- number of repeated summary computations
- number of client calls in a synthetic batch

Prefer an invariant like “one connection per batch” over “must finish in 17 ms.”

- [ ] **Step 4: Add broad timing ceiling only after operation-count checks**

Example policy: fail only if runtime is both above an absolute ceiling and >2x the recorded baseline. Store baseline metadata in the test/workflow artifact, not as dozens of fragile exact numbers.

- [ ] **Step 5: Run workflow on `workflow_dispatch` + weekly schedule first**

After several stable runs, the owner may choose to move specific high-signal checks into PR CI. Do not make that decision automatically in this task.

- [ ] **Step 6: Commit**

```bash
git add tests/perf tools/performance_smoke.py .github/workflows/performance.yml
git commit -m "Add synthetic performance regression checks"
```

---

## Task 20: Finalize CI topology, artifacts, and failure semantics

**Files:**
- Modify: `.github/workflows/quality.yml`
- Modify: `.github/workflows/tests.yml` only if duplication/trigger cleanup is needed
- Modify: `.github/workflows/kalshi-contract.yml`
- Modify: `.github/workflows/docs-drift-check.yml`
- Modify: `.github/workflows/performance.yml`

**Interfaces:** clearly separated GitHub check names.

- [ ] **Step 1: Verify final job taxonomy**

Target:

```text
tests / pytest
tests / dependency-audit
quality / architecture-audit
quality / frontend-build
quality / frontend-api-contract
quality / browser-e2e
quality / project-manifest
kalshi-contract / fixture-contracts
kalshi-contract / public-api-canary      scheduled/manual
kalshi-docs / content-drift              scheduled/manual
performance / synthetic-regressions      scheduled/manual initially
```

- [ ] **Step 2: Add concurrency cancellation for PR/push workflows**

Match the existing `tests.yml` convention so obsolete runs do not waste minutes.

- [ ] **Step 3: Ensure diagnostic artifacts upload with `if: always()`**

At minimum:

- `quality-audit.json`
- browser server log on E2E failure
- docs drift report
- public canary report
- performance report

- [ ] **Step 4: Ensure no workflow requires secrets for normal PRs**

Public API canary must not expose or require account credentials.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows
git commit -m "Finalize quality-control CI workflows"
```

---

## Task 21: Documentation and agent-operability pass

**Files:**
- Modify: `CLAUDE.md`
- Modify: `ROADMAP.md`
- Modify: `static/status.html`
- Create/update: `services/quality/CHEATSHEET.md`
- Create/update: `services/observability/CHEATSHEET.md`
- Create/update: `services/storage_health/CHEATSHEET.md`
- Create/update: `services/research/CHEATSHEET.md`
- Modify relevant existing CHEATSHEETs if their handoff/diagnostic responsibilities changed

**Interfaces:** future agent starts from stable quality entry points.

- [ ] **Step 1: Add a “start investigations here” section to `CLAUDE.md`**

It should name, in order:

```text
GET /api/quality/summary
GET /api/health/pipeline
GET /api/health/faults
GET /api/observability/summary
GET /api/health/storage
python -m tools.quality_audit ...
```

State that ad hoc scripts should be the exception after these are checked.

- [ ] **Step 2: Codify the investigation-to-guard rule**

Use explicit language:

> When a debugging/audit pass finds a real bug class, decide before closing the work whether the measurement that exposed it belongs in runtime diagnostics, CI, or an existing guard. Record that disposition in the commit/status entry.

- [ ] **Step 3: Document baseline-ratchet semantics**

Future agents must not “fix CI” by blindly adding new real errors to `baseline.json`.

- [ ] **Step 4: Update each new CHEATSHEET**

For each module document:

- owns
- upstream/downstream
- persistence
- hot-path impact
- routes
- failure behavior
- what is deliberately not automated

- [ ] **Step 5: Sync ROADMAP/status**

Use the repo’s established sync skill/process. Keep roadmap forward-looking and status backward-looking.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md ROADMAP.md static/status.html services/*/CHEATSHEET.md
git commit -m "Document the quality control plane"
```

---

## Task 22: Full verification, deliberate fault injection, and rollout review

**Files:** No new files unless verification finds a real bug requiring its own fix/guard commit.

**Interfaces:** final evidence packet.

- [ ] **Step 1: Full backend suite**

```bash
ddev exec -s fastapi python3 -m pytest -q
```

Expected: all collected tests pass; no live-data guard violation.

- [ ] **Step 2: Frontend**

```bash
cd frontend
npm ci
npm run lint
npm run build
cd ..
git diff --exit-code -- static/js/dashboard.bundle.js
```

- [ ] **Step 3: Quality audit**

```bash
ddev exec -s fastapi python3 -m tools.quality_audit \
  --repo-root . \
  --baseline tools/quality_audit/baseline.json \
  --json-out /tmp/quality-audit.json
```

Expected: zero new high-confidence errors.

- [ ] **Step 4: Deliberately prove CI guards in isolated fixtures**

Do not damage real source. Use test fixtures or a temporary copied mini-repo to prove:

- unmounted router fails audit
- unwired `_maybe_*` scheduler fails audit
- live repo DB connect is blocked
- stale frontend route fails contract audit
- changed docs content fails drift comparison
- stale project manifest fails check

- [ ] **Step 5: Browser E2E**

Run locally using the isolated server and Chrome/Selenium. Verify no severe console errors.

- [ ] **Step 6: Live runtime read-only checks**

On the normal DDEV app:

```text
/api/quality/summary
/api/health/pipeline
/api/observability/current
/api/observability/summary
/api/health/storage
/api/research/status
/status
```

Confirm routes respond, historical DBs were not reset, trading mode/gates unchanged, and the app continues normal signal/market processing.

- [ ] **Step 7: Restart verification**

Use a real `ddev restart` once. Confirm:

- observability DB persists
- research checkpoint state persists if enabled
- no surprise immediate heavy task storm
- backup scheduler behavior remains restart-safe
- app health returns cleanly

- [ ] **Step 8: Inspect git diff for accidental data/generated/noise files**

```bash
git status --short
git diff --check
git diff --stat
```

No `data/*.db`, `.env`, `.htpasswd`, temp files, browser profiles, or build artifacts beyond intentionally committed `static/js/dashboard.bundle.js` / `static/project-manifest.json`.

- [ ] **Step 9: Review baseline debt**

For every ID in `tools/quality_audit/baseline.json`, classify:

- legitimate intentional exception
- real debt to add to ROADMAP
- already resolved and removable

Do not leave unexplained IDs.

- [ ] **Step 10: Run CI on the branch/PR and inspect each job separately**

Do not treat aggregate “green” as sufficient; inspect architecture, frontend, contract, and test jobs individually.

- [ ] **Step 11: Final commit only if verification produced documentation/baseline cleanup**

Example:

```bash
git add tools/quality_audit/baseline.json ROADMAP.md static/status.html
git commit -m "Finalize quality control plane verification"
```

- [ ] **Step 12: Request code review before merge**

Use `superpowers:requesting-code-review`. Specifically ask the reviewer to check:

- live-data isolation cannot be bypassed accidentally
- runtime metric capture does not block the hot path
- static audit hard failures are truly high-confidence
- no quality endpoint causes external Kalshi calls unexpectedly
- no route or background task was left unregistered
- CI browser harness cannot touch real config/data
- docs/canary workflows never use trading credentials

---

# Post-Implementation Operating Procedure

Once this plan ships, future Claude sessions should follow this investigation order before writing one-off scripts:

```text
1. GET /api/quality/summary
2. GET /api/health/pipeline
3. GET /api/health/faults
4. GET /api/observability/summary
5. GET /api/health/storage
6. relevant module CHEATSHEET.md
7. tools/quality_audit JSON
8. existing analytics/research report
9. only then create a new temporary probe if the question is still unanswered
```

When a new temporary probe proves useful, the closing step is:

```text
Can this measurement detect the same bug class in the future?
  ├─ yes, live-data dependent  -> runtime service/check
  ├─ yes, deterministic       -> CI guard
  ├─ yes, both                -> pure shared check + runtime/CI adapters
  └─ no                       -> document why it is genuinely one-off
```

That is the durable behavior change this entire initiative is meant to establish.

---

# Plan Self-Review Checklist

Before execution begins, the implementing agent must confirm:

- [ ] No task requires real-trading enablement.
- [ ] No task requires deleting/resetting live historical DBs.
- [ ] Every new stateful module has an explicit test-isolation step.
- [ ] Every new router has an explicit registration step and is covered by the router audit.
- [ ] Every new background scheduler has an explicit call-site/wiring step and is covered by the background audit.
- [ ] Every CI heuristic has severity/confidence semantics and baseline behavior.
- [ ] Browser E2E uses isolated temp state and no external credentials.
- [ ] Kalshi fixtures are docs-derived.
- [ ] Scheduled canary is read-only/public.
- [ ] Runtime observability samples aggregates rather than every message/request.
- [ ] Storage checks never repair/mutate automatically.
- [ ] Research reports never apply config.
- [ ] `status.html` historical narrative is not auto-generated.
- [ ] Existing diagnostics/alerting/backup/task-supervisor systems are extended, not duplicated.
