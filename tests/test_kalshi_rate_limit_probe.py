"""Kalshi REST demand / endpoint-cost / batching probe (realtime data-plane
task I8). Every network-facing piece is exercised through fakes; the live
run is manual (see the module docstring)."""
import asyncio

import pytest

from services import http_client
from tools import kalshi_rate_limit_probe as probe


class _Err429(Exception):
    status = 429


# --- limits / costs (doc-backed shapes) ---------------------------------------

class _Account:
    def __init__(self, limits=None, costs=None, fail=False):
        self._limits, self._costs, self._fail = limits, costs, fail

    async def get_api_limits(self):
        if self._fail:
            raise RuntimeError("401 unauthorized")
        return self._limits

    async def get_endpoint_costs(self):
        if self._fail:
            raise RuntimeError("401 unauthorized")
        return self._costs


_LIMITS = {"usage_tier": "basic", "read": {"refill_rate": 200, "bucket_capacity": 600},
           "write": {"refill_rate": 100, "bucket_capacity": 100}, "grants": []}
_COSTS = {"default_cost": 10, "endpoint_costs": [
    {"method": "GET", "path": "/markets/trades", "cost": 20},
    {"method": "POST", "path": "/portfolio/orders/batched", "cost": 30},
]}


def test_limits_probe_reports_tier_and_buckets():
    r = asyncio.run(probe.probe_limits(_Account(limits=_LIMITS)))
    assert r["available"] is True and r["usage_tier"] == "basic"
    assert r["read"] == {"refill_rate": 200, "bucket_capacity": 600}


def test_limits_and_costs_degrade_explicitly_without_credentials_or_on_error():
    assert asyncio.run(probe.probe_limits(None))["available"] is False
    failed = asyncio.run(probe.probe_costs(_Account(fail=True)))
    assert failed["available"] is False and "RuntimeError" in failed["reason"]


def test_costs_probe_matches_non_default_prices_to_the_app_endpoints():
    r = asyncio.run(probe.probe_costs(_Account(costs=_COSTS)))
    assert r["default_cost"] == 10 and r["non_default_count"] == 2
    trades = next(a for a in r["app_endpoints"] if a["path"] == "/markets/trades")
    assert trades == {"method": "GET", "path": "/markets/trades", "cost": 20, "non_default": True}
    assert [a["path"] for a in r["app_endpoints_non_default"]] == ["/markets/trades"]
    markets = next(a for a in r["app_endpoints"] if a["path"] == "/markets")
    assert markets["cost"] == 10 and markets["non_default"] is False


def test_implied_read_rates_follow_the_documented_bucket_arithmetic():
    limits = asyncio.run(probe.probe_limits(_Account(limits=_LIMITS)))
    rates = probe.requests_per_second(limits, 10)
    assert rates == {"sustained_per_sec": 20.0, "burst_requests": 60, "burst_seconds": 3.0}
    assert probe.requests_per_second({"available": False}, 10) is None


# --- batching ------------------------------------------------------------------

class _Public:
    """Records batch sizes; optionally rate-limits a given size once."""

    def __init__(self, known: int = 1000, fail_size: int | None = None):
        self.calls = []
        self._known = known
        self._fail_size = fail_size

    async def get_markets_by_tickers(self, tickers, batch_size=None):
        self.calls.append((len(tickers), batch_size))
        if self._fail_size == len(tickers):
            raise _Err429()
        return {t: {"ticker": t} for t in tickers[:self._known]}


def _snapshot_factory(state):
    def snapshot():
        return {"by_class": {"interactive": {
            "attempts": state["attempts"], "rate_limited": state["rate_limited"],
            "limiter_wait": {"lifetime": {"count": 1, "avg_ms": state["limiter_ms"]}},
            "network": {"lifetime": {"count": 1, "avg_ms": state["network_ms"]}},
            "backoff": {"lifetime": {"count": 0, "avg_ms": None}},
        }}}
    return snapshot


def test_batch_probe_issues_exactly_one_request_per_size_with_that_many_tickers():
    public = _Public()
    tickers = [f"T{i}" for i in range(250)]
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    rows = asyncio.run(probe.probe_batch_sizes(public, tickers, sizes=(50, 100, 200), sleep=sleep,
                                               snapshot=_snapshot_factory({"attempts": 0, "rate_limited": 0, "limiter_ms": 0.0, "network_ms": 0.0})))
    assert public.calls == [(50, 50), (100, 100), (200, 200)]
    assert [r["completeness"] for r in rows] == [1.0, 1.0, 1.0]
    assert all(r["error"] is None and r["short"] is False for r in rows)
    assert sleeps == [probe.BATCH_PAUSE_SEC, probe.BATCH_PAUSE_SEC]  # pause between sizes, not after the last


def test_batch_probe_reports_partial_completeness_and_short_ticker_lists():
    public = _Public(known=80)  # Kalshi only "knows" the first 80 of any request
    tickers = [f"T{i}" for i in range(120)]
    rows = asyncio.run(probe.probe_batch_sizes(public, tickers, sizes=(50, 100, 200), pause_sec=0,
                                               snapshot=_snapshot_factory({"attempts": 0, "rate_limited": 0, "limiter_ms": 0.0, "network_ms": 0.0})))
    assert rows[0]["completeness"] == 1.0
    assert rows[1]["completeness"] == pytest.approx(0.8)
    assert rows[2]["requested"] == 120 and rows[2]["short"] is True


def test_batch_probe_records_a_429_as_a_result_row_not_an_exception():
    public = _Public(fail_size=200)
    rows = asyncio.run(probe.probe_batch_sizes(public, [f"T{i}" for i in range(200)], sizes=(100, 200), pause_sec=0,
                                               snapshot=_snapshot_factory({"attempts": 0, "rate_limited": 0, "limiter_ms": 0.0, "network_ms": 0.0})))
    assert rows[1]["error"].startswith("_Err429") and rows[1]["returned"] == 0 and rows[1]["completeness"] is None
    assert rows[0]["error"] is None


def test_batch_probe_attributes_limiter_wait_network_and_429s_from_the_class_deltas():
    state = {"attempts": 0, "rate_limited": 0, "limiter_ms": 0.0, "network_ms": 0.0}

    class _Public2(_Public):
        async def get_markets_by_tickers(self, tickers, batch_size=None):
            state["attempts"] += 2
            state["rate_limited"] += 1
            state["limiter_ms"] += 120.0
            state["network_ms"] += 45.0
            return await super().get_markets_by_tickers(tickers, batch_size)

    rows = asyncio.run(probe.probe_batch_sizes(_Public2(), [f"T{i}" for i in range(50)], sizes=(50,), pause_sec=0,
                                               snapshot=_snapshot_factory(state)))
    assert rows[0]["attempts"] == 2 and rows[0]["rate_limited"] == 1
    assert rows[0]["limiter_wait_ms"] == 120.0 and rows[0]["network_ms"] == 45.0


def test_batch_probe_runs_its_requests_under_the_interactive_caller_class():
    seen = []

    class _Public3(_Public):
        async def get_markets_by_tickers(self, tickers, batch_size=None):
            seen.append(http_client.current_caller_class())
            return await super().get_markets_by_tickers(tickers, batch_size)

    asyncio.run(probe.probe_batch_sizes(_Public3(), ["A"], sizes=(1,), pause_sec=0,
                                        snapshot=_snapshot_factory({"attempts": 0, "rate_limited": 0, "limiter_ms": 0.0, "network_ms": 0.0})))
    assert seen == ["interactive"]


# --- demand shares / duplicate estimate (pure) -------------------------------------

def _summary():
    # observability.summary(hours) shape: metric -> {count, min, max, avg}
    return {
        "kalshi_rest_class.background_catalog.calls": {"count": 60, "min": 0, "max": 20, "avg": 6.0},
        "kalshi_rest_class.critical_whale.calls": {"count": 60, "min": 0, "max": 5, "avg": 2.0},
        "kalshi_rest_class.critical_position.calls": {"count": 60, "min": 1, "max": 6, "avg": 2.0},
        "kalshi_rest_class.critical_whale.rate_limited": {"count": 60, "min": 0, "max": 1, "avg": 0.05},
        "kalshi_rest_endpoint.get_milestones.calls": {"count": 60, "min": 0, "max": 12, "avg": 4.0},
        "kalshi_rest_endpoint.get_live_datas.calls": {"count": 60, "min": 0, "max": 3, "avg": 1.0},
        "kalshi_rest_endpoint.get_markets.calls": {"count": 60, "min": 1, "max": 3, "avg": 2.0},
        "tick.duration_sec": {"count": 60, "min": 0.3, "max": 4.0, "avg": 0.8},
    }


def test_demand_shares_total_the_window_samples_per_class_and_endpoint():
    d = probe.demand_shares(_summary(), hours=1.0)
    assert d["total_calls"] == 600
    assert d["by_class"]["background_catalog"] == {"calls": 360, "share": 0.6, "per_sec": 0.1, "rate_limited": 0}
    assert d["by_class"]["critical_whale"]["rate_limited"] == 3
    assert d["critical_share"] == 0.4 and d["background_share"] == 0.6
    assert d["by_endpoint"]["get_milestones"]["calls"] == 240
    assert list(d["by_class"]) == ["background_catalog", "critical_position", "critical_whale"]  # descending by calls


def test_milestone_duplicate_estimate_compares_observed_to_the_repoll_minimum():
    d = probe.demand_shares(_summary(), hours=1.0)
    est = probe.milestone_duplicate_estimate(d["by_endpoint"], tracked_events=2, hours=1.0)
    # observed 240 + 60 = 300 milestone-family calls; the minimum for 2 events
    # at one poll per 60 s each is 2 * 60 = 120 -> 2.5x duplicate factor
    assert est["total_observed"] == 300 and est["minimum_required"] == 120
    assert est["duplicate_factor"] == 2.5


def test_milestone_duplicate_estimate_is_unknown_without_a_tracked_event_count():
    est = probe.milestone_duplicate_estimate({}, tracked_events=0, hours=1.0)
    assert est["duplicate_factor"] is None and est["minimum_required"] is None


# --- CLI argument contract -----------------------------------------------------

def test_cli_requires_at_least_one_probe_and_parses_sizes():
    with pytest.raises(SystemExit):
        probe._parse_args([])
    args = probe._parse_args(["--batch", "--sizes", "50,100"])
    assert args.sizes == (50, 100) and args.batch is True and args.all is False
