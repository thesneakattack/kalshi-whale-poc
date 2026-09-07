# Strategy Edge Gate Design

## Status

Design document (2026-09-03), still paper-mode only. Research this design implements:
`docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`
§3 ("Is the app's strategic approach even good?"), §3.3's five-step sketch, §3.4, §10.2,
§11 items 8–9, §12 open question 1; amended (unchanged on this topic) by
`docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-second-pass.md` §6.7, and its
revised plan §8 items 20 (EV gate + markout measurement) and 27 (`market_analyst_agent`
cleanup + category fair-value anchors, scoped here as Phase 2, §7). Per CLAUDE.md's
"nothing advances on one pass" HARD RULE this is the design stage only: it needs its own
self-review (appended below), a separate adversarial review (a fresh Agent call with no
memory of this session), and consolidation before an implementation plan is written. No
code, config, or data changed while producing this document.

## Goal

Give `strategy_engine.evaluate()` an entry gate that compares the app's own belief about a
market to the price it would actually pay, instead of gating entry on confidence alone.
Confidence stays an input (it already scales Kelly sizing); it stops being the only thing
standing between a whale print and a trade.

## Non-goals

- No change to `kalshi_account.trading_enabled`, the daily-loss kill switch, or any
  live-trading gate. This is Program 1–2 (paper trading) work per CLAUDE.md's standing
  goal.
- No rebuild of `market_analyst_agent` — it already exists, already has a real (if
  currently near-zero-weight) integration point, and the audit is explicit that
  rebuilding it isn't warranted (§3.4).
- No change to `_effective_entry_threshold`'s confidence bar, `kelly_scaled_max_size`, or
  any existing gate's semantics — the edge gate is additive, sitting alongside them, not
  replacing them.
- No category-specific fair-value model (options-IV, Poisson scoreline, base-rate tables)
  in this phase — deferred to Phase 2 (§7), per the audit's own "later, optional" framing
  (§3.3 item 4, second-pass item 27).
- Zero code changes in this document — design only.

---

## 0. Critical finding, verified directly: is `market_analyst_agent` wired into real trade decisions?

The audit flagged this as unresolved and explicitly warned against inferring it from a
`grep` (§12 open question 1, §3.4). Verified here against the actual call graphs, not a
`grep` count.

**`strategy_engine.evaluate()` itself (`services/strategy_engine.py:285-684`, read in
full): zero calls into `market_analyst_agent`.** The only reference to `market_analyst` in
the file is `services/strategy_engine.py:63`, inside `kelly_scaled_max_size`'s docstring,
listing `market_analyst.enabled` as an example of the "ships fully built, opt-in"
precedent — prose, not executable code. This confirms the audit's own (corrected)
citation.

**But `market_analyst_agent.analyst_lean()` *is* wired into the composite scores that feed
both the entry and exit paths — with materially different live weight on each side:**

- **Entry side (confidence, which `evaluate()` does gate and size on):**
  `services/whalewatchers/kalshi_trade_tape.py:199-211`'s `_analyst_factor()` calls
  `market_analyst_agent.analyst_lean(ticker, max_age_sec=_ANALYST_FRESHNESS_SEC)`
  unconditionally on every real trade-tape print and folds the result into
  `composite_confidence_breakdown` (`services/confidence_scoring.py`) as factor 8,
  `analyst_factor`. That composite becomes `signal.confidence`, which
  `_validate_entry_price` gates on and `kelly_scaled_max_size` sizes with — so this *is*
  a real path into `evaluate()`'s decision, structurally. **But the live, currently
  deployed weight is `config/settings.yaml`'s `whale_confidence_weights.analyst_factor:
  0.0`** (vs. the code's own `DEFAULT_WEIGHTS["analyst_factor"] = 0.13`,
  `services/confidence_scoring.py:132`) — a deliberate override, not an oversight (the
  same config block carried a comment documenting a 2026-08-30 factor-by-factor audit
  that re-floored several other factors and left `analyst_factor` at 0 "pending a human
  decision," `docs/open-decisions.md` — **that comment is currently absent from the live
  primary-repo `config/settings.yaml`, per an uncommitted in-progress edit already tracked
  as "the third data-wipe of the same shape" in
  `docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-second-pass.md` §4.4; a reader
  of the live file today would not find this justification written down, only in git
  history/this worktree's checkout — the underlying `0.0` value itself is unaffected and
  still confirmed live**). **Net effect: the code path
  runs on every print, but its output cannot currently move `signal.confidence` by even
  one part in a thousand** — the weight is exactly zero, not merely small. (Line numbers
  for these `config/settings.yaml` keys are intentionally omitted throughout this
  document — the file has already shifted twice under uncommitted UI-driven edits during
  this design's own authoring window, per the adversarial review that checked this
  section against the live primary-repo file, not just this worktree's committed copy.)
- **Exit side (`auto_exit_enabled`, a real automated position-closing decision):**
  `services/exits/exit_engine.py:570-578`'s `_exit_confidence()` reads the same
  `analyst_lean()` (cached per-tick) into an `analyst_divergence` factor, weighted by
  `strategy.auto_exit_analyst_weight`, **live-configured to `0.5`** — not zero.
  `strategy.auto_exit_enabled` is also **live `true`**. So when `analyst_lean()` returns a value,
  it currently carries real, non-trivial weight (tied with `auto_exit_pnl_weight: 0.85`
  and `auto_exit_sentiment_weight: 1.5` in the same weighted average) in a decision that
  actually closes paper positions.
- **The gate on how often either path fires is the same: `analyst_lean()` only returns
  non-`None` when a market_analyst_agent analysis exists for that exact ticker and is less
  than `_ANALYST_FRESHNESS_SEC` (24h) old, and per
  `services/analytics/routes.py:178-186`'s own comment, analyses are triggered
  **only** by a human clicking "Analyze" on one specific market — "replaces the earlier
  automatic per-tick background scan ... nothing runs on a schedule anymore." Both call
  sites' own docstrings independently describe the neutral/absent case as "the
  overwhelmingly common case, not an edge case" (`services/confidence_scoring.py:312`) —
  i.e., in practice `analyst_lean()` returns `None` for the vast majority of ticks on the
  vast majority of tickers, regardless of either weight.

**Precise, falsifiable characterization, replacing the audit's provisional "still
advisory-only":** `market_analyst_agent` is not wired into `evaluate()` directly, and its
entry-side wiring is live but zero-weighted by deliberate config choice. Its exit-side
wiring is live, non-zero-weighted, and does influence real (paper) auto-exit decisions —
but only ever for the small number of tickers a human has personally analyzed in the last
24 hours, since nothing schedules an analysis automatically. This is a third answer,
distinct from both "still advisory-only" and "wired into decisions" — structurally wired
on both sides, functionally near-zero-influence on both, for two different and independent
reasons (a zero weight on one side, population sparsity on both). Evidence: file:line
citations above, plus direct reads of `config/settings.yaml:92,97,132,181-215` and
`services/confidence_scoring.py:132,299-312`. Falsifier for anyone re-checking this later:
`grep -n "analyst_factor\|auto_exit_analyst_weight\|auto_exit_enabled" config/settings.yaml`
and compare against `DEFAULT_WEIGHTS` in `services/confidence_scoring.py` — if either live
value has changed, this characterization needs re-deriving, not assumed to still hold.

**Implication for this design:** the new `p_est`/`edge` mechanism below is a separate,
purpose-built pathway, not a re-use or extension of `analyst_lean()` — it has to run on
every whale print (not only the rare manually-analyzed ticker), so it cannot depend on a
human clicking "Analyze." This design does not touch `whale_confidence_weights.analyst_factor`,
`auto_exit_analyst_weight`, or `auto_exit_enabled` — those are the config owner's existing,
already-deliberate choices, not incidental defaults this work happens to pass through.
Phase 2 (§7, = second-pass item 27) is where `market_analyst_agent`'s estimate could later
become one more input to `p_est` once the core mechanism here is live and measured, per the
audit's own sequencing ("once #11's core mechanism is live and measured," first-audit §13
item 19 / second-pass item 27) — not decided here.

---

## 1. The arithmetic and the fee-model caveat

### 1.1 The formula, with units stated

```
EV per contract [$]  =  p_true [prob, 0-1]  −  P [$/contract]  −  f(P) [$/contract]
edge [$/contract]    =  p_est  −  ask_now  −  f(ask_now)
```

For a Kalshi event contract, price and probability are numerically interchangeable by
construction (a contract pays exactly $1 if the event resolves in the held direction, $0
otherwise, so a fair price *is* the probability, in dollars-per-contract) — this identity
is why the formula above can mix a `[prob]`-dimensioned term and `[$/contract]`-dimensioned
terms without an explicit conversion factor, not because the units are actually the same
label. This is stated once here rather than re-derived at every call site, in the same
spirit as `services/kalshi_fees.py::unit_cost`'s own docstring already does for cost.
**Side convention, to avoid this app's own two historical bugs in this exact spot** (the
no-side `1 − price` inversion, and the `equity − starting_bankroll` mislabel, both named
in CLAUDE.md's "a displayed value must match its label" section): every quantity in this
design is **side-relative** by construction, computed via `kalshi_fees.unit_cost(side,
yes_price)` — the same function `_validate_entry_price`/`evaluate()` already use for
`unit_cost` — never by hand-deriving `1 - price` at a new call site. `p_est`, `ask_now`,
`P_pre`, and `q_pre` below are all "implied probability that *this signal's own side*
wins," not "yes probability."

### 1.2 Kalshi's fee model, verified directly against `docs/kalshi/` (not from the audit's summary)

Re-checked directly for this document, not trusted from the audit's citation:

- **`docs/kalshi/fee_rounding.md`** (read in full): "**Net fee** = trade fee + rounding fee
  − rebate (always >= \$0.00)." The trade fee is "rounded up to the nearest `$0.000001`";
  the rounding fee "restores the user's target balance precision" ($0.0001 for direct
  members, $0.01 otherwise); the rebate is "a refund from accumulated rounding
  overpayment." `services/kalshi_fees.py` (read in full) implements only the first
  component (`taker_fee()`'s `math.ceil(raw * 1_000_000) / 1_000_000`) — confirmed
  directly, not merely cited: there is no rounding-fee or rebate arithmetic anywhere in
  that module. **`f(P)` as currently computed is a lower bound on net fee, confirmed
  against the primary source, not just the audit's paraphrase of it.**
- **`docs/kalshi/get-series-list.md:198-208,260-271`** (read in full): the `FeeType` schema
  documents four values — `quadratic`, `quadratic_with_maker_fees`,
  `quadratic_with_combo_maker_fees` (all three described by the same "General Trading Fees
  Table," i.e., the 0.07·P·(1−P) formula, differing only in maker treatment — confirmed by
  `services/kalshi_fees.py:89-97`'s own docstring, which is more precise than the audit's
  summary: **the taker rate never changes across these three**, only maker does), and
  `flat`, described by a separate "**Specific Trading Fees Table**." **`kalshi-fee-schedule.pdf`
  read page by page for this document: that PDF contains a "General Trading Fees Table"
  (pages 4–5, the same 0.07·P·(1−P) schedule), a "Non-Standard Fees" per-series
  maker/taker-multiplier table (pages 6–11), and a "Perpetual Futures Fees" table (page 12,
  bps-tiered, for margin/perps products this app does not trade) — no "Specific Trading
  Fees Table" appears anywhere in the 12-page document.** The `flat` fee type's own
  referenced table is not present in this repo's mirrored docs at all, confirming
  `kalshi_fees.py:98-102`'s own docstring is accurate, not merely cautious: "`flat` ...
  isn't modeled here ... inventing its formula from the enum name alone would be exactly
  the guess CLAUDE.md's 'never guess' rule forbids." **This is a real, live, currently
  un-closed gap, not a hypothetical one**: `services/series_cache.py` already persists
  each series' raw `fee_type` from `GET /series`, so the app *has* the data needed to know
  when a ticker is on the undocumented formula — nothing currently reads it before pricing
  a trade.
- `services/kalshi_fees.py::taker_fee`/`taker_fee_per_contract` already do more than the
  audit's simplified citation suggested: they apply a per-series multiplier table
  (`_FEE_MULTIPLIER_BY_SERIES`, live-verified against all 13,029 real series 2026-08-15)
  and an event-level override (`_event_fee_override`, from `title_cache`, itself sourced
  from `docs/kalshi/get-event-fee-changes.md`'s documented override semantics) — this is
  the right function to reuse for `f(ask_now)` below, not a hand-rolled flat 0.07 formula.

### 1.3 How this design avoids silently under-stating fees

Three separate, additive measures — none of them alone is sufficient, which is the point:

1. **Reuse, never re-derive.** `f(ask_now)` in the edge formula is always
   `kalshi_fees.taker_fee_per_contract(ask_now, ticker)` — the existing, per-series/event-
   aware, already-tested function — never a fresh `0.07 * p * (1-p)` literal. This alone
   fixes nothing about the lower-bound problem, but it does prevent a *second*,
   independently-drifting fee formula from existing in the codebase (the DRY concern §9 of
   the first audit raises generally, applied here specifically).
2. **A fee-uncertainty buffer, not a point estimate treated as truth.** The gate compares
   `edge` against a threshold `min_edge`, where `min_edge` is deliberately set with the
   known lower-bound gap in mind, not at the fee formula's own value. `fee_rounding.md`'s
   worked example (a $0.055 fill) shows the rounding-fee component landing at roughly a
   third of the trade fee's own magnitude in that example ($0.001361/$0.003639 ≈ 0.374) —
   not a general bound, just
   evidence the gap is a real, non-negligible fraction of the modeled fee, not a rounding
   artifact safe to ignore. Config field `strategy.edge_gate_fee_buffer_usd` (§5) adds a
   fixed per-contract pad to `f(ask_now)` before the comparison — conservative in the
   `edge < min_edge` direction, i.e., it can only make the gate *more* restrictive, never
   less. Default is non-zero (§5) specifically so the gate cannot ship silently trusting
   the lower bound as if it were the net fee.
3. **A hard fail-closed check on `flat`-type series, not a silent mis-price.** Before
   computing `edge` for a ticker, the gate reads that ticker's series `fee_type` from
   `services/series_cache.py` (already persisted, zero new fetch). If `fee_type ==
   "flat"`, the gate returns "unknown fee model" rather than computing an edge at all —
   the same "fail open on missing data, but count and log it, never guess" idiom
   `strategy_engine.py`'s own `_record_me_gate_unknown` already established for the
   mutually-exclusive gate (issue #267). This is deliberately **fail-closed for the gate
   specifically** (no trade admitted through *this* mechanism) while leaving the
   pre-existing confidence-only gates untouched — i.e., a `flat`-type market can still
   trade exactly as it does today (this design's other config field,
   `strategy.edge_gate_enabled`, governs whether the new gate applies at all; §5), it just
   never gets to claim a positive-edge admission it cannot actually support. This affects
   an unknown, currently-unmeasured number of series — `kalshi_fees.py`'s own docstring
   states, undated, "no market/event in this app's own data has ever resolved to it" — worth
   re-confirming with a live count before this ships to a plan, not assumed still true.

What this explicitly does **not** do: claim to compute the true net fee. The rounding-fee
and rebate components depend on the account's own accumulated per-order rounding state
(`fee_rounding.md`'s "Fee Accumulator," carried per order across fills) — data this app has
no way to reconstruct for a hypothetical future order before it's placed, real or paper.
Modeling that precisely is out of scope for this design; the buffer in (2) is a stated,
conservative substitute for it, not a claim of exactness.

---

## 2. Settled history and `Δ_calibrated`

### 2.1 What "settled history" means here

**Primary table: `signal_log.db`'s `signals` table**, not `candidate_log.db`. Reasoning:
`signal_log.signals` (`services/signal_log.py:55-67`) logs every whale print `evaluate()`
was ever called with — traded or not — carrying `ticker`, `side`, `price` (the print's own,
post-impact price), `seen_at`, `series`, `factors_json` (the full 9-factor confidence
breakdown, for real providers), `raw_notional_usd`/`raw_spread`/`raw_volume_24h`, and,
once settled, `resolved`/`correct`/`resolved_at`. `correct` is defined precisely at its one
call site, `main.py:301`: `correct=(result == item["side"])` — 1 exactly when the market
settled in the direction the whale's own print bet, 0 otherwise. This is the outcome label
`Δ_calibrated` needs, already computed, already stored, for the app's real whale-print
population (not only the ones that became trades) — the completeness the data-plane rule
asks for. `candidate_log.db`'s `rejected_candidates`/`rejection_events` is a narrower,
complementary table (only *rejected* candidates, per-gate) — reused in §6 for validating
whether the new gate is over-rejecting relative to what those candidates would have done,
not as the primary training set.

**A row's `price` column is the print's own price, not `P_pre`** — per the audit's own
§3.2 argument (a follower buys post-impact), reusing `signal.price` as if it were the
pre-print anchor would defeat the whole mechanism. `P_pre` for a *historical* row has to be
independently reconstructed the same way it will be for a live signal (§3.1 below):
`market_history.recent_price(ticker, max_age_sec, as_of=seen_at)`. This means building the
training set is not a single `SELECT` — it's one query against `signal_log.signals` for the
resolved population, joined in application code against one `market_history.recent_price`
lookup per row. At current volume (`resolved_signals_with_factors()`'s own docstring:
~103k+ rows, ~1s unscoped) this needs the same `since_ts` bounding the audit's Tier 1 item
3 already recommends for that sibling query — a new function here,
`signal_log.resolved_signals_for_edge_calibration(since_ts=None)`, selecting `ticker,
side, price, seen_at, correct, factors_json, series` (extending, not replacing,
`resolved_signals_with_factors`'s existing query shape), always called with a bounded
`since_ts` from this feature's own call sites even though the function itself keeps the
existing unscoped default for parity with its sibling.

### 2.2 The target quantity, precisely

For a settled historical signal: `q_pre = kalshi_fees.unit_cost(side, P_pre)` (side-relative
implied probability the print's side was winning *before* the print), `y = correct` (1 if
that side actually won). The quantity being estimated is:

```
Δ_calibrated(features)  =  E[y | features]  −  E[q_pre | features]
```

in probability units (dimensionless, range realistically small, bounded in
`[-1, 1]` by construction). For a live signal: `p_est_side = q_pre_now +
Δ_calibrated(features_now)`, where `q_pre_now = kalshi_fees.unit_cost(signal.side,
P_pre_now)`. This is the audit's §3.3 step 1 sketch (`p_est = P_pre + Δ_calibrated`), made
concrete and side-consistent.

### 2.3 Two design alternatives for computing `Δ_calibrated`, compared

**Alternative A — bucketed mean, extending the existing calibration machinery.**
`services/whale_calibration/confidence_calibration.py`'s `_bucket_win_rates`/`_factor_report`
already split resolved signals into tertiles by a factor and compute a per-bucket outcome
rate, with real, already-battle-tested guards: a minimum-distinct-values check
(`insufficient_variance`), a tie-contamination check at the cut boundary
(`contaminated`), and a materiality floor. A sibling function,
`_bucket_delta(rows, feature_name)`, would do the identical split but compute
`mean(y - q_pre)` per bucket instead of `mean(y)` — same guard logic, new statistic.
Buckets: category (already resolved per-signal via `trade_category.py`), a price-band
dimension (already a first-class concept — `strategy.min_unit_cost`/`max_unit_cost`), and
optionally `depth_factor`/`raw_notional_usd` for size. *Mechanism:* nonparametric,
model-free, per-bucket. *Correctness:* inherits the existing guards' known limitations —
`config/settings.yaml:181-205`'s own comment on this exact module warns "do not trust this
module's `gap_pts` output on any factor until [a documented] measurement-instrument bug is
fixed" for the *win-rate-gap* statistic specifically; `Δ`'s mean-difference statistic is a
different computation over the same buckets and is not automatically covered by that
warning, but the underlying bucketing mechanics (tie handling, index-based tertiles) are
shared, so the same class of measurement bug is a live risk here too and needs its own
check, not an inherited pass. *Complexity:* low — one new function in an existing,
well-understood module, reusing its tests' shape.

**Alternative B — a fitted calibration model (e.g., isotonic regression of `y` on
`q_pre`, optionally with a couple of covariates), refit periodically.** *Mechanism:*
standard model-calibration technique (a reliability-diagram fit), continuous rather than
tertile-discretized, in principle captures curvature bucketing can't. *Correctness:* this
codebase has already independently reached the "not enough resolved signal history to fit
a trained model reliably" conclusion **twice** for structurally similar problems —
`docs/prediction-market-strategy-alignment-plan.md` Part 3.2 explicitly rejected a
classical trained-ML model for the market-analyst agent on exactly this basis ("both
`advisory_engine.py` and `confidence_calibration.py` independently concluded there's
nowhere near enough resolved real-signal data to fit anything trustworthy... this app has
had stretches with single-digit resolved real signals"). Estimating `Δ_calibrated` is a
narrower, better-conditioned problem than that agent's job (one residual, not a full
probability estimate from rich context), so the same objection doesn't transfer at full
force — but the same *class* of risk (overfitting a thin, non-stationary sample) is real
and needs the same kind of gate `stats_power.py` already provides elsewhere (§2.4).
*Complexity:* materially higher — a refit job, a held-out validation split to catch
overfitting, and a decision about refit cadence, none of which exist today.

**Recommendation: Alternative A first, ship it as the whole mechanism.** Not "A now, B
later as an upgrade" — B is not part of this design's scope at all, on the same evidence
this codebase already used twice to make the identical call for market_analyst_agent. Revisit
only if a future measurement shows Alternative A's discretization is the binding
constraint on gate quality (a question §6's validation plan can actually answer, since it
tracks markouts against the gate's decisions), not on a schedule.

### 2.4 Sample-size gating, using `services/stats_power.py` (not reinvented)

Per the audit's §3.4 finding, this significance-testing framework is "already shipped and
reasonably rigorous" and used project-wide for gate/override decisions — reused here, not
duplicated. Before a bucket's `Δ_calibrated` value is trusted: `min_n_for_margin(margin_pts,
observed_pct=50.0)` (already-existing function) with a config-set acceptable margin
(§5) determines the minimum resolved-signal count a bucket needs; a bucket below that count
falls back to `Δ_calibrated = 0` (i.e., `p_est_side = q_pre_now`, the neutral "no measurable
edge yet" case, never a fabricated one) rather than a noisy small-sample estimate. This is
the same fail-safe direction §1.3's fee buffer uses: an under-populated bucket makes the
gate *more* conservative (closer to admitting nothing), never less.

---

## 3. The entry gate itself, and where it plugs into `strategy_engine.evaluate()`

### 3.1 `P_pre`: reusing an existing primitive, not inventing a new price lookup

`services/market_history.py:306-337`'s `recent_price(ticker, max_age_sec, as_of=None)` —
already live, already used by `check_exits`' stop-loss corroboration — is "most recent
snapshot's `yes_price` at or before `as_of`, or `None` if none within `max_age_sec`." This
is exactly `P_pre`'s definition: `market_history.recent_price(signal.ticker, max_age_sec,
as_of=signal.timestamp)`. No new subscription scope, no new table — `market_history.snapshots`
is already populated from two paths that already run: the once-per-tick REST snapshot
(`record_snapshots`, zero extra API cost — data already fetched for the whale-follow
strategy) and `record_snapshot_from_ticker`'s WS-pushed updates
(`services/whale_stream/whale_stream_handlers.py:251,334`, throttled to
`_TICKER_SNAPSHOT_MIN_INTERVAL_SEC = 5.0` per ticker). This satisfies the data-plane HARD
RULE's "never change subscription scope... because it should help" — this design adds a
*read* against an existing, already-flowing store, not a new subscription.

**One real caveat, worth stating precisely rather than assuming the lookup is clean:**
because `record_snapshot_from_ticker` fires on ticker-channel pushes throttled to a 5s
minimum interval per ticker, a snapshot taken in the same narrow window as the whale print
itself could already reflect the print's own price impact if the ticker message that
carries it is processed before (or concurrently with) the trade message — the two arrive
on different WS channels with no ordering guarantee documented between them. `max_age_sec`
alone doesn't rule this out (a contaminated snapshot can still be recent). Mitigation:
`P_pre` is looked up with a small, explicit backward offset —
`as_of=signal.timestamp - strategy.edge_gate_pre_print_offset_sec` (§5) — so the query
deliberately excludes the last few seconds immediately preceding the print, not just "the
latest thing on file." This is a stated, checkable assumption (falsifiable by comparing
`P_pre` values with and without the offset against known-large prints once markout data
exists, §4), not a proof of cleanliness.

### 3.2 The gate check

```
q_pre_now   = kalshi_fees.unit_cost(signal.side, P_pre)
p_est_side  = q_pre_now + Δ_calibrated(features_now)          # clamped to (0, 1)
ask_now     = kalshi_fees.unit_cost(signal.side, signal.price) # == today's existing `unit_cost`
fee         = kalshi_fees.taker_fee_per_contract(signal.price, signal.ticker) + edge_gate_fee_buffer_usd
edge        = p_est_side − ask_now − fee
```

Admit only if `edge >= strategy.edge_gate_min_edge`. **Why a threshold, not `edge > 0`:**
`predictionmarketspicks.com`'s own published methodology (audit §10.2, its own
`/tools/guide`) uses a "dead zone" of roughly 3–5 percentage points around fair value
below which a signal isn't actionable at all — not because a smaller positive edge is
mathematically wrong, but because `P_pre` and `Δ_calibrated` are both estimates with real
sampling and staleness error, and a razor-thin nominal edge is indistinguishable from noise
given those error sources. `strategy.edge_gate_min_edge`'s default (§5) is set in that same
ballpark, explicitly labeled a starting point pending this app's own real calibration data
(§6), not a value derived from this app's own history yet — there isn't any to derive it
from before the gate has run.

### 3.3 Placement relative to the existing gates, read from `evaluate()`'s actual current order

Read in full (`services/strategy_engine.py:285-684`); the existing order is: (1) daily-loss
kill switch, (2) already-resolved-market skip, (3) `live_markets_only`, (4)
`close_window_sec` upper bound, (5) `min_seconds_to_close` lower bound, (6) the special-
market (early-close/mutually-exclusive) conservative gate, (7) `excluded_series`, (8)
**`_validate_entry_price`** — confidence-vs-threshold, the hard 0/1 tradeable-price floor,
then the configurable `min_unit_cost`/`max_unit_cost` band — (9) `max_open_positions_per_series`
concentration cap, (10) cooldown, (11) sizing (`max_trade_size`, `kelly_scaled_max_size`),
(12) `contracts <= 0` check, (13) limit-order-or-market-order execution.

**The edge gate belongs inside `_validate_entry_price`, as a new check appended after the
existing `min_unit_cost`/`max_unit_cost` band, not as a separate call in `evaluate()`.**
Reasoning, from `_validate_entry_price`'s own docstring: that function is shared
*specifically* so a resting limit order's fill-time re-check
(`validate_pending_fill` → `check_pending_fills`) is held to the same bar a fresh signal at
the fill price would be — the exact mechanism that closed the "four-entry gate bypass" bug
(real entries at unit costs 0.97, 1.00, 0.20, 0.97). An edge gate checked only at signal
time and never at fill time would reopen that identical bypass shape for edge specifically:
a limit order placed when `edge` was healthy could fill later, after the market moved,
with no re-check that the edge still exists at the fill price. Placing it inside the shared
function closes both paths for free, the same way the price-band check already does.
Ordered *after* the existing price-band checks (not before) because the hard tradeable-
price floor and the confidence threshold are cheaper, more fundamental invariants — no
reason to compute `Δ_calibrated`/fetch `P_pre` for a signal that was going to be rejected
by a cheaper check anyway. `EntryValidation`'s existing `gate_name`/`observed`/`threshold`
shape already fits an edge gate rejection without modification —
`candidate_log.record_rejection(..., "edge_gate", observed=edge, threshold=min_edge, ...)`
follows the exact pattern every other gate in this function already uses, so the
counterfactual-tracking machinery (§2.1, `candidate_log`) picks up edge-gate rejections
automatically, with no separate wiring.

**One consequence worth stating explicitly:** `_validate_entry_price` currently has no
network or DB access — it's pure arithmetic over its arguments. Adding a `market_history.recent_price`
read (a SQLite query) and a bucket lookup (in-memory, if `Δ_calibrated` is precomputed
periodically rather than recomputed per signal — see §5's `edge_gate_recompute_interval_sec`)
changes that. `evaluate()` already does other SQLite reads on this same path
(`signal_log.series_stats`, `candidate_log.record_rejection`), so this isn't a new category
of cost on this function, but it is worth measuring once implemented, per the data-plane
HARD RULE's "any diagnostic or abstraction on the exchange-wide hot path is measured for
runtime cost before it ships" — not asserted safe by analogy alone. **The same measurement
requirement applies to §4's markout-capture sweep** (see §5's note on why that sweep is the
one piece of this design's new surface that is *not* gated behind `edge_gate_enabled` and
therefore runs, and costs something, unconditionally from ship day).

---

## 4. Markout measurement

### 4.1 What already streams this data, and why lazy (query-time) computation is the wrong shape

`market_history.snapshots` already carries exactly what a markout needs — `(ticker,
yes_price, timestamp)` — from the same two zero-marginal-cost paths §3.1 describes. A
markout is structurally identical to what `compute_hypothetical_trades`
(`services/market_history.py:403-451`) already does — "snapshot closest to (target_time),
preferring one at or before it" — just evaluated *forward* from a real entry instead of
*backward* from settlement.

**Design alternative 1 — lazy/pull: a new function, no new table, computed on demand from
existing `snapshots`.** *Mechanism:* `market_history.markout(ticker, entry_ts, entry_price,
entry_side, offset_sec)` reuses `compute_hypothetical_trades`'s "closest snapshot at-or-
before target time" pattern, called by a reporting endpoint at read time. *Correctness
risk, decisive against this alternative:* `market_history.snapshots` is pruned on a
7-day rolling window (`config/settings.yaml:324`, `retention_hours: 168`, confirmed live) —
while `strategy.close_window_sec`'s own default is `2764800` seconds (32 days,
`config/settings.yaml:76`). A `t+close` markout (and, for a market that closes weeks out,
even a distant `t+1h` reporting run) computed lazily against `snapshots` risks the
underlying raw rows having already been pruned by the time anyone asks — a silent
completeness failure indistinguishable from "no markout exists yet," exactly the failure
shape the data-plane HARD RULE calls out. *Complexity:* low, but the correctness gap is not
a tuning parameter, it's structural.

**Design alternative 2 — eager/push: a lightweight new table, populated by a low-frequency
scheduled sweep.** A new table (extending `market_history.db`, not a new file — same
concern, same retention/backup posture) — `markouts(signal_id, ticker, entry_ts,
entry_side, entry_price, offset_sec, markout_price, captured_at)` — populated by a sweep
matching the exact idiom `main.py`'s `_maybe_prune_capture_stores`/`_maybe_check_signal_resolutions`
already establish: a module-level "last run" guard, called from the tick loop or as an
independent background task (`_maybe_capture_markouts(cfg, now)`, gated on its own
interval, e.g. every 300s), non-raising, faults logged not thrown. On each run: for every
signal whose `entry_ts + offset_sec` has just elapsed (across the configured offsets, §5)
and has no `markouts` row yet for that `(signal_id, offset_sec)`, look up the closest
`snapshots` row at-or-before that target time (identical query shape to
`compute_hypothetical_trades`) and write one row — capturing the price *while it's still in
the 7-day window*, permanently, independent of `snapshots`' own later pruning.
*Correctness:* closes alternative 1's gap directly — a markout, once captured, survives
`market_history.prune()`. *Complexity:* one new table, one new scheduled sweep reusing an
established pattern — not a new category of machinery for this codebase.

**Recommendation: alternative 2.** The retention-window mismatch is a real, already-
measured (not hypothetical) number (168h vs. a 32-day-default execution window), not a
close call.

### 4.2 What gets recorded, and for what population

Every **real entry** (a `PaperBroker.open_position`, from `paper_broker.trade_log`, not
every whale print — markouts measure realized trading outcomes, `signal_log`'s full
print population is §2's job), at offsets `t+5m`, `t+1h`, `t+close` (close time from the
same `market_lookup.effective_close_time` `evaluate()` already resolves — reused, not
re-derived). `markout_price` is always yes-price from `snapshots`; the report layer
side-adjusts via `kalshi_fees.unit_cost(entry_side, markout_price)` at read time — the
table itself stays in the neutral yes-price convention every other table in this app uses,
consistent with `market_history.snapshots`' own `yes_price` column.

### 4.3 Feedback into evaluating the gate

`markout_pts(offset) = kalshi_fees.unit_cost(entry_side, markout_price) −
kalshi_fees.unit_cost(entry_side, entry_price)` — positive means the market kept moving in
the held direction after entry (the edge, if real, showing up), negative means it reverted
(the print's impact fading, or the entry itself adverse-selected). Aggregated by whether the
entry passed the *new* edge gate vs. would have passed only the old confidence-only gate
(recoverable from `candidate_log`'s rejection population plus `signal_log`, §2.1) — this is
what tells apart "the gate is working" (persistently better markouts among edge-gate-passed
entries) from "bad signal, not a pricing problem" (poor markouts regardless of gate), the
exact distinction the audit's §3.3 step 3 names as the reason to measure this first. No
model is required for this step — it's arithmetic over data already collected once §4.1's
sweep exists.

---

## 5. Config surface

New fields under the existing `strategy:` block, following the flat, prefixed-key
convention already established there (`auto_exit_*`, `longshot_*`) rather than a nested
sub-object — matching `config/settings.yaml:71-120`'s existing shape, not inventing a new
one. Every one of the 8 fields in the table below defaults to a value that changes nothing
about `_validate_entry_price`'s current gating/sizing behavior until deliberately turned
on, the same "ships fully built, opt-in" precedent `kelly_fraction_of_cap: 0.0` (off)
already sets and `strategy_engine.py:55-68`'s own docstring states explicitly ("At 0.0 (the
default) this returns max_size unchanged - nothing about existing behavior changes unless
deliberately turned on"). **This opt-in framing does not cover §4's markout-capture sweep**
— per §8's own Risks disclosure, that sweep (the new `markouts` table plus `signal_log`/
`candidate_log` extensions) runs unconditionally on its own interval from the moment this
ships, regardless of `edge_gate_enabled`, costing one scheduled sweep's worth of SQLite
writes. That is a deliberate exception to the opt-in pattern, not an oversight: markout
data has to exist before there's anything to decide whether to turn the gate on with. It is
still new, always-on runtime cost and needs its own `dimensional-analysis`-adjacent
runtime-cost measurement before it ships, exactly as §3.3 already requires for
`_validate_entry_price`'s new DB reads — both are new per-tick-or-per-print costs on the
data-plane hot path, not only the gated ones.

| field | default | why this default |
|---|---|---|
| `strategy.edge_gate_enabled` | `false` | Master switch. Off means `_validate_entry_price` runs exactly as it does today — zero behavior change on ship. |
| `strategy.edge_gate_min_edge` | `0.04` (4 percentage points, side-relative probability) | Mid-point of predictionmarketspicks' documented 3–5pp dead zone (audit §10.2) — a stated starting point from external methodology, not yet from this app's own calibration data (none exists before the gate runs; §6 revisits this once markouts accumulate). |
| `strategy.edge_gate_fee_buffer_usd` | `0.005` ($0.005/contract) | Non-zero specifically so the lower-bound-only fee formula (§1.2) cannot silently pass as the true net fee; on the same order as `fee_rounding.md`'s own worked example's rounding-fee component. Revisit once real net-fee data exists (paper mode has none — fees are simulated, `services/kalshi_fees.py` — so this stays a documented estimate, not a measured one, until real trading; flagged, not fixed, by this design). |
| `strategy.edge_gate_pre_print_offset_sec` | `10.0` | Backward offset for the `P_pre` lookup (§3.1), clear of `record_snapshot_from_ticker`'s 5s throttle window with margin. |
| `strategy.edge_gate_p_pre_max_age_sec` | `600.0` (10 min) | `recent_price`'s own `max_age_sec` — how stale a "before" snapshot can be and still count as `P_pre`; beyond this, `P_pre` is unavailable and the gate fails open (§below) rather than trusting a half-hour-old anchor. |
| `strategy.edge_gate_min_bucket_n` | `50` | Fed to `stats_power.min_n_for_margin` (§2.4); a bucket below this uses `Δ_calibrated = 0`. |
| `strategy.edge_gate_recompute_interval_sec` | `3600` (hourly) | How often bucketed `Δ_calibrated` values are recomputed from `signal_log`, not per-signal — matches the existing hourly-sweep idiom (`_maybe_prune_capture_stores`) rather than inventing a new cadence class. |
| `strategy.edge_gate_markout_offsets_sec` | `[300, 3600, null]` | `t+5m`, `t+1h`, `t+close` (`null` meaning "use the market's own resolved close time," not a fixed offset). |

**Fail-open semantics, stated explicitly (matching every other gate in this file's
documented convention, e.g. the mutually-exclusive gate's "fails open" language):** if
`P_pre` is unavailable (`recent_price` returns `None` — no snapshot within
`edge_gate_p_pre_max_age_sec`) or the ticker's series `fee_type` is `flat` (§1.3), the edge
gate returns "cannot compute — not evaluated," **not** "computed, edge is negative." When
`edge_gate_enabled` is `false`, this is moot; when `true`, an unresolvable gate currently
falls open (admits, subject to every other existing gate) — the same fail-open convention
the rest of `evaluate()` already uses for missing data, stated here rather than silently
inherited, since a HARD RULE gate failing open on missing data has different stakes than a
descriptive one. This choice — fail-open vs. fail-closed on missing `P_pre` specifically —
is exactly the kind of judgment call `docs/open-decisions.md` should carry forward
explicitly rather than this document deciding unilaterally; noted, not resolved, here.

---

## 6. Validation plan — paper-mode only

No part of this validates against real money; `kalshi_account.trading_enabled` and the
kill switch are untouched (Non-goals). "Working" is established from data the app already
collects once §4's markout sweep and §2's calibration query exist — no new instrumentation
beyond what this design already specifies:

1. **Pre-registration, not post-hoc.** Once `edge_gate_enabled: true` ships (still off by
   default — a deliberate, logged config change, not a silent flip), every entry already
   carries `config_fingerprint` (`PaperBroker.open_position`'s existing field) — the
   population of entries taken *while the gate was on* is therefore already distinguishable
   from entries taken before, with no new column.
2. **Flat per-contract P/L and Brier score, reported separately from sized P/L** —
   predictionmarketspicks' own methodology (audit §10.2), directly actionable here since
   `signal_log.correct` already is the Brier-score input (a 0/1 outcome against a stated
   probability, `signal.confidence` or, once it exists, `p_est_side`) and
   `trade_analytics.py` already computes flat-P/L-shaped reports elsewhere in this app —
   reused, not invented.
3. **The decisive comparison: markouts (§4.3) for edge-gate-admitted entries vs. the
   candidate population the gate would have rejected** (`candidate_log`'s
   `population_gate_summary()`, extended to include an `edge_gate` gate name once §3.3
   ships — the same mechanism every other gate's counterfactual tracking already uses,
   zero new plumbing). A working gate shows persistently less-negative or positive
   markouts on the admitted population relative to the rejected one, specifically in the
   0.60–0.95 unit-cost band the audit's own already-tracked diagnostic flags as negative-EV
   today — that flip, if it happens, is the single most legible "this worked" signal this
   design can produce, because it's the exact number this design exists to move.
4. **Sample-size discipline on the validation itself**, not just on `Δ_calibrated`:
   `stats_power.min_n_for_margin`/`two_proportion_z_score` (already-existing functions,
   §2.4) gate whether "the gate looks better" is a real finding or noise, before it's acted
   on — the same standard this document's own self-review below holds itself to.
5. **A stop condition, stated up front:** if, after `edge_gate_min_bucket_n`-scale data
   accumulates, admitted-entry markouts are *not* better than the pre-gate baseline, that's
   a real finding about this design (Δ_calibrated's bucketing is too coarse, `P_pre`'s
   staleness/contamination concern in §3.1 is real, or the underlying "residual mispricing
   after whale impact" thesis is weaker than the audit's external validation suggested) —
   to be recorded and acted on, not quietly re-run with a lower threshold until it looks
   better.

---

## 7. Phase 2 (deferred, not designed here): `market_analyst_agent` cleanup + category fair-value anchors

Second-pass item 27, first-audit §13 item 19/§3.3 item 4 — explicitly "later, optional" in
the audit and explicitly out of scope for this document beyond naming the sequencing
already established: **after** §3's core mechanism is live and §6 has produced real
markout/Brier data, not before. Two candidate directions, named but not designed:

- Whether `market_analyst_agent.analyst_lean()` becomes one more term feeding
  `Δ_calibrated`'s features (not a replacement for it — it's populated for far fewer
  tickers than every whale print needs, per §0's finding) once its own value against real
  outcomes can be measured the same way §6 measures the edge gate itself.
- Category-specific fair-value anchors (base-rate tables, a realized-vol model for
  15-minute crypto/index series, mirroring predictionmarketspicks' own 15-min commodity
  tool per audit §10.2) as a second, richer source for `p_est` alongside the bucketed
  `Δ_calibrated` from §2 — for market families where this app's own settled history is
  thin but a domain-appropriate base rate exists independent of it.

Any real design work here should re-verify §0's findings first — `whale_confidence_weights.analyst_factor`
and `auto_exit_analyst_weight` are live config values a human could change independently of
this design between now and then.

---

## 8. Risks and rollback

- **`edge_gate_enabled: false` is the rollback** — no migration, no data loss, no code path
  removed; the gate simply stops being consulted, `_validate_entry_price` reverts to
  exactly its current behavior. This is true at every layer: the new `markouts` table and
  `signal_log`/`candidate_log` extensions are additive and harmless to leave in place even
  with the gate off (they cost one scheduled sweep's worth of SQLite writes, §4.1's own
  interval).
- **Risk: the gate admits fewer trades than expected, starving downstream calibration of
  data.** `edge_gate_min_edge` (§5) and the fail-open defaults are deliberately permissive
  starting points precisely to avoid this — but §6's own validation plan needs enough
  admitted volume to say anything with `stats_power`-backed confidence, and if
  `edge_gate_enabled: true` turns out to cut volume sharply, that's a finding to surface
  (via the existing `/api/health/pipeline`/`candidate_log` surfaces), not a silent
  self-defeating outcome.
- **Risk: `P_pre` contamination (§3.1) is worse than assumed.** The stated offset
  (`edge_gate_pre_print_offset_sec`) is a design-time guess, not a measured value — §6's
  markout data is also the mechanism to check this specific assumption once it exists
  (comparing gate performance with the offset on vs. off is a cheap, later experiment this
  design enables but does not run).
- **Risk: this design's own arithmetic has a dimensional bug.** Per the HARD RULE, this
  needs its own `dimensional-analysis` pass at implementation time, not only the unit
  labels stated in §1.1/§3.2 here — this document is the design, not the verified
  implementation.

## 9. Success criteria (per CLAUDE.md's per-module axes, not a 70%/70% target)

- **Effectiveness:** admitted-entry markouts (§4.3) beat the pre-gate baseline in the
  0.60–0.95 band, at `stats_power`-adequate sample size (§6).
- **Efficiency:** no measured regression in `evaluate()`'s per-call cost or the tick loop's
  own latency budget (data-plane HARD RULE — measured, not assumed, per §3.3's own note).
- **Informativeness:** every gate rejection is visible in `candidate_log` with the same
  `gate_name`/`observed`/`threshold` shape every other gate already provides, and `p_est`/
  `Δ_calibrated`/`edge` are inspectable per-signal (via `factors_json`-shaped storage,
  matching the existing convention), not only a boolean pass/fail.

---

## Design self-review

Scope of this review: internal consistency and unaddressed scope in the document above —
not a re-derivation from primary sources (that's the separate adversarial review this
document still needs, per CLAUDE.md's "nothing advances on one pass" HARD RULE, run as a
fresh Agent call with no memory of this session, after this document is returned).

**What holds up:**
- The §0 finding is the one piece of this document most exposed to being wrong if
  mis-read, and it's the one backed by the most direct file:line evidence (both weight
  values, both gating flags, both call sites) rather than inference — appropriate given the
  task's explicit warning that a prior grep-only check on this exact question was called
  insufficient.
- Every fee/pricing claim in §1.2 traces to a primary source read during this session
  (`fee_rounding.md`, `get-series-list.md`, the PDF itself page-by-page, `kalshi_fees.py`
  read in full), not the audit's summary of them — satisfying the Kalshi-integration-
  authority HARD RULE's "grep it yourself, don't trust the audit's citations without
  re-checking."
- The two required alternative comparisons (§2.3 for `Δ_calibrated`, §4.1 for markout
  storage) are both decided on a concrete, checkable piece of evidence specific to this
  codebase (the twice-repeated "not enough resolved data to fit a model" precedent; the
  measured 168h-vs-32-day retention/execution-window mismatch) rather than generic
  engineering taste — satisfying "compare... on mechanism, correctness, and complexity."
- The gate's placement (§3.3) is derived from actually reading `evaluate()`'s current 400-line
  body end to end, including a mechanism (`validate_pending_fill`'s shared-function bypass
  fix) this design explicitly extends rather than overlooks — the task's own warning that "a
  design that bolts this on without understanding the existing gate order is likely wrong"
  was taken literally.

**Gaps and honest limitations, not fixed here because fixing them is plan-stage or
implementation-stage work, not design-stage:**
- `edge_gate_min_edge`'s 4pp default and `edge_gate_fee_buffer_usd`'s $0.005 default are
  both stated as external-methodology-derived starting points, explicitly not derived from
  this app's own data — because none exists yet. This is honest, not a defect, but it means
  §6's validation plan is doing real work these defaults can't: a plan/implementation stage
  should treat both as loudly-labeled placeholders, not settled numbers.
- §3.1's pre-print-contamination concern is flagged with a proposed mitigation (a fixed
  10s offset) but not verified — I do not have live evidence of ticker-vs-trade message
  ordering on the same WS connection; this is stated as an assumption, per the never-guess
  HARD RULE, not asserted as checked.
- I did not verify a live count of how many currently-tradeable series have `fee_type ==
  "flat"` — `kalshi_fees.py`'s own docstring says, undated, "no market/event in this app's
  own data has ever resolved to it," and I did not re-run that check against
  current `series_cache.db` data for this document (it would have required either a live
  DB query against the running app or a scan I judged out of scope for a design document
  that doesn't touch data) — flagged explicitly in §1.3 rather than left implicit.
- This document does not fully specify `Δ_calibrated`'s bucket boundaries (how many
  categories, what price-band width, whether size is bucketed by `depth_factor` or
  `raw_notional_usd`) — that level of detail is implementation-plan-appropriate, not
  design-appropriate, but a reader could reasonably want more concreteness here than "reuse
  `_bucket_win_rates`'s shape." Left as a plan-stage decision deliberately, not an
  oversight.
- The interaction between this design's new `markouts` table and `services/backup/backup.py`
  (backup size/restore cost for yet another growing table) is not addressed — the second-
  pass audit's own open-questions list (§9 of that document) already flags this exact class
  of gap for its own DuckDB/retention items and this design inherits the same unaddressed
  question rather than resolving it.
- I relied on one live grep of `config/settings.yaml` for the §0 weight values; per the
  P2 process finding in the second-pass audit ("a claim about `main` starts with a fetch"),
  the equivalent discipline here is that these are this worktree's checkout at the time of
  writing, not a fetched-fresh confirmation against a shared branch — worth re-checking at
  adversarial-review time if any time has passed.

No claim in this document rests on the audit's own tables or summaries where a primary
source was available and cheaper to check directly — the one exception is §2.1's citation
of `resolved_signals_with_factors()`'s own docstring-stated ~1s/103k-row cost, which I read
from the function's own docstring (a primary source) rather than re-measuring live, since
re-measuring against a running instance was judged unnecessary for a design decision that
only needs "this needs bounding," not an exact number.
