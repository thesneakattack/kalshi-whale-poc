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
live and nearly took orders from a ghost. The reliable check is
`ls /run/user/1000/cc-socks/*.sock` and testing each basename PID against
`/proc/<pid>`. Eight live sockets, all mapping to live PIDs, all accounted
for = 7 peers + coordinator. **`9e` is dead.**

| Session | Role right now |
|---|---|
| `1f` | coordinator (this doc's author) |
| `df` | owns PR **#574**, author — merge on hold pending `36` |
| `36` | independent adversarial review of **#574** |
| `8f` | implementing **#410** (design settled in #571) |
| `d2` | owns PR **#575**, running its review cycle |
| `21` | standing app-health/responsiveness watch; filed **#576** |
| `64` | closed **#539**; now owns **#577** (`or 0.5` root cause) |
| `portfolio-87` | different repo (`~/code/portfolio/`), not ours |

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

6. **Issue #539 — closed out by `64`, keep open, no knob change.** Window
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
kill switch halted. Corrected rows keep `excluded=1` **and** their original
`(realized +N)` text precisely so `build_trade_history` cannot parse the
stale figure — **do not un-exclude them.** Any per-series win rate,
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
