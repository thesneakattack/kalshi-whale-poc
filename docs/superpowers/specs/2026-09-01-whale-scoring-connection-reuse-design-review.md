# Whale-Scoring Connection Reuse — Design Review Consolidation

Reconciles the design (`2026-09-01-whale-scoring-connection-reuse-design.md`), its
inline self-review, and an independent adversarial review (fresh Agent call, no
conversation memory, re-derived every claim from current source plus a fresh live
telemetry pull).

## Verdict: NO-GO as written. Revise, then recheck against the fix list below.

Every factual citation in the spec held up under independent re-verification — a
genuinely good sign for the source-citation rigor. But the causal/design analysis has
a real gap serious enough to change the core mechanism, not just patch a detail.

## Merged required-fix list

1. **(Breaks the design's safety case) The spec's own investigation missed an
   already-tracked, unfixed bug (issues #145/#150) that this fix would make worse in
   one specific way.** `services/kalshi/websocket.py` documents: a WS handler that
   exceeds `_HANDLER_TIMEOUT_SEC` (10s) gets abandoned at the asyncio layer, but the
   underlying OS thread is **not freed** (`Future.cancel()` doesn't stop it).
   Live telemetry confirms this class of timeout has actually fired during the
   incident (`handler_timeouts_total: 45` for the `trade` class, `handler_total.
   window_max_ms: 20319.8` — twice the 10s ceiling — still present in the
   *recovered* state, not just during the acute spike). Today, a leaked thread just
   wastes a slot in Python's shared default executor. Under this spec's design, each
   leaked thread would also permanently hold 3 open, cached SQLite connections for
   the rest of the process's life — converting a known thread leak into a
   compounding connection leak. The design must address this directly, not note it
   as a residual risk. Fix (see item 2): move off the shared default executor onto a
   small, dedicated, explicitly-sized pool, so any leak is bounded and countable
   instead of open-ended.
2. **(Related, strengthens item 1) "Thread-local" caching on the shared default
   executor is a weaker guarantee than the spec's framing implies.** That pool
   (`min(32, cpu_count+4)` = 20 threads on this container) is shared process-wide
   with unrelated `asyncio.to_thread` callers (`backup.py`, `research.py`,
   `storage_health/routes.py`, `diagnostics/routes.py`) — not a small, stable set of
   threads dedicated to this workload. A dedicated small `ThreadPoolExecutor` (2-4
   workers, matching `tick_executor`'s own established pattern but kept genuinely
   separate from it — `tick_executor`'s pool is already shared by
   `candidate_ledger.claim()`/`record_decision()`, which gate whale-signal
   detection, and shouldn't gain a new competitor either) makes the cache's actual
   footprint match its intended one, and turns a silent leak into a visible,
   bounded backlog if it ever does happen.
3. **(Real scope gap, and arguably a bigger bug than the one this spec targets)
   `candidate_retry.run_pending()` reaches the exact same four scoring functions via
   `score_recovered_trade()` — synchronously, on the event-loop thread, not via
   `asyncio.to_thread` at all.** A slow call here blocks the *entire* event loop
   (all ingestion, not just one worker-thread task) for its duration — strictly
   worse than the WS path this spec analyzed. Must be brought into scope: route it
   through the same dedicated executor and cache, which both fixes it and keeps the
   design internally consistent (one mechanism, not two).
4. **(Real implementation gap) Section 4's code sketch doesn't implement what its
   own prose claims.** The prose says schema DDL runs once per thread on first use;
   the shown `cached_read_connection()` has no way to signal "this is a new
   connection, run DDL" to callers, and runs no DDL itself. Needs a concrete
   mechanism (return an `is_new` flag, or accept a schema-init callback) before this
   is implementable as written.
5. **(Correctness of a secondary claim) Section 5's retry-on-broken-connection
   exception handling targets the wrong failure mode.** File deletion/move
   underneath an open `sqlite3` connection on Linux does not typically raise
   `OperationalError`/`ProgrammingError` — the open connection keeps working against
   the now-unlinked inode while a *fresh* connection would silently open an empty
   new file. `ProgrammingError` really covers API misuse (closed connection,
   cross-thread reuse), which is a real but different scenario. Given this app's own
   standing invariant (`data/*.db` files are live, permanent, never deleted while
   running — CLAUDE.md), the file-relocation scenario isn't practically reachable
   here; the spec should say that plainly rather than imply the mitigation covers a
   risk it doesn't.
6. **(Minor, tighten wording) Section 6's "replay the real historical message rate
   from `data/observability.db`" overstates what's retrievable** — confirmed the
   schema stores windowed aggregates (`count/min/max/avg`), not a raw per-message
   trace. Reword to "a representative synthetic burst profile derived from those
   aggregates."
7. **(Worth stating explicitly, not left implicit) WAL checkpoint-starvation is
   very likely a non-issue here, but the spec should say so and why**, per this
   repo's own "verify or falsify" standard: none of the four functions holds an open
   transaction across calls (each does one `SELECT` fully drained via `.fetchall()`
   and returns), so no long-running reader snapshot should ever block a writer's
   checkpoint.

## Not required, informational

- A pre-existing thread-safety gap in `_process_trades_sync`'s own docstring
  (`self._seen_trade_ids` claimed "only ever touched from within one in-flight
  fetch_signals() call at a time" — false once `score_recovered_trade`'s
  event-loop-thread path is considered) is real but pre-existing and out of this
  spec's scope. Worth a one-line pointer to a future item, not a blocker here.
- The reviewer's live pull showed `last_tick_duration_sec: 58.98s` at one moment,
  above the pre-incident baseline. Re-checked directly just now: `10.64s`, queue
  depth 0, 0 stale positions, `dropped_messages: 0` — that reading was a transient
  bump from several schedulers (`catalog_scan`/`mve_scan`/`milestone_scan`/
  `signal_resolution`/`settlement_resolver`) running concurrently, not a
  re-degradation. System is genuinely at its recovered baseline right now.
- Handspun-vs-proven: reviewer suggested parameterizing `tick_executor.
  connection_for()` itself (add `busy_timeout_ms`/schema-init callback) rather than
  a second near-identical cache module. Given item 2 moves this fix onto a
  genuinely separate, dedicated executor (deliberately not sharing `tick_executor`'s
  pool, for the reason stated there), a second small, self-contained cache scoped
  to that separate executor's threads is still defensible — but the revision should
  say this explicitly rather than silently pick a side, since it's a real trade-off
  the reviewer raised in good faith.

## Disposition

Items 1-4 are correctness/design fixes, not optional. Item 5 corrects a claim to
match reality rather than changing behavior. Items 6-7 are wording/rigor tightening.
None of this changes the confirmed root cause (per-trade fresh connections across
three files) or the general fix direction (connection reuse) — it changes *how* the
reuse is implemented (dedicated executor, not the shared default pool or
`tick_executor`'s) and *what's in scope* (the event-loop-thread retry path too).
