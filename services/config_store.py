"""
Loads settings.yaml into memory and lets the API mutate + persist it.
Every other service reads config through this singleton so a change
made from the dashboard takes effect on the next loop tick, with no restart.
"""
import logging
import threading
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

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
        handful of times per tick, not per message.

        Torn-read guard added 2026-08-23: real fault_log evidence
        (services.market_watch.discovery_cache.refresh, KeyError:
        'base_url', ~198 occurrences over ~16 hours - almost exactly one
        per _DISCOVERY_REFRESH_SEC=300s cycle, i.e. every single discovery
        refresh silently failing, not a rare blip) proved update()'s
        atomic tmp-file-then-rename write (also 2026-08-17, same incident)
        doesn't fully close this on its own - a bind-mounted filesystem
        (this project runs under ddev/Docker Desktop on WSL2) doesn't
        necessarily give a concurrent reader in a different process/
        container the same torn-read immunity a same-host POSIX rename
        would. A reader opening the file mid-rename could see a truncated
        or otherwise malformed parse. yaml.safe_load() would then hand back
        None (empty file) or a non-dict, and the old code accepted that
        outright as the new self._data - corrupting every config field, not
        just the one a caller happened to touch, until the next real file
        change gave get() another chance to retry. OSError was already
        guarded (unreadable/mid-write) with "keep serving the last good
        copy"; this closes the other half - a read that *succeeds* but
        parses to something too malformed to trust. Deliberately does NOT
        update self._mtime on a bad read, so the very next get() retries
        rather than getting stuck silently serving stale-but-good data
        forever."""
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
                if mtime != self._mtime:
                    with open(self._path, "r") as f:
                        new_data = yaml.safe_load(f)
                    if isinstance(new_data, dict) and new_data:
                        self._data = new_data
                        self._mtime = mtime
                    else:
                        # torn/empty/malformed read - keep serving the last
                        # good copy, and deliberately leave self._mtime
                        # unset so the next call retries instead of
                        # accepting this as "no change since last time."
                        logger.warning(
                            "settings.yaml read as %s at mtime %s - rejecting, still serving last known-good config",
                            type(new_data).__name__, mtime,
                        )
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
