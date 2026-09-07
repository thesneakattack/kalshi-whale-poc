# Adversarial Review — run_offline() Cooperative Yield + Elastic Connection Pool

Target: commit `85631d2` on `fix/run-offline-cooperative-yield`, diffed against
`23f12e5` (the PR #420 merge). Independent pass, no memory of how the branch was
built; every load-bearing claim re-derived from source, from the installed
`aiosqlite` 0.22.1, or from a reproduction run in the app container.

**Scope note.** The working tree drifted during this review. At review start
`HEAD` was `85631d2` with a clean tracked tree; by the end the branch carried two
further commits (`6073df4`, `ce9bc66` — a 24h `since_ts` default for
`check_confidence_input_coverage`, unrelated to this review's target). All
findings below are against `85631d2` only. In passing: my first suite run caught
that in-flight work in a red state (`AttributeError: 'Check' object has no
attribute 'headline'` at `tests/test_diagnostics.py:487`); it was corrected to
`c.summary` before my second run. Not a finding against `85631d2`, recorded only
so the timeline is honest.

---

## Summary verdict

**NO-GO.** The `run_offline()` half of this commit is harmless but does not do
what the commit, the two source docstrings, and the new test all say it does —
its mechanism claim ("no yield point between them, so ANY other coroutine
sharing the loop waited behind the whole sequence") is false, and its regression
test cannot fail against the pre-fix code, which I verified by executing the new
test against `23f12e5`'s `run_offline` (it passes; measured 1019–1183 heartbeat
ticks pre-fix against an assertion threshold of `>= 10`). The `_aio_db` half
contains two reproduced correctness defects in the same class: `connection_for()`
can return `None`, and it can return a dead, never-probed connection — both
raising a non-`sqlite3.Error` exception that falls straight through every
caller's honest-degradation branch and surfaces as a 500. That second one is a
direct regression of PR #420's own finding-I1 fix, merged the previous day, and
needs no concurrency at all to hit. The elastic pool's *capacity* behavior is
sound and correctly bounded (verified at 15-way concurrency), and the process-exit
hook, `close_for_current_loop()`, `reset()`, and `_in_flight` bookkeeping are all
correct — so this is a fixable branch, not a wrong idea. But three of the commit's
own load-bearing claims are falsified by measurement, and the module docstring
still describes a fixed-2 design this same commit replaced with a 2–10 elastic
one. It should not merge as it stands.

---

## Findings

### C1 (Critical) — `connection_for()` can return `None`

`services/diagnostics/_aio_db.py:346-385`

The locked fill loop iterates `for i in range(len(pool))`, skipping non-`None`
slots, and then returns `pool[idx]` for a freshly computed round-robin index:

```python
            for i in range(len(pool)):
                if pool[i] is not None:
                    continue
                conn = await aiosqlite.connect(db_path)     # <-- await: preemption point
                ...
                pool[i] = conn
            idx = _round_robin.get(key, 0) % len(pool)
            _round_robin[key] = idx + 1
            return pool[idx]
```

Nothing re-checks that `pool[idx]` is still non-`None`. A concurrent fast-path
caller whose liveness probe fails executes `pool[idx] = None`
(`_aio_db.py:320-321`) with no lock held, so it can null a slot the fill loop has
*already passed* while the fill loop is suspended inside `await
aiosqlite.connect(db_path)`. If the final round-robin index lands on that slot,
the function returns `None` through a signature annotated
`-> aiosqlite.Connection`.

**Failure scenario:** a burst of `GET /api/quality/summary` requests grows a
pool while one earlier caller's `SELECT 1` probe is still in flight against a
connection whose worker thread has broken. The grower returns `None`; the caller
does `conn.execute(...)`; the request 500s with `AttributeError: 'NoneType'
object has no attribute 'execute'`. `AttributeError` is not a `sqlite3.Error`,
so every `except sqlite3.Error` degradation branch in `diagnostics.py` and
`series_watcher.py` misses it — the exact "500 rather than degrade honestly"
shape this module's own docstring (`_aio_db.py:265-273`) says it exists to
prevent.

**Evidence — deterministic reproduction** (`/tmp/repro_none.py` in the fastapi
container; uses only reachable states, no internal counter presetting: three
sequential warm-up calls leave `_round_robin == 1`, two fast-path callers whose
probe raises `sqlite3.ProgrammingError` *after* a real suspension — faithful to a
worker-thread error, which the code explicitly catches — and two more callers to
push `_in_flight` past the pool size):

```
after warm-up: len(pool)=2 round_robin=1
in_flight after E1/E2 parked: 2
grower parked inside connect; pool now: ['FAKE', 'FAKE', 'None'] round_robin= 1
after evictions, pool: ['None', 'None', 'None']
  E1 -> <aiosqlite.core.Connection object at 0x...>
  E2 -> <aiosqlite.core.Connection object at 0x...>
  G1 -> None
  G2 -> <aiosqlite.core.Connection object at 0x...>

RESULT: BUG REPRODUCED - connection_for() returned None for G1
  caller using G1's result raises: AttributeError: 'NoneType' object has no attribute 'execute_fetchall'
  is sqlite3.Error (would degrade honestly)? False
```

The same run confirmed no connection was orphaned and `_in_flight` drained to
`{}`, so this is specifically the return-value defect, not a bookkeeping one.

---

### C2 (Critical) — the self-healing liveness probe is regressed: a dead, unprobed connection is handed back

`services/diagnostics/_aio_db.py:285-296` and `:383-385`

The probe lives only on the fast path, and it probes `pool[idx]` for one
round-robin index. The locked path returns `pool[idx]` for a *second, later*
round-robin read and never probes it. Those two indices are different by
construction (`_round_robin[key] = idx + 1` runs between them), so the connection
that gets verified and the connection that gets returned are routinely not the
same object. The fill loop also skips any slot that is merely non-`None`
(`if pool[i] is not None: continue`) — a dead-but-not-yet-evicted connection is
non-`None`, so it is skipped, left in place, and can then be selected and
returned.

Under PR #420's single-connection design this could not happen: the probed
connection *was* the cached connection, so a failed probe always led to a fresh
connection being created and returned.

**Failure scenario:** more than one pooled connection to a DB file breaks
together (the file replaced underneath, a worker thread lost, any transient
breakage that isn't slot-local). The next caller heals one slot and returns
another, dead one. Callers get `ValueError: Connection closed` — which, as this
module's own comment at `:298-307` explains at length, is *not* a
`sqlite3.Error` and therefore bypasses every degradation branch. The permanent
"unreadable, quietly" degradation the docstring promises to avoid becomes a
recurring 500.

**Evidence — reproduced, and contrasted against the pre-commit module**
(`/tmp/probe_probe2.py` / `/tmp/probe_old.py`):

Post-commit (`85631d2`):
```
A) 1 sequential caller, both slots dead -> returned: DEAD (ValueError: Connection closed; is sqlite3.Error=False)
B) 3 concurrent callers, both slots dead -> returned: ['DEAD (ValueError...)', 'live', 'DEAD (ValueError...)']
C) 1 sequential caller, slot 0 dead -> returned: live
C) next sequential caller            -> returned: live
```

Pre-commit (`23f12e5`, single connection per key), identical scenario:
```
pre-commit cache entry type: Connection
A') 1 sequential caller, the cached conn dead -> returned: live
B') 3 concurrent callers ->                      ['live', 'live', 'live']
```

Note scenario **A**: no concurrency whatsoever. One sequential caller, both
slots dead, and the caller gets a dead connection back. This is not a narrow
race.

A related amplifier: when `_in_flight[key] > len(pool)` every caller takes the
locked path and *no* probe runs at all. Measured at 15-way concurrency against a
capped pool of 10: `liveness probes run: 10/15` — five of fifteen callers
received a connection nobody verified.

**Suggested shape of the fix (both C1 and C2 are the same defect):** choose the
index first, guarantee *that* slot is filled, probe *that* object, and return the
exact object probed — never a slot re-selected after an await. Growing other
slots for capacity is fine; returning an unverified one is not. A
`assert`/`if returned is None` guard would also have turned C1 into a loud
failure instead of an `AttributeError` two frames away.

---

### I1 (Important) — the new `run_offline` yield test is vacuous, and the mechanism it documents is false

`tests/test_diagnostics.py:301-341` (as committed at `85631d2`);
`services/diagnostics/diagnostics.py:753-766`; `services/diagnostics/_aio_db.py:52-62`

The test asserts a concurrent heartbeat ticks `>= 10` during `run_offline()`, and
its docstring states *"Pre-fix this was 0 or 1: the heartbeat's own
asyncio.sleep(0) never got a turn until run_offline's whole unbroken sequence
finished."* Both halves are false.

**Evidence 1 — the committed test passes unchanged against the pre-commit
`run_offline`.** I loaded `23f12e5:services/diagnostics/diagnostics.py` as
`services.diagnostics.diagnostics` via a `-p` plugin (no checkout, no file in the
worktree touched) and ran the new test against it:

```
OLD DIAGNOSTICS LOADED FROM /tmp/old_diagnostics.py
.                                                    [100%]
1 passed, 19 deselected in 0.32s
```

**Evidence 2 — the actual tick counts, same seeded data, three trials each:**

```
PRE-COMMIT 23f12e5: sleep(0) occurrences in run_offline body = 0
    trial 0: ticks=1183 run_offline_wall=20.1ms
    trial 1: ticks=1088 run_offline_wall=14.6ms
    trial 2: ticks=1019 run_offline_wall=16.0ms
POST-COMMIT 85631d2: sleep(0) occurrences in run_offline body = 8
    trial 0: ticks=1061 run_offline_wall=14.1ms
    trial 1: ticks=1072 run_offline_wall=15.0ms
    trial 2: ticks=1178 run_offline_wall=14.9ms
```

Pre-fix was ~1,100 ticks, not 0–1. The test cannot distinguish the two versions
and therefore guards nothing.

**Evidence 3 — the mechanism, from primary source.** `aiosqlite` 0.22.1's
`Connection._execute` (`/usr/local/lib/python3.13/site-packages/aiosqlite/core.py:149-159`)
ends in `return await future`, where the future is resolved from the worker
thread via `future.get_loop().call_soon_threadsafe(set_result, ...)`
(`core.py:65`). Every single aiosqlite call is therefore a genuine suspension
point that returns control to the event loop, as is
`check_confidence_input_coverage`'s `asyncio.to_thread`. `run_offline()` already
yielded the loop roughly a thousand times per call. What it never did — and
still does not — is break up the ~80-90ms of *pure-Python aggregation inside a
single check*, which is where the measured on-loop CPU actually sits; a
`sleep(0)` placed at a check boundary splits only the (tail of check N + head of
check N+1) fragment, not the contiguous block.

The change itself is cheap and mildly beneficial, so this is not a
"revert it" finding. But under CLAUDE.md's never-guess HARD RULE ("a claim ships
with its evidence and with what would falsify it"; "a gap filled with a guess is
read as fact by the next session") the current state is a defect in its own
right: a falsified mechanism is now recorded as measured fact in two source
docstrings, a commit message, and a test docstring, and the accompanying test
gives false assurance that the property is guarded. Either the test must be
rewritten to measure the thing that actually changed (e.g. the longest
contiguous interval the heartbeat observes, not its tick count), or the claims
must be corrected to what is true.

Corollary: the commit's "root cause was two compounding mechanisms" framing is
unsupported for mechanism 1. Whatever stalled `GET /api/state` for minutes, it
was not the absence of yield points, because there was no absence of yield
points.

---

### I2 (Important) — the module docstring documents a design this same commit replaced

`services/diagnostics/_aio_db.py:5-6, 22-32, 38-44`

Four passages, all touched or added by this commit, describe a fixed 2-connection
pool that the same diff replaced with a 2–10 elastic one:

- `:5` — *"`_POOL_SIZE` persistent aiosqlite.Connections per (event loop, db_path) pair"*. `_POOL_SIZE` does not exist. `grep -rn "_POOL_SIZE" services/ tests/` excluding `_MIN_`/`_MAX_` returns only these two docstring lines.
- `:22-27` — *"`_POOL_SIZE = 2`, not higher: ... Raising it further is a new, separately-measured decision, not assumed to also be needed."* The same commit raises it to 10 and, per its own self-review gap 2, does not measure it.
- `:29-32` — *"this module's pool size mirrors but does not need to exceed that sibling's."* It now exceeds it by 5x (see I3 for the sibling's real number).
- `:38-44` — *"1. Concurrency. RESOLVED ... (2 workers per file again, matching `_diagnostics_pool`'s own prior concurrency) ... 2 concurrent callers on a 3rd request still queue."* Describes the intermediate fixed-2 design, not what shipped.

This file's docstring is explicitly the repo's record of *why* these numbers are
what they are — the commit message itself cites it ("its own docstring named
2-per-file as an available escape hatch ... That is the exact condition the
original docstring set as its own trigger"). Leaving a superseded design in it
means the next session inherits a contradiction between the prose and the
constants directly beneath it.

---

### I3 (Important) — the cited precedent for the pool size does not say what the docstring says

`services/diagnostics/_aio_db.py:22-27` claims the size *"matches
services/whalewatchers/_scoring_pool.py's own already-proven 2-per-file
reasoning"*. Re-derived from that file:

```python
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whale-scoring")
_local = threading.local()
...
def cached_read_connection(db_path, schema_init):
    cache = getattr(_local, "connections", None)
```

The cache is **thread-local** across a **4-worker** executor, i.e. up to **4**
connections per file, not 2 — and `git log -S'max_workers=4'` shows that line
landed at `3ff41d4` (2026-09-01) with no `max_workers=2` ever in its history.
That sibling's own docstring further says *"4 workers: this call path normally
needs ~1 concurrently ... not a load-bearing capacity guess"* — the opposite of
"already-proven reasoning" to inherit.

This wording is partly inherited from #420, but this commit restates and leans on
it as justification, which is what makes it in scope here. Under the never-guess
HARD RULE, a justification attributed to a sibling module has to be what that
module actually says.

---

### I4 (Important) — a connection/thread-count increase whose own measurement shows no benefit for the stated problem

`services/diagnostics/_aio_db.py:112-138`; commit message.

CLAUDE.md's data-plane HARD RULE: *"Never change queue capacity,
consumer/connection/thread counts ... because it 'should help': identify the
measured bottleneck and its mechanism first."* The commit message identifies the
bottleneck honestly — *"the dominant cost there is raw per-query/per-row cost at
real table sizes"* — and states the measured result of this very change: *"did
NOT meaningfully reduce total wall time for 5 concurrent calls against real data
(still ~4.5min each)"*. It then raises the per-file connection ceiling from 1 to
10 anyway, reframed as a "guard rail". A capacity increase measured to not help
the problem it targets is the shape the rule names, and the reframing does not
change the measurement.

Footprint, since the docstring quantifies it: five distinct DB files reach this
module (`market_catalog`, `config_performance`, `signal_log`, `paper_broker`,
`series_watcher` — enumerated from every `connection_for(` call site in
`diagnostics.py` and `series_watcher.py`). At `_MAX_POOL_SIZE` that is up to
**50** aiosqlite connections and 50 non-daemon OS threads per event loop, plus a
fresh set per `research.py` throwaway loop until `close_for_current_loop()` runs.
`:131-134`'s *"a handful of threads"* understates that by an order of magnitude.

Not a correctness defect on its own — WAL is enabled on all of these stores by
their writer modules, so extra readers do not contend — but it needs either a
measurement that justifies 10, or an honest restatement of the number as
unmeasured headroom (which the self-review already concedes internally but the
shipped docstring does not).

---

### M1 (Minor) — `_in_flight` measures acquisition concurrency, not usage; round-robin has no checkout

`services/diagnostics/_aio_db.py:275, 386-389, 383-385`

The counter is decremented in the `finally` of `connection_for()` — i.e. the
moment the connection is *handed back*, not when the caller finishes using it. A
coroutine running a 90-second query is not counted as in flight. Growth therefore
systematically undershoots real demand, which is exactly what the commit's own
live numbers show (`config_performance.db` 2→4 and `signal_log.db` 2→3 under
5-way load, neither reaching 5). Relatedly, round-robin hands out an index with
no lease or busy-tracking, so two genuinely concurrent callers can be given the
same connection while other slots sit idle. Growing the pool consequently does
not guarantee a distinct worker per concurrent user — worth saying plainly in the
docstring, since "grows toward however many callers are concurrently asking"
(`:331-338`) reads as though it does.

### M2 (Minor) — above `_MAX_POOL_SIZE`, the fast path is unreachable

`services/diagnostics/_aio_db.py:285`. Once `_in_flight[key] > 10` the guard can
never be satisfied, so every caller takes the loop-wide lock (`_locks` is keyed
by loop, not by key, so this also serializes callers for *other* DB files) and
skips the probe. Measured: 15 concurrent callers → pool size exactly 10, 0
errors, 0 `None`s, `liveness probes run: 10/15`. The cap itself behaves
correctly; the probe gap is the C2 half.

### M3 (Minor) — the return type annotation is now false

`services/diagnostics/_aio_db.py:236-239, 385`: declared `-> aiosqlite.Connection`,
returns `pool[idx]` whose declared element type is `aiosqlite.Connection | None`
(`:119`). `mypy.ini` scopes CI type checking to `services/kalshi/`
(`.woodpecker/quality-architecture-audit.yml:75`), so nothing catches it — but a
checker run over this file would have found C1 statically.

### M4 (Minor) — `reset()` and `close_for_current_loop()` do not clear `_in_flight`

`services/diagnostics/_aio_db.py:392-437`. Balanced today (verified empirically:
`_in_flight` drained to `{}` after every reproduction run), so this is
defence-in-depth rather than a live bug. But `reset()` is the autouse
test-isolation hook (`tests/test_diagnostics.py:36-41`), and a single abandoned
or cancelled `connection_for()` would leak a count that silently inflates the
next test's `target_size`. `_round_robin` is cleared in both; `_in_flight` should
be too.

---

## Confirmed non-issues

Checked against the specific hypotheses raised for this review, and found
correct — recording them so they are not re-litigated:

- **The `all(slot is None ...)` schema-init-failure deletion (`:378-380`) cannot discard another coroutine's filled slots.** All fills happen under the lock, so only the lock holder fills; and a concurrent fast-path evictor cannot null a freshly created connection because of the identity guard `if pool[idx] is conn` (`:320`) — its captured object is always a pre-existing slot, never one the fill loop just created. The `and _connections.get(key) is pool` half additionally prevents deleting a replacement entry.
- **`_close_all_at_process_exit`'s `_all_conns()` handles `None` slots.** `[c for pool in _connections.values() for c in pool if c is not None]` (`:183`) filters correctly; verified end-to-end by letting a process exit with `[None, <Connection>]` cached: `EXIT=0`, no hang, no `AttributeError`.
- **`close_for_current_loop()` and `reset()` iterate the list-of-slots correctly**, skipping `None`, and `reset()` now also clears `_round_robin`.
- **`_in_flight` increment/decrement is balanced.** Exactly two sites: `:275` (before the `try`, so it always pairs) and `:387` (in the `finally`, so cancellation and exceptions both release). No path decrements without incrementing; the counter cannot go negative; the `pop` at `:388-389` has no await between the read and the pop.
- **The pool is correctly bounded.** `target_size = min(_MAX_POOL_SIZE, ...)` plus growth-only-under-lock: 15 concurrent callers produced a pool of exactly 10.
- **`test_connection_for_grows_the_pool_under_real_concurrent_demand` is non-vacuous.** Ran its exact body against a copy of `_aio_db` with elasticity reverted (fast-path guard's `_in_flight` clause dropped, `target_size = _MIN_POOL_SIZE`): `distinct=2 ... assertion 'distinct > MIN' FAILS`, versus `distinct=4 ... PASSES` on the shipped module. This test does its job.
- **`run_offline()`'s new yield points introduce no new interleaving hazard.** `checks` is function-local (`diagnostics.py:767`); the module's only module-level state is the `_OK`/`_WARN`/`_FAIL`/`_UNKNOWN`/`*_PATH` string constants; `series_watcher`'s mutable module state (`_book_buffer`, `_last_book_write`, `_quarantine_cache`) is touched only from fully synchronous functions with no await between read and write. And since aiosqlite already suspends on every call (~1,100 yields per `run_offline`, measured), no interleaving class exists after this commit that did not exist before it.
- **No diagnostics data-completeness or accuracy regression from the pool itself.** All five stores run `journal_mode=WAL` (set by their writer modules), so additional readers neither block nor see different snapshots; each query was its own implicit transaction before and after. The data-plane risk in this commit is C2 converting honest `unknown` degradation into 500s, not the connection count.
- **"Full targeted suite: 101 passed" is consistent.** I measured 102 passing at review end, of which one (`test_confidence_input_coverage_defaults_to_a_24h_window_not_full_history`) is the parent session's later, uncommitted work — 101 at `85631d2`.

---

## Self-review assessment

The self-review's three declared gaps:

1. **"Real per-query cost at production data volumes remains unaddressed."**
   **Confirmed**, and honestly scoped. This is the correct framing and the commit
   message carries it too. No correction needed.
2. **"`_MAX_POOL_SIZE = 10` is not itself measured."** **Confirmed, and
   understated.** The gap is not only that 10 is unmeasured — the shipped
   docstring simultaneously asserts the opposite ("`_POOL_SIZE = 2`, not higher
   ... Raising it further is a new, separately-measured decision"), and
   quantifies the cost as "a handful of threads" when it is up to 50 across five
   DB files per loop. See I2 and I4.
3. **"No test exercises the pool actually hitting `_MAX_POOL_SIZE`."**
   **Confirmed.** I ran that case manually: the cap holds exactly (15 callers →
   pool of 10, zero errors). So the behavior is right but unguarded; the gap is
   accurately stated.

Three of the self-review's *other* claims are falsified:

- **"Verified with a concurrent heartbeat task: 0-1 ticks before, 2.2M ticks
  during 5 real concurrent `run_offline()` calls after"** and **"confirmed FAILS
  against the pre-fix code (this exact assertion, run manually against the
  unmodified diagnostics.py, ticks 0-1)."** **False.** The committed test passes
  against the pre-commit `run_offline`, and the pre-commit tick count in that
  test's own setup is 1019–1183. See I1 for the mechanism and the runs.
- **"Existing safety properties from #420 are untouched in shape, only extended
  to a list ... re-read each one against the original single-connection logic to
  confirm the same guarantee holds per-slot instead of per-key."** **False for
  the liveness probe.** The guarantee does not hold per-slot: the probed index
  and the returned index are different reads of `_round_robin`, and the locked
  path returns without probing at all. See C2, including the pre/post contrast.
- **"No new issues found."** Two reproduced correctness defects (C1, C2), one
  vacuous test (I1), and three documentation-accuracy defects (I2, I3, I4).

The self-review's remaining internal-consistency claims — elastic sizing never
shrinks, `_in_flight` released in a `finally`, the fast path's second condition
is not an off-by-one, growth only under the lock so no double-grow past the cap —
are all **correct**, and I verified the last one empirically.

---

## What I verified, and how

| Claim | Method | Result |
|---|---|---|
| Targeted suite state | `pytest` (7 files) in the fastapi container | 102 passed (101 at `85631d2` + 1 later uncommitted test) |
| `connection_for()` can return `None` | Deterministic repro with a parked `aiosqlite.connect` and probes that fail after a real suspension | Reproduced; caller gets `AttributeError`, not a `sqlite3.Error` |
| Probe/self-healing regression | Dead-pool scenarios, sequential and 3-way, against `85631d2` and `23f12e5` | Post: dead conns returned (`ValueError`). Pre: always healed |
| Probe coverage under burst | 15 concurrent callers, wrapped connections counting `SELECT 1` | 10/15 probed |
| `_MAX_POOL_SIZE` cap | 15 concurrent callers | pool size exactly 10, 0 errors, 0 `None`s |
| Growth test non-vacuity | Same test body against an elasticity-reverted copy of `_aio_db` | Reverted: `distinct=2`, FAILS. Shipped: `distinct=4`, PASSES |
| Yield test non-vacuity | New test run against `23f12e5`'s `diagnostics.py` loaded via a `-p` plugin (no checkout) | **Passes** — test is vacuous |
| Pre/post heartbeat ticks | Standalone race harness, identical seeded data, 3 trials each | Pre 1019–1183, post 1061–1178 |
| aiosqlite suspends on every call | Read `aiosqlite/core.py:65,149-159` in the installed 0.22.1 | `return await future`, resolved via `call_soon_threadsafe` |
| Exit hook vs. a `None` slot | Process exits with `[None, <Connection>]` cached | `EXIT=0`, no hang |
| `_in_flight` balance | Printed after every reproduction run | `{}` — drained cleanly |
| `_POOL_SIZE` exists? | `grep -rn "_POOL_SIZE" services/ tests/` minus `_MIN_`/`_MAX_` | Only the two docstring lines; symbol undefined |
| `_scoring_pool` is 2-per-file? | Read the file; `git log -S'max_workers=4' / -S'max_workers=2'` | Thread-local cache × `max_workers=4`; never 2 |
| WAL on the affected stores | `grep -rn "journal_mode" services/` | WAL set by each store's writer module |
| `run_offline` shared state | Read `run_offline` in full; module-level scan of `diagnostics.py` / `series_watcher.py` | `checks` local; no mutable module state reachable across the new yields |
| mypy scope | `.woodpecker/quality-architecture-audit.yml:75`, `mypy.ini` | Scoped to `services/kalshi/`; this file unchecked |

Reproduction scripts live in the container at `/tmp/repro_none.py`,
`/tmp/repro2.py`, `/tmp/probe_ticks.py`, `/tmp/probe_growth.py`,
`/tmp/probe_probe2.py`, `/tmp/probe_old.py`, with the historical sources at
`/tmp/old_diagnostics.py` and `/tmp/old_aio_db.py` (both from `git show`, no
checkout performed). Nothing under the worktree was modified by this review
except this document.
