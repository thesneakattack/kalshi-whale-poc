"""Shared, closing SQLite connection helper - the single fix for the
30-module "opens a connection, never calls .close()" leak shape that
produced a real 6.8-hour fd-exhaustion incident (2026-09-02). Tier 0
(PR #501) already fixed 5 modules with their own per-module
@contextlib.contextmanager; this module is the shared version the
remaining 26 migrate onto. Design:
docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design.md.

Schema is registered as a callback (register_schema), not a DDL string -
a callback can express CREATE TABLE, CREATE INDEX, and add_column_if_missing
calls together, in one registration, matching what every real module in
this migration actually needs (confirmed: PR #484's every migrated module
needed a post-connect() escape hatch under the string-DDL design this
module supersedes). Keyed by table name alone, not (db_path, table) - this
repo's universal `monkeypatch.setattr(mod, "DB_PATH", tmp_path/...)` test
convention (64 test files) means a caller's db_path is routinely swapped at
test time; a path-keyed registry silently finds nothing for a monkeypatched
path (demonstrated by running the alternative design) - table-name keying
means the registered schema is found regardless of which literal path is
passed to connect().

Every existing `with _connect() as conn:` call site keeps working unchanged
once its owning module's _connect() is rewritten to wrap db.connect(...) -
this yields the same conn as before, but now closes it on exit. See each
migrated module's own migration task for the exact before/after diff."""
import contextlib
import sqlite3
import threading
from pathlib import Path
from typing import Callable

_SCHEMAS: dict[str, Callable[[sqlite3.Connection], None]] = {}
_SCHEMAS_LOCK = threading.Lock()


def register_schema(table_name: str, init_fn: Callable[[sqlite3.Connection], None]) -> None:
    """Registered once, at import time, by each table's owning (or, for the
    three capture_writer.py-owned tables this migration's D2 ruling covers,
    registering) module - keyed by table name alone, not db_path, so a
    caller's later db.connect(monkeypatched_path, tables=("t",)) finds the
    same registered init_fn regardless of which literal path is passed at
    connect time. Raises on a genuine conflict (a different init_fn already
    registered for this table name) rather than silently keeping the first
    one - the db-foundation-audit's own must-fix #1. Re-registering the
    identical callable (e.g. a module re-imported under test) is a no-op,
    not an error."""
    with _SCHEMAS_LOCK:
        existing = _SCHEMAS.get(table_name)
        if existing is not None and existing is not init_fn:
            raise ValueError(f"conflicting schema registration for table {table_name!r}")
        _SCHEMAS[table_name] = init_fn


@contextlib.contextmanager
def connect(db_path: Path, *, tables: tuple[str, ...] = (), busy_timeout_ms: int = 5000):
    """Every existing `with _connect() as conn:` call site's replacement.
    Opens, sets WAL + the given busy_timeout as one policy, runs each named
    table's registered init_fn (in the order given - significant only if
    one table's init_fn depends on another already existing in the same
    file), yields, and ALWAYS closes - this is the entire fix. tables=()
    (the default) runs no init_fn, for read-only/diagnostic callers whose
    owning module has already guaranteed the schema exists. A table name
    passed here with no registered init_fn (the owning module hasn't been
    imported, or the name is simply wrong) raises a bare KeyError - louder
    than the alternative design's silent 'no such table,' but still worth
    naming: each migrated module's own import graph must guarantee its
    register_schema call runs before its first connect() call."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        for table in tables:
            _SCHEMAS[table](conn)
        with conn:
            yield conn
    finally:
        conn.close()


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Shared replacement for the AST-identical per-module copies the
    architecture audit's §9.2 found - takes conn as a parameter, shares no
    state, so it doesn't need its caller to route through connect() above."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
