# Active Initiative Tracks — Cross-Session Board

> Living document, not a plan in its own right. It does not replace any
> canonical plan/spec/investigation/research doc — it points to them, states
> which ones can run concurrently, and tracks the one-line "what's next" for
> each so a session (or a parallel worktree) can pick up work without
> re-deriving the full 2026-08-26 reprioritization conversation from
> `git log`. Update it in place; don't let it grow into a second narrative
> doc — see "Update discipline" at the bottom.

## How to resume with this board

1. Read this file top to bottom (it's short by design).
2. Pick a track whose Status line says there's a next task and no unmet
   entry gate.
3. Open that track's canonical plan doc and orchestrator skill (linked
   below) and follow *its* re-grounding step — don't trust this board's
   status line blindly, it can go stale between sessions faster than the
   canonical docs do. Verify against the plan doc's own checkboxes and
   `git log` before starting.
4. When a task finishes: update both the canonical plan doc's checkbox
   *and* this board's status line for that track, in the same commit
   where practical.

## Concurrency ground truth

Full matrix: `docs/kalshi-personal-production-execution-program-2026-08-26.md`
§8. Relevant summary for the tracks below: **Track A (realtime) and Track B
(economic) are explicitly safe to run concurrently** — different runtime
surfaces, already reasoned about in that matrix. Track C is sequential and
gated behind both; nothing in it should start yet.

---

## Track A — Realtime data plane (lead track)

**Status (2026-08-26):** CH1 (measure a subscription-churn burst's real
cost) is next. Not started. No blocking decision needed to begin.

**Canonical docs**
- Investigation plan: `docs/superpowers/plans/2026-08-26-subscription-churn-investigation.md`
  — CH1 → CH2 → CH3 → (conditional) CH4 → CH5.
- Prior findings it builds on: `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`,
  Hypothesis H11.
- Follow-on implementation, authorized and independently available:
  `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`,
  Program 1 **P3 = Tasks 14–17** (writer thread → reader-side capture
  contract → sub-threshold rejection aggregation → the live reader-gate
  flip). Authorized 2026-08-26 (execution program doc §5.1 Verdict,
  reprioritization pass) — not yet started.
- **New (2026-08-27), sequenced between P3 and P4**: Phase **P3.5 = Tasks
  17a–17b**, a live (not replay-based) watchlist-scale stress test
  (`tools/watchlist_scale_stress_test.py`) that widens `kalshi.
  min_volume_24h`/`categories` and toggles `kalshi.live_markets_only`
  (discovery) / `strategy.live_markets_only` (decision layer) to
  empirically calibrate P4's two-consumer threshold and P5's REST-scheduler
  presets before either is built, instead of after. Not started.
- Orchestrator: `.claude/skills/realtime-data-plane-investigation/SKILL.md`
  for CH1–CH5; the remediation plan's own per-phase workflow for Tasks
  14–17.

**Order inside this track:** CH1 → CH2 → CH3 (classifies H11). If CH3
confirms a real bottleneck, do CH4/CH5 *before* Task 14 — a real
churn-driven design constraint should shape the writer-thread's design
up front, not get retrofitted after. Then Task 14 → 15 → 16 → 17. If CH3
classifies H11 as negligible/unrelated, skip straight to Task 14.

**Standing rule while working this track:** no queue/rate/worker/
subscription tuning before CH3 actually classifies a bottleneck
(`.claude/rules/realtime-data-plane-evidence.md`).

---

## Track B — Economic strategy validity

**Status (2026-08-26):** deferred behind Track A by explicit user
decision this session (P3 authorized ahead of Program 2R) — but *not
technically blocked*. A separate session or worktree can pick up Program
2R's re-verification today without conflicting with Track A.

**Canonical docs**
- Investigation (substantially complete — E1–E7, E11–E12 done with real
  evidence): `docs/superpowers/plans/2026-08-26-economic-strategy-effectiveness-investigation.md`.
- Current findings snapshot — **provisional**, needs a fresh re-run
  against then-current data before anything is built on it:
  `docs/superpowers/research/2026-08-26-economic-strategy-effectiveness-status-report.md`.
- Remediation design + plan — **not approved for execution**:
  `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md`
  / `docs/superpowers/plans/2026-08-26-economic-strategy-remediation.md`.

**Entry gate — Program 2R re-run:** enough elapsed time/trade volume
since the realtime P0–P2 fix (`main`@`22d1a79`, 2026-08-26) to make a
re-verification sample meaningful. Check current trade count before
re-running the E1–E7 queries; don't assume it's ready.

**Entry gate — Program 2 (remediation), after that:** explicit human
review/approval of the remediation design doc. Not a Claude-side call.

**Why "deferred," not "blocked":** the 2026-08-26 reprioritization was a
work-priority decision (P3 goes first when the same session/attention
has to choose), not a technical dependency between the two tracks — §8's
matrix already classifies them as safe to run concurrently.

---

## Track C — Downstream production programs (sequential, hard-gated)

Programs 3R → 3 → 4 → 5 → 6 → 7 → 8. Full detail in
`docs/kalshi-personal-production-execution-program-2026-08-26.md` §7.
**Nothing here should start.** Listed only so the dependency shape is
visible in one place.

| Program | Entry gate | Status |
|---|---|---|
| 3R — canonical decision/live execution investigation | Program 1 (incl. P3) + Program 2 exit | not started |
| 3 — canonical decision/real execution implementation | 3R exit + human authorization | not started |
| 4 — canonical shadow qualification | 3 exit | not started |
| 5 — frontend operator console | plan refresh (spec stale) | not started |
| 6 — personal production operations | 3/4 exit + human capital-policy decision | not started |
| 7 — AQC implementation (workflow-health, corrected scope 2026-08-27) | none — write-lane question resolved (see below), spec approved | **not started** |
| 8 — capital qualification (Stages A–F) | 6 exit + each stage's own human gate | not started |

Programs 5 and 7 have no technical dependency on Track A/B — they're
sequenced late by priority, not by a hard gate, which is why Program 7
(AQC implementation) was picked up out of order on 2026-08-26 with no
issue. Program 5 remains available for the same reason if there's ever
a reason to parallelize further.

**Program 7 status (2026-08-27): fresh spec approved, implementation not
started.** History, newest first:

- **2026-08-27 — scope corrected a second time.** Direct user correction:
  even the original 9(+6)-task plan's *subject* was wrong — it audited the
  trading application's own static code findings, not "the automated
  workflow itself" as AQC was always meant to mean (an automated
  project-manager/janitor over this repo's own branches/PRs/CI/plans/
  ledgers). That original implementation is kept, fully merged, and
  renamed `tools/quality_ratchet.py` (PR #43) — a legitimate, working,
  differently-scoped capability, no longer Program 7. The real Program 7
  is freshly designed at
  `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-
  workflow-design.md` — brainstormed and approved in chat, not yet
  planned/implemented. Next step: `superpowers:writing-plans` against
  that spec.
- **2026-08-26 — the write-lane gate below was resolved, not left open.**
  The user's mid-Task-4 request (install GitHub Issues Kanban + dispatch
  write-capable remediation) was generalized during brainstorming into
  **Autonomous Engineering Mode** (AEM) — a separate, source-agnostic
  background-agent mechanism, merged as design docs
  (`docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-
  design.md`, PR #42). AEM explicitly defers *which* sources feed its
  GitHub Issues queue (§11) — whether AQC's own findings ever do is its
  own separate, still-undecided question, deliberately not resolved by
  either design.
- **2026-08-26 (original) — paused after Task 3, mid-plan.** Tasks 1-3
  (schema, automation-key derivation, coordinator policy port) were
  committed on `feat/autonomous-quality-coordination`; Tasks 4-9 (plus
  follow-up Tasks 10-15) later completed under the corrected, decoupled
  design and merged as `quality_ratchet` (see above) — Ledger:
  `.superpowers/sdd/2026-08-26-autonomous-quality-coordination/progress.md`
  on that branch, historical record of that (differently-scoped) effort.

**Old gate, now resolved — kept for history:** ~~a separate write-lane
design investigation must complete and produce its own explicit plan
before implementation resumes — Tasks 4-9 are otherwise unblocked
(zero-write as originally scoped) but should not resume until that
parallel decision is resolved one way or another, so the two efforts
don't end up designing against each other. That investigation had not
yet started as of this board update (blocked on inspecting the actual
MCP tool's real capabilities/credential model — a marketplace page
fetch failed 3x with HTTP 429).~~

---

## Standing human decisions

Not a track — no Claude-executable next step. Listed here so none of
them get lost between sessions.

- [x] Sports-category legal exposure — **decided 2026-08-26: keep scope
  as-is, accept the exposure for now.** (`docs/prediction-markets-research-reference.md`
  Part 3 has the full detail if this is ever revisited.)
- [ ] Shadow-mode sustained run + review — `services/shadow_mode.py` has
  never logged a trade to review (mode flipped to `shadow` twice, both
  reverted within 24h, zero rows either time).
- [ ] Real deployment target — currently local `ddev` on one machine.
- [ ] Auth model confirmation for real money — `services/auth.py`,
  single-operator Google OAuth; sufficiency is a human call.
- [ ] `advisory`/`confidence_calibration` auto-apply governance for real
  money — currently tuned against paper-mode history only.
- [ ] Real position-size/kill-switch numbers — `risk.max_daily_loss_pct`
  is currently `0.85`, not meaningfully protective as configured.

---

## Update discipline

This board is only useful while it's accurate. When a track's next task
changes, edit that track's Status line here in the same commit as the
underlying plan doc's own checkbox update — don't let the two drift.
If keeping this current ever starts costing more than it saves, delete
stale detail rather than let it silently disagree with the canonical
docs — same lesson `CLAUDE.md`'s "Git history + supplementary docs"
section already recorded once for `static/status.html`.
