"""Simple end-to-end style smoke: verify static frontend is served and API is healthy.

This runs inside the `fastapi` container (via `ddev exec -s fastapi`) and
validates the web container serves `index.html` to the fastapi container's
network and that `/api/state` returns the expected shape. This is a pragmatic
in-container end-to-end check when full browser automation isn't available.
"""
import requests
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from services import risk_manager as rm_module
from services.config import config_performance as cp_module
from services import market_history as mh_module
from services.market_catalog import market_catalog as mc_module
from services import series_evaluator as se_module
from services import series_watcher as sw_module

_tmp_dir = Path(tempfile.mkdtemp(prefix="e2e_terminal_"))
# services.paper_broker.DB_PATH is deliberately NOT imported+re-overridden
# here - see
# tests/test_trading_gate.py's own comment at the same spot for the full
# mechanism (conftest's install_runtime_isolation() already redirects it
# before this file is even collected; reassigning it again here is dead code
# for main.broker but stays live and dangerous for anything reading the
# module attribute fresh, like trade_archive.archive_epoch()).
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


# Docker Desktop/WSL2's embedded DNS resolves the single label "web" to
# this synthetic placeholder instead of raising socket.gaierror - ddev's
# own project genuinely registers a compose service literally named "web"
# elsewhere on this Docker Desktop instance, just not reachable from a
# container outside ddev's own network. Confirmed live 2026-08-31: bogus
# hostnames raise gaierror as expected, "web" specifically resolves to
# exactly this address on this host. Checking for it by name (not just
# catching whatever error the subsequent connect attempt raises) matters:
# a real "web" container down *while actually running inside ddev* also
# raises requests.exceptions.ConnectionError, which must fail this test,
# not skip it (PR #298 self-review finding, 2026-08-31 - autotrade-8a).
_DOCKER_DESKTOP_UNRESOLVED_SENTINEL = "127.0.53.53"


def test_static_index_served_from_web_container():
    # From inside the fastapi container the nginx/web service is reachable
    # as the host "web" on port 80 in ddev. Only ddev's docker-compose
    # network provides that hostname - plain CI runners (.github/workflows/
    # tests.yml's bare ubuntu-latest, no ddev) never will, so skip there
    # rather than fail on an environment this test was never meant to cover.
    try:
        resolved_ip = socket.gethostbyname("web")
    except socket.gaierror:
        pytest.skip("'web' host not reachable outside ddev's docker network")
    if resolved_ip == _DOCKER_DESKTOP_UNRESOLVED_SENTINEL:
        pytest.skip("'web' resolved to Docker Desktop's not-reachable-here placeholder, not ddev's real container")
    resp = requests.get('http://web/', timeout=5)
    assert resp.status_code == 200
    assert '<div class="view" id="view-terminal">' in resp.text
    # index.html's <script>/<style> got extracted into real ES modules under
    # frontend/src/js/ + static/css/dashboard.css (2026-08-22 modularization,
    # later bundled with esbuild - see frontend/package.json), so the page
    # itself now loads one built bundle rather than inlining everything or
    # loading 12 separate files - assert the page references and can fetch
    # it, and spot-check a helper that lives inside the bundle.
    assert '<script src="js/dashboard.bundle.js"></script>' in resp.text
    js_resp = requests.get('http://web/js/dashboard.bundle.js', timeout=5)
    assert js_resp.status_code == 200
    assert 'clearTerminalFeedCaches' in js_resp.text


def test_api_state_available_and_shapes():
    resp = client.get('/api/state')
    assert resp.status_code == 200
    st = resp.json()
    for key in ('markets', 'latest_prices', 'signal_feed', 'decision_feed'):
        assert key in st
