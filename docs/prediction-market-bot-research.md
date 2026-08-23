# Algorithmic Prediction Market Trading Bot — Research & Build Notes

Compiled from research into Kalshi, Polymarket, and general algo-trading architecture. Venue APIs and regulatory status move fast — re-verify anything time-sensitive against current docs before building against it.

---

## 1. Venue Research

### Kalshi
- REST API + a single unified WebSocket (one connection, subscribable channels: market ticker, order book deltas, public trades, and — authenticated — your own orders/fills).
- Auth: API key pair + per-request RSA-PSS signature (`KALSHI-ACCESS-KEY` / `-TIMESTAMP` / `-SIGNATURE` headers). No session tokens, no JWT refresh.
- All market data (events, markets, order books, candlesticks, trades) is public and unauthenticated. Trading and portfolio endpoints require signed requests.
- **No public trader identity.** You only see an anonymized trade tape and order book — not who's behind a trade. Any "whale watching" here is inherently proxy-based, not identity-based.
- Order book stream carries sequence numbers — a skipped `seq` means local book state is untrustworthy until you resync from a fresh REST snapshot.
- Rate limits are tiered; check `GET /account/api-limits` for your budget before designing polling/order cadence.
- Full demo/paper-trading environment with fake money — use it before going live.
- **No MCP server exists for Kalshi.** Integration has to be built custom (standard Python/TS `requests` + `websockets`, no dependency on an official SDK confirmed).

### Polymarket — two distinct products, easy to conflate
- **Polymarket US (operated by QCX LLC):** CFTC-regulated Designated Contract Market, launched Dec 3, 2025. Full KYC (government ID, SSN). Settles in **USD via an FCM — not crypto.** Functionally similar to Kalshi: anonymized tape, no public wallets. API is split three ways (Gamma = market metadata, CLOB = order book/trading, Data = historical) vs. Kalshi's single unified API.
- **Polymarket international (polymarket.com):** the original crypto/on-chain platform, settled in USDC on Polygon. **Currently geoblocked for US IP addresses** under a 2022 CFTC settlement ($1.4M penalty). VPN circumvention violates ToS and carries real legal exposure — not a foundation to build a real-money bot on. Polymarket filed with the CFTC in April 2026 to reopen this exchange to US users; as of this research, that's pending, not decided.
- **State-level fights are ongoing and unresolved:** Nevada, Massachusetts, Tennessee, Minnesota, and others have issued cease-and-desist orders, bans, or restraining orders against CFTC-regulated event-contract platforms (affecting both Kalshi and Polymarket). Treat "is this legal for me" as a live, state-dependent question, not settled fact.
- **Because the international platform is on-chain, wallet addresses are public.** If it ever becomes legally accessible, whale-watching stops being proxy-based and becomes literal — you can follow specific addresses, and Polymarket's own Data API already exposes wallet-level positions and activity. Polymarket US and Kalshi do not offer this; both are fiat/KYC with no public wallet data.
- No MCP server exists for either Polymarket product.

---

## 2. Whale-Watching Signal Design

**Key distinction to keep straight:** crypto-style whale-watching (public wallets, on-chain) only applies to the international/on-chain Polymarket — and that's not currently accessible to US persons. Kalshi and Polymarket US require proxy signals instead:

- Trade-size spikes relative to a given market's typical clip size (public trade feed)
- Order book wall behavior — large resting size appearing or being pulled (also watch for spoofing-like patterns, not just conviction)
- Directional order-flow imbalance — YES vs. NO volume skew over rolling short windows
- Cross-venue divergence — the same event often trades on both Kalshi and Polymarket; if one venue moves first, that's an informed-money signal

If/when on-chain access becomes viable: wallet-level historical win-rate scoring, following labeled "smart money" addresses, USDC flow tracking.

---

## 3. Architecture — Keep Strategy Swappable

The core risk of building this without a quant background isn't syntax — it's baking an unproven strategy assumption into plumbing that's expensive to rip out. Structure the app in layers that only talk to each other through fixed interfaces:

1. **Data ingestion** — venue-specific (Kalshi WS/REST, Polymarket Gamma/CLOB/Data)
2. **Signal/strategy** — whale-watching, sentiment, anything else; this is the layer most likely to be wrong and should be the cheapest to replace
3. **Risk & position sizing** — venue-agnostic; must be independent of strategy conviction (see §5)
4. **Execution** — venue-specific order placement, behind a common interface
5. **Backtest/paper-trade replay** — runs through the *same* strategy and risk code as live, not a separate script, so a good backtest result actually means something

If a strategy turns out to be garbage, you delete a module — you don't rewrite the app.

---

## 4. Quant Concepts to Program In

- **Overfitting** — a signal that looks great on a few months of history can be curve-fit noise. Requires out-of-sample and walk-forward validation, not a single backtest pass.
- **Backtest optimism / slippage** — don't assume fills at the observed price. Thin Kalshi/Polymarket liquidity means your own orders move the market; model realistic slippage.
- **Position sizing (Kelly criterion for binary event contracts):**
  For a contract priced at `P` (pays $1 if the event happens, $0 otherwise) with your model's probability estimate `p`:

  **f\* = (p − P) / (1 − P)**  — i.e., edge ÷ max loss per dollar

  Worked example: market prices YES at $0.35, model says true probability is 0.50 → edge = 0.15 → full Kelly ≈ 23.1% of bankroll, half-Kelly ≈ 11.5%, quarter-Kelly ≈ 5.8%.
  Full Kelly assumes your probability estimate is exactly right — since the signal is unproven, quarter-Kelly (or smaller, with a hard cap) is the sane default until the signal has a track record.
- **Binary payoff tails** — contracts settle at $0 or $1; variance is fatter than daily P&L makes it look.
- **Risk of ruin** — a positive-EV strategy can still cause ruin if sizing is wrong; sizing matters more than signal quality.

---

## 5. Engineering Safety Requirements — Things to Program

- [ ] WebSocket sequence-gap handling: on a skipped `seq`, stop trusting the local order book, reconnect, and rebuild from a fresh REST snapshot before resuming
- [ ] Rate-limit awareness: query account tier/limits programmatically; design polling and order cadence within budget
- [ ] Position reconciliation: periodically diff local event-sourced state against the venue's own portfolio/position endpoint to catch drift
- [ ] Paper-trade everything through the exact same code path as live before real money touches it
- [ ] **Independent kill switch / circuit breaker** — max daily loss, max position size, enforced outside the strategy/signal code, so a bug in signal generation can't blow through risk limits
- [ ] Logging and replay capability for every trade/signal/position change — needed for post-mortem and backtest refinement
- [ ] Advisory-only mode first: system proposes trades for human confirmation; only automate a strategy after it's earned a track record
- [ ] Contingency for sudden loss of venue access (regulatory/compliance action) — a defined "get flat immediately" path for open positions

---

## 6. Premortem — Failure Modes Worked Backward From "The Bot Failed Catastrophically"

1. **The whale signal was noise, not edge.** Looked great backtested on limited data, decayed once live because it was curve-fit rather than real. Warning sign: live performance quietly diverging from backtest for weeks before it's admitted.
2. **Sizing, not signal, caused the blowup.** Strategy was net profitable on paper; one fat-tailed settlement combined with an oversized "high-conviction" position wiped out months of gains. Root cause: risk sizing wasn't independent of strategy confidence.
3. **The backtest lied about fills.** Assumed liquidity that wasn't there; real slippage in thin markets made a "profitable" backtest fictional.
4. **Venue access vanished mid-position.** A state-level enforcement action or platform compliance move cut off trading with open positions live, with no contingency to flatten.

**Throughline:** none of these are caught by a smarter strategy — they're caught by an independent risk layer and structural skepticism toward your own backtest.

---

## 7. Open Decisions

- Kalshi vs. Polymarket US vs. (contingent on regulatory change) Polymarket international — the first two share an architecture; the third unlocks genuine wallet-level whale data but isn't currently a legal option for US persons.
- Check your own state's current stance on prediction-market legality before committing capital — this is unsettled and actively litigated as of this research.
