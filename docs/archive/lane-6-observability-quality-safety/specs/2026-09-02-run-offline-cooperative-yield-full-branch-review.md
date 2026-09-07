# Full-Branch Adversarial Review — `fix/run-offline-cooperative-yield`

Independent, memory-less pass over the complete branch diff `23f12e5..HEAD`
(`8497032`), per CLAUDE.md's "nothing advances on one pass" HARD RULE. Every
load-bearing claim below was re-derived from source, from the installed
library, or from a measurement I ran myself; nothing is taken from the three
prior review documents on this branch.

Scope: `85631d2` (elastic pool + yield points), `6073df4`/`ce9bc66` (24h
query bound — never independently reviewed before now), `a9d06b2` (C1/C2
fix), `8497032` (consolidation doc).

---

## Summary verdict

**NO-GO.**

The two Critical findings from round 1 (C1, C2) are genuinely fixed — I
reproduced both against `85631d2` and confirmed both regression tests fail
there and pass at HEAD. The 24h query bound is a real, worthwhile
improvement I independently measured against the live 127,285-row
`signal_log.db`. But the branch's *headline* change — the elastic connection
pool — is refuted by direct measurement: on the real production workload it
makes the incident's own metric **worse than the pre-branch code it
replaces**. Swapping only `_aio_db.py` and holding everything else constant,
5 concurrent `run_offline()` calls against real data show worst-case
event-loop starvation of 168–216 ms with the pre-branch single-connection
design versus 231–1939 ms with this branch's pool, and wall time of
15.0–16.4 s versus 26.5–28.0 s — reproduced 4/4 runs each, with
non-overlapping ranges. The mechanism is a fast-path thundering herd
introduced *by the C1 fix itself*: because `_round_robin` now advances only
after a successful probe, every caller that arrives while an earlier
caller's probe is in flight reads the same index and is handed the same
connection (measured: 5 concurrent callers → 1 distinct connection, 10 → 1).
Round 1's own M1 predicted exactly this and the consolidation dismissed it
as "moot"; round 1's I4 raised the capacity objection and the consolidation
downgraded it to a docstring edit that was then not made. The
`check_confidence_input_coverage` change is separately sound in mechanism
but ships an undisclosed ~5x shift in a displayed, persisted diagnostic
value. Recommendation: **keep `6073df4` (with the two accuracy fixes below),
revert or redesign the pool.**

---

## Round 1 findings — confirmed fixed or refuted

### C1 (Critical) — `connection_for()` returns `None`/an unvalidated connection under concurrency → **FIXED**

Independently verified, not accepted on the commit message.

I walked the current `connection_for()` (`services/diagnostics/_aio_db.py:307-453`)
against the invariant the docstring claims. It holds:

- The fast path (`:318-363`) advances `_round_robin[key]` at `:334` **only**
  after `await conn.execute_fetchall("SELECT 1")` returns, and mutates
  nothing on the failure branch (`:363` is a bare `pass`). Compare
  `85631d2`, where the advance was at line 52 of the function — *before*
  the probe.
- The locked section (`:364-449`) reads `idx` at `:391`, and every write
  reachable from there (`pool[idx] = None` at `:411`, `pool[idx] = new_conn`
  at `:447`, `_round_robin[key] = idx + 1` at `:396`/`:448`) targets that
  same `idx` while the lock is held.
- Between `pool[idx] = new_conn` (`:447`) and `return pool[idx]` (`:449`)
  there is no `await`, so no other coroutine can null the slot in that gap.
  The gap round 1 exploited — round-1's eager fill loop (`:111-147` of that
  version) filling slot *i* while a different coroutine's lock-free fast
  path nulled an already-passed slot *j* — no longer exists, because HEAD
  never touches an index other than the one it returns.

**Evidence.** I extracted `85631d2`'s and HEAD's `_aio_db.py` with
`git show <ref>:<path>` (no checkout), loaded each as a module, and ran the
branch's two new regression tests against both:

```
ROUND-1 (pre-fix 85631d2)  C2/heals_every_dead_slot           FAIL -> ValueError: Connection closed
ROUND-1 (pre-fix 85631d2)  C1/never_none_under_concurrency    FAIL -> ValueError: Connection closed
CURRENT (HEAD)             C2/heals_every_dead_slot           PASS
CURRENT (HEAD)             C1/never_none_under_concurrency    PASS
```

Both tests are non-vacuous and genuinely RED against the buggy commit.

One accuracy note, not a defect: the C1 test's docstring and the `a9d06b2`
commit message both claim it reproduces `"connection_for() returned None
under concurrency"`. In my runs it never tripped the `is not None` assert —
it tripped the subsequent real query, i.e. it reproduces the *dead
connection* variant of C1, not the `None` variant. The test is still a valid
regression guard for the finding's class; the claim about which assertion
fires is wrong.

### C2 (Critical) — locked section trusts non-`None` as alive → **FIXED**

The locked section now probes any non-`None` slot before returning it
(`:393-411`) and evicts on failure, instead of `85631d2`'s
`if pool[i] is not None: continue`. I re-derived the zero-concurrency
reproduction independently before running anything: with `pool=[c0,c1]`,
`rr=0`, both closed externally, round-1's fast path advanced `rr` to 1 on
the failed probe, its fill loop then repaired only slot 0, and
`return pool[rr % 2]` handed back the still-dead `c1`. Confirmed by the
`FAIL` line above, which is fully sequential.

### I1 (Important) — falsified yield-point premise → **FIXED (with residue)**

The `asyncio.sleep(0)` calls and the test asserting they mattered are both
gone, and the correction is recorded at `diagnostics.py:770-785`. I confirm
the underlying falsification independently from the installed library:
`aiosqlite/core.py`'s `Connection._execute` awaits a future resolved by the
worker thread via `call_soon_threadsafe`, so every awaited DB call in
`run_offline()` already suspends. Two pieces of residue — see N7 and N8.

### I2 (Important) — docstring described a superseded design → **FIXED**

The module docstring now describes the elastic pool it actually implements.

### I3 (Important) — `_scoring_pool.py` precedent misstated → **FIXED, and the correction is accurate**

Verified directly against `services/whalewatchers/_scoring_pool.py`:
`:35` is `ThreadPoolExecutor(max_workers=4, ...)` and `:11-13` reads
*"4 workers: this call path normally needs ~1 concurrently ... not a
load-bearing capacity guess"*. HEAD's `_aio_db.py:28-33` states both facts
correctly.

### I4 (Important) — capacity increase with no measured benefit → **NOT FIXED; refuted further, see N1**

The consolidation (`...-consolidation.md`) files I4 under *"I2/I3/I4
(docstring accuracy, fixed in `a9d06b2`)"*. That is a mis-adjudication: I4
is a substantive data-plane objection, not a wording problem, and even the
wording fix it asked for was not made. I4 asked for *"an honest restatement
of the number as unmeasured headroom"*; `_aio_db.py:149-151` still reads
*"total footprint even at `_MAX_POOL_SIZE` across every DB file ... is a
handful of threads"*, unchanged by `a9d06b2` (`git diff 85631d2..a9d06b2`
shows no edit to that comment). Enumerating every `connection_for(` call
site myself — `market_catalog`, `config_performance`, `signal_log`,
`paper_broker`, `series_watcher` — that is up to **50** connections and 50
non-daemon OS threads per loop. In my real-data run the five pools reached
`5+2+4+3+4 = 18` connections under only 5 concurrent callers, versus 5 for
the pre-branch design.

More importantly, I4's core claim is now *understated*: the pool does not
merely fail to help, it actively harms. See N1.

### M1–M4 (Minor) — **all four still apply verbatim; the consolidation's "moot" ruling is wrong**

The consolidation states the 4 Minor findings are *"moot rather than fixed
or deferred"* because the surface they applied to was superseded. I checked
each against HEAD:

| | Status at HEAD | Evidence |
|---|---|---|
| M1 `_in_flight` measures acquisition, not usage; round-robin has no checkout | **Still applies, and is now the root cause of N1** | decrement is in the `finally` at `:450-453`, at hand-back time; no lease anywhere |
| M2 above `_MAX_POOL_SIZE` the fast path is unreachable | **Still applies, worse** | `:318`'s `_in_flight[key] <= len(pool)` is unsatisfiable once in-flight > 10; and HEAD's locked section now awaits a probe (`:395`) while holding the **loop-global** lock (`_locks` is keyed by loop, not by key), so a burst above the cap serializes every DB file behind one probe round-trip |
| M3 return annotation is false | **Still applies** | `:256` `-> aiosqlite.Connection`; `:449` returns `pool[idx]`, element type `aiosqlite.Connection \| None` (`:136`) |
| M4 `reset()` does not clear `_in_flight` | **Still applies** | `:490-501` clears `_connections`, `_round_robin`, `_locks` only |

M1 is the serious one: it is a verbatim prediction of N1's mechanism
("*round-robin hands out an index with no lease or busy-tracking, so two
genuinely concurrent callers can be given the same connection while other
slots sit idle*"), and the C1 fix converted it from an occasional case into
the steady-state behaviour.

---

## New findings

### N1 (Critical) — the elastic pool regresses the incident's own metric versus the code it replaces

**File:** `services/diagnostics/_aio_db.py:307-453` (whole design).

**Failure scenario.** This branch exists because 5 concurrent
`GET /api/quality/summary` requests stalled an unrelated `GET /api/state`
for minutes. With this branch merged, that scenario is *worse* than before
it, on both wall time and worst-case co-resident latency.

**Evidence.** A/B against the real production `data/*.db`, swapping **only**
`services/diagnostics/_aio_db.py` (grafted into `sys.modules` before
importing `diagnostics`; `diagnostics.py` is HEAD's in all three arms, so
the 24h query bound is present throughout and the pool is cleanly isolated).
A co-resident coroutine wanting to run every 10 ms stands in for the starved
`GET /api/state`. 4 runs per arm:

| `_aio_db` version | 5-concurrent wall time | worst co-resident gap |
|---|---|---|
| `23f12e5` pre-branch, 1 conn/file | 15.03 / 15.38 / 16.30 / 16.40 s | 168 / 177 / 202 / 216 ms |
| `85631d2` branch round 1 | 35.43 / 36.04 / 40.95 / 43.43 s | 801 / 2352 / 2409 / 2917 ms |
| `a9d06b2` branch HEAD | 26.48 / 26.63 / 27.28 / 28.02 s | 231 / 672 / 850 / 1939 ms |

Ranges do not overlap on wall time; the starvation tail is up to 9x worse at
HEAD. Median gap is ~10.3 ms in all three arms and p99 is slightly *better*
with the pool (27–65 ms vs 71–85 ms) — more worker threads produce more
frequent small yields — but "stalled for minutes" is a tail property, and
the tail is what regresses.

**Mechanism (measurement is fact; the causal account is my hypothesis).**
Each aiosqlite connection is its own non-daemon OS thread that marshals rows
in Python and schedules callbacks onto the single event loop. Going from 5
threads to 18 multiplies GIL contention and `call_soon_threadsafe` traffic
against the one loop the app serves requests on, while the workload is not
actually bound by per-connection serialization. Falsifiable by: a `py-spy`
profile showing the loop thread's stall attributable to something other than
GIL/callback contention.

**Corroboration from the branch's own data, previously unread as such.**
`a9d06b2`'s commit message records *"2.43s solo, 7.43s for 3 concurrent"*.
3 × 2.43 = 7.29 s: the branch's own final measurement shows exactly zero
concurrency benefit and was not recognised.

### N2 (Critical, mechanism behind N1) — the C1 fix converted round-robin into a thundering herd

**File:** `services/diagnostics/_aio_db.py:325-335`.

Because `_round_robin[key]` is now read at `:325` and written at `:334`
*after* the `await` at `:333`, every caller that enters the fast path while
an earlier caller's probe is still in flight reads the same un-advanced
index and is handed the same connection. `asyncio.gather` guarantees this:
each coroutine runs to its first `await` before the next starts, so all N
read the index before any of them advances it. The comment at `:319-324`
acknowledges the collision but calls it *"merely redundant, never
incorrect"* — it is neither redundant nor free, it is the pool ceasing to
be a pool.

**Evidence** (controlled benchmark, tmp DB, a ~140 ms query so the
connection is genuinely busy):

```
pool len=5, filled=5
VIA POOL:      5 concurrent -> 0.736s wall, 1 DISTINCT connection used
CONTROL (1 conn per caller): 0.185s wall
serial-equivalent would be 0.714s
```

0.736 s ≈ the fully-serial 0.714 s. The same benchmark against `85631d2`
(which advanced the index *before* the probe) returns **5 distinct
connections in 0.172 s**, matching the control. Warm-pool bursts of 10
likewise collapse to 1 distinct connection.

So the two Critical fixes and the pool's purpose are in direct conflict as
currently designed: advancing the index before the probe spreads callers but
reintroduces C1; advancing it after is safe but collapses the pool. Neither
horn is acceptable — a correct design needs a real checkout/lease (M1's
point), not an index counter. Note that fixing N2 alone would *not* fix N1:
the round-1 arm, which does spread callers, is the worst of the three on
real data.

### N3 (Important) — the 24h bound silently changes a displayed, persisted diagnostic value by up to 5.4x

**Files:** `services/diagnostics/diagnostics.py:733-755`;
`services/research/research.py:154,209,236-244`.

`check_confidence_input_coverage`'s docstring (`:726-729`) frames the bound
as costing *"no real coverage for THIS check's own purpose"*. That is true
of the sample-size floor but false of the numbers the check actually
reports. Measured read-only (`mode=ro` URI) against the live
`data/signal_log.db`:

| window | n | depth | trend | agreement | raw_spread |
|---|---|---|---|---|---|
| all history (pre-fix) | 127,285 | 5.3% | **16.5%** | 2.0% | 0.8% |
| last 24h (post-fix) | 23,594 | 28.8% | **89.1%** | 10.9% | 4.2% |

`trend_factor` absence jumps 16.5% → 89.1% on merge. The cause is
composition, verified directly: all-history covers 332 distinct series, the
24h window only 71, and the mix inverts (`KXBTC15M` 21,431 → 565;
`KXGOLD15M` 10,948 → 10,827 and now dominant). Because `since_ts` filters on
`seen_at` (`services/signal_log.py:697-699`) while the rows must also be
`resolved = 1`, the window structurally excludes every series that takes
longer than ~24h to settle.

Arguably the new number is the *better* answer to "how healthy is the data
right now" — an 89% trend-factor absence rate in live flow is a real finding
the all-history average was masking. That is not the defect. The defect is
that neither the summary string (`:751-753`, `"... absent (n=...)"`) nor
`detail["input_coverage"]` discloses the window, so an operator sees a 5.4x
jump with no way to tell a window change from a real degradation.

Worse, `services/research/research.py` builds one report containing **both**
variants under the same field name: `confidence_calibration.report
.input_coverage` from the unscoped `resolved_signals_with_factors()` at
`:154`, and `diagnostics.checks[].detail.input_coverage` from the now-24h
path at `:209`. `run_and_store` persists that report as JSON
(`:236-244`), so every future research report permanently carries two
identically-shaped, unlabelled `input_coverage` blocks differing by ~5x.
That is squarely CLAUDE.md's "a displayed value must match its label" and
the data-plane accuracy property.

**Fix:** put the window in the summary and in `detail` (e.g.
`"window_hours"`, and `"... absent (n=…, last 24h)"`).

### N4 (Important) — the `UNKNOWN` message now misattributes its own cause

**File:** `services/diagnostics/diagnostics.py:742-747`.

The gated message — *"N/50 resolved real signals with a factor breakdown -
calibration activates once that's reached"* — is copied verbatim from
`confidence_calibration.generate_calibration_report`'s `gated_reason`
(`services/whale_calibration/confidence_calibration.py:368-374`). That was
accurate while both counted the same unscoped rows. It no longer is:
calibration's gate remains all-history (`services/whale_calibration/routes.py:112,147`
call `resolved_signals_with_factors()` with no argument), so this check can
now print *"0/50 … calibration activates once that's reached"* while
calibration is in fact long since active on 127,285 rows.

Reachability: not today — I measured the last ten rolling 24h windows of
resolved-with-factors volume at 3,799–23,601 rows, so the floor of 50 has
76x headroom at the observed minimum (this does confirm the docstring's
"~5,150/day" claim, though the day-to-day spread is ~6x, not the "two orders
of magnitude" the docstring implies at the low end). It becomes reachable
precisely during a whale-stream outage — a recurring class in this repo per
`docs/open-decisions.md` — and then this diagnostic, whose job is to detect
data-plane problems, would report the opposite of what happened.

**Fix:** distinguish the two, e.g. *"N in the last 24h (floor 50) — this is
a recency window, not the all-history calibration gate"*.

### N5 (Important) — the "~20.4s → 1.88s" attribution is not reproducible

**Files:** `6073df4` commit message; `docs/open-decisions.md:39`;
`services/diagnostics/_aio_db.py:50-53`.

I measured the full end-to-end work this fix removes — the exact query from
`resolved_signals_with_factors()` plus its per-row `json.loads` plus
`compute_input_coverage`'s counters — read-only against the live DB:

```
UNSCOPED (pre-fix): 127,285 rows, 1.187s / 1.264s
24h    (post-fix):  23,594 rows, 0.252s / 0.239s
```

~1.0 s saved per `run_offline()` call. That reproduces the open-decisions
entry's own "~1.0–1.1s" estimate exactly, and it cannot account for an 18.5 s
drop. `check_confidence_input_coverage` is one of 15 checks and nothing else
in the call graph changed. The 20.4 s baseline was plausibly taken under
live writer contention or a cold cache; either way the number is now
recorded in three places as this fix's measured effect, and it is not.
Falsifiable by: a reproduction of the 20.4 s baseline with the mechanism
identified. (For reference, my warm single `run_offline()` against real data
at HEAD is 3.2–3.4 s, not 1.88 s.)

The fix is still worth keeping — a ~1.0 s / ~30% reduction on a 5 s-polled
route is real. Only the attribution needs correcting.

### N6 (Important) — a load-bearing docstring number appears to be an estimate presented as a measurement

**File:** `services/diagnostics/_aio_db.py:61-65`.

The docstring claims the pool *"demonstrably helps once finding 1's fix is
also in place (43.89s, not the >90s a fixed single-connection or fixed-2-
connection pool alone would still show)"*. The ">90s" figure has no
measurement anywhere in the branch's commits or review documents, and my
direct A/B refutes it: with finding 1's fix in place, the single-connection
design completes the same 5-concurrent workload in 15.0–16.4 s, *faster*
than the pool's 26.5–28.0 s. Per the never-guess HARD RULE, an unmeasured
figure must be labelled as such in the same sentence. Note also that the
43.89 s number was measured at `6073df4`, i.e. against the round-1 pool that
`a9d06b2` then replaced — it does not characterise HEAD.

### N7 (Minor) — dead `import asyncio`

`services/diagnostics/diagnostics.py:35`, added by `85631d2` for the
`sleep(0)` calls and not removed with them. Both remaining `asyncio` uses
(`:264`, `:740`) are covered by function-local imports at `:241` and `:730`.
No Python linter runs in `.woodpecker/`, so nothing catches it.

### N8 (Minor) — behaviour-free diff churn in `run_offline()`

`services/diagnostics/diagnostics.py:786-792` rewrites a clean list literal
as one-element-list-plus-six-`append` calls. This is residue from removing
the interleaved `sleep(0)`s; it is semantically identical and should revert
to the literal, per this repo's "reviewable diffs" rule.

### N9 (Minor) — "a handful of threads" understates the footprint by an order of magnitude

`services/diagnostics/_aio_db.py:149-151`. Up to 50 connections/threads per
loop; 18 observed under only 5 concurrent callers. This is the correction
round 1's I4 asked for and did not get.

### N10 (Minor) — dangling cross-reference

`services/diagnostics/_aio_db.py:153-154` points at *"the module's 'MEASURED
TRADEOFF' section above"*. No such section exists; the module docstring's
heading is *"MEASURED, in order of what actually mattered"* (`:38`).

### N11 (Minor) — no test covers the pool's actual purpose

`test_connection_for_grows_the_pool_under_real_concurrent_demand` exercises
only the **cold** pool, where a slow `schema_init` forces every caller onto
the lock path and distinct indices follow trivially. The concurrency stress
test asserts liveness but never distinctness. Nothing asserts that a
**warm**, fully-filled pool distributes concurrent callers — the one
property the pool exists for, and the one N2 shows is absent. Whatever
replaces this design needs that assertion.

---

## Query-bounding fix review (`6073df4`/`ce9bc66`) — first independent pass

**Verdict: mechanically sound, keep, with N3 and N4 fixed first.**

- **Threading.** `check_confidence_input_coverage` has exactly one
  production caller: `run_offline()` at `diagnostics.py:792`, positionally
  `(cfg, since_ts, now)`. `run_offline` has three callers:
  `services/quality/routes.py:92` (`run_offline(cfg)` → both `None` → the
  new 24h default applies; this is the 5 s-polled route the fix targets),
  `services/diagnostics/routes.py:53` (explicit `since_ts=now - hours*3600`
  → passes straight through, unchanged behaviour, including a `hours=720`
  request which correctly still scans 30 days), and
  `services/research/research.py:209` (`now=` only → the 24h default now
  applies; this is the changed path behind N3). No route calls the check
  directly.
- **No caller wants full history through this function.** The one component
  that genuinely needs unscoped history — the calibration weight gate —
  calls `resolved_signals_with_factors()` directly at
  `services/whale_calibration/routes.py:112,147` and
  `services/research/research.py:154`, never through this check. The
  docstring's claim on this point is correct.
- **Default convention matches the file.** `:733-734` is byte-identical in
  shape to `:170-171`, `:245-246`, `:320-321`, and correctly derives the
  window from the caller's `now` rather than `time.time()`.
- **Dimensional check.** `now - 24 * 3600` — seconds − (hours × sec/hour) =
  seconds, matching `seen_at`'s unit in `signal_log`. `hours =
  (now_ts - since_ts) / 3600` in `run_offline` is unchanged and consistent.
  `target_size = min(_MAX_POOL_SIZE, max(_MIN_POOL_SIZE, _in_flight[key]))`
  compares callers to connections — coherent at 1:1. `_round_robin[key] =
  idx + 1` with `idx = rr % len(pool)` keeps the counter bounded in
  `[1, len(pool)]`; no overflow. No unit or scale errors found.
- **Sample-size floor holds.** 3,799–23,601 resolved-with-factors rows per
  rolling 24h over the last 10 days, against a floor of 50. See N4 for the
  caveat about what happens if it ever doesn't.
- **Read-only.** Verified before running anything against real data: no
  `INSERT`/`UPDATE`/`DELETE` anywhere in `run_offline`'s call graph; the
  only DDL is the idempotent `CREATE TABLE IF NOT EXISTS` in
  `series_watcher._ensure_schema_aio` that the live app already runs every
  ~5 s. The repo also guards this with
  `tests/test_diagnostics.py::test_run_offline_never_writes_to_any_db`. My
  own production reads used a `file:...?mode=ro` URI.

### Interaction between the two fixes

No hidden coupling found, and specifically not the one hypothesised: the
query bound removes ~1.0 s of `asyncio.to_thread` work, which is *outside*
`_aio_db` entirely (`resolved_signals_with_factors` is a plain `sqlite3`
call on a worker thread), so it does not change how `_in_flight` counts or
when the pool grows. Confirmed empirically — pool sizes in my real-data runs
(`5/2/4/3/4`) match the sizes the branch reports from its own pre-bound
measurements. The two changes are independent, which is exactly why
`6073df4` can be kept while the pool is reverted.

Key-derivation check (requested): `_key()` is the only key constructor and
is used consistently; `close_for_current_loop` filters by `key[0] is loop`
and never rebuilds a key. Every call site passes a module-level
`DB_PATH` `Path` (`Path(__file__).resolve()...`), so `Path` value-equality
gives no collisions and no accidental duplicate pools; no call site passes a
`str`. `schema_init` is passed consistently per key (always for
`series_watcher.DB_PATH`, never for the others). `_in_flight` bookkeeping is
balanced by construction — the increment is unconditional and cannot raise,
the decrement is in a `finally`, and the key is popped only at zero — so the
undercount hypothesis is refuted; the real problem is M1's, that the counter
measures the wrong thing, not that it is unbalanced.

---

## Test-quality spot check

| Test | Verdict |
|---|---|
| `test_connection_for_heals_every_dead_slot_not_just_the_first_one_probed` | Real. RED against `85631d2`, GREEN at HEAD, fully sequential and deterministic. |
| `test_connection_for_never_returns_none_or_a_dead_connection_under_concurrency` | Real. RED against `85631d2`, GREEN at HEAD. Docstring overstates *which* assertion fires (see C1). |
| `test_connection_for_grows_the_pool_under_real_concurrent_demand` | Real but narrow — cold pool only; see N11. |
| `test_schema_init_runs_once_per_pool_slot_never_again_after_the_pool_fills` | Real. `_MIN_POOL_SIZE + 3` sequential calls, asserts exactly `_MIN_POOL_SIZE` invocations — genuinely discriminating. |
| `test_connection_for_is_cached_within_the_same_loop` | Real. Asserts distinct-count *and* wrap-around identity. |
| `test_confidence_input_coverage_defaults_to_a_24h_window_not_full_history` | Real. Seeds 60 in-window + 60 out-of-window and asserts `n=60`; discriminating power confirmed independently by the 127,285-vs-23,594 production measurement of the same filter. |

No vacuous assertions found.

---

## What I verified, and how

1. **Full suite, as specified** — 103 passed in 18.98 s, in
   `ddev-kalshi-whale-poc-fastapi` with `HOME=/tmp`. Count confirmed.
2. **C1/C2 regression proof** — extracted `85631d2` and HEAD `_aio_db.py`
   via `git show <ref>:<path>` (no checkout), loaded each as a module, ran
   the branch's two new tests against both. Both RED on the old, GREEN on
   the new.
3. **N2 herd** — controlled benchmark on a tmp DB with a ~140 ms query:
   HEAD 5 concurrent → 1 distinct connection / 0.736 s; `85631d2` → 5
   distinct / 0.172 s; dedicated-connection control 0.185 s.
4. **N1 regression** — `run_offline()` against the real `data/*.db` with
   only `_aio_db.py` swapped (`sys.modules` graft, verified with
   `assert diagnostics._aio_db is m`), a 10 ms heartbeat coroutine as the
   starvation probe, 4 runs per arm across three arms.
5. **N3/N4/N5 production measurements** — read-only
   `sqlite3.connect("file:/app/data/signal_log.db?mode=ro", uri=True)`:
   row counts, per-window volume for 10 rolling days, series composition,
   absence rates, and end-to-end scoped/unscoped timings.
6. **I3** — read `services/whalewatchers/_scoring_pool.py` directly.
7. **Caller graph** — `grep -rn` for every caller of
   `check_confidence_input_coverage`, `run_offline`,
   `resolved_signals_with_factors`, and `connection_for`.
8. **Round-1 finding disposition** — re-read every M1–M4 line against HEAD
   line by line rather than trusting the consolidation's "moot".
9. **Read-only discipline** — no file in the worktree was edited, no
   checkout, no commit; `git status --short` is clean. All probe scripts
   lived in the container's `/tmp` and were deleted.

**Limits of my evidence.** My probes run in a separate process from the live
app, so they do not reproduce the live writers' lock contention on these
DBs; absolute numbers will differ from production. The A/B arms all face
identical conditions, so the *comparison* is sound even though the absolute
timings are not production figures. The GIL/callback-contention explanation
for N1 is a hypothesis; the measured regression is not.

---

## Required before merge

1. **Revert or redesign the elastic pool** (N1, N2, I4, M1). If a pool is
   still wanted, it needs a real checkout/lease rather than an index
   counter, plus a warm-pool distribution test (N11), plus a measurement
   showing it beats `23f12e5`'s single connection on both wall time and
   worst-case co-resident latency. It currently loses on both.
2. **Disclose the window** in `check_confidence_input_coverage`'s summary
   and `detail` (N3).
3. **Fix the `UNKNOWN` message** so it stops claiming the calibration gate
   (N4).
4. **Correct the `20.4s → 1.88s` attribution** in the commit message record,
   `docs/open-decisions.md:39`, and `_aio_db.py:50-53` (N5); label or remove
   the unmeasured ">90s" (N6).
5. Minor cleanups: N7, N8, N9, N10.
6. If the pool is reverted, the surviving `6073df4`/`ce9bc66` change is
   small enough to re-review against the reduced diff rather than
   re-litigating this document.
