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
        self._mtime = None
        self.reload()

    def reload(self):
        with self._lock:
            with open(self._path, "r") as f:
                self._data = yaml.safe_load(f)
            try:
                self._mtime = self._path.stat().st_mtime
            except OSError:
                self._mtime = None
        return self._data

    def get(self) -> dict:
        """Current config, re-reading settings.yaml if the file changed on
        disk since the last read.

        Added 2026-08-17 after this cost most of a session. The class
        docstring promised a change "takes effect on the next loop tick,
        with no restart", but that was only ever true for edits made
        through update() - a direct edit to settings.yaml was never picked
        up, because reload() ran once at construction and nothing called it
        again. `uvicorn --reload` watches .py files, not .yaml, so nothing
        restarted the process either.

        The result was a running app silently using stale config while every
        measurement taken from a separate `ddev exec` process re-read the
        file and reported the NEW values. Confirmed live: the app held
        min_notional_usd 5000 / KXBTC15M 2500 while the file on disk said
        2000 / 500, and every explanation built on those measurements was
        describing a configuration the app was not running.

        An mtime check is one stat() call per get(); get() is called a
        handful of times per tick, not per message."""
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
                if mtime != self._mtime:
                    with open(self._path, "r") as f:
                        self._data = yaml.safe_load(f)
                    self._mtime = mtime
            except OSError:
                # Unreadable or mid-write - keep serving the last good copy
                # rather than failing a caller that just wants config.
                pass
            return dict(self._data)

    def update(self, patch: dict):
        """Shallow-merge a patch into the top-level config and persist it."""
        with self._lock:
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(self._data.get(key), dict):
                    self._data[key].update(value)
                else:
                    self._data[key] = value
            # Atomic write (2026-08-17, real live incident: a background
            # task's cfg["kalshi"]["base_url"] raised KeyError mid-session,
            # right after this exact write path ran). open(path, "w")
            # truncates the file to zero bytes before writing a single byte
            # back, so any concurrent reader - this same app's own get()
            # mtime-triggered reload, or a separate process (e.g. a `ddev
            # exec` script) reading the same bind-mounted file - can observe
            # a torn, partially-written file mid-flight. Writing to a temp
            # file in the same directory and replacing it over the real path
            # is a single atomic filesystem rename: any reader sees either
            # the complete old file or the complete new one, never a partial
            # write in between.
            tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp_path, "w") as f:
                yaml.safe_dump(self._data, f, sort_keys=False)
            tmp_path.replace(self._path)
            # Own write - record the new mtime so get()'s change detection
            # doesn't immediately re-read the file we just produced.
            try:
                self._mtime = self._path.stat().st_mtime
            except OSError:
                self._mtime = None
            return dict(self._data)


config_store = ConfigStore()
