# Consolidation — `_seen_trade_ids`/`_seen_order` race root-cause doc (issue #546)

2026-09-06. Stage 4: reconciles the research doc
(`2026-09-03-seen-trade-ids-concurrency-race-root-cause.md`), its 2026-09-03
self-review, and the 2026-09-06 adversarial review into a verdict. Lean-sized.

- Self-review (2026-09-03): fixed 4 citation errors in the draft; flagged 4
  items for adversarial attention. Adversarial review (2026-09-06): re-derived
  every load-bearing claim from `git show` of pre-fix/historical commits and
  current source — findings 1-6 all CONFIRMED, self-review's 4 flagged items
  dispositioned non-blocking (duplicate-signal empiricism is now settled by the
  shipped regression test's no-op-lock reproduction).
- No disagreement between the two reviews to adjudicate.
- Fix list from adversarial review: (1) one postscript sentence naming the
  issue #563 candidate-retry pool split — **applied** in this recovery PR
  (verified against `kalshi_trade_tape.py:421` + `_candidate_retry_pool.py`
  before applying). (2) §2's `_process_trades_timed`/`fetch_signals` lambda
  wording inversion — **deliberately not applied**: non-material, and the
  recovered body stays byte-identical to commit `8013260` by design (recovery,
  not authorship); this file is its record.
- Context: the fix this doc recommends (Option A, `threading.Lock`) shipped
  separately and is live; this PR lands the doc as the permanent root-cause
  record, recovered verbatim from the orphaned branch
  `fix/seen-trade-ids-concurrency-race-investigation` (`5a608d1`, `8013260`)
  plus the dated postscript.

**Verdict: GO** — land the recovered record.
