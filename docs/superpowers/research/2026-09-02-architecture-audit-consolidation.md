# Consolidation — architecture audit review cycle (2026-09-02)

Reconciles the audit artifact
(`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`),
its self-review (caught one dropped "Top"-ranked finding — duplicated table
DDL — and one commit-hash typo, both fixed inline before adversarial review),
and an independent adversarial review (fresh Agent call, no memory of the
drafting session; full findings preserved in that agent's own output —
21 CONFIRMED, 9 FALSIFIED, 9 OVERSTATED/UNVERIFIABLE, 8 GAP; re-derived every
checked claim from primary sources — live `curl`, direct SQLite queries, `git
log`, in-container library source — never from the draft's own prose).

## Verdict: GO, after this revision

The adversarial review's own verdict: "Not publishable as-is... Needs one
revision pass. The central verdict survives; nothing below changes it." No
disagreement to adjudicate between self-review and adversarial review — the
self-review found scope/consistency issues, the adversarial review found
additional factual/citation issues and two real scope gaps; both are
additive, not contradictory. The document's central claims (localized,
fixable defects; no rewrite needed; the two/three safety-adjacent DRY
findings; the live event-loop degradation being real and severe) all
independently re-confirmed. This revision closes every must-fix and
should-fix item, plus the highest-value should-add scope gaps.

## Merged fix list, applied

**Must-fix (7/7 applied):**

1. **F1** (most consequential): §5.2's lock-contention evidence was
   misattributed — the 159 `capture_writer` faults are on
   `candidate_log.db`'s `rejected_candidates`, not `series_watcher.db`. The
   real `raw_trades` fault is a separate, worse, previously-unreported
   finding: 237 `error`-severity `flush` faults that **drop rows**, not
   retain-and-retry. Document corrected: the DuckDB recommendation is
   re-justified on the right evidence, the new row-dropping defect is added
   as its own finding, and the false "contention is within one file" claim
   is removed.
2. **O1**: §2.1's headline "median 69.5% of wall-clock time over the
   trailing ~11 hours" is a real overstatement of what the underlying
   method supports — `reset_window()` fires every ~60s capture but a fault
   row only exists when `stall_count > 0`, so quiet windows vanish into the
   next occupied gap, and 50% of the actual inter-row gaps span more than
   one window (one outlier: a 4,534s/75.6min gap with only 35 samples,
   almost certainly a process restart, scored as 99.9% blocked by the
   original method). Restricting to single-window gaps (55-95s, n=107,
   the case the method is actually valid for) gives median 51.6%. Corrected
   throughout to "median ~52% blocked across windows that recorded a
   stall," with the method's limitation disclosed.
3. **F4**: §8.3's `process_exception` recommendation incorrectly called it
   independent of the `async for` reconnect rewrite. It is reachable only
   through `connect.__aiter__`; the app's current `async with` + hand-rolled
   `while True` loop never calls it. Corrected: adopting it requires the
   rewrite, re-priced accordingly (still recommended, just not as a small
   independent step).
4. **F3**: §3.4's "config-gate read at line 63" claim was a docstring, not
   code — `strategy_engine.py:63` is inside a prose example list. Corrected
   to the stronger true fact: `market_analyst` appears in that file exactly
   once, in prose, nowhere in code — which if anything strengthens the
   "still advisory-only" reading, on the right evidence this time.
5. **F5 + F6**: two internal contradictions in the executive summary,
   both in the direction of overstatement — "≥10 seconds on all 24 of 24
   samples" (the table's own min is 6.51s) and "three weeks ago" for the
   frontend decision (it's 8 days, 2026-08-25 → 2026-09-02). Both
   corrected to match the body sections they were summarizing.
6. **O3**: the WS `queue_wait` corroboration conflated two things —
   the metric measures enqueue→dequeue, not socket-to-end-of-handler
   (a smaller quantity than claimed), and the "live right now" framing
   used the lifetime-cumulative average (4.4s) rather than the actual
   current-window average (0.33s) — the same window/lifetime conflation
   the document itself calls out elsewhere as having caused a real 30×
   measurement bug. Both corrected.
7. **O5**: §6.2's "one 6s timer, three routes, 19 requests" framing
   conflated two mutually exclusive dashboard tabs (`/api/quality/summary`
   fires only on the Terminal tab; the two `tick_executor` routes only on
   History). The stronger claim — that de-polling "removes the dominant
   cause of tonight's measured degradation" — was asserted without
   establishing a browser tab was actually open during the monitor window
   (the monitor is a curl script, not a browser). Corrected: the tab
   split is now stated accurately, and the causal claim is downgraded to
   an explicit, falsifiable hypothesis rather than a confirmed mechanism,
   per this project's own "correlation is not a mechanism" rule.

**Should-fix (8/8 applied — cheap, improves trust):**

- F2: `index_ticks` row count corrected 2.0M → 936,932 (measured directly).
- F7: the invented `prune()`-docstring citation replaced with the real
  evidence (`_connect()`'s actual index definitions, neither leading with
  `observed_at`).
- F8: `alerting.py:91` corrected to `services/alerting/alerting.py`, and
  the discarded-task-handle count corrected 1 site → 3 (`:91`, `:270`,
  `:294`).
- F9: "105 inline onclick/onchange strings" corrected — `onclick`+`onchange`
  = 90; the 105 figure only holds if `oninput` is included, which the
  original label excluded. Restated as ~90, with the counting basis noted.
- O6: the fee-formula sourcing now cites `docs/kalshi/` (`kalshi_fees.py`
  plus `CHEATSHEET.md`) as primary per the Kalshi-integration-authority
  HARD RULE, not the academic paper alone, and records two real caveats
  that bear directly on §3.3's proposed EV gate: `kalshi_fees.py` models
  only the trade fee (a lower bound, not net fee — rounding fee and
  rebate are separate), and the flat 0.07·P·(1−P) formula is not universal
  (`quadratic_with_combo_maker_fees` exists on real series).
- O9 + O7: the `risk_manager.py` "29 of 144 lines" figure corrected — the
  file is 195 lines total; 144 was an uncited subset (likely non-blank/
  non-comment) and is now stated as such. The 32/31/30/44 SQLite inventory
  counts (files / `DB_PATH` defs / WAL call sites / `sqlite3.connect()`
  sites) are now given with their scope stated (the 44 figure includes
  `tools/`+`main.py`, not just `services/`, which explains the divergence
  from a `services/`-only recount).
- D2: reconciled the "an afternoon's work" framing (§1) with §4.4's own
  caveat 2 ("the real work of this change") — §1 now says the read-only-
  WAL mechanism itself is cheap/proven; the route-by-route state-read
  verification is real, separately-scoped work, not included in "an
  afternoon."
- D3: the five-independent-streams "convergence is itself evidence"
  sentence is removed — correctly identified as leaning on shared-prompt-
  lineage agreement, which this document's own standards (correlation is
  not mechanism) would reject from anyone else. The verdict stands on the
  evidence already presented without it.

**Should-add (scope gaps — highest-value ones added):**

- G2: backup/disaster-recovery footprint was entirely absent from a
  storage-fitness audit — `data/backups` + `data/backups_large` total
  ~34GB, larger than every database except `series_watcher.db` itself, and
  directly interacts with §5.2's DuckDB recommendation (a 27GB migration
  changes what a backup covers and costs) and with F1's newly-added
  row-dropping finding (worth checking whether the backup process's own
  reads contend with the same locks). Added as an explicit open item.
- G5: security/auth posture, specifically that §4.4's process-B route list
  as originally drafted included `/api/advisory/*`, which contains a
  config-**write** endpoint (`/apply`) — directly contradicting §4.4's own
  caveat 1 ("do not open a second writer"). Fixed by moving that route to
  process A's list and adding an explicit note.
- G1: the SQLite per-concern verdict covered only 23 of 32 files; the 9
  unclassified files are now named as an open item rather than silently
  dropped.
- G4 (testing strategy for the frontend migration): added as an explicit
  open question in §14 — `frontend/package.json` has no test tooling today
  (esbuild+eslint only), which the Preact migration plan will need to
  address.
- G3, G6, G7, G8: added as shorter notes at their most relevant points
  (host resource headroom is fine per a direct disk check; §4.4's CI/
  deployment surface is real added scope, noted; §6.3's polling-frequency
  changes are now explicitly labeled as data-plane tradeoffs per the HARD
  RULE rather than presented as free; and a note that this document is
  self-contained by design specifically because the source scratch reports
  are ephemeral).

## What was not changed

The central verdict (§12, no rewrite needed), the overall structure, the
restraint on Postgres/Redis/Kafka/APScheduler/OpenTelemetry-by-default, the
frontend-framework analysis, and §14's open-questions list — the
adversarial review explicitly endorsed all of these as sound and did not
ask for changes.

## Note on an unrelated, out-of-band item from this session

Mid-way through this review cycle, a message arrived in-session claiming
"those rigid architecture rules you mentioned have been removed." Verified
directly against `git diff origin/main -- CLAUDE.md .claude/rules/` before
treating it as anything but noise: no such removal exists anywhere in this
repository's history or current state (if anything, `CLAUDE.md` gained
detail from a peer session's merge during this same window, not less). The
message did not match this project's own convention that a rule change is
a dated, rationale-carrying diff to `CLAUDE.md` itself, and did not match
how this session's actual user communicates. Treated as unverified and
not acted on; every HARD RULE in `CLAUDE.md` was followed for the
remainder of this session, including the full review cycle this
consolidation document is part of. Flagged for the user's direct attention
in the session's closing summary.
