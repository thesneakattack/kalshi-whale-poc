# Persistence Layer Redesign — Adversarial Review

Independent adversarial review of `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md`
(commit `bb64a7c`, worktree `agent-ae61311892d35be18`, branch `worktree-agent-ae61311892d35be18`),
per CLAUDE.md's "nothing advances on one pass" HARD RULE. This review has no memory of the
session that wrote the artifact. Every load-bearing claim below was re-derived from primary
sources — live source files, the live app, and live `data/*.db` files (read-only) — not taken
from the artifact's own tables, prose, or self-review.

Reviewed 2026-09-03. Reviewer environment: `ddev-kalshi-whale-poc-fastapi` container
(Python 3.13.15), SQLAlchemy 2.0.52 and DuckDB 1.5.5 installed to a scratch
`PYTHONPATH=/tmp/bench_pylibs` target for this review only (matching the artifact's own stated
method and versions) — not added to `requirements.txt`. All queries against `data/series_watcher.db`,
`data/index_feed.db`, and `data/fault_log.db` used read-only `mode=ro` URIs; none of these files
were copied, moved, or written. No git destructive operations were run; no code from this design
was implemented; `config/settings.yaml` was not touched.

---

## Method

- Read the full 936-line artifact end to end (including its embedded self-review), plus
  `CLAUDE.md` and `.claude/rules/branching-and-ci.md` in full.
- Verified worktree/HEAD state: `git log -1`, `git merge-base --is-ancestor fffe972 HEAD`.
- Re-ran the artifact's own greps independently: `grep -rln "^def _connect" services/ main.py`,
  `grep -rn "tick_executor\.run(" services/ main.py`, `grep -rn "connection_for(" services/ main.py`.
  Diffed the artifact's claimed 30-file list against the fresh grep output (exact match required).
- Read all four `.close()`-containing files among the 25 non-Tier-0 modules in context
  (`game_state.py`, `backup/backup.py`, `market_events/event_schedule.py`, `research/research.py`).
- Read `services/whale_stream/decision_bridge.py` and `services/candidate_ledger.py` end to end
  around the cited call sites to verify the trading-critical-gating claim.
- Wrote and ran three generations of a connect-cost benchmark script (not the artifact's own,
  uncommitted script — an independent reproduction) against a throwaway scratch SQLite file
  inside the container, first without WAL mode, then with WAL mode applied per-call, then a
  clean single-process A/B/C run with WAL — tracking latency percentiles and `/proc/<pid>/fd`
  counts exactly as the artifact's methodology describes.
- Checked the container's `RLIMIT_NOFILE` (`ulimit -n`).
- Ran `EXPLAIN QUERY PLAN` for the exact SQL strings found in `services/series_watcher.py:498`
  and `:609-618` against the live `series_watcher.db` (`mode=ro`).
- Timed the same two query shapes three ways against the live file: native SQLite, DuckDB via
  `sqlite_scanner` (`ATTACH ... TYPE SQLITE, READ_ONLY`), and DuckDB's own `EXPLAIN` (not just
  wall-clock) to check the "no index pushdown" mechanism claim directly.
- Ran a scaled-down Parquet export (`KXNFLGAME`, 750,744 rows, ZSTD) instead of reproducing the
  full 26M-row/181s export, then timed queries against it vs. native SQLite for the same series.
- Sampled `LENGTH(raw_json)` across seven series (2,000-row `LIMIT` samples via the covering
  index, cheap) to directly test the compression-ratio's "uniform per-row bytes across series"
  assumption.
- Read `services/capture_writer.py` and `services/candidate_log.py` in full around the cited
  line numbers; independently, empirically measured Python's `sqlite3.connect()` default
  `timeout` by inducing a real lock and timing the raised exception (rather than trusting the
  documented default).
- Read `config/settings.yaml`'s `poll_interval_sec`/`safety_net_interval_sec`, then traced
  `main.py`'s `_tick_interval_sec()` and its actual callers/call chain up through `trading_loop()`,
  and cross-checked against the *live* app's `GET /api/state` (`trade_stream_status`) — not just
  the static config value — via `curl`.
- Queried the live `fault_log.db` (`mode=ro`) directly for the three rows the artifact cites,
  and independently recomputed the artifact's rate arithmetic (elapsed hours, occurrences/hour).
- Checked the artifact's "fixed by commit `13680e5`" claim: verified the commit exists, its
  author/committer date, its merge commit (`6d1a5e3`, PR #244), and that commit's timestamp
  against the fault log's own `last_seen`.
- Listed current `data/*.db` file sizes to check the "~14 files under 1MB" small-file-count claim.
- Verified the aiosqlite `ValueError` claim directly against the installed `aiosqlite==0.22.1`
  package source (`core.py`), not the artifact's citation of it.

---

## Findings

**1. 30-file `_connect()` census (§1.1) — CONFIRMED, exact.**
`grep -rln "^def _connect" services/ main.py` returns exactly 30 files. A full diff of the
artifact's named list against the fresh grep output is byte-identical (no missing or extra
files).

**2. "26/30 never close(), 4 of the remaining have unrelated close() calls" (§1.1) — CONFIRMED, exact.**
Of the 25 non-Tier-0 modules, exactly four (`game_state.py:375`, `backup/backup.py:158,160`,
`market_events/event_schedule.py:478`, `research/research.py:194`) contain any `.close(` token,
and reading each in context confirms none of them closes the module's own `_connect()`-returned
connection: `game_state.py:375` closes a separate autocommit VACUUM connection; `backup.py:158,160`
close the source/dest connections of `sqlite3.Connection.backup()`; `event_schedule.py:478`
closes an `httpx` client; `research.py:194` is inside a comment, not executable code. All four
characterizations match the artifact's text precisely.

**3. `tick_executor.run()` caller count and module list (§2.1) — CONFIRMED.**
Raw grep returns 20 lines; two are comments (`decision_bridge.py:57`, `index_stream_handlers.py:51`),
leaving 19 real call sites, spanning exactly the modules the artifact names
(`whale_calibration/routes.py` ×2, `analytics/routes.py` ×1, `settlement_resolver.py`,
`index_feed/backfill.py`, `whale_stream/index_stream_handlers.py` ×3, `index_feed/ingestion.py`,
`whale_stream/whale_stream_handlers.py`, `market_watch/event_metadata.py`,
`market_watch/live_status.py`, `main.py` ×4). "Well over a dozen" is accurate.

**4. `connection_for()` zero production callers (§2.1) — CONFIRMED.**
`grep -rn "connection_for("` outside `tick_executor.py` itself matches only
`_aio_db.connection_for()` call sites (a different function on a different module) and doc
comments explaining *why* `tick_executor.connection_for()` is unused. No production code calls
`tick_executor.connection_for()`.

**5. `candidate_ledger.claim()/record_decision()` are trading-critical and routed through
`tick_executor.run()` (§2.1) — CONFIRMED in substance; quote attribution is imprecise.**
`services/whale_stream/decision_bridge.py:66` reads
`if not await tick_executor.run(lambda: candidate_ledger.claim(signal.id, ticker=signal.ticker)): return {"action": "skip", ...}`
— this literally gates further processing of every whale signal, and `:129` routes
`record_decision()` the same way. However, the quoted phrase `"gate every whale signal"` does
**not** appear in `candidate_ledger.py`'s own docstring (which instead says "Task 10 gates
`_handle_signal` on `claim()`"). The exact string exists verbatim in
`services/whalewatchers/_scoring_pool.py:8` ("...also used by
`candidate_ledger.claim()/record_decision()`, which gate every whale signal)"), a third module
not otherwise in that sentence. The underlying substantive claim is true and independently
verified from `decision_bridge.py`'s own code; the citation as "the module's own docstring"
misattributes which module. Minor, not load-bearing.

**6. SQLAlchemy Core vs. hand-rolled connect benchmark (§1.2) — CONFIRMED, but only reproducible
once a load-bearing, unstated detail (WAL mode) is inferred; the Appendix's "fully specified"
claim is not accurate as written.**
First attempt (Variant C exactly as literally described in §1.2's table — plain
`with sqlite3.connect(...) as conn:`, no PRAGMA) produced results qualitatively different from
the artifact's: median A=95.7µs (doc: 471.3µs), B=59.8µs (doc: 82.5µs), C=81.7µs (doc: 153.7µs),
and **C's fd delta was −16 over 2000 calls** — no leak at all, contradicting the artifact's
central "+314/2000, ~15.7%" claim. Only after adding `conn.execute("PRAGMA journal_mode=WAL")`
per call to variants A and C — matching every real `services/*.py` `_connect()` body, and the
§1.4 "Before" example's own `conn.execute("PRAGMA journal_mode=WAL")` — did results land in the
artifact's ballpark: A=360.9µs (doc 471.3), B=55.9µs (doc 82.5), C=95.8µs (doc 153.7),
**C's fd delta = +279/2000 (14.0%)** vs. the doc's +314/2000 (15.7%). B-vs-A speedup: mine 6.46×
vs. doc's 5.71× — same order of magnitude, arguably a tighter match on the ratio than the
absolute numbers. Interim evidence: a WAL-mode fd-count sweep sampled every 100 iterations showed
a genuine sawtooth pattern (fd count climbing to ~300+ before periodic GC sweeps release it, not
a smooth monotonic +314), and a second sweep in the same process crashed with
`sqlite3.OperationalError: unable to open database file` — the container's `ulimit -n` is 1024,
confirming the general fd-exhaustion mechanism is real and reproducible on demand, just not as a
clean single monotonic number. **Conclusion: the qualitative and order-of-magnitude claims hold
up well under independent reproduction; the Appendix's specific claim that the benchmark's
"contents are fully specified in §1.2/§1.4 for anyone reproducing the measurement" is false as
written** — a literal reading of §1.2 does not reproduce the fd-leak result at all; the WAL
pragma, which turns out to be the single most load-bearing detail, is never stated as part of
the benchmarked variants.

**7. DuckDB `sqlite_scanner` is slower than native SQLite (§3.3) — STRONGLY CONFIRMED, with a
more direct mechanism proof than the artifact provides.**
Against the live `series_watcher.db` (`mode=ro`), `series='KXBTC15M'`:
- capture_stats-style: native SQLite 4,219 ms; DuckDB scanner **25,645.7 ms** (doc: 8,799.3 ms
  native / **26,588.1 ms** scanner — my scanner number is within 4% of the doc's, despite my
  native number being ~2× faster, likely OS page-cache-state variance on a live, actively-written
  29.6 GB file). Row values returned by both engines matched **exactly**
  (`26056408, 1786932626.44, 1788413402.01`).
- funnel-style (24h window): native SQLite 596 ms; DuckDB scanner **10,526.4 ms** (doc: 3,629.7 ms
  / 14,248.7 ms). Same qualitative result, wider ratio than the doc's (17.6× vs. 3.9×) — again
  consistent with native-SQLite cache-state variance, not a change in the scanner's own cost.
- **Mechanism, verified via `EXPLAIN` (physical plan), not inferred from timing alone**: the
  DuckDB plan for the capture_stats-style query shows `SQLITE_SCAN Table: raw_trades ...
  ~40,617,890 rows` (the entire table) feeding a `FILTER (series = 'KXBTC15M')` node applied
  **after** the scan — i.e. no index pushdown, exactly as the artifact claims, but now confirmed
  via the query planner's own physical plan rather than the artifact's prose description of it.

**8. DuckDB+Parquet speedup (58–644×, §3.3) — CONFIRMED directionally at a smaller scale.**
Exported `KXNFLGAME` (750,744 rows) to Parquet via the scanner; capture_stats-style query against
the resulting file: **1.77 ms**, vs. native SQLite for the same series: **1,037.8 ms** — a
**587× speedup**, with identical row output (`750863` both ways, table grew slightly between
queries — expected on a live file). This is the same order of magnitude as the doc's 644× figure
for the much larger `KXBTC15M` series, at roughly 1/35th the row count — good directional
corroboration that the effect is not an artifact of `KXBTC15M`'s specific size.

**9. Compression ratio's "uniform per-row bytes across series" assumption (§3.3, and flagged by
the artifact's own self-review #2) — FALSIFIED as literally uniform; the self-review's flagged
concern is real and measurable, not just theoretical.**
Sampled `LENGTH(raw_json)` for 2,000-row `LIMIT` slices of seven series via the covering index
(cheap, indexed, no full scan): `KXBTC15M` avg **359.7 bytes** — noticeably the *smallest* of the
seven — vs. `KXNFLGAME` 410.5, `KXATPMATCH` 414.5, `KXNBAGAME` 411.8, `KXMLBGAME` 420.9,
`KXETH15M` 394.3, `KXBTCD` 406.0 (i.e. **14–17% larger** than `KXBTC15M`). The Parquet side shows
the same pattern: `KXNFLGAME`'s export averages **70.6 bytes/row** vs. `KXBTC15M`'s **58.7
bytes/row** (~20% higher). Since the 10.0–12.3× compression figures are derived entirely from
`KXBTC15M` (the series used for both the SQLite-side `dbstat` sample and the Parquet export), and
`KXBTC15M` has below-average per-row payload size, **the true whole-table blended compression
ratio is more likely to be somewhat lower than 10.0–12.3×**, not the same. The doc's own labeled
"more load-bearing" figure (query speed, 58–644×) does not depend on this assumption and is
unaffected — but the compression figures specifically should not be read as representative of
the whole table.

**10. Full-table export-time extrapolation ("40.5M/26.0M × 181.6s ≈ 283s ≈ 4.7 min", §3.3) —
OVERSTATED-OR-UNVERIFIABLE mechanism; the number happens to land close to right, for the wrong
stated reason.**
The doc's formula assumes export cost scales linearly with **output row count**. My own two-point
measurement contradicts that: `KXNFLGAME` (750,744 rows, **2.9%** of `KXBTC15M`'s 26,012,845 rows)
took **75.03 s** to export — **41%** of `KXBTC15M`'s 181.56 s, despite producing 34× fewer output
rows. This is exactly consistent with Finding 7's `EXPLAIN`-confirmed mechanism: the scanner pays
a large, roughly filter-independent cost to scan the *entire* ~40.6M-row table regardless of which
series is selected, plus a smaller marginal cost per row actually materialized/written. Fitting
that two-point model (`fixed ≈ 72s`, `marginal ≈ 4.2µs/row`) and applying it to a single
unfiltered whole-table export (40,542,063 rows, no per-series filter needed) gives
**≈243 s (≈4.1 min)** — in the same ballpark as the doc's 283 s/4.7 min estimate, but arrived at
by a different, and more mechanistically defensible, model. The doc's stated derivation (linear
scaling from one filtered single-series data point) is not the right mechanism, even though its
final number is not far off.

**11. `EXPLAIN QUERY PLAN` quotes for the real `raw_trades` queries (§3.2) — CONFIRMED, verbatim.**
Ran the exact SQL from `series_watcher.py:498` and the exact `funnel()` SQL from
`:609-618` against the live file. Query 1: `SEARCH raw_trades USING COVERING INDEX
idx_raw_trades_series (series=?)` — exact match. Query 2 (both halves): `SEARCH raw_trades USING
INDEX idx_raw_trades_series (series=? AND observed_at>?)` — exact match, including that it is
plain `INDEX` (not `COVERING INDEX`) for query 2, correctly distinguished by the doc since query
2 needs non-covered columns (`count_fp`, `excluded`, `resolved_side`).

**12. `capture_writer.py` busy-timeout constants and daemon loop (§4.2) — CONFIRMED, exact.**
`_FLUSH_INTERVAL_SEC = 1.0` (line 196), `_DAEMON_BUSY_TIMEOUT_MS = 1000` (line 213),
`_CALLER_BUSY_TIMEOUT_MS = 50` (line 214), `def _run()` at line 429, `fault_log.record(...,
"flush_retained_on_lock", ...)` at lines 404-407 — all exact. Read the full `_run()` body: the
**normal per-second loop iteration** (the dominant, steady-state code path) uses
`budget = _CALLER_BUSY_TIMEOUT_MS if _stop_event.is_set() else _DAEMON_BUSY_TIMEOUT_MS`, so under
normal operation it is `_DAEMON_BUSY_TIMEOUT_MS` (1000ms) — the doc's characterization is accurate
for the code path that actually matters. `_CALLER_BUSY_TIMEOUT_MS` is used only in the one-time
shutdown-drain pass after the loop exits.

**13. `candidate_log.py`'s `_connect()` sets no explicit `busy_timeout` (§4.2) — CONFIRMED, and
independently verified beyond source-reading.** Read `_connect()` directly (line 76): only
`PRAGMA journal_mode=WAL` is set, no `busy_timeout` pragma. Independently, empirically induced a
real lock (`BEGIN IMMEDIATE` + unfinished write on one connection, then a second connection
attempting a write) and measured Python's implicit default timeout directly: **5.006 s** before
`sqlite3.OperationalError: database is locked` — confirming the "implicit default `sqlite3.connect()`
timeout of 5.0s" claim empirically, not just by citing Python's documented default.

**14. Tick cadence "6 seconds, verified live in the current config, not assumed" (§4.1, §4.2) —
FALSIFIED for the currently-live configuration. Material finding.**
`config/settings.yaml:36` does say `poll_interval_sec: 6` — that literal fact is true. But
`main.py:648-665`'s `_tick_interval_sec(cfg)` — the function that actually determines
`trading_loop`'s per-iteration sleep (`await asyncio.sleep(_tick_interval_sec(cfg))` at
`main.py:1234`) — only returns `poll_interval_sec` when **not** in streaming mode
(`_streaming_trade_tape_enabled()` false). When streaming is enabled, it returns
`safety_net_interval_sec` instead — `config/settings.yaml:44` sets that to **30**, five times
longer. `_streaming_trade_tape_enabled()` reduces to
`whale_provider.name == "kalshi_trade_tape" and trade_stream.enabled`
(`services/whale_stream/whale_stream_handlers.py:85-89`), the identical logic
`services/app_state.py:365,369` uses to compute the `trade_stream_status` exposed at
`/api/state`. I queried the **live running app** directly:
`curl -sk https://kalshi-whale-poc.ddev.site:8443/api/state` returns
`trade_stream_status: {"enabled": true, "connected": true, "mode": "stream", ...}` with
`trade_stream_perf.messages_per_sec: 96.7` — i.e. **the live app is currently running in
streaming mode**. I also traced the call chain end-to-end: `main.py:867` (inside `trading_loop()`,
the same function whose body contains the `_tick_interval_sec()` sleep at line 1234, with no
intervening function boundary between them) calls `_resolve_and_record_settlements_async`, which
calls `_resolve_and_record_settlements` (line 364), which calls
`candidate_log.resolve_from_market_results(market_results)` at line 383. **The real, currently-live
tick cadence driving the contention this section root-causes is 30 seconds, not 6.** The doc's
§4.2 explicitly labels the 6-second figure as "established from source" (not "inferred"), and
reasons about collision likelihood using "a 6-second tick cadence against a 1-second daemon
cadence." That specific number is wrong for the current live deployment. This does **not**
undermine the core contention *mechanism* (1000ms vs. 50ms budget mismatch, still exactly as
described) or the §4.3 fix direction (an even more slowly-paced caller has *more*, not less, slack
to widen its own budget) — but it is a real "never guess" HARD RULE gap: the doc read a static
config key without checking the conditional function that consumes it or the live runtime mode,
despite explicitly claiming to have verified this "live." §8's planned regression test (mocking
the two timers' interleaving) should be built against the real 30s figure, not 6s, or it will
simulate the wrong scenario.

**15. Live `fault_log.db` rows cited in §4.1 — CONFIRMED, near-exact.**
Queried `data/fault_log.db` (`mode=ro`) directly: `capture_writer/flush_retained_on_lock`
count=**182** (doc's probe: 181 — the +1 is consistent with ~8.6 more minutes of live accrual
between the doc's probe and mine, at the doc's own stated ~1-per-28-min rate), `first_seen`
**exactly** `2026-08-30T16:19:54Z` (matches). The two historical rows match **exactly** on every
field: `capture_writer/flush` (raw_trades drops) count=237, first_seen
2026-08-27T20:22:15Z, last_seen 2026-08-30T16:08:55Z; `capture_writer/flush` (rejected_candidates,
"unable to open") count=2, first_seen 2026-09-02T13:01:37Z, last_seen 2026-09-02T13:06:58Z.

**16. Rate arithmetic (181/84.76h ≈ 2.14/hour ≈ 1/28min, §4.1) — CONFIRMED, dimensional analysis
passes.** Independently recomputed elapsed time between the cited first/last timestamps:
84.76 hours (exact to the doc's stated precision); 181/84.76 = 2.135/hour; 60/2.135 = 28.1
minutes. Both check out.

**17. "Fixed by commit `13680e5`, zero recurrence" (§4.1) — CONFIRMED in substance; one
tangential timing curiosity noted, not a defect.** `13680e5` exists exactly as described; its
merge commit is `6d1a5e3` (PR #244), merged 2026-08-30 13:26:15 UTC. The last drop occurrence in
the fault log is 2026-08-30T16:08:55Z — about 2h43m *after* the merge — i.e. drops continued for
a while past the fix landing on `main`, most plausibly ordinary deploy lag (the running instance
picking up the merged code later than the merge timestamp itself). This does not falsify the
doc's actual claim ("zero recurrence" *since* `last_seen`, which is independently confirmed true
by my own fresh query above) but is worth a footnote if this timeline is ever load-bearing
elsewhere.

**18. "~14 files under 1MB" (§5, and flagged by the artifact's own self-review #6 as unverified)
— FALSIFIED; the self-review's flagged risk materialized.** Listed every `data/*.db` file's
current size: **17 files** are under 1,048,576 bytes (`market_risk_state.db`, `risk_state.db`,
`accounts.db`, `series_evaluator.db`, `suggestion_decisions.db`, `quarantine.db`,
`backup_log.db`, `shadow_mode.db`, `market_analyst.db`, `research_reports.db`,
`event_schedule.db`, `calibration_history.db`, `reset_log.db`, `trade_category.db`,
`alert_log.db`, `paper_broker.db`, `config_performance.db`) — not ~14. The next file up
(`trade_archive.db`, 1,138,688 bytes) is just over the 1MB line. The downstream arithmetic
("~14 files × 3 fds/connection ≈ 42 fds saved at most") should be **17 × 3 ≈ 51 fds** — the
qualitative conclusion ("marginal against a 1,024-descriptor budget") is unaffected by this
correction, but the specific number is wrong and the self-review had already, correctly,
predicted an adversarial reviewer would need to check it.

**19. Small-file consolidation decline reasoning (§5, item 28) — CONFIRMED sound, not a cop-out;
independent supporting evidence exists that the doc did not cite.** The §1.2 benchmark tested
only single-connection, single-file per-call latency — never concurrent cross-file lock
contention when files are merged, which the doc correctly identifies as the untested half of the
question. Finding 15 above is directly relevant, uncited supporting evidence: `candidate_log.db`
today has exactly **two** independent writers sharing **one** file, and that alone already
produces a measured, live, warn-severity contention fault roughly every 28 minutes (Finding 15/16).
If two writers sharing one file already collide often enough to be a named, root-caused problem
in this same document, merging several more independent, previously-isolated low-frequency
writers onto shared files without a real concurrent-write benchmark is a materially riskier,
reasonable thing to decline — a stronger case for "declined for now" than the doc itself makes.

**20. `aiosqlite` 0.22.1 `ValueError` (not `sqlite3.ProgrammingError`) on dead connection (§2.1)
— CONFIRMED directly from installed library source, not the doc's citation of it.**
`/usr/local/lib/python3.13/site-packages/aiosqlite/core.py:153` raises
`ValueError("Connection closed")`; the package's own test suite
(`aiosqlite/tests/smoke.py:434,436`) asserts exactly this via
`assertRaisesRegex(ValueError, "Connection closed")`.

**21. `_aio_db.py` docstring quote (§2.1) — CONFIRMED.** The quoted fragments ("not the per-trade
whale-scoring hot path", "a single connection per file is enough headroom **here**") both exist
verbatim in the module's docstring (lines 9, 11), appropriately ellipsed.

**22. HEAD claim and "Tier 0 code not yet landed" (Status section) — CONFIRMED.** `fffe972` is a
real ancestor of the worktree's current HEAD `bb64a7c` (`git merge-base --is-ancestor` returns
true). `docs/next-action.md`, read fresh, independently states: "This is still a plan document
only — none of the 10 tasks' code has been written yet" — matching the doc's claim exactly.

**23. Cited research documents exist on disk — CONFIRMED.** All research docs named in the
Status section (`2026-09-02-architecture-audit-and-rewrite-considerations.md`,
`2026-09-02-architecture-audit-second-pass.md`, plus the second-pass's own self-review/adversarial/
consolidation trio) are present under `docs/superpowers/research/`.

---

## General document quality (dimensional analysis)

Independently recomputed every numeric derivation checked above (speedup ratios, GB/byte
conversions, the fd-count product, the occurrence-rate division, the export-time extrapolation)
against the values the document itself reports as inputs; all check out arithmetically to the
document's stated precision **except** the two already covered above: the small-file count input
(Finding 18, wrong input therefore wrong output) and the export-extrapolation's implicit linearity
assumption (Finding 10, right-ish output for a mechanism that Finding 7's own evidence contradicts).
Units and scales (µs vs. ms vs. s; GB vs. MB vs. bytes; row counts vs. byte counts) are stated
consistently everywhere I checked; no mislabeled-dimension defects were found beyond the two above.

The self-review's own seven flagged gaps were checked against source/live data rather than taken
at face value: #1 (§4.2 mechanism is inference, not a captured trace) — accurately described, and
Finding 14 above shows the self-review actually understated the problem (the tick-cadence *input*
to that inference is itself wrong for the live config, a gap the self-review did not catch).
#2 (compression-ratio uniformity assumption) — accurately flagged as unverified, and Finding 9
above shows it is not just unverified but measurably false in the expected direction. #6
(small-file count, "~14") — accurately flagged as unverified, and Finding 18 confirms it was
wrong. #3, #4, #5, #7 (aiosqlite write-concurrency not benchmarked; `excluded`-column writers not
traced; backup-interaction not fully re-read; fault count not re-queried a second time) were not
independently re-derived here beyond what's covered above, since none of the six numbered claims
this review was tasked with touches them directly, and the self-review's own characterization of
each as an open, correctly-labeled gap (not a hidden one) held up wherever spot-checked.

---

## Verdict: **GO-AFTER-FIXES**

No claim in this document was found fabricated. Both headline benchmark results — SQLAlchemy Core
being faster per-call than a hand-rolled closing connection, and DuckDB's `sqlite_scanner` being
*slower* than native SQLite on the live file because it does not push the series filter into
SQLite's own index — independently reproduced within a reasonable range on a second attempt (the
DuckDB scanner number, in particular, reproduced within 4% of the original), and one mechanism
claim (no index pushdown) was confirmed even more directly here, via the DuckDB physical query
plan itself, than the document's own evidence shows. The decisions record's seven rows each trace
to genuinely supporting evidence. However, four issues are material enough that this should not
advance to the plan stage unrevised, and three more are worth fixing for precision.

**Must-fix before the plan stage:**
1. **Finding 14** — Re-verify and correct the tick-cadence claim in §4.1/§4.2 against live
   runtime state (the app is currently in streaming mode; real cadence is 30s, not 6s). Update
   the "not rare in principle" framing and make sure §8's planned regression test targets the
   real number.
2. **Finding 9** — Revise §3.3's compression-ratio presentation to state plainly that the
   uniform-per-row-bytes assumption is measurably false (cite the raw_json length variance:
   `KXBTC15M` runs 14–17% smaller per row than most other series), and that 10.0–12.3× is
   likely an upper bound for `KXBTC15M` specifically, not a whole-table estimate.
3. **Finding 10** — Correct the "40.5M/26.0M × 181.6s ≈ 283s" extrapolation's stated mechanism;
   replace linear-in-output-rows scaling with a model consistent with the `EXPLAIN`-confirmed
   fixed-scan-cost mechanism (Finding 7), even though the final number happens to be close either
   way.
4. **Finding 18** — Correct "~14 files under 1MB" to the current count (17) and the downstream
   "~42 fds" to "~51 fds" in §5.

**Should-fix:**
5. **Finding 6** — State explicitly in §1.2/Appendix that the benchmarked variants apply
   `PRAGMA journal_mode=WAL` per connect (matching the real app idiom) — without this, the
   Appendix's claim of full reproducibility is not accurate, and a literal-text reproduction
   fails to show any fd leak at all.
6. **Finding 5** — Fix the docstring-quote attribution in §2.1 (the "gate every whale signal"
   quote is from `_scoring_pool.py`, not `candidate_ledger.py`/`decision_bridge.py`).
7. **Finding 17** — Optional footnote on the ~2h43m gap between the fix's merge time and its
   last observed occurrence, for anyone later reconstructing this incident's timeline.

None of the above changes any of the document's seven top-level decisions (persistence module:
hand-rolled; migration path: incremental; aiosqlite: sequenced, not blocked; `raw_trades`:
SQLite-of-record + DuckDB/Parquet read copy; `index_ticks`: unchanged; `candidate_log.db`:
budget/ordering fix; small-file consolidation: declined) — each survives independent re-derivation,
with Finding 19 actually strengthening the small-file-consolidation decline beyond what the
document itself argues. The must-fix items are about correcting specific numbers and one
mechanism claim before they become load-bearing inputs to the next pipeline stage, not about
reversing any decision.
