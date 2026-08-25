"""
Bootstraps pytest-time isolation from live repository data before any test
module in this directory is collected (and therefore before any test can
`import main` or a services module that constructs an app_state singleton).
The actual mechanism - what gets redirected, the sqlite3.connect hard guard,
and the collection-order incident that made per-file redirects insufficient
on their own - lives in tests/support/runtime_isolation.py.
"""
from tests.support.runtime_isolation import install_runtime_isolation

install_runtime_isolation()


import pytest


@pytest.fixture(autouse=True)
def _fresh_whale_pipeline_perf(monkeypatch):
    """services/whale_pipeline_perf.py's module-level singleton is process-
    global mutable state the hot path records into; without this every
    test that exercises the whale provider or stream handler would leak
    lifetime counters into the next test (observability's "no evidence ->
    no rows" contract is the first thing that breaks). Same principle as
    the DB isolation above, applied to an in-memory aggregator."""
    from services import whale_pipeline_perf as wpp
    monkeypatch.setattr(wpp, "perf", wpp.WhalePipelinePerf())
