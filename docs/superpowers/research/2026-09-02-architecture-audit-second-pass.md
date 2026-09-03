# Architecture audit — second pass (2026-09-02)

A second, independent pass over
`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`
(PR #430, with follow-ups #431/#433/#434 — "the first audit" below). Direct
request: "review the architectural audit that was done today and do another
pass on it, especially now that Claude's understanding of project rules has
changed." Same scope rules as the first audit: pre-brainstorming research,
docs only, no code, config, or data changed; the input to a future
`superpowers:brainstorming` → design → plan cycle. This document does not
replace the first audit — it amends it section by section (§5, §6), adds
what the first audit could not have seen (§4), and re-derives the
conclusions that were reasoned inside rules that no longer exist (§6).

**How "the rules have changed" is read here — stated as interpretation, not
fact.** Three things changed between the first audit's research window
(2026-09-02 07:49–08:15 UTC, against `main` HEAD `999fcb9`) and now:

1. PR #429 (merged 08:35 UTC, owner-directed) removed three `CLAUDE.md`
   rules the first audit cites by name as constraints: "schema changes are
   additive only", "one SQLite file per concern, no shared DB, no ORM", and
   "workflow/tooling and application code never overlap". Its commit message
   is explicit about why: "These were never user-authored architecture
   constraints; they accreted into the rulebook and were being enforced as
   if they were policy."
2. PR #436 (merged 17:31 UTC, owner-directed) disabled `guard_workflow.py`'s
   R2 (ad hoc `data/*.db` access) and R4 (GitNexus impact before a hot-path
   edit) and unregistered `guard_data_db.py`; its own commit message records
   that "there is now no hook-enforced protection left against rm/mv against
   a live trading database."
3. The first audit's own session received a mid-turn message that the rules
   had been removed, checked, and recorded it as unverified noise (its
   consolidation document's closing note). The message was true — PR #429
   had merged 6.5 hours before the audit PR did. PR #431 corrected the
   document afterwards. So "Claude's understanding of the rules" was, for
   that session, a defended reading of a rule set the owner had already
   retired.

Also in force but not applied by the first audit to *application* code: the
2026-08-30 Toolchain rule "prefer proven installed plugins/MCP servers/
skills/commands over handspun equivalents; a handspun tool defaults to
disabled until its own run history proves real value." The owner's kickoff
message framed the app the same way ("much of this app code is handrolled
where frameworks ... would do just as good if not a better job"). §6.3
re-scores the first audit's "correctly hand-rolled" table with the burden
placed where that rule and that message put it.

**Method (18:00 UTC ± 20 min, 2026-09-02).** Everything below is either a
direct citation (`file:line`, a live measurement with its UTC timestamp) or
labeled as inference/assumption in the same sentence. Sources: live probes
against `https://kalshi-whale-poc.ddev.site:8443` (`/api/health/pipeline`,
`/api/health/storage`, `/api/health/faults`, `/api/observability/summary`,
`/api/observability/history` for six metrics over 24 h, `/api/quality/summary`,
`/api/state`); the nginx access log inside the `web` container (last 60,000
lines: 2026-09-01 23:43 → 2026-09-02 12:58 local, 23,722 `/api/*` rows with
user agents) and nginx's error log (`ddev logs -s web`, 2,967 upstream-timeout
/ client-closed lines); the `fastapi` container log (32,080 lines, 04:42 →
18:10 UTC); a three-sample file-descriptor census of the live uvicorn worker
via `/proc` (`docker exec`, read-only); direct source reads. Local time in
nginx logs is UTC−5; app logs and this document are UTC unless marked
"local". **The container's stdout log and nginx's access log are the
process's own ephemeral state — a restart wipes both.** This document's
own adversarial review confirmed that directly: both containers restarted
at 2026-09-02T21:38:39Z, after this document's research window, and
`docker logs`/`access.log` now begin at that instant with nothing earlier
recoverable by anyone, including a future reader of this document. Where
this document cites a raw log line or an access-log count, that citation
is only as durable as the container's uptime; `fault_log` and
`observability` (SQLite files on the bind-mounted `data/` volume) survive
a restart and are the more durable source for the same underlying events
when both are available — prefer them, and archive a raw excerpt into the
document or a linked file at the moment a log-based finding is made,
rather than planning to re-derive it later. Every quantity carries its unit inline (ms, s, min, per hour); the
only conversions performed are ms→s→min by /1000 and /60 — this is the
dimensional-analysis discipline applied to a document rather than to code.
No subagents were used for research; the adversarial review (Appendix) is
the one fresh-Agent step, per the "nothing advances on one pass" rule.

---

## 1. Executive summary

**The first audit's central verdict survives in its conclusion — no full
rewrite; the single-process, SQLite-per-concern shape is proportionate; the
defects are localized — but not in its shape.** The localized defects are
more severe, more numerous, and less tied to dashboard polling than the
first audit measured, and the most important ones are new. One of them is
live and ongoing as this document is being finished:

0. **As of this document's own review cycle (2026-09-03, 00:50–01:30 UTC),
   the app is watching zero markets and stuck in a 44-minute-and-counting
   tick** (§4.7) — `markets_watched: 0`, `last_tick_duration_sec` frozen at
   2,642.07 s across two independent probes ten minutes apart, 45 of 46
   open positions stale over 300 s. This is a complete, current
   completeness failure on the primary discovery path, not an intermittent
   one, and it is now Tier 0's top item, ahead of the fd leak below —
   which may be a contributing cause (§4.7's uneliminated hypotheses).
1. **A 6.8-hour data-loss incident happened today, twenty minutes after the
   first audit's monitor ended, and nothing has tracked it since.** At
   08:24 UTC the live worker hit its 1,024-descriptor limit (`OSError:
   [Errno 24] Too many open files`, 116 log lines). From then until the
   14:45 UTC hot-reload every SQLite open failed intermittently across at
   least seven components (1,187 `unable to open database file` /
   `attempt to write a readonly database` lines); `capture_writer` took its
   non-lock **drop** path 1,127 times (414 `raw_trades`, 356
   `rejection_events`, 357 `rejected_candidates` batches); `game_state`
   dropped 9 rows; outbound Kalshi HTTP failed DNS with `[Too many open
   files]`; `task_supervisor` restarted `scheduler.auto_apply`,
   `signal_resolution.background_check` and `market_catalog.scan_batch`
   after crashes; and the observability capture stream has a 408-minute
   hole starting at exactly 08:24 UTC with no fault row of its own (§4.1).
   The replacement worker (started 17:32 UTC) already held 425–619
   descriptors at 35–40 minutes of uptime, 366 of them SQLite data files —
   144 on `market_history.db` alone, opened read-write at file position 0 — so
   recurrence is plausible on a similar timescale until the leak is fixed,
   though a third live sample (§4.1's "Recurrence" note) shows it is not a
   fixed wall-clock period — treat the leak as urgent and the specific
   "2-3 hours" figure as unconfirmed. The EMFILE cause is log-confirmed;
   which references keep 100+ connections per file alive is not yet
   pinned, and §4.1 names the falsifiers.
2. **The first audit's Tier-1 item 3 is wrong.** "237 `error`-severity
   `raw_trades` `flush` faults in the last 24h that drop rows outright" is a
   lifetime aggregate whose last occurrence was **2026-08-30 16:08 UTC**,
   three days before the audit; commit `13680e5` (2026-08-30) changed the
   lock path to retain-and-retry, and the live counter reads 0 dropped rows
   for every store in the current process. The misread has a mechanism:
   `GET /api/health/faults?hours=` applies `hours` to the `summary` block
   only — the `faults` list it returns alongside is lifetime
   (`services/diagnostics/routes.py:489-496`). The audit's adversarial
   reviewer, the audit's author, and `docs/next-action.md` all read that
   list as "last 24h". That is a data-plane *accuracy* defect in the
   diagnostic API ("a value means exactly what its label says") and a
   one-line fix (§4.2, §5 C1).
3. **Event-loop stalls and dashboard-polling timeouts are two different
   problems; the first audit's top priority fixes only the second.** The
   nginx access log contains a natural experiment: the same browser tab,
   foregrounded on the History tab at the 6 s timer, produced 411–2,305
   gateway 504s per hour; the same tab background-throttled by the browser
   to about one fetch per minute (03:00–06:00 local) produced 0–10 per hour
   (§3.3). De-polling those routes will remove the 504s. But ≥10 s
   event-loop stalls occurred in **every** hour of the last 24 (4–21 per
   hour, worst 130 s), including 14 in the 17Z hour while the tab was
   throttled, and their hourly counts do not track tab state at all (§3.2).
   Their cause is still unattributed; the container cannot run `py-spy`
   (no `CAP_SYS_PTRACE`, `ptrace_scope=1`), and §4.3 names the cheap
   in-process alternative.
4. **The product metric is measured in minutes.** `whale_pipeline.stage.
   receive_to_decision.window_max_ms` reached 562,930 ms (9.4 min) in the
   04Z hour and exceeded 10 s in 156 of 258 captured windows;
   `receive_to_handler_end` peaked at 818,541 ms (13.6 min); the trade
   queue reached its 20,000-message cap (or one message short of it) in
   three separate hours and
   `trade_stream.dropped_messages` reached 78,457 in one window; the
   current process's `queue_wait` window average is 28.97 s with 718 of
   1,253 messages waiting more than 10 s (§3.4). The first audit reported
   route latency and loop-blocked percentage; it did not put signal-to-
   decision latency — the quantity the data-plane rule calls the edge —
   at the top. This pass does.

**What the rule change does to the first audit's conclusions (§6):**

- **§5 (SQLite fitness) was reasoned inside the removed "one file per
  concern, no shared DB, no ORM" rule.** Without it, the recommendation
  changes shape: not "keep 18 files, DuckDB for 2, retention for 3" but
  **one persistence module instead of thirty copies of one** — a shared
  `connect()` that actually closes, one WAL/busy-timeout policy, one schema
  registry, one `add_column_if_missing`. The first audit explicitly declined
  exactly this ("a shared `_connect()` would fight the one-file-per-concern
  architecture rule for no correctness gain"). Today's incident is the
  correctness gain: 26 of the 30 modules that define `_connect()` never call
  `close()` anywhere, `with sqlite3.Connection` does not close, and the
  worker is holding hundreds of read-write handles on three files. Engine
  choice (SQLite vs DuckDB vs Postgres) is secondary to that and is
  re-scored on its merits in §6.1.
- **§9's DDL-duplication finding stands on engineering grounds** (silent
  schema divergence); the "conflicts with CLAUDE.md's additive-schema rule"
  framing is dropped.
- **§8's "correctly hand-rolled" table is re-scored under the 2026-08-30
  rule** with incident counts as the run history: the config store (three
  data-wipe incidents, the third in today's working tree — §4.4), the
  observability layer (three semantics misreads in 24 h and a 6.8-hour
  self-inflicted blind spot — §4.6), and the per-call SQLite idiom (today's
  EMFILE) move from "keep as-is" to "replace or bound." Five others stay.
- **Hooks:** every migration or retention item the first audit proposes now
  runs with no harness guard on `data/*.db`; each such plan inherits
  "backup-first, dry-run, row-count reconciliation" as its own requirement
  rather than assuming a hook will catch a mistake.
- **Tooling/app separation removed:** `tools/soak_analyzer.py`'s checks —
  which would have surfaced today's incident had anyone run them — may now
  run in-process on a schedule. That is the cheapest alerting path this
  repo has, and it was previously forbidden.

**Revised verdict on "is this a rewrite":** still not a full rewrite. But
"targeted fixes" undersells the shape: the persistence layer (connection
lifetime, schema ownership, hot-path sync writes, diagnostics labels) needs
one bounded rewrite as a unit, and it is now permitted. The rest of the
first audit's plan — de-polling, the DRY safety bugs, the EV gate, the Preact
migration, the eventual process split — stands, re-ordered behind that and
behind the live incident (§8).

---

## 2. What changed in the ground rules, and where the first audit leaned on them

| Rule / guard | Status now | First-audit sections that leaned on it | Effect (detail in §6) |
|---|---|---|---|
| "One SQLite file per concern, no shared DB, no ORM" (`CLAUDE.md`, persistence idiom) | Removed, PR #429, 08:35Z | §1 ("the one architectural rule ... is followed consistently"), §5.1, §5.2 ("A shared `_connect()` would fight ..."), §9 intro, §9.2 #4 ("doesn't violate the one-DB-per-concern rule"), §11, §12 | Re-derived: shared persistence module now recommended; engine choice re-scored; ORM/migration tooling evaluated for the first time |
| "Schema changes are additive only (`CREATE TABLE IF NOT EXISTS` + `_add_column_if_missing`)" | Removed, PR #429 | §9.1 (DDL duplication "directly conflicts with" it), §13 Tier 1 #2 | Finding kept on its own merits; framing dropped; a real migration tool becomes an option |
| "Workflow/tooling and application code never overlap" | Removed, PR #429 | §2.5 (worktree scanning framed as tooling-only), §8.2 (`task_supervisor`), §11 #4 (`soak_analyzer` as external contract enforcer) | In-process scheduling of soak checks now allowed — the missing alerting path |
| `guard_workflow.py` R2 (ad hoc `data/*.db` access), R4 (GitNexus before hot edit); `guard_data_db.py` (rm/mv guard) | Disabled / unregistered, PR #436, 17:31Z | None cited explicitly; §5.2's DuckDB migration and retention items implicitly assumed the rm/mv guard | Each data-layer plan must carry its own safety steps |
| "Prefer proven installed tooling over handspun; handspun proves itself by run history" (2026-08-30) | In force before the first audit | §8 did not apply it to application code | §6.3 re-scores §8.2 |
| Data-plane HARD RULE, never-guess HARD RULE, nothing-advances-on-one-pass, dimensional analysis, Kalshi docs authority, safety invariants | Unchanged | Throughout | Unchanged; this document is bound by them |

`tools/quality_audit/persistence.py` still enforces the `DB_PATH`
registration the old idiom implied; PR #429's own message says adjusting it
is separate follow-up work. It is a test-isolation guard, not an
architecture rule, and this pass recommends keeping it regardless of what
the persistence layer becomes (§6.1).

---

## 3. Live re-measurement

### 3.1 Routes — the same six endpoints, 17:54–17:57 UTC

| Endpoint | First audit (07:49–08:04Z, 24 samples) | This pass (single probes, 17:54–17:57Z) |
|---|---|---|
| `/api/health/pipeline` | 0.10–4.78 s, 24/24 OK | **no response in 30 s**, then 200 in **36.7 s** on a second probe |
| `/api/health/storage` | 0.04–1.34 s, 24/24 OK | **no response in 30 s** |
| `/api/observability/summary` | 0.55–11.55 s | 2.63 s |
| `/api/quality/summary` | 6.51–12.94 s, 9/24 client timeouts | 13.72 s |
| `/api/state` | 0.84–9.42 s | 961,639 bytes on the wire with `Content-Encoding: gzip` (see §3.6) |

`/api/health/pipeline` on the successful probe: `last_tick_duration_sec`
11.99; 67 open positions, all price-stamped, oldest 70.2 s; `capture_writer`
`dropped_rows` 0 / `lock_retries` 0 for all three stores (process-lifetime
counters, worker started 17:32 UTC).

The two 30-second silences are the event loop, not the routes: the
`loop_watchdog` fault rows written in the same minutes read "worst 78,279 ms",
"82,601 ms", "82,496 ms", "88,181 ms" (§3.2). Nothing in these probes is
comparable to the first audit's route table as a route measurement — the
comparison is that the app is at least as degraded now as then, with the
dashboard nearly idle (§3.3, 12:xx local column).

### 3.2 Event-loop stalls — 24 h, two independent sources

**Source A — `fault_log` rows** (`/api/health/faults?component=loop_watchdog&limit=400`;
288 rows returned, one per capture window with `stall_count > 0`, spanning
20.81 h; note per §4.2 that this list is lifetime-sorted, not 24 h-filtered,
but all 288 rows fall inside the last 21 h):

| Metric | Value |
|---|---|
| Median worst single block per window | **12,081 ms** |
| p90 / max worst single block | 71,895 ms / **114,779 ms** |
| Windows with a ≥10 s block | 147 of 288 |
| Windows with a ≥60 s block | 34 |
| Last 2 h only (16:10–18:10Z) | 60 windows, 26 with a ≥10 s block, max 114,779 ms |

The first audit's comparable figures (183 rows, 10.81 h): median 15,845 ms,
max 100,757 ms. Same order of magnitude; not improved.

**Source B — `observability` samples** (`/api/observability/history?metric=loop_watchdog.stall_max_ms&hours=24`,
392 samples). Per UTC hour: worst stall in ms / number of samples with a
≥10 s stall / samples captured that hour:

| Hour (UTC) | 09-01 18 | 19 | 20 | 21 | 22 | 23 | 09-02 00 | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09–14 | 15 | 16 | 17 | 18* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| worst ms | 130,318 | 77,139 | 82,995 | 65,358 | 54,969 | 77,345 | 40,827 | 29,270 | 69,901 | 23,670 | 100,757 | 57,565 | 39,770 | 71,895 | 44,356 | **none** | 108,632 | 114,779 | 108,272 | 95,291 |
| ≥10 s | 21 | 16 | 17 | 21 | 15 | 10 | 5 | 4 | 4 | 1 | 8 | 15 | 8 | 12 | 9 | — | 9 | 12 | 14 | 2 |
| samples | 25 | 37 | 33 | 30 | 33 | 15 | 6 | 4 | 5 | 1 | 19 | 27 | 14 | 36 | 16 | **0** | 28 | 27 | 33 | 6 |

\* partial hour. Gaps longer than 15 min between consecutive samples:
23:21Z (76 min), 01:19Z (18), 01:37Z (20), 02:30Z (24), 02:54Z (45), 03:39Z
(43), **08:24Z (408 min)**. The last one is the incident (§4.1); the others
are the capture loop itself being starved (§4.6). The sample counts matter
for reading every other row: an hour with 4 samples (01Z) is not a quiet
hour, it is an hour the monitor mostly could not observe.

**What this table says about attribution.** Read it against §3.3's
per-hour dashboard column: the hours with the heaviest foreground polling
(05Z–06Z and 15Z–16Z UTC, i.e. 00–01 and 10–11 local) have 15/8 and 9/12
≥10 s stalls; the hours with the tab throttled to ~1 fetch/min (08Z–11Z
local 03–06) have 9 (08Z) and then no samples; the 17Z hour, with the
dashboard nearly idle (26 `/api/state` fetches in the whole local 12:xx
hour), has 14. The first audit's sentence "the frontend polls those routes
on a fixed 6-second timer regardless of cost, so both problems compound"
is true of the 504s and not shown for the stalls. The stalls are an
always-on mechanism this pass could not attribute (§4.3).

### 3.3 The dashboard natural experiment — nginx access log, per local hour

All rows are browser (`Mozilla` user agent) `/api/*` requests from the public
tunnel client `172.18.0.2` (21,787 rows) plus a second browser client
`172.18.0.8` (1,703). "History-5" is the sum of five of the ten loaders
`refreshHistoryInsightsIfActive()` fans out (`/api/candidate-log/summary`,
`/api/confidence-calibration/report`, `/api/regime/by-category`,
`/api/backtest/entry-threshold`, `/api/advisory/status`); "504" and "499"
are nginx's upstream-timeout and client-closed statuses on any browser row.

| Local hour (UTC−5) | `/api/state` | History-5 | `/api/quality/summary` | 504 | 499 | Reading |
|---|---|---|---|---|---|---|
| 09-01 23 | 62 | 93 | 8 | 152 | 0 | partial hour, tab foreground |
| 09-02 00 | 704 | 1,129 | 1 | **779** | 137 | History tab, foreground, 6 s timer |
| 01 | 1,071 | 1,626 | 0 | **2,305** | 353 | same; worst hour |
| 02 | 166 | 661 | 18 | 75 | 0 | transitioning to background |
| 03 | 76 | 294 | 8 | 10 | 0 | tab hidden: ~1 fetch/min per route |
| 04 | 60 | 285 | 0 | **0** | 0 | hidden |
| 05 | 60 | 301 | 0 | **0** | 0 | hidden |
| 06 | 19 | 90 | 0 | 0 | 0 | hidden, then closed |
| 10 | 236 | 384 | 0 | **411** | 0 | foreground again |
| 11 | 317 | 785 | 0 | **877** | 28 | foreground |
| 12 | 26 | 99 | 0 | 7 | 0 | hidden / mostly idle |

Three things this establishes that the first audit left open:

- **The falsifier the first audit named and did not run ("was a browser tab
  open during the 15-minute monitor?") has its answer: yes.** During
  02:45–03:10 local (07:45–08:10Z, bracketing the monitor) the browser
  fetched each History-tab route 29–31 times and `/api/state` 50 times,
  alongside the monitor's own 30 `/api/state` and 29 `/api/quality/summary`
  curls. The tab was open on the History tab and already throttled to about
  one fetch per minute. So the monitor measured: a throttled History tab,
  plus its own `/api/quality/summary` every 20 s on the single shared
  `_aio_db` connection (see §5 C8 on self-interference).
- **Foreground vs. background is the whole difference for the 504s.**
  Same tab, same routes, same client: 6 s cadence → 411–2,305 gateway
  timeouts per hour; ~60 s cadence → 0–10. The ~1/min cadence is consistent
  with Chromium's documented intensive wake-up throttling of timers in
  hidden tabs (inference; the browser build was not checked). Timeouts
  cluster in 5-minute bursts (01:20–02:00 local: 27–289 per 5 min;
  11:10–11:30: 160–265 per 5 min), the signature of the two-worker
  `tick_executor` pool being pinned by `/api/candidate-log/summary`
  (13.6 s per call in the first audit) requested every 6 s.
- **`/api/quality/summary` is almost never fetched by the browser** (35
  browser rows in 13 hours; the Terminal tab was rarely open). The first
  audit's Tier-1 #1 lists it first; on this evidence it is the least
  important of the three routes to de-poll and the most important one to
  make cheap, because it is the endpoint `CLAUDE.md` tells every session to
  start from.

### 3.4 The product metric — signal-to-decision latency and completeness

From `/api/observability/history` (24 h) and the live `/api/health/pipeline`:

| Metric | 24 h worst | Windows > 10 s | Note |
|---|---|---|---|
| `whale_pipeline.stage.receive_to_decision.window_max_ms` | **562,930 ms** (9.4 min, 04Z) | 156 of 258 | WS receive → strategy decision |
| `whale_pipeline.stage.receive_to_handler_end.window_max_ms` | **818,541 ms** (13.6 min, 22Z) | — | WS receive → handler done |
| `trade_stream.ingest.queue_depth` | **20,000** (the cap) at 20Z and 04Z, 19,999 at 21Z | — | queue full or one short in three separate hours |
| `trade_stream.dropped_messages` | **78,457** in one window (04Z); 24,898 (20Z) | — | trade messages discarded, exchange-wide subscription |
| `index_stream.ingest.handler.index.window_max_ms` | **533,989 ms** (8.9 min, 15Z) | — | index-feed handler |
| `kalshi_rest_class.critical_position.network.window_max_ms` | 116,462 ms | — | position REST calls |
| `writer.last_flush_age_ms.raw_trades` | 636,065 ms (10.6 min) | — | capture writer starved |

Live, current process (started 17:32Z, `/api/health/pipeline` at 17:57Z):
`queue_wait` window average **28.97 s**, max 92.5 s, 718 of 1,253 messages
waited more than 10 s; lifetime average 14.38 s over 18,728 messages;
`discarded_on_reconnect` 11,236 trade messages. Whale-pipeline handler time
itself is small (`handler_total` window avg 4.7 ms, max 458 ms): the
minutes are spent waiting for the loop, not doing work.

The first audit's §2.1 headline ("median ~52% of a stalled window blocked")
is a correct description of the same condition from the watchdog's side.
This table is the same condition from the product's side, and it is the
one a trading system is judged on: a whale print that reaches the decision
code nine minutes after the exchange sent it is not a signal, and 78,457
dropped trade messages in one capture window is a completeness failure of
the exchange-wide subscription the app pays for.

### 3.5 File-descriptor census of the live worker (read-only, `/proc`)

`docker exec` into the `fastapi` container as uid 1000; `ulimit -n` =
**1024**; worker = the `multiprocessing.spawn_main` child of uvicorn's
reloader (pid 9544, started 17:32:06Z):

| Sample (uptime) | Total fds | Threads | Notes |
|---|---|---|---|
| ~33 min | 619 | 26 | during concurrent `/api/health/faults` and `/api/observability/history` probes |
| 35 min | 425 | 26 | 366 on `data/*.db`, 44 sockets, 9 pipes |
| 37 min | 436 | 26 | `market_history.db` 144, `market_catalog.db` 92, `signal_log.db` 80, `title_cache.db` 32 |
| ~38 min | 609 | 26 | 60 `market_history.db` handles sampled: 59 opened read-write, 1 read-only, all at position 0 |

Per-file handle counts on the three largest were identical across samples
(144 / 92 / 80) while `title_cache.db` grew 5 → 32 → ≥40 (the last sample capped at 40) and the total swung
by ~190 between samples. A healthy SQLite connection in WAL mode holds three
descriptors (db, `-wal`, `-shm`); `market_history.db` shows 144 on the main
file against 2 on its `-wal`. These are not 144 concurrent transactions.
They are connections that were opened and not closed, or were closed by
nothing yet (§4.1 for what that means and how to pin it).

### 3.6 `/api/state` on the wire

With `Accept-Encoding: gzip` (what a browser sends) the live response was
961,639 bytes, `Content-Encoding: gzip`; the access log's per-response byte
counts for successful browser `/api/state` fetches averaged 0.32–0.74 MB per
local hour. The first audit's "5.13–5.38 MB ... ≈855 KB/s (~6.8 Mbit/s)
sustained per open browser tab" is the uncompressed JSON size; the network
cost is ~5× smaller. The serialization cost on the event loop and the
gzip cost in nginx stand; the bandwidth argument for §6.3 #2 is weaker than
stated (§5 C5). It is still worth doing for the serialization reason.

---

## 4. New findings — what the first audit could not have seen, or did not look at

### 4.1 File-descriptor exhaustion: a 6.8-hour data-loss incident, 08:24–~15:10 UTC today, untracked

**Timeline (UTC, from the `fastapi` container log and `fault_log`):**

| Time | Evidence |
|---|---|
| 07:23:56 | worker pid 9189 starts (hot-reload after a test-file edit) |
| 08:24:45 | `ERROR [services.game_state] flush failed, 9 row(s) dropped` — `sqlite3.OperationalError: unable to open database file` at `game_state.py:83 _connect` |
| 08:24:48 | `ERROR [services.capture_writer] capture_writer flush failed for store raw_trades` — same error at `capture_writer.py:374` (the `sqlite3.connect` line of `_flush_store`) |
| 08:24 | `fault_log`: `market_history.record_snapshot_from_ticker` — `attempt to write a readonly database` (1) and the first of **717** `unable to open database file` (last 15:10) |
| 08:24:52 / 08:24:58 | `task_supervisor`: `scheduler.auto_apply crashed`, `signal_resolution.background_check crashed` (60 supervisor error lines in the window) |
| ~08:24–08:25 | log line 14737: **`OSError: [Errno 24] Too many open files`**; then `aiohttp ... Cannot connect to host external-api.kalshi.com:443 ... [Too many open files]` from `milestone_scan` for every category (116 EMFILE-bearing lines in total) |
| 08:24 → 15:12 | **observability capture: zero samples for 408 minutes** (§3.2), no `fault_log` row records the gap |
| 08:20–14:50 | 1,187 `unable to open database file` / `readonly database` lines, in 10-minute clusters: 08:20 192, 08:30 28, 08:40 53, 09:00 142, 09:10 223, 11:10 51, 12:40 34, 12:50 6, **13:00 352**, 13:10 43, 14:40 63 |
| 08:26 → 13:10 | `market_catalog.scan_batch` 5 faults; 13:01–13:08 `capture_writer` (2) and `signal_resolution` (2) `unable to open` faults |
| ongoing → 20:44 | **`market_history.record_snapshot_from_ticker` raising `DatabaseError: database disk image is malformed`, 45 occurrences, last seen 20:44:11 UTC** — a corruption signature, distinct from and more severe than the `OperationalError: unable to open` class above (an `open(2)` failure); confirmed live by this document's own adversarial review, not in the original log sweep |
| 14:45:34 | hot-reload (PR #429's `tools/quality_audit/persistence.py` arriving in the checkout) — last `capture_writer flush failed` line 14:45:34 |
| 16:26–16:34, 17:31–17:32 | further hot-reloads from PR #436's hook/test edits; current worker pid 9544 from 17:32:06 |

**What was lost.** `capture_writer._flush_store` (`services/capture_writer.py:348-412`)
retains a batch only on a lock error; any other exception — this one —
**drops the batch and counts it** (`_dropped_counts[store] += len(rows)`,
line 409). 414 `raw_trades`, 356 `rejection_events`, 357
`rejected_candidates` batches went that way. Batch sizes are not in the
log; `capture_writer`'s own docstring records ~38 rows per `raw_trades`
batch at the 1 s cadence, so the `raw_trades` loss is on the order of ten
thousand rows — an estimate, not a count. The `dropped_rows` counters that
would have given the count are process-lifetime and were reset by the
14:45 reload before anyone read them. `market_history` lost 717 ticker
snapshots outright (the function swallows and logs). Every REST call that
needed a new socket failed for as long as the process sat at the limit,
which is why `auto_apply` and `signal_resolution` crashed and
`milestone_scan` failed DNS. In the data-plane rule's terms: completeness,
timeliness, and fidelity all failed silently for 6.8 hours, and the
monitoring system that is supposed to measure them was itself a victim.

**A second, more severe fault class was also firing and is not explained by
fd exhaustion alone.** Alongside the "unable to open" `OperationalError`
faults, `market_history.record_snapshot_from_ticker` also raised
`DatabaseError: database disk image is malformed` — 45 occurrences, last
seen 2026-09-02 20:44:11 UTC, found by this document's adversarial review
querying `/api/health/faults?component=market_history` fresh (not in the
original sweep, because the original sweep only grepped the container log
for the `OperationalError` string). `"database disk image is malformed"`
is SQLite's on-disk corruption signature — it means a page failed its own
integrity check, not merely that `open(2)` failed. Whether it is a
consequence of the fd-exhaustion incident (a write truncated by a failed
open, or two processes racing a partial WAL checkpoint under file-handle
pressure) or a separate mechanism is not established here; either way it
changes the risk framing of Tier 0 item 1 from "rows dropped, capacity
regained on restart" to "possible unrecoverable page-level corruption on
`market_history.db`" and argues for an integrity check (`PRAGMA
integrity_check`) on that file before any further write activity, not
after.

**Why nothing surfaced it.** `fault_log` has the rows (1,042
`error`-severity faults in the trailing 24 h per
`/api/health/pipeline.faults_last_24h`, `market_history` 723 of them) and
`/api/health/faults` shows them — but nothing reads that endpoint unless a
person does. `tools/soak_analyzer.py` was not run today. `services/alerting`
was not audited by either pass; whether a burst of 1,042 error faults is an
alert condition there is an open question (§9). The observability layer
went blind at the same instant. At least three Claude sessions and the repository owner were active on this repository between 08:24 and 18:10 UTC (PRs
#429–#438 merged in that window), and none of `docs/next-action.md`,
`docs/open-decisions.md`, the open issues, or the first audit's PR-stage
review (which probed `/api/quality/summary` at ~15:00Z, mid-incident, and
recorded it "still slow, if anything worse") mentions it. This is the
"fail silently; measure them; never infer health from the absence of
errors" case the data-plane HARD RULE was written for, and it happened on
the day the rule's biggest audit was being reviewed.

**Mechanism — what is established and what is not.**

- *Established (log):* the process reached `RLIMIT_NOFILE` = 1024. SQLite
  reports a failed `open(2)` as `SQLITE_CANTOPEN` ("unable to open database
  file") and falls back to read-only when it cannot open the `-shm`/`-wal`
  files, which is the "attempt to write a readonly database" row.
- *Established (source + census):* the app's dominant persistence idiom is
  a fresh `sqlite3.connect()` per call — 167 `with _connect(` sites across
  `services/` and `main.py`, 67 `sqlite3.connect(` calls, 30 modules
  defining their own `_connect()`. `sqlite3.Connection`'s context manager
  commits or rolls back; **it does not close**. Zero sites use
  `contextlib.closing`; 13 call `conn.close()` explicitly; **26 of the 30
  `_connect()`-owning modules never call `close()` anywhere.** Closing is
  left to CPython reference counting, which is prompt only when no cycle
  keeps the connection alive. The census shows hundreds of read-write
  handles held open on three files in a 35-minute-old process, with the
  per-file counts stable across samples and the total swinging by ~190 —
  the shape of connections that pile up until something (the cyclic
  garbage collector, on its own schedule) releases them.
- *Not established:* which reference path keeps them alive, and which call
  sites contribute. Candidates, in the order worth testing: (1) exception
  tracebacks captured under load (`except Exception as exc:` blocks whose
  frames own a `conn`, alive until the cyclic GC runs — the burst-under-
  stall shape fits: lock errors during a stall create the cycles that hold
  the handles that cause EMFILE, which creates more exceptions); (2)
  cursors/row iterators left unexhausted in generator-shaped readers; (3)
  the three hand-rolled connection caches (`_scoring_pool`'s thread-local
  cache, `_aio_db`'s per-loop cache, `tick_executor`'s unused one) holding
  more than one connection per thread per file across 26 threads.
  `title_cache.db`'s 5 → ≥40 growth in ten minutes, from a module with
five `with _connect()` sites sharing the same non-closing `_connect()`
(`services/title_cache.py:139,157,182,220,282`; line 139,
`load_market_titles`, is the one on the read path most call volume goes
through) is the smallest reproducer.
- *Falsifiers (cheap, in order):* `gc.collect()` from a diagnostic hook and
  re-census — if the data-file handles drop to ~3 per file, it is (1) or
  (2); wrap `title_cache.py:139` in `contextlib.closing` and watch that
  file's handle count; add `len(os.listdir('/proc/self/fd'))` to
  `/api/health/pipeline` (one syscall per capture; measure it) and a
  `fault_log` row at 80% of `RLIMIT_NOFILE` so the next approach to the
  limit is visible before it is an outage.

**Recurrence, observed live during this pass (not inferred).** The
current worker (pid 9544, started 17:32:06Z) was re-censused three more
times: 17:57Z (fd), then 20:08:34Z — **926 of 1024 descriptors in use**, 903
on `data/*.db` (`market_history.db` 181, `title_cache.db` 148,
`market_catalog.db` 118, `title_cache.db-wal` 115, `market_history.db-wal`
111, `signal_log.db` 81) — then 20:09:48Z at 665 (a drop of 261 in 74
seconds with no restart, the shape of a garbage-collection sweep). The
current worker's own log shows 21 fresh `unable to open database file`
lines clustered at 20:06–20:08Z (5 `raw_trades`, 4 `rejection_events`, 4
`rejected_candidates` capture-writer drops; `market_catalog.scan_batch`,
`trading_loop.run`, and `scheduler.auto_apply` crashes), i.e. **the same
incident recurred, at the descriptor ceiling, 154–156 minutes into this
one process's life** — not a one-off tied to this morning's specific
conditions. On this evidence alone the recurrence period looked like it might be on
the order of 2.5 hours per worker lifetime. **A fourth data point, from
this document's own adversarial review (§ Appendix, review cycle), directly
contradicts a fixed-clock reading**: the same worker, live-censused again
at 215.5 minutes (3.6 h) of uptime — past the 2.5 h mark — held only 223
total fds (98 on `signal_log.db`, 73 on `market_history.db`), nowhere near
the ceiling, despite having just served two heavy diagnostic queries of
the reviewer's own. Two conclusions replace the wall-clock-period claim:
(1) the growth is coupled to *request volume against the leaking modules*
(diagnostics/observability calls, which open many connections per call,
including this document's own probing and the review's), not to elapsed
time on its own — consistent with a leak whose rate is per-call, which is
exactly what §4.1's mechanism candidates already predict; (2) the ambient
recurrence rate under **normal** trading-only load (no diagnostic
probing) is not established by any sample gathered so far, and should not
be assumed from either the ~2.5 h or the 3.6 h-clean data point alone.
This raises Tier 0 item 1's urgency on mechanism grounds (a leak that
scales with request volume gets worse exactly when someone is
investigating it) without supporting a specific hours-until-recurrence
number; it does not change the fix (§4.1's falsifiers, §6.1's persistence
module).

**Why this is also an architecture finding, not only a bug.** The first
audit correctly identified "142 `with _connect() as conn:` call sites open
a fresh connection per call" as a cost (548 µs each) and proposed
consolidating the three connection caches. It did not identify connection
*lifetime* as a risk, and it explicitly declined a shared `_connect()`
because the rule forbade shared persistence code. Thirty independent copies
of an idiom that does not close is the DRY failure the owner's kickoff
message was pointing at, with a measured consequence.

### 4.2 `GET /api/health/faults?hours=` — the label does not mean what three readers took it to mean

`services/diagnostics/routes.py:489-496`:

```python
@router.get("/api/health/faults")
async def get_faults(limit: int = 50, component: str | None = None, hours: float = 24.0):
    return {"summary": fl.summary(since_ts=time.time() - hours * 3600),
            "faults": fl.recent(limit=limit, component=component)}
```

`hours` scopes `summary` only; `faults` is `fault_log.recent()` — lifetime,
ordered by `last_seen DESC`, no `since_ts`. A `?component=X&hours=24` query
therefore returns every fault ever recorded for X (within `limit`), each
carrying its lifetime `count`. The first audit's adversarial reviewer read
the `raw_trades` row (`count` 237, `last_seen` 2026-08-30 16:08Z) from
exactly this query and reported "237 error-severity faults in the last 24h";
the audit adopted it as Tier-1 #3 and as the strongest leg of its DuckDB
recommendation; `docs/next-action.md` repeats it. This pass made the same
query and caught it only by decoding `last_seen`. The route is the second
of the six "start investigations here" endpoints in `CLAUDE.md`. A
diagnostic whose parameter silently applies to half its response is a
data-plane accuracy defect by the rule's own definition and a one-line fix
(pass `since_ts` to `recent()`, or rename the parameter to say what it
scopes). `services/fault_log.py:148-166` already supports `since_ts` on
`recent()`.

### 4.3 The stalls are unattributed, and the container cannot attribute them from outside

`loop_watchdog` samples the loop every 0.1 s and records stall magnitude
and count per window (PR #417). It records nothing about *what* the loop was
doing. `py-spy` is not installed in the `fastapi` container; even if it
were, `docker exec` there runs with `CapEff: 0000000000000000` and
`ptrace_scope` 1, so no external profiler can attach. The first audit's
suggestion — `loop.slow_callback_duration` — only fires with asyncio debug
mode on, which instruments every callback and is exactly the kind of
hot-path cost the data-plane rule says to measure before shipping.

The cheap in-process alternative: the watchdog thread already knows when
the loop has been silent for N seconds; at that moment `sys._current_frames()[main_thread_id]`
is the main thread's stack, and `traceback.format_stack(frame)` on it costs
microseconds and runs only on the stall path, never on the hot path. One
stack per stall window, stored in the existing `fault_log` row's
`first_traceback` slot, would have answered "what is blocking the loop for
80 seconds" tonight with no benchmark needed. This is the single most
valuable diagnostic missing from the app, and it should precede any
capacity or topology change aimed at the stalls (data-plane rule: identify
the bottleneck and its mechanism first).

What this pass *can* say about candidates, from source, without claiming
attribution: `market_history.record_snapshot_from_ticker` is called
synchronously from `async def _process_stream_ticker`
(`services/whale_stream/whale_stream_handlers.py:251,334`) and does a
`with _connect(DB_PATH)` SQLite write per throttled ticker message on the
event loop, with the default 5 s busy timeout — the same shape PR #414
removed from six other sites, not from this one; it recorded 717 + 5 + 1
faults today. `market_catalog.scan_batch` and the hourly prune are the
other on-loop SQLite users the first audit named. None of this is
measured; all of it is what a stack capture would settle in one night.

### 4.4 `config/settings.yaml`: the third data-wipe of the same shape is sitting uncommitted in the working tree

`git diff config/settings.yaml` in the primary checkout, re-checked during
this document's adversarial review (01:08 UTC, 2026-09-03 — later than
this section's own authoring, and the working tree may have changed
between the two reads; `stat`'s `mtime` for the file, 2026-09-02 20:48:07
UTC, sits after this section's last fd census but the exact authoring
instant of §4.4 is not separately timestamped, so which changes were
present when this paragraph was first written cannot be reconstructed),
now shows **four** changes, not two: `kalshi.markets_watchlist_mode: merge
→ exclusive`; `kalshi.max_children_per_parent: 5 → 0`; `kalshi.categories`
narrowed from ten entries (Sports, Crypto, Climate and Weather,
Entertainment, Economics, Politics, Mentions, Commodities, Financials,
Science and Technology, Elections) to two (Crypto, Commodities); and the
deletion of the 24-line calibration-audit comment block that sat between `whale_watcher_kalshi.min_contracts_by_series`'s
last entry and `whale_confidence_weights:` (HEAD lines 182–205) — the
same comment PR #389 restored on 2026-09-01 after the previous wipe.

Mechanism, from source: the Controls panel's Save builds its patch with
`min_contracts_by_series: Object.fromEntries(...)` as a **new plain object**
from the text field and POSTs every section it renders
(`frontend/src/js/config-panel.js:196-225`); `ConfigStore.update()`
shallow-merges with `self._data[key].update(value)`
(`services/config/config_store.py:154-160`), which replaces the ruamel
`CommentedMap` node carrying the comment with the incoming plain dict. The
comment is attached to the node, so it dies at dump. This is consistent
with the diff and with PR #389's "silently wiped `min_contracts_by_series`
and `strategy_overrides.by_category` to `{}`" (same code path, empty
field); the exact ruamel attachment point was not verified by experiment —
falsifier: replay the panel's POST body against a copy of the file.

**This diff is no longer only a documentation-loss story.** §4.7 below
records a live, currently-ongoing `markets_watched: 0` completeness
failure found by this document's adversarial review. `exclusive` mode
takes the pinned watchlist and skips category-based discovery entirely
(`services/market_watch/market_fetch.py:100-108` sets `markets =
pinned_markets`; the `max_children_per_parent`-consuming round-robin
branch in `services/market_watch/selection.py:126-127` is not reached
under `exclusive`), so the `categories` narrowing should not by itself
explain zero watched markets — but the coincidence of an uncommitted
scope-narrowing config change and a live zero-markets condition, on a
file (`market_catalog.db`) that is also faulting with "unable to open
database file" (§4.1), is close enough that it needs checking together,
not treated as two unrelated findings.

Incident history of this one hand-rolled component: 2026-08-23 (PyYAML
dump wiped every comment → migrated to ruamel round-trip), 2026-09-01 (PR
#389: two nested sections wiped to `{}` by the same shallow merge), today
(comment block wiped by the same shallow merge). The first audit's §9.2 #3
(the apply-suggestion block duplicated 6×) is downstream of this store; the
store itself was not in the first audit. The fix is not a fourth patch to
`update()`: documentation does not belong in a file a machine rewrites, and
a config layer that survives its own UI needs a schema (pydantic model with
field descriptions, the YAML holding values only) and a real merge
(deep-merge or per-field PATCH), both of which are proven-library territory
under the 2026-08-30 rule (§6.3).

### 4.5 `record_snapshot_from_ticker`: a synchronous SQLite write per ticker message, on the event loop

Covered in §4.3 as a stall candidate; recorded here as a finding in its own
right because it is the same bug class PR #414 closed elsewhere ("6
functions ... no longer call SQLite `flush()` inline, synchronously,
unawaited, from `async def` functions on the event loop") and it was not in
that PR's scope or the first audit. It also has the per-call connection
shape of §4.1 and contributed 723 faults today. Measure its per-call cost
under lock contention before moving it (the data-plane rule), but the
direction is not in doubt: the ticker stream is the hot path.

### 4.6 The monitor is subject to the stalls it measures

`observability.maybe_capture()` runs on the event loop. When the loop is
stalled it does not capture; when it cannot open `observability.db` it
records nothing and raises nothing (0 `observability` error lines in the
log across a 6.8-hour write outage). The 24 h sample series has seven gaps
longer than 15 minutes (§3.2), and the per-hour sample counts fall to 1–6
in the hours when §3.4's queue-depth and drop metrics are worst. Two
consequences for every number in both audits: stall statistics are
*undercounted* precisely when stalls are worst (the first audit's "single-
window subset" method inherits this bias and says so implicitly), and a
capture-stream gap is currently indistinguishable from a healthy quiet
period. The fix is a data-plane measurement fix, not a feature: persist
captures off the loop it measures, and emit a `fault_log` row whenever the
gap between captures exceeds a multiple of the cadence. `loop_watchdog`
already runs on its own thread; the capture path should too.

### 4.7 Live, ongoing at time of review: `markets_watched: 0`, a 44-minute tick, and a system that is not recovering on its own

Found by this document's own adversarial review (2026-09-03, 00:50–01:30
UTC — roughly seven hours after §3's research window), independently
confirmed by this document's author at 01:26:31 UTC with a fresh probe
returning the identical `last_tick_duration_sec` value ten minutes later
(evidence the tick loop has not completed a new cycle since, not that the
field is merely large):

| Field | §3's research window (~18:00Z) | Adversarial review / re-confirmed (01:16–01:26Z, 2026-09-03) |
|---|---|---|
| `last_tick_duration_sec` | 11.99 s | **2,642.07 s (44.0 min)**, unchanged across two probes 10 min apart |
| `markets_watched` | not sampled at this call | **0** |
| `open_position_oldest_age_sec` | 70.2 s | 2,784.5 s → 3,406.0 s (rising) |
| `stale_over_300s_count` | 0 of 67 | 45 of 46 |
| `ingest.queue_wait.window` avg/max | 28.97 s / 92.5 s | 47.49 s / 244.53 s |
| `handler_timeouts_total` (trade class) | not sampled | 274 |

Every other route probed during the review (`/api/observability/history`
×2, `/api/health/faults?component=loop_watchdog`, `/api/state`) timed out
at 63 s; this document's author's own follow-up probes of
`/api/health/faults?component=market_catalog` and `?component=market_history`
both timed out at 63 s as well (01:26–01:28Z) and were not retried a third
time, consistent with the data-plane rule's own caution against adding
load to a system already this degraded.

**`markets_watched: 0` is a 100% completeness failure on the app's primary
discovery path**, worse in kind than anything §2–§4.6 measured (those are
latency and partial-loss findings; this is total). It was chased one layer
into source by the adversarial review: `exclusive` watchlist mode
(§4.4's uncommitted config change) sets `markets = pinned_markets` and
bypasses the `max_children_per_parent`-consuming discovery branch
entirely, so neither of §4.4's two newly-found config changes obviously
composes into zero by that code path alone. It was not chased further —
correctly, per the reviewer's own judgment that continued probing of an
already-failing system past a reasonable review budget is not the right
use of either review cycle. Two live, uneliminated hypotheses, in the
order worth checking first: (1) `market_catalog.open_markets_for_series()`
— the function `exclusive` mode's pinned-markets resolution still depends
on — is itself failing, consistent with `market_catalog.db`'s own "unable
to open database file" faults (§4.1) and the possibility that the same
corruption class found on `market_history.db` (§4.1's new finding above)
extends to it, unchecked; (2) the pinned watchlist genuinely has no
currently-open markets at this moment (benign, transient, and testable by
re-reading `markets_watched` a few minutes apart without any other
change). This document does not resolve which; it is Tier 0's new top
item (§8), ahead of the fd leak, because it is total and current rather
than intermittent.

---

## 5. Corrections to the first audit

Each item: the first audit's claim → the evidence → the corrected claim →
what it changes in the first audit's §13 plan.

- **C1 — "237 `error`-severity `raw_trades` `flush` faults in the last 24h
  that drop rows outright" (§1, §5.2, §12, §14, Tier-1 #3).** Lifetime
  row; `first_seen` 2026-08-27 20:22Z, `last_seen` 2026-08-30 16:08Z; the
  next fault on the same component (`rejected_candidates` retain-on-lock)
  begins 2026-08-30 16:19Z, eleven minutes later — the deploy boundary of
  commit `13680e5` ("capture_writer retains a batch on `database is locked`
  instead of dropping it (#211)", authored 2026-08-30). `capture_writer.py`'s
  own docstring (lines 22-25) already records "227 `database is locked`
  faults since 2026-08-27, every one on raw_trades ... 460 raw_trades rows
  lost" as the *pre-fix* history. Corrected: the row-dropping lock defect
  was real, was fixed on 2026-08-30, and has not recurred; the current
  lock path retains. **Tier-1 #3 is closed, not open.** Today's row drops
  (§4.1) are a different path (non-lock error) with a different cause.
- **C2 — "`capture_writer.py`'s `flush` path on a lock drops the rows
  outright (`_dropped_counts[store] += len(rows)`)" (§5.2).** True of the
  code before `13680e5`; false of `main` since then (lines 393–409: lock →
  `_retain`, `warn`; other errors → drop, `error`). Corrected as above.
- **C3 — "the frontend polls those routes on a fixed 6-second timer
  regardless of cost, so both problems compound" (§1, §4.2, §6.2).** The
  polling-timeout problem and the loop-stall problem are distinct: §3.3
  shows the 504s switch off with the tab's cadence; §3.2 shows the stalls
  do not. Corrected: de-polling is the fix for issue #410's symptom (route
  timeouts, pool pinning) and is still Tier 1; it is not the fix for the
  stalls, whose cause is unattributed (§4.3). The plan gains a stall-
  attribution item ahead of any stall "fix".
- **C4 — "this document does not have direct evidence a browser tab was
  open ... the cheap falsifier ... was not run" (§1, §6.2).** Run here: a
  tab was open on the History tab throughout the monitor, background-
  throttled to ~1 fetch/min per route (§3.3). Corrected: the monitor's
  numbers were taken under a throttled History tab plus the monitor's own
  `/api/quality/summary` every 20 s. The "dominant cause of tonight's
  numbers" hypothesis is *not* supported for the stalls and *is* supported
  for the 504s.
- **C5 — "≈855 KB/s (~6.8 Mbit/s) sustained per open browser tab" (§2.4).**
  The 5.13–5.38 MB is the uncompressed body; nginx gzips it to ~0.96 MB
  (§3.6). Corrected: ~160 KB/s on the wire at the 6 s cadence. The
  serialization-on-the-loop argument for scoping `event_live_data` stands;
  the bandwidth argument is ~5× weaker.
- **C6 — `title_cache.py`'s "final design has ... zero DB access" (§7,
  §8.2).** The module still defines `DB_PATH`, `_connect()`, and one
  `with _connect()` site (`services/title_cache.py:41,56,139`), and the live
  worker held at least 40 read-write handles on `title_cache.db` at 38
  minutes of uptime (sample capped at 40). Corrected: the *lookup* path may be DB-free (not re-verified
  here); the module is not, and it is the smallest reproducer for §4.1.
- **C7 — "Runtime dependencies are deliberately minimal (`fastapi`,
  `uvicorn`, `httpx`, `pydantic`, `aiosqlite`, `websockets`, `anthropic`)"
  (§8).** `requirements.txt` pins 14: those seven plus `pyyaml`,
  `ruamel.yaml`, `python-dotenv`, `cryptography`, `itsdangerous`,
  `kalshi-python-async`, `urllib3`. Corrected count; the "every pin carries
  a written justification" claim was not re-checked. The premise that the
  app avoids libraries is weaker than stated — it already carries a YAML
  round-trip library (and still loses comments), an official Kalshi SDK,
  and a crypto stack.
- **C8 — §2.2's monitor as evidence of `/api/quality/summary`'s cost.** The
  monitor called that route every 20 s while each call took 6.5–13 s on a
  single shared `_aio_db` connection per file, i.e. it queued behind
  itself for part of every interval. The per-call floor (≥6.5 s, three
  60 s-budget probes at 13.7–37 s, today's single probe 13.72 s) stands;
  the 37.5% client-timeout rate includes self-load and should not be
  quoted as a property of the route alone.
- **C9 — DuckDB's justification (§5.2, Tier-2 #9).** The first audit's own
  words: the lock-contention finding "remains the strongest argument for
  this migration even without the scan-speed estimate." That argument is C1
  and is gone. What remains is an unmeasured scan/compression estimate and
  a real 27–29 GB file. Corrected: DuckDB moves from Tier 2 to "benchmark
  before deciding" (§8), and the second lock-contention set on
  `candidate_log.db` (163 retain-on-lock warnings, live and current) is
  the one that still needs a root cause.
- **C10 — the first audit's process claim "the rigor is present and
  demonstrably working."** Partly. The same cycle that caught 9 false claims
  introduced one (C1, from the adversarial review, accepted at consolidation
  without re-derivation), dismissed a true out-of-band message about the
  rules, and reviewed a PR at ~15:00Z without noticing the incident the app
  was in at that moment. §7 treats this as a process finding with a
  concrete rule change, not as a reason to trust the cycle less.

---

## 6. Re-derivation under the current rule set, section by section

### 6.1 §5 — SQLite fitness, without the persistence idiom rule

The first audit's per-concern verdict was built inside three constraints
that no longer exist: one file per concern, no shared persistence code, no
ORM. Removing them does not change the *measured* facts (32 files, WAL
everywhere, 27–29 GB `raw_trades`, 3.6 GB `candidate_log.db`, the
contention set); it changes which solutions were allowed to be compared.
Re-derived:

**First, a persistence layer — one module, not thirty.** The single
highest-value data-layer change is now the one the first audit ruled out:
`services/db.py` (name illustrative) owning `connect(path)` as a context
manager that **closes**, the WAL and `busy_timeout` pragmas as one policy
instead of 30 divergent copies, `add_column_if_missing` once instead of 11
AST-identical copies, and a per-table DDL registry so `raw_trades`,
`rejection_events`, `rejected_candidates` each have one definition instead
of 2–3 (the first audit's §9.1 finding, now without a rule to hide behind).
This directly closes §4.1's leak class (connection lifetime becomes a
property of one function), the 548 µs-per-connect cost the first audit
measured, and the "fourth connection cache being reinvented" pattern it
named. It is a bounded rewrite of one layer: 30 modules change their
import, not their logic. It is the precondition for any engine decision,
because with one module you can measure, swap, and pool per table; with
thirty you cannot.

**ORM / migrations — evaluated for the first time, and mostly declined.**
A full ORM (SQLAlchemy ORM, SQLModel) buys nothing on the trading hot path
this app has — the queries are simple, the schemas are flat, and the
measured costs are connection churn and lock hold time, neither of which an
ORM improves and both of which its session lifecycle can worsen. SQLAlchemy
*Core* as a connection/pool manager is a reasonable implementation of the
shared module above (it closes, it pools, it is the proven answer to
exactly §4.1), and is worth prototyping against the hand-rolled version on
the same fd census; not a foregone conclusion either way. Alembic-style
migrations are heavy for 32 files with additive-only history and are
declined *unless* the DDL registry above turns out to need versioning —
revisit then, not now.

**Engine, per table, on merits:**

- *Hot, small, per-trade or human cadence (`paper_broker`, `risk_state`,
  `accounts`, `config_performance`, and the ~14 files under 1 MB):* SQLite,
  unchanged. The first audit's reasoning here did not depend on the rule
  and stands. With the shared-DB prohibition gone, **consolidating the
  small files into one** is now an option: fewer descriptors (each open
  file is 3 fds per connection), one backup unit, one WAL. Cost: one write
  lock shared across concerns that today never contend. Verdict: not until
  the persistence module exists to measure it; listed as a design question
  (§9), not a recommendation.
- *`candidate_log.db` / `signal_log.db` / `market_history.db` (growing,
  polled, the live lock-contention set):* retention plus bounded queries,
  as the first audit says — and now also **connection reuse from the shared
  module**, since these three are the files the census shows hundreds of
  leaked handles on. Postgres was declined by the first audit on mechanism
  grounds (contention is inside one process, a network hop on the broker's
  synchronous path); that reasoning is unchanged by the rule removal.
- *`raw_trades` (append-only, analytics-only, 27–29 GB):* DuckDB's
  strongest argument was C1 and is gone. What remains is real but
  unmeasured (columnar scan and compression on this specific file). Verdict
  changes from "Tier 2, highest-leverage data-layer change" to "**benchmark
  first**: a one-off read of the live file into a DuckDB file, timed
  against the three analytics queries that actually read `raw_trades`
  today; decide on the number." Parquet-on-disk with DuckDB as a query
  engine is the variant to include in that benchmark, since it removes a
  writer entirely rather than adding a second engine.
- *Postgres:* still declined as a default for the reasons the first audit
  gave. The rule removal opens one genuinely new case: if the two-process
  split (§4.4 of the first audit) lands and process B needs to *write*
  (advisory apply, config), a single multi-writer store is the honest
  answer and Postgres is it. Not before.

**`tools/quality_audit/persistence.py`** keeps its job: it guards
`PERSISTENCE_MODULE_PATHS` registration for test isolation, the 2026-08-23
test-contamination class. A shared persistence module makes its work easier
(one place to register), not obsolete.

### 6.2 §9 — DRY, without the rule

The first audit's §9 conclusion ("real but narrow, concentrated in the
config-apply/advisory path") was correct about *logic* duplication and
wrong about *idiom* duplication, because the rule made thirty `_connect()`
copies look like architecture rather than duplication. Re-derived:

- The three safety-adjacent findings (auto-apply ignoring `declined_ids`;
  `RiskManager` lacking `ShadowTrader`'s zero-bankroll guard; duplicated
  DDL) stand unchanged and are still Tier 1. The DDL one is now framed as
  "one definition per table in the registry" (§6.1), not as a rule
  conflict.
- **Add:** the `_connect()` idiom itself — 30 copies, 26 without a
  `close()`, one incident (§4.1). This is the largest DRY finding in either
  audit and it is a correctness finding.
- **Add:** `config_store.update()`'s shallow merge (§4.4) — one site, three
  incidents; the six duplicated apply-suggestion blocks the first audit
  found all route through it.
- Everything else in §9.2's table stands as ranked.

### 6.3 §8 — hand-rolled vs. proven, with the burden where the 2026-08-30 rule puts it

The first audit asked "is this hand-rolled component good?" and answered
yes seven times on design grounds. The rule in force since 2026-08-30 asks
the other question: what has this component's *run history* shown, and
what would a proven replacement have cost? Re-scored with incident counts
from `git log`, the two audits, and today:

| Subsystem | First audit | Run history | This pass |
|---|---|---|---|
| Per-call `sqlite3.connect()` idiom (30 copies) | not scored as a subsystem | 2026-08-11 WAL incident; 548 µs/connect; three caches invented to work around it; **today's EMFILE** | **Replace** with one shared module (§6.1); SQLAlchemy Core pool is the proven candidate to benchmark against |
| `services/config/config_store.py` | not audited | 2026-08-23 comment wipe; 2026-09-01 section wipe (PR #389); today's comment wipe | **Replace the contract**: pydantic schema + values-only YAML + deep-merge/PATCH; ruamel stays as the writer |
| `services/observability/` (~900 lines) | "narrow adopt": `prometheus_client` for instrumentation | 30× window/lifetime bug (first audit §8.3); the first audit's own `queue_wait` misread; this pass's `hours=` misread on the sibling faults API; **6.8 h self-blind** (§4.6) | **Raise to Tier 1**: standard-semantics instrumentation (`prometheus_client` types, still scraped into SQLite) *and* capture off the measured loop; three label misreads in 24 h by three careful readers is the run history |
| `services/http_client.py` rate limiter | keep | no incidents found; `waiters_high_water` telemetry used in real diagnoses | **Keep** |
| `services/fault_log.py` | keep | works (it is the only reason §4.1 is reconstructible); its *readers* are the problem | **Keep**; fix the route label (§4.2); give it a consumer (§6.5) |
| `series_cache` / `title_cache` | keep ("zero DB access") | `title_cache` still opens connections and holds ≥40 handles on its file (C6) | **Keep the design, fix the DB path** under the shared module |
| `task_supervisor` + `_maybe_*` scheduler | keep (restart-safe by persisted `due()`) | restarted three crashed schedulers today as designed; the backup cold-start bug (2026-08-23) was the one incident | **Keep** |
| `ws_manager.py` (40 lines) | keep | none | **Keep** |
| `loop_watchdog.py` | keep + `slow_callback_duration` | caught the stalls; cannot say what they are | **Keep, extend** with stack capture on stall (§4.3), not asyncio debug mode |
| `services/kalshi/websocket.py` reconnect loop | convert to `async for` + `process_exception` | 11,236 messages discarded on reconnect in a 40-minute process; 78,457 dropped in one window (§3.4) | **Keep the recommendation, raise its priority**: the reconnect-discard accounting is honest, and what it is honestly reporting is a completeness failure |
| Frontend (vanilla, 13 flat files) | Preact migration, already decided | the dashboard's own polling is one of two live degradation mechanisms | **Unchanged** (Tier 2); the de-polling fix does not wait for it |
| Charts (hand-rolled OHLC) | uPlot / Lightweight-Charts later | none | **Unchanged** |

Net: three "keep" verdicts reverse, one is raised in priority, five stand.
The pattern in the reversals is the same each time: the hand-rolled version
was defensible on paper and its run history is what a proven library would
have prevented (connection lifetime, comment-preserving merges, metric
semantics everyone already knows).

### 6.4 §7 — caching

Unchanged. Nothing measured in this pass is a cache miss either; the
"connection reuse is the caching that would actually pay" line is now the
shared persistence module. Redis remains unjustified; the fd budget is a
new reason (a Redis client is more sockets in a process that ran out of
descriptors today).

### 6.5 §4 — trade-critical vs. app-facing decoupling

The two-process recommendation's mechanism (WAL readers in a second
address space) is unchanged by the rule removal and is reinforced by §4.1:
a second process gets its own 1,024 descriptors and its own event loop, so
a diagnostics route can neither pin the trading pool nor exhaust the
trading process's fd table. Two amendments:

- The tooling rule's removal means `tools/soak_analyzer.py`'s checks (the
  `BLIND`/`FAIL`/`UNKNOWN` verdicts the data-layer contract already
  defines) can run inside process B on a schedule and write `fault_log`
  rows — which turns the app's best existing detector into an alerting
  path. `services/alerting/alerting.py`'s `check_and_alert` (`:220-256`) monitors
  exactly three edge-detected conditions — kill switch, trade-stream
  connectivity, index-stream connectivity — plus crash-alert expiry; it
  writes its own dispatch failures to `fault_log` and never reads a fault
  row, so today's 1,042 error-severity faults had no path to a human. This is the cheapest alerting the repo can have and it
  was forbidden until this morning.
- The route-by-route `state`-read verification and the CI/deployment
  surface the first audit flagged as unscoped stay unscoped here; the
  disabled `data/*.db` guards add "a read-only process must open its files
  `mode=ro` and the plan must prove it" to that list.

### 6.6 §6 — polling

Split into what the evidence now supports:

- **Tier 1, unchanged:** stop the History-tab loaders and the Terminal-tab
  `/api/quality/summary` fetch from firing on the 6 s timer. §3.3 is the
  measurement: it removes the 504 bursts and the pool pinning.
- **Not a stall fix:** §3.2. Do not expect the loop stalls to change when
  the polling does; if they do, that is a finding worth recording, not an
  assumption worth making.
- `event_live_data` scoping: keep, on serialization grounds; the bandwidth
  claim is corrected (C5).
- The ETag/304 finding stands (unchanged code, unchanged mechanism).
- The backend WS-vs-REST gaps (signal-resolution poll, `orderbook_delta`,
  discarded lifecycle events) stand unchanged; none is rule-dependent.

### 6.7 §3 — strategy: edge, not confidence

Unchanged. Nothing in §3 depended on a rule that changed, and nothing
measured here touches its argument. One operational note: today's
uncommitted `markets_watchlist_mode: exclusive` (§4.4) narrows the trade
subscription to the 16-series watchlist if it is saved; the first audit's
§6.1 and the standing "watchlist is the coverage bottleneck" finding both
bear on that choice, and it deserves a deliberate decision rather than a
side effect of a Save click.

### 6.8 §12 — the rewrite question, restated

The first audit: "a bounded, sequenced remediation program against
specific, measured problems." This pass: the same, with one of the
problems being a *layer*, not a site. The persistence layer — connection
lifetime, schema ownership, pragma policy, the on-loop sync writes that
survived PR #414, and the diagnostics that describe all of it — needs to
be rewritten as one unit under one design, because the failure today was
the *idiom* replicated thirty times, and thirty patches would replicate
the next idiom too. That is a rewrite of perhaps 1,500 of 40,000 lines,
done once, with the rest of the plan sequenced behind it. It is not a
rewrite of the application, and the evidence against a full rewrite (§12
of the first audit) is unchanged: the domain logic, the safety stack, the
Kalshi boundary, and the overall process shape are not where the incidents
are.

---

## 7. Process findings

Recorded because the request was explicitly about how the rules are
understood, and because the first audit's own review cycle both caught
real errors and produced one of the errors this pass corrects.

- **P1 — Reviewer-introduced claims got a lower evidence bar than author
  claims.** The "nothing advances on one pass" rule requires the
  adversarial review to re-derive the *artifact's* claims from primary
  sources. The C1 finding entered the artifact *from* the review, at
  consolidation, on the strength of one API response whose label was
  misleading (§4.2), and nothing in the rule asked the consolidation step
  to re-derive it. Suggested amendment (a rule change; in scope for its own
  review cycle, not made here): a finding that first appears in a review is
  a new claim and meets the same standard before it enters the artifact —
  at minimum, the reviewer states the primary source and the author reads
  it, not the reviewer's summary of it.
- **P2 — "Verify or falsify" was executed against a stale ref.** The first
  audit's session checked `git diff origin/main -- CLAUDE.md` and found no
  rule removal; PR #429 had merged 6.5 hours earlier. The check was right
  in form and wrong in execution: without a fresh `git fetch origin main`,
  `origin/main` is whatever the last fetch left. The
  `never-infer-integration-branch-from-local-log` memory already records
  this class; the general form is "a claim about `main` starts with a
  fetch."
- **P3 — A named cheap falsifier was published instead of run.** §6.2 of
  the first audit named "re-run the monitor with zero browser tabs open, or
  confirm no tab was open" as the cheap falsifier and carried it as an
  open item. One `tail` of the nginx access log answered it (§3.3). The
  never-guess rule's "when a check is cheap, run it instead of reasoning
  about it" applies to falsifiers the author names themselves.
- **P4 — Diagnostics labels are data-plane surface, and three careful
  readers misread one in 24 hours.** `hours=` (§4.2), `queue_wait` lifetime
  vs. window (the first audit's own correction O3), and `count` as a
  window quantity (C1) are the same failure: a hand-rolled diagnostic whose
  semantics live in the reader's head. The fix is partly code (§4.2) and
  partly the §6.3 observability verdict; the process form is that an
  investigation that quotes a diagnostic number states what the field
  measures, read from the route or the store, not from its name.
- **P5 — The rigor was pointed at documents while the runtime was in an
  incident.** Between 2026-09-01 and now the repository gained roughly 30
  review, consolidation, and research documents under `docs/superpowers/`;
  no Tier-1 code item from the first audit has landed; and a 6.8-hour data-
  loss incident ran from 08:24 UTC with three sessions and the owner active
  and nothing reading the endpoint that recorded it. This is not an
  argument against the review rule — the rule caught nine false claims in
  the first audit and P1 is a fix to it, not a repeal. It is an argument
  that the app needs one automated consumer of its own faults (§6.5) more
  than it needs its next review document, and that "start investigations
  here" should be a scheduled check, not a reading assignment.
- **P6 — The first audit's process self-assessment ("the rigor is present
  and demonstrably working") is amended, not reversed.** It worked on the
  claims it re-derived and failed on the one it accepted, the one it
  dismissed, and the one it did not look for. That is what an honest run
  history of a process looks like, and it is the same standard §6.3 applies
  to code.

---

## 8. Revised prioritized plan

Ordered by measured payoff ÷ effort, superseding the first audit's §13 where
the two disagree; items are numbered against the first audit's numbering
where they correspond ("= #n"). Nothing here is executed by this document.
Every item that touches `data/*.db` now carries its own safety steps
(backup-first, dry-run, row-count reconciliation) because no hook will
catch a mistake (§2).

**Tier 0 — the live incident (hours, not days; `superpowers:systematic-debugging`):**

0. **`markets_watched: 0` (§4.7) — resolve first, it is total and current,
   not intermittent.** Re-read `/api/health/pipeline` a few minutes apart
   with no other change to rule out "genuinely nothing open right now";
   if it persists, run `PRAGMA integrity_check` on `market_catalog.db` and
   `market_history.db` (read-only; the second is also flagged for
   corruption below) before touching `exclusive`-mode config; only then
   consider whether the uncommitted `markets_watchlist_mode`/`categories`/
   `max_children_per_parent` changes (§4.4) should be reverted, and treat
   that as the config owner's call, not an automatic revert.
1. **Pin and fix the file-descriptor leak** (§4.1). Census → falsifiers in
   the order given → fix the lifetime at the idiom (a closing `connect()`
   context manager is the minimal form; the shared module in Tier 2 is the
   full form). Interim, in the same PR: fd count in `/api/health/pipeline`
   and a `fault_log` row at 80% of `RLIMIT_NOFILE`. Do not raise the
   container's `nofile` limit as the fix; it hides a leak that grows.
2. **Check `market_history.db` for corruption** (§4.1's new finding):
   `PRAGMA integrity_check` (read-only) before any further write activity
   on that file; if it reports damage, restore from `services/backup/backup.py`'s
   most recent pre-incident snapshot rather than continuing to write
   against a possibly-corrupt file. Sequence with item 0 above, not after.
3. **Fix `/api/health/faults`'s `hours` scoping** (§4.2) and re-read
   `docs/next-action.md`'s open item 3 against the corrected output.
4. **Reconcile today's loss** (§4.1): count the dropped batches from the
   log; decide whether the 08:24–14:45 UTC `raw_trades` window is worth
   backfilling from Kalshi's REST trade history (a human cost/value call —
   §9); record the incident where incidents are recorded.
5. **Give the faults a consumer** (§6.5): schedule `tools/soak_analyzer.py`'s
   checks in-process, or the smallest equivalent (`fault_log` error-count
   per component per hour against a threshold → `alerting.record_alert`).
   Today's incident had 1,042 error faults and zero alerts.
6. **Make the monitor survive the stall it measures** (§4.6): capture
   persistence off the loop; a fault row on capture gaps.

**Tier 1 — days, no new infrastructure (first audit's Tier 1, re-ordered):**

7. Stall attribution before any stall fix: stack capture from the watchdog
   thread on the stall path (§4.3). Cost: microseconds, only on stall.
8. = #1 De-poll the History-tab loaders and the Terminal-tab
   `/api/quality/summary` from the 6 s timer (§3.3). Expect the 504 bursts
   to stop; do not expect the stalls to.
9. = #2 The three safety-adjacent DRY fixes (auto-apply honoring
   `declined_ids`; `RiskManager`'s zero-bankroll guard; one DDL definition
   per table).
10. `record_snapshot_from_ticker` off the event loop (§4.5) — measure the
   per-call cost first, then move it the way PR #414 moved its siblings.
11. `config_store`: stop resending whole sections from the panel, deep-merge
    or per-field PATCH on the server, and move the calibration-audit
    documentation out of the machine-written file (§4.4). Restore the
    comment block a third time only after that, or it will be wiped a
    fourth.
12. = #3, #4 Bound `resolved_signals_with_factors()` and
    `population_gate_summary()`; one `paginate()` dependency for the
    unbounded `limit` routes.
13. = #6 Scope `event_live_data`; fix or retire the dead ETag (bandwidth
    argument corrected, serialization argument stands).
14. = #7 `alerting.py`'s three discarded task handles; `http_client.py`'s
    bare `httpx.AsyncClient()` — both are also fd-hygiene items now.

**Tier 2 — its own design/review cycle each:**

15. **The persistence module** (§6.1): `connect()` that closes, one pragma
    policy, the DDL registry, `add_column_if_missing` once; benchmark the
    hand-rolled form against SQLAlchemy Core's pool on the fd census and
    the 548 µs connect cost before choosing. Subsumes the first audit's
    "consolidate the three connection caches" and #5.
16. = #8 Finish the `aiosqlite` migration on top of (15), deleting
    `tick_executor`/`_scoring_pool` as callers drain — with the fd budget
    as a measured dimension (one aiosqlite connection per loop per file is
    also three descriptors per file).
17. = #14, raised: standard-semantics instrumentation (`prometheus_client`
    types, SQLite store kept) for `services/observability/` (§6.3).
18. = #15, raised: `services/kalshi/websocket.py` to the `async for`
    reconnect pattern with fatal-vs-retryable classification — 11,236
    messages discarded on reconnect in a 40-minute process is the
    priority argument.
19. = #10 `apply_suggestion()` extraction (after 11).
20. = #11, #12 The EV gate and markout measurement (unchanged; §6.7).
21. = #13 The Preact migration (unchanged; after 7).
22. = #16 Frontend DRY sweep (unchanged).
23. `raw_trades` engine: **benchmark, then decide** — DuckDB and Parquet+
    DuckDB against the queries that read the table today (C9). No longer
    justified by contention.
24. `candidate_log.db`'s 163 live retain-on-lock warnings: root-cause (the
    one lock-contention set that is current).

**Tier 3 — after Tier 1/2 land and are re-measured:**

25. = #17 The two-process split, now also an fd- and loop-isolation
    argument (§6.5); the read-only process runs the soak checks.
26. = #18 Push more state over the dashboard WebSocket.
27. = #19 `market_analyst_agent` cleanup; category fair-value anchors.
28. Consolidating the small SQLite files into one, if (15)'s measurements
    say the shared lock is free (§6.1, §9).

**Explicitly not recommended (unchanged from the first audit, plus):**
raising `ulimit -n` as a fix; a full ORM on the hot path; Alembic before
the DDL registry needs versioning; Redis (now also an fd argument); a
capacity or topology change aimed at the stalls before item 6 says what
they are.

---

## 9. Open questions and directives carried forward

The first audit's §14 list, updated. Resolved items are marked; new ones
are added in the same one-line-plus-owner form so they can move to
`docs/open-decisions.md` when a human picks them.

**Resolved by this pass:**

- "Was a browser tab open during the monitor?" — yes, the History tab,
  background-throttled to ~1 fetch/min (§3.3).
- "The 237 `raw_trades` flush faults that drop rows outright" — historical,
  fixed 2026-08-30 by `13680e5`, zero recurrence (C1). Closed.
- "`game_state.db`'s 7.3× 24 h size swing" — not re-examined here; but
  `game_state` dropped rows at 08:24Z today (§4.1), so its growth pattern
  now has a confounder. Still open, lower priority.

**Changed:**

- DuckDB go/no-go: no longer contention-justified; benchmark first (C9,
  §8 #22) · you (go/no-go on the number) · 2026-09-02.
- "Does removing the persistence-idiom rule change §5?" — yes: §6.1. The
  remaining human call is whether the persistence module is built by hand
  or on SQLAlchemy Core (a benchmark decides), and whether the small files
  consolidate (§8 #27) · you (design call) · 2026-09-02.

**New:**

- **`markets_watched: 0`, live and ongoing as of this document's own
  review cycle (§4.7)** — is it a genuinely empty pinned watchlist right
  now, or is `market_catalog.db`/`market_history.db` corruption (below)
  blocking discovery? Run the integrity checks (§8 Tier 0 #0, #2) before
  anything else · you or me, Tier 0 top item · 2026-09-03.
- **`market_history.db` shows a `DatabaseError: database disk image is
  malformed` fault (45 occurrences, last seen 20:44:11 UTC, §4.1)** —
  confirm via `PRAGMA integrity_check` whether the file is actually
  corrupt, and if so, restore from the most recent `services/backup/backup.py`
  snapshot rather than continue writing to it · you or me, Tier 0 · 2026-09-03.
- **`config/settings.yaml`'s primary-checkout working tree carries two more
  uncommitted changes than this document's §4.4 first recorded**
  (`max_children_per_parent: 5 → 0`, `categories` narrowed from ten
  entries to `Crypto`/`Commodities` only) — all four uncommitted changes
  need one decision together, not `markets_watchlist_mode` alone · you ·
  2026-09-03.
- **Backfill the 08:24–14:45 UTC `raw_trades` gap from Kalshi's REST trade
  history, or accept the hole?** The table is "never pruned by design";
  the loss is on the order of 10⁴ rows (estimate); the REST cost is
  bounded by the watchlist and the window · you (cost/value) · 2026-09-02.
- **`markets_watchlist_mode: exclusive` is uncommitted in the working tree
  alongside the wiped comment** (§4.4) — intended? It narrows subscription
  scope; the coverage-bottleneck finding argues the other way · you ·
  2026-09-02.
- **Which reference path holds the leaked connections** (§4.1 falsifiers) ·
  me or next session, Tier 0 · 2026-09-02.
- **What the 60–130 s stalls are** (§4.3) — answerable only after the
  stack-capture diagnostic exists · me or next session, Tier 1 #6 ·
  2026-09-02.
- **Alerting coverage**: `services/alerting/check_and_alert` monitors three
  conditions (kill switch, trade-stream and index-stream connectivity) and
  writes to `fault_log` but never reads it;
  should error-fault bursts be an alert category, or should `soak_analyzer`
  be the in-process consumer (§8 #4)? · you (design) · 2026-09-02.
- **P1's rule amendment** (reviewer-introduced claims meet the artifact's
  evidence standard before consolidation) — a `CLAUDE.md` change with its
  own review cycle · you (accept/decline) · 2026-09-02.
- **Config documentation home**: where do the calibration-audit notes live
  once the YAML is values-only (a schema's field descriptions, a
  `config/README.md`, the research doc they cite)? · you · 2026-09-02.
- **`tools/quality_audit`'s worktree scanning**, the stale 2026-08-27
  REST-vs-WS inventory, `ROADMAP.md`'s wrong ETag claim, the two-process
  write-proxying design, the route-by-route `state`-read verification, the
  frontend test tooling, the security/auth posture, category fair-value
  anchors, process B as a separate deployable — all unchanged from the
  first audit's §14; none touched here.

---

## Appendix — evidence log and review-cycle record

**Live probes (UTC, 2026-09-02):** 17:54:19 `/api/health/pipeline` (no
response in 30 s), `/api/health/storage` (no response in 30 s),
`/api/observability/summary` 200 in 2.63 s, `/api/quality/summary` 200 in
13.72 s; 17:56:39 `/api/health/pipeline` 200 in 36.7 s; 17:55–17:58
`/api/health/faults` for `capture_writer`, `loop_watchdog`, `exit_engine`,
`market_catalog`, `signal_resolution`, `market_history`;
`/api/observability/history` for `loop_watchdog.stall_max_ms`,
`trade_stream.dropped_messages`, `whale_pipeline.stage.receive_to_decision.window_max_ms`,
`trade_stream.ingest.queue_depth`, `index_stream.ingest.handler.index.window_max_ms`,
`whale_pipeline.stage.receive_to_handler_end.window_max_ms` (24 h, limit
1000); `/api/state` with `Accept-Encoding: gzip`.

**Logs:** nginx access log, `web` container, `tail -n 60000
/var/log/nginx/access.log` (23,814 lines, 23,722 `/api` rows, 2026-09-01
23:43 → 2026-09-02 12:58 local); nginx error stream via `ddev logs -s web`
(last 3,000 lines, 2,683 upstream timeouts + 256 client-closed);
`ddev logs -s fastapi` (32,080 lines, 04:42:57 → ~18:10 UTC).

**Container census (read-only):** `docker exec -i ddev-kalshi-whale-poc-fastapi sh`
— `ulimit -n`, `/proc/<pid>/fd`, `/proc/<pid>/fdinfo`, `/proc/<pid>/task`,
`df -h /app/data` (18% used, 791 GB free), `which py-spy`,
`/proc/sys/kernel/yama/ptrace_scope`, `CapEff`.

**Source reads (`main` at `f12bb46`):** `services/diagnostics/routes.py:489-496`;
`services/fault_log.py:148-224`; `services/capture_writer.py:18-70,348-412`;
`services/market_history.py:86-125,153-398`; `services/whale_stream/whale_stream_handlers.py:251-342`;
`services/config/config_store.py:1-60,154-180`; `frontend/src/js/config-panel.js:196-225,414-440`;
`services/title_cache.py:38-139`; `services/alerting/alerting.py`;
`tools/quality_audit/persistence.py`; `.claude/hooks/guard_workflow.py`,
`.claude/settings.json`, `CLAUDE.md`, `.claude/rules/*.md` at `999fcb9`
and `f12bb46`; `git log`/`git show` for `13680e5`, `3e274a5`, `10913b8`,
`09b2553`, `fe47956`; PR bodies #429, #430, #431, #433, #436.

**Counts:** `grep -rn 'with _connect(' services main.py` = 167 (the first
audit's 142 counted the narrower `with _connect() as conn:` form; the
difference is `_connect(DB_PATH)`-style calls, not a change in the code);
`'def _connect'` = 32 definitions in 30 files; `'conn.close()'` = 13;
`'closing('` = 0; files defining `_connect` with no `close()` = 26;
`'def _add_column_if_missing'` = 11; `'CREATE TABLE IF NOT EXISTS'` = 67;
`'sqlite3.connect('` = 67; `requirements.txt` runtime pins = 14;
`docs/superpowers/plans/2026-08-25-frontend-modularization.md` unchecked
tasks = 50, checked = 5 (unchanged since the first audit).

**Not done in this pass, on purpose:** no subagent research for the
document's own findings (the first audit's four-agent method was not
repeated for §1–§9; every claim there is firsthand) — the one subagent
used in this pass is the independent adversarial review itself (required
by the review cycle, and deliberately memory-less of this session); no
code, config, or data change; no `py-spy` install; no issue filed (the
request was for a document — §8 Tier 0 is where the incident goes next);
`services/backup`, `services/alerting`'s condition set, security/auth
posture, and the 9 unclassified SQLite files remain unaudited.

**Review-cycle record:** per the "nothing advances on one pass" HARD RULE,
this artifact went through self-review
(`docs/superpowers/research/2026-09-02-architecture-audit-second-pass-self-review.md`,
8 corrections applied before the next step) and an independent adversarial
review (a genuinely separate Agent call, no memory of this session,
re-deriving every checked claim from the live app, `docker exec`/`/proc`,
`git`/`gh`, and source —
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass-adversarial-review.md`):
verdict GO-AFTER-FIXES, every mechanical count in this Appendix reproduced
exactly, one factual error found and fixed (the PR #436 merge timestamp,
§2), one overstated claim found and softened (the "2.5-hour steady state"
recurrence framing, §4.1), and two new live findings surfaced and folded
in (the `market_history.db` corruption signature and the `markets_watched:
0` incident, §4.1/§4.7). The merged fix list and final GO are recorded in
the companion document,
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass-consolidation.md`.
