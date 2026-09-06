# Next steps — position netting, config tuning, and what's still open

Written at the end of a long session as a deliberate handoff point — the
next session (or the next person) should be able to pick up cold from this
doc alone, without needing the full conversation history. Cross-references
`ROADMAP.md` (forward-looking checklist) and `static/status.html` phases 99
and 100 (the full backward-looking narrative for everything below) rather
than repeating their detail.

## What shipped this pass

1. **`services/position_netting.py`** — payout-profile-based detection and
   (opt-in, currently off) response for hedge-mode/concentration risk on
   confirmed mutually-exclusive event groups. `GET /api/position-netting/groups`
   is read-only and safe to poll anytime; the Portfolio tab's "Hedge-Mode /
   Concentration Check" panel shows the same thing.
2. **`services/config_overrides.py`** — generic per-category/per-series
   resolver for any `strategy.*`/`market_strategy.*` field, wired into both
   strategies. `config/settings.yaml`'s `strategy_overrides.by_series` has
   three real, sigma-vetted entries (`KXBTC15M`, `KXMLBSPREAD`, `KXBTCD`).
3. **`services/kalshi_fees.py`** — fixed a real fee-model gap (the entire MLB
   proposition-market family was charged double the real fee) found by
   live-sampling Kalshi's own `GET /series` endpoint. Already-recorded
   historical trades in both `data/paper_broker.db` and
   `data/market_broker.db` were corrected in place (not just the code fix).

All 810 tests passing as of this write. Live app healthy (`GET /api/state`:
`error: null`, `running: true`).

## Immediate next actions, roughly in priority order

### 1. Decide whether to enable `position_netting.enabled`

It's `false` by default (`config/settings.yaml`). The read-only endpoint
(`GET /api/position-netting/groups`) has been live-verified against the
real UFC and PGA groups and produces sensible, non-obvious classifications
(see status.html phase 99 for the exact numbers). Before flipping it on:

- Watch the Portfolio tab's panel for a while across different event types
  (not just the two groups already seen) to build confidence the
  classification is right in general, not just on the two cases checked.
- Re-read `services/position_netting.py`'s own module docstring and A4's
  reasoning in the (now-completed) plan — the `trim_worst_leg`/`close_all`
  candidate-action math, and why `add_to_best_leg` ("double down") was
  deliberately left out.
- If enabling: `min_edge_improvement_usd`/`normal_volatility`/
  `volatility_lookback_sec` in the same config block are the tuning knobs -
  no code changes needed to adjust sensitivity.

### 2. The `add_to_best_leg` ("double down") response — deliberately deferred

`_best_variable_action()` in `services/position_netting.py` only considers
`hold`/`trim_worst_leg`/`close_all`. A fourth candidate — adding capital to
strengthen the better-priced leg of a still-variable group, reusing
`kelly_scaled_max_size`/entry-threshold machinery against the *net*
exposure rather than a fresh independent signal — was scoped out as the
highest-risk direction (automatically *increasing* exposure). Build this
once `trim`/`close_all` have real live track record, not before.

### 3. `KXMLBGAME`'s real but bimodal price-zone finding — not actionable yet

Real, well-supported finding (bucket-level win-rate z = -3.4 vs. the book
average, n=31) that this series' whale signals in the `unit_cost` 0.5–0.8
zone specifically underperform — while its 0.0–0.5 zone is real evidence
strongly positive (n=16, +$367). The current `config_overrides` resolver
can only express a flat `min_unit_cost`/`max_unit_cost` *band* per series,
which can't cleanly carve out "avoid the middle, keep both ends" without
also cutting off zones the data doesn't condemn. Documented in
`config/settings.yaml`'s `strategy_overrides` comment block rather than
forced into a misleading override. If this persists as more data
accumulates, the real fix is a richer override shape (e.g. a list of
excluded sub-bands per series) rather than a single min/max pair — a
schema change to `config_overrides.py`, not a value tweak.

### 4. Re-run the sigma-vetted analysis periodically as more trades accumulate

The methodology (not just the specific numbers) is worth keeping: group
closed trades by series (and by `unit_cost` bucket within a series), then
before treating any gap as real, check **both**:
- a one-sample t-stat for mean `realized_pnl` vs. zero
  (`mean / (stdev / sqrt(n))`), and
- a one-sample proportion z-score for win rate vs. the book's own overall
  win rate (`(observed_pct - overall_pct) / sqrt(p(1-p)/n) * 100`, reusing
  the SE math already in `services/stats_power.py`).

Several plausible-looking raw-dollar findings this pass did *not* survive
this check (see `config/settings.yaml`'s comment block for the specific
ones ruled out, and status.html phase 99). With ~15-30 series/bucket
combinations tested at once, remember the multiple-comparisons caveat — a
couple of "significant-looking" results are expected from chance alone at
that count, so don't stop at the first low-p-value-looking number.

The exact scratch analysis scripts used this pass weren't committed to the
repo (per this project's own convention — scratch scripts live in a
session scratchpad, not the repo, to avoid disrupting the live `--reload`
dev server), so they'd need to be rewritten, not just re-run — but the
shape is straightforward: pull `GET /api/trading-history` (paginated),
group by `ticker.split("-")[0]`, bucket by side-aware `unit_cost`, apply
the two significance tests above.

### 5. Kalshi's real per-series metadata — only partially consumed

Live-sampling `GET /series/{ticker}` (not just the bulk `GET /series`
list this app already used for category filtering) surfaced fields this
app doesn't use anywhere yet: `tags` (used only informally in this pass'
own analysis, not wired into any live code path), `settlement_sources`,
`contract_url`/`contract_terms_url`, and — most relevant to the existing
open `ROADMAP.md` "Path to production" item on category-level legal risk
— **`additional_prohibitions`**, a real, per-series list of who's
restricted from trading a given contract (league employees, players,
people with material non-public information, etc.). This is a *different*
kind of restriction than the sports-category multi-state legal dispute
already tracked in `ROADMAP.md`, and hasn't been cross-referenced against
it. Worth a real look before real trading is ever considered.

Also worth deciding: the `fee_multiplier`/`fee_type` data this pass used
was a one-time snapshot (like the PDF-sourced list it replaced), not a
live-refreshed cache. If Kalshi changes its fee schedule again, this will
silently drift the same way the original PDF-sourced version did. The
"do it properly" version would mirror `services/title_cache.py`'s pattern
(fetch once, cache in `data/*.db`, refresh periodically) rather than a
hardcoded dict in `services/kalshi_fees.py` — not done this pass because
fee schedules change rarely and deliberately, but flagged honestly as the
same kind of gap that caused this pass' bug in the first place.

### 6. `market_strategy_overrides` exists but is empty by design

The resolver was wired into `MarketNativeStrategy` for code-path symmetry,
but per the standing instruction ("ignore the market native strategy...
it's a control to test against whale-follow, not ready yet"), no values
were ever populated. When market-native is ready to be evaluated as more
than a control group, the same sigma-vetted methodology above applies
directly — it just hasn't been pointed at that strategy's own trade
history yet (`data/market_broker.db`, 9,722+ trade rows already exist
there, more than enough for a first real pass).

## Everything else already tracked

Not repeated here — see `ROADMAP.md` directly:
- **"Path to production"** section — the real, still-open operational
  gates before any real capital: shadow-mode review, deployment target,
  `data/*.db` backup policy, monitoring/alerting, auth model, real
  position-sizing by a human (not a default), and the sports-category
  legal-risk question (see item 5 above for a related, newly-found wrinkle
  on the *same* underlying question).
- **P4 section** — `docs/platform-deep-scan-findings-2026-08-10.md` (7
  cited strategy/risk gaps, not yet closed) and
  `docs/hardening-and-accuracy-roadmap-2026-08-11.md` (event-lifecycle
  awareness, wash-trading detection, and others) are both still open and
  unrelated to this pass' work — worth a fresh look independent of
  everything above.

## Verification commands for picking this back up

```
ddev describe                                  # confirm it's running
ddev exec -s fastapi python -m pytest -q       # should show 810 passing
curl -sk https://kalshi-whale-poc.ddev.site/api/state | python3 -m json.tool | head -5
curl -sk https://kalshi-whale-poc.ddev.site/api/position-netting/groups | python3 -m json.tool
```
