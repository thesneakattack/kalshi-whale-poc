# STANDING PRIORITY — David, 2026-09-05 ~09:0x UTC (container-local)

**"Right now the priorities are the data plane overall integrity and accuracy
and near-zero latency, and also fixing the errors downstream of that so we can
confidently turn trading back on... resetting whole tables and pruning table
rows etc is totally allowed... as long as the math is right, I am okay
starting from 0 for everything."**

This re-orders active work and relaxes one standing constraint. Read before
picking anything up.

**Ordering, explicit:**
1. Data-plane integrity/accuracy/near-zero-latency — **#577** (fabricated
   `or 0.5` bid values, the largest data-plane *accuracy* defect found
   tonight) and **#579/#580** (whale-print drops / `tick_executor`
   contention, *completeness* and *latency*). Both now top priority,
   in parallel — **#577 is unblocked from waiting on #574. Precision: this
   specific unblock is the coordinator's own scheduling call applying
   David's stated priority, not a sentence David said verbatim** — he named
   the data-plane category as top priority; the coordinator concluded #577
   qualifies and has no technical dependency on #574 (different files
   entirely), and reordered on that basis. Correct if this reasoning is
   wrong, not because it's misattributed.
2. Downstream-error fixes so trading can confidently resume — **#574**
   (NO/YES-side exit-pricing fabrication). Continues exactly as before,
   just now explicitly framed as tier 2, not tier 1.
3. **#575** (crash-recovery documentation) is **not** in either category —
   it is process documentation, unrelated to the data plane or trading.
   Let it finish merging (nearly done, self-contained), then its owner
   moves to tier-1 work rather than starting anything else outside this
   priority list.

**Relaxation, bounded:** resetting/pruning `data/*.db` tables broadly is
explicitly authorized once the underlying write path is verified correct —
this **simplifies #578** (the ~1.43M-row contaminated `market_history`
snapshots) from "measure re-derivation coverage, recover what's
recoverable" to "fix #577's write path, verify it, then purge" — the
careful recovery work `a2`/`62` already did (48.8% recoverable, 93.2% of
recovered rows a real 0.0 not 0.5) is not wasted, just no longer required
before acting. Same logic applies to **#532**'s `rejection_events` growth:
a blunt prune becomes acceptable once the write path stops logging one row
per sub-threshold print.

**The relaxation is NOT a blank check — order matters and now has one more
step:** verify the write-path fix first, then a **pre-purge checkpoint**
(David, 2026-09-05: "if things can be done before tables get emptied out,
work around it") — confirm nothing still needs the current pre-purge data
for analysis or evidence, take a full backup, then purge. Purging before
the fix is confirmed correct just lets fresh data get contaminated again
immediately; purging before the checkpoint destroys evidence that can never
be recovered once the tables are empty (backup aside). The checkpoint is
explicit, not implied by "the fix passed review" — confirm with whoever
knows the data best (currently `62`/formerly `a2` for #578) and with the
coordinator before the actual destructive step runs.
"As long as the math is right" is the gate on using this authorization, not
a suspension of it.

**Consequence for the bankroll-reset/re-enable-trading question David asked
to be alerted on:** unchanged in substance, but #578/#532 move from "design
questions" to "mechanical cleanup once #577 lands" — meaning the actual
gating conditions are now #574 (fully fixed, including the newly-found
YES-side mirror gap below) and #577, not the data-recovery question that
was previously a separate open design thread.

---


**Policy confirmed explicitly (David, 2026-09-05): the lean-execution amendment
(#587) applies to the full "nothing advances on one pass" research → design/spec
→ implementation-plan pipeline, not only PR review cycles.** Tonight's #577/
#578/#579/#580 work has run this pipeline informally (GitHub issue comments
for investigation and design, not formal `docs/superpowers/` staged
documents) — that's fine, don't retroactively formalize what's already done.
Going forward, any stage of this pipeline that produces its own artifact
still needs self-review + independent adversarial review + consolidation
genuinely present, but sized to the content: no restated context, no
ceremony, no artifact inflated beyond what the work actually calls for.
Shrink artifacts, never skip one — same rule as before, now stated as
covering the whole pipeline explicitly.

# SESSION RECONCILIATION — reduced fleet, 2026-09-05 ~08:5x UTC (container-local)

David closed several terminals ("removed a few sessions") and kept 4. Coordinator
is now `autotrade-05` (chain: `1f` -> `48` -> `01` -> `05`, all one continuous
session, no memory loss — only the SendMessage name keeps changing on reconnect).

**New lesson, learned the hard way this round:** `ListAgents`' "started Xm ago"
is **not evidence of a fresh, memory-less session**. The coordinator assumed all
4 remaining peers were brand-new workers based solely on that field and briefed
them as such — wrong for at least one of them (`autotrade-32` turned out to be
`autotrade-8d`/`df`, PR #574's author, mid-hold, with full memory). "Started Xm
ago" reflects when the *terminal/process* attached, not whether conversation
memory survived. Verify identity by direct reply, every time, same discipline
as the WSL-restart roster problem — this is the same failure shape one layer up.

**Confirmed identity mapping for the current 4-session fleet (by direct reply,
not inferred):**

| current | actually is | role |
|---|---|---|
| `autotrade-32` | `autotrade-8d` (was `df`) | PR #574 author, holding |
| `autotrade-07` | former `autotrade-7e` lineage (perf/I/O specialist from the earlier incident) | PR #574 independent reviewer — genuinely fresh to #574, dispatched its own fresh subagent |
| `autotrade-bd` | `autotrade-24` (was `d2`) | PR #575 owner, mid-revision (8/16 fix-list items done as of this write) |
| `autotrade-62` | `autotrade-a2` (was `64`) | was #577/#578 owner; **now also covers app-health watch** (see below) |

**Confirmed genuinely gone** (three independent testimonies, not one absence):
`autotrade-71` (was #574's prior independent reviewer — its work is superseded
by `07`'s fresh pass, not resumed) and `autotrade-ef` (was app-health watch
owner, #576/#579/#580). Nobody currently holds `ef`'s identity; `62`/`a2` has
taken the watch role in addition to #577/#578 since #577/#578 are both blocked
(pending #574, pending David) and have spare bandwidth.

**Two documentation-drift findings from `62`/`a2`'s status sweep, being posted
to GitHub now (do not treat this doc as the durable record for either):**
- **#582** was auto-closed by PR #583's title keyword (04:08:19Z) *before*
  #583's own stated condition — #581 merging and its figures being confirmed
  — was actually met (#581 merged 04:20:47Z, 12 minutes later). Zero comments
  on #582 ever. Needs reopening + the confirmation #583 promised, or an honest
  note about what's still unconfirmed.
- **#532**'s current figures (29.6M rows, 45.3 rows/sec measured over a 62s
  bracket -> 3.91M/day, 99.0% from one gate, the 4.2x/week-decaying-to-1.9x/week
  analysis) exist **only in this doc** — #532 itself has exactly one comment,
  from 07:31Z, citing the older 25.8M/4.2x figures. Exactly the failure this
  file's own closing rule warns about: state that should be durable on an
  issue, sitting instead in a file this file itself calls liable to rot.

Also: an unplanned `ddev` container rebuild happened during this session's own
pipeline-health check (~08:44Z container-local time) — recovered cleanly,
fastapi healthy, `0 oldest dropped` in the capture_writer retry warnings.
Cause not chased; noted in case it recurs.

---

# POST-RESTART STATUS — two-way comms re-established, 2026-09-05 ~03:3x UTC

**Coordinator identity update (~04:3x UTC):** was `autotrade-48`, now
`autotrade-01` after the coordinator's own terminal was accidentally closed
and reopened — same session, same full context, nothing lost, just a new
name. `48` is gone and will not respond; route to `autotrade-01`. If this
happens again, verify via a direct reply before assuming continuity, same as
every other identity claim tonight.

Coordinator was `autotrade-1f` before the WSL restart, then `48`, now `01`. All 8 pre-restart peers accounted
for and confirmed via direct reply, not `ListAgents` alone — a transient
duplicate session (`autotrade-a7`) appeared for ~2s during startup and vanished
on its own; harmless, but a reminder that a fresh `ListAgents` snapshot right
after a mass restart can show a ghost.

**Identity mapping (old name -> new name), confirmed by each session's own
reply, not assumed:**

| old | new | inherited memory? | owns |
|---|---|---|---|
| `d2` | `autotrade-24` | yes | PR #575 |
| `df` | `autotrade-8d` | yes | PR #574 (author) |
| `36` | `autotrade-71` | yes | PR #574 (independent review) |
| `21` | `autotrade-ef` | yes | app-health watch, #576/#579/#580 |
| `64` | `autotrade-a2` | yes | #577/#578 |
| `8f` | `autotrade-bf` | **NO — came back with zero memory** | PR #581 (#410) |

**The one loss from the restart: `8f`'s successor session lost all memory of
the #410/#581 work.** Nothing was lost from the repo's side — PR #581, its
branch, and all 3 comments (self-review, production-scale equivalence check,
full adversarial NO-GO with 10 findings) survived on GitHub untouched. The
session has been re-briefed to re-orient from the PR directly rather than from
memory. Two portfolio-named sessions (`portfolio-96`, `portfolio-cd`) both
confirmed they are on a **different, unrelated repo** (`~/code/portfolio`
infra/traefik work) and were never autotrade workers.

**Do not assume names above stay stable.** They are current as of this
banner's timestamp; verify via a reply if it matters later.

---

# CRASH RECOVERY — planned WSL restart, 2026-09-05 ~03:1x UTC

Written by coordinator `autotrade-1f` immediately before a **deliberate** WSL
restart. All 8 sessions, the ddev containers, and the live app went down at
once. This is the resume point. Read it fully before doing anything.

## 0. What survives, what does not

**Survives:** the filesystem — so all git commits (including unpushed ones on
local `main`), all working-tree changes, and every `data/*.db` are intact.
`config/settings.yaml`'s uncommitted `auto_exit_enabled: false` is **on disk**
and therefore still in force after restart. Good.

**Does NOT survive:** every session's conversation memory; any in-flight
subagent (unrecoverable, and none had returned); background tasks and CI
waiters; `/tmp` scratchpads (see §5); the app's in-memory queues.

**⚠️ The single most likely post-restart mistake:** the app's queues start
empty, so `ingest.dropped_messages` resets to **0** and
`settlement_resolver.pending` drains. **#579's 14,172 is a historical figure,
not a live counter.** A post-restart reading of 0 does **not** mean the defect
is fixed — it means the counter was reset. Do not close #579 or #580 on it.

The restart also **destroyed a natural experiment**: we were waiting to see
whether drops resumed as the settlement backlog drained, which would have
tested #580's hypothesis for free. That evidence is gone; #580's falsifier now
has to be met deliberately (per-task `tick_executor` timing, or inbound trade
rate flat while drain falls).

## 1. First five minutes

1. `ddev describe` — expect it down. `ddev start`. Then `ddev logs -s fastapi`
   and watch the **cold start**: a 33 GB `series_watcher.db` and a 4.8 GB
   `candidate_log.db` with empty page cache is exactly when a cold-start
   pathology shows. **#549** (fault_log WAL cold-start race) is a known one.
2. `GET /api/state` — confirm `running: true`, both WS streams connected.
3. **🚨 THE DAILY-LOSS KILL SWITCH IS NON-FUNCTIONAL — see #584.** The UTC
   rollover re-based `day_start_bankroll` to the *negative repaired* value
   (-8338.35) and cleared `halted` -> 0 **automatically, not by a human**.
   Nothing is trading, so nothing is at risk this instant — but there is **no
   working loss guard** if anything resumes before David resets the bankroll.
   Check this before anything is allowed to trade. Do not "fix" it by editing
   risk state; the bankroll reset is David's own action.
4. **Verify the safety state:**
   `grep -n auto_exit_enabled config/settings.yaml` must read **`false`**, and
   `git status --short config/settings.yaml` must still show ` M`. If either
   changed, something reset it — say so loudly, do not "fix" it silently.
5. `git status --short` and `git log origin/main..HEAD --oneline` in the
   primary. Expect ~20 unpushed commits on `main` (see §5).
6. `ls /run/user/1000/cc-socks/*.sock` + `/proc/<pid>` to see who is actually
   back. **Do not trust `ListAgents` alone** — it showed a dead coordinator as
   live earlier tonight. `readlink /proc/<pid>/cwd` names each session's
   checkout and gives worktree occupancy for free.

## 2. Session roster and where each was

Names change on restart. Re-derive ownership from **branches, PRs and issue
comments**, never from a remembered name. This table is who was doing what,
so the work can be reclaimed — not an assignment to a name that no longer exists.

| was | work | state at shutdown |
|---|---|---|
| `1f` | coordinator | this doc; PR **#583** open, revised, awaiting CI |
| `df` | PR **#574** (author) | CI green at `219a350`; **merge held** |
| `36` | independent review of #574 | pass in flight at `219a350` — **killed** |
| `8f` | PR **#581** (#410) | `37687b9` pushed (WIP); adversarial **NO-GO**, 10 findings |
| `d2` | PR **#575** | self-review posted, NO-GO; adversarial in flight — **killed** |
| `64` (now `autotrade-a2`) | **#577** design, filed **#578** | **posted** (comment 5549041032, labeled INCOMPLETE) — confirmed by the session itself post-restart; only this row was stale, the #578 coverage numbers below were already correct |
| `21` | app-health watch | filed **#576/#579/#580**; final readings posted |

Also filed at shutdown: **#584** (kill switch non-functional). All six workers
confirmed clean shutdown: nothing uncommitted, nothing unpushed, and every
piece of chat-only state posted to a PR or issue first.

**Both adversarial passes died to API rate limits, not to findings** - `d2`'s
confirmed exactly one item (S4) before dying; `36`'s finished primary-source
gathering and never wrote a review. Both posted INCOMPLETE comments. **Silence
on the unchecked items is not "found nothing".** A fresh pass must run from
scratch for #574 and #575.

**Anything marked "killed" returned no verdict.** If a PR comment claims a
review was running, it did not finish. Do not assume a GO.

## 3. Merge gates — nothing merges without these

- **#574** (`fix/no-side-exit-valuation`) — NO exits priced at `1 - yes_bid`
  instead of `1 - yes_ask`, paying $1.00/contract on an empty book. CI green at
  `219a350`. **Needs a fresh independent adversarial review + consolidation at
  the current head, posted as PR comments.** Five defects found across four
  rounds and *each fix introduced the next*; the fifth (`sellable_quote`'s
  zero-ask twin in the automated path) was found after round 4 said GO. Its
  standing consolidation comment says "four adversarial rounds → **GO**" but
  covers `c09b730`, **not** the current head — do not merge on it.
- **#575** (recovered crash-recovery draft) — `d2`'s NO-GO stands; ≥3 HIGH
  findings unaddressed.
- **#581** (#410 aiosqlite split) — **adversarial review returned NO-GO as
  submitted, 10 findings, posted in full** (comment 5549045515). F2 fixed and
  F5 partially fixed in `37687b9` (WIP, 43/43 affected tests passing); **F3,
  F4, F6, F7, F9 remain open** and **no consolidation is written**. Re-read CI
  from `gh api .../commits/37687b9/status`, never from memory — it was
  resolving normally at shutdown, which contradicts F1's "webhook silently
  broken" finding, so F1 may be stale or the breakage transient.
- **#583** (this coordinator's #571/#567 correction) — revised `087c3ca` after
  an adversarial NO-GO; self-review, adversarial review and consolidation all
  posted. **CI green (`success`) at `087c3ca`.** This is the one PR whose full
  cycle is complete; it is mergeable on return, and `Refs #582` means #582
  stays open until #581 lands and confirms its provisional figures.

## 4. Open issues, current as of shutdown

**#579** trade-class whale-print loss — 14,172 (0.518%), gate-survivors,
permanently unarchived, `QueueFull` in `_ingest_raw()` before the consumer.
Root cause **open**. The diagnostic-route hypothesis is **falsified** for that
burst (routes unhit for 78 min before it). · **#580** settlement backlog
104→2322, shares a 2-worker `tick_executor` pool with the whale-print critical
path; leading **by elimination**, falsifier unmet. · **#577** `or 0.5`
fabricated bids, 5 sites; root fix must be deeper than `or → is None`
(a real `0.0` bid is mishandled, and wire types are mixed: 146 float / 38 str /
25 None in one payload). · **#578** up to 1.43M contaminated snapshots;
**6,504 provably** fabricated is the only hard bound. **Re-derivation coverage
IS measured** (comment 5547552268): 14,246 of 1,433,666 sampled against
`series_watcher.book_snapshots` -> **48.8% recoverable** (4.0% within +/-5s,
13.4% +/-30s, 31.5% +/-300s); 51.2% have no raw coverage. Of recovered rows
**93.2% were a real `0.0` bid, not a real 0.5** - so purge collateral damage
drops from "up to 1.4M genuine values" to roughly **0.5% (~7k)**, which makes
a purge far less costly than this doc's earlier framing implied. · **#576**
ticker-coalescing starvation. · **#582** #571's cost mis-attribution (PR #583).
· **#532** `rejection_events` — see §6.

## 5. Traps specific to this restart

- **`/tmp` scratchpads may be gone.** A git worktree for PR #583 lived at
  `/tmp/claude-1000/.../scratchpad/wt-571`. Its branch
  `docs/571-cost-attribution-correction` **is pushed**, so no content is lost,
  but git may hold a stale registration: run `git worktree prune`.
- **~20 unpushed commits sit on the primary's local `main`**, including
  David's `ef662c0` (`kelly_fraction_of_cap` + `KXBTC15M`) and four
  coordinator docs commits (this file's history). They survive the restart.
  **Do not push them as a batch** — `ef662c0` is David's to push, and the docs
  commits belong on a branch. `origin/main`'s copy of this file is stale and
  still describes a messaging outage that ended hours ago.
- **~40 worktrees under `.claude/worktrees/`.** Under WSL2 `watchfiles` has no
  inotify and polls, so each one costs the live app real CPU on every reload
  cycle. If the app is slow after restart, check the reload watcher before
  blaming the app.
- Do not `rm`/`mv` any `data/*.db`. Prefer `POST /api/reset` over deleting
  `paper_broker.db`.

## 6. Decisions waiting on David — unchanged by the restart

1. **`auto_exit_enabled`** stays paused; re-enabling is his call after #574
   merges *and* is observed live. Merging does not re-enable it, and merging
   does not deploy it — the live app only updates when someone pulls.
2. **`ef662c0`** needs a home.
3. **Rebuild plan** — his stated intent: scrap the derived datasets, rebuild on
   `signal_log`. Premise **verified on corruption** (`signal_log` preserves
   NULLs — 32,915 of 316,258; 1.25% at 0.5 vs `market_history`'s 29%; `correct`
   comes from Kalshi settlement) but **holed on completeness** (#579).
   Coordinator recommendation: scrap `market_history.snapshots`, the
   calibration cache and `paper_broker` history; **keep `signal_log` and
   `series_watcher` raw payloads** (33 GB, the only re-derivation source);
   **fix #579 before starting the clean dataset**, or it inherits the same
   burst-biased hole.
4. **#532** — 29.6M rows, **99.0% from one gate** (`min_contracts`, which has
   ~23.8M resolved samples against a `min_samples=30` threshold). Coordinator
   recommendation: **sample `min_contracts` at write time, keep every other
   gate whole** — a retention window is backwards, since the fragile gates
   (`market_unresolved` at 21 resolved, `min_whale_winrate_pct` at 384) are 1%
   of volume. Measured growth **45.3 rows/sec → 3.91 M/day**; the "4.2x/week"
   multiplier **decays as the base grows while the absolute rate does not** —
   at ~3.9 M/day it is ~1.9x/week now, so a future reader will wrongly think it
   is easing.
5. **Unexplained YES-side profit** (279 exits, +$68,589) that #574 does not
   account for.

## 7. Standing rules that outlived tonight

- **Post to a PR or issue, not to chat.** Every serious loss tonight was state
  that lived only in a session that then died.
- **Never leave an assignment in this file that was not confirmed received by
  the session named.** The previous revision invented three, and three sessions
  nearly collided on #574 as a result.
- **Check the artifact against live state, from the right vantage point.** Five
  times tonight a claim was true when written and had quietly stopped being
  true — twice the claim was the coordinator's own, including a `grep` of a
  local working tree asserted as a fact about `origin/main`.
- **No knob changes** to queue capacity, worker counts, subscription scope or
  rates without a measured bottleneck and its mechanism.
