# Adversarial review — `_seen_trade_ids`/`_seen_order` race root-cause doc (issue #546)

2026-09-06. Stage 3 of the review cycle for
`2026-09-03-seen-trade-ids-concurrency-race-root-cause.md` (as postscripted
during its 2026-09-06 recovery from the orphaned branch
`fix/seen-trade-ids-concurrency-race-investigation`). Fresh Agent call, no
memory of the recovering session. Stance: every load-bearing claim re-derived
from primary sources — `git show d386b73:...` (pre-fix source),
`git show a84d35c`/`7beb658`/`3ff41d4`/`a6ef68b`/`5a608d1`/`8013260`, and
current source in the checkout — never from the document's own quotes.
Lean-sized per the 2026-09-05 lean-execution amendment; findings + verdict.

## Findings

1. **Pre-fix "zero locking" + quoted code + line citations** — verified against
   `git show d386b73:services/whalewatchers/kalshi_trade_tape.py`: no
   `Lock`/`threading.` anywhere; `_mark_seen` exactly 263-270, verbatim;
   `__init__` 223-253, no sync primitive; gate at 541, mark at 564; enqueue
   branches 456-465 (enqueue 463) and 470-486 (enqueue 486); H4 quote at
   559-561; one production `_mark_seen` call site. The self-review's
   "re-verified" claim holds. **CONFIRMED.**
2. **Concurrency mechanism** — `_candidate_retry_loop` independently supervised
   (current `main.py:838`, supervise at :1545-1547, 5.0s interval), no
   cross-path synchronization; `candidate_retry.py:167` call site intact.
   **CONFIRMED** as written for 2026-09-03. The "same ThreadPoolExecutor"
   wiring is stale today — issue #563 split candidate-retry onto
   `_candidate_retry_pool.py` — see finding 7.
3. **Central technical claim** (eviction loop GIL-safe via thread-local
   `oldest`; real hazard is the check-then-act gate race on the same trade_id)
   — reasoning holds; the shipped regression test's no-op-lock variant
   empirically reproduces both threads passing the gate. One non-material
   wording inversion in §2: `fetch_signals` calls
   `_scoring_pool.run(lambda: self._process_trades_timed(...))`, which calls
   `_process_trades_sync` in-thread — the doc has the lambda wrapping
   `_process_trades_sync` from `_process_trades_timed`. Conclusion unaffected.
   **CONFIRMED** (minor wording defect noted, left unfixed — recovered text is
   verbatim by design).
4. **Git-historical narrative** — all three hashes/dates/messages exact
   (a84d35c 2026-08-11, 7beb658 2026-08-28, 3ff41d4 2026-09-01); a84d35c
   introduced the then-true single-caller docstring claim; 7beb658 moved
   `run_pending` out of the tick body; 3ff41d4 added only `_scoring_pool.py` +
   test. **CONFIRMED.**
5. **Postscript accuracy** — current file: `self._seen_lock = threading.Lock()`
   (:273), `_mark_seen`(:283)/`_mark_seen_locked`(:296) split, lock held across
   the full check-then-act span (:645-664), #546 cited in comments;
   `tests/test_kalshi_trade_tape_seen_lock_race.py` exists and reproduces the
   race (read, not executed — CI is the run record). "§7's Option A" is the
   correct characterization of the shipped fix. Recovered body byte-identical
   to `8013260`'s version. **CONFIRMED.**
6. **Precedent claims** (`series_watcher.py` `_buffer_lock` :126-142,
   `game_state.py` "threading.Lock, not asyncio.Lock" :72-73, same pattern in
   `index_feed/ingestion.py` and `settlement_edge.py`) — all as quoted.
   **CONFIRMED.**
7. **Material-mismatch gate** — one drift the original postscript did not name:
   the shared-`_scoring_pool` topology (§2/§3) was superseded by issue #563's
   pool split. Assessed **non-material** (postscript freezes the body as
   2026-09-03 text; the race mechanism and fix are pool-topology-independent,
   as the current test header itself states). Recommended fix: one postscript
   sentence naming the #563 split. Self-review's four flagged items all
   dispositioned without a blocking finding.

## Verdict

**GO** — accurate as a historical root-cause record with the postscript; sole
recommended amendment is the one-line #563 note (applied to the postscript in
the same recovery PR, verified against `kalshi_trade_tape.py:421` and
`_candidate_retry_pool.py` before applying).
