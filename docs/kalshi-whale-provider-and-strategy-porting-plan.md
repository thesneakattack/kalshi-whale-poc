# Plan: a real Kalshi whale-watcher provider, and a strategy-porting pattern

Direct request (2026-08-08), across two follow-ups in the same thread:

1. "Is there a website we can scrape kalshi leaderboard data for whale
   watching, or an api i can access that gives it" — answered by research,
   not assumption: Kalshi has no trader-identity leaderboard (trades are
   anonymous member-to-member), but this app **already has**, right now, for
   free, real-time, no-auth access to every real trade on the exchange —
   `services/kalshi_client.py`'s `get_trades()`, already called every tick
   by `_fetch_trade_tape()` (`main.py:268`). Every third-party "Kalshi whale
   tracker" surveyed (Polywhaler, Oddpool, CrossOdds, Rivo Markets,
   WhaleScanr) turns out to be a size threshold applied to this exact same
   public feed, not a distinct data source. Part 1 below closes
   `ROADMAP.md`'s still-open P2 item ("add at least one more real named
   whale-watcher provider") using data this app already fetches.
2. Clarifying an earlier, broader question about "integrating Simmer's
   Agents and Skills": **not** ClawHub/OpenClaw compatibility, **not**
   running Simmer's `simmer-sdk` or any third-party skill's actual code —
   "I want to be able to use their skills in my project to test different
   strategies, or use an existing agent and import it into my project."
   Resolved as: port the *algorithms* published skills implement into this
   project's own idiom, run against this project's own `PaperBroker`, the
   same way `services/market_strategy.py` already runs as a second,
   independent strategy next to `FollowTheWhaleStrategy`. Part 2 below.

Sequenced deliberately, not two independent efforts: Part 2's concrete
worked example (porting `diagnostikon/polymarket-whale-momentum-trader`'s
whale-consensus idea) is only meaningful once Part 1 gives it real whale
prints to work with — building it against the simulator would just be
re-testing what `FollowTheWhaleStrategy` already covers.

---

## Part 1 — Real Kalshi whale-watcher provider

### 1.1 What's already true, verified against this codebase directly

- `services/kalshi_client.py:1-8`'s own docstring: "public (unauthenticated)
  market-data endpoints... No API key is required for any of this."
  `get_trades(ticker, limit)` (`kalshi_client.py:222`) is part of that
  surface — the real, exchange-wide trade tape.
- `main.py:551-558`: `_fetch_trade_tape(client, markets)` already runs every
  trading-loop tick, before the whale-provider call, and its result is
  already sitting in `state["trade_tape"]` by the time
  `whale_provider.fetch_signals()` runs at `main.py:623`. Building the new
  provider to reuse this (§1.3) costs zero extra API calls — same
  "already-fetched, don't fetch again" discipline `market_history.py` used
  this session.
- Confidence scoring has a proven shape to reuse, not invent:
  `whale_simulator._score_confidence()` (`whale_simulator.py:146`) already
  implements a four-factor composite (size relative to the market's own
  `volume_24h_fp`, price unusualness, proximity to close, market context),
  explicitly modeled on "Polywhaler's stated 'Insider Score' shape" per
  `ROADMAP.md`. Same formula, real inputs instead of synthetic ones.

### 1.2 What Kalshi genuinely does not give us — state honestly, not oversold

No trader identity, no public leaderboard, no wallet-style position
history — confirmed independently across multiple sources during research,
not assumed. This means the new provider is **strictly size-based whale
detection** ("a real $2,500+ trade just printed on this market"), never
reputation-based ("...and it was placed by a trader with a $980K track
record") — that second kind of signal is what Polymarket's on-chain wallet
data uniquely enables (see `docs/simmer-integration-research.md` §3) and
structurally cannot exist for Kalshi via any legitimate channel. Every real
third-party Kalshi whale tracker surveyed has the same limitation for the
same reason.

### 1.3 New provider — `services/whalewatchers/kalshi_trade_tape.py`

Follows `template_provider.py`'s existing "copy this file" pattern exactly:

```python
class KalshiTradeTapeProvider(WhaleWatcherProvider):
    name = "kalshi_trade_tape"

    def __init__(self):
        self.min_notional_usd = float(
            os.getenv("KALSHI_TRADE_TAPE_MIN_NOTIONAL_USD", "2500")
        )

    @property
    def enabled(self) -> bool:
        return True  # needs no external credentials — reads this app's own already-fetched data

    async def fetch_signals(
        self, since_ts: float | None = None, market_context: dict | None = None,
    ) -> list[WhaleSignal]:
        ...
```

**One small interface change, applied uniformly, not just to this
provider:** `WhaleWatcherProvider.fetch_signals()` gains a new optional
`market_context: dict | None = None` param
(`{"markets": [...], "trade_tape": [...]}`) so this provider can read the
tick's already-fetched data instead of independently re-fetching it — the
first provider that actually needs it, since `generic_rest` and
`template_provider` both fetch from an external source with no dependency
on this app's own tick state. Add the param to all three
(`base.py`/`generic_rest.py`/`template_provider.py`) for interface
consistency even though only the new one reads it — same reasoning
`close_if_settled()` got extracted to a shared function rather than
duplicated: one place, one shape, everyone conforms. `main.py:623` becomes:

```python
new_signals = await whale_provider.fetch_signals(
    market_context={"markets": markets, "trade_tape": trade_tape},
)
```

**Classification — the side-aware dollar-math lesson, applied deliberately
here, not re-learned the hard way a third time.** This app has already
shipped and fixed the exact bug class once
(`ROADMAP.md`'s Active Position Management section: `open_position` charged
`size * price` unconditionally, when a **no**-side position's real cost is
`size * (1 - price)`). A Kalshi trade object's real notional dollar size is
side-aware the same way:

```python
def _notional_usd(trade: dict) -> float:
    count = trade.get("count") or 0
    taker_side = (trade.get("taker_side") or "").lower()
    price_cents = trade.get("yes_price") if taker_side == "yes" else trade.get("no_price")
    return count * (float(price_cents or 0) / 100.0)
```

(Exact field names — `count`/`taker_side`/`yes_price`/`no_price`/
`created_time` — to be confirmed against one real `get_trades()` response at
implementation time, same "confirmed directly, not assumed" discipline as
every other real-schema fact in this codebase; not hard-coded here from
memory alone.)

A trade becomes a `WhaleSignal` when `_notional_usd(trade) >=
self.min_notional_usd`. Confidence: reuse `whale_simulator`'s four-factor
formula (extract `_score_confidence`'s math to a shared, importable
function if it isn't already reusable as one — check at build time rather
than duplicating the weights) against the real trade's real size, real
price, real time-to-close, and the real market's `volume_24h_fp` from
`market_context["markets"]`.

**Threshold default:** researched real-world conventions during this
session — Oddpool floors at $1,000 (adjustable up), Polywhaler/CrossOdds use
$5-10k. Land on **$2,500** as a sensible default (documented rationale, not
arbitrary), exposed as `whale_watcher_kalshi.min_notional_usd` in
`config/settings.yaml`, tunable from the Config tab like every other
threshold in this app.

**Registration** — `services/whalewatchers/__init__.py`'s `PROVIDERS` dict
gains `"kalshi_trade_tape": KalshiTradeTapeProvider`. Selecting it is opt-in
via `WHALE_WATCHER_PROVIDER=kalshi_trade_tape` in `.env`, same mechanism
every other provider already uses — **zero behavior change for anyone who
doesn't set it**, matching this app's every other "ships disabled/opt-in by
default" precedent.

### 1.4 A real UI implication worth flagging now, not discovering later

The Config tab's Whale Signal section currently carries a blanket 🧪 "sim
data" badge (shipped this session, `ROADMAP.md`'s P1 Config-tab-redesign
item) because every whale-dependent field is calibrated against the
simulator. Once a real provider exists and can be selected, that badge
becomes conditionally wrong — it should reflect **which provider is
currently active** (`state["whale_source"]`, already set at `main.py:624`
whenever a real provider is enabled), not hardcode "simulated." Concretely:
badge reads "🧪 sim data" only when the simulator is the active fallback,
and something like "📡 real (kalshi_trade_tape)" when a real provider is
selected. Small, but a genuine follow-up to already-shipped work, not an
afterthought to skip.

### 1.5 Tests

- `tests/test_whalewatchers_kalshi_trade_tape.py` (new): side-aware notional
  math for both yes-taker and no-taker trades; threshold boundary (exactly
  at `min_notional_usd` — pick and document inclusive vs. exclusive);
  confidence scoring against known fixtures, same shape as
  `tests/test_whale_simulator.py`'s existing `_score_confidence` coverage if
  it exists (check before duplicating); malformed/missing-field trades
  skipped, not crashing the loop (matching `generic_rest`'s established
  try/except-continue pattern); `enabled` always `True` (no credentials
  needed, unlike `generic_rest`).
- Extend `tests/test_trading_gate.py` only if provider selection needs any
  startup-time coverage beyond what the existing provider-swap tests
  already establish.

### 1.6 Build order

1. Provider + tests, registered but not selected by default.
2. Verify live against the real running app: set
   `WHALE_WATCHER_PROVIDER=kalshi_trade_tape`, watch for a real signal to
   appear (unlike the simulator's timer-driven prints, this fires only when
   a real trade actually clears the threshold — may take genuine market
   observation, not an instant `curl` check).
3. §1.4's badge follow-up in the Config tab.

---

## Part 2 — A config-driven strategy framework ("many strategies, config not code")

### 2.0 Revised twice by direct correction — stated plainly so it doesn't drift back

First draft of this section planned to hand-port one specific skill
(`diagnostikon/polymarket-whale-momentum-trader`) into its own bespoke
Python file. Corrected: (1) not just one skill — the goal is testing
*various* strategies against each other, not one custom module; (2) "drop-in
compatible... so you don't have to transform things in existing project
code" — i.e. adding a new strategy shouldn't mean hand-writing a new module
each time. A literal third-party-code-execution path (a `simmer-sdk`-alike
compatibility shim so real skill scripts run with minimal edits) was
considered next and explicitly rejected, for reasons worth keeping on
record: it means running unreviewed third-party code (which even OpenClaw's
own docs flag as needing sandboxing), it's a large, open-ended build against
an SDK with dozens of methods different skills use inconsistently, and —
the decisive one — **it wouldn't even solve the original problem**, since
most of the actually-interesting skills (whale-tracking, copytrading) need
Polymarket wallet data that structurally doesn't exist for Kalshi (§1.2).
Shimming the API doesn't conjure data that isn't there.

Landed on instead: a small, reusable, **config-driven framework**, built
from pieces this app already has, informally duplicated across its existing
strategies rather than shared. Adding a new strategy becomes a config block
using existing building blocks — genuinely "drop-in" for the strategies this
app's real data can actually support — with a new one-function building
block only needed when a strategy idea genuinely requires a new kind of
signal, not a whole new module.

### 2.1 The insight: this app already has most of the pieces, just not factored out

`FollowTheWhaleStrategy` and `MarketNativeStrategy` already independently
reimplement versions of the same four things: a signal to react to, gates
that decide whether a market qualifies, a sizing formula, and shared exit
handling (`close_if_settled()`, already properly extracted — the model for
what §2.2 does to the other three). The framework isn't new infrastructure
so much as finishing that extraction and adding one config layer on top.

### 2.2 Four building blocks, extracted once, shared everywhere

**Signal sources** — `services/signal_sources.py`. Each returns the same
shape (`{ticker, side, strength: 0-1, timestamp}`), regardless of where it
came from:

- `whale` — wraps `whale_provider.fetch_signals()` (Part 1) plus
  `signal_log`'s persisted history, since consensus-style aggregation (§2.2
  below) needs to look backward across ticks, not just this tick's signal.
- `momentum` — wraps `market_history.momentum(ticker, lookback_sec)`
  (already built this session), the same input `market_strategy.py`
  already turns into a confidence score, now exposed as a swappable source
  instead of being hard-coded inside one strategy file.
- **Explicit non-goal:** no wallet/copytrading source. That data doesn't
  exist for Kalshi (§1.2) — not building a source that would silently
  return nothing or fake it.

**Aggregation rules** — `services/signal_aggregation.py`:

- `single` — act on any one signal clearing a confidence threshold (today's
  `FollowTheWhaleStrategy` behavior, generalized to work with any source,
  not just whale).
- `consensus_n` — require ≥N independent signals agreeing on the same
  ticker+side within a lookback window, queried from `signal_log`'s
  existing persisted history. This is the generalized version of the
  whale-momentum-trader idea from the first draft — now usable with *any*
  signal source (momentum consensus, not just whale consensus, falls out
  for free), not a one-off.

**Market-quality gates** — `services/strategy_gates.py`:
`passes_market_filters(market, gate_cfg, live_status, now) -> str | None`
(a skip-reason or `None`). Extracts what `market_strategy.py` and
`strategy_engine.py` each currently check ad hoc/independently — spread,
price band, min volume, days-to-close, live-only, excluded-series — into
one shared, named function. Same "one place this logic lives" reasoning
that `close_if_settled()` already established for exits; existing
strategies can adopt it too as a non-breaking follow-up, not just new ones.

**Sizing curves** — `services/strategy_sizing.py`:

- `fixed_pct` — flat `max_position_pct` of bankroll.
- `confidence_scaled` — `max_position_pct * signal_strength`.
- `conviction_boost(base_pct, boost_per_signal_pct, boost_cap_pct)` — the
  specific curve from `diagnostikon`'s skill (2 signals: +15%, 3: +30%,
  4+: +45-50% capped), now a named, generically-parameterized function
  instead of a bespoke one-off — credited in the module docstring as the
  source of the curve shape, same norm this app already applies to
  Polywhaler/WhaleScanr's filtering conventions.

### 2.3 The engine — `services/configurable_strategy.py`

```python
class ConfigurableStrategy:
    """Wires one named custom_strategies.* config block into a running
    strategy: a signal source, an aggregation rule, a gate set, and a
    sizing curve, executed against its own PaperBroker/RiskManager pair.
    Exits reuse close_if_settled() from strategy_engine.py, same as every
    other strategy — never reimplemented here."""

    def __init__(self, name: str, broker: PaperBroker, risk: RiskManager, spec: dict):
        self.name = name
        self.broker, self.risk = broker, risk
        self.signal_source = signal_sources.get(spec["signal_source"])
        self.aggregate = signal_aggregation.get(spec["aggregation"]["mode"])
        self.size = strategy_sizing.get(spec["sizing"]["mode"])
        self.spec = spec

    def evaluate_all(self, markets, tick_now, cfg, market_results): ...
    def check_exits(self, markets_by_ticker, tick_now, cfg, market_results): ...
```

### 2.4 Config schema — `config/settings.yaml`'s new `custom_strategies` section

```yaml
custom_strategies:
  whale_consensus:                 # first concrete instance, see §2.6
    enabled: false
    starting_bankroll: 10000
    signal_source: whale
    aggregation:
      mode: consensus_n
      min_signals: 2
      lookback_sec: 172800         # 48h, the skill's own default
    sizing:
      mode: conviction_boost
      base_pct: 0.02
      boost_per_signal_pct: 0.15
      boost_cap_pct: 0.50
    gates:
      max_spread: 0.10
      min_days_to_close: 5
      min_price: 0.05
      max_price: 0.95
    max_position_pct: 0.05
    cooldown_sec: 300
```

A second entry (say, a plain single-signal momentum variant with different
thresholds) is another top-level key under `custom_strategies`, reusing the
same `signal_source`/`aggregation`/`sizing`/`gates` vocabulary — no new
Python required unless it needs a signal source or sizing curve §2.2
doesn't already have.

### 2.5 `main.py` wiring

A module-level registry (`_custom_strategies: dict[str, ConfigurableStrategy]`,
alongside the existing `strategy`/`market_strategy` instances) is
lazily populated: on each tick, for every enabled `custom_strategies.*`
entry not yet instantiated, create its `PaperBroker`/`RiskManager` pair
(`data/custom_{name}_broker.db`, same `db_path`-per-instance mechanism
`market_strategy` already uses) and a `ConfigurableStrategy` wrapping it,
cached for subsequent ticks; a disabled or removed entry's instance is left
alone (still holds its capital/history) rather than torn down, matching how
`market_strategy.enabled=false` today just stops the loop from calling it,
not deletes its data. `evaluate_all()`/`check_exits()` called once per
enabled entry per tick, same as the two built-in strategies, results not
merged into `state["decision_feed"]` (same "own debug surface, not the
shared feed" choice `market_strategy.py` made).

### 2.6 First concrete instance — `whale_consensus`, proving the framework

The §2.4 config block above *is* the whale-consensus strategy — no bespoke
Python file. Only meaningful once Part 1 ships (needs real whale prints to
reach consensus on), which is why it's still sequenced after Part 1. Ships
with a debug endpoint (`GET /api/custom-strategies/{name}/state`, generic
across every entry rather than one route per strategy) — backend-only
first, no dashboard panel, same choice already made for `market_strategy.py`.

### 2.7 Explicit non-goals

- No OpenClaw, no ClawHub, no `simmer-sdk`, no SKILL.md, no execution of any
  third party's actual code — a rule-based engine of our own, not a runtime
  for someone else's.
- No promise every published skill's idea maps onto these building blocks.
  Wallet/copytrading-based ones don't apply to Kalshi at all (§1.2, not a
  framework gap — a real data gap). External-API-based ones (weather,
  AI-divergence, RSS signal-sniper) would each need a new `signal_sources`
  function written by hand — real work, but scoped to one small,
  independently-testable function, not a new strategy class.
- No comparison dashboard yet — each entry already has its own isolated
  broker, so comparing two is reading two debug endpoints side by side.
  Revisit only if that gets genuinely cumbersome with several running at
  once, matching `docs/advisory-engine-plan.md`'s "no new schema unless
  truly needed" philosophy.

### 2.8 Tests

- `tests/test_signal_sources.py`, `tests/test_signal_aggregation.py`,
  `tests/test_strategy_gates.py`, `tests/test_strategy_sizing.py` — each
  building block is a small pure function, individually unit-tested (easier
  to cover thoroughly than a monolithic strategy class would be).
- `tests/test_configurable_strategy.py`: the engine wiring, against fixture
  specs covering each aggregation × sizing combination.
- One integration test: seed `signal_log` with synthetic multi-signal
  agreement, confirm a `consensus_n` + `conviction_boost` config actually
  opens a correctly-boosted-size position, and that a single-signal case
  under `min_signals` does not.

---

## Build order (both parts)

1. **Part 1** — real whale-watcher provider. Self-contained, ships first,
   immediately useful on its own (real signal history/track-record data for
   anyone who opts in, independent of Part 2 ever happening).
2. **Part 2 §2.2-2.3** — the four building blocks + the engine. Useful and
   testable before any specific `custom_strategies` entry exists.
3. **Part 2 §2.6** — `whale_consensus` as the first config entry, proving
   the framework end to end against Part 1's real data.
4. Further entries over time, as config — new `signal_sources` functions
   only when an idea genuinely needs a kind of data this app doesn't
   already have a source for.
