# Issue #410 design: dedicated pool vs. aiosqlite for the two `tick_executor`-sharing diagnostic routes

Design/spec stage. Written because `docs/archive/lane-5-runtime-infrastructure/research/2026-09-04-issue-410-tick-executor-measurement.md (moved there 2026-09-06, planning-lanes migration)`
(merged, PR #567) §5 explicitly refused to make this call inside a measurement note:

> leaning toward option 2 (aiosqlite rewrite) as the better-fitted long-term pattern given this
> codebase's own most recent precedent, **but that comparison (mechanism, benchmark, correctness,
> failure behavior, complexity, per the data-plane HARD RULE) deserves its own design pass before
> implementation, not a decision made inside this measurement note.**

Scope is exactly that comparison, on the axes CLAUDE.md's data-plane HARD RULE names. It does not
re-open the measurement (that is settled and merged) and does not cover the cache-alignment bug,
which is orthogonal and already fixed separately (PR #569).

## Verdict

**Correction (2026-09-05, tracked as #582 — the Verdict below stands; §1's cost attribution does
not).** This doc splits `whale_calibration._build_report()` into a *"~2s fetch"* plus *"~3.4s
compute"* and concludes aiosqlite helps *"only for the ~2s fetch"*. That fetch is **not** I/O:
`signal_log.resolved_signals_with_factors()` (`services/signal_log.py:688-745`) runs the SQL **and**
a per-row `json.loads` loop inside one function, so "convert its fetch to aiosqlite" literally puts
that CPU loop on the event loop — the regression `services/diagnostics/_aio_db.py`'s own docstring
already records for `run_offline()`. **The materialize step must go to `asyncio.to_thread` too.**
That mechanism is verified from source and is the load-bearing point of this correction.

**The numbers below are PROVISIONAL.** They are transcribed from PR #581, which at the time of
writing is **unmerged, unreviewed and has no CI result**, and no command or raw output is recorded
for the fetch/materialize split. Do not treat them as settled; re-check them against #581 as merged.
Provisionally: SQL **0.884s**, materialize **2.691s**, compute **4.383s** — a **~7.96s** total
against §2's *"~5.5s"* (line ~46), i.e. the error is one of **magnitude as well as attribution**
(the "fetch" is ~3.58s, not ~2s). On those figures aiosqlite addresses **~11%** of the path, not the
~36% this doc implies.

**Open question this correction does not settle (MED-5):** at ~11%, whether `aiosqlite` +
`asyncio.to_thread` still beats a plain `await asyncio.to_thread(_build_report)` — one hop, no
aiosqlite, and none of §3's `_aio_db` footgun surface — was **never compared**, because the design
rejected "option 1" as a *new dedicated pool*, which `to_thread` on the default executor is not.
Per the data-plane HARD RULE that comparison is owed before this path is considered closed.


**Neither option alone. Adopt a split fix, matched to the two routes' genuinely different
bottlenecks — and do not add a third thread pool.**

- `candidate_log.population_gate_summary()` → **native `aiosqlite`** (option 2).
- `whale_calibration._build_report()` → **`aiosqlite` for its fetch, plus `asyncio.to_thread`
  for its compute pass** — **[corrected 2026-09-05, #582: "its fetch" here means the SQL *only*
  (~0.884s). The materialize/`json.loads` step (~2.691s) is CPU and must ALSO go to
  `asyncio.to_thread`; sending it to aiosqlite alone is a regression.]** (option 2 + an offload,
  because option 2 alone provably cannot help the
  dominant cost).
- **Reject option 1** (a new dedicated `ThreadPoolExecutor`) for both.

## 1. The asymmetry that decides this

The research note measured `_build_report()`'s ~5.5s as a fetch plus a report-generation pass **[corrected #582: provisionally ~7.96s, and the "fetch" is ~3.58s not ~2s]**. The
design-relevant fact is *what kind* of work each part is — verified by reading the functions, not
inferred from the timings:

| Path | Dominant cost | Nature | Does aiosqlite help? |
|---|---|---|---|
| `candidate_log.population_gate_summary()` (`services/candidate_log.py:274`) | 15-22s | **SQL-bound.** Its own docstring (`:310`): *"Aggregates via SQL GROUP BY, not a per-row Python loop (2026-08-26 fix)"* — one `conn.execute` of a `SELECT ... GROUP BY` at `:331-334`, trivial post-processing. | **Yes, fully.** The blocking is the driver waiting on SQLite. |
| `whale_calibration._build_report()` | ~2s fetch + **~3.4s compute** — **[corrected #582: ~0.884s SQL + ~2.691s materialize + ~4.383s compute, provisional]** | `_bucket_win_rates(rows: list[dict], factor_name: str)` (`services/whale_calibration/confidence_calibration.py:89`) takes an **already-materialised list** and does index-based tertile sorting per factor, 9 factors. **No database access at all.** | **Only for the ~2s fetch.** aiosqlite cannot touch the 3.4s, which is pure CPU. **[corrected #582: only for the ~0.884s SQL — the ~2.691s materialize is CPU too. The quoted sentence remains true; it simply understates how much of this path aiosqlite cannot touch.]** |

This is the crux, and it is why "just do the aiosqlite rewrite" — the research note's own leaning —
is **insufficient as stated**. Converting `_build_report()` to aiosqlite and stopping there would
move ~2s off the pool and leave the larger ~3.4s still blocking, still occupying a `tick_executor`
worker. A reader of §5 could easily implement exactly that and believe the route was fixed.

## 2. Option comparison

### Option 1 — dedicated `ThreadPoolExecutor`

Precedent: `services/whalewatchers/_scoring_pool.py:35` (`max_workers=4`), and PR #409's
now-deleted `_diagnostics_pool.py`.

- **Mechanism:** isolates contention. Diagnostics no longer queue behind
  `candidate_ledger.claim()`/`record_decision()` in `tick_executor`'s 2 workers
  (`services/tick_executor.py:80`, `max_workers=2`).
- **Benchmark:** absolute query cost unchanged — 15-22s stays 15-22s. Only *where* it blocks moves.
- **Correctness:** lowest risk of the three. No query rewrite, no new library semantics, no change
  to result shape.
- **Failure behavior:** the isolated pool's own occupancy keeps climbing with `rejection_events`
  growth. Two concurrent History polls still saturate a 2-worker pool; sizing it larger trades
  memory for the same unbounded problem.
- **Complexity:** cheapest to implement. Near-identical shape exists already.
- **Decisive objection:** this codebase **already tried and abandoned this exact pattern for this
  exact problem**. PR #409's `_diagnostics_pool.py` was deleted in favour of the aiosqlite rewrite
  ~7 hours after #410 was filed. Re-adopting it would be a knowing regression to a superseded
  pattern, and would add a **third** long-lived pool (`tick_executor` 2 + `_scoring_pool` 4 + new N)
  to a container that CLAUDE.md already documents as memory-pressured.

### Option 2 — native `aiosqlite`

Precedent: `services/quality/routes.py`'s `run_offline()`, backed by
`services/diagnostics/_aio_db.py`. `aiosqlite==0.22.1` is **already a pinned dependency**
(`requirements.txt:61`) — no new dependency decision.

- **Mechanism:** removes the thread-pool dependency entirely for SQL-bound work; the driver yields
  to the loop natively rather than occupying a worker for the query's whole duration.
- **Benchmark:** the only option that also improves the *underlying* blocking profile rather than
  relocating it. (It does not make the query itself faster — see §4.)
- **Correctness:** the read is a single `GROUP BY` returning rows; conversion is mechanical.
- **Failure behavior:** two documented footguns, both already surfaced by this codebase's own prior
  adversarial review (`docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-pr-adversarial-review.md`)
  — see §3. Both are **already mitigated in the shared `_aio_db` layer**, which a new consumer
  inherits for free, provided it also inherits the test hygiene.
- **Complexity:** materially more invasive than option 1, but the invasiveness is confined to two
  read functions.

### Option 3 — fix the query cost / table growth

Out of scope here, deliberately, and **not** an alternative to the above: it is the actual root
cause and it is tracked separately on **#532** (updated 2026-09-04 with the current 25.8M rows /
~4.2x-in-a-week figures). Retention is a human decision per CLAUDE.md's "accumulated history is a
first-class asset" rule, and it changes what `population_gate_summary()`'s *total*-population gate
**means**, not merely what it costs. Named here so the record shows options 1 and 2 both leave it
untouched.

## 3. Failure behavior of the recommended path, stated honestly

Both risks below are real, were found by a prior adversarial review of the aiosqlite work, and are
**already fixed in `_aio_db`** — verified in current source, not assumed:

- **C1, leaked non-daemon threads.** `aiosqlite` gives each connection a **non-daemon** OS thread;
  a cached-and-never-closed connection keeps it alive, and CPython's shutdown *joins* every
  non-daemon thread — the original finding reproduced a process that never exited
  (`EXITCODE=124`) and a test file that hung forever. **Mitigation is present today:**
  `services/diagnostics/_aio_db.py:101` `_close_all_at_process_exit()`, plus
  `tests/test_diagnostics_routes.py:22-36`'s autouse `_reset_aio_db_cache` fixture calling
  `_aio_db.reset()`.
  → **Binding requirement on implementation:** any new test module touching an aiosqlite path
  MUST carry that same reset fixture. This is the single most likely way to reintroduce a hang.
- **I3, a wrong rationale in the docstring.** `_aio_db`'s stated justification (connections are
  "loop-bound") is **factually false** for 0.22.1 — the library removed loop binding; the transport
  is a plain `SimpleQueue` on a plain `Thread`. Loop-scoped caching remains correct, but for
  *lifetime* reasons, not the reason written down.
  → **Do not propagate that explanation** into new code comments. Cite lifetime, not loop binding.

One further consideration, stated as an open question rather than a settled fact:
`asyncio.to_thread` runs on the loop's **default** executor, which is shared process-wide with any
other `run_in_executor(None, ...)` caller. Its default ceiling (`min(32, cpu_count + 4)`) is far
above `tick_executor`'s 2, so contention is unlikely to bind — but it is a shared resource, not a
private one, and the implementation should confirm no other hot-path code is contending for it
before treating the 3.4s offload as fully isolated **[corrected #582: the offload is ~2.691s materialize + ~4.383s compute, not 3.4s]**.

## 4. What this fix does *not* achieve

Deliberately explicit, because the surrounding objective is "make data-plane stalls a thing of the
past":

- It does **not** make `population_gate_summary()` faster. 15-22s of SQLite work remains 15-22s of
  SQLite work; it stops monopolising a shared worker, it does not shrink.
- It does **not** stop the cost climbing. That is #532, untouched by either option.
- Combined with PR #569 (cache stamped at completion), the *frequency* of paying the cost drops and
  the *blocking* character of paying it improves. Both are real; neither is elimination.

## 5. Permanent detection for recurrence

Per the data-plane HARD RULE's "permanent detection for recurrence" requirement:

- A regression test asserting neither route's handler calls `tick_executor.run` — the shape of the
  existing `test_candidate_log_summary_runs_population_gate_summary_via_tick_executor`, inverted,
  so a future refactor that silently routes this work back onto the shared pool fails CI.
- A test asserting the aiosqlite read path yields (the `run_offline()` precedent's own approach) so
  a conversion back to a blocking driver is caught.
- The mandatory `_reset_aio_db_cache` fixture on any new aiosqlite-touching test module (§3).

## 6. What would falsify this recommendation

- If `_bucket_win_rates`' 3.4s is not reproducible — **[#582: it did not reproduce at 3.4s; PR #581
  re-measured it provisionally at 4.383s. The "aiosqlite alone is insufficient" conclusion is
  strengthened, not weakened, since the CPU share grew]** — the entire "aiosqlite alone is insufficient"
  argument rests on that measurement plus the read that it takes `rows` as a parameter. Both should
  be re-confirmed at implementation time.
- If the default executor turns out to be contended by hot-path work (§3's open question), the
  `to_thread` half needs a different home.
- If #532 gains a retention policy first, `population_gate_summary()`'s cost may fall far enough
  that the whole pool-sharing question stops mattering, and the cheaper option 1 — or no fix —
  becomes adequate. That ordering is worth knowing before spending the implementation effort.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
