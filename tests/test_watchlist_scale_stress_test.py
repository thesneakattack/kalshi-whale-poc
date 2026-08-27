import copy

from tools import watchlist_scale_stress_test as stress


class _FakeAppClient:
    """Fakes GET/POST against the live app's own API - same HttpGetter-injection
    pattern tools/quality_coordination.py's fetch_app_report already uses. get()
    returns a deep copy, not a live reference - matching what a real HTTP GET +
    json.loads() round-trip always produces (an independent snapshot). Returning
    self.config directly here would silently alias run_stress_steps' own captured
    original_config to this fake's mutable state, corrupting the revert patch the
    moment a later post() mutates self.config in place - a fake-only bug that a real
    HTTP client could never actually exhibit, caught by tracing this exact test
    through by hand before trusting it."""

    def __init__(self):
        self.config = {
            "kalshi": {"live_markets_only": True, "min_volume_24h": 10000},
            "kalshi_account": {"trading_enabled": False},
        }
        self.patches_applied = []

    def get(self, url: str, timeout: float) -> dict:
        if url.endswith("/api/config"):
            return copy.deepcopy(self.config)
        if "/api/health/pipeline" in url:
            return {"markets_watched": len(self.patches_applied) + 8, "ingest": {}}
        if "/api/observability/history" in url:
            metric = url.split("metric=")[1].split("&")[0]
            return {"metric": metric, "samples": []}
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, body: dict, timeout: float) -> dict:
        assert url.endswith("/api/config")
        patch = body["patch"]
        assert "kalshi_account" not in patch  # must never be touched, not even on revert
        self.patches_applied.append(patch)
        for section, values in patch.items():
            self.config.setdefault(section, {}).update(values)
        return self.config


def test_run_stress_steps_applies_measures_and_reverts_only_touched_sections():
    client = _FakeAppClient()
    steps = [{"label": "widen_scope", "patch": {"kalshi": {"min_volume_24h": 1000}}}]
    slept = []
    result = stress.run_stress_steps(
        "http://fastapi:8000", steps,
        getter=client.get, poster=client.post, sleep_fn=slept.append, measure_after_sec=5.0,
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["label"] == "widen_scope"
    assert result["results"][0]["pipeline"]["markets_watched"] == 9
    assert slept == [5.0]
    # Captures loop_watchdog AND the three subscription_churn counters CH1 already
    # tracks (Phase P2.5 above) - this phase's live run doubles as
    # a larger-scale churn data point for CH3, not just a P4/P5 input.
    obs = result["results"][0]["observability"]
    assert set(obs) == {
        "loop_watchdog.stall_max_ms",
        "trade_stream.ingest.subscription_churn.syncs_window",
        "trade_stream.ingest.subscription_churn.tickers_added_window",
        "trade_stream.ingest.subscription_churn.tickers_removed_window",
    }
    assert obs["trade_stream.ingest.subscription_churn.syncs_window"]["metric"] == (
        "trade_stream.ingest.subscription_churn.syncs_window"
    )
    # 2 posts total: the step's own patch, then the revert - both audited above for
    # never containing kalshi_account.
    assert len(client.patches_applied) == 2
    assert client.patches_applied[-1] == {"kalshi": {"live_markets_only": True, "min_volume_24h": 10000}}
    assert client.config["kalshi"]["min_volume_24h"] == 10000  # reverted


def test_run_stress_steps_reverts_even_if_a_step_raises():
    client = _FakeAppClient()

    def _failing_get(url, timeout):
        if "/api/health/pipeline" in url:
            raise RuntimeError("connection reset")
        return client.get(url, timeout)

    steps = [{"label": "widen_scope", "patch": {"kalshi": {"min_volume_24h": 1000}}}]
    try:
        stress.run_stress_steps(
            "http://fastapi:8000", steps,
            getter=_failing_get, poster=client.post, sleep_fn=lambda s: None, measure_after_sec=0.0,
        )
    except RuntimeError:
        pass
    assert client.config["kalshi"]["min_volume_24h"] == 10000  # revert still happened


def test_probe_market_search_times_the_call_and_brackets_it_with_pipeline_snapshots():
    client = _FakeAppClient()
    call_log = []

    def _get(url, timeout):
        call_log.append(url)
        if "/api/markets/search" in url:
            return {"markets": [{"ticker": "K1"}, {"ticker": "K2"}], "market_titles": {}}
        return client.get(url, timeout)

    result = stress.probe_market_search(
        "http://fastapi:8000", limit=200, live_only=True,
        getter=_get, sleep_fn=lambda s: None,
    )
    assert result["result_count"] == 2
    assert result["elapsed_sec"] >= 0
    assert "pipeline_before" in result and "pipeline_after" in result
    # Order matters: pipeline snapshot, then the search call, then a second snapshot.
    assert [u.split("?")[0] for u in call_log] == [
        "http://fastapi:8000/api/health/pipeline",
        "http://fastapi:8000/api/markets/search",
        "http://fastapi:8000/api/health/pipeline",
    ]
