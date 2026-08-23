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
read actually parsed to a well-formed dict - a torn read that yaml.safe_load
turns into None or a partial structure got accepted outright as the new
config, corrupting every field until the next real file change gave get()
another chance.
"""
import yaml
import pytest

from services.config_store import ConfigStore


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


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

    monkeypatch.setattr(yaml, "safe_dump", _boom)

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
    # torn read on that specific re-read the way a real yaml.safe_load()
    # would surface one - returning None for a truncated/empty parse.
    store._mtime = None
    monkeypatch.setattr(yaml, "safe_load", lambda f: None)

    assert store.get() == good  # rejected the bad read, kept serving the last good copy


def test_get_rejects_a_non_dict_read(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    good = {"kalshi": {"base_url": "https://example.com"}}
    _write_yaml(path, good)
    store = ConfigStore(path=path)
    store.get()

    store._mtime = None
    monkeypatch.setattr(yaml, "safe_load", lambda f: ["not", "a", "dict"])

    assert store.get() == good


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

    real_safe_load = yaml.safe_load
    calls = {"n": 0}

    def _flaky(f):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_safe_load(f)

    store._mtime = None
    monkeypatch.setattr(yaml, "safe_load", _flaky)

    assert store.get() == good  # first attempt: torn read, rejected
    assert store.get() == good  # second attempt: real retry succeeds
    assert calls["n"] == 2
