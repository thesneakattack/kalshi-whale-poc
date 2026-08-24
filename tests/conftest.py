"""
Redirects every DB_PATH/config path that `services.app_state`'s eager
module-scope singleton construction (`broker = PaperBroker(...)`,
`risk = RiskManager(...)`) and `services.config_store`'s module-scope
`config_store = ConfigStore()` could touch, to a throwaway temp directory -
before pytest collects (imports) a single test_*.py file in this directory.

Real, live bug found and fixed 2026-08-23, same "module quality" pass as
the rest of this session: several individual test files (e.g.
test_trading_gate.py, test_active_terminal_refresh.py) already redirect
these same paths at their own module scope, *before their own* `import
main` - each one is safe in isolation. But `services.app_state`/
`services.config_store`'s singletons are constructed exactly ONCE per
Python process, the first time anything imports them (`import main`
transitively does) - cached in `sys.modules` for the rest of that pytest
session regardless of which file triggers it. Whichever test file pytest
happens to collect *first* determines which redirect actually protects the
real files; every other file's own "redirect before import" block is
inert for the singleton (it still helps that file's own later reassignment
of per-test paths, but not the one-time construction). At least one file,
test_signal_resolution.py, does `import main` at module scope with *no*
redirection at all - safe only because, in every observed run, some other
file happened to import `main` first and win the race.

Confirmed live: this fragility is not hypothetical. The real
`data/paper_broker.db` this session's running `ddev` app reads and writes
picked up two real trades (tickers `KXTICK-A`/`OTHER-B`, matching
test_trading_gate.py's `test_build_series_context_scopes_stats_and_trades_
to_the_series` fixture data byte-for-byte) at a timestamp matching this
session's own test runs - a real violation of CLAUDE.md's "Tests always
redirect DB_PATH... never touch a real data/*.db file" rule, most likely
via `.claude/hooks/run_tests.py`'s post-edit pytest invocation racing
against its own 60s subprocess timeout. The exact trigger mechanism doesn't
matter - relying on collection-order luck for a hard safety rule is the
actual bug. `conftest.py` is pytest's own mechanism for setup that must run
before any test in a directory is collected, which is exactly the guarantee
needed here: whichever file first triggers the singleton construction, it
now always sees these paths already pointed at this file's own tmp dir, not
the module's real default.

This does NOT replace the per-file redirects already in place - those
still matter for tests that explicitly reassign a path again later for
their own isolation (e.g. a second, distinct PaperBroker instance). It just
makes the *first* import, whichever file triggers it, safe by construction
instead of by convention.
"""
import shutil
import tempfile
from pathlib import Path

from services import candidate_log as _cl_module
from services import config_performance as _cp_module
from services import config_store as _config_store_module
from services.market_catalog import market_catalog as _mc_module
from services.market_analyst_agent import _db as _maa_module
from services import market_history as _mh_module
from services import paper_broker as _pb_module
from services import risk_manager as _rm_module
from services import series_evaluator as _se_module
from services import series_watcher as _sw_module
from services import settlement_edge as _sedge_module

_tmp_dir = Path(tempfile.mkdtemp(prefix="pytest_conftest_"))
_pb_module.DB_PATH = _tmp_dir / "paper_broker.db"
_rm_module.DB_PATH = _tmp_dir / "risk_state.db"
_cp_module.DB_PATH = _tmp_dir / "config_performance.db"
_mh_module.DB_PATH = _tmp_dir / "market_history.db"
_mc_module.DB_PATH = _tmp_dir / "market_catalog.db"
_se_module.DB_PATH = _tmp_dir / "series_evaluator.db"
_sw_module.DB_PATH = _tmp_dir / "series_watcher.db"
_cl_module.DB_PATH = _tmp_dir / "candidate_log.db"
_sedge_module.DB_PATH = _tmp_dir / "settlement_edge.db"
_maa_module.DB_PATH = _tmp_dir / "market_analyst.db"

_tmp_config_path = _tmp_dir / "settings.yaml"
shutil.copy(_config_store_module.CONFIG_PATH, _tmp_config_path)
_config_store_module.config_store._path = _tmp_config_path
_config_store_module.config_store.reload()
