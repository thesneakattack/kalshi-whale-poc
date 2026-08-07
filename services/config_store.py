"""
Loads settings.yaml into memory and lets the API mutate + persist it.
Every other service reads config through this singleton so a change
made from the dashboard takes effect on the next loop tick, with no restart.
"""
import threading
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._data = {}
        self.reload()

    def reload(self):
        with self._lock:
            with open(self._path, "r") as f:
                self._data = yaml.safe_load(f)
        return self._data

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)

    def update(self, patch: dict):
        """Shallow-merge a patch into the top-level config and persist it."""
        with self._lock:
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(self._data.get(key), dict):
                    self._data[key].update(value)
                else:
                    self._data[key] = value
            with open(self._path, "w") as f:
                yaml.safe_dump(self._data, f, sort_keys=False)
            return dict(self._data)


config_store = ConfigStore()
