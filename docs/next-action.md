# Next action

**Coordinator:** `autotrade-36` (chain tonight: `1f`→`48`→`01`→`05`→`36`, one
continuous session — the SendMessage name changes on reconnect, memory
doesn't). **Verify identity by direct reply before trusting a name, in
EITHER direction** — a WSL restart tonight (Windows `winnat` port-exclusion
fix, see `windows-port-exclusion-breaks-ddev-router` memory) was survived by
all 4 peers' memory, but only 1 of 4 kept its SendMessage name.

**Safety, check every session start:** `auto_exit_enabled: false` and
`risk.max_daily_loss_pct: 0` in `config/settings.yaml`, both uncommitted
(David's own edits) — must stay uncommitted and unchanged.
`kalshi_account.trading_enabled` stays `false`. Never touch any of these
without David. **Kill switch is currently TRIPPED** (`risk.halted: true`,
"Daily loss limit hit", 2026-09-05T21:00:37Z) — working as designed on the
first paper loss after the reset, not a bug. David said this is fine as-is;
awaiting his direction on when/whether to clear it. A third, unexplained
config diff also sits uncommitted: `whale_watcher_kalshi.min_contracts.
KXBTC15M: 2500 → 2000` — provenance unknown, nobody on the fleet claims it,
flagged to David directly, not yet resolved.

**Infra:** `ddev-router` outage (Windows port-exclusion) resolved via WSL
restart — `windows-port-exclusion-breaks-ddev-router` memory has the full
signature if it recurs. Live app healthy, DB integrity confirmed.

---

## Peers — current task and next goal

- **`49`** (was `07`) — **`#601` DONE, merged and deployed live** (PR #617,
  `aedd8ad`, confirmed via `WatchFiles` + `merge-base --is-ancestor 6b32ac3
  HEAD`). Full arc: measured → 3 fix families benchmarked (PR #603) →
  Family 2 (ticker-scoped UPDATE, no new index needed — the table's
  existing composite primary key already covers it) implemented with TDD +
  self-administered mutation testing → adversarial review ran a 4000-trial
  independent differential test against verbatim pre/post SQL, zero
  mismatches, plus live `EXPLAIN QUERY PLAN` confirmation (232.4ms scan →
  0.0075ms index search) on the real 5.1GB file → 2 real gaps found and
  fixed before merge (a test that didn't actually assert `resolved_at`,
  caught via a mutant that passed with a wrong value; a structurally
  identical bug in `market_analyst_agent/per_market.py` filed as its own
  follow-up, **`#619`**, not fixed blind). Both call sites (settlement
  loop + the every-6s tick call) now fast. **Now on `#616`** (edge-gate
  enablement prerequisites): reading the full decision comment before
  starting spec D1's banded cost-aware gate diagnostic.
- **`0d`** (was `62`) — **`#599` DONE, merged and deployed live** (PR #614,
  `1453e63`, confirmed via `WatchFiles` + `merge-base --is-ancestor
  ee3719e HEAD`; adversarial review added `idx_faults_first`). Also fixed
  `soak_analyzer.check_event_loop_stalls`, found silently pinned to
  permanent FAIL by the same bug class. Condition-4 YES-side analysis is
  done for tonight (see gate section below) — correctly stopped rather
  than push a fatigued per-tick-replay attempt. **Now on `#532`'s backlog
  purge, mechanism-first**: caught and corrected its own mid-mistake
  (started writing the purge mechanism on `#599`'s branch, would have
  bundled two unrelated initiatives — reverted cleanly, moved to its own
  branch). **PR #620** (mechanism only, nothing invoked against real data
  yet): `candidate_log.prune_gate(gate_name, retention_hours, now,
  batch_size)` mirrors `market_history.prune()`'s exact shape, scoped to
  one named gate's `rejection_events` rows only, `rejected_candidates`
  untouched for any gate, not wired into any automatic sweep — a one-off
  tool for the manual purge, not a new standing policy. Self-review
  honestly flagged one unverified assumption (no index covers the
  `gate_name`+`rejected_at` filter; reasoned by analogy to `market_
  history.prune()`'s own unindexed age filter, not measured) — adversarial
  review dispatched specifically to verify that empirically. Already
  confirmed once tonight, independent of #620: `#604`'s sampling fix is
  holding live (`min_contracts` rows all carry `sample_weight=100.0`,
  every other gate `1.0`, zero exceptions across the table's history).
  Once #620 lands: full pre-purge checkpoint, then the coordinator's
  explicit go before executing, same pattern as `#578`.
- **`c4`** (was `32`) — free, two goals just assigned:
  1. Review **PR #618** — a 2-day-old completed branch (`fix/tier0-live-
     incident-remediation`, 7 commits: `_connect()` leak fixes across 5
     modules + a health-probe timeout bound) recovered from disk and pushed
     tonight. Explicitly unreviewed — needs the full self-review/adversarial/
     consolidation cycle before it's mergeable.
  2. Drive **issue `#150`** to an actual decision. What was chased tonight as
     a "new" stall emergency (`#605`) turned out to be a duplicate of this
     already-known, already-deferred issue: `asyncio.wait_for`'s timeout
     can't actually kill the underlying OS thread once running, so every
     handler timeout leaks a worker slot from the shared pool — confirmed
     live, magnitude matches exactly (~9.3-9.6s stalls against a 10s
     timeout). `#150` names two competing fixes (dedicated smaller thread
     pool, or root-cause the SQLite hang) — benchmark and compare them with
     the same rigor `49` gave `#601`, don't pick one blind.
- **`ea`** (was `bd`) — standing watch, broadened to general app health
  (`/api/quality/summary`, `/api/health/faults`, `/api/observability/
  summary`, `/api/health/storage`) plus `#579`/`#580`. Currently clean;
  briefed that `#150`'s stall pattern and a likely upcoming `#532` backlog
  purge are both expected, not fresh incidents. **Next goal:** keep watching
  on the same cadence, flag genuine deviations from documented baselines.

`ef` (original app-health watch owner) confirmed gone, not renamed.

---

## THE ACTION: bankroll-reset / re-enable-trading gate

David asked to be alerted when it's safe to reset the bankroll and
re-enable `auto_exit_enabled`. **Single tracker — do not create another.**
(Note: the bankroll itself was separately reset tonight per David's own
direct action, independent of this gate being met — see Safety above. This
gate is specifically about re-enabling `auto_exit_enabled`/live strategy
trust, which stays a distinct question.)

1. `#574` (exit-valuation fix) — ✅ merged `244372b`, confirmed live.
2. Deployed + reload confirmed — ✅ 2026-09-05T09:44Z.
3. Observed live for a real stretch, no new exit-pricing anomalies — ⬜
   window reopened after tonight's reset; too short to call again.
4. Unexplained YES-side auto-exit profit addressed — ⬜ real progress, not
   resolved (detail below).
5. No active data-completeness incident — ⬜ `#579`/`#580`'s shared-pool
   hypothesis falsified, real mechanism found and being fixed (`#601`,
   `49`); `#150` (separate mechanism) still open, being scoped (`c4`).

**0 of 5 fully met.**

### Condition 4 — YES-side profit (`#591`), current state

Mirror-bug ruled out; headline figure corrected from a double-counting bug
(202 trades, $60,276.44, 94.6% win rate — not the original $68,589/279);
naive-rule selection-bias falsifier and split-half robustness check both
done. Factor-isolation ablation (`0d`) found the composite replay diverges
sharply from real profit, but proved why: a controlled clean-vs-
contaminated-price comparison shows the **contamination confound is real**
(pnl/composite flip sign entirely when re-priced on clean data; `staleness`,
which never reads price, is identical between runs — internal-consistency
proof it's genuinely price contamination). **But even clean, a real ~$58k
gap remains unexplained** for the tested subset — leading hypothesis is the
replay's sampling cadence being sparser than the live system's real
per-tick evaluation, unconfirmed. Resolving that needs a materially harder
real per-tick replay; `0d` correctly declined to force that attempt while
fatigued tonight. **Condition 4 stays open.** Full detail and numbers on
issue `#591`.

---

## Standing priorities (David, verbatim, both nights)

> "Right now the priorities are the data plane overall integrity and
> accuracy and near-zero latency, and also fixing the errors downstream of
> that so we can confidently turn trading back on... resetting whole tables
> and pruning table rows etc is totally allowed... as long as the math is
> right, I am okay starting from 0 for everything."

> "Remember to stay on track with the 2 priorities I gave at the start: the
> data-plane and the logged data integrity - no corruptions due to software
> problems, no contaminations due to mishandled logic (like what caused the
> trade log issues, losses being counted as wins, etc)."

Two named priorities, read every open thread against both: (1) the data
plane itself (completeness/accuracy/flow-rate/timeliness/fidelity/speed,
CLAUDE.md's HARD RULE), (2) logged data integrity specifically — no
software-caused corruption, no mishandled-logic contamination. The NO-side
exit-valuation bug (`#574`) and the `#591` double-counting bug are both
already-caught instances of priority 2, not hypothetical — `0d`'s YES-side
work is priority-2 work by this definition, not a side investigation.

**Lean-execution policy** (PR #587): self-review + independent adversarial
review + consolidation still required at every PR/pipeline stage, sized to
the content — shrink artifacts, never skip one.

---

## Tonight's Fable-model decision pass (David's delegation, 2026-09-05)

David delegated the entire `docs/open-decisions.md` backlog for direct
decisions ("review open-decisions and next-action and make the decisions on
your own using fable"). Result, independently spot-checked by the
coordinator before trusting it (branch-protection status, the recovered
tier0 branch, and the Kalshi no-ask-sentinel finding all held up): 41 of
~42 lines decided, 10 new issues filed (`#606`–`#613`, `#615`, `#616`), 11
decisions posted on existing issues/PRs. Full reasoning lives on each
GitHub item, not duplicated here. Two things surfaced that need David
specifically:

1. **`main` branch protection is genuinely OFF**, not just API-unreadable —
   triple-confirmed independently (`GET /branches/main` → `"protected":
   false`, both via `gh api` and a raw `curl` bypassing `gh` entirely; a
   peer's own initial recheck hit the same old 403 by querying the
   different, Pro-gated `/branches/main/protection` sub-resource, then
   found the correct endpoint and confirmed it themselves too). The
   `branching-and-ci.md` doc's description of a configured required-
   status-check gate is stale. Interim practice (manually read
   `commits/<sha>/status` before every merge) is already standing
   behavior — restoring real enforcement needs GitHub Pro or a public
   repo. Tracked as `#615`.
2. The unexplained `KXBTC15M` config diff noted in Safety above.

`docs/open-decisions.md` now holds just these 2 open lines plus pointers to
every decided item's GitHub home — check there for the full list, not here.

---

## Standing lessons (apply, don't re-litigate)

- **Never put a closing-shaped verb next to a bare issue/PR number in a
  commit message pushed straight to `main`** — GitHub's issue-linking regex
  doesn't care what comes after the number ("resolved #597's followup"
  auto-closed PR #597 once tonight). Write "issue #N"/"PR #N", or separate
  a closing-shaped word (close/closes/fix/fixes/resolve/resolves, etc.)
  from a bare `#N` reference — applies to every commit message and PR body
  in this repo.
- **Commit hot-path benchmark scripts/raw output somewhere durable**, not
  just PR/issue prose — a benchmark run in a throwaway worktree produces
  numbers that become unfalsifiable the moment the worktree's gone.
- **Dispatch subagents in parallel for independent pieces of a task list**
  — every session including the coordinator. Doesn't change the
  self-review → adversarial-review → consolidation stage order.
- **Checkpoint/push regularly, but don't bombard GitHub with pushes/PRs/
  comments all at once** — up to 4-5 sessions hitting one repo's API risks
  a rate limit that stalls everyone, not just the one that caused it.
- **Post durable findings to a PR or issue, never leave them only in
  chat** — every real loss tonight was state that lived only in a session
  that then died.
- **Verify identity and state by direct reply / live check, never by
  inference** — `ListAgents` uptime, a doc's last-known state, and a peer's
  relayed claim have all been wrong at least once tonight, in both
  directions (assumed-fresh-was-actually-continuous and vice versa).
- **No knob changes** (queue capacity, worker counts, subscription scope,
  rates) without a measured bottleneck and its mechanism.
- **This file holds the single next action and current state — rewrite it,
  don't append to it.** Full history of tonight (every PR's blow-by-blow,
  every investigation's numbers) is in `git log`/`git show` and the
  relevant PRs/issues — that's the durable record, not this file.
