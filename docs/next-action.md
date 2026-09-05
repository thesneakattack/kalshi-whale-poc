# Next action

**Coordinator:** `autotrade-36` (chain tonight: `1f` → `48` → `01` → `05` →
`36`, one continuous session — the SendMessage name changes on reconnect,
memory doesn't). **Verify identity by direct reply before trusting a name**
— `ListAgents`'s "started Xm ago" is not evidence of a fresh session, in
EITHER direction: a WSL restart (2026-09-05, Windows `winnat` port-exclusion
fix, see `windows-port-exclusion-breaks-ddev-router` memory) was survived by
all 4 fleet peers' MEMORY, but only 1 of 4 kept its SendMessage name — ask
every time, don't assume either outcome.

**Peers and current task, as of this write, all reconfirmed post-WSL-restart:**
- `c4` (was `32`, chain `df`→`8d`→`32`→`c4`, PR #574 author) — **`#578` DONE.**
  Looped David in directly before executing (not just the coordinator's
  relayed go — right call for something this destructive). Purged via the
  real, production-tested `prune()` function (retention_hours=0, cutoff
  pinned exactly to the fix commit `da4b93a`'s timestamp): 5,049,063
  pre-fix rows deleted, 102 batches, 36.9s, 0 errors. Post-purge: 494,197
  rows remain, ALL confirmed post-fix (oldest row timestamp exactly at the
  cutoff), 0 hard-lower-bound fabricated rows, `integrity_check: ok`, app
  stayed up throughout. Deliberately skipped `VACUUM` (a separate,
  unapproved exclusive-lock operation) — file stays ~800MB with 84% of
  pages on the freelist, reusable but not reclaimed; pre-purge backup
  (`20260905T174550Z`) still available if ever needed. Free for next
  assignment.
- `49` (was `07`, kept memory AND name through the restart, separate
  lineage, independent reviewer) — **`#576` DONE, merged and deployed live**
  (PR #597, merge commit `70fc147`, pulled onto the primary and confirmed
  live via `WatchFiles` + `merge-base --is-ancestor 2546f5c HEAD`). Family
  B (independent scheduled flush) shipped over A (A's own benchmark showed
  it structurally can't help under the mechanistically plausible trigger —
  semaphore/resolve contention suspends the consumer before A's counter
  check ever runs). The adversarial review's benchmark-falsifiability
  followup was self-resolved after its recovering subagent didn't survive
  the WSL restart: `49` recovered the script from disk, re-verified it
  against the PR's actual shipped code, fixed a real `quality_audit`
  boundary finding along the way, added a smoke test, committed the raw
  benchmark output as a durable artifact (`33a83e2`). PR was briefly
  auto-closed by an unrelated coordinator docs commit's phrasing (see
  standing lessons), reopened cleanly, no state lost. **`#579`/`#580`
  shared-2-worker-pool hypothesis: FALSIFIED**, real numbers (PR #600,
  benchmark harness + raw output committed) — sub-second p95 under every
  current-state scenario including a synthetic reproduction of #580's own
  reported surge (p95 307ms vs. the 10s timeout); a positive control
  (re-adding the pre-#581 diagnostic routes) reproduces the original
  failure exactly, proving the harness genuinely detects the mechanism
  when present. **Real, previously-unknown root cause found for #580**:
  an unindexed full-table scan in `candidate_log.py`'s settlement path
  (`rejected_candidates WHERE resolved = 0`, no index, 258k+ rows) — caps
  throughput at ~2-3 tickers/sec vs. the 9.16/sec surge rate. Independently
  re-verified by both `49` and the coordinator (`EXPLAIN QUERY PLAN` shows
  `SCAN rejected_candidates`). Filed as **#601** with 3 competing fix
  families tabled, none implemented yet per the data-plane HARD RULE.
  `#579`'s original trigger stays genuinely unidentified; `#580` re-scopes
  to #601's real mechanism. Both issues stay open — only the shared-pool
  hypothesis is closed.
- `ea` (was `bd`, chain `d2`→`24`→`bd`→`ea`, PR #575 owner) — **standing
  watch, broadened 2026-09-05 from `#579`/`#580`-only to general app
  health** (David: nobody was covering this) — now also runs the full
  CLAUDE.md check order (`/api/quality/summary`, `/api/health/faults`,
  `/api/observability/summary`, `/api/health/storage`) on the same cadence.
  Coordinator's own pass found: normal-range event-loop stalls (few/min,
  mostly <200ms, one 3s spike), a brief SQLite lock episode ~20min prior
  (2 occurrences, nothing since), 2 observability warnings (`kalshi_client`
  rate-limit hits 6/23 samples, `settlement_edge.db` grew 2.7x/23.6h) —
  none urgent. **#599 filed**: `/api/health/faults`'s `hours=` window leaks
  one legacy fault signature's all-time count into any query — cross-check
  individual `faults[].last_seen`, don't trust `summary.total_occurrences`
  for that one signature (id 24009). `ea` independently re-verified all 4
  endpoints (not taking the coordinator's summary at face value) and added
  one clarification worth keeping: `/api/quality/summary`'s
  `diagnostics.overall: "fail"` is NOT a new incident — it's `series_funnel`
  flagging the already-documented pricing/edge gap (CLAUDE.md's own
  "Standing goal": KXBTC15M/KXETHD underwater after fees at entry,
  0.60-0.95 unit-cost band negative-EV, designed not implemented) plus
  sparse-series `unknown`s (zero closed positions in 24h). Don't mistake
  this `overall: fail` for a data-plane regression.
- `0d` (was `62`, chain `64`→`a2`→`62`→`0d`, `#577`/`#578` owner) — **YES-side
  auto-exit profit analysis (gate condition 4) — done for tonight, stopped
  at the right point.** Found and fixed a real O(n²) bug in its own
  analysis script first (8.75hr honest ETA collapsed to ~100s, verified
  identical output before trusting it). Survived a live bankroll reset
  mid-run cleanly (switched to `trade_archive`'s pre-reset epoch, disclosed
  a real limitation rather than risk a new discrepancy). Ran the controlled
  clean-vs-contaminated-price comparison: **contamination confound now
  proven** (pnl/composite both flip sign when re-priced clean, staleness
  identical as an internal-consistency check) **but a real unexplained gap
  remains even clean** (~$58k short of the fair target) — leading
  hypothesis is sampling-cadence, unconfirmed. Correctly declined to push
  into a real per-tick replay (materially harder, would need a fresh
  approach) rather than force a fatigued attempt. Condition 4 stays open;
  see detail above. Free for next assignment.

`ef` (original app-health watch owner) is confirmed gone, not renamed.

**Safety, check every session start:** `auto_exit_enabled: false` in
`config/settings.yaml`, uncommitted (David's own edit) — must stay
uncommitted and unchanged. `kalshi_account.trading_enabled` stays `false`.
Never touch either without David.

**Bankroll reset + risk lockdown (David, 2026-09-05 ~20:42Z):** paper
bankroll reset to a clean $10,000 (pre-reset trade history archived,
`trade_archive` `epoch_id=9`, "pre-reset 2026-09-05 20:42", 2,184 trades —
not destroyed). `risk.max_daily_loss_pct` set to `0` (also uncommitted,
same pattern as `auto_exit_enabled`) — reactive, not preemptive: trips the
kill switch on the first position showing any loss, does not block a new
position from opening in the meantime. `running` was briefly toggled
`false` then back to `true` at David's explicit request, specifically
because `state["running"]` gates whale-signal detection/logging itself
(`whale_stream_handlers.py:233`, verified in source) — pausing the loop
was found to ALSO stop signal logging, not just position-opening, which
David did not want. **No existing flag decouples "block new entries" from
"keep signal detection running"** — `running: true` + `max_daily_loss_pct:
0` is the current compromise; a real decoupling fix is a named, not-yet-
requested follow-up if David wants a true preemptive block later.

**Kill switch tripped as designed (2026-09-05T21:00:37Z, found by `ea`'s
resumed watch):** `risk.halted: true`, `"Daily loss limit hit: -1.2%"`,
equity $9,793.22 vs. the $10,000 reset — the `max_daily_loss_pct: 0`
setting above did exactly its job on the very first loss. Real trading
confirmed still off throughout (`trading_enabled: false`), paper-only.
Not the #584-style silent-clear bug (verified currently, actively halted,
not cleared). `ea` correctly did not touch it — clearing a halt is
explicitly David's own action. Awaiting David's direction on the halt.

**Infra: `ddev-router` outage RESOLVED (2026-09-05)** — traced to Windows
port exclusion (`winnat` dynamically excluding port ranges that collide
with Docker Desktop's WSL2 port-forwarder), fixed by David restarting WSL.
Confirmed post-restart: `ddev-router` `Up ... (healthy)`, `paper_broker.db`/
`market_history.db` both `PRAGMA quick_check: ok`, app live. Also explained
3 unexplained full-stack container restarts that night (all 4 peers
independently confirmed zero `ddev restart`/`stop`/`start` from their own
history) — see `windows-port-exclusion-breaks-ddev-router` memory for the
full signature and remedy if this recurs. `https://autotrade.webfoundry.dev`
(the separate `traefik` container) was the working access point during the
outage but goes down with everything else during the WSL restart itself.

---

## THE ACTION: bankroll-reset / re-enable-trading gate

David asked to be alerted when it's safe to reset the bankroll and
re-enable `auto_exit_enabled`. **This is the single tracker for that
question — do not create another one.**

1. `#574` (exit-valuation fix) merged — ✅ `244372b`.
2. Deployed live, confirmed reload (not just merged) — ✅ 2026-09-05T09:44Z,
   `WatchFiles` named the changed files, fresh server process,
   `merge-base --is-ancestor 244372b HEAD` true.
3. Observed live for a real stretch, no new exit-pricing anomalies — ⬜
   window opened 09:44Z, not yet long enough to call.
4. Unexplained YES-side auto-exit profit addressed — ⬜ **in progress, see
   below.**
5. No active data-completeness incident — ⬜ trending positive
   (`#579`/`#580` showed a clean 16-minute drain with zero drops after
   `#581`), not resolved; both stay open pending more evidence.

**0 of 5 fully met.**

### Condition 4 detail — YES-side profit

- Mirror-bug ruled out: checked directly against `trades.price`, zero of
  279 rows hit `price>=1.0`.
- **Headline figure was wrong, now corrected** (`62`, issue #591, verified
  independently to the cent against live `paper_broker.db`): "279 exits,
  +$68,589" double-counted 77 already-corrected rows whose stale
  `(realized ±X.XX)` ledger text was never rewritten by
  `correct_erroneous_close()` (it flips `excluded` and adjusts bankroll,
  not the ledger text). **Real: 202 trades, $60,276.44, 94.6% win rate**
  (77 excluded = $8,312.41 + 202 real = $60,276.44 = $68,588.85, confirming
  the mechanism, not just the correction).
- Selection-bias falsifier (naive "sell after +X%" across all 664 YES
  entries): finds real but far smaller money ($12–19k vs $60,276.44) — not
  pure artifact, not proof of genuine composite edge either.
- **Split-half robustness check DONE** (`62`, dispatched as a parallel
  subagent, 2026-09-05): cross-validated against #591's own figures —
  99+103=202 auto-exits, $19,204.93+$41,071.51=$60,276.44, verified
  independently to the cent by the coordinator. Naive-rule gap appears in
  BOTH halves (41.0%/20.0% of real) — not a single-period artifact.
  Independently pinned the underlying-bug timeline sharper than the
  original framing: #574 merged the day AFTER the window ended
  (2026-09-05T09:41:47Z), #577 closed entirely after
  (2026-09-05T10:19:40Z), #578 still open today — verified directly via
  `gh` by the coordinator, not just relayed.
- **David's decision: commission the larger analysis, both avenues.**
  Assigned to `62`/`0d` (same continuous session). Feasibility checked
  first, verified independently: **avenue 1 (out-of-sample window) is
  genuinely impossible** — the entire trade history is one ~2-day window,
  zero trades in 18.87h+ and still climbing; substitute is a labeled
  split-half *robustness, not validation* check. **Avenue 2 (factor
  isolation) is feasible and narrower than expected** — `analyst_divergence`
  and `series_track_record` proven zero-contributors from source (empty
  table; zero config weight), leaving `pnl` (done) + `sentiment` +
  `staleness` as the real ablation.
- **Factor-isolation ablation completed 2026-09-05, but result is
  INCONCLUSIVE, not a resolution of condition 4** — `0d` explicitly flagged
  this itself rather than let a dramatic number stand unqualified. Full
  composite replay (pnl+sentiment+staleness): **-$4,342.79**, sharply
  diverging from the real +$60,276.44 (pnl alone +$7,665; sentiment alone
  -$40,007; staleness alone -$44,510 — both net-negative in isolation).
  **Two real, unresolved confounds identified, not yet separated:**
  (1) 7.6% of the 198,962 snapshot-points walked are exactly `0.5` —
  measured contamination from the pre-#577-fix fabricated-price era, a
  real fraction of which are fake, not real, bids; (2) the replay only
  evaluates at `market_history.snapshots` cadence (~5-6s/ticker), far
  sparser than the live system's actual per-tick evaluation — could
  systematically under-fire relative to what really happened. **Do not
  treat -$4,342.79 as evidence the composite lacks edge** until one of
  these is fixed or the approach is explicitly acknowledged as unable to
  answer the question with available data. Full results + caveats posted
  to #591.
- **Controlled follow-up completed (2026-09-05): contamination confound
  now PROVEN, not just suspected — but it isn't the whole story.** Same
  291 entries, same formula, only the price source swapped
  (`series_watcher.book_snapshots`, confirmed clean, vs. the original
  `market_history.snapshots`): `pnl`-alone flips sign entirely (-$23,942.73
  clean vs. +$15,122.08 original, a $39,065 swing); full composite also
  flips sign (-$24,529.22 vs. +$10,541.39, $35,071 swing). Internal
  consistency check: `staleness` (never reads price) is IDENTICAL to the
  cent between both runs — proves the swing is genuinely price
  contamination, not a bug in the comparison itself. **But even clean, a
  real unexplained gap remains**: these 291 entries are only ~56% of real
  auto-exit profit (fair target ~$33,700-34,000 for this subset), and the
  clean replay still gives -$24,529 — ~$58k short, still wrong sign.
  Leading remaining hypothesis is the sampling-cadence confound (still
  unconfirmed, not a second proof) — resolving it needs a real per-tick
  replay, a materially harder undertaking `0d` correctly declined to start
  tonight rather than push a fatigued, ad hoc attempt at it. **Condition 4
  stays open.** Real progress stands (mirror-bug ruled out, headline figure
  corrected, contamination confound now proven) but the core edge-vs-
  selection-bias question is unresolved. Full write-up on #591.

---

## Standing priority (David, 2026-09-05 — reinforced later the same night)

> "Right now the priorities are the data plane overall integrity and
> accuracy and near-zero latency, and also fixing the errors downstream of
> that so we can confidently turn trading back on... resetting whole
> tables and pruning table rows etc is totally allowed... as long as the
> math is right, I am okay starting from 0 for everything."

> "Remember to stay on track with the 2 priorities I gave at the start: the
> data-plane and the logged data integrity - no corruptions due to software
> problems, no contaminations due to mishandled logic (like what caused the
> trade log issues, losses being counted as wins, etc)."

**Two named priorities, not one blended one — read every open thread against
both:** (1) the data plane itself (completeness/accuracy/flow-rate/
timeliness/fidelity/speed, per CLAUDE.md's HARD RULE), and (2) logged data
integrity specifically — no software-caused corruption, no mishandled-logic
contamination. The named failure pattern ("losses being counted as wins")
is exactly the NO-side exit-valuation bug (PR #574) and the #591
double-counting bug — both already-caught instances of priority 2, not
hypothetical. `0d`'s YES-side ablation is priority-2 work by this
definition (distinguishing genuine profit from a logic-contamination
artifact), not a side investigation — keep it framed that way.

- **Tier 1 (data-plane):** `#577` (fabricated `or 0.5` prices) — **done,
  merged, deployed, verified live** (392/392 current markets have a real
  price, zero at exactly 0.5). `#579`/`#580` (whale-print drops /
  `tick_executor` contention) — mitigated by `#581`, trending clean, not
  formally closed.
- **Tier 2 (downstream):** `#574` — **done**, see gate above.
- **Purge authorization** (bounded): resetting/pruning `data/*.db` tables
  is fine once the write path is verified correct. Order, unchanged:
  fix → verify → **pre-purge checkpoint** (confirm nothing still needs the
  current data for analysis, take a full backup, get an explicit go from
  the coordinator) → purge. Simplifies `#578` (contaminated
  `market_history` snapshots) from "recover what's recoverable" to "fix
  `#577`'s write path [done], then purge" — the recovery measurement
  already done (48.8% recoverable, 93.2% of those a real 0.0) isn't
  wasted, just no longer required first. Same logic applies to `#532`.
- **Lean-execution policy** (PR #587, merged): self-review + independent
  adversarial review + consolidation still required at every stage of any
  multi-stage pipeline, PR or otherwise — but sized to the content. Shrink
  artifacts, never skip one. No pre-merge courtesy pings required; a
  session holding a genuine GO can act on it without a second blessing.

---

## Open issues

- **`#579`** trade-class whale-print loss (14,172 historical, gate-survivors,
  `QueueFull` before the consumer) — root cause open; diagnostic-route
  hypothesis falsified for that burst. **`#580`** settlement backlog
  sharing `tick_executor`'s 2-worker pool — leading by elimination,
  falsifier unmet. Both mitigated by `#581`, neither formally closed.
- **`#578`** — **DONE, purged.** 5,049,063 pre-fix `market_history.snapshots`
  rows deleted via `prune()` cut exactly to the `#577` fix commit's
  timestamp (`da4b93a`), 102 batches, 36.9s, 0 errors. 494,197 rows remain,
  all confirmed post-fix, 0 fabricated, `integrity_check: ok`. `VACUUM`
  deliberately skipped (unapproved); backup preserved
  (`20260905T174550Z`). Was blocked earlier on `0d`'s YES-side ablation
  actively querying this same table live — resolved by waiting for that
  work to finish rather than racing it.
- **`#532`** `rejection_events` unbounded growth — 29.8M rows, 98.98% from
  one gate (`min_contracts`, ~23.8M resolved samples against a
  `min_samples=30` threshold — wildly oversampled). Measured rate spans
  18–32 rows/sec depending on method (not a single clean number); the
  "4.2x/week" multiplier mechanically decays as the base grows while the
  absolute rate doesn't — don't read a smaller multiplier later as
  improvement. Coordinator recommendation: sample `min_contracts` at write
  time, keep every other gate whole.
- **`#589`** — DONE via PR #592 (recorded in `open-decisions.md`, still
  genuinely open there pending `#578`'s purge decision). **`#590`** — DONE,
  PR #593 merged (`4c0e11b`): bounded single-assignment reaching-definition
  resolution closes both the ternary and intermediate-variable fabrication
  shapes; real remaining limits (reassignment, branch-scoped, cross-function)
  documented, not claimed as full coverage.
- **`#576`** ticker-coalescing starvation — **DONE, merged and deployed
  live** (PR #597, `70fc147`; observability-persistence half PR #594,
  `a2e9757`). Mechanism: `_consume_market_from` only services the ticker
  map when the trade queue happens to empty, unbounded under sustained
  load. Family B (independent scheduled flush) shipped over A per real
  benchmark numbers — see peer roster above for detail.
- **`#595`/`#596`** — DONE, merged (`9870b76`). Whale Watch Terminal's trade
  tape was exchange-wide (Sports >95%) despite being labeled
  "watchlist-only" — `state["trade_tape"]`'s streaming-path insert had no
  watchlist filter. **David's decision: go watchlist-only, not fix the
  filter** — off-watchlist whale discovery is out of scope right now
  (signal log noise, "focus on a few markets and then expand" later).
  Flipped the existing `trade_stream_exchange_wide` config flag to `false`
  (already the purpose-built knob, no new code) — reversible by flipping it
  back + a real restart whenever exchange-wide is back in scope. Live-
  verified: `mode: stream` (not degraded to polling), `exchange_wide:
  false`, trade tape watchlist-only. Caused the `ddev-router` incident
  above as a side effect of the required restart, not of the change itself.
- **Connectivity-badge fix** — DONE, merged (PR #598). Root-caused a David
  report of "the whole dashboard looks stale" while using the
  `autotrade.webfoundry.dev` tunnel workaround above: loading the page as
  `https://user:pass@host/...` (credentials embedded in the URL) makes the
  Fetch spec throw on every same-origin `fetch()` from then on, forever —
  not a backend defect, the backend was fully live the whole time. Fix
  distinguishes this permanent failure from an ordinary transient
  connection drop and tells the viewer to reload with the bare URL instead
  of showing a countdown that will never resolve on its own. Live-verified
  both branches via chrome-devtools before and after merge, adversarial
  review GO.

## Decisions waiting on David

**Tracked in `docs/open-decisions.md`, per CLAUDE.md — the single list of
parked decisions, not duplicated here.** Cleaned up 2026-09-05 (~63 lines
→ 51): removed everything marked `RESOLVED` per the file's own convention,
trimmed 3 entries that mixed a resolved narrative with a still-open
decision down to just the open part. Six items from tonight specifically:
`ef662c0`'s home, whether the rebuild-on-`signal_log` plan still applies,
`#532`'s retention design, `#578`'s purge go-ahead, `#589`'s
schema-column timing, and the branch-protection-API 403 gap. The other
~45 lines are a genuine backlog dating back to 2026-08-22 — not urgent,
but unanswered.

## Standing lessons (apply, don't re-litigate)

- **Never put a closing-shaped verb next to a bare issue/PR number in a
  commit message pushed straight to `main`** (self-inflicted, 2026-09-05) —
  a docs commit describing "`49` also resolved `#597`'s benchmark-
  falsifiability followup" auto-closed PR #597 via GitHub's issue-linking
  regex (`resolved #597` — it doesn't care what comes after the number,
  "'s followup" included), even though the commit never touched that PR's
  branch and nobody intended to close it. `49` root-caused and reopened it
  cleanly, nothing lost. Write "issue #N"/"PR #N" or otherwise separate a
  closing-shaped word (close/closes/closed/fix/fixes/fixed/resolve/
  resolves/resolved) from a bare `#N` reference — this applies to every
  commit message and PR body in this repo, not just this file's own.
- **Commit hot-path benchmark scripts/raw output somewhere durable, not just
  the PR/issue prose** (`07`, 2026-09-05, from `#576`'s A-vs-B review) — a
  benchmark run in a throwaway subagent worktree produces numbers that
  become unfalsifiable to a future reader the moment the worktree's gone.
  Paste the raw output as a code block in the PR/issue, or land the script
  in a scratch-but-tracked location — don't let a cited number's only home
  be a sentence describing it.
- **Dispatch subagents in parallel for independent pieces of a task list**
  (David, 2026-09-05) — applies to every session including the coordinator.
  Independent sub-tasks run on their own tracks and converge on
  completion, rather than being worked one at a time in one context.
  Doesn't change the self-review → adversarial-review → consolidation
  stage order (consolidation genuinely needs both prior outputs) — it's
  about how the content of any one stage gets built.
- **Checkpoint/push regularly, but don't bombard GitHub with pushes/PRs/
  comments all at once** (David, 2026-09-05) — with up to 4 sessions
  hitting one repo's API, a simultaneous burst risks tripping GitHub's
  rate limit (primary or secondary/abuse-detection) and stalling every
  session's `gh`/API calls at once, not just the one that caused it.
  Doesn't reverse "push ASAP once verified" — keep polling loops at
  reasonable intervals rather than tight loops, and if a real rate-limit
  error comes back, back off and retry with a delay rather than hammering
  again immediately.
- **Post durable findings to a PR or issue, never leave them only in
  chat.** Every real loss tonight was state that lived only in a session
  that then died.
- **Verify identity and state by direct reply / live check, never by
  inference** — `ListAgents` uptime, a doc's last-known state, and a
  peer's relayed claim have all been wrong at least once tonight.
- **No knob changes** (queue capacity, worker counts, subscription scope,
  rates) without a measured bottleneck and its mechanism.
- **This file holds the single next action and current state — rewrite
  it, don't append to it.** Three copies of the YES-side-profit status
  drifted independently earlier tonight because edits kept landing in
  whichever copy an editor's search text happened to match. Full history
  of tonight (the WSL restart, the messaging outage, the fleet
  reconciliation, every PR's blow-by-blow) is in `git log`/`git show` on
  this file and the relevant PRs/issues — that's the durable record, not
  this file.
