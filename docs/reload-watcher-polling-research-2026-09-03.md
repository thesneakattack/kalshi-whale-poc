# Reload-watcher polling research (issue #513)

Research stage only. Follows up on issue #513's report that the uvicorn
`--reload` parent process burns 43.5% of a core polling ~47k files, 82% of
them under `.claude/worktrees`. Assignment (autotrade-1d, 2026-09-03): (1)
confirm/refute the polling premise independently and establish *why*, (2)
determine what `--reload-dirs` would actually do — restrict the walk or only
filter after it — since this is the one thing gating whether a `ddev
restart` is worth spending, (3) quantify the remaining cost after the
worktree count dropped 34→10, (4) name the alternatives honestly. A later
message from the PM narrowed active scope to (2) alone, since (1) and (3)
were independently answered by measurement before this doc was finished —
this doc still carries all four, crediting each finding to whoever actually
produced it.

## 1. Polling premise: confirmed, independently, with the correct PID

Issue #513's own evidence used PID `981356` — a **host**-namespace PID from
`docker top`, which does not exist inside the container's PID namespace, so
the fdinfo check against it silently returned zero for the wrong reason (no
such process, not "process has no watches"). The PM caught and corrected
this before I started; I re-derived it independently rather than trusting
either version, per this repo's own "never guess, verify or falsify" rule.

Located the real process from inside the container:

```
PID=1 CMD=/usr/local/bin/python3.13 /usr/local/bin/uvicorn main:app --host
  0.0.0.0 --port 8000 --reload --reload-exclude /app/.claude/worktrees
  --proxy-headers --forwarded-allow-ips=*
PID=25541 PPID=1 CMD=... spawn_main(...) --multiprocessing-fork   (worker)
```

Container PID 1 *is* the uvicorn reload supervisor — confirmed by its own
cmdline, not assumed from position. Checked directly:

```
/proc/1/fd: 7 total open fds, 0 matching anon_inode:inotify
/proc/1/fdinfo/<n> for each: no `inotify wd:` lines
```

**Zero inotify watches, confirmed on the correct PID.** Not a resource
limit either — `/proc/sys/fs/inotify/max_user_watches` = 524288,
`max_user_instances` = 128, nowhere close to exhausted.

### Why — the issue's own stated mechanism doesn't hold up

Issue #513 states the mechanism as "Docker bind mounts on WSL2 do not
propagate inotify." That is a plausible-sounding but **unverified** claim,
and it's wrong as the actual mechanism here — I falsified it directly.

Traced the real cause to primary source. `watchfiles/main.py` computes
`force_polling` per call (condensed below for brevity — the multi-line
`if`/`else` blocks are collapsed onto one line each, logic unchanged; see
the cited line numbers for the literal source):

```
watchfiles/main.py:325  def _default_force_polling(force_polling):
  ...
  if force_polling is not None: return force_polling
  env_var = os.getenv('WATCHFILES_FORCE_POLLING')
  if env_var: return env_var.lower() not in {'false','disable','disabled'}
  else: return _auto_force_polling()

watchfiles/main.py:358  def _auto_force_polling() -> bool:
    """Whether to auto-enable force polling, it should be enabled
    automatically only on WSL. See samuelcolvin/watchfiles#187."""
    import platform
    uname = platform.uname()
    return 'microsoft-standard' in uname.release.lower() and \
           uname.system.lower() == 'linux'
```

Checked both preconditions directly against the running container:

- No `WATCHFILES_FORCE_POLLING` anywhere in `/proc/1/environ` (the
  authoritative env for PID 1 itself, not just a fresh `docker exec`
  session's env) — the override path isn't in play.
- `uname -r` inside the container: `6.18.33.2-microsoft-standard-WSL2` —
  matches `'microsoft-standard' in release.lower()` exactly.

So `_auto_force_polling()` returns `True` **unconditionally on any WSL2
kernel**, regardless of whether the specific mount actually supports
inotify. It's a blanket policy watchfiles ships deliberately (their own
issue #187), not a symptom of this bind mount specifically failing to
propagate events.

**That distinction is falsifiable, so I tested it.** The container's `/app`
mount is a real `ext4` device (`mountinfo`: `/dev/sdd ext4 rw`, not a 9p or
virtiofs cross-VM passthrough), and `docker info` shows the `default`
context — a dockerd running natively inside this same WSL2 distro, not
Docker Desktop's separate VM. Wrote a throwaway script that calls
`watchfiles.watch(path, force_polling=False, ...)` directly against a temp
dir under `/app`, touches a file, and waits for an event:

```
force_polling=False, mount=/app/tmp_gyhy7jp
EVENT RECEIVED after 0.058s (inotify appears to work)
```

**Inotify works fine on this exact mount, in 58ms, when not blanket-
disabled.** The issue's stated mechanism ("bind mounts on WSL2 don't
propagate inotify") is refuted for this specific setup — this is a native
dockerd on a native ext4-backed WSL2 filesystem, not the Docker-Desktop-
on-WSL2 case the upstream watchfiles blanket rule most plausibly exists
for. The premise (polling is happening) holds; the *why* the issue gave
does not — watchfiles isn't failing to get inotify to work here, it's
refusing to even try, because of a kernel-string check that doesn't
distinguish this environment from the one it's actually guarding against.

This matters directly for the alternatives section below: it opens a
second lever (§4C) beyond scoping what gets watched.

## 2. `--reload-dirs`: does it restrict the walk, or only filter after it?

**This is the load-bearing question the PM asked me to answer from source,
with file:line.** Answer: **`--reload-dirs` restricts the walk itself — it
is structurally different from `--reload-exclude`, which only filters
after the walk.** But there's a real, separate gotcha that blocks a naive
fix (below).

### How `--reload-exclude` actually behaves today (confirmed, not assumed)

`uvicorn/supervisors/watchfilesreload.py:55-79`:

```python
class WatchFilesReload(BaseReload):
    def __init__(self, config, target, sockets):
        ...
        self.reload_dirs = []
        for directory in config.reload_dirs:
            if Path.cwd() not in directory.parents:
                self.reload_dirs.append(directory)
        if Path.cwd() not in self.reload_dirs:
            self.reload_dirs.append(Path.cwd())

        self.watch_filter = FileFilter(config)
        self.watcher = watch(
            *self.reload_dirs,
            watch_filter=None,          # <- no filter reaches the walker
            stop_event=self.should_exit,
            yield_on_timeout=True,
        )

    def should_restart(self) -> list[Path] | None:   # :81-88
        self.pause()
        changes = next(self.watcher)          # <- full unfiltered batch
        if changes:
            unique_paths = {Path(c[1]) for c in changes}
            return [p for p in unique_paths if self.watch_filter(p)]  # filter HERE
        return None
```

`watch_filter=None` is passed to the low-level `watchfiles.watch()` call —
the `FileFilter` built from `--reload-include`/`--reload-exclude`
(`watchfilesreload.py:13-52`) is applied *manually*, in Python, **after**
`next(self.watcher)` already returns a full batch of changes from the
underlying watcher (polling, per §1). This is exactly what the
docker-compose.fastapi.yaml comment already found on 2026-08-27 for the
absolute-vs-relative-path bug, confirmed again here for the deeper question:
`--reload-exclude` cannot reduce what the poller stats each cycle — it can
only discard results after the full-tree poll already paid for them.

There's also a *second*, separate exclude mechanism in
`uvicorn/config.py:294-302` that looks like it should help but doesn't for
this case:

```python
reload_dirs_tmp = self.reload_dirs.copy()
for directory in self.reload_dirs_excludes:        # from --reload-exclude
    for reload_directory in reload_dirs_tmp:        # from --reload-dirs (or cwd)
        if directory == reload_directory or directory in reload_directory.parents:
            self.reload_dirs.remove(reload_directory)
```

This removes a *watched root* entirely, but only when the exclude directory
is an **exact match or an ancestor** of a watched root. `/app/.claude/
worktrees` is a *descendant* of `/app` (today's sole watched root, since no
`--reload-dirs` flag is passed at all — `config.py:308-315` falls back to
`[Path(os.getcwd())]` = `[/app]`), not an ancestor — so this condition is
false in both directions and nothing is pruned. Confirmed directly against
the resolved values, not inferred from reading the condition alone.

**Net: today's `--reload-exclude /app/.claude/worktrees` is real (it
correctly stops worktree edits from triggering a reload — issue #513's own
confirmation, "a branch switch in the primary at 11:56 UTC reloaded only on
services/tests paths," holds) but it does nothing to reduce the per-cycle
stat cost. All 17,884 files currently under `/app` get walked every cycle
regardless.**

### What `--reload-dirs` would actually do (the paths-vs-filter question)

`--reload-dirs` values flow through `uvicorn/config.py:290`
(`resolve_reload_patterns(reload_includes, reload_dirs)`) into
`self.reload_dirs`, which then becomes **the literal `*paths` argument**
passed into `watchfiles.watch()` at `watchfilesreload.py:72-73`. There is
no filter step between "what `--reload-dirs` names" and "what the walker
recurses into" — the named directories *are* the walk roots. This is
structurally different from `--reload-exclude`, confirmed above to be a
post-hoc, per-change filter that never touches the walk. **By the PM's own
framing: yes, it's paths, not a filter — the mechanism is real.**

### The gotcha that blocks a naive fix: `main.py` is not inside any subdirectory

`resolve_reload_patterns` (`config.py:131-164`) only keeps entries that
survive `is_dir()`:

```python
config.py:150-152
directories = list(map(Path, directories))
directories = list(map(lambda x: x.resolve(), directories))
directories = list({reload_path for reload_path in directories if is_dir(reload_path)})
```

This `is_dir()` filter applies to **every** candidate, including values
passed directly via `--reload-dirs` — a bare file never survives it. The
app's entrypoint, `main.py` (109 KB, the FastAPI app + trading loop, per
this repo's own "Quick file map" — among the most actively edited files in
the repo), sits directly at `/app/main.py`, not inside `services/`,
`tools/`, `tests/`, or any other subdirectory. If `--reload-dirs` were
scoped to just the app-code subdirectories (`services`, `tools`, `tests`,
`config`, `static`, `frontend` — deliberately omitting `.claude` to drop
`.claude/worktrees` from the walk), `main.py` itself would silently **stop
being watched at all** — edits to the app's own entrypoint would no longer
trigger a reload, a real correctness regression on the dev loop, not a cost
tradeoff.

There is no clean way around this within `--reload-dirs`/`--reload-include`
alone: a bare filename passed as `--reload-include main.py` only adds to
the *pattern* list used for post-hoc trigger-matching (`watchfilesreload.py:
16-18`), it does not add `/app` (main.py's parent) back to the walked roots
— confirmed by the same `is_dir()` gate in `resolve_reload_patterns`,
which only promotes a glob match into `directories` when the match is
itself a directory (`config.py:145-147`), never a bare file. Watching
`/app` itself to keep `main.py` covered reintroduces `.claude/worktrees`
into the walk, defeating the purpose.

**So `--reload-dirs` is a real mechanism (restricts the walk, not just the
result), but a naive "list the app subdirectories" implementation of it is
unsafe here** — it would trade the CPU cost for a silent dev-loop
correctness bug on the app's own entrypoint. A safe version would need
either (a) moving `main.py`'s logic into a subdirectory (a real code-layout
change, out of scope for a config-only fix), or (b) relocating
`.claude/worktrees` outside `/app` entirely (§4D — its own real tradeoff),
neither of which is a same-session config flip.

## 3. Remaining cost after the worktree drop (measured by autotrade-1d/84; corroborated here)

Authoritative measurement is autotrade-1d/84's, using `utime+stime` deltas
from `/proc/<pid>/stat` over a fixed clean window — the correct method,
called out explicitly because `docker top`'s `C` column and `ps`'s `%CPU`
are cumulative lifetime averages that don't show a recent change (their
first after-cleanup reading looked flat for exactly this reason before the
method was corrected):

- Watcher: **42.9%** (lifetime avg, 5h13m) → **20.8%** (clean 64s window)
  after worktrees dropped 34→10 and the tree went 47,401 → 17,877 files
  (−62%).
- Worker (control, unaffected by the watcher): unchanged at 107.8%.
- Of the remaining 17,877 files, 9,594 (54%) are still worktrees — most of
  which can't be removed further since they're live sessions' workspaces.

I corroborated the file counts (not the CPU deltas, which I did not
re-derive) with an independent, lightweight recount minutes later:

```
worktrees: 9 (git worktree list, excluding primary)
total files under /app:                    17,884
files under /app/.claude/worktrees:         9,601
.py files under /app/.claude/worktrees:     3,500
files under app-code dirs alone
  (services+tools+tests+config+static+frontend+main.py):  2,115
```

Close enough to 1d/84's numbers (within normal drift from files changing
between the two measurement windows) to trust both. **~52% of the watcher's
original cost came from worktree count; the other ~48% (≈20.8% of a core,
sustained) is structural — driven by continuing to poll the full `/app`
tree, worktrees included, every cycle** — this is the part neither cleanup
nor further worktree hygiene touches, since 2 of the remaining 9 worktrees
are live sessions' active workspaces that can't be removed regardless.

## 4. Alternatives, named honestly

**A. Do nothing.** Cleanup already recovered roughly half the watcher's
CPU (42.9%→20.8%) at zero risk and zero interruption. Remaining cost is a
real, sustained 20.8% of a core — not negligible on a shared container that
also runs the trading loop, WebSocket readers, and every other request —
but it's a known, bounded cost, not a growing one, as long as worktree
count stays roughly where it is.

**B. `--reload-dirs` scoped to app-code subdirectories.** Confirmed real
(§2) — it would eliminate the ~9,600 worktree files from the walk
entirely, not just filter them post-hoc. **Not recommended as a standalone
fix**: the `main.py`-at-top-level gotcha (§2) means a naive implementation
silently stops watching the app's own entrypoint. Viable only combined with
either moving `main.py` into a subdirectory (real code-layout change) or
§4D below (moving worktrees out of `/app` instead, which achieves the same
walk-scoping goal without touching `main.py`'s location at all).

**C. `WATCHFILES_FORCE_POLLING=false`.** Newly surfaced in this research
(§1), not part of the original issue. Overrides watchfiles' blanket
WSL2-kernel heuristic and switches the underlying mechanism from continuous
per-cycle full-tree `stat()` polling to event-driven inotify — confirmed
empirically functional and fast (58ms) on this exact bind mount. Zero
change to *what* is watched (still `/app` as a whole, `main.py` stays
covered, no code-layout change), so it carries none of §2's correctness
risk. The FileFilter's post-hoc exclude logic (`watchfilesreload.py:81-88`)
applies identically regardless of which backend produced the change batch,
so `--reload-exclude /app/.claude/worktrees`'s existing behavior (worktree
edits don't trigger a reload) is preserved either way — confirmed from the
same source read in §2, not assumed. **Not yet cost-measured at real tree
scale**: this doc confirms functional correctness and per-event latency in
a synthetic single-file test, not the steady-state cost of registering and
maintaining inotify watches recursively across ~18,000 directories under
real worktree churn (a new worktree appearing mid-session, or a peer
session's git operations generating bursts of file events) — that
quantification is fix/plan-stage work, not established here.

**D. Move worktrees outside the bind mount.** Would remove the cost at its
root for both B and C's purposes, but breaks the reason worktrees live
inside `/app` in the first place: `ddev exec` (and this repo's documented
workaround for running it from a worktree, `docker exec -w /app <container>
<cmd>` — see CLAUDE.md's dev-workflow section) depends on worktrees being
reachable inside the container's `/app` bind mount. Real, already-
acknowledged tradeoff (issue #513 itself names it as "would need its own
decision"); not evaluated further here since it's a bigger, separate change
than what this research was scoped to weigh.

## Recommendation

**Test `WATCHFILES_FORCE_POLLING=false` (§4C) first, not `--reload-dirs`
(§4B), despite `--reload-dirs` being the one the PM asked me to settle.**
By the PM's own stated decision rule — "if it's paths, the fix is real and
worth a restart" — `--reload-dirs` clears that bar (§2 confirms it
restricts the walk, not just the result). But it's not the fix to spend the
interruption on *first*: it carries a real, structural correctness risk
(silently losing `main.py`'s reload coverage) that has no clean same-session
fix, while `WATCHFILES_FORCE_POLLING=false` targets the same root cause
(continuous full-tree polling) with no code-layout change, no risk to what
gets watched, and a working empirical proof-of-concept on this exact
filesystem.

Both are one-line changes to `.ddev/docker-compose.fastapi.yaml` and both
require a real `ddev restart` (the same interruption cost either way), so
there's no reason to spend it on the riskier option first. Concretely: add
`environment: - WATCHFILES_FORCE_POLLING=false` to the `fastapi` service (or
pass it via the existing `command:` is not an option — it's an env var, not
a CLI flag — so it needs an `environment:` block), restart, then verify
end-to-end before declaring it done: (a) an edit under `services/` still
triggers a reload, (b) an edit inside a worktree still does *not* trigger
one (confirms the FileFilter's exclude logic still applies against
inotify-sourced events, per §4C's source-level reasoning — verify it live,
don't trust the reasoning alone), and (c) a clean-window `/proc/1/stat`
utime+stime delta (1d/84's own method, §3) actually drops from the current
20.8%. If (c) doesn't show a real drop, the WSL2 blanket-polling heuristic
may be masking a different cost than assumed, and that's worth a fresh
investigation rather than a second guess layered on this one.

`--reload-dirs` (§4B) is not dismissed — it's a real, larger fix genuinely
worth doing eventually, just gated on relocating `main.py` (or the
worktrees) first, which is a bigger, separate decision than this research
was scoped to make.

## Summary for whoever picks up the fix/plan stage

- Polling premise: confirmed on the correct PID (container PID 1, not the
  host-namespace PID the issue originally cited); the *why* is watchfiles'
  own blanket `_auto_force_polling()` WSL2-kernel check
  (`watchfiles/main.py:358-367`), not a bind-mount inotify-propagation
  failure as issue #513 stated — falsified directly: inotify works in 58ms
  on this exact mount when the blanket check is overridden.
- `--reload-dirs` is real (restricts the watcher's walk roots, confirmed
  from `uvicorn/supervisors/watchfilesreload.py:64-79` and
  `uvicorn/config.py:131-164,275-320`) — genuinely different from
  `--reload-exclude`, which only filters an already-produced change batch
  (`watchfilesreload.py:81-88`) and can never prune a *nested* exclude
  directory from a broader watched root (`config.py:294-302`'s pruning only
  fires for an ancestor/exact-match exclude, confirmed against the actual
  resolved values here, not just the code's shape).
- Remaining cost after the worktree drop: 20.8% of a core, clean-window
  measured by 1d/84, corroborated here on file counts; ~52% of the original
  cost is gone, ~48% is structural (full-`/app` polling) and doesn't
  improve with further worktree cleanup alone.
- Recommendation: test `WATCHFILES_FORCE_POLLING=false` first (lower risk,
  same root-cause target, empirically proven functional here); treat
  `--reload-dirs` as a larger follow-up gated on relocating `main.py` or the
  worktrees, not a same-session fix.
