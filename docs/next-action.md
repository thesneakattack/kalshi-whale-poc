# Next action

**Coordinator:** `autotrade-36`, one continuous session since `1f`.
Fleet active. Verify identity by direct reply before trusting a name,
in either direction.

---

## Safety (check every session start — grep the values, don't trust `git status`)

`strategy.auto_exit_enabled: false` and `risk.max_daily_loss_pct: 0` in
`config/settings.yaml`, both **uncommitted** (David's own edits) — must
stay uncommitted and unchanged. `kalshi_account.trading_enabled` stays
`false`. Kill switch TRIPPED by design; David confirmed that's fine.

**Real incident, 2026-09-06:** both lines were found silently reverted
to their unsafe defaults with **zero git diff** — a bare `git checkout
<branch>` on the primary (for read-only inspection, not a commit) can
drop them if unstashed. Fixed, verified live, impact confirmed nil
(zero open paper positions during the window). Memory:
`bare-checkout-can-drop-uncommitted-safety-config`. **Standing check:**
`grep -n 'auto_exit_enabled\|max_daily_loss_pct' config/settings.yaml`
after any checkout on the primary — a clean `git status` is not proof
these survived.

---

## `#532`/`candlestick-volatility`/VACUUM — all CLOSED

`#532`: 31.4M-row purge done, independently verified. `#642`:
`candlestick-volatility` (#641) decided — kept as reference, re-implement
when prioritized. VACUUM done (6.4GB→146MB) on David's go-ahead.

---

## `#605` — one fix away from closing

Root-caused to 3 distinct mechanisms; 2 fixed and live (`#636`, `#637`).
Third (`#634`, `/api/state`'s 60s periodic full-repoll spike from
`event_metadata.py`'s `_EVENT_LIVE_DATA_REPOLL_SEC`) is **pinned via real
production log evidence** (verified against source) and its fix is
**dispatched, in progress** (`c4`) — competing-solutions comparison,
ETag/304 preservation, standalone review artifacts, careful issue-
reference phrasing all specified up front. **Once this lands and is
confirmed live, `#605` closes** — that's my call once deploy is
confirmed, not automatic on merge.

---

## `#642` — mechanism still genuinely unexplained, actively narrowing

Event-loop-blocking ruled out by source. SQLite lock contention weakened
by SQLite's own docs (the backup API is documented non-blocking even as
a single-step full copy) — a deliberately-designed live-lock-hold test
was correctly declined (`0d`) because it would have caused real data
loss via `capture_writer`'s 1s retry budget, and wouldn't have tested
the right mechanism anyway. Two zero-risk synthetic tests (GIL
contention, disk I/O contention) both came back negative against an
isolated tick monitor; against the *live app's own* `last_tick_duration
_sec` the result was confusingly non-clean (baseline higher than either
load phase) until `0d` traced the metric to its actual definition
(`main.py:1366`: full wall-clock tick including real Kalshi network
calls, not a tight event-loop probe) — reframing the noisy baseline as
ordinary variance, not a symptom. **All three tested mechanisms now
weakened.** A memory-pressure hypothesis (the backup's data volume
causing OS-level paging that slows real syscalls generally, a different
mechanism than either synthetic test) is being folded into the write-up
as a named, untested candidate. **Independent adversarial review of this
whole interpretation is running now** — `0d` correctly held rather than
post a counter-intuitive result without extra scrutiny. Report pending.

---

## Planning lanes — design + migration steps 1-3 ALL DONE, step 4 in planning

**On `main`:** design (PR #640), all 3 step-1 classification tables
(issues/plans/specs+research — PRs #643/#644 + a direct commit), step 3
`kanban_sync` retooling (PR #645), and the `LANES`/`CONCERNS`
infrastructure + full step-2 labeling (PR #646).

**Step 2 (label all 147 issues) is DONE and verified three independent
ways**: `49`'s own two-method check (12-issue sample + full per-label
count match), the coordinator's direct `gh issue list --label` spot-
check (3 counts, all exact), and `ea`'s from-scratch re-derivation (one
issue per lane, all 9 correct, 2 cross-checked against the table itself
to confirm the copy step). 138 issues carry a lane label, 9 carry a
`RULE-GAP` tracking comment instead of a forced label, 17 carry
`concern:hotpath`. The 8 stale `area:*` definitions and the
`phase:implementation-plan` mislabel (found on 6 issues, not the 3
originally named — `49` fixed the full extent, verified nothing lost)
are both gone.

**A real fabrication bug was found and generalized while building the
`LANES` constant**: 3 paths wrongly listed as nested under
`whalewatchers/` (adversarial review's catch), plus a second instance of
the *same defect class* `49` found on its own follow-up sweep
(`config_performance.py`, actually under `services/config/`) that the
review missed. Fixed both, then replaced the weak 9-item spot-check test
with one that resolves and verifies **all 231** `LANES` paths against
the real tree — the right response to a fabrication bug is a
structural test, not just fixing the two known instances.

**Step 4 (move files in lane-sized batches) — EXECUTING.** Plan (GO,
full review cycle) covers 246 files across 8 populated lanes, moving to
`docs/archive/lane-N-<slug>/{plans,specs,research}/`. Prerequisite
`kanban_sync`/`quality_coordination` `PLANS_DIR` fix landed first (PR
#650) — took 3 full review rounds to get right, each round catching a
real, previously-unseen bug (a second hardcoded call site, then a third
in a genuinely different category — string literals baked into written
GitHub issue text, not filesystem reads; the bug that let the original
version through was the regression test mocking away the exact function
whose output text needed checking).

**Batch order (decided): 4 → 8 → 5 → 6 → 3 → 2 → 1 → 9**, smallest/
lowest-risk first to prove the process, Lane 9 forced last (contains the
design doc governing the whole migration). Lane 1 deliberately near the
end despite "Kalshi first" — that instruction was about substantive
engineering priority (honored all night via real `#605`/`#634`/`#642`
work), not file-archival sequencing, which doesn't advance or delay any
actual engineering outcome either way.

**Known, accepted, temporary side effect — not a bug if you see it:**
each batch only fixes its own outgoing references, not incoming
citations from not-yet-moved lanes. A file in a later batch citing an
already-moved file will have a stale path until *its own* batch runs.
**Currently affects 2 active Lane-3 plans**
(`economic-strategy-effectiveness-investigation.md`,
`economic-strategy-remediation.md`), which cite Lane 4's now-moved
files — self-heals when Lane 3's batch runs (5th in order). Decided
deliberately: proactively fixing forward-references would mean every
batch also edits files outside its own lane's scope, real complexity
and cross-batch merge-collision risk for a low-severity, self-healing,
loudly-failing (not silent) inconvenience. Not worth it.

**Lane 4 (11 files): DONE, PR #651.** Lane 8 in progress. `c4`
independently verifying each batch as it lands, same role `ea` played
for step 2.

**Step 5 (retire `plans/README.md`)** — not started, low-risk, can
follow step 4's first batch.

**Hard gates, proven necessary in practice tonight, not just in
principle:** every one of the three step-1 tables needed real correction
after independent review; the `LANES` constant needed two rounds of
fixes for the same defect class. Step 4's much larger blast radius gets
at least the same rigor, not less because the pattern is now familiar.

---

## Peer status

- **`49`** — steps 1-3 + step 2 done and merged. Now planning step 4
  (not executing).
- **`c4`** — `#634` fix in progress (dispatched, full cycle).
- **`0d`** — `#642` synthetic-test interpretation in independent
  adversarial review, holding before posting.
- **`ea`** — standing watch, nominal. Queued a second labeling
  spot-check into its next regular cycle (not urgent, first sample was
  clean).

---

## Standing lessons from tonight (apply, don't re-litigate)

- **A real safety violation can hide behind a clean `git status`** — see
  Safety section above.
- **A conditional authorization is scoped to its condition, not to
  whenever the result eventually lands** — confirmed twice tonight from
  different angles (`0d`'s resume-ping-isn't-authorization, `49`'s
  paused-review-isn't-still-authorized-to-merge).
- **Before authorizing a deliberate hold/contention test against a live
  shared resource, check every OTHER component's own retry/timeout
  budget** — a hold longer than the shortest one causes the real harm
  the test was trying to explain, not a simulation of it. Memory:
  `think-through-third-party-retry-behavior-before-authorizing-hold-tests`.
- **A confusing result deserves more scrutiny, not a forced clean
  narrative** — check what a metric actually measures at its source
  before interpreting a counter-intuitive pattern.
- **A found bug is a prompt to look for the same defect class
  elsewhere**, not just fix the reported instance — `49` did this twice
  tonight (`phase:implementation-plan`'s real scope, the second
  `LANES`-path fabrication) and both times found more than what was
  originally reported.
- **An unverified "fix" is worse than an honest open gap** — three times
  in one stretch, a correction that only edited prose (never the
  underlying data/column/value) was caught by a peer checking the actual
  artifact, not the claim about it.
- **Ancestry checks lie about supersession — compare content.**
- **This file holds the single next action — rewrite it, don't append.**
