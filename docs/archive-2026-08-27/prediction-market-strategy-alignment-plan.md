# Plan: aligning this app's algorithms with real prediction-market research, and a new market-analyst agent

Direct request (2026-08-09), across several messages in one thread: "do deep
research on prediction markets... be a single source of truth," then "once
youre done researching i want you to compare these strategies and logic and
make adjustments to all the relevant algorithms. i want to add an 'agent'
that acts as a 'doctorate level prediction market trader'," then "build out
a comprehensive planning doc," then "apply this research to confidence,
whale watching, position management, etc while also informing my nascent
analysis engine and scaffolded AI agent."

This is a **plan, not yet implemented** — same discipline as
`docs/advisory-engine-plan.md` before the advisory engine got built: lay out
the reasoning and the concrete design first, since this touches confidence
scoring, entry/exit logic, and a brand-new agent that will eventually sit
upstream of real trading decisions. Full research backing every claim below
lives in `docs/prediction-markets-research-reference.md` — this doc only
restates a finding when it's needed to justify a specific proposed change.

**"Nascent analysis engine"** = `services/advisory_engine.py` (built
2026-08-08, rule-based config-tuning recommendations, currently `enabled:
true` but gated on 30 resolved trades per config variant). **"Scaffolded AI
agent"** = `services/ml_feed.py`'s `build_context_snapshot()` — built the
same day, explicitly as inert scaffolding for "a machine-learning agent...
work WITH these things, not as a replacement," deliberately deferred until
"the project is already finished." The user is now asking to un-defer it —
see Part 3.

---

## Part 1 — What the research validates (don't fix what isn't broken)

Worth stating first, since a research-driven review can read as universally
critical if it only lists problems: several of this app's existing design
choices are independently supported by the evidence, not just
coincidentally similar to it.

- **Multi-factor composite confidence over a single size threshold** —
  `composite_confidence_breakdown`'s multi-factor blend (depth, unusualness,
  proximity, context, agreement — and, per §2.1's implementation, now
  cluster) is the right shape. Easley & O'Hara (1987)
  theoretically grounds "size correlates with informedness"; but the
  strongest *direct* empirical evidence of real informed trading in a
  prediction market (Mitts & Ofir 2026) only found a real signal by
  combining size with profitability history, timing, and directional
  concentration — a naive single-factor size threshold would have missed
  it. This app already doesn't do naive size-only scoring.
- **Rule-based, not ML, for both the advisory engine and confidence
  calibration** — both modules' own docstrings already cite "too little
  resolved-signal data to fit anything trustworthy" as the reason. The
  research independently reinforces this from a different angle: Gomez Cram
  et al.'s finding that real prediction-market accuracy concentrates in
  ~3% of persistently-skilled accounts, not a broad, learnable population
  pattern, is exactly the kind of low-signal, needle-in-haystack structure
  classical ML struggles with at small sample sizes. Keep this decision.
- **`market_strategy`'s price-band filter (`min_price: 0.15, max_price:
  0.85`)** already excludes the most extreme favorite-longshot territory —
  a real, if partial, FLB mitigation that predates this research and turns
  out to be directionally correct.
- **The `agreement_factor` concept** (do recent real prints on this same
  market agree with this one) is a genuine, if narrow, instance of exactly
  the kind of multi-signal aggregation the strongest informed-trading
  evidence (Mitts & Ofir) shows actually works, and Hanson's aggregation
  theory (Part 1 of the reference doc) supports in general.
- **Shadow-mode-before-live and the whole layered safety architecture**
  (see the earlier "Defense in Depth" guide) is reinforced, not just
  validated: the research shows real, sometimes large and sustained,
  distortions from herding and manipulation on modern platforms — exactly
  the class of risk shadow mode exists to surface before it's real money.

---

## Part 2 — What the research contradicts or complicates, and concrete proposed adjustments

### 2.1 The core whale-detection premise has a real, documented blind spot: stealth trading

**Finding:** Barclay & Warner (1993, JFE) — the strongest empirical result
in this whole research pass on the whale-signal question — found most
cumulative price change in real markets traces to *medium*-size trades, not
the largest blocks, because sophisticated informed traders deliberately
split orders to avoid the price impact and detection a single giant print
would trigger. `services/whalewatchers/kalshi_trade_tape.py` currently
classifies a whale purely by **one trade's** notional size crossing
`min_notional_usd`. The single most informed flow this app is trying to
catch is, per this research, disproportionately likely to be the flow that
*doesn't* look like a single whale print at all.

**What's already built but unused for this:** `signal_log.find_clusters()`
(the "Possible Accumulation" feature) already detects exactly this pattern
— same-ticker/same-side signals within a time window and a size-similarity
ratio, grouped as probable-same-actor clusters. Today this is a **pure UI
display feature** on the Whale Watch tab — it feeds nothing back into
confidence scoring or `strategy_engine.evaluate()`'s decision at all.

**Proposed change:** compute a cluster-membership signal at the point a
trade is classified (`kalshi_trade_tape.fetch_signals()`) and feed it into
`composite_confidence_breakdown` as a real input — either as a boost to the
existing `agreement_factor` (a trade that's part of an active cluster is a
stronger agreement signal than an isolated same-side print) or as a genuine
6th factor. This directly closes the gap between what the research says is
actually informative (sustained, clustered flow) and what the app currently
scores (isolated print size).

**Flagging a separate, concrete finding while looking at this file:** the
live `config/settings.yaml` currently has `whale_watcher_kalshi.min_notional_usd:
1` — not the documented `_DEFAULT_MIN_NOTIONAL_USD = 2500.0`. At $1, this
provider classifies essentially *every* real trade on the exchange as a
"whale" print, which is a fundamentally different (and much noisier)
signal than what the whole confidence-scoring apparatus above was designed
around. `git blame` traces this to the 2026-08-08 watchlist/series-grouping
commit — it's very likely a leftover test value, not an intentional
production setting, but that's worth confirming with you directly rather
than silently "fixing" a live config value.

### 2.2 No fee-awareness anywhere in P&L modeling

**Finding:** the real, live-verified taker fee formula is
`ceil_4dp(0.07 × contracts × price × (1−price))` — up to $0.0175/contract
(~3.5¢/contract round-trip at the worst-case 50¢ price). `PaperBroker.open_position`/
`close_position` deduct **zero** fees anywhere. This means every paper-mode
P&L figure — the History tab's win rate, average realized P&L, the
`sentiment_reversal`/`momentum_reversal` insight hints, and (critically)
**shadow mode's own "would I trust this with real money" read** — is
systematically more optimistic than what real trading would actually
produce. This matters most exactly where it's most load-bearing: shadow
mode is the one stage of the paper→shadow→live progression whose entire
job is answering "would this have been good with real stakes," and it's
currently blind to a real, confirmed cost of those real stakes.

**Proposed change:** add a fee-estimation function (mirroring the verified
formula) and (a) deduct it in `close_position`'s realized-P&L math so
paper/shadow figures reflect it, (b) surface it as its own line in the
Trading History summary (not just silently baked into a lower P&L number —
this app's own convention throughout is to show the mechanism, not just
the result), and (c) consider a minimum-edge-after-fees check before entry,
since a signal that clears `entry_threshold` on raw confidence could still
be a near-zero- or negative-expected-value trade once the fee is priced in,
especially for smaller positions where the fee is a larger fraction of
stake.

### 2.3 Favorite-longshot bias, confirmed on Kalshi, worse for takers — and this app trades as a taker

**Finding:** Bürgi/Deng/Whelan's real Kalshi data shows takers lose ~32% on
average buying longshots vs. ~10% for makers. `kalshi_account_client.py`'s
real order path defaults `time_in_force` to `"immediate_or_cancel"` — a
taker order, confirmed by direct code read. Once real trading is ever
enabled, this app is on the expensive side of the single most robust,
Kalshi-specific bias documented in this research, and nothing in
`strategy_engine.py` currently distinguishes "this signal sits at a
longshot price" from "this signal sits near a coin-flip price" when
applying `entry_threshold` — the cutoff is a single flat number (0.6)
regardless of where on the price curve the signal falls.

**Proposed change, two independent levers:**
- **Price-aware entry bar**: require a *higher* confidence for signals
  whose price sits in longshot territory (e.g., below ~15¢ or above ~85¢ —
  reusing `market_strategy`'s existing band as a reference point) rather
  than one flat threshold — cheap, config-driven, no new data needed.
- **Maker-style real order support** (larger change, real-trading-path
  only, no paper-mode urgency): today's IOC-only order path can't express
  "post a limit order and wait," which is the empirically cheaper side of
  this specific bias on Kalshi. Worth a P2-tier follow-up once real trading
  is closer, not blocking anything now.

### 2.4 No manipulation/trend-consistency awareness

**Finding:** the classic "manipulation self-corrects quickly" result (IEM
era) does not obviously generalize to larger, less-monitored markets — the
2024 Polymarket "French whale" case was a sustained, weeks-long, double-
digit-percentage-point deviation. Nothing in `composite_confidence_breakdown`
distinguishes a large print that's consistent with where a market's price
has already been drifting from one that's fighting an established trend
trying to reverse it — mechanically different situations with different
manipulation risk, currently scored identically by every existing factor.

**Proposed change:** a trend-consistency factor — compare a signal's
direction against `market_history.momentum()`'s already-computed price
trend for that ticker (this data already exists for the market-native
strategy, zero new fetch cost to reuse it here) and treat a print *against*
an established trend as needing more corroboration (e.g., require cluster
membership or a stronger agreement_factor) than one confirming an existing
trend. This is explicitly a caution factor, not a block — Hanson's own
theoretical result (a manipulator's presence isn't unambiguously
accuracy-destroying) argues against overreacting to this signal alone.

### 2.5 Accuracy measurement: hit-rate vs. calibration

**Finding:** the Clinton & Huang vs. Kalshi dispute (reference doc §1.4) is
a real, substantive, unresolved methodological question — is raw hit-rate
(did the side priced above 50% win) or calibration (do 70%-confidence
signals resolve ~70% of the time, across many signals) the meaningful
accuracy metric? `signal_log.stats()`/`series_stats()` currently compute
**only** binary hit-rate. `confidence_calibration.py` already does
calibration-style tertile bucketing, but only per individual *factor*
(does `depth_factor`'s high third beat its low third), never for the
**overall composite confidence score** against actual outcomes.

**Proposed change:** a small, natural extension of a module that already
exists — bucket resolved signals by their final `composite_confidence`
score (not just individual factors) and report whether, e.g., the
60–70%-confidence bucket actually won ~60–70% of the time. This is a more
meaningful readout than the flat win-rate percentage currently shown on the
Whale Track Record panel, and it's the same underlying data
(`signal_log.resolved_signals_with_factors()`) `confidence_calibration.py`
already reads — no new persistence needed.

### 2.6 Long-dated markets — lower priority, worth flagging

**Finding:** Manski/Wolfers-Zitzewitz plus a 2026 preprint (arXiv
2602.21091) on the "long-horizon problem" — absent yield-bearing
collateral, a contract resolving far in the future should trade slightly
*below* true probability purely from the time-value-of-money cost of
capital tied up, independent of any real mispricing. `market_strategy`
filters out very *short*-dated markets (`min_seconds_to_close: 3600`) but
nothing filters or adjusts for very long-dated ones on either strategy.

**Proposed change:** lowest priority in this doc — a config field
acknowledging the effect (e.g., a small confidence discount scaling with
time-to-resolution) rather than treating "price divergence" as meaning the
same thing at 2 days vs. 6 months out. Not urgent since this app's real
watchlist skews heavily toward near-term/live markets already (the
`market_catalog` near-term horizon, 1 week past–3 weeks future).

---

## Part 3 — The new agent: a "doctorate-level prediction market trader"

### 3.1 Framing

This is not a new idea introduced from scratch — it's the thing
`services/ml_feed.py` was scaffolded for on 2026-08-08 and explicitly told
to wait on ("let's not pursue that until the project is already finished").
The user is now asking to build it. `build_context_snapshot()` already
assembles exactly the bundle a sophisticated analyst would want: live
config, portfolio state, market snapshot, trade history, whale track
record, and the existing advisory engine's own recommendations — "work
WITH, not replace" the rule-based engine, per that module's own docstring.

### 3.2 The design fork this doc needs your call on

**Option A — classical trained ML model. Not recommended, same reasoning
already on record twice in this codebase.** Both `advisory_engine.py` and
`confidence_calibration.py` independently concluded there's nowhere near
enough resolved real-signal data to fit anything trustworthy (calibration's
own gate gets checked against `min_resolved_signals: 50`; this app has had
stretches with single-digit resolved real signals). The research
reinforces this from another angle — Gomez Cram et al.'s "informed minority,
not crowd wisdom" finding describes a needle-in-haystack pattern that's
exactly the kind of thing you can't reliably fit with a small sample.
Nothing about "doctorate level" requires huge training data, though — a
real domain expert doesn't need thousands of labeled examples to reason
about one specific market, which is why:

**Option B — an LLM-based reasoning agent. Recommended.** Instead of
learning statistical patterns from this app's own (still-thin) trade
history, an LLM-based agent reasons about *each individual candidate
market* using general knowledge and the specific context this app already
has, the way an actual domain expert would form a view without needing a
personal track record on that exact market first. Concretely: for a
candidate market, feed it `ml_feed.build_context_snapshot()`'s bundle plus
that market's own title/rules/category/current price/time-to-close, and
ask for a structured judgment — the agent's own estimate of the true
probability, a confidence level, and its reasoning — which then gets
compared against the current market price to flag genuine, *reasoned*
divergence, distinct from this app's existing statistical/whale-flow-based
divergence signal. Two fundamentally different kinds of edge-detection,
side by side, not one replacing the other — matching the "work WITH, not
replace" framing already on record.

**Option C — a further-enhanced rule-based/statistical layer.** This is
really just "extend `confidence_calibration.py` further" (e.g., with the
trend-consistency and cluster-aware factors from Part 2) — valuable, but
it's what Part 2 already proposes, and it doesn't meet the "doctorate
level" bar the user asked for, which implies qualitative reasoning (reading
a market's actual rules text, weighing real-world context) current
rule-based code structurally can't do.

**This doc recommends B, informed by A and C where they overlap** — the
new agent's prompt should explicitly include this app's own accumulated
evidence (win rates by close type, whale track record, series stats, the
existing advisory engine's own recommendations) so its qualitative
judgment is grounded in this app's actual track record, not just general
world knowledge. That's precisely what `ml_feed.py`'s bundle already
assembles — Option B is the natural consumer of scaffolding that already
exists, not a new data-plumbing project.

### 3.3 Proposed architecture

- **New `services/market_analyst_agent.py`** (naming open) — reads
  `ml_feed.build_context_snapshot()`'s bundle plus a candidate market's real
  data, calls an LLM with a structured prompt, requires a **structured JSON
  response** (probability estimate, confidence, reasoning, cited factors) —
  not free text — so its output can be logged, compared, and calibration-
  tracked exactly like any other signal in this app, not a black box.
- **Own persistence**, same one-file-per-concern idiom as every other
  module (`data/market_analyst.db` or similar) — logs every analysis with
  the market, the agent's estimate, the market price at analysis time, and
  (once resolved) whether it was right, so this new agent's own track
  record can eventually be calibration-checked the exact same way
  `confidence_calibration.py` already checks the rule-based factors.
- **Own config gate, same precedent as `advisory.enabled`/
  `confidence_calibration.enabled`** — `market_analyst.enabled: false` by
  default, plus (following the exact `min_resolved_trades_per_variant`
  pattern) a minimum-track-record gate before its output is trusted for
  anything beyond logging.
- **Advisory-only, no execution authority, at least for this phase.** Its
  output becomes a new, visible signal alongside the existing ones — it
  does **not** get wired into `strategy_engine.evaluate()`'s actual trade
  decision in this phase. Promoting it into an actual decision input is a
  distinct, explicit, later step once there's a real track record — the
  same "prove it, then promote it" pattern `advisory_engine`'s manual-apply-
  with-audit-trail design and `confidence_calibration`'s read-only-v1 scope
  already established, twice, in this exact codebase. This is a safety
  call, not just a process one: an LLM's reasoning, however well-grounded,
  is a genuinely new and different failure mode from anything currently in
  the trading loop, and it should earn trust the same way everything else
  in this codebase has had to.
- **Cadence, a real new operational concern:** an LLM call per candidate
  market per tick is not free the way the existing arithmetic factors are —
  needs its own slower cadence, decoupled from the 15s poll loop, likely
  pre-filtered (e.g., only markets already clearing some existing
  confidence/volume bar) and cached until something material changes
  (price moves past some delta, new whale prints arrive, time-to-close
  crosses a threshold) rather than re-analyzed every tick.
- **New dependency, not currently in this codebase:** no Anthropic/OpenAI/
  LLM SDK is in `requirements.txt` or referenced in `.env.example` today —
  confirmed by direct check. Building this needs a real new API-key
  dependency, worth surfacing explicitly since every other credential this
  app uses is documented in `.env.example`'s "every variable is optional"
  pattern.

### 3.4 What this doc is explicitly NOT proposing

Not proposing wiring this into real trade execution, not proposing removing
or replacing any existing rule-based logic, not proposing skipping the
gated/advisory-only rollout every other analytical feature in this codebase
has gone through first.

---

## Part 4 — Phased implementation plan

| Phase | Work | Risk/cost | Depends on |
|---|---|---|---|
| **1 — quick, low-risk** | Confirm/fix `min_notional_usd: 1`; add fee modeling to `PaperBroker` P&L + Trading History display; extend `confidence_calibration.py` with overall-composite-score calibration buckets (not just per-factor) | Low — arithmetic/display changes, well-trodden pattern in this codebase | Nothing |
| **2 — moderate** | Feed `find_clusters()` into whale-signal confidence scoring; add the trend-consistency factor (reuses existing `market_history.momentum()`); price-aware (FLB-tiered) entry threshold | Moderate — changes what fires a trade, needs the same test-coverage discipline as every other `strategy_engine.py` change | Phase 1's calibration extension helps validate these before/after |
| **3 — larger, new surface area** | `market_analyst_agent.py` — advisory-only, gated, own persistence, own Config-tab section | Higher — new external dependency (LLM API), new cost/latency profile, genuinely new failure mode | `ml_feed.py` (already exists); an LLM API credential |
| **4 — explicitly deferred** | Promoting the analyst agent's output into actual `strategy_engine.evaluate()` decisions; any auto-apply path for it | Not started until Phase 3 has a real track record | Phase 3's own calibration history |

---

## Part 5 — Open decisions needing your call

1. **Confirm Option B (LLM-reasoning agent) over A/C** for the new agent,
   or redirect — this is the single biggest architectural fork in this doc.
2. **`min_notional_usd: 1`** — confirm whether this is an intentional live
   value (e.g., deliberately testing with a low bar) or should revert
   toward something closer to the documented $2,500 default.
3. **Fee-modeling scope** — retrofit onto already-closed historical trades
   in the Trading History display, or only apply prospectively to new
   closes? (Retrofitting is more honest but would change historical numbers
   someone may have already looked at.)
4. **Phase priority** — confirm the Phase 1→4 ordering above, or reorder
   (e.g., some may want the new agent sooner, accepting it'll initially run
   with less validation infrastructure around it than Phase 1/2 would
   provide).
5. **LLM provider/credential** — this app has no LLM SDK dependency today;
   confirm which to add before Phase 3 starts.

---

## Part 6 — Regulatory findings and what they imply for this app

Full detail in `docs/prediction-markets-research-reference.md` Part 3.
Headline: **sports-category event contracts are in genuinely live, actively
contested, multi-state legal dispute** as of August 2026 — not a settled
question with a clear answer, and one that's moving on a timescale of weeks.
Nevada is fully geofencing sports/election/entertainment contracts by
Aug. 12, 2026; Massachusetts has blocked sports contracts specifically since
January; several other states (Ohio, Illinois, Connecticut, Michigan, New
York) have active, unresolved suits running in both directions, including
the CFTC itself now suing states directly. Election contracts, by contrast,
are practically settled as tradeable (the CFTC abandoned its own appeal).

**This app has zero category-level legal-risk awareness today** —
`kalshi.categories` exists as a config filter but nothing maps a category to
"currently disputed in ways that matter for where I actually am." This
doesn't block anything about the algorithm work in Parts 1–3 above (this
app is paper/shadow-only right now, `trading_enabled: false`), but it's a
concrete, genuinely new item for the "Path to production" list in
`ROADMAP.md`: **before real trading is ever enabled, category selection
(and possibly a state-of-residence check) needs a real answer, not just a
volume/confidence filter** — trading a real Kalshi sports contract from a
state where that specific product is currently subject to an injunction is
a materially different risk than trading an election or economics contract.

**One direct, positive finding for the new agent design in Part 3 above:**
the CFTC's Feb. 2026 advisory on Kalshi's own two insider-trading cases (a
candidate trading his own race; a MrBeast employee trading on non-public
video content) is a concrete illustration that Kalshi's real-time
surveillance is real, and that the CFTC's anti-fraud authority (§6(c)(1)/
Reg. 180.1) treats trading on material non-public information as the
serious violation. This app's whole design — the real whale-watcher
provider, the market-native strategy, and the proposed market-analyst agent
alike — is already built entirely on **public** market/trade-tape data,
which is the right side of that line. Worth stating explicitly as a hard
constraint on the new agent's design (Part 3.3): its prompt/context must
stay to public data plus this app's own accumulated track record, never any
non-public information about a specific real-world event, full stop.
