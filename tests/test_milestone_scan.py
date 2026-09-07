"""services/market_watch/milestone_scan.py - broad, watchlist-independent
event_ticker -> milestone_id discovery (docs/archive/
lane-3-strategy-risk-execution/specs/2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md, Part
3). Mirrors tests/test_mve_scan.py's shape: a fake KalshiPublicGateway
stands in for the real gateway, app_state's real `state` dict is reset
between tests since this module reads/writes it directly."""
import asyncio
import time

import pytest

from services.app_state import state
from services.market_watch import milestone_scan


@pytest.fixture(autouse=True)
def _isolated_state():
    state["milestone_scan"] = {"scanning": False, "last_started_at": 0.0, "task": None, "watermarks": {}}
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
    assert state["milestone_scan"]["watermarks"] == {}
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert client.calls == [("Sports", None)]  # cold start - no watermark yet
    assert state["milestone_scan"]["watermarks"]["Sports"] > 0


def test_scan_advances_the_watermark_short_of_scan_started_at_not_to_it(monkeypatch):
    # Self-review finding: docs/kalshi/get-milestones.md documents
    # min_updated_ts as "updated AFTER this timestamp" - exclusive. Advancing
    # exactly to scan_started_at would let a milestone updated in that same
    # second, but not returned by this cycle's in-flight request, be
    # permanently skipped once the next cycle's min_updated_ts equals it.
    fixed_now = 2_000_000_000
    monkeypatch.setattr(milestone_scan.time, "time", lambda: fixed_now)
    client = _FakeMilestoneClient({"Sports": []})
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert state["milestone_scan"]["watermarks"]["Sports"] == fixed_now - milestone_scan._WATERMARK_OVERLAP_SEC


def test_scan_passes_each_categorys_own_watermark_on_the_next_cycle():
    client = _FakeMilestoneClient({"Sports": [], "Politics": []})
    state["milestone_scan"]["watermarks"] = {"Sports": 111, "Politics": 222}
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports", "Politics"])))
    assert dict(client.calls) == {"Sports": 111, "Politics": 222}


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


def test_a_failed_categorys_watermark_does_not_advance_while_a_sibling_succeeds():
    """Per-category watermarks (review finding, final-review fix pass). A
    single shared scalar advanced unconditionally at the end of the cycle,
    so a category that kept failing had its window marched forward anyway -
    everything that changed while it was down would be permanently skipped
    the moment it recovered. mve_scan/catalog_scan take the opposite
    posture: a failure means don't mark it scanned."""
    class _PartialFailClient(_FakeMilestoneClient):
        async def get_milestones_bulk(self, category, min_updated_ts=None, limit=500):
            if category == "Politics":
                raise RuntimeError("boom")
            return await super().get_milestones_bulk(category, min_updated_ts, limit)

    client = _PartialFailClient({"Sports": [_milestone("ms-1", ["EVT-A"])]})
    state["milestone_scan"]["watermarks"] = {"Sports": 111, "Politics": 222}

    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports", "Politics"])))

    watermarks = state["milestone_scan"]["watermarks"]
    assert watermarks["Sports"] > 111  # succeeded - its own window closes
    assert watermarks["Politics"] == 222  # failed - unchanged, retried in full next cycle


def test_a_failed_category_stays_unwatermarked_from_a_cold_start():
    class _AllFailClient(_FakeMilestoneClient):
        async def get_milestones_bulk(self, category, min_updated_ts=None, limit=500):
            raise RuntimeError("boom")

    client = _AllFailClient({})
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    # No key at all, not a 0/now sentinel: the next cycle must re-ask with
    # min_updated_ts=None exactly as if this cycle never ran.
    assert state["milestone_scan"]["watermarks"] == {}


def test_scan_watermark_is_an_int_not_a_float():
    # docs/kalshi/get-milestones.md types min_updated_ts `integer, format:
    # int64`; live-verified 2026-08-30 that a float returns HTTP 400
    # ("strconv.ParseInt: parsing \"1756500000.123\": invalid syntax").
    client = _FakeMilestoneClient({"Sports": []})
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert isinstance(state["milestone_scan"]["watermarks"]["Sports"], int)


def test_maybe_scan_milestone_batch_respects_the_due_interval():
    state["milestone_scan"]["last_started_at"] = time.time()  # just started
    milestone_scan._maybe_scan_milestone_batch(_cfg(["Sports"]))
    assert state["milestone_scan"]["scanning"] is False  # not due yet, nothing kicked off


# --- _maybe_scan_milestone_batch: due()/overlap-guard scheduling ------------
# Mirrors tests/test_mve_scan.py's identical three tests for
# _maybe_scan_mve_batch (review finding, task-5 fix pass) - the due-interval
# test above only exercised the NOT-due path; these three monkeypatch
# task_supervisor.supervise to verify the actual due-and-not-scanning branch
# fires (or doesn't) with the right kwargs, not just that `scanning` ends up
# False in the one case that never reaches it.

def test_maybe_scan_milestone_batch_triggers_when_due_and_not_already_scanning(monkeypatch):
    supervised = []
    monkeypatch.setattr(
        milestone_scan.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel"
    )

    milestone_scan._maybe_scan_milestone_batch(_cfg(["Sports"]))

    assert len(supervised) == 1
    assert supervised[0]["component"] == "milestone_scan"
    assert state["milestone_scan"]["scanning"] is True
    assert state["milestone_scan"]["task"] == "task-sentinel"


def test_maybe_scan_milestone_batch_does_not_refire_before_the_interval_elapses(monkeypatch):
    supervised = []
    monkeypatch.setattr(
        milestone_scan.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel"
    )
    state["milestone_scan"]["last_started_at"] = time.time()  # just started

    milestone_scan._maybe_scan_milestone_batch(_cfg(["Sports"]))

    assert supervised == []


def test_maybe_scan_milestone_batch_does_not_overlap_a_run_already_in_flight(monkeypatch):
    supervised = []
    monkeypatch.setattr(
        milestone_scan.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel"
    )
    state["milestone_scan"]["scanning"] = True
    state["milestone_scan"]["last_started_at"] = 0.0  # otherwise due()

    milestone_scan._maybe_scan_milestone_batch(_cfg(["Sports"]))

    assert supervised == []
