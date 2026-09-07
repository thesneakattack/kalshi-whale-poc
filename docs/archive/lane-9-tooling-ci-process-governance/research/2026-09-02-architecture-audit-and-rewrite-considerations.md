# Comprehensive architecture audit — 2026-09-02

Direct request, overnight/autonomous session: a full audit of whether this
app has "gone off the rails" architecturally — DRY, hand-rolled-vs-framework,
frontend framework choice, caching, SQLite fitness, service decoupling,
polling load, comparison against real auto-trading systems, comparison
against `predictionmarketspicks.com`'s tooling/methodology, and a direct
verdict on whether the app targets edge, mispricing, confidence, or size.
Explicitly scoped as **pre-brainstorming research** — this document makes no
code changes and decides nothing; it is the input to a future
`superpowers:brainstorming` → design → plan cycle, per this project's
"nothing advances on one pass" standing rule. Directive, verbatim: "this may
end up becoming a comprehensive rewrite of the application entirely" — that
question is addressed head-on in §12, with a specific, evidence-backed
answer that is *not* "yes."

**Second pass (2026-09-02, later the same day):** a follow-up pass over
this document — re-deriving its conclusions under the rule set as it
stands after PR #429 and PR #436, re-measuring the live app, and adding
what happened after this document's monitor window — is
`docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-second-pass.md`.
Read it alongside this one: it corrects Tier-1 item 3 and §5.2's
`raw_trades` finding (a lifetime fault row misread as 24 h — the last
occurrence was 2026-08-30), separates the event-loop stalls from the
dashboard-polling timeouts, answers the browser-tab question §6.2 left
open (a tab was open, background-throttled), and records a 6.8-hour
file-descriptor-exhaustion data-loss incident that began at 08:24 UTC,
twenty minutes after this document's monitor ended. This document is
otherwise left as merged.

**Post-merge note (2026-09-02, added the same day, after this document's
own review cycle completed and it was merged as PR #430):** while this
audit's research and review cycle were in progress, the repository owner
merged a separate, unrelated PR (#429, `docs: drop additive-schema,
one-DB-per-concern, and tooling-separation rules`, merged 2026-09-02T08:35Z)
removing three `CLAUDE.md` rules this document cites by name in a few
places: the "schema changes are additive only" mandate (§9.1's DDL-
duplication finding; the underlying engineering concern — silent schema
divergence across duplicate `CREATE TABLE` blocks — stands on its own
merits independent of whether it's mandated policy), and the "one SQLite
file per concern, no shared DB, no ORM" persistence-idiom bullet (§5.1,
§5.2, §11, §12 all reference it as "the" architectural rule this app
follows — it remains an accurate description of the *current code*, but
it is no longer a *mandated* constraint on future design, which is
directly relevant to §5's own recommendations and is flagged as an open
item in §14 rather than silently assumed unchanged). Nothing else in this
document is affected — no other finding, measurement, or recommendation
depends on either removed rule. This note is a factual correction to keep
the document accurate against a same-day external change, not a new
analysis pass; per this project's own process, a mechanical correction of
this kind doesn't require re-running the full review cycle, and it
doesn't change §12's verdict (which was never conditioned on this
specific pair of rules existing).

**Method.** A 15-minute live read-only monitor (24 samples, 20s apart)
against the app's own diagnostic endpoints, run in parallel with four
independent research agents (each opus-model, one on the newly available
Fable 5.1 model for the external-research/strategy track), each producing
its own scratch report before this document was written. This document
synthesizes all five streams plus firsthand live evidence gathered directly
(curl against `/api/*`, `python -m tools.quality_audit`, direct source
reads, `git worktree list`, GitHub issue census). Every claim below is
either a direct citation (`file:line`, a live measurement with timestamp)
or explicitly labeled as inference/assumption. Nothing here was accepted
from a subagent's summary without at least spot-checking the underlying
evidence against source or the live app.

**Scale context, for calibrating every recommendation below.** This is a
single-developer, ~40,000-line paper-trading hobby application (`services/`
35,875 lines, `frontend/src/js/` 6,369 lines, `main.py` 1,796 lines, 168
test files, 83 open GitHub issues, real CI). It already runs a genuinely
disciplined process — the "nothing advances on one pass" review cycle is
real and git-log-verifiable across dozens of PRs. The findings below should
be read as "a fast-moving solo project accumulated real, specific, fixable
debt under rapid iteration," not "this project lacks rigor" — the rigor is
present and demonstrably working; it just hasn't yet been pointed at its
own architecture end to end. That's what this document is.

---

## 1. Executive summary

**The headline finding, measured live tonight, not inferred:** across the
windows where this app's own 0.1s-resolution loop watchdog actually
recorded a stall over the trailing ~11 hours, the event loop was blocked a
**median ~52% of that window's wall time** (single-window sample, n=107;
p90 78.3%; the single worst recorded block was 100.8s). §2.1 states the
method and its limits explicitly — this is a real, severe, and current
finding, stated at the confidence level the underlying data actually
supports, not inflated by folding quiet windows into occupied ones.
`GET /api/quality/summary` — one of the six endpoints `CLAUDE.md` itself
names as where every investigation should start — took **≥6.5 seconds on
all 24 of 24 samples** in a fresh 15-minute monitor run tonight, and
**timed out client-side (no response within ~13s) on 9 of those 24
(37.5%)** — a pathologically slow route, not one returning errors (three
separate manual probes at a 60s budget all eventually returned 200).
This is not a historical incident being re-read from logs; it is the
app's current, ongoing, live condition as of 2026-09-02 08:00–08:15 UTC.
`docs/next-action.md`'s open item (issue #410, tick_executor pool
starvation) already names the mechanism; tonight's evidence shows it is
worse and more sustained than the single 4-minute window that item was
built from.

**Verdict on "has this gone off the rails": partially, and in a specific,
identifiable, fixable place — not everywhere, and not in the way "full
rewrite" implies.** Four independent research streams plus this session's
own live probing converge on the same shape (each stream's findings were
independently re-derived from primary sources by a separate adversarial
review before this document was finalized — see the companion
consolidation document, `2026-09-02-architecture-audit-consolidation.md`,
for the full review-cycle record):

- The **overall architecture** — single FastAPI/asyncio process, one
  SQLite file per concern, WAL mode, no ORM — is validated as proportionate
  for this project's scale by direct comparison against Freqtrade,
  Hummingbot, and NautilusTrader (§10). It is not the problem.
- The **actual defect is concentrated and well-understood**: too many
  reporting/diagnostic HTTP routes share the same small thread pool and
  event loop as trading-critical writes, and the frontend polls those
  routes on a fixed 6-second timer regardless of cost, so both problems
  compound (§2, §4, §6).
- **DRY duplication is real but narrow**, not systemic — concentrated in
  the config-apply/advisory-suggestion path, where it has already produced
  a real behavioral bug (the unsupervised auto-apply path silently ignores
  human declines) and a real safety asymmetry (the shadow kill-switch has a
  divide-by-zero guard the real one lacks) (§9).
- **Most hand-rolled code is correctly hand-rolled** for this app's actual
  constraints — the rate limiter, the caches, the fault log, the scheduler
  — and swapping several of them for "real" libraries (Redis, APScheduler,
  OpenTelemetry) would add operational cost this single-developer,
  no-deployment-target project cannot currently justify (§8).
- **The frontend genuinely does need a framework** — and that decision was
  already made 8 days ago (2026-08-25) and never executed (§8, §11).
- **The trading strategy has a real, specific, well-evidenced gap**: it
  scores confidence and sizes on confidence, but never compares its belief
  to the price it is paying. Edge, not confidence or size alone, is the
  missing ingredient, and it is addable using data the app already
  collects (§3).

**Top 5 priorities, in the order the evidence supports (detail in §13):**

1. Stop polling the three slow diagnostic/report routes on their fixed
   6s dashboard timers (they cost 4.5–48.6s each). §6.2 corrects an earlier
   draft's framing: these three routes are actually split across two
   mutually exclusive dashboard tabs, not one combined poll — but each one
   is independently a real, currently-measured cost, and de-polling all
   three is still the cheapest, highest-confidence fix on this list. Note
   this document does **not** have direct evidence a browser tab was open
   during tonight's 15-minute curl-based monitor window, so "this is *the*
   dominant cause of tonight's specific numbers" is a strong, falsifiable
   hypothesis, not a confirmed mechanism (§6.2 names the falsifier); the
   routes' cost and their live-reported role in issue #410's *previously
   reported* incident are independently confirmed regardless.
2. Fix the three real safety-adjacent findings the DRY audit surfaced:
   auto-apply silently skipping declined suggestions, `RiskManager`
   missing the zero-bankroll guard `ShadowTrader` already has, and the
   duplicated table DDL that conflicts with the additive-schema rule
   against live multi-gigabyte `.db` files.
3. Finish the `aiosqlite` migration and delete `tick_executor` /
   `_scoring_pool` as call sites drain onto it — this is subtraction, not a
   new library, and directly addresses the pool-contention root cause.
4. Execute the already-designed, already-approved, never-started Preact
   frontend migration (`docs/archive/lane-8-frontend-dashboard/plans/2026-08-25-frontend-modularization.md`)
   — it is not a new decision, it is 8-day-old unstarted work.
5. Add a pre-whale-price anchor and an explicit EV gate (`p_est − price −
   fee > threshold`) to the entry path — the single highest-leverage
   strategy change, using data already collected, addressing the
   already-tracked 0.60–0.95 negative-EV band from the *cause* side instead
   of a band-ban.

**None of the five priorities above requires a rewrite, a new database
engine as the default, a message bus, or a multi-service split with
network calls between them.** The one process-topology change that *is*
recommended — a second read-only uvicorn process for app-facing routes —
is proportionate specifically because SQLite's WAL mode already gives
concurrent-reader safety for free, and that mechanism itself is cheap and
already proven (§4.4). The route-by-route verification that each moved
route reads only from DB/config, never in-memory `state`, is real,
separate, non-trivial work on top of that — §4.4 and §14 are explicit that
this is not included in "cheap." §12 makes the full case for why "rewrite"
is not the right frame, while preserving every consideration the user
asked to keep open for future research.

---

## 2. Live evidence — the app's current, measured condition (2026-09-02)

Gathered directly this session (curl against the running ddev instance,
`python -m tools.quality_audit`, direct source reads) plus the dedicated
15-minute monitor (24 read-only polls, 20s apart, against 6 endpoints,
07:49–08:04 UTC). This section is the "run diagnostics" leg of the audit —
everything past this point interprets what's here.

### 2.1 Event loop health — the single most important number in this report

`GET /api/health/faults?component=loop_watchdog&hours=24&limit=400` — 183
`event_loop_stall` fault rows over the trailing 10.81 hours (each row is
one `observability.maybe_capture()` window; `reset_window()` fires every
~60s capture unconditionally, but a `fault_log` row is only written when
`stall_count > 0`, so a quiet window between two occupied ones leaves no
row of its own and is silently absorbed into the next occupied row's
inter-row gap — median gap 89.9s, but 50% of gaps exceed 90s and one
outlier gap is 4,534s/75.6min carrying only 35 samples, almost certainly a
process restart rather than one real window). **Method, stated honestly:**
"blocked % of window" is only a valid measure for a gap that really is one
~60s window — restricting to gaps of 55–95s (n=107, single-window,
no-restart cases) gives the number this document treats as the headline:

| Metric | Value (single-window subset, n=107) |
|---|---|
| Median % of window blocked | **~52%** (51.6%) |
| Mean / p90 % blocked | 54.6% / 78.3% |
| Median worst single block (all 183 rows) | 15,845 ms |
| Mean / max worst single block (all 183 rows) | 20,429 ms / **100,757 ms** |

The unrestricted, all-183-rows figure (naively treating each inter-row gap
as its own window) computes to a median of ~69.5% — that number is real
arithmetic on the raw data, but it overstates the finding by folding
multi-window quiet gaps and at least one likely restart into the
denominator, so it is **not** used as this document's headline number.
Either way the underlying condition is severe and current: even the
conservative, methodologically-defensible 52% median means the event loop
spends roughly half its time, in the windows that do stall, unable to
service a 0.1s-resolution watchdog wakeup.

Corroborating, live right now: `GET /api/health/pipeline` shows
`last_tick_duration_sec: 25.69` against a 30s safety-net interval (this
single field is volatile — it read 0.0 on a different probe minutes
later, so treat it as one sample, not a stable baseline); WS `queue_wait`
— which measures the gap between a message being enqueued and dequeued,
**not** the time to the end of its handler — read a *lifetime cumulative*
average of 5.24s (max 194.19s, n=90,761 messages since process start) but
a **current-window** average of only 0.33s (n=9,309) at the same moment.
The lifetime figure is the wrong one to read as "right now" — it is the
same window-vs-lifetime conflation this document's own §8.3 identifies as
having already caused a real 30× measurement bug elsewhere in this
codebase. The current-window figure (0.33s enqueue-to-dequeue) is still a
real, non-zero cost worth watching, just not the "5 seconds socket-to-
handler" figure an earlier draft of this document claimed.

### 2.2 `/api/quality/summary` — reproduced 24/24 times tonight, not a one-off

15-minute dedicated monitor, one call every 20s, no synthetic load:

| Endpoint | n | 200 OK | timeout/error | min | avg | max |
|---|---|---|---|---|---|---|
| `/api/state` | 24 | 24 | 0 | 0.84s | 2.36s | 9.42s |
| `/api/health/pipeline` | 24 | 24 | 0 | 0.10s | 1.45s | 4.78s |
| `/api/health/faults` | 24 | 24 | 0 | 0.03s | 0.77s | 4.55s |
| **`/api/quality/summary`** | 24 | 15 | **9 (37.5%)** | **6.51s** | **10.92s** | 12.94s |
| `/api/observability/summary` | 24 | 23 | 1 | 0.55s | 1.78s | 11.55s |
| `/api/health/storage` | 24 | 24 | 0 | 0.04s | 0.27s | 1.34s |

**Every single one of the 24 quality/summary calls took ≥6.5s; not one
completed in under 6 seconds.** The "timeout/error" column is a
**client-side** timeout at the monitor script's own ~12–13s budget, not a
server error — the route is pathologically slow, not broken: three
separate manual probes at a generous 60s budget all eventually returned
`200`, at 36.95s, 24.17s, and 13.68s respectively, and the data-layer
research agent's independent measurement, taken at a different moment,
recorded 48.6s for the same route. A separate probe earlier the same
session (two sequential `curl -m 30` calls) both hit that 30s budget with
zero response — consistent with the same slow-not-broken pattern at a
tighter timeout. This is the same live mechanism `docs/next-action.md`
already names (issue #410) — tonight's evidence is that it is sustained
and reproducible on demand, in the 13–49 second range, not a rare
4-minute window.

### 2.3 Storage footprint — some real outliers

`GET /api/health/storage` + `/api/observability/summary`'s 24h size series
(92 samples):

| File | Size | 24h trend | Notes |
|---|---|---|---|
| `series_watcher.db` | **~27.2 GB** | flat | `raw_trades` (39.2M rows), never pruned by design |
| `candidate_log.db` | **~3.4–3.6 GB** | growing | `rejection_events` has no retention |
| `market_history.db` | ~1.1–1.2 GB | flat | |
| `game_state.db` | 122 MB → **885 MB** | **7.3× in 24h**, flagged by `/api/quality/summary`'s own `storage-growth` check | worth understanding before calling it expected |
| `index_feed.db` | ~464 MB | flat | |
| `observability.db` | ~285 MB | growing | the monitoring system's own footprint is non-trivial |

### 2.4 `/api/state` — 5.1MB every 5–6 seconds, per open tab

Confirmed by direct measurement and independently by the data-layer agent:
body size 5.13–5.38MB, of which **88% is one field (`event_live_data`)**.
The endpoint's own ETag/304 mechanism (`main.py:1637-1650`) is
**structurally unable to fire** under real trading conditions: three
consecutive conditional GETs with a valid `If-None-Match` all returned 200
with a new ETag each time, because `bump_generation()` fires on
essentially every processed WS message (measured ~3.7 bumps/second).
`ROADMAP.md`'s "Revisit the 5s dashboard polling model" item states ETag
"already makes an unchanged poll nearly free" — **that claim does not hold
under real live conditions and should be corrected, not repeated.** Cost:
≈855 KB/s (~6.8 Mbit/s) sustained per open browser tab, plus a full 5MB+
JSON serialization on the event loop every poll.

### 2.5 Process/tooling observations (context, not this audit's to fix)

`python -m tools.quality_audit` surfaced ~90 `[error/high]` findings, but
every one is inside `.claude/worktrees/{candlestick-volatility,
frontend-realtime-push,main-tools}/` — real, currently-live peer-session
worktrees (`git worktree list` confirmed), not stale garbage, and
explicitly out of scope for this session (per `docs/next-action.md`'s
"leave alone" list and 3 live peer sessions confirmed via `ListAgents`).
Noted because it's a real workflow-friction observation in its own right:
the tool scans every worktree indiscriminately, which means a human/session
running it has to manually filter peer-session noise every time — worth a
line in `docs/open-decisions.md` (see §14), not a finding about the app
itself.

83 open GitHub issues (67 claimable, 24 `type:feature`, 4 `type:bug`, 47
`type:plan-task`) — an actively-used backlog, evidence of process health
rather than neglect.

---

## 3. Is the app's strategic approach even good? Edge vs. mispricing vs. confidence vs. size

**Direct answer: target edge. Confidence is one input to it; size is a
consequence of it. A strategy that scores confidence and sizes on
confidence, but never compares its belief to the price it is paying, is
structurally incomplete — and this app's own already-tracked 0.60–0.95
negative-EV finding is the predicted symptom of exactly that gap.**

### 3.1 The arithmetic that settles it

For a YES contract at ask price `P` with Kalshi's taker fee
`f(P) = 0.07·P·(1−P)` — this app's own already-implemented, already-
verified formula (`services/kalshi_fees.py:8,132`,
`ceil_to_$0.000001(0.07 * contracts * price * (1 - price))`), which agrees
with the academic literature (Burgi, Deng & Whelan 2026,
https://www.karlwhelan.com/Papers/Kalshi.pdf — makers were fee-free in
their sample, taker-charged since) but is cited here from the app's own
code plus `docs/kalshi/` per this project's Kalshi-integration-authority
HARD RULE, not from the paper alone:

```
EV per contract = p_true − P − f(P)
```

Confidence lives inside `p_true`. Price and fees live outside it.
**Two caveats `docs/kalshi/` records that any real implementation of this
gate must account for, which this simplified formula does not:**
`docs/kalshi/CHEATSHEET.md:141-143` — `kalshi_fees.py` models only the
*trade* fee; Kalshi's real net fee is trade fee + rounding fee − rebate,
so `f(P)` above is a **lower bound**, not the net fee, and an EV gate
built on it alone will systematically over-admit marginal trades unless
that's accounted for. `CHEATSHEET.md:220-237` — the flat
`0.07·P·(1−P)` formula is not universal across every tradeable series;
`quadratic_with_combo_maker_fees` is a distinct real fee type on at least
some series. Any future implementation of §3.3's EV gate needs the real,
current fee definition per series from `docs/kalshi/`, not this
document's simplification.

- **A perfect signal at a fair price loses.** If the whale genuinely knows
  something and the market has already moved to `P = p_true`, EV = −f(P) <
  0. Certainty is not edge; *disagreement with the price* is edge.
- **A weak signal at a good price beats a strong signal at a bad one.**
  p_est=0.55 against a 40¢ ask is +13¢ gross; p_est=0.93 against a 92¢ ask
  is +1¢ gross, negative after fees. Confidence alone inverts the correct
  ranking here.
- Confidence's legitimate role is scaling the *variance* of `p_est`, which
  belongs in Kelly sizing (shrink the fraction as uncertainty grows) — not
  in the entry decision on its own.

### 3.2 Why whale-flow-as-confidence is specifically vulnerable to this

A whale print is public the instant it happens; the whale's own execution
already consumed the book and moved price toward their belief. A follower
buys at the **post-impact** price, as a **taker**, paying the fee, against
makers who watched the same print and already re-quoted. The tradable
question was never "was the whale informed?" — informed order flow moving
price is exactly what the efficient-market mechanism predicts — it is
"**is there residual mispricing left after the whale's own impact?**" That
is an edge question, and it is unanswerable without a price/fair-value
comparison.

**The market-wide evidence makes this diagnostic, not merely theoretical.**
Whelan et al.'s 300k+-contract Kalshi study finds the 0.60–0.95 price band
is *on average slightly positive-EV market-wide* ("for contracts above
70c, there is evidence of statistically significant, though small,
positive post-fee returns... Makers earn higher returns than Takers").
This app's own diagnostic measures that exact band as **negative**-EV for
its own entries. The gap between "market average: mildly positive" and
"this app's realized result: negative" is the signature of
**entry-price/adverse-selection cost** — taking post-impact asks as a
taker, in the region where each cent of overpay is a large fraction of the
remaining upside (at 90¢, 2¢ of chase + ~0.6¢ fee consumes ~26% of the 10¢
max profit). More confidence cannot fix this; only a price test can.

### 3.3 What "computing edge" concretely requires here — ordered by cost

The app has no independent pricing model today, and doesn't need a grand
one — it needs a `p_est` that isn't simply the current ask echoed back:

1. **Reframe the existing calibration output as a probability, anchored
   pre-impact.** Baseline `P_pre` = market price immediately before the
   whale print. Estimate `Δ = E[outcome] − P_pre` conditional on whale
   features (size, side, aggression, category, time-to-close) from the
   app's own settled history, which it already accumulates. Then `p_est =
   P_pre + Δ_calibrated`, and `edge = p_est − ask_now − f(ask_now)`. If the
   book has already moved past `p_est`, there is no trade **no matter how
   strong the signal was.** This converts the existing calibration
   machinery into an edge machine with data already on hand — the cheapest,
   highest-leverage change in this whole document.
2. **Add mechanical gates** (predictionmarketspicks.com's own pattern,
   §10.2): minimum edge ≥ fee + expected spread cost, plus a liquidity
   guard. This implements the tracked 0.60–0.95 finding as a *consequence*
   of the gate rather than a hard-coded band ban.
3. **Measure realized edge with markouts** (the crypto/equities CLV
   analog): for every entry, record market mid at t+5m/t+1h/t+close vs.
   entry price. Persistently negative markouts distinguish "bad signal"
   from "no edge left" — using data already streamed, no model required.
   Do this first; it's pure measurement.
4. **Category-specific fair-value anchors, later, optional**: base-rate
   tables for recurring market families; a realized-vol fair-value model
   for 15-minute crypto/index series, mirroring predictionmarketspicks'
   own 15-min commodity tool.
5. **Re-point sizing at edge**: fractional Kelly on `(p_est, price)`,
   fraction shrunk by estimate variance (confidence's proper home).
   Report per-contract flat P/L and Brier score separately from sized
   P&L, so signal quality and sizing quality stay distinguishable — see
   §10.2.

### 3.4 What already exists here and is *not* the gap

Not everything is missing. Already shipped and reasonably rigorous:
Kelly-scaled sizing (`kelly_fraction_of_cap`), a real sigma-vetted
significance-testing framework (`services/stats_power.py`) used
project-wide for gate/override decisions, a price-band filter, a maker/
limit-order path (paper book only), and — most relevant here — a
**designed and partially built** LLM-based "market_analyst_agent"
(`services/market_analyst_agent/`) specifically conceived to produce an
independent probability estimate comparable against market price. Per its
own design doc (`docs/prediction-market-strategy-alignment-plan.md` Part
3), it was scoped deliberately advisory-only, never wired into
`strategy_engine.evaluate()`'s actual decision. **Corrected citation** (an
earlier draft misread `strategy_engine.py:63` as a config-gate read; it is
actually inside a docstring's prose example list, not executable code):
`grep -n market_analyst services/strategy_engine.py` returns exactly one
line, and it is that same docstring reference — there is no config-gate
read of `market_analyst` anywhere in the file, and no evidence of decision
wiring. If anything this is stronger evidence for "still advisory-only"
than the original (wrong) citation, but **it should still be directly
confirmed against the real `strategy_engine.evaluate()` call graph, not
inferred from an absence of matches, before anyone treats it as settled**
(flagged as an explicit open item in §14). Do not propose
rebuilding this agent from scratch; it already exists and was designed by
exactly the reasoning in §3.1–3.3 above, just never connected to a price
test.

Full analysis, methodology, and sourcing: §10.

---

## 4. Trade-critical vs. app-facing decoupling — the load-bearing finding of this audit

### 4.1 Current topology

Everything runs in one FastAPI/uvicorn process (`main.py`, `lifespan()` at
`:1299-1395`): one event loop runs the tick loop, the loop watchdog, 8
`_scheduler_loop` instances, both Kalshi WS gateways and consumers, two
liveness loops, the index-feed backfill loop, **and every HTTP request
handler**. Three separate thread pools compete for the same trading-critical
work: `tick_executor` (2 workers — trading-critical), `_scoring_pool` (4
workers — whale scoring, split off in PR #409 specifically to stop sharing
`tick_executor`), and `_aio_db`'s one-thread-per-(loop, file) aiosqlite
model. A fourth pool (`_diagnostics_pool.py`, 2 workers) was created and
then deleted within 24 hours during the same investigation thread — this
is the third time in three days a resource-sharing symptom has been
"solved" by handing the offending workload its own pool, which is static
partitioning of a problem that needs dynamic prioritization.

### 4.2 App-facing routes still share the trading pool — verified against current source, not the incident summary

`services/analytics/routes.py:103` (`await tick_executor.run(...)`) and
`services/whale_calibration/routes.py:117,152` (same) are live, today, on
`main` HEAD `999fcb9`. Measured wall time for a single sequential request,
no synthetic load, taken tonight:

| Endpoint | Wall time | Shares pool with |
|---|---|---|
| `GET /api/quality/summary` | **48.6s** (independently reproduced at 24/24 ≥6.5s, §2.2) | `_aio_db` single-connection-per-file + on-loop aggregation |
| `GET /api/candidate-log/summary` | **13.6s** | `tick_executor` (2 workers, shared with trading writes) |
| `GET /api/confidence-calibration/report` | **4.5s** | `tick_executor` (2 workers) |

`services/diagnostics/_aio_db.py`'s own docstring (from its PR review)
already recorded 20.4s for the first route; tonight's 48.6s is 2.4× that.
**Both routes are polled every 6 seconds by the dashboard whenever the
History tab is open** (§6.2) — this is the exact, confirmed mechanism
behind `docs/next-action.md`'s "they also degrade on their own without any
action" symptom report: one open browser tab saturates the pool with zero
user interaction.

### 4.3 What real trading systems do, and what's proportionate here

The standard production separation is three tiers — market-data/feed
handler, strategy+execution, read-only analytics — coupled by a durable
log or message bus specifically so a slow report can never acquire
anything the trading tier needs. **NautilusTrader**, the most
production-grade open-source system surveyed (§10.1), reaches the same
principle *without* a bus: single-threaded deterministic core, with
"writes to \[persistence\] backings occur asynchronously; the engine
publishes values even if persistence fails." That is the transferable
pattern here — not more processes, not a bigger database, but the decision
path made structurally unable to be blocked by reporting.

### 4.4 Recommendation: two processes, not three, no bus, no CDC

**A second uvicorn process serving the app-facing/diagnostic routes,
opening the same `data/*.db` files read-only (`file:...?mode=ro` URIs),
fronted by the existing nginx.** Concretely:

- **Process A** (current `main.py`, minus the heavy read routes): tick
  loop, WS ingestion, strategy, execution, `risk_manager`, `paper_broker`,
  `candidate_ledger`, `capture_writer`, `/api/state`, `/api/health/*`, and
  every write endpoint regardless of which prefix it otherwise looks like
  it belongs under — see the correction immediately below.
- **Process B** (new, same codebase, different route set): `/api/quality/*`,
  `/api/diagnostics*`, `/api/candidate-log/*`, `/api/confidence-calibration/report`,
  `/api/regime/*`, `/api/backtest/*`, `/api/advisory/status`,
  `/api/advisory/recommendations`, `/api/signals/history`,
  `/api/observability/*`.
- **Correction from the adversarial review:** an earlier draft of this
  section listed `/api/advisory/*` under process B wholesale. That prefix
  is not read-only — `POST /api/advisory/.../apply` (§9.2's "apply one
  suggestion" finding, `services/advisory/routes.py:192-200`) writes
  `config/settings.yaml`, directly contradicting caveat 1 below. The split
  here has to be by *verb and specific route*, not by URL prefix alone —
  `/api/advisory/status` and `/api/advisory/recommendations` are read-only
  and belong in B; `/api/advisory/.../apply` (and every other suggestion-
  apply/config-write endpoint under any prefix) stays in A. Re-audit every
  route nginx would send to B against this same test before implementing,
  not just the ones this document happened to name.
- nginx (`.ddev/nginx/kalshi-proxy.conf`) routes by path (and, given the
  correction above, sometimes by method) — the frontend does not change at
  all.

**Why this shape specifically:** SQLite's WAL mode already gives
multi-process concurrent readers that never block the single writer — a
property this codebase already pays for (every `_connect()` sets WAL) and
currently wastes by putting readers in the writer's own process. No bus,
no replication, no CDC, no schema change, no new engine. A 48-second scan
in process B cannot touch process A's event loop or thread pools, because
it is a different address space — this is the cheapest possible
implementation of the standard tier-3 separation, available today because
of a design decision (one file per concern, WAL) that was already made for
other reasons.

**Three things this needs to actually handle, not hand-wave:**

1. Writes from process B (`/api/config`, `.../apply`, `/api/reset`,
   `/api/trading/enable`) must stay in process A or proxy to it — do not
   open a second writer.
2. Process B has no in-memory `services/app_state.py` `state` dict — every
   moved route must be verified to read only from DB/config. This
   verification, route by route, is the real work of this change.
3. `_build_state_body()` stays in A (`main.py:1525-1607`, reads `state`
   directly).

**A third process for observability specifically is not warranted.** Once
B is read-only and out of A's process, a slow diagnostic query already
cannot contend with a trading write. Splitting B further buys isolation
between two things that don't currently contend with each other — revisit
only if B's own routes start starving each other, which is measurable and
not measured today.

**Sequencing: do §6's polling fixes first.** They are cheaper, need no new
process, and remove most of the *offered load* that makes the split urgent
— a 48.6s route hit once when a human opens a tab is a fundamentally
different problem than the same route hit every 6 seconds forever, forced
by a fixed frontend timer. Fix demand, then decide how much isolation the
residual actually justifies.

**Cost this document has not scoped: CI and deployment surface.** A second
process means changes to `.ddev/docker-compose.*`, `.ddev/nginx/kalshi-proxy.conf`,
some subset of the five required Woodpecker checks, and this repo's own
wiring tests (`tests/test_hooks_wiring.py`, `tests/test_mcp_and_plugin_wiring.py`)
plausibly need attention too. None of that is scoped here — "the read-only
WAL mechanism is cheap and already proven" (§1, verified directly by the
adversarial review against the live 27GB file under active writes) is a
narrower claim than "the whole split is cheap." Treat the route-by-route
`state`-read verification (caveat 2 above) and this CI/deployment surface
as the two pieces of real, separately-estimated work a future plan needs
to size, not as included in "an afternoon."

---

## 5. SQLite fitness — per-concern, not blanket

### 5.1 Inventory

32 live `data/*.db` files, 31 module-level `DB_PATH` definitions, WAL mode
verified across 30 call-site definitions (the small 32/31/30 deltas
between these three counts were not individually reconciled in this pass
— flagged rather than silently rounded together) — the one architectural
rule (`CLAUDE.md`: "one SQLite file per concern, no shared DB, no ORM") is
followed consistently.
`busy_timeout` is *not* uniform (most modules use Python's implicit 5.0s
default; `tick_executor`'s 50ms cache has zero production callers;
`capture_writer` uses a documented two-tier budget) — divergent but each
divergence is deliberate and traceable, not accidental drift.

**142 `with _connect() as conn:` call sites open a fresh connection per
call**, each re-running WAL pragma + idempotent DDL + schema checks —
measured directly in this codebase's own regression history at **548µs
per fresh connect**, against a documented ~20µs hot-path ceiling
(`title_cache.py:302-306`). Three hand-rolled connection-lifetime caches
already exist to work around this (`tick_executor`'s unused one,
`_scoring_pool`'s live one, `_aio_db`'s live one) — a fourth being
reinvented is the real recurring pattern, not the storage engine itself.

### 5.2 The per-concern verdict — not "migrate off SQLite," a specific split

**Correction from the adversarial review, load-bearing — read this before
the rest of this section.** An earlier draft attributed `capture_writer`'s
159 24h "database is locked" faults to `series_watcher.db`/`raw_trades`.
Directly re-queried (`GET /api/health/faults?component=capture_writer&hours=24`,
whose `context` field names the actual store): **the 159 `warn`-severity
`flush_retained_on_lock` faults are on `candidate_log.db`'s
`rejected_candidates` table, not `series_watcher.db`.** The real
`raw_trades` fault is a separate row this document had not previously
surfaced: **237 `error`-severity `flush` faults on `raw_trades` itself**
— and unlike the 159 `warn`-level faults (which retain the batch and
retry it), `capture_writer.py`'s `flush` path on a lock **drops the rows**
outright (`_dropped_counts[store] += len(rows)`). **This is a real,
previously-unreported data-plane completeness defect** — per `raw_trades`'
own "never pruned, accumulated history is a first-class asset" design
intent, losing rows to a lock collision is exactly the silent-defect shape
the data-plane HARD RULE's "measure them; never infer health from the
absence of errors" line exists to catch. It was sitting in the same
endpoint response this section already queries; add it to whatever
session picks up §13's action items — root-cause and fix it with
`superpowers:systematic-debugging`, not folded silently into the DuckDB
migration below.

**Keep on SQLite, unchanged (18 of 32 files):** `paper_broker.db`,
`market_broker.db`, `risk_state.db`, `market_risk_state.db`, `accounts.db`,
`config_performance.db`, and 12 others ≤0.6MB, human- or per-trade-cadence
writes, single-process readers. Single-writer semantics are a **feature**
here — `paper_broker`/`risk_manager` want serialized, durable, synchronous
writes, and a network round-trip to Postgres would slow the signal→order
path down, not speed it up. **Open gap, not resolved here:** this
classification plus the 3 "add retention" files and the 2 "move to
DuckDB" files below account for 23 of §5.1's 32 inventoried files — the
remaining 9 are not individually classified in this pass; a future
session should close that gap rather than assume they're all fine by
omission.

**Keep on SQLite, add retention + bound the queries (3 files):**
`candidate_log.db` (`rejection_events`, no retention, 3.4GB — 21.2M rows —
an unfiltered scan measured at 13.6s live tonight), `signal_log.db`
(`resolved_signals_with_factors()`'s own docstring already tells its
polled caller to pass `since_ts` — it doesn't, PR #424 already fixed the
identical shape for a sibling function), `market_history.db`. ~10-line
changes each; together they remove most of §4.2's measured route latency.

**Move off SQLite — to DuckDB, not Postgres (2 files):**
`series_watcher.db`'s `raw_trades` (39.2M rows, 27.2GB, append-only, never
updated, read only by analytics) and `index_feed.db`'s `index_ticks`
(**936,932 rows** — corrected from an earlier draft's 2.0M estimate,
measured directly). This is a textbook OLAP shape: immutable columnar
facts, scanned in aggregate. **The scan/compression multipliers commonly
quoted for DuckDB-vs-SQLite (roughly 10–50× on scans, 5–10× on
compression) are industry-typical figures, not a measurement taken
against this specific 27GB file — label them as an estimate to validate
with a real benchmark before committing to the migration, not as a
settled number.** What *is* directly measured and load-bearing on its own:
`raw_trades` now has its own confirmed, real lock-contention problem
(above) independent of `rejected_candidates`' — moving it to DuckDB
**removes a real write-lock holder from the SQLite contention set**, which
remains the strongest argument for this migration even without the
scan-speed estimate. **This is still the highest-leverage data-layer
change available**, and it is surgical, not a platform migration — but
re-derive its priority against the corrected 237-fault/row-dropping
evidence above, not the original 159-fault figure.

**PostgreSQL: recommended against, for now.** The contention actually
firing is *within single files* — `candidate_log.db` (retained-and-retried
lock faults) and, separately, `series_watcher.db`'s `raw_trades`
(lock faults that drop rows) — and *within one process*, not coordinated
across concerns the way a shared multi-writer database would fix. Postgres
without a process split buys nothing the current model doesn't already
give, while adding a network hop to the broker's synchronous write path (a
data-plane-rule regression) and a real ongoing operational commitment for
a solo maintainer. Revisit specifically if and when §4's process split
needs a shared multi-writer store — it doesn't yet.

**TimescaleDB/InfluxDB: recommended against.** The genuinely time-series
stores (`index_ticks`, `book_snapshots`, `observability.metric_samples`)
total ~1.5GB, already pruned, already fast. The one huge time-series-shaped
table (`raw_trades`) is analytical-scan-shaped, which DuckDB serves better
than a downsampled-metrics engine.

**Redis as a database: recommended against** — see §7.

**Two fixes independent of any engine choice, worth more than any
migration:** (1) consolidate the three existing hand-rolled connection
caches into one shared, documented mechanism instead of a fourth being
invented next time a workload contends; (2) `series_watcher.prune()`'s
full-scan DELETE on `book_snapshots` likely needs an index leading with
`observed_at` — `_connect()`'s actual index definitions are
`idx_book_ticker (ticker, observed_at)` and `idx_book_series (series,
observed_at)`, neither of which leads with `observed_at` alone, so
`DELETE FROM book_snapshots WHERE observed_at < ?` cannot use either
index and falls back to a scan — this alone should remove most of the
lock hold the `raw_trades`/`rejected_candidates` faults above cluster
around.

**Scope gap this document does not close: backups.** `data/backups/` +
`data/backups_large/` together total **~34GB** — larger than every
database in `data/` except `series_watcher.db` itself, and roughly half
of `data/`'s total footprint. `services/backup/backup.py` is not audited
anywhere in this document. Before acting on the DuckDB recommendation
above: does the backup process's own read of `series_watcher.db` (via
`sqlite3.Connection.backup()`) contend with the same locks implicated in
the 237 `raw_trades` faults? What does a 27GB→DuckDB migration do to
backup size/cost/restore time? Neither question is answered here —
flagged explicitly in §14 rather than silently assumed away.

---

## 6. Polling inventory — "there's still way too much polling" is correct, and specific

Prior art exists and was re-verified rather than inherited:
`docs/archive/lane-1-kalshi-ingestion/research/2026-08-27-application-wide-rest-vs-ws-inventory.md`.
Two of its findings are now stale (fixed) and are corrected below; four are
confirmed still open.

### 6.1 Backend REST-vs-WS — mostly fine, three real gaps

Every 5s/10s backend supervision loop (scheduler triggers, liveness
checks, the loop watchdog itself) has an idle path of one dict comparison
or one `snapshot()` read — genuinely necessary, and the data-plane HARD
RULE explicitly forbids changing their cadence without a measured
bottleneck. **None of them appear in any measurement taken tonight; do not
touch them.**

Real gaps, verified against `docs/kalshi/` today (not assumed):

- **`_check_signal_resolutions`'s 30s REST poll is the clearest remaining
  gap.** `market_lifecycle_v2`'s `determined`/`settled` WS events already
  arrive, already resolve `market_history`/`settlement_edge`/
  `market_analyst_agent`/`candidate_log` — `signal_log` is the one store
  left on the poll. Keep the REST poll as a much-longer-interval safety
  net; don't delete it outright.
- **`orderbook_delta` and `user_orders` WS channels are entirely
  unsubscribed** despite existing (re-verified: zero matches for
  `orderbook_delta|user_orders` in `services/`). Orderbook depth is real,
  new capability (the app has never had resting-depth data), not just a
  poll-reduction — worth its own design pass.
- **5 of 8 `market_lifecycle_v2` event types are still discarded**
  (`created`/`activated`/`deactivated`/`metadata_updated`/
  `price_level_structure_updated`) — real, lower payoff than the items
  below, needs its own measurement of what it would actually save.
- One prior-audit finding is **fixed and the old doc is stale**:
  `latest_prices`'s wholesale REST overwrite is now age-aware
  (`market_fetch.py:272-300`), WS-fresh prices already win.

### 6.2 The frontend is effectively 100% poll-based, and this is where the real load is

A dashboard→browser WebSocket exists (`ws_manager.py`) but carries exactly
two message types (`signal_decision`, `trade_stream_status`). **There is
exactly one `setInterval` in the whole frontend**
(`trading-gate-and-connectivity.js:89-93`, at `kalshi.poll_interval_sec ×
1000` = 6000ms), and it fans out via `refresh()`.

**Correction from the adversarial review:** an earlier draft presented
`/api/quality/summary` and the two `tick_executor` routes as one combined
19-request-per-6-seconds load. They are not on the same trigger.
`/api/quality/summary` is fetched by `loadSystemHealth()`
(`system-health.js:91`, called from `polling-and-websocket.js:108`) only
inside `refresh()`'s `active === 'terminal'` branch — the **Terminal**
tab. The two `tick_executor` routes are fetched by
`refreshHistoryInsightsIfActive()` (`main.js:72-84`, called from
`polling-and-websocket.js:134`), which returns immediately unless the
**History** tab is active. These are mutually exclusive tabs — a user has
one or the other open, not both.

**Per 6-second tick, History tab open — `refreshHistoryInsightsIfActive()`
fires 10 loaders = 18 HTTP requests, plus `/api/state` = 19 requests every
6 seconds**, including the two slow tick_executor routes (13.6s, 4.5s) and
5 parallel `/api/regime/*` calls. This is the precise mechanism that
matches `docs/next-action.md`'s reported "9 upstream timeouts each... in a
~4-minute window": a 13.6s route requested every 6s from a 2-worker pool
shared with the tick loop, on the History tab specifically. One open
browser tab on that tab saturates it with zero user interaction — matching
the reported symptom for those two routes.

**Per 6-second tick, Terminal tab open** — `/api/state` plus
`/api/quality/summary` (48.6s independently measured, §2.2) fire together
on a separate, independent trigger from the History-tab routes above.

**What is and isn't established about tonight's specific numbers**: this
document does not have direct evidence that a browser tab was open on
either the Terminal or History view during the 15-minute curl-based
monitor run in §2.2/§2.1 — the monitor is a script hitting the API
directly, not a browser session. So "de-polling these three routes is
*the* dominant cause of tonight's specific measured degradation" is a
strong, falsifiable **hypothesis** consistent with all the evidence
gathered, not a confirmed mechanism, per this project's own "correlation
is not causation" standard — the cheap falsifier (re-run the same monitor
with zero browser tabs open, confirm the routes are hit some other way,
or confirm no tab was open and the degradation persists anyway) was not
run. Independent of that open question, both routes' cost and their
previously-reported live incident role (issue #410) are independently
confirmed and reproducible on demand (§2.2) — de-polling them is still
justified on that basis alone.

### 6.3 Recommendation, ordered by measured payoff per unit of effort

1. **Stop polling `/api/quality/summary` (Terminal tab) and the two
   `tick_executor` routes (History tab) on their 6s timers.** On-demand
   only (button + one fetch on tab-open), or at minimum their own 5-minute
   independent timer, for each tab's trigger. A `main.js`/
   `polling-and-websocket.js`/`system-health.js` change of a few lines
   that removes the entire issue-#410 mechanism without touching pool
   architecture. **Do this first, before anything else in this document.**
2. **Scope or remove `event_live_data` from `/api/state`.** 88–96% of a
   multi-megabyte payload (re-measured across two probes: 5.13MB/88% and
   5.72MB/96%, moving in the wrong direction), changes on a 60s cadence,
   read only by markets/whale panels. Move to its own on-demand endpoint,
   or send only currently-rendered events, or delta-key it (the client
   already merges rather than replaces). Expected: a multi-megabyte body
   drops to roughly the size of everything else combined (~230–600KB),
   an order of magnitude less bandwidth/serialization per poll.
3. **Fix or retire the `/api/state` ETag.** `bump_generation()` currently
   means "any WS message arrived," not "anything in this body changed" —
   either scope the bump to fields the body carries, or coarsen it (bump
   at most once per N ms) so a 6s poll has a real chance at a 304. **Name
   this explicitly as what it is: a deliberate data-plane tradeoff**
   (trading timeliness of the change signal for fewer full-body
   re-sends), not a free win — per the data-plane HARD RULE's "never trade
   one property for another silently; make the tradeoff explicit and
   measured," state the chosen coarsening interval and why it's
   acceptable for a UI poll specifically (not for anything trading-
   decision-facing) before shipping it.
4. **Replace the 30s signal-resolution REST poll with the lifecycle
   stream** (§6.1) — keep the poll as a bounded safety net at a much
   longer interval. This is itself a polling-frequency change and should
   get the same "identify the measured bottleneck first" treatment §6.1
   already applies to the backend supervision loops, not be assumed safe
   because the replacement mechanism (the WS lifecycle stream) already
   exists.
5. **Push more over the existing dashboard WebSocket** — `latest_prices`
   deltas, trade-tape appends, account/broker changes — each removes a
   slice of `/api/state` and its poll. This is the structurally correct
   end state (a WS-pushed dashboard, `/api/state` as cold-start snapshot
   only), but sequenced after 1–4 since it's more work.
6. Consume the 5 discarded lifecycle event types to reduce `catalog_scan`'s
   rescan cadence — real, lower priority, needs its own measurement.
7. Subscribe `orderbook_delta` — new capability, its own design pass, not
   a polling fix.

---

## 7. Caching layer assessment — a red herring, and Redis specifically is not justified

**There is no shared cache infrastructure and no cache library anywhere**
(`grep` for `lru_cache|cachetools|redis|TTLCache|@cache` across `services/`:
zero real hits). ~10 independently hand-rolled caches exist instead
(`series_cache.py`, `title_cache.py`, several module-level dicts). Two of
these have a **documented history of being the cause of an incident, not a
fix**: `title_cache.py`'s progression (real regression narrative,
`:292-369`) went dict → SQLite-backed memo with TTL (made a hot-path
incident worse, 548µs/miss measured against a 20µs ceiling) → back to a
plain in-memory dict with **no TTL, no eviction, zero DB access** — "a
miss costs nothing to re-check, so there is nothing to avoid re-checking."
**Every iteration where a caching library would have applied made things
worse; the fix both times was removing cache semantics, not improving
them.**

**Nothing measured tonight is slow because of a cache miss.** Decomposing
the three slow routes from §4.2: all three are *query cost* (unfiltered
scans, unbounded reads) plus *pool contention* — not repeated computation
of a stable value. A cache in front of any of them would mask the cost at
a 6s poll cadence without reducing it, and the first request after every
invalidation still pays full price on the same shared pool.

**Redis specifically: recommended against, clearly.** It buys cross-
process shared state and pub/sub. There is one process today; if §4's
split happens, the two processes already share state through WAL-mode
SQLite files — simpler, and it already works. Redis would add a network
hop, a serialization step, a second always-up process under ddev, and a
new failure mode on paths currently incapable of failing (`title_cache`'s
final design was specifically rewritten so it "can no longer raise").
Reconsider only if the app ever runs genuinely multi-process with workers
that need to coordinate beyond what WAL gives for free — not the case
today.

**What's actually worth doing:** (1) nothing, until §6.3's polling fixes
land — most of the apparent need for a cache disappears once the slow
routes aren't hit every 6 seconds; (2) if a cache is still wanted after
that, one small shared in-process TTL memo (not Redis, not an eleventh
hand-rolled dict); (3) fix `_state_body_cache`'s ~0% hit rate (§2.4's
ETag problem is the same root cause); (4) **connection reuse is the
"caching" that would actually pay** — 548µs × 142 per-call sites is real,
recurring, measured cost, and consolidating the existing three connection
caches (§5.1) is a better use of the same effort than any result cache.

---

## 8. Hand-rolled vs. framework/library — most of it is correctly hand-rolled; two things aren't

Runtime dependencies are deliberately minimal (`fastapi`, `uvicorn`,
`httpx`, `pydantic`, `aiosqlite`, `websockets`, `anthropic` — every pin
carries a written justification comment). No Prometheus client, no
OpenTelemetry, no structlog, no APScheduler, no tenacity, no cachetools
anywhere. Frontend has **zero runtime dependencies** (esbuild/eslint are
dev-only) — 6,369 lines of vanilla ES modules.

### 8.1 The real defect: thread-pool proliferation, not a missing library

Covered in full in §4. The fix family used by every surveyed real system
(§10) is *stage decoupling inside the process*, not a library swap.
**Recommendation: finish the `aiosqlite` migration `_aio_db.py` already
started; delete `tick_executor` and `_scoring_pool` as their callers
drain onto it.** This is subtraction. `_aio_db.py`'s own docstring is
already honest that this isn't a free win (aiosqlite serializes ops per
connection, so some concurrency the thread pool gave is traded for
removing the shared-resource coupling that's the actual defect) — do it
per-caller with measurement, the pattern this repo already follows.

### 8.2 Correctly hand-rolled, keep as-is

| Subsystem | Why it's right as-is |
|---|---|
| `services/http_client.py` rate limiting | Empirically derived from mirrored official docs + a live rate-limit probe; a drop-in (`aiolimiter`) would cost the `waiters_high_water` telemetry this app specifically relies on to distinguish local contention from network latency |
| `services/fault_log.py` | Sentry's own dedup-by-(component,operation,exc_type,message) grouping model, 227 lines, offline, no vendor, no sampling — a better trade than a Sentry free tier for a solo project |
| `services/series_cache.py` / `title_cache.py` | Final design has no TTL and no eviction because it doesn't need them (§7); Redis would reintroduce exactly the machinery two review rounds already removed |
| `services/task_supervisor.py` + `main.py`'s `_maybe_*` scheduler | Restart-safe by design (`due()` derived from *persisted* state, not wall-clock) — APScheduler's in-memory/SQLAlchemy job store would reintroduce a cold-start reload bug this repo already hit once |
| `services/ws_manager.py` | 40 correct lines; nothing smaller exists |
| `services/loop_watchdog.py` | Cheap, falsifiable, caught real 130s stalls; complement (don't replace) with `loop.slow_callback_duration` behind a flag for attribution |
| `services/kalshi/websocket.py`'s reconnect-discard accounting | Domain-specific message-conservation invariant no WS library provides |

### 8.3 Worth a narrow adopt, not a rewrite

- **`services/observability/` (~900 lines: `LatencyAgg`, bucket math,
  window/lifetime split)** — adopt `prometheus_client` for
  *instrumentation only* (replace `Histogram`/`Counter` internals), keep
  the SQLite store as a local 60s scraper. This deletes the window/
  lifetime double-accounting that already caused one real 30× measurement
  bug, without requiring a deployment target or a Prometheus server to
  exist. Defer OpenTelemetry until a real deployment target exists (it's
  an open Path-to-production gap regardless).
- **`services/kalshi/websocket.py`'s exception handling** — the pinned
  `websockets` 17.0.1 already ships `process_exception` to distinguish
  retryable from fatal errors; the current loop retries *everything*
  forever (`:714`), so a permanently-revoked key reconnects every 30s
  indefinitely instead of surfacing. **Correction from the adversarial
  review:** an earlier draft called this independent of the `async for`
  reconnect-loop rewrite. It is not — reading the installed 17.0.1 source
  directly, `process_exception` is consumed in exactly one place,
  `websockets.asyncio.client.connect.__aiter__`'s own reconnect loop. The
  app's current pattern (`async with websockets.connect(...)` inside a
  hand-rolled `while True`/`except Exception`, `:601-714`) never calls
  `connect.__aiter__` and therefore never reaches `process_exception` at
  all. Adopting fatal-vs-retryable classification **requires** converting
  to the `async for connection in websockets.connect(...):` pattern — this
  is one change, not two independently-priceable ones; re-price it as the
  `async for` rewrite plus the classification logic it unlocks, not as a
  small separate step on top of an optional rewrite.
- **Three incidental bugs found while reading, unrelated to any library
  choice**: `services/alerting/alerting.py` discards a `supervise()` task
  handle (a GC hazard — asyncio only weak-references fire-and-forget
  tasks) at **three** call sites in that file (`:91`, `:270`, `:294`, not
  just one as an earlier draft stated); every call site in `main.py`
  correctly stores its handle instead. `http_client.py:430` constructs
  `httpx.AsyncClient()` bare with no explicit `timeout=`/`limits=`,
  silently inheriting library defaults — one line to fix, and exactly the
  kind of silent-default CLAUDE.md's "never guess" rule exists to
  prevent.

### 8.4 Frontend — the framework decision is already made, and never executed

**Verified directly tonight: `ROADMAP.md` states the framework question
was answered 2026-08-25 (Preact 10 + `@preact/signals` + `htm`, strangler-
fig migration, uPlot for charts) — and it has not started.**
`frontend/src/js/` has zero subdirectories (13 flat files); `package.json`
has no `preact`/`@preact/signals`/`htm` dependency; the task plan
(`docs/archive/lane-8-frontend-dashboard/plans/2026-08-25-frontend-modularization.md`) has 50
unchecked tasks and 5 checked. This is not a fresh architectural question —
it's 8-day-old, fully-designed, unstarted work.

**The evidence in current code independently re-confirms the framework
choice was correct**, not merely "already decided so don't reopen it":

- A hand-rolled VDOM diff already exists, written in direct response to a
  user-reported bug (`signals-feed.js:13-31`, "a full replace every 5s
  flashes visibly and resets scrollTop").
- Manual focus/selection restoration wraps an `innerHTML` rebuild
  (`screener-and-header.js:694-698`) — on **the real-trading-enable
  confirmation input**, the single most safety-sensitive field in the app.
- 196 `window.<fn> =` global re-exports plus **~90** inline `onclick=`/
  `onchange=` strings (corrected from an earlier draft's 105, which
  double-counted a third handler type — `onclick` alone is 60, `onchange`
  30) bypass the real ES-module import graph entirely — the largest
  correctness liability in the frontend (renaming an exported function is
  silently safe to the bundler and silently broken at runtime).
- A real bug already shipped from exactly this shape
  (`main.js:52-71`'s removed `refreshActiveViewPanels()`, which caused
  scroll resets and a laggy Apply button from duplicated fetches).

**Correction, direct from the repo owner:** an earlier draft of this
paragraph described "a deliberate single-command-esbuild-no-build-step
constraint" as if it were an established rule. **No such rule exists
anywhere** — not in `CLAUDE.md`, not in the frontend-modularization spec,
not stated by the owner. It was an unverified inference stated as fact,
which is exactly what this project's own "never guess; verify or falsify"
HARD RULE exists to prevent, and it was also imprecise on its own terms:
this repo already has a build step (`frontend/package.json`: "esbuild
bundles \[`src/js/`\] into `static/js/dashboard.bundle.js`... edits need a
rebuild"). Corrected, narrower, and actually cited: the frontend-
modularization spec (`docs/archive/lane-8-frontend-dashboard/specs/2026-08-25-frontend-modularization-design.md` §10, "JSX escape hatch (documented, not used)")
chose `htm` specifically so `.js` files stay plain JavaScript processable
by the *existing* esbuild bundle, without adding a *second*,
framework-specific compiler/transform stage (JSX, or a Vue/Svelte SFC
compiler) on top of it. That's a real, narrower technical fact worth
weighing — not a "constraint this repo has," just a cost/benefit point in
Preact+htm's favor over Vue/Svelte, which still stands on its own once
stated accurately.

**Why Preact over the alternatives, re-derived independently:** React is
the same model at ~5× the bundle for no benefit a solo local dashboard
needs. Vue/Svelte would work but would add a second compiler/transform
stage beyond the existing esbuild bundle (see the correction above) — a
real cost, not a violated rule. htmx is the wrong
shape — this backend is API-only by explicit standing design, and htmx
means moving rendering back into FastAPI, reversing that decision. Alpine
cleans up the inline-handler mess but has no keyed-list reconciliation, so
both bugs above would survive it. Solid needs a compiler for its
fine-grained reactivity, same objection as Svelte.

**Sequencing:** do §4/§6's backend fixes first — they're an actively
measured production defect with a user-visible symptom tonight; the
frontend migration is a multi-week, five-PR-group effort against a UI that
currently works. Note the two are causally linked (two of the routes
starving the pool are driven by the dashboard's own 6s poll), which is an
argument for eventually doing both, not for reordering them.

**Charts specifically**: the spec's choice of uPlot for the six line
charts is right (fastest canvas time-series option, axes/legend/cursor for
free). The spec's decision to leave the hand-rolled OHLC candlestick view
(~130 lines, no axes/crosshair/zoom) unchanged is defensible on bundle
size but is the one place real user-facing value is left on the table —
**Lightweight-Charts (TradingView)**, purpose-built for OHLC+volume, ~45KB
gz, is worth naming as a specific follow-on once the core migration lands.

---

## 9. DRY / duplication — real, but narrow and concentrated, not systemic

**The owner's original SQLite `_connect()` hypothesis was checked directly
and is not the problem** — AST-extracted kwargs from 44
`sqlite3.connect()` call sites (scoped across `services/`, `main.py`, and
`tools/`; §5.1's separately-scoped `services/`-only recount found 40 — the
delta is `main.py`/`tools/` inclusion, noted here so the two figures don't
read as contradictory) show consistent pragma/timeout/isolation settings
(2 deliberate `timeout=` overrides, both documented; 0 inconsistent
`isolation_level`; WAL set everywhere non-read-only). A shared `_connect()`
would fight the one-file-per-concern architecture rule for no correctness
gain. **The real duplication is concentrated in the config-apply/advisory
path**, and it has already produced live bugs.

### 9.1 Three findings that are safety-adjacent, not merely stylistic

**`generate_recommendations(...)` is hand-assembled at 6 call sites; 3
omit `declined_ids`** — including `main.py:583`, inside
`_maybe_run_auto_apply`, the **unsupervised** path that writes
`config/settings.yaml` with no human in the loop. The 3 sites that include
it are the ones a human clicks. `declined_ids`'s own contract
(`advisory_engine.py:926-929`): "a suggestion a human already clicked 'no
thanks' on doesn't come back with the exact same evidence behind it." The
one caller with no human in the loop is the one that doesn't honor the
human's decline.

**Table DDL for `raw_trades`/`rejection_events`/`rejected_candidates` is
duplicated across modules against live, multi-gigabyte `data/*.db`
files, with a self-documented "keep the two DDL blocks in sync by hand"
comment** (`services/series_watcher.py:212-217`, `services/capture_writer.py:155-162`).
`raw_trades` has 3 copies (`series_watcher.py:161,221`,
`capture_writer.py:134`); `rejection_events`/`rejected_candidates` each
have 2, differing by one column (`unit_cost`) added via migration in one
copy but baked directly into the DDL in the other — benign today because
both converge on the same effective schema, but **whichever module's
`CREATE TABLE IF NOT EXISTS` executes first on a fresh file decides its
shape**, and the next column added to only one copy silently diverges
them. `services/game_state.py:108-118` already documents having hit
exactly this failure mode once (a broken write looked like a quiet market
rather than a bug, because nothing raised). This directly conflicts with
`CLAUDE.md`'s "schema changes are additive only" rule, because additive
is only enforceable if there is one definition to add to.

**`RiskManager`/`ShadowTrader` duplicate the daily-loss kill-switch state
machine** (29 lines are byte-identical between the two files —
`risk_manager.py` is 195 lines total, `shadow_mode.py` 282; the "144"
figure in an earlier draft was an uncited non-blank/non-comment subset
and is corrected here to the real file length) — and they've diverged:
`ShadowTrader.check_daily_loss`
guards against a zero bankroll baseline (`shadow_mode.py:145-147`,
`if not self.day_start_bankroll: return True`); `RiskManager`'s equivalent
function has no such guard. The reachable path: `reset_day(current_bankroll)`
sets the baseline from the live bankroll at each UTC date rollover, so a
bankroll of exactly 0 at rollover would raise `ZeroDivisionError` in the
**real kill switch**, while the inert shadow copy is protected. **The
safety asymmetry runs backwards — the copy that does nothing is hardened,
the copy that gates real trading is not.**

All three are flagged here for the record per this session's explicit
pre-brainstorming, doc-only scope — **not fixed in this pass.** They
should be near the top of whatever session picks up this document's
findings (§13, §14).

### 9.2 The rest, ranked (this table is the complete record — the source
agent's own scratch report was ephemeral session output and is not
committed; every load-bearing claim from it is restated here so this
document is self-contained, per the Appendix's note on evidence
provenance)

| # | Finding | Sites | Fix effort |
|---|---|---|---|
| 3 | "Apply one suggestion to config" block (staleness check → fingerprint → `config_store.update` → `log_applied_change` → `bump_generation`) copy-pasted 6× with divergent staleness guards (2 of 6 have none, including the auto-apply path) and divergent `trade_count` values (one site hardcodes `0`, discarding audit-trail evidence) | 6 | Medium — writes `config/settings.yaml`, wants its own TDD cycle |
| 4 | `_add_column_if_missing` — 11 AST-identical copies (+1 hand-inlined 12th in `game_state.py`) | 11 | Trivial — shared helper takes `conn` as a param, shares no state, doesn't violate the one-DB-per-concern rule |
| 5 | Route `limit` handling: 16 routes accept it, only 8 clamp (3 different idioms, 5 different ceilings); 4 traced end-to-end unclamped routes reach `LIMIT ?` directly — `?limit=99999999` is an unbounded synchronous read on a live multi-GB file on the event loop | 16 | Trivial — one `paginate()` FastAPI dependency closes 8 gaps at once |
| 2, 6 | (§9.1 above — table DDL, kill-switch duplication) | 2+7 | Medium — safety code, own branch/review cycle |
| 7 | Frontend: `(x*100).toFixed(0)` + `¢`/`%` inline, no shared formatter, across 7 files, 30 sites — the repo's own documented no-side-inversion bug class becomes un-auditable at the call site once there's no single place to check | 30 | Trivial — precedent already set by commit `8e203f5`'s `pnlRowTint` extraction |
| 8 | `build_trade_history([t.to_dict() for t in broker.trade_log])` — a full O(n) trade-log rescan, called at 14 sites, several as consecutive route handlers in the same file, several dashboard-polled | 14 | Trivial — one memoized `current_trade_history()` wrapper, invalidated on the existing `bump_generation()` signal |
| 9 | Diagnostics `check_*` DB-read preamble repeated 5×; a 6th (`check_confidence_input_coverage`) has no `sqlite3.Error` guard at all, so 5 siblings degrade to `UNKNOWN` on an unreadable store while the 6th propagates an exception | 6 | Low — one `read_or_unknown()` helper |
| 10 | Frontend: apply-button async handler (disable → "Applied ✓" → "Apply failed") duplicated 5× in one file | 5 | Low |
| 11 | Frontend: series→event market-grouping block, 13+6 identical lines including a copied justification comment, duplicated across 2 files | 2 | Trivial |
| 12 | `market_analyst_agent`'s 3 `analyze_*` LLM-call wrappers share 11 AST-identical lines (including an unused exception binding — a copy-paste fingerprint) | 3+2 | Low |
| 13 | `tools/soak_analyzer.py`'s `by_component` preamble, 3 copies — 2 read an absent key as `UNKNOWN` when it should be `0`; the 3rd copy in the **same file**, 80 lines above, already has the correct `.get(component, 0)` handling. **Already tracked** in `docs/open-decisions.md:43-44`; not a new finding — listed for completeness only, per this project's own "act on a tracked line or ask, never re-plan it" rule. | 3 | (parked — see §14) |
| 14 | Small AST-identical helper twins (`_float`, `_parse_ts`, `_today`, `_maybe_rollover_day`, `_git_head`, etc.) — `_parse_ts`'s two copies sit in the *same package* (`services/market_events/`), the clearest unjustified case | 7 pairs | Trivial |
| 15 | `services/kalshi/public.py`'s chunked-batch-fetch loop, 4 near-identical copies; one correctly diverges (`Optional` vs. required SDK field) with the reasoning documented in only one copy | 4 | Low priority |
| 16 | Config section access: both `cfg["x"]` and `cfg.get("x") or {}` in use for the same sections, including both idioms **in the same file** for `advisory` | 3 sections | Low priority |

**What's genuinely not a problem, confirmed rather than assumed:** the
`_connect()` idiom itself (§9 intro); `now = now if now is not None else
time.time()`'s 74 sites (a deliberate testability seam, extraction would
be net-negative); retry/backoff/rate-limiting (**already centralized
correctly** — `services/http_client.py:call_with_backoff` is the single
stack, `services/kalshi/transport.py`'s alias is verified to be `is`-equal,
not a second copy that could drift — this is the pattern the rest of the
repo should be measured against); `tools/quality_audit/`'s 11 scanners
(well-factored, shared `source.iter_python_files`); `fetchJSON`/
`sideAdjustedPrice` (already correctly extracted — finding 7 is about the
*formatting* half that was never given the same treatment).

---

## 10. External research — how real systems are built, and what's proportionate here

### 10.1 Comparable auto-trading system architectures

**Freqtrade** (the most popular open-source crypto trading bot, thousands
of users) — a **single continuous process**, a throttled sequential loop
(fetch open trades → OHLCV → strategy → order-state → exits → entries),
**SQLite by default**, dry-run and live share the identical loop. This
app's overall shape is not exotic for its class.

**Hummingbot** — single event loop, a central `Clock` ticking every
component in deterministic order; the engineering effort goes into
order/`in_flight`-state correctness (tracking an order *before* submitting
it, so an API timeout can't orphan it), not infrastructure.

**NautilusTrader** (the most production-grade system surveyed) — seven
components communicating only through an in-process message bus; **the
core is deliberately single-threaded** for backtest-live parity;
persistence/logging run as separate background tasks and **"the engine
publishes values even if persistence fails."** This is the directly
transferable pattern for this app's `tick_executor` contention: not more
processes, but the decision path made structurally unable to be blocked
by persistence/reporting — exactly §4's recommendation, independently
arrived at by both this app's own data-layer research and by reading how
the best comparable open-source system solves the identical problem.

**Jesse** — the honest cost line for "just add Postgres+Redis": Jesse
needs them specifically because it splits into multiple processes (web
dashboard vs. workers) and the message broker exists to ferry updates
between them. **If a system stays single-process, that entire
infrastructure layer is dead weight it doesn't need** — which is exactly
this app's current situation, and exactly why §5/§7 recommend against
Postgres/Redis as defaults.

**Event-driven pattern literature (Kafka/CDC/read-replica sources,
directionally consistent across sources):** the universal principle is
hot path (data→decision→order) kept minimal, cold path (analytics,
reporting) consuming the same events asynchronously — "the hot path
reacts, the cold path remembers." The mechanisms real systems use to
enforce this (Kafka, replicas, CDC) all answer multi-process/multi-host
problems this app does not have; the *principle* is universal, the
*infrastructure* is not proportionate here.

**Conclusion, independently reached by two research streams**: single
process, FastAPI+asyncio, SQLite-per-concern is a defensible, mainstream
shape at this project's scale. Do not adopt Kafka, Redis Streams,
Postgres, TimescaleDB, or a full process split on architectural fashion.
Do adopt the one pattern every surveyed system actually uses: keep the
decision path on one thread/process, make persistence and reporting
asynchronous and unable to block it.

### 10.2 predictionmarketspicks.com — tooling and methodology audit

A sports/econ/commodities picks site (fundamentally different signal
source — model-vs-market price gaps, not order flow) whose entire
published pipeline is **fair value → EV gate → fee band → half-Kelly**:

- Explicit fee modeling everywhere: Kalshi's `0.07·P·(1−P)` taker fee is
  quoted directly, with a "dead zone" (~3–5pp fee band around fair value)
  below which a signal is not actionable at all.
- Independent per-domain fair-value models (options-implied probability
  via Black-Scholes inversion, trailing-vol commodity models, Poisson
  scoreline models for correlated combos) — the part that does **not**
  transfer to a generic whale-flow strategy, since it assumes the event
  itself can be modeled.
- **Track-record transparency worth adopting outright**: every signal
  timestamped and written to a database *before* the market settles, then
  auto-graded against the real resolution; reported metrics are win rate,
  **flat per-contract P/L** (explicitly separating "was the signal
  predictive" from "was the sizing good" — precisely the decomposition
  §3's answer needs), and **Brier score**, specifically chosen to prevent
  win-rate inflation from betting on heavy favorites; losing/paused
  engines are shown alongside winners, not hidden.
- Liquidity/quality guards (minimum recent volume, bid-ask tightness) as
  signal-tier gates.

**What transfers directly to this app**: the EV gate itself (the
already-tracked pricing/edge gap, §3), the fee-band "dead zone" concept as
a cheap mechanical implementation of the 0.60–0.95 finding, Brier-score +
timestamped-auto-graded reporting as the primary track-record instrument
(this app already persists candidates and has a calibration pipeline —
adding per-signal-variant Brier/P&L-per-contract reporting is nearly
free), flat per-contract P/L as the research metric, half-Kelly driven by
computed edge rather than raw confidence.

**What doesn't transfer**: the domain-specific fair-value models
themselves (this app has no equivalent options-IV/Poisson input for
arbitrary Kalshi categories), cross-platform arbitrage (needs Polymarket
access), and their batch cadence (daily picks vs. this app's realtime
order-flow decisions).

Full source list and direct quotes: retained in the underlying research
agent's output for anyone picking this up — every claim above traces to
either `predictionmarketspicks.com/tools/guide`,
`predictionmarketspicks.com/track-record`, or Burgi/Deng/Whelan (2026),
"Makers and Takers: The Economics of the Kalshi Prediction Market,"
https://www.karlwhelan.com/Papers/Kalshi.pdf.

---

## 11. Cross-reference — what this audit extends vs. what's genuinely new

This project already tracks a large amount of relevant context; this audit
is written to extend and validate it, not duplicate or contradict it
without saying so explicitly.

1. **Frontend framework**: already decided (§8.4), verified unexecuted.
   This audit's contribution is independently re-confirming the choice was
   right and quantifying exactly why (VDOM hand-roll, focus-restore hack
   on the trading-confirmation input, the 196-global-export correctness
   gap) — not proposing a fresh option set.
2. **"Dedicated charts/graphs module, possibly server-rendered via
   Plotly/Matplotlib"** — an already-open `ROADMAP.md` item (2026-08-22).
   §8.4's uPlot/Lightweight-Charts recommendation directly answers it
   (client-side canvas, not server-rendered — Plotly was independently
   ruled out on bundle-size grounds by the same reasoning that produced
   the frontend framework choice).
3. **"Move analytics/advisory computation out of the live tick loop"** —
   an already-open `ROADMAP.md` item (2026-08-21). §4's process-split
   recommendation is the concrete, measured, sequenced version of this
   exact idea — today's evidence (§2.1's ~52% median blocked-window
   figure, and 24/24 monitor samples showing `/api/quality/summary`
   taking ≥6.5s) makes it considerably more urgent than when it was
   queued.
4. **A logical data/trade layer contract already exists**
   (`docs/data-layer-analysis-layer-contract.md`, 2026-08-30) —
   ingest/WS/settlement vs. trade/history/logging, enforced by
   `tools/soak_analyzer.py`'s BLIND/FAIL/UNKNOWN verdicts. This is real,
   working *logical* separation — but it is a soft contract inside **one
   process sharing one thread pool**. Tonight's live evidence
   (`/api/quality/summary` timing out) is direct proof the logical
   contract alone does not prevent resource contention; §4's physical
   split is the natural next step on top of it, not a competing idea.
5. **"Flatten the config surface"** and **"retroactively move already-
   stable flat files into their concern's folder"** — both already-open,
   already-scoped `ROADMAP.md` items, complementary to but distinct from
   this audit's DRY findings (config-surface flattening is about knob
   count; §9's findings are about duplicated logic).
6. **"Per-module data-consumption audit + report"** — an already-open
   `ROADMAP.md` item (2026-08-22) described as "the systematic,
   one-organized-report version is still not started." This document
   materially advances that standing want; worth recording that
   connection explicitly rather than treating tonight's audit as an
   unrelated new initiative.
7. **Legal/deployment/auth/kill-switch-sizing gaps** are already fully
   tracked in `ROADMAP.md`'s "Path to production" as human-decision gates.
   This audit does not re-propose solving them, but §4/§12 note where a
   future service-split or deployment-target decision would interact with
   them.
8. **The edge/mispricing strategy gap already has substantial prior
   internal work** (`docs/prediction-market-strategy-alignment-plan.md`,
   2026-08-09) — the fee-awareness gap, the FLB/taker-cost asymmetry, and
   critically, a **designed and partially built** `market_analyst_agent`
   for exactly this purpose (§3.4). This audit's contribution is
   connecting that existing design to §3's arithmetic and naming the
   cheapest concrete next step (pre-whale-price anchoring), not
   proposing a new agent.
9. **Kelly sizing, sigma-vetted significance testing, price-band
   filtering** are already implemented and reasonably rigorous
   (`services/stats_power.py`). Nothing in this audit proposes reinventing
   this.

---

## 12. Is this a rewrite?

**No — not the core architecture. A bounded, sequenced remediation program
against specific, measured problems, with two genuinely open design
questions carried forward rather than answered here.**

The user's framing explicitly kept "comprehensive rewrite" on the table,
and that consideration is preserved, not dismissed — but every piece of
independent evidence gathered tonight points the same direction, and
presenting a rewrite as equally supported would be dishonest to the
evidence:

- The **process/storage/frontend-tooling architecture** (single FastAPI
  process, one SQLite file per concern in WAL mode, no ORM, an esbuild
  bundle without a second framework-specific compiler stage on top of
  it — see §8.4's correction on this point) is independently validated as
  proportionate by direct comparison against three real open-source
  trading systems (§10.1) — including the most production-grade one
  surveyed. Rewriting this shape would be discarding something the
  external evidence says is *already correct*.
- Every measured defect in this document is **localized and individually
  fixable**: two routes need to stop being polled every 6 seconds (§6.3
  #1); two thread pools need to be deleted as a migration already in
  progress completes (§8.1); one table needs to move off SQLite (§5.2,
  now including a real row-dropping defect this document's own review
  cycle found, not just the originally-cited lock faults); a frontend
  framework migration that's already fully designed needs to actually
  start (§8.4); one arithmetic gate needs to be added to the entry path
  using data already collected (§3.3). None of these require touching the
  other 95% of a ~40,000-line codebase — but "specific" does not mean
  "correctly scoped on the first pass": this document's own adversarial
  review caught one of these five (the DuckDB justification) resting on a
  misattributed fault row, which is itself evidence for "targeted fixes,
  done carefully, with independent review" rather than "no further
  scrutiny needed" — not evidence against the localized-defect pattern
  itself, which held up under that same scrutiny.
- The DRY findings (§9), while real, are concentrated in one path (config-
  apply/advisory) and are the kind of thing a targeted refactor closes in
  days, not the kind of systemic entanglement that forces a rewrite.

**What a "full rewrite" framing would actually cost, stated honestly**: a
solo maintainer re-deriving ~40,000 lines of already-debugged domain logic
(fee formulas, Kelly sizing, the whale-scoring pipeline, settlement
resolution, the entire safety-gate stack) from scratch, re-discovering
every incident already fixed and documented in git history (the no-side
inversion bug class, the WAL-mode incident, the zero-volume denominator
bug, dozens more), while the real, live, currently-degraded production
system (§2) continues running unfixed the entire time. Every research
stream that looked at a specific subsystem — DRY, hand-rolled-vs-framework,
data layer, external comparison — independently concluded "targeted fix,"
not "start over," and each of those conclusions is backed by its own
in-section evidence (file:line citations, live measurements, direct
comparison against real external systems), not by the fact that four
separately-prompted agents happened to agree — agreement produced by a
shared framing from one orchestrator is not independent replication, and
this document does not lean on it as if it were.

**Two genuinely open, unresolved design questions worth carrying forward
explicitly** (not decided here, per this document's own pre-brainstorming
scope):

1. **Is `market_analyst_agent` still advisory-only, or has it been wired
   into real trade decisions since its 2026-08-09 design?** (§3.4) — this
   materially changes how urgent §3.3's edge-gate work is, and needs a
   direct confirmation, not an inference from a `grep`.
2. **If the two-process split (§4.4) and the frontend migration (§8.4)
   both land, does the resulting shape still deserve the label "one
   application," or has it become several coordinated small services that
   would benefit from being designed as such from the start** — e.g.,
   should process B eventually be a genuinely separate deployable unit
   with its own repo/CI, once a real deployment target (an already-open
   Path-to-production gap) gets decided? This audit's answer is "not yet,
   and not by default" — but it's a fair question to revisit once §4
   ships and the app has a real deployment target, not before.

---

## 13. Prioritized action plan

Ordered by measured payoff ÷ effort, cross-referencing every finding
above. This is a plan of *what a future session should brainstorm/design/
implement*, per this project's own process — nothing here is executed by
this document.

**Tier 1 — do first, days not weeks, no new infrastructure:**

1. Stop polling `/api/quality/summary`, `/api/candidate-log/summary`,
   `/api/confidence-calibration/report` on the 6s dashboard timer (§6.3
   #1). Removes the dominant, currently-active cause of degradation.
2. Fix the three safety-adjacent DRY findings: auto-apply honoring
   `declined_ids`, `RiskManager`'s missing zero-bankroll guard, and
   centralizing the duplicated table DDL (all §9.1) — each its own small,
   careful, TDD'd change given what they touch.
3. Bound `resolved_signals_with_factors()` and `candidate_log`'s
   `population_gate_summary()` reads (§5.2) — the same pattern PR #424
   already used for a sibling function.
4. One `paginate()` FastAPI dependency closing 8 unbounded-`limit` routes
   at once (§9.2 #5).
5. `services/db_helpers.py:add_column_if_missing()` — mechanical, 11 call
   sites (§9.2 #4).
6. Scope/remove `event_live_data` from `/api/state`, fix or retire its
   dead ETag (§6.3 #2, #3).
7. `services/alerting/alerting.py`'s 3 discarded task handles (`:91`,
   `:270`, `:294`); `http_client.py:430`'s bare `httpx.AsyncClient()`
   (§8.3).

**Tier 2 — medium effort, needs its own design/review cycle:**

8. Finish the `aiosqlite` migration; delete `tick_executor`/
   `_scoring_pool` as callers drain (§8.1).
9. Move `raw_trades`/`index_ticks` to DuckDB (§5.2) — the single highest-
   leverage data-layer change, surgical, not a platform migration.
10. `services/config/apply.py:apply_suggestion()` — the 6-site config-
    write extraction (§9.2 #3); writes `config/settings.yaml`, wants its
    own TDD cycle.
11. Add the pre-whale-price EV gate to the entry path (§3.3 #1–2) — the
    highest-leverage strategy change, using data already collected.
12. Add markout measurement for realized-edge tracking (§3.3 #3) — pure
    measurement, do before #11 or alongside it.
13. Execute the already-designed Preact frontend migration
    (`docs/archive/lane-8-frontend-dashboard/plans/2026-08-25-frontend-modularization.md`, §8.4)
    — sequenced after Tier 1's backend fixes given the causal link between
    the two.
14. Adopt `prometheus_client` for observability instrumentation only,
    keeping the SQLite store (§8.3).
15. Convert `services/kalshi/websocket.py`'s reconnect loop to the
    `async for connection in websockets.connect(...):` pattern together
    with `process_exception` for fatal-vs-retryable reconnect
    classification (§8.3) — these are one change, not two: the current
    `async with`-based loop never reaches `process_exception` at all.
16. Frontend DRY sweep: `centsHTML()`/`pctHTML()` formatters (§9.2 #7),
    apply-button handler extraction (§9.2 #10), series/event grouping
    extraction (§9.2 #11) — precedent already set by commit `8e203f5`.

**Tier 3 — larger, sequence after Tier 1/2 land and are measured:**

17. The two-process split (§4.4) — do this *after* Tier 1's polling fixes,
    since they remove most of the load that makes it urgent; re-measure
    before deciding how much isolation the residual demand justifies.
18. Push more state over the existing dashboard WebSocket, moving toward
    `/api/state` as cold-start-snapshot-only (§6.3 #5).
19. `market_analyst_agent` code cleanup (§9.2 #12) and category-specific
    fair-value anchors for the edge gate (§3.3 #4) — once #11's core
    mechanism is live and measured.

**Explicitly not recommended, with the evidence against each:** Postgres
or Redis as defaults (§5.2, §7), a message bus or CDC pipeline (§4.3,
§10.1), a three-way process split (§4.4), OpenTelemetry before a real
deployment target exists (§8.3), APScheduler (§8.2), a frontend framework
other than the already-decided Preact (§8.4), rebuilding
`market_analyst_agent` from scratch (§3.4), or a full rewrite (§12).

---

## 14. Open questions and directives for continued research and planning

Per direct instruction, these are retained explicitly rather than resolved
here — this is the list a future `superpowers:brainstorming` session
should start from, formatted to slot directly into `docs/open-decisions.md`'s
one-line-plus-next-action convention when that happens.

- **Now that "one SQLite file per concern, no shared DB, no ORM" is no
  longer a `CLAUDE.md`-mandated rule (PR #429, see the post-merge note at
  the top of this document), does §5's per-concern SQLite-fitness verdict
  still reflect what you actually want, or does removing that constraint
  open up options (e.g. a broader consolidation, a different default
  engine) this document didn't evaluate because it was reasoning inside
  that boundary?** · you (design call) · 2026-09-02.
- **Is `market_analyst_agent` still advisory-only?** (§3.4, §12) · confirm
  directly against current `strategy_engine.evaluate()` call graph, not by
  inference from a `grep` · you or me · 2026-09-02.
- **`game_state.db`'s 7.3× 24h size swing (122MB→885MB)** (§2.3) — is this
  the expected growth-then-hourly-prune cycle, or a genuine unbounded-
  growth symptom caught mid-cycle? · check against `series_watcher.prune()`'s
  own cadence and a longer sampling window · me · 2026-09-02.
- **The two-process split's write-path proxying** (§4.4 caveat 1) — does
  process B proxy config/trading-enable writes to process A, or does the
  frontend call A directly for those specific routes? Needs a design
  decision before implementation, not assumed either way.
- **Route-by-route verification that every route moved to process B reads
  only from DB/config, never `services/app_state.py`'s in-memory `state`**
  (§4.4 caveat 2) — this is real, non-mechanical work and should be
  planned as its own task, not assumed to fall out of the split for free.
- **Whether DuckDB's operational footprint (a second query engine, even
  if embedded/no-server) is worth it, and what its real scan-speed/
  compression payoff on this specific 27GB file actually is** (§5.2) — the
  commonly-quoted 10-50×/5-10× figures are industry-typical estimates, not
  a benchmark run against `raw_trades`; get a real number before
  committing. The audit's answer leans yes given the file size and the
  *corrected* lock-contention evidence (237 error-level row-dropping
  faults, not the originally-misattributed 159), but this is worth an
  explicit go/no-go with a real benchmark, not treated as self-evidently
  correct.
- **The 237 `raw_trades` `flush`-path lock faults that drop rows outright**
  (§5.2, found during this review's own adversarial pass — not in any
  earlier draft) — root-cause via `superpowers:systematic-debugging`
  before any DuckDB migration, since moving the table doesn't
  automatically fix a lock/drop race in the write path that feeds it · me
  · 2026-09-02.
- **Backup/disaster-recovery interaction with the DuckDB and retention
  recommendations** (§5.2) — `data/backups*` is ~34GB, unaudited here;
  does `services/backup/backup.py`'s own read of `series_watcher.db`
  contend with the same locks as the finding above, and what does a 27GB
  migration or a `rejection_events` retention change do to backup size/
  restore cost? · you or me · 2026-09-02.
- **Frontend test tooling for the Preact migration** (§8.4) —
  `frontend/package.json` has no test framework today (esbuild+eslint
  devDeps only); the migration plan should address how the new component
  tree gets tested, not assume it inherits coverage the current vanilla-JS
  code doesn't have either · whoever picks up §8.4's Tier-2 item.
- **Security/auth posture was not audited in this pass** — the real-
  trading-enable route, the `anthropic` API key, `.env` secrets, the
  Basic-Auth public tunnel, and CORS config are all real surface this
  document does not cover; §4.4's write-route correction (moving
  `/api/advisory/.../apply` back to process A) is a narrow, related fix,
  not a substitute for a real security review of the two-process split
  once it's designed.
- **The `event_live_data` scoping decision** (§6.3 #2) — three concrete
  options given (on-demand endpoint, rendered-only scope, delta-keying);
  which one depends on how the markets/whale panels actually use it today,
  not yet investigated at that level of detail.
- **`tools/quality_audit`'s indiscriminate worktree scanning** (§2.5) —
  should it skip peer-session worktrees by default, or is manual filtering
  an acceptable/deliberate cost? Small, standalone tooling question,
  separate from the app architecture itself.
- **`docs/archive/lane-1-kalshi-ingestion/research/2026-08-27-application-wide-rest-vs-ws-inventory.md`
  is now partially stale** (§6.1) — two of its findings are fixed; worth a
  dated addendum or supersession note the next time someone works from it,
  so a future reader doesn't re-investigate what's already resolved.
- **`ROADMAP.md`'s "Revisit the 5s dashboard polling model" item's ETag
  claim is now known to be wrong under real conditions** (§2.4) — worth
  correcting in place next time that item is touched, so it doesn't keep
  citing a mitigation that doesn't actually fire.
- **Category-specific fair-value anchors for the edge gate** (§3.3 #4) —
  explicitly deferred as a later, optional step; which market families
  are worth building one for first (the audit suggests the 15-minute
  crypto/index series as the cheapest, mirroring predictionmarketspicks'
  own tool) is an open design question, not decided here.
- **Whether process B (§4.4), once real, should become a genuinely
  separate deployable service with its own repo/CI** (§12, open question
  2) — explicitly not decided; revisit once §4 ships and a real deployment
  target exists.
- **Every Tier 2/3 item in §13** is, by construction, a planning-pipeline
  candidate (design → plan → implement) rather than a decided task — this
  document is the research stage input to that pipeline, not the pipeline
  itself.

---

## Appendix — sources and raw evidence

**Live app evidence** (this session, 2026-09-02, `https://kalshi-whale-poc.ddev.site:8443`):
`GET /api/health/pipeline`, `/api/health/faults` (multiple queries),
`/api/quality/summary` (30+ calls across two independent probes),
`/api/observability/summary`, `/api/health/storage`, `/api/state`;
`ddev exec -s fastapi python -m tools.quality_audit`; `git worktree list`;
`gh issue list`; direct `Read`/`grep` of `services/`, `main.py`,
`frontend/src/js/`, `config/settings.yaml`, `ROADMAP.md`,
`docs/next-action.md`, `docs/open-decisions.md`,
`docs/data-layer-analysis-layer-contract.md`,
`docs/prediction-market-bot-research.md`,
`docs/profit-maximization-assessment-2026-08-15.md`,
`docs/prediction-market-strategy-alignment-plan.md`.

**External sources** (fetched/searched live this session; the sourcing
agent's own scratch output carried fuller quotes per-claim, but that
output was ephemeral session scratchpad and is not committed — every
claim this document makes from these sources is restated with its
citation inline in §3/§10 above, so this list is for locating the
primary source directly, not a pointer to a richer copy that no longer
exists):
https://www.freqtrade.io/en/stable/bot-basics/ ·
https://www.freqtrade.io/en/stable/sql_cheatsheet/ ·
https://hummingbot.org/blog/hummingbot-architecture---part-1/ ·
https://nautilustrader.io/docs/latest/concepts/architecture/ ·
https://nautilustrader.io/docs/latest/concepts/message_bus/ ·
https://github.com/nautechsystems/nautilus_trader ·
https://github.com/jesse-ai/jesse ·
https://blog.openalgo.in/event-driven-architecture-in-algo-trading-platform-3a2957ff11a6 ·
https://hackernoon.com/designing-trade-pipelines-with-event-driven-architecture-and-apache-kafka-in-financial-services ·
https://predictionmarketspicks.com/tools/guide ·
https://predictionmarketspicks.com/track-record ·
Burgi, Deng & Whelan (Jan 2026), "Makers and Takers: The Economics of the
Kalshi Prediction Market," https://www.karlwhelan.com/Papers/Kalshi.pdf ·
https://medium.com/@simomenaldo/understanding-order-flow-toxicity-58fa317b3d01 ·
https://multicoin.capital/2026/02/17/adverse-selection-rules-everything-around-me/.

**Four parallel research agents' full scratch reports** (not committed —
ephemeral session scratchpad; every load-bearing claim from each is
restated with its evidence inline in this document, so this document is
self-contained and does not depend on those files surviving):
DRY/duplication audit, hand-rolled-vs-framework audit, data-layer/service-
boundary/polling/caching audit, external-architecture-and-strategy
research. Each ran independently, opus-model (the external-research/
strategy track on Fable 5.1), with read-only tool access and explicit
instructions not to edit any files.

**Review-cycle record**: this document went through the full self-review →
independent adversarial review → consolidation cycle required by this
project's "nothing advances on one pass" HARD RULE before being finalized.
The adversarial review (a genuinely separate Agent call, no memory of the
drafting session, re-deriving every checked claim from primary sources —
live `curl`, direct SQLite queries, `git log`, in-container library
source) found 21 confirmed claims, 9 falsified, 9 overstated/unverifiable,
and 8 real scope gaps; every one is reflected in this revision. The merged
fix list and GO decision are recorded in the companion document,
`docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-consolidation.md`.
