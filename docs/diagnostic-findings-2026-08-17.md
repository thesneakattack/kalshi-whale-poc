# Diagnostic findings — 2026-08-17

Written in response to a live session's three reports: "bad whale signals…
0-1 or 99-100 at exact or even after closing", "even worse position
management", and "on kalshi ill see at least 20 whale worthy trades that
should appear… that dont appear at all". Everything below is measured
against real data, with the measurement method stated so it can be re-run
and disputed. Where an earlier guess of mine was wrong, it's marked.

## TL;DR

Four *independent* problems presented identically as "bad signals" from the
dashboard. They have nothing to do with each other and need separate fixes.

| # | Problem | Status | Severity |
|---|---------|--------|----------|
| 1 | Coverage: the app sees ~2% of active markets | open | **dominant** |
| 2 | Profitability: entries outside the validated 0.5–0.8 price band | open (config) | **high** |
| 3 | Runway: entries with no time left to manage | fixed in code, unset by default | high |
| 4 | Density: per-ticker signal flow too sparse for exits to engage | open | **high** |
| 4b | Signal accuracy | **not broken** — stable 71–83% throughout | resolved |

## 1. Coverage — the dominant problem

The trade websocket subscribes with an explicit `market_tickers` list built
from the discovery watchlist, so **a trade on any unwatched market is never
received at all**. It isn't filtered or rejected — it never arrives, and
therefore appears in no log, no candidate_log rejection row, and no stat.

Measured 2026-08-17T00:37Z via exchange-wide `GET /markets/trades`:

- exchange rate: **~7,382 trades/min**, **425 distinct markets** trading in a
  30-second window
- watchlist at that moment: **15 markets**, of which **8** were trading
- whale prints ≥$2,500 in that window: **5** — **all 5 invisible** (100% miss)
- extrapolated: **~1,000 qualifying prints/hour** never seen

Real examples missed while the app was idle: `KXWTAMATCH-26AUG16GAUSAM-SAM`
12,121 contracts / $5,333; `KXATPMATCH-26AUG16SHEFAR-FAR` 11,567 / $7,634;
`KXCPLMATCH-26AUG161900BARSTL-BAR` 7,113 / $4,694. Note `KXATPMATCH-…-SHE`
*was* watched while `-FAR`, the other side of the same match, was not.

The watchlist is also **unstable**, not merely small — sampled at 3-second
intervals it read 2 → 2 → 18 → 15.

### Why the watchlist is so small — the funnel

| stage | count |
|---|---|
| catalog open candidates | 27,746 |
| after `categories: [Sports, Crypto]` + `min_volume_24h: 100000` | 416 markets / 52 series |
| after `top_series_per_category: 8` | ~9 series |
| after `round_robin_select(watchlist_size=50)` | **15 markets** |

`top_series_per_category: 8` is the dominant limiter (52 eligible series →
9). `min_volume_24h: 100000` is what specifically excludes **newly-opened
markets**, which is the "within minutes of opening" case reported: a market
that just opened has ~0 24h volume by definition and can never clear a
100,000 floor. `KXBTC15M` only works because it has an explicit
`min_volume_24h_by_series: {KXBTC15M: 0}` override.

### The real fix

`docs/kalshi/public-trades.md` states market specification is **optional**
on the `trade` channel — subscribing without `market_tickers` streams the
entire exchange. That decouples whale *detection* from the discovery
watchlist entirely, which is the correct architecture: discovery should
decide what we're willing to *trade*, not what we're allowed to *see*.

Caveat already identified: `kalshi_trade_tape.py` does
`markets_by_ticker.get(ticker)` and `continue`s on a miss (silently, with no
candidate_log row), so going exchange-wide needs an on-demand market fetch
or a degraded-confidence path first. `websocket-connection.md` lists a
"subscription market limit exceeded" error but the mirror doesn't give the
number — raise incrementally and watch for it.

Interim, config-only mitigation: raise `top_series_per_category`, lower
`min_volume_24h`, raise `watchlist_size`.

## 2. Profitability — the 52456f0 lesson recurring

`min_unit_cost`/`max_unit_cost` (0.5/0.8) were introduced by commit
`52456f0` to fix the original "near 70% win rate but only pennies earned"
problem. Today's **global** values are still the stress-test values
`0.3`/`0.95`; the validated 0.5/0.8 pair currently exists only inside
`strategy_overrides.by_series.KXBTC15M` and `by_category.Sports`.

Measured over 24h (48 whale-follow entries):

- **24 entries (50%) above 0.80 unit cost**, 5 below 0.50, 19 inside the band
- at 0.95/contract a win pays **$0.05** and a loss costs **$0.95** — at a 70%
  win rate that is **−$0.23/contract expected value**

Realized P&L, same window: **−$5,303 net**. 25 stop-losses totalling
**−$6,422** (avg −$257) against 17 settled wins totalling **+$573** (avg
+$34) and 3 take-profits (+$669). High win rate, deeply negative P&L — the
exact signature `52456f0` documented.

This is why "70% win rate" is not by itself evidence of a good config.

## 3. Runway — ROADMAP #1, now implemented

`close_window_sec` was only an *upper* bound on time-to-close.
`special_market_min_seconds_to_close` looks like a lower bound but only
applies to markets with `can_close_early`/`collateral_return_type`/
`mutually_exclusive` set — which plain crypto price-crossing markets never
have.

- **755** KXBTC15M whale signals in 6h fired inside the final **60 seconds**
  of their market's life; the latest was **8 seconds** before close
- **12 of 25** stop-losses fired only after price had already gapped **≥10
  points past** the configured limit (avg fire depth 97% vs a 90% limit) — a
  percentage stop cannot help on a market that settles to zero

Shipped in commit `2974e42`: `strategy.min_seconds_to_close` (entry) and
`strategy.exit_min_seconds_to_close` (exit). **Both default to unset = no-op**
and must be set in `config/settings.yaml` to take effect.

### Correction to an earlier claim

I initially implied signals were firing *after* close. That is **false** —
across 20,305 KXBTC15M signals in 6h, **zero** fired after `close_time`. The
real finding is the final-60-seconds flood above, plus exits landing after
close (e.g. a position on `KXBTC15M-26AUG161915-15` closed 6s past its
close). Stated here because the wrong version is more alarming than the
truth.

## 4. Signal density — the real mechanism behind "quiet maintain and win"

**This section was rewritten after a direct correction. My first version
measured signal *accuracy* to evaluate a hypothesis that was about
*latency/data flow*, then aggregated by day — which averaged a 28-minute
deliberate test into a 24-hour bucket and produced a "50.6% collapse"
headline that was an artifact of my own bucketing. Both errors are
corrected below; the original conclusion ("hypothesis refuted") was
wrong.**

### What the $1 window actually was

`whale_watcher_kalshi.min_notional_usd_by_series.KXBTC15M` was set to `1`
at **20:36:32Z** and back to `1000` at **21:04:55Z** — a **28-minute**
deliberate test of end-to-end latency through watchlist → whale stream →
position open → position management. Not a misconfiguration. It produced
~20,075 of that day's ~20,306 KXBTC15M signals, so any day-level statistic
covering 08-16 is dominated by it and is meaningless without excluding it.

At hour granularity, KXBTC15M at a legitimate threshold reads **100.0%
(18:00, n=27)** and **100.0% (19:00, n=25)** on that same day.

### What the test proved

`exit_on_sentiment_reversal` requires `exit_sentiment_min_signals: 15`
prints **on the same ticker** before it can fire. Measured across 47 closed
positions:

- **only 15 (32%) ever accumulated 15+ prints on their own ticker**
- for the other **68%, sentiment-based exit is structurally unreachable**,
  regardless of how sentiment actually moved

And the eligible ones are almost entirely KXBTC15M *during the $1 window*
(3,189 / 2,128 / 1,294 / 10,237 prints per position). Sports positions in
the same period got 0, 0, 1, 7, 9, 16 prints over lifetimes of 8–50
minutes.

Per-ticker signal gaps outside that window: `KXMLBGAME` median **20s**, p90
**298s**; `KXATPMATCH` median **32s**, p90 **212s**, max **5,771s** (96
minutes).

### The corrected conclusion

The hypothesis is **substantively correct**, with a sharper mechanism than
"the old API front-loaded data": **position management is gated on
per-ticker signal density, and density is currently far too low for the
management logic to ever engage.** The causal chain:

1. coverage is 15 markets (§1) →
2. few prints land on any one held ticker →
3. `exit_on_sentiment_reversal` and the `auto_exit` composite never reach
   their sample-size gates for 68% of positions →
4. positions ride to stop-loss or settlement unmanaged →
5. and because half of entries sit above 0.80 unit cost (§2), each such
   loss is close to maximal.

The $1 test demonstrated stage 3 clearing when density is high — the
pipeline *does* manage positions properly when fed. But $1 signals are
noise, so it wins nothing. **The fix is density from coverage at a
legitimate threshold (§1), not density from lowering thresholds.**

Latency itself is not the bottleneck: signal→open is **p50 0.32s**, though
the tail is real (**p90 16.6s, max 22.4s**), which matters on a 15-minute
market.

## 4b. Signal accuracy — genuinely not broken

Accuracy by day, **excluding KXBTC15M**, is stable and high throughout, so
nothing about signal *correctness* regressed:

Hypothesis under test (user's): the earlier ~70% win rate came from an older
internal API structure that front-loaded market data before rate limiting
throttled it, giving enough information to quietly maintain and win.

`signal_log.resolved/correct` survives paper-broker resets, so it reaches
back 4.8 days — further than `paper_broker.trades` (0.2 days after today's
reset). Accuracy by day, **excluding KXBTC15M**:

| day | n | accuracy |
|---|---|---|
| 08-12 | 8,833 | 71.4% |
| 08-13 | 21,505 | 73.9% |
| 08-14 | 588 | 83.2% |
| 08-15 | 531 | 80.6% |
| **08-16** | 499 | **80.2%** |

Non-crypto signal accuracy **never degraded**. Per-series, early (08-12→15)
vs late (08-16+): `KXMLBGAME` 68.6%→81.1% (**+12.5**), `KXWTAMATCH`
72.7%→92.7% (**+19.9**), `KXATPMATCH` 75.5%→77.3% (**+1.8**).

Per-series, early (08-12→15) vs late (08-16+): `KXMLBGAME` 68.6%→81.1%
(**+12.5**), `KXWTAMATCH` 72.7%→92.7% (**+19.9**), `KXATPMATCH`
75.5%→77.3% (**+1.8**).

The only series showing an apparent drop is KXBTC15M, and that is entirely
the 28-minute $1 test window described in §4 — at hour granularity, outside
that window, it reads 100% / 100% on the same day.

**Conclusion: signal correctness is fine and always was.** What is broken is
**opportunity** (§1 coverage), **manageability** (§4 density), and
**profitability** (§2 price band) — three separate things, none of which is
signal quality.

## Data-integrity note

`signal_log` currently holds **20,147 rows (96.6% of a 24h window)** below
the notional floor their series is now configured for — residue from the
20:36–21:04 stress-test window. Every consumer (`confidence_calibration`,
`regime_analytics`, the whale-winrate filter) reads those rows with no idea
which config epoch produced them, so they silently skew sample-size-gated
heuristics. The Danger Zone's time-range clearing (commit `f4021fd`) exists
precisely for this; the frontend for it is not built yet.

## Reproducing all of this

`services/diagnostics.py` (commit `e6913ee`) automates every check above:

```
GET /api/diagnostics?hours=24      # offline: integrity, price band, runway, epochs
GET /api/diagnostics/coverage      # makes real API calls; the §1 measurement
```

All checks are read-only and report `unknown` with a reason rather than
fabricating a number when they can't be computed.
