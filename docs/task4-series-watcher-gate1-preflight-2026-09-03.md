# Task 4 Gate 1 pre-flight: `services/series_watcher.py`

2026-09-03. Written analysis only, no code, no branch — deliberately held back from
implementation while an active whale-stream-path incident is being fixed on an adjacent hot
path (autotrade-3b fixing, autotrade-a7 reviewing), so attribution stays clean if anything
moves afterward. This is the thinking Task 4 needs, done now so implementation is fast once the
incident clears.

## 1. `_connect()` body in full — everything the migrated callback must preserve

`services/series_watcher.py:151-186`:

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")                                    # :158
    conn.execute(capture_writer.RAW_TRADES_DDL_SQL)                            # :159
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_series ...")       # :160
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_ticker ...")       # :161
    conn.execute("""CREATE TABLE IF NOT EXISTS book_snapshots (...)""")        # :162-183
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_ticker ...")             # :184
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_series ...")             # :185
    return conn
```

Two tables: `raw_trades` (shared, `capture_writer.RAW_TRADES_DDL_SQL`) and `book_snapshots`
(owned solely by this module, 16 columns, `id INTEGER PRIMARY KEY AUTOINCREMENT`). Four
indexes total, two per table. No `add_column_if_missing` calls, no `busy_timeout` set
currently (confirmed: `grep -n busy_timeout services/series_watcher.py` → no hits) — matches
D3's expectation that no module in this migration's scope sets it explicitly today.

**Mapping onto the `register_schema`/`connect(tables=...)` shape**: `raw_trades` registers via
D2's shared `capture_writer.init_raw_trades` (identical reasoning to Task 3/candidate_log.py —
that function only runs `RAW_TRADES_DDL_SQL`, not the two `raw_trades` indexes, so those two
indexes must run on the yielded connection, same pattern Task 3 already established for its own
non-`CREATE TABLE` statements). `book_snapshots` is NOT shared with any other module — no D2
concern — so `series_watcher.py` registers its own local `init_fn` for it (can include or
exclude its two indexes; either is defensible since there's no cross-module identity-conflict
risk for a table only this module ever registers).

## 2. D1: `_ensure_schema_aio` vs. `_connect()` — confirmed independently, not inherited

`_ensure_schema_aio()` (`:189-227`) is a byte-for-byte async mirror of `_connect()`'s DDL —
verified by reading both in full: same `PRAGMA journal_mode=WAL`, same
`capture_writer.RAW_TRADES_DDL_SQL`, same two `raw_trades` indexes (identical SQL text), same
`book_snapshots` `CREATE TABLE` (identical column list), same two `book_snapshots` indexes —
the only difference is `await conn.execute(...)` vs `conn.execute(...)`, confirming the
module's own docstring claim ("only the caller's execute differs, not the string itself") is
accurate, not just asserted.

`_ensure_schema_aio` has **3 real, active production call sites**, all via
`_aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)`: `:457`, `:567`, `:891`. This
is not a dormant/dead path — Task 4 must not touch it. D1's ruling (migrate only the sync
`_connect()`, leave `_ensure_schema_aio` and `_aio_db.connection_for` completely alone) is
confirmed correct and low-risk: the two paths share DDL text already (no duplication risk from
migrating one and not the other), and the async path's own connection pooling is a genuinely
different, already-correct pattern this migration explicitly declines to touch (per the design
spec's "Considered and declined: pooling" section).

## 3. PR #23's lock vs. every `_connect()` call site — independently verified, both sites

Two `_connect()` call sites in this module, not one:

- **`flush()`** (`:390-433`): `with _buffer_lock:` (`:414-415`) wraps only the buffer
  swap-and-clear (`books, _book_buffer = _book_buffer, []`); the lock is released **before**
  `with _connect() as conn:` is reached at `:419`. Confirmed by direct read of the function
  body — the lock's own scope ends at line 415, `_connect()` is called four lines later,
  outside any lock. Matches autotrade-73's earlier finding for this specific function,
  independently re-verified here rather than inherited.
- **`prune()`** (`:436-447`): no `_buffer_lock` interaction anywhere in this function — its
  `_connect()` call at `:445` has zero lock involvement, direct from the function's first line.
  This is the "other call site" this pre-flight was specifically asked to check, not just
  `flush()`; confirmed clean independently.

A third, non-`_connect()`-touching use of `_buffer_lock` exists at `record_book()`
(`:362-366`) — appends to `_book_buffer` under the lock, never opens a connection. Confirms the
lock and a DB connection are never held simultaneously anywhere in this file, not just in the
two functions this migration touches.

**Conclusion: no lock-ordering risk from this migration.** Both call sites are already
lock-free at the point `_connect()` is reached; migrating to `db.connect()` (which adds a
`close()` on exit but changes nothing about when the connection opens relative to
`_buffer_lock`) does not change this property.

## 4. Full cross-repo call-site census (the Task 3 lesson, applied)

`grep -rn "series_watcher\._connect\|sw\._connect\|series_watcher\._ensure_schema_aio"` across
the whole repo: **exactly one hit**, `tests/test_series_watcher.py:678`
(`test_connect_uses_the_shared_raw_trades_ddl`) — a plain `with sw._connect() as conn:`, no
spy/wrapper pattern, structurally identical to the majority of Task 3's call sites that needed
no fix. Specifically re-checked `tests/test_performance_regressions.py` (the file that held
Task 3's actual miss) for any series_watcher reference: **none found** — it seeds
`series_status`/`rejection_events` via `series_evaluator`/`candidate_log` only, never touches
`series_watcher`. No `_CountingConn`-style spy pattern targeting `series_watcher._connect`
exists anywhere in the current test suite, as far as a full-repo grep can confirm — this is not
a "the plan says so" claim, it's this pre-flight's own independent sweep.

Not exhaustively checked: whether any test spies on `_ensure_schema_aio` or
`_aio_db.connection_for` directly (out of scope — D1 says this migration doesn't touch that
path, so a spy on it isn't this migration's concern to fix even if one exists).

## 5. What would make this not a mechanical migration

- **`book_snapshots`'s ownership is genuinely solely this module's** — unlike Task 3's two
  tables (both `capture_writer`-owned), there's a real design choice here Task 3 didn't face:
  whether `book_snapshots`'s two indexes belong inside its `register_schema` callback or run on
  the yielded connection like `raw_trades`'s do. Either is correct; the implementation task
  should pick one and state why, not leave it implicit.
- **Two tables from one `_connect()` call, only one of which is D2-shared** — the migrated
  `_connect()` needs `tables=("raw_trades", "book_snapshots")`, mixing a shared-registration
  table with a solely-owned one in a single `connect()` call. Nothing here is unsafe (confirmed
  above: no lock conflict, D2's identity rule only binds `raw_trades`), but it's a shape Task 3
  didn't need to handle (both of Task 3's tables were `capture_writer`-owned), so it isn't
  purely "the same pattern as Task 3, just copy it."
- **This module has a real async twin that must stay untouched** — the actual implementation
  risk is not in the sync migration itself (which is genuinely mechanical per points 1-4 above)
  but in the discipline of touching *only* `:151-186` and leaving `:189-227` and every one of
  its 3 call sites completely alone. A migration that "helpfully" also touched
  `_ensure_schema_aio` for consistency would be a real, avoidable scope violation — worth an
  explicit test or diff-check (mirroring Task 4's own plan text's "verifies this with a
  targeted git diff grep, not just a stated intention") confirming `_ensure_schema_aio` has
  zero diff.
- Otherwise: this genuinely is mechanical. No event-loop exposure was found for either
  `_connect()` call site during this pre-flight (neither `flush()` nor `prune()` is called from
  `services/reset/routes.py` or any other async handler checked so far — `series_watcher.py`
  was not in the general-bucket pre-audit's list, so this is a fresh check, not inherited: `grep
  -rn "series_watcher\." services/reset/routes.py` → no hits); no `busy_timeout` currently set
  (D3 applies cleanly); the lock/connect relationship is provably safe at both call sites, not
  assumed.
