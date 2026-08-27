# Autonomous Quality Coordination — Production Design Specification (I11)

**Task:** I11 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `6526eee`,
merged forward with `origin/main` @ `09a6abd` (PR #14, the "personal-use, real-money production"
standing-goal directive — CLAUDE.md-only, no conflict with this branch, merge clean). This spec is
completed 2026-08-26, one day after the investigation's own dating convention (`2026-08-25-*`);
per the plan's own instruction ("using the actual completion date if it differs from this
investigation date"), this file is dated `2026-08-26`.

**Architecture under specification:** Candidate D (report-only), as decided in I10
(`docs/superpowers/research/2026-08-25-autonomous-quality-architecture-decision.md`), extended with
exactly one active capability: a persisted, read-only coordinator observation series. **No write
lane, no GitHub credential, no issue/PR authority is specified here** — I10 §2 items 2–4 explicitly
deferred SARIF, issue escalation, and draft-PR remediation to a future decision with its own
re-verification requirement. This is why this specification is markedly shorter than a full A/B/C
write-lane design would be: several of the plan's I11 checklist items are answered "not applicable
under this decision," each with the reason stated, not skipped silently.

---

## Amendment (2026-08-26, post-implementation): the application-coupling this spec
originally designed was wrong and has been reversed

This spec, as originally written and implemented (Tasks 1-9, PR #43), wired the module
into the trading application: a `_maybe_run_quality_coordination` scheduler inside
`main.py`'s own tick loop, a `quality_coordination:` section in `config/settings.yaml`,
and two read routes in `services/quality/routes.py`. Direct user correction, same day:
this was a real misunderstanding of the feature's own name — "Autonomous Quality
**Coordination**" was about coordinating the *engineering workflow's* quality (this
repo's own code health, via `tools.quality_audit`'s static scanners), not the trading
*application*; the word "autonomous" led to conflating it with the app's own
automation (it's an autotrader), which it was never about. Standing instruction: **the
application and this tool must have zero coupling** — no shared execution, no shared
config, no shared code location, no shared test-isolation registry, and no API surface
in either direction except a tool reading a real, intentional endpoint from the app if a
future need ever justifies one (never the reverse — the app must never import, run,
configure, or schedule this tool).

**What actually changed, corrected here rather than left for the reader to infer from
diffing against the original text below:**
- The module lives at `tools/quality_coordination.py`, not `services/quality_coordination.py`
  — `tools/` is this repo's existing home for workflow tooling (`tools/quality_audit/`,
  which this module observes, already lives there).
- There is no `_maybe_run_quality_coordination` scheduler, no tick-loop wiring, and no
  entry in `services/app_state.py`'s `state` dict. The module is invoked only via
  `python -m tools.quality_coordination`, by whatever external process a human chooses
  (manually, a cron entry, a Woodpecker scheduled pipeline) — never by the trading app's
  own process.
- There is no `config/settings.yaml` entry. `enabled`/`interval_sec` don't mean anything
  once there's no in-process scheduler to gate or space out — an external invoker decides
  whether and when this runs at all.
- There are no API routes (§2 below is obsolete) and `GET /api/quality/summary` has no
  `"coordination"` field. A human or a future Claude session inspects
  `tools/quality_coordination_data/quality_coordination.db` directly (its own SQLite
  file, deliberately **not** under the shared `data/` directory the app's backup and
  storage-health mechanisms own) or runs the module's own CLI output — never through the
  trading app's API surface. If a UI is ever wanted, that's a separate, standalone
  concern (e.g. a dashboard reading the tool's db directly), not something built into
  this application.
- `tests/support/runtime_isolation.py`'s `PERSISTENCE_MODULE_PATHS` (the app's own
  test-isolation registry) does **not** list this module — it manages its own test
  isolation directly (every test explicitly monkeypatches its own `DB_PATH`), and
  `tools/quality_audit/persistence.py`'s scanner was corrected to exclude `tools/` from
  that check entirely, for the same reason.
- See `CLAUDE.md`'s "workflow and tooling should never overlap with app code" standing
  rule (added the same day) for the durable version of this principle, and
  `docs/superpowers/plans/2026-08-26-autonomous-quality-coordination.md`'s addendum
  (Task 15) for the full list of changed files.

Sections §1, §2, §6, and §11 below still contain the original, now-superseded design —
struck through in place rather than deleted, per this project's own documentation
convention, with the corrected reality noted alongside each.

---

## 0. Checklist-to-section map (so a reviewer can confirm nothing was silently dropped)

| Plan's I11 checklist item | Where answered | Status |
|---|---|---|
| Exact modules/interfaces/state schema; which QCP types are reused | §1–§2 | Specified |
| Stable automation identity and migration behavior | §3 | Specified |
| Branch/main observation semantics and active-work suppression precedence | §4 | Specified |
| Local advisory tier only if I3 proved it worthwhile | §5 | **N/A** — I3 §9 rejected it on measurement |
| Reporting surfaces per finding class | §6 | Specified (one surface: the persisted series itself) |
| Credential/token/event architecture if any write lane survived I10 | §7 | **N/A** — no write lane; forward-reference only |
| Protected paths/domains and self-modification prevention | §8 | Specified (minimal — no repo-file writes exist to protect against) |
| Deterministic fixer registry if any candidate survived I7 | §9 | **N/A for activation** — I7's one candidate stays unregistered; forward-reference only |
| State retention, idempotence, retry/recovery, outage behavior | §10 | Specified |
| Staged activation and rollback/kill switch | §11 | Specified |
| Self-review for ambiguity/contradictions/placeholders/scope | §12 | Specified |

## 1. Module and interfaces

~~New module: `services/quality_coordination.py`~~ **Corrected (2026-08-26, see the
Amendment above): `tools/quality_coordination.py`** — same persistence idiom
(`CLAUDE.md` "Persistence idiom" section — one file per concern, its own `DB_PATH`,
`_connect()` creates tables `IF NOT EXISTS`), just under `tools/` (workflow tooling) not
`services/` (application code). `DB_PATH` itself also moved:
`tools/quality_coordination_data/quality_coordination.db`, not under the shared `data/`
directory — see the Amendment for why.

```python
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "quality_coordination.db"

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_items (
        automation_key TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        level TEXT NOT NULL,
        first_observed_at TEXT NOT NULL,
        last_observed_at TEXT NOT NULL,
        observation_count INTEGER NOT NULL,
        resolved_at TEXT,
        reopen_count INTEGER NOT NULL DEFAULT 0,
        scope_paths TEXT NOT NULL,
        source_finding_id TEXT NOT NULL,
        source_check TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        automation_key TEXT NOT NULL,
        at TEXT NOT NULL,
        message TEXT NOT NULL,
        FOREIGN KEY(automation_key) REFERENCES coordination_items(automation_key)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        audit_fingerprint TEXT NOT NULL UNIQUE,
        commit_sha TEXT,
        ran_at TEXT NOT NULL,
        items_observed INTEGER NOT NULL,
        items_resolved INTEGER NOT NULL,
        error TEXT
    )""")
    return conn
```

**Reused QCP types, unmodified:** `services.quality.models.QualityFinding` and `QualityReport`
(fields: `finding_id`, `check`, `severity`, `confidence`, `source`, `scope`, `summary`, `evidence`,
`remediation` — read directly, this module adds no field to that dataclass; see §3 for why).

**Audit source — corrected from an earlier draft of this section.** `GET /api/quality/summary`
(`services/quality/routes.py`) does **not** run the static scanner suite at all — its own docstring
states it "deliberately composes only sources that are already local/read-only" (runtime findings
from `observability` + `storage_health`), explicitly excluding anything CI-only. Static findings —
the only class this whole investigation (I1's identity table, I2's replayed episodes) is actually
about — come from `tools.quality_audit.__main__.run_audit(repo_root: Path) -> QualityReport`, which
iterates the real `_SCANNERS` list (9 scanners, the same ones I1 §3.1 enumerated) and is otherwise
only invoked by that module's own CLI (`python -m tools.quality_audit`, run by Woodpecker and by a
human). ~~This module imports and calls that function directly, in-process, on its own schedule
(§1's `_maybe_run_quality_coordination` below) — the only place in the running app that does.~~
**Corrected (2026-08-26): this module calls that function directly too, but never "in-process"
with the trading app — it's a wholly separate process, invoked externally (see the Amendment).**
Cost: I1 §2 measured ~2s on host for the scan itself (CI's ~60s figure is dominated by dependency
install, not the scan) — cheap regardless of who invokes it or how often. **Runtime findings
(`source="runtime"`) are never fed into this coordinator** — I1 §3.2 already classified them
"unsuitable for external defect state by construction," and this design does not revisit that.

**Ported from I8's prototype, not copy-pasted:** the state-machine *policy*
(`tools/quality_coordination_sim/coordinator.py`'s `Coordinator._evaluate`, `_floor_met`,
`_suppressing_signal`, `_deburst_count` — same constants: `FLOOR_HOURS = {"error": 2.0, "warning":
6.0, "info": 6.0}`, `DEBURST_GAP_HOURS = 1.0`, `DEBURST_COUNT = 2`, `STALENESS_IDLE_HOURS = 3.0`,
`STALENESS_HARD_CAP_HOURS = 24.0`) is the *specified behavior*, re-implemented against SQLite
instead of in-memory dicts, with the schema above as its storage, and against real timestamps
(`datetime.now(timezone.utc)`, ISO 8601 strings) instead of I8's synthetic float hours. The
prototype's own tests (`tests/test_quality_coordination_sim.py`,
`tests/test_quality_coordination_when_filter.py`, `tests/test_quality_coordination_write_gate.py`)
are the executable behavior spec for this port — an implementation that passes an equivalent test
suite against the SQLite-backed version satisfies this section. `tools/quality_coordination_sim/`
itself stays in the tree as documentation/proof, explicitly still labeled EXPERIMENTAL — production
code lives only in `services/quality_coordination.py`.

**Entrypoint** (named `observe_main`, not `run_audit`, deliberately — `tools.quality_audit.
__main__.run_audit` already owns that name for the static-scanner call itself, and this module
calls it rather than re-implementing it, so reusing the name here would make every reference in
this document and its future code ambiguous):

```python
def observe_main(repo_root: Path, at: datetime | None = None) -> RunResult:
    """Called once per scheduler tick. Calls tools.quality_audit.__main__.run_audit(repo_root)
    itself (see above) rather than taking a QualityReport as a parameter, and fetches its own
    branch/claim signals (see §4). Idempotent per audit-content fingerprint, not commit SHA
    (see §10)."""
```

**~~Wiring: a new `_maybe_run_quality_coordination()` scheduler in `main.py`, following the exact
shape of this repo's existing `_maybe_*` schedulers (CLAUDE.md's backup-scheduler cold-start bug is
the concrete precedent to avoid — see §10 for how this design avoids the same class of bug). Fires
once per `main`-branch audit opportunity; since Woodpecker has no scheduled `main` audit (I2 §5,
I10 §2 item 3 — deliberately not added), "once per opportunity" means once per app-process
lifecycle tick at a fixed interval (existing scheduler convention, e.g. hourly), not once per git
push — this module runs inside the long-lived `fastapi` process, not in CI.~~**

**Corrected (2026-08-26, see the Amendment above): there is no wiring into `main.py` at all,
and never should be — that was the exact application-coupling this correction exists to
undo.** Invocation is external and manual/scheduled by whatever process a human sets up
(`python -m tools.quality_coordination`), decoupled from both the trading app's process and
from Woodpecker/CI. "Once per opportunity" is now whatever cadence the external invoker
chooses — this spec does not prescribe one.

## 2. Read endpoint

**~~`GET /api/quality/coordination` (new route, `main.py`) returns the current
`coordination_items` table plus each item's `coordination_log` trail, shaped for
`system-health.js`-style consumption. `GET /api/quality/summary` (existing route) gains
one new field: `"coordination": {"escalation_eligible": <count>, "suppressed": <count>,
"observed": <count>}` — a three-number rollup, not the full detail, matching that route's
existing "composite health read" role (CLAUDE.md's "Start investigations here" section).
No write route exists — there is nothing to `POST` to.~~**

**Corrected (2026-08-26, see the Amendment above): this entire section is obsolete. There
is no read endpoint, in either app route.** The trading application exposes nothing about
this tool and imports nothing from it. Inspection is direct: read
`tools/quality_coordination_data/quality_coordination.db` (its own SQLite file) or run
`python -m tools.quality_coordination` for a one-shot JSON report. If a UI is ever
genuinely wanted, it's a separate, standalone concern (e.g. a small dashboard reading the
tool's db directly, per the user's own suggestion) — never a route added back into this
application.

## 3. Automation identity

**Design decision, diverging from I1 §9's literal recommendation, with reason:** I1 §9 recommended
adding an `automation_key` field directly to `QualityFinding` and modifying 16 scanner constructor
sites (5 of them to add missing `evidence` fields: class name for `resource-unclosed`, an isolated
host token for `kalshi-boundary-host`). That is the right design **for a system that uses
`automation_key` as an external deduplication key against GitHub issues** — this system does not,
because no issue-writing stage is activated (I10 §2 item 4). Modifying 16 scanner call sites in
`tools/quality_audit/` — a file this investigation was repeatedly warned is "contested" and
concurrently owned by other initiatives (I0 §3, I3's evidence-rule citation) — for a purely
internal, read-only bookkeeping key is exactly the kind of unjustified invasive change the
evidence rule's remediation-authority section warns against. Instead:

```python
def derive_automation_key(f: QualityFinding) -> str:
    """Computed externally, over QualityFinding's EXISTING fields only. No scanner file is
    modified by this design. Formula: <check>/<rule>|<scope-without-line>|<subject>, per I1 §9,
    with two documented fallbacks where a scanner's current evidence doesn't yet carry a
    location-free subject."""
```

| I1 rule # | subject source today | fallback needed? |
|---|---|---|
| 1,2,3,5,6,8,9,10,11,12 (semantic) | `f.scope` as-is (already location-free by construction) | no |
| 6 api-usage | `f.scope` (`receiver.method`, already aggregate) | no |
| 7 frontend-route-unknown | `f.evidence["raw"]`, normalized (already present) | no |
| 13 kalshi-boundary-sdk-import | `f.evidence["imported"]` (already present) | no |
| 15 kalshi-boundary-legacy-import | `f.evidence["module"]` (already present) | no |
| 16 kalshi-boundary-deprecated-read | `f.evidence["field"]` (already present, just never added to `finding_id` — I1 §9 item 8's scanner bug is orthogonal to this key and stays a separate, later fix) | no |
| **4 resource-unclosed** | `f.scope` + var name (parseable from `f.finding_id`'s own suffix, per I1 §3.1's "finding_id inputs: module, func, var name") — **class name is not yet available anywhere** | **yes — falls back to including `f.finding_id` verbatim** (i.e., stays as location/symbol-sensitive as `finding_id` already is) until a scanner change adds class name to `evidence`, which is out of scope here |
| **14 kalshi-boundary-host** | `f.evidence["snippet"]` only; the isolated host token is not separately available | **yes — falls back to `f.evidence.get("snippet", "")[:80]`** (bounded, not the unbounded raw snippet) until a scanner change isolates the host token, out of scope here |

**Consequence of the two fallbacks:** rules 4 and 14 keep whatever line-shift sensitivity they
already have in `finding_id`/`evidence` today (I1 §6.2: this has never actually caused a
mis-tracked recurrence in this repo's history — it is a proven-latent risk, not an observed one).
If either fires in practice and produces a spurious "reopen," §10's idempotence design already
treats a reopen as a safe, self-correcting event (a new `first_observed_at`, old history retained
in `coordination_log`, never data loss) — this is deliberately a **degrade-gracefully**, not a
**block-until-fixed**, design point.

**Migration:** none required at ship time — `coordination_items` starts empty; the first
`observe_main()` call populates it from whatever `tools.quality_audit.__main__.run_audit()`
currently reports. There is no prior automation_key-keyed state to migrate from.

## 4. Branch/main observation semantics and suppression precedence

Reuses I3 §11's precedence exactly: exact claim → path-overlap (PR or live branch, merged excluded,
staleness-expired excluded) → persistence floor → escalation-eligible. What I8's prototype left as
caller-supplied (`Observation.branches`, `Observation.claims`) must now have a real data source,
specified here for the first time:

- **Claims:** none implemented. I3 §6 measured **zero** real exact-claim coverage in this repo's
  history (no PR has ever carried structured finding-claim metadata) — building a parser for a
  signal with a 0% historical hit rate is deferred, not silently dropped; `derive_claims()` returns
  `[]` unconditionally and is a named extension point, not a missing function.
- **Branch/PR path overlap:** `GET https://api.github.com/repos/thesneakattack/kalshi-whale-poc/pulls?state=open`
  and `GET .../branches` (both public, anonymous, no token required — same pattern this
  investigation itself used throughout via `gh api`/anonymous `curl`), each paired with
  `GET .../compare/main...{branch}` for the changed-path list. Rate limit: 60 req/h unauthenticated
  per GitHub's documented anonymous limit — at this repo's measured cadence (I2 §5: commits arrive
  in bursts, not continuously) an hourly scheduler tick making ≤3 calls per run is far under budget;
  if the limit is ever hit, the run degrades to "no branch signal available" (§10 outage behavior),
  never a crash.
- **Merged-exclusion and staleness:** `branches` response's own data answers "merged?" (a closed,
  merged branch is simply absent from the open-branches list on the next call, matching I3 §11's
  invariant more simply than tracking a `merged` flag) and staleness (`last_commit_at` from the
  compare response, checked against `STALENESS_IDLE_HOURS`/`STALENESS_HARD_CAP_HOURS`).

## 5. Local advisory tier

**Not specified — N/A.** I3 §9 measured a ≤2.3-minute real coverage window for any local Claude
session/worktree registry and rejected building one; I10 did not revisit that finding. If a future
decision reopens this question, it needs its own new measurement, not a retrofit onto this spec.

## 6. Reporting surfaces per finding class

Exactly one surface is activated: the `coordination_items`/`coordination_log` tables
~~via §2's routes~~ **(corrected 2026-08-26: via direct SQLite inspection or the module's
own CLI — §2's routes never existed in the final design; see the Amendment)**. Mapped
against I5 §7's silent-intermediate-state rule (unchanged by this narrower scope — the
*policy* of what state transitions emit anything is identical, only "emit" now means
"persist a row," never "call GitHub"):

| I5 finding class | Surface under this spec |
|---|---|
| Class 1 (source-located static) | Persisted row only; SARIF stays a candidate (I5 §9, I10 §2 item 2) — **not implemented** |
| Class 2 (branch-local regression) | Not tracked by this module at all — `observe_main()` always calls `tools.quality_audit.__main__.run_audit()` against whatever tree its invoker checks out. **Corrected (2026-08-26): the original guarantee here ("the live app process is running from") no longer applies — there is no live app process running this at all.** The guarantee now rests on operational discipline (invoke this against a real `main` checkout, never a feature branch), the same discipline `python -m tools.quality_audit`'s own CLI already depends on, not a structural property of a shared process. |
| Class 3/4 (transient observation/runtime) | Persisted row; no external emission (matches I5's "everything else... never a new comment" rule) |
| Durable actionable defect (would-be issue) | Reaches `ESCALATION_ELIGIBLE` in `coordination_items.state`; **no issue is filed** — a human or a future Claude session inspects the persisted db directly (§2), exactly as this investigation's own I2 task had to reconstruct history by hand before this module existed |

## 7. Credential/token/event architecture

**Not applicable.** No write lane is activated. For a future reader who reaches this section looking
for "what would Candidate C need if activated later": I4 §3's Candidate C sketch and I9's proven
`when_filter.py`/`write_gate.py` mechanics are the complete answer already on record — this spec
does not repeat them, and does not pre-provision any credential, secret, or App registration now.
The only network calls this module makes are the anonymous, unauthenticated GitHub reads in §4.

## 8. Protected paths and self-modification prevention

This module writes to exactly one path: `data/quality_coordination.db`. It has no code path capable
of writing to `.woodpecker/**`, `.github/**`, `tools/quality_audit/**`, `baseline.json`, or any
source file — there is no file-write call anywhere in `services/quality_coordination.py` other than
its own `_connect()`'s SQLite handle. "Self-modification prevention" (I6 T5, veto V7) is satisfied
structurally, the same way I9's `FakeGitHubTransport` had no network dependency: not by a check that
could be bypassed, but by the capability not existing in the module at all. A future write-lane
stage inherits I7's protected-path list (`.claude/rules/autonomous-quality-coordination-evidence.md`
"Remediation authority rule") unchanged — this spec does not weaken or restate it, only confirms
this stage doesn't need it yet.

## 9. Deterministic fixer registry

**Not activated.** I7 proved exactly one candidate (`tools/project_manifest.py --write`,
AUTO_DRAFT_PR-eligible under three wrapper conditions — I7 §3). This spec does not register it
anywhere runnable; `services/quality_coordination.py` has no fixer-invocation code path. If a future
decision activates the draft-PR stage, I7's proof and its three conditions are the registry's first
(and, per I7's own inventory, only) entry — re-stated here as a pointer, not duplicated.

## 10. State retention, idempotence, retry/recovery, outage behavior

- **Retention:** unbounded — `coordination_items`/`coordination_log` follow this repo's "first-class
  asset, not disposable state" rule (`CLAUDE.md`) like every other `data/*.db` file; no TTL, no
  automatic pruning. A future capacity concern is a new, evidenced decision, not a default here.
- **Idempotence, keyed on audit content, not commit SHA or wall-clock time.** An earlier draft of
  this section keyed idempotence on `git rev-parse HEAD`, the way `tools/project_manifest.py`'s
  `_git_head()` does — **wrong for this specific runtime location**, caught in self-review by this
  repo's own recorded incident: `_git_head()` already writes `generated_from_head: null` whenever
  run via `ddev exec -s fastapi`, because **the `fastapi` container image has no `git` binary**
  (`project-manifest-regen-gotcha` — `project_manifest.py` is only ever run on the host or in
  Woodpecker, never inside the live app process this module actually runs in). Depending on a git
  call from inside `services/quality_coordination.py` would silently degrade to "SHA always null,"
  breaking the uniqueness key. Instead: `audit_fingerprint` is a SHA-256 of the sorted
  `(finding_id, severity)` tuples from the `QualityReport` `observe_main()` just computed — content-
  addressed, not provenance-addressed. This is arguably the more correct key regardless of the
  container gap: most commits don't change any finding at all (I2 §6's own replay showed 51 of 54
  first-parent commits produced zero finding-set change), so keying on "did the findings actually
  change" skips more redundant work than keying on "did the commit change" would have. `commit_sha`
  is still attempted best-effort (`git rev-parse HEAD`, 5s timeout, tolerating `None` exactly like
  `_git_head()` already does) and stored for diagnostic/informational value only — it is nullable in
  the schema above and never part of the uniqueness constraint. ~~`observe_main()` checks whether the
  computed `audit_fingerprint` already has a `coordination_runs` row before doing anything else; if
  so, it returns the prior `RunResult` without re-touching `coordination_items` at all. This is the
  direct fix for the cold-start-reload bug class CLAUDE.md already documents for `_maybe_*`
  schedulers (`backup_cold_start_reload_bug`): an in-memory "have I run yet" flag would be wrong
  across an `uvicorn --reload`; a durable, content-keyed table row is not.~~ **Corrected
  (2026-08-26, post-implementation, found by an addendum consolidated review):** the design above
  was wrong in a way self-review at spec time did not catch. Gating `apply_observation` itself on
  the fingerprint check means a finding whose surrounding audit content never changes — i.e. a
  finding that is genuinely, stably persisting — produces the identical fingerprint call after
  call, so the short-circuit fires every time and `apply_observation` (where the persistence floor
  and escalation logic live) never runs again after the first observation. A persisting, unresolved
  problem could then never reach `escalation_eligible`, which is exactly backwards for a
  persistence floor: it defeats the state machine for the one case it exists to handle. The
  original design also froze `latest_run_at()` for the same reason (no new `coordination_runs` row
  ever got written for a stable fingerprint), which reopened the very
  `backup_cold_start_reload_bug` class this section cites as its own motivation — every
  `uvicorn --reload` would see a stale seed and treat the module as newly overdue. **Corrected
  design:** `apply_observation` always runs, unconditionally; the fingerprint is used only to
  decide whether `coordination_runs` gets a fresh `INSERT` (content changed since the immediately
  preceding run) or an in-place `UPDATE` of that same row's `ran_at`/`commit_sha`/counts (content
  unchanged) — preserving the original's only real benefit (bounded run-history growth for static
  content) without gating the state machine on it. The expensive work this section worried about
  saving (`run_audit()`'s scanner pass, the GitHub branch fetch) was never actually saved by the
  short-circuit either way — both already run before the fingerprint is even computable — so
  nothing about the original performance rationale survives scrutiny; only the DB-row-growth
  rationale was real, and the corrected design keeps exactly that part.
- **Retry/recovery:** a crashed or interrupted run leaves `coordination_runs` without a row for that
  SHA (the row is written only after `coordination_items` updates commit inside the same SQLite
  transaction) — the next invocation (whatever external process/cadence triggers it — see the
  Amendment, this is no longer a scheduler tick) naturally retries the same SHA rather than needing
  a separate retry mechanism. `coordination_items` updates and the `coordination_runs` insert happen
  in one `BEGIN`/`COMMIT` block, so a mid-run crash never leaves a partially-updated item.
- **Outage behavior (GitHub read unavailable — rate limit, network, GitHub down):** `observe_main()`
  proceeds using `branches=[]` (no active-work signal available), which per I3 §11's precedence
  degrades to "evaluate on persistence floor alone" — the same accepted-gap behavior §4/I8's
  `test_ambiguous_local_only_work_provides_no_suppression_signal` already documents for local-only
  work, now extended to "GitHub temporarily unreachable." The run still completes, still writes
  `coordination_runs` (with `error` populated), and never raises out of the invocation. A future session
  reading `error` populated on recent rows is the informativeness signal that something degraded —
  matching CLAUDE.md's "does it surface enough of its own behavior" pillar, not a silent failure.

## 11. Staged activation and rollback

**~~Activation: a single boolean, `quality_coordination.enabled` (default `true` once
this ships, following this repo's live-reloadable `config/settings.yaml` convention),
checked once per scheduler tick before `observe_main()` runs at all. Setting it `false`
is the complete kill switch — the module makes zero GitHub calls and zero DB writes when
disabled; no partial-disable state exists because there is only the one capability (§0's
table) to disable.~~**

**Corrected (2026-08-26, see the Amendment above): there is no config flag, and none is
needed.** The kill switch is simply: does anything invoke `python -m
tools.quality_coordination`? If nothing does, nothing runs — no GitHub calls, no DB
writes, by construction, with no flag to flip. If an external scheduler is ever set up
(cron, a Woodpecker pipeline), removing or disabling *that* is the complete kill switch;
this module itself has no notion of being "enabled" or "disabled" to track.

**No further stage is activated by this spec.** I10 §2's rollout note and I11's own §6/§7/§9 already
state SARIF, issue-escalation, and draft-PR are designed-elsewhere-or-not-yet, each gated on a
future decision with its own re-verification requirement (I10 §2 item 4) — this section does not
restate a staged timeline for them, since inventing one now would imply a schedule this investigation
explicitly declined to commit to.

## 12. Self-review — ambiguity, contradictions, placeholders, scope

- **Two real errors caught and fixed during this self-review, not before it — recorded rather than
  quietly smoothed over:** an earlier draft of §1 claimed `GET /api/quality/summary` already
  produces the static-scanner `QualityReport` this module needs; it does not (that route explicitly
  excludes CI-only sources by its own docstring) — fixed by pointing §1 at the real entrypoint,
  `tools.quality_audit.__main__.run_audit()`, and renaming this module's own entrypoint to
  `observe_main` to avoid a name collision. Separately, an earlier draft of §10 keyed run-level
  idempotence on `git rev-parse HEAD` called from inside the live app process — wrong, because the
  `fastapi` container has no `git` binary (a real, previously-recorded incident in this exact repo,
  `project-manifest-regen-gotcha`) and the call would silently return null there, breaking the
  uniqueness key it was meant to provide. Fixed by keying on a content fingerprint of the audit
  result instead, with the SHA kept only as a best-effort diagnostic field. Both are exactly the
  class of error CLAUDE.md's own "Bug pattern to watch for" section warns about — a claim that reads
  as locally plausible from the call site but doesn't match the real backend/environment behavior —
  caught here by tracing each claim back to its actual source rather than the nearest-sounding
  existing route/pattern, per that section's own instruction.
- **Ambiguity check:** every "N/A" section (§5, §7, §9) states the reason and points to the source
  task, rather than leaving a bare "N/A" a future reader has to chase down.
- **Contradiction check:** §3's identity design deliberately diverges from I1 §9's literal
  recommendation; the divergence and its reason are stated inline rather than silently overriding
  I1 without comment, and the two named fallbacks (rules 4, 14) are each traced to a specific missing
  `evidence` field rather than hand-waved.
- **Placeholder check:** every code sketch above (`DB_PATH`, the three `CREATE TABLE` statements,
  `observe_main`'s signature, `derive_automation_key`'s table) names real, checkable file paths, field
  names, and constants already proven in I1/I2/I3/I8's research docs or read directly from
  `services/quality/models.py` this task — nothing here is a `TODO` or an invented API this repo
  doesn't already have an analog for (`_maybe_*` schedulers, `GET /api/quality/summary`,
  `data/*.db` idiom).
- **Scope check against I10:** this spec activates exactly the one capability I10 §2 approved (a
  persisted observation series) and explicitly declines to design implementation detail for anything
  I10 deferred — an implementing agent following this document cannot accidentally build a write
  lane, because no write-capable interface is specified anywhere in it.

---

**Commit for this task:** `docs: specify autonomous quality coordination`.
