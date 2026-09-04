# Self-review: Option B bounded-concurrency trade dispatch implementation

2026-09-03. Author: the implementing session (this branch,
`fix/trade-resolve-bounded-concurrency`). Reviews the actual code on this
branch against the already-reviewed research doc
(`docs/superpowers/research/2026-09-03-trade-resolve-consumer-blocking-solution-comparison.md`,
GO verdict, PR #547 merged) and issue #546. This is a live-incident
implementation: per CLAUDE.md's standing allowance, the
self-review/adversarial-review/consolidation cycle was not run at every
intermediate step while iterating, but this document is the required
self-review before the eventual PR's own full cycle (still owed, not done
here) and before any merge.

**No code changes were made while writing this document** - it reviews
what is already committed on this branch.

## What this branch does, in one paragraph

`services/kalshi/websocket.py`'s market-queue consumer
(`_consume_market_from`, and `_consume_from` for the less-common
single-queue path) no longer awaits a trade item's full `on_trade` handling
inline. A "trade" item is instead dispatched through
`_dispatch_trade_concurrent`, which acquires one of
`_TRADE_DISPATCH_CONCURRENCY` (4) semaphore slots and spawns the real work
(`_run_trade_item`) as its own `asyncio.Task`, returning to the consumer
loop immediately. `_run_trade_item` serializes same-ticker work through
`_TickerDispatchGate` before calling the unchanged `_process_item`.
Separately, `services/whalewatchers/kalshi_trade_tape.py` fixes two
correctness preconditions: `_resolve_unknown_markets` returns its
failed-ticker set per-call instead of mutating `self._resolve_failed_tickers`,
and `_process_trades_sync`'s "already seen?" check-through-mark span is now
one atomic transaction under a new `self._seen_lock` (`threading.Lock`)
instead of two separate, unsynchronized operations (issue #546).

## High confidence

- **The two correctness preconditions are real fixes, not cosmetic.** Both
  are proven by a falsifier, not just a passing test: for the
  `resolve_failed_tickers` fix,
  `tests/test_kalshi_trade_tape_resolve_failed_tickers_concurrency.py::test_old_shared_self_state_shape_would_have_lost_the_slow_calls_failure`
  reproduces the OLD shape's actual data loss (a self-contained
  reimplementation of the pre-fix `self._resolve_failed_tickers = set()`
  reset, not the current provider) and shows `SLOW-TICK`'s failure is
  silently wiped by a concurrent call - then the real provider's
  equivalent scenario (`test_a_slow_failing_resolve_is_not_corrupted_by_a_concurrent_call`)
  shows it is not. For issue #546,
  `tests/test_kalshi_trade_tape_seen_lock_race.py` does the same thing
  against the real `_process_trades_sync`: with the real lock, exactly one
  of two concurrent OS threads proceeds past the "already seen?" gate for
  the same `trade_id`; with a no-op stand-in swapped in for
  `self._seen_lock` (simulating the pre-fix state), both do. Both test
  files were re-run 5-8 times each with no flakes.
- **All existing tests still pass, unmodified in behavior.** 217 tests
  across every file that touches `_consume_market_from`, `_consume_from`,
  `_process_item`, `_resolve_unknown_markets`, `_process_trades_sync`,
  `score_recovered_trade`, and the whale-candidate-lifecycle/candidate-retry
  integration suites - none of their assertions needed to change to keep
  passing. `import main` still succeeds. `tools.quality_audit` shows zero
  *new* findings attributable to this branch (one was found and fixed
  during implementation - see "Judgment calls" below - the remaining 27
  findings are pre-existing `config-unread` warnings unrelated to this
  work, unchanged from a baseline run before these edits).
- **`self._market_cache`'s safety claim (research doc's own "confirmed
  safe, not merely unaudited") is now enforced structurally, not just
  documented.** Same-ticker trade dispatch is serialized end-to-end by
  `_TickerDispatchGate` before `on_trade` (and therefore
  `_resolve_unknown_markets`) is ever called for a second same-ticker
  print, so two concurrent `fetch_signals()` calls for the same ticker are
  now impossible by construction - not merely unlikely.
- **`score_recovered_trade`'s `resolve_failed_tickers=set()` is a no-op
  behavior change, verified by reading `candidate_retry.run_pending`
  (`services/candidate_retry.py:167`)**: it always resolves the market
  itself before calling `score_recovered_trade`, so `market is not None`
  always short-circuits the `or ticker not in resolve_failed_tickers`
  check in `_process_trades_sync` - this parameter was already dead for
  that call site even before the fix (it read `self._resolve_failed_tickers`,
  never consulted because of the short-circuit). Confirmed by re-reading
  the source, not assumed.

## Judgment calls made beyond the literal task instructions (please weigh these explicitly)

1. **The #546 fix is broader than "lock inside `_mark_seen`."** The task
   description said to lock "the check-then-act gate on
   `_seen_trade_ids`/`_seen_order` in `_mark_seen`." Reading the referenced
   in-flight investigation doc
   (`fix/seen-trade-ids-concurrency-race-investigation` branch,
   `2026-09-03-seen-trade-ids-concurrency-race-root-cause.md`) showed the
   real TOCTOU window is NOT inside `_mark_seen`'s own body (concurrent
   `_mark_seen` calls for *different* trade_ids don't corrupt the ring -
   `set`/`deque` ops are individually GIL-atomic) - it's the gap between
   `_process_trades_sync`'s own pre-check (`if trade_id in
   self._seen_trade_ids`) several lines earlier and the eventual mark
   call. Locking only inside `_mark_seen` would have shipped a lock that
   exists but doesn't close the actual race - I verified this by writing
   the falsifier test first with the narrow fix and confirming it still
   failed, before implementing the broader one. **This is a deliberate
   deviation from the literal instruction, made because the narrower
   version demonstrably does not work** - flagging explicitly per this
   repo's "never guess" rule rather than silently doing something
   different from what was asked.
2. **The semaphore is acquired before the ticker gate, not after.** This
   means a burst of same-ticker trades can occupy multiple of the 4
   semaphore slots while genuinely idle (blocked behind each other on the
   ticker gate, not doing real concurrent work), reducing the *effective*
   cross-ticker concurrency available during such a burst. I chose this
   ordering because it gives a strictly simpler invariant (`_TRADE_DISPATCH_CONCURRENCY`
   bounds total in-flight dispatch tasks, full stop) over a
   marginally-more-efficient-but-more-complex alternative (acquire the
   ticker gate first, semaphore only once actually about to run). Given
   the research doc's own measurement that off-list whale prints are
   roughly ~1/sec exchange-wide, same-ticker collisions frequent enough to
   matter are not expected - but this is reasoning, not a benchmark of the
   live system, and worth an adversarial pass.
3. **`_consume_from` (the single-queue-mode consumer, not live today's
   config) was given the same bounded-concurrency treatment as
   `_consume_market_from`, via a shared `_consume_one` helper**, rather
   than only fixing the path the live incident is actually hitting
   (`two_consumer_mode: true`). Rationale: avoiding a silent behavioral
   fork between the two modes seemed better than a narrower patch, and it
   cost nothing extra to implement since both consumers already shared
   `_process_item`. This widens the blast radius of this change slightly
   beyond the minimum needed for the live incident - flagging so the
   adversarial reviewer can judge whether that's the right call under
   time pressure.
4. **`_TickerDispatchGate.hold` was renamed to `_hold` mid-implementation**
   after `python -m tools.quality_audit` (run per CLAUDE.md's "Start
   investigations here") surfaced a real new finding:
   `kalshi-contract-docs-missing:services.kalshi.websocket:hold` - the
   scanner requires every *public* method of any class in
   `services/kalshi/*.py` to carry a `CONTRACT_DOCS` doc-page mapping,
   correctly, since this file is the Kalshi vendor boundary. `hold` is a
   pure internal concurrency primitive with no Kalshi API contract behind
   it, so renaming it private (matching every other internal helper method
   in this codebase's convention) was the correct fix rather than
   fabricating a doc mapping that doesn't really apply. Re-ran the audit
   after the rename and confirmed the finding is gone with no other new
   findings introduced.

## What still needs independent (adversarial) verification

The concurrency claims are the highest-risk part of this change, per the
task's own framing - specifically:

- **My own reasoning about `asyncio.Lock`'s uncontended fast path not
  yielding control** (the argument in this session's own working notes,
  not written into this document's prose above, for why
  `_TickerDispatchGate`'s get-or-create-then-increment-refcount sequence
  is race-free without its own guarding lock) should be re-derived
  independently against CPython's actual `asyncio.locks` source rather
  than taken on my say-so - it is exactly the kind of "I reasoned through
  it" claim CLAUDE.md's HARD RULE says needs re-deriving from primary
  source, not trusted from the artifact's own prose.
- **The refcount-based eviction in `_TickerDispatchGate`** (locks/refcounts
  popped from the dict once nothing holds or is waiting on them) has no
  dedicated unit test in this branch beyond the indirect evidence of the
  concurrency tests passing repeatedly without leaking or misbehaving - a
  reviewer should check whether a targeted test (e.g., asserting the dict
  is empty after N sequential same-ticker dispatches complete) is worth
  adding before merge.
- **No test exercises three-or-more-way same-ticker contention** (only
  two concurrent same-ticker trades are tested) - the FIFO-fairness
  property of `asyncio.Lock` under 3+ waiters was not independently
  verified here, only assumed from Python's documented behavior.
- **`run()`'s teardown cancellation of `_pending_trade_tasks`** (added
  alongside the existing, unawaited `consumers` cancellation) has no
  dedicated test - it follows the exact same unawaited-cancel pattern the
  existing `consumers` list already uses, but that pattern itself has
  never had a test proving cancelled tasks actually release their
  semaphore slots and don't leak. Worth a targeted check before merge,
  since a reconnect storm leaking semaphore slots would silently degrade
  `_TRADE_DISPATCH_CONCURRENCY` toward zero over time - a slow,
  hard-to-notice regression of exactly the kind CLAUDE.md's data-plane
  rule warns against.
- **No live before/after measurement against the running app has been
  done** - this is explicitly deferred to the coordinator per the task's
  own instructions, coordinating a live before/after read against
  `/api/observability/summary` and `/api/health/pipeline` once this is
  reviewed, since the primary checkout must not be touched from this
  worktree.
- **Dimensional-analysis pass**: `_TRADE_DISPATCH_CONCURRENCY = 4` is a
  concurrency-bound integer, not a money/probability/unit-conversion
  value, and this branch introduces no new numeric *derivation* (no
  formula, no unit conversion) - only control-flow/concurrency primitives
  and one pass-through parameter rename. Treated as out of the HARD
  RULE's practical scope for that reason, but noting it explicitly rather
  than silently skipping the rule, per the "any arithmetic... gets a
  dimensional-analysis pass" language covering "batch sizes" and similar
  scale values.

## Explicitly NOT done in this branch (by task instruction)

- No PR opened yet.
- No merge.
- `config/settings.yaml` untouched.
- The primary checkout untouched; the live app was not restarted or
  reloaded from this session.
- The full self-review/adversarial-review/consolidation cycle for "nothing
  advances on one pass" has only had its self-review stage (this document)
  completed - adversarial review and consolidation are still owed before
  any PR merges, per CLAUDE.md and per the task's own instructions.
