# Profit-maximization assessment — 2026-08-15 (session 2)

Direct request: re-read `docs/next-steps-2026-08-15.md`, assess the app for
logic holes/gaps/quirks/bugs, and produce a to-do plan for maximizing
profit given the real win rate. Mid-session follow-up: "it looks like i
have 4 different exit strategies" — folded into the same pass rather than
treated separately, since it's the same question (is the app's own
complexity working against the win rate?).

Method: read the core money-math/gating files directly
(`paper_broker.py`, `strategy_engine.py`, `kalshi_fees.py`,
`risk_manager.py`, `trade_analytics.py`, `advisory_engine.py`'s
recommendation logic, `position_netting.py`), pulled every existing live
analysis endpoint against the real 607-trade whale-follow book and
4,861-trade market-native book, and ran one background sweep specifically
re-hunting for new instances of the "displayed value must match its label"
bug class CLAUDE.md already documents twice. Real numbers throughout, no
example/synthetic figures.

## Headline numbers (whale-follow, the real book — 607 closed trades)

- **Win rate: 68.4%** (415W/192L) — genuinely good, matches the
  sigma-vetted baseline from the last pass.
- **Total realized P&L: +$623.13.** Gross (before fees): **+$1,557.82**.
- **Total fees paid: $934.69 — 60% of gross profit.**
- **Total left on table (early exits that would've paid more at
  settlement): $1,796.24** — 2.9x the net realized profit.
- By close type: `settled_win` avg **+$27.09** (n=367), `settled_loss` avg
  **-$68.30** (n=155) — losses run 2.5x the size of wins, the classic
  favorite-longshot shape from the existing 0.5–0.8 `unit_cost` band.
  `take_profit` avg **+$85.97** (n=28, currently disabled). `auto_exit` avg
  **-$8.94** (n=45, currently disabled). `sentiment_reversal` avg **-$27.20**
  (n=8, 1 win, currently disabled). `stop_loss` avg **-$172.50** (n=3, too
  small to read).

market-native (the control book, 4,861 closed trades, not the primary
strategy per standing instruction) is currently net-losing: 24.0% win
rate, **-$9,451.58** realized, $5,181.59 in fees. Not this session's focus,
but one finding below concerns it directly.

## Confirmed bugs, ranked by severity

### 1. `strategy.kelly_fraction_of_cap: null` crashes position sizing — reproduced directly

`services/strategy_engine.py:380-381` and `services/market_strategy.py:255-256`
read the field as `strat_cfg.get("kelly_fraction_of_cap", 0.0)`. Python's
`.get(key, default)` only returns the default when the *key is absent* — a
key present with value `None` (YAML `null`) returns `None` unchanged. That
`None` is passed straight into `kelly_scaled_max_size()`
(`strategy_engine.py:93`), which does `if kelly_fraction <= 0 ...` —
`None <= 0` raises `TypeError`. Reproduced directly:

```
$ ddev exec -s fastapi python3 -c "from services.strategy_engine import kelly_scaled_max_size; kelly_scaled_max_size(100.0, 0.9, 0.75, None)"
TypeError: '<=' not supported between instances of 'NoneType' and 'int'
```

This matters more than a normal crash because of *where* it sits:
`main.py`'s tick loop calls the whole signal-evaluation loop
(`main.py:2275-2276`), `strategy.check_exits` (`:2290`), and
`position_netting.review` (`:2304`) all inside **one** `try` block with a
single catch-all `except Exception: state["error"] = str(e)` at `:2309`. A
crash partway through signal evaluation means **exit management and
position netting never run for that tick** — not just the one signal that
tripped it.

`null` is not an edge case here — it's the exact "disabled" convention
this same codebase uses everywhere else (`take_profit_pct`,
`stop_loss_pct`, `max_open_positions_per_series` all treat `null` as
off/unlimited). `kelly_fraction_of_cap: null` was the **committed
default in HEAD** until today's session, and was reintroduced live for
14.3 hours today (2026-08-14 13:23 → 2026-08-15 03:41 CDT, per
`config_performance`'s own audit trail) before being manually corrected
back to `0`. Whether it actually fired during that window depends on
whether any whale signal cleared entry_threshold/unit_cost/cooldown/
concentration gates in that stretch (inconclusive from the trade log
alone — zero entries either way, but the equal-length prior window also
shows zero, so entries are just sparse in general). Either way, it's a
live landmine, not a hypothetical: any operator who sets this field to
`null` again — the single most natural way to try to "turn Kelly off,"
mirroring three other fields in the exact same config block — retriggers
it.

**Fix**: one line, either `kelly_fraction = strat_cfg.get("kelly_fraction_of_cap") or 0.0`
at both call sites, or (better — protects both today and any future
caller) a `None`-guard inside `kelly_scaled_max_size()` itself.

### 2. Real-account header mixes cash-only and portfolio-value scopes (found by background sweep)

`static/index.html:5592-5595` (`renderHeaderStrip`, real-account branch):

```js
const portfolioValue = bal.portfolio_value != null ? bal.portfolio_value / 100 : null;
const first = realBalanceHistory[0];
const pnl = (portfolioValue != null && first) ? portfolioValue - first.balance : null;
```

`first.balance` comes from `main.py:2235-2242`, which only ever persists
the **cash** field (`real_balance.get("balance")`) into
`real_balance_history` — never `portfolio_value`. So "Change (session)"
subtracts a cash-only historical baseline from a cash+positions current
total. The app's own frontend documents these as genuinely different,
non-interchangeable numbers elsewhere (`static/index.html:6164`:
*"'balance' is uninvested cash; 'portfolio_value' is cash + open
positions' value ... not a fallback for each other"*) — this is the same
mistake made against its own documented distinction.

Concretely: at the first poll after a restart, if the real account has
$500 cash and one already-open $500 position, `portfolio_value` = $1,000
but `first.balance` = $500. Nothing has to actually move for "Change
(session)" to display **+$500** out of nothing. Every prior
`real_balance_history` bug on record (status.html) was a cents/dollars
unit error — this is new, and it's specifically in the real-money display
path, which is exactly the surface `ROADMAP.md`'s "Path to production"
shadow-mode review depends on being trustworthy.

**Fix**: persist `portfolio_value` history alongside (or instead of)
cash-only history, and diff like-for-like.

### 3. Advisory engine's counterfactual comparison has no significance test — and it already produced a backwards suggestion that got applied today

`services/advisory_engine.py:558-614` (`_rejected_candidate_recommendations`,
shipped phase 98) suggests loosening a gate whenever rejected candidates'
hypothetical win rate is within `_COMPARABLE_MIN_WIN_RATE_GAP = 15`
percentage points of accepted trades' actual win rate (line 60), at n≥5
(`_REJECTED_CANDIDATE_MIN_N`, line 553). This is a **flat point-gap
tolerance with no reference to sample size** — a fundamentally different
(and much weaker) standard than this same app's own sigma-vetted
methodology (`services/stats_power.py`, the t-stat/proportion-z-score
process documented in `config/settings.yaml`'s `strategy_overrides`
comment block from the *last* session).

Running that same proportion z-test
(`(rejected_wr - accepted_wr) / sqrt(p(1-p)/n) * 100`) against every gate
`candidate_log` currently has data for:

| gate | rejected win rate (n) | accepted win rate | z |
|---|---|---|---|
| `min_whale_winrate_pct` | 55.1% (n=49) | 68.4% | **-2.00σ** |
| `close_window` | 78.9% (n=76) | 68.4% | +1.97σ |
| `special_market_gate` | 96.3% (n=27) | 68.4% | +3.12σ |
| `entry_threshold` | 79.3% (n=720) | 68.4% | +6.29σ |
| `entry_confidence_threshold` (market_native) | 41.4% (n=29) | 24.0% | +2.19σ |
| `min_momentum_delta` (market_native) | 66.8% (n=750) | 24.0% | +27.44σ |

Every gate except one clears a real, honest significance bar in the
*loosen* direction (some very comfortably). **`min_whale_winrate_pct`
does not** — its rejected pool performed **worse** than accepted trades,
by about 2 standard errors, the opposite of what its generated rationale
claimed ("comparable to or better than accepted trades' actual 68%").
That suggestion was reviewed and applied today anyway
(`min_whale_winrate_pct: 85 → 76.5`, `config_performance` id 271) — not
because you were shown misleading data on purpose, but because the
rationale text itself doesn't reflect what a real significance test would
say, and nothing in the pipeline runs one.

(Separately: `min_momentum_delta`'s 27σ result is real per this test but
implausibly large for an organic effect — worth a confound check, e.g.
whether the rejected pool is concentrated in a different time window or
series mix than the accepted pool, before leaning on it further. It's
market-native's own gate, not whale-follow's.)

**Fix**: replace the flat 15-point tolerance in
`_rejected_candidate_recommendations` with the same z-test
`stats_power.py` already implements elsewhere in this app, so this
mechanism holds itself to the standard the rest of the app already uses.
**Separately, your call**: whether to revert today's
`min_whale_winrate_pct: 76.5` back to 85 pending a cleaner re-check — I
did not revert it unilaterally.

### 4. Minor / latent — no-side fallback in the frontend

`static/index.html:2983` and `:6399`: `p.cost_basis ?? (p.size * p.entry_price)`.
The fallback formula is missing the `(1 - price)` no-side inversion — the
exact bug class CLAUDE.md already documents twice. Not live today
(`PaperBroker.state()` always populates `cost_basis`, so the `??` never
falls through), but it's a landmine for the day some future caller omits
`cost_basis`. Cheap to fix now while it's fresh: drop the fallback
entirely, or make it side-aware.

**Clean bills of health**, checked directly and worth stating plainly
rather than omitting: `services/position_netting.py`'s payout/EV math,
`services/kalshi_fees.py`'s fee formula, `services/paper_broker.py`'s
cost_basis/equity/mark_to_market, and `services/advisory_engine.py`'s
dollar-figure sourcing all correctly trace back to
`PaperBroker`'s canonical definitions — no new no-side or
label-mismatch bugs found in any of them.

## Direct answer: are the "4 exit strategies" redundant?

Not fully redundant, but real overlap — and the overlap is exactly the
kind of thing that already caused two of this app's own past bugs (the
`elif`-chain unreachability bug and the volatility-blind whipsaw bug,
both referenced in `strategy_engine.py`'s own comments, both already
fixed). What actually exists for whale-follow:

1. **`take_profit_pct`** — hard rule, unrealized gain ≥ X% of cost basis.
2. **`stop_loss_pct`** — hard rule, unrealized loss ≥ X%.
3. **`exit_on_sentiment_reversal`** — hard rule, whale sentiment flipped
   against the held side (`_whale_lean()`).
4. **`auto_exit_enabled`** — a composite 0-1 score blending *four*
   sub-factors (P&L magnitude, sentiment reversal, signal staleness,
   analyst divergence), evaluated **only as a fallback** when none of 1-3
   already fired this tick (correctly implemented as `if`/`elif` today).

The overlap: auto_exit's `pnl` factor is the same underlying signal as
#1/#2, just continuous instead of a hard cutoff; its `sentiment` factor
calls `_whale_lean()` — **the literal same function** #3 uses. The two
genuinely unique factors are staleness and analyst-divergence. So it's
closer to "3 hard rules, plus one soft fallback that re-weighs 2 of those
3 signals continuously and adds 2 new ones" than "4 independent
strategies" — but the config surface doesn't make that cheap to see:
**22 exit-related fields** across the Exit Management and Position
Netting panels for whale-follow alone (4 toggles + 13 auto-exit
sub-fields + 5 netting fields), before counting market-native's separate
3-field copy.

Data-driven verdict on what to actually do with it: **`take_profit` is
your best-performing close type by a wide margin (+$85.97 avg, n=28) and
it's currently off.** `auto_exit` and `sentiment_reversal` both have
negative track records (-$8.94 avg/45, -$27.20 avg/8) and are correctly
off right now. Given hard rules always preempt auto_exit when both are
configured, if you turn `take_profit_pct`/`stop_loss_pct` back on,
auto_exit's own `pnl` weight becomes largely redundant motion — a
concrete simplification is to zero `auto_exit_pnl_weight` once the hard
rules are active, leaving auto_exit to do only what it can't already do
elsewhere (staleness + analyst-divergence), which shrinks the real
decision surface without removing any capability.

## Whale-sizing: is a flat dollar minimum the right filter?

Third mid-session question: whale detection filters purely on a dollar
notional minimum (`whale_watcher_kalshi.min_notional_usd`, per-series
overridable) — does trade size *relative to the market's own volume*
matter more than a flat dollar amount?

Two separate things answer this, and they point in different directions:

**The entry gate itself is exactly what it looks like — flat dollars,
not volume-relative.** `kalshi_trade_tape.py:242-244` compares
`notional_usd` against `min_notional_by_series.get(series, default)` — a
static number, never `market.get("volume_24h_fp")`. Per-series overrides
(`min_notional_usd_by_series`, already set for BTC/ETH/Trump-mention
series) are a hand-curated approximation of "different markets need
different bars," not the real thing. And this gate's real, validated
performance is excellent as-is: candidates it rejects win only **11.3%**
of the time (n=965) vs. accepted trades' 68.4% — by far the strongest
single discriminator this app has data on. Nothing here argues for
loosening it.

**But the exact signal you're describing — size relative to this
market's own volume — already exists, as `depth_factor`**
(`services/whale_simulator.py:292-306`, the code's own comment: *"a
20,000-contract print is unremarkable in a 2M-volume market, huge in a
5,000-volume one"*) — an exponentially-saturating ratio of trade size to
`volume_24h_fp`. It shipped with a real, meaningful default weight
(`DEFAULT_WEIGHTS.depth_factor = 0.18`, second-highest of nine factors).
**The live config weights it at only 0.03** — a 6x cut from its shipped
default.

That cut is not arbitrary — it's exactly what
`confidence_calibration`'s real report (268 resolved signals) supports.
Bucketing every resolved signal into low/mid/high `depth_factor` terciles:

| factor | low | mid | high | gap | discriminates? |
|---|---|---|---|---|---|
| `depth_factor` | 76.4% (n=89) | 90.0% (n=90) | 71.9% (n=89) | -4.5pts | **no** |
| `agreement_factor` | 67.4% | 73.3% | 97.8% | +30.4pts | yes |
| `cluster_factor` | 70.8% | 80.0% | 87.6% | +16.8pts | yes |
| `trend_factor` | 68.5% | 82.2% | 87.6% | +19.1pts | yes |
| `unusualness_factor` | 100.0% | 81.1% | 57.3% | -42.7pts | no (inverted) |

`depth_factor` is non-monotonic (mid beats both low and high) and fails
`confidence_calibration`'s own discrimination test — which is exactly
*why* the live weight already sits at 0.03 instead of 0.18. So: your
instinct is reasonable and this codebase already tried almost exactly
what you're describing, but the current construction of it doesn't
show real predictive power against 268 resolved signals. That's a
reason to investigate the construction, not to conclude the idea is
wrong — candidates worth checking before assuming "impact doesn't
matter": whether 24h volume is too stale/coarse a denominator for a
15-minute BTC market vs. a multi-day political one (the exact same
per-series heterogeneity `min_notional_usd_by_series` already works
around elsewhere), whether the exponential-saturation curve's rate
constant needs retuning, or whether "impact" needs a different
denominator entirely (recent order-book depth rather than trailing 24h
volume). Not chased further this pass — flagged as the concrete next
step if you want to pursue this thread.

## Correction: `unusualness_factor` is not an amalgam

Fourth mid-session question, worth correcting directly rather than
building on a false premise: `unusualness_factor` is **not** multiple
values consolidated into one score. Its actual definition
(`whale_simulator.py:314-315`):

```python
traded_side_price = price if side == "yes" else (1.0 - price)
unusualness_factor = 1.0 - abs(traded_side_price - 0.5) * 2
```

One input (how far the traded price sits from a 50/50 coinflip), one
output. There's nothing "in there" to split out.

The actual structure already is what you're describing wanting: all
**9** factors (`depth`, `unusualness`, `proximity`, `context`,
`agreement`, `cluster`, `trend`, `analyst`, `block_trade`) are separately
computed and independently weighted in `config/settings.yaml`'s
`whale_confidence_weights` — none of them is a grab-bag of sub-signals
except one: **`context_factor`** (`whale_simulator.py:341-345`) *is* a
single derived number — this market's 24h volume, percentile-ranked
against every other market in the current batch — so if there's a
factor here worth splitting into independent sub-signals, that's the one
candidate, not `unusualness_factor`. Per the discrimination table above,
though, `context_factor` doesn't currently discriminate either
(gap -3.4pts) — same caveat as `depth_factor`: worth understanding why
before investing in restructuring it.

## Profit-maximization findings (data, not bugs)

**A. Fees are eating ~60% of gross profit.** $934.69 paid against
$1,557.82 gross. The fee formula itself is correct (confirmed above) —
this is a structural finding, not a bug: Kalshi's quadratic taker fee
(`0.07 * price * (1-price)`) peaks exactly at price=0.5, and this
strategy's own `min_unit_cost`/`max_unit_cost` gate (0.5-0.8) deliberately
targets the priciest part of that curve. `kalshi_fees.py`'s own docstring
confirms the maker rate is confirmed at exactly 1/4 the taker rate
(0.0175 vs 0.07) but **no maker/limit-order path exists yet** — real
orders default to `immediate_or_cancel`. That's the single largest
identified lever in this whole assessment, and it's already scoped
(`kalshi_account_client.py`), just not built.

**B. Kelly-scaled sizing (already built, tested, shipped) is fully
inert.** `kelly_fraction_of_cap: 0` for both strategies — every position
sized identically regardless of signal confidence, throwing away
whatever the confidence score knows the instant a trade is placed. Once
finding #1 above is fixed (so `null` can't crash it), turning this up
from 0 is a pure-config lever with no new signal or data required.

**C. `market_native`'s core momentum thesis may be inverted** (control
group, not your book, but a 27σ effect is too large to ignore given
9,722+ trades sitting there). Flagged, not acted on this pass.

## To-do plan, in priority order — status at end of session

1. [x] **Fix the `kelly_fraction_of_cap: null` crash** — done, tested.
2. [x] **Fix the real-account header P&L scope bug** — done.
3. [x] **Decide on `min_whale_winrate_pct`** — reverted to 85, then
   lowered further to 50 once a live symptom (blocking `KXBTCD`) exposed
   a bigger problem than the original borderline-evidence question. See
   the Resolution section above.
4. [x] **Fix the advisory engine's comparability check** — done, and
   widened in scope from 1 call site to all 6 that shared the pattern.
5. [x] **Decide on `take_profit_pct`** — enabled, but at a corrected,
   lower value (0.2, not the originally-suggested ~0.5-0.95) once the
   supporting data turned out not to transfer to the current unit_cost
   band. See the Resolution section above.
6. [x] **Turn up `kelly_fraction_of_cap`** — 0.3 for `strategy.*`.
7. [x] **Build the maker/limit-order path** — done in full for the paper
   book; real-account extension explicitly deferred (see Resolution).
8. [ ] Zero `auto_exit_pnl_weight` — not done; `auto_exit_enabled` is
   still off, so this has no live effect yet. Revisit if/when auto_exit
   is reconsidered.
9. [x] Fix the latent no-side frontend fallback — done.
10. [x] Confound-check `market_native`'s `min_momentum_delta` finding —
    done; was a confound (win-definition mismatch), real effect much
    smaller than it first looked. See Resolution above.

## Resolution (same session, continued) — direct request: "fix the bugs, act on the data-driven recommendations, implement new analyzers if needed"

Everything below happened after the assessment above, in the same session.
827 tests passing (was 810 at the start of this doc).

### Bugs — all fixed and tested

1. **`kelly_fraction_of_cap: null` crash** — `None`-guard added inside
   `kelly_scaled_max_size()` itself (protects every caller, not just
   today's two), plus `or 0.0` at both call sites for defense-in-depth.
   Regression test added reproducing the exact crash.
2. **Real-account header P&L scope bug** — `main.py` now persists
   `portfolio_value` into `real_balance_history` alongside `balance`;
   `renderHeaderStrip` diffs `portfolio_value` against
   `first.portfolio_value` (`null` until a post-fix history entry exists,
   not a guess).
3. **No-side frontend fallback** (`static/index.html:2983`, `:6399`) —
   dropped entirely rather than made side-aware; `PaperBroker.state()`
   already guarantees `cost_basis` is always present, so the fallback
   was dead code standing in for a guarantee that already exists.
4. **Advisory engine's flat-tolerance significance check** — turned out
   to be a **systemic** pattern, not the one call site originally found:
   `_COMPARABLE_MIN_WIN_RATE_GAP` (flat 15pts, no sample-size awareness)
   was used in **six** places across `advisory_engine.py`
   (`_entry_threshold_recommendation`, `_longshot_bonus_recommendation`,
   `_cross_variant_recommendations`, `_rejected_candidate_recommendations`,
   `_category_conditional_recommendations`, plus itself). All six now go
   through `_comparability_margin_pts()` (or a direct
   `stats_power.margin_of_error_pts()` call for the two comparisons
   against a fixed/aggregate reference), reusing this app's own existing
   margin-of-error math instead of an arbitrary constant. One existing
   test (`test_cross_variant_recommends_differing_fields_from_better_variant`)
   had its fixture n raised from 10 to 100 — its original n=10/20pt-gap
   case is genuinely not distinguishable from noise, which is exactly the
   bug being fixed, not a false positive in the fix.

   **Important correction to this doc's own earlier claim**: re-verifying
   `min_whale_winrate_pct`'s specific case against the *actual* fixed
   mechanism (not the ad hoc null-hypothesis z-test used above to first
   spot it) shows it's genuinely **borderline**, not a clean violation —
   the real margin at n=49 is ~13.9pts, leaving 55.1% just inside 68.4%'s
   interval (54.47 cutoff vs. 55.1 observed). Locked in as a documented
   regression test (`test_rejected_candidate_recommendation_matches_the_
   real_2026_08_15_incident`) precisely because the nuance is worth
   preserving, not smoothing over.

### Config changes applied (all via `POST /api/config`, real audit trail)

- **`min_whale_winrate_pct`**: `76.5 → 85` (reverted, given the borderline
  evidence above didn't clear a real bar) → **then `85 → 50`**, a second,
  larger change driven by a live symptom you reported directly ("why is
  the system avoiding crypto markets with super high whale winrates"):
  `strategy_engine.py:286-298`'s entry gate compares each series' win
  rate against this field as a **flat global floor** — and at 85%, it
  was flagging `KXBTCD` (80.5% win rate, n=82) as `AVOIDING`, despite
  `KXBTCD` being one of this book's two strongest **proven** performers,
  already sized up in `strategy_overrides.by_series` for exactly that
  reason. A global floor set *above* the book's own 68.4% average is
  guaranteed to reject roughly half of all series by construction,
  including good ones — not a tuning nuance, a real design flaw. 50 is
  comfortably below every currently-tracked series' win rate while still
  catching genuinely broken ones; the code's own fallback default
  (`strat_cfg.get("min_whale_winrate_pct", 40)`, used when the field is
  absent entirely) was already 40, well below where the live value had
  drifted to. The nuanced/surgical cases (like `KXBTC15M`'s real but
  moderate underperformance) are correctly handled by their own
  `strategy_overrides.by_series` entry instead, not by the blunt global
  floor.
- **`take_profit_pct`**: `null → 0.2`. Important correction to this
  doc's own earlier recommendation: closer inspection found **every
  single one of the 28 historical `take_profit` trades was priced
  outside the current 0.5–0.8 `unit_cost` band** (all between 0.18–0.49)
  — the entire "+$85.97 avg, best performer" track record comes from a
  price regime `min_unit_cost: 0.5` no longer even allows. That evidence
  doesn't transfer to current conditions, so this is now shipped as an
  **opt-in experiment at a value achievable across the current band**
  (0.2 = reachable even at the band's worst case, unit_cost 0.8, whose
  theoretical max gain is 25%) rather than the "proven, high-confidence"
  framing originally given. Worth watching, not assuming.
- **`kelly_fraction_of_cap`**: `0 → 0.3` for `strategy.*` (whale-follow)
  only — `market_strategy.*` left at 0, control group untouched per
  standing instruction.

### Maker/limit-order path — built, opt-in, off by default

Full implementation, not just scoping:
- `services/kalshi_fees.py`: `maker_fee()` — same formula/rounding/
  per-series-multiplier convention as `taker_fee()`, at 1/4 the rate.
- `services/paper_broker.py`: new `PendingOrder` dataclass, own
  `pending_orders` SQLite table (same persistence idiom as
  positions/trades — survives a restart), `place_limit_order()`,
  `check_pending_fills()` (side-aware fill check against real bid/ask,
  fills at the real available price if better than the limit, never
  worse; unfilled orders expire and cancel rather than falling back to
  a market order — never chases a price the original signal has moved
  past), `open_position()` gained a `fee_fn` param (defaults to
  `taker_fee`, unchanged for every existing caller).
- `main.py`: new `state["latest_asks"]` (genuinely missing when no fresh
  quote exists, never guessed — `check_pending_fills` must be able to
  tell "no data" from "a real 0.5 ask"), `check_pending_fills` wired into
  the tick loop right after signal handling and before `check_exits`
  (same `opened_since` freshness reasoning), new `_handle_fill_decision`,
  new `stats.limit_orders_placed` counter (a placed-but-unfilled order is
  neither a trade nor a skip).
- `services/strategy_engine.py`: new opt-in `strategy.use_limit_orders`
  (default `false`) / `strategy.limit_order_timeout_sec` (default `60`).
  When on, `evaluate()` rests a limit order at the signal's own observed
  price instead of taking the market immediately — identical
  sizing/entry-gate logic either way, this only changes execution.
- 13 new tests across both files (fill/no-fill both sides, real-price-
  better-than-limit, expiry charges nothing, missing-quote doesn't guess,
  persistence, `reset()` clears pending orders, one-order-per-ticker).

**Left off by default** (`use_limit_orders: false`), consistent with
every other opt-in mechanism in this app (`kelly_fraction_of_cap`,
`auto_exit_enabled`, `position_netting.enabled`) — this is genuinely new,
zero live track record yet, unlike e.g. take-profit which at least had
imperfect history. Turn it on when ready to watch it run.

**Explicitly not done this pass** (real scope boundary, not an
oversight): `services/kalshi_account_client.py`'s real order path was
not extended for real limit orders — real trading stays gated behind
`kalshi_account.trading_enabled` + typed confirmation regardless, so
this doesn't block anything today, but it means the fee-reduction lever
only applies to the paper book until someone deliberately extends the
real path too. No dashboard Config-tab panel for the two new fields
either (editable via `POST /api/config`/direct file edit only for now).

### Diagnostic findings (background research, no code changes from these)

- **`market_native`'s 27σ `min_momentum_delta` finding was a confound,
  not a backwards thesis.** The comparison mixed two different
  definitions of "win": the rejected pool used eventual-settlement-match
  (unlimited patience), the accepted pool used realized P&L after active
  exits, where `stop_loss` mechanically scores 0% by construction (54.4%
  of the whole book closes this way). Recomputed on the same basis, the
  gap shrinks from 42.8pts/~27σ to **10.1pts/z=5.12** — real, but nowhere
  near large enough to call the momentum thesis inverted. Separate,
  genuinely actionable side-finding for whenever market_native gets
  real attention: of 1,883 stop-lossed trades with known outcomes,
  **44.9% would have gone on to win** — `market_strategy.stop_loss_pct:
  0.2` may be cutting off nearly half its "losers" before they'd have
  won. Not acted on — control group, per standing instruction.
- **`depth_factor`'s non-discrimination has a confirmed root cause for
  KXBTC15M specifically**: within one 15-minute market's own signal
  history, Kalshi's `volume_24h_fp` field jumps in large discrete steps
  unrelated to that contract's own trading (one ticker: 57,569→1,416,268,
  a 25x jump in 65 seconds) — it behaves like a shared/rolling figure,
  not real per-contract volume, for very-short-duration series. KXBTCD
  (a longer-lived series) shows the field growing smoothly instead. Per-
  series discrimination checked directly (the two largest series with
  n≥30 both independently show the same non-monotonic/inverted shape as
  the aggregate) — no salvageable per-series signal hiding in the
  pooled data. Current 0.03 live weight (vs. 0.18 shipped default)
  stands confirmed-correct; not worth raising without first fixing the
  volume denominator for short-duration series specifically.

### Full sigma-vetted series/category re-analysis — no new overrides warranted

Direct request: "go through the data, analyze series, categories,
markets, and assign the category/series/market-specific changes that the
data supports." Full re-run of the exact t-stat/z-score methodology
against the fresh 607-trade book, 16 series and both categories tested.
**Honest answer: the data doesn't currently support any new
`strategy_overrides` entry.** All three existing overrides (`KXBTC15M`,
`KXMLBSPREAD`, `KXBTCD`) reconfirm at essentially unchanged strength (the
dataset only grew by 1 trade since the original pass, and it didn't land
in any of these three series). `KXMLBGAME`'s bimodal pattern reconfirms
as real but still correctly unshippable — `config_overrides.py` has no
unit_cost-bucket-scoped override tier, only `by_series`/`by_category`
(confirmed directly from the resolver's own `_TIERS`). `by_category`
should stay empty: Crypto's near-2σ blended signal is fully explained by,
and would actively conflict with, the two already-correct
oppositely-signed series underneath it (`KXBTCD` up, `KXBTC15M` down) —
shipping a Crypto-level override would dilute both correct existing
per-series ones. Two bucket-only findings (`KXMLBTOTAL` &lt;0.5 zone,
`KXATPCHALLENGERMATCH` ≥0.8 zone) don't clear the aggregate-corroboration
bar yet, worth re-checking as more trades close.

**Correction to this doc's own earlier text**: the "0.0-0.5 zone is real
evidence strongly positive" language used above (in the original
headline numbers section, inherited from the prior session's framing)
overstates what the data actually supports — that zone's n=16, t=1.19,
which is not statistically significant on its own. Directionally
positive in raw dollars, yes; a "real, well-supported" finding the way
the ≥0.8 zone's t=4.98 is, no.

## Explicitly out of scope, end of session

- The full per-series/unit_cost sigma pass **was** re-run later this same
  session (direct request) — see "Full sigma-vetted series/category
  re-analysis" above. Superseded, not stale.
- `regime/by-hour`/`by-day-of-week` still not pulled - available,
  genuinely unexamined this session (`by-category` was, as part of the
  re-analysis above).
- `series_evaluator.enabled` still not turned on - the global
  `min_whale_winrate_pct` floor fix above addresses the specific symptom
  that was reported (a good series getting blocked); series_evaluator
  itself is a separate, broader mechanism (qualifying-rate-based, not
  win-rate-based) that was only ever a read-only cross-check this
  session, not evaluated for whether turning it on is warranted.
- `market_native`'s book got real attention this session (the momentum
  confound-check, the stop_loss-cutting-off-winners side-finding) but no
  code/config changes were made to it anywhere - control group, per
  standing instruction. The stop_loss finding (44.9% of stopped-out
  trades would have won) is a real, actionable lever whenever it's
  someone's priority.
- `services/kalshi_account_client.py`'s real order path was not extended
  for maker/limit orders - see the maker-order section above for why
  that's a deliberate boundary, not an oversight.
- No dashboard Config-tab panel for `strategy.use_limit_orders`/
  `limit_order_timeout_sec` - both are live and functional
  (`POST /api/config`-editable), just not yet surfaced as a UI checkbox.
