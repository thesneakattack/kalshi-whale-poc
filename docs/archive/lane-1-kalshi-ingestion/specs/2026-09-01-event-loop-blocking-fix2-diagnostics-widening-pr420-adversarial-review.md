# PR #420 Adversarial Review — PR-Stage Cycle

Fresh Agent call, no memory of the branch's development. Scoped per CLAUDE.md's
"nothing advances on one pass" stacking requirement: the PR as *submitted* —
integrity of the pushed artifact, honesty of the PR body, real CI, and one
independent re-derivation of the most safety-critical code — rather than a
re-litigation of the branch-level cycle already documented in
`...-pr-self-review.md` / `...-pr-adversarial-review.md` / `...-pr-consolidation.md`.

Reviewed head: `61b45ab85f60f7cc675e1ee5401047869a9f5979`. Merge-base with
`origin/main`: `4493a672ecfdf006a6ac3494eac0c34bc7ee3099` (= current `origin/main`
tip, so the branch is not behind).

---

## Summary verdict

**GO.** The PR as opened is byte-identical to what the branch-level consolidation
approved (`61b45ab`, verified three ways: local `HEAD`, `origin/fix/...`, and the
PR's own `headRefOid`, with an empty `git diff` between local and remote). All
five GitHub-required CI contexts are genuinely green, re-read from the API rather
than from the PR body. I re-derived `_aio_db.py`'s connection-lifecycle logic from
the installed `aiosqlite` 0.22.1 source rather than from the prior reviews'
conclusions, and every load-bearing claim in it holds — the non-daemon thread
mechanism, the `close()` no-op short-circuit, the `ValueError` dead-connection
signal, the cross-loop freedom, the compare-and-swap eviction, and the
schema-init close-before-raise are all correct as written. I re-ran two checked
Test-plan items myself (the 99-test targeted surface, and the Critical finding's
exact interpreter-exit repro) and both pass. Three **Minor** findings and one
bookkeeping item are new to this pass — one empirically reproduced comment
falsehood about the exit hook's own failure mode, one PR-body claim about the
live environment that has gone stale since it was written, and one measured
test-suite resource accumulation — none of which is a correctness regression, a
safety-gate weakening, or a reason to withhold merge. They are recorded below
with reproductions so the next session inherits fact rather than assertion.

---

## PR-integrity checks

### 1. Is the PR the same code the GO verdict covered? — **PASS**

| Check | Result |
|---|---|
| PR `headRefOid` | `61b45ab85f60f7cc675e1ee5401047869a9f5979` |
| Local worktree `git rev-parse HEAD` | `61b45ab85f...` — identical |
| `git rev-parse origin/fix/aiosqlite-diagnostics-widening` | `61b45ab85f...` — identical |
| `git diff origin/fix/aiosqlite-diagnostics-widening HEAD --stat` | empty |
| SHA named in `...-pr-consolidation.md` chain / PR body | `61b45ab` — matches |
| Commit count & order: `git log origin/main..HEAD` vs PR's `commits[]` | 18 commits, identical SHAs in identical order |
| Working tree clean? | Only one untracked file: this cycle's own `...-pr420-self-review.md` (not part of the PR) |

Nothing was silently added, dropped, amended, or reordered between the reviewed
branch tip and the open PR. The 18 commits run `361e8e4` → `61b45ab`, matching the
consolidation doc's narrative sequence (plan → 6 task/lane commits → 2 lane merges
→ Task 6 integration → 3 fix commits → 1 re-review fix → review-artifact commit).

Diff shape independently re-derived: 23 files, +3687/−316. `services/diagnostics/_diagnostics_pool.py`
(−34) and `tests/test_diagnostics_pool.py` (−29) are genuinely deleted as claimed.
No file outside the stated scope is touched; nothing under `services/risk_manager.py`,
`services/kalshi_account_client.py`, `config/settings.yaml`, or any trading/sizing/
calibration path appears in the diff — the safety invariants are untouched.

### 5. GitHub-PR-specific checks — **PASS**

- **Base branch**: `main`. Correct. (`mergeable: MERGEABLE`, `state: OPEN`.)
- **Labels**: `phase:plan`, `phase:implementing`, `phase:verification`. All three
  are real members of the vocabulary `tools/kanban_sync/labels.py` defines
  (lines 54–59: `PHASE_RESEARCH`/`PHASE_SPEC`/`PHASE_PLAN`/`PHASE_IMPLEMENTING`/
  `PHASE_VERIFICATION`/`PHASE_DONE`) — no invented names, which is what
  `.claude/rules/branching-and-ci.md` actually forbids. The rule mandates
  `phase:plan` for a PR carrying a plan doc under `docs/superpowers/`; this PR
  carries `docs/superpowers/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md`,
  so `phase:plan` is required and present. The other two are additive and honest.
- **Binary files**: none. `git diff --numstat origin/main...HEAD` returns numeric
  add/delete counts for all 23 paths (a binary file would show `-`/`-`).
- **Whitespace noise**: `git diff --check origin/main...HEAD` is empty — no
  trailing whitespace, no space-before-tab, no whitespace-only hunks.
- **Unexpected files**: none. Every path is either the plan/spec/review artifact
  set, `requirements.txt`, one of the 6 touched `services/` modules, or one of
  the 7 touched `tests/` files.

### 6. `docs/open-decisions.md` newest entry — **PASS, independently verified**

The `check_series_funnel` double-computation entry is present (added as the last
line of the file; the diff is `2 +/1 -`, the other change being the reworked
`_diagnostics_pool` note on line 39).

Format matches the file's own convention exactly: `- <finding> · <action> ·
<owner> · <date>` with `you (design call)` and `2026-09-01`.

Every source reference in it was checked against the file rather than trusted:

| Claim in the entry | Verified against `services/series_watcher.py` |
|---|---|
| `check_series_funnel` at line 978 | `async def check_series_funnel(...)` at **978** ✅ |
| `reconcile()` at line 985 | `r = await reconcile(series, ...)` at **985** ✅ |
| `funnel()` at line 1044 | `evidence=(await funnel(...))["stages"]` at **1044** ✅ |
| `reconcile` calls both helpers at 756–757 | `_signals_for_series` **756**, `_trades_for_series` **757** ✅ |
| `funnel` calls them at 641 / 645 | `_signals_for_series` **641**, `_trades_for_series` **645** ✅ |
| "Pre-existing, NOT introduced by this branch" | Correct — `origin/main`'s `check_series_funnel` has the identical `evidence=funnel(...)["stages"]` shape ✅ |

The entry correctly characterises this as compute redundancy rather than
event-loop blocking, and correctly explains *why it is more relevant now*
(serialisation onto one shared connection per file) without overstating it as a
regression this PR introduced. Accurate and correctly formatted.

---

## PR body honesty check

**Substantially accurate, with one stale environmental claim and one checkbox
inconsistency.** The body does not overclaim the code: it states the NO-GO, names
the Critical finding and its real mechanism, names the `atexit` vs
`threading._register_atexit` discovery, and lists the deferred Minor findings
rather than burying them. The "Deferred, not blocking" section is honest and
matches the consolidation doc.

**Test plan, verified item by item — not trusted from the checkmarks:**

| Item | Claimed | My independent verification |
|---|---|---|
| `[x]` 99-test targeted surface | 99 passed, pristine | **Re-ran it.** `99 passed, 1 warning in 11.20s`, `EXIT=0`. ✅ True |
| `[x]` Full suite under CI's `-n 4` | 3052+ passed, 0 failed | Not re-run locally (CI is the full-suite owner per CLAUDE.md). **Verified by proxy from the authoritative source**: `ci/woodpecker/pr/tests-pytest` = `success` on this exact SHA. ✅ True |
| `[x]` C1's exact repro reconfirmed clean | interpreter-exit hang gone | **Re-ran it.** `pytest tests/test_diagnostics_routes.py` alone → `7 passed in 0.29s`, `EXIT=0` (the pre-fix behaviour was `EXIT=124`). Also re-ran `test_quality_routes.py` and `test_research.py` in isolation → `EXIT=0` each. ✅ True |
| `[ ]` Post-merge pull + `ddev restart` + live smoke test | not yet done | **Genuinely undone.** PR is open, not merged; the image demonstrably lacks `aiosqlite` (see F2). ✅ Correctly left unchecked |
| `[ ]` `open-decisions.md` follow-up, text says "**done**" | done | **Genuinely done** (verified above) but left `- [ ]`. ⚠️ See F4 |

**Gap found — F2 below**: the body's most prominent claim, the ⚠️ outage warning,
is no longer true of the live environment. It is stated as certainty ("The instant
primary pulls this merge, `main.py`'s module-level import chain hits
`ModuleNotFoundError: aiosqlite`") but `import aiosqlite` succeeds in the running
container today. The claim was true when `requirements.txt`'s own comment recorded
it (commit `76a8e13`, 22:53Z); it went stale before the PR was opened. The
mitigation it prescribes is still correct and still necessary — the error is
over-caution, not under-caution — but under CLAUDE.md's "a claim ships with its
evidence" this needed re-verification at PR time.

Everything else checks out: the "3 parallel lanes merged with zero conflicts"
claim is consistent with commits `49dd164`/`718bc99` being real merge commits; the
`~5.9s/call` and `20.4s wall` numbers are reproduced verbatim in `_aio_db.py`'s
own docstring and the spec, so the body is not inventing figures. The
`docs/next-action.md` staleness the body defers is real and still present
(lines 25 and 185 both still name `_diagnostics_pool.py` as live) — correctly
disclosed rather than hidden.

---

## CI status

Read directly from `gh api repos/thesneakattack/kalshi-whale-poc/commits/61b45ab85f60f7cc675e1ee5401047869a9f5979/status`
and cross-checked with `gh pr checks 420`. Overall combined state: **`success`**.

**All five GitHub-required contexts** (per `.claude/rules/branching-and-ci.md`'s
documented branch protection) are green on this exact SHA:

| Required context | State |
|---|---|
| `ci/woodpecker/pr/tests-pytest` | **success** |
| `ci/woodpecker/pr/tests-dependency-audit` | **success** |
| `ci/woodpecker/pr/quality-architecture-audit` | **success** |
| `ci/woodpecker/pr/quality-browser-e2e` | **success** |
| `ci/woodpecker/pr/kalshi-contract-fixtures` | **success** |

The five parallel `ci/woodpecker/push/*` contexts (pipeline 278) are also all
`success`. Note for the record: on my first read (mid-review) the combined state
was `pending` because `ci/woodpecker/push/tests-pytest` had not finished; it has
since completed `success`. Nothing was ever treated as passing while pending, and
no single green context was read as the whole picture.

`ci/woodpecker/pr/quality-frontend-build` posts no status — expected and correct,
it is path-filtered to `frontend/**` and this PR touches no frontend file. That is
exactly why the branch protection deliberately excludes it.

---

## Fresh code spot-check — `services/diagnostics/_aio_db.py`

I read the file end to end and re-derived every load-bearing claim from the
**installed** `aiosqlite` 0.22.1 in the container
(`/usr/local/lib/python3.13/site-packages/aiosqlite/core.py`), not from the prior
reviews' conclusions.

**Independently confirmed correct:**

- **Non-daemon worker thread.** `core.py:90` — `self._thread = Thread(target=_connection_worker_thread, args=(self._tx,))`,
  no `daemon=True`. The module's central premise holds.
- **Thread exits only on the sentinel.** `_connection_worker_thread` (`core.py:48`)
  loops forever and `break`s only on `if result is _STOP_RUNNING_SENTINEL`, which
  only `stop()`'s `close_and_stop` returns. So an unclosed cached connection *is*
  a permanently-live non-daemon thread, and CPython's `threading._shutdown()` joins
  it. C1 was real.
- **`close()` is a genuine no-op on an already-closed connection.** `core.py:199-202`:
  `if self._connection is None: return`. The comment at `_aio_db.py:234-236` is
  accurate, and the best-effort `await conn.close()` after eviction cannot hang.
- **The `ValueError` dead-connection signal is correctly identified.** `_conn`
  (`core.py:135-139`) raises `ValueError("no active connection")`; `_execute`
  (`core.py:150-153`) raises `ValueError("Connection closed")` when `_running` is
  False. Neither is a `sqlite3.Error`, so the docstring's reasoning about callers'
  `except sqlite3.Error` degradation branches being bypassed is correct — and the
  corrected MRO claim about `sqlite3.ProgrammingError` (that it *is* a
  `sqlite3.Error`) is also correct.
- **Connections are not loop-bound in 0.22.1.** `_execute` builds its future with
  `asyncio.get_event_loop().create_future()` on the *calling* loop, and the worker
  returns results via `future.get_loop().call_soon_threadsafe(...)`. The transport
  is a plain `SimpleQueue` on a plain `Thread`. The I3 correction is right, and the
  exit hook's ability to close connections opened under dead loops follows from it.
- **Schema-init failure handling (`_aio_db.py:244-260`) is correct.** `aiosqlite.connect()`
  has already started the worker thread by the time `schema_init` runs (`__await__`
  does `self._thread.start()` before `_connect()`), so a raising `schema_init` would
  otherwise strand it. `except BaseException` → suppressed `close()` → `raise` is the
  right shape, and suppressing only the *close* preserves the real schema error.
- **Compare-and-swap eviction (`_aio_db.py:230-231`) is correct under interleaving.**
  I walked the three racing orders: (a) two coroutines both probe the same dead
  connection — the first deletes, the second's `is` check fails and correctly
  declines to delete a replacement it did not observe; (b) A evicts, B enters, sees
  `None`, opens and caches a fresh connection, then A takes the lock and finds B's
  connection rather than `None` and returns it — correct; (c) A evicts and reopens
  under the lock while B waits — correct. No path returns a closed connection and
  no path discards a healthy one.
- **`close_for_current_loop()`** — the `list()` snapshot and `pop(key, None)` guard
  are both sound; the loop-object key genuinely makes "close exactly what this loop
  opened" expressible, and `services/research/research.py:208-211`'s `try/finally`
  really does call it on the exception path.
- **No re-entrancy deadlock.** `connection_for` is never called (directly or via
  `schema_init`) while the loop-wide lock is held, so the coarse lock cannot
  self-deadlock.
- **Call-site completeness.** All three production `run_offline()` call sites are
  `await`ed (`services/diagnostics/routes.py:53`, `services/quality/routes.py:92`,
  `services/research/research.py:209`), as are all four `series_watcher` calls in
  `services/diagnostics/routes.py:204-207`. `grep` finds no remaining synchronous
  caller of any converted function anywhere in `services/`, `main.py`, or `tools/`.

**What I found that the prior rounds did not:** one comment falsehood about the
exit hook's own failure mode, reproduced empirically (F1). Details below. I looked
hard at the connection-lifecycle logic specifically and found nothing else beyond
what the prior rounds already fixed — the eviction, the probe, and the schema-init
path are, in my independent judgement, correct as written.

---

## Findings

### F1 — Minor. `_aio_db.py:143-145` states the wrong failure mode for its own exit hook; a verified loop-free fallback exists

**File:** `services/diagnostics/_aio_db.py:143-147`

```python
# Suppressed: this runs during interpreter shutdown, where a raised
# exception is unhelpful noise. A failure here costs a leaked thread
# (the pre-existing behaviour), never a crash on a working exit path.
with contextlib.suppress(Exception):
    asyncio.run(_close_all())
```

The claim "costs a leaked thread ... never a crash" is false. A leaked *non-daemon*
thread is not a benign cost here — it is precisely the C1 hang, as this same file's
own docstring at lines 108-114 explains. If anything inside `asyncio.run(_close_all())`
raises, the suppression swallows it, no connection is closed, and CPython's
non-daemon join blocks forever.

**Reproduced, not argued.** Probe: open one connection through `_aio_db`, then make
the hook's `asyncio.run` raise, then exit.

```
MODE=ok        → opened, connections: 1 / exiting / EXIT=0
MODE=break_run → opened, connections: 1 / exiting / EXIT=124   (timed out at 20s)
```

**A strictly-safer fallback exists and was verified.** `aiosqlite`'s
`Connection.stop()` (`core.py:116-132`) needs no event loop at all — it wraps
`asyncio.get_event_loop().create_future()` in `try/except Exception: future = None`
and puts the stop job on the plain `SimpleQueue` regardless, and the worker breaks
on the sentinel whether or not a future is attached. Registering a `conn.stop()`
sweep as a fallback turns the failing case green:

```
break_run + stop() fallback → EXIT=0
```

**Why this matters beyond tidiness:** finding I3 in the branch-level cycle was
*a false claim in this exact docstring*, and this is the same class of defect
introduced by the fix for it. A future session reading line 145 would conclude the
suppression's downside is bounded and benign, and would be wrong.

**Severity Minor, not blocking:** the trigger (an exception escaping `asyncio.run`
during `threading._shutdown()`) is unlikely, and the code as shipped exits cleanly
in the normal case — verified `EXIT=0`. This is a comment-accuracy defect plus a
missed defence-in-depth, with zero behavioural change today. Recommend correcting
the comment and adding the `stop()` sweep in a follow-up (roughly four lines); not
worth holding the merge.

**Related observation (no action needed):** the hook also has no timeout. If a
worker thread is mid-query when shutdown begins, `await conn.close()` queues behind
it, so shutdown can block for the duration of the in-flight query (measured up to
~20s for `/api/quality/summary`). Bounded, not infinite.

### F2 — Minor. The PR body's ⚠️ outage claim is stale; the running container already has `aiosqlite`

**File:** PR #420 body, "⚠️ Merging this takes the live app down until the container image rebuilds"

The body asserts as certainty: *"The instant primary pulls this merge, `main.py`'s
module-level import chain hits `ModuleNotFoundError: aiosqlite`."* That is not true
of the current environment.

**Verified:**

| Probe | Result |
|---|---|
| `docker exec ddev-kalshi-whale-poc-fastapi python -c "import aiosqlite"` | **succeeds**, `/usr/local/lib/python3.13/site-packages/aiosqlite/__init__.py`, v0.22.1 |
| `docker run --rm --entrypoint python ddev-kalshi-whale-poc-fastapi -c "import aiosqlite"` (fresh container from the **image**) | `ModuleNotFoundError: No module named 'aiosqlite'` |
| `docker diff ddev-kalshi-whale-poc-fastapi \| grep -c usr/local/.../aiosqlite` | **33** paths in the container's writable layer |
| `pip show aiosqlite` → `Required-by:` | empty — not transitive, an explicit runtime install |

So `aiosqlite` was `pip install`ed into the *running container* (the plan's own
Task 1 "pip install used for local iteration", plan line 1234) and is **not** in
the image. Consequences:

1. **The pull would not break the app today** — the reload's `import aiosqlite`
   resolves against the writable layer. The stated immediate outage would not occur.
2. **The rebuild is still genuinely required**, because any container recreation
   discards that layer. The prescribed sequence is therefore still correct.
3. **The recovery mechanism itself checks out** (I verified this rather than
   assuming it, since it is load-bearing): `docker history` shows a `COPY . .`
   layer rebuilt at 21:10:53 by the last `ddev restart`, with the
   `pip install -r requirements-dev.txt` layer cached from 14:33:11 — and
   `requirements-dev.txt` begins `-r requirements.txt`, so changing
   `requirements.txt` invalidates the `COPY requirements.txt requirements-dev.txt ./`
   layer and forces the pip layer to re-run. `ddev restart` post-merge really will
   bake `aiosqlite==0.22.1` into the image.

**Severity Minor:** the error is over-caution. The prescribed action is a superset
of what is actually needed and causes no harm. But it is a stated-as-fact claim
about live state that stopped being true before the PR was opened, which is exactly
what CLAUDE.md's "never guess; verify or falsify" asks to be caught. Recommend
softening the wording (or re-verifying at merge time) so the next session does not
inherit a false premise about how this environment got into its current state.

### F3 — Minor. Measured connection/thread accumulation in a test file lacking the reset fixture

**Files:** `tests/test_quality_routes.py` (no fixture), vs. `tests/test_aio_db.py`,
`tests/test_diagnostics.py`, `tests/test_diagnostics_routes.py`,
`tests/test_main_tick_executor_wiring.py`, `tests/test_series_watcher.py` (all
carry `_reset_aio_db_cache`).

`_reset_aio_db_cache` was added per-file during the fix rounds. `tests/test_quality_routes.py`
reaches the same call graph (`GET /api/quality/summary` → `await diagnostics.run_offline(cfg)`)
and has no such fixture. Measured, not inferred — I ran the file under `pytest.main()`
and inspected the module state afterwards:

```
6 passed
cached connections after suite: 30
live threads: 31
```

Five DB files × six tests, none reclaimed, because `tests/conftest.py` redirects
every persistence module's `DB_PATH` to a fresh `tmp_path` per test and each fresh
path is a new `(loop, path)` cache key.

This does **not** hang — `EXIT=0`, the `threading._register_atexit` hook closes all
31 threads correctly, which is a real independent confirmation that C1's fix works
under load. The defect is that the guard against accumulation is per-file
convention rather than structural: any future test file that reaches this call
graph will silently accumulate ~5 non-daemon threads per test with nothing to catch
it, and the same is true of a growing `test_quality_routes.py`.

**Severity Minor, not blocking:** bounded, non-fatal, and the full suite passes in
CI under `-n 4`. Recommended disposition (CLAUDE.md's investigation-to-guard):
promote `_reset_aio_db_cache` to an `autouse` fixture in `tests/conftest.py` so no
future file can regress, rather than adding a sixth per-file copy.

### F4 — Bookkeeping. Checklist items that are done but left unchecked

`.claude/rules/branching-and-ci.md` requires checking off *what is genuinely done*
in the PR itself, and grepping the linked `docs/superpowers/` document for
checklists. Doing both:

- **PR body Test-plan item 5** is `- [ ]` while its own text reads
  "— **done**, entry already added in this branch." It *is* done (verified in
  section 6 above). A reviewer grepping `- [ ]` reads it as an open gate. Should
  be `- [x]`.
- **Plan doc line 1255, Step 6** ("Record the deferred findings") is unchecked, but
  commit `5449215`'s message states "This closes Task 7 Step 6 part (2), not just
  part (1)", and both parts are verifiably present in `docs/open-decisions.md`.
- **Plan doc line 1251, Step 5** ("Push, open PR, label, merge") is unchecked while
  push, PR-open and labelling are all done; only the merge remains.
- Plan doc Steps 2, 3, 4 (lines 1234, 1238, 1247) are correctly unchecked —
  genuinely not done (Step 2 confirmed undone by F2's image probe).

**Severity: bookkeeping, not a defect.** Both documents err toward *under*-claiming,
which is the safe direction and the one the rule explicitly prefers ("leave a real
gate unchecked rather than pre-checking it"). Worth a one-line fix before merge so
the merged artifacts read truthfully.

---

## Observations (not findings, no action requested)

- **`_lock_for_current_loop()` is loop-wide, not per-`(loop, path)`.** The first
  open of one DB file serialises the first opens of every other file on that loop
  (~4 files per `run_offline()`). Negligible after warmup, and the coarser lock is
  what makes the no-re-entrancy argument trivial. Worth knowing if connection
  churn ever increases.
- **`reset()` (`_aio_db.py:296-304`) does not suppress exceptions.** A raising
  `close()` would abort the sweep and leave `_connections` partially populated for
  the next test. Test-only, and unreachable while the liveness probe keeps the
  cache free of dead connections.
- **`docs/next-action.md` still describes `_diagnostics_pool.py` as live** (lines 25,
  185). Correctly disclosed in the PR body as deferred to the session-end rewrite.

---

## Verdict

**GO — merge.** Head SHA integrity confirmed, base branch and labels correct, no
binaries or whitespace noise, all five required CI contexts genuinely green on this
exact SHA, two checked Test-plan items independently re-executed and true, the
unchecked ones genuinely undone, the `open-decisions.md` entry present and
line-accurate, and the safety-critical connection-lifecycle code independently
re-derived from the installed library source and found correct.

Three Minor findings and one bookkeeping item, none blocking. Suggested handling:
fix F4's checkboxes before `gh pr merge` (seconds, and it makes the merged record
honest); take F1 and F3 as a small follow-up commit — either on this branch before
merge or as a tracked follow-up — with F2 either corrected in the PR body or
re-verified at merge time so the recorded rationale matches the environment's
actual state.
