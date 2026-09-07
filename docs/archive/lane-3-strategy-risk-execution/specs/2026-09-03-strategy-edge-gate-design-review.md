# Adversarial Review — Strategy Edge Gate Design (2026-09-03)

Reviewing: `docs/archive/lane-3-strategy-risk-execution/specs/2026-09-03-strategy-edge-gate-design.md`,
worktree `agent-a0cfb3e1e724c2431`, commit `3733d73`. Per CLAUDE.md's
"nothing advances on one pass" HARD RULE, this is a fresh Agent call with no
memory of the session that wrote the artifact; every load-bearing claim below
was re-derived from primary sources (source code, config, docs/kalshi/, git
history), not taken from the artifact's own tables, quotes, or its embedded
"Design self-review" section.

Scope note: this design is documentation-only (no code/config/data changed),
stays inside Program 1-2 (paper mode), and never touches
`kalshi_account.trading_enabled` or the kill switch — confirmed by direct
reading, see Finding 10.

## Method

Read in full: the artifact itself; both research documents it cites
(`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`
§3, §3.3, §3.4, §11, §12, §13; `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`
§4.4, §6.7, §8); `docs/prediction-market-strategy-alignment-plan.md` Part 3.2.

Read/grepped directly: `services/strategy_engine.py` (`evaluate()` in full,
`_validate_entry_price`, `kelly_scaled_max_size`, `EntryValidation`),
`services/whalewatchers/kalshi_trade_tape.py` (`_analyst_factor`,
`_process_trades_sync`), `services/confidence_scoring.py` (`DEFAULT_WEIGHTS`,
factor-8 docstring), `services/exits/exit_engine.py` (`_exit_confidence`),
`services/analytics/routes.py`, `services/kalshi_fees.py` (in full),
`services/market_history.py` (`recent_price`, `prune`,
`compute_hypothetical_trades`), `services/signal_log.py` (schema,
`resolved_signals_with_factors`), `services/whale_calibration/confidence_calibration.py`
(`_bucket_win_rates`, `_factor_report`), `services/stats_power.py`,
`services/candidate_log.py`, `services/paper_broker.py`, `services/market_lookup.py`,
`main.py` (line 301, `_maybe_prune_capture_stores`/`_maybe_check_signal_resolutions`),
`docs/kalshi/fee_rounding.md`, `docs/kalshi/get-series-list.md`,
`docs/kalshi/get-market-orderbook.md`, `ROADMAP.md`.

Two live-data checks that went beyond what the artifact itself did:

1. **`docs/kalshi/kalshi-fee-schedule.pdf` read directly, not trusted from
   the artifact's page-by-page description.** No PDF renderer (`pdftoppm`,
   `pdftotext`) or Python PDF library was available in this environment, and
   `pip`/`python3 -m pip` were both absent, so I wrote a from-scratch PDF
   parser: located the page tree via the trailer → `/Root` → `/Pages` chain
   (confirmed `/Count 12`, `/Kids` walked recursively to exactly 12 leaf
   `/Page` objects, resolving an initial false alarm where `file`/libmagic
   misreported "8 page(s)" by reading only one intermediate `/Pages` subtree
   node rather than the root), extracted each page's content stream,
   resolved inherited `/Resources`/`/Font` dictionaries, parsed each font's
   `/ToUnicode` CMap (`beginbfchar`/`beginbfrange`), and decoded the CID hex
   strings in every `Tj`/`TJ` operator into real text. This produced full,
   independently-obtained text for all 12 pages, cross-checked against the
   artifact's claims (Finding 2).
2. **`config/settings.yaml` read from the actual primary-checkout repo root
   (`/home/davidf/code/portfolio/showcase-projects/autotrade/config/settings.yaml`),
   not only the worktree's committed copy**, per the task's explicit
   instruction and because a worktree's working tree cannot reflect another
   checkout's uncommitted edits. `git diff config/settings.yaml` in the
   primary repo, `git status`, and file `mtime` were all checked (Finding 7).

Not done (documented, not silently skipped): did not probe the live
`ddev` app (`GET /api/health/pipeline` etc.) — nothing in the artifact's
load-bearing claims depended on current live telemetry rather than source/
config/docs, so this was judged unnecessary rather than skipped for
convenience. Did not attempt to verify the design author's un-verified
`fee_type == "flat"` live-series-count admission (§1.3 point 3 /
self-review) — the artifact already labels this correctly as an unverified
assumption, so there is nothing to falsify, only to note it's honestly
flagged.

---

## Findings

### 1. CONFIRMED — `market_analyst_agent` wiring characterization (§0)

Every sub-claim checked and reproduced exactly:

- `services/strategy_engine.py`'s `evaluate()` method spans lines 285–684
  (the artifact says 285–683, off by one at the boundary with the next
  method `_skip` at 685 — immaterial). `grep -c analyst` over that span
  returns zero. The only "market_analyst" hit in the whole file is line 63,
  inside `kelly_scaled_max_size`'s docstring: `"...same 'ships fully built,
  opt-in' precedent as every other optional engine in this app
  (advisory.enabled, market_analyst.enabled, confidence_calibration.
  enabled, ...)"` — prose, not executable code. Confirmed via
  `grep -n market_analyst services/strategy_engine.py` returning exactly
  one line.
- `services/whalewatchers/kalshi_trade_tape.py:200-211` defines
  `_analyst_factor(ticker, side)`, whose body (line 211) calls
  `market_analyst_agent.analyst_lean(ticker, max_age_sec=_ANALYST_FRESHNESS_SEC)`.
  It is called at line 725, unconditionally, inside `_process_trades_sync`
  (function starts line 499) — i.e., on every real trade-tape print, not
  behind any config gate.
- `services/confidence_scoring.py:132`: `DEFAULT_WEIGHTS = {..., "analyst_factor":
  0.13, ...}` — exact line and value match. Its factor-8 docstring
  (lines 299–312) reads: `"...neutral is the overwhelmingly common case, not
  an edge case."` (artifact cites line 311 for this phrase; the phrase
  itself lands one line later at 312 — trivial off-by-one, not a
  misquote).
- `services/exits/exit_engine.py:501`: `w_analyst = strat_cfg.get("auto_exit_analyst_weight",
  0.5)`; lines 570–578 read the same `analyst_lean()` via a per-tick cache
  and set `factors["analyst_divergence"] = (analyst_factor, w_analyst)`
  (artifact cites 571–578 — matches within one line at the top boundary).
- `services/analytics/routes.py:178-186`: the `/api/market-analyst/analyze`
  route's comment reads verbatim: `"On-demand trigger, direct request
  (2026-08-09) - replaces the earlier automatic per-tick background scan
  ... A human clicks 'Analyze' on one specific market they're actually
  looking at; nothing runs on a schedule anymore."` — exact quote match.
- `_ANALYST_FRESHNESS_SEC = 24 * 3600` is independently defined in both
  `kalshi_trade_tape.py:101` and `exit_engine.py:26` — both 24h, confirmed.
- Live config values (checked against the **worktree's** committed
  `config/settings.yaml`, and separately against the **primary repo's**
  live file — see Finding 7 for why these differ in line number but not in
  value): `whale_confidence_weights.analyst_factor: 0.0`,
  `strategy.auto_exit_analyst_weight: 0.5`, `strategy.auto_exit_enabled:
  true`, `strategy.auto_exit_pnl_weight: 0.85`, `strategy.auto_exit_sentiment_weight:
  1.5` — every one of these values matches the artifact's claims exactly.

**Verdict on this claim: fully CONFIRMED.** This is the single most
consequential and most carefully-sourced claim in the document, and it
holds up completely under independent re-derivation. The "structurally
wired on both sides, functionally near-zero-influence on both, for two
different and independent reasons" characterization is accurate and is a
genuine improvement on both the first audit's "still advisory-only" and a
naive "wired into decisions" reading.

### 2. CONFIRMED (with three sub-issues) — Kalshi fee-documentation gap (§1.2/§1.3)

**Core claim, independently re-derived, not trusted from the artifact:**
`services/kalshi_fees.py` (read in full) implements only
`math.ceil(raw * 1_000_000) / 1_000_000` (line 254) — the trade-fee
ceiling component. No rounding-fee or rebate arithmetic exists anywhere in
the 388-line module. `docs/kalshi/fee_rounding.md` (read in full) states
`"Net fee = trade fee + rounding fee - rebate (always >= $0.00)"` — three
components, of which this app implements one. **CONFIRMED: `f(P)` as
implemented is a lower bound on net fee, not net fee itself.**

**The "Specific Trading Fees Table" absence claim — independently
re-verified from the raw PDF, not from the artifact's description.** I
wrote a from-scratch PDF text extractor (see Method) because no rendering
tool was available, and extracted genuine text for all 12 pages of
`docs/kalshi/kalshi-fee-schedule.pdf`. Result:

| Page(s) | Actual extracted content |
|---|---|
| 1 | Blank/cover (4 bytes of whitespace) |
| 2 | "Trading Fees" — states the `fees = round up(M x 0.07 x C x P x (1-P))` formula and the maker formula |
| 3 | Settlement/Membership/ACH/Wire/Debit/Crypto deposit-withdrawal fees — unrelated to trading-fee tables |
| **4–5** | **"General Trading Fees Table (See below for fees on specific markets)"** — a price→fee lookup table, values consistent with the 0.07·P·(1−P) formula (e.g. $0.50 → $0.02/contract, $1.75/100 contracts = 0.07×100×0.5×0.5 exactly) |
| **6–11** | **"Non-Standard Fees" — Series / Maker Multiplier / Taker Multiplier table**, one row per series ticker (KXAAAGASM…KXWTAMATCH), continuous across the six pages |
| **12** | **"Perpetual Futures Fees" — Table 1 (Exchange Taker Fee Schedule) and Table 2 (Exchange Maker Fee Schedule)**, both bps-tiered by 30-day trailing volume |

A full-text search across all 12 extracted pages for "specific" (case
sensitive on "Specific") returns exactly two hits: "apart from **specific**
products listed below" (page 2) and "(See below for fees on **specific**
markets)" (page 4) — neither is the phrase "Specific Trading Fees Table".
**CONFIRMED: the `flat` FeeType's own referenced "Specific Trading Fees
Table" does not appear anywhere in this 12-page mirrored document.** This
is a real, previously-unnoted documentation gap, exactly as the artifact
claims — Kalshi's own `get-series-list.md` schema promises a table by that
name and the mirrored fee-schedule PDF does not contain it under that
name or any recognizable synonym.

`kalshi_fees.py`'s own docstring (lines 98-102) reads verbatim: `"`flat`
(the fourth FeeType value, "Specific Trading Fees Table") isn't modeled
here - no market/event in this app's own data has ever resolved to it, and
inventing its formula from the enum name alone would be exactly the guess
CLAUDE.md's "never guess" rule forbids; revisit if one ever does."` —
matches the artifact's quote.

**Sub-issue 2a — FALSIFIED (citation only).** The artifact cites
`docs/kalshi/get-series-list.md:172-183` for the FeeType schema's prose
("'quadratic' is described by the General Trading Fees Table... 'flat' is
described by the Specific Trading Fees Table"). Lines 172–183 of that file
actually contain the `settlement_sources`/`contract_url` schema fields —
unrelated content. The real text is at lines 198–208 (prose) and 260–271
(the `FeeType` enum block itself). The substance of the claim is still
correct (confirmed independently at the correct lines), but the specific
line-number citation is wrong by roughly 26–88 lines, which matters under
this repo's "never guess; verify or falsify" HARD RULE's emphasis on
file:line precision.

**Sub-issue 2b — FALSIFIED (fabricated date).** §1.3 point 3 states: `"As
of 2026-08-16's live sample this affects an unknown, currently-unmeasured
number of series (kalshi_fees.py's own docstring: 'no market/event in this
app's own data has ever resolved to it' as of that check)"`. I grepped
every date mentioned in `kalshi_fees.py`'s docstring (`2026-08-09,
2026-08-14, 2026-08-15, 2026-08-30`) — **there is no 2026-08-16 anywhere in
the file.** The nearest related date is 2026-08-15, attached to a
*different* fact (the live per-series multiplier sample across 13,029
series), not to the "no market/event has ever resolved to `flat`" claim,
which is stated undated in the docstring. This looks like a
conflation/misremembering of the 2026-08-15 date, not a real citation.

**Sub-issue 2c — OVERSTATED (arithmetic imprecision, dimensional-analysis
relevant).** §1.3 point 2 states `fee_rounding.md`'s worked example "shows
the rounding-fee component landing at **roughly a quarter** of the trade
fee's own magnitude." The worked example itself: trade fee = $0.003639,
rounding fee = $0.001361. $0.001361 / $0.003639 = **0.374, i.e. roughly a
third, not a quarter.** This doesn't change the substantive point (the
rounding-fee gap is a real, non-negligible fraction of the modeled fee,
which the design's buffer mechanism accounts for either way), but under
CLAUDE.md's dimensional-analysis HARD RULE ("any arithmetic... gets a pass
before being trusted"), a stated ratio should match the numbers it's
computed from.

### 3. CONFIRMED — `_validate_entry_price` shared-gate placement and the "four-entry gate bypass" bug (§3.3)

`services/strategy_engine.py:149-165`'s `_validate_entry_price` docstring
reads verbatim: `"shared by evaluate()'s market-order path and
check_pending_fills()'s limit-fill path (via
FollowTheWhaleStrategy.validate_pending_fill) so a resting order that
fills at a moved price is held to the same bar a fresh signal at that
price would be. Root-caused the still-open 'four-entry gate bypass'
ROADMAP item: four real entries at unit costs 0.97, 1.00, 0.20, 0.97, one
of them at conf 0.25 against a 0.495 threshold - check_pending_fills
previously called open_position() with none of this re-checked at all,
only whatever the order looked like at placement time."` — this is an
exact match to the artifact's claim, including all four specific unit-cost
numbers. `ROADMAP.md:291` confirms `[x] **Four-entry gate bypass**,
root-caused 2026-08-22` — checked off, i.e. fixed, resolving the
docstring's "still-open" phrasing (written before the ROADMAP was
subsequently marked done, or referring to the ROADMAP item being open
until this exact fix).

**Additionally independently re-verified (not merely spot-checked): the
full 13-step gate order §3.3 lists for `evaluate()`.** I read the function
body and matched every step to its actual line: (1) `check_daily_loss` at
line 370, (2) already-resolved-market skip at 375, (3) `live_markets_only`
at 377, (4) `close_window_sec` upper bound at ~382-408, (5)
`min_seconds_to_close` lower bound at ~431-445, (6) the
`mutually_exclusive`/`can_close_early`/`collateral_return_type` special-
market gate at ~457-485, (7) `excluded_series` at 508-509, (8)
`_validate_entry_price` call at 579, (9) `max_open_positions_per_series`
at 597-599, (10) cooldown (`can_trade`) at 606-607, (11) sizing
(`max_trade_size`/`kelly_scaled_max_size`) at 609-618, (12) `contracts <=
0` at 626, (13) limit-vs-market execution at 648-663. **Every step matches
the artifact's description exactly, in order.** This is the load-bearing
justification for the design's placement decision (inside
`_validate_entry_price`, after the price-band checks) and it holds up
completely.

### 4. CONFIRMED — `Δ_calibrated`'s "twice-repeated" precedent (§2.3)

`docs/prediction-market-strategy-alignment-plan.md:239-246` reads verbatim:
`"Both advisory_engine.py and confidence_calibration.py independently
concluded there's nowhere near enough resolved real-signal data to fit
anything trustworthy (calibration's own gate gets checked against
min_resolved_signals: 50; this app has had stretches with single-digit
resolved real signals)."` — an exact quote match to the artifact's
citation. The artifact's framing ("this codebase's own twice-repeated...
precedent") is an accurate paraphrase of "independently concluded... twice"
(two modules, `advisory_engine.py` and `confidence_calibration.py`).

### 5. CONFIRMED — Markout eager-sweep justification (168h vs. 32-day mismatch) (§4.1)

Live config, checked in both the worktree's committed file and the primary
repo's live file (values identical in both, see Finding 7 on why line
numbers differ): `market_history: retention_hours: 168` with the comment
`"7d - momentum()/volatility()/compute_hypothetical_trades()'s longest
lookback is 86400s/24h... Added 2026-08-30 to close the one retention gap
found in data/*.db"`; `strategy: close_window_sec: 2764800` — 2,764,800 /
86,400 = exactly 32 days. `services/market_history.py:357`'s `prune(
retention_hours: float = 168.0, ...)` default matches the config exactly.
`recent_price` (line 306) and `compute_hypothetical_trades` (line 403)
both exist and match the artifact's characterization of their mechanics
almost verbatim (`"Most recent snapshot's yes_price, or None if there
isn't one within max_age_sec"`; `"Closest snapshot to the target time,
preferring one at or before it"`). **Both numbers behind the "168h
retention < 32-day execution window" argument are confirmed exactly, and
the argument itself is sound: a 7-day rolling prune genuinely cannot
support a `t+close` markout on a market that closes up to 32 days out.**

### 6. CONFIRMED, with a genuine internal-consistency gap — config-defaults-safe claim (§5)

`services/strategy_engine.py:55-59`'s docstring on `kelly_fraction_of_cap`
reads verbatim: `"At 0.0 (the default) this returns max_size unchanged -
nothing about existing behavior changes unless deliberately turned on"` —
exact match, confirming the cited precedent is real.

**However:** §5 states "Every field defaults to a value that changes
nothing about current behavior until deliberately turned on" as a blanket
claim about this design's new config surface. This is true for all 8
listed `edge_gate_*` fields (all gated behind `edge_gate_enabled: false`).
It is **not** true of the new markout-capture sweep the design introduces
in §4.1/§4.2, which is not gated by any of the 8 new fields at all. The
design's own §8 Risks section says so directly: `"the new markouts table
and signal_log/candidate_log extensions are additive and harmless to
leave in place even with the gate off (they cost one scheduled sweep's
worth of SQLite writes, §4.1's own interval)"` — i.e., the sweep runs
unconditionally, on its own interval, from the moment this ships,
regardless of `edge_gate_enabled`. That's a defensible design choice (you
need markout data to ever evaluate turning the gate on), but it is a real,
new, always-on background task with periodic SQLite writes — not the "zero
behavior change until deliberately turned on" pattern §5 invokes the
`kelly_fraction_of_cap` precedent to justify. The two claims sit in mild
tension: §5's blanket framing and §8's more careful, accurate disclosure
say different things about the same feature, and only §8's is correct.

Separately: §3.3 explicitly flags `_validate_entry_price`'s new DB reads
as needing runtime-cost measurement before shipping (citing the data-plane
HARD RULE). The new markout-capture sweep is exactly the same category of
new, unconditional runtime cost, and is not given the same explicit
measurement callout anywhere in the document.

### 7. GAP — `config/settings.yaml` citations are stale against the live PRIMARY checkout, not just "if time has passed" (§0)

The task instructions required checking the *live* `config/settings.yaml`
at the repo root, not a worktree copy, and this surfaced a finding the
artifact's own self-review anticipated as a hypothetical risk but that
turned out to already be real:

- `git status --short config/settings.yaml` in the **primary** repo shows
  `M config/settings.yaml` — an uncommitted, in-progress edit.
  `git diff` shows it removes the 24-line "2026-08-30 factor-by-factor
  audit" comment block (HEAD lines 182–205) that §0 cites at
  `config/settings.yaml:181-205` as evidence `analyst_factor: 0.0` is "a
  deliberate override, not an oversight... pending a human decision," and
  also changes `kalshi.markets_watchlist_mode`, `kalshi.max_children_per_parent`,
  and `kalshi.categories`. This is the **same** uncommitted change the
  second-pass research doc's own §4.4 already documents and names "the
  third data-wipe of the same shape... sitting uncommitted in the working
  tree" (the same comment PR #389 restored once already, on 2026-09-01,
  after a prior wipe).
- Because this edit removes lines, every subsequent line number in the
  live primary file has shifted relative to the worktree's git-tracked
  copy: `auto_exit_enabled` is worktree-line-92 / primary-line-83;
  `auto_exit_analyst_weight` is worktree-97 / primary-88; `analyst_factor:
  0.0` is worktree-214 / primary-181 (**and the comment block the artifact
  cites at that location is currently absent from the primary file
  entirely**); `market_history.retention_hours: 168` is worktree-324 /
  primary-291.
- **The underlying values are unaffected** — I spot-checked every value
  the artifact cites (`analyst_factor: 0.0`, `auto_exit_analyst_weight:
  0.5`, `auto_exit_enabled: true`, `auto_exit_pnl_weight: 0.85`,
  `auto_exit_sentiment_weight: 1.5`, `close_window_sec: 2764800`,
  `market_history.retention_hours: 168`) against the live primary file and
  every one matches exactly. **§0's substantive claim is not falsified.**
- What's genuinely wrong: the specific `:N` line citations throughout §0,
  and the more consequential fact that the comment block §0 leans on as
  *evidence* for "deliberate, not an oversight" is not currently visible
  in the live file a reader would actually open today — only in git
  history/the worktree's checkout.
- Chronology check: the primary file's `mtime` (2026-09-02 15:48:07) is
  *before* the design's own commit timestamp (2026-09-03 00:10:45) — so
  this divergence between the worktree checkout the design was researched
  against and the live primary file was **already present at authoring
  time**, not something that drifted in afterward. The artifact's own
  self-review already flagged this exact risk category ("these are this
  worktree's checkout at the time of writing, not a fetched-fresh
  confirmation against a shared branch — worth re-checking at
  adversarial-review time if any time has passed") — that caution was
  well-founded, and the risk it named turned out to already be realized,
  not merely hypothetical.

This should be corrected before the design proceeds to a plan: re-cite
`config/settings.yaml` line numbers against the actual live file (or drop
line numbers in favor of key names, which don't drift), and note
explicitly that the analyst-factor justification comment is presently
missing from the live config due to an already-tracked, unresolved issue.

### 8. CONFIRMED — two required design-alternative comparisons present and substantive

§2.3 (Alternative A: bucketed-mean extension of existing calibration
machinery vs. Alternative B: a fitted calibration model) and §4.1
(Alternative 1: lazy/query-time markouts vs. Alternative 2: eager/scheduled
sweep) are both genuine comparisons on mechanism, correctness, and
complexity, each resolved on a specific, checkable piece of evidence (the
Finding-4 precedent; the Finding-5 retention-window numbers) rather than
generic engineering taste. No strawman option was included in either
comparison — both alternatives are real, currently-viable approaches, and
the rejected one in each case is rejected on stated, falsifiable grounds.

### 9. CONFIRMED (as an accurate pass-through) — predictionmarketspicks "dead zone" figure

`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md:1211`
reads verbatim: `"a 'dead zone' (~3–5pp fee band around fair value) below
which a signal is not actionable at all"` — matches the design's "roughly
3–5 percentage points" citation exactly. This is correctly sourced through
the audit (which itself sources it to `predictionmarketspicks.com/tools/guide`)
rather than claimed as independently re-verified against the live site —
appropriately labeled.

### 10. CONFIRMED — Program 1-2 / paper-mode safety boundary respected

No code, config, or data changed (verified: `git status` in the worktree
shows only the design doc and this review as untracked/new). Non-goals
explicitly exclude `kalshi_account.trading_enabled`, the daily-loss kill
switch, and any live-trading gate. The Validation plan (§6) states "No
part of this validates against real money" and the Risks section (§8)
repeats the same boundary. Nothing in the document proposes or implies
touching a safety gate.

### 11. CONFIRMED — core EV/edge dimensional analysis is sound

The formula in §1.1 mixes a `[prob]`-dimensioned term (`p_est`) with
`[$/contract]`-dimensioned terms (`ask_now`, `f(ask_now)`) and explicitly
states why that's valid rather than a bug: a Kalshi contract pays exactly
$1 or $0, so price and probability are numerically interchangeable by
construction. This is independently confirmed by `kalshi_fees.py::unit_cost`'s
own docstring (`"The result is also the market-implied probability of
side... which is why breakeven_unit_cost() below can add a fee to it
directly"`) and by `docs/kalshi/get-market-orderbook.md`'s own text on the
yes/no price-complement identity (`"a bid for yes at price X is equivalent
to an ask for no at price (100-X)"`), both read directly. The only
concrete arithmetic imprecision found anywhere in the document is Finding
2c (the "roughly a quarter" ratio).

---

## Verdict: **GO-AFTER-FIXES**

This is an unusually well-sourced design document. Every one of the six
headline claims specified for this review is **substantively confirmed**
against primary sources — including the two most consequential and most
checkable ones: the `market_analyst_agent` wiring characterization
(Finding 1) and the fee-documentation gap, specifically the "Specific
Trading Fees Table" absence, which I independently re-derived from the raw
PDF bytes rather than trusting either the artifact's description or a
rendering tool (Finding 2). The gate-placement claim (Finding 3) and the
markout retention-window arithmetic (Finding 5) both checked out to the
letter, including a full independent line-by-line re-derivation of
`evaluate()`'s 13-step gate order. Two genuine design-alternative
comparisons exist and are substantive (Finding 8). No safety boundary is
crossed (Finding 10). The design's own self-review is honest about its
real limitations and correctly anticipated the one area (config-citation
staleness) that turned out to actually be wrong (Finding 7) — that's a
point in favor of the process, even though the underlying issue still
needs fixing before this becomes a plan.

Nothing found here undermines the design's core reasoning, its recommended
approach, or its architecture. The issues are all citation-precision,
staleness, or internal-consistency problems that a revision pass can fix
without re-deriving the document.

### Must-fix (block advancing to plan stage until addressed)

1. **Finding 2a**: Correct or remove the `docs/kalshi/get-series-list.md:172-183`
   line citation — the FeeType schema text is actually at lines ~198-208
   (prose) and 260-271 (enum). The claim itself is right; the line numbers
   are not.
2. **Finding 2b**: Remove or correct the fabricated "2026-08-16" date
   attached to the `flat`-fee-type live-sample claim in §1.3 point 3 — no
   such date exists in `kalshi_fees.py`. Either cite the real 2026-08-15
   date if that's the intended reference (confirm it's the same fact
   first) or state the claim is undated in the primary source.
3. **Finding 7**: Re-verify every `config/settings.yaml` line-number
   citation in §0 against the actual live primary-repo file (not the
   worktree's committed copy), and add an explicit note that the
   "2026-08-30 factor audit" comment justifying `analyst_factor: 0.0` is
   currently absent from the live file due to an already-tracked,
   unresolved config-wipe issue (second-pass audit §4.4) — so a reader of
   the live app today would not find the documented justification this
   design leans on. Consider citing config keys without line numbers
   generally, since this file is demonstrably unstable under uncommitted
   UI-driven edits.
4. **Finding 6**: Resolve the tension between §5's blanket "every field
   defaults to a value that changes nothing... until deliberately turned
   on" framing and §8's (correct) disclosure that the markout-capture
   sweep runs unconditionally regardless of `edge_gate_enabled`. Either
   gate markout capture behind its own explicit flag, or state plainly in
   §5 (not only as a Risks-section aside) that it is a deliberate
   exception to the opt-in pattern and why. Extend §3.3's "measured for
   runtime cost before it ships" note to cover the new scheduled sweep,
   not only `_validate_entry_price`'s new DB reads.

### Should-fix (not blocking, worth cleaning up)

1. **Finding 2c**: Correct "roughly a quarter" to "roughly a third" (the
   actual ratio from `fee_rounding.md`'s own worked example is ~37.4%,
   $0.001361/$0.003639).
2. Revisit whether `edge_gate_fee_buffer_usd: 0.005` (~3.7× the worked
   example's $0.001361 rounding-fee component) is still fairly described
   as "on the same order" — defensible but worth tightening once real
   net-fee data exists, which §6 already earmarks for revisit anyway.
3. Fix the minor off-by-one line citations noted in Finding 1
   (`confidence_scoring.py:311` vs. 312; `evaluate()` "285-683" vs. the
   actual 285-684) and Finding 1's `exit_engine.py:571-578` vs. the actual
   ~570-578 — cosmetic, but this document holds itself to file:line
   precision elsewhere, so it's worth being consistent.

No finding here rises to NO-GO: nothing falsifies a headline claim, no
safety invariant is at risk, and every issue found is a citation or
internal-consistency fix rather than a flaw in the design's reasoning or
recommendations.
