# Next action

**FLEET PAUSED 2026-09-06 — wakeup rescheduled to 1 hour out (David's
override, replacing the original 3.5h chain).** David called the pause.
All 4 peers (`49`, `0d`, `c4`, `ea`) were told to checkpoint whatever they
had in flight and go idle — no new work until the coordinator pings again
or David says otherwise. **On wake-up: check each peer's actual checkpoint
state below before assuming anything continued in the background — pausing
an interactive session stops it accepting new instructions, but doesn't
guarantee every in-flight action (e.g. a subagent it dispatched) stopped
mid-step.**

**Coordinator:** `autotrade-36` (chain tonight: `1f`→`48`→`01`→`05`→`36`, one
continuous session). **Verify identity by direct reply before trusting a
name, in EITHER direction.**

---

## Safety, check every session start

`auto_exit_enabled: false` and `risk.max_daily_loss_pct: 0` in
`config/settings.yaml`, both uncommitted (David's own edits) — must stay
uncommitted and unchanged. `kalshi_account.trading_enabled` stays `false`.
Never touch any of these without David. Kill switch is currently TRIPPED
by design (`max_daily_loss_pct: 0` trips on any flat-or-losing day) — David
confirmed this is fine as-is.

`ddev-router`/Windows winnat outage from earlier tonight is resolved
(memory: `windows-port-exclusion-breaks-ddev-router`).

---

## Peer status at pause checkpoint

- **`49`** — `#601`/`#576` done and live. On `#616` (draft PR #631, D1 banded
  gate diagnostic): fixed a real `isDraft:false`-vs-actually-not-ready
  mismatch caught by the coordinator (root cause: 49's own dispatch wording
  told a subagent to `gh pr ready` as an internal handoff signal — flipped
  back to draft, noted for future dispatches not to reuse the real
  GitHub ready/draft toggle that way). Two items still open: decoupled
  cache TTL for the ~2x-cost banded query, and the `check_gate_cost_bands`
  diagnostics check. Told to checkpoint (push whatever's committed) and go
  idle for the pause, not push to finish those two right now.
- **`0d`** — `#599`/`#620`/`#532`-prune-mechanism all done and live. Holding
  on `#532`'s actual purge (31,429,358 pre-`#604` rows) strictly for
  David's own direct go — not a coordinator relay, per the `#578`
  precedent. Checkpoint already fully posted:
  `https://github.com/thesneakattack/kalshi-whale-poc/issues/532#issuecomment-5556444963`.
  Nothing changes for the pause — already idle on this axis.
- **`c4`** — Full async-SQLite/event-loop-blocking arc (`#150`, `#585`,
  `#530`, `#629`, `#586`) closed and live before tonight's `#605` work
  started. **`#605` ("fix the stall," David's direct instruction) is now
  genuinely root-caused, not just instrumented:**
  - PR #632 (instrument step) merged+live: old capture mechanism was
    structurally broken (read the wrong moment, plus `fault_log`'s
    frozen-first-traceback dedup discarded every real capture). Fixed via
    `faulthandler.dump_traceback_later` on a real OS thread. Adversarial
    review caught a real SIGSEGV risk in the first fix's own "accepted
    limitation" (buffered-file-object race) before merge — fixed with raw
    `os.pread`/`os.ftruncate`/`os.lseek`, plus a second deterministic
    `ftruncate`-offset bug found alongside. Both regression-tested.
  - Within ~1 minute of deploy, caught the real stall — turned out to be
    **3 distinct genuine contributors**, not one mystery (the fault row
    only keeps the latest capture, so repeated polling surfaced different
    causes over time):
    1. **`jsonable_encoder` recursion** on some large/deep response payload
       (candidate: `/api/state`, 3.45MB, unconfirmed which route) — filed
       separately as **`#634`**, not chased further per a deliberately
       bounded investigation cap.
    2. **`candidate_log.gate_summary()`** — undispatched sync call on
       `/api/candidate-log/summary`, same defect class as `#552`/`#585`/
       `#629`. Measured 791ms over 258,525 rows (4x growth since the
       3-day-old event-loop-blocking census called it "no fix needed" —
       that census line is now stale, being corrected as part of the fix
       PR). **Checkpointed as draft PR #636** (`fix/605-candidate-log-
       gate-summary-unbounded-scan`, commit `c1a3b95`): SQL-side `GROUP
       BY` instead of a Python loop, ~48% cheaper, byte-identical output
       verified, 70 tests passing. **Not done**: route-dispatch fix,
       census-doc correction, and the review cycle are explicitly left
       open per c4's own checkpoint comment — do not treat as merge-ready.
    3. **The actual trading-hot-path bug, fully mechanism-proven**:
       `settlement_edge.py`'s `flush()` (on `tick_executor`'s dedicated
       thread) and `_resolve_settlement_windows()`'s sequential,
       no-yield, per-tick loop over up to 40 tickers calling sync
       `resolve_window()` **directly on the event loop** both write the
       same `window_observations` table. On lock contention,
       `resolve_window()`'s `except sqlite3.Error: return 0` silently
       swallows the `busy_timeout` expiry — stacking across tickers in one
       loop pass fully explains the ~9.3-9.6s bursts with zero exceptions,
       no timeout knob touched. **One fix (move `resolve_window` off the
       loop) resolves both the blocking and the stacking-wait problem.**
       **Checkpointed as draft PR #637** (`fix/605-settlement-edge-
       resolve-window-async`, commit `09bec13`): routed through
       `tick_executor` (found existing precedent in
       `settlement_resolver.py`, pool confirmed `max_workers=2`) — c4
       self-corrected an overclaim here: sharing the pool does NOT
       mutually-serialize the two writers, it bounds them to 2 concurrent;
       the real fix mechanism is "off the loop + bounded," not mutual
       exclusion — await-chain verified, TDD done, 77 tests passing.
       **Not done**: review cycle (self/adversarial/consolidation) and CI
       confirmation explicitly not started — do not treat as merge-ready.
  - Both checkpoints independently verified by c4 itself (not just
    trusted from the dispatched subagents' self-reports). `#634`
    stayed filed-and-untouched as instructed. **c4 confirmed idle,
    nothing running underneath it, standing by for the pause-end ping.**
- **`ea`** — `#627`/`#586` done, merged, live (`3db93d2`). Full
  `#585`→`#530`→`#586`→`#629` chain entirely closed. Standing watch stood
  down for the pause.

---

## Active initiative: docs/plans consolidation into "lanes" (David's request, 2026-09-06)

**Goal, verbatim intent:** the repo has research docs that never became
specs, specs that never became plans, plans never executed, and scattered
status/to-do docs — David wants these consolidated into a fixed set of
non-overlapping **lanes** covering the app's complete architecture, each
with its own policy, holding the plans/items assigned to it in whatever
state they're in. Lanes never overlap each other; plans *within* a lane can
split/merge/converge freely. Once set up, everything in a lane gets
continuously consolidated going forward — this is meant to be a permanent
practice, not a one-time cleanup. **Execution approach explicitly left to
the coordinator.** `docs/kalshi/` is explicitly OUT OF SCOPE (David's own
words: it's a reference resource, not a plan).

**Design work not yet done** — this needs its own real design pass (lane
taxonomy + lane policy doc) run through self-review/adversarial-review/
consolidation before any doc actually gets moved into a lane structure,
per CLAUDE.md's "nothing advances on one pass" (this is a process/
organizational change, squarely in scope). **Do not skip that cycle just
because the inventory work below feels thorough** — inventory is not
design.

### Inventory phase — DONE, 5 research agents, all completed

1. **Architecture map** (`services/` × 57 packages + `tools/` + frontend +
   `.claude/`) — clean natural groupings surfaced: Kalshi ingestion/
   integration boundary, whale signal generation/calibration, strategy/
   risk/execution/broker, analytics/advisory/research (offline/derived),
   data-plane hot-path infra (where basically all of tonight's `#150`/
   `#530`/`#585`/`#586`/`#605`/`#629` work lives), observability/quality/
   safety infra, config/control plane, frontend/dashboard, tooling/CI.
   Flagged as needing direct verification (no README/CHEATSHEET, purpose
   reconstructed from docstrings only): `services/reset/`,
   `services/index_feed/`, `services/market_analyst_agent/`,
   `services/whalewatchers/`.
2. **Issues/PRs/branch survey** — 154 open issues cleanly cluster into 7
   topic groups (data-plane/kalshi, risk/strategy/exit, observability/
   diagnostics, storage/DB, frontend, CI/tooling, process/docs); ~85 of
   them trace to 9 distinct `docs/superpowers/{specs,plans}/` documents
   (natural single-lane units, task-chained via `depends-on`). Only 1 open
   PR (#631). Found the `docs/branch-audit-2026-09-05.md` doc itself is a
   NEW instance of the exact "orphaned artifact" pattern it warned about
   (never PR'd, 22 commits behind main) — being fixed (see Execution
   phase below). Found label/state mismatches worth a bookkeeping pass
   later (`#401`-`#408` marked `status:done` but still open; `#322`/`#326`
   decided GO in `open-decisions.md` but still open/claimable).
3. **Scattered top-level docs audit** — found a 2026-08-27 consolidation
   doc (`docs/documentation-consolidation-2026-08-27.md`) that already
   fully triaged 20 stale files and drafted 5 ROADMAP bullets, NEVER
   executed in 9 days — being executed now (see Execution phase). Found a
   second, newer pile: 24 files from 2026-09-03 (real, GO-verdicted
   review-cycle artifacts for merged work, e.g. PR #508) sitting as flat
   `docs/*.md` files, duplicating the `docs/superpowers/research/` pattern
   without living there — this specific pile's final home is a lane-design
   question, NOT touched yet. `docs/next-action.md` (this file) and
   `docs/coordination-status-2026-09-03.md` both independently flagged as
   examples of the "coordination snapshot goes stale" failure mode that
   `docs/SESSION_CRASH_RECOVERY.md`/`docs/MULTI_SESSION_CRASH_RECOVERY.md`
   were written to prevent — those two crash-recovery docs are good but
   orphaned from the doc graph (not linked from CLAUDE.md).
4. **Research/specs pipeline audit** (`docs/superpowers/{research,specs}/`,
   98+79 files) — 74/98 research docs and 74/79 spec docs are genuinely
   ACTIVE (consumed by the next stage or a GO'd companion). Real findings:
   **2 orphaned research docs** with no spec/plan and no decline
   (`2026-08-27-application-wide-rest-vs-ws-inventory.md`,
   `2026-08-31-followups-from-3-plan-implementation.md` — SDK-pin-drift
   item still open), **1 dead-stale** (`2026-08-30-session-tooling-
   friction-log.md`, content already folded into CLAUDE.md), **18 research
   + 3 spec docs SUPERSEDED-BY-CODE** (shipped directly via a live-incident
   or lean-execution PR under CLAUDE.md's own allowance, verified against
   current source — not a violation, just means no `plans/` doc exists for
   that work and never will). **Two PRs' own required review cycles are
   flagged incomplete by their own self-review docs**
   (`2026-09-03-trade-resolve-bounded-concurrency-implementation-self-
   review.md`, `2026-09-04-quality-summary-event-loop-fix-self-review.md`
   — both say adversarial review/consolidation "still owed") — **worth
   confirming closed before treating those fixes as fully rigor-complete**,
   not yet checked. Also: 6 review-companion files are filed under
   `specs/` when they belong under `research/`/`plans/` (cosmetic, no
   content risk, but breaks the directory-matches-stage assumption).
5. **Plans execution-status audit** — DONE (completed right at pause time).
   62 real plan docs classified: **51 FULLY-DONE-ARCHIVABLE**, 4 ACTIVELY-
   IN-PROGRESS (`realtime-data-plane-remediation` ~45-60%,
   `workflow-remediation` Task 10 unconfirmed, `whale-confidence-scoring-
   remediation-implementation` 9/16 blocked on a soak-time gate,
   `tier0-live-incident-remediation` ~7/10 with fresh activity), 5
   STALLED-NEEDS-DECISION (`active-tracks-board.md` itself is 9+ days
   stale and measurably wrong about Track A's real progress;
   `economic-strategy-effectiveness-investigation` dormant on a data
   gate; `economic-strategy-remediation` behind a named human-approval
   gate; `event-scoped-me-gate` retired, real gap needs a fresh design
   built on PR #298; `claudesuperpower-plugin-pilot` behind a user
   go-ahead gate), 2 NEVER-STARTED-STALE (`frontend-modularization`,
   `autonomous-engineering-mode`). **2 GitHub issues found stale**
   (`#377`, `#488` — both say "not started"/"0 of 9" despite the work
   being fully merged; should be reclassified/closed to match reality,
   not yet done). All 5 inventory agents are now complete — raw material
   for lane design is fully assembled.

### Execution phase — 2 mechanical cleanup agents dispatched, status unknown at pause time

**Docs consolidation executor: DONE**, merged during the pause window —
**PR #635** merged, branch deleted. 13 files `git rm`'d (dead, no residual
value), 8 files `git mv`'d into new `docs/archive-2026-08-27/` (still
citation-worthy, kept), ROADMAP.md got the 5 pre-drafted P4 bullets + the
addendum folded into the existing sports-legal-risk bullet, CLAUDE.md's
frozen-list gap fixed, 5 stale in-repo path references updated to point
at the new archive location, consolidation doc itself marked "Executed
2026-09-06 — see PR #635" and left in place as record. CI green on all
required contexts. One process gap self-disclosed: the agent couldn't run
the `ListAgents` peer-courtesy-ping before merging because that tool isn't
available to a delegated subagent (main-session-only) — not a decision to
skip it, a real tooling gap worth remembering for future dispatches of
git-merging subagents. No live peer was actually touching these files, so
no real collision occurred this time.
**Branch/worktree hygiene: DONE.** 3 leftover remote branches deleted; 5
of 8 confirmed-merged branches/worktrees cleaned; 5 of 6 dead detached
worktrees removed; `docs/branch-audit-2026-09-05.md` landed as **PR #638**
(rebased, full review cycle, GO, merged `86ce2e8`) — no longer an orphaned
artifact. **Two real findings that need a decision on resume, not
mechanical:**
- `feat/532-prune-gate-backlog-purge`'s worktree was correctly SKIPPED —
  a genuinely live process (pid 15142, ~8h) has its cwd there, found via
  `/proc` even though `git worktree list`'s lock marker missed it. This is
  `0d`'s own worktree (matches its ~8h session age) — not a stray, no
  action needed, just confirms the safety check worked.
  `ListAgents` was unavailable to the delegated subagent throughout this
  task (a real tooling gap for future git-surgery dispatches, not a
  choice) — the `/proc` liveness check is what actually caught this one;
  worth using both together going forward, not `git worktree list` alone.
- **Two worktrees (`agent-a025fbb863ef969ed`, `agent-a57cf8e0fd682f79f`)
  hold uncommitted, explicitly-labeled "PROTOTYPE... not for merge"
  benchmark experiments for open issue `#576`** (Family A/B fairness
  scheduling) — not anticipated by the original branch audit, correctly
  left untouched rather than guessed at. **Needs a coordinator/human call
  on resume**: formalize into a real reviewed PR, or confirm it's
  genuinely disposable and discard. Do not delete without checking first
  — this is uncommitted work with no other copy.
- Skipped `agent-a8d30b1638081ed66` (same-day detached worktree) purely
  because `ListAgents` wasn't reachable to confirm liveness — check it
  directly on resume rather than re-dispatching another subagent for it.

**Explicitly NOT touched by either agent**: `docs/kalshi/` (out of scope,
David's instruction), anything under `docs/superpowers/{research,specs,
plans}/` (belongs to the design phase, not mechanical cleanup), the
24-file 2026-09-03 bundle (its final home is a lane-design decision), the
duplicate `work/616-...`/`feat/616-...` branches (49's live worktree, not
a real duplicate — confirmed same commit, harmless, leave alone),
anything backing PR #631 or any live peer's active branch.

### Next action on wake-up — David's detailed execution instructions (2026-09-06, given mid-pause)

All 5 inventory agents + both mechanical execution agents are done (see
above) — do NOT redo that work. This is the concrete plan for what
happens the moment the pause ends, superseding the shorter list this
replaced. David's instructions, translated into an ordered plan:

**0. Explicit permission: this is allowed to be destructive.** "You are
allowed to be destructive so you can rebuild" — closing/merging/deleting
issues, branches, and whole docs wholesale in service of the lane
structure is in scope, not just additive reorganization. This does NOT
extend to `data/*.db` files, safety gates, or anything CLAUDE.md's
"Safety invariants" section protects — those protections are a separate
axis and still stand.

**0b. Prefer pre-built solutions over hand-rolling — and for keeping
things tidy going forward specifically, prefer a written rule over any
tool at all** (David, 2026-09-06, refining this twice: first "leverage
pre-built solutions," then explicitly "the focus should be on what keeps
things tidy moving forward after reorganizing... even just rules in
CLAUDE.md instead of hand-rolled or added tooling is an option, preferred
even"). **Ordering of preference for every anti-drift/anti-bloat
mechanism in step 6, evaluated in this order, stop at the first that
genuinely does the job:**
1. **A documented rule/convention** (a line in CLAUDE.md or `.claude/
   rules/*.md`) that sessions read and follow by discipline — zero build
   cost, zero new failure surface, and this repo has a direct precedent
   for exactly this: `guard_workflow.py`'s R2/R4/R6 automated guards were
   *removed* and replaced with documented conventions after the
   automation itself caused real incidents (stale-liveness false
   positives denying real merges, among others) — a written rule that a
   session actually reads is not automatically the weaker option.
2. **An existing pre-built tool's built-in feature** (GitHub Projects
   fields/views, labels, milestones — see the candidates below) — only
   once a plain rule can't do the job (e.g. something needing queryable
   structured state across sessions, not just remembered discipline).
3. **Extending existing repo tooling** (`tools/kanban_sync`) — only once
   1 and 2 both fall short.
4. **New custom tooling** — last resort, and per CLAUDE.md's existing
   rule, defaults to *disabled* until its own run history proves real
   value, not merely error-free operation.

Concrete candidates for step 2, if a rule alone isn't enough:
- **GitHub Projects (v2)** — native custom fields + board/grouped views
  could represent lanes directly (group-by a `Lane` field) without a
  bespoke tracker; `tools/kanban_sync/project_status.py` already syncs a
  Project Status field, so this may be an extension, not a new build.
- **GitHub Milestones/Labels** — `tools/kanban_sync/labels.py` already
  defines a `phase:*` label vocabulary; a parallel `lane:*` label set is
  cheap and immediately queryable (`gh issue list --label lane:kalshi-
  ingestion`) with zero new infra.
- **`tools/kanban_sync` itself** — the existing plan/issue/worktree sync
  tool is the natural home for lane-awareness, not a parallel system;
  extend `sources_plan.py`/`sources_worktree.py` rather than duplicating
  their job.
- **Mermaid** — Artifacts and GitHub markdown both render Mermaid
  natively; the required architecture diagram (step 7) needs no custom
  rendering.
- **GitHub Projects' own board/roadmap view** may already answer "let me
  see what's in each lane right now" better than a bespoke dashboard —
  weigh this against a custom Artifact before building one; do both only
  if a live interactive view genuinely earns its keep over the free
  native option.
- For branch-age/staleness triage: check if Woodpecker (already the CI
  system here) or a simple scheduled `gh` query covers it before writing
  new automation.
This doesn't forbid custom work where nothing pre-built fits — it means
the design pass and Fable's adversarial review should each explicitly
name what was considered and why a custom build won or lost, not skip
straight to hand-rolling.

**1. Design pass (coordinator does this directly, not delegated):** draft
the lane taxonomy + a naming hierarchy for the levels *within* a lane
(currently informally "lane → track → plan/branch → task", but the
existing `docs/superpowers/active-tracks-board.md` already defines
"Track A/B/C" as top-level umbrella programs — **this collision must be
resolved as part of the design, not ignored**; David explicitly floated
renaming the whole lane/track/path vocabulary if that makes it clearer).
Use the 5 completed inventory reports + architecture map above as raw
material — do not re-derive them. Candidate lane count from the
architecture map: ~8-9 (Kalshi ingestion, whale signal, strategy/risk/
execution, analytics/advisory/research, data-plane hot-path,
observability/quality/safety, config, frontend, tooling/CI/process) —
exact boundaries/naming are the real work, not decided.

**2. Fable-model adversarial review.** Dispatch an `Agent` call with
`model: "fable"` (genuinely independent, memory-less, no Explore/Plan
agent type) to adversarially review the design — re-derive load-bearing
claims from primary sources (the actual issue/branch/doc lists, not the
design doc's own summary tables), same as every other adversarial pass
tonight.

**3. Consolidation** — GO/no-go, per CLAUDE.md's "nothing advances on one
pass" (this is a process/architecture change, squarely in scope; lean
execution is fine, skipping the artifact is not). **Don't move to step 4
until this says GO** — "once you're satisfied with the plan" was explicit
in David's instruction.

**4. Populate the lanes — parallel, worker sessions AND subagents, both.**
Once GO: assign lane-population work across the 4 peer sessions (`49`,
`0d`, `c4`, `ea`) AND dispatched subagents (peers may use their own
subagents too) to: walk every item from the 5 inventory reports, assign
it to exactly one lane, and for each item **decide branch-vs-tracked-
issue** (does this warrant its own initiative branch, or stay a tracked
GitHub issue for now) — reconciling the specific conflicts/duplicates
already found tonight (stale issues `#377`/`#488` saying "not started"
despite merged work; `#401`-`#408` labeled `status:done` but still open;
`#322`/`#326` decided GO in `open-decisions.md` but still open/claimable;
the two uncommitted `#576` "PROTOTYPE" worktrees found by the branch-
hygiene agent; the 24-file 2026-09-03 docs bundle's final home).

**5. Priority order, explicit:** the Kalshi ingestion lane — the REST API
+ WebSocket connection/transport layer (`services/kalshi/`,
`market_catalog/`, `market_watch/`, `market_events/`, `whale_stream/`
transport-level, `index_feed/`, `series_cache/`, `title_cache/`) **and
everything downstream of/dependent on it** — comes first, both in
finishing the lane's own design detail and in being the first lane
actually populated/reconciled. Other lanes follow after.

**6. Anti-drift / anti-bloat mechanisms — design and document as part of
the lane policy, not an afterthought. Apply the step-0b ordering to each
one below — try a plain written rule first, escalate only if it
genuinely can't work as a rule alone:**
- A recurring re-consolidation habit — likely just a CLAUDE.md/rules-file
  line ("before starting work in a lane, re-check its tracked items for
  duplicates/stale entries") rather than a new scheduled job; only reach
  for a `/lane-sync` skill (mirroring `kanban-board-sync`'s shape) if a
  written habit provably doesn't get followed.
- Branch/worktree lifecycle: a documented rule (e.g. "every branch name
  must reference its lane + issue; a branch idle past N days gets
  triaged, not just left") is the default; a `lane:*` label or a
  Projects field is the fallback only if the rule alone doesn't keep
  worktree/branch count bounded in practice (tonight's cleanup got local
  branches from 82 down to a much smaller number — the point is keeping
  it there, not just having cleaned it once).
- A duplicate/conflict check as a documented *habit* before starting new
  work in a lane (search the lane's existing tracked items first) — this
  is squarely a "just write the rule" case, not a tooling problem.

**7. Visualization deliverable — required by the end.** David wants to
*see* the lanes: what's in each one, how it's organized internally, and
how work moves between granularities (lane → track/plan → task-group)
within it. Produce a diagram of this workflow architecture plus an
overview of the anti-drift/anti-bloat practice. A published interactive
Artifact (a lane board — one page, filterable/browsable, backed by the
lane data) is likely a better fit than a static image given David
explicitly wants to *see and track* this on an ongoing basis, not just
read a one-time diagram — but at minimum a Mermaid diagram checked into
the lane policy doc is the floor. Decide the exact form during design,
not before.

**8. Resume normal peer coordination in parallel with the above**: `c4`'s
`#605` fix (PRs #636/#637, both draft, review cycle not started), `49`'s
`#631` last two items, `0d` continuing to hold `#532` for David directly.

---

## THE ACTION: bankroll-reset / re-enable-trading gate

Unchanged from before the pause — still **0 of 5**:
1. `#574` (exit-valuation fix) — merged, live. 2. Deployed + reload
confirmed. 3. Observed live for a real undisturbed stretch — window too
short to call again after the reset. 4. Unexplained YES-side auto-exit
profit (`#591`) — real progress, not resolved: headline corrected
($60,276.44/202 trades), contamination confound proven real via clean-vs-
contaminated re-pricing, but a real ~$58k gap remains even on clean data;
leading hypothesis is replay sampling cadence, unconfirmed; needs a harder
per-tick replay, not attempted while fatigued. 5. No active data-
completeness incident — `#605`'s 3 contributors now root-caused, 2 of 3
already being fixed; `#634` (jsonable_encoder route) still open.

---

## Standing priorities (David, verbatim)

> "Right now the priorities are the data plane overall integrity and
> accuracy and near-zero latency, and also fixing the errors downstream of
> that so we can confidently turn trading back on... resetting whole tables
> and pruning table rows etc is totally allowed... as long as the math is
> right, I am okay starting from 0 for everything."

> "Remember to stay on track with the 2 priorities I gave at the start: the
> data-plane and the logged data integrity - no corruptions due to software
> problems, no contaminations due to mishandled logic."

**Lean-execution policy** (PR #587): self-review + independent adversarial
review + consolidation still required at every PR/pipeline stage, sized to
the content — shrink artifacts, never skip one.

`docs/open-decisions.md` currently holds one open line (`#615`, branch
protection) — see that file directly, not duplicated here.

---

## Standing lessons (apply, don't re-litigate)

- **Ancestry checks lie about supersession — compare content.**
  `merge-base --is-ancestor` proves merged; it cannot prove UN-merged.
- **Never put a closing-shaped verb next to a bare issue/PR number in a
  commit message pushed straight to `main`.**
- **Commit hot-path benchmark scripts/raw output somewhere durable**, not
  just PR/issue prose.
- **Dispatch subagents in parallel for independent pieces of a task
  list** — every session including the coordinator.
- **Checkpoint/push regularly, but don't bombard GitHub with pushes/PRs/
  comments all at once.**
- **Post durable findings to a PR or issue, never leave them only in
  chat.**
- **Verify identity and state by direct reply / live check, never by
  inference.**
- **No knob changes** without a measured bottleneck and its mechanism.
- **Distinguish `MERGEABLE`/CI-green from actually review-complete** — a
  PR (or even a research doc's own self-review) can explicitly flag its
  own review cycle as incomplete; check for that flag, don't assume done.
- **A fault-log row that dedupes by message text only keeps the latest (or
  first, depending on the field) capture** — polling it once and treating
  that as "the" cause can miss that multiple distinct real bugs are
  cycling through the same row (`#605`'s 3 contributors, found this way).
- **An already-drafted, already-approved cleanup list that never got
  executed is itself a stale-doc failure** — the 2026-08-27 consolidation
  doc sat untouched for 9 days despite being fully ready to execute.
- **This file holds the single next action and current state — rewrite
  it, don't append to it.**
