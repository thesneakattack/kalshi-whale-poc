"""P8 Task 38's own precondition: before check_pending_fills and
position_netting.review get a second, WS-triggered call site alongside
trading_loop's tick, prove near-simultaneous callers can't produce
overlapping/duplicate decisions on the same order or position. The plan
explicitly required this proven with a real test before wiring landed - see
docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md's
Task 38.

Both functions are plain `def`, not `async def`: no line inside either one
can ever be interrupted mid-execution by the asyncio scheduler (there is no
await for it to interrupt at), and each mutates broker state as its very
last synchronous step before returning a decision. That combination means
two callers can never truly execute inside either function "at the same
time" - the real question is whether a SECOND caller, scheduled to run
right after the first one via a genuine await boundary (the same kind of
boundary a real WS message dispatch or tick loop iteration introduces),
reads broker state fresh enough to see the first caller's mutation and
skip. These tests force exactly that interleaving with asyncio.gather and a
real await point between each caller reading state and acting on it, and
assert the total decision count across BOTH callers is what one full
resolution should produce - never double."""
import asyncio
import time

from services import market_history as mh_module
from services import paper_broker as pb_module
from services.exits.position_netting import review
from services.paper_broker import PaperBroker


def _broker(tmp_path, monkeypatch, bankroll=100000.0):
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(mh_module, "DB_PATH", tmp_path / "market_history.db")
    return PaperBroker(starting_bankroll=bankroll)


async def _racing_pair(coro_fn):
    """Runs two invocations of coro_fn "at the same time": both start, both
    yield once at a real await boundary (simulating the gap between reading
    inputs and generating a decision that a genuine WS-dispatch/tick-loop
    interleaving would introduce), then both resume and call the target
    function. asyncio.gather schedules task A first; it runs to its first
    await and suspends, task B then runs to the same point and suspends,
    then both resume in submission order - the actual race window this
    integration depends on being safe under."""
    async def _slot():
        await asyncio.sleep(0)
        return coro_fn()

    return await asyncio.gather(_slot(), _slot())


def test_check_pending_fills_racing_callers_fill_the_order_exactly_once(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=time.time() + 60)
    latest_bids, latest_asks = {"TICK-A": 0.48}, {"TICK-A": 0.50}

    results = asyncio.run(_racing_pair(
        lambda: broker.check_pending_fills(latest_bids=latest_bids, latest_asks=latest_asks)
    ))

    all_fills = results[0] + results[1]
    trade_fills = [f for f in all_fills if f["action"] == "trade"]
    assert len(trade_fills) == 1  # exactly one caller actually filled it
    assert results[0] == [] or results[1] == []  # the other saw nothing left to do
    assert broker.pending_orders == {}  # no order left dangling
    assert len(broker.positions) == 1  # not opened twice
    assert broker.positions["TICK-A"].size == 100  # not double-sized


def test_check_pending_fills_racing_callers_never_double_charge_the_bankroll(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    starting = broker.bankroll
    broker.place_limit_order("TICK-A", "yes", size=100, limit_price=0.5, reason="r", expires_at=time.time() + 60)
    latest_bids, latest_asks = {"TICK-A": 0.48}, {"TICK-A": 0.50}

    asyncio.run(_racing_pair(
        lambda: broker.check_pending_fills(latest_bids=latest_bids, latest_asks=latest_asks)
    ))

    spent = starting - broker.bankroll
    assert 0 < spent < 60  # one 100-contract fill at ~0.50, not two


def test_position_netting_review_racing_callers_close_each_leg_exactly_once(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.6, "r")
    broker.open_position("B", "yes", 100, 0.6, "r")
    market_titles = {"A": {"event_ticker": "EVT-1"}, "B": {"event_ticker": "EVT-1"}}
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0}}
    latest_prices = {"A": 0.6, "B": 0.6}

    results = asyncio.run(_racing_pair(
        lambda: review(broker, market_titles, event_titles, latest_prices, cfg)
    ))

    all_decisions = results[0] + results[1]
    assert {d["ticker"] for d in all_decisions} == {"A", "B"}  # both legs closed, collectively
    assert len(all_decisions) == 2  # never each leg closed by both racing callers
    assert broker.positions == {}  # nothing left open, nothing double-closed
