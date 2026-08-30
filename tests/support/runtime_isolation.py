"""
Centralizes the pytest-time defenses that keep the suite from ever touching
a live data/*.db file: every known persistence module's DB_PATH is
redirected into a throwaway temp directory before any test can import it,
and sqlite3.connect itself is wrapped to hard-refuse any path under the
repository's real data/ directory - a second, independent layer that also
covers a persistence module this registry doesn't (yet) know about.

Real, live bug found and fixed 2026-08-23: several test files redirected
their own DB_PATH at module scope, before their own `import main`, and each
one was safe in isolation. But services.app_state/services.config.config_store's
module-scope singletons (`broker = PaperBroker(...)`, `config_store =
ConfigStore()`) are constructed exactly ONCE per Python process, the first
time anything imports them - cached in sys.modules for the rest of that
pytest session regardless of which file triggers it. Whichever test file
pytest happened to collect first determined which redirect actually
protected the real files; every other file's own redirect was inert for
that one-time construction. The real data/paper_broker.db picked up
synthetic trades from test fixture data as a result. Redirecting every
registered module here, from conftest.py, before collection starts, fixes
the "whichever file goes first" race for good instead of by convention.

The sqlite3.connect guard below is what makes that safe even when this list
falls behind current HEAD: it blocks any write under data/ regardless of
whether the module that attempted it is registered.
tests/test_runtime_isolation.py cross-checks PERSISTENCE_MODULE_PATHS
against a live AST scan of services/ so that sync gap itself has a test.
"""
from __future__ import annotations

import copy
import importlib
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# Every current `DB_PATH = ...`-owning module under services/, as the
# dotted import path used to reach it. Kept complete by
# tests/test_runtime_isolation.py's AST cross-check against services/ -
# treat a failure of that test as "update this tuple," not as a false
# positive to work around.
PERSISTENCE_MODULE_PATHS: tuple[str, ...] = (
    "services.accounts_store",
    "services.alerting.alerting",
    "services.backup.backup",
    "services.candidate_ledger",
    "services.candidate_log",
    "services.config.config_performance",
    "services.data_quarantine",
    "services.fault_log",
    "services.game_state",
    "services.history.suggestion_decisions",
    "services.index_feed.ingestion",
    "services.market_analyst_agent._db",
    "services.market_catalog.market_catalog",
    "services.market_events.event_schedule",
    "services.market_history",
    "services.observability.observability",
    "services.paper_broker",
    "services.reset.reset_log",
    "services.research.research",
    "services.risk_manager",
    "services.series_cache",
    "services.series_evaluator",
    "services.series_watcher",
    "services.settlement_edge",
    "services.shadow_mode",
    "services.signal_log",
    "services.title_cache",
    "services.reset.trade_archive",
    "services.trade_category",
    "services.whale_calibration.calibration_history",
)

# Real, live gap found 2026-08-24 (QCP Task 18, building the browser E2E
# System Health test): PERSISTENCE_MODULE_PATHS only knows the DB_PATH
# shape (one module -> one file, redirected by basename). services/backup/
# backup.py and services/storage_health/storage_health.py each also own a
# DATA_DIR constant (a *directory*, globbed for every data/*.db file) -
# a different shape the registry above never covered. Both modules' own
# test files (tests/test_backup.py, tests/test_quality_routes.py) already
# carry a local `monkeypatch.setattr(module, "DATA_DIR", tmp_path)` fixture
# protecting THEIR OWN tests, but tests/support/e2e_server.py (the real
# browser-E2E harness, driven only by install_runtime_isolation() - no
# per-file fixtures of its own) had no such protection: GET /api/quality/
# summary's storage_health.inventory_data_dir(storage_health.DATA_DIR) call
# still resolved to the REAL repo data/ directory, real data/accounts.db
# included - and the sqlite3.connect guard below correctly refused to open
# it (working as designed), which surfaced as a hard 500 on the very first
# browser test to ever actually hit that route
# (tests/test_browser_e2e.py::test_system_health_panel_renders_a_status_with_no_console_error).
# Centralizing this here, the same way the DB_PATH gap was centralized
# after the 2026-08-23 collection-order incident (see this file's own
# docstring), closes it for every current and future test context at once
# instead of depending on each new caller remembering its own local fixture.
DATA_DIR_MODULE_PATHS: tuple[str, ...] = (
    "services.backup.backup",
    "services.storage_health.storage_health",
)

# Config sections whose committed values are LIVE RUNTIME STATE, not test
# fixtures (issue #229, 2026-08-30). config/settings.yaml is the running
# app's own live-reloadable config and the user commits it as-is, so a flag
# the soak owner flips in production lands in the file every test reads:
# PR #222 committed realtime_data_plane.two_consumer_mode: true and 16
# gateway tests that never set the flag - they passed only while the file
# said false - failed at once. _redirect_config_store() below only protects
# the file from tests; it copies the file's VALUES, so it cannot protect
# tests from the file. Every section listed here is served to the whole
# suite as exactly this dict by tests/conftest.py's autouse
# _pinned_runtime_config fixture, whatever the file says. A test that needs
# a specific value patches config_store.get itself (the idiom
# tests/test_kalshi_ws_two_consumers.py's _gateway() uses) and that explicit
# patch replaces the pin outright; config_store.update() on a pinned section
# is invisible through get() by design - the file is what the pin exists to
# make irrelevant. Whole sections, with every key explicit: a flag added to
# a pinned section must be given a test value here, and
# tests/test_runtime_isolation.py cross-checks each pinned section's key set
# against the committed file - the same discipline PERSISTENCE_MODULE_PATHS
# gets - so no flag can be inherited again by omission.
PINNED_CONFIG_SECTIONS: dict[str, dict] = {
    "realtime_data_plane": {
        # Single queue: the legacy topology and the code's own default when
        # the key is absent. tests/test_kalshi_ws_ingest_metrics.py runs its
        # accounting under both topologies explicitly.
        "two_consumer_mode": False,
        # Shadow-count only, never filter; the reader-gate tests set True
        # themselves where filtering is the subject.
        "reader_gate_enabled": False,
    },
}


@dataclass(frozen=True)
class IsolationContext:
    temp_root: Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def repo_data_dir() -> Path:
    return repo_root() / "data"


def loaded_registered_modules() -> list:
    return [importlib.import_module(name) for name in PERSISTENCE_MODULE_PATHS]


# services.app_state connects to these at import time, not just define a
# DB_PATH constant: `broker = PaperBroker(...)`, `risk = RiskManager(...)`,
# and `shadow = ShadowTrader(...)` construct eagerly, and the module-scope
# `state` dict literal calls `series_cache.load()`, `event_schedule.
# load_all()`, and `title_cache.load_market_titles()`/`load_event_titles()`
# directly. All six must be redirected before anything that might
# transitively import services.app_state - e.g. services.backup.backup,
# which imports it for `state` - gets imported below. Every other
# registered module only defines a DB_PATH constant at import time and
# connects lazily, so import order among the rest doesn't matter. Found by
# running this file's own guard against itself: it raised on each of these
# one at a time until every eager call site in app_state.py was covered
# here (a full read of that module's top-level code, not just a grep -
# grep's own pattern missed load_market_titles()) - trust the guard over a
# static read/grep of app_state.py if it disagrees again after a future
# edit there.
_EAGER_SINGLETON_MODULES: tuple[str, ...] = (
    "services.paper_broker",
    "services.risk_manager",
    "services.shadow_mode",
    "services.series_cache",
    "services.market_events.event_schedule",
    "services.title_cache",
)


def _redirect_persistence_modules(temp_root: Path) -> None:
    ordered = _EAGER_SINGLETON_MODULES + tuple(
        name for name in PERSISTENCE_MODULE_PATHS if name not in _EAGER_SINGLETON_MODULES
    )
    for name in ordered:
        module = importlib.import_module(name)
        module.DB_PATH = temp_root / Path(module.DB_PATH).name


def _redirect_data_dir_modules(temp_root: Path) -> None:
    # One shared empty directory, not one per module - both current owners
    # (backup.py/storage_health.py) only ever *read* (glob/enumerate) this
    # path in the code paths runtime isolation needs to cover; nothing here
    # needs them kept apart the way each DB_PATH does.
    data_dir = temp_root / "isolated_data_dir"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in DATA_DIR_MODULE_PATHS:
        module = importlib.import_module(name)
        module.DATA_DIR = data_dir


def _redirect_config_store(temp_root: Path) -> None:
    from services.config import config_store as config_store_module

    tmp_config_path = temp_root / "settings.yaml"
    shutil.copy(config_store_module.CONFIG_PATH, tmp_config_path)
    config_store_module.config_store._path = tmp_config_path
    config_store_module.config_store.reload()


def pinned_config_get(original_get):
    """Wrap a ConfigStore.get so every PINNED_CONFIG_SECTIONS section in the
    dict it returns is a fresh copy of the pinned dict, never the file's.
    Everything else - the schema, tuning values, safety defaults, anything a
    test wrote through update() - still comes from the isolated copy exactly
    as before. A copy per call so a test mutating what it got back cannot
    edit the registry for the tests after it."""
    def get() -> dict:
        cfg = original_get()
        for section, pinned in PINNED_CONFIG_SECTIONS.items():
            cfg[section] = copy.deepcopy(pinned)
        return cfg
    return get


def _is_repo_data_path(database: object) -> bool:
    if not isinstance(database, (str, os.PathLike)):
        return False
    raw = str(database)
    if raw in ("", ":memory:"):
        return False
    if raw.startswith("file:"):
        raw = urlparse(raw).path
    try:
        resolved = Path(raw).resolve()
    except (OSError, ValueError):
        return False
    data_dir = repo_data_dir().resolve()
    return resolved == data_dir or data_dir in resolved.parents


_original_connect = sqlite3.connect


def _guarded_connect(database, *args, **kwargs):
    if _is_repo_data_path(database):
        raise AssertionError(
            f"pytest attempted to open live repository data: {database}"
        )
    return _original_connect(database, *args, **kwargs)


def _install_sqlite_guard() -> None:
    if sqlite3.connect is _original_connect:
        sqlite3.connect = _guarded_connect


def install_runtime_isolation(temp_root: Path | None = None) -> IsolationContext:
    if temp_root is None:
        temp_root = Path(tempfile.mkdtemp(prefix="pytest_runtime_isolation_"))
    _install_sqlite_guard()
    _redirect_persistence_modules(temp_root)
    _redirect_data_dir_modules(temp_root)
    _redirect_config_store(temp_root)
    return IsolationContext(temp_root=temp_root)
