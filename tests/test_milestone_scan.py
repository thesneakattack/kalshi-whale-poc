"""services/market_watch/milestone_scan.py - broad, watchlist-independent
event_ticker -> milestone_id discovery (docs/superpowers/specs/
2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md, Part
3). Mirrors tests/test_mve_scan.py's shape: a fake KalshiPublicGateway
stands in for the real gateway, app_state's real `state` dict is reset
between tests since this module reads/writes it directly."""
import asyncio

import pytest

from services.app_state import state
from services.market_watch import milestone_scan


@pytest.fixture(autouse=True)
def _isolated_state():
    state["milestone_scan"] = {"scanning": False, "last_started_at": 0.0, "task": None, "watermark": 0.0}
    state["milestone_by_event"] = {}
    yield


def _milestone(id_, related_event_tickers, last_updated_ts="2026-08-30T00:00:00Z"):
    return {"id": id_, "related_event_tickers": related_event_tickers, "last_updated_ts": last_updated_ts}


class _FakeMilestoneClient:
    def __init__(self, by_category):
        self._by_category = by_category  # category -> list[milestone dict]
        self.calls = []  # (category, min_updated_ts)

    async def get_milestones_bulk(self, category, min_updated_ts=None, limit=500):
        self.calls.append((category, min_updated_ts))
        return self._by_category.get(category, [])


def _cfg(categories):
    return {"kalshi": {"categories": categories}}


def test_scan_builds_the_broad_event_ticker_to_milestone_id_map():
    client = _FakeMilestoneClient({
        "Sports": [_milestone("ms-1", ["EVT-A", "EVT-B"])],
    })
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert state["milestone_by_event"] == {"EVT-A": "ms-1", "EVT-B": "ms-1"}


def test_scan_covers_every_configured_category():
    client = _FakeMilestoneClient({
        "Sports": [_milestone("ms-1", ["EVT-A"])],
        "Politics": [_milestone("ms-2", ["EVT-C"])],
    })
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports", "Politics"])))
    assert state["milestone_by_event"] == {"EVT-A": "ms-1", "EVT-C": "ms-2"}
    assert {c for c, _ in client.calls} == {"Sports", "Politics"}


def test_scan_skips_milestones_with_no_related_event_tickers():
    client = _FakeMilestoneClient({"Sports": [_milestone("ms-1", [])]})
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert state["milestone_by_event"] == {}


def test_scan_first_call_passes_no_watermark_then_advances_it():
    client = _FakeMilestoneClient({"Sports": []})
    assert state["milestone_scan"]["watermark"] == 0.0
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert client.calls == [("Sports", None)]  # cold start - no watermark yet
    assert state["milestone_scan"]["watermark"] > 0.0


def test_scan_survives_one_category_failing(capsys):
    class _PartialFailClient(_FakeMilestoneClient):
        async def get_milestones_bulk(self, category, min_updated_ts=None, limit=500):
            if category == "Politics":
                raise RuntimeError("boom")
            return await super().get_milestones_bulk(category, min_updated_ts, limit)

    client = _PartialFailClient({"Sports": [_milestone("ms-1", ["EVT-A"])]})
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports", "Politics"])))
    assert state["milestone_by_event"] == {"EVT-A": "ms-1"}  # Sports still landed
    assert "Politics" in capsys.readouterr().out  # failure surfaced, not swallowed silently


def test_maybe_scan_milestone_batch_respects_the_due_interval():
    import time
    state["milestone_scan"]["last_started_at"] = time.time()  # just started
    milestone_scan._maybe_scan_milestone_batch(_cfg(["Sports"]))
    assert state["milestone_scan"]["scanning"] is False  # not due yet, nothing kicked off
