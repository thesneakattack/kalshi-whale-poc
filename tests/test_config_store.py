"""
services/config_store.py - update() must persist settings.yaml atomically.

Real live incident (2026-08-17): a background task's own
cfg["kalshi"]["base_url"] raised KeyError mid-session, immediately after a
dashboard config save ran. update() used to open(path, "w") directly, which
truncates the file to zero bytes before writing a single byte back - any
concurrent reader (this app's own get(), whose mtime-triggered reload
shipped the same day, or a separate process touching the same bind-mounted
file) could observe a torn, partially-written file mid-flight.
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
