# Adversarial review, round 1: `reload-watcher-polling-research-2026-09-03.md`

Fresh, memory-less Agent dispatch (general-purpose, opus) against the
research doc as originally drafted — assumed wrong until its load-bearing
claims were re-derived from primary sources, never from the doc's own
quoted excerpts or the self-review's "GO" verdict.

**Verdict: NO-GO.** Findings 1-3 below are load-bearing and required the
doc to be substantially rewritten (done, see the main doc's "corrected
after adversarial review" subsection in §2, and §3/§4/Recommendation/
Summary). Per this repo's "nothing advances on one pass" HARD RULE, that
rewrite required a full second review cycle from scratch (round 2, its own
sibling documents), not just a fix-list recheck, since it changed the
doc's scope and introduced a claim (`--reload-dirs` is inert here) neither
the original self-review nor this round saw coming in.

Findings 4-8 were folded into the same rewrite. Reproduced in full below
exactly as the reviewing agent reported it, since this is the primary
record of what was actually checked and how.

---

## FINDING 1 — CRITICAL. §2's operative conclusion is falsified: `--reload-dirs` cannot narrow the walk in this deployment at all.

**What the doc claims.** §2: "`--reload-dirs` restricts the walk itself." §4B: scoping it to app subdirs "would eliminate the ~9,600 worktree files from the walk entirely." Summary: "`--reload-dirs` is real (restricts the watcher's walk roots)."

**What the source actually does.** The doc quotes `watchfilesreload.py:64-69` **verbatim in its own code block** and then never analyzes it:

```python
 64| self.reload_dirs = []
 65| for directory in config.reload_dirs:
 66|     if Path.cwd() not in directory.parents:
 67|         self.reload_dirs.append(directory)
 68| if Path.cwd() not in self.reload_dirs:
 69|     self.reload_dirs.append(Path.cwd())
```

Every `config.reload_dirs` entry that lives **under cwd is discarded**, and cwd is then **unconditionally appended**. The live supervisor's cwd is `/app` (`/proc/1/cwd -> /app`; compose `working_dir: /app`). Every app-code subdirectory is under `/app`. So they are all dropped and `/app` is re-added.

**Evidence — faithful instantiation of the real `WatchFilesReload` class, cwd=/app:**

```
LIVE config (no --reload-dirs):
    config.reload_dirs -> ['/app']
    WatchFilesReload.reload_dirs (== watch() *paths) -> ['/app']
--reload-dirs services tools tests config static frontend:
    config.reload_dirs -> ['/app/config','/app/frontend','/app/services','/app/static','/app/tests','/app/tools']
    WatchFilesReload.reload_dirs (== watch() *paths) -> ['/app']       <-- UNCHANGED
--reload-dirs /app/services (absolute):
    config.reload_dirs -> ['/app/services']
    WatchFilesReload.reload_dirs (== watch() *paths) -> ['/app']       <-- UNCHANGED
--reload-dirs pointing OUTSIDE cwd (/usr/local/.../uvicorn):
    WatchFilesReload.reload_dirs -> ['/app', '/usr/local/lib/python3.13/site-packages/uvicorn']
```

`watch()` is a generator function, so `self.watcher = watch(...)` does not construct `RustNotify` — no watcher was started, nothing live was touched.

**Consequence.** While cwd is `/app`, `--reload-dirs` can only ever **widen** the walk, never narrow it. It is inert for the purpose the issue and the doc want it for. Worse, uvicorn's own startup log is misleading here — it logs `config.reload_dirs` (`config.py:317-320`), not the actual walk roots, so the log would say `Will watch for changes in these directories: ['/app/config', '/app/frontend', ...]` while the watcher walks all of `/app`.

**Required fix.** §2, §4B, the Recommendation and the Summary bullet must be rewritten: `--reload-dirs` does *not* clear the PM's "if it's paths, the fix is real" bar for this deployment. Narrowing the walk additionally requires changing the container's `working_dir` away from `/app`. `--app-dir` does **not** help — verified: `uvicorn/main.py:513-514` is `if app_dir is not None: sys.path.insert(0, app_dir)`, no `chdir`.

---

## FINDING 2 — HIGH. The §2 "`main.py` gotcha" — the doc's headline risk finding — does not exist.

**What the doc claims.** §2 heading "The gotcha that blocks a naive fix: `main.py` is not inside any subdirectory"; "`main.py` itself would silently **stop being watched at all** — a real correctness regression on the dev loop."

**What actually happens.** Because `/app` is always a walk root (Finding 1), `main.py` never stops being watched. Confirmed across all five configurations I tested:

```
FileFilter(/app/main.py) -> True   | walk roots -> ['/app']   (in every case)
```

The doc's *sub*-claims are individually correct, and I verified each:
- `resolve_reload_patterns([], ['main.py'])` → `([], [])` — a bare file passed as `--reload-dirs` is dropped by the `is_dir()` filter at `config.py:152`. ✅
- `--reload-include main.py` adds a pattern only, no directory (`config.py:145-147` only promotes a glob match when the match is itself a directory). ✅

But the conclusion built on them is wrong, because the doc never checked what happens to the *remaining* roots. This is exactly the "one wrong inference is indistinguishable from broken wiring" failure the repo's `never guess; verify or falsify` rule targets — the falsifying lines were inside the doc's own quoted block.

**Required fix.** Delete or fully rewrite the "gotcha" subsection. It propagates into §4B ("silently stops watching the app's own entrypoint"), the Recommendation ("carries a real, structural correctness risk (silently losing `main.py`'s reload coverage)"), and the Summary — all four sites need correcting, not just §2.

---

## FINDING 3 — HIGH. The recommendation's stated reasoning is invalid; its conclusion survives only by accident.

The doc recommends `WATCHFILES_FORCE_POLLING=false` **over** `--reload-dirs` on the grounds that `--reload-dirs` is *riskier*. The real position is that `--reload-dirs` is *inert* — there is nothing to weigh. Three specific sentences must change:

1. §4B "it would eliminate the ~9,600 worktree files from the walk entirely, not just filter them post-hoc" — **false**, strike it.
2. Recommendation: "`--reload-dirs` clears that bar (§2 confirms it restricts the walk, not just the result)" — **false for this deployment**, invert it.
3. Recommendation closing: "`--reload-dirs` … genuinely worth doing eventually, just gated on relocating `main.py` (or the worktrees) first" — wrong gate. It is gated on changing the container's `working_dir`, not on `main.py`'s location.

The end recommendation (test force-polling first) still stands, but the doc must not present it as a risk-weighted choice between two working levers.

---

## FINDING 4 — MEDIUM. §4 "Alternatives, named honestly" omits the cheapest lever, which sits 15 lines from source the doc quotes.

`WATCHFILES_POLL_DELAY_MS` is read by `_default_poll_delay_ms` (`watchfiles/main.py:340-348`), immediately below the `_default_force_polling` the doc quotes at `:325`. Verified live in the container:

```
_default_poll_delay_ms(300) with no env   -> 300
_default_poll_delay_ms(300) with env=2000 -> 2000
```

Default poll delay is 300 ms. Raising it is a same-shape one-line `environment:` change with the same restart cost, **zero** change to what is watched, zero correctness risk, and it does not depend on inotify working. It is strictly lower-risk than §4B and lower-risk than §4C (which changes the notification backend outright). Its cost is reload latency, which is measurable and bounded.

CLAUDE.md requires "competing solution families compared on mechanism, benchmark, correctness, failure behavior, and complexity." A section titled "Alternatives, named honestly" that misses this one is incomplete.

**Required fix.** Add it as a named alternative (with its latency tradeoff stated), and reconsider the recommendation ordering against it.

---

## FINDING 5 — MEDIUM. Units error: "~18,000 directories" is the *file* count, not the directory count — off by ~12x, and it inflates the risk of the doc's own recommendation.

§4C: "the steady-state cost of registering and maintaining inotify watches recursively across **~18,000 directories**". The self-review repeats it ("the real ~18,000-directory tree"). 17,884 / 18,967 is the **file** count. inotify allocates one watch descriptor per **directory**.

**Measured live, inside the container:**

```
/app                        files= 18,967   dirs= 1,542
/app/.claude/worktrees      files= 10,613   dirs=   870
inotify max_user_watches = 524288   max_user_instances = 128
```

Real watch-descriptor demand is ~1,542 — about **0.3%** of `max_user_watches`. The doc's own §1 already established the limit is nowhere near exhausted, so §4C's hedge contradicts §1's evidence while overstating the residual risk on the option it recommends. This is squarely the class of error CLAUDE.md's `dimensional-analysis` HARD RULE names.

**Required fix.** §4C: "~1,542 directories (18,967 files)" and note this is ~0.3% of `max_user_watches`. Same correction in the self-review.

---

## FINDING 6 — LOW-MEDIUM. §3 contradicts itself on whether the residual cost is "structural."

Two statements in the same section:
- bullet 3: remaining worktree files are "**most** of which can't be removed further since they're live sessions' workspaces"
- closing paragraph: "**2 of the remaining 9** worktrees are live sessions' active workspaces that can't be removed regardless"

If 2 of 9 are live, then 7 of 9 *can* be removed — i.e. most **can** be removed, the opposite of bullet 3. This matters because it is the sole support for "the other ~48% … is **structural** … this is the part neither cleanup nor further worktree hygiene touches." Residual cost scales with file count, and worktrees are still 10,613/18,967 = **56%** of it (doc said 54% at 9,601/17,877). Further hygiene demonstrably would touch it. §4A's "known, bounded cost, not a growing one" also leans on this.

**Required fix.** Reconcile the two sentences and soften "structural" to "does not go to zero with hygiene alone."

---

## FINDING 7 — LOW. Misleading source annotation in §2's code block.

The doc annotates:
```python
watch_filter=None,          # <- no filter reaches the walker
```
This implies a filter *would* reach the walker if one were passed. It would not. `watchfiles.watch()` applies `watch_filter` in Python at `main.py:146` — `changes = _prep_changes(raw_changes, watch_filter)` — **after** `RustNotify.watch()` returns. watchfiles' own filter is post-hoc too. The doc's conclusion (exclude cannot reduce walk cost) is unaffected and correct; the annotation is wrong and should read something like `# watchfiles' own filter is post-hoc too (main.py:146)`.

## FINDING 8 — LOW. §2's `config.py:294-302` block is presented as verbatim but is condensed.

§1 explicitly flags its block as condensed. §2's ```python block for `config.py:294-302` silently drops the `try:` / `except ValueError: pass` at lines 299-302. Logic unchanged, but a reader diffing against source will trip on it. Add the same "condensed" note §1 carries.

---

## Claims independently CONFIRMED as correct (round 1)

**Every `file:line` citation checks out** against the installed source (uvicorn 0.32.0, watchfiles 1.2.0). All of `watchfilesreload.py` `:13-52` (FileFilter), `:55-79` (class+`__init__`), `:64-79`, `:72-73` (the `watch()` call), `:81-88` (`should_restart`), `:16-18`; `config.py` `:131-164` (`resolve_reload_patterns`, exactly that range), `:145-147`, `:150-152`, `:275-320`, `:290`, `:294-302`, `:308-315`; `watchfiles/main.py` `:325`, `:358`, ending at `:367` (confirming the self-review's own `:358-364`→`:358-367` correction was right).

**§1 polling premise — confirmed.** `/proc/1/cmdline` byte-for-byte matches the doc's quote. `/proc/1/fd` = 7 fds, **0** inotify; **0** `inotify wd:` lines across all of `/proc/1/fdinfo`. `max_user_watches`=524288, `max_user_instances`=128. Worker PID **25541** exists and is the `--multiprocessing-fork` child, exactly as §1 says.

**§1 mechanism — confirmed.** No `WATCHFILES_FORCE_POLLING` in `/proc/1/environ`. `uname -r` = `6.18.33.2-microsoft-standard-WSL2`. Executed in-container: `_auto_force_polling() -> True`, `_default_force_polling(None) -> True`, and with `WATCHFILES_FORCE_POLLING=false` → `False`.

**§1 mount characterization — confirmed.** `/proc/1/mountinfo`: `8:48 /home/davidf/code/portfolio/showcase-projects/autotrade /app rw,relatime - ext4 /dev/sdd`. Single mount. `docker context ls` shows `default *` active (desktop-linux present but not selected).

**Inotify-works claim — independently reproduced, stronger than the doc's single run.** Own script, three trials each: `force_polling=False` → 13ms/12ms/12ms; `force_polling=True` → 13ms/305ms/294ms (the `True` arm landing right on the 300ms `poll_delay_ms` default is a clean positive control the doc didn't have).

**§2's exclude-is-post-hoc claim — confirmed.** `should_restart` (`:84-87`) pulls the full batch then applies `self.watch_filter`. Live-config `FileFilter` results: `main.py`→True, `services/x.py`→True, `.claude/worktrees/w/services/x.py`→False. And the `config.py:294-302` prune genuinely does not fire — verified against resolved values: `config.reload_dirs=['/app']`, `config.reload_dirs_excludes=['/app/.claude/worktrees']`, a descendant, so neither branch matches.

**§3 file counts — corroborated, and the CPU numbers too (which the doc did not claim to have re-derived).** App-code subtotal: 2,117 vs the doc's 2,115. A 40s clean `/proc/1/stat` utime+stime window independently gave PID 1 at 23.5% of a core, lifetime avg 42.1%, age 5.45h; worker PID 25541 lifetime 112.8% — corroborating 1d/84's 42.9%→20.8% and 107.8% without the doc having claimed to re-derive it.

**"Falsified" characterization — fair, not an overstatement.** `gh issue view 513` states as mechanism: "Docker bind mounts on WSL2 do not propagate inotify, so watchfiles falls back to polling" — a causal claim, wrong on both halves (watchfiles doesn't "fall back," it force-polls unconditionally on the kernel string; inotify demonstrably does propagate on this mount). The doc represents the issue accurately elsewhere (82% figure, 11:56 UTC confirmation quote, `--reload-exclude` "works for its actual job" framing, §4D's "would need its own decision").

**The doc's honesty about what it didn't measure — real, no overreach found.** §4C's "not yet cost-measured at real tree scale," the Recommendation's step (c), and the self-review's "What this doc does NOT do" all state plainly that force-polling's steady-state CPU under real churn was never measured. The overreach found in this round was in §2/§4B (Findings 1-3), not in that hedging.
