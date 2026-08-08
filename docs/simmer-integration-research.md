# Research: Simmer / agentskills.io / OpenClaw — is there real leverage here?

Direct request (2026-08-08): "do deep research on autotrading tool
simmer.markets which utilizes agentskills.io to create 'bots'... these
agents/bots also have access to real whale watcher data. i dont want to
rebuild the wheel, but i'd like to leverage this for testing strategies and
training my engine? possibly integrate these 'Agents' and 'Skills' simmer
uses into my own project?" — prompted by a specific example,
`diagnostikon/polymarket-whale-momentum-trader`. This is a research
write-up, not a build plan: no option below has been chosen yet. It exists
so the three real paths (and the "do nothing" baseline) can be compared
side by side before committing to one, same purpose
`docs/advisory-engine-plan.md` served before that build started.

## 1. What Simmer/agentskills.io/OpenClaw actually are — three separate layers

Easy to conflate; they're not the same product:

1. **agentskills.io** — an open spec (published by Anthropic, Dec 2025) for
   `SKILL.md` folders: YAML frontmatter (`name`, `description` minimum) +
   instructions + optional `scripts/`/`references/`/`assets/`, discovered
   via progressive disclosure (name+description at startup, full body only
   once a task matches). It's a packaging convention, not a runtime — it
   doesn't execute anything by itself.
2. **OpenClaw** — the agent runtime/CLI that actually *executes* skills.
   Sibling product to Claude Code, not the same tool. Skills install via its
   `clawhub` registry (`npm i -g clawhub`, `clawhub install <slug>`, or
   `openclaw skills install @user/skill-slug`), get scheduled via a
   `clawhub.json` `cron` field (e.g. `*/15 * * * *`) inside an "automaton,"
   and run with a Node.js >= 22.12 toolchain.
3. **Simmer** (`simmer.markets` / `api.simmer.markets`) — the actual
   product surface: a REST API + paper-trading venue unifying Polymarket,
   Kalshi, and its own LMSR paper market (`$SIM`) behind one interface, a
   `simmer-sdk` PyPI package, and a curated skill registry
   (`GET /api/sdk/skills`, no auth — ~12 official strategies: fast-loop
   momentum, weather, copytrading, AI-divergence, wallet-xray, signal-sniper,
   mert-sniper, etc.). This is the layer with anything resembling "whale
   data" or "paper trading."

None of the three requires the other two to be useful in isolation — that
matters for scoping, since "integrate Simmer" doesn't have to mean "run
OpenClaw."

## 2. The example skill, concretely — `diagnostikon/polymarket-whale-momentum-trader`

v1.0.6, MIT-0, install via `openclaw skills install
@diagnostikon/polymarket-whale-momentum-trader`. A Python script, cron-run
by OpenClaw, seven-step loop:

1. Fetch the top ~15 wallets from **predicting.top** — a free, public,
   no-auth leaderboard site ("Kolscan of Polymarket") that aggregates
   Polymarket trader P&L and ranks them.
2. Pull each wallet's recent trades from **Polymarket's own public Data
   API** — also free, no auth. This is possible at all *because* Polymarket
   positions settle on-chain (Polygon), so wallet-level activity is public
   by construction, the same way any blockchain explorer works.
3. Build a per-market YES/NO momentum tally across a 48h lookback window.
4. Filter to markets where ≥2 whales independently agree
   (`SIMMER_MIN_WHALES=2`).
5. Apply a conviction boost scaled by agreement strength (2 whales: +15%,
   3: +30%, 4+: +45–50% capped).
6. Gate on market-quality filters: spread ≤10% (`SIMMER_MAX_SPREAD`),
   slippage ≤15%, ≥5 days to resolution (`SIMMER_MIN_DAYS`), price
   thresholds (buy YES below 0.38 / buy NO above 0.62).
7. Execute via `simmer-sdk`'s `SimmerClient` against Simmer's `$SIM` paper
   venue by default; `--live` required for real money. Position sizing
   $5–$40 per trade, max 8 concurrent positions.

Dependencies: `simmer-sdk`, predicting.top's public endpoint, Polymarket's
public Data API. No other pip packages — the script itself uses stdlib
`urllib` for the non-Simmer calls.

## 3. The finding that actually decides everything else

**The "real whale watcher data" isn't something Simmer generates or owns —
it's public, on-chain Polymarket wallet data, reachable directly and for
free without Simmer at all.** Simmer's role in that skill is purely the
*execution* venue (the paper-trading fill simulation + optional live
Polymarket order placement), not the data source.

And it's **Polymarket-specific**, structurally, not just as this one
skill's choice — whale-watching here works because Polymarket settles
through public on-chain wallet addresses. **Kalshi has no equivalent.** It's
a CFTC-regulated exchange with its own account model, not a public
wallet-address ledger — there is no "top holders" or "wallet activity"
endpoint for Kalshi the way there is for Polymarket. That's exactly why this
project's own `services/whale_simulator.py` is simulated rather than real,
and why `ROADMAP.md`'s P2 item — "Add at least one more real named
whale-watcher provider (beyond the `generic_rest` default and the unused
`template_provider.py`) with actual, tested setup steps" — has stayed
unchecked. **Simmer does not close that gap for Kalshi.** Nothing currently
can, because Kalshi doesn't publish the underlying data. This isn't a
Simmer limitation to work around; it's a real fact about what data exists in
the world.

Checked this project's own extension point to confirm the gap is real, not
assumed: `services/whalewatchers/base.py` defines exactly the right
interface (`WhaleWatcherProvider.fetch_signals() -> list[WhaleSignal]`,
registered in `PROVIDERS` dict, selected via `WHALE_WATCHER_PROVIDER` env
var — `services/whalewatchers/template_provider.py` even has the "copy this
file" instructions already written). But `WhaleSignal.ticker`
(`services/whale_simulator.py:29`) is matched everywhere downstream against
Kalshi's own market list — a Polymarket whale signal doesn't slot into this
interface as a new provider without a Polymarket-market ↔ Kalshi-market
correspondence layer, which only exists for a handful of major recurring
events both platforms happen to list (elections, Fed rate decisions), not
systematically across the two catalogs.

## 4. Other things actually worth taking, independent of the whale-data question

- **`simmer backtest`** — a self-serve historical tape (Nov 2022 → ~May
  2026), auto-downloaded and cached under `~/.simmer/tapes/`, replayable via
  CLI or `simmer_sdk.backtest.run_backtest(...)`. Real, concrete tooling for
  validating a strategy *shape* offline before committing engineering time
  to it — though the tape is Simmer/Polymarket market data, not Kalshi, so
  it validates "does whale-consensus filtering help in principle," not
  Kalshi-specific parameter values.
- **The skill registry as an idea corpus.** The ~12 official skills plus
  community ones like this ClawHub example encode real, tested strategy
  patterns worth reading even without executing any of the code: the
  momentum-consensus conviction-boost formula, the flip-flop/slippage/spread
  guard combination, copytrading's size-weighted allocation +
  conflicting-position-skip + top-N concentration logic. Directly minable
  for `services/advisory_engine.py` heuristics.
- **The `SKILL.md` packaging convention itself** — low value here right
  now; this app has no reason to publish itself as an installable skill for
  someone else's agent runtime today.

## 5. Options

### Option A — Study only, no dependency, no code change

Read the registry and community skills for strategy ideas; hand-port
whatever's genuinely useful into `advisory_engine.py` in this project's own
idiom (pure functions, explainable, no black box — same reasoning that
picked rule-based over ML in `docs/advisory-engine-plan.md` §1). No new pip
package, no new venue, no new API key, stays 100% Kalshi.

- **Effort:** ongoing, ad hoc — not a discrete build.
- **Risk:** none. Nothing runs, nothing is trusted, nothing touches the live
  app.
- **Payoff:** ideas only, not real data. Doesn't move the needle on "real
  whale data" at all — it can't, per §3.

### Option B — `simmer-sdk` for offline backtesting only

Add `simmer-sdk` as a **dev-only** dependency (not imported by `main.py` or
anything in the live trading loop) purely to drive `simmer backtest`
against its historical tape, to sanity-check a strategy hypothesis (e.g.
"would a whale-consensus filter have actually helped, in a market with real
whale data, before I build an approximation of it against simulated Kalshi
whale data") before encoding it as an `advisory_engine.py` heuristic or a
`market_strategy.py`-style rule.

- **Effort:** small–medium. Self-contained; no changes to `main.py`,
  `config/settings.yaml`, or any safety-gated path.
- **Risk:** low. The dependency never runs inside the live app process —
  it's a research/validation tool, invoked manually or from a one-off
  script, same trust tier as a Jupyter notebook. Worth noting for the
  record: `simmer-sdk`'s own GitHub repo self-describes as **"Alpha
  Access-only. Not for Production Use"** — fine for offline, throwaway
  validation; not a reason to avoid Option B, but a real reason not to let
  it anywhere near the live trading loop (which Option B doesn't).
- **Payoff:** a validation methodology, not real Kalshi data — same caveat
  as §4's backtest note.

### Option C — Real whale data via a new Polymarket venue

The only option that actually produces *real* (not simulated) whale data
inside this app. Two ways to build it, meaningfully different in dependency
footprint:

**C1 — data only, own execution (preferred variant).** Build a new
`services/whalewatchers`-style module that calls predicting.top and
Polymarket's public Data API **directly** — both are free, public, and
require no Simmer account or `simmer-sdk` dependency at all, per §3. Pair it
with a second venue built the same way `services/market_strategy.py` +
`services/market_history.py` were just built this session: a
Polymarket-native `PaperBroker`/`RiskManager` pair with their own capital
pool and their own SQLite files (this project's existing `db_path`-per-instance
refactor already supports exactly this), trading Polymarket markets against
real Polymarket prices and real whale-wallet signals. Zero new third-party
runtime dependency — Simmer/OpenClaw contribute nothing to this path beyond
having been the thing that surfaced predicting.top + the strategy shape as
worth copying.

**C2 — Simmer as the execution venue too**, via `simmer-sdk` and a
registered agent (`POST /api/sdk/agents/register`), using Simmer's `$SIM`
LMSR paper venue instead of this project's own `PaperBroker`. Less code to
write, but: a live dependency on an alpha-stage third party
(`simmer-sdk`'s own "not for production use" disclaimer applies with more
force here, since this *would* be a live path), a different fill-simulation
model (LMSR synthetic fills vs. this app's own side-aware cost-basis math,
which has already had two real bugs found and fixed this project — see
`ROADMAP.md`'s Active Position Management section), and a second
account/API-key surface to manage. Not recommended over C1 unless there's a
specific reason to want Simmer's own execution/portfolio-aggregation layer
rather than this project's existing one.

Either sub-variant is a genuinely new scope, common to both:

- **New asset class** — Polymarket markets are structurally different from
  Kalshi's (different resolution mechanics, different fee/spread behavior,
  no `event_ticker`/series grouping the way Kalshi has).
  `trade_analytics.py`'s close-type classification, the dashboard's
  event-grouping (`eventGroupCardHTML`), and probably parts of
  `advisory_engine.py`'s config-fingerprinting would all need a
  Polymarket-aware variant or an explicit "which venue" dimension threaded
  through — not a drop-in.
- **New UI surface** — at minimum a debug endpoint (matching how
  `market_strategy.py` shipped backend-only first, per this session's
  earlier direct choice), likely a dashboard panel eventually given this
  app's "no real thing hidden behind an API-only surface" pattern so far.
- **Effort:** large. This is a new venue, not a new heuristic — closer in
  size to the whole `market_history.py`/`market_strategy.py` build than to
  a config addition.
- **Risk:** medium (C1) to medium-high (C2) — new external data
  dependencies (predicting.top's availability/rate limits/ToS weren't
  independently verified as part of this research; flagging that
  explicitly rather than assuming it's fine, same as this app's own
  "confirmed directly, not assumed" convention elsewhere), plus real scope
  growth into a second exchange this app wasn't originally built around.
- **Payoff:** the only option that's actually "real whale data," full stop
  — but for Polymarket, never for Kalshi.

## 6. What this doc does not decide

- Whether Option C is worth the scope increase at all — that's a call about
  whether this app stays Kalshi-only or becomes multi-venue, which is a
  bigger question than whale data specifically.
- Exact API/schema shape for C1's Polymarket provider or venue, if chosen —
  deferred to a follow-up plan doc once a direction is picked, same as
  `docs/advisory-engine-plan.md` came only after the rule-based-vs-ML
  question was settled.
- Nothing here touches real-money trading, `kalshi_account.trading_enabled`,
  or any existing safety gate — every option above is either research-only
  (A, B) or, if C is ever pursued, would ship disabled-by-default the same
  way `market_strategy.enabled` and `advisory.enabled` did.

## 7. Recommendation, for when a decision is wanted

Do **A now, unconditionally** — it's free and already partially informing
`advisory_engine.py`'s design vocabulary. Add **B** if/when a specific
strategy hypothesis needs offline validation before being worth building for
real. Treat **C** as a distinct, later, explicitly-scoped decision — not
because it's a bad idea, but because it's a different-shaped project
addition (new venue) than "improve the whale signal," and this app's own
precedent (the ML-scaffolding note in `docs/advisory-engine-plan.md` §9:
"let's not pursue that until the project is already finished") argues for
finishing the Kalshi-native buildout already in flight
(`market_history.py`/`market_strategy.py`, the advisory engine's data
threshold actually being reached) before taking on a second exchange.
