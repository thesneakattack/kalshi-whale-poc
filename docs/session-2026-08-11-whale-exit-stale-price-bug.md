# Session notes — 2026-08-11: whale trades instantly stop-losing on stale prices

Status: **fix written and unit-tested, not yet committed or live-verified.**
Pick this back up before doing anything else with `strategy_engine.py` or
`check_exits`.

## What was reported

Direct report: "there a MASSIVE bug, whale informed trades arent actually
occuring. they dont show up in the portoflio page as open positions. likely
theres a problem with the whole stream itself."

## What was actually wrong

The whale signal stream and trade placement were both fine (328 signals
seen, trades genuinely being opened). The bug: `FollowTheWhaleStrategy.
check_exits()` (`services/strategy_engine.py`) runs once per trading-loop
tick, **right after that same tick's signal loop may have just opened new
positions**, and it marked-to-market using `state["latest_prices"]` — a
quote-poll snapshot taken at the *top* of that same tick, before the
trade-tape read that generated the new position. On a fast-moving market, a
whale's real fill can already be more current than that snapshot, so a
brand-new position got judged against a price that predates its own entry.

Confirmed against real data, not inferred:

- `data/market_history.db`, table `snapshots`, ticker
  `KXBTC15M-26AUG110215-15`: quote poll went `0.67` (t=1786428521) →
  `0.928` (t=1786428576).
- A whale bought **yes @ 0.82** in between those two polls (trade tape,
  more current than the `0.67` snapshot).
- `check_exits()` ran in the *same tick* the position opened, used the
  stale `0.67`, computed a fabricated **-21% unrealized loss**, and
  stop-lossed the position **0.146 seconds** after it opened
  (`hold_sec: 0.14627504348754883` in `broker.recent_trades`).
- That market went on to settle at `0.999` — this would have been a big
  winner had it survived to the next tick's fresh price.
- Two more of the last handful of trades on the same 15-minute BTC markets
  died the same way, within seconds to a few minutes (`hold_sec` 62s, 304s).

Not a stream/detection bug, and scoped to the whale-follow strategy only:
`services/market_strategy.py` (the market-native strategy) doesn't have
this problem — its entries and exits both derive from the same single
per-tick `markets` poll, so there's no second, more-current data source to
race against.

## Fix shipped this session (uncommitted)

- `services/strategy_engine.py`: `check_exits()` gained an `opened_since`
  param. Any position with `pos.opened_at >= opened_since` (i.e. opened
  this same tick) is skipped — settlement handling is untouched, still
  unconditional.
- `main.py`: the `check_exits()` call site now passes `opened_since=
  tick_now`.
- `tests/test_strategy_engine.py`: two new regression tests —
  `test_check_exits_skips_position_opened_this_same_tick` and
  `test_check_exits_still_applies_to_positions_opened_before_this_tick`.
- Full suite: **724 passing** (was 722 before this session's other work
  landed at 722; +2 net here).
- Verified `--reload` picked the change up cleanly (`ddev logs -s fastapi`
  shows three clean `WatchFiles ... Reloading` events, zero tracebacks),
  and the persisted broker bankroll (`$10,888.13`) survived the resulting
  process restart untouched.

## Not done yet — pick up here tomorrow

- [ ] **Live-verify against a real trade.** Only unit-tested so far — no
      real whale signal has cleared the 0.5 confidence threshold since the
      fix deployed (that's infrequent; historically ~4 qualifying trades
      across many hours of ticks). Check `GET /api/state`'s
      `broker.positions` / `broker.recent_trades` for a trade opened after
      this session whose `hold_sec` (once closed) is meaningfully longer
      than one poll tick (`kalshi.poll_interval_sec` = 15s in
      `config/settings.yaml`) — that's the real confirmation this was
      fixed, not just that the code compiles.
- [ ] **Commit the fix** — `main.py`, `services/strategy_engine.py`,
      `tests/test_strategy_engine.py` are all modified but uncommitted.
      Per this session's own instructions, nothing gets committed without
      being asked.
- [ ] **Decide on `config/settings.yaml`'s `mode: paper → shadow` edit** —
      still sitting uncommitted, predates this bug and is unrelated to it
      (`shadow_active` only gates the parallel `ShadowTrader` logger in
      `main.py`, never the real `strategy.evaluate()`/`broker.
      open_position()` path the paper broker actually trades through — see
      `main.py:1896-1947`). Commit it deliberately, revert it, or leave it
      — just don't let it sit as an accidental diff.
- [ ] **Once live-verified: fold into `ROADMAP.md`/`static/status.html`**
      the normal way (`/sync-status-docs`) — this doc is a session-scoped
      stand-in until then, not a replacement for that pass.

## Where to look if picking this back up cold

- The exact price series that exposed the bug:
  `data/market_history.db` → `snapshots` WHERE
  `ticker='KXBTC15M-26AUG110215-15'` AND `timestamp BETWEEN 1786428500 AND
  1786428900`.
- The three affected trades: `data/paper_broker.db` → `trades`, or
  `GET /api/state`'s `broker.recent_trades`, around timestamps
  1786428455–1786428551 (all `KXBTC15M-*`) plus one on
  `KXWTAMATCH-26AUG10ALESVI-SVI` (that one held ~14.9h before a legitimate,
  much slower stop-loss — not part of the same-tick bug, included above
  only as a normal-case contrast).
- The fix itself: `services/strategy_engine.py`'s `check_exits()`
  docstring now has the full incident writeup inline, next to the
  `opened_since` skip check.
