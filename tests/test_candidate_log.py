import pytest

from services import candidate_log as cl


@pytest.fixture(autouse=True)
def _redirect_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "DB_PATH", tmp_path / "candidate_log.db")


def test_record_rejection_creates_unresolved_row():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    gates = cl.gate_summary()
    assert len(gates) == 1
    assert gates[0]["strategy"] == "market_native"
    assert gates[0]["gate_name"] == "max_spread"
    assert gates[0]["rejected_count"] == 1
    assert gates[0]["resolved_count"] == 0


def test_repeated_rejection_updates_in_place_not_a_new_row():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.09, 0.05, now=2000.0)
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.10, 0.05, now=3000.0)
    gates = cl.gate_summary()
    assert len(gates) == 1
    assert gates[0]["rejected_count"] == 1  # one row, not three


def test_different_gates_and_tickers_produce_separate_rows():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    cl.record_rejection("TICK-A", "market_native", "min_volume_24h", 100, 500)
    cl.record_rejection("TICK-B", "whale_follow", "entry_threshold", 0.5, 0.6)
    gates = cl.gate_summary()
    assert len(gates) == 3


def test_resolve_from_market_results_marks_resolved_and_records_result():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    resolved = cl.resolve_from_market_results({"TICK-A": "yes"})
    assert resolved == 1
    gates = cl.gate_summary()
    assert gates[0]["resolved_count"] == 1
    assert gates[0]["yes_count"] == 1
    assert gates[0]["no_count"] == 0


def test_resolve_ignores_tickers_not_yet_settled():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    resolved = cl.resolve_from_market_results({"TICK-A": ""})
    assert resolved == 0
    resolved = cl.resolve_from_market_results({"TICK-A": None})
    assert resolved == 0
    resolved = cl.resolve_from_market_results({})
    assert resolved == 0
    gates = cl.gate_summary()
    assert gates[0]["resolved_count"] == 0


def test_resolved_row_is_not_overwritten_by_a_later_rejection():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    cl.resolve_from_market_results({"TICK-A": "yes"})
    # A later call for the same ticker/gate (shouldn't normally happen once
    # a market has resolved, since both strategies skip resolved tickers
    # before reaching any gate - but the WHERE resolved=0 guard should hold
    # regardless).
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.20, 0.05, now=9999.0)
    gates = cl.gate_summary()
    assert gates[0]["resolved_count"] == 1
    assert gates[0]["yes_count"] == 1


def test_hypothetical_win_rate_none_when_no_side_known():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, side=None)
    cl.resolve_from_market_results({"TICK-A": "yes"})
    gates = cl.gate_summary()
    assert gates[0]["hypothetical_win_rate"] is None
    assert gates[0]["hypothetical_win_rate_n"] == 0


def test_hypothetical_win_rate_computed_when_side_known():
    cl.record_rejection("TICK-A", "whale_follow", "entry_threshold", 0.5, 0.6, side="yes")
    cl.record_rejection("TICK-B", "whale_follow", "entry_threshold", 0.5, 0.6, side="yes")
    cl.record_rejection("TICK-C", "whale_follow", "entry_threshold", 0.5, 0.6, side="no")
    cl.resolve_from_market_results({"TICK-A": "yes", "TICK-B": "no", "TICK-C": "no"})
    gates = cl.gate_summary()
    assert gates[0]["hypothetical_win_rate_n"] == 3
    # TICK-A (side yes, result yes) wins, TICK-B (side yes, result no) loses,
    # TICK-C (side no, result no) wins -> 2/3 = 66.7%
    assert gates[0]["hypothetical_win_rate"] == pytest.approx(66.7, abs=0.1)


def test_clear_all_wipes_every_row():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    cl.record_rejection("TICK-B", "whale_follow", "entry_threshold", 0.5, 0.6)
    cl.clear_all()
    assert cl.gate_summary() == []


def test_gate_summary_sorted_by_resolved_count_descending():
    cl.record_rejection("TICK-A", "market_native", "gate_a", 1, 2)
    cl.record_rejection("TICK-B", "market_native", "gate_b", 1, 2)
    cl.resolve_from_market_results({"TICK-B": "yes"})
    gates = cl.gate_summary()
    assert gates[0]["gate_name"] == "gate_b"  # 1 resolved, sorts first
    assert gates[1]["gate_name"] == "gate_a"  # 0 resolved
