# Next action

**FLEET RESUMED 2026-09-06 ~14:00, all 4 peers working.** (Resumed ~35min
late: the pause was chained in 1h legs because `ScheduleWakeup` caps at
3600s, and **the chain died silently after leg 1 with no error** — see
memory `chained-wakeups-silently-die`. For any future multi-hour pause:
write the absolute target time into this file first, and check `date`
against it on every wake rather than trusting the chain.)

**In flight right now — do not duplicate any of these:**
- `0d` — `#532` purge. **Was on backup verification, NOT yet executing**
  as of last report; will report before anything destructive.
- `c4` — PR #640's independent adversarial review (dispatched to a fresh
  memory-less agent, raw output to be posted as its own PR comment).
- `49` — migration step 1 slice: 147 open issues → lane table (5 parallel
  batches, then its own review cycle).
- `ea` — health watch + migration step 1 slice: 79 specs + 97 research
  docs → lane table.
- coordinator — migration step 1 slice: 62 plan files → lane table
  (dispatched); PR #640 self-review posted; `phase:spec` label added.

**Visualization deliverable — DONE** (David asked for a diagram of the
workflow architecture + anti-drift overview):
https://claude.ai/code/artifact/f7e8fdc9-4fbf-4eab-b38e-4b19b601f737
Covers the 9 lanes as a data-flow spine on the runtime substrate, the
Lane→Initiative→Task hierarchy, `concern:hotpath` crossing lanes with
the 7-PR evidence, the 5 anti-drift rules, the migration gate, and the
review record including both failures. Republish the same scratchpad
file path to update it in place.

**Measured finding — effect confirmed, MECHANISM NOT ESTABLISHED.** A
large-tier backup (`POST /api/backup/run?tier=large`, ~169s) coincided
with `last_tick_duration_sec` freezing at 90.61s, recovering to 1.87-3.2s
after. Completeness signals stayed at zero throughout (no drops, queue
depth 0) — transient cost, not data loss.

**The first explanation was wrong and is ruled out.** Both `0d` and `ea`
initially attributed it to `sqlite3.backup()` being "synchronous and
blocking by design" (`backup.py:199-200`, a real quote). But that call
is already correctly isolated: `backup.py:285`/`:333` both
`await asyncio.to_thread(...)` (`:275` says so explicitly), the manual
route does the same (`routes.py:61`), and `_maybe_run_large_backup` is a
`_SCHEDULER_TRIGGERS` entry (`main.py:771`) running in `_scheduler_loop`
— **not** in the trading tick (`main.py:990-991`, P8 Task 36). Zero
drops independently corroborate the event loop was never blocked.

**Leading hypothesis, unconfirmed:** SQLite lock contention — the backup
holds `candidate_log.db` while the tick's entry gates write to that same
file via `tick_executor`, so the tick waits on the lock while the loop
stays healthy. Falsifiable test: does the stall track entry-gate writes
specifically, or occur on ticks with no `candidate_log` write?

**Direct consequence for `#532`:** if that hypothesis holds, the purge
will do this harder and longer — a 31.4M-row DELETE holds write locks
far longer than a read-side backup. `0d` was told to expect tick stalls,
to treat nonzero completeness counters (not slow ticks) as the stop
signal, and to prefer batched/chunked deletes if `prune_gate()` supports
them, since smaller transactions release locks between batches.

> **`#532`'s purge — UNBLOCKED, runs on the resume signal. `0d` owns it.**
> Sequence worth preserving, because the gate worked exactly as
> designed: David authorized it to the coordinator ("the purge is
> allowed to run"); `0d` **declined the coordinator relay** — it had
> stated twice that for this one action no relay would ever suffice
> however accurate, per the `#578` precedent, and it held that line
> against a message shaped like authority. It was not pushed, not
> re-relayed, and not run from another session. David then instructed
> `0d` **directly in its own conversation**, which satisfied the bar.
>
> **The distinction that matters on resume:** David's direct word is the
> *authorization*; the coordinator's resume ping is only an *operational
> timing signal*. Pinging `0d` to resume is not granting anything.
>
> `0d`'s protocol, already agreed and unchanged: re-verify the backup is
> current → re-verify the scoping predicate live immediately before
> touching anything (**stop and report if the count has drifted
> materially from 31,429,358**) → execute via `prune_gate()` only, never
> ad hoc SQL → before/after counts + "every other gate whole" invariant
> + `integrity_check` + health check through the **real nginx-proxy
> path** (the docker healthcheck lies) → post the full before/after
> durably to `#532` → idle. Any step that looks wrong: stop, report,
> leave it stopped.

**Coordinator:** `autotrade-36` (chain: `1f`→`48`→`01`→`05`→`36`, one
continuous session). Verify identity by direct reply before trusting a
name, in either direction.

---

## Safety (check every session start)

`strategy.auto_exit_enabled: false` and `risk.max_daily_loss_pct: 0` in
`config/settings.yaml`, both **uncommitted** (David's own edits) — they
must stay uncommitted and unchanged. `kalshi_account.trading_enabled`
stays `false`. Kill switch is TRIPPED by design (`max_daily_loss_pct: 0`
trips on any flat-or-losing day); David confirmed that's fine.

Live app healthy at pause. One real outage earlier today (zombied
uvicorn worker from leftover `#586` repro scripts in the shared fastapi
container — 3rd occurrence of that class) was caught by `ea`, fixed via
a narrow `docker restart`, and verified recovered on the real
nginx-proxy path. Memory `fastapi-container-high-cpu-observed-2026-09-03`
broadened: it's *any* leftover script, not just pytest loops, and the
docker healthcheck lies — verify through the proxy, never loopback.

---

## Where the work stands

### Lanes initiative (David's main ask) — design DONE, migration NOT started

**PR #640 is open** (`docs/planning-lanes-design`) carrying the design
and its complete review record. Not merged: the PR-level review cycle
hasn't run yet.

The design: **9 package-bounded lanes**, `Lane > Initiative > Task`
replacing the stale `Track A/B/C` vocabulary, `concern:*` labels for
cross-cutting properties (event-loop hot path is a *label*, not a lane —
round 1 got that wrong and it was the central error of the whole cycle),
a 4-clause straddler rule, and anti-drift rules written as rules (three
of five are pointers to tooling `/checkpoint` already runs).

Review record, because the failures matter more than the outcome:
round 1 **NO-GO** (property-defined lane overlapping everything; its
supporting evidence false against actual PR file lists — 1 of 7 PRs, not
7; 9 packages assigned against their own docstrings; three wrong
citations; migration would have orphaned plan issues and broken hundreds
of path refs) → round 2 **GO-WITH-REQUIRED-FIXES** (10 items, lane list
survived) → recheck 1 **FAILED** (I had Lane 7's file layout inverted,
and my own rewrite silently dropped a section a prior review had
confirmed — the exact defect CLAUDE.md names) → recheck 2 **PASSED**.

**Migration is gated deliberately.** Approving the design does not start
it. Hard gates, carried out of the consolidation:
1. Step 1's ~238-row classification tables (146 issues, 62 plan files,
   97 research, 79 specs → lane + status) get their **own full review
   cycle** before step 2 labels anything. Evidence this is real: a sweep
   of 66 `services/` units found 1 omission and 7 rule-application
   inconsistencies *made by the rule's own author*.
2. **No file moves before `kanban_sync`'s Track touchpoints are fixed** —
   deleting `sources_tracks.py` while `__main__.py:26` imports it breaks
   every subcommand at import, including the sync `/checkpoint` runs.
3. If populating the tables forces a lane to be added/merged/removed,
   that's a scope change → back to a full review cycle.

### `#605` "fix the stall" — root-caused, two fixes live, deliberately still open

Went from "unexplained ~9.3-9.6s stalls" to three distinct proven
mechanisms. The instrument step (PR #632) found the capture mechanism
itself was structurally broken — it read the stack *after* the block
ended, so every stall traceback the app had ever recorded was garbage.
Its adversarial review then caught a real SIGSEGV risk in the first fix.
Within a minute of deploying the working mechanism, it caught real
stalls:
- **`gate_summary()`** undispatched sync call, 791ms over 258K rows →
  **PR #636 merged**, SQL `GROUP BY` rewrite, 2.63s→0.34s end-to-end.
- **`resolve_window()`** on the event loop inside the trading tick,
  contending with `flush()` on the same table; `busy_timeout` expiry
  swallowed by a bare `except sqlite3.Error` — stacking across up to 40
  tickers per pass, which fully explains the ~9s bursts with zero
  exceptions → **PR #637 merged**, dispatched via `tick_executor`.
- **`jsonable_encoder`** recursion on a large response payload → **#634,
  filed and unfixed**, route not yet pinned.

`#639` filed for the other sync `gate_summary()` callers. `#605` stays
open by design.

### Peers at checkpoint

- **`c4`** — `#605` arc above; both fixes live and verified deployed.
  Caught and self-corrected a real compliance gap on #637 (its
  adversarial review existed only as a paraphrase inside the
  consolidation; the raw artifact was posted retroactively with an
  honest preamble). Idle.
- **`49`** — `#631`/`#616` D1 **merged** (banded gate diagnostic,
  decoupled cache TTL, `check_gate_cost_bands` calibrated against all 12
  real E4 rows). Then reconciled the stale GitHub bookkeeping: `#488`,
  `#401`-`#406`, `#408`, `#322`, `#326` closed with merged-PR evidence;
  `#377` found already correctly closed. Flagged honestly that `#488`'s
  own live-validation checkbox was never formally completed. Idle.
- **`0d`** — still holding `#532`'s purge (31,429,358 rows) for
  **David's own direct word only**, not a coordinator relay, per the
  `#578` precedent. Checkpoint posted on the issue. Worktree clean and
  fully merged. Idle.
- **`ea`** — `#586`/`#627` merged and live; caught and fixed today's
  outage. Watch stood down. Idle.

---

## Next action on resume (in order)

1. **PR #640's own review cycle** — self-review, independent adversarial
   review, consolidation, each its own PR comment — then merge. It is
   docs-only but asserts a design, so it's in scope for the full cycle.
2. **Migration step 1**: generate the four classification tables. This
   is delegable and should be split by lane group across `c4`/`49` plus
   subagents — but the output is an artifact that gets its **own review
   cycle**, not something accepted on a subagent's completion claim.
3. **Then** step 2 (labels: `lane:1`-`lane:9`, `concern:hotpath`, delete
   the 8 stale `area:*` definitions and the stray
   `phase:implementation-plan` on `#75`-`#77`), step 3 (`kanban_sync`
   touchpoints), step 4 (file moves in lane-sized batches), step 5
   (retire `plans/README.md`).
4. **Then** the visualization deliverable David asked for: a Mermaid
   diagram of the 9 lanes, the Lane→Initiative→Task hierarchy, and
   `concern:*` crossing lane boundaries. A live board view stays
   deferred — `gh issue list --label lane:N` is the day-one answer, and
   a Projects `Lane` single-select field is real work that hasn't
   earned itself yet.
5. Unblocked side work if capacity allows: `#634` (pin the
   `jsonable_encoder` route), `#639`.

**Do not** start migration before step 1's tables are reviewed.

**`#532`'s purge is `0d`'s to run on the resume ping** (authorized by
David directly in `0d`'s own session — see the note at the top). Ping it
to resume like any other peer; do not re-authorize, do not supervise the
SQL, do not run any part of it from another session. Expect its
before/after report on `#532`.

### `feat/candlestick-volatility` — DECIDED 2026-09-06, closed out

David delegated this call ("you should decide"). Decision: **keep the
branch as a reference implementation, do not rebase, re-implement
against current `main` when prioritized.** Tracked as **#641** with a
forcing trigger so it can't drift (#611 needing a real volatility
measure, or the Lane 4 population pass — whichever first).

Worth recording *why* the first read was wrong: from the branch-audit
summary it looked like a stale 5-commit scrap. Reading the actual
content showed 13 commits, ~1,055 lines, a complete tested feature
including a commit that deliberately rewrote its own tests off mocks
onto real seeded data. The reason not to land it isn't quality — it's
that it predates three completed migrations it would regress (its own
`_connect()` vs `services/db.py`'s `register_schema`; a new background
REST scan during REST/event-loop stabilization; unmeasured prompt load
on `market_analyst_agent`, which sits on the whale-scoring hot path).
Same lesson as the branch audit's own: **decide from content, never
from a summary of content.**

---

## THE ACTION: bankroll-reset / re-enable-trading gate

Still **0 of 5**. 1. `#574` merged+live ✅. 2. Deploy confirmed ✅.
3. Undisturbed observation stretch ⬜ (window keeps reopening).
4. YES-side auto-exit profit (`#591`) ⬜ — headline corrected to
$60,276.44/202 trades; contamination confound *proven* via clean-vs-
contaminated re-pricing; but a real ~$58k gap remains unexplained on
clean data. Leading hypothesis (replay sampling cadence sparser than
live per-tick evaluation) is unconfirmed and needs a harder per-tick
replay. 5. No active data-completeness incident ⬜ — `#605`'s three
mechanisms now named, two fixed; `#634` open.

---

## Standing priorities (David, verbatim)

> "Right now the priorities are the data plane overall integrity and
> accuracy and near-zero latency, and also fixing the errors downstream
> of that so we can confidently turn trading back on... resetting whole
> tables and pruning table rows etc is totally allowed... as long as the
> math is right, I am okay starting from 0 for everything."

> "Remember to stay on track with the 2 priorities I gave at the start:
> the data-plane and the logged data integrity - no corruptions due to
> software problems, no contaminations due to mishandled logic."

On the lanes work specifically: prefer a **written rule** over a tool,
and a **pre-built tool** over anything hand-rolled — David's explicit
ordering. Destructive rebuild is authorized for the planning/docs/branch
layer, scoped away from `data/*.db` and safety gates.

---

## Standing lessons (apply, don't re-litigate)

- **A review that exists only as a paraphrase inside a later artifact is
  not a review.** Post the actual output as its own comment/document.
  Happened twice now — once cross-session, once inside a single
  session's own subagent chain (#637).
- **A revision that silently drops a previously-confirmed fix is itself
  a defect** — caught in this session's own lane-design rewrite, only
  because the recheck re-derived instead of trusting.
- **A self-review that checks its own reasoning but none of its own
  facts will pass a rotten artifact.** Round 1's did exactly that.
- **Ancestry checks lie about supersession — compare content.**
  `merge-base --is-ancestor` proves merged; it cannot prove un-merged.
- **A fault-log row that dedupes by message text holds only one capture**
  — polling once and calling it "the" cause missed that three distinct
  bugs were cycling through the same row (`#605`).
- **The docker healthcheck can report OK while the app serves nothing.**
  Verify through the real proxy path, never container loopback.
- **Never leave any script running unattended in the shared fastapi
  container** — not just pytest loops.
- **Never put a closing-shaped verb next to a bare `#N`** in a commit
  message pushed to `main`.
- **Distinguish CI-green/MERGEABLE from review-complete.**
- **Verify identity and state by direct reply or live check, never
  inference.**
- **No knob changes** without a measured bottleneck and its mechanism.
- **This file holds the single next action — rewrite it, don't append.**
