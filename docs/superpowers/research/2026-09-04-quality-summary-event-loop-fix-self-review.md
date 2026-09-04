# Self-review: GET /api/quality/summary event-loop-blocking fix (issue #530)

2026-09-04. Branch `fix/quality-summary-event-loop-blocking`. Live-incident-pace
implementation per coordinator instruction — this is the self-review layer only
(same-author internal-consistency check); the required independent adversarial
review and consolidation happen as a separate pass before this PR merges, per
CLAUDE.md's "nothing advances on one pass" HARD RULE. Written by the same
session/context that did the implementation, so treat every claim below as
unverified by anyone else yet.

## What changed

`services/quality/routes.py`'s `get_quality_summary()` (`GET /api/quality/summary`)
ran seven synchronous DB/file-read calls directly on the FastAPI event loop with
no dispatch. Each is now wrapped in `await asyncio.to_thread(...)`:

1. `observability.runtime_findings(cfg, state, trade_stream, index_stream)`
2. `storage_health.inventory_data_dir(storage_health.DATA_DIR)`
3. `backup.latest()`
4. `storage_health.storage_findings(storage_entries, last_backup_run=..., backup_interval_sec=...)`
5. `alerting.active_alerts()`
6. `research.latest()`
7. `fault_log.summary()`

Deliberately left undispatched (confirmed by direct read, not by assumption):
`alerting.alert_findings()` (pure computation over an already-fetched list),
`evidence_provenance.findings()` (its whole call graph is in-memory counters —
`settlement_resolver.snapshot()`, `index_feed.ingestion.snapshot()`,
`capture_writer.dropped_count()` — no I/O), and `diagnostics.run_offline()`
(already `await`ed and runs natively on aiosqlite, per its own file's prior
fix history — PR #409/#420/#424).

Also corrected `services/quality/README.md`'s "Hot-path impact: None" claim,
which was stale/wrong for the failure mode this issue is about (see below).

## Scope deviation from the coordinator's brief — stated explicitly

The brief named four undispatched calls (`alerting.active_alerts`,
`fault_log.summary`, `research.latest`, `observability.runtime_findings`),
matching issue #530's census doc. Direct line-by-line reading of the current
route body (per the brief's own "confirm this transcription against current
source before writing any fix" instruction) found three more undispatched
sync calls in the *same handler*: `storage_health.inventory_data_dir`,
`storage_health.storage_findings` (itself calling `observability.history()`
once per `data/*.db` entry — a second level of indirection identical in shape
to `runtime_findings`'s own already-flagged one), and `backup.latest()`
(buried as an inline keyword-argument expression inside the
`storage_findings(...)` call, easy to miss on a first pass).

All seven are the same defect class, in the same file, fixed with the same
mechanism, in the same commit — this is completing the stated task ("find the
actual route handler... read it in full"), not scope creep onto unrelated
work. Flagging it explicitly per this repo's "never guess" rule rather than
silently fixing four and leaving three undispatched sync calls in a route I'm
claiming to have fixed.

## Mechanism choice — deviates from the brief's suggested pattern, with evidence

The brief suggested either `tick_executor.run()` (PR #414's precedent) or,
per issue #510's rejection of pool-sharing, a new dedicated pool shaped like
`services/whalewatchers/_scoring_pool.py`. I used neither — I used
`asyncio.to_thread()` instead, because `services/storage_health/routes.py`
(the *sibling* file in the same package family, already reviewed and marked
"genuinely clean" by the census doc itself) already establishes this exact
pattern for this exact class of problem: a diagnostic read that must leave
the event loop without touching `tick_executor`'s trading-critical 2-worker
pool. `asyncio.to_thread` uses the asyncio default loop executor — a
completely different pool from `tick_executor`, so it carries none of PR
#409's starvation risk, and it needed no new file/abstraction (a dedicated
pool would have been unjustified complexity for calls now measured at
1-40ms each). This is "the codebase's own precedent," just a closer and
simpler match than the one named in the brief — `services/backup/backup.py`'s
own `_run_backup_background` and `services/loop_watchdog.py`'s fault_log
write use the identical pattern.

**Risk this introduces, named honestly**: `asyncio.to_thread`'s pool is
shared app-wide by whatever else already uses it (storage_health's routes,
backup's background task, loop_watchdog's stall-capture write). I did not
measure whether adding up to 7 more dispatches per `/api/quality/summary`
call meaningfully increases contention on that shared pool under real
concurrent load — the individual calls are cheap (1-40ms), and Python's
default executor sizing (`min(32, cpu_count+4)`) gives real headroom, but
"cheap in isolation" is not the same claim as "no aggregate effect on a
pool other diagnostics also use." Worth a look in the adversarial pass.

## The load-bearing measurement this fix rests on — and where it diverges from the brief's framing

Direct live measurement (`ddev exec -s fastapi python3 -c ...`, one-shot,
read-only, against the running app's real `data/*.db` files — not
simulated/isolated data) of each candidate function found:

| Call | Measured cost |
|---|---|
| `alerting.active_alerts()` | 1.5ms |
| `fault_log.summary()` | 36.7ms |
| `research.latest()` | 1.9ms |
| `observability._repeated_rate_limit_hits_finding()` (the DB-touching part of `runtime_findings`) | 0.9ms |
| `storage_health.inventory_data_dir()` (32 `data/*.db` files) | 14.8ms |
| `storage_health.storage_findings()` (32 files, each triggering a nested `observability.history()` call) | 30.7ms |
| `evidence_provenance.findings()` | 0.0ms (confirms no I/O) |
| `diagnostics.run_offline(cfg)` | **9,104.8ms** |

**Correction, post-adversarial-review (2026-09-04)**: the independent review
re-measured against the primary checkout's real `data/*.db` (this table's
figures were taken from the worktree's own separate, nearly-empty `data/`
directory — a real measurement trap, not caught the first time) and got a
higher sum: `fault_log.summary()` 93.8ms, `storage_findings()` 108.6ms,
`inventory_data_dir()` 21.1ms, `runtime_findings` 11.3ms, `active_alerts`
1.3ms, `research.latest` 2.6ms, `backup.latest` 0.9ms — **~240ms total**,
not "well under 100ms" as originally written here. The conclusion is
unchanged (still ~30-90x below `run_offline()`'s ~9.1s), but the specific
number below was wrong and should not be repeated. Corrected in `routes.py`
and `README.md`; left here with the correction rather than silently
rewritten, per this repo's practice of not erasing a wrong number without
saying so.

This directly contradicts the brief's framing that the four named calls are
"the root cause" of the route's 11.59-33.35s measured latency (census doc).
They sum to roughly 100-300ms (not "well under 100ms" — see correction
above), and that figure will keep climbing as `fault_log.db`/
`observability.db` grow. The dominant cost by nearly two orders of
magnitude is still `diagnostics.run_offline()` — which the brief itself, and the
census doc, both already correctly excluded from the "undispatched" set
(it's genuinely `await`ed and yields control via aiosqlite, per prior
verified research in that file's own comments — "~1,100 real yields per
run_offline() call"). **Also per adversarial review**: that yielding is real,
but `run_offline()` still has its own separate, pre-existing, undisclosed
on-loop CPU chunks between yields (`_aio_db.py`'s own docstring: >=280ms
measured 2026-09-01) — out of scope for this fix, but this doc's original
framing ("doesn't block the loop the way the 7 calls did") understated that,
and is corrected here for the record.

**What this means for the fix's actual impact, stated plainly rather than
implied**: this fix removes genuine event-loop-blocking time — real,
previously-undispatched synchronous I/O that could stall *every other*
concurrent request (the PR #424 precedent: "5 concurrent GET
/api/quality/summary requests stalling an unrelated GET /api/state for
minutes") — but the *magnitude* of that removed blocking time is roughly
100-300ms per call (corrected from the original "40-100ms," see above),
not the multi-second figures the census doc's own priority-ranking table
used to estimate "~1.2-3.3 event-loop-blocked hours." One more deliberately
out-of-scope call worth naming for completeness: `config_store.get()` (the
handler's first line) also does an undispatched `stat()`/conditional YAML
reparse — an 8th call, identical across nearly every route in the app, so a
systemic fix belongs in `config_store.py`, not repeated here.
That table's estimate conflated the route's total client-observed latency
(dominated by the already-non-blocking `run_offline()`) with actual
event-loop-blocked time. I did not go back and recompute a corrected
cumulative-impact figure for the 19.6h census window — that would need the
per-call cost of the *previously*-undispatched code specifically (now
unobservable in isolation post-fix without reverting), and doing so is not
required to know the fix is correct and worth having; I'm flagging it as a
correction to the historical record rather than doing the recomputation
myself under incident time pressure.

**This is the single most important thing for the adversarial reviewer to
re-derive from primary sources, not trust from this document**: verify
independently (a) that `diagnostics.run_offline()` really does yield
control during its ~9s (not just cite the existing code comment — that
comment's own claim, ~1,100 real yields, predates this task and was not
re-verified by me), and (b) that the seven newly-dispatched calls' measured
costs are representative and not an artifact of a currently-quiet
process/idle DB state.

## Testing

TDD followed: wrote `test_quality_summary_dispatches_its_blocking_calls_off_the_event_loop`
first (spy pattern borrowed from `tests/test_loop_watchdog.py`'s
`test_stall_captures_a_stack_and_records_it_off_the_loop` — checks whether
`asyncio.get_running_loop()` succeeds *inside* the real call, proving actual
thread-boundary crossing rather than merely that `asyncio.to_thread` was
invoked somewhere), confirmed it failed against the unmodified route (all
seven calls reported `on_loop: True`), implemented the fix, confirmed it
passes (all seven `on_loop: False`). Ran the full existing
`tests/test_quality_routes.py` suite (7/7 pass) plus every test file for
directly touched modules — `test_storage_health.py`, `test_backup.py`,
`test_alerting.py`, `test_fault_log.py`, `test_research.py`,
`test_observability.py`, `test_diagnostics_routes.py` — 201/201 pass,
0 failures, 0 skips beyond pre-existing.

**Not done**: full-suite CI confirmation (per branching-and-ci.md, that's a
push-then-`gh api .../status` check, done after this commit is pushed, not
before — noted as the next step, not yet executed as of this document).
Also not done: a live HTTP measurement of the *fixed* route against the real
production app — the fastapi container runs the primary checkout's code, not
this worktree's, so an in-process HTTP timing comparison isn't possible
without merging/deploying first; the function-level dispatch proof (the spy
test) is the correctness evidence for this PR, and a live before/after HTTP
comparison is left for post-merge confirmation once the primary checkout
picks up the change.

## Unaddressed scope, named rather than silently left out

- `services/observability/observability.py:_connect()`'s own docstring notes
  `services/observability/routes.py`'s *own* routes (`GET
  /api/observability/history`, `GET /api/observability/summary`) still call
  `history()`/`summary()` undispatched — tracked as "item 2 of issue #530."
  That is a different route file, out of scope for this fix, not touched.
- Issue #410 (`candidate_log.population_gate_summary`,
  `whale_calibration/routes.py`'s `_build_report`) is explicitly a separate,
  already-tracked capacity/starvation problem on `tick_executor` itself — not
  touched, not conflated with this fix, per the brief's own caution.
- The census doc's "at least 63, not exactly 63" floor stands — this fix
  addresses one route's full undispatched-call set (verified as complete for
  *this* handler by direct read), not the other ~55+ instances the sweep
  found elsewhere in the route layer.

## Dimensional analysis

No new arithmetic or unit conversion was introduced — this is purely
dispatch-mechanism wiring (`await asyncio.to_thread(fn, ...)` around
existing calls with unchanged arguments/return values). All numbers in this
document and in `routes.py`'s updated docstring are measured, cited with
their unit, and either taken directly from a `time.perf_counter()` delta
(printed in ms/s) or from the pre-existing census doc — no derived formula
needing a `dimensional-analysis` pass exists in the diff.

## GO / no-go self-assessment

Self-review verdict: internally consistent, no contradiction found between
the code, the tests, and this document's own claims. The load-bearing
measurement claims (per-function costs, `run_offline()`'s dominance, the
seven-call-not-four-call scope correction) are the parts most likely to be
wrong if the live process state was unusual at measurement time — that is
exactly what the adversarial pass should re-derive independently rather than
take from this table. Recommend proceeding to the adversarial-review stage
before merge; not recommending merge on this document alone.
