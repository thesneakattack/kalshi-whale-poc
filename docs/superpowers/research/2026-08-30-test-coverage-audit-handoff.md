# Test-coverage audit + two live bugs — session handoff

Date: 2026-08-30. Status: findings verified firsthand, **nothing fixed yet**.
Written to be picked up cold by a fresh session; it assumes no memory of the
session that produced it.

Origin: a request to "derive data-layer performance/effectiveness and trade
analysis tests from this session, compare with those that already exist, fill
in gaps and add to it (or remove) as you think." Two subagents audited in
parallel. Both went past test gaps and each surfaced a **live production
defect**. Every claim below was re-verified against the source by the main
session before being written down — subagent assertions alone are not evidence
(CLAUDE.md "never guess; verify or falsify").

---

## Read this first: the two live bugs

### BUG 1 — `position_netting` treats a zero volatility reading as "perfectly calm"

`services/exits/position_netting.py:247` filters `None` but not `0.0`:

```python
vols = [market_history.volatility(ticker, vol_lookback, as_of=now) for ticker, _ in members]
vols = [v for v in vols if v is not None]          # <-- misses 0.0
if not vols:
    return min_edge_usd
vol_ratio = max(0.25, min(4.0, max(vols) / normal_vol))
return min_edge_usd * vol_ratio
```

**This is the identical bug the sibling call site was fixed for on 2026-08-17.**
`services/exits/exit_engine.py:547` carries the fix and, at `:526-544`, the
incident write-up:

> `vol == 0` is treated as NO READING, not as "perfectly calm" (2026-08-17).
> volatility() returns None when there aren't enough snapshots, but a real 0.0
> when there are and the price never moved - and measured live, **142 of 183
> well-sampled markets sat at exactly 0.0**, because a price that hasn't ticked
> in 30 minutes usually means nobody is trading it, not that it is genuinely
> placid. [...] No value of auto_exit_normal_volatility could fix it either - a
> zero numerator clamps to the floor regardless - so this belongs at the point
> of use.

**Why it matters, and why it is worse than a copy of the old bug.** The
function's own docstring states its intent: "a noisier read on the group's own
tickers means the live prices behind expected_value() are less trustworthy, so
a bigger edge is required before acting." A zero reading means *nothing is
trading* — the least trustworthy state there is — and it produces
`vol_ratio = 0.25`, the floor, which **quarters the bar instead of raising it**.
The defect inverts the function's stated purpose: netting churn fires most
easily exactly when the price data behind it is least trustworthy.

Scope note (narrower than exit_engine's, still large): this uses `max(vols)`, so
it only bites when *every* member of a group reads 0.0. Netting groups are
same-event markets, which are correlated in liquidity, so co-occurrence is
expected to be common — but it has not been measured. **Measure it before
claiming a blast radius.**

Interaction with live config: `min_edge_improvement_usd` was changed 50 → 10 on
2026-08-30 at 02:25 UTC. For an all-stale group the bar is therefore
`10 × 0.25 = $2.50`. That change was itself made on a premise later falsified
(see "User decisions still open").

Fix: mirror exit_engine — `[v for v in vols if v is not None and v > 0]`, or
guard at the ratio. Existing netting tests do **not** catch it: they all pass
`normal_volatility: None`, which short-circuits at the `if not normal_vol`
guard on line 246 before the filter is ever reached.

### BUG 2 — `breakeven_accuracy_pct` is fee-blind, and it is a displayed value

`services/series_watcher.py:731`:

```python
breakeven_accuracy_pct = round(mean_unit_cost * 100, 1) if mean_unit_cost is not None else None
```

Same computation in `services/reset/trade_archive.py:137`. It is rendered to the
user at `services/series_watcher.py:932`:

> `f"{r['breakeven_accuracy_pct']}% accuracy just to break even, so this series is "`

The label says "accuracy just to break even". The number is the **fee-free**
breakeven. The real figure is `c + taker_fee(1, c)` where
`taker_fee = 0.07 × c × (1 − c)`. Verified in Wolfram:

| unit_cost | app displays | true breakeven | understated by |
|---|---|---|---|
| 0.60 | 60.0% | 61.68% | 1.68 pts |
| 0.70 | 70.0% | 71.47% | 1.47 pts |
| 0.80 | 80.0% | 81.12% | 1.12 pts |
| 0.85 | 85.0% | 85.89% | 0.89 pts |
| 0.90 | 90.0% | 90.63% | 0.63 pts |

`max_unit_cost` is currently 0.85, so the live operating band understates the
bar by roughly 0.9–1.7 points on every series. The derived `edge_pts`
(`series_watcher.py:733`, `signal_accuracy − breakeven_accuracy_pct`) inherits
the error and **overstates edge at every entry**.

This is exactly the class CLAUDE.md names under "A displayed value must match
its label", alongside the two already-shipped bugs of this shape.

**A test pins the wrong value**: `tests/test_trade_archive.py:86` asserts
`r["breakeven_accuracy_pct"] == 70.0`. Fixing the bug fails that test — the
assertion must be updated to 71.47 as part of the fix, not treated as a
regression.

---

## The metric the live soak is gating on can silently read zero

Not a bug in the sense above — a **measurement hole**, and the highest-value
item in the data-layer audit, because it undermines the verification currently
in flight.

`docs/next-action.md` gates the `two_consumer_mode` soak on
`oldest_message_age_sec` staying near 0. That value comes from
`services/kalshi/websocket.py:1169` `_oldest_message_age`, which iterates
**only the three queues**:

```python
for queue in (self._queue, self._critical_queue, self._market_queue):
    if queue is None or queue.empty():
        continue
    ...
return round(max(ages), 4) if ages else 0.0
```

It never inspects `self._ticker_by_market`, the coalescing pending map added by
the same feature the soak is validating. The map is drained one entry per
consumer iteration, and the wake sentinel is enqueued **only on the empty →
non-empty transition** (`if was_empty:` at `websocket.py:1009`). So once that
sentinel is consumed, the queues can be empty while the map still holds
arbitrarily old entries, and no further sentinel is ever enqueued for entries
added while the map is already non-empty.

**Consequence:** a wedged or starved market consumer leaves a growing pending
map with all queues empty, and `_oldest_message_age` returns exactly `0.0` —
which the soak reads as perfect health. The metric cannot detect the specific
new failure mode the coalescing feature introduced.

This was already logged as a deferred Minor in the PR #198 review ("pending-map
staleness in `_oldest_message_age`") and is named in `docs/next-action.md`.
**It should be escalated from Minor to blocking**, because it is now the pass
criterion of an active verification, not a cosmetic gap.

Fix is a one-liner plus a test: fold
`max(now - ts for ts, _ in self._ticker_by_market.values())` into `ages`.

---

## Test gaps, prioritized

Priority reflects "what can currently lie to us", not test count.

### P0 — write with the fixes above

1. **Pending-map staleness.** Non-empty `_ticker_by_market`, all queues drained,
   assert `oldest_message_age_sec > 0`. Fails today.
2. **`position_netting` zero volatility.** A group whose members all read `0.0`
   with a real `normal_volatility` set. Must assert the bar is **not** reduced.
   Note every existing netting test passes `normal_volatility: None` and so
   never reaches the code under test.
3. **`breakeven_accuracy_pct` fee inclusion**, and update the pinned
   `tests/test_trade_archive.py:86` assertion from 70.0 to 71.47.

### P1 — guards that currently do not guard

4. **`_TICKER_WAKE` sentinel.** The existing test **stays green if the guard is
   deleted** — it does not exercise the parked-consumer wake path it names.
   Rewrite so deleting the `if was_empty:` block fails it.
5. **Ticker conservation invariant:**
   `received == processed + coalesced + pending + dropped`. Nothing asserts the
   coalescing path conserves messages, which is the completeness property from
   the data-plane HARD RULE.
6. **`ts_ms` ordering.** Every coalescing test uses the **deprecated `ts`**
   field. `_update_ts_ms` has no direct test, and no test mixes `ts` and
   `ts_ms` — that mixture is a 1000× dimensional error
   (`docs/kalshi/CHEATSHEET.md` records `ts` as deprecated in favour of `ts_ms`).

### P2 — isolation and hygiene

7. **No conftest reset for `settlement_resolver._pending` / `_stats`.** Module-
   level mutable state with no fixture reset; this is the exact shape of three
   previously documented cross-test-leak incidents.
8. **Stale fixture note.** `tests/fixtures/kalshi/market_lifecycle_settled.json:4`
   still describes the inline REST read that PR #198 removed. Fixture notes are
   what `kalshi-contract-review` treats as ground truth, so a wrong one actively
   misleads.

### Recommended instead of more unit tests: one shared helper + a CI scanner

The trade/config audit's strongest structural finding. The **frontend already
solved this**: `frontend/.../shared-utils.js:118` exposes `sideAdjustedPrice()`
with 23 call sites and zero inline re-derivations. The **backend did not**:
**26 inline re-derivations across 14 modules**, plus **three byte-identical
private `_unit_cost(side, yes_price)` helpers** — verified present at:

- `services/diagnostics/diagnostics.py:99`
- `services/series_watcher.py:505`
- `services/reset/trade_archive.py:111`

Three modules each independently concluded "write it once, here." That is the
signal for shared logic, not for N more unit tests. Proposed disposition
(CLAUDE.md requires one per bug class in the commit message): **shared logic +
CI guard** — one `unit_cost()` in a shared module, call sites migrated, and a
`tools/quality_audit` scanner that fails on a fresh inline `1 - price`
side-adjustment. This is the bug class CLAUDE.md names twice; a scanner closes
it permanently, whereas tests only cover today's call sites.

---

## User decisions still open (do not decide these unilaterally)

1. **`min_edge_improvement_usd` 50 → 10** and **`excluded_series: [KXATPMATCH]`**
   were applied 2026-08-30 02:25 UTC on premises that later analysis falsified.
   Both warrant reversion; the user has not ruled. The netting bug above
   interacts with the first.
2. **`max_daily_loss_pct` is 0.8** (set manually 02:32 UTC, from 0.2). CLAUDE.md
   itself flags this as "not protective". Needs a real number.
3. **PR #202 plan revision 3** — 8 recorded items; the spec was fixed at
   revision 2 but the plan still carries five of six defects in its tasks. The
   plan is **not implementation-ready**; do not execute it as-is.
4. **PR #201** ready to merge; afterwards merge `origin/main` into
   `feat/event-scoped-me-gate`.
5. **Unverified concern**, stated but never checked: that entries may have
   stopped. Verify time-since-last-trade before acting on it.

---

## How to resume

The soak in `docs/next-action.md` is still the standing next action and is
unaffected by this document, **except** that its pass criterion is now known to
be blind (see the measurement hole above) — fixing `_oldest_message_age` makes
the remaining soak boundaries meaningful, so it is reasonable to do that first.

Suggested order: BUG 1 and the `_oldest_message_age` fix (both one-liners with
tests, both affect live behaviour or live verification) → BUG 2 with its pinned-
test update → P1 guards → the shared `unit_cost()` + scanner as its own branch.

Branch for this document: `docs/test-coverage-audit-2026-08-30`.
Nothing in it has been fixed. No config was changed to produce it. All reads
were read-only.
