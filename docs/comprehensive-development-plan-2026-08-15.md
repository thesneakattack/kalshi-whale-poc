# Comprehensive development plan (2026-08-15)

Direct request: consume every research/planning doc in this repo and produce
a comprehensive forward-looking development plan. Methodology: three parallel
research agents each read a subset of the 10 docs below **in full**, then
cross-checked every recommendation against the *real* code (`grep` for the
actual function/field names, not trusting a doc's own internal completion
markers) and against `ROADMAP.md`'s live "Shipped" narrative, since several
of these docs are from 2026-08-08 through 2026-08-11 and a large amount of
work has landed since. Findings below are grounded in that verification, not
just the docs' own text.

Docs consumed: `prediction-markets-research-reference.md`,
`prediction-market-strategy-alignment-plan.md`,
`kalshi-whale-provider-and-strategy-porting-plan.md`,
`platform-deep-scan-findings-2026-08-10.md`,
`hardening-and-accuracy-roadmap-2026-08-11.md`,
`config-tuning-data-gaps-2026-08-10.md`,
`session-2026-08-11-whale-exit-stale-price-bug.md`, `advisory-engine-plan.md`,
`simmer-integration-research.md`, `roadmap-archive-2026-08-09.md`.

This doc is organized by **what to actually do next**, not by source
document — items are grouped by size/risk, since that's the axis that
matters for sequencing. A data-points appendix at the end preserves the
specific numbers these docs contain, since those are easy to lose in
summarization and several are load-bearing (fee formula, confidence weights,
sample-size gates).

## Headline finding: why a ~69% win rate only produced pennies

Direct follow-up mid-session, escalated to top priority: "figure out why
even with a near 70% winrate only pennies are earned... the portfolio is
looking more like a straight line." This is not one of the 10 research docs
above — it's a fresh, real-data investigation this session ran in response,
and it's the single most consequential finding in this document.

Pulled all 606 real settled whale-follow trades and bucketed every one by
**unit_cost** — the actual side-aware price paid per contract
(`signal.price` if yes, `1 - signal.price` if no), not the raw yes-price a
quick glance would use. First confirmed the settlement payout math itself
has zero bug (theoretical vs. actual realized P&L matched to the cent on
every sampled trade - the accounting is correct). The real story is in
*which prices get bought*:

| unit_cost band | n | win rate | net P&L |
|---|---|---|---|
| 0.1 – 0.5 (cheap/underdog) | 155 | 20-43% | **-$1,588** |
| 0.5 – 0.8 (the only clean band) | 230 | 54-76% | **+$1,152** |
| 0.8 – 1.0 (expensive/heavy favorite) | 208 | 78-94% | **-$314** |

The 0.8-1.0 band is the counterintuitive half: 78-94% win rates, still net
*negative* — a win only pays a few cents when you've already paid most of
the dollar for it, so the rare loss (paying nearly $1, getting $0 back)
erases many wins' worth of gains. That's the literal mechanism behind "high
win rate, pennies of profit, flat-looking equity curve": the strategy's
real edge lives in one band (0.5-0.8) and is being diluted by a large
volume of trades on both sides of it that net negative despite looking
individually safe (high win rate) or individually smart (cheap longshot).

**Fixed directly, not just documented**: `services/strategy_engine.py` gained
a real price-band entry gate (`strategy.min_unit_cost: 0.5`,
`strategy.max_unit_cost: 0.8`), side-aware, mirroring the hard min/max-price
precedent `market_strategy.py` already used successfully for the other
strategy (whale-follow had deliberately chosen a softer graduated-bonus
approach for extreme prices only - `longshot_price_threshold`/
`longshot_entry_threshold_bonus` - but this data shows that doesn't reach
nearly far enough into the real losing zone). 774+ tests passing, verified
live. This is a real, immediate behavior change to what future whale-follow
trades get accepted - watch the real trade history over the next stretch to
confirm the effect holds up outside this backward-looking sample, and
revisit the exact band edges once enough post-change trades accumulate to
measure it directly (a natural fit for `change_effect_windowed()`, see
"In-flight work" below).

## Housekeeping — do first, trivial

- [x] **ROADMAP.md's checkbox for the whale-exit stale-price bug fix was
      stale** — it said "not yet committed"; the fix (`opened_since` param on
      `check_exits()`) has been committed for days. Fixed as part of this
      pass — see below.
- [ ] **`docs/config-tuning-data-gaps-2026-08-10.md` Gap 5** —
      `market_strategy.stop_loss_pct` had zero real data to calibrate against
      when that doc was written (field had just gone live). It's had 5+ days
      of runtime since; worth a fresh check of whether `market_broker.db` now
      has real `stop_loss` close-type rows to look at. No code to build here,
      just a data check next time someone's in that area.
- [x] **`me_pairs` (this session's own mutually-exclusive pair detection)
      was silently missing from `GET /api/state` since it shipped** — found
      while wiring up the tick-duration/rate-limit fields below.
      `_build_state_body()` is a curated whitelist, not a passthrough of the
      whole `state` dict, and `me_pairs` was never added to it - the entry
      gate itself worked correctly (`main.py`'s own internal use never went
      through this function), but nothing outside the process could ever
      see which pairs were currently detected. The earlier "curl and check"
      verification that shipped it missed this because it read
      `(resp.get("me_pairs") or {})` - indistinguishable between "present
      but empty" and "key missing entirely." Fixed; confirmed live
      (`KXUFCFIGHT-26AUG15MAKMGI-MAK`/`-MGI` showing as a real detected pair).

## Small, well-scoped gaps (closed directly in this pass)

Each of these is grounded in a specific, verified-still-open finding from the
research, small enough to implement correctly in one sitting without a
design detour, and safety/correctness-relevant rather than speculative:

- [x] **Kalshi's own `is_block_trade` flag was parsed and never consumed**
      (found by reading `docs/kalshi/public-trades.md` in full - a real
      first-party signal, already captured by `services/kalshi_trade_ws.py`,
      never read by `composite_confidence_breakdown`/`kalshi_trade_tape.py`).
      Added as a real 9th confidence factor (`block_trade_factor`), given a
      reasoned starting weight (0.15) rather than 0, same "ships with a real
      value, gets recalibrated once data exists" precedent as
      cluster_factor/trend_factor/analyst_factor - the other 8 weights
      proportionally rescaled to make room, preserving their existing
      relative proportions rather than resetting the user's own live-tuned
      values. Also fixed a related reuse gap while here:
      `confidence_calibration.py`'s `_FACTOR_NAMES` was a hand-duplicated
      tuple that would have silently never bucket-analyzed this new factor -
      now derived directly from `DEFAULT_WEIGHTS`.
- [x] **`ShadowTrader` never got `config_fingerprint`** (`advisory-engine-
      plan.md` §1's disclosed v1-scope gap) — shadow-mode trades can't be
      attributed to a config variant the way paper trades can via
      `config_performance`.
- [x] **No margin-of-error framing on calibration auto-apply**
      (`hardening-and-accuracy-roadmap-2026-08-11.md` Part 4 Item 2) —
      `services/stats_power.py`'s real margin-of-error math exists and is
      used by the series-evaluator win-rate cross-check, but
      `confidence_calibration.py`'s auto-apply gate remains a bare
      sample-size floor with no confidence-interval framing.
- [x] **No tick-duration/rate-limit visibility** (`hardening-and-accuracy-
      roadmap-2026-08-11.md` Part 3 Item 2) — real incident evidence exists
      (a genuine 502 during the phase-97 incident) but `state` still has no
      `last_tick_duration_sec` or rolling rate-limit-hit counter to see it
      coming next time.

Deliberately **not** closed in this pass (small individually, but each needs
a real design decision about *which* consumer/UI surface to wire into, and
CLAUDE.md's own standing guidance is against half-finished implementations):

- [ ] **`market_history.snapshots`' write-only columns** (`hardening-and-
      accuracy-roadmap-2026-08-11.md` Part 4 Item 1) — `spread`/
      `volume_24h`/`time_to_close_sec` are written every tick, read by
      nothing. Real candidate consumer once picked: a liquidity-quality
      factor alongside `market_history.volatility()` (this session's own new
      function) in `_exit_confidence()`, or a `market_strategy.py` entry
      factor. Needs a decision on which, not just plumbing.
- [ ] **No engine suggests per-series notional overrides** (`hardening-and-
      accuracy-roadmap-2026-08-11.md` Part 4 Item 3) — natural extension of
      the already-shipped series_evaluator↔advisory_engine wiring
      (`_series_evaluator_recommendations()`), but needs the same
      `_by_series` merge-safety handling this session's `config_overrides.py`
      work (still in progress, see "In-flight work" below) is already
      building — sequencing this after that lands avoids building the
      pattern twice.
- [ ] **Ambient auto-apply-enabled badge** (`advisory-engine-plan.md` §6) —
      the plan's own text calls this "an implementation detail, not a
      planning decision." Small UI addition, low urgency.
- [ ] **Long-horizon/time-value-of-money discount** (`prediction-market-
      strategy-alignment-plan.md` §2.6) — the doc's own lowest-priority item,
      and less urgent since the real watchlist already skews near-term.
- [ ] **Minimum-edge-after-fees entry check** (`prediction-market-strategy-
      alignment-plan.md` §2.2c) — attempted in this pass and deliberately
      NOT shipped once the math was checked: `fee / cost_basis = 0.07 ×
      (1 - unit_cost)`, which is mathematically bounded to [0, 0.07] no
      matter the price - a naive "skip if fee eats more than X% of cost
      basis" gate would need X below 7% to ever fire at all, and even then
      would only fire in the same extreme-price zone the existing longshot
      bonus already scrutinizes. A version that's actually meaningful needs
      a real edge/probability estimate this app doesn't cleanly have outside
      the composite confidence score itself (which isn't a calibrated
      probability) - a real design question, not a quick patch. Superseded
      in practical value by the price-band gate above, which was directly
      informed by real P&L data rather than a theoretical fee-ratio bound.

## Medium items — real findings, need more than a quick patch

- [ ] **Wash-trading / alternating-side detector** (`platform-deep-scan-
      findings-2026-08-10.md` Finding 5) — confirmed fully unbuilt (no
      `opposite_side`/`wash_trad`/`alternat` logic anywhere). Deliberately
      last in that doc's own recommended sequencing, pending more data.
- [ ] **Regime-aware live entry gating** (`hardening-and-accuracy-roadmap-
      2026-08-11.md` Part 2 Item 3 / deep-scan Finding 7) — the *analytics*
      half shipped (`regime_analytics.by_hour_of_day`/`by_day_of_week`/
      `by_category`), but nothing feeds it into live entry decisions.
      Explicitly scoped out of the 2026-08-11 pass by direct instruction
      ("not risk the live trading path twice in one pass") — same caution
      applies here.
- [ ] **Calibration-band feedback into position sizing** (`platform-deep-
      scan-findings-2026-08-10.md` Finding 4) — "closed in spirit" per
      ROADMAP (whale weights became live-tunable/auto-apply-capable), but
      the doc's literal ask — scale Kelly sizing by a signal's calibration
      band — was never built. Smaller lift now that Finding 1's Kelly hook
      (`kelly_scaled_max_size()`) exists.
- [ ] **Unthrottled per-ticker API concurrency — escalated, live evidence
      this is an active problem, not just a historical incident.**
      (`hardening-and-accuracy-roadmap-2026-08-11.md` Part 3 Item 1)
      — confirmed zero `asyncio.Semaphore` usage anywhere in
      `main.py`/`services/*.py`. Building this session's tick-duration/
      rate-limit visibility (small gaps, above) immediately surfaced a real,
      *currently ongoing* instance: the live app is hitting **45-66 429
      rate-limit responses every single tick**, consistently, not a one-off
      - confirmed by watching `last_tick_rate_limit_hits` across several
      consecutive ticks right after shipping it. Tick duration (5-6.5s) is
      already at or above the configured `poll_interval_sec` (5s). The app
      isn't currently erroring (`call_with_backoff` absorbs it), but this is
      real, sustained load on Kalshi's rate limiter with no throttling on
      this app's side - exactly the precondition the phase-97 502 incident
      had. Items 3 (backoff retune) and 4 (audit
      `trade_category.record_category` for the same blocking pattern -
      confirmed still called synchronously inline) are both blocked on or
      related to this one. Given this live evidence, this should likely move
      ahead of event-lifecycle awareness in the sequencing below.
- [ ] **Maker-style real order support** (`prediction-market-strategy-
      alignment-plan.md` §2.3) — `kalshi_account_client.create_order()`
      already accepts `post_only`/`time_in_force="good_till_canceled"`, but
      nothing selects maker orders for real trades. Doc's own framing: "P2
      follow-up once real trading is closer," which it still isn't (paper
      mode).
- [ ] **Category-level legal-risk awareness** (`prediction-markets-research-
      reference.md` Part 3, echoed in `ROADMAP.md`'s "Path to production")
      — sports-category contracts are in genuine, live multi-state legal
      dispute; `kalshi.categories` is a volume filter, not a risk one. Needs
      a real product/legal decision (and possibly a state-of-residence
      check), not just an engineering pass. Already tracked in `ROADMAP.md`.
- [ ] **Full keyboard navigation for clickable cards/rows** (`roadmap-
      archive-2026-08-09.md`, disclosed inside an otherwise-checked-off
      accessibility pass, confirmed still open via `static/status.html`) —
      broad surface (every `onclick`-driven card/row: positions, market
      cards, decision feed, trade log), no `tabindex`/keydown handling
      anywhere. Never promoted to its own `ROADMAP.md` checkbox despite
      being a real, disclosed gap. Not attempted this pass - flagged here so
      it has a real home instead of staying buried inside an already-closed
      accessibility item.

## Large items — need a dedicated design pass, not a quick win

- [ ] **Event-lifecycle awareness** (pre-tail/mid-series/post-tail activity
      phases, `hardening-and-accuracy-roadmap-2026-08-11.md` Part 1) — the
      single largest open item across all 10 docs. Confirmed fully unbuilt:
      `round_robin_select()` still ranks purely by cumulative 24h volume,
      with no phase concept at all, despite `occurrence_datetime` already
      being fetched and cached. The existing `_LIVE_STATUS_LOOKAHEAD_SEC`/
      `_LIVE_STATUS_LOOKBACK_SEC` window (1h before → 6h after scheduled
      start) is the only existing occurrence-aware boundary in the codebase
      and the natural starting point for a "mid-series" definition.
- [ ] **Config-driven multi-strategy framework** (`kalshi-whale-provider-and-
      strategy-porting-plan.md` Part 2) — confirmed fully unbuilt as
      originally scoped: no `signal_sources.py`/`signal_aggregation.py`/
      `strategy_gates.py`/`strategy_sizing.py`/`configurable_strategy.py`,
      no `custom_strategies` config section. **Important nuance**: the
      narrow idea inside it (whale-consensus) was NOT built as this general
      framework — it was folded directly into `composite_confidence_
      breakdown` as `agreement_factor` instead, a materially narrower
      resolution. If a genuinely pluggable "many strategies, config not
      code" framework is still wanted, this is fully open, not partially
      done. The doc's own `strategy_gates.passes_market_filters()`
      extraction (deduplicating the spread/price-band/volume/days-to-close
      filters currently hand-duplicated between `market_strategy.py` and
      `strategy_engine.py`) would be a reasonable first slice if this is
      ever prioritized, since it's pure refactoring with no behavior change.
- [ ] **Stateful backtest replay** (`config-tuning-data-gaps-2026-08-10.md`
      Gap 2's explicitly-deferred half) — portfolio-state-aware replay
      (cooldowns, concentration limits, bankroll/risk-halt simulated
      tick-by-tick against history), vs. today's aggregate-only replay.
      Described in the source doc itself as "probably its own design pass."
- [ ] **Simmer / Polymarket real whale-data venue** (`simmer-integration-
      research.md` Option C1) — the *only* path in this app's whole design
      space to real (non-simulated) whale data, but only for Polymarket,
      never Kalshi (Kalshi has no public wallet ledger — structurally
      impossible, not a Simmer limitation). This is a "should this app go
      multi-venue" decision, materially bigger than a whale-data
      improvement — new asset class, new UI surface, `trade_analytics.py`/
      event-grouping/fingerprinting all need Polymarket-aware variants.

## Explicitly deferred / non-goals (documented so they aren't re-litigated)

- **An actual ML model for the advisory engine** (`advisory-engine-plan.md`
  §9/§10) — intentionally deferred until real trade volume is large; the
  `ml_feed.py` scaffolding exists and is even live-consumed by the market
  analyst agent's context-building, but no model has been or should be
  built yet.
- **Simmer as an execution venue** (Option C2, `$SIM` LMSR paper market via
  `simmer-sdk` instead of this app's own broker) — the source research
  explicitly recommends against this over Option C1 (alpha-stage
  third-party dependency, `simmer-sdk`'s own repo self-describes as "Alpha
  Access-only. Not for Production Use," different fill-simulation model).
- **Simmer as an offline backtest dependency** (Option B) — reasonable, but
  only once a specific strategy *hypothesis* needs validating against
  Simmer's historical tape before being encoded as a heuristic — not a
  standing task.

## In-flight work (as of this doc, separate from the above)

Not from these research docs — the per-series/per-market config
fine-tuning pivot (generic override resolver, migrating the two legacy
`_by_series`/`_by_category` fields, `change_effect_windowed()` series
filter, series-level advisory suggestions, manual override editor UI) was
approved and partially shipped earlier this session (the ELI5 History tab
redesign half). The per-series-notional-override item above is
deliberately sequenced after that work's resolver lands, to avoid building
the same merge-safety pattern twice.

Also queued, direct request immediately following this document: a "robust
active management plan for when volatility causes large whale bets on both
sides" - detecting genuine opposing-whale-conviction situations during
volatile stretches (using `market_history.volatility()`, shipped this
session, as a real input), filtering that signal from ordinary noise, with
the actual response mechanism (reverse the position, add to it, something
else) explicitly left as a later decision rather than guessed at now. This
overlaps meaningfully with the still-open wash-trading/alternating-side
detector above (both are fundamentally about "what does it mean when real
money shows up on both sides of the same market close together") and
should be designed as one coherent piece, not two separate mechanisms that
end up disagreeing with each other.

## Recommended sequencing

1. The active-management design above - directly requested, next up.
2. Finish the in-flight per-series/market config pivot (already approved,
   partially shipped).
3. **Unthrottled concurrency (Part 3 Item 1) and its two dependents** -
   re-ranked above event-lifecycle awareness given this session's own live
   evidence (45-66 rate-limit hits every tick, right now, not historical).
4. Event-lifecycle awareness — the largest single open item, and a
   prerequisite for several medium items (regime-aware gating, wash-trading
   detection both benefit from knowing what phase a series is in).
5. Category-level legal risk — blocks "Path to production," worth resolving
   before it's the last thing standing between paper and real capital.
6. Everything else, roughly in the size order above — small first.

---

## Appendix: notable data points worth preserving verbatim

**Fees** (`services/kalshi_fees.py`, live-verified against 3 real fills):
`taker_fee = round_up_to_$0.0001(0.07 × contracts × price × (1 − price))`,
max **$0.0175/contract** at 50¢. Maker fee confirmed (2026-08-14, against the
real fee schedule PDF) at exactly 1/4 the taker rate. Ten series carry a real
0-multiplier full fee waiver (`KXBTCY`, `KXCITRINI`, `KXDOED`,
`KXELECTIRAN`, `KXGAMBLINGREPEAL`, `KXGREENLAND`, `KXIRANDEMOCRACY`,
`KXLAYOFFSYINFO`, `KXPAHLAVIHEAD`, `KXETHY`).

**Favorite-longshot bias**: Bürgi/Deng/Whelan (CEPR DP20631, 300k+
contracts) — takers lose ~32% on average buying longshots vs. ~10% for
makers, a ~3x gap; this app's real order path defaults to taker.

**Whale-signal research**: Mitts & Ofir (~93k markets/~50k wallets) —
210,718 flagged suspicious wallet-market pairs, 69.9% aggregate win rate,
>60 standard deviations from chance. Gomez Cram et al. — accuracy
concentrated in ~3% of accounts. Barclay & Warner (1993) — the strongest
empirical caution against naive whale-watching: most cumulative price change
traces to *medium*-size trades, not the largest blocks.

**Election-market accuracy**: Clinton & Huang — hit-rate PredictIt 93%,
Kalshi 78%, Polymarket 67% (disputed by Kalshi on calibration-vs-hit-rate
grounds, unresolved). IEM — 1.33-1.34pp average absolute forecast error
across 14 presidential contracts, beat individual polls 74% of the time.

**Live confidence weights** (`services/whale_simulator.py`, real-data
retuned): `depth_factor 0.21, unusualness_factor 0.09, proximity_factor
0.13, context_factor 0.08, agreement_factor 0.13, cluster_factor 0.13,
trend_factor 0.08, analyst_factor 0.15`. `unusualness_factor`/
`agreement_factor` were cut to a 0.05 floor after showing *negative*
discrimination against ~9,200 real resolved signals.

**Whale-threshold conventions researched**: Oddpool floors at $1,000
(adjustable), Polywhaler/CrossOdds use $5-10k — basis for this app's
$2,500 initial default (now live-tuned to $5,000 + per-series overrides).

**`conviction_boost` curve** (source skill researched for the never-built
multi-strategy framework, worth reusing if that's ever built): 2 signals →
+15%, 3 → +30%, 4+ → +45-50% capped, 48h lookback.

**Config fragmentation**: 16 distinct `strategy.*` fingerprints existed at
one point, most with 0 resolved trades — the direct motivator for
`change_effect_windowed()`.

**Real incident numbers** (whale-exit stale-price bug): ticker
`KXBTC15M-26AUG110215-15`, stale quote 0.67 vs. real fill 0.82, fabricated
-21% unrealized loss, stop-lossed 0.146 seconds after opening, market
later settled at 0.999.
