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
| `8f` | PR **#581** (#410) | `2eed9a7` pushed; CI + adversarial in flight — **killed** |
| `d2` | PR **#575** | self-review posted, NO-GO; adversarial in flight — **killed** |
| `64` | **#577** design, filed **#578** | design in progress, may be unposted |
| `21` | app-health watch | filed **#576/#579/#580**; final readings posted |

Also filed at shutdown: **#584** (kill switch non-functional). `8f` had not
confirmed its shutdown state when this was written - check `2eed9a7` and #581
for uncommitted or unpushed work first thing.

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
- **#581** (#410 aiosqlite split) — cycle unfinished; re-read CI from
  `gh api .../commits/2eed9a7/status`, never from memory.
- **#583** (this coordinator's #571/#567 correction) — revised `087c3ca` after
  an adversarial NO-GO; self-review, adversarial review and consolidation are
  all posted. Awaiting CI only.

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
