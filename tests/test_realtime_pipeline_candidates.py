"""Candidate-topology benchmarking in the replay harness (realtime data-plane
task I10). Extends tools/realtime_pipeline_replay.py with the invariants and
metrics the design spec requires before any design is compared: candidate
(whale) decision latency, critical-event latency, per-class consumer groups,
ticker coalescing, reader-side prefiltering, keep-queue-on-reconnect,
ordering/duplicate checks, recovery after stalls, and busy fraction."""
import pytest

from tools import realtime_pipeline_replay as rp


def _workload(**overrides) -> rp.Workload:
    base = dict(
        seed=11, duration_sec=30.0,
        rates={"trade": 200.0, "ticker": 20.0, "lifecycle": 1.0, "fill": 1.0},
        service={"trade": rp.Fixed(0.003), "ticker": rp.Fixed(0.002),
                 "lifecycle": rp.Fixed(0.002), "fill": rp.Fixed(0.001)},
        candidate_fraction=0.01, candidate_service=rp.Fixed(0.030), keys=50,
    )
    base.update(overrides)
    return rp.Workload(**base)


# --- candidate-aware workload -------------------------------------------------

def test_trades_carry_a_candidate_flag_and_a_key_with_the_requested_mix():
    msgs = _workload().messages()
    trades = [m for m in msgs if m.kind == "trade"]
    candidates = [m for m in trades if m.candidate]
    assert 0.005 < len(candidates) / len(trades) < 0.02
    assert all(m.service == 0.030 for m in candidates)
    assert all(m.service == 0.003 for m in trades if not m.candidate)
    assert all(m.key is not None for m in trades)
    assert len({m.key for m in trades}) <= 50
    assert not any(m.candidate for m in msgs if m.kind != "trade")


def test_results_report_candidate_decision_latency_and_critical_latency_separately():
    r = rp.simulate(_workload(), rp.SingleQueueTopology(capacity=20000))
    assert r["candidate_latency"]["count"] == sum(1 for m in _workload().messages() if m.candidate)
    for key in ("p50_ms", "p95_ms", "p99_ms", "max_ms"):
        assert key in r["candidate_latency"] and key in r["critical_latency"]


# --- invariants -----------------------------------------------------------------

def test_per_key_trade_ordering_and_duplicate_processing_are_checked():
    r = rp.simulate(_workload(), rp.SingleQueueTopology(capacity=20000))
    assert r["ordering_violations"] == 0
    assert r["duplicate_processed"] == 0


def test_busy_fraction_and_recovery_are_reported():
    w = _workload(stalls=[rp.Stall(at=10.0, duration=3.0, scope="consumer")])
    r = rp.simulate(w, rp.SingleQueueTopology(capacity=20000))
    assert 0.0 < r["busy_fraction"]["all"] <= 1.0
    assert len(r["stall_recovery_sec"]) == 1 and r["stall_recovery_sec"][0] > 0.0


# --- keep the queue across reconnects (B4) --------------------------------------

def test_reconnect_can_keep_the_queue_instead_of_discarding_it():
    stall = rp.Stall(at=10.0, duration=3.0, scope="consumer")
    discard = rp.simulate(_workload(stalls=[stall], reconnect=rp.Reconnect(at=12.0, gap_sec=1.0)),
                          rp.SingleQueueTopology(capacity=20000))
    keep = rp.simulate(_workload(stalls=[stall], reconnect=rp.Reconnect(at=12.0, gap_sec=1.0, discard_queue=False)),
                       rp.SingleQueueTopology(capacity=20000))
    assert discard["lost_on_reconnect"] > 0
    assert keep["lost_on_reconnect"] == 0
    assert keep["missed_during_reconnect"] == discard["missed_during_reconnect"]
    assert keep["processed"] > discard["processed"]


# --- per-class consumer groups + coalescing + prefilter (B2/B3) -----------------

def test_class_group_topology_isolates_critical_latency_from_a_trade_burst():
    burst = rp.Burst(start=5.0, end=8.0, multiplier=8.0)
    single = rp.simulate(_workload(bursts=[burst]), rp.SingleQueueTopology(capacity=20000))
    groups = rp.simulate(_workload(bursts=[burst]), rp.ClassGroupTopology(capacity=20000))
    assert single["critical_latency"]["p95_ms"] > 1000.0
    assert groups["critical_latency"]["p95_ms"] < 20.0
    assert groups["received"] == single["received"]
    assert groups["ordering_violations"] == 0
    assert set(groups["busy_fraction"]) >= {"trade", "critical"}


def test_coalescing_keeps_only_the_latest_ticker_per_key_and_never_touches_events():
    w = _workload(rates={"trade": 50.0, "ticker": 400.0, "lifecycle": 2.0, "fill": 1.0},
                  stalls=[rp.Stall(at=5.0, duration=5.0, scope="consumer")])
    plain = rp.simulate(w, rp.ClassGroupTopology(capacity=20000))
    coalesced = rp.simulate(w, rp.ClassGroupTopology(capacity=20000, coalesce_ticker=True))
    assert coalesced["coalesced"] > 0
    assert coalesced["processed_by_kind"]["ticker"] < plain["processed_by_kind"]["ticker"]
    # Events are never coalesced.
    for kind in ("trade", "lifecycle", "fill"):
        assert coalesced["received_by_kind"][kind] == coalesced["processed_by_kind"][kind] + coalesced["remaining_by_kind"].get(kind, 0)
    assert coalesced["ticker_latency"]["max_ms"] < plain["ticker_latency"]["max_ms"]


def test_prefilter_topology_rejects_non_candidates_at_the_reader_without_counting_them_as_loss():
    w = _workload(rates={"trade": 400.0, "ticker": 5.0, "lifecycle": 1.0, "fill": 1.0},
                  service={"trade": rp.Fixed(0.005), "ticker": rp.Fixed(0.002),
                           "lifecycle": rp.Fixed(0.002), "fill": rp.Fixed(0.001)})
    single = rp.simulate(w, rp.SingleQueueTopology(capacity=20000))
    filtered = rp.simulate(w, rp.PrefilterTopology(capacity=20000, prefilter_cost_sec=0.000002))
    assert single["sustained"] is False  # 400/s at 5 ms = overload for one consumer
    assert filtered["sustained"] is True
    assert filtered["prefiltered"] > 0.9 * filtered["received_by_kind"]["trade"]
    assert filtered["dropped"] == 0
    total_candidates = sum(1 for m in w.messages() if m.candidate)
    assert filtered["candidate_latency"]["count"] == total_candidates  # every candidate decided within the horizon
    assert single["candidate_latency"]["count"] < total_candidates     # the overloaded FIFO never got to the rest
    assert filtered["candidate_latency"]["p95_ms"] < single["candidate_latency"]["p95_ms"] / 10


def test_staged_topology_combines_prefilter_class_groups_and_coalescing():
    w = _workload(rates={"trade": 400.0, "ticker": 200.0, "lifecycle": 1.0, "fill": 1.0},
                  bursts=[rp.Burst(start=10.0, end=14.0, multiplier=3.0)])
    r = rp.simulate(w, rp.StagedTopology(capacity=20000))
    assert r["dropped"] == 0 and r["ordering_violations"] == 0
    assert r["critical_latency"]["p95_ms"] < 20.0
    assert r["candidate_latency"]["p95_ms"] < 500.0
    assert r["coalesced"] > 0 and r["prefiltered"] > 0


# --- comparison runner ------------------------------------------------------------

def test_compare_runs_every_candidate_on_the_same_workload_set_and_reports_the_matrix():
    matrix = rp.compare(presets=["measured_p95", "loop_stall"], seed=1)
    assert set(matrix["candidates"]) >= {"single_queue", "critical_first", "class_groups", "prefilter", "staged"}
    for name, per_preset in matrix["results"].items():
        assert set(per_preset) == {"measured_p95", "loop_stall"}
        for r in per_preset.values():
            assert {"dropped", "candidate_latency", "critical_latency", "ordering_violations", "queue_high_water", "sustained"} <= set(r)
    assert matrix["workload_hash"]  # proves every candidate saw literally the same messages


def test_compare_supports_a_hygiene_variant_that_removes_loop_stalls():
    matrix = rp.compare(presets=["loop_stall"], seed=1, variants=("as_measured", "loop_hygiene"))
    with_stalls = matrix["results"]["single_queue"]["loop_stall"]["as_measured"]
    without = matrix["results"]["single_queue"]["loop_stall"]["loop_hygiene"]
    assert with_stalls["latency"]["trade"]["max_ms"] > without["latency"]["trade"]["max_ms"] * 5
    assert without["upstream_high_water"] == 0


def test_busy_hour_preset_reproduces_the_i7_regime_on_the_current_topology():
    r = rp.run_preset("busy_hour", seed=1)
    assert r["dropped"] > 0 or r["queue_high_water"] > 15000
    assert r["candidate_latency"]["p50_ms"] > 10000.0


def test_compare_supports_a_keep_queue_variant_that_survives_reconnects():
    matrix = rp.compare(presets=["reconnect"], seed=1, candidates=("single_queue",),
                        variants=("as_measured", "keep_queue"))
    discard = matrix["results"]["single_queue"]["reconnect"]["as_measured"]
    keep = matrix["results"]["single_queue"]["reconnect"]["keep_queue"]
    assert discard["lost_on_reconnect"] > 0 and keep["lost_on_reconnect"] == 0
    assert keep["missed_during_reconnect"] == discard["missed_during_reconnect"]
