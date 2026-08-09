# Prediction markets: research reference

Direct request (2026-08-09): "do deep research on prediction markets. dont
simplify anything. i need you to be a single source of truth on how these
markets work." Compiled from real web research (not recalled from training
data) across three parallel tracks — market mechanism theory/academic
evidence, Kalshi's specific product mechanics, and the regulatory/legal
landscape — plus several claims independently verified against this app's
own live, real Kalshi data where that was faster and more authoritative than
scraped documentation. Every claim below is sourced; contested or unverified
points are flagged as such rather than resolved silently. This is a
reference document, not a to-do list — see
`docs/prediction-market-strategy-alignment-plan.md` for what this implies
for this codebase's actual algorithms.

**Status:** All three parts complete.

---

## Part 1 — Market mechanism theory and academic evidence

*(Full research pass, high source density — see the consolidated source
list at the end of this part for every citation.)*

### 1.1 What a prediction market fundamentally is, and why price ≈ probability

A prediction market trades contracts whose payoff is contingent on a
specified, publicly verifiable future event — canonically $1 if a
proposition is true at resolution, $0 if false. The market is simultaneously
a risk-transfer venue and an information-aggregation mechanism: trading
itself compresses dispersed private information into one public number.

**The no-arbitrage argument for price = probability:** a risk-neutral trader
who believes the true probability of event E is *q* has expected value
*q*·$1 − *p* from buying a contract at price *p*. They buy if *q* > *p*,
sell if *q* < *p*. At equilibrium, price gets pushed toward the
wealth-weighted consensus belief. Primary source: Wolfers, J. & Zitzewitz,
E., "Interpreting Prediction Market Prices as Probabilities," NBER WP 12200
(2006); peer-reviewed companion: "Prediction Markets," *Journal of Economic
Perspectives* 18(2), 2004, 107–126.

**This interpretation depends on assumptions frequently violated in
practice:** risk-neutrality among marginal traders; no time-value-of-money
distortion (a contract resolving in 2 years should trade slightly below
true probability absent yield-bearing collateral — see a 2026 preprint,
arXiv 2602.21091, modeling this "long-horizon problem" directly); sufficient
liquidity; no counterparty/settlement risk; no trading frictions (fees,
spread, position limits); no persistent market power by one trader.

**The formal counter-argument — Manski's quantile result.** Manski, C.F.,
"Interpreting the Predictions of Prediction Markets," *Economics Letters*
91(3), 2006, 425–429 (peer-reviewed). Exact abstract: *"the price of a
contract in a prediction market reveals nothing about the dispersion of
traders' beliefs and partially identifies the central tendency of beliefs
... The mean belief of traders lies in an interval whose midpoint is the
equilibrium price."* I.e., under pure risk-neutral heterogeneous-belief
trading, price is a specific quantile of the belief distribution, not
necessarily the mean. **This is a live, unresolved tension in the
literature**: Wolfers & Zitzewitz's rebuttal is that once you allow standard
risk-aversion, price converges close to the wealth-weighted mean under
"reasonable" assumptions — Manski's camp would say "reasonable" is doing a
lot of unverified work. Both papers are correct on their own terms; which
idealization better describes real traders is not settled.

### 1.2 Favorite-longshot bias (FLB)

Systematic mispricing where longshots are overpriced relative to true win
rate and favorites underpriced. Described across sources as "the single
most robust finding in prediction/betting market research" — but with a
genuine, important exception noted below.

**Confirmed on Kalshi itself, with real transaction data:** Bürgi, C.,
Deng, W., & Whelan, K., "Makers and Takers: The Economics of the Kalshi
Prediction Market," CEPR DP20631 (Sept 2025, working paper, 300,000+
contracts). Kalshi prices are informative and improve approaching close, but
show clear FLB — **and the bias is concentrated in liquidity-*takers*: takers
lose ~32% on average buying longshots, makers lose only ~10%**, consistent
with a maker/taker adverse-selection microstructure model. This is directly
relevant to any strategy that trades Kalshi as a taker (market/IOC orders).

**The important counter-finding:** Berg, J.E. & Rietz, T.A., "Longshots,
Overconfidence and Efficiency on the Iowa Electronic Market," *International
Journal of Forecasting*, 2019 (peer-reviewed) found **no** significant FLB
on IEM at short horizons — plausibly because IEM is small-stakes
($500 cap), academically monitored, with a disproportionately sophisticated
trader base. Consistent with a broader pattern (exchanges show less FLB than
dealer-quoted bookmakers) suggesting participant composition, not some
universal law, moderates the bias.

**Two competing, both empirically-supported explanations:** (1) risk-love —
rational risk-seeking bettors overpay for lottery-like payoffs; (2)
prospect-theory misperception — Snowberg, E. & Wolfers, J., "Explaining the
Favorite-Longshot Bias: Is it Risk-Love or Misperceptions?" *Journal of
Political Economy* 118(4), 2010 (peer-reviewed, top-5 journal) — using
horse-racing data specifically chosen to separate the hypotheses, finds
evidence favoring misperception/prospect theory; (3) Shin, H.S. (1991, 1992,
both peer-reviewed, *Economic Journal*) — a pure microstructure explanation:
a market-maker facing possible insider participation rationally sets a
markup proportionally larger on longshots (more information-sensitive) to
protect against being picked off, generating FLB with zero behavioral bias
required. All three have peer-reviewed empirical support in different
settings; the dispute is not resolved.

### 1.3 Market mechanism designs

| Design | Mechanics | Used by |
|---|---|---|
| **CLOB** (central limit order book) | Live ledger of resting bid/ask orders, matched price-time priority. Market orders execute now (walk the book); limit orders guarantee price, not fill. No single "the price" once there's a spread — last-trade, mid, and each side's executable price all differ. | Kalshi, PredictIt, Polymarket (since ~2021–22) |
| **LMSR** (logarithmic market scoring rule, Hanson 2003/2007) | Cost function `C(q) = b·ln(Σᵢ e^(qᵢ/b))`; price `pᵢ(q) = e^(qᵢ/b) / Σⱼ e^(qⱼ/b)` — a softmax, so **Σpᵢ = 1 exactly, always, by construction**. Worst-case operator loss bounded at `b·ln(n)`. | Augur v1 (2018), early Gnosis-based markets |
| **CPMM/FPMM** (constant-product, Uniswap-derived via Gnosis) | Pool invariant `Π(reserves) = k`; odds derived from relative reserves, same sum-to-1 guarantee via a different functional form. No closed-form worst-case-loss bound; LPs bear impermanent loss that worsens near resolution. | Pre-2021/22 Polymarket, various decentralized clones |
| **Parimutuel** | Wagers pooled, house takes a commission, split pro-rata among winners only after close. **No continuous price discovery** — displayed "odds" pre-close aren't tradable/executable prices at all. | Horse racing; largely absent from modern prediction-market platforms |

Historical arc: parimutuel → LMSR/AMM (~2014–2021, needed to guarantee a
quote existed in a brand-new market) → CLOB (Kalshi/PredictIt throughout,
Polymarket since ~2021–22, once organic volume/market-maker interest made a
CLOB's capital efficiency outweigh the bootstrapping problem).

**Multi-outcome sum-to-100% enforcement differs sharply by design** — this
matters directly for a CLOB-based app: on an AMM, sum-to-100% is a
mathematical identity. **On a CLOB (Kalshi included), it is not enforced by
the mechanism at all** — each binary market's book clears independently, and
N outcome prices summing to something other than 100% is a real, observed
phenomenon until arbitrageurs close the gap (arXiv 2601.01706, 2026 preprint,
documents cases of this "law of one price" violation directly).

### 1.4 Prediction market accuracy — the academic evidence

**Iowa Electronic Markets (IEM), the longest clean track record:** across 14
presidential-election contracts, average absolute forecast error of the
market's own price vs. actual outcome ≈ 1.33–1.34 percentage points;
comparing IEM prices to 964 individual polls across 5 elections (1988–2004),
**the market was closer to the eventual outcome than the poll 74% of the
time**, with the advantage growing further out from election day. Primary
citation: Berg, Forsythe, Nelson & Rietz, "Results from a Dozen Years of
Election Futures Markets Research," *Handbook of Experimental Economics
Results* Vol. 1, Ch. 80 (2008, peer-reviewed).

**Recent (2024–2026) research specifically on Kalshi/Polymarket/PredictIt —
an active, largely still-preprint literature:**

- **Clinton & Huang (Vanderbilt, Dec 2025, preprint):** 2,500+ political
  markets across IEM/Kalshi/PredictIt/Polymarket, final 5 weeks of the 2024
  election, ~$2.4–2.5B volume. **Hit-rate accuracy: PredictIt 93%, Kalshi
  78%, Polymarket 67%.** Also found weak/negative serial autocorrelation in
  daily price changes and *increasing* arbitrage opportunities in the final
  two weeks (the opposite of efficient aggregation) — interpreted as
  evidence of herding, not just information processing.
  **Directly disputed by Kalshi**, who argued *calibration* (do 70%-priced
  events happen ~70% of the time across many events) is the correct
  accuracy metric, not binary hit-rate, and claimed near-perfect calibration
  by that measure. **This is a genuinely unresolved methodological
  dispute** — a platform listing mostly close races will show a hit-rate
  near a coin flip almost by construction regardless of calibration
  quality, and no source reconciles the two metrics for this dataset.
- **Gomez Cram, Guo, Jensen & Kung, "Prediction Market Accuracy: Crowd
  Wisdom or Informed Minority?"** (SSRN preprint): evidence favors
  "informed minority" over "crowd wisdom" — **accuracy is concentrated in
  roughly 3% of accounts that are persistently skilled**, not spread evenly.
  Directly relevant to whale-signal design: informativeness traces to *who*
  trades (skill, persistence), not raw trade size per se.
- **Abid, "The Polymarket Paradox"** (SSRN preprint, Apr 2026, single-author,
  lowest-authority tier here): documents a single French trader across 11
  linked wallets accumulating ~$85M profit, pushing Polymarket's implied
  Trump probability **10–15 percentage points above competing platforms for
  weeks** during October 2024 — a sustained, large deviation, not a brief
  blip. Also two 2025 oracle-resolution disputes (combined $250M+) allegedly
  captured by governance-token whales — a decentralized-governance-specific
  attack surface with no analogue on Kalshi's centralized resolution.
- **Mitts & Ofir, "From Iran to Taylor Swift: Informed Trading in Prediction
  Markets"** (SSRN, via Harvard Law School Forum, Mar 2026): systematic
  screening of ~93,000 Polymarket markets / ~50,000 wallets (Feb 2024–Feb
  2026) using a **composite score** (cross-sectional size + within-trader
  size + profitability + pre-event timing + directional concentration) —
  **210,718 flagged suspicious wallet-market pairs, 69.9% aggregate win
  rate**, over 60 standard deviations from chance under a permutation test,
  ~$143M aggregate profit (likely a lower bound). Named cases: a Feb 2026
  Iran strike (one wallet trading 71 minutes pre-announcement), a Maduro
  contract, a Google product-launch contract, an OpenAI browser launch, and
  the widely-reported "romanticpaul" Taylor Swift engagement trade.
  **Important caveat: this evidence is specifically about trades the authors
  classify as reflecting likely misappropriated non-public information** — a
  narrower, legally-loaded claim than "large bettors are generally smarter,"
  though directly on point for "does whale flow correlate with being
  right" (answer, for this flagged subset: clearly yes).

**Manipulation research:** the classic finding (Rhode & Strumpf, field
experiment + a century of observational data, peer-reviewed companion in
*PS: Political Science & Politics*) is that manipulation attempts on IEM had
only short-lived price effects, quickly undone. **This does not obviously
generalize** to larger, less-monitored, more heavily-capitalized markets —
the Polymarket "French whale" episode (weeks-long, double-digit-point
deviation) is a materially different scale of effect. Hanson's own
theoretical work (2009, peer-reviewed) shows manipulator presence can
*increase* average price accuracy in a stylized model (more noise raises
returns to becoming informed) — manipulation risk isn't unambiguously bad
for accuracy in theory, but the empirical record on real, large markets is
mixed.

**Overall assessment:** both "markets aggregate real information" and
"markets are subject to herding/bias/manipulation" are true simultaneously,
and the balance varies by market and by time-to-resolution. No source
supports either extreme as a blanket claim.

### 1.5 Why large trades might (or might not) carry informational value

**The core theory, in order of relevance:**

- **Kyle (1985, Econometrica, foundational):** informed traders
  *strategically restrict* how aggressively they trade specifically to
  avoid revealing information too fast — the model's informed trader has an
  incentive to *disguise* size, not broadcast it in one giant print.
- **Glosten & Milgrom (1985, JFE):** bid-ask spread emerges purely as
  compensation for adverse-selection risk from possibly-informed order
  flow — directly explains why Kalshi "makers" (Bürgi/Deng/Whelan) price in
  a longshot-bias premium as protection.
- **Easley & O'Hara (1987, JFE) — the direct theoretical grounding for
  "whale = probably informed":** informed traders, wanting to extract value
  before their information becomes public, prefer to trade *larger*
  quantities than uninformed traders at any given price. A rational market
  maker's pricing schedule should therefore depend on size — larger trades
  should move price more, because size is a real (if noisy) signal.
- **Barclay & Warner (1993, JFE) — the key empirical complication:** using
  real equities data, **most cumulative price change traces to *medium*-size
  trades, not the largest blocks**, despite blocks being a minority of
  count. Their "stealth trading hypothesis": informed traders deliberately
  break orders into medium pieces specifically to avoid the price impact and
  detection a single giant block would trigger. **This is the single
  strongest empirical caution against naive whale-watching**: a
  sufficiently sophisticated informed trader has both theoretical incentive
  (Kyle) and documented empirical tendency (Barclay-Warner) to *avoid*
  looking like an obvious whale in one print.

**Applied to prediction/betting markets specifically:** Shin's insider model
(1.2 above) reinforces this directly. Closing Line Value — whether a
bettor's action, on average, beats the eventual closing line — is the
standard practitioner heuristic for identifying "sharp" bettors in sports
betting (industry consensus, not peer-reviewed, but real and consequential —
books actually restrict sharp action based on it). Mitts & Ofir's Polymarket
finding required a **multi-signal composite** (size + profitability history
+ timing + directional concentration) to achieve real predictive power —
**size alone was not the sufficient statistic even in the one dataset where
flagged large/composite-scored trades were dramatically right more often
than chance.**

**Reasons a large trade might NOT be informative** (directly requested,
synthesized): a wealthy-but-wrong trader; a hedger offsetting real
commercial exposure (observationally identical size signature, zero
informational content); deliberate manipulation (the "French whale" case);
stealth-trading logic implying the *most* sophisticated informed flow
specifically avoids looking like a conspicuous single whale print; and
market-making/liquidity-provision activity that isn't a directional bet at
all.

**Synthesis:** the theory (Kyle, Easley-O'Hara) and some direct evidence
(Mitts & Ofir) genuinely support "large trades correlate with elevated
probability of being informed" as a real, peer-reviewed-grounded prior — but
it is a **probabilistic, average-case heuristic, not a reliable per-trade
signal**, and the strongest empirical work on *which* large trades are
informative required combining size with profitability history, timing, and
directional concentration — not raw size in isolation.

### Consolidated sources — Part 1

**Peer-reviewed:** Manski (2006, *Econ Letters*); Wolfers & Zitzewitz (2004,
*JEP*); Snowberg & Wolfers (2010, *JPE*); Shin (1991, 1992, *Econ Journal*);
Kyle (1985, *Econometrica*); Glosten & Milgrom (1985, *JFE*); Easley & O'Hara
(1987, *JFE*); Barclay & Warner (1993, *JFE*); Berg/Forsythe/Nelson/Rietz
(2008, *Handbook of Experimental Economics Results*); Berg/Nelson/Rietz
(2008, *IJF*); Berg & Rietz (2019, *IJF*); Hanson (2003, *Information
Systems Frontiers*; 2007, *J. Prediction Markets*; 2009, *Economica*); Rhode
& Strumpf (*PS: Political Science & Politics*).

**Working papers/preprints (not peer-reviewed, cited for currency, flagged
throughout):** Wolfers & Zitzewitz NBER WP12200; Manski NBER WP10359;
Bürgi/Deng/Whelan CEPR DP20631 (2025); Clinton & Huang (Vanderbilt/OSF,
2025); Gomez Cram et al. (SSRN); Abid (SSRN, 2026); Mitts & Ofir (SSRN via
Harvard Law Forum, 2026); Tetlock (2008); Rhode & Strumpf (2008 WP); arXiv
2602.21091 (2026, long-horizon problem); arXiv 2601.01706 (2026, law-of-one-
price violations).

**Lowest tier (platform mechanics only, not empirical/theoretical claims):**
DL News, Cultivate Labs, Ruckus Markets docs, Gnosis docs/GitHub, Polymarket
official docs, DataGolf (contrarian "not a bias" take), Wikipedia.

---

## Part 2 — Kalshi's specific mechanics

Mixed-confidence research pass — the agent researching this hit a tool
session limit partway through and multiple Kalshi doc pages 429'd or
required a headless browser. **Two of the most operationally important
gaps were closed independently, by checking this app's own live data
directly against the real Kalshi API rather than scraped docs** — flagged
below as `[live-verified]`, the highest-confidence tier available.

### 2.1 Company/regulatory history

**Verified (CFTC.gov, primary):** KalshiEX LLC received an **Order of
Designation as a Designated Contract Market (DCM) on November 4, 2020**
under Section 5 of the Commodity Exchange Act and CFTC Regulation 38.3(a)
(CFTC Press Release 8302-20). A DCM designation authorizes trading of
futures, swaps, and/or options on commodities — the regulatory bucket
"event contracts" occupy.

**Self-certification (secondary legal-commentary sources, converging,
moderate confidence):** under CFTC Reg. 40.2(a), a DCM can self-certify and
list a new contract; the filing must reach the CFTC by the business day
before listing, and must include contract terms, listing date, a CEA-
compliance certification, and a concise explanation/analysis. The CFTC then
has a **90-day post-hoc review window** (review starts within 10 days of
listing; DMO can issue a written statement of concerns; if no CFTC order
issues within 90 days, the listing stands). This 90-day window is the actual
limit on self-certification — the CFTC retains the power to disapprove a
contract after the fact (a real, named instance: "CFTC Disapproves KalshiEX
LLC's Congressional Control Contracts," CFTC Press Release 8780-23 — title
confirmed, contents not independently fetched this pass).

### 2.2 Market hierarchy

**Verified directly against docs.kalshi.com/getting_started/terms** (Kalshi's
own API glossary): the real hierarchy is **Category → Subcategory → Series →
Event → Market** — five levels, not four. Category is one-to-one (a series
belongs to exactly one category); **Subcategory is many-to-many** (a series
can belong to multiple subcategories) — worth knowing since this app's
`_series_meta_map()` currently surfaces category/tags but this many-to-many
nuance is worth double-checking against how it's modeled. Quoted
definitions: **Event** = "a collection of markets and the basic unit that
members should interact with on Kalshi." **Market** = "a single binary
market... rarely will need to be exposed on its own."

**Multi-outcome/mutually-exclusive mechanics — already resolved by this
app's own prior work, not a research gap:** `get_event()`'s real
`mutually_exclusive` boolean field is already fetched and used by this
codebase (`title_cache.py`, the inversion-pair-collapsing work). No
Kalshi-side cross-market arbitrage enforcement is implied or needed by that
field — see Part 1 §1.3's finding that CLOB-design sum-to-100% is *not*
mechanically enforced, only arbitraged.

### 2.3 Order types and mechanics

**Verified against Kalshi's FIX protocol docs (institutional order-entry
API — field names may differ from the REST API):** supported time-in-force
values: Day, GTC (Good-Till-Cancelled), **IOC (Immediate-or-Cancel)**, FOK
(Fill-or-Kill), GTD (Good-Till-Date). Prices are integer cents, 1–99.
Self-trade prevention has two modes: "Taker At Cross" (default) and "Maker."
A confirmed rejection code ("Order exceeds limit") proves *some* position/
order limit is enforced — exact values not found.

**`[live-verified]` This app's real order path uses `time_in_force:
"immediate_or_cancel"` as its default** (`services/kalshi_account_client.py`,
confirmed by direct code read this session) — meaning once real trading is
enabled, this app trades as a **taker** by default. Combined with §1.2's
Kalshi-specific finding that takers lose ~3x more than makers on longshots,
this is a direct, concrete implication for this codebase (see the
alignment plan).

**Not independently verified this pass, worth a follow-up:** the exact REST
API JSON field names for order creation (vs. the FIX names above), and
whether the unified-orderbook claim (Yes bid at X ≡ No ask at 1−X, so Kalshi
only needs to track one side) is confirmed in Kalshi's own REST docs — this
is very likely true given binary-contract economics and is already how this
app's own code treats price (see the "price is always the yes-side price by
convention" comment threaded throughout `paper_broker.py`/`whale_simulator.py`),
but wasn't independently re-confirmed against a primary Kalshi source this
pass.

### 2.4 Fee structure

**`[live-verified]` — the real, current formula, confirmed against three
real fills on this app's own connected account** (not scraped from a blog):

```
taker_fee = round_up_to_$0.0001( 0.07 × contracts × price × (1 − price) )
```

Verified against real `fee_cost` values on real fills: a 14.11-contract fill
at $0.84 → predicted $0.132747, rounded up to 4dp = $0.1328, actual
`fee_cost` = **$0.1328** (exact match). A 9.13-contract fill at $0.53 →
predicted $0.159200, actual **$0.1592** (exact match). A 21.75-contract fill
at $0.17 → predicted $0.214825→$0.2149, actual **$0.2149** (exact match).
**This directly corrects a wrong secondary-source claim** (several
fee-schedule blog aggregators reported rounding to the nearest whole cent —
the real precision is 4 decimal places, matching Kalshi's `FixedPointDollars`
convention used throughout this app's own code).

This is a **parabolic fee curve**: zero at price extremes (near 0¢ or 100¢),
maximal at 50¢ — worst case exactly **$0.0175/contract** taker fee, so
~$0.035/contract round-trip (buy+sell) at the 50¢ midpoint. Cheaper the
further a contract's price sits from 50¢.

**Not independently verified — secondary sources only, converging but
unconfirmed against Kalshi's own fee schedule PDF (which rate-limited every
fetch attempt):** maker fee reported as exactly 1/4 of the taker rate
(`0.0175 × C × P × (1−P)`), charged only if a resting order fills, never on
cancellation. Whether fees differ by category (e.g., sports vs. politics) —
unresolved, one source hinted "some products sit on their own, lower table"
with no specifics found.

**This app currently models zero fees anywhere in `PaperBroker`'s open/close
P&L math** — see the alignment plan for why this matters.

### 2.5 Settlement mechanics

**Verified via help.kalshi.com (Kalshi's own help center, official but
consumer-facing):** markets typically settle within a few hours of the
outcome being known (~3h typical), resolving against named "source
agencies" (official league stats, government releases, other event
authorities) — Kalshi won't settle until the authoritative source publishes
a finalized result. Winning positions pay $1.00/contract, losing $0.00,
funds move to cash balance on settlement. Non-standard resolutions exist
(e.g. "last fair market value" for a player who didn't participate); combo
markets multiply payouts across legs. Users should contact support after
~12h without an update per the help center; a separate, unreconciled
secondary source cites a 48h internal dispute-investigation window — these
may describe different things (when to first ask vs. how long investigation
can take) but weren't reconciled.

### 2.6 Kalshi vs. Polymarket vs. PredictIt (2026 regulatory posture)

- **Polymarket**: acquired QCX LLC (a CFTC-licensed exchange) and its
  clearinghouse QC Clearing for $112M, forming "Polymarket US" — its own
  CFTC-regulated Designated Contract Market, the *same* regulatory category
  as Kalshi, not a lesser one. CFTC issued an Amended Order of Designation
  (Nov 2025). Polymarket US currently offers only fully-collateralized
  contracts (no margin/leverage). Separately, Polymarket filed (Apr 28,
  2026) seeking approval to let US users trade on its *original* offshore
  platform too — implying two distinct Polymarket surfaces persist. A 2022
  CFTC investigation was reportedly dropped without charges (Jul 2025), but
  a *new* CFTC probe reportedly opened June 2026 — unresolved, details not
  found.
- **PredictIt**: operates under CFTC staff no-action relief (not a DCM).
  Operator changed from Victoria University of Wellington to the Prediction
  Market Research Consortium (PMRC), a US nonprofit, per CFTC Letter 25-20
  (Jul 14, 2025). Position cap reportedly raised from $850 to $3,500. **An
  unresolved ambiguity**: PredictIt historically had *two* separate caps
  (a per-trader dollar cap and a separate per-market trader-count cap,
  historically ~5,000 traders) — only the dollar-cap change was confirmed;
  whether the trader-count cap still exists is unverified.

### Gaps still open in Part 2 (flagged honestly rather than guessed at)

- Exact REST API field names for order creation (only FIX names confirmed).
- Position/order limit values (existence confirmed, magnitude not).
- Whether fees differ by market category.
- Current (2026) full market-category scope — sports dominance is plausible
  (one secondary source claimed 80–87% of volume) but not confirmed, and
  weather/entertainment/"mentions"/earnings categories (all of which this
  app's own trade tape already shows real examples of —
  `KXFOXNEWSMENTION`, `KXPGATOUR` were seen in this app's own live data
  during this research) were not independently confirmed as Kalshi's
  official current category taxonomy from an external source.

---

## Part 3 — Regulatory and legal landscape

Current as of early August 2026. This area has moved extremely fast — most
of the consequential activity below happened in 2025–2026. Every claim is
dated and sourced; primary sources (statute text, court dockets, CFTC
orders, state AG filings) are used where found, secondary reporting flagged
as such. **This is a genuinely unsettled, rapidly-changing area of law** —
contested points are described as contested, not resolved in either
direction.

### 3.1 The statutory foundation

The Commodity Exchange Act's "Special Rule," **7 U.S.C. § 7a-2(c)(5)(C)**
(text confirmed directly against uscode.house.gov), lets the CFTC bar an
event contract as "contrary to the public interest" if it involves: unlawful
activity, terrorism, assassination, war, **"gaming,"** or other
Commission-designated activity. The statute defines none of these terms —
that interpretive gap is the entire fight below. A **90-day** review-and-order
structure governs how the CFTC can act on a self-certified contract.

**History of its use:** first invoked April 2012 against NADEX's political
contracts (a case-specific adjudicative order, not a generalized
rulemaking — no 2012–13 notice-and-comment rule was found). ErisX withdrew
NFL contracts in 2020 rather than face a similar order. September 2023: the
CFTC used it against **Kalshi's** congressional-control contracts —
non-unanimously (Commissioner Mersinger dissented; Commissioner Pham
abstained) — triggering the litigation in §3.2.

**The current rulemaking track, separate from and now largely supplanting
the litigation:** a broad, contested May 2024 NPRM under then-Chairman
Behnam (would have presumptively classified political/sports contracts as
"gaming") was **withdrawn February 4, 2026** by new Chairman **Michael
Selig** (confirmed Dec. 18, 2025), who called it "merit regulation." A
replacement NPRM, "Prediction Markets; Public Interest Determinations,"
published June 12, 2026 (91 FR 35806), proposes a defined three-step test
for "involve"/"gaming." **Public comments closed July 27, 2026 — this rule
is not yet final.** A separate NPRM retrofitting large-trader data-reporting
rules for event contracts is also pending. **Bottom line: the master legal
test for what counts as impermissible "gaming" could still change by
administrative rule, independent of anything the litigation below settles.**

### 3.2 Election contracts (Kalshi v. CFTC) — practically resolved, not appellate-binding

*KalshiEX LLC v. CFTC*, No. 1:23-cv-03257 (D.D.C.). Sept. 6, 2024: Judge
Jia Cobb vacated the CFTC's order, holding election contracts don't involve
"gaming" or unlawful activity. The CFTC's D.C. Circuit stay request was
denied (Oct. 2024), and the **CFTC itself voluntarily dismissed its own
appeal** (May 7, 2025) rather than litigate to a merits ruling. **Practical
effect:** election contracts are settled as tradeable. **Legal nuance worth
keeping in mind:** the D.C. Circuit never ruled on the merits, so there is
no binding appellate precedent — only a persuasive, non-binding district
opinion plus an abandoned appeal.

### 3.3 Sports contracts — the live, actively-contested core of this landscape

Kalshi/CFTC's argument: **federal preemption** — the CEA gives the CFTC
exclusive jurisdiction over swaps on a registered DCM (field preemption),
and a state can't force a nationally-listed exchange to selectively deny a
product by state residency without conflicting with the CEA's national-
market design (conflict preemption; CFTC complaints cite 7 U.S.C.
§ 2(a)(1)(A)). States' argument: gambling is a traditional state police
power the CEA (a financial-derivatives statute) wasn't clearly written to
displace, and a sports-outcome contract is functionally a sports bet
regardless of its federal wrapper.

**State-by-state status, verified per-state (a genuinely mixed, moving
picture — do not treat any single outcome as universal):**

| State | Status (as of Aug 2026) |
|---|---|
| **New Jersey** | **Win for Kalshi, the only circuit-court merits ruling to date**: Third Circuit affirmed (*KalshiEX v. Flaherty*, No. 25-1922, Apr. 2026, 2–1, Judge Roth dissenting) that sports contracts are CEA "swaps" and state law is field/conflict-preempted. Binding only in the 3rd Circuit (DE/NJ/PA/VI). |
| **Nevada** | **Loss for Kalshi, on the merits, after a preliminary injunction was won then reversed**: Judge Gordon (D. Nev.) dissolved Kalshi's own injunction Nov. 25, 2025, holding sports contracts fall **outside** CFTC exclusive authority — "upsets decades of federalism." Ninth Circuit declined to disturb it. Kalshi settled with Nevada July 27, 2026: full GeoComply geofencing of NV users from sports/election/entertainment contracts by Aug. 12, 2026, or $120,000/day. **This sits in the same circuit (9th) that will decide the tribal-lands appeal below — a real, brewing circuit split.** |
| **Arizona** | Win for Kalshi: permanent injunction (May 5, 2026) blocking a 20-count criminal prosecution, on the same preemption theory. |
| **Maryland** | **Kalshi's first courtroom loss** (Aug. 1, 2025, predates Nevada's reversal) — preliminary injunction denied. On appeal to the 4th Circuit; **outcome still pending** as of this research. |
| **Massachusetts** | Loss for Kalshi on sports contracts specifically (other contract types unaffected): state-court injunction Jan. 20, 2026; 38 state AGs filed amicus support for MA Apr. 27, 2026; SJC appeal status unclear/ongoing. |
| **Ohio** | Cease-and-desist + a $5M fine Kalshi is now suing to block (filed June 30, 2026) — unresolved. |
| **Illinois** | New state law (SB 3019, effective July 1, 2026) taxing prediction markets as sports wagering; **both** Kalshi (as plaintiff) and the **CFTC+DOJ jointly** (as plaintiff, against the state, Apr. 2, 2026) have sued — unresolved; Kalshi reported still operating in IL as of July 2026. |
| **Connecticut** | Swept into the same Apr. 2, 2026 CFTC+DOJ suit as Illinois — unresolved. |
| **Michigan** | **A genuine federal-vs-state standoff, unresolved**: an Ingham County court ordered Kalshi to halt/void MI trades (June 29, 2026); the CFTC then invoked emergency authority directing Kalshi to **honor** those trades anyway, effectively telling it to defy the state court. |
| **New York** | **The largest dollar figure in this entire landscape**: NY sued Kalshi July 31, 2026 seeking a minimum **$36 billion** in penalties/disgorgement; the CFTC filed its own suit the same day to block NY's enforcement. Brand-new, fully unresolved. |
| **Montana** | An initial agreement unraveled; Kalshi sued Montana officials Apr. 2026 — unresolved. |
| **Minnesota** | The only outright legislative ban (broader than sports, effective Aug. 1, 2026) — **blocked** by a preliminary injunction (Judge Menendez, July 27, 2026) sought jointly by Kalshi, Polymarket, **and the CFTC** as co-plaintiffs. |
| **Tribal lands (IGRA)** | A separate legal track: 3 California tribes' preliminary-injunction motion was denied (Nov. 2025); Ninth Circuit heard argument July 10, 2026 with judges reportedly skeptical of Kalshi's position; **27 states + D.C. filed an amicus brief supporting the tribes**. No ruling yet. A parallel Ho-Chunk Nation (WI) suit's status is reported as having "survived" a challenge but **could not be independently corroborated** — low confidence. |

**Net assessment: this is not settled, in either direction.** The Third
Circuit and Arizona favor Kalshi; Nevada (after full merits briefing) and
Maryland/Massachusetts do not. Both the Nevada appeal and the tribal-lands
appeal sit before the Ninth Circuit, which could produce the first real
circuit split against the Third Circuit — a strong Supreme Court review
candidate. **Do not treat any single-state outcome, in either direction, as
representative of the whole picture.**

**A notable pattern of its own:** starting April 2026, the **CFTC itself
became an affirmative plaintiff** suing states directly (Arizona,
Connecticut, Illinois, and joining the Minnesota/Michigan/New York fights)
— described by federal officials as the first-ever direct CFTC preemption
suit against a state over event contracts. This is a meaningful shift from
neutral regulator to active combatant defending a specific regulated
entity's business model against sovereign states.

### 3.4 Other platforms' regulatory posture

**PredictIt**: no-action letter rescinded by the CFTC in Aug. 2022,
litigated, and restored/expanded via **CFTC Letter 25-20 (July 14, 2025)** —
operator changed to a new US nonprofit (Prediction Market Research
Consortium), position cap raised **$850 → $3,500**, the historical
5,000-trader-per-contract cap **eliminated**. Remains restricted to
**political/election contracts only** by design — stays entirely outside
the sports-contract fight. **Currently active and more permissive than
before**, not damaged by Kalshi's litigation.

**Polymarket**: a Jan. 2022 CFTC enforcement order ($1.4M penalty) for
operating an unregistered facility predates its offshore/geofenced
US-blocked era. Acquired **QCX LLC + QC Clearing** (a small existing
CFTC-licensed DCM/DCO) for a reported **$112 million**, rebranding as
"Polymarket US" — the **same regulatory category as Kalshi**, not a lesser
one. Live real-money US operations since late 2025/2026, reportedly over
**$3.5 billion** in June 2026 monthly notional volume. Also a co-plaintiff
with Kalshi/CFTC in the Minnesota preemption case.

### 3.5 Manipulation, insider trading, and integrity

**The 2024 Polymarket "French whale" episode** ($28M+ wagered across 4
linked accounts, correctly calling the full 2024 election outcome,
profit estimates to $85M): Polymarket's own internal investigation
("including third-party experts") found no evidence of manipulation — an
interested party's self-report, not an independent finding. France's
gambling regulator reportedly opened an inquiry. **No US regulatory finding
of manipulation has been made public.**

**Kalshi-specific insider-trading cases — the closest thing to a documented
US enforcement record, and directly relevant to how mature Kalshi's own
market surveillance actually is:** a Feb. 25, 2026 CFTC Division of
Enforcement advisory (primary source, cftc.gov) disclosed two cases, **both
caught by Kalshi's own internal surveillance, not a CFTC-initiated
investigation**: (1) a political candidate traded contracts on his own
candidacy — Kalshi fined him $2,246.36 and imposed a 5-year platform ban;
(2) a MrBeast/Beast Industries video editor (**Artem Kaptur**) traded ~$4,000
on Kalshi's YouTube "streaming markets" using non-public information about
video content, achieving "near-perfect" results — Kalshi fined him $15,000
plus $5,397.58 disgorgement and a 2-year ban. The CFTC's advisory states it
retains full authority to independently pursue either case under §6(c)(1)/
Reg. 180.1 (the CEA's anti-fraud/anti-manipulation rule) but **had not done
so as of the advisory**. **No confirmed wash-trading enforcement action**
against a US-regulated prediction-market platform was found — the statutory
tools exist (CEA §4c(a)(1)-(2)(A), Reg. 1.38(a)) but this research found no
case where they've actually been used here.

**Is CFTC oversight comparable to legacy futures-market surveillance?**
Mixed. The same DCM Core Principles and anti-manipulation authority formally
apply, but the CFTC's own reporting/surveillance infrastructure for this
specific product category is visibly still being built (the June 2026
NPRM retrofitting Parts 15–21 large-trader reporting exists precisely
because those rules predate event contracts), and the enforcement track
record is thin (the Feb. 2026 advisory describes the first publicly
disclosed cases in this category). The NFL itself publicly pushed the CFTC
in July 2026 for stronger rules on sports prediction contracts — an
interested party's advocacy, but a real signal that even a major
stakeholder sees gaps.

### 3.6 Legality for the end user

Federally, any eligible US person can open a Kalshi account nationwide (no
federal bar by state) — 18+ (notably **below** the 21+ most licensed
sportsbooks require, a point states have explicitly cited as a consumer-
protection objection), US residency, standard KYC. **But this breaks down
at the state level for specific contract categories**, currently: Nevada
users are being fully geofenced from sports/election/entertainment
contracts by Aug. 12, 2026; Massachusetts users have been blocked from
sports contracts specifically since Jan. 2026. Everywhere else currently in
litigation, Kalshi is reported as continuing to operate pending outcomes —
**this is a moving target that can change within days**, not a stable list.
**No state has yet fully and permanently blocked Kalshi statewide on a
final, non-appealable basis.**

### Summary: settled vs. genuinely unresolved

**Reasonably settled:** election contracts are practically tradeable
nationally (though not via binding appellate precedent); PredictIt's
narrower academic no-action framework is intact and was expanded; Polymarket
now has a real US regulatory pathway; the CFTC has real (if still-maturing)
tools against fraud/manipulation/insider trading and has used them at least
twice via Kalshi's own surveillance systems.

**Actively, unambiguously contested — treat as live, not resolved:**
whether the CEA preempts state gaming law for **sports** contracts (a real
brewing circuit split, Third Circuit vs. Nevada/Maryland/Massachusetts);
the exact legal definition of "gaming" itself (still in proposed-rule form,
comments just closed); tribal sovereignty/IGRA claims (pending, Ninth
Circuit skeptical per reports); the Michigan federal-vs-state standoff and
the $36B New York suit (both essentially brand-new); whether the Polymarket
French-whale episode involved real misconduct (no US regulator has ruled
either way).
