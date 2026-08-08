from services import config_performance as cp


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
    assert cp.applied_changes_count() == 1


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
