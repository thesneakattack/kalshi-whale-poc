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
          "dropped_by_class": {}, "discarded_on_reconnect_by_class": {},
          "queue": q}
    qh.update(over.pop("queue_health", {}))
    sched = {"settlement_resolver": {"enqueued_total": 100, "resolved_total": 90,
                                     "pending": 10, "dropped_total": 0,
                                     "dropped_after_max_attempts": 0,
                                     "skipped_non_binary_result": 0}}
    sched.update(over.pop("schedulers", {}))
    faults = {"by_component": {"capture_writer": 0, "exit_engine": 0},
              "most_frequent": []}
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


def test_conservation_counts_reconnect_discards_as_accounted():
    """#209: _begin_connection throws away every queued item and the pending
    map on each reconnect - counted on arrival, deliberately discarded, and
    until counted indistinguishable from a leak (gap constant at 74 across
    12 reconnects). A distinct term, not folded into dropped."""
    p = _payload(queue_health={"received_by_class": {"ticker": 1174},
                               "processed_by_class": {"ticker": 1000},
                               "discarded_on_reconnect_by_class": {"ticker": 74}},
                 queue={"coalesced_tickers": 100, "pending_tickers": 0})
    c = _by_id(sa.run_checks(p))["ticker_conservation"]
    assert c.status == sa.PASS
    assert c.measured["discarded_on_reconnect"] == 74
    assert c.measured["gap"] == 0
    assert "discarded_on_reconnect" in c.detail


def test_conservation_with_the_discard_counter_absent_never_passes_silently():
    """An app that predates #209 exposes no discarded_on_reconnect_by_class.
    The term is assumed 0 - and the check says so - and an identity that
    balances only with an assumed term is UNKNOWN, never PASS."""
    p = _payload(queue_health={"received_by_class": {"ticker": 1100},
                               "processed_by_class": {"ticker": 1000}},
                 queue={"coalesced_tickers": 100, "pending_tickers": 0})
    del p["ingest"]["queue_health"]["discarded_on_reconnect_by_class"]
    c = _by_id(sa.run_checks(p))["ticker_conservation"]
    assert c.status == sa.UNKNOWN
    assert "discarded_on_reconnect_by_class absent" in c.detail
    assert "assumed 0" in c.detail
    assert c.measured["discarded_on_reconnect"] == 0
    assert c.measured["discarded_on_reconnect_measured"] is False


def test_conservation_with_the_discard_counter_absent_still_fails_on_a_gap():
    """A gap is real whatever the app version: FAIL is kept, and the detail
    still names the unmeasured term so the reader knows the gap may be an
    uncounted reconnect discard rather than a leak."""
    p = _payload(queue_health={"received_by_class": {"ticker": 1174},
                               "processed_by_class": {"ticker": 1000}},
                 queue={"coalesced_tickers": 100, "pending_tickers": 0})
    del p["ingest"]["queue_health"]["discarded_on_reconnect_by_class"]
    c = _by_id(sa.run_checks(p))["ticker_conservation"]
    assert c.status == sa.FAIL
    assert c.measured["gap"] == 74
    assert "discarded_on_reconnect_by_class absent" in c.detail


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


def test_queue_headroom_fails_on_current_depth_above_threshold():
    p = _payload(queue={"depth": 14755, "capacity": 20000})
    c = _by_id(sa.run_checks(p))["queue_headroom"]
    assert c.status == sa.FAIL
    assert c.measured["fraction"] == pytest.approx(0.7378)  # tool rounds to 4dp


def test_queue_headroom_does_not_latch_on_a_historical_high_water():
    """high_water is monotonic and never reset. Gating on it would pin the
    check FAIL forever after one spike, and a permanently red check gets
    ignored or baselined - the outcome the tool is meant to prevent."""
    p = _payload(queue={"depth": 0, "high_water": 14755, "capacity": 20000})
    c = _by_id(sa.run_checks(p))["queue_headroom"]
    assert c.status == sa.PASS
    assert c.measured["high_water"] == 14755   # still reported, just not gated
    assert "lifetime peak" in c.detail


def test_resolver_accounting_fails_when_totals_do_not_balance():
    p = _payload(schedulers={"settlement_resolver": {
        "enqueued_total": 100, "resolved_total": 90,
        "pending": 5, "dropped_total": 0,
        "dropped_after_max_attempts": 0, "skipped_non_binary_result": 0}})
    assert _by_id(sa.run_checks(p))["resolver_accounting"].status == sa.FAIL


def test_resolver_accounting_still_balances_on_the_conflated_sum():
    """dropped_total keeps counting both branches on purpose (issue #208):
    it is the conservation term, and enqueued == resolved + pending +
    dropped_total only holds if it stays the sum."""
    p = _payload(schedulers={"settlement_resolver": {
        "enqueued_total": 100, "resolved_total": 90, "pending": 4,
        "dropped_total": 6, "dropped_after_max_attempts": 2,
        "skipped_non_binary_result": 4}})
    assert _by_id(sa.run_checks(p))["resolver_accounting"].status == sa.PASS


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
        "pending": 1161, "dropped_total": 64,
        "dropped_after_max_attempts": 64, "skipped_non_binary_result": 0}})
    c = _by_id(sa.run_checks(p))["settlement_completeness"]
    assert c.status == sa.FAIL
    assert c.layer == sa.ANALYSIS_READINESS
    assert c.invalidates and "P&L" in c.invalidates


def test_non_binary_skips_alone_do_not_fail_settlement_completeness():
    """Issue #208, the whole point: a finalized market with no binary
    outcome (docs/kalshi/market_lifecycle.md:68, market-settlement.md:23 -
    `yes`, `no`, or `scalar`) is correctly skipped, not a completeness
    defect. Gating on the conflated total made the criterion unsatisfiable
    whenever any scalar market settled."""
    p = _payload(schedulers={"settlement_resolver": {
        "enqueued_total": 32128, "resolved_total": 30903,
        "pending": 1161, "dropped_total": 64,
        "dropped_after_max_attempts": 0, "skipped_non_binary_result": 64}})
    c = _by_id(sa.run_checks(p))["settlement_completeness"]
    assert c.status == sa.PASS
    assert c.measured["skipped_non_binary_result"] == 64
    assert "64" in c.detail  # reported, just not gated on


def test_settlement_completeness_is_unknown_when_only_the_conflated_total_exists():
    """An app predating the split exposes dropped_total alone. Reading it
    as the defect counter is exactly the bug; UNKNOWN is the honest answer,
    and UNKNOWN is never a pass."""
    p = _payload(schedulers={"settlement_resolver": {
        "enqueued_total": 32128, "resolved_total": 30903,
        "pending": 1161, "dropped_total": 64}})
    assert _by_id(sa.run_checks(p))["settlement_completeness"].status == sa.UNKNOWN


def test_every_failing_analysis_check_states_what_it_invalidates():
    """The layer contract: an analysis-readiness failure is only useful if
    it says which downstream conclusion it poisons."""
    p = _payload(
        queue_health={"received_by_class": {"ticker": 1174},
                      "processed_by_class": {"ticker": 1000}},
        schedulers={"settlement_resolver": {
            "enqueued_total": 100, "resolved_total": 30,
            "pending": 6, "dropped_total": 64,
            "dropped_after_max_attempts": 64,
            "skipped_non_binary_result": 0}},
        faults_last_24h={"by_component": {"capture_writer": 220,
                                          "exit_engine": 175},
                         "most_frequent": [
                             {"operation": "stale_price_uncorroborated",
                              "count": 3}]})
    failing = [c for c in sa.run_checks(p)
               if c.layer == sa.ANALYSIS_READINESS and c.status == sa.FAIL]
    assert len(failing) == 4
    for c in failing:
        assert c.invalidates, f"{c.id} fails without naming the impact"


def test_exit_engine_faults_counted_from_complete_data_not_the_top_n_list():
    """Regression: the check summed `most_frequent`, a truncated top-N
    list, and called it a total - reporting 1-3 while the component had
    175 faults. It must gate on `by_component`, which is complete."""
    p = _payload(faults_last_24h={
        "by_component": {"capture_writer": 0, "exit_engine": 175},
        "most_frequent": [{"operation": "stale_price_uncorroborated",
                           "count": 1}]})
    c = _by_id(sa.run_checks(p))["exit_engine_faults"]
    assert c.status == sa.FAIL
    assert c.measured["exit_engine_faults_24h"] == 175
    assert "175" in c.detail


def test_incomplete_per_operation_figure_is_labelled_a_floor():
    """The API exposes no complete per-operation breakdown, so the
    stale-price number is reported as a floor and never gated on."""
    p = _payload(faults_last_24h={
        "by_component": {"capture_writer": 0, "exit_engine": 175},
        "most_frequent": [{"operation": "stale_price_uncorroborated",
                           "count": 1}]})
    c = _by_id(sa.run_checks(p))["exit_engine_faults"]
    assert c.measured["stale_price_uncorroborated_floor"] == 1
    assert "floor" in c.detail


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
    f.write_text(json.dumps(_payload(queue={"depth": 19999,
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
    assert len(d["checks"]) == 10


# --- fetch hardening ------------------------------------------------------

def test_unreachable_api_reports_unknown_not_a_traceback(monkeypatch, capsys):
    """A tool that reports health must never turn a degraded app into a
    stack trace. /api/health/pipeline was measured at 41.9s on a loaded
    app - the case this tool is most needed for."""
    def boom(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(sa, "fetch_pipeline", boom)
    assert sa.main([]) == 1
    out = capsys.readouterr().out
    assert "VERDICT: UNKNOWN" in out and "unreachable" in out


def test_default_timeout_exceeds_the_measured_slow_response():
    assert sa.DEFAULT_TIMEOUT_SEC > 41.9


def test_tls_verification_kept_for_a_non_local_host():
    assert sa._is_local("https://kalshi-whale-poc.ddev.site:8443")
    assert sa._is_local("http://localhost:8000")
    assert not sa._is_local("https://example.com")


# --- transitive trust: a derived metric never outranks its inputs --------

def test_backlog_timeliness_inherits_blind_from_the_metric_it_reads():
    """The whole point: a wedged coalescing map makes
    oldest_message_age_sec report 0.0. A timeliness check reading that
    number must not answer PASS - it must inherit BLIND. This is how the
    soak's pass criterion came to be believed."""
    p = _payload(queue={"pending_tickers": 42, "depth": 0,
                        "oldest_message_age_sec": 0.0})
    ids = _by_id(sa.run_checks(p))
    assert ids["staleness_metric_trustworthy"].status == sa.BLIND
    assert ids["backlog_timeliness"].status == sa.BLIND
    assert "inherited BLIND" in ids["backlog_timeliness"].detail


def test_price_completeness_inherits_unknown_from_conservation():
    p = _payload(queue={"depth": 5})  # not quiescent -> conservation UNKNOWN
    ids = _by_id(sa.run_checks(p))
    assert ids["ticker_conservation"].status == sa.UNKNOWN
    assert ids["price_completeness"].status == sa.UNKNOWN


def test_dependency_resolution_never_promotes_a_failure():
    """Downgrade only. A healthy input must not rescue a real FAIL."""
    checks = [sa.Check("dep", sa.DATA_PLANE, sa.PASS, ""),
              sa.Check("derived", sa.DATA_PLANE, sa.FAIL, "",
                       depends_on=("dep",))]
    assert sa.resolve_dependencies(checks)[1].status == sa.FAIL


def test_partial_source_cannot_yield_a_pass():
    """'Nothing found' in an incomplete sample is not evidence that nothing
    is there, so a partial source downgrades PASS to UNKNOWN."""
    checks = [sa.Check("c", sa.DATA_PLANE, sa.PASS, "clean",
                       source_complete=False)]
    out = sa.resolve_dependencies(checks)[0]
    assert out.status == sa.UNKNOWN and "source is partial" in out.detail


def test_partial_source_still_reports_a_real_failure():
    """What a partial sample DID find is real - only the all-clear is void."""
    checks = [sa.Check("c", sa.DATA_PLANE, sa.FAIL, "found 3",
                       source_complete=False)]
    assert sa.resolve_dependencies(checks)[0].status == sa.FAIL


def test_a_missing_dependency_is_ignored_not_crashed_on():
    checks = [sa.Check("derived", sa.DATA_PLANE, sa.PASS, "",
                       depends_on=("nonexistent",))]
    assert sa.resolve_dependencies(checks)[0].status == sa.PASS


def test_every_declared_dependency_names_a_real_check():
    """A typo in depends_on would silently disable propagation."""
    checks = sa.run_checks(_payload())
    ids = {c.id for c in checks}
    for c in checks:
        for dep in c.depends_on:
            assert dep in ids, f"{c.id} depends on unknown check {dep!r}"
