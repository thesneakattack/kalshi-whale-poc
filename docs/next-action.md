# Next action — live coordinator state, 2026-09-04 ~23:15 UTC

Coordinator is **`autotrade-1f`**. Written from verified `git`/`gh`/socket
evidence and direct peer replies, not from the previous doc — which was
wrong in ways that cost real time (see "What the last revision got wrong").

## ✅ Cross-session `SendMessage` WORKS AGAIN — the outage is over

The previous revision of this file said messaging was dead and told every
session to stop trusting it. **That is no longer true and following it now
is actively harmful.** **All six peers replied** to a direct ping
(`36`, `d2`, `8f`, `df`, `21`, `64`) — the last within ~20 minutes, and its
delay was a deliberate finish-the-task choice, not a delivery failure.

Two-way delivery is confirmed in both directions. Coordinate normally.

Best datum on the *old* outage: `64` reports its own report-ready message to
`9e` returned success and was never acted on, while messages **to** `64`
arrived fine. That places the failure on `9e`'s receive side. Unexplained,
and not worth chasing now that `9e` is dead.

Keep these habits anyway, because they are correct regardless:
- Durable findings go to a GitHub **issue or PR comment**, never only to
  chat. Every problem below was caused by state that lived only in a
  session that then died.
- Verify a peer's *effect* (branch pushed, PR labeled, issue filed) for
  anything load-bearing. Not because messaging is broken, but because
  "X was verified by Y" is unfalsifiable unless Y is a readable artifact.
- The one delivery-failure clue on record (a peer's send resolving to a
  Remote Control endpoint instead of the local session) did **not** hold
  up: all six of tonight's sends described peers as "also connected via
  Remote Control" and five delivered fine. Cause of the earlier outage is
  still unexplained. Do not chase it; do not repeat the claim as fact.

## Live sessions (verified by socket→PID, not by ListAgents alone)

`ListAgents` can be **stale**: `d2` saw the dead coordinator `9e` listed as
live and nearly took orders from a ghost.

**The reliable zero-cost check** (found by `d2` while reviewing #575, which
falsified my first attempt at this — see below):

```sh
for s in /run/user/1000/cc-socks/*.sock; do p=$(basename "$s" .sock)
  [ -d /proc/$p ] && printf '%s %s\n' "$p" "$(readlink /proc/$p/cwd)"; done
```

It resolves every live session and its working directory without sending a
single message, and yields **worktree occupancy for free** — that is how
`8f`'s work on #410 was confirmed by effect (cwd
`.claude/worktrees/issue-410-impl`) rather than by its say-so, and how
`portfolio-87` is confirmed to be in `/home/davidf/code/portfolio`, a
different repo.

Two limits, both real, so do not overstate it:
- **Socket count alone proves nothing.** My first version of this argued
  "8 sockets = 7 peers + me, so `9e` is dead". `d2`'s *stale* roster also
  totalled 8 (7 peers + itself). The membership differed, not the number.
  What actually discriminates is mapping sockets to names — via the `from=`
  address of a received message, or cwd — not counting them.
- **Names appear nowhere in `/proc/<pid>/cmdline`**, so sockets prove a
  roster is stale without naming the corpse. `ListAgents` carries no cwd
  field, so the two sources are complementary, not redundant.

**`9e` is dead** — established by socket→name mapping, not by the count.

| Session | Role right now |
|---|---|
| `1f` | coordinator (this doc's author) |
| `df` | owns PR **#574**, author — merge on hold pending `36` |
| `36` | independent adversarial review of **#574** |
| `8f` | implementing **#410** (design settled in #571) |
| `d2` | owns PR **#575**, running its review cycle |
| `21` | standing watch; owns the live incident; filed **#576/#579/#580** |
| `64` | closed **#539**; owns **#577** + filed **#578**; designing the root fix |
| `portfolio-87` | different repo (`~/code/portfolio/`), not ours |

## 🚨 LIVE INCIDENT — whale prints are being lost (#579, #580)

**Open, mechanism partly unproven, actively recurring in bursts.** Owner
`21` (standing watch). This outranks everything else below.

- **14,172 trade-class messages permanently lost** = **0.518%** of trade
  traffic (`2,735,458` received / `2,721,286` processed). 100% trade-class;
  ticker/lifecycle/control clean. Ongoing in saturate-then-drain bursts
  since ~21:18 UTC.
- **Unrecoverable, and not visible as loss anywhere downstream.** A
  `QueueFull` drop in `_ingest_raw()` returns *before* the consumer
  dequeues, so the message never reaches `series_watcher.record_trade()`,
  never reaches `capture_writer`, never lands in `raw_trades`.
  `capture_writer.dropped_rows.raw_trades: 0` is **not** reassurance — it
  is 0 because capture_writer never saw them.
- **They are gate-survivors, not noise.** The queue-full check runs *after*
  `_gate_check_and_maybe_filter`, so below-threshold noise was already
  stripped into a separate counter.
- **The loss is biased, which makes 0.518% understate the damage.** Drops
  are conditioned on having passed the gate, and gate-passing correlates
  with genuine market activity — so loss concentrates in the higher-signal
  subset *because* those flood the gate. Compounding, not correlating.
- Separately, `queue_wait.lifetime.max_sec: 833.96` — a 13.9-minute wait on
  messages that were **not** dropped. A timeliness failure in its own right.
- Ruled out, each on evidence: not a reload regression (no
  `Started server process` since ~21:35 UTC); not a memory leak
  (`memory.current` 6.83 GB is **82% page cache** — `anon` is 1.08 GB and
  stable); not the 2026-09-03 container-contention precedent (clean `/proc`
  walk, no strays, nothing in D state); not handler timeouts (`record_trade`
  is the first line of `_process_stream_trade` at 0.09ms, long before the
  timeout-prone REST stages — so the count is 14,172, **not** 14,245).
- **Leading hypothesis, verified as structure but NOT as causation:**
  `services/tick_executor.py:80` is `ThreadPoolExecutor(max_workers=2)`, and
  its own docstring (root-cause report **C1**) names
  `candidate_ledger.claim()`/`record_decision()` — the per-whale-print
  critical path via `decision_bridge` — as sharing those 2 threads with
  `resolve_and_record` and capture-flush. Live: `settlement_resolver.pending`
  went 104 → 687 → **2322**, `busy: true`. **Falsifier stated in #579/#580:**
  per-task timing on `tick_executor`, or inbound trade-rate flat while drain
  falls. Not clean same-instant correlation yet — the queue drained while the
  backlog was still climbing.
- **No knob changes.** Raising queue capacity converts a counted drop into an
  invisible latency backlog — already a timeliness failure — and destroys the
  evidence. Any mitigation comes to the coordinator with a mechanism first.

## Decision in flight — David: scrap derived data, rebuild clean

David (2026-09-04, late): *"as long as the whale signal logs are accurate we
can scrap everything else and start clean."* Purge of the contaminated
derived data is authorized in principle; **the root fix is the deliverable,
not the purge.**

**Premise VERIFIED on the corruption axis.** `signal_log` does not fabricate:
316,258 rows, **32,915 with `price IS NULL`** — if `or 0.5` touched this path
there would be exactly zero, since falsy-coalescing destroys absence. Only
**1.25%** sit at exactly 0.5, against **29%** in `market_history.snapshots`.
`correct` comes from Kalshi settlement via
`settlement_resolver.resolve_from_market_results`, not from any app-derived
price. No `or 0.5` anywhere in `services/whale_stream/` or `signal_log.py`.

**Premise HOLED on the completeness axis** — see the incident above. Clean of
corruption, not clean of holes.

**Coordinator recommendation:** scrap the derived layers, keep the two
sources of truth. Discard `market_history.snapshots`, the calibration cache,
and `paper_broker` history (derived, contaminated, rebuildable). **Keep
`signal_log` and `series_watcher`'s raw payloads** — the latter is the
fidelity/replay layer, is what `df` used to re-price 214 exits for #574, and
is the only thing that could re-derive truth for #578's snapshots. Scrapping
it forecloses that permanently. **Fix #579 before starting the clean
dataset**, or the rebuild inherits the burst-biased hole from hour one.

## Open work

1. **PR #574** — `fix/no-side-exit-valuation`. NO exits priced at
   `1 - yes_bid` (the NO *ask*) instead of `1 - yes_ask`, paying $1.00/contract
   on an empty book as if settled. Owner `df` (author; David asked for the
   10x investigation directly). **Merge is held** until `36`'s independent
   cross-session adversarial review + consolidation are posted **as PR
   comments**. **Head is now `219a350`, not `c09b730`** — a review of the
   old head does not cover it. Rationale: four review rounds happened, but rounds 2 and 3
   each found defects *introduced by the previous fix* (round 3's was
   severe — $0.00 booked against a true $0.99 on 65 of 209 live markets),
   and only one review artifact exists on the PR. The current head has
   never been independently reviewed. Since then `df` found a **fifth**
   defect that round 4 missed: the zero-ask hole review reported in
   `forced_exit_quote` also existed in `sellable_quote` — the *automated*
   `check_exits` path — where `sellable_quote("no", 0.0, 0.0)` returned 0.0,
   i.e. the same $1.00/contract fabrication, still live after four rounds.
   Cause named by `df` itself: round 4's prompt was framed around
   `forced_exit_quote` because that is where round 3 pointed, so it
   inherited the author's blind spot. All four rounds are now posted as PR
   comments. `df` merges on `36`'s GO + CI green at that same head —
   nobody else touches the merge button.
2. **PR #575** — recovered 488-line `docs/MULTI_SESSION_CRASH_RECOVERY.md`,
   orphaned by a dead session and untouched for ~15h. Owner `d2`, which
   preserved it verbatim with provenance in the commit message. **Do not
   merge**: its review cycle has not run. `d2` is running it now.
3. **#410 implementation** — owner `8f`. Design settled and merged in
   **#571**: aiosqlite for `population_gate_summary()`, aiosqlite +
   `asyncio.to_thread` for `whale_calibration._build_report()`, **no third
   pool**. Do not redesign it.
4. **Issue #576** (filed tonight by `21`) — ticker-coalescing pending map
   starves independently of a healthy main queue, staling open-position
   prices >300s. Unowned. Matters directly to #574: stale prices are the
   input the new exit logic will trust.
5. **Issue #577 — fabricated bid values (root cause).** Owner `64`.
   `main.py:1109`,
   `whale_stream_handlers.py:345`, `main.py:495` build prices with `or 0.5`,
   inventing a bid whenever Kalshi sent none. `latest_asks` three lines
   below `main.py:1109` is built honestly and its own comment says why
   guessing is wrong. **Every guard in #574 defends against this invented
   value, and `main.py:495` feeds the `market_history` snapshots used as
   "independent" corroboration — so that corroboration is not independent
   of the defect.** Live: 209 active markets, 90 carrying the fabricated
   0.5, 65 with a real ask below it. Two further sites scoped out by `df`
   and still to be verified: `whale_simulator.py:110`,
   `analytics/market_analyst_orchestrator.py:117`. Fixing it touches
   `check_pending_fills`, netting EV, `market_history`, and the dashboard.
   **Sequencing: let #574 land first** — do not change the fabrication
   sites out from under a PR whose guards defend against them.

6. **Issue #578 — contaminated snapshots, David's call.** Up to **1.43M**
   fabricated 0.5 prices in `market_history.snapshots` (29% of a 7-day
   window, 97% of tickers). **Only 6,504 are provably fabricated** — a hard
   arithmetic lower bound, since `spread = ask - bid` and `ask <= 1.00` make
   `spread > 0.5` impossible for a real 0.5 bid. Truth between the two is
   **unrecoverable from that table**: no ask column, no substitution flag. So
   "purge all impacted rows" does not name a set — purging all 1.43M destroys
   genuine 0.5 bids at the *middle* of the probability range, which is where
   calibration correction matters most. **Re-derivation from
   `series_watcher`'s raw payloads is the uncosted option and must be
   measured before anything destructive.** Backup first.

7. **Root fix for #577 must go one layer deeper than `or 0.5`.** Two findings
   force it: (a) `or 0.5` fires on a **real `0.0` bid** and does so
   *type-dependently* — one live payload carried 146 `float` / 38 `str` / 25
   `None`, so float `0.0` becomes 0.5 while string `"0.0"` is truthy and
   stores correctly; `or` → `is None` is therefore **not** the fix; (b) the
   damage is unrecoverable not because the code guessed but because
   **nothing recorded that it had guessed**. Design must carry: producers
   never substitute; schema able to represent absence *and provenance*;
   consumer absence-handling; and a CI guard against falsy-coalescing on
   price fields. Note `confidence_calibration.py:527`'s guard needs **no
   change** — it is already correct and starts working once producers stop
   lying to it. A fourth producer path exists that #577's body did not list:
   `whale_stream_handlers.py:399` → `record_snapshot_from_ticker`.

8. **Issue #539 — closed out by `64`, keep open, no knob change.** Window
   result: 90 events / 17.86h = **5.04/hr** (95% CI [4.00, 6.08]). Baseline
   2.4/hr predicted 42.9, elevated 13.5/hr predicted 241 — **both excluded**
   (z ≈ +7.2 and −9.7). Residual ~2.1x baseline, stable, ongoing. Per-event
   attribution *is* possible (contra the resumption note):
   `capture_writer._lock_retry_counts` is per-store, incrementing in
   `_retain()` (capture_writer.py:374) once per fault immediately before the
   `fault_log.record` at :436 — 1:1 by construction. Live split:
   `raw_trades 8, rejection_events 2, rejected_candidates 1`, so **`raw_trades`
   is 73%** and the "most likely `rejected_candidates`" guess is unsupported
   (that guess came from `context`, which only shows the latest occurrence).
   `dropped_rows`/`overflow_dropped_rows` **0 on all three stores** — churn,
   not loss, so not a data-completeness failure. **No knob change**: the
   residual 2x has no identified mechanism. Next step is a rate-vs-throughput
   measurement across two regimes.

## ⚠️ Needs David — not blocked on anything finishing

1. **`config/settings.yaml` is dirty in the shared primary checkout**:
   `strategy.auto_exit_enabled: true → false`, uncommitted, with a dated
   rationale comment. This is the **live mitigation** for #574's bug. Six
   sessions share this checkout — any `git checkout`/`stash`/`reset`
   silently re-enables auto-exit on the live paper app. It survives only
   because nobody has run one. **Nobody may touch it without David.**
2. **The primary has 16 unpushed commits on `main`**, including `ef662c0`
   ("Update kelly_fraction_of_cap and add KXBTC15M"). Any branch cut from
   `origin/main` silently reverts that tuning. Needs a home; David's call.
3. **Re-enabling `auto_exit_enabled` after #574 merges is David's decision**,
   explicitly, after live observation. Merging #574 does **not** re-enable
   it. The unexplained YES-side profit below means the fix alone does not
   justify it.
4. **Issue #532** — `rejection_events` unbounded growth (25.8M rows,
   ~4.2x/week). #571's design confirmed this is the actual reason #410's
   query costs keep climbing *regardless of which fix lands*. Every fix
   tonight amortizes or relocates the cost; none stop the growth.
   Retention policy is David's decision.
5. **Unexplained YES-side edge.** #574's review could not attribute the YES
   half of auto-exit profit (279 exits, +$68,589) to the NO-side bug —
   those priced off the correct bid. A second source of apparent edge may
   exist and is not addressed anywhere.

## Live data — do not re-derive P&L from 2026-09-02..04

`data/paper_broker.db` is **already repaired**: 294 rows corrected via
`PaperBroker.correct_erroneous_close`, bankroll $104,100 → −$8,338, backup
at `data/backups/pre-no-side-exit-repair/paper_broker.db`. Portfolio flat,
kill switch halted. Corrected rows keep `excluded=1` **and their original
realized text** precisely so `build_trade_history` cannot parse the stale
figure — **do not un-exclude them.** (Precision, verified by `36` against
live data: the `(realized +N)` shorthand used in #574's body covers 251 of
the 294; the other 43 are negative, so the true invariant is "original text
retained", not the `+N` form. Backup intact: 2,174 trades, 0 excluded.
Live bankroll −8338.35, agreeing across `/api/state` and `broker_meta`.) Any per-series win rate,
calibration, or advisory output from that window was fabricated. True
whale-follow calibration is unremarkable: 985 known-outcome entries, mean
unit cost 0.554, settlement win rate 56.1%.

**Post-repair, the strategy shows no demonstrated edge**: 798 honest round
trips at **−$5,383**. The auto-exit bucket only looks profitable because it
is, by construction, the positions that had already moved favourably. Treat
this as the current honest baseline, not the 10x run.

## What the last revision got wrong (kept as a worked example)

This is why #2 in "Open work" exists — the recovered doc theorizes exactly
these gaps, and tonight supplied live instances of two of them:

- It recorded **#410 as assigned to `36`** and the **crash-recovery plan as
  assigned to `d2`**. Both sessions confirmed directly they never received
  those assignments; the outage ate the dispatch. `d2` did not even exist
  when the doc was written. Acting on that list would have chased two
  sessions for work they had never been given.
- It **omitted PR #574 entirely** — the largest open item.
- It said the crash-recovery draft was current "as of ~08:15 UTC". Actual
  mtime was 07:39:44Z, i.e. ~15h stale and a finished orphan, not
  work-in-progress. `d2` caught this by measuring instead of reading.
- Its "messaging is dead" guidance outlived the outage and had five
  sessions working under a protocol that no longer applied.
- Net effect: **three sessions independently converged on PR #574**, two of
  them intending to merge it. Caught only because the new coordinator
  pinged everyone before dispatching.

**Rewrite this file at the end of a session. Never leave it describing
finished work, and never leave an assignment in it that was not confirmed
received by the session named.**
