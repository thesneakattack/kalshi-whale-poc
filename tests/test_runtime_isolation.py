"""
Proves the two guarantees tests/support/runtime_isolation.py exists to give:
every registered persistence module's DB_PATH is redirected outside the
repository's real data/ directory, and sqlite3.connect itself hard-refuses
any path under that directory even for a module the registry doesn't know
about. See tests/support/runtime_isolation.py for the incident that made
"remember to redirect DB_PATH" not good enough on its own.
"""
import ast
import sqlite3
from pathlib import Path

import pytest

from tests.support.runtime_isolation import (
    DATA_DIR_MODULE_PATHS,
    PERSISTENCE_MODULE_PATHS,
    PINNED_CONFIG_SECTIONS,
    loaded_registered_modules,
    repo_data_dir,
    repo_root,
)


@pytest.fixture
def isolation_repo_root() -> Path:
    return repo_root()


def test_repo_data_sqlite_connection_is_blocked(isolation_repo_root):
    forbidden = isolation_repo_root / "data" / "forbidden-test.db"
    assert not forbidden.exists()
    with pytest.raises(AssertionError, match="live repository data"):
        sqlite3.connect(forbidden)
    assert not forbidden.exists()


def test_repo_data_sqlite_connection_is_blocked_for_string_path(isolation_repo_root):
    forbidden = isolation_repo_root / "data" / "forbidden-test-2.db"
    with pytest.raises(AssertionError, match="live repository data"):
        sqlite3.connect(str(forbidden))
    assert not forbidden.exists()


def test_repo_data_sqlite_connection_is_blocked_for_relative_path(monkeypatch, isolation_repo_root):
    monkeypatch.chdir(isolation_repo_root)
    with pytest.raises(AssertionError, match="live repository data"):
        sqlite3.connect("data/forbidden-test-3.db")
    assert not (isolation_repo_root / "data" / "forbidden-test-3.db").exists()


def test_memory_connection_is_not_blocked():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
    finally:
        conn.close()


def test_registered_persistence_modules_are_redirected_outside_repo_data():
    assert len(PERSISTENCE_MODULE_PATHS) > 0
    data_dir = repo_data_dir().resolve()
    for module in loaded_registered_modules():
        path = Path(module.DB_PATH).resolve()
        assert data_dir != path
        assert data_dir not in path.parents


def test_persistence_module_paths_cover_every_current_db_path_owner(isolation_repo_root):
    """Cross-checks the registry against a live AST scan of services/ so a
    newly added DB_PATH owner can't silently ship unregistered."""
    owners = set()
    for path in (isolation_repo_root / "services").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "DB_PATH"
                for target in node.targets
            ):
                rel = path.relative_to(isolation_repo_root).with_suffix("")
                owners.add(".".join(rel.parts))
                break

    missing = owners - set(PERSISTENCE_MODULE_PATHS)
    assert not missing, f"DB_PATH owners missing from PERSISTENCE_MODULE_PATHS: {missing}"

    stale = set(PERSISTENCE_MODULE_PATHS) - owners
    assert not stale, f"PERSISTENCE_MODULE_PATHS entries with no current DB_PATH owner: {stale}"


# --- DATA_DIR (directory-glob constant) isolation - QCP Task 18 -----------
#
# A second, distinct constant shape from DB_PATH (one module -> a whole
# *directory* it globs *.db files from, not one file it owns) - see
# DATA_DIR_MODULE_PATHS's own comment in runtime_isolation.py for the real
# GET /api/quality/summary 500 this closed.


def test_registered_data_dir_modules_are_redirected_outside_repo_data():
    import importlib

    assert len(DATA_DIR_MODULE_PATHS) > 0
    data_dir = repo_data_dir().resolve()
    for name in DATA_DIR_MODULE_PATHS:
        module = importlib.import_module(name)
        path = Path(module.DATA_DIR).resolve()
        assert data_dir != path
        assert data_dir not in path.parents


def test_data_dir_module_paths_cover_every_current_data_dir_owner(isolation_repo_root):
    """Same cross-check discipline as test_persistence_module_paths_cover_
    every_current_db_path_owner above, for DATA_DIR instead of DB_PATH."""
    owners = set()
    for path in (isolation_repo_root / "services").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "DATA_DIR"
                for target in node.targets
            ):
                rel = path.relative_to(isolation_repo_root).with_suffix("")
                owners.add(".".join(rel.parts))
                break

    missing = owners - set(DATA_DIR_MODULE_PATHS)
    assert not missing, f"DATA_DIR owners missing from DATA_DIR_MODULE_PATHS: {missing}"

    stale = set(DATA_DIR_MODULE_PATHS) - owners
    assert not stale, f"DATA_DIR_MODULE_PATHS entries with no current DATA_DIR owner: {stale}"


def test_quality_summary_route_does_not_open_live_repo_data():
    """The actual regression guard for the bug this task's own DATA_DIR_
    MODULE_PATHS fix closed: tests/support/e2e_server.py (the real browser-
    E2E harness) drives GET /api/quality/summary with no per-file fixture
    of its own, relying entirely on install_runtime_isolation() (already
    applied globally by conftest.py before this test module was even
    collected) - if DATA_DIR redirection regresses, storage_health.
    inventory_data_dir would try to open real data/accounts.db and the
    sqlite3.connect guard would raise, turning into a 500 here exactly like
    it did live (confirmed via the real browser test before this fix)."""
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        resp = client.get("/api/quality/summary")

    assert resp.status_code == 200
    assert "storage" in resp.json()


# --- config_store pin (issue #229) -----------------------------------------
#
# A third shape: not a path the suite must never touch, but a file whose
# committed VALUES the suite must never inherit - config/settings.yaml is
# the running app's live state, committed as-is. See PINNED_CONFIG_SECTIONS'
# own comment in runtime_isolation.py for the 16-test failure that made
# "patch it in the test that cares" not good enough on its own.


def _committed_config_section(section: str) -> dict:
    """The committed config/settings.yaml, read directly and on purpose -
    never through config_store.get(), which the pin rewrites. The one place
    in the suite that is meant to see the file's own value."""
    from ruamel.yaml import YAML

    from services.config.config_store import CONFIG_PATH

    with open(CONFIG_PATH) as f:
        return YAML(typ="safe").load(f)[section]


def test_config_store_get_serves_the_pinned_section_not_the_stores_own_value(monkeypatch):
    """Proof the autouse pin fires, every run, whatever the file says: plant
    a value no test could expect straight into the singleton's in-memory
    copy (its mtime is unchanged, so an unpinned get() would serve exactly
    that) and read it back through the suite-facing get()."""
    from services.config.config_store import config_store

    for section, pinned in PINNED_CONFIG_SECTIONS.items():
        monkeypatch.setitem(config_store._data, section, {key: "NOT-PINNED" for key in pinned})
        served = config_store.get()[section]
        assert served == pinned
        assert served is not pinned  # a copy: mutating it cannot edit the registry
        assert config_store.get()[section] == pinned  # and the next read is pinned again


def test_gateway_queue_topology_comes_from_the_pin_not_the_file(monkeypatch):
    """The #229 instance itself: a KalshiStreamGateway built with no config
    patch routes by the pinned two_consumer_mode, not by whatever the
    committed file (or a test's leftover update()) says."""
    from services.config.config_store import config_store
    from services.kalshi.websocket import KalshiStreamGateway

    pinned = PINNED_CONFIG_SECTIONS["realtime_data_plane"]["two_consumer_mode"]
    monkeypatch.setitem(config_store._data, "realtime_data_plane", {"two_consumer_mode": not pinned})
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")
    assert gw._two_consumer_mode() is pinned


def test_a_config_store_a_test_builds_itself_is_not_pinned(tmp_path):
    """Scope of the pin: the shared singleton only. tests/test_config_store.py
    is about ConfigStore reading a file, and must keep seeing its file."""
    from services.config.config_store import ConfigStore

    path = tmp_path / "settings.yaml"
    path.write_text("realtime_data_plane:\n  two_consumer_mode: NOT-PINNED\n")
    assert ConfigStore(path).get()["realtime_data_plane"] == {"two_consumer_mode": "NOT-PINNED"}


def test_pinned_config_sections_cover_every_key_of_the_committed_section():
    """Same cross-check discipline as PERSISTENCE_MODULE_PATHS: a flag added
    to a pinned section without an explicit test value would be served from
    the pin without it - i.e. silently absent, which reads as False to every
    `.get()` consumer - and a pinned key the file no longer has is a test
    value for nothing. Both are "update the registry", never a false
    positive to work around."""
    for section, pinned in PINNED_CONFIG_SECTIONS.items():
        committed = _committed_config_section(section)
        missing = set(committed) - set(pinned)
        assert not missing, f"{section} keys in config/settings.yaml with no pinned test value: {missing}"
        stale = set(pinned) - set(committed)
        assert not stale, f"pinned {section} keys no longer in config/settings.yaml: {stale}"
