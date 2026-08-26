# Realtime Data-Plane — Adversarial Architecture Review (I12)

**Task:** I12 of `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Under review:** the leading combination from I10/I11 — *not yet selected*:

1. **Loop hygiene** — the trading tick's synchronous SQLite phases move to a dedicated
   single-writer DB thread (persistent connections, batched writes, ~1 s flush cadence,
   N+1 removal), with a stall watchdog.
2. **Reader-side prefilter** — the pure contract-count gate runs before the thread hop;
   only whale-sized candidates (and non-trade classes) are enqueued; sub-threshold
   rejection rows are batched off the hot path.
3. **Per-class consumers** on one loop — trade / coalesced ticker / never-shed critical
   (fill, position, lifecycle, control).
4. **Keep the ingest queue and consumer across reconnects.**
5. **REST** — demand reduction first (settled re-read, duplicate milestone polling, de-burst
   the tick's gather); a critical reserve inside the existing limiter with a global 429
   brake; shared bucket stays at 8/s until the anonymous ceiling is probed.
6. **Recovery** — retryable candidate state machine, idempotency on `trade_id`, bounded
   reconciliation triggered by the ingest drop/reconnect counters, journal only if
   reload-time loss is material.

**Method.** Three independent attacks: (a) targeted falsification runs in the two replay
harnesses against the criticisms the author could anticipate; (b) two independent
reviewer agents with separate scopes (WebSocket/loop side; REST/recovery side) instructed
to falsify, whose claims were re-verified against code and re-run where measurable;
(c) an internal review pass. The Devil's Advocate MCP tool listed in the toolchain
requires a paid subscription that is not present — treated as BLOCKED_EXTERNAL per
`.claude/rules/tooling-plugins.md` (fail open to the documented fallback; not retried).

## 1. Targeted falsification runs (author)

| Attack | Run | Result | Verdict |
|---|---|---|---|
| **Starve the critical reserve** — whale enrichment at 5/s (≈10–40× the measured 0.15–0.5/s) during 40-call catalog storms every 15 s, 8/s shared bucket | `rest_scheduler_replay`, 600 s | `reserved`: whale wait p95 597 ms / max 1.25 s — bounded — but **catalog max wait 207 s**: critical borrowing drains the shared bucket and background starves. `fifo_8` and `priority_aging`: whale p95 **94.5 s** (the 8/s bucket is simply over capacity: everyone starves). `reserved_20_60`: 0 ms, no 429 (peak 620 tokens in one second, inside the 600-capacity + refill). | **Needs a design change**: the reserve protects critical calls only while total demand fits the local budget; above that, the budget size (C2) is the constraint and the reserve merely chooses who starves. Borrowing must be capped or background must keep a floor. |
| **Reserve under a low anonymous ceiling** — upstream 5/s / 10-burst, whale at 2/s | same | `reserved` cuts whale total p95 from 2.3 s (FIFO) to 0.86 s, but its retries draw **more** 429s (669 vs 495) and **two critical calls exhaust their retries into hard errors** (FIFO: none). | **Needs a design change**: a global 429 brake (shrink every bucket for a few seconds on any 429, SRE adaptive client-side throttling) is mandatory with any policy that lets critical calls retry aggressively; and C2/ceiling verification comes before any local sizing. |
| **Prefilter with the rejection write left on the reader** — 12.6 ms per print (the `_connect()` idiom) for the 27% of prints on watched markets | `realtime_pipeline_replay` `busy_hour` + hygiene, `StagedTopology` with `prefilter_cost_sec` = amortised 3.4 ms / 12.6 ms | reader busy **262 s of 600 s** (amortised) / **968 s of 600 s** (every print) versus 0.2 s with a batched write. The harness does not model reader saturation, so these are lower bounds on the damage. | **Fatal unless batched**: the prefilter is only a design at all if the rejection row is written by the DB thread in batches (or dropped/sampled). The comparison in I10 assumed ~2 µs and must say so. |
| **Critical consumer doing inline REST** — the lifecycle `settled` re-read (mean 0.47 s, 0.75/s) modelled as the lifecycle handler's service time | `realtime_pipeline_replay` `busy_hour` + hygiene, `class_groups`/`staged` | critical p95 74 ms but **max 10.8 s**; the critical consumer 39% busy on REST waits. | **Needs a design change** (already in the leading design's own condition): no inline REST on the critical consumer — the settled re-read becomes a background resolver fed by a queue, which also removes 23% of REST demand (I8). |
| **Whale-heavy burst** — 383/s with 1% candidates at 30–100 ms | `measured_burst` with `candidate_fraction=0.01`, `staged` | candidate p95 2.3 ms, p99 42 ms, trade consumer 6% busy, no drops. | Holds. |

## 2. Internal review pass (author, code-anchored)

| # | Criticism | Mechanism (code) | Verdict |
|---|---|---|---|
| 2.1 | **The prefilter changes what "seen" means.** Today every unseen trade is `_mark_seen` in `_process_trades_sync`, so the 250k ring covers the whole exchange-wide flow (10–30 min of retention). Prefiltered prints would never be marked; the ring would hold only candidates (~800/h). | `kalshi_trade_tape.py::_process_trades_sync`, `_mark_seen` | **Benefit with one obligation**: retention stretches from minutes to days (I4's reconciliation horizon caveat mostly disappears), but the REST tape poll (`_fetch_trade_tape`, poll mode) and any reconciliation replay must run the same gate before consulting the ring — otherwise a re-presented sub-threshold print is "unseen" and pays the full path again. Needs a test. |
| 2.2 | **Archival capture happens before the gate today.** `series_watcher.record_trade` (watched-series `raw_trades`) and the series-evaluator denominator (`trades_observed_by_series`, counted inside the worker) run for every print; a reader-side gate that skips the hop would silently stop both unless they move to the reader. | `whale_stream_handlers.py::_process_stream_trade`, `_process_trades_sync` | **Needs a design change**: the reader must still call the buffered `record_trade` (it is an append) and keep the per-series counter; the harness's ~2 µs prefilter cost is only honest if these stay µs-cheap (they are — buffered append and a dict increment). |
| 2.3 | **The `state["trade_tape"]` UI ring and `bump_generation()` run per message on the loop** and would remain per message. | same | Cosmetic (µs), keep. |
| 2.4 | **Ticker coalescing vs. `check_exits`.** `_process_stream_ticker` runs `strategy.check_exits` on every ticker when positions are open; coalescing to the latest per market *reduces* that work and cannot regress an exit decision (only the latest mark matters), but a coalesced update must carry the newest `ts` or `market_history.record_snapshot_from_ticker`'s 5 s throttle could record an older mark. | `_process_stream_ticker`, `market_history.record_snapshot_from_ticker` | Needs a guard: apply-if-newer on the coalescing map. |
| 2.5 | **Keeping the queue across reconnects** keeps messages from the previous `sid`; no handler reads `sid`, `_sync_subscriptions` re-subscribes on the new socket while the old backlog drains, and a message from the old connection is still a real exchange print. The one hazard is `_begin_connection` today also resets `_queue_high_water`/per-connection counters. | `websocket.py::run`, `_begin_connection` | Needs a guard or test: connection-scoped counters (`connects`) reset, queue-scoped ones do not; and I10 showed keeping the queue only helps once capacity exists. |
| 2.6 | **The single-writer DB thread has a large blast radius.** Fifteen-plus modules use the `_connect()`-per-call idiom from the tick, routes and the worker; making one thread the only writer means touching every write path, and `check_same_thread` forbids sharing a connection across threads. | every `services/*.py` with `DB_PATH`/`_connect` | **Needs sequencing, not a different design**: step one is moving the tick's synchronous phases onto a dedicated executor with persistent per-thread connections (`to_thread` today already runs the worker's SQLite this way), which removes the loop stall with a contained change; the shared writer thread and batching are step two, justified per store by the I5/I2 numbers, with money stores (`paper_broker.db`, `risk_state.db`) explicitly excluded from write-behind. |
| 2.7 | **WAL single-writer contention** between the new writer and the remaining synchronous callers (routes, the worker's own rejection writes). | SQLite WAL: one writer at a time; Python `timeout` 5 s is a *sleep on the caller's thread* | Needs a guard: `busy_timeout` must never be waited on the loop thread — every remaining loop-thread write is a stall candidate; the watchdog must attribute stalls to their caller. |
| 2.8 | **Observability blind spots the new design creates.** A prefilter bug would look like "no whales"; a dead DB-writer thread would look like "quiet stores"; per-class consumers need per-class queue depth/age (I1's metrics are per connection); the reserve bucket needs its own tokens/waiters gauges. | I1/I2/I5 metric surfaces | Needs guards in the design: `prefiltered` counted by class and reconciled against `received`; writer-queue depth/age and a liveness finding (the same shape as `ws-dropped-messages`); per-group queue metrics; reserve gauges. `fault_log` for the writer thread's exceptions. |
| 2.9 | **Failure recovery.** Writer thread death: writes pile up in memory, nothing crashes, data silently stops persisting (the exact `game_state` failure shape fault_log was created for). Critical consumer exception: I1 counts and fault-logs it per class; the consumer loop continues. | `services/fault_log.py` docstring; I1 `_process_item` | Needs a supervisor for the writer thread (restart + fault_log + quality finding) — `task_supervisor` is the existing pattern. |
| 2.10 | **Deployment.** `uvicorn --reload` restarts on any `.py` change and on files landing in in-repo worktrees (I7); a restart loses pending candidates unless journaled and discards the queue regardless. The same in-repo worktree also pollutes every tree-walking tool: `tools.quality_audit` reported 166 findings against the copy and `tools.project_manifest` doubled its counts (a manifest regenerated in that tree failed CI twice during I11/I12). | `.ddev/docker-compose.fastapi.yaml`, `tools/quality_audit/source.py`, `tools/project_manifest.py` | Needs one shared guard (exclude `.claude/worktrees` from the reloader and from both tree walkers) — small, deterministic, CI-owned; and it decides the journal question: under `--reload` the restart case is *common* in dev, rare in production — the journal's value is a dev-environment value. |
| 2.11 | **Idempotency has no single home today.** `signal_log.signals` has no unique key on the whale id; `strategy_engine` guards per market, not per trade; the paper broker opens on the signal. | `signal_log.py`, `strategy_engine.py:436` | Needs a design change: the idempotency key lives at the decision bridge (`_handle_signal`) keyed on `trade_id`, backed by an additive unique index on the signal's source id, before any second evaluation path (retry or reconciliation) is enabled. |
| 2.12 | **De-bursting the tick's gather is a code-order fact no scheduler sees.** The 375 ms position wait under strict priority in I11 comes from coroutine launch order inside one `asyncio.gather`. | `main.py` trading loop gathers | Needs a design change independent of policy: launch critical fetches first (or sequence them ahead of the background gather). |

## 3. Independent reviewer findings, verified

Two reviewer agents (general-purpose, separate scopes, instructed to falsify) reported
after ~13 minutes each. Every load-bearing claim below was re-checked against code, the
mirrored Kalshi docs, or an independent re-run before being accepted. "Confirmed" means
the mechanism is in the code as claimed; "reproduced" means the number was re-run here.

### 3.1 WebSocket / loop side

| # | Claim | Verification | Disposition |
|---|---|---|---|
| W1 | **The harness serves each class group on its own independent server** (`busy_until` per group), so `class_groups`/`staged` were modelled as three parallel machines; one asyncio loop with synchronous handlers serialises them. | Confirmed (`realtime_pipeline_replay.py` `busy_until: dict[group]`). **Fixed** in this task: `simulate(..., single_loop=True, service_order=...)` serialises every group on one server in priority order; `--single-loop/--order` on the CLI. Re-run in §3.3: `staged`+hygiene candidate p95 goes from 0.0 ms (parallel) to 18.9–21.9 ms, max 117–172 ms. | Harness defect fixed; every I13 number cites the single-loop model. |
| W2 | **`sustained` counted prefiltered prints in its denominator** (83% of real work could strand and still read "sustained"); `duplicate_processed` is vacuous (`seq` unique by construction); **enrichment was drawn on all trades independently of the candidate flag**, so under a prefilter every REST stall landed on a print the prefilter discards — a staged design *could not* show a resolve stall on the decision path. | All three confirmed. **Fixed**: denominator is `received − prefiltered`; `candidates_generated/served/stranded` reported and `sustained` requires zero stranded candidates; enrichment attaches to candidates whenever a candidate model is set (I6 baselines with no candidate model stay byte-identical — pinned by test). `duplicate_processed` stays as the harness self-check it is. Re-run: today's topology strands 68/179 candidates at the busy-hour horizon (`class_groups` alone 31/179); every hygiene variant strands 0. | Harness defect fixed; tests in `tests/test_realtime_replay_review_fixes.py`. |
| W3 | **Lifecycle in the critical queue starves fills/positions**: the `settled` handler awaits `get_market` inline (I7: mean 565 ms, max 27.9 s); modelled at 8% of lifecycle, `staged` gives fill p99 3.4 s / max 12 s; a separate lifecycle queue gives fill max 0.3 ms. | Mechanism confirmed (`whale_stream_handlers.py` settled path, §1 row 4 measured max 10.8 s the same way). Reviewer's fill numbers not independently re-run; the direction is the same as §1. | **Design change**: four consumer groups — trade / ticker / lifecycle / account (fill + position) — and no inline REST on any of them; `settled` resolution moves to a background resolver (see R11). Pre-existing gap noted for the plan: `settled` should call `close_if_settled` directly rather than wait for the tick's `market_results`. |
| W4 | **The ticker consumer stays synchronous SQLite on the loop**: per ticker message `check_exits` reads `market_history.recent_price`, `volatility`, `analyst_lean`, `series_stats` per open position (each a fresh `_connect()`) plus `record_snapshot_from_ticker` — the measured 25.2 ms. At a 150-market watchlist (≈50 tickers/s) the ticker group saturates. | Confirmed (`services/exits/exit_engine.py:229/448/494/506`, handlers `:277`). | **Design change**: memoise the per-tick reads inside `check_exits` (one read per tick, not per message), route snapshot writes through the writer, keep `check_exits` synchronous (its atomicity with `broker.close_position` is what prevents a double close across consumers). Priority order stated explicitly: account/lifecycle > trade > ticker (§3.3: with the prefilter the two orders differ by 3 ms; without it, trade-first starves ticker to a 2.0 s critical max). |
| W5 | **The prefilter as written destroys a first-class dataset and breaks I4**: every print today feeds `state["trade_tape"]`, `series_watcher.record_trade` → `raw_trades` (12.9 GB), the watched-market `min_contracts` rejection rows (`candidate_log.db`, 903 MB, feeds `gate_summary`), and `trades_observed_by_series` — **which is already dead code**: built in `_process_trades_sync` but never passed to `record_trades_observed_bulk`. Moving capture to the reader would put `record_trade`'s inline 500-row `executemany` flush on the recv task. With the ring holding only candidates, I4's `capture_completeness` becomes ~0.3% permanently. | All confirmed: `grep record_trades_observed_bulk` finds only the docstring and the comment that promises the call; `series_watcher.py:253` flushes inline at `_FLUSH_BATCH`; `reconcile_window` compares REST ids against `seen_exchange_ts_by_id()`. The dead counter is a real effectiveness bug (the series-evaluator denominator has been zero since it was written) — but wiring it as-is would add a `_connect()` per stream trade (the exact 2026-08-11 freeze), so it is a remediation-plan task through the batched writer, not a drive-by fix here. | **Design change**: an explicit reader-side capture contract — append-only in-memory buffers on the reader (µs), flushed by the writer thread, never on the reader; sub-threshold rejections aggregated per (ticker, side, minute) instead of per row; I4 reports count-based exchange-wide completeness plus id-based whale-sized completeness with the gate threshold snapshot; `ingest.prefiltered.<class>` counters; the gate is an injected pure predicate over `services/kalshi/` contract accessors and is exception-guarded (`_ingest_raw` guards only JSON today — confirmed). Row 2.1/2.2 above stand, sharpened. |
| W6 | **Loop hygiene cannot be complete; the harness deletes every stall.** Still synchronous on the loop by invariant or omission: `paper_broker` write-through, `log_signal` (66.8 ms), `candidate_log.resolve_from_market_results`'s full `WHERE resolved = 0` scan per tick on a table growing ~100k rows/h, hourly prune DELETEs, GC over a 250k-string ring, `/api/state` serialisation. Residual 0.3 s/10 s + 1.5 s/60 s → `staged` cand p95 50 ms / p99 469 ms / max 1.0 s. | List confirmed in code (`candidate_log.py:198`, decision bridge, broker). Residual-stall numbers are the reviewer's run (plausible; same harness). | **Guard**: the watchdog exports `loop.stall_max_ms`/`loop.stall_count` per observability window so "stalls gone" is falsifiable; the design enumerates residual loop-thread writers per file and bounds each; money stores stay write-through by invariant (row 2.6) and are *measured*, not assumed cheap. |
| W7 | **Writer thread vs remaining loop writers**: no module sets `timeout=`, so a WAL write collision costs up to the 5 s default busy sleep on the loop — a new stall mechanism introduced by the fix. | Confirmed: 64 `sqlite3.connect(` sites, none pass `timeout`/`isolation_level`. | **Design change**: per-file single-writer ownership table; any connection that remains on the loop thread opens with a short `timeout` (~50 ms) and explicit BUSY handling; WAL + `synchronous=NORMAL` for non-money stores; the money stores keep their current durability. Sharpens row 2.7. |
| W8 | **Keeping the queue across reconnects corrupts `_subscription_sids`**: `subscribed` acks are applied by the *consumer*, `run()` resets the dict on the new socket, so a stale ack still queued at reconnect is applied after the reset; `_sync_subscriptions` then sends `update_subscription` on a dead sid yet sets `_subscribed_tickers = desired` — the watchlist re-scope is silently lost. | Confirmed (`websocket.py` consumer `subscribed` branch, `run()` reset, `_sync_subscriptions` tail). **Row 2.5 above was wrong** ("no handler reads sid"): the consumer does. | **Guard**: stamp a connection generation on queued items; drop control frames (`subscribed`, `ok`, `error`) from a dead generation before they reach the handlers; test. |
| W9 | **Consumer/writer lifetime once decoupled from the connection**: `run()`'s `finally: consumer.cancel()` must go; nothing restarts an exited consumer; a dead writer leaves the new buffers uncapped; a non-daemon writer blocks `--reload` exit. | Confirmed (`websocket.py` `finally: consumer.cancel()`). | **Guard**: `task_supervisor`-style supervision with `done_callback` → `fault_log` + restart; writer liveness metrics (depth, last-flush age); daemon writer with a bounded shutdown flush. Sharpens row 2.9. |
| W10 | **Dedupe ring**: `seen_exchange_ts_by_id()` does `dict(self._seen_order)` on the loop while the worker thread appends → `RuntimeError: deque mutated during iteration` on the I4 diagnostic route. | Confirmed (`kalshi_trade_tape.py:256–260`, no lock). Rare, diagnostics-only. | **Guard** (remediation task): bounded retry or a snapshot under a lock; an assertion that only one `fetch_signals` is in flight, which any keyed partitioning would break silently. |
| W11 | Not credible: memory growth (a full queue ≈ 30 MB), ticker-vs-lifecycle ordering under coalescing, fill-before-trade in paper mode. | Agreed. | None. |
| W12 | **Observability blind spots**: no prefilter counter; `whale_pipeline.counter.*` lose their denominators; `capture_flush_and_titles` drops to ~0 while the cost moves to an untimed thread; `trade_stream.messages_per_sec` and `dedup_ids_held` silently change meaning. | Agreed. | **Guards** in the design: every moved cost gets a timer where it lands; metric-meaning changes are recorded in `services/observability/CHEATSHEET.md` in the same commit that changes them. |

### 3.2 REST / recovery side

| # | Claim | Verification | Disposition |
|---|---|---|---|
| R1 | **The simulator's alphabetical tie-break invented the "de-burst" finding**: `Demand.requests()` sorts same-instant arrivals by `(time, class_name)`, so `background_live_status` was dispatched before `critical_position`; in `main.py` the live-status gather runs a phase *after* the critical gather. The real contention is `_maybe_scan_catalog_batch` spawning the catalog task five lines *before* the critical gather. | Confirmed: `rest_scheduler_replay.py` sort key; `main.py:346` spawns via `task_supervisor.supervise`, its batch is one `asyncio.gather` of `get_markets` calls (`catalog_scan.py:307`), the critical gather is at `:351`, live-status at `:578`. **Fixed**: `LAUNCH_ORDER` tie-break mirroring the tick, `burst_phase=0.5` for live-status in every preset. Re-run (§3.3): the finding **strengthens** — under today's FIFO bucket the tick's own position fetch waits p95 1.13 s (quiet) / 4.9 s (catalog storm) behind the catalog batch it just spawned. | Harness defect fixed; **design change**: "de-burst" re-aimed at the catalog launch order — spawn the tick's background tasks *after* the critical gather returns (or pace the catalog batch) — a code-order fix no scheduler policy sees. |
| R2 | **The reserve flips from best to worst below a ~4/s anonymous ceiling**: critical retries land on an empty upstream bucket and exhaust into hard errors (60 position errors at 3/s). | Partially reproduced (seed 1, whale 2/s, upstream burst = 2×rate): 3/s — `reserved` whale/position total p95 9.0/8.5 s with 90 + 13 exhausted-retry errors vs `fifo_8` 276/280 s with 115 + 29; 4/s — `reserved` 7.9/8.0 s (56 + 26) vs `fifo_8` 40.6/35.6 s (40 + 9); 5/s — `reserved` 0.86/2.1 s with 1 position error vs `fifo_8` 2.3/3.2 s with none. The exact ranking depends on the upstream burst size, which is unmeasured; the robust conclusion is that **below the local rate every policy hard-fails critical calls, and the reserve's retries draw more 429s**. | **Design change**: a global 429 brake (any 429 shrinks every bucket for a few seconds — adaptive client-side throttling) is mandatory with any reserve; the anonymous-ceiling probe is a prerequisite for the reserve (C5), not only for the budget-sized bucket (C2). |
| R3 | **Borrowing has no ceiling and the shared rate silently drops 8 → 5/s**: `ReservedCapacity` builds `shared = _Bucket(rate − reserve_rate)`; the streaming-off REST tape poll (`_fetch_trade_tape`, unclassified → `other`) goes from 5.7 s to 222 s max wait; under overload the reserve starves background forever (§1 row 1 saw catalog max 207 s). An additive reserve (11/s total) fixes both but draws more 429s at a 4/s ceiling. | Construction confirmed; the unclassified tape poll confirmed by grep; the additive-reserve and streaming-off runs are the reviewer's (not re-run). I11's prose "shared bucket stays at 8/s" was false as modelled. | **Design change**: state the variant explicitly (additive reserve inside a measured total, or a borrow cap plus background aging); classify the tape poll; size against the *measured* background need, not a carve-out. |
| R4 | **Reserve sizing ignores the tick's own critical burst** (≥5: ceil(150/50) market chunks + balance/positions/fills + exchange status); whale and position share one critical FIFO. | Confirmed from `main.py`'s critical gather. | **Guard**: a test pinning the real tick burst; whale-first ordering inside the critical class or per-class reserves. |
| R5 | **"Inside the existing limiter" is a rewrite**: `acquire()` holds `self._lock` while it sleeps, so waiters are strictly FIFO by lock order; a critical caller cannot pass a sleeping background waiter without restructuring `acquire` into a dispatcher — the code size and failure class I9 charged to C3. | Confirmed (`http_client.py:225–233`). | **Matrix correction**: C5 costs what C3 costs; a dispatcher-free design (per-class waiter deques woken in order, no separate task) avoids the stuck-dispatcher failure class and is the shape to plan. |
| R6 | **Rate-limit doc facts**: every sentence is about *authenticated* requests (the market-data client is unauthenticated); Basic Read buckets "hold up to two seconds of budget" = 400 tokens, yet `GET /account/limits` reported 600; rejected-request billing unstated. | Confirmed in `docs/kalshi/rate_limits.md`. | Recorded as a docs/live discrepancy in `docs/kalshi/CHEATSHEET.md` (budget entry) rather than resolved by guessing; the ceiling probe is the resolution. |
| R7 | **State-machine interactions the sim cannot see**: a timer-driven retry is a second mutator of `_seen_trade_ids` on another thread; each attempt is a `call_with_backoff` (up to 5 HTTP attempts, 7.5 s of sleep), not one call; a 12 s budget abandons 11/400 in a 60 s outage and needs ≥72 s; a 5-minute 429 storm abandons 48–57/400 — abandonment is residual loss, not H4 "fixed outright"; retries created outside the `@classify("critical_whale")` frame inherit `other`; over-cap trades (`_MAX_ONDEMAND_MARKET_FETCH = 100`, REST poll path) are marked seen before the lookup — a second H4-shaped loss; a successful-but-empty batch negative-caches for 300 s. | Confirmed in code (`_mark_seen` precedes the market lookup; `_MARKET_CACHE_TTL_SEC = 300`; `_MAX_ONDEMAND_MARKET_FETCH = 100`). Budget numbers are the reviewer's recovery-sim runs. | **Guards/tests** in the plan: single-owner retry task (no second mutator), retry work charged as scheduler demand, classification carried on the retry, over-cap and empty-batch paths covered, retry budget ≥ 72 s; the design states abandonment as the residual loss it is. |
| R8 | **Idempotency has no home**: `WhaleSignal.id` (= `trade_id`) is never read downstream; `signal_log.signals` has no trade-id column; `_handle_signal` logs *before* `strategy.evaluate`; `strategy` guards per ticker only — a re-evaluated trade whose position already closed opens a second one. The ring is bounded (~32 min at 129 trades/s) and empty after every `--reload`, so any restart-time reconciliation re-presents trades it cannot dedupe. | Confirmed (`decision_bridge.py`: `log_signal` precedes `evaluate`; `strategy_engine.py:436`; grep for `signal.id` readers: none). | **Design change** (sharpens row 2.11): a durable `INSERT OR IGNORE` ledger keyed on the source `trade_id` gates `evaluate` — required for *any* second evaluation path (retry, reconciliation, restart), independent of whether reload-time loss proves material. I11's "journal only if material" criterion was wrong. |
| R9 | **Triggered reconciliation degenerates under continuous drops** (I7: 49/148 windows dropping) into 15 s scheduling; a sweep with lag < backlog age trips `backlog_exceeds_lag`; lag ≥ 200 s means 230–307 s decision latency; sweep cost under-modelled ~4× (pages per window at 129 trades/s); sweep tokens drain the reserve if classed critical. | Consistent with I7 (oldest-queued age p50 119 s / p95 192 s) and I4's caveat. | **Design change**: trigger only on reconnect / error-25 (real loss); queue-full-while-alive is the WS design's problem, not reconciliation's; sweeps run in their own caller class; the model charges real page counts. |
| R10 | **Observability blind spots**: no reserve-vs-borrow counter or reserve-token gauge, no "background starved by borrow" attribution, `waiters_high_water` is lifetime, no pending/oldest-pending/abandoned/retry metrics, no duplicate counter in production. | Agreed. | **Guards** in the design (the ledger is what makes a duplicate counter possible). |
| R11 | **"Remove the settled re-read" is wrong**: the app grades on `finalized`, not `determined` (2026-08-23 correction, commits 83f878b / a415fff — the disputed-and-reversed-result gap); batching via `GET /markets?tickers=` is doc-backed. | Confirmed from the mirror: the `settled` WS message carries only `settled_ts`, `determined` carries `result`, REST ends at `finalized`, and the channel has no `amended`/`disputed` event (new `docs/kalshi/CHEATSHEET.md` entry). | **Design change**: batch + defer + retry the settled resolution in a background resolver (also fixes W3); never "remove". |

### 3.3 Corrected numbers (harness fixed, seed 1)

`busy_hour` + `hygiene_keep_queue`, candidate = whale-decision latency (arrival → service
start), crit = fill/position/lifecycle/ticker; `single` = one loop, priority order given:

| Topology | Model | cand p50 / p95 / p99 / max (ms) | crit p95 / max (ms) | stranded | busy (trade / ticker / crit) |
|---|---|---|---|---|---|
| `single_queue` (today + hygiene) | one server | 24.5 / 194 / 316 / 445 | 226 / 681 | 0 / 179 | 0.875 |
| `class_groups` (coalesced) | parallel (I10) | 3.4 / 42 / 69 / 200 | 32 / 302 | 0 | 0.68 / 0.18 / 0.02 |
| `class_groups` | single crit>ticker>trade | 27.7 / 241 / 439 / 525 | 48 / 501 | 0 | same |
| `class_groups` | single crit>trade>ticker | 13.7 / 76 / 134 / 202 | **765 / 1,987** | 0 | same |
| `prefilter` (one consumer) | one server | 0.0 / 20.1 / 66.8 / 117 | 40 / 490 | 0 | 0.203 |
| `staged` | parallel (I10) | 0.0 / 0.0 / 0.0 / 0.0 | 32 / 302 | 0 | 0.009 / 0.18 / 0.02 |
| `staged` | single crit>ticker>trade | 0.0 / 21.9 / 108 / 172 | 39 / 500 | 0 | same |
| `staged` | single crit>trade>ticker | 0.0 / 18.9 / 66.8 / 117 | 40 / 500 | 0 | same |

Same preset `as_measured` (stalls + discarding reconnect): `single_queue` strands 68/179
candidates at the horizon (cand p95 109 s), `class_groups` 31/179 (59 s), `prefilter`/
`staged` 0/179 but cand p95 4.0–4.2 s and crit p95 4.1 s — the loop stalls are the floor
for every in-process design, exactly as I10 §4.3 said.

What the honest model changes: (1) the 0-ms headline was a parallel-server artifact — the
leading design's candidate p95 under the busy hour is **~20 ms, max ~120–170 ms**, still
5–10× better than today's topology with the same hygiene; (2) **the prefilter is the
load-bearing element** — one prefiltered consumer lands within 1–3 ms of the full staged
topology; per-class groups earn their place only through *critical isolation* (W3) and
explicit priority, not through latency; (3) without the prefilter, the priority order
decides who starves (ticker at 2.0 s crit max under trade-first).

REST, launch order corrected (`fifo_8` = today):

| Preset | Policy | position limiter wait p95 / max (ms) | whale wait p95 / max (ms) | background max (ms) |
|---|---|---|---|---|
| `measured_quiet` | `fifo_8` | **1,125 / 1,237** | 1,163 / 1,486 | catalog 362, live-status 1,500, resolution 862 |
| `measured_quiet` | `priority_aging` | 375 / 375 | 45 / 491 | 750 / 1,625 / 1,375 |
| `measured_quiet` | `reserved` | 0 / 0 | 0 / 0 | 400 / 2,100 / 1,200 |
| `background_storm` | `fifo_8` | **4,875 / 4,987** | 4,913 / 5,236 | 4,112 / 5,250 / 4,612 |
| `background_storm` | `priority_aging` | 375 / 375 | 111 / 491 | 4,750 / 5,875 / 5,237 |
| `background_storm` | `reserved` | 0 / 0 | 0 / 0 | 6,400 / 8,100 / 7,200 |

The 375 ms of I11 §4.2 was the artifact; the code-faithful number is worse and has a
different cause (the tick's own catalog batch, spawned before the critical gather). The
`reserved` rows carry R3's caveat: the shared rate is 5/s in this model, which is where the
background maxima come from.

## 4. Verdict on the leading design

The direction survives every attack — no reviewer produced a candidate that beats
*prefilter + loop hygiene + critical isolation + kept queue + demand reduction + a bounded
critical REST path + retry/idempotency* on the matrix — but the design **as written in I10/
I11 does not**. Twelve items change before it can be selected, in severity order:

| Rank | Change | Source |
|---|---|---|
| 1 | A durable idempotency ledger keyed on the source `trade_id` gates `strategy.evaluate`; it is a precondition of *any* retry, reconciliation or restart path, not a conditional extra. | R8, 2.11 |
| 2 | No inline REST on any consumer: settled resolution becomes a batched, deferred, retried background resolver; lifecycle and account traffic get their own consumers. | W3, R11, §1 row 4 |
| 3 | The reader prefilter ships only with an explicit capture contract (append-only reader buffers, writer-thread flush, aggregated sub-threshold rejections, count-based + id-based completeness in I4, injected and guarded predicate). | W5, 2.1, 2.2, §1 row 3 |
| 4 | A global 429 brake is mandatory with any reserve; the anonymous-ceiling probe precedes both the reserve and the budget-sized bucket; the reserve is sized against measured background need with bounded borrowing (or additive), never a carve-out that halves the shared rate. | R2, R3, §1 rows 1–2 |
| 5 | The tick spawns its background tasks *after* the critical gather returns (catalog launch order), independent of any limiter policy. | R1, 2.12 |
| 6 | Loop hygiene is sequenced (executor with persistent connections first, batched writer per store second, money stores excluded), residual loop-thread writers are enumerated per file, and the stall watchdog exports per-window metrics so the claim is falsifiable. | W6, W7, 2.6, 2.7 |
| 7 | The ticker path memoises per-tick reads inside `check_exits` and routes snapshot writes through the writer; priority order is account/lifecycle > trade > ticker. | W4, 2.4 |
| 8 | Kept-queue reconnects stamp a connection generation and drop dead-generation control frames; connection-scoped counters reset, queue-scoped ones do not. | W8, 2.5 |
| 9 | Consumers and the writer are supervised (restart + `fault_log` + quality finding), with liveness metrics and a bounded shutdown flush. | W9, 2.9 |
| 10 | Reconciliation triggers only on reconnect / error-25, runs in its own caller class, and is costed at real page counts; queue-full is the WS design's problem. | R9 |
| 11 | The recovery state machine is a single-owner task, carries its caller class, charges real `call_with_backoff` attempts, covers the over-cap and empty-batch losses, and reports abandonment as residual loss. | R7 |
| 12 | Observability moves with the cost: prefilter/writer/reserve/pending/duplicate metrics, and metric-meaning changes recorded in the observability cheatsheet in the same commit. | W12, R10, 2.8 |

Matrix corrections carried into the I13 selection: C5's implementation cost equals C3's
(R5); "keep queue" is conditional on W8's guard; `staged`'s latency advantage over
`prefilter` is ~1–3 ms in the honest model (§3.3) — its justification is isolation, and
I13 must argue it on that basis or choose the simpler topology.

## 5. Guard dispositions (investigation-to-guard rule)

| Finding | Disposition |
|---|---|
| Harness artifacts W1/W2/R1 | Permanent CI guard: `tests/test_realtime_replay_review_fixes.py` pins single-loop serialisation, the prefiltered denominator, enrichment-on-candidates, the tick launch order and the live-status phase. |
| Dead `trades_observed_by_series` counter (W5) | Real latent bug; remediation-plan task via the batched writer (a direct wiring would reintroduce the 2026-08-11 per-trade `_connect()` freeze). |
| `seen_exchange_ts_by_id` deque race (W10) | Remediation-plan task (bounded retry / lock) plus a single-in-flight assertion. |
| Kalshi doc facts (R6, R11) | Recorded in `docs/kalshi/CHEATSHEET.md` (discrepancy note on the budget entry; new `settled`/`result` entry). |
| In-repo worktree pollution (2.10) | Shared, deterministic guard worth landing once: exclude `.claude/worktrees` from the reloader and from `tools.quality_audit` / `tools.project_manifest`; outside this initiative's scope, flagged for the user. |
| Everything else | Design obligations in I13, each with a named test or metric. |

## 6. Open verifications carried into I13 (facts, not designs)

1. Anonymous REST ceiling and burst for the unauthenticated market-data client (prerequisite for rank 4).
2. Whether a re-determination re-fires `determined` on `market_lifecycle_v2` (decides whether the `determined` result can be cached as provisional).
3. Whether trade envelopes carry `seq`, and `shard_factor` for the trade channel (I9 §7).
4. The real tick critical burst size at the current watchlist (R4's pinning test).
5. Residual loop-thread write cost per store once the executor lands (W6) — measured by the new watchdog metric, not assumed.

