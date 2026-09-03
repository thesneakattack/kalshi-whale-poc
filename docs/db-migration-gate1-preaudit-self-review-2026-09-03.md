# Self-review: Gate 1 pre-audit (general-bucket, PR #508)

Reviewing my own artifact for internal consistency and unverified claims before independent
adversarial review, per this repo's PR-stage cycle requirement.

## Error found and fixed before this review (by a peer, not by me)

autotrade-1d caught a real mistake: the document originally said the assignment's "22 bare test
callers" figure for `tools/coordination_engine.py` was wrong, based on grepping only
`tests/test_coordination_engine.py` (15). It wasn't wrong — 22 is correct across four test files
that all call `coordination_engine._connect()` directly. Verified myself via
`grep -rnE '_connect\b' tests/ | grep coordination` before fixing (15+4+2+1=22, all bare),
fixed in commit `5f84635`. Flagging this here rather than treating it as invisible history: my
own "correction" pass wasn't thorough enough the first time — I checked the most obvious file
and stopped, rather than checking whether the module was imported/tested from elsewhere too.

## Checks run during this pass

- **Section count**: `grep -c "^### services\|^### tools"` → exactly 21, matching the assigned
  module list name-for-name (`grep -n "^### "` output cross-checked against the assignment's
  list line-by-line). No missing or duplicated modules.
- **The `@contextlib.contextmanager` sweep** (the document's central, most load-bearing claim —
  "all 21 modules leak") was run directly by me, not delegated to a subagent, specifically
  because delegating this exact check is what produced the `backup.py` error in the first
  gathering pass. Re-ran it again just now as part of this self-review to confirm the output
  hasn't changed since compilation: unchanged, all 21 still show a plain `def _connect()`, none
  decorated.

## Known, disclosed scope gaps (already stated in the document's own closing section)

- Several "event loop: not confirmed either way" lines (`series_cache.py`, `series_evaluator.py`,
  `trade_category.py`, `shadow_mode.py`, `whale_calibration/calibration_history.py`) are honest
  gaps, not silent assumptions — they should not be read as "confirmed NO" by whoever consumes
  this for the implementation plan.
- The document does not independently re-verify every subagent-reported line-by-line claim
  (PRAGMA statements, exact line numbers for CREATE TABLE/INDEX, DB_PATH isolation mechanics)
  against primary source — only the two highest-stakes claims (the universal leak, and the
  bare-call count) got a direct personal check. The rest rests on the four gathering
  subagents' reports, which is why an independent adversarial review is the right next step
  rather than treating this as fully self-verified.

## Items for the adversarial reviewer to specifically re-derive from source (per autotrade-1d's request)

1. `accounts_store.py`'s event-loop-blocking claim — `main.py:1828-1850`, four synchronous
   calls from async route handlers with no `tick_executor` wrapping visible.
2. `data_quarantine.py`'s event-loop exposure claim — via `series_watcher.py:384` →
   `record_trade()` L310 → `whale_stream/whale_stream_handlers.py:170`'s
   `async def _process_stream_trade()`.
3. `suggestion_decisions.py`'s event-loop exposure claim — `services/analytics/routes.py`
   L66/L79/L85.
4. The "all 21 leak, none `@contextmanager`" claim — re-run the sweep independently rather than
   trust this document's stated grep output.
5. The corrected 22-count (four-file breakdown) — re-verify the exact line numbers cited.

## Verdict

One real error found (by a peer) and fixed before this review; the fix itself was verified, not
just applied. Proceeding to independent adversarial review with the five items above as its
starting checklist, consistent with how PR #504's own adversarial review was scoped.
