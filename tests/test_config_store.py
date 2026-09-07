"""
services/config_store.py - update() must persist settings.yaml atomically,
and get() must never accept a torn/malformed read as the new config.

Real live incident (2026-08-17): a background task's own
cfg["kalshi"]["base_url"] raised KeyError mid-session, immediately after a
dashboard config save ran. update() used to open(path, "w") directly, which
truncates the file to zero bytes before writing a single byte back - any
concurrent reader (this app's own get(), whose mtime-triggered reload
shipped the same day, or a separate process touching the same bind-mounted
file) could observe a torn, partially-written file mid-flight.

Second real live incident (2026-08-23): fault_log showed the exact same
KeyError still recurring ~198 times over ~16 hours - almost exactly once
per discovery_cache._DISCOVERY_REFRESH_SEC (300s) cycle, i.e. every single
attempt failing, not a rare blip. The 2026-08-17 write-side fix alone
wasn't enough: this project runs under ddev/Docker Desktop on WSL2, and a
bind-mounted filesystem doesn't necessarily give a reader in a different
process/container the same torn-read immunity a same-host POSIX rename
would. get()'s mtime-triggered re-read had no validation that a "successful"
read actually parsed to a well-formed dict - a torn read that the parser
turns into None or a partial structure got accepted outright as the new
config, corrupting every field until the next real file change gave get()
another chance.

Third real live incident (2026-08-23, same day): verifying an unrelated
feature live, POST /api/config's update() round-tripped the whole file
through PyYAML's safe_load/safe_dump pair, which cannot preserve comments -
every hand-written comment in settings.yaml disappeared after ONE toggle,
not just near the touched field. Confirmed via git history that this had
been happening on every dashboard save and every advisory/confidence-
calibration auto-apply all along. Switched to ruamel.yaml's round-trip
mode, which attaches comment metadata to the loaded structure and
preserves it through mutation and re-dump - see config_store.py's own
module docstring for the verification this switch was checked against
before landing (CommentedMap's dict-ness, formatting fidelity against the
real file, and identical exception behavior on truncated reads).
"""
import yaml
import pytest

from services.config import config_store as config_store_module
from services.config.config_store import ConfigStore


def _write_yaml(path, data):
    # Seeding test fixtures only - plain PyYAML is fine here since this
    # just produces valid YAML content for ConfigStore's own ruamel.yaml
    # engine to load; the two are format-compatible (verified directly,
    # see config_store.py's module docstring).
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def _write_text(path, text):
    with open(path, "w") as f:
        f.write(text)


def test_update_persists_the_merged_config_correctly(tmp_path):
    path = tmp_path / "settings.yaml"
    _write_yaml(path, {"kalshi": {"base_url": "https://example.com"}, "mode": "paper"})
    store = ConfigStore(path=path)

    store.update({"mode": "live"})

    with open(path) as f:
        on_disk = yaml.safe_load(f)
    assert on_disk == {"kalshi": {"base_url": "https://example.com"}, "mode": "live"}


def test_update_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "settings.yaml"
    _write_yaml(path, {"kalshi": {"base_url": "https://example.com"}})
    store = ConfigStore(path=path)

    store.update({"kalshi": {"base_url": "https://other.example.com"}})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.yaml"]


def test_update_failure_mid_write_does_not_corrupt_the_real_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    original = {"kalshi": {"base_url": "https://example.com"}, "mode": "paper"}
    _write_yaml(path, original)
    store = ConfigStore(path=path)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(config_store_module._yaml, "dump", _boom)

    with pytest.raises(RuntimeError):
        store.update({"mode": "live"})

    with open(path) as f:
        assert yaml.safe_load(f) == original


def test_get_rejects_a_torn_read_and_keeps_serving_last_good_config(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    good = {"kalshi": {"base_url": "https://example.com"}, "mode": "paper"}
    _write_yaml(path, good)
    store = ConfigStore(path=path)
    assert store.get() == good

    # Force the next get() to treat the file as changed, then simulate a
    # torn read on that specific re-read the way a real parse of a
    # truncated/empty file would surface one - returning None.
    store._mtime = None
    monkeypatch.setattr(config_store_module._yaml, "load", lambda f: None)

    assert store.get() == good  # rejected the bad read, kept serving the last good copy


def test_get_rejects_a_non_dict_read(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    good = {"kalshi": {"base_url": "https://example.com"}}
    _write_yaml(path, good)
    store = ConfigStore(path=path)
    store.get()

    store._mtime = None
    monkeypatch.setattr(config_store_module._yaml, "load", lambda f: ["not", "a", "dict"])

    assert store.get() == good


def test_get_rejects_a_read_the_parser_raises_on(tmp_path):
    """Real gap found 2026-08-23 switching to ruamel.yaml: a torn read
    isn't guaranteed to come back as None or the wrong type - depending on
    where the cut lands, the parser can raise instead. Checked directly
    against this project's real settings.yaml (179 sampled truncation
    points) that PyYAML and ruamel.yaml raise on identically the same
    ones - this was a latent, previously-uncaught gap in the pre-ruamel
    code too, not something the library switch introduced."""
    path = tmp_path / "settings.yaml"
    good = {"kalshi": {"base_url": "https://example.com"}}
    _write_yaml(path, good)
    store = ConfigStore(path=path)
    store.get()

    # A genuinely malformed rewrite - unterminated flow mapping - that
    # raises a YAMLError on parse rather than returning a wrong-typed value.
    # Forcing _mtime to None (rather than relying on the filesystem's mtime
    # resolution to have ticked over) is what the other tests in this file
    # already do for the same reason.
    _write_text(path, "kalshi: {base_url: [unterminated\n")
    store._mtime = None

    assert store.get() == good  # rejected the raise, kept serving the last good copy


def test_get_retries_after_rejecting_a_torn_read(tmp_path, monkeypatch):
    # The real incident this closes: get() must not get permanently stuck
    # serving stale-but-good data just because one read attempt failed -
    # the very next call has to retry, not silently give up until some
    # unrelated future file change comes along.
    path = tmp_path / "settings.yaml"
    good = {"kalshi": {"base_url": "https://example.com"}}
    _write_yaml(path, good)
    store = ConfigStore(path=path)
    store.get()

    real_load = config_store_module._yaml.load
    calls = {"n": 0}

    def _flaky(f):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_load(f)

    store._mtime = None
    monkeypatch.setattr(config_store_module._yaml, "load", _flaky)

    assert store.get() == good  # first attempt: torn read, rejected
    assert store.get() == good  # second attempt: real retry succeeds
    assert calls["n"] == 2


# ---- comment preservation (2026-08-23) - the actual bug this session found -

def test_update_preserves_existing_comments(tmp_path):
    """The real, previously-undiagnosed bug: PyYAML's safe_load/safe_dump
    pair cannot round-trip comments, so ONE update() call used to strip
    every hand-written comment out of the whole file, not just near the
    touched field. This is the regression test for the ruamel.yaml switch."""
    path = tmp_path / "settings.yaml"
    _write_text(path, (
        "mode: paper\n"
        "kalshi:\n"
        "  # explains the base_url choice\n"
        "  base_url: https://example.com\n"
        "settlement_edge_entry:\n"
        "  enabled: false  # inline note about this field\n"
    ))
    store = ConfigStore(path=path)

    store.update({"settlement_edge_entry": {"enabled": True}})

    on_disk = path.read_text()
    assert "# explains the base_url choice" in on_disk
    assert "# inline note about this field" in on_disk
    # The actual patched value really did change, not just the comments
    # surviving alongside a no-op write.
    assert store.get()["settlement_edge_entry"]["enabled"] is True
    with open(path) as f:
        assert yaml.safe_load(f)["settlement_edge_entry"]["enabled"] is True


def test_update_preserves_comments_on_an_untouched_section(tmp_path):
    """The specific shape of the bug (2026-08-23 live incident): editing
    ONE field used to strip comments from EVERY section, not just the one
    touched. A patch to settlement_edge_entry must leave kalshi's own
    comment completely untouched."""
    path = tmp_path / "settings.yaml"
    _write_text(path, (
        "kalshi:\n"
        "  # a completely unrelated section\n"
        "  base_url: https://example.com\n"
        "settlement_edge_entry:\n"
        "  enabled: false\n"
    ))
    store = ConfigStore(path=path)

    store.update({"settlement_edge_entry": {"enabled": True}})

    assert "# a completely unrelated section" in path.read_text()


def test_update_preserves_a_comment_trailing_a_nested_dict_field(tmp_path):
    """The real incident (docs/open-decisions.md, 3 documented comment
    wipes; §4.4 of docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-second-pass.md): a comment sitting immediately after a NESTED dict
    field's last entry (not a top-level scalar - the two existing comment
    tests above don't cover this shape) was destroyed because update()'s
    one-level dict.update() replaces that nested dict's VALUE wholesale
    with a brand-new plain dict object, even though the PARENT map object
    is never replaced. Reproduces the real config-panel.js patch shape for
    whale_watcher_kalshi.min_contracts_by_series exactly."""
    path = tmp_path / "settings.yaml"
    _write_text(path, (
        "whale_watcher_kalshi:\n"
        "  enabled: true\n"
        "  min_contracts: 5000\n"
        "  min_contracts_by_series:\n"
        "    KXBTC15M: 100\n"
        "    KXETH15M: 90\n"
        "# calibration-audit comment block\n"
        "# second line\n"
        "whale_confidence_weights:\n"
        "  depth_factor: 0.04\n"
    ))
    store = ConfigStore(path=path)

    store.update({
        "whale_watcher_kalshi": {
            "min_contracts": 5000.0,
            "min_contracts_by_series": {"KXBTC15M": 100.0, "KXETH15M": 90.0},
        },
    })

    on_disk = path.read_text()
    assert "# calibration-audit comment block" in on_disk
    # The values really did round-trip through the patch, not a no-op.
    assert store.get()["whale_watcher_kalshi"]["min_contracts_by_series"]["KXBTC15M"] == 100.0
    # enabled (untouched by this patch, and not part of the
    # min_contracts_by_series allowlist) must survive unchanged - the
    # regression this task's own second experiment found in a naive
    # "delete every stale key at every level" fix.
    assert store.get()["whale_watcher_kalshi"]["enabled"] is True


def test_update_replaces_min_contracts_by_series_keys_entirely_not_merges_them(tmp_path):
    """whale_watcher_kalshi.min_contracts_by_series is the one field
    config-panel.js always resends as a complete rebuilt map from a text
    input (Object.fromEntries over the whole comma-separated field) - a
    series removed from that field must actually disappear from the saved
    config, not linger as an orphaned stale key (which a naive "merge,
    never delete" fix at every level would produce)."""
    path = tmp_path / "settings.yaml"
    _write_text(path, (
        "whale_watcher_kalshi:\n"
        "  min_contracts_by_series:\n"
        "    KXBTC15M: 100\n"
        "    KXETH15M: 90\n"
    ))
    store = ConfigStore(path=path)

    store.update({
        "whale_watcher_kalshi": {
            "min_contracts_by_series": {"KXBTC15M": 200.0, "KXSOL15M": 50.0},
        },
    })

    result = store.get()["whale_watcher_kalshi"]["min_contracts_by_series"]
    assert result == {"KXBTC15M": 200.0, "KXSOL15M": 50.0}
    assert "KXETH15M" not in result
