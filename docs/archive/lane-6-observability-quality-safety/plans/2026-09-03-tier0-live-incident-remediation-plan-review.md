# Adversarial Review: Tier 0 Live-Incident Remediation Implementation Plan

**Reviewer:** independent adversarial pass per CLAUDE.md's "nothing advances
on one pass" HARD RULE — no memory of the session that wrote the artifact.
Every load-bearing claim below was re-derived from primary sources (live
source in the `tier0-live-incident-plan` worktree, the live app, the running
container, and a deterministic standalone repro), not from the plan's own
tables or self-review.

**Artifact reviewed:**
`docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`
(1115 lines, 8 tasks).

**Scope of this review:** correctness of the plan's diagnosis against
current live state, and whether every code diff in the plan would actually
work if applied verbatim to current source — the stated highest-value check
for an implementation plan (as opposed to a research doc).

---

## Method

All commands read-only; nothing in this review touched app state, git state,
or any file under `data/`. Worktree used: `/home/davidf/code/portfolio/
showcase-projects/autotrade/.claude/worktrees/tier0-live-incident-plan`,
confirmed at the tip of `origin/main` (`git diff origin/main --stat` empty
except the untracked plan doc itself) — so all source reads below are
against exactly what the plan would be diffing against.

| Time (UTC) | Command | Purpose |
|---|---|---|
| 2026-09-03T02:29:27Z | `curl -sk -m 30 .../api/state` | Baseline: is the app up at all |
| 02:29:36–02:30:05Z | `curl -sk -m 90 .../api/health/pipeline` (probe 1) | Re-verify plan's "does not return" claim |
| 02:30:29–02:30:52Z | `curl -sk -m 90 -w <timing>` `.../api/health/pipeline` (probe 2, with TTFB breakdown) | Confirm/reproduce, isolate server vs. network time |
| 02:31:40Z | `docker exec` iterating `/proc/[0-9]*/cmdline` for `spawn_main` | Find live worker pid |
| ~02:31:45Z | `ls /proc/8/fd \| wc -l`; `/proc/8/limits`; `stat -c %Y /proc/8` vs `date +%s` | fd count, soft limit, uptime |
| 02:32:00–02:32:46Z | `curl -sk -m 90 .../api/health/faults?component=market_history&limit=15` | Task 6 Step 1's primary probe; also re-verify the plan's "`/api/health/faults` also stuck" claim |
| 02:33:51Z, 02:35:03Z | `docker exec ... python3 -c "sqlite3.connect('file:.../market_history.db?mode=ro', uri=True); PRAGMA integrity_check"` (Task 6 Step 2's exact command, run twice) | Execute the plan's own not-yet-run diagnostic |
| after | same command against `market_catalog.db` (not in the plan's Task 6, run to answer a question the plan leaves open) | Cross-check the sibling file `docs/next-action.md` also names |
| throughout | `sed -n`, `grep -n`, `grep -c`, `awk` against `services/diagnostics/routes.py`, `services/market_history.py`, `services/title_cache.py`, `services/market_catalog/market_catalog.py`, `services/signal_log.py`, `services/fault_log.py`, `tests/test_pipeline_health_cost.py`, `tests/test_market_history.py`, `tests/test_title_cache.py`, `tests/test_market_catalog.py`, `tests/test_signal_log.py` | Re-derive every line number, function body, and call-site count the plan asserts |
| — | standalone Python repro (`/tmp/.../repro_default_arg.py`) reproducing the plan's proposed `_bounded()` helper and a `monkeypatch.setattr`-style reassignment | Deterministically test whether Task 1 Step 5's own regression test would pass |

---

## Findings

### F1 — CONFIRMED: fd leak still cannot explain the pipeline-health route's current behavior

Live worker is pid 8 (same pid the plan's own live re-verification names),
started **2026-09-03T01:46:07Z**, uptime **~45m38s** at review time
(2026-09-03T02:31:45Z). Open fds: **230 / 1024** (22.5%), soft limit
confirmed via `/proc/8/limits` = 1024. The plan's own snapshot (02:09–02:15Z)
recorded 216/1024 at ~26min uptime on the same pid; my reading 20 minutes
later shows slow, continuous growth (216→230), not a restart. This confirms
the plan's Architecture-section claim: fd exhaustion is not, and still is
not, an explanation for `/api/health/pipeline`'s current degraded behavior.

### F2 — OVERSTATED: the plan's core "does not return" claim no longer holds at review time

The plan's Live re-verification section (drafted 02:09–02:15Z) states:
"`GET /api/health/pipeline` still does not return — two probes (90s and 60s
budgets) both got zero response." My two independent probes ~15–20 minutes
later **both returned HTTP 200**:

- Probe 1 (02:29:36Z): `HTTP 200 in 28.144363s`
- Probe 2 (02:30:29Z, with TTFB breakdown): `starttransfer=23.882343
  total=23.882924 http=200` — confirms the ~24s is genuine server think
  time, not network/TLS (`namelookup`/`connect`/`appconnect` all <13ms).

This is not a refutation that the incident is real — the route is still
badly degraded (24–28s for something CLAUDE.md's own docstring in the route
calls out as previously "instant") — but the plan's specific "hangs
indefinitely, zero response" framing is a point-in-time snapshot that had
already changed by the time of independent review, and the plan does not
discuss intermittency as a possibility. Anyone re-running Task 8 Step 3's
literal "confirm it no longer hangs" check might reasonably conclude the
symptom "went away on its own" pre-fix, which would be the wrong
conclusion given F3 below.

### F3 — GAP (load-bearing): Task 1's fix only bounds ~5s of a ~24–28s response

Both probes' response bodies report `stores_probe_ms` of **4653.0** and
**5004.79** respectively — i.e. the `asyncio.gather()` block Task 1 wraps
in `_bounded()` completes in ~5 seconds, well inside even the *unpatched*
`STORE_PROBE_TIMEOUT_SEC = 10.0`. The remaining **~19–23 seconds** of the
measured 24–28s server-side time (confirmed via `time_starttransfer`, not
network) is spent **outside** the specific block Task 1 bounds — in the
rest of `get_pipeline_health`'s body (the in-memory-looking reads:
`strategy_engine.me_gate_stats()`, `mutual_exclusivity.me_pairing_stats()`,
`http_client.rest_latency_snapshot()`, `capture_writer.loss_snapshot()`,
etc.) or in scheduling delay before the handler even starts (e.g. event-loop
or `to_thread` worker-pool contention from other concurrent traffic this
review did not control for).

Consequence: **Task 8 Step 3's stated pass/fail criterion for the whole
plan** — "a response within `STORE_PROBE_TIMEOUT_SEC` (10s) of the slowest
probe, not an indefinite hang" — is likely to fail even after Task 1 ships
correctly, for a reason outside Task 1's fix. The plan should either widen
Task 1's scope to find where the other ~20s goes, or explicitly amend Task
8 Step 3's expected-outcome language to say "the *store-probe* portion
completes in 10s; total route latency is a separate, still-open question,"
rather than presenting one clean number as the whole plan's pass/fail gate.

### F4 — GAP (load-bearing): `/api/health/faults`, which the plan names as affected, is untouched by any task

The plan's Live re-verification section states: "Only `/api/health/pipeline`
(and, per the PR-stage review of the architecture-audit PR,
`/api/health/faults`) is stuck." My probe of exactly the URL Task 6 Step 1
specifies (`/api/health/faults?component=market_history&limit=15`,
02:32:00–02:32:46Z) took **45.3s** (`starttransfer=45.307674,
total=45.316994, http=200`) — slower than `/api/health/pipeline`.

Read `services/diagnostics/routes.py:489-497` directly: `get_faults()` is
an `async def` route with **no `asyncio.to_thread`, no `asyncio.gather`,
no timeout of any kind** — it calls `fl.summary(...)` and `fl.recent(...)`
straight, synchronously, on the event loop. Both internally use
`services/fault_log.py`'s own `_connect()` (`fault_log.py:58`, 4 call sites
at lines 129, 153, 191, 203 — grepped directly) — **a fifth non-closing
`_connect()`** with the identical shape as the four this plan fixes, not
named anywhere in the plan's "four confirmed modules" / "22 more modules"
accounting.

None of this plan's 8 tasks touch `get_faults()` or `fault_log.py`. Given
the plan's own diagnosis names `/api/health/faults` as one of only two
currently-stuck routes, and this route has the *same* "blocking sqlite3 in
an `async def`, no thread hop" defect class the test file's own docstring
(`tests/test_pipeline_health_cost.py:1-17`) describes as previously fixed
once already for `/api/health/pipeline` (issue #210) — leaving it
completely out of scope, with no explicit disclosure the way the plan
explicitly disclosed excluding the other 22 `_connect()` modules, is an
inconsistency in how honestly the plan states its own boundaries.

### F5 — FALSIFIED (deterministic): Task 1 Step 5's own regression test would not pass against the plan's own implementation

This is the most important finding in this review — a concrete bug in the
plan's specified TDD flow, reproduced deterministically, not inferred.

Task 1 Step 3 defines:
```python
async def _bounded(coro, *, timeout: float = STORE_PROBE_TIMEOUT_SEC) -> dict:
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"timed out after {timeout:.0f}s"}
```
`timeout`'s default value `STORE_PROBE_TIMEOUT_SEC` is a **default parameter
expression**, evaluated exactly once — at `async def` execution (module
import) time — and frozen into `_bounded.__kwdefaults__["timeout"]`. Every
production call site (`_bounded(asyncio.to_thread(...))`) omits `timeout=`,
so production always uses whatever `STORE_PROBE_TIMEOUT_SEC` equalled at
import time (10.0) — this part is fine, and matches ordinary production
behavior.

Task 1 Step 5's test does:
```python
monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC", 0.2)
```
This reassigns the **module attribute** `routes.STORE_PROBE_TIMEOUT_SEC`,
which is a completely different object from `_bounded.__kwdefaults__
["timeout"]` — Python does not re-read a default expression when the name
it referenced is later reassigned. `_bounded`'s effective timeout stays
**10.0**, not 0.2.

Deterministic reproduction (not reasoning — actually run):
```python
STORE_PROBE_TIMEOUT_SEC = 10.0
async def _bounded(coro, *, timeout: float = STORE_PROBE_TIMEOUT_SEC) -> dict:
    ...
# later, simulating monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC", 0.2):
STORE_PROBE_TIMEOUT_SEC = 0.2
print(_bounded.__kwdefaults__["timeout"])   # -> 10.0, NOT 0.2
```
Output: `module attr now: 0.2` / `_bounded's actual bound default timeout:
10.0` / a 0.5s-sleeping coroutine passed to `_bounded()` returns normally
(`{'ok': True}`), not a timeout error — confirming the frozen default wins.

Applied to the actual test: `_hangs_for_rejections` reads
`routes.STORE_PROBE_TIMEOUT_SEC + 5` **inside its function body** (a live
lookup, correctly picks up 0.2, sleeps **5.2s**) — but `_bounded`'s real
wait_for bound stays **10.0s** (frozen default, unaffected by the
monkeypatch). Since 5.2s < 10.0s, `time.sleep` completes *before*
`wait_for`'s deadline, so the fake probe proceeds past the sleep and hits
its own `raise AssertionError("should have been cancelled/timed out before
returning")`. That AssertionError is not `asyncio.TimeoutError`, so
`_bounded`'s `except` clause does not catch it; it propagates through
`asyncio.to_thread` → `asyncio.wait_for` → `asyncio.gather()` (gather
propagates the first raised exception without waiting) → out of the
unguarded route handler → Starlette's `TestClient` (default
`raise_server_exceptions=True`) re-raises it to the caller. The test would
**error on `TestClient(main.app).get(...)`**, never reaching any of its own
`assert` statements — not "pass," and not "fail on the intended assertion"
either.

**Must-fix**, one of (not exhaustive): make `_bounded` read
`STORE_PROBE_TIMEOUT_SEC` live inside the function body instead of via a
default parameter (`timeout: float | None = None; ...; timeout = timeout if
timeout is not None else STORE_PROBE_TIMEOUT_SEC`), or have the test
monkeypatch `_bounded.__kwdefaults__["timeout"]` directly, or have the test
pass an explicit override some other way. Task 7's analogous
`FD_BUDGET_WARN_FRACTION` does **not** have this problem — it's read
directly in the function body (`if fd_limit and fd_count / fd_limit >=
FD_BUDGET_WARN_FRACTION:`), not captured as a default argument — confirmed
by direct read of Task 7 Step 2's code, so this defect is scoped to Task 1
only.

### F6 — CONFIRMED (syntax) / GAP (documented failure mode): Task 6's integrity check runs, but the file is genuinely corrupt, not merely "possibly ok"

Ran Task 6 Step 2's exact command, verbatim, twice (02:33:51Z, 02:35:03Z),
against the live `data/market_history.db`:
```
sqlite3.connect('file:/app/data/market_history.db?mode=ro', uri=True)
conn.execute('PRAGMA integrity_check')   # -> raises before returning rows
```
Both runs: `sqlite3.DatabaseError: database disk image is malformed` —
raised while fetching, not returned as a row. This is SQLite's own
corruption-detection exception (distinct from `OperationalError: database
is locked`, which is what a lock-contention/race would produce), and it
reproduced identically twice, ~90 seconds apart, on a live, currently
mounted, actively-written file — this is real, structural, persistent
damage, not the "transient read racing a write" scenario Task 6 Step 3's
"if ok" branch treats as the likely explanation.

The plan's Task 6 Step 2 "Expected output" text only anticipates two shapes:
exactly one row `ok`, or a list of row-based corruption descriptions. It
does not anticipate `PRAGMA integrity_check` itself **raising** rather than
returning rows — which is what actually happens here. The plan's Step 3
decision logic still functionally lands in the right branch (a human
running this would correctly read the exception as "not ok" and stop), but
the written expected-output language should be corrected to say the
check may raise `DatabaseError` directly and that exception text *is* the
verbatim finding to record, not a list of rows.

Corroborating cross-check: `/api/health/faults?component=market_history`
(F4's probe) reports the `malformed` fault's `count=45`,
`last_seen=1788381851.57` → **2026-09-02T20:44:11.57Z**, an exact match to
the plan's own cited audit baseline ("45 occurrences, last seen
2026-09-02 20:44:11 UTC") — the count has not grown since the audit, but
the file is still corrupt right now, ~6 hours later, which itself is
evidence against the "transient" hypothesis (a transient race wouldn't
still fail a clean read-only check hours after the last write-time fault).

### F7 — GAP: `market_catalog.db` is the sibling file `docs/next-action.md` also names for this exact check, but Task 6 skips it

`docs/next-action.md` (read directly, not from the plan's summary) says:
"if it persists, run `PRAGMA integrity_check` (read-only) on
`market_catalog.db` **and** `market_history.db` before touching the config
below." The plan's Goal section cites `docs/next-action.md`'s Tier-0 items
as this plan's own source of scope, yet Task 6 only checks
`market_history.db`. Ran the same read-only check against
`market_catalog.db` myself (safe, explicitly permitted): **`ok`** — clean.
This is useful, independently-obtained evidence that partially resolves
one of the plan's own open items (Task 4's note that "a corrupt or
contended `market_catalog.db` is exactly the kind of file this bug class
makes more likely" for the `markets_watched: 0` mystery — ruled out by this
result, at least for structural corruption). Given the check is a one-line,
zero-risk addition and the plan's own cited source document explicitly asks
for it, Task 6 omitting it is a real, if minor, scope gap.

### F8 — CONFIRMED: all four `_connect()` diagnoses (Tasks 2–5) are byte-exact against current source

Verified directly, not from the plan's own tables:

| Module | `_connect()` lines | Call sites (verified count) | Plan's claimed count | Already imports `contextlib`? |
|---|---|---|---|---|
| `services/market_history.py` | 86–97 | 12 (lines 153,217,234,291,329,341,348,353,380,398,414,418) | 12 | No |
| `services/title_cache.py` | 56–121 | 5 (139,157,182,220,282) | 5 | No |
| `services/market_catalog/market_catalog.py` | 65–109 | 11 (136,162,208,218,233,362,403,504,566,605,616) | 11 | No |
| `services/signal_log.py` | 150–161 | 21 (`grep -c` confirmed) | 21 | No |

Every call site is `with _connect(...) as conn:`; grepping for `_connect(`
minus `with`/`def` lines returns nothing in any of the four files — no
stored reference, no `isinstance` check, confirming the plan's
"call-site-transparent" claim. None of the four bodies contain a branch or
early `return` before the final `return conn`, so wrapping the body in
`@contextlib.contextmanager` + `try: with conn: yield conn; finally:
conn.close()` is a sound, mechanical transformation — traced the
`contextlib.contextmanager.__exit__`/`.throw()` protocol by hand for the
exception-inside-`with`-block case and confirmed it correctly re-propagates
the caller's original exception after closing, matching the "same behavior,
now also closes" claim.

`title_cache.py`'s and `market_catalog.py`'s `_connect()` bodies are
confirmed genuinely longer than what the plan's own Architecture-section
excerpt transcribes (title_cache.py: two inline `CREATE TABLE` blocks plus
14 `_add_column_if_missing` calls; market_catalog.py: two `CREATE TABLE`
blocks — `markets` and `series_scan_state` — plus two `CREATE INDEX`
statements, not the single "an inline CREATE TABLE" the Files section
describes). This doesn't break anything: both tasks' Step 2 correctly
instructs reading the real current body before editing rather than trusting
the plan's own partial transcription, so the imprecise summary text is
cosmetic, not load-bearing.

Import-insertion line claims all confirmed exact: `market_history.py:25`
(currently `import sqlite3`), `title_cache.py:37` (`import json`),
`market_catalog.py:33` (`import sqlite3`), `signal_log.py:24` (`import
json`) — `contextlib` alphabetically precedes all four correctly.

### F9 — CONFIRMED: `fault_log.record()`'s signature matches Task 7's claim exactly

`services/fault_log.py:85-87`:
```python
def record(component: str, operation: str, exc: BaseException,
           context: str | None = None, severity: str = "error",
           now: float | None = None) -> bool:
```
Exact match to the plan's claim, including the line numbers. Task 7's
proposed call (`fault_log.record("fd_budget", "approaching_limit",
RuntimeError(...), severity="warn")`) is valid against this signature.
`record_message` (a name the plan explicitly disclaims) does not exist
anywhere in the repo — confirmed via `grep -rn`.

### F10 — Minor inaccuracy: Task 7's own grep-count claim is off by one

Task 7's "Why the test patches..." note claims `grep -n 'from services
import fault_log' services/diagnostics/routes.py` "returns only local,
inside-function imports at lines 161, 173, 332." Running that exact grep
returns a **fourth** line, 494 (`from services import fault_log as fl`,
inside `get_faults()`). This doesn't change the conclusion — line 494 is
also local/inside-function, so "no module-scope import" still holds — but
the specific claim as written ("lines 161, 173, 332") is factually
incomplete against what the grep it cites actually returns.

### F11 — CONFIRMED: Task 1's Files-section line range is imprecise, but the operative diff instructions are exact

The plan's Task 1 Files section says "Modify: `services/diagnostics/
routes.py:293-465`." Line 293 is actually `_price_staleness` (an unrelated
function); `_blocking_extras`/`get_pipeline_health` actually span
~328–486, past the stated 465. However, Step 2's specific `sed -n
'314,331p'` and `sed -n '342,382p'` ranges, and Step 3's literal
before/after code blocks (including the exact `results = await
asyncio.gather(...)` block), are all **byte-exact** against current source
— confirmed by direct `sed -n` reads. The four `extras[...]` direct-key
accesses Step 4 names (`extras["schedulers"]` at line 397,
`extras["faults_last_24h"]` at 466, `extras["settlement_edge_buffered"]`/
`extras["game_state_buffered"]` at 474-475) are also confirmed exact and
complete — no fifth direct-key access was missed. The summary line range
is cosmetic; the actual instructions an executor would follow are correct.

### F12 — CONFIRMED: internal Task-numbering cross-references are consistent; Task 8's test-count arithmetic checks out

Manually audited every "Task [0-9]" occurrence in the document (~55
matches) against the final 8-task structure. All resolve correctly to the
task they reference. The only two instances that look like stale
references ("Task 2's four confirmed modules," "Task 3's integrity check,"
at lines 1094/1097) are, on reading in context, deliberately quoted as
**historical, already-corrected** text inside the plan's own "Gap found in
this self-review" paragraph — not live stale references. No additional
stale cross-reference found beyond what the plan's self-review already
disclosed.

Task 8 Step 1's "7 net new tests" claim: counted directly from the task
steps — Task 1 nets 1 (Step 1's `pytest.skip` placeholder is textually
replaced by Step 5's real assertion, confirmed by Step 5's own "Replace
Step 1's placeholder test... with:" instruction), Tasks 2–5 each add
exactly 1 (`test_connect_closes_its_connection`, one per module — 4 total),
Task 7 adds exactly 2
(`test_pipeline_health_reports_open_fd_count`,
`test_fd_budget_fault_fires_past_80_percent`). 1+4+2 = 7. Arithmetic
confirmed correct.

### F13 — CONFIRMED (with minor inconsistency): test-fixture conventions match, though not all new tests reuse the existing helper as claimed

`tests/test_market_history.py:6-9` has `_mh(tmp_path, monkeypatch)`;
`tests/test_title_cache.py:4-6` has `_tc(tmp_path, monkeypatch)` (returns
the module); `tests/test_market_catalog.py:6` has `_mc`; `tests/
test_signal_log.py:8` has `_log`. All four confirmed present, matching the
plan's "check its existing fixture helper first" instruction. All four
modules import `sqlite3` at module scope, so `mh.sqlite3.connect`,
`tc.sqlite3.connect`, `mc.sqlite3.connect`, `sl.sqlite3.connect` are all
valid `monkeypatch.setattr` targets, matching what each new test assumes.

Minor: only Task 3's new test actually **calls** `_tc(tmp_path,
monkeypatch)`; Tasks 2, 4, and 5's new tests inline the equivalent
`monkeypatch.setattr(module, "DB_PATH", ...)` line instead of calling
`_mh`/`_mc`/`_log`. Functionally equivalent (Task 2's `_mh` additionally
clears `mh._last_ticker_snapshot`, irrelevant to a test that only does
`_connect()` + `SELECT 1`), but not a literal reuse of "its existing
fixture helper" the way the plan's own framing implies for all four.

### F14 — CONFIRMED: Task 1's change does not break the existing `test_pipeline_health_runs_store_probes_off_the_event_loop`

That test (untouched by this plan) monkeypatches `routes.store_stats.
store_stats` with a probe that asserts `asyncio.get_running_loop()` fails
inside it. Task 1 still routes every probe through `asyncio.to_thread(...)`
— `_bounded()` only adds an `asyncio.wait_for()` around the *coroutine*,
it does not change which thread executes the wrapped call — so the probe
still runs off the event loop and the test's assertion (`on_loop == []`)
still holds. `time` is already imported at `tests/test_pipeline_
health_cost.py:21`, confirming Step 5's new test's `time.sleep`/`time.
perf_counter()` usage needs no new import.

---

## Verdict: **GO-AFTER-FIXES**

Tasks 2–5 (the bulk of the plan — closing four confirmed live fd leaks) are
independently sound: every line number, function body, and call-site count
was re-derived from current source and matches exactly; the
`@contextlib.contextmanager` transformation is a verified-safe, call-site-
transparent change for all four. Task 7 is sound (implementation and test
both). Task 6's mechanism works (the exact command runs, read-only,
successfully) and, run for real during this review, surfaces a genuine,
reproducible, non-transient corruption finding on `market_history.db` that
the plan itself had not yet actually executed. None of this needs to be
blocked.

What must change before this plan is executed as written:

**Must-fix**
1. **F5** — Task 1 Step 5's regression test would error, not pass, against
   the plan's own proposed `_bounded()` implementation, because of Python's
   default-argument early-binding (deterministically reproduced). Fix
   `_bounded`'s timeout resolution (read the module constant live inside
   the function body) or restructure the test's monkeypatch strategy before
   Task 1 is considered done.

**Should-fix**
2. **F3** — Task 8 Step 3's pass/fail criterion ("responds within 10s")
   is likely to fail even after a correct Task 1 deploy, because live
   evidence shows only ~5s of the route's ~24–28s total latency is inside
   the block Task 1 bounds. Either broaden the investigation to the
   remaining ~20s or rewrite Step 3's expected-outcome language to not
   conflate "store probes bounded" with "route is fast."
3. **F4** — `/api/health/faults`, named by the plan itself as one of only
   two currently-stuck routes, is untouched by any task, and has the same
   blocking-sqlite3-on-the-event-loop defect class as the pre-fix
   `/api/health/pipeline`, plus its own fifth non-closing `_connect()`
   (`services/fault_log.py`). Either bring it into this plan's scope or
   give it the same explicit, honest scope-exclusion treatment the plan
   gives the other 22 `_connect()` modules.
4. **F6** — Task 6 Step 2/3's "Expected output" language should explicitly
   cover the case actually observed live: `PRAGMA integrity_check` can
   raise `sqlite3.DatabaseError` while fetching, rather than returning
   corruption-description rows.
5. **F7** — Task 6 should also run the same one-line, read-only check
   against `market_catalog.db`, since `docs/next-action.md` — the plan's
   own cited Tier-0 source — explicitly asks for both files, and the check
   is free. (This review already obtained that result: `ok`, clean.)
6. **F10** — Correct Task 7's grep-count claim (161, 173, 332, **and 494**).

**Nice-to-have**
7. **F11** — Tighten Task 1's Files-section line range (293-465 →
   ~314-486) for future readers; the operative `sed`/diff instructions
   are already correct and don't need to change.
8. **F8** — Task 4's Files-section description of market_catalog.py's
   `_connect()` body ("an inline CREATE TABLE") undercounts — it's two
   `CREATE TABLE` + two `CREATE INDEX` statements. Cosmetic; Step 2 already
   instructs reading the real body before editing.
9. **F13** — For consistency with the plan's own stated intent, have
   Tasks 2, 4, 5's new tests call their modules' existing `_mh`/`_mc`/`_log`
   fixture helpers the way Task 3's test calls `_tc`, rather than inlining
   an equivalent `monkeypatch.setattr`.

None of the above requires re-running the earlier research/design stages —
these are implementation-plan-level corrections, most concentrated in Task
1 and Task 6, that a fix-list recheck (per CLAUDE.md's "nothing advances on
one pass" HARD RULE) can resolve without re-opening the whole cycle. Tasks
2, 3, 4, 5, and 7 can proceed as written; Task 1's code is sound but its
test needs the fix in item 1 before Task 1 is "done" by this repo's own
TDD standard, and Task 6/Task 8 need their written expectations brought in
line with what this review found actually happens live.
