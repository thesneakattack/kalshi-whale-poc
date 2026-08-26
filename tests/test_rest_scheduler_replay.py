"""REST scheduling and candidate-recovery replay (realtime data-plane task
I11). Two deterministic simulators in tools/rest_scheduler_replay.py:

- the REST side: per-class demand shaped like the measured tick/catalog
  fan-outs, a local scheduler policy (the current shared FIFO token bucket
  and the I9 candidate families), and an upstream token bucket at the
  verified account budget that answers 429 when empty and is retried with
  the production backoff;
- the recovery side: whale candidates whose enrichment fails transiently
  or whose wire copy is lost, under the current mark-seen-first policy and
  the I9 candidate recovery families, scored on evaluated-once / lost /
  duplicated / decision latency / extra REST calls.

Nothing here opens a socket or touches a database."""
import pytest

from tools import rest_scheduler_replay as rs


# --- demand model -------------------------------------------------------------

def _demand(**overrides) -> rs.Demand:
    base = dict(
        seed=3, duration_sec=120.0,
        classes={
            "critical_whale": rs.ClassDemand(rate=0.2, network=rs.Fixed(0.047)),
            "critical_position": rs.ClassDemand(burst_size=3, burst_every=6.0, network=rs.Fixed(0.090)),
            "background_live_status": rs.ClassDemand(burst_size=10, burst_every=6.0, network=rs.Fixed(0.074)),
            "background_catalog": rs.ClassDemand(burst_size=12, burst_every=15.0, network=rs.Fixed(0.179)),
            "background_resolution": rs.ClassDemand(burst_size=5, burst_every=30.0, network=rs.Fixed(0.180)),
        },
    )
    base.update(overrides)
    return rs.Demand(**base)


def test_demand_generates_bursts_and_poisson_streams_deterministically():
    reqs = _demand().requests()
    assert reqs == _demand().requests()
    by = {}
    for r in reqs:
        by[r.cls] = by.get(r.cls, 0) + 1
    assert by["critical_position"] == 3 * 20          # a 3-call gather every 6 s for 120 s
    assert by["background_catalog"] == 12 * 8         # a 12-call batch every 15 s
    assert 10 < by["critical_whale"] < 40             # Poisson at 0.2/s
    first_burst = [r for r in reqs if r.cls == "critical_position" and r.arrival < 1.0]
    assert len(first_burst) == 3 and len({r.arrival for r in first_burst}) == 1  # a gather fires at once


# --- the current policy reproduces the measured pathology ---------------------

def test_shared_fifo_bucket_at_8_per_sec_makes_critical_calls_wait_behind_background_bursts():
    r = rs.simulate(_demand(), rs.FifoBucket(rate=8.0, burst=8.0), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    # The tick's own gather: 3 position calls enqueued behind 10 live-status
    # calls every 6 s (alphabetical class order mirrors the real gather's
    # coroutine order), and 12 more catalog calls every 15 s.
    assert r["by_class"]["critical_position"]["limiter_wait"]["p95_ms"] > 250.0
    assert r["by_class"]["critical_whale"]["limiter_wait"]["max_ms"] > 500.0
    assert r["upstream"]["rate_limited"] == 0            # the local cap keeps the upstream bucket untouched
    assert r["upstream"]["max_tokens_used_per_sec"] <= 200.0
    assert r["local_queue_high_water"] >= 15


def test_budget_sized_bucket_removes_the_local_wait_but_exposes_upstream_429s_under_storms():
    normal = rs.simulate(_demand(), rs.FifoBucket(rate=20.0, burst=60.0), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    assert normal["by_class"]["critical_whale"]["limiter_wait"]["p95_ms"] < 50.0
    storm = rs.simulate(_demand(classes={
        "critical_whale": rs.ClassDemand(rate=0.2, network=rs.Fixed(0.047)),
        "background_catalog": rs.ClassDemand(burst_size=90, burst_every=5.0, network=rs.Fixed(0.179)),
    }), rs.FifoBucket(rate=20.0, burst=60.0), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    assert storm["upstream"]["rate_limited"] > 0
    assert any(v["backoff"]["count"] > 0 for v in storm["by_class"].values())  # a 429 lands on whoever is unlucky


# --- candidate policies -----------------------------------------------------------

@pytest.mark.parametrize("policy, improvement", [
    # Strict priority only reorders the QUEUE: a critical call that arrives
    # just after a burst has drained the bucket still waits one refill
    # interval behind work that was already dispatched.
    (rs.PriorityAging(rate=8.0, burst=8.0, aging_sec=5.0), 2.0),
    # A reserve of its own removes even that wait.
    (rs.ReservedCapacity(rate=8.0, burst=8.0, reserve_rate=3.0, reserve_burst=10.0), 4.0),
    (rs.DeficitRoundRobin(rate=8.0, burst=8.0, quanta={"critical_whale": 4, "critical_position": 4}), 4.0),
])
def test_scheduling_policies_protect_critical_wait_without_exceeding_the_upstream_budget(policy, improvement):
    fifo = rs.simulate(_demand(), rs.FifoBucket(rate=8.0, burst=8.0), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    r = rs.simulate(_demand(), policy, rs.Upstream(rate_per_sec=20.0, burst=60.0))
    assert r["by_class"]["critical_whale"]["limiter_wait"]["p95_ms"] < fifo["by_class"]["critical_whale"]["limiter_wait"]["p95_ms"] / improvement
    assert r["upstream"]["max_tokens_used_per_sec"] <= 200.0
    assert r["completed"] == fifo["completed"]  # nothing starved to death: every call still completes


def test_priority_aging_bounds_background_starvation():
    hot = _demand(classes={
        "critical_whale": rs.ClassDemand(rate=9.0, network=rs.Fixed(0.047)),  # above the whole 8/s budget
        "background_catalog": rs.ClassDemand(burst_size=5, burst_every=10.0, network=rs.Fixed(0.179)),
    })
    no_aging = rs.simulate(hot, rs.PriorityAging(rate=8.0, burst=8.0, aging_sec=None), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    aging = rs.simulate(hot, rs.PriorityAging(rate=8.0, burst=8.0, aging_sec=5.0), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    assert aging["by_class"]["background_catalog"]["limiter_wait"]["max_ms"] < no_aging["by_class"]["background_catalog"]["limiter_wait"]["max_ms"]
    assert aging["by_class"]["background_catalog"]["limiter_wait"]["max_ms"] < 30000.0


def test_concurrency_caps_bound_burst_amplitude_but_do_not_order_the_bucket():
    fifo = rs.simulate(_demand(), rs.FifoBucket(rate=8.0, burst=8.0), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    capped = rs.simulate(_demand(), rs.ConcurrencyCapped(rate=8.0, burst=8.0, caps={"background_catalog": 2, "background_live_status": 2}),
                         rs.Upstream(rate_per_sec=20.0, burst=60.0))
    assert capped["local_queue_high_water"] < fifo["local_queue_high_water"]
    assert capped["completed"] == fifo["completed"]
    # Amplitude is bounded, but nothing reorders the bucket: position calls
    # still queue behind whatever background got in first.
    assert capped["by_class"]["critical_position"]["limiter_wait"]["max_ms"] > 100.0


def test_fairness_index_and_starvation_are_reported():
    r = rs.simulate(_demand(), rs.FifoBucket(rate=8.0, burst=8.0), rs.Upstream(rate_per_sec=20.0, burst=60.0))
    assert 0.0 < r["background_fairness_jain"] <= 1.0
    assert set(r["starvation_max_wait_ms"]) == set(_demand().classes)


def test_upstream_timeouts_are_errors_not_retries():
    r = rs.simulate(_demand(), rs.FifoBucket(rate=8.0, burst=8.0),
                    rs.Upstream(rate_per_sec=20.0, burst=60.0, timeout_prob=0.05, timeout_sec=10.0))
    assert r["errors"] > 0
    assert r["upstream"]["rate_limited"] == 0
    assert any(v["errors"] > 0 for v in r["by_class"].values())


# --- candidate recovery ------------------------------------------------------------

def _recovery(**overrides) -> rs.RecoveryScenario:
    base = dict(seed=5, candidates=400, rate_per_sec=2.0,
                transient_windows=[(30.0, 40.0), (100.0, 103.0)],   # every enrichment fails inside these
                wire_loss_windows=[(60.0, 70.0)],                  # wire copies dropped/reconnect-lost inside these
                restart_at=None)
    base.update(overrides)
    return rs.RecoveryScenario(**base)


def test_current_mark_seen_first_policy_loses_every_candidate_that_hits_a_transient_failure():
    r = rs.simulate_recovery(_recovery(), rs.MarkSeenFirst())
    assert r["lost_transient"] > 0 and r["lost_wire"] > 0
    assert r["evaluated_once"] + r["lost_transient"] + r["lost_wire"] == 400
    assert r["duplicates"] == 0 and r["extra_rest_calls"] == 0


def test_retry_state_machine_recovers_transient_failures_but_not_wire_loss():
    r = rs.simulate_recovery(_recovery(), rs.RetryStateMachine(max_attempts=6, backoff_sec=2.0))
    assert r["lost_transient"] == 0
    assert r["lost_wire"] > 0
    assert r["duplicates"] == 0
    assert r["extra_rest_calls"] > 0
    assert r["decision_latency"]["max_ms"] >= 2000.0  # a retried candidate decides after its backoff


def test_retry_state_machine_abandons_after_max_attempts_and_says_so():
    r = rs.simulate_recovery(_recovery(transient_windows=[(20.0, 80.0)]), rs.RetryStateMachine(max_attempts=3, backoff_sec=2.0))
    assert r["abandoned"] > 0 and r["lost_transient"] == 0  # abandoned is counted, not silently lost


def test_reconciliation_recovers_wire_loss_and_needs_idempotency_to_avoid_duplicates():
    dup = rs.simulate_recovery(_recovery(), rs.RetryPlusReconciliation(max_attempts=6, backoff_sec=2.0,
                                                                        sweep_every_sec=30.0, sweep_delay_sec=60.0, idempotent=False))
    safe = rs.simulate_recovery(_recovery(), rs.RetryPlusReconciliation(max_attempts=6, backoff_sec=2.0,
                                                                         sweep_every_sec=30.0, sweep_delay_sec=60.0, idempotent=True))
    assert dup["lost_wire"] == 0 and safe["lost_wire"] == 0
    assert dup["duplicates"] > 0                 # the late wire copy and the sweep both evaluate
    assert safe["duplicates"] == 0               # idempotency key on trade_id
    assert safe["extra_rest_calls"] >= dup["extra_rest_calls"] - 1


def test_restart_loses_in_memory_pending_candidates_unless_journaled():
    # The retry budget (12 x 2 s) must outlast the 15 s outage, otherwise a
    # journaled candidate is merely abandoned later instead of lost sooner.
    scenario = _recovery(transient_windows=[(30.0, 45.0)], wire_loss_windows=[], restart_at=35.0)
    memory = rs.simulate_recovery(scenario, rs.RetryStateMachine(max_attempts=12, backoff_sec=2.0, journaled=False))
    journal = rs.simulate_recovery(scenario, rs.RetryStateMachine(max_attempts=12, backoff_sec=2.0, journaled=True))
    assert memory["lost_restart"] > 0
    assert journal["lost_restart"] == 0
    assert journal["evaluated_once"] > memory["evaluated_once"]


def test_reconciliation_cost_is_reported_against_the_loss_it_recovers():
    r = rs.simulate_recovery(_recovery(), rs.RetryPlusReconciliation(max_attempts=6, backoff_sec=2.0,
                                                                      sweep_every_sec=30.0, sweep_delay_sec=60.0, idempotent=True))
    assert r["sweeps"] > 0
    assert r["sweep_rest_calls"] > 0
    assert r["recovered_by_sweep"] > 0
    assert r["sweep_rest_calls_per_recovered"] > 0.0


# --- CLI / presets ----------------------------------------------------------------

def test_presets_and_cli_cover_the_plan_requirements(capsys):
    assert {"measured_quiet", "measured_busy", "background_storm", "429_storm", "timeouts"} <= set(rs.PRESETS)
    assert rs.main(["--compare", "--presets", "measured_quiet", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"measured_quiet"' in out and '"fifo_8"' in out
    assert rs.main(["--recovery", "--json"]) == 0
