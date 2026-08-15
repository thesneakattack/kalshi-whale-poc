import pytest

from services import suggestion_decisions as sd


@pytest.fixture(autouse=True)
def _redirect_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "DB_PATH", tmp_path / "suggestion_decisions.db")


def test_decline_then_declined_ids_contains_it():
    sd.decline("abc123", "strategy.entry_threshold", now=1000.0)
    assert sd.declined_ids() == {"abc123"}


def test_undeclared_suggestion_is_not_in_declined_ids():
    assert sd.declined_ids() == set()


def test_decline_is_idempotent_not_a_new_row():
    sd.decline("abc123", "strategy.entry_threshold", now=1000.0)
    sd.decline("abc123", "strategy.entry_threshold", now=2000.0)
    assert len(sd.list_declined()) == 1
    assert sd.list_declined()[0]["declined_at"] == 2000.0


def test_undecline_removes_it_and_reports_it_existed():
    sd.decline("abc123", "strategy.entry_threshold", now=1000.0)
    assert sd.undecline("abc123") is True
    assert sd.declined_ids() == set()


def test_undecline_nonexistent_id_reports_false():
    assert sd.undecline("nope") is False


def test_a_new_suggestion_id_for_the_same_config_path_is_unaffected():
    # Direct design point: since every suggestion id already encodes its
    # own evidence (config_path|suggested_value|n), declining one id must
    # never suppress a *different* id on the same config_path - that's how
    # "don't nag with stale evidence, but resurface on genuinely new
    # evidence" falls out for free once the underlying value/n changes.
    sd.decline("old-id-for-0.75", "strategy.entry_threshold", now=1000.0)
    assert "new-id-for-0.80" not in sd.declined_ids()


def test_list_declined_orders_newest_first():
    sd.decline("first", "strategy.entry_threshold", now=1000.0)
    sd.decline("second", "strategy.stop_loss_pct", now=2000.0)
    ids_in_order = [row["id"] for row in sd.list_declined()]
    assert ids_in_order == ["second", "first"]


def test_clear_all_removes_every_row():
    sd.decline("a", "strategy.entry_threshold", now=1000.0)
    sd.decline("b", "strategy.stop_loss_pct", now=2000.0)
    sd.clear_all()
    assert sd.declined_ids() == set()
