# Reload-watcher polling research (issue #513)

Research stage only. Follows up on issue #513's report that the uvicorn
`--reload` parent process burns 43.5% of a core polling ~47k files, 82% of
them under `.claude/worktrees`. Assignment (autotrade-1d, 2026-09-03): (1)
confirm/refute the polling premise independently and establish *why*, (2)
determine what `--reload-dir` would actually do — restrict the walk or only
filter after it — since this is the one thing gating whether a `ddev
restart` is worth spending, (3) quantify the remaining cost after the
worktree count dropped 34→10, (4) name the alternatives honestly. A later
message from the PM narrowed active scope to (2) alone, since (1) and (3)
were independently answered by measurement before this doc was finished —
this doc still carries all four, crediting each finding to whoever actually
produced it.

## 1. Polling premise: confirmed independently

**Two corrections layered on each other here, both left visible rather than
silently edited away, because the second one changes what the first one
actually established.** This doc's first draft claimed issue #513's PID
(`981356`, from `docker top`) "does not exist inside the container's PID
namespace," invalidating its zero-inotify result. Round-2 adversarial
review corrected that: `docker inspect -f '{{.State.Pid}}'` on this
container returns `981356` — the container has no separate init process,
so container PID 1 *is* host PID 981356, the same process viewed through
two different `/proc` mount namespaces. That much is true and settled.

But round 2 then concluded the issue's *original evidence-gathering
method* was valid, which doesn't follow from "it's the same process."
The issue's own commands were run via `docker exec` — *inside* the
container's own namespace, where `/proc/981356` (the host-side PID number)
does not exist, only `/proc/1` does. Reproduced directly: `find /proc/
981356/fd -lname 'anon_inode:inotify'` run inside the container errors
with "No such file or directory" — no watches were actually checked, the
command failed against a path that isn't there. Piped through `| wc -l`
with stderr not redirected to it (as a typical inline shell command would
be run), that failure's error text never reaches `wc -l`, so it reports
`0` — a **spurious zero, indistinguishable from a genuine "checked, and
there are none" result**, exactly the "a retry that succeeds is not
verification" trap this repo's own HARD RULE names. The conclusion (zero
inotify watches) is correct and independently confirmed here at container
PID 1; the original evidence did not actually support it, even though it
happened to agree with the true answer.

Located and confirmed the process from inside the container independently
either way:

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

**Zero inotify watches, confirmed independently.** Not a resource
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
disabled** (later independently reproduced during adversarial review with
three repeated trials: 13ms/12ms/12ms with force_polling off, versus
13ms/305ms/294ms with it forced on — the forced-on arm landing right on
watchfiles' 300ms default poll delay, a clean confirmation this is really
measuring the polling-vs-event-driven difference and not noise). The
issue's stated mechanism ("bind mounts on WSL2 don't
propagate inotify") is refuted for this specific setup — this is a native
dockerd on a native ext4-backed WSL2 filesystem, not the Docker-Desktop-
on-WSL2 case the upstream watchfiles blanket rule most plausibly exists
for. The premise (polling is happening) holds; the *why* the issue gave
does not — watchfiles isn't failing to get inotify to work here, it's
refusing to even try, because of a kernel-string check that doesn't
distinguish this environment from the one it's actually guarding against.

This matters directly for the alternatives section below: it opens a
second lever (§4C) beyond scoping what gets watched.

## 2. `--reload-dir`: does it restrict the walk, or only filter after it?

**This is the load-bearing question the PM asked me to answer from source,
with file:line.** Answer, precisely: **`--reload-dir` is structurally a
different mechanism from `--reload-exclude` — it restricts the walk itself
rather than filtering after it — but the specific, concrete way of using it
here (scoping to app-code subdirectories) is verified inert for this
deployment, not merely risky.** This doc's first draft got that second part
wrong; corrected below, with the mistake and its catch left visible rather
than silently edited away.

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
            watch_filter=None,          # watchfiles' own watch_filter is post-hoc too (watchfiles/main.py:146) — passing one here wouldn't reduce the walk either
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
this case (condensed below — the real code wraps the `.remove()` call in a
`try`/`except ValueError: pass`, omitted here since it doesn't change the
logic):

```python
reload_dirs_tmp = self.reload_dirs.copy()
for directory in self.reload_dirs_excludes:        # from --reload-exclude
    for reload_directory in reload_dirs_tmp:        # from --reload-dir (or cwd)
        if directory == reload_directory or directory in reload_directory.parents:
            self.reload_dirs.remove(reload_directory)
```

This removes a *watched root* entirely, but only when the exclude directory
is an **exact match or an ancestor** of a watched root. `/app/.claude/
worktrees` is a *descendant* of `/app` (today's sole watched root, since no
`--reload-dir` flag is passed at all — `config.py:308-315` falls back to
`[Path(os.getcwd())]` = `[/app]`), not an ancestor — so this condition is
false in both directions and nothing is pruned. Confirmed directly against
the resolved values, not inferred from reading the condition alone.

**Net: today's `--reload-exclude /app/.claude/worktrees` is real (it
correctly stops worktree edits from triggering a reload — issue #513's own
confirmation, "a branch switch in the primary at 11:56 UTC reloaded only on
services/tests paths," holds) but it does nothing to reduce the per-cycle
stat cost. Every file under `/app` (§3: tens of thousands, and growing —
17,884 as of this doc's first measurement, 24,443 by the time round-2
adversarial review re-checked a few hours later) gets walked every cycle
regardless.**

### What `--reload-dir` would actually do (the paths-vs-filter question) — corrected after adversarial review

**First pass on this doc got this wrong in its operative form, caught by a
fresh adversarial-review pass (documented in full in this doc's
`-adversarial-review-round1.md` sibling). Recording the correction here rather
than silently rewriting, since the wrong version already reached a peer
session's PR discussion before this fix landed.**

`--reload-dir` values do flow through `uvicorn/config.py:290`
(`resolve_reload_patterns(reload_includes, reload_dirs)`) into
`self.reload_dirs`, and `WatchFilesReload.__init__` does pass
`*self.reload_dirs` as the literal walk roots to `watchfiles.watch()`
(`watchfilesreload.py:72-73`) — that much is correctly a "paths, not a
filter" mechanism, structurally different from `--reload-exclude`. But
**the doc's original draft quoted the constructor's own root-selection
loop verbatim and then never traced what it actually computes for this
deployment.** Re-reading it:

```python
watchfilesreload.py:64-69
self.reload_dirs = []
for directory in config.reload_dirs:
    if Path.cwd() not in directory.parents:
        self.reload_dirs.append(directory)
if Path.cwd() not in self.reload_dirs:
    self.reload_dirs.append(Path.cwd())
```

Any `config.reload_dirs` entry whose parents include `Path.cwd()` — i.e.
any directory *underneath* the process's working directory — gets dropped
from the first loop, then `Path.cwd()` itself is unconditionally appended.
The container's `working_dir` is `/app` (`.ddev/docker-compose.fastapi.
yaml`), and every candidate app-code directory (`services`, `tools`,
`tests`, `config`, `static`, `frontend`) is a descendant of `/app` — so
**all of them get filtered out, every time, and `self.reload_dirs`
collapses to exactly `[/app]` regardless of what `--reload-dir` names.**

Verified this directly rather than trusting the trace on paper — replicated
the exact logic in the live container against several inputs:

```
cwd: /app
--reload-dir services tools tests config static frontend
  -> [/app]                                            <- collapsed, unchanged
--reload-dir /app/services (absolute path)
  -> [/app]                                            <- collapsed, unchanged
--reload-dir pointing OUTSIDE cwd (e.g. site-packages/uvicorn)
  -> [/usr/local/.../uvicorn, /app]                    <- only case that adds anything, and it ADDS rather than replaces
```

**`--reload-dir` is therefore inert for the purpose this research exists
to evaluate, as long as the container's `working_dir` stays `/app`.** It
can only ever *widen* the watched tree (by naming a root genuinely outside
cwd), never narrow it — the opposite of what issue #513 needs. This also
means the earlier draft's "`main.py` gotcha" (a scoped `--reload-dir`
would silently stop watching the app's entrypoint) doesn't exist as
described: `main.py` is never at risk of losing coverage, because `/app`
— which contains it — is *always* one of the walk roots regardless of what
`--reload-dir` is given. The real problem isn't a coverage gap on one
file; it's that the whole approach doesn't reduce anything at all.

The only way to make `--reload-dir` actually narrow the walk would be to
change the *supervisor process's* cwd away from `/app` (so it stops being
an ancestor of every candidate directory) and then explicitly list the
app-code directories as roots — strictly, this is about the process's own
working directory at the moment `WatchFilesReload` initializes, not
specifically the compose `working_dir:` key (a `cd` inside `command:`
would set it too, though `main:app` would then need `--app-dir /app`
alongside it to stay importable, since `uvicorn/main.py:513-514` only does
`sys.path.insert(0, app_dir)`, never a `chdir`). Two precision points worth
carrying forward for whoever attempts this: the *new* cwd is itself
unconditionally added to the walk roots too (the same
`watchfilesreload.py:68-69` logic this doc traces above), so it has to be
a small, unrelated directory — picking something like `/` as the new cwd
would make the walk catastrophically worse, not better, since `--reload-
dir`'s app-code entries would no longer collapse into it but `/` itself
would still be added whole. That's a materially bigger change than a
`--reload-dir` flag either way — the process's own cwd affects how the
Dockerfile, any relative-path assumption in the app (this repo has direct
prior incidents with relative-path assumptions breaking on directory moves
— see this repo's own `DB_PATH`-relative-move history), and container
tooling all resolve paths, and evaluating its blast radius is out of scope
for this research pass. Not recommended as a same-session fix.

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
- Of the remaining 17,877 files at that measurement, 9,594 (54%) were still
  worktrees.

I corroborated the file counts (not the CPU deltas, which I did not
re-derive) with an independent, lightweight recount minutes later, then
again during adversarial review, then again while fixing this doc — the
worktree count moves constantly as peer sessions open and close their own
(9, then 10, then 11 across three checks in under an hour), so treat every
absolute count below as "as of its own measurement," not a fixed number:

```
worktrees: 9→10→11 across three successive checks
total files under /app:              17,884 → 18,967 → 20,155
files under /app/.claude/worktrees:   9,601 → 10,613 → 11,763
directories under /app:                   —    1,542 →  1,640
directories under /app/.claude/worktrees: —      870 →    967
files under app-code dirs alone
  (services+tools+tests+config+static+frontend+main.py): ~2,115 (stable)
```

App-code's own footprint is stable at ~2,100 files regardless of worktree
churn — it's genuinely the worktrees, not the app, driving both the
absolute count and its growth. **Correction from the doc's first draft:**
worktree-workspace files are **not** mostly permanent — only 2 of the
current worktrees belong to sessions with work still in flight; the rest
are provably-merged and were left behind only because nobody had run
`scripts/cleanup-worktrees.sh` yet. So the ~54-58% "worktree share" of the
walk is not a floor — it fluctuates with how promptly cleanup runs, and
**"structural" below describes the fact that continuous full-tree polling
scales with whatever the tree happens to be at any given moment, not that
this specific 20.8% is a fixed, un-recoverable cost.** ~52% of the
watcher's original cost came from worktree count reduction alone; the
remaining ~20.8% (as of 1d/84's measurement) comes from continuing to poll
whatever's left in `/app`, worktrees included, every cycle — cleanup
discipline helps it, but doesn't zero it out, since watching `/app` at all
means paying for its current size every cycle no matter how small that
size is kept.

## 4. Alternatives, named honestly

**A. Do nothing.** Cleanup helped — 42.9% (lifetime avg) to 20.8% (clean
window) is a real drop — but that comparison mixes a lifetime average with
an instantaneous reading (exactly the trap §3 itself warns about for other
numbers), so "~52%/roughly half" is an estimate of unknown bias, not a
measured share: no instantaneous pre-cleanup reading exists to compare
against like-for-like. **Correction, caught by round-2 adversarial
review: this cost is not bounded or fixed — it scales with tree size, and
tree size is observed growing.** A fresh same-method measurement taken
during that review found the watcher at 29.3% (clean window) against a
tree that had grown to 24,443 files / 15 worktrees by then — up from
17,877 files / 10 worktrees when §3's number was taken, tracking almost
exactly linearly (files +37%, watcher CPU +41%). Worktree count moved
9→10→11→15 across a few hours of normal parallel-session activity in this
repo. "Do nothing" is not a stable state; it's "accept whatever the tree
happens to be at any given moment," which drifts upward between cleanup
runs.

**B. `--reload-dir` scoped to app-code subdirectories. Ruled out —
verified inert, not merely risky.** §2's correction: as long as the
container's `working_dir` is `/app` (it is), any `--reload-dir` value
under `/app` gets silently discarded by `watchfilesreload.py:64-69`'s own
root-selection logic, and the watch always collapses back to `[/app]`
regardless. This isn't a tradeoff to weigh — it does nothing, confirmed by
directly instantiating the real logic against several inputs (§2). Not
viable without also changing `working_dir` away from `/app`, which is a
materially bigger, unevaluated change (§2's closing paragraph).

**C. `WATCHFILES_FORCE_POLLING=false`.** Newly surfaced in this research
(§1), not part of the original issue. Overrides watchfiles' blanket
WSL2-kernel heuristic and switches the underlying mechanism from continuous
per-cycle full-tree `stat()` polling to event-driven inotify — confirmed
empirically functional and fast on this exact bind mount (a synthetic
single-file test in the 12-58ms range across multiple runs; not a
statistically rigorous distribution, but consistent and fast every time).
Zero change to *what* is watched (still `/app` as a whole, no code-layout
change), so it carries none of the risk §B turned out to almost (wrongly)
justify. The FileFilter's post-hoc exclude logic (`watchfilesreload.py:
81-88`) applies identically regardless of which backend produced the
change batch, so `--reload-exclude /app/.claude/worktrees`'s existing
behavior (worktree edits don't trigger a reload) is preserved either way —
confirmed from the same source read in §2, not assumed. **Not yet
cost-measured at real tree scale**: this doc confirms functional
correctness and per-event latency in a synthetic single-file test, not the
steady-state cost of registering and maintaining inotify watches
recursively across the real tree under worktree churn (a new worktree
appearing mid-session, or a peer session's git operations generating
bursts of file events). That watch-registration cost scales with
**directory** count, not file count — every count taken during this
research (1,542, then 1,640, then 1,946 as the tree grew) stayed under
0.4% of this system's `max_user_watches` (524288), so headroom isn't the
concern regardless of the tree's exact size at any given moment; steady-
state CPU under real churn is what's still unmeasured, and that
quantification is fix/plan-stage work, not established here.

**D. `WATCHFILES_POLL_DELAY_MS`, raised from its 300ms default.** Missed
in this doc's first draft, caught by round-1 adversarial review:
`watchfiles/main.py`'s `_default_poll_delay_ms` reads this env var
directly (confirmed live: unset → 300, set to 2000 → 2000). **Correction,
caught by round-2 adversarial review: this doc originally described C and
D as combinable ("worth quoting alongside C rather than instead of it,"
"and/or" in the Recommendation) — that's wrong, they're mutually
exclusive.** `watchfiles/main.py:107` states `poll_delay_ms` is "only used
if `force_polling=True`," confirmed live: with `force_polling=False`,
setting `WATCHFILES_POLL_DELAY_MS=2000` measured identically to leaving it
unset (~10-11ms either way) — the delay setting has zero effect once
force-polling is off. **D is a fallback if C is rejected, never an
addition to it.** It doesn't change the notification backend at all, still
polls (with a longer interval), so it carries none of C's "does inotify
actually stay reliable under this specific container setup long-term" open
question, and its cost is bounded and measurable ahead of time: reload
latency increases by however much the delay is raised, nothing else
changes. Worth having as the safer fallback, not as something to set
alongside C.

**E. Move worktrees outside the bind mount.** Would remove the cost at its
root, but breaks the reason worktrees live inside `/app` in the first
place: `ddev exec` (and this repo's documented workaround for running it
from a worktree, `docker exec -w /app <container> <cmd>` — see CLAUDE.md's
dev-workflow section) depends on worktrees being reachable inside the
container's `/app` bind mount. Real, already-acknowledged tradeoff (issue
#513 itself names it as "would need its own decision"); not evaluated
further here since it's a bigger, separate change than what this research
was scoped to weigh.

## Recommendation

**Test `WATCHFILES_FORCE_POLLING=false` (§4C) first; `WATCHFILES_POLL_
DELAY_MS` (§4D) is the fallback if that's rejected, not something to set
alongside it — they're mutually exclusive, not complementary (§4D's own
correction). Do not spend the restart on `--reload-dir` (§4B) — it's
verified inert, not merely risky, as long as `working_dir` stays `/app`.**

This answers the PM's original decision rule ("if it's paths, the fix is
real and worth a restart; if it's a filter, it buys nothing") more
precisely than a yes/no on `--reload-dir` alone can: `--reload-dir` *is*
a paths-not-filter mechanism in the abstract (§2), but the specific,
concrete instantiation of it available here (scope to app-code
subdirectories) does not clear that bar, because uvicorn's own root-
selection logic discards every one of those directories before the watcher
ever starts. There's no live version of "the `--reload-dir` fix" to
choose between it and something else — it isn't on the table.

`WATCHFILES_FORCE_POLLING=false` and `WATCHFILES_POLL_DELAY_MS` both are
env vars, not CLI flags, so neither is a `command:` array edit — both need
a new `environment:` block added to the `fastapi` service in
`.ddev/docker-compose.fastapi.yaml`, which has none today (checked
directly: the file's own keys under `services.fastapi` are
`container_name, build, restart, user, volumes, working_dir, command,
labels` — no `environment:`, and its comment at lines 36-37 says so
explicitly for an unrelated reason). Either one requires a real `ddev
restart`, and neither touches what's watched or `main.py`'s coverage at
all. Concretely: add `WATCHFILES_FORCE_POLLING=false` (the mechanism-level
fix, targets the root cause directly, has a working proof-of-concept on
this exact filesystem but unmeasured steady-state cost under real churn —
§4C) **first**; only fall back to `WATCHFILES_POLL_DELAY_MS` raised from
300 (a strictly bounded, predictable latency/CPU tradeoff with no open
questions about reliability — §4D) if force-polling-off turns out not to
work here for some reason step (c) below would catch — never both at once,
since D has no effect while force-polling is off. Restart, then verify
end-to-end before declaring it done: (a) an edit under `services/` still
triggers a reload, (b) an edit inside a worktree still does *not* trigger
one (confirms the FileFilter's exclude logic still applies against
whichever backend produced the change — verify it live, don't trust the
source-level reasoning alone), and (c) a clean-window `/proc/1/stat`
utime+stime delta (1d/84's own method, §3) actually drops from whatever it
measures at restart time. If (c) doesn't show a real drop for the force-
polling change specifically, the WSL2 blanket-polling heuristic may be
masking a different cost than assumed, and that's worth a fresh
investigation rather than a second guess layered on this one.

`--reload-dir` is not a "do it later" item — it would need `working_dir`
to change first, which is a separate, unevaluated initiative with its own
blast radius, not a follow-up step on this fix.

## Summary for whoever picks up the fix/plan stage

- Polling premise: confirmed independently at container PID 1 — the
  correct answer, but the issue's own original evidence (a `docker exec`
  check against host-namespace PID 981356, a path that doesn't exist
  inside the container's own `/proc`) returned a spurious zero rather than
  a real one, an important distinction from "the evidence was valid,"
  which round-2 review's own correction got wrong before this final pass;
  the *why* is watchfiles'
  own blanket `_auto_force_polling()` WSL2-kernel check
  (`watchfiles/main.py:358-367`), not a bind-mount inotify-propagation
  failure as issue #513 stated — falsified directly: inotify works in the
  12-58ms range on this exact mount when the blanket check is overridden.
- `--reload-dir` is a real paths-not-filter mechanism in the abstract
  (confirmed from `uvicorn/supervisors/watchfilesreload.py:64-79` and
  `uvicorn/config.py:131-164,275-320`), genuinely different from
  `--reload-exclude`'s post-hoc-only filtering (`watchfilesreload.py:
  81-88`) — **but verified inert for this deployment specifically**:
  `watchfilesreload.py:64-69`'s own root-selection loop discards any
  candidate directory under the process's `working_dir` (`/app`), so the
  watch always collapses back to `[/app]` no matter what `--reload-dir`
  names. This doc's first draft missed this despite quoting the exact
  lines verbatim — caught by a fresh adversarial-review pass, corrected
  here, and the wrong version's implications (a nonexistent `main.py`
  coverage-loss risk) removed.
- Remaining cost after the worktree drop: 20.8% of a core at 1d/84's
  clean-window measurement, corroborated here on file counts (which kept
  climbing across successive checks as peer sessions opened new worktrees
  — treat any specific percentage as a point-in-time reading, not a fixed
  number); ~52% of the original cost came from worktree count reduction,
  the rest from continuing to poll whatever remains in `/app` every cycle
  regardless of its current size.
- Recommendation: test `WATCHFILES_FORCE_POLLING=false` first (targets the
  root cause, empirically functional here, steady-state cost still
  unmeasured), with `WATCHFILES_POLL_DELAY_MS` raised from 300 as the
  fallback if that doesn't pan out — not something to set alongside it,
  since the delay setting has zero effect while force-polling is off
  (confirmed live). Both are real, same-session, `main.py`-safe config
  changes, requiring a new `environment:` block in the compose file (none
  exists today). `--reload-dir` is not a viable lever here at all without
  a separate `working_dir` change.
