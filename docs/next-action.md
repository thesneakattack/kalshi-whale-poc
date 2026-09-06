# Next action

**Coordinator:** `autotrade-36`, one continuous session since `1f`. Fleet
resumed and active as of 2026-09-06 ~14:00. Verify identity by direct
reply before trusting a name, in either direction.

---

## Safety (check every session start)

`strategy.auto_exit_enabled: false` and `risk.max_daily_loss_pct: 0` in
`config/settings.yaml`, both **uncommitted** (David's own edits) — must
stay uncommitted and unchanged. `kalshi_account.trading_enabled` stays
`false`. Kill switch TRIPPED by design; David confirmed that's fine.

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

## Planning lanes — David's initiative — design MERGED, migration mid-step-1

**Design is on `main`** (PR #640, `8f4976a`) — 9 package-bounded lanes,
Lane > Initiative > Task, `concern:*` labels for cross-cutting
properties (not lanes), a 4-clause straddler rule. Full two-round review
record in the diff, including both rejections. **Visualization
deliverable done and published:**
https://claude.ai/code/artifact/f7e8fdc9-4fbf-4eab-b38e-4b19b601f737

**Migration step 1 (classification tables) — 2 of 3 slices done, 1 in final review:**

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
- **Specs (79) + research (97) → lane+status** — `ea`, **IN FINAL
  REVIEW**. Self-review done (176/176 structural, 5 evidence claims
  verified, one leftover-wording fix applied). Independent adversarial
  review dispatched and running, specifically scoped to: (1) whether the
  "deliverable" 3-part test holds up (it's the coordinator's own
  unvalidated reconstruction, not an inherited methodology — no citable
  source exists for the original inventory beyond a rolled-up count in
  git history, said plainly rather than oversold), (2) whether 146/176
  rows landing in `superseded` is correct or too broad against the
  narrow definition, (3) independent re-derivation of the 9 rows
  affected by the tightened `stalled`/`never-started` boundary.
  **Report pending — do not treat this table as final until that lands.**

**Shared status vocabulary, now stable across all three tables** (took
three rounds to get right — see Standing lessons):
`done` / `active` (partly shipped **and** an open tracker) / `stalled`
(some real artifact exists, no tracker, no movement) / `never-started`
(zero artifacts ever produced) / `declined` / `superseded` (decline-
shaped but not a decline decision — different work overtook it).

**Migration step 3 (fix `kanban_sync`'s Track touchpoints) — assigned to
`49`, independent of step 1 finishing** (it's about the tool, not the
classification content). Full touchpoint list is in the merged design's
§6 step 3 — read it directly, it's more complete than any summary,
including two real citation errors caught across two review rounds
(`__main__.py`'s two separate `read_text()` call sites, not one; the
sync-line SKILL.md citation was wrong — it's `checkpoint/SKILL.md:69-70`,
not `kanban-board-sync/SKILL.md:69`). Also asked `49` to add the
`LANES`/`CONCERNS` constants to `labels.py` and create the `lane:*`/
`concern:hotpath` label *definitions* (pure infrastructure, zero risk —
not applying labels to any issue yet, that's step 2).

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

## Peer status

- **`49`** — issues table done/merged; now on step 3 (`kanban_sync`
  fixes) + label infrastructure prep.
- **`ea`** — specs/research table in final adversarial review; standing
  watch continues in parallel (last full health read: nominal,
  `trading_enabled: false` unchanged).
- **`0d`** — `#532` fully closed. Idle, available for new work.
- **`c4`** — last known: dispatched PR #640's adversarial review (GO,
  merged). Idle since, available for new work — reconfirm directly
  before assuming, don't infer from this line.

---

## Standing lessons from this stretch (apply, don't re-litigate)

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
