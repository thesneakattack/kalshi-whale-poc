from services.config import config_performance as cp


def _cfg(**overrides):
    strategy = dict(
        name="follow_the_whale", entry_threshold=0.65, max_position_pct=0.05,
        cooldown_sec=300, min_whale_winrate_pct=40, min_resolved_for_whale_filter=5,
        live_markets_only=False, excluded_series=[],
    )
    strategy.update(overrides)
    return {"strategy": strategy}


def test_fingerprint_stable_regardless_of_dict_key_order():
    cfg_a = {"strategy": {"name": "x", "b": 2, "a": 1}}
    cfg_b = {"strategy": {"name": "x", "a": 1, "b": 2}}
    assert cp.fingerprint(cfg_a) == cp.fingerprint(cfg_b)


def test_fingerprint_changes_when_any_strategy_field_changes():
    base = cp.fingerprint(_cfg(entry_threshold=0.65))
    changed = cp.fingerprint(_cfg(entry_threshold=0.66))
    assert base != changed


def test_fingerprint_excludes_name_field():
    a = cp.fingerprint({"strategy": {"name": "strategy_a", "entry_threshold": 0.5}})
    b = cp.fingerprint({"strategy": {"name": "strategy_b", "entry_threshold": 0.5}})
    assert a == b


def test_fingerprint_ignores_non_strategy_config():
    a = cp.fingerprint({**_cfg(), "kalshi": {"poll_interval_sec": 15}})
    b = cp.fingerprint({**_cfg(), "kalshi": {"poll_interval_sec": 30}})
    assert a == b


def test_strategy_subset_excludes_name():
    subset = cp.strategy_subset(_cfg())
    assert "name" not in subset
    assert "entry_threshold" in subset


def test_record_variant_is_idempotent_first_seen_at_set_once(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cfg = _cfg()
    fp = cp.fingerprint(cfg)
    cp.record_variant(fp, cfg)
    first = cp.get_variant(fp)["first_seen_at"]
    cp.record_variant(fp, cfg)
    second = cp.get_variant(fp)["first_seen_at"]
    assert first == second


def test_record_variant_stores_actual_config_values(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cfg = _cfg(entry_threshold=0.42)
    fp = cp.fingerprint(cfg)
    cp.record_variant(fp, cfg)
    variant = cp.get_variant(fp)
    assert variant["config"]["entry_threshold"] == 0.42
    assert "name" not in variant["config"]


def test_get_variant_returns_none_for_unknown_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    assert cp.get_variant("nope") is None


def test_all_variants_ordered_by_first_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    fp1 = cp.fingerprint(_cfg(entry_threshold=0.1))
    fp2 = cp.fingerprint(_cfg(entry_threshold=0.2))
    cp.record_variant(fp1, _cfg(entry_threshold=0.1))
    cp.record_variant(fp2, _cfg(entry_threshold=0.2))
    variants = cp.all_variants()
    assert [v["fingerprint"] for v in variants] == [fp1, fp2]


def test_log_and_read_applied_change(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cp.log_applied_change(
        config_path="strategy.entry_threshold", old_value=0.05, new_value=0.1,
        rationale="test rationale", trade_count=30,
        fingerprint_before="fp1", fingerprint_after="fp2", auto_applied=False,
    )
    changes = cp.recent_applied_changes()
    assert len(changes) == 1
    c = changes[0]
    assert c["config_path"] == "strategy.entry_threshold"
    assert c["old_value"] == 0.05
    assert c["new_value"] == 0.1
    assert c["trade_count"] == 30
    assert c["auto_applied"] is False
    assert c["source"] == "manual"  # log_applied_change's own default when unspecified
    assert cp.applied_changes_count() == 1


def test_source_column_backfills_pre_existing_rows_as_unified_advisory(tmp_path, monkeypatch):
    # data/config_performance.db is a live file (CLAUDE.md) - simulates the
    # real table as it existed before the source column was added (2026-08-10,
    # Item 3D). Every real row up to that point came from exactly one place
    # (the Advisory apply route), so the column's own SQL DEFAULT backfilling
    # them as 'unified-advisory' is factually correct, not a placeholder.
    import sqlite3
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    conn = sqlite3.connect(cp.DB_PATH)
    conn.execute(
        """CREATE TABLE applied_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, applied_at REAL NOT NULL, config_path TEXT NOT NULL,
            old_value TEXT NOT NULL, new_value TEXT NOT NULL, rationale TEXT NOT NULL,
            trade_count INTEGER NOT NULL, fingerprint_before TEXT NOT NULL, fingerprint_after TEXT NOT NULL,
            auto_applied INTEGER NOT NULL DEFAULT 0
        )"""
    )
    conn.execute(
        "INSERT INTO applied_changes (applied_at, config_path, old_value, new_value, rationale, "
        "trade_count, fingerprint_before, fingerprint_after) VALUES (0, 'strategy.entry_threshold', "
        "'0.5', '0.6', 'old row', 10, 'fp1', 'fp2')"
    )
    conn.commit()
    conn.close()

    assert cp.recent_applied_changes()[0]["source"] == "unified-advisory"


def test_log_applied_change_records_explicit_source(tmp_path, monkeypatch):
    # Item 3D (2026-08-10) - source distinguishes a plain Config-tab save
    # from an Advisory-applied suggestion (and, later, agent-driven ones)
    # in the same shared change-history table.
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cp.log_applied_change(
        config_path="risk.max_daily_loss_pct", old_value=0.03, new_value=0.04,
        rationale="r", trade_count=20, fingerprint_before="fp1", fingerprint_after="fp1",
        source="unified-advisory",
    )
    assert cp.recent_applied_changes()[0]["source"] == "unified-advisory"


def test_applied_changes_pagination_and_ordering(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    for i in range(3):
        cp.log_applied_change(
            config_path=f"strategy.field{i}", old_value=i, new_value=i + 1,
            rationale="r", trade_count=10, fingerprint_before="a", fingerprint_after="b",
        )
    changes = cp.recent_applied_changes(limit=2, offset=0)
    assert len(changes) == 2
    # newest first
    assert changes[0]["config_path"] == "strategy.field2"


# --- last_applied_at (2026-08-10, auto-apply cooldown checks) --------------

def test_last_applied_at_none_when_nothing_logged_for_that_source(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    assert cp.last_applied_at("calibration-auto-apply") is None


def test_last_applied_at_returns_the_most_recent_timestamp_for_that_source(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cp.log_applied_change(
        config_path="whale_confidence_weights", old_value={}, new_value={},
        rationale="r1", trade_count=0, fingerprint_before="a", fingerprint_after="a",
        source="calibration-auto-apply",
    )
    first = cp.last_applied_at("calibration-auto-apply")
    assert first is not None
    cp.log_applied_change(
        config_path="whale_confidence_weights", old_value={}, new_value={},
        rationale="r2", trade_count=0, fingerprint_before="a", fingerprint_after="a",
        source="calibration-auto-apply",
    )
    second = cp.last_applied_at("calibration-auto-apply")
    assert second >= first


def test_last_applied_at_scoped_to_the_given_source_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cp.log_applied_change(
        config_path="strategy.entry_threshold", old_value=0.5, new_value=0.6,
        rationale="r", trade_count=10, fingerprint_before="a", fingerprint_after="b",
        source="unified-advisory",
    )
    assert cp.last_applied_at("calibration-auto-apply") is None


# --- all_last_applied_by_path (2026-08-11, stale-suggestion bug fix) -------

def test_all_last_applied_by_path_empty_when_nothing_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    assert cp.all_last_applied_by_path() == {}


def test_all_last_applied_by_path_one_entry_per_distinct_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cp.log_applied_change(
        config_path="strategy.entry_threshold", old_value=0.5, new_value=0.6,
        rationale="r", trade_count=10, fingerprint_before="a", fingerprint_after="b", source="manual",
    )
    cp.log_applied_change(
        config_path="risk.max_daily_loss_pct", old_value=0.03, new_value=0.04,
        rationale="r", trade_count=3, fingerprint_before="a", fingerprint_after="a", source="manual",
    )
    result = cp.all_last_applied_by_path()
    assert set(result.keys()) == {"strategy.entry_threshold", "risk.max_daily_loss_pct"}


def test_all_last_applied_by_path_returns_the_most_recent_timestamp_per_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")
    cp.log_applied_change(
        config_path="strategy.entry_threshold", old_value=0.5, new_value=0.6,
        rationale="first", trade_count=10, fingerprint_before="a", fingerprint_after="b", source="manual",
    )
    first = cp.all_last_applied_by_path()["strategy.entry_threshold"]
    cp.log_applied_change(
        config_path="strategy.entry_threshold", old_value=0.6, new_value=0.7,
        rationale="second", trade_count=10, fingerprint_before="b", fingerprint_after="c", source="manual",
    )
    second = cp.all_last_applied_by_path()["strategy.entry_threshold"]
    assert second >= first


# --- diff_patch (Item 3D, 2026-08-10) -----------------------------------------
# Mirrors ConfigStore.update()'s own one-level-deep merge exactly, so
# main.py's POST /api/config can log every field a patch actually changes
# without reimplementing that merge logic separately.

def test_diff_patch_reports_a_nested_section_field_change():
    old_cfg = {"strategy": {"entry_threshold": 0.5, "cooldown_sec": 300}}
    patch = {"strategy": {"entry_threshold": 0.6}}
    assert cp.diff_patch(old_cfg, patch) == [("strategy.entry_threshold", 0.5, 0.6)]


def test_diff_patch_reports_a_bare_top_level_scalar_change():
    old_cfg = {"mode": "paper"}
    patch = {"mode": "shadow"}
    assert cp.diff_patch(old_cfg, patch) == [("mode", "paper", "shadow")]


def test_diff_patch_skips_fields_where_the_value_is_unchanged():
    old_cfg = {"strategy": {"entry_threshold": 0.5}}
    patch = {"strategy": {"entry_threshold": 0.5}}
    assert cp.diff_patch(old_cfg, patch) == []


def test_diff_patch_handles_multiple_sections_and_fields_in_one_patch():
    old_cfg = {"strategy": {"entry_threshold": 0.5}, "risk": {"max_daily_loss_pct": 0.03}}
    patch = {"strategy": {"entry_threshold": 0.6}, "risk": {"max_daily_loss_pct": 0.04}}
    changes = cp.diff_patch(old_cfg, patch)
    assert set(changes) == {
        ("strategy.entry_threshold", 0.5, 0.6),
        ("risk.max_daily_loss_pct", 0.03, 0.04),
    }


def test_diff_patch_treats_a_field_missing_from_old_cfg_as_none():
    old_cfg = {"strategy": {}}
    patch = {"strategy": {"take_profit_pct": 0.5}}
    assert cp.diff_patch(old_cfg, patch) == [("strategy.take_profit_pct", None, 0.5)]


# --- history-push trigger point (docs/superpowers/specs/2026-09-03-
# history-event-driven-design.md §2/§4.3 - loadChangeHistory. Fired for
# BOTH manual and auto-apply sources - see log_applied_change's own comment
# for why the design's per-source exemption doesn't apply once the hook
# lives inside the write function itself) --------------------------------


def test_log_applied_change_notifies_history_push_for_manual_source(tmp_path, monkeypatch):
    from services import history_push
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")

    calls = []
    monkeypatch.setattr(history_push, "mark_history_changed", lambda: calls.append(1))
    cp.log_applied_change(
        config_path="strategy.entry_threshold", old_value=0.05, new_value=0.1,
        rationale="test rationale", trade_count=30,
        fingerprint_before="fp1", fingerprint_after="fp2", auto_applied=False,
    )

    assert calls == [1]


def test_log_applied_change_notifies_history_push_for_auto_applied_source(tmp_path, monkeypatch):
    from services import history_push
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "config_performance.db")

    calls = []
    monkeypatch.setattr(history_push, "mark_history_changed", lambda: calls.append(1))
    cp.log_applied_change(
        config_path="strategy.entry_threshold", old_value=0.05, new_value=0.1,
        rationale="test rationale", trade_count=30,
        fingerprint_before="fp1", fingerprint_after="fp2", auto_applied=True, source="unified-advisory",
    )

    assert calls == [1]
