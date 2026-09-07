"""
Loads settings.yaml into memory and lets the API mutate + persist it.
Every other service reads config through this singleton so a change
made from the dashboard takes effect on the next loop tick, with no restart.

ruamel.yaml, not PyYAML (2026-08-23 fix - real live incident): every write
through update() used to round-trip the whole file through
yaml.safe_dump(), and PyYAML's safe_load/safe_dump pair cannot preserve
comments - a load-mutate-dump cycle silently deleted every hand-written
comment in settings.yaml, not just near the touched field. Confirmed via
git history (`git log -p -- config/settings.yaml`) that this has been
happening on every dashboard Controls-panel save and every advisory/
confidence-calibration auto-apply since those features shipped, not
something new - see ROADMAP.md. ruamel.yaml's round-trip mode (`YAML()`,
the default typ) attaches comment metadata to the loaded structure itself
and preserves it through mutation and re-dump. Verified directly against
this project's real settings.yaml before switching, not assumed safe:
CommentedMap is a genuine dict subclass (isinstance/equality/JSON
serialization all behave like a plain dict), the configured indent below
reproduces this file's existing formatting byte-for-byte except one
harmless null-vs-blank cosmetic difference on a single pre-existing line,
and exception-raising behavior for a truncated/malformed read was checked
identical to PyYAML's across 179 sampled truncation points of the real
file - the torn-read guard in get() below needed no logic change.
"""
import logging
import threading
from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"

_yaml = YAML()
_yaml.preserve_quotes = True
# Matches PyYAML safe_dump's default block-sequence style (this file's
# existing lists), where a "- item" dash sits at the SAME column as its
# parent key, not indented a further level under it.
_yaml.indent(mapping=2, sequence=2, offset=0)


def _represent_none(representer, _):
    # ruamel's default for Python None is a blank value (`key:` with
    # nothing after it) - valid YAML, but a needless diff against this
    # file's existing explicit `key: null` fields (take_profit_pct et al.)
    # on every future write. Cosmetic only; either form parses back to
    # None identically.
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


_yaml.representer.add_representer(type(None), _represent_none)


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
                self._data = _yaml.load(f)
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
        or otherwise malformed parse. Parsing it would then hand back
        None (empty file) or a non-dict, and the old code accepted that
        outright as the new self._data - corrupting every config field, not
        just the one a caller happened to touch, until the next real file
        change gave get() another chance to retry. OSError was already
        guarded (unreadable/mid-write) with "keep serving the last good
        copy"; this closes the other half - a read that *succeeds* but
        parses to something too malformed to trust. Deliberately does NOT
        update self._mtime on a bad read, so the very next get() retries
        rather than getting stuck silently serving stale-but-good data
        forever.

        YAMLError also guarded (2026-08-23, found verifying the ruamel.yaml
        switch above): a torn read isn't guaranteed to parse into "None or
        the wrong type" - depending on exactly where the cut lands, the
        parser can raise instead. Checked directly, not assumed: sampling
        179 truncation points across this project's real settings.yaml,
        both PyYAML and ruamel.yaml raised on identically the same 70 of
        them. Every one of those was previously an uncaught exception
        straight out of get() - this had been a latent gap in the
        pre-ruamel code too, just never triggered by the specific torn
        reads fault_log had actually captured."""
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
                if mtime != self._mtime:
                    with open(self._path, "r") as f:
                        new_data = _yaml.load(f)
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
            except (OSError, YAMLError):
                # Unreadable, mid-write, or a torn read the parser rejected
                # outright - keep serving the last good copy rather than
                # failing a caller that just wants config.
                pass
            return dict(self._data)

    # Full-replace-with-deletion paths (2026-09-03, Task 5 of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md, moved there 2026-09-06, planning-lanes migration): the general
    # merge below never deletes a key the incoming patch doesn't mention
    # (same safety property update() has always had, just extended past
    # one level - see this task's own experiment log in the plan for why a
    # uniform "delete stale keys everywhere" policy is unsafe). Exactly one
    # field needs the opposite: whale_watcher_kalshi.min_contracts_by_series
    # is always resent as a complete, freshly-rebuilt map by config-
    # panel.js's own Object.fromEntries(...) over a single text input - a
    # series a user removes from that field must actually disappear, not
    # linger as an orphaned stale key. Dotted-path strings, checked at each
    # recursion level by the (current key path) tuple joined with ".".
    _FULL_REPLACE_PATHS = {"whale_watcher_kalshi.min_contracts_by_series"}

    def _merge_in_place(self, dst: dict, patch: dict, _path: str = "") -> None:
        for key, value in patch.items():
            key_path = f"{_path}.{key}" if _path else key
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                existing = dst[key]
                if key_path in self._FULL_REPLACE_PATHS:
                    # Delete-then-merge-in-place: the incoming dict becomes
                    # the complete truth for this one key, but the
                    # EXISTING ruamel map object is mutated (del/setitem),
                    # never replaced wholesale - replacing the object
                    # reference is what destroys an attached comment;
                    # mutating it in place does not (verified experimentally,
                    # see this task's own header).
                    for stale_key in [k for k in list(existing.keys()) if k not in value]:
                        del existing[stale_key]
                self._merge_in_place(existing, value, key_path)
            else:
                dst[key] = value

    def update(self, patch: dict):
        """Recursive merge (2026-09-03, Task 5): every nested dict field is
        merged key-by-key into the EXISTING object rather than replaced
        wholesale, which is what let ruamel's attached comments survive a
        real, 3-times-repeated live incident (docs/open-decisions.md) -
        replacing a child map's object reference is what discards a
        comment ruamel attached to it, even when the PARENT object is never
        touched. Never deletes a key the patch doesn't mention, at any
        level, except the one explicit path in _FULL_REPLACE_PATHS above.
        Does not itself restore config/settings.yaml's already-wiped
        comment - see docs/open-decisions.md for that (human) decision."""
        with self._lock:
            self._merge_in_place(self._data, patch)
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
                _yaml.dump(self._data, f)
            tmp_path.replace(self._path)
            # Own write - record the new mtime so get()'s change detection
            # doesn't immediately re-read the file we just produced.
            try:
                self._mtime = self._path.stat().st_mtime
            except OSError:
                self._mtime = None
            return dict(self._data)


config_store = ConfigStore()
