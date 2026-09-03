# Self-review: `edge-gate-retro-measurement-2026-09-03.md`

Own-context review before adversarial review, per CLAUDE.md's "nothing
advances on one pass" HARD RULE.

## Caught and fixed during self-review

The §1 measurement block originally listed "no p_pre" (123,886),
"category=None" (40,801), and "fully attached" (49,915) as three sequential
bullets under the 173,801 total — readable as three disjoint partitions
summing to the total, which they are not: `category=None` is a *subset* of
`fully attached` (40,801 of the 49,915 attached rows lack a category), not
a third exclusion bucket. Verified the real arithmetic: 123,886 + 49,915 =
173,801 (the two genuinely disjoint buckets); 49,915 − 40,801 = 9,114
(the rows that actually feed a calibration cell) — this second number
matched the sum of all 18 printed per-cell sizes exactly (152+155+...+2235
= 9,114), which is what caught the ambiguity in the first place: if the
three bullets had been disjoint, the per-cell sizes should have summed
close to 49,915, not 9,114. Rewrote the block to show the subset
relationship explicitly and added a sentence flagging the 81.8%
category-loss rate as its own finding, not just a footnote to the cell
count.

## Numeric consistency spot-checks

- 123,886 + 49,915 = 173,801 ✓ (two disjoint top buckets)
- 49,915 − 40,801 = 9,114 ✓ (rows actually entering a cell)
- Sum of all 18 printed cell sizes = 9,114 ✓ (matches the above exactly)
- §3: 196 + 123 = 319 ✓ (fail-open + evaluable = total checked)
- §3: 92 + 31 = 123 ✓ (rejected + admitted = evaluable)
- §3: 54 + 69 = 123 ✓ (in-band + out-of-band checked = evaluable — every
  evaluable trade falls into exactly one band bucket, no double-count)
- §3: 39 + 53 = 92 ✓ (in-band rejected + out-of-band rejected = total
  rejected)

## Citation spot-check

Re-read each cited range directly against the file a second time,
independent of the read used while writing the doc: `strategy_engine.py:
287-308` (`_edge_gate_check`'s core math), `:731-736` (the reject-path
caller, confirming `record_rejection`'s actual arguments), `:811-812` /
`:836-837` (the two `decision["edge_gate"]` attachment sites),
`confidence_calibration.py:511-534` (`recompute_deltas`'s attachment
loop), `candidate_log.py:116-119` (`record_rejection`'s signature). All
five match what the doc quotes or paraphrases.

## Methodology, stated plainly for the reviewer

- All measurements came from one script (read-only `sqlite3` URI
  connections plus direct calls into the real, unmodified `services.*`
  functions — `confidence_calibration.recompute_deltas`,
  `trade_category.categories_for_tickers`, `market_history.recent_price`,
  `kalshi_fees.unit_cost`/`taker_fee_per_contract`), deleted after use, not
  committed. §3's per-trade simulation inlines `_edge_gate_check`'s exact
  formula rather than calling the function directly, since the function
  needs a live `ticker`/`series_cache` lookup for its flat-fee-type
  early-out that a standalone script can't easily replicate — checked that
  omission doesn't matter here by confirming none of the 319 sampled
  tickers are on a flat-fee series (all are `KX*` binary/scalar markets,
  visually confirmed against the ticker list in the debug output).
- A real bug was caught and fixed mid-measurement, not swept past: the
  first script run returned all-zero results because the script's own
  directory (this worktree, which has its own git-checked-out `services/`
  copy) shadowed the real `/app/services` package on `sys.path[0]`,
  silently redirecting `signal_log.DB_PATH` to an empty path inside the
  worktree instead of the live `/app/data/signal_log.db`. Fixed with an
  explicit `sys.path.insert(0, "/app")`; the doc's numbers are all from the
  corrected run. Documented here since it's exactly the kind of silent-
  wrong-answer failure mode CLAUDE.md's "never guess" rule exists to catch
  — a script that ran without error and printed plausible-looking zeros
  would have been very easy to report as a real "recompute_deltas returns
  no cells" finding if not checked against the DB directly first.
- The 71.3%/61.4% "no p_pre" rates were verified as a real data
  characteristic, not a script parameter bug, by widening the lookback
  window to 7 days for a sample of failures and confirming `recent_price`
  still returned `None` even then, while the *same ticker* queried as of
  right now returned a real price — ruling out "the window is just too
  narrow" and pointing at "no snapshot existed yet at trade time" instead.

## What this doc does NOT do, stated explicitly

Does not implement observe-only mode or touch `record_rejection`'s
signature — §4 sizes the work, doesn't do it. Does not investigate the
71.3%/61.4% `market_history` coverage gap's own root cause beyond
confirming it's real and characterizing which market types it concentrates
in — flagged as a separate follow-up, not chased to full resolution here
since it wasn't the question asked. Does not change `config/settings.yaml`
or any strategy code.

## Addendum: §3.5 follow-up measurements (added after this self-review, before the doc's adversarial review lands)

The PM requested three additional measurements after reading the initial
doc: in-band vs out-of-band coverage of the un-evaluable 61.4%, whether
that gap is config- or data-limited, and rejection-rate sensitivity to
`min_edge`. Added as §3.5 using the same script, same live data, same
already-verified methodology (no new mechanism, just more slices of the
same computation) — run immediately after §3's own numbers, no meaningful
gap for the underlying data to drift. Spot-checked arithmetic before
committing: in-band (54+70=124) and out-of-band (69+126=195) both sum
correctly and 124+195=319 matches the total; the in-band/out-of-band
*evaluable* counts (54, 69) match §3's own band-checked counts exactly,
confirming the two measurements are drawing from the same underlying
per-trade computation rather than a second, potentially-diverging one.
The `min_edge` and `max_age_sec` sensitivity sweeps are both monotonic in
the expected direction (lower `min_edge` → fewer rejections; wider
`max_age_sec` → more coverage), which is a basic sanity check any bug in
the loop logic would likely have broken.

This addendum was written before the doc's adversarial review agent's
result was known — if that review's scope predates this section (timing
not yet confirmed), §3.5 should get its own check as part of consolidation
rather than being treated as already covered.

## Verdict

GO, with the §1 presentation fix already applied and the §3.5 addendum
self-checked as above. Ready for (or already in) adversarial review.
