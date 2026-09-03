# Order-dependent test failures: root cause (issue #525)

Read-only investigation. No fix attempted — per the PM's explicit
instruction, the fix "may be trivial or may touch shared test
infrastructure that every other suite depends on," and that decision
comes after the mechanism is known, not before.

## Answering the four things asked for, up front

1. **Minimal reproducer**: exactly **2 files**, in this exact order —
   `tests/test_trading_gate.py` then `tests/test_main_tick_executor_wiring.py`.
   Narrowed from the issue's original 9-file combination; the other 7
   files (`test_whale_stream_stage_timing.py`, `test_kalshi_contracts.py`,
   `test_series_watcher.py`, `test_position_management_concurrency.py`,
   `test_db.py`, `test_candidate_log.py`, `test_capture_writer.py`) are
   not part of the trigger at all.
2. **The actual mechanism**: a split-brain between two separate,
   independent "where does candidate_log's data live" pointers —
   `services/candidate_log.py`'s `DB_PATH` module attribute, and
   `services/capture_writer.py`'s `_STORE_PATHS["rejected_candidates"]`/
   `["rejection_events"]` module-level dict entries — that a bare,
   unguarded module-level assignment in one test file desynchronizes from
   the other, depending on pytest's collection order. Full trace below.
3. **Does anything touch a real `data/*.db`?** **No.** Both DB_PATH
   values involved are `tempfile.mkdtemp()`-created throwaway
   directories, categorically outside the repo's `data/` directory by
   construction — confirmed directly from the exact paths each involved
   line sets, not inferred. `runtime_isolation.py`'s `sqlite3.connect`
   guard (which blocks real `data/` access) never even engages here,
   because neither path it would need to block is a repo data path in
   the first place. This is safe to deprioritize on urgency grounds,
   though not on correctness grounds (see below).
4. **Predates tonight's changes?** Yes — reproduces identically on both
   the issue's cited commit (`fc481ac`) and current `main`
   (`b1b8b49` at investigation time). The two module-level assignment
   blocks responsible (`test_trading_gate.py:74-87`,
   `test_main_tick_executor_wiring.py:37-46`) have not changed in either
   diff; this is long-standing, not newly introduced.

## The mechanism, traced from source

`candidate_log.record_rejection()` and `candidate_log.gate_summary()`
don't write/read a single, unified store — `record_rejection()`'s writes
go through `capture_writer.submit(...)` (an in-memory buffer, flushed
later), while `gate_summary()` calls `capture_writer.flush_now(...)`
(forcing that buffer to disk) and then does its own `SELECT` via
`candidate_log._connect()`. Two different code paths, two different
sources of truth for "where is the file":

```python
# services/capture_writer.py:92-96 — hardcoded at import time
_STORE_PATHS: dict[str, Path] = {
    "raw_trades": .../ "data" / "series_watcher.db",
    "rejection_events": .../ "data" / "candidate_log.db",
    "rejected_candidates": .../ "data" / "candidate_log.db",
}
```

```python
# services/candidate_log.py:65 — separate constant, also hardcoded
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_log.db"
```

These are **two independent module-level attributes with no relationship
to each other in the source** — nothing keeps them in sync automatically.
Every test file that wants candidate_log's data isolated has to
separately redirect *both*.

`test_trading_gate.py` does this correctly, at its own module level
(runs once, at import/collection time):

```python
# tests/test_trading_gate.py:74, 82-87
cl_module.DB_PATH = _tmp_dir / "candidate_log.db"          # tmp dir "A"
cw_module._STORE_PATHS = {
    "rejected_candidates": cl_module.DB_PATH, "rejection_events": cl_module.DB_PATH,
}
cw_module._buffers = {"rejected_candidates": {}, "rejection_events": []}
cw_module._last_flush_at = {"rejected_candidates": 0.0, "rejection_events": 0.0}
cw_module._dropped_counts = {"rejected_candidates": 0, "rejection_events": 0}
```

Both pointers set to the same tmp dir (`A`), in the same block — correct,
by itself.

`test_main_tick_executor_wiring.py` does the DB_PATH half of the same
redirect, at its own module level, but never touches `_STORE_PATHS`:

```python
# tests/test_main_tick_executor_wiring.py:29, 43
_tmp_dir = Path(tempfile.mkdtemp(prefix="tick_executor_wiring_"))  # tmp dir "B"
...
cl_module.DB_PATH = _tmp_dir / "candidate_log.db"   # only this one pointer
```

Neither block is a `monkeypatch.setattr` — both are bare, permanent
module-level assignments, executed once during pytest's **collection**
phase (which imports every specified file before any test runs), and
never reverted for the rest of the process.

**pytest preserves command-line file order for collection** — verified
directly with `--collect-only` against the issue's exact 9-file command,
not assumed: files are collected in the order given, and (separately,
also verified, via the reversed-order test below) execute in that same
order too. This project pins `pytest-xdist` (`requirements-dev.txt:18`)
but neither the issue's repro command nor any check in this investigation
passed `-n`/`--dist`, so worker-distribution reordering wasn't a factor
here; whether CI's own invocation ever runs this file combination under
`-n auto` (which could scramble which worker collects which file first)
wasn't checked and would be worth confirming if this ever needs to be
ruled in or out as a CI-specific risk. So when `test_trading_
gate.py` precedes `test_main_tick_executor_wiring.py` on the command
line:

1. `test_trading_gate.py` imports first → `cl_module.DB_PATH = A`,
   `cw_module._STORE_PATHS["rejected_candidates"] = A` (in sync).
2. `test_main_tick_executor_wiring.py` imports next → `cl_module.DB_PATH
   = B` — `cw_module._STORE_PATHS` is never touched, so it still reads
   `A`. **Split-brain, permanent for the rest of the process**: `DB_PATH
   = B`, `_STORE_PATHS["rejected_candidates"] = A`.
3. Collection finishes; execution begins. `test_trading_gate.py`'s own
   tests run first (same file order governs execution too). The two
   failing tests call `record_rejection(...)` (buffers in memory, no path
   involved yet) then `gate_summary()`, which calls `capture_writer.
   flush_now("rejected_candidates")` — this writes the row to `_STORE_
   PATHS["rejected_candidates"] = A` (still correct, unaffected) — then
   runs its own `SELECT` through `candidate_log._connect()`, which uses
   `cl_module.DB_PATH = B` (corrupted) — **a different, empty database**.
   Zero rows found. `assert matching and matching[0]["resolved_count"]
   >= 1` → `assert ([])`. Exactly the observed failure, both tests,
   every time.

**Confirmed the order-dependence directly, not just reasoned about it**:
reversing the two files (`test_main_tick_executor_wiring.py` then
`test_trading_gate.py`) makes the failure disappear entirely (218
passed, 0 failed) — because then `test_trading_gate.py`'s own block runs
*last*, resetting both `DB_PATH` and `_STORE_PATHS` back into sync,
overwriting whatever `test_main_tick_executor_wiring.py` had set. This is
the single piece of evidence that most precisely nails the mechanism:
it's not "these two files don't get along," it's specifically "whichever
of these two files' `cl_module.DB_PATH` reassignment runs *last* during
collection determines the value `DB_PATH` ends up with, but only
`test_trading_gate.py`'s block ever updates `_STORE_PATHS` alongside it."

No exception, error, or `fault_log` entry appears anywhere in this
chain — both the write (to `A`) and the read (from `B`) individually
*succeed* as far as their own code is concerned; the bug is a pure
logical routing mismatch between two operations that each believe they
agree on where the data lives. This is part of why it's easy to miss:
there's no stack trace pointing at the actual defect, only a downstream
assertion that looks like the *test's own* expectation is wrong.

## Why the original 9-file reproduction included 7 unrelated files

None of the other 7 files in the issue's original combination touch
`cl_module.DB_PATH`, `cw_module._STORE_PATHS`, or `cw_module._buffers` at
module level — checked each file directly. `test_series_watcher.py` and
`test_position_management_concurrency.py` redirect `paper_broker.DB_PATH`
via `monkeypatch.setattr` **inside a fixture function**, not at module
level — properly scoped, auto-reverted, harmless to this mechanism.
`test_db.py`'s own `_SCHEMAS`-clearing fixture (`monkeypatch.setattr(db,
"_SCHEMAS", {})`) looked like a plausible lead early in this
investigation (a shared-registry pattern in the same family of bug), but
is unrelated: it's per-test, monkeypatch-scoped, and `test_db.py` collects
and runs *after* the two failing tests regardless — it cannot be the
cause of a failure in tests that already ran before it. Including it in
the original reproduction command was incidental, not causal; the real
trigger is exactly the two files named above, order-sensitive between
just those two.

## A related, structurally identical risk spotted in passing — not yet confirmed to cause a failure

While reading `test_trading_gate.py:82-87`'s block closely, one more
thing stood out, worth flagging honestly even though chasing it further
was out of this investigation's scope: `cw_module._STORE_PATHS = {...}`
and `cw_module._buffers = {...}` are **whole-dict replacements**, not
merges — and the replacement dict only contains `"rejected_candidates"`/
`"rejection_events"` keys, not `"raw_trades"` (`capture_writer.py`'s
third store, owned by `series_watcher.py`). After `test_trading_gate.py`'s
module-level code runs, `cw_module._STORE_PATHS` and `cw_module._buffers`
**no longer have a `"raw_trades"` key at all**, for the rest of the
process, unless some later file's own `monkeypatch.setattr` supplies one
back (several do, scoped to their own tests only). This wouldn't crash
anything (`_flush_store`'s `db_path = _STORE_PATHS[store]` lookup is
inside its own `try`, and a `KeyError` there gets caught and silently
counted as a dropped row, same as any other write failure; `_run()`'s
`for store, rows in list(_buffers.items())` simply never iterates a
missing key, no error either) — but it is a real, structurally identical
"one file's module-level reset silently un-syncs shared capture_writer
state for everyone downstream" pattern, just for `raw_trades` instead of
`rejected_candidates`/`rejection_events`. Not confirmed to currently
manifest as any observable test failure (would need its own targeted
investigation to establish that either way) — noted here as a sibling
risk in the same mechanism family, for whoever picks up a fix to be aware
of, not as a second confirmed bug.

## Disposition

**Genuinely benign? No — this is a real defect, just a low-severity,
test-only one.** It doesn't touch live data (§3 above), and CI's full
unscoped suite doesn't hit it (confirmed in the issue's own "Update"
section, corroborated here: the specific 2-file ordering that triggers it
doesn't occur in CI's own file collection). But it's not "two tests
sharing a module-level cache with no correctness implication" either —
it's two production-ish test-infrastructure files disagreeing about where
shared test state lives, silently, with no error surfaced anywhere in the
chain. A future test file (or a reordering of the existing ones, which
CI's own collection order isn't pinned against changing) could trigger it
again, hit a *different* pair of tests than these two, and produce a
result that looks like a real product bug rather than a test-harness
mismatch — exactly the failure shape CLAUDE.md's data-plane rule warns
about, just contained to test infrastructure this time instead of the
live `paper_broker.db` the 2026-08-23 incident hit.

The actual fix (not attempted here, per assignment) has at least two
honest candidates: (a) have `test_main_tick_executor_wiring.py`'s
module-level block also set `cw_module._STORE_PATHS`/`_buffers` for
candidate_log's stores, mirroring `test_trading_gate.py`'s own pattern
(narrow, fixes exactly this pair, doesn't address the general "any two
files could do this" class); or (b) centralize `capture_writer`'s store
paths into `tests/support/runtime_isolation.py`'s own registry-based
redirect (the same mechanism that already prevents this exact class of
bug for every `PERSISTENCE_MODULE_PATHS`-registered module) so no
individual test file has to remember to keep two separate pointers in
sync by hand — `runtime_isolation.py`'s own docstring describes fixing an
almost identical incident this way already (the 2026-08-23 collection-
order bug), and `capture_writer` isn't in `PERSISTENCE_MODULE_PATHS` at
all today, confirmed by reading the tuple directly (line 46-77) —
`"services.capture_writer"` is absent.

**Decision (autotrade-1d, after reading this doc): (a), the narrow
per-file sync — not (b).** Reasoning: centralizing touches shared test
infrastructure every suite depends on, and this repo has twice built
confident sweeping changes in adjacent territory that had to be shelved
or reverted (`tick_executor.connection_for()`; PR #424's elastic-pool
attempt) — a regression in `runtime_isolation.py` would land on every
suite at once, a materially worse failure mode than the narrow defect
this doc found. **But the narrow fix alone leaves the trap armed**: the
next test file that redirects `candidate_log.DB_PATH` without also
redirecting `capture_writer._STORE_PATHS` hits this identically, and
fails exactly as silently. Per CLAUDE.md's investigation-to-guard rule,
pair the narrow fix with an assertion that the two pointers agree —
placed so a future divergence fails loudly at collection or session
start, not as a mysterious empty read three files later. **Disposition:
CI guard** (a loud, fail-fast assertion added alongside the narrow fix),
not a runtime diagnostic (this is test-infrastructure, not live-app
code) and not shared logic (rejected — that's option (b), the systemic
centralization, deliberately not chosen here for the reason above).

**A separate coupling worth flagging for whoever tracks the `db.py`
migration, not this issue's own concern to resolve:** `capture_writer.py`
imports raw `sqlite3` directly (confirmed: no `from services import db`,
no `db.connect()`/`db.register_schema()` anywhere in the file) — its
`_flush_store` always opens `_STORE_PATHS[store]` via bare
`sqlite3.connect()`, entirely independent of whatever `services/db.py`'s
unified layer does. Task 3 of the persistence-layer migration moved
`candidate_log.py`'s own read/write paths onto `db.connect()`/
`register_schema()`; `capture_writer` was deliberately left out of that
migration's scope. In production this coupling is currently inert —
nothing reassigns `candidate_log.DB_PATH` after import, so there's no
live divergence risk today — but it means `candidate_log.db`'s on-disk
location has two independent sources of truth in the codebase (`services/
candidate_log.py:65`'s `DB_PATH`, and `services/capture_writer.py:92-96`'s
hardcoded `_STORE_PATHS` entries), and if a future migration task changes
how `DB_PATH` is resolved without capture_writer following along, this
exact test-infrastructure bug's production-code analog becomes possible.
Worth a sentence on whichever plan/issue tracks that migration's later
tasks, not a blocker for anything here.
