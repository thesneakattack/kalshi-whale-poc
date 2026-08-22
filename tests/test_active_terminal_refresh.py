"""Smoke test: exercise the active Terminal refresh path via GET /api/state.

Lightweight check ensuring the state endpoint returns the fields the
frontend's `refresh()` expects (markets, latest_prices, signal_feed,
decision_feed, stats, broker, risk). Follows the repo's test pattern of
redirecting DB paths before importing `main` so it cannot touch real data.
"""
import shutil
import tempfile
from pathlib import Path

from services import paper_broker as pb_module
from services import risk_manager as rm_module
from services import config_performance as cp_module
from services import market_history as mh_module
from services.market_catalog import market_catalog as mc_module
from services import series_evaluator as se_module
from services import series_watcher as sw_module

_tmp_dir = Path(tempfile.mkdtemp(prefix="active_terminal_refresh_"))
pb_module.DB_PATH = _tmp_dir / "paper_broker.db"
rm_module.DB_PATH = _tmp_dir / "risk_state.db"
cp_module.DB_PATH = _tmp_dir / "config_performance.db"
mh_module.DB_PATH = _tmp_dir / "market_history.db"
mc_module.DB_PATH = _tmp_dir / "market_catalog.db"
se_module.DB_PATH = _tmp_dir / "series_evaluator.db"
# main.py captures raw prints/book snapshots through series_watcher on the
# stream + tick paths - redirect it like every other store so a test run
# can never write into the real data/series_watcher.db (CLAUDE.md).
sw_module.DB_PATH = _tmp_dir / "series_watcher.db"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(main.app)


def test_active_terminal_state_endpoint_smoke():
    resp = client.get('/api/state')
    assert resp.status_code == 200
    st = resp.json()
    # Basic shape the frontend expects
    for key in ('markets', 'latest_prices', 'signal_feed', 'decision_feed', 'stats', 'broker', 'risk'):
        assert key in st, f"missing {key} in /api/state"

    # Call twice to simulate a refresh; ensure response is still valid JSON
    resp2 = client.get('/api/state')
    assert resp2.status_code == 200
    st2 = resp2.json()
    assert isinstance(st2.get('markets', []), list)
    assert isinstance(st2.get('signal_feed', []), list)
