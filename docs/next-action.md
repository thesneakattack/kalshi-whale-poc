# Next action

**Coordinator:** `autotrade-05` (chain tonight: `1f` → `48` → `01` → `05`, one
continuous session — the SendMessage name changes on reconnect, memory
doesn't). Peers: `32` (was `df`/`8d`, PR #574 author), `07` (separate
lineage, independent reviewer), `bd` (was `d2`/`24`, PR #575 owner), `62`
(was `64`/`a2`, #577/#578, now also the YES-side-profit analysis and
app-health watch — `ef`, the original watch owner, is confirmed gone).
**Verify identity by direct reply before trusting a name** — `ListAgents`'
"started Xm ago" is not evidence of a fresh session; ask.

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
  `series_watcher`, 93.2% of those a real `0.0` not `0.5`. Purge path now
  open per the priority section above; needs the pre-purge checkpoint.
- **`#532`** `rejection_events` unbounded growth — 29.8M rows, 98.98% from
  one gate (`min_contracts`, ~23.8M resolved samples against a
  `min_samples=30` threshold — wildly oversampled). Measured rate spans
  18–32 rows/sec depending on method (not a single clean number); the
  "4.2x/week" multiplier mechanically decays as the base grows while the
  absolute rate doesn't — don't read a smaller multiplier later as
  improvement. Coordinator recommendation: sample `min_contracts` at write
  time, keep every other gate whole.
- **`#589`/`#590`** — linked follow-ups from `#577`'s review (schema
  provenance column deferred; CI guard misses ternary/indirection shapes).
  Non-blocking, open.
- **`#576`** ticker-coalescing starvation — open, not yet actioned.

## Decisions waiting on David

- **`ef662c0`** (`kelly_fraction_of_cap` + `KXBTC15M`, unpushed on the
  primary's local `main`) needs a home — his commit, his call when/whether
  to push.
- **Rebuild plan**: scrap derived datasets, rebuild on `signal_log`.
  Premise verified on corruption (signal_log preserves NULLs, 1.25% at 0.5
  vs market_history's 29%) but was holed on completeness (`#579`) —
  largely addressed now that `#577`/`#581` are live; worth a fresh look
  before committing to the rebuild.
- **`#532`** retention policy (see Open issues above) — needs a go-ahead
  on the sampling design, or a different call.
- **`#578`** purge timing — needs the pre-purge checkpoint confirmation
  when `62`/whoever is ready to execute it.

## Standing lessons (apply, don't re-litigate)

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
