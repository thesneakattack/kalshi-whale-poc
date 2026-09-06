# Next action

**Coordinator:** `autotrade-36` (chain tonight: `1f`→`48`→`01`→`05`→`36`, one
continuous session). **Verify identity by direct reply before trusting a
name, in EITHER direction** — session names have changed on reconnect
multiple times tonight, sometimes with memory intact, sometimes not.

**`autotrade-1d` mystery RESOLVED:** it was the session David tasked with a
full branch/PR audit (`docs/branch-audit-2026-09-05.md`, on its own branch
`docs/branch-audit-2026-09-05`); its permission request to the coordinator
never got through (2-way comms genuinely failed), so it correctly saved the
audit unapproved. **The audit was Fable-assessed, spot-checked (8/27
superseded-branch claims independently re-verified, all held), and its
action plan EXECUTED 2026-09-06 on David's direct go:** local branches
82→16, 9 merged remote branches deleted, 24+3 content-verified-superseded
branches force-deleted (worktrees checked for liveness+dirt first — all
clean), `feat/candlestick-volatility` pushed to origin per its recorded
decision (stays unmerged), `feat/frontend-realtime-push` deleted + issue
#398 closed, the orphaned PR #505 adversarial review recovered and posted
as a PR comment (branch deleted), the stale #448 record corrected on the
issue and in `open-decisions.md`, PR #603 merged as the durable #601
benchmark record, and the orphaned #546 root-cause research doc **DONE**:
PR #633 merged (own full review cycle, GO at both stage and PR level from
fresh memory-less agents), pulled into primary at `3fb800b`, both source
branches deleted local+remote. The audit's methodology lesson is in
Standing lessons below.

**Safety, check every session start:** `auto_exit_enabled: false` and
`risk.max_daily_loss_pct: 0` in `config/settings.yaml`, both uncommitted
(David's own edits) — must stay uncommitted and unchanged.
`kalshi_account.trading_enabled` stays `false`. Never touch any of these
without David. **Kill switch is currently TRIPPED** (`risk.halted: true`,
`halt_reason` has retripped at least once at ~0% loss — confirmed this is
the *designed* behavior of `max_daily_loss_pct: 0`, not a bug: it trips on
any flat-or-losing day by construction). David said the halt is fine as-is;
no action needed unless he says otherwise.

**Infra:** `ddev-router` outage (Windows port-exclusion) resolved via WSL
restart earlier tonight — `windows-port-exclusion-breaks-ddev-router` memory
has the full signature if it recurs. Live app healthy, DB integrity
confirmed repeatedly throughout the night.

---

## Peers — current task and next goal

- **`49`** (was `07`) — **`#601`/`#576` both DONE and deployed live earlier.**
  **Now on `#616`** (edge-gate enablement prerequisites, spec D1 only —
  items 2/3 of #616 explicitly out of scope): traced the exact spec
  (`docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md`
  §D1, function `population_gate_summary_banded`), confirmed this is
  genuinely new shipped code (the only prior version was an uncommitted
  scratchpad prototype). Checked sibling functions first to preserve their
  existing SQL-side-GROUP-BY/async discipline rather than regress it.
  Checkpointed as **draft PR #631** when its implementation subagent hit a
  rate limit mid-edit (verified on-disk state was valid and tested — 62
  passing — before committing, not a blind commit). Done: the core banded
  diagnostic + route wiring. Disclosed gap blocking merge: the banded query
  costs ~2x the existing one and needs a decoupled cache TTL first. Not
  started: the `check_gate_cost_bands` diagnostics check. **Resumed on
  exactly those two items**, then full review cycle before leaving draft.
- **`0d`** (was `62`) — **`#599`/`#620` (the `#532` purge mechanism) both DONE
  and deployed live.** Condition-4 YES-side work is done for tonight (see
  gate section below), correctly stopped rather than force a fatigued
  per-tick-replay attempt. **`#532`'s actual pre-purge measurement is done**
  (live, read-only, against the real 31.7M-row table): purge target
  (pre-`#604`-cutoff `min_contracts` rows) = 31,429,358; post-fix sampled
  rows so far = 1,782 (~36k/day, matches design intent); zero non-
  `min_contracts` rows carry a non-1.0 `sample_weight` anywhere in the
  table's history (the "keep every other gate whole" invariant holds with
  zero exceptions); `rejected_candidates` (untouched) = 258,456 rows;
  `integrity_check: ok`. **Held before posting the checkpoint or taking the
  backup** — David paused mid-task to give `0d` direct instructions;
  resolve that with `0d` directly before doing anything on `#532`, don't
  assume the coordinator can just say "go."
- **`c4`** (was `32`) — **`#150`, `#618`, `#585`, `#530` all DONE.** Real
  outcomes worth knowing: `#150` needed no code fix (both named options were
  already moot; real hang mechanism stays genuinely unexplained without new
  instrumentation — stated plainly, not forced into a fix). `#618` was
  closed, not merged (independently re-verified: genuine GitHub conflict,
  the work was already superseded by a different commit lineage) — one
  small genuine nugget salvaged as PR #623. `#585`'s two event-loop-blocking
  fixes are live; checked against tonight's stall timestamps first and got
  a clean negative (neither call site fired during the stall window) —
  closes on independent merits, not as a root-cause claim; caught a real
  near-miss where a naive fix would have silently killed the whole
  calibration/auto-apply feature. `#530`'s sweep found `/api/quality/
  summary` already fixed elsewhere and `observability/routes.py` still
  live-reproduced (~7s stall) — ~61 other catalogued instances deliberately
  left untouched for later, not chased all in one night.
  **`#629` DONE, merged and deployed live** (PR #630, `e584c60`, confirmed
  via `WatchFiles`). Measured on a real-scale ~3M-row DB copy: the `/api/
  state` concurrency stall went from near-total starvation (1 sample
  through during a 5.95s call) to 300 samples through at ~1.3ms max during
  a 6.22s call — same magnitude fix as the already-closed `quality/
  summary` bug. Adversarial review caught the fix's own justification
  comment inverting an earlier lesson (code was always correct, only the
  written rationale was wrong) — corrected before merge, independently
  re-verified. This closes the full `#585` → `#530` → `#629` chain
  end-to-end, all three fixes deployed live (`#624`, `#625`, `#630`). One
  more harmless instance noted for the ~61-item backlog
  (`tools/kalshi_rate_limit_probe.py:303`, one-shot CLI, not touched).
  **Now on `#605` — David's direct "fix the stall" instruction.** Full arc:
  instrument → catch live → root-cause → fix. Two coordinator-supplied
  leads to check first: (1) `fault_log` dedup never updates
  `first_traceback` (per the #599 finding), so the garbage
  multiprocessing-bootstrap stall traceback may be a weeks-old first
  capture with every fresh one discarded since; (2)
  `loop_watchdog._capture_stall_traceback()` may capture the watchdog's
  own thread's stack, not the blocked loop's — `sys._current_frames()`
  keyed to the loop thread is the standard fix. Known constraints: stalls
  burst at ~9.3-9.6s just under the 10s handler timeout; timeouts fire
  with ZERO exceptions (rules out plain SQLite busy-wait); `#585`'s call
  sites ruled out by timestamp; hot-path instrumentation must have its
  cost measured before shipping. **Instrument step DONE: PR #632 open** —
  root-caused the capture mechanism itself as structurally broken
  (`_capture_stall_traceback()` read `sys._current_frames()` after the
  block already ended, plus `fault_log`'s frozen-first-traceback dedup —
  both coordinator-supplied leads confirmed real). Fixed via
  `faulthandler.dump_traceback_later` on a genuine separate OS thread,
  cost-measured (~99us/rearm, ~0.1% of a tick). Found + fixed a real
  latent race in `db.add_column_if_missing` along the way (own regression
  test). CI all green. **Self-review posted; adversarial review +
  consolidation still pending — not merge-ready per the PR's own body.**
  Coordinator pinged `c4` to confirm status before touching it, awaiting
  reply. This does not yet root-cause or fix #605's actual stall — it
  makes the next real occurrence observable for the first time; still
  need to catch one live and read `last_traceback`.
- **`ea`** (was `bd`) — standing watch, resumed. **`#627`/`#586` DONE,
  MERGED, deployed live** (`3db93d2`, confirmed ancestor of primary's
  current HEAD `3fb800b`) — self-review, real GO-verdict adversarial
  review (the reviewer wasn't dead, just took ~2.6h; `ea` corrected its
  own earlier "confirmed dead" call plainly), consolidation, CI green,
  merged. Full async-SQLite arc (`#585`→`#530`→`#586`→`#629`) is now
  entirely closed and live.

`ef` (original app-health watch owner) confirmed gone, not renamed.

---

## THE ACTION: bankroll-reset / re-enable-trading gate

David asked to be alerted when it's safe to reset the bankroll and
re-enable `auto_exit_enabled`. **Single tracker — do not create another.**
(The bankroll itself was already reset tonight per David's own direct
action, independent of this gate — see Safety above. This gate is
specifically about re-enabling `auto_exit_enabled`/live strategy trust.)

1. `#574` (exit-valuation fix) — ✅ merged, confirmed live.
2. Deployed + reload confirmed — ✅.
3. Observed live for a real stretch, no new exit-pricing anomalies — ⬜
   window reopened after tonight's reset; too short to call again.
4. Unexplained YES-side auto-exit profit addressed — ⬜ real progress, not
   resolved (detail below).
5. No active data-completeness incident — ⬜ `#579`/`#580`'s shared-pool
   hypothesis falsified with a real fix shipped (`#601`); `#150` closed
   with no fix needed; `#585`/`#530`/`#586` (a separate, newly-discovered
   class of event-loop-blocking bugs) mostly fixed tonight, `#629`/`#586`
   still mid-review, ~61 lower-priority instances still open.

**0 of 5 fully met**, but meaningfully more evidence-backed progress
tonight than the number alone shows.

### Condition 4 — YES-side profit (`#591`), current state

Mirror-bug ruled out; headline figure corrected from a double-counting bug
(202 trades, $60,276.44, 94.6% win rate — not the original $68,589/279);
naive-rule selection-bias falsifier and split-half robustness check both
done. Factor-isolation ablation found the composite replay diverges sharply
from real profit, but a controlled clean-vs-contaminated-price comparison
proved why: the **contamination confound is real** (pnl/composite flip sign
entirely when re-priced on clean data; `staleness`, which never reads
price, is identical between runs — internal-consistency proof). **But even
clean, a real ~$58k gap remains unexplained** for the tested subset —
leading hypothesis is the replay's sampling cadence being sparser than the
live system's real per-tick evaluation, unconfirmed. Resolving that needs a
materially harder real per-tick replay — correctly not attempted tonight
while fatigued. **Condition 4 stays open.** Full detail on issue `#591`.

---

## Standing priorities (David, verbatim)

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
CLAUDE.md's HARD RULE), (2) logged data integrity — no software-caused
corruption, no mishandled-logic contamination. `0d`'s YES-side work is
priority-2 work by this definition, not a side investigation.

**Lean-execution policy** (PR #587): self-review + independent adversarial
review + consolidation still required at every PR/pipeline stage, sized to
the content — shrink artifacts, never skip one.

`docs/open-decisions.md`: cleaned up in a Fable-model pass tonight (David's
delegation), 41 items decided across ~10 new issues + 11 decisions on
existing ones. Currently holds just **one** open line: `#615` (`main`
branch protection is genuinely, triple-confirmed OFF — GitHub Pro or a
public repo needed to restore it; interim manual `commits/<sha>/status`
checks are the standing, permanent practice either way).

---

## Standing lessons (apply, don't re-litigate)

- **Ancestry checks lie about supersession — compare content.** The branch
  audit found 14 branches that `git log origin/main..<branch>` called
  "undelivered" whose code was fully on `main`, shipped via squashed/
  rewritten/differently-named commits. `merge-base --is-ancestor` proves
  merged; it cannot prove UN-merged. Before calling a branch's work lost or
  undelivered, read `main`'s actual current files.

- **Never put a closing-shaped verb next to a bare issue/PR number in a
  commit message pushed straight to `main`** — GitHub's issue-linking regex
  doesn't care what comes after the number. Write "issue #N"/"PR #N", or
  separate a closing-shaped word (close/closes/fix/fixes/resolve/resolves)
  from a bare `#N` reference.
- **Commit hot-path benchmark scripts/raw output somewhere durable**, not
  just PR/issue prose — a benchmark in a throwaway worktree becomes
  unfalsifiable the moment the worktree's gone.
- **Dispatch subagents in parallel for independent pieces of a task list**
  — every session including the coordinator. A session can keep its own
  context on a standing job (e.g. a health watch) while a dispatched
  subagent handles a separate task end-to-end, including its own review
  cycle — this worked cleanly tonight (`ea`+`#586`).
- **Checkpoint/push regularly, but don't bombard GitHub with pushes/PRs/
  comments all at once** — several sessions hitting one repo's API risks a
  rate limit that stalls everyone.
- **Post durable findings to a PR or issue, never leave them only in
  chat.**
- **Verify identity and state by direct reply / live check, never by
  inference** — in both directions (assumed-fresh was actually continuous,
  and vice versa, both happened tonight).
- **No knob changes** without a measured bottleneck and its mechanism.
- **Distinguish `MERGEABLE`/CI-green from actually review-complete** — a
  PR can be conflict-free and fully green while still only carrying a
  self-review, no adversarial pass or consolidation yet. Check comment
  count/content, not just the merge/CI badges, before assuming something's
  ready.
- **This file holds the single next action and current state — rewrite it,
  don't append to it.** Full history is in `git log`/`git show` and the
  relevant PRs/issues — that's the durable record, not this file.
