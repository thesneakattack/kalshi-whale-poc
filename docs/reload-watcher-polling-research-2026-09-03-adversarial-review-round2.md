# Adversarial review, round 2: `reload-watcher-polling-research-2026-09-03.md`

Fresh, memory-less Agent dispatch (general-purpose, opus) against the doc
as revised after round 1 (`-adversarial-review-round1.md`, which found
`--reload-dirs` was verified inert rather than merely risky). This round
was explicitly instructed to try to break that central claim rather than
rubber-stamp it, since a second reviewer trusting the first reviewer's
correction without re-deriving it would defeat the point of an independent
pass.

**Verdict: GO WITH REQUIRED FIXES.** The central claim survived a
deliberate, broad attempt to break it. Eight findings below (one HIGH, four
MEDIUM/MEDIUM-HIGH, three LOW/LOW-MEDIUM) required fixing before this is
final; none of them touch the central conclusion or the recommendation's
direction. Reproduced in full below, exactly as the reviewing agent
reported it.

---

## THE KEY CLAIM: `--reload-dir` is inert while cwd is `/app` — CONFIRMED, and I could not break it

I did not merely re-read the code. I instantiated the **real**
`uvicorn.config.Config` + `uvicorn.supervisors.watchfilesreload.
WatchFilesReload` in-container and read `self.reload_dirs` — the literal
`*paths` handed to `watchfiles.watch()` — across **18 input shapes** (safe:
`watch()` is a generator function, so no `RustNotify` is constructed and
nothing live was touched).

Attempts to break it, all with `cwd=/app`: relative dirs; absolute dirs;
cwd itself (`/app`); trailing slash; `./services`; `/app/services/../
services`; a symlink **outside** `/app` → `/app/services`; a symlink
**inside** `/app` → `/tmp`; multiple `--reload-dir` occurrences;
`--reload-exclude /app` and `--reload-exclude /` (trying to prune cwd via
`config.py:294-302`); `--reload-include` naming a directory;
`--reload-include` glob (`s*`); `/` as a root; a nonexistent dir; and the
exact proposed fix (6 app dirs + the live worktree exclude).

```
relative app-code dirs      config.reload_dirs -> [/app/config,/app/frontend,/app/services,...]
                            WALK ROOTS         -> ['/app']
absolute /app/services      WALK ROOTS         -> ['/app']
symlink outside -> services WALK ROOTS         -> ['/app']
exclude /app (prune cwd)    WALK ROOTS         -> ['/app']
proposed fix (6 dirs+excl)  WALK ROOTS         -> ['/app']
outside cwd (site-packages) WALK ROOTS         -> ['/app', '/usr/local/.../uvicorn']   <- WIDENS
/ as a root                 WALK ROOTS         -> ['/', '/app']                        <- WIDENS
=> NO case removed /app from the walk roots.
```

Mechanism, three independent locks: `resolve_reload_patterns` `.resolve()`s
every entry (`config.py:151`), so symlinks cannot dodge the descendant
test; `config.py:308-315` restores `[Path(os.getcwd())]` whenever the
excludes empty the list; `watchfilesreload.py:68-69` appends `Path.cwd()`
unconditionally. `--app-dir` is also closed off — `uvicorn/main.py:513-514`
is `sys.path.insert(0, app_dir)` with no `chdir`. **The round-2 correction
is right, and right for the right reason.**

---

## Findings, most severe first

### F1 — HIGH. §1's headline "wrong PID" correction is itself factually wrong

**Claim.** §1 ¶1 and Summary bullet 1: issue #513 used PID `981356`, "a
**host**-namespace PID from `docker top`, which does not exist inside the
container's PID namespace, so the fdinfo check against it silently
returned zero for the wrong reason (no such process, not 'process has no
watches')."

**Evidence.** `docker inspect -f '{{.State.Pid}}'` on this container
returns **981356** — the exact PID the issue cited. Host-side
`/proc/981356/cmdline` is byte-identical to container PID 1's:
```
/usr/local/bin/python3.13 /usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8000
  --reload --reload-exclude /app/.claude/worktrees --proxy-headers --forwarded-allow-ips=*
```
The container has no separate init, so container PID 1 **is** host PID
981356. Re-running the issue's own two commands on the host right now:
```
find /proc/981356/fd -lname 'anon_inode:inotify' | wc -l   -> 0
cat  /proc/981356/fdinfo/* | grep -c '^inotify wd:'        -> 0
```
Both work and return a **valid** zero. The issue's check targeted the
correct process. The doc's "silently returned zero for the wrong reason"
is an unverified inference presented as established fact — exactly the
failure mode CLAUDE.md's "never guess; verify or falsify" HARD RULE names
— and it is used to open the document by discrediting the issue's
evidence. Neither round-1 nor round-2 review checked it.

**Required fix.** Rewrite §1 ¶1 and Summary bullet 1. Honest version: the
issue cited a host-namespace PID, which is the correct namespace for
`docker top` output and resolves to the same process as container PID 1;
the zero-inotify result was valid. This doc independently re-confirmed it
from inside the container's namespace — still worth having, but there was
no methodological error to correct. Drop "with the correct PID" from the
§1 heading and drop the "The PM caught and corrected this before I
started" framing, since the correction was wrong. **Does not change the
recommendation.**

### F2 — MEDIUM-HIGH. §4C and §4D are presented as combinable; they are mutually exclusive

**Claim.** §4D: "Worth quoting alongside C rather than instead of it."
Recommendation: "with `WATCHFILES_POLL_DELAY_MS` (§4D) as a lower-risk
**complement** or fallback"; "add `WATCHFILES_FORCE_POLLING=false` …
**and/or** `WATCHFILES_POLL_DELAY_MS` raised from 300". Summary bullet 4
repeats the "and/or".

**Evidence.** `watchfiles/main.py:107`: "poll_delay_ms: delay between
polling for changes, **only used if `force_polling=True`**" (same at
`:85-89`). Measured live, 4 arms × 5 trials on a temp dir under `/app`:
```
A  force_polling=False                       TIMEOUT, 11ms, 10ms, 11ms, 10ms
B  force_polling=True  (default 300ms)       TIMEOUT, 114ms, 297ms, 302ms, 300ms
C  force_polling=True  + POLL_DELAY_MS=2000  TIMEOUT, 2006ms, 1998ms, 2004ms, 1997ms
D  force_polling=False + POLL_DELAY_MS=2000  TIMEOUT, 10ms, 10ms, 11ms, 10ms   <- delay inert
```
(The leading TIMEOUT is a harness priming artifact, identical in all four
arms.) Arm D is the decisive one: with force polling off,
`WATCHFILES_POLL_DELAY_MS` has **zero** effect — no latency change, and
therefore no CPU benefit either. Setting both env vars is just C.

**Required fix.** §4D and the Recommendation must say these are **mutually
exclusive alternatives**, not complements: "`WATCHFILES_POLL_DELAY_MS`
only takes effect while force polling is on (`watchfiles/main.py:107`,
verified live), so it is a *fallback* if C is rejected, never something to
ship alongside it." Replace every "and/or" with "either / or"
(Recommendation ¶3 and Summary bullet 4). As written, an implementer
following the Recommendation would add both lines and silently get nothing
from D.

### F3 — MEDIUM. §4A's "not a growing one" is falsified by fresh measurement

**Claim.** §4A: "it's a known, bounded cost, **not a growing one**, as
long as worktree count stays roughly where it is."

**Evidence.** Measured just now with the doc's own method (60s clean
window, `/proc/1/stat` `utime+stime`, `CLK_TCK=100`):
```
watcher PID 1:  1758 ticks / 60s -> 29.3% of a core   (lifetime avg 35.4%, age 31,784s)
worker PID 25541:                   194.7% of a core
tree at the same moment: 24,443 files / 1,946 dirs under /app; 15 worktrees
                         15,969 files / 1,264 dirs under .claude/worktrees
```
vs. the doc's 20.8% at 17,877 files. Files +37%, watcher CPU +41%;
cost-per-file 1.16e-3 vs 1.20e-3 %/file — essentially linear. Worktree
count went 9→10→11 across the doc's own hour, and is 15 now. The cost
demonstrably **is** growing, and §3's own three snapshots already showed
the trend — so this is an internal contradiction, not only a staleness
issue.

**Required fix.** §4A: strike "not a growing one"; state that the cost
scales with tree size and that tree size is observed to grow with worktree
count (cite §3's own trend). If any absolute % stays in §4A/Summary, attach
the "point-in-time" caveat §3 already applies to counts.

### F4 — MEDIUM. §4C's "currently ~1,540-1,640 directories" is already stale, and stated without §3's caveat

Live now: **1,946** dirs, **24,443** files under `/app`. §2's "All 17,884
files currently under `/app`" is likewise stale. The **conclusion is
unaffected** — 1,946 / 524,288 = 0.37%, still "well under 1%" of
`max_user_watches`. **Required fix:** carry §3's as-of caveat into §4C and
§2, or state the headroom as a ratio ("<0.5% of `max_user_watches` at
every count observed") rather than an absolute range that will keep
drifting.

### F5 — MEDIUM. The uvicorn CLI flag is `--reload-dir`, not `--reload-dirs`

`uvicorn --help` in the container: `--reload-dir PATH  Set reload
directories explicitly, instead of using the current working directory.`
Source: `uvicorn/main.py:81-84` → `@click.option("--reload-dir",
"reload_dirs", ...)`. `reload_dirs` is the click destination / `Config`
kwarg, **not** a CLI flag. The doc names `--reload-dirs` roughly 20 times
(§2 heading, §4B, Recommendation, Summary). Issue #513 makes the same
slip, so it was inherited rather than invented — but under this repo's "a
flag comes from reading the authoritative source" rule it still needs
correcting. **Required fix:** `--reload-dir` (or `--reload-dir` /
`Config(reload_dirs=…)`) throughout.

### F6 — MEDIUM. The Recommendation asserts an `environment:` block that does not exist

Recommendation ¶3: "one-line changes to `.ddev/docker-compose.fastapi.
yaml`'s `environment:` block". Full read of that file (75 lines): keys
under `services.fastapi` are `container_name, build, restart, user,
volumes, working_dir, command, labels`. **There is no `environment:`
block** — and the file's own comment at lines 36-37 says so explicitly
("no Dockerfile change or explicit `environment:` entry is needed").
**Required fix:** "a new two-line `environment:` block added to the
`fastapi` service (the file has none today)". Trivial in effort, but it is
a checkable claim about a file that was asserted without checking.

### F7 — LOW-MEDIUM. The 42.9%→20.8% delta compares a lifetime average with an instantaneous window — the exact trap §3's own opening warns about

§3 opens by explaining that lifetime averages "don't show a recent
change", correctly labels its two numbers ("lifetime avg, 5h13m" / "clean
64s window") — and then uses their difference as a quantitative result
without carrying the caveat: "~52% of the watcher's original cost came
from worktree count reduction alone" (§3 close) and "recovered roughly
half the watcher's CPU (42.9%→20.8%)" (§4A, unlabeled). No instantaneous
pre-cleanup measurement exists, so the "before" term is not like-for-like.
My own reading shows how far the two diverge on the same process right
now: lifetime avg 35.4% vs clean-window 29.3%. Direction is certainly
right; the **share** is not measured. **Required fix:** state that no
instantaneous pre-cleanup reading exists, so ~52% is an estimate of
unknown bias — or drop the percentage and keep the qualitative claim.

### F8 — LOW. §2's closing paragraph omits that the *new* cwd also becomes a walk root

§2 names changing `working_dir` away from `/app` as the only route to a
real `--reload-dir` fix. Two precision gaps, both re-derived live (real
`Config` + `WatchFilesReload`, `chdir` per case):
```
cwd=/app  reload_dirs=[6 app dirs] -> ['/app']
cwd=/tmp  same                     -> [6 app dirs..., '/tmp']     <- new cwd added too
cwd=/     same                     -> ['/']                       <- catastrophically worse
```
(a) The replacement cwd is itself unconditionally appended, so it must be a
small, non-ancestor directory — picking `/` would make the problem far
worse, and the doc as written doesn't warn the next reader. (b) Strictly,
the binding constraint is the **supervisor process's cwd**, not the
compose `working_dir` key specifically — a `cd` inside `command:` would
set it too (with `--app-dir /app` needed to keep `main:app` importable,
since `main.py:513-514` only does `sys.path.insert`). Doesn't change "out
of scope / not recommended as a same-session fix", but the follow-up path
is described imprecisely. **Suggested fix:** one sentence each in §2's
closing paragraph and §4B.

---

## Claims independently confirmed correct

- **Every `file:line` citation is exact** against the installed source:
  `watchfilesreload.py` 13-52 (FileFilter), 55-79 (class + `__init__`),
  64-69 (root selection), 72-73 (the `watch()` call), 81-88
  (`should_restart`); `config.py` 131-164 (`resolve_reload_patterns`),
  275-320, 290, 294-302, 308-315; `watchfiles/main.py` 146, 325, 340-348,
  358-367. Both condensed blocks are flagged as condensed and are
  logic-preserving.
- **§1 polling premise.** `/proc/1/cmdline` matches the doc's quote
  exactly; `/proc/1/cwd` → `/app`; 7 open fds, **0** inotify; **0**
  `inotify wd:` lines across all of `/proc/1/fdinfo`;
  `max_user_watches`=524288, `max_user_instances`=128; worker PID 25541
  present as `--multiprocessing-fork` child.
- **§1 mechanism.** No `WATCHFILES_FORCE_POLLING` in `/proc/1/environ`;
  `uname -r` = `6.18.33.2-microsoft-standard-WSL2`; in-container
  `_auto_force_polling()` → `True`, `_default_force_polling(None)` →
  `True`, with env `false` → `False`. The "blanket WSL2 kernel-string
  check, independent of whether inotify would actually work"
  characterization is exactly what `main.py:358-367` does. *(Unmentioned
  gotcha, covered implicitly by the doc's own quoted set:
  `WATCHFILES_FORCE_POLLING=0` still means polling **on** — only
  `false`/`disable`/`disabled` disable it. The doc recommends `false`,
  which is correct.)*
- **§1 mount/context.** `/proc/1/mountinfo`: `8:48
  /home/davidf/…/autotrade /app rw,relatime - ext4 /dev/sdd`. `docker
  context ls` → `default *` active. Both as stated.
- **Inotify works on this exact mount.** 10/11/10/11 ms with
  `force_polling=False` vs ~300ms with polling forced on. The doc's
  "12-58ms" range is consistent with mine.
- **`WATCHFILES_POLL_DELAY_MS` does what §4D claims** (while polling):
  unset→300, `2000`→2000, and live latency tracks it exactly
  (1997-2006ms). Extra gotcha worth a parenthetical: it requires
  `isdecimal()`, so `"2000ms"` silently falls back to 300.
- **§2's exclude-is-post-hoc analysis is correct.** `watch_filter=None` is
  passed to `watchfiles.watch()`; the `FileFilter` is applied in
  `should_restart` after `next(self.watcher)` returns a full batch;
  watchfiles' own filter is post-hoc too (`main.py:146`). `config.py:
  294-302` genuinely cannot fire, because `/app/.claude/worktrees` is a
  *descendant*, not an ancestor, of `/app`. `working_dir: /app` confirmed
  in the compose file.
- **§3's app-code footprint.** 2,114 files across the six dirs, +`main.py`
  = **2,115** — exactly the doc's "~2,115 (stable)", across a tree that
  grew 37% meanwhile. The doc's broader shape claim (files ≫ dirs;
  worktrees dominate both and drive the growth) holds: worktrees are 65%
  of files and 65% of dirs today.
- **The "falsified the issue's mechanism" characterization is fair, not an
  overstatement.** Issue #513 states "Docker bind mounts on WSL2 do not
  propagate inotify, so watchfiles falls back to polling" — both halves
  are wrong here (inotify propagates fine; watchfiles doesn't fall back,
  it force-enables on a kernel string). The doc otherwise represents the
  issue accurately: the 82% figure, the 11:56 UTC reload quote, the
  "`--reload-exclude` works for its actual job" framing, and fix #3's
  "would need its own decision".
- **Honesty about what is unmeasured is real, no overreach.** §4C plainly
  says force-polling's steady-state cost under real churn is unmeasured;
  the Recommendation's verification step (c) requires measuring it, and
  step (b) requires live-verifying the exclude rather than trusting the
  source read. F2 is the single place the Recommendation over-claims, and
  it is about *combinability*, not effectiveness.
- **Does the Recommendation follow from what is established?** Yes. §4B
  is ruled out by verified inertness (not by risk-weighting), §4C targets
  the mechanism §1 established, §4E is deferred with a real reason. The
  logic holds once F2's "and/or" is corrected to "either/or".

---

## Method note from the reviewer

No code, config, doc, or live service was modified; no restart was
performed. `WatchFilesReload` was only constructed, never iterated, so no
watcher was started. All scratch scripts were deleted and verified gone
from the primary root, from `/app` inside the container, and from both
worktrees.
