# Gate 1 pre-audit: 5 named modules — 2026-09-03

Per-module read of the actual `_connect()` body and schema/DDL init path, in full, for `risk_manager.py`, `paper_broker.py`, `candidate_ledger.py`, `series_watcher.py`, `settlement_edge.py`. Every claim below is a direct citation, not inferred. Also includes each module's test call-site shape (`with`-wrapped vs. bare-assignment) per the correction that test-only callers of `_connect()` are a real migration risk the hybrid design must account for (`ce._connect()` in `tools/coordination_engine.py`'s own tests is bare-assignment and would break on a context-manager-only API — see that finding).

## `services/risk_manager.py`

- `_connect(db_path: Path)` — `services/risk_manager.py:49-75`. Parameterized (takes `db_path` explicitly, matching the PM's own `risk_manager.py:49` example).
- Non-`CREATE TABLE` statements the migrated callback must preserve:
  - `PRAGMA journal_mode=WAL` — `:58`.
  - `_add_column_if_missing(conn, "risk_meta", "day_start_date", "TEXT")` — `:74`, one guarded `ALTER TABLE`.
  - **No `busy_timeout` PRAGMA set at all** — notably absent, unlike several sibling modules. Confirm with the plan owner whether this was deliberate or an oversight before assuming db.py's own default (5000ms) is a safe drop-in.
  - No indexes, no `row_factory`, no `isolation_level`.
- Instance-method wrapper: `def _connect(self): return _connect(self.db_path)` — `:124-125`. All real call sites use `with self._connect() as conn:` (`:102`, `:128`).
- Monkeypatch mechanics: `self.db_path = db_path or DB_PATH` at `__init__` (`:89`), module-level `DB_PATH` resolved at *construction* time, not import time — the class's own comment (`:83-88`) states this explicitly so `monkeypatch.setattr(rm, "DB_PATH", ...)` keeps working. `RiskManager` also accepts an explicit `db_path` constructor arg for "a second, independent risk tracker" (`:81`, `:86-88`) — two mechanisms, same as `paper_broker.py` below.
- Test call-site shape: `tests/test_risk_manager.py` has **zero direct `_connect()` calls** — interacts purely through `RiskManager`'s public API, `monkeypatch.setattr` on the module-level `DB_PATH` before constructing an instance.

## `services/paper_broker.py`

- `_connect(db_path: Path)` — `services/paper_broker.py:163-267`. Parameterized, same shape as `risk_manager.py`.
- Non-`CREATE TABLE` statements:
  - `PRAGMA journal_mode=WAL` — `:172`.
  - **13 `_add_column_if_missing` calls** (`:209, 210, 215, 216, 222, 226, 236, 242, 243, 244, 245, 265, 266`) — by far the most schema evolution of the 5 modules. Every one is a real, separately-dated migration (config_fingerprint, entry_fee/fee, hold_to_settlement, signal_seen_at, excluded, four netting_* columns, two pending_orders columns) — the migrated callback needs all 13, in order, not a representative subset.
  - One explicit index: `CREATE INDEX IF NOT EXISTS idx_trades_excluded ON trades (excluded)` — `:237`.
  - No `busy_timeout` PRAGMA (same gap as `risk_manager.py`), no `row_factory`, no `isolation_level`.
- Instance-method wrapper: `def _connect(self): return _connect(self.db_path)` — `:337-338`, identical shape to `risk_manager.py`. Real call sites all use `with self._connect() as conn:` (`:294` and 8 more, per the earlier orphan-check survey).
- Monkeypatch mechanics: identical pattern and identical comment shape to `risk_manager.py` — `self.db_path = db_path or DB_PATH` (`:278`), documented at `:272-277` as resolved at construction time specifically so `monkeypatch.setattr(pb, "DB_PATH", ...)` keeps working.
- Test call-site shape: `tests/test_paper_broker.py` has **zero direct `_connect()` calls** — same as `risk_manager.py`, public-API-only.

## `services/candidate_ledger.py`

- `_connect()` — `services/candidate_ledger.py:20-38`. Parameterless, reads module-level `DB_PATH` directly.
- Non-`CREATE TABLE` statements:
  - `PRAGMA journal_mode=WAL` — `:33`, added later per its own comment ("code-review fix, finding #4 - every sibling persistence module already has this; this one was missed") — worth noting since it shows this module's schema has needed a *behavioral* patch before, not just column additions.
  - **Nothing else.** No `add_column_if_missing`, no indexes, no `row_factory`, no `isolation_level`, no `busy_timeout`. Single table (`candidates`), single unconditional `CREATE TABLE IF NOT EXISTS` (`:35-37`), no schema evolution ever. Simplest of the 5 modules by a wide margin.
- No instance-method wrapper — this is a pure-function module (no class), all four public functions (`claim`, `record_decision`, `decision_for`, `stats`) call the module-level `_connect()` directly, all four `with`-wrapped (`:48, 60, 68, 74`).
- Test call-site shape: `tests/test_candidate_ledger.py` monkeypatches `DB_PATH` (`:7`) and **does** call `_connect()` directly once, correctly `with`-wrapped: `with candidate_ledger._connect() as conn:` (`:56`) — hybrid-compatible as written, unlike `coordination_engine.py`'s bare-assignment test pattern.

## `services/series_watcher.py`

By far the most structurally complex of the 5 — two independent schema-init paths, a shared cross-module DDL, and the PR #23 lock.

- `_connect()` — `services/series_watcher.py:151-186`. Parameterless.
- Non-`CREATE TABLE` statements:
  - `PRAGMA journal_mode=WAL` — `:158`.
  - `conn.execute(capture_writer.RAW_TRADES_DDL_SQL)` — `:159`. **This DDL is not owned by this module** — it's a plain SQL string defined in `services/capture_writer.py:132-151`, whose own comment (`:152`) states it's "Shared with services/series_watcher.py's `_connect()`/`_ensure_schema_aio()`". Two separate modules execute the identical string today. **Concrete integration risk for the migration**: `db.py`'s `register_schema()` (as fixed in `fix/db-foundation-must-fix-tests`) now identity-checks the registered `init_fn` callable and raises on a genuine mismatch (see that branch's commit). If `capture_writer.py` and `series_watcher.py` each migrate independently and each wrap `RAW_TRADES_DDL_SQL` in their *own* separately-defined `init_fn` closure, that's two different callables for the same `(db_path, "raw_trades")` pair — even though they run byte-identical SQL, `register_schema` would raise. The migration plan needs both modules to import and register the exact same `init_fn` object (e.g. one function defined once, likely alongside `RAW_TRADES_DDL_SQL` itself in `capture_writer.py`, imported by `series_watcher.py` rather than re-wrapped) — not two independently-written wrappers around the same string.
  - Two indexes on `raw_trades`: `idx_raw_trades_series` and `idx_raw_trades_ticker` — `:160-161`.
  - Two indexes on `book_snapshots`: `idx_book_ticker` and `idx_book_series` — `:184-185`.
  - No `add_column_if_missing`, no `busy_timeout`, no `row_factory`/`isolation_level` in the sync path.
- **Second, async schema-init path** — `_ensure_schema_aio(conn)`, `:189-227`. Byte-identical DDL to `_connect()` above (WAL PRAGMA, the shared `RAW_TRADES_DDL_SQL`, both index pairs, the `book_snapshots` table), but every statement uses `await conn.execute(...)` instead of `conn.execute(...)`, run once per `(loop, db_path)` key via `services/diagnostics/_aio_db.py`'s `connection_for(DB_PATH, schema_init=_ensure_schema_aio)` (used at `:457, 567, 891`). **`db.py` as it exists today (commit 17b2e8f + the must-fix-tests branch) has no async equivalent at all** — `connect()` is a plain `@contextlib.contextmanager`, not an `@asynccontextmanager`, and `register_schema`'s `init_fn` is called synchronously inside it. This module cannot fully migrate onto the current `db.py` API without either (a) `db.py` growing a genuine async counterpart, or (b) `series_watcher.py` keeping `_ensure_schema_aio` as a permanent, separate, hand-maintained duplicate of whatever DDL `db.py`'s registered `init_fn` ends up owning — which reintroduces exactly the "two copies of one schema" problem `db.py` exists to eliminate, just for this one module. This is the single largest open question for `series_watcher.py`'s migration and should be a named decision point in the plan, not an assumed detail.
- **PR #23 lock, exact position relative to `_connect()`**: `_buffer_lock = threading.Lock()` (`:140`) guards only in-memory buffer operations, never any `_connect()`/SQL work. In `record_book()` (`:363-364`), the lock wraps `_book_buffer.append(row)` only. In `flush()` (`:414-419`), the lock wraps *only* the swap-and-clear (`books, _book_buffer = _book_buffer, []`, `:414-415`) — the lock is released (the `with _buffer_lock:` block exits) **before** `with _connect() as conn:` is reached at `:419`. The lock and the DB connection are never held simultaneously; the module's own comment (`:410-413`) states this is deliberate ("holding a lock across blocking disk I/O would needlessly serialize captures against a flush that's still writing"). **Consequence for the migration**: no lock-ordering or deadlock interaction to design around — `db.connect()` can be substituted directly at `:419` (and the identical shape in `capture_stats`/`funnel`/`reconcile`'s async paths) with zero changes needed to `_buffer_lock`'s own scope.
- Monkeypatch mechanics: no constructor, pure module — `monkeypatch.setattr(sw, "DB_PATH", ...)` is the only mechanism (`tests/test_series_watcher.py:22`, its own header comment at `:3` calls this "CLAUDE.md's standing rule").
- Test call-site shape: most of `tests/test_series_watcher.py` bypasses `_connect()` entirely, asserting directly via `sqlite3.connect(sw.DB_PATH)` (10+ call sites, e.g. `:94, 118, 129...`). Exactly one test calls `sw._connect()` directly, correctly `with`-wrapped: `test_connect_uses_the_shared_raw_trades_ddl` (`:671-680`), `with sw._connect() as conn:` at `:678`.

## `services/settlement_edge.py`

- `_connect()` — `services/settlement_edge.py:49-83`. Parameterless.
- Non-`CREATE TABLE` statements:
  - `PRAGMA journal_mode=WAL` — `:52`.
  - Two indexes: `idx_se_window` (`:76-78`, plain composite index) and **`idx_se_unresolved`** (`:79-82`) — a **partial index** (`... WHERE settled_yes IS NULL`). This is the only partial index found across all 5 modules; the migrated callback must preserve the `WHERE` clause exactly, not just the column list, or the index silently stops matching SQLite's own query planner the way it does today.
  - No `add_column_if_missing` anywhere — single-shot `CREATE TABLE IF NOT EXISTS` (`:53-75`), no schema evolution has ever been needed.
  - `conn.row_factory = sqlite3.Row` — but **set at the call site, not inside `_connect()`**: `edge_report()` sets it explicitly after obtaining `conn` (`:275-276`), no other caller in this file does. Since this is caller-side (assigned on the yielded connection object, after `with db.connect(...) as conn:` returns it), it needs no special handling from `db.py` itself — just carries over unchanged at that one call site. Flagging only so it isn't assumed to be a `_connect()`-level setting when it isn't.
  - No `isolation_level`, no `busy_timeout`.
- No instance-method wrapper — pure-function module like `candidate_ledger.py`/`series_watcher.py`. All real call sites (`:193, 215, 275, 365, 392, 404, 414`) use `with _connect() as conn:`.
- Monkeypatch mechanics: pure module-level `DB_PATH`, `monkeypatch.setattr(se, "DB_PATH", ...)` (`tests/test_settlement_edge.py:20`).
- Test call-site shape: `tests/test_settlement_edge.py` never calls `_connect()` directly — asserts via raw `sqlite3.connect(se.DB_PATH)` at three call sites (`:58, 77, 260`).

## Cross-module summary

| module | `_connect` shape | busy_timeout set? | schema evolution | indexes | async schema-init path | test calls `_connect()`? |
|---|---|---|---|---|---|---|
| risk_manager.py | parameterized + instance wrapper | no | 1 `add_column_if_missing` | none | no | no |
| paper_broker.py | parameterized + instance wrapper | no | 13 `add_column_if_missing` | 1 | no | no |
| candidate_ledger.py | parameterless | no | none | none | no | yes, `with`-wrapped |
| series_watcher.py | parameterless | no | none | 4 (2 tables) | **yes — no db.py equivalent exists** | yes, `with`-wrapped |
| settlement_edge.py | parameterless | no | none | 2 (1 partial) | no | no |

Two findings that apply across all 5, not just one module:
1. **None of the 5 sets `busy_timeout` explicitly** — every one of them predates the `busy_timeout_ms` parameter added to `db.py` in `fix/db-foundation-must-fix-tests`. Worth a single explicit decision (not 5 separate ones) on what value each should migrate to, rather than silently inheriting `db.py`'s 5000ms default without anyone deciding that's right for, say, `paper_broker.py`'s hot execution path.
2. **Zero of the 5 tests calls `_connect()` in the bare-assignment shape** that broke for `coordination_engine.py` — every test that does call `_connect()` directly (`candidate_ledger.py`, `series_watcher.py`) already does so `with`-wrapped. The other 3 don't call it at all. None of these 5 modules carries the specific test-migration risk the `coordination_engine.py` correction found — that risk is real, but it isn't universal, and this pre-audit found no instance of it in this batch.
