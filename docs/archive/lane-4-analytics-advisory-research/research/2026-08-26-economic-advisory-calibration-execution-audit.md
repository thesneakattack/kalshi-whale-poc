# Economic Strategy Effectiveness — Advisory/Calibration Objective Audit & Execution Realism (E6-E7)

Companion: `docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-effectiveness-investigation.md`
(tasks E6, E7).

## E6 — Advisory/calibration objective audit

Classification taxonomy per execution program §6.1: economically aligned / partially
economic / accuracy-calibration-only / unknown. Applied per *function* where a module mixes
both (advisory_engine), not just per module, since a single blanket label would hide the
real split.

### `services/advisory/advisory_engine.py` — **partially economic, split along entry/exit
lines**

Grep for `cost_basis`/`realized_pnl`/`unit_cost` across every recommendation-generating
function:

| Function | Decides | Cost-aware? |
|---|---|---|
| `_entry_threshold_recommendation` | whether to suggest raising `strategy.entry_threshold` | **No** — buckets by confidence, compares win rate alone |
| `_longshot_bonus_recommendation` | longshot-bonus tuning | **No** |
| `_cross_variant_recommendations` | config-A-vs-config-B | **No** — compares `win_rate_pct` alone |
| `_rejected_candidate_recommendations` | gate loosening (reads the *old*, deduped `rejected_candidates` table, not `rejection_events`) | **No** — win rate + z-score/margin-of-error, but no cost term |
| `_category_conditional_recommendations` / `_series_conditional_recommendations` | category/series-segment tuning | **No** — compares segment `win_rate_pct` to book-wide `win_rate_pct` |
| `_exit_pct_recommendation` | take-profit/stop-loss sizing | **Yes** — normalizes by `cost_basis`, uses `realized_pnl` directly |
| `_auto_exit_threshold_recommendation` | `auto_exit` composite-scorer tuning | **Yes** — uses `realized_pnl` |
| `_sentiment_exit_recommendations` | sentiment-reversal exit tuning | **Yes** — uses `realized_pnl` |

Every entry-side/selection-side recommendation function is win-rate-only; every exit-side
recommendation function is cost/P&L-aware. This is the same finding
`services/advisory/CHEATSHEET.md` already recorded (2026-08-22/23) — re-verified directly
against current source in this pass, not re-derived from scratch, and confirmed unchanged
since. Practical consequence, concretely: `_entry_threshold_recommendation` cannot
distinguish "this confidence bucket wins more because the signal is genuinely better" from
"this bucket wins more because it's mechanically priced into the near-certainty band" — and
E4's own banded-EV evidence (this investigation, not the CHEATSHEET's) shows real KXBTC15M
gate-rejection data with exactly that shape (0.60-0.95-band rejects showing negative EV
despite respectable win rates). A live `entry_threshold` recommendation drawn from this
function today would be blind to that distinction by construction.

### `services/whale_calibration/confidence_calibration.py` — **accuracy-calibration-only,
not a partial case**

`grep -n "cost_basis\|realized_pnl\|unit_cost\|expected_value" services/whale_calibration/confidence_calibration.py`
returns **zero matches**. This module computes per-factor discrimination gaps and
confidence-vs-observed-accuracy calibration bands from `signal_log.resolved_signals_with_
factors()` — a pure accuracy/Brier-style calibration exercise by construction, with no cost
term anywhere in the file to be partially aware of. Its downstream write-back
(`blended_weights_for_auto_apply`, gated behind `confidence_calibration.auto_apply_enabled`
+ a typed confirmation phrase, same as advisory) tunes `whale_confidence_weights` purely
toward better-calibrated *accuracy*, not better-calibrated *EV*. This is a stronger, more
absolute finding than advisory_engine's split verdict — there is no cost-aware branch
anywhere in this module to weigh against the accuracy-only branches.

### `services/config_performance.py` — **reclassified: infrastructure, not a scored
mechanism**

Not itself a recommendation/tuning mechanism — it has no `_*_recommendation`-shaped
function at all. It is the fingerprint/applied-change-log persistence substrate both
`advisory_engine` and `confidence_calibration` write through
(`log_applied_change`/`recent_applied_changes`/`diff_patch`), used for the
*retrospective* "what happened after a change was already applied" reporting
(`GET /api/advisory/applied-changes`), not for deciding what to suggest. Forcing it into
the four-way taxonomy would misrepresent it; recorded here as a fifth category
("infrastructure") rather than mislabeled "unknown."

## E7 — Execution realism

`services/series_watcher.py`'s `book_context_at_entry()` (existing, reviewed function) joins
every real paper entry to the nearest `book_snapshots` row within its documented 30 s
match window and reports spread + a depth ratio. Ran via
`GET /api/diagnostics/series/KXBTC15M?hours=400` (read-only, no Kalshi network call — see
`services/diagnostics/CHEATSHEET.md`'s own no-network proof for `run_offline`'s sibling
routes; `book_context_at_entry` reads only the local `series_watcher.db`).

```
entries: 90
entries_with_book: 74          (82% matched within 30s)
mean_spread_pts: 0.98          (cents — ask_dollars - bid_dollars, ×100)
mean_depth_ratio: 8.44         (resting size at the crossed side ÷ position size)
entries_with_insufficient_depth: 12   (16% of matched entries, depth ratio < 1.0)
```

**Interpretation:**

- 82% book-context coverage for the current (post-2026-08-24-reset) trade population is
  good, but not universal — 16 of 90 entries have no reconstructable book context at all
  (either book capture wasn't running, or the entry fell outside the 30 s match window).
- Mean spread (0.98 cents) and mean depth ratio (8.44×) both look healthy on average — but
  averages hide the tail that matters for execution realism: **12/74 matched entries (16%)
  had depth ratio below 1.0**, meaning the paper broker's assumed fill (unconditionally at
  the signal's own price, `PaperBroker.open_position`) would very likely not have been the
  real fill price for those 12 — real execution against that resting book would have had to
  cross more of the book (worse price) or accept a partial fill, neither of which paper
  P&L reflects at all. This is a concrete, non-hypothetical instance of the execution
  program's concern that paper-mode economics may overstate real-world EV.
- **Hard replay-gap boundary:** `series_watcher.db`'s raw book-snapshot capture starts
  2026-08-19T13:53:33 UTC (`book_first_at` in the `capture` diagnostic). **No spread/depth
  reconstruction is possible for any KXBTC15M signal or trade before that date** — this
  covers the entire pre-cutover era analyzed in E1/E5 by definition; the execution-realism
  question can currently only be asked about data from 2026-08-19 onward, narrower even than
  the post-2026-08-23-cutover trade population E4/E5 actually has trades for.
- **Fill-probability / IOC-no-fill / partial-fill: does not exist and is not estimated
  here.** `kalshi_account_client.create_order` (real order placement) has never been
  exercised — `kalshi_account.trading_enabled` has never been flipped true in this app's
  history (per CLAUDE.md's own safety-invariant framing) — so there is zero real fill-
  outcome data anywhere in this app to calibrate a fill-probability model against.
  Fabricating one from the book-context data alone (e.g. "depth ratio < 1 implies X% fill
  probability") would be exactly the kind of invented precision the execution program's
  design constraint (§6, "do not fabricate precision the historical data can't support")
  forbids. The honest, buildable next step — not attempted in this research-only pass — is
  a **price-impact estimate** (not a fill-probability model): for entries with depth ratio
  < 1, walk the resting book size at the crossed side to estimate what price would actually
  have been needed to fill the full position size, and compare that estimated cost against
  the signal-price cost paper mode used. This is a Program 2 design candidate (see
  `docs/archive/lane-3-strategy-risk-execution/plans/2026-08-26-economic-strategy-remediation.md`), not built here.
- **Fees**: `series_watcher.reconcile()`'s own output already includes real fee data
  (`fees_paid: $737.14` over the 90-trade post-cutover window, `fee_drag_pct_of_cost: 2.52%`)
  — this is not a gap, `services/kalshi_fees.py` computes real Kalshi fee schedules and
  `PaperBroker` applies them to every paper trade already. Recorded here for completeness of
  the "fees/slippage" required-evidence item, not re-derived.
