"""Deterministic replay/load harness for the Kalshi realtime data plane
(realtime data-plane investigation task I6).

A seeded discrete-event simulation of the reader -> bounded queue ->
consumer topology under configurable arrival/service distributions, so
queue/worker ideas can be tested without experimenting on production code.
Nothing here opens a socket or touches a database; a virtual clock makes
every run deterministic and fast."""
import json

import pytest

from tools import realtime_pipeline_replay as rp


def _workload(**overrides) -> rp.Workload:
    base = dict(
        seed=7, duration_sec=30.0,
        rates={"trade": 100.0, "ticker": 5.0, "lifecycle": 0.5, "fill": 0.2},
        service={"trade": rp.Fixed(0.003), "ticker": rp.Fixed(0.002),
                 "lifecycle": rp.Fixed(0.002), "fill": rp.Fixed(0.001)},
    )
    base.update(overrides)
    return rp.Workload(**base)


def _run(workload: rp.Workload, topology=None) -> dict:
    topology = topology or rp.SingleQueueTopology(capacity=20000)
    return rp.simulate(workload, topology)


# --- basic mechanics --------------------------------------------------------

def test_same_seed_gives_identical_results():
    assert _run(_workload()) == _run(_workload())


def test_different_seeds_give_different_arrival_patterns():
    a, b = _run(_workload(seed=1)), _run(_workload(seed=2))
    assert a["received"] != b["received"] or a["wait"]["trade"]["max_ms"] != b["wait"]["trade"]["max_ms"]


def test_messages_are_conserved_across_processed_dropped_remaining_and_lost():
    r = _run(_workload())
    assert r["received"] == r["processed"] + r["dropped"] + r["remaining"] + r["lost_on_reconnect"]
    assert r["received"] > 0


def test_under_capacity_nothing_drops_and_waits_stay_small():
    r = _run(_workload())  # ~105 msg/s against ~333 msg/s of capacity
    assert r["dropped"] == 0
    assert r["queue_high_water"] < 100
    assert r["wait"]["trade"]["p95_ms"] < 50.0
    assert r["sustained"] is True


def test_over_capacity_a_finite_queue_only_delays_the_loss():
    # 400 msg/s against 5 ms service = 200 msg/s: a 200/s deficit fills a
    # 20,000 slot queue in ~100 s. At 60 s: no drops yet, but the backlog and
    # the oldest-message age are already unmistakable (H10's arithmetic).
    r = _run(_workload(duration_sec=60.0, rates={"trade": 400.0}, service={"trade": rp.Fixed(0.005)}))
    assert r["dropped"] == 0
    assert 10000 < r["queue_high_water"] <= 20000
    assert r["oldest_age_max_sec"] > 30.0
    assert r["sustained"] is False
    # ...and at 150 s the queue is full and drops begin.
    r2 = _run(_workload(duration_sec=150.0, rates={"trade": 400.0}, service={"trade": rp.Fixed(0.005)}))
    assert r2["dropped"] > 0
    assert r2["queue_high_water"] == 20000
    assert r2["dropped_by_kind"]["trade"] == r2["dropped"]


# --- message classes and head-of-line blocking (H2 mechanism) -------------

def test_critical_messages_wait_behind_a_trade_burst_in_one_fifo():
    burst = rp.Burst(start=5.0, end=8.0, multiplier=12.0)  # 3 s at ~1200 trades/s against ~333/s
    rates = {"trade": 100.0, "ticker": 5.0, "lifecycle": 0.5, "fill": 2.0}
    r = _run(_workload(bursts=[burst], rates=rates))
    assert r["wait"]["fill"]["max_ms"] > 1000.0  # a fill arriving mid-burst waits behind thousands of trades
    assert r["critical_wait"]["p95_ms"] > 500.0
    assert r["critical_wait"]["count"] == sum(r["processed_by_kind"].get(k, 0) for k in ("fill", "lifecycle", "ticker"))


def test_topologies_are_pluggable_and_a_critical_first_topology_changes_the_answer():
    # Proves the harness can compare designs, not that this design is right:
    # the same burst, once through a critical-first two-queue topology.
    burst = rp.Burst(start=5.0, end=8.0, multiplier=12.0)
    rates = {"trade": 100.0, "ticker": 5.0, "lifecycle": 0.5, "fill": 2.0}
    single = _run(_workload(bursts=[burst], rates=rates), rp.SingleQueueTopology(capacity=20000))
    prio = _run(_workload(bursts=[burst], rates=rates), rp.CriticalFirstTopology(capacity=20000))
    assert prio["critical_wait"]["p95_ms"] < single["critical_wait"]["p95_ms"] / 10
    assert prio["received"] == single["received"]  # same workload, only the topology differs


# --- stalls: consumer-only versus whole-event-loop ---------------------------

def test_consumer_stall_grows_the_app_queue_while_the_reader_keeps_draining():
    r = _run(_workload(stalls=[rp.Stall(at=10.0, duration=4.0, scope="consumer")]))
    assert r["dropped"] == 0
    assert r["queue_high_water"] >= 350  # ~4 s of ~105 msg/s parked in the app queue
    assert r["wait"]["trade"]["max_ms"] >= 3500.0     # the queue-wait metric (I1) sees a consumer stall
    assert r["latency"]["trade"]["max_ms"] >= 3500.0
    assert r["upstream_high_water"] == 0


def test_loop_stall_parks_arrivals_upstream_then_dumps_them_into_the_queue():
    # A blocked event loop stops the READER too: nothing reaches the app
    # queue during the stall, the websockets/TCP side buffers, and the
    # backlog lands in the queue in one burst when the loop frees.
    r = _run(_workload(stalls=[rp.Stall(at=10.0, duration=4.0, scope="loop")]))
    assert r["dropped"] == 0
    assert r["upstream_high_water"] >= 350
    assert r["queue_high_water"] >= 350  # the dump
    assert r["latency"]["trade"]["max_ms"] >= 3500.0  # arrival -> service: the truth
    # ...but the reader only timestamps a frame once the loop frees, so the
    # queue-wait metric (what I1 records in production) barely notices:
    assert r["wait"]["trade"]["max_ms"] < 2500.0


def test_enrichment_stalls_raise_the_tail_not_the_median():
    plain = _run(_workload())
    enriched = _run(_workload(enrichment=rp.Enrichment(probability=0.003, latency_sec=1.0)))
    assert enriched["wait"]["trade"]["p99_ms"] > plain["wait"]["trade"]["p99_ms"] * 5
    assert enriched["wait"]["trade"]["p50_ms"] < 50.0


def test_reconnect_loses_the_queued_backlog_and_misses_the_gap():
    r = _run(_workload(
        stalls=[rp.Stall(at=10.0, duration=3.0, scope="consumer")],
        reconnect=rp.Reconnect(at=12.0, gap_sec=2.0),
    ))
    assert r["lost_on_reconnect"] > 0  # what was queued at 12.0 s is gone with the old consumer
    assert r["missed_during_reconnect"] > 0  # arrivals during the 2 s gap were never received
    assert r["received"] == r["processed"] + r["dropped"] + r["remaining"] + r["lost_on_reconnect"]


# --- sustainable rate --------------------------------------------------------

def test_sustainable_rate_search_finds_the_service_ceiling():
    rate = rp.sustainable_trade_rate(
        lambda trade_rate: _workload(duration_sec=40.0, rates={"trade": trade_rate},
                                     service={"trade": rp.Fixed(0.004)}),
        lambda: rp.SingleQueueTopology(capacity=20000), lo=50.0, hi=1000.0, tolerance=10.0,
    )
    assert 200.0 <= rate <= 260.0  # 1000/4 ms = 250/s, minus queueing slack


def test_lognormal_service_model_reproduces_its_mean_and_tail():
    model = rp.LogNormal(mean_sec=0.0033, p95_sec=0.010)
    import random
    rng = random.Random(3)
    samples = sorted(model.sample(rng) for _ in range(20000))
    mean = sum(samples) / len(samples)
    assert 0.0028 < mean < 0.0040
    assert 0.007 < samples[int(0.95 * len(samples))] < 0.014


# --- measured presets and CLI ------------------------------------------------

def test_presets_cover_every_workload_the_plan_requires():
    assert set(rp.PRESETS) >= {
        "measured_normal", "measured_p95", "measured_burst", "mixed_trade_ticker",
        "mixed_critical", "slow_handler", "enrichment_stall", "reconnect", "loop_stall",
    }


@pytest.mark.parametrize("name", sorted(["measured_normal", "measured_p95", "measured_burst", "loop_stall"]))
def test_each_preset_runs_and_reports_the_required_fields(name):
    r = rp.run_preset(name, seed=1)
    for key in ("received", "processed", "dropped", "queue_high_water", "oldest_age_max_sec",
                "throughput_per_sec", "wait", "critical_wait", "sustained"):
        assert key in r, key
    assert set(r["wait"]["trade"]) >= {"count", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"}


def test_measured_normal_preset_is_sustained_but_measured_burst_is_not():
    assert rp.run_preset("measured_normal", seed=1)["sustained"] is True
    assert rp.run_preset("measured_burst", seed=1)["queue_high_water"] > 1000


def test_cli_prints_json_and_exits_zero(capsys):
    assert rp.main(["--preset", "measured_normal", "--json", "--seed", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["preset"] == "measured_normal" and "received" in out["result"]


def test_cli_all_runs_every_preset(capsys):
    assert rp.main(["--all", "--json", "--seed", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == set(rp.PRESETS)
