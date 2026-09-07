# PR-Stage Adversarial Review: PR #441 — Tier 0 Live-Incident Remediation Implementation Plan

Independent review at the PR stage, per CLAUDE.md's "nothing advances on one pass" HARD
RULE and `.claude/rules/branching-and-ci.md`. This is a distinct review cycle from the
artifact-stage self-review/adversarial-review/consolidation already embedded in the PR
diff (`...-plan-review.md`, `...-consolidation.md`) — those documents' own claims are
treated as unverified until re-derived here from primary sources (current repo source,
live probes, git/gh state), never trusted from their own tables or summaries.

No memory of the session that opened this PR. No files were modified. No git
destructive operations were run. No code from the plan was implemented.

## Method (commands run, UTC timestamps)

- `gh pr view 441 --json body,commits,files` and `gh pr diff 441` — full PR metadata and
  diff, saved to scratch files, ~2026-09-03T02:53:51Z.
- Read the final plan document, its adversarial-review document, and its consolidation
  document in full from the pushed worktree
  (`.claude/worktrees/tier0-live-incident-plan/docs/superpowers/plans/`), via `sed -n`,
  `grep -n`, `wc -l`, `awk` range extraction — not the PR diff view, to see the documents
  as a whole with accurate line numbers.
- Read current primary-checkout source for every file the plan proposes to touch:
  `services/diagnostics/routes.py` (full 538 lines), `services/market_history.py`,
  `services/title_cache.py`, `services/market_catalog/market_catalog.py`,
  `services/signal_log.py`, `services/fault_log.py`, `tests/test_diagnostics_routes.py`,
  `tests/test_fault_log.py` — via direct `sed -n`/`grep -n`, not the plan's own quotes.
- `docker exec ddev-kalshi-whale-poc-fastapi python3 --version` → confirmed `Python
  3.13.15` (native `X | None` union-type support at runtime, no `from __future__ import
  annotations` needed — cross-checked that `services/diagnostics/routes.py` already uses
  this syntax three times without that future-import).
- Re-ran Task 8 Step 2's exact read-only `PRAGMA integrity_check` command via `docker
  exec ddev-kalshi-whale-poc-fastapi python3 -c "..."` against both live `.db` files,
  2026-09-03T02:59:23Z–02:59:27Z (uses `mode=ro` URIs, wrapped in `try/except`, no writes
  — the safe, explicitly-permitted read-only check).
- Independently queried `data/fault_log.db` read-only (`mode=ro` URI) for the
  `market_history`/`malformed` fault row's `count`/`last_seen` to cross-check Task 8's
  cited baseline, 2026-09-03T02:59:2xZ.
- `curl -sk -m 90` once each against `GET /api/health/pipeline`
  (2026-09-03T02:59:47Z–02:59:59Z, two probes, no retries beyond the one re-probe for
  precise timing) and `GET /api/health/faults?component=market_history&limit=15`
  (2026-09-03T03:00:10Z), per the task's "don't retry more than once per endpoint"
  constraint.
- `grep -c "^diff --git"` on the raw PR diff and cross-checked against `gh pr view
  --json files` to confirm file count/paths independently of the API's own listing.

## Findings

### F1 — CONFIRMED: exactly 3 files in the diff, all docs, no code/config touched

`gh pr view 441 --json files` and an independent `grep -c "^diff --git"` on the raw
`gh pr diff 441` output both return exactly 3 files, all under
`docs/superpowers/plans/`: the plan
(`2026-09-03-tier0-live-incident-remediation.md`, +1496), its adversarial review
(`...-plan-review.md`, +461), its consolidation (`...-consolidation.md`, +141). No
`config/settings.yaml`, no `.py` file, no test file is in the diff. (The repo's
pre-existing uncommitted `M config/settings.yaml`, visible in `git status` at session
start, is unrelated local state, not part of this PR.)

### F2 — CONFIRMED: task numbering is sequential 1–10, no gaps or duplicates

Ten `### Task N:` headings, N = 1..10, each appearing exactly once, in order (lines 195,
400, 522, 611, 694, 790, 890, 1041, 1160, 1294 of the final plan document).

### F3 — CONFIRMED: no "Task N" cross-reference anywhere points at the wrong task

Every `Task [0-9]` occurrence in the 1496-line document (~90 matches) was enumerated via
`grep -n` and checked in context. Every standalone "Task 6", "Task 7", "Task 8", "Task 9"
reference consistently resolves to its current heading (Task 6 = `fault_log.py`
connection fix, Task 7 = `GET /api/health/faults` event-loop fix, Task 8 = integrity
check, Task 9 = fd visibility) — no instance found where a number still points at what
that number used to mean before the mid-draft insertion of Tasks 6–7 (i.e., no leftover
reference calling the integrity check "Task 6" or fd-visibility "Task 7"). References to
*other* plan documents' own "Task 5" (`docs/archive/lane-1-kalshi-ingestion/plans/2026-09-01-event-loop-blocking-fix1.md`'s
Task 5; "the precedent plan's own 'Task 5: live validation'") are
correctly disambiguated with an explicit "the precedent plan's own" qualifier and are not
self-references. The "9 net new tests" arithmetic in Task 10 (Task 1 nets 1; 4 in Tasks
2–5; 1 in Task 6; 1 in Task 7; 2 in Task 9 = 9) is internally consistent with the final
task structure.

### F4 — FALSIFIED: the "Global Constraints" section was NOT fully corrected after the two-task insertion, contradicting an explicit claim in the consolidation document

This is the most significant finding of this review. The PR body and
`...-consolidation.md` both explicitly claim full-document correction after Tasks 6–7
were inserted. Consolidation's own words (item 3, F4 disposition):

> "Inserting these shifted the former Tasks 6-8 to 8-10; every cross-reference to the old
> numbering across the whole document (**Global Constraints**, and inside Tasks 1, 4, 9,
> and 10) was searched for and corrected — not assumed complete after the first pass..."

Independent re-read of the "Global Constraints" section (plan doc lines 133–192) found
**three stale statements that were not corrected**, all still describing the pre-Task-6/7
(four-module, three-fix) state, contradicted by the plan's own later sections:

1. **Line 136–137**: "No fix for the fd-exhaustion root cause... beyond **Tasks 2-5's
   four confirmed modules** — the audit names **22 more modules**..." Should read "Tasks
   2-6's five confirmed modules... 21 more modules" — the plan's own Architecture section
   item 2 (line 84) says "five confirmed-leaking `_connect()` functions" and explicitly
   lists all five including `fault_log.py`; the self-review's "Scope boundary" paragraph
   (line 1468) says "does not fix the remaining **21** modules"; Task 10's live-validation
   Step 4 (line 1385–1386) says "Tasks 2-6's **five** modules... the remaining **21**
   modules." 26 total − 5 fixed = 21, not 22. The Global Constraints bullet was never
   updated to match.
2. **Line 162**: "`contextlib.closing` is not used for **Tasks 2-5's** fix" — Task 6
   (`fault_log.py`) undergoes the identical `@contextlib.contextmanager` transformation
   (confirmed by reading Task 6 Step 3 directly), so this comparison-of-alternatives note
   logically applies to Tasks 2-6, not just 2-5.
3. **Line 170–171**: "This plan's **three fixes** are each a narrow, mechanical
   application... (`asyncio.wait_for` bounding a gather; a `contextlib.contextmanager`-
   wrapped connection function; a read-only `PRAGMA` check...)" — omits the fourth fix
   (Task 7's `asyncio.to_thread` event-loop-blocking fix). The document's own top-line
   Architecture header, three paragraphs above the affected text (line 61), already says
   "**four** independent fixes" and lists all four including the `GET /api/health/faults`
   fix — so this bullet is inconsistent with the very section it sits inside, not just
   with later sections.

None of these three stale statements corrupts any task's actual diff instructions — Task
6's own body (line 796: "the identical non-closing `_connect()` shape as Tasks 2-5's four
modules") correctly uses "four" in its own local, historically-accurate context (fault_log
being the fifth, added afterward). The defect is confined to the Global Constraints
section's *aggregate* scope description, which was supposed to describe the plan's
overall final footprint and does not. But the specific, load-bearing claim that this
section "was searched for and corrected" is demonstrably false as written.

**Also found, related and minor**: Tasks 2 and 5's own Step 2 instructions ("Confirm it
still matches **this plan's Architecture section's quoted excerpt** before editing")
refer to a code excerpt that does not exist — the Architecture section (plan lines
1–195, before Task 1 begins) contains **zero** code fences (verified: `grep -c '```' `
over that range returns 0). The actual "before" quote for both `_connect()` functions
lives in each task's own Step 3, not the Architecture section. Harmless in practice (the
`sed -n` commands given are correct and an executor would just read current source), but
it is an inaccurate self-reference inside a document whose own adversarial review (F12)
explicitly certified "internal Task-numbering cross-references are consistent" — that
certification predates this specific inaccuracy's existence in the reviewed text (see F9
below) and in any case didn't catch it.

### F5 — CONFIRMED: Task 1's `_bounded()` bug diagnosis is real, the fix resolves it, and Step 5's test would pass against the fixed implementation

**(a) Is the claimed original bug real?** Yes. Python evaluates a function's default
*positional/keyword* argument expressions exactly once, at `def`/`async def` execution
time (module-import time here), and stores the result in the function object's
`__defaults__`. A signature written as `async def _bounded(coro, *, timeout: float =
STORE_PROBE_TIMEOUT_SEC) -> dict:` would freeze `timeout`'s default at whatever
`STORE_PROBE_TIMEOUT_SEC` equals at import time (10.0). A later `monkeypatch.setattr(
routes, "STORE_PROBE_TIMEOUT_SEC", 0.2)` reassigns the **module attribute**, which does
not retroactively change the already-bound default stored on the function object. This is
a standard, well-documented Python gotcha, correctly reasoned in the plan.

**(b) Does the shipped fix resolve it?** Yes. The final Task 1 Step 3 code is:
```python
async def _bounded(coro, *, timeout: float | None = None) -> dict:
    ...
    if timeout is None:
        timeout = STORE_PROBE_TIMEOUT_SEC
    ...
```
Here the default is the sentinel `None` (itself immutable, no staleness possible), and
`STORE_PROBE_TIMEOUT_SEC` is looked up as a **bare global name inside the function body**,
resolved live against the enclosing module's namespace (`services.diagnostics.routes`'s
`__dict__`) every time the line executes. Since `monkeypatch.setattr(routes,
"STORE_PROBE_TIMEOUT_SEC", 0.2)` mutates that same module's `__dict__`, the live lookup
sees the patched value. Every call site added in Step 3's diff (`_bounded(asyncio.
to_thread(...))`) omits the `timeout=` argument, so every call goes through this live-
resolution path — confirmed by reading the actual "after" diff, not assumed.

**(c) Step 5's test traced line by line against the fixed implementation:**
- `monkeypatch.setattr(routes.store_stats, "store_stats", _hangs_for_rejections)` —
  patches the same module object `get_pipeline_health` calls through (confirmed:
  `routes.py` does `from services.diagnostics import store_stats` and calls
  `store_stats.store_stats(...)`, so this is the correct seam).
- `monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC", 0.2)` — live-visible to
  `_bounded()` per (b) above.
- `TestClient(main.app).get("/api/health/pipeline")` drives `get_pipeline_health`, whose
  `specs.values()` includes `("rejections" → candidate_log.DB_PATH, "rejected_candidates",
  "rejected_at")` (confirmed against current `_store_specs()`), so `_hangs_for_rejections`
  is invoked with `table="rejected_candidates"` and takes the `time.sleep(routes.
  STORE_PROBE_TIMEOUT_SEC + 5)` branch — `0.2 + 5 = 5.2s`, read live off the same patched
  attribute, so this sleep duration is correct regardless of the `_bounded` bug's
  presence or absence.
- Inside `_bounded`, `asyncio.wait_for(coro, timeout=0.2)` cancels the *await* after
  0.2s and raises `asyncio.TimeoutError` in `_bounded`'s own frame — this happens well
  before the underlying `time.sleep(5.2)` call in the worker thread finishes, because
  `wait_for`'s timeout is evaluated independently of whether the wrapped `asyncio.
  to_thread` future has actually completed (documented asyncio behavior: cancelling the
  waiting task does not forcibly stop a running executor thread, but it does make `wait_
  for` return/raise on schedule). `_bounded` catches `TimeoutError` and returns
  `{"error": "timed out after 0s"}` (the `{timeout:.0f}s` format rounds 0.2 to "0").
- `asyncio.gather()` does not propagate this internally-caught exception, so it waits for
  the other (fast, real) store probes and completes in on the order of the 0.2s bound
  plus real-probe time, not 5.2s — satisfying `assert elapsed < 5.0`.
- `body["stores"]["rejections"]["error"]` contains "timed out" (substring match against
  `"timed out after 0s"` — passes). `body["stores"]["signals"]` is untouched by the mock
  and returns normally (no `"error"` key) — passes.
- The plan's own "Note on the leftover monkeypatched worker thread" correctly
  acknowledges the abandoned thread keeps running `time.sleep` to completion in the
  background rather than being forcibly killed, and correctly distinguishes this from a
  genuinely-wedged syscall — accurate, not glossed over.

All three sub-claims hold. This finding also independently corroborates
`...-plan-review.md`'s F5 and `...-consolidation.md`'s disposition of it — but re-derived
here from the actual current `services/diagnostics/routes.py` source and Python's
documented default-argument semantics, not trusted from those documents' own text.

### F6 — CONFIRMED: Task 1 Steps 3–4's diffs are byte-exact against current `services/diagnostics/routes.py`

Direct read of `services/diagnostics/routes.py` lines 293–538 (the full remainder of the
file) confirms: the "before" block in Step 3 (`probe_started = time.perf_counter(); ...
results = await asyncio.gather(asyncio.to_thread(_blocking_extras, now), *(asyncio.
to_thread(store_stats.store_stats, ...) for ...))`) is character-for-character identical
to current source. All four `.get(...)`-conversion targets named in Step 4 —
`extras["schedulers"]` (line 397 in current source), `extras["faults_last_24h"]` (line
466), `extras["settlement_edge_buffered"]`/`extras["game_state_buffered"]` (lines
474–475) — exist exactly as named, and no fifth direct-key `extras[...]` read was found
elsewhere in the function. `_store_specs()`'s seven entries (`raw_trades`,
`book_snapshots`, `signals`, `rejections`, `index_ticks`, `settlement_observations`,
`game_states`) match the plan's description exactly.

### F7 — CONFIRMED: all five Task 2–6 `_connect()` diffs are byte-exact against current source; none of the five files already import `contextlib`

| Task | Module | `_connect()` body match | Call-site count claimed | Call-site count actual (grep) | `contextlib` already imported? |
|---|---|---|---|---|---|
| 2 | `services/market_history.py` | exact | 12 (`153,217,234,291,329,341,348,353,380,398,414,418`) | 12, exact line match | No (confirmed, no hit) |
| 3 | `services/title_cache.py` | exact (full 65-line body incl. 2 `CREATE TABLE` + 15 `_add_column_if_missing` calls, honestly disclosed as not fully quoted in the plan itself) | 5 (`139,157,182,220,282`) | 5, exact line match | No |
| 4 | `services/market_catalog/market_catalog.py` | exact (ends `return conn`, confirmed) | 11 (`136,162,208,218,233,362,403,504,566,605,616`) | 11, exact line match | No |
| 5 | `services/signal_log.py` | exact | 21 (`grep -c`) | 21 | No |
| 6 | `services/fault_log.py` | exact (`_connect` lines 58–81) | 4 (`129,153,191,203`) | 4, exact line match | No |

Every module's stated import-ordering instruction ("add `import contextlib` alphabetically
before X") checked against the file's actual current top-level import block and confirmed
correct (`contextlib` sorts before `json`, `sqlite3`, and `time` in every case, matching
each file's real import list). `market_history.py`'s and `signal_log.py`'s
`_scoring_read_connection` functions, named as separate/untouched, were confirmed present
and structurally distinct from `_connect()` at the cited line numbers.

### F8 — CONFIRMED: Task 7's quoted `get_faults()` matches current source verbatim, and `tests/test_diagnostics_routes.py`'s cited convention is real

Task 7 Step 1's quoted block is character-for-character identical to the current
`services/diagnostics/routes.py:489-497`:
```python
@router.get("/api/health/faults")
async def get_faults(limit: int = 50, component: str | None = None, hours: float = 24.0):
    ...
    from services import fault_log as fl
    return {"summary": fl.summary(since_ts=time.time() - hours * 3600),
            "faults": fl.recent(limit=limit, component=component)}
```
`tests/test_diagnostics_routes.py` line 14 is `import asyncio` (exact line match to the
plan's cited `grep -n '^import asyncio' tests/test_diagnostics_routes.py:14`), line 18 is
`from services.diagnostics import routes as diagnostics_routes` (exact match to the
plan's claimed import alias). Lines 201–204 carry the cited convention comment near-
verbatim ("Exercised through the real route functions, same asyncio.run(...) convention
as every other test in this file... no TestClient here - that's test_quality_routes.py's
convention, not this file's" — the plan's quote reformats parens to em-dashes but
preserves the substance exactly). Task 7 Step 2's new test correctly uses `asyncio.run(
diagnostics_routes.get_faults())`, not `TestClient` — matching the file's actual, verified
convention, not a fabricated one.

### F9 — CONFIRMED: Task 6's claims about `tests/test_fault_log.py` are exact

The file exists; it imports `from services import fault_log as fl` (line 14, not
`fault_log`); it has `@pytest.fixture(autouse=True) def _isolated(monkeypatch, tmp_path):
monkeypatch.setattr(fl, "DB_PATH", tmp_path / "fault_log.db")` (lines 18–20), matching the
plan's claim word for word.

### F10 — CONFIRMED: `fault_log.record()`'s signature and Task 9's usage of it are correct

Current `services/fault_log.py:85-87`:
```python
def record(component: str, operation: str, exc: BaseException,
           context: str | None = None, severity: str = "error",
           now: float | None = None) -> bool:
```
Task 9's implementation calls `fault_log.record("fd_budget", "approaching_limit",
RuntimeError(...), severity="warn")` — three positional args matching
`(component, operation, exc)`, one keyword override — valid against the real signature.
Task 9's test unpacks the captured call as `(component, operation, exc), kwargs =
recorded[0]` and asserts `kwargs.get("severity") == "warn"` — traced against the actual
call shape, this unpacking and assertion are correct. Also confirmed:
`services/diagnostics/routes.py` has no module-scope `fault_log` import — the only four
`from services import fault_log...` occurrences are local, inside-function, at lines 161,
173, 332, and 494 exactly as the plan's corrected (post-F10-from-the-artifact-review)
claim states — independently re-grepped, not trusted from the plan's own citation.

### F11 — CONFIRMED (independently re-derived, third reproduction): `market_history.db` is genuinely corrupt; `market_catalog.db` is clean

Re-ran Task 8 Step 2's exact command (read-only `mode=ro` URI, wrapped in
`try/except sqlite3.DatabaseError`) at 2026-09-03T02:59:23Z–02:59:27Z:
```
--- market_history.db ---
RAISED: database disk image is malformed
--- market_catalog.db ---
ok
```
This is a third independent reproduction (after the artifact-review's two, at
02:33:51Z and 02:35:03Z) — same failure, same file, ~24–26 minutes later, no write to the
file in between. Also independently re-queried `data/fault_log.db` (read-only) for the
`market_history`/`malformed` fault row and got `count=45, last_seen=1788381851.5658898`
(unix), which converts to `2026-09-02T20:44:11.57Z` — matching Task 8's cited baseline
exactly, and confirming the count has **still** not grown since the original architecture-
audit reading, consistent with "persistent structural damage," not a live-recurring
transient fault. This is strong, freshly-obtained evidence for the plan's "confirmed,
persistent corruption" conclusion in Task 8 Step 3.

### F12 — MATERIAL OBSERVATION (informational, not a plan defect): both previously-"stuck" routes now respond fast

`GET /api/health/pipeline`: `HTTP 200`, `time_total: 0.189s` (2026-09-03T02:59:59Z).
`GET /api/health/faults?component=market_history&limit=15`: `HTTP 200`, `time_total:
0.036s` (2026-09-03T03:00:10Z). These are the same two routes the plan's Live re-
verification (drafted 02:09–02:15Z) and adversarial review (02:32–02:35Z) found taking
24–28s and ~45.3s respectively, roughly 25–50 minutes earlier. This does not falsify the
plan's underlying code-level findings — the missing per-item timeout in `asyncio.gather()`
(Task 1) and the fully-synchronous SQLite calls with no thread hop (Task 7) are real,
independently confirmed defects in the current source regardless of whether they are
manifesting as a hang right now — but it does mean the "currently stuck" / "live
incident, in progress" framing repeated throughout the plan (and in the PR body) is
already stale relative to the live system's current state at PR-review time. The plan's
own Task 10 Step 1–3 already requires re-running these exact probes live before drawing
conclusions, so this self-corrects at execution time; flagged here per this review's
explicit instruction to note, not reconcile, unless load-bearing. It is not load-bearing:
Tasks 1–9's diffs do not depend on the routes currently hanging, only on the code shape
that made them capable of hanging.

### F13 — CONFIRMED: PR body accurately represents the diff and the review-cycle documents

Cross-checked the PR body's claims against the actual `...-consolidation.md` content: "1
must-fix and 5 should-fix items applied" matches consolidation's own "Must-fix (1/1
applied)" / "Should-fix (5/5 applied)" headers exactly. "Two new tasks (6, 7) added...
market_history.db: confirmed corrupt, reproduced twice; market_catalog.db: confirmed
clean" matches both documents and this review's own independent re-derivation (F11). The
PR body's Test-plan checklist correctly leaves "PR-stage cycle... running next" and "CI
green" **unchecked** rather than pre-checking either — consistent with `.claude/rules/
branching-and-ci.md`'s instruction not to assume an unchecked item executes on its own.
No overclaiming found in the PR body itself. (The one inaccuracy found in this review — F4
— lives in `...-consolidation.md`'s prose, not in the PR body, though the PR body does
repeat consolidation's "every downstream cross-reference re-checked" framing without
qualification.)

## Verdict: **GO-AFTER-FIXES**

The plan's actual engineering content is sound and, on this review's independent,
from-source re-derivation, byte-exact: all ten tasks' code diffs (Tasks 1–2–3–4–5–6–7–9)
match current repository source exactly — line numbers, function bodies, call-site
counts, import orderings — with no fabricated line number, no misquoted function body,
and no test that would fail to compile or fail to pass against the fixed implementation
it targets. Task 1's central claimed bug (Python default-argument early-binding) is real,
correctly diagnosed, correctly fixed, and its regression test was traced line-by-line to
a passing result against the fixed code, not merely judged plausible. Task 8's corruption
finding was independently reproduced a third time, read-only, with the exact cited
command, and the cited fault-log baseline (`count=45`,
`last_seen=2026-09-02T20:44:11.57Z`) was independently re-confirmed unchanged.

What must change before this plan is executed as written:

**Must-fix**
1. **F4** — The "Global Constraints" section (plan lines 133–192) still contains three
   statements describing the pre-Task-6/7 state ("Tasks 2-5's four confirmed modules...
   22 more modules," "for Tasks 2-5's fix," "This plan's three fixes"), directly
   contradicting the plan's own Architecture section, Scope-boundary self-review, and
   Task 10 validation section (all of which correctly say five modules / 21 remaining /
   four fixes). This directly falsifies `...-consolidation.md`'s explicit claim that
   "every cross-reference to the old numbering across the whole document (**Global
   Constraints**, and inside Tasks 1, 4, 9, and 10) was searched for and corrected." Fix
   the three statements in Global Constraints (update "four"→"five", "22"→"21", "three
   fixes"→"four fixes" and name the `GET /api/health/faults` fix alongside the other
   three), or explicitly retract the "searched and corrected" claim in the consolidation
   document if a conscious decision is made to leave prose stale — but not both leave it
   stale and claim it was fixed.

**Should-fix**
2. Tasks 2 and 5's Step 2 instructions ("Confirm it still matches this plan's
   Architecture section's quoted excerpt") reference a code excerpt that does not exist —
   the Architecture section (plan lines 1–195) has zero code fences. Reword to point at
   each task's own Step 3 "before" block, the actual location of the quoted excerpt, or
   simply drop the "Architecture section" clause since the given `sed -n` command is
   already sufficient and correct on its own.
3. Note in the plan (or in `docs/next-action.md` when this plan is picked up for
   execution) that as of 2026-09-03T02:59–03:00Z — after this plan was drafted and
   reviewed but before this PR-stage review — both `GET /api/health/pipeline` (0.19s) and
   `GET /api/health/faults` (0.036s) are responding quickly, not hanging. This doesn't
   change any task's diff, but Task 10's live-validation numbers (24–28s / 45.3s
   baselines) should be understood as the historical trigger evidence, re-measured fresh
   at actual execution time per the plan's own Step 1–3 instructions, not assumed still
   current.

Neither item invalidates any task's code diff or blocks safe execution of the plan
task-by-task; both are documentation-accuracy defects in prose that makes claims about
its own completeness. Given this repo's explicit "never guess; verify or falsify" HARD
RULE and the specific, demonstrable falsity of the consolidation document's "searched and
corrected" claim, this review's verdict is GO-AFTER-FIXES rather than a clean GO — the
fix-list is short and does not require re-opening the artifact-stage review cycle (no new
scope, no new claim introduced), consistent with CLAUDE.md's fix-list-recheck carve-out.
