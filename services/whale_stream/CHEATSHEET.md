# Whale stream module — cheat sheet

Owns: `decision_bridge.py` (signal → decision, shared with `trading_loop`),
`whale_stream_handlers.py` (the trade/ticker websocket callback layer),
`index_stream_handlers.py` (the CF Benchmarks/Pyth index callback layer).
`services/kalshi_trade_ws.py` (the websocket transport — reader/worker
queue split, `dropped_messages` on overflow) and
`services/whalewatchers/` (whale-detection logic) are already clean and
stay where they are.

## Relevant Kalshi API docs

- `docs/kalshi/public-trades.md` — the `trade` channel, subscribed
  exchange-wide (no `market_tickers`) per the coverage-bottleneck fix
  documented in `docs/kalshi/CHEATSHEET.md`'s "How do you subscribe to the
  WHOLE exchange" entry.
- `docs/kalshi/market-ticker.md` — the `ticker` channel; `whale_stream_handlers.py`
  keeps only `yes_bid_dollars`/`yes_ask_dollars`, see `docs/kalshi/CHEATSHEET.md`'s
  "Which `ticker`-channel fields does the app keep" entry for what's
  dropped and why that was a real bug once.
- `docs/kalshi/market-and-event-lifecycle.md` — `market_lifecycle_v2`,
  consumed in `_process_stream_lifecycle` (only `close_date_updated` is
  wired to mutate state; `determined`/`settled` are deliberately
  observation-only — REST stays authoritative for real outcomes).
- `docs/kalshi/websocket-connection.md` — `user-fills`/`user-orders`/
  `market-positions` channels, consumed in `_process_stream_fill`/
  `_process_stream_position` (best-effort parsing, never verified against a
  real fill since real trading has never been enabled — see
  `services/position/CHEATSHEET.md`, this module produces the WS-sourced
  side of that reconciliation).

## Handoff — who calls this module, who it calls

- **Upstream:** `main.py`'s `lifespan()` wires `trade_stream.run(...)`/
  `index_stream.run(...)` to this module's handlers once, at startup — the
  handlers then run for the app's entire life on their own asyncio tasks,
  independent of `trading_loop`'s tick cadence.
- **Downstream — the real cross-boundary call, preserved deliberately:**
  `_process_stream_trade`/`_process_stream_ticker` call
  `strategy.check_exits(...)` directly (position management) and
  `decision_bridge._handle_signal`/`_handle_close_decision` (which call
  `strategy.evaluate()` → `services/paper_broker.py`) - whale-signal
  detection and position management both run off the stream path in real
  time, not gated by `trading_loop`'s 6s cadence. This is the one place
  in the app where a background stream task and the tick loop mutate the
  same `state["markets"]`/`state["latest_prices"]` concurrently with no
  lock — a known, not-yet-fixed finding (see the modularization plan).
- `decision_bridge.py` is deliberately its own module, not folded into
  `whale_stream_handlers.py` — `trading_loop` (which stays in `main.py`)
  calls the same four functions directly, so a shared home avoids
  `main.py` importing from the whale-stream module just for its own loop
  body.

## Where the two performance findings from this session live now

- **Message drops under exchange-wide load** (`dropped_messages` on queue
  overflow) — the queue/consumer split is in `services/kalshi_trade_ws.py`
  itself (transport layer, not moved this pass); `whale_stream_handlers.py`
  is where a future backpressure/parallelism fix would plug in on the
  consumer side.
- **Silent stream wedge** (self-reports `connected: true`, stops producing)
  — a future watchdog belongs directly next to `_handle_trade_stream_status`/
  `_handle_index_stream_status` in this module, since that's where
  connection status is already tracked.
