# Adversarial review — architecture audit second pass (2026-09-02)

Independent pass per the "nothing advances on one pass" HARD RULE: a fresh
Agent call with no memory of the session that wrote the artifact under
review. Assumption going in: the artifact is wrong until its load-bearing
claims are re-derived from primary sources — never from its own tables, its
self-review, or the first audit's prose. This review is read-only against
the app, the container, the logs, and git; it does not modify the artifact,
the first audit, or any application file.

**Review conducted:** 2026-09-03, 00:50–01:30 UTC (approximately 7 hours
after the artifact's own 18:00 UTC ± 20 min research window). That gap
matters mechanically, not just cosmetically: the `fastapi` and `web`
containers both restarted at **2026-09-02T21:38:39Z**, between the
artifact's research and this review, which erased the container-log
evidence (`docker logs`, nginx `access.log`) the artifact's §3.3 and part
of §4.1 are built on. Where that happened, it is called out explicitly
below rather than silently treated as a clean re-derivation.

---

## Method

All times UTC unless marked local (UTC−5, matching the artifact's own
convention). Container: `ddev-kalshi-whale-poc-fastapi` / `-web` via
`docker exec` / `docker inspect` / `docker logs`, read-only. App:
`https://kalshi-whale-poc.ddev.site:8443` via `curl -sk -m 120`. Git: the
primary checkout at `/home/davidf/code/portfolio/showcase-projects/autotrade`
(HEAD `f12bb46`) for everything except the working-tree diff check, which
the task specified must be read from the primary checkout, not the
worktree. No file was modified; no `git checkout/stash/reset/rebase/merge`
was run; no package was installed; nothing under `data/` was touched.

- 00:50Z `git status`, `git branch --show-current`, `git worktree list`,
  `docker ps` (primary + worktree checkouts).
- 00:52Z `git -C .claude/worktrees/audit-second-pass log --oneline -5`,
  `git status --short` (worktree HEAD confirmed `f12bb46`, matching
  primary; uncommitted edits are the in-progress artifact + its
  `next-action.md`/`open-decisions.md`/pointer-note companions, as
  expected for a not-yet-merged research PR).
- 01:07Z `date -u` (established review clock).
- 01:08Z `git -C <worktree> diff docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-and-rewrite-considerations.md docs/next-action.md docs/open-decisions.md` (confirmed the pointer note and the next-action/open-decisions rewrites exist and match the artifact's description of them).
- 01:08–01:09Z `stat`/`git diff config/settings.yaml` in the **primary**
  checkout (not the worktree, per the task).
- 01:09–01:13Z source reads: `services/diagnostics/routes.py:470-510`,
  `services/fault_log.py` (`recent`/`summary`/`record` signatures),
  `services/capture_writer.py:340-412` + docstring lines 15-32,
  `services/market_history.py` (grep for `_connect`/`record_snapshot_from_ticker`),
  `services/whale_stream/whale_stream_handlers.py:240-345`,
  `services/config/config_store.py:140-190`,
  `frontend/src/js/config-panel.js:185-230`,
  `services/title_cache.py` (grep + lines 30-60, 130-145),
  `services/alerting/alerting.py:200-260`.
- 01:10–01:11Z `git show 13680e5 --stat -s`, `git show 3e274a5 --stat -- CLAUDE.md`,
  `git show 09b2553 --stat -s`.
- 01:13–01:16Z `docker inspect` `StartedAt` for both containers; `docker logs`
  head/tail/`wc -l`; `docker exec ... ls -la /var/log/nginx` + `wc -l`/`head`/`tail`
  on `access.log`; `grep logging_config` for a persistent app log (none found
  beyond stdout).
- 01:16–01:24Z live probes: `curl` `/api/health/faults?component=market_history`
  (504 after 63 s), `/api/health/faults?component=capture_writer&limit=10`
  (200, 43.9 s), `/api/health/pipeline` (200), `/api/observability/history?metric=loop_watchdog.stall_max_ms&hours=24&limit=1000`
  (504 twice), `/api/health/faults?component=loop_watchdog&limit=400` (504),
  `/api/state` with `Accept-Encoding: gzip` (504 after 63 s).
- 01:17–01:19Z epoch-timestamp decodes (`date -u -d @<epoch>`) for every
  `first_seen`/`last_seen` returned by the fault queries.
- 01:20–01:22Z live fd census: `docker exec -i ... sh -s` iterating `/proc/[0-9]*/cmdline`
  for the `multiprocessing.spawn_main` worker (found pid 8, started
  `1788385119` = `2026-09-02T21:38:39Z`, matching the container restart to
  the second); `ulimit -n`; `grep CapEff /proc/self/status`;
  `/proc/sys/kernel/yama/ptrace_scope`; `ls /proc/8/fd | wc -l`;
  per-file handle counts via `ls -l /proc/8/fd`.
- 01:22–01:24Z static counts in the primary checkout: `grep -rn 'with _connect('`,
  `'with _connect() as conn:'`, `'def _connect'` (both `-l` and plain),
  `'conn.close()'`, `'closing('`, `'def _add_column_if_missing'`,
  `'CREATE TABLE IF NOT EXISTS'`, `'sqlite3.connect('`, plus a loop over
  every file defining `_connect` checking for the string `close()` anywhere
  in the file; `cat requirements.txt`.
- 01:24–01:25Z `.claude/hooks/guard_workflow.py` grep for `R2`/`R4`/`GIT_ADD_ALL_BLOCKED`;
  `.claude/settings.json` grep for `guard_data_db`.
- 01:26–01:28Z `services/market_watch/market_fetch.py:55-135`,
  `services/market_watch/selection.py` grep for `max_children_per_parent`
  (chased the live `markets_watched: 0` observation from the `/api/health/pipeline`
  probe against the primary checkout's uncommitted config change).
- 01:29–01:31Z `git log --merges --format='%H %cI %s' | grep -E '#429|#436'`,
  `gh pr view 429/436 --json mergedAt,number,title` (network read against
  GitHub's own record, the authoritative source for merge time — not
  inferred from a local commit's author timestamp).
- 01:31–01:33Z grep of the first audit
  (`docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`)
  for the specific strings the second pass claims to be quoting/correcting
  (`237`, `855 KB/s`, `zero DB access`, `Runtime dependencies are deliberately minimal`,
  `A shared _connect()`, `followed consistently`).

---

## Findings

### F1 — §4.2, `GET /api/health/faults?hours=` scoping bug — **CONFIRMED**

`services/diagnostics/routes.py:489-497` (read directly):

```python
@router.get("/api/health/faults")
async def get_faults(limit: int = 50, component: str | None = None, hours: float = 24.0):
    return {"summary": fl.summary(since_ts=time.time() - hours * 3600),
            "faults": fl.recent(limit=limit, component=component)}
```

`fl.recent()` (`services/fault_log.py:147-148`) takes an optional `since_ts`
that this call site never passes; `fl.summary()` does. The artifact's claim
is exact: `hours` scopes `summary` only, `faults` is lifetime-ordered by
`last_seen DESC`. `fault_log.record()`'s default `severity` is `"error"`
(`services/fault_log.py:85-87`), confirming that a bare `fl.recent()` read
returns lifetime rows carrying their lifetime `count`, indistinguishable in
shape from a genuine 24 h reading unless the reader decodes `last_seen`.
No correction needed.

### F2 — §5 C1/C2, the 237-fault `raw_trades` row is historical, fixed by `13680e5` — **CONFIRMED, independently re-derived**

Live query `GET /api/health/faults?component=capture_writer&limit=10` (run
at 01:16Z, 200 in 43.9 s) returned the exact row:

```
id 1567, component capture_writer, operation "flush", severity "error",
exc_type OperationalError, message "database is locked", context "raw_trades",
count 237, first_seen 1787862135.12, last_seen 1788106135.37
```

Decoded: `first_seen` = **2026-08-27 20:22:15 UTC**, `last_seen` =
**2026-08-30 16:08:55 UTC** — matching the artifact's claim exactly, from a
fresh query, not the artifact's own numbers. The next fault on the same
component/store family (`flush_retained_on_lock`, `rejected_candidates`)
has `first_seen` = **2026-08-30 16:19:54 UTC**, i.e. 10 m 59 s later — the
artifact's "eleven minutes later, the deploy boundary of commit `13680e5`"
is confirmed to the minute. `git show 13680e5 -s` confirms the commit
(2026-08-30 08:17:54 **-0500** = 13:17:54 UTC — same calendar day, well
before the 16:08 last-fault timestamp, consistent with a same-day deploy
via hot-reload rather than a fresh container start) and its message matches
the artifact's quotes verbatim (227 lock faults since 2026-08-27, 460
`raw_trades` rows lost, the retain-on-lock fix). Source read of
`capture_writer.py:388-409` confirms the current branching exactly as
described: `_is_lock_error(exc)` → retain + `warn`; anything else → drop +
`_dropped_counts[store] += len(rows)` + `error`.

Also newly confirmed, live: that same query's `flush_retained_on_lock`
row's `last_seen` is now **2026-09-02 17:22:21 UTC** with `count: 163` —
exactly the "163 live retain-on-lock warnings" the artifact cites in C9 as
the still-open lock-contention set on `candidate_log.db`. This is
independent confirmation of a second artifact claim from the same query.

### F3 — §4.1 mechanism: per-call `sqlite3.connect()` idiom, no `close()` in 26/30 modules — **CONFIRMED, exact counts**

Every count in the Appendix reproduced independently in the primary
checkout, all matching exactly:

| Count | Artifact | Re-derived |
|---|---|---|
| `with _connect(` (services + main.py) | 167 | **167** |
| `with _connect() as conn:` (narrower form) | 142 | **142** |
| `def _connect` occurrences / files | 32 / 30 | **32 / 30** |
| `conn.close()` | 13 | **13** |
| `contextlib.closing(` | 0 | **0** |
| `def _add_column_if_missing` | 11 | **11** |
| `CREATE TABLE IF NOT EXISTS` | 67 | **67** |
| `sqlite3.connect(` | 67 | **67** |
| Modules defining `_connect` with no `close()` anywhere in the file | 26 | **26** (full file list reproduced and matches, including `market_history.py` and `title_cache.py`) |
| `requirements.txt` runtime pins | 14 | **14** (counted every non-comment package line) |

This is the artifact's best-supported section. No correction needed.

### F4 — §4.2/§4.3/§4.5 source claims (event-loop sync write, alerting scope) — **CONFIRMED**

- `services/whale_stream/whale_stream_handlers.py:250` is `async def
  _process_stream_ticker`, and at line ~334 it calls
  `market_history.record_snapshot_from_ticker(...)` directly (not awaited
  through an executor, not scheduled as a task) — a synchronous SQLite
  write shape on the event loop, exactly as claimed.
- `services/config/config_store.py:154-160`, `update()`: `self._data[key].update(value)`
  when both sides are dicts — a shallow merge that replaces the ruamel
  `CommentedMap` node wholesale, exactly as claimed. `frontend/src/js/config-panel.js:196-201`
  builds `min_contracts_by_series` as a fresh `Object.fromEntries(...)` and
  the same function POSTs six full sections
  (`market_analyst`, `whale_signal`, `whale_watcher_kalshi`,
  `series_evaluator`, `risk`, `kalshi`) — confirms "resends every section it
  renders."
- `services/title_cache.py`: `DB_PATH` line 41, `_connect()` at line 56, and
  (contrary to "one site") **five** `with _connect() as conn:` sites (lines
  139, 157, 182, 220, 282) — the artifact's own line citation
  (`title_cache.py:41,56,139`) only names the first of these, and its prose
  says "one `with _connect()` site." This is a minor undercount: the module
  has five call sites sharing the same non-closing `_connect()`, not one.
  Doesn't change the finding (still no `close()` anywhere in the file, per
  F3's 26-file list), but "the smallest reproducer" claim should say "one of
  five sites," not "the" site.
- `services/alerting/alerting.py:219-256`, `check_and_alert`: exactly three
  edge-detected conditions (kill switch, trade-stream connectivity,
  index-stream connectivity) plus `_expire_stale_crash_alerts`; it imports
  `fault_log` only to call `fault_log.record(...)` for its own dispatch
  failures (line ~215) and never calls `fault_log.recent()`/`summary()`.
  Matches the artifact (and its self-review's own correction) exactly.

### F5 — §2/§9, PR #429 and PR #436 mechanics — **CONFIRMED, with one timestamp error (see F9)**

`git show 3e274a5 -- CLAUDE.md` confirms the diff removes exactly the three
named blocks (additive-schema, persistence-idiom/one-file-per-concern, and
"Workflow/tooling and application code never overlap" in full) with the
commit message quoted accurately. `git show 09b2553 -s` confirms the
message and disposition ("commented out, not deleted... no hook-enforced
protection left against rm/mv"). Live grep of the **current**
`.claude/hooks/guard_workflow.py` confirms R2 and R4 are commented out
in place (not deleted) with "disabled 2026-09-02 by direct instruction"
notes at the cited lines, and `.claude/settings.json` no longer registers
`guard_data_db.py` (zero grep hits). The mechanism claims are all correct;
see F9 for the one wrong timestamp.

### F6 — §3.3 nginx access-log natural experiment — **UNVERIFIABLE at review time (container log evidence destroyed by a restart after the artifact's session)**

`docker inspect` on both `ddev-kalshi-whale-poc-fastapi` and `-web` shows
`StartedAt: 2026-09-02T21:38:39Z` for **both** containers — after the
artifact's research window (18:00 UTC ± 20 min, last live probe 20:09:48Z)
and well after the claimed 20:06–20:09Z "recurrence." The current
`/var/log/nginx/access.log` inside the `web` container begins at
`02/Sep/2026:16:38:48 -0500` = **2026-09-02T21:38:48Z**, eleven seconds
after the container's own start — i.e. the log genuinely starts at the
restart; nothing from before it survives. `docker logs` on `fastapi`
likewise starts from `Will watch for changes...` with no earlier content
(19,912 lines total, all since the restart). There is no bind-mounted or
rotated copy of either log outside the container (checked: no
`logging.FileHandler`/`RotatingFileHandler` in the app; nginx's log
directory is container-local per `ls -la`).

Practical consequence: **this review cannot re-derive §3.3's per-local-hour
table, the 504/499 counts, the "History tab foreground vs. throttled
background" comparison, or the 02:45–03:10 local falsifier answer from
primary evidence**, because the primary evidence no longer exists anywhere
this review can reach. This is not a reason to doubt the artifact's
numbers — they were pulled from a 60,000-line tail that genuinely existed
at the time (the self-review's arithmetic checks on that data are
internally consistent, and the counts are the kind of thing a `grep`/`awk`
script gets right or wrong deterministically) — but it means F6 is
classified **UNVERIFIABLE**, not **CONFIRMED**, and any future reader who
wants to re-check §3.3 has the same problem this review just hit: **the
evidence window for a live incident audited via ephemeral container logs
closes the next time something restarts the container**, which in this
repository's own workflow (`ddev restart`, a peer session's tooling, a
crash) can happen at any time. This is a process finding worth adding
to the artifact's own §7 (see Gaps below).

### F7 — §4.1 EMFILE incident timeline (08:24–15:10 UTC) raw log lines — **UNVERIFIABLE from `docker logs` (same restart as F6); PARTIALLY CORROBORATED via persisted `fault_log`**

The specific log lines quoted (`OSError: [Errno 24] Too many open files`
at "log line 14737," the 10-minute-bucket counts 192/28/53/142/223/...) are
not re-derivable: they came from the same `docker logs` stream wiped by
the 21:38:39Z restart. However, `fault_log` and `observability` persist as
SQLite files on the bind-mounted `data/` volume and are **not** wiped by a
container restart, and a live query against them corroborates the shape of
the incident:

- `GET /api/health/faults?component=capture_writer&limit=10` (01:16Z),
  read as a whole (its `summary.most_frequent` is not component-filtered),
  shows `market_history` / `record_snapshot_from_ticker` /
  `OperationalError` / `"unable to open database file"` with **count 1002**,
  `last_seen` = 2026-09-02T20:40:07Z, in a 24 h window ending at the query
  time (~2026-09-03T01:16Z). The artifact's own count was 717, "last 15:10"
  as of its ~20:00Z session. 1002 ≥ 717 with a later `last_seen` is
  consistent with the same fault class continuing to occur between the
  artifact's last sample and now (including, plausibly, the claimed
  20:06–20:09Z recurrence) — it does not contradict the artifact's number,
  it extends it.
- **New, not in the artifact**: the same query surfaced
  `market_history` / `record_snapshot_from_ticker` / `DatabaseError` /
  `"database disk image is malformed"`, **count 45**, `last_seen`
  2026-09-02T20:44:11Z. This is a corruption-class SQLite error — distinct
  from, and more severe than, "unable to open database file" (an `open(2)`
  failure) or "attempt to write a readonly database" (a fallback mode). It
  is not mentioned anywhere in the artifact, and it was still occurring as
  of 20:44Z, four minutes after the artifact's last recorded fd sample
  (20:09:48Z) and arguably within the artifact's own session window. See
  the Must-fix list.
- `market_catalog` / `scan_batch` / `"unable to open database file"`:
  count 6, `last_seen` 2026-09-02T20:38:02Z — consistent with the
  artifact's "market_catalog.scan_batch 5 faults" (one more since).

Classification: **F7a (timeline/log-line specifics): UNVERIFIABLE**.
**F7b (that the fault class was real, large, and market_history-dominated):
CORROBORATED** via a source the artifact did not need to rely on for this
part (the SQLite-persisted fault store survives what the stdout log does
not) — good, but worth the artifact stating outright that its raw-log
evidence has this fragility, since the next reader (or the next incident)
may not have it.

### F8 — §4.1 "recurrence period on the order of 2.5 hours... the current steady state" — **OVERSTATED**

The artifact's own recurrence claim rests on two data points: the original
incident (worker started 07:23:56Z, EMFILE at ~08:24Z, ~60 min) and one
recurrence in the *same* worker (pid 9544, started 17:32:06Z, fresh
`unable to open` lines at 20:06–20:08Z, ~154–156 min in). From two points
in one process's lifetime it generalizes to "a long-running deploy would
hit it every 2–3 hours indefinitely" and elevates Tier 0 item 1 to "the
current steady state."

This review adds a third, independent data point that does not fit that
pattern. The **current** worker (pid 8, container/worker restarted
2026-09-02T21:38:39Z, confirmed via both `docker inspect` and
`/proc/8`'s own start time) was censused live at **01:20Z, 215.5 minutes
(3.59 h) into its lifetime** — past the claimed 2.5 h period —
and held **223 total fds**, of which 98 were on `signal_log.db` and 73 on
`market_history.db`. That is materially *lower* than the artifact's own
samples at *shorter* elapsed time in the prior worker (425–619 fds at
33–40 min; 926 at ~156 min), and nowhere close to the 1,024 ceiling. This
same worker had, moments before the census, already served two of this
review's own heavy diagnostic queries (one of which itself timed out after
63 s) — i.e. it was under load comparable to what the artifact's own
probing put on its worker, and still did not reproduce anything like the
artifact's fd growth curve.

This does not falsify the underlying mechanism (F3's connection-lifetime
finding is solid, source-level, and independently reproduced) — it
falsifies the specific quantitative claim that the leak follows a
~2.5-hour, workload-independent clock and is therefore "the current steady
state" in the strong sense the Executive Summary and Tier 0 use to justify
urgency framing. Three data points (60 min-ish, ~156 min, and now 215+ min
with no incident) are far more consistent with the leak rate being coupled
to *specific request patterns* (diagnostics/observability endpoints that
themselves open many connections per call, of exactly the kind both this
review's and the artifact's own probing generate) than with wall-clock
time. The artifact should not withdraw the finding, but should soften "the
current steady state... every 2-3 hours indefinitely" to "recurs under
concentrated diagnostic/API load; ambient recurrence rate under normal
operation is not established by two samples," and say so before this
framing is used to set Tier 0's time budget.

### F9 — PR #436 merge timestamp — **FALSIFIED**

The artifact states "PR #436 (merged 16:38 UTC, owner-directed)" twice
(line 26 of the intro list, and the §2 table's "Disabled / unregistered,
PR #436, 16:38Z"). `gh pr view 436 --json mergedAt` — GitHub's own record,
the authoritative source, not a local commit's author timestamp — returns
`"mergedAt": "2026-09-02T17:31:31Z"`. The `git log --merges` entry for the
merge commit (`2833bf5`) independently confirms `2026-09-02T12:31:31-05:00`
= `17:31:31Z`. **16:38 UTC is commit `09b2553`'s own author timestamp**
(`Wed Sep 2 11:38:28 2026 -0500` = 16:38:28 UTC) — the underlying commit,
not the PR merge. The two are 53 minutes apart. This does not change any
ordering conclusion in the document (17:31Z is still hours after PR #429's
08:35Z merge and well before the artifact's own 18:00Z session start, so
every "before/after" argument in §2 and §6 that depends on PR #436 still
holds) but it is a factual error in a document whose own stated method is
"a direct citation... or labeled as inference/assumption," and whose P2
finding specifically calls out the *first* audit for exactly this class of
mistake (trusting a timestamp without verifying it against the
authoritative source). Cross-check: PR #429's claimed "merged 08:35 UTC" is
independently confirmed correct via `gh pr view 429 --json mergedAt` →
`"2026-09-02T08:35:11Z"` — so this is a one-off transcription error, not a
systematic one.

### F10 — §4.4 config diff, primary checkout — **CONFIRMED but materially incomplete relative to the diff's current state; timing of the extra changes not established**

`git diff config/settings.yaml` in the **primary** checkout (per task
instructions, not the worktree) at 01:08Z confirms both changes the
artifact names (`markets_watchlist_mode: merge → exclusive`; the 24-line
calibration-audit comment block removed, hunk `@@ -179,30 +170,6 @@`,
lines 182–205 per the self-review's correction — independently confirmed
by counting the hunk: 30 old lines − 6 kept context = 24 removed). But the
**current** diff contains at least two more changes the artifact's §4.4
does not mention at all:

- `kalshi.max_children_per_parent: 5 → 0`
- `kalshi.categories`: narrowed from ten entries (`Sports`, `Crypto`,
  `Climate and Weather`, `Entertainment`, `Economics`, `Politics`,
  `Mentions`, `Commodities`, `Financials`, `Science and Technology`,
  `Elections`) to two (`Crypto`, `Commodities`) — a major scope-narrowing
  change directly relevant to the repository's own standing
  "watchlist is the coverage bottleneck" finding, unmentioned anywhere in
  either audit.

`stat` on `config/settings.yaml` shows `mtime = 2026-09-02 15:48:07 -0500`
= **2026-09-02T20:48:07Z** — after the artifact's last recorded fd-census
timestamp (20:09:48Z) but plausibly still inside its authoring window
(the document's Appendix cites source reads "at `f12bb46`" with no closing
timestamp, and its live probes run through at least 20:09Z). This review
cannot establish whether these two extra changes were present when §4.4
was written and simply omitted, or were made to the file afterward (by the
owner, directly, outside this session) — the working tree carries no
history for uncommitted changes, so there is no way to reconstruct the
diff's contents at an earlier instant. Either way, the artifact's current
description ("two changes") undercounts the live working tree by at least
two more, and the categories change is not a minor omission: it changes
what market categories the app watches *at all*, unrelated to the fd-leak
incident. Flagged as a should-fix: re-run the diff and describe what is
actually there before this document is used as the basis for the "is
`exclusive` intended?" open question.

### F11 — Live app state at review time is markedly worse than any figure in the artifact, and surfaces one new, urgent, uninvestigated symptom — **NEW FINDING, not a correction to an existing claim**

`GET /api/health/pipeline` (01:16Z, 200 OK) on the current worker (uptime
~3.4 h at request time) returned, among other fields:

- `last_tick_duration_sec: 2642.07` (44 minutes) — versus the artifact's
  own live sample of 11.99 s and its characterization of "4–24 s baseline."
- `"markets_watched": 0`.
- `price_staleness.stale_over_300s_count: 45` of 46 open positions;
  `open_position_oldest_age_sec: 2784.5` (46.4 min stale).
- `ingest.queue_wait.window`: `avg_sec 47.49`, `max_sec 244.53` — worse
  than the artifact's `queue_wait` window average 28.97 s / max 92.5 s.
- `handler_timeouts_total: 274` (all in the `trade` class) — a counter not
  discussed in either audit.

Every other diagnostic route probed after this one (`/api/observability/history`
×2, `/api/health/faults?component=loop_watchdog`, `/api/state`) timed out
at 63 s. This is read as corroboration, not contradiction, of the
artifact's core severity claim — the system is not recovering on its own —
but it means several of the artifact's specific point-in-time figures are,
naturally, already stale a few hours later, and the document should not be
read as a current snapshot without saying so.

`markets_watched: 0` is the one figure here worth flagging as urgent on
its own terms: it is a 100% completeness failure against the data-plane
HARD RULE's own definition, on the app's primary discovery path, and it
sits directly downstream of Open Question 9 (whether the uncommitted
`markets_watchlist_mode: exclusive` change is intended). Source read of
`services/market_watch/market_fetch.py:100-108` shows `exclusive` mode
sets `markets = pinned_markets` and explicitly skips the round-robin
discovery branch where `max_children_per_parent` is consumed
(`services/market_watch/selection.py:126-127`) — so the two config changes
found in F10 do not obviously *compose* into "zero markets" by the code
path this review read (exclusive mode should still return whatever
`market_catalog.open_markets_for_series()` resolves for the pinned
watchlist tickers, e.g. `KXBTC15M`). This review did not chase the actual
cause further (out of scope, and the app was too degraded to probe more
without extending well past a reasonable review budget), but is confident
enough in what it did check to say: **`markets_watched: 0` is real, is
current, and is not explained by anything either audit has looked at** —
it could be the exclusive-mode/pinned-series path finding nothing open
right now (benign, transient), or it could be a further symptom of the
market_catalog corruption in F7 (`market_catalog.db` "unable to open"
faults, and possibly the same "malformed" class as `market_history.db`,
not separately checked). Either way, this is worth someone's next look
before Tier 0 executes, not after.

### F12 — Static claims previously spot-checked against the first audit's text — **CONFIRMED**

Grepped the first audit directly for every phrase the second pass claims
to quote or correct: "237 `error`-severity `flush` faults on `raw_trades`
itself" (line 610, matches), "≈855 KB/s (~6.8 Mbit/s)" (line 281, matches),
"zero DB access" (line 854, matches), "Runtime dependencies are
deliberately minimal" (line 892, matches), "A shared `_connect()`" (line
1048, matches), "followed consistently" (line 585, matches — this is from
the first audit's own already-existing post-merge addendum, not
fabricated). No misquote found.

---

## Gaps

These are limits of this review, not necessarily defects in the artifact:

- **G1.** §3.3 (nginx natural experiment) and the raw log-line portion of
  §4.1 could not be independently re-derived at all (F6/F7) because the
  container restarted after the artifact's session and before this review.
  Their *numbers* were not falsified — they simply cannot be checked from
  primary evidence anymore by anyone, including a future reader. The
  artifact should say so, and the repository should treat "extract and
  archive the raw log excerpt the moment an incident is found" as a
  standing practice for any container-log-based finding, not just this one
  (see Must-fix M4).
- **G2.** §3.2's `loop_watchdog` figures (median/p90/max stall,
  windows-with-a-≥10s-block counts) and §3.4's whale-pipeline latency table
  could not be re-queried live: every `/api/observability/history` and
  `/api/health/faults?component=loop_watchdog` call timed out at 63 s
  during this review (F11 explains why — the app is currently worse, not
  better). Not falsified; not independently confirmed either.
- **G3.** §3.6's `/api/state` gzip byte count could not be re-fetched live
  (63 s timeout). The mechanism (nginx `gzip on`, `Content-Encoding: gzip`)
  is plausible and unchanged in the nginx config, but the specific
  961,639-byte figure is unverified by this review.
- **G4.** P2 ("verify or falsify executed against a stale ref" — the first
  audit's session not re-fetching `origin/main` before checking for the
  rule removal) was not independently re-derived; it is a claim about a
  different session's process, not reproducible from this repository's
  current state, and was accepted on the artifact's own account.
- **G5.** The Chromium background-tab-throttling explanation (§3.3) and the
  ruamel comment-attachment mechanism (§4.4) are both explicitly labeled
  inference in the artifact itself and were not independently tested here
  either — consistent labeling, not a gap in the artifact's honesty about
  its own limits.
- **G6.** F11's `markets_watched: 0` was chased one layer deep (confirmed
  `exclusive` mode's code path doesn't obviously zero it via
  `max_children_per_parent`) but not resolved to a root cause. Left as an
  open, urgent item rather than a finished finding.

---

## Verdict: **GO-AFTER-FIXES**

The artifact's central, load-bearing claims hold up against independent
re-derivation to an unusually high standard: every mechanical count in its
Appendix reproduced exactly (F3), the `hours=` diagnostic bug is exactly as
described (F1), the 237-fault historical mis-read and its `13680e5` fix are
independently confirmed down to the minute from a fresh query this review
ran itself (F2), every source-code mechanism claim checked out against the
current file (F4, F5), and the first audit is quoted accurately everywhere
checked (F12). This is genuinely strong work, and — per the "never guess;
verify or falsify" HARD RULE — the artifact itself modeled the standard it
should be held to, including catching its own errors in self-review before
this review ran.

It is not GO-as-is because it carries one outright factual error (F9, a
53-minute-off merge timestamp, checkable in one `gh` call), one overstated
quantitative claim that a second, independent live sample directly
contradicts (F8, the "2.5-hour steady state" framing that Tier 0's urgency
partly rests on), and two live-evidence gaps this review surfaced that the
artifact's own Tier 0/open-questions list should incorporate before anyone
acts on it (F7's newly-found database-corruption-class fault, F11's live
`markets_watched: 0`). None of these undermine the document's core
conclusion (the persistence-idiom fix is real, urgent, and correctly
diagnosed at the mechanism level) — they are corrections and additions a
revision can absorb without re-scoping the document.

**Must-fix (before merge):**

1. Correct "PR #436 (merged 16:38 UTC...)" to **17:31 UTC** in both
   locations (intro list item 2, §2 table) — F9. One-line fix, verified via
   `gh pr view 436 --json mergedAt`.
2. Soften the "recurrence period on the order of 2.5 hours... the current
   steady state" claim (Executive Summary item 1, §4.1's closing
   paragraph, and its echo in Tier 0's framing) to reflect that a third
   live sample (this review's, 215+ minutes into a worker's life, 223 fds,
   no incident) does not fit a fixed-clock pattern — F8. State plainly that
   the ambient/normal-operation recurrence rate is not established by two
   same-session samples, and that the observed growth may be coupled to
   diagnostic/API request volume rather than wall-clock time. This does not
   weaken the case for fixing the leak; it weakens the specific "every 2-3
   hours indefinitely" urgency number used to sequence Tier 0.
3. Add the "database disk image is malformed" fault (`market_history`,
   `DatabaseError`, count 45 as of this review, last seen 2026-09-02
   20:44:11Z) to §4.1 or as a new Tier 0 item — F7. This is a corruption
   signature, not just a failed-open signature, and changes the risk
   framing (potential unrecoverable data loss beyond dropped writes) for
   whoever picks up Tier 0 item 1.

**Should-fix (before or shortly after merge, author's judgment):**

4. Note in §4.1/§4.4/Method that this class of finding — raw container
   stdout logs as primary evidence — has a hard expiry the moment the
   container restarts, and that this repository's normal workflow
   (`ddev restart`, peer-session tooling, crashes) can trigger that at any
   time; recommend extracting and archiving the raw excerpt into the
   artifact or a linked file at the moment it's found, not just quoting
   counts from it — F6/G1.
5. Re-run `git diff config/settings.yaml` in the primary checkout and
   describe what's actually there (at minimum the `categories` narrowing
   and `max_children_per_parent: 5 → 0`) rather than "two changes" — F10.
6. Flag the live `markets_watched: 0` observation (F11) as a new, urgent,
   unresolved item for whoever next opens the app or picks up Open
   Question 9 — it is more severe than "narrows subscription scope" and
   was not visible during either audit's own session.
7. Correct "`services/title_cache.py:41,56,139`... one `with _connect()`
   site" to reflect the five call sites found (lines 139, 157, 182, 220,
   282) — F4. Doesn't change the finding, sharpens the citation.

None of the above rises to a NO-GO: no central claim in the artifact was
falsified outright, the mechanism-level findings (persistence idiom,
diagnostics-label defect, config shallow-merge, sync write on the event
loop) are all independently confirmed from source, and the document is
transparent about its own inference-vs-fact boundaries everywhere this
review checked. Revise per the must-fix list, spot-check the should-fix
list, and this is ready for consolidation.
