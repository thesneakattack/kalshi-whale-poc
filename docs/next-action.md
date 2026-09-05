# Next action

**Coordinator:** `autotrade-05` (chain tonight: `1f` → `48` → `01` → `05`, one
continuous session — the SendMessage name changes on reconnect, memory
doesn't). **Verify identity by direct reply before trusting a name** —
`ListAgents`'s "started Xm ago" is not evidence of a fresh session; ask.

**Peers and current task, as of this write:**
- `32` (chain: `df`→`8d`, PR #574 author) — **on `#578` pre-purge prep**
  (non-destructive): re-verify #577's fix still holds live, full backup,
  re-confirm contamination scope, write the pre-purge checkpoint artifact.
  Explicitly told NOT to execute the purge — gated on `62` confirming their
  ablation doesn't touch `market_history.snapshots`, then coordinator go.
- `07` (separate lineage, independent reviewer) — **on `#576`**: solution B
  (independent scheduled flush) landed real benchmark numbers (see Open
  issues below); solution A (bounded fairness) still running, ~40min+.
  Observability-persistence half already merged (#594). Converging A-vs-B
  once A lands.
- `bd` (chain: `d2`→`24`, PR #575 owner) — **standing watch on `#579`/`#580`**,
  reconfirmed clean 2026-09-05 (`dropped_after_max_attempts: 0`,
  `handler_timeouts_total: 0`, `queue.depth: 1`, `settlement_resolver.pending:
  38`) — no regression.
- `62` (chain: `64`→`a2`, `#577`/`#578` owner) — **on the YES-side auto-exit
  profit analysis** (gate condition 4 below): split-half robustness check
  DONE (see below); pnl+sentiment+staleness ablation still running (long
  pole — full per-snapshot signal_log walk across 664 entries).

`ef` (original app-health watch owner) is confirmed gone, not renamed.

**Safety, check every session start:** `auto_exit_enabled: false` in
`config/settings.yaml`, uncommitted (David's own edit) — must stay
uncommitted and unchanged. `kalshi_account.trading_enabled` stays `false`.
Never touch either without David.

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
  Assigned to `62`. Feasibility checked first, verified independently:
  **avenue 1 (out-of-sample window) is genuinely impossible** — the entire
  trade history is one ~2-day window, zero trades in 18.87h+ and still
  climbing; substitute is a labeled split-half *robustness, not
  validation* check. **Avenue 2 (factor isolation) is feasible and
  narrower than expected** — `analyst_divergence` and
  `series_track_record` proven zero-contributors from source (empty
  table; zero config weight), leaving `pnl` (done) + `sentiment` +
  `staleness` as the real ablation. **In progress** — full review cycle
  before this reaches a decision.

---

## Standing priority (David, 2026-09-05)

> "Right now the priorities are the data plane overall integrity and
> accuracy and near-zero latency, and also fixing the errors downstream of
> that so we can confidently turn trading back on... resetting whole
> tables and pruning table rows etc is totally allowed... as long as the
> math is right, I am okay starting from 0 for everything."

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
- **`#578`** up to 1.43M contaminated `market_history` snapshots — 6,504
  provably fabricated (hard lower bound); 48.8% recoverable via
  `series_watcher`, 93.2% of those a real `0.0` not `0.5`. **Purge BLOCKED,
  confirmed active dependency, not hypothetical** (2026-09-05): `62`'s
  YES-side ablation issues live per-entry queries against
  `market_history.snapshots` (+ `outcomes`) right now, mid-run, across 664
  entries — a purge during the run would silently produce an
  internally-inconsistent result, not an error. `32` assigned non-destructive
  prep only (backup, re-verify #577 holds live, contamination recount,
  checkpoint writeup); actual purge withheld until `62` confirms the run
  finished cleanly + coordinator go-ahead.
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
- **`#576`** ticker-coalescing starvation — `07` confirmed a real, recurring,
  load-dependent bug (mechanism: `_consume_market_from` only services the
  ticker map when the trade queue happens to empty, unbounded under
  sustained load; recurrence proven confound-independent via the
  `reconnects` counter staying flat, two clean instances found). Going
  straight to a PR with the lean cycle rather than a formal
  docs/superpowers pipeline — precedent set by `#577`/`#581` tonight — but
  the A-vs-B comparison (bounded fairness cap vs. independent scheduled
  flush) must include real benchmarks, not just mechanism, before it
  satisfies the data-plane HARD RULE's bar. **Observability-persistence
  half already merged** (PR #594, `a2e9757`) — split out since it's
  independent of which fairness approach wins. Main fix: benchmarking in
  progress, not yet a PR.

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
