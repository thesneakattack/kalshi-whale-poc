"""Stage-by-stage whale-pipeline timing (realtime data-plane task I2).

The aggregator itself is pure and bounded: a fixed set of stage and counter
names, O(1) per sample, no persistence of its own. Persistence and window
ownership belong to services/observability (see the tests appended to
tests/test_observability.py)."""
import pytest

from services import whale_pipeline_perf as wpp


def test_stage_names_and_counter_names_are_a_fixed_bounded_set():
    assert set(wpp.STAGES) == {
        "capture", "config", "provider", "resolve", "thread_wait", "sync", "signals",
        "handler_total", "receive_to_handler_end", "receive_to_decision",
    }
    assert set(wpp.COUNTERS) == {
        "trades", "below_threshold", "offlist_skipped", "unresolved_market", "candidates",
        "offlist_candidates", "to_thread_entries", "rejection_writes", "resolve_calls",
        "resolve_failures", "batch_capacity_truncated", "signals_emitted",
    }


def test_record_stage_aggregates_window_and_lifetime_in_milliseconds():
    perf = wpp.WhalePipelinePerf()
    perf.record_stage("sync", 0.002)
    perf.record_stage("sync", 0.006)
    s = perf.snapshot()["stages"]["sync"]
    assert s["window"] == {"count": 2, "avg_ms": 4.0, "max_ms": 6.0}
    assert s["lifetime"] == {"count": 2, "avg_ms": 4.0, "max_ms": 6.0}


def test_unrecorded_stages_report_empty_windows_not_fabricated_zeros():
    perf = wpp.WhalePipelinePerf()
    s = perf.snapshot()["stages"]["capture"]
    assert s["window"] == {"count": 0, "avg_ms": None, "max_ms": None}


def test_unknown_stage_or_counter_is_rejected_so_typos_cannot_grow_the_label_set():
    perf = wpp.WhalePipelinePerf()
    with pytest.raises(KeyError):
        perf.record_stage("sink", 0.1)
    with pytest.raises(KeyError):
        perf.record_count("tradez")


def test_counters_accumulate_in_both_window_and_lifetime():
    perf = wpp.WhalePipelinePerf()
    perf.record_count("trades", 3)
    perf.record_count("trades")
    perf.record_count("candidates")
    c = perf.snapshot()["counters"]
    assert c["window"]["trades"] == 4 and c["lifetime"]["trades"] == 4
    assert c["window"]["candidates"] == 1
    assert c["window"]["below_threshold"] == 0


def test_receive_to_decision_gets_fixed_buckets_and_a_p95_bound():
    perf = wpp.WhalePipelinePerf()
    for _ in range(19):
        perf.record_stage("receive_to_decision", 0.05)
    perf.record_stage("receive_to_decision", 12.0)
    r = perf.snapshot()["receive_to_decision"]
    assert r["buckets"] == {"le_1ms": 0, "le_10ms": 0, "le_100ms": 19, "le_1s": 0, "le_10s": 0, "gt_10s": 1}
    assert r["window_p95_upper_bound_sec"] == 0.1


def test_reset_window_clears_window_figures_but_keeps_lifetime():
    perf = wpp.WhalePipelinePerf()
    perf.record_stage("provider", 0.004)
    perf.record_stage("receive_to_decision", 0.5)
    perf.record_count("trades", 7)
    perf.reset_window()
    snap = perf.snapshot()
    assert snap["stages"]["provider"]["window"]["count"] == 0
    assert snap["stages"]["provider"]["lifetime"]["count"] == 1
    assert snap["counters"]["window"]["trades"] == 0
    assert snap["counters"]["lifetime"]["trades"] == 7
    assert sum(snap["receive_to_decision"]["buckets"].values()) == 0


def test_module_singleton_is_the_one_the_hot_path_records_into():
    assert isinstance(wpp.perf, wpp.WhalePipelinePerf)
