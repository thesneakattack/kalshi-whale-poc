"""Tests for tools/soak_analyzer.py.

Every test drives the pure check functions over a synthetic payload - the
tool reaches the app only through fetch_pipeline(), which nothing here
calls, so the suite never touches the live app or any data/*.db.
"""

import json

import pytest

from tools import soak_analyzer as sa


def _payload(**over):
    """A quiescent, healthy pipeline payload. Overrides are deep-merged
    one level into queue_health/queue so a test can state only what it
    is actually about."""
    q = {"depth": 0, "capacity": 20000, "coalesced_tickers": 100,
         "pending_tickers": 0, "high_water": 500,
         "oldest_message_age_sec": 0.0}
    q.update(over.pop("queue", {}))
    qh = {"dropped_messages": 0, "dropped_window": 0,
          "received_by_class": {"ticker": 1100},
          "processed_by_class": {"ticker": 1000},
          "dropped_by_class": {}, "queue": q}
    qh.update(over.pop("queue_health", {}))
    sched = {"settlement_resolver": {"enqueued_total": 100, "resolved_total": 90,
                                     "pending": 10, "dropped_total": 0}}
    sched.update(over.pop("schedulers", {}))
    faults = {"by_component": {}, "most_frequent": []}
    faults.update(over.pop("faults_last_24h", {}))
    return {"ingest": {"queue_health": qh}, "schedulers": sched,
            "faults_last_24h": faults, **over}


def _by_id(checks):
    return {c.id: c for c in checks}


# --- the BLIND path: the failure mode this tool exists for ----------------

def test_staleness_metric_flagged_blind_when_pending_map_is_nonempty():
    """A wedged coalescing map with drained queues makes
    oldest_message_age_sec report 0.0. That must not read as health."""
    p = _payload(queue={"pending_tickers": 42, "depth": 0,
                        "oldest_message_age_sec": 0.0})
    c = _by_id(sa.run_checks(p))["staleness_metric_trustworthy"]
    assert c.status == sa.BLIND
    assert "42" in c.detail


def test_blind_outranks_fail_in_the_verdict():
    """BLIND must win: a FAIL is a known-bad measurement, BLIND means the
    measurement itself cannot be trusted, which is strictly worse."""
    checks = [sa.Check("a", sa.DATA_PLANE, sa.FAIL, ""),
              sa.Check("b", sa.DATA_PLANE, sa.BLIND, "")]
    assert sa.verdict(checks) == sa.BLIND


def test_staleness_passes_when_pending_map_corroborates_the_age():
    p = _payload(queue={"pending_tickers": 0, "oldest_message_age_sec": 0.0})
    assert _by_id(sa.run_checks(p))["staleness_metric_trustworthy"].status == sa.PASS


# --- conservation is only decidable on a quiescent sample -----------------

def test_conservation_holds_when_counters_balance():
    p = _payload(queue_health={"received_by_class": {"ticker": 1100},
                               "processed_by_class": {"ticker": 1000}},
                 queue={"coalesced_tickers": 100, "pending_tickers": 0})
    assert _by_id(sa.run_checks(p))["ticker_conservation"].status == sa.PASS


def test_conservation_fails_and_reports_the_gap():
    p = _payload(queue_health={"received_by_class": {"ticker": 1174},
                               "processed_by_class": {"ticker": 1000}},
                 queue={"coalesced_tickers": 100, "pending_tickers": 0})
    c = _by_id(sa.run_checks(p))["ticker_conservation"]
    assert c.status == sa.FAIL
    assert c.measured["gap"] == 74


def test_conservation_is_unknown_not_failed_while_items_are_in_flight():
    """With a non-empty queue the difference is legitimately in-flight.
    Failing here would be noise, and noise is what gets baselined away."""
    p = _payload(queue_health={"received_by_class": {"ticker": 1174},
                               "processed_by_class": {"ticker": 1000}},
                 queue={"depth": 74, "coalesced_tickers": 100})
    assert _by_id(sa.run_checks(p))["ticker_conservation"].status == sa.UNKNOWN


# --- data-layer checks ----------------------------------------------------

def test_ingest_drops_fail_on_lifetime_drops_even_when_window_is_clean():
    p = _payload(queue_health={"dropped_messages": 5, "dropped_window": 0})
    assert _by_id(sa.run_checks(p))["ingest_no_drops"].status == sa.FAIL


def test_queue_headroom_fails_above_the_pressure_threshold():
    p = _payload(queue={"high_water": 14755, "capacity": 20000})
    c = _by_id(sa.run_checks(p))["queue_headroom"]
    assert c.status == sa.FAIL
    assert c.measured["fraction"] == pytest.approx(0.7378)  # tool rounds to 4dp


def test_resolver_accounting_fails_when_totals_do_not_balance():
    p = _payload(schedulers={"settlement_resolver": {
        "enqueued_total": 100, "resolved_total": 90,
        "pending": 5, "dropped_total": 0}})
    assert _by_id(sa.run_checks(p))["resolver_accounting"].status == sa.FAIL


def test_missing_counters_are_unknown_never_pass():
    """An absent counter must never silently satisfy a criterion."""
    p = _payload(schedulers={"settlement_resolver": {}})
    ids = _by_id(sa.run_checks(p))
    assert ids["resolver_accounting"].status == sa.UNKNOWN
    assert ids["settlement_completeness"].status == sa.UNKNOWN


# --- analysis-readiness checks name what they invalidate ------------------

def test_settlement_drops_fail_and_name_the_invalidated_analysis():
    p = _payload(schedulers={"settlement_resolver": {
        "enqueued_total": 32128, "resolved_total": 30903,
        "pending": 1161, "dropped_total": 64}})
    c = _by_id(sa.run_checks(p))["settlement_completeness"]
    assert c.status == sa.FAIL
    assert c.layer == sa.ANALYSIS_READINESS
    assert c.invalidates and "P&L" in c.invalidates


def test_every_failing_analysis_check_states_what_it_invalidates():
    """The layer contract: an analysis-readiness failure is only useful if
    it says which downstream conclusion it poisons."""
    p = _payload(
        queue_health={"received_by_class": {"ticker": 1174},
                      "processed_by_class": {"ticker": 1000}},
        schedulers={"settlement_resolver": {
            "enqueued_total": 100, "resolved_total": 30,
            "pending": 6, "dropped_total": 64}},
        faults_last_24h={"by_component": {"capture_writer": 220},
                         "most_frequent": [
                             {"operation": "stale_price_uncorroborated",
                              "count": 3}]})
    failing = [c for c in sa.run_checks(p)
               if c.layer == sa.ANALYSIS_READINESS and c.status == sa.FAIL]
    assert len(failing) == 4
    for c in failing:
        assert c.invalidates, f"{c.id} fails without naming the impact"


def test_capture_writer_faults_invalidate_the_raw_archive():
    p = _payload(faults_last_24h={"by_component": {"capture_writer": 220}})
    c = _by_id(sa.run_checks(p))["capture_writer_health"]
    assert c.status == sa.FAIL and "raw_trades" in c.invalidates


# --- layer separation is structural, not a naming convention -------------

def test_checks_are_partitioned_into_exactly_the_two_layers():
    layers = {c.layer for c in sa.run_checks(_payload())}
    assert layers == {sa.DATA_PLANE, sa.ANALYSIS_READINESS}


def test_data_layer_checks_never_claim_to_invalidate_an_analysis():
    """Naming a downstream analysis is the analysis layer's job. A data
    check that did it would be reaching across the boundary."""
    for c in sa.run_checks(_payload()):
        if c.layer == sa.DATA_PLANE:
            assert c.invalidates is None


# --- CLI ------------------------------------------------------------------

def test_main_reads_a_saved_payload_and_exits_nonzero_on_failure(tmp_path, capsys):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(_payload(queue={"high_water": 19999,
                                            "capacity": 20000})))
    assert sa.main(["--from-file", str(f)]) == 1
    assert "VERDICT: FAIL" in capsys.readouterr().out


def test_main_exits_zero_when_everything_passes(tmp_path, capsys):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(_payload()))
    assert sa.main(["--from-file", str(f)]) == 0
    assert "VERDICT: PASS" in capsys.readouterr().out


def test_json_mode_emits_parseable_output_with_every_check(tmp_path, capsys):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(_payload()))
    sa.main(["--from-file", str(f), "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["verdict"] == sa.PASS
    assert len(d["checks"]) == 9
