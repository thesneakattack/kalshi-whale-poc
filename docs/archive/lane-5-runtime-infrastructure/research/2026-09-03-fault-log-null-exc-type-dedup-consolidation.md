# Consolidation: fault_log NULL exc_type dedup fix (issue #543)

Reconciles the fix, its self-review, and its independent adversarial review
into a GO/no-go verdict and fix-list recheck, per CLAUDE.md's "nothing
advances on one pass" HARD RULE. Authored by the same session that wrote the
fix (`autotrade-bc`) — per that HARD RULE's own text, the independence
requirement attaches to the adversarial review, not to consolidation.

## Review history

1. **Fix** (`services/fault_log.py`, `tests/test_fault_log.py`) — a partial
   unique index `(component, operation, message) WHERE exc_type IS NULL`,
   additive alongside the table's existing `UNIQUE` constraint, closing the
   dedup gap `record_fault()`'s always-`None` `exc_type` leaves open. A
   guarded, idempotent, automatic one-time merge for pre-existing duplicate
   rows (86,506 → 20,122 measured live, 76.7% reduction).
2. **Self-review** — found and fixed 2 real defects: a stale line-number
   citation and a stale duplicate-count figure, both self-inflicted by the
   fix's own insertion. Explicitly flagged one open question for the
   adversarial pass: whether any genuine multi-threaded concurrency exists
   on this write path.
3. **Adversarial review** — genuinely independent: fresh Agent call, no
   memory of the authoring session, re-derived every load-bearing claim
   from primary sources (sandboxed SQLite scripts, live thread reproduction,
   direct source reads, the live production database read-only) rather than
   trusting the self-review's own tables. **Verdict: GO-AFTER-FIXES.**
   Confirmed the core mechanism, the migration's crash-safety (verified via
   an actual `SIGKILL` mid-transaction), and the count-sum/traceback-
   preservation correctness. Found the self-review's concurrency claim
   itself **wrong** — `loop_watchdog.py` and `diagnostics/routes.py` both
   dispatch `fault_log` calls via `asyncio.to_thread`, genuine concurrent OS
   threads — and, re-deriving from that corrected fact, found 1 must-fix (a
   reproducible race causing silent write loss) and 2 should-fix items.

## Adjudication

No disagreement to adjudicate between the two reviews — the adversarial
review's findings supersede the self-review's own (incorrect) concurrency
conclusion, which the self-review had explicitly flagged as unverified and
handed off rather than asserted. That is the review pipeline working as
designed: self-review's honesty about its own blind spot is exactly what let
the adversarial pass catch it.

## Fix list — merged, and applied in this pass

| # | Item | Source | Status |
|---|---|---|---|
| 1 | Two `to_thread` call sites (`loop_watchdog.py`, `diagnostics/routes.py`) genuinely dispatch `fault_log` calls onto separate OS threads, contradicting the self-review's "none do" claim | Adversarial finding, must-fix #1 | **Applied** — `_ensure_null_exc_type_dedup_index`'s retry statement gained `IF NOT EXISTS` (was raising uncaught `OperationalError` on the losing thread, silently dropping that call) |
| 2 | Testing fix #1 surfaced a second, distinct race: the merge's `SUM(count)`/`MAX(last_seen)` read (`CREATE TEMP TABLE ... AS SELECT`, pure DDL, no transaction) was not atomic with its own later `UPDATE`/`DELETE` — two connections could read the same pre-merge duplicates and the second writer would silently overwrite rather than add to the first's numbers | Found during fix verification, not by either review (both predate this discovery) | **Applied** — merge+retry wrapped in `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`, serializing the whole read-then-write against other writers; a `busy_timeout` (previously absent from this module entirely) added so a losing connection waits instead of failing instantly |
| 3 | Fixing #2 surfaced a third, **pre-existing, unrelated** race: `PRAGMA journal_mode=WAL`'s first-ever conversion needs exclusive access and can itself raise "database is locked" under concurrent first connections, before a connection's own `busy_timeout` has taken effect | Found during fix verification; confirmed reproducible on unmodified `origin/main` too (3/40 trials), independent of issue #543 | **Not fixed in this PR** — different root cause, pre-existing since this module's inception, narrow in practice (`data/fault_log.db` has 86,000+ rows and has been in WAL mode for its entire real life; only bites a genuinely fresh/empty database under concurrent first connections). Filed separately as issue #549 with full reproduction evidence. The regression test added for item #2 bootstraps WAL mode via its seed connection (matching every real deployment's actual state) so it stays isolated to the race this PR is responsible for. |
| 4 | `DELETE`'s `message` comparison used `=` instead of null-safe `IS`; a `NULL` `message` (schema-permitted, though unreachable in practice — every caller passes `message` through `str()`) would inflate the keeper's count without deleting the duplicate rows | Adversarial finding, should-fix #2 | **Applied** — changed to `IS`, verified null-safe column-to-column comparison in a sandbox first |
| 5 | Self-review's "additional call sites with secondary benefit" list missed `index_feed/backfill.py:277`'s `backfill_response_shape_unknown` (interpolates only `type(payload).__name__`, low cardinality, no in-process gate) | Adversarial finding, should-fix #3 | **Not applied to code** — self-review-doc-only omission, doesn't affect the fix's correctness or scope; noted here for the record rather than editing an already-merged-in-spirit review artifact |

Every code fix was re-run against the test suite after applying it, per the
HARD RULE's "never accepted on its own completion claim": `tests/
test_fault_log.py` 21/21 (up from 20 — one new regression test for finding
#2), plus 5 additional single-shot runs of the new concurrency test and a
30-trial loop (all passed) before finding #2 was even fully diagnosed, and
again after — the empirical verification this fix's own correctness claims
rest on, not assertion.

## An incident during this pass, disclosed plainly

Verifying finding #2's fix empirically required running many-trial
concurrent-thread loops against the shared fastapi container. A stacked
sequence of these (30+ rapid `subprocess.run()` pytest spawns) left 5,286
zombie processes in that container - unreaped children, exhausting its
process table and causing a real several-minute live-app outage, coordinated
and resolved with `autotrade-ce` (root-caused as worker-crash-to-zombie /
listening-socket-held-open by a peer's diagnosis, recovered via `ddev
restart`). This is a repeat of the exact failure shape documented in this
session's own memory from earlier the same day ("never leave a pytest loop
running unattended in the fastapi container") - noted here because it
happened again despite that standing warning, and because it's the direct
reason findings #2 and #3 above carry unusually strong empirical evidence
(dozens of real concurrent-thread trials, not sandbox theory) - the same
loops that produced that evidence are what caused the outage. Scratch
debug scripts used for this diagnosis were removed before this commit;
no `data/*.db` file was touched, read-write, during any of it.

## Verdict: GO

The core mechanism (partial unique index closes the NULL-`exc_type` dedup
gap, additive, no schema rebuild) was independently confirmed at the
strongest evidence tier by the adversarial review and not weakened by
anything found since. All 1 must-fix and the in-scope should-fix items are
applied and empirically verified; the one should-fix left unapplied (item 5)
is a review-artifact accuracy note, not a code defect. The one item
deliberately **not** fixed here (item 3, the pre-existing WAL cold-start
race) has an explicit disposition — filed as issue #549 with reproduction
evidence, confirmed out of scope via direct comparison against unmodified
`origin/main`, and does not weaken this PR's own claims since the
regression test for this PR's actual race isolates around it. This PR is
ready for the PR-level review cycle (its own self-review, adversarial
review, and consolidation, per CLAUDE.md's requirement that this stacks on
top of, not substitutes for, this stage's cycle) before merge.

## What's still genuinely open (not blocking this PR)

- Issue #549 (WAL-mode cold-start race) - pre-existing, unrelated,
  unimplemented.
- The self-review doc's own "additional call sites" list is one entry short
  (item 5 above) - cosmetic, not corrected in the doc itself since doing so
  would edit an artifact this consolidation is reconciling, not extending.
