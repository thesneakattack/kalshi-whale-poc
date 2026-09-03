# Self-review: `persistence-layer-baseline-2026-09-03.md`

Own-context review before adversarial review, after rebasing this branch onto current `origin/main` (was 29 commits behind; clean rebase, no conflicts).

## Real correction made during this review, not just the one flagged

The coordinator flagged the "Net: 25 modules (24 services, 1 tools)" line as a labeling slip (the list actually named 25 services/ modules, not 24). Fixing that specific line surfaced a **second, larger counting error this review found independently**: the doc's own header claimed "38 files (37 real modules + one README)," but a fresh `grep -rl 'def _connect\|sqlite3.connect' services/ tools/ main.py` run today returns **40 files (39 real modules + one README)** — and the doc's own four pattern-buckets (leaking / Tier0-fixed / already-closing / pooled) already summed to 39, not 37, even in the original version. The header total never matched the body's own arithmetic. Fixed both:
- Header: "38 files, 37 real modules" → "40 files, 39 real modules" (§1's title and intro).
- Leaking-bucket count: "25 modules" → "26 modules" (both the bucket header and the "Net:" summary line and the closing summary section) — the bucket already named 25 `services/` modules plus `tools/coordination_engine.py`, which is 26, matching the coordinator's own count from the pattern-analysis follow-up.

Verified the corrected total holds: 26 (leaking) + 5 (Tier0-fixed) + 5 (already-closing) + 3 (pooled/cached) = 39, matching the header exactly. No other module was added, removed, or reclassified — this was purely a header/summary-line arithmetic fix, not a re-classification.

## Re-verification after rebase

- Re-ran the module-discovery grep fresh (not from memory of the original run) — same 40 files, same list, confirms no new `_connect()`/`sqlite3.connect()` call sites landed on `main` in the 29 commits this branch was behind.
- Did **not** re-query `data/fault_log.db` or re-check live fd counts / DB file sizes for this review — those are point-in-time measurements (explicitly labeled with UTC timestamps in the doc itself) and are expected to have drifted since original capture; re-querying them now would produce a *different*, not more-correct, snapshot. Flagging this explicitly so a reader doesn't expect §2/§3/§4's numbers to match a fresh query today - they're accurate as of when they were taken, not asserted as current.

## Internal consistency

- The `_connect()` pattern classification (§1) and the cross-referenced module names in §2's fault table (`market_history`, `capture_writer`) are consistent - both correctly reflect that `market_history.py` is Tier0-fixed while `capture_writer.py` was always in the "already closes correctly" bucket, not conflated.
- The "Summary for the implementation plan" section's bullet points are each traceable to a specific numbered section above them - spot-checked each of the four bullets against its source section rather than trusting the summary was transcribed correctly.

## Verdict

GO, with two real corrections made during this pass (documented above, not silently applied) - both are counting/labeling fixes to already-correct underlying data, not new claims or reclassified modules. Ready for adversarial review.
