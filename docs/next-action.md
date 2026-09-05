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
- `c4` (was `32`, chain `df`→`8d`→`32`→`c4`, PR #574 author) — **on `#578`
  pre-purge prep** (non-destructive), checkpoint artifact posted (issue
  #578 comment): fix re-verified live and holding (contamination flat at
  6,260, zero new fabricated rows since the fix), fresh full backup taken
  and verified. Still holding the actual purge — gated on `0d`'s ablation
  finishing cleanly + coordinator go.
- `49` (was `07`, kept memory AND name through the restart, separate
  lineage, independent reviewer) — **on `#576`**: A-vs-B decided — **Family
  B (independent scheduled flush), not A** (A's own benchmark showed it
  structurally can't help under the mechanistically plausible trigger —
  semaphore/resolve contention suspends the consumer before A's counter
  check ever runs; B, as a genuinely separate task, gets scheduled
  regardless). PR #597 open (`ceace0e7`, `fix/576-ticker-flush-independent-
  drain`, mergeable), adversarial-reviewed GO-with-followups. **Followup
  resolved** (`49`, since its recovering subagent didn't survive the
  restart): recovered the benchmark script from disk (survived the WSL
  restart, `/tmp` did not), re-verified it against the PR's actual shipped
  code, fixed a real `quality_audit` boundary finding (raw Kalshi host
  string relocated to `tests/`, matching existing precedent), added a smoke
  test, pushed `33a83e2`. CI running on that commit; consolidation withheld
  until confirmed green, not posted yet.
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
- `0d` (was `62`, chain `64`→`a2`→`62`→`0d`, `#577`/`#578` owner) — **on the
  YES-side auto-exit profit analysis** (gate condition 4 below): split-half
  robustness check DONE (see below). The pnl+sentiment+staleness ablation
  was killed
  mid-run **three times** by unexplained full-stack restarts before the
  Windows root cause was found — paused before a 4th attempt; resume by
  relaunching once the fleet is confirmed stable post-WSL-restart, not
  before.

`ef` (original app-health watch owner) is confirmed gone, not renamed.

**Safety, check every session start:** `auto_exit_enabled: false` in
`config/settings.yaml`, uncommitted (David's own edit) — must stay
uncommitted and unchanged. `kalshi_account.trading_enabled` stays `false`.
Never touch either without David.

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
  `reconnects` counter staying flat, two clean instances found).
  **Observability-persistence half already merged** (PR #594, `a2e9757`).
  A-vs-B benchmarked with real numbers, **Family B chosen** (see peer
  roster above) — implementation in progress, not yet a PR.
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
