# Next action

**Coordinator:** `autotrade-36`, one continuous session since `1f`.
**FLEET CHECKPOINTED AND PAUSED (David's call)** — all 4 peers confirmed
idle with clean worktrees. Verify identity by direct reply before
trusting a name, in either direction.

---

## Safety (check every session start — read this, don't just trust `git status`)

`strategy.auto_exit_enabled: false` and `risk.max_daily_loss_pct: 0` in
`config/settings.yaml`, both **uncommitted** (David's own edits) — must
stay uncommitted and unchanged. `kalshi_account.trading_enabled` stays
`false`. Kill switch TRIPPED by design; David confirmed that's fine.

**Real incident, 2026-09-06, caught by a peer's routine status check:**
both safety lines were found **silently reverted** to their unsafe
defaults (`auto_exit_enabled: true`, `max_daily_loss_pct: 0.8`) with
**zero working-tree diff** — `git status --short` looked completely
clean, which is exactly why nobody noticed sooner. Root cause: a bare
`git checkout <branch>` on the shared primary (done to *inspect* a PR
branch, not to commit) can drop the override if the target branch's
tracked file differs and it isn't stashed first — the established
stash-before-commit pattern was never applied to read-only checkouts.
Memory: `bare-checkout-can-drop-uncommitted-safety-config`. **Fixed
immediately** (restored both lines, confirmed live via `GET /api/config`
within seconds — config is live-reloaded, no restart needed). **Actual
impact was nil**, checked directly: `trading_enabled` was `false`
throughout (real capital never at risk), `risk.halted` stayed `true`
(kill switch didn't self-clear), and `GET /api/state` showed **zero open
paper positions** during the unknown-length window — nothing was
available for the wrongly-live auto-exit logic to act on.

**Standing check from now on**: a clean `git status` is not proof these
two lines survived — `grep -n 'auto_exit_enabled\|max_daily_loss_pct'
config/settings.yaml` and confirm `false`/`0` directly, at session start
and after any checkout on the primary. Prefer `EnterWorktree` over a
bare `git checkout` for read-only inspection of another branch — it
removes the hazard instead of requiring perfect discipline every time.

---

## `#532` and `candidate_log.db` — CLOSED, both actions done

31,429,358 `min_contracts` rows purged (630 batched `prune_gate()` calls,
zero drift from target), independently verified (`integrity_check: ok`,
row counts, app health). Real payoff: `min_contracts` was 98.98% of
`rejection_events`; it's now 4.8%, with `tradeable_price_range` (75.6%)
finally visible. Then, on David's explicit go-ahead, **VACUUM**:
6.4GB → 146MB, freelist 0, integrity ok, row counts intact.

VACUUM doubled as the properly-instrumented experiment `#642` needed —
absolute timestamps, tick duration on a 2s interval across VACUUM's real
2.45s hold. Result: **zero tick disruption**, recorded honestly as
partial evidence (weakens but doesn't settle the lock-contention
hypothesis, since VACUUM's hold was short — 97.2% of pages were
freelist, nothing to copy). `#642` stays open; the dedicated long-hold
test is still what would close it.

---

## `#605` — stays open by design

Two of three root-caused mechanisms fixed and live (`#636` gate_summary
SQL rewrite, `#637` resolve_window off the event loop). `#634`
(jsonable_encoder recursion, route unpinned) is filed and open — that's
why the parent issue stays open. `#639` filed for other sync
`gate_summary()` callers found along the way, correctly scoped down
after review caught an overstatement.

---

## Planning lanes — David's initiative — design MERGED, step 1 DONE, step 3 in review

**Design is on `main`** (PR #640, `8f4976a`) — 9 package-bounded lanes,
Lane > Initiative > Task, `concern:*` labels for cross-cutting
properties (not lanes), a 4-clause straddler rule. Full two-round review
record in the diff, including both rejections. **Visualization
deliverable done and published:**
https://claude.ai/code/artifact/f7e8fdc9-4fbf-4eab-b38e-4b19b601f737

**Migration step 1 (classification tables) — ALL 3 slices done and on `main`:**

- **Issues (147 → lane+concern)** — `49`, **DONE, merged**
  (`docs/superpowers/lanes/step1-issues-classification.md`, PR #643).
  9 RULE-GAP rows, one real NO-GO caught by its adversarial review
  (`#532`/`#642` issue-membership drift from live-state changes
  mid-draft — a genuinely different failure class from every reasoning
  error tonight, worth remembering as direct evidence for why this
  system needs *continuous* re-consolidation, not a one-time table).
- **Plans (63 files → lane+status)** — coordinator, **DONE, committed to
  `main`** (`docs/superpowers/lanes/step1-plans-classification.md`).
  Went through two self-review rounds after a real process failure (see
  Standing lessons) — final state: `backend-services-modularization`
  and `tier1-backend-hygiene` correctly on Lane 4/Lane 5 respectively
  (via clause (d), flagged as an ordering-artifact, not a considered
  pick — this is a **real, now-triple-confirmed design gap**: a bundled
  application-code initiative, independent tasks, several lanes, no
  named subject, no clean mechanism — feedback for a future design
  round, not something to force-fix mid-table); `event-scoped-me-gate`
  corrected to `superseded` (its own tracking issue #277 is closed, but
  the real successor work `#289`-`#293` is open — the goal is `active`,
  the document is superseded); `economic-strategy-effectiveness-
  investigation` corrected to `active` (`#76` open, E8/E9 never built).
- **Specs (79) + research (97) → lane+status** — `ea`, **DONE, merged**
  (`docs/superpowers/lanes/step1-specs-research-classification.md`, PR
  #644, `0fc33df`). Went through a full document-level NO-GO→revision
  cycle (the "deliverable" 3-part test's first application put 146/176
  rows in `superseded`; only 3 survived the narrow definition on
  independent recheck — most were actually `done` or `active`, a
  genuinely large correction, not a rounding error) **plus** a
  PR-level NO-GO→revision cycle (a real completeness gap: the
  planning-lanes design docs merged mid-review and needed their own
  Lane 9 entries — the system had not yet classified itself). Final
  distribution: 118 done / 43 active / 5 declined / 5 superseded / 4
  stalled / 1 never-started, plus 1 genuinely lane-UNDECIDED row
  (`backend-services-modularization-design.md`, a 4-way co-equal split
  left unresolved rather than forced — a **legitimate, disclosed
  divergence** from the sibling plans-table row for the same initiative,
  which forced a clause-(d) answer with a RULE-GAP flag; both treatments
  are honest, this was not reconciled and doesn't need to be).

**Shared status vocabulary, now stable across all three tables** (took
three rounds to get right — see Standing lessons):
`done` / `active` (partly shipped **and** an open tracker) / `stalled`
(some real artifact exists, no tracker, no movement) / `never-started`
(zero artifacts ever produced) / `declined` / `superseded` (decline-
shaped but not a decline decision — different work overtook it).

**Migration step 3 (fix `kanban_sync`'s Track touchpoints) — DONE,
merging now.** `49` dispatched a full TDD implementation (own worktree,
self-review + adversarial review + consolidation as PR comments), ran
its own separate PR-stage verification on top (live-tested
`list_plan_candidates`: 38 candidates, 63−38=25 matching the plans
table's independently-derived companion count), then a fresh PR-stage
adversarial pass — GO, one disclosed non-blocking finding (`ROADMAP.md
:66`, `quality_coordination.py:71`, the execution-program doc's `:995`
are deliberately deferred to the actual file-move step, since they're
prose references to the board file, not code that imports/depends on
the retired module — they don't hit an import-time crash, confirmed
real for later, not a gap now). CI green throughout. Authorized to
merge on resume from this pause.

**On resume:** confirm PR #645 merged, then add the `LANES`/`CONCERNS`
constants to `labels.py` and create the `lane:*`/`concern:hotpath` label
*definitions* (pure infrastructure, zero risk, sequenced after #645 to
avoid a `labels.py` conflict — not applying labels to any issue yet,
that's step 2, which needs the full classification content now that all
three tables exist).

**Hard gates, unchanged from the design, still binding:**
1. Step 1's tables are reviewed artifacts, not accepted on completion —
   proven necessary in practice, not just in principle: every one of
   the three tables needed real correction after independent review.
2. No file moves before step 3 (`kanban_sync` fixes) lands.
3. If any correction is ever found to require adding/merging/removing a
   lane, that's a scope change back to a full cycle — has not happened;
   every fix tonight was a row-level reassignment within the same 9.

**Not yet started:** step 2 (apply labels — waits on `ea`'s table),
step 4 (move files in lane-sized batches), step 5 (retire
`plans/README.md`).

---

## Peer status — all 4 CHECKPOINTED AND IDLE (paused)

- **`49`** — issues table (PR #643) merged; step 3 (PR #645) open,
  CI green, one adversarial-review pass left paused mid-flight, will
  record its result on the PR but not merge until pinged.
- **`ea`** — specs+research table (PR #644) merged. Watch stood down.
  Found and fixed the real config-safety incident's *observation*
  wasn't hers — that was `c4` — but confirm on resume: last full health
  read was nominal before the pause (`trading_enabled: false`, kill
  switch unchanged).
- **`0d`** — `#532` fully closed, worktree clean. Idle.
- **`c4`** — flagged the safety-config anomaly that led to the incident
  fix above. Confirmed clean, idle.

---

## Standing lessons from this stretch (apply, don't re-litigate)

- **A real safety-invariant violation can hide behind a clean `git
  status`.** A bare `git checkout <branch>` for read-only inspection
  (not just a commit-bound one) can silently drop `config/settings.yaml`
  's uncommitted safety overrides. Memory:
  `bare-checkout-can-drop-uncommitted-safety-config`. Grep the actual
  values, don't trust a clean diff as proof they survived.
- **A conditional authorization is scoped to the condition, not to
  whenever the result happens to land.** "Merge on GO if it comes back
  while you're waiting" does not carry forward to "merge on GO whenever
  it eventually lands after you've already paused" — `49` correctly
  held rather than assumed, twice tonight from two different angles
  (this one, and `0d`'s resume-ping-isn't-authorization earlier).

- **An unverified "fix" is worse than an honest open gap** — it reads as
  settled when it isn't. This exact shape happened three times in one
  sitting on the same table: (1) a retraction quoted design text that
  doesn't exist anywhere in the merged document — fabricated/misremembered
  from an earlier draft; (2) the "fix" for a bad Lane 9 ruling edited
  only prose, never the actual `lane` column, so the table still carried
  the wrong data under a paragraph describing a correction; (3) a
  promised vocabulary fix was dropped when a different fire came up, and
  the gap was papered over by restating the original claim as "final"
  instead of either delivering the fix or saying it hadn't landed.
  All three were caught by a peer checking the actual artifact/sent-
  messages against the claim, not by the author re-reading their own
  work.
- **A citation needs a citable artifact, not a recollection.** Pointed a
  peer at "the research-docs inventory agent's methodology" as if it
  were an established, checkable source — it wasn't. The only trace in
  git is a rolled-up summary count; the actual reasoning only ever
  existed in a dispatched agent's response text, never committed. Said
  so plainly once checked, rather than let the peer build on it.
- **Relay authority doesn't reach grandchild agents** (memory:
  `relay-authority-doesnt-reach-grandchild-agents`) — a peer relaying a
  coordinator's correction to its own subagent can't prove sender
  authority two hops deep; the subagent correctly re-verified what was
  checkable and declined what wasn't, resolved by a direct message from
  the real authority.
- **Chained wakeups silently die** (memory:
  `chained-wakeups-silently-die`) — `ScheduleWakeup` caps at 3600s; a
  multi-hour pause needs the absolute target written down, checked
  against `date` on every wake, not trusted to a chain.
- **fastapi container high-CPU is recurring, not pytest-specific**
  (memory: `fastapi-container-high-cpu-observed-2026-09-03`, 3rd
  occurrence) — any leftover script in the shared container can zombie
  the live worker; the docker healthcheck lies, verify through the real
  proxy path.
- **Live-state drift during table-drafting is a real, distinct failure
  class** — not a reasoning error like everything above; GitHub issue
  states can change *during* the ~20 minutes a snapshot table takes to
  build. Direct evidence for why this system needs continuous
  re-consolidation, not a one-time classification.
- **Ancestry checks lie about supersession — compare content.**
- **Never put a closing-shaped verb next to a bare `#N`** in a commit
  message pushed to `main`.
- **This file holds the single next action — rewrite it, don't append.**
