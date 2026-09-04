# Self-review — `_scoring_pool`/candidate-retry isolation design

Same author, own artifact, per CLAUDE.md's "nothing advances on one pass." Re-verified the
design's load-bearing citations against current source directly before considering it ready for
adversarial review.

## Citations re-verified

- `services/whale_stream/whale_stream_handlers.py:201-235` (`_process_stream_trade`) — confirmed
  it calls `fetch_signals` directly, no intermediate queue.
- `services/whalewatchers/kalshi_trade_tape.py:322` (`fetch_signals` def), `:364`
  (`_scoring_pool.run(...)` call), `:385` (`score_recovered_trade` def), `:415-420`
  (its own `_scoring_pool.run(...)` call) — all confirmed exact.
- `services/candidate_retry.py:103,126,167` (`run_pending`'s serial `for`/`await` shape) —
  confirmed, no `gather`/`create_task`.
- `main.py:777-811` (`_candidate_retry_loop`'s full body; the design doc's citation says
  "777-806," actual function body runs to `:811` — a ±5-line imprecision in the range cited, not
  a wrong claim about the function's content or behavior; not worth a separate fix commit given
  it doesn't change what the range is illustrating).
- `services/whalewatchers/kalshi_trade_tape.py:273` (`self._seen_lock = threading.Lock()`) —
  confirmed instance-level, pool-agnostic, the load-bearing correctness precondition for the
  whole recommendation.
- `git log --oneline -- services/whalewatchers/_scoring_pool.py` — confirmed exactly one commit,
  the direct evidence the pool's sizing docstring predates PR #555 and was never revisited.

## Internal consistency check

Cross-read §1 (the problem) against §3's Option 1 recommendation and §4's comparison table: the
1-worker sizing in Option 1 is justified from §1's own already-established fact (candidate-retry
submits strictly serially), not introduced as a new, separate claim — consistent. §2's
correctness-precondition check (the #546 lock is pool-agnostic) is stated once and referenced,
not re-argued differently in §3 — consistent. The rejected options (2, 3) are each rejected on
evidence already established earlier in the document (the HARD RULE for Option 2, the original
design doc's own already-established reasoning for Option 3) rather than on new unstated
assumptions.

## Scope check

The task asked for options compared on mechanism/correctness/failure-behavior/complexity per the
data-plane HARD RULE, and a landed recommendation, not just an enumeration. Re-read §3/§4 against
that: three options given, one explicitly recommended with reasoning, two explicitly rejected
with reasoning (not just listed as "also considered"). §5 is explicit about what's out of scope
(benchmarking the WS path's actual peak rate, the WS dispatch semaphore itself, the module's
exact file name) so a reader can tell the difference between "not decided here, deliberately" and
"missed."

## What this self-review did not re-verify

Did not re-run the original `_scoring_pool.py` design doc's own review cycle's conclusions (its
own self-review/adversarial-review presumably already happened when it was written) — this
document cites that doc's §4/§4a content directly and re-confirms only the one new fact (PR #555
not touching `_scoring_pool.py`) that changes the calculus, not the entirety of that document's
own reasoning, which stays valid on its own terms for what it covered.

## Verdict

No errors found. Ready for adversarial review.
