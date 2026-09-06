# Migration step 1 — `docs/superpowers/plans/` lane classification

Date: 2026-09-06
Applies: `docs/superpowers/specs/2026-09-06-planning-lanes-design.md` (branch
`docs/planning-lanes-design`, commit `f01fd59` — **not merged to `main`**; read from
that branch, since no copy exists on `main`). §3 lane table, §2 straddler clauses
(a)-(d), §5 initiative-granularity rule, §6 step 1 (DECLINED bucket, 24 named
companions).

Scope: all 63 files in `docs/superpowers/plans/` — 62 dated + `README.md`. No
file was moved, renamed, deleted, or labeled on GitHub. No `tools/kanban_sync`
file was touched.

## How each column was decided

- **kind** — `plan`, or `companion-of:<parent filename>`. Companions are the
  `-review` / `-self-review` / `-adversarial-review` / `-consolidation` artifacts
  that belong to a sibling primary plan; they move with the parent and are never
  counted as plans. Parent identified by filename, except the one the design
  names explicitly (`...task8-candidate-ledger-self-review.md`).
- **lane** — §5: the lane matching **what the initiative's own Goal/subject line
  names as its subject**, never a file-reference count (§5 deletes that metric).
  Companions write `inherits`.
- **status** — verified against **reality** (merged PR, shipped code, open issue,
  recorded decision), never against the plan document's own checkboxes.
  `docs/superpowers/plans/README.md` states outright: "The checkboxes lie."
  Operational definitions used, because **the design defines none** (see
  RULE-GAP G1):
  - `done` — every task's deliverable verifiably present in source / merged, or
    explicitly closed as infeasible.
  - `active` — partly shipped **and** carries an open tracking issue or a live
    line in `docs/open-decisions.md`.
  - `stalled` — partly shipped or written-only, no open tracker, no movement.
  - `never-started` — zero deliverables exist in the tree.
  - `declined` — a recorded decision not to do it.
  - Companion rows carry the status of **the review artifact itself** (all are
    committed, with a recorded verdict), not of their parent plan. Counts are
    reported separately below so the two never blend.
- **basis** — the quoted subject phrase for the lane; the checked artifact for
  the status.
- **flag** — `RULE-GAP` where the stated rule genuinely does not decide the row.

---

## Table (63 rows)

| path | kind | lane | status | basis | flag |
|---|---|---|---|---|---|
| `2026-08-24-kalshi-integration-dual-phase.md` | plan | 1 | done | Goal: "Move all production Kalshi **semantic interpretation** behind a documented, CI-enforced **integration boundary**" → Lane 1 (`services/kalshi/`). Status: PR #3 + PR #9 merged; `.claude/rules/kalshi-integration-authority.md` states "the migration is complete (Phase A merged 2026-08-25; Phase C finalized the boundary the same day)"; `services/kalshi/` present. | |
| `2026-08-24-kalshi-integration-phase-a.md` | plan | 1 | done | Goal: "Establish a documented, measurable, CI-enforced **Kalshi integration boundary** and migrate production consumers to it" → Lane 1. Status: `19ed74d` "complete document-backed Kalshi integration"; `tools/kalshi_census.py` (task A0's deliverable) present. | |
| `2026-08-24-kalshi-integration-phase-c.md` | plan | 1 | done | Goal: "Strengthen the stable **Phase A boundary** with selective static typing and final interfaces" → Lane 1. Status: PR #9 merged from `refactor/kalshi-integration-phase-c` (`94bfbe1`). | |
| `2026-08-24-quality-control-plane.md` | plan | 6 | done | Goal: "Convert the repository's recurring manual audits, diagnostics, live investigations, and verification rituals into **durable runtime services** and CI/CD guardrails" — two purposes, neither marked primary; clause (d) first-stated → runtime quality services → Lane 6 (`quality/`, `observability/`, `storage_health/`, `diagnostics/`, `alerting/`). Lane 9 (`tools/quality_audit/`, CI) is a declared cross-lane dependent. Status: plan's last task is `## Task 22`; `76c9be1` "Finalize quality control plane verification (QCP Task 22)"; all five packages present. | |
| `2026-08-25-autonomous-quality-coordination-investigation.md` | plan | 9 | done | Goal: determine how the QCP can "report/escalate/remediate durable findings without stepping on concurrent work or exposing excessive **GitHub authority**" → process governance → Lane 9. Status: plan's own header "Agentic execution: complete (I0-I13)"; deliverable `tools/quality_coordination_sim/` present; branch `chore/autonomous-quality-coordination-investigation` merged. | |
| `2026-08-25-frontend-modularization-catchup-consolidation.md` | companion-of:`2026-08-25-frontend-modularization.md` | inherits | done | Filename suffix `-catchup-consolidation`; body: "Consolidation: frontend-modularization design+plan catch-up review (2026-08-31)". Verdict recorded, committed to `main`. | |
| `2026-08-25-frontend-modularization.md` | plan | 8 | never-started | Goal: "Turn the 13-module, single-import-cycle **dashboard frontend** into owned, testable panels on Preact + signals + htm" → Lane 8 (`frontend/`, `static/`). Status: `ls frontend/src/js/` shows no `legacy/`/`core/`/`lib/`/`charts/`/`panels/`; `frontend/package.json` has no `dependencies` block at all (no preact/signals/htm); issue **#89** `Plan: 2026-08-25-frontend-modularization.md` still OPEN. | |
| `2026-08-25-realtime-data-plane-investigation.md` | plan | 1 | done | Goal: "Causally explain persistent **Kalshi latency, rate-limit pressure, WebSocket backlog/loss**, and missed whale opportunities" → the Kalshi transport plane → Lane 1. Status: deliverable `docs/superpowers/research/2026-08-25-realtime-root-cause-report.md` present (plus baseline/solution-research docs); branch `chore/realtime-data-plane-investigation` merged. | |
| `2026-08-25-realtime-data-plane-remediation.md` | plan | 1 | active | Design §5 assigns this explicitly: Goal names "event-loop stalls and cross-blocking on the **WebSocket and REST planes** — the Kalshi connection itself. Primary lane: **Lane 1**." Status: partly shipped — `_critical_queue`/`_consume_market` in `services/kalshi/websocket.py`, `settlement_resolver` present (Tasks 18/19/24, PR #198); `trip_brake`, `on_loss_event`, `ws_state_verify`, `_connection_generation` all ABSENT from `services/`+`main.py` (Tasks 21-23, 25-28, 31, 32, 40). Open plan-task issues **#134 / #136 / #140** and track issue **#75**; last merge PR #418 (2026-09-01). | |
| `2026-08-26-active-tracks-board.md` | plan | 9 | stalled | Its own opening line: "Living document, **not a plan in its own right**". Lane 9 owns "loose `docs/superpowers/*.md` kickoff files … this lane system's own upkeep". Status: design §1 calls the Track A/B/C concept "9+ days stale"; §6 step 3 schedules it for `docs/archive/` and retires `sources_tracks.py`; last status line inside is dated 2026-08-27. | **RULE-GAP** — the `kind` vocabulary is `plan` \| `companion-of:<parent>`; this file self-declares as neither, and the design's own §6 step-1 count (62 dated = 24 companions + 38 plans) forces it into the `plan` bucket it explicitly denies being. |
| `2026-08-26-autonomous-engineering-mode.md` | plan | 9 | never-started | Goal: "Build the deterministic, machine-checkable **safety gates and the launcher skill** … a background agent which claims GitHub Issues" → `tools/autonomous_mode/` + `.claude/skills/` → Lane 9. Status: `tools/autonomous_mode/` ABSENT, `.claude/skills/autonomous-mode/SKILL.md` ABSENT; only the doc PR #42 ever merged; issue **#81** still OPEN. | |
| `2026-08-26-autonomous-quality-coordination.md` | plan | 9 | done | Goal: "a persisted, read-only **coordinator observation series** that watches `tools.quality_audit`'s static findings on `main`" → governance over the repo's own audit tooling → Lane 9. Status: shipped across `7d442c0`→`f23ba0c` (schema, identity, SQLite policy port, GitHub suppression fetch, scheduler wiring); the module was later renamed `services/quality_coordination.py` → `tools/quality_ratchet.py` (confirmed by `git log --follow tools/quality_ratchet.py`), which is why the plan's named file no longer exists. | |
| `2026-08-26-economic-strategy-effectiveness-investigation.md` | plan | 3 | done | Title/subject: "**Economic Strategy Effectiveness & Execution Realism**" → Lane 3 (`strategy_engine.py`, `execution.py`, `paper_broker.py`). Status: the doc's own task table is E0-E12 with E12 "DONE (this pass)"; deliverables exist in `docs/superpowers/research/` (gate-marginal-contribution, advisory-calibration-execution-audit, population-and-replay-gaps, adversarial-review, status-report, plus an E3-E5 re-verification); E8/E9 never built, E10 closed infeasible — the investigation itself concluded and produced its remediation plan. | **RULE-GAP** — Lane 4 is named "Analytics, advisory & **research**", so every investigation-shaped initiative reads as Lane 4 under the lane's *name* while §5's subject rule sends it to the subject's lane. §3 populates Lane 4 by *package* (`services/research/`), not by document genre, so subject wins here — but the design never states that, and it decides four rows in this table. |
| `2026-08-26-economic-strategy-remediation.md` | plan | 3 | active | Title: "**Economic Strategy Remediation** — Candidate Implementation Plan (Program 2)" → Lane 3 by §5's subject rule. Status: header says "**Status: NOT approved for execution**", but P2-1 (D1, banded cost-aware gate diagnostic) *has* shipped — `population_gate_summary_banded()` is live in `services/candidate_log.py:195,259,295`, and `docs/open-decisions.md:35` records it as "**shipped** (PR #631)" under issue **#616** (branches `feat/616-…` and `work/616-…` exist). P2-2..P2-5 unshipped; D5 explicitly parked "after the enable decision". | **RULE-GAP** — the subject ("Economic Strategy") is Lane 3, but **all five** candidate tasks land in Lane 4 files (`candidate_log.py`, `advisory_engine.py` — `candidate_log.py` is Lane 4 by §3's own correction). §5 explicitly forbids the file-reference count that would resolve the split, and states no other tiebreak for subject-vs-every-deliverable disagreement. |
| `2026-08-26-kanban-board-sync.md` | plan | 9 | done | Goal: "Build **`tools/kanban_sync`**, a deterministic, independently-testable reconciler" → Lane 9 (`tools/`). Status: `tools/kanban_sync/` present with the full parser/reconciler/`github_client` surface. | |
| `2026-08-27-autonomous-quality-coordination-workflow.md` | plan | 9 | done | Goal: "Build `tools/coordination_engine.py` … and `tools/quality_coordination.py` … over **this repository's own engineering workflow** (branch/PR/CI lifecycle … standing-rule and process hygiene)" → Lane 9. Status: both files present in `tools/`, plus `tools/quality_coordination_data/`. | |
| `2026-08-27-backend-services-modularization.md` | plan | 9 | done | Goal: "**Physically relocate** 8 flat `services/*.py` files into the existing packages … and extract `POST /api/reset` … into a new `services/reset/` package — pure relocation, zero behavior change." Status: `services/reset/`, `services/config/`, `services/history/`, `services/position/` all present; PR #101 merged from `refactor/backend-services-modularization`. | **RULE-GAP** — the subject is *the `services/` package layout itself*, which no lane owns. Its four tasks land in Lanes 4/7/3/6 respectively; §5 forbids the file-count tiebreak, and §3's "`main.py` stays unowned" escape covers only `main.py`. Assigned Lane 9 as the closest fit (repo-structure governance), but the rule does not produce this answer. |
| `2026-08-27-kanban-sync-milestones-and-subissues.md` | plan | 9 | done | Goal: "Give plan-tracked **`kanban_sync` issues** a milestone … and one GitHub-native sub-issue per task" → Lane 9. Status: `tools/kanban_sync/plan_tasks.py` present; `decompose-plan` subcommand wired at `tools/kanban_sync/__main__.py:298`. | |
| `2026-08-27-workflow-remediation.md` | plan | 9 | done | Goal: "Turn the **workflow audit's** four root causes into mechanisms — one test owner, plugin routing printed at session start, a single open-decisions list, a stop rule, no duplicate skills" → Lane 9 (`.claude/`, hooks, rules). Status: PR #147 merged 2026-08-28 (`b575288`); Task 6's skill consolidation landed as `05faa3b`/`bc6dd47`; `docs/open-decisions.md` exists and is session-printed. (Task 5's stop rule was later withdrawn by direct instruction — a subsequent decision, not an unfinished task.) | |
| `2026-08-28-kanban-sync-improvements.md` | plan | 9 | done | Goal: "Fix three failures from the 2026-08-28 live sync run: **closed-parent guard, classification guidance, and mismatch comment quality**" → Lane 9. Status: `get_issue()` at `tools/kanban_sync/github_client.py:282`; `classification` field at `tools/kanban_sync/models.py:23`; branch `chore/kanban-sync-improvements` merged. | |
| `2026-08-29-event-scoped-me-gate.md` | plan | 3 | declined | Title: "**Event-Scoped ME Entry Gate**" → an entry gate in `mutual_exclusivity.py` → Lane 3. Status: the document's own banner is "⛔ **RETIRED 2026-08-31** — do not execute this plan; it would regress shipped code"; PR #298 (merged) independently fixed the same root cause; residual scope preserved as issues #277/#289–293 on the "Unplanned" milestone. Zero of its tasks will ever run. | **RULE-GAP** — "retired because superseded by shipped code, goals re-homed as issues" is decline-*shaped* but is not a decision to decline the work; the design names only `weather-index-ingestion` as the DECLINED case and provides no bucket for supersession. Filed as `declined` because none of the other four fits at all. |
| `2026-08-30-data-retention-pruning.md` | plan | 6 | done | Goal: "Stop **`data/backups/`'s unbounded growth** … by splitting the backup mechanism into two independently-cadenced tiers" — first-stated purpose (clause d) is the backup mechanism → Lane 6 (`backup/`). `market_history.py`'s `prune()` (Lane 1) is the second-stated half and a declared cross-lane dependent. Status: PR **#302** MERGED 2026-08-31; follow-up `a3debd3` bounded the prune DELETE. | |
| `2026-08-30-entry-gate-me-pairing-and-netting-remediation.md` | plan | 3 | done | Goal, first-stated: "Stop a verified, real-dollar **entry-side bug** (whale-follow buying both sides … because the **ME-pairing gate** can't see off-watchlist candidates)" → `mutual_exclusivity.py`/`decision_bridge.py` entry gate → Lane 3. Status: PR **#298** MERGED, follow-up PR **#303** MERGED; Part 3 shipped as `1aff28e` (`milestone_scan` scheduler) + `56ae320` (`_fetch_live_status` cache check); `fee_cost` column present at `services/position/account_positions.py:63,67`. | |
| `2026-08-30-kalshi-category-data-completeness-implementation-catchup-consolidation.md` | companion-of:`2026-08-30-kalshi-category-data-completeness-implementation.md` | inherits | done | Filename suffix `-catchup-consolidation`; body: "Consolidation: kalshi-category-data-completeness plan catch-up review (2026-08-31)", verdict **GO**. | |
| `2026-08-30-kalshi-category-data-completeness-implementation.md` | plan | 1 | done | Goal: "a real **`series_metadata`/`series_tags` store**, a shared milestone live-data extractor … the Commodities Pyth feed, and `political_race`'s structured-candidate resolution. **Read/store-more-data work only**" → Kalshi data capture → Lane 1. Status: PR **#374** MERGED 2026-09-01, titled "Kalshi category data completeness — 14-task implementation"; per-task commits through `2646ddd` (Task 14) present; `services/market_watch/milestone_live_data.py` present. | |
| `2026-08-30-self-feeding-loop-provenance.md` | plan | 4 | done | Goal, first-stated (clause d): "Give the **advisory**/calibration auto-tuning loop a way to see that its own inputs were incomplete (#214)" → `advisory/` → Lane 4. `whale_calibration/` (Lane 2) and the module's actual home `services/quality/` (Lane 6) are declared cross-lane dependents. Status: `services/quality/evidence_provenance.py` present; `dbca2e1`/`16a807c` close the #214 open-decision. | |
| `2026-08-30-whale-confidence-scoring-remediation-implementation-catchup-consolidation.md` | companion-of:`2026-08-30-whale-confidence-scoring-remediation-implementation.md` | inherits | done | Filename suffix `-catchup-consolidation`; body: "Consolidation: whale-confidence-scoring-remediation plan catch-up review (2026-08-31)". | |
| `2026-08-30-whale-confidence-scoring-remediation-implementation.md` | plan | 2 | active | Goal: "Fix **`whale_confidence_weights`**' four fabricated-input sites and its tie-blind tertile measurement … split the single composite score into an accuracy score and a real-time edge score" → `confidence_scoring.py` / `whale_calibration/` → Lane 2. Status: PR **#388** MERGED 2026-09-01 — but its title is explicitly "(**Tasks 1-9**)" of a 16-task plan; issue **#320** `Plan: …whale-confidence-scoring-remediation-implementation.md` still OPEN; `docs/open-decisions.md:39-41` carries live follow-ups (#364 Task 10 re-validation, #366 Apply-guard, #493). | |
| `2026-08-31-claudesuperpower-plugin-pilot-consolidation.md` | companion-of:`2026-08-31-claudesuperpower-plugin-pilot.md` | inherits | done | Filename suffix `-consolidation`; body: "Consolidation: claudesuperpower.com plugin pilot plan stage (2026-08-31)". | |
| `2026-08-31-claudesuperpower-plugin-pilot-review.md` | companion-of:`2026-08-31-claudesuperpower-plugin-pilot.md` | inherits | done | Filename suffix `-review`; body: "Self-review: claudesuperpower.com plugin pilot plan (2026-08-31)". | |
| `2026-08-31-claudesuperpower-plugin-pilot.md` | plan | 9 | never-started | Subject: "plugin pilot rollout" for four `claude-plugins-official` plugins; the plan's own constraint says "This is **workflow/tooling config**, not application code — `services/`, `main.py`, and `config/settings.yaml` are untouched by every task" → Lane 9 (`.claude/`). Status: `.claude/settings.json`'s `enabledPlugins` holds only `context7`, `dimensional-analysis`, `chrome-devtools-mcp` — none of the four pilot plugins; issue **#321** OPEN. `docs/open-decisions.md:37` records a human GO for two of them and a decline for `codspeed`, but nothing has been installed. | |
| `2026-08-31-weather-index-ingestion-consolidation.md` | companion-of:`2026-08-31-weather-index-ingestion.md` | inherits | done | Filename suffix `-consolidation`; body: "Consolidation: weather index ingestion plan stage (2026-08-31)". | |
| `2026-08-31-weather-index-ingestion-review.md` | companion-of:`2026-08-31-weather-index-ingestion.md` | inherits | done | Filename suffix `-review`; body: "Self-review: weather index ingestion plan (2026-08-31)". | |
| `2026-08-31-weather-index-ingestion.md` | plan | 1 | **declined** | Subject: "New **`services/weather_index/`** package polling Kalshi's per-city **weather index** (ingestion only)" → Lane 1 ("Kalshi & **index data ingestion**", which already owns `index_feed/ingestion.py`). Status: `docs/open-decisions.md:38` — "Weather-index ingestion: **declined for now** (ingestion with no consumer, no Climate series watched); design/plan stay valid to reopen · **#331** comment". `services/weather_index/` ABSENT, no `data/weather_index.db`. **Not `done`.** | |
| `2026-09-01-event-loop-blocking-fix1.md` | plan | 1 | done | Goal: stop "`record_cfbenchmarks()`/`record_pyth()`/`record_observation()`/`record()`/`record_book()`" blocking the loop — the named functions live in `services/index_feed/ingestion.py`, `services/game_state.py`, `services/series_watcher.py` (all Lane 1) → Lane 1 + `concern:hotpath`. (`settlement_edge.py`, Lane 4, is a declared cross-lane dependent.) Status: the `should_flush` return contract is live in all nine files (`services/settlement_edge.py:130-158`, `services/game_state.py:228`, `index_feed/ingestion.py`, `series_watcher.py`, `whale_stream/*_handlers.py`, `market_watch/live_status.py`, `market_watch/event_metadata.py`, `index_feed/backfill.py`). | |
| `2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md` | plan | 6 | done | Goal, first-stated: "Convert every DB-touching function `**diagnostics**.run_offline()` … in `services/diagnostics/diagnostics.py` and `services/series_watcher.py`, from raw synchronous `sqlite3` to `aiosqlite`" → `diagnostics/` → Lane 6 + `concern:hotpath`. `series_watcher.py` (Lane 1) is the declared cross-lane dependent. Status: `services/diagnostics/_aio_db.py` PRESENT; `_diagnostics_pool.py` deleted exactly as the Goal requires (`1f24571` "wire run_offline() as async end-to-end, delete _diagnostics_pool.py"); PR **#420** merged. | |
| `2026-09-01-whale-scoring-connection-reuse.md` | plan | 2 | done | Title "Write-Path Capacity Fixes"; Goal, first-stated fix: "(1) Eliminate the per-trade fresh-SQLite-connection overhead in the **whale-scoring pipeline**, on its own dedicated pool" → `services/whalewatchers/`, `kalshi_trade_tape.py` → Lane 2. Fix 2 (diagnostics, Lane 6) is the second-stated half. Status: both shipped — `3ff41d4` created `services/whalewatchers/_scoring_pool.py` (PRESENT), `e3821d4` created `_diagnostics_pool.py` (since deleted by the fix2 plan above, by design). | |
| `2026-09-03-ci-pipeline-audit-tier1-fixes.md` | plan | 9 | done | Goal: "restore real **per-push test selection**, remove proven sqlite fsync overhead from the pytest suite, stop four tests duplicating an already-required **CI job**, and correct three drifted claims in `docs/woodpecker-ci.md`" → Lane 9 (`.woodpecker/`, `tests/`, CI docs). Status: all four shipped — `e546110`, `a624fa2`, `e5b43b2`, `042d124`. | |
| `2026-09-03-frontend-modularization-freshness-check.md` | companion-of:`2026-08-25-frontend-modularization.md` | inherits | done | Its own second line: "**Subject:** `docs/superpowers/plans/2026-08-25-frontend-modularization.md`"; verdict "GO, with **5 corrections applied directly to the plan**"; merged as PR #461 "frontend-modularization plan freshness fixes". Functionally a review companion of that plan. | **RULE-GAP** — the design defines companions by four filename suffixes (`-review`, `-self-review`, `-adversarial-review`, `-consolidation`) and fixes the count at **24**. This file is companion-shaped in content but carries none of those suffixes, so the design's arithmetic counts it as an independent plan. Classified by content, which makes the real split **25 companions / 37 plans**, not 24/38 — a one-row correction to §6 step 1's own number. |
| `2026-09-03-persistence-layer-db-migration-implementation-consolidation.md` | companion-of:`2026-09-03-persistence-layer-db-migration-implementation.md` | inherits | done | Filename suffix `-consolidation`; body: "Consolidation: persistence-layer db.py migration implementation plan (PR #516)". | |
| `2026-09-03-persistence-layer-db-migration-implementation-self-review.md` | companion-of:`2026-09-03-persistence-layer-db-migration-implementation.md` | inherits | done | Filename suffix `-self-review`; body: "Self-review: persistence-layer db.py migration implementation plan". | |
| `2026-09-03-persistence-layer-db-migration-implementation.md` | plan | 5 | done | Title/Goal: "**Persistence Layer db.py Migration**" — 26 modules onto `services/db.py` → Lane 5 (`db.py` is listed there). Status: PR **#516** MERGED (plan doc); Tasks 4-13 all merged as individual PRs (`04b8bba` T4, `ca23ab1` T5, `3081096` T6, `4737dcf` T7, `f3bdfd2` T8, `4d29ce7` T9-10, `ec4e665` T11, `fb38e08` T12, `1e3ca1b` T13, PRs #524/#540/#545/#553/#554/#556/#557/#559/#561); Task 15 Gate 2 recorded in `3ba55ff`. | |
| `2026-09-03-persistence-layer-implementation-consolidation.md` | companion-of:`2026-09-03-persistence-layer-implementation.md` | inherits | done | Filename suffix `-consolidation`; body: "Consolidation — Persistence Layer Implementation Plan (2026-09-03)", verdict **GO**. | |
| `2026-09-03-persistence-layer-implementation-pr-consolidation.md` | companion-of:`2026-09-03-persistence-layer-implementation.md` | inherits | done | Filename suffix `-pr-consolidation`; body: "PR-Stage Consolidation — Persistence Layer Implementation Plan (PR #484)". | |
| `2026-09-03-persistence-layer-implementation-pr-self-review.md` | companion-of:`2026-09-03-persistence-layer-implementation.md` | inherits | done | Filename suffix `-pr-self-review`; body: "PR-Stage Self-Review — Persistence Layer Implementation Plan (PR #484)". | |
| `2026-09-03-persistence-layer-implementation-review.md` | companion-of:`2026-09-03-persistence-layer-implementation.md` | inherits | done | Filename suffix `-review`; body: "Adversarial Review — Persistence Layer Implementation Plan". | |
| `2026-09-03-persistence-layer-implementation.md` | plan | 5 | done | Goal: "build the shared **`services/db.py`** closing-connection module and migrate the three highest-value modules onto it, root-cause-fix `candidate_log.db`'s live lock contention" → Lane 5. Status: PR **#484** MERGED 2026-09-03; `services/db.py` PRESENT (`d124151` "build services/db.py, the shared closing-connection module"); Task 3 `candidate_log.py` migration merged (`3e3f17a`), Tasks 5/6 merged (`04b8bba`, `ca23ab1`). | |
| `2026-09-03-persistence-layer-task8-candidate-ledger-self-review.md` | companion-of:`2026-09-03-persistence-layer-db-migration-implementation.md` | inherits | done | **Design-assigned exception** (§6 step 1): no filename-obvious parent; assigned to the db-migration plan as Task 8. Confirmed by the file's own first body line: "Plan: `…2026-09-03-persistence-layer-db-migration-implementation.md` lines 1597-1715 (**Task 8**)". | |
| `2026-09-03-strategy-edge-gate-implementation-consolidation.md` | companion-of:`2026-09-03-strategy-edge-gate-implementation.md` | inherits | done | Filename suffix `-consolidation`; body: "Consolidation — Strategy Edge Gate Implementation Plan (2026-09-03)", verdict **GO**. | |
| `2026-09-03-strategy-edge-gate-implementation-review.md` | companion-of:`2026-09-03-strategy-edge-gate-implementation.md` | inherits | done | Filename suffix `-review`; body: "Adversarial Review: Strategy Edge Gate Implementation Plan". | |
| `2026-09-03-strategy-edge-gate-implementation.md` | plan | 3 | done | Goal: "Turn the approved edge/EV **entry gate** design into working, tested, paper-mode-only code: … **the gate itself inside `_validate_entry_price`**" → `strategy_engine.py` → Lane 3. Status: PR **#502** MERGED 2026-09-03, titled "Strategy edge/EV gate implementation — **all 10 tasks**"; per-task commits `2452e84` (T1), `6348273` (T5), `fedfd35` (T7), `eed3a40` (T8), `bb6033b` (T9). `edge_gate_enabled: false` at `config/settings.yaml:201` is the plan's own intended inert-by-default end state, not incompleteness; enablement is a separate human decision (#616). | |
| `2026-09-03-tier0-live-incident-remediation-consolidation.md` | companion-of:`2026-09-03-tier0-live-incident-remediation.md` | inherits | done | Filename suffix `-consolidation`; body: "Consolidation — Tier 0 live-incident remediation plan (2026-09-03)", verdict **GO**. | |
| `2026-09-03-tier0-live-incident-remediation-plan-review.md` | companion-of:`2026-09-03-tier0-live-incident-remediation.md` | inherits | done | Filename suffix `-plan-review`; body: "Adversarial Review: Tier 0 Live-Incident Remediation Implementation Plan". | |
| `2026-09-03-tier0-live-incident-remediation-pr-review.md` | companion-of:`2026-09-03-tier0-live-incident-remediation.md` | inherits | done | Filename suffix `-pr-review`; body: "PR-Stage Adversarial Review: PR #441 — Tier 0 Live-Incident Remediation Implementation Plan". | |
| `2026-09-03-tier0-live-incident-remediation.md` | plan | 6 | active | Goal, first-stated: "Stop **`GET /api/health/pipeline`** from hanging indefinitely" → `services/diagnostics/routes.py` → Lane 6 (`diagnostics/`) + `concern:hotpath`. Status: PR **#441** MERGED (plan doc). Code: Tasks 1-6 shipped per `docs/open-decisions.md:42`; **Task 7 also verifiably shipped** (`/api/health/faults` is now `async` with `asyncio.gather(asyncio.to_thread(...))` at `services/diagnostics/routes.py:577-588`) and **Task 9 too** (`_current_fd_count`/`_fd_soft_limit` at `:102-106,451`, commit `ecedade`) — so open-decisions' 2026-09-06 line "Tasks 7/9/10 still open" is **stale on 7 and 9**. Genuine remainder: Task 10 (full regression + live validation gate). Issue **#448** still OPEN. | |
| `2026-09-03-tier1-backend-hygiene-consolidation.md` | companion-of:`2026-09-03-tier1-backend-hygiene.md` | inherits | done | Filename suffix `-consolidation`; body: "Consolidation — Tier 1 Backend Hygiene Implementation Plan (2026-09-03)", verdict **GO**. | |
| `2026-09-03-tier1-backend-hygiene-pr-consolidation.md` | companion-of:`2026-09-03-tier1-backend-hygiene.md` | inherits | done | Filename suffix `-pr-consolidation`; body: "PR-Stage Consolidation — Tier 1 Backend Hygiene Plan (PR #483)". | |
| `2026-09-03-tier1-backend-hygiene-pr-review.md` | companion-of:`2026-09-03-tier1-backend-hygiene.md` | inherits | done | Filename suffix `-pr-review`; body: "PR-Stage Adversarial Review — Tier 1 Backend Hygiene Plan (PR #483)". | |
| `2026-09-03-tier1-backend-hygiene-pr-self-review.md` | companion-of:`2026-09-03-tier1-backend-hygiene.md` | inherits | done | Filename suffix `-pr-self-review`; body: "PR Self-Review — Tier 1 Backend Hygiene Plan (PR #483)". | |
| `2026-09-03-tier1-backend-hygiene-review.md` | companion-of:`2026-09-03-tier1-backend-hygiene.md` | inherits | done | Filename suffix `-review`; body: "Adversarial Review: Tier 1 Backend Hygiene Implementation Plan". | |
| `2026-09-03-tier1-backend-hygiene.md` | plan | 5 | done | Goal: "Work the second-pass architecture audit's Tier 1 … items 7-14 — **stall attribution**, dashboard de-polling, three safety-adjacent DRY fixes, one more event-loop-blocking write, the config-comment-wipe mechanism, unbounded-`limit` routes …, `/api/state`'s oversized field and dead ETag, and two GC/timeout hygiene items." First-stated item is stall attribution (`loop_watchdog.py`) → Lane 5. Status: PR **#500** MERGED 2026-09-03 ("Tier 1 backend hygiene — stall logging, de-polling, DRY fixes, config fix, pagination, throttling"); per-task commits `0e90287`, `d2baefd`, `610cf54`, `cbf8315`, `24e848f`. | **RULE-GAP** — a deliberately heterogeneous "hygiene" batch whose eight tasks span Lanes 3 (`risk_manager.py`, `shadow_mode.py`), 5 (`loop_watchdog.py`, `pagination.py`, `state_view.py`, `http_client.py`), 6 (`alerting/`), 7 (`config_store.py`) and 8 (dashboard de-polling). It names **no single subject**; §5's clause-(d) first-stated tiebreak lands it on Lane 5 purely by the order items were listed in the audit, which is not a meaningful assignment. |
| `2026-09-04-scoring-pool-candidate-retry-isolation-implementation.md` | plan | 2 | done | Goal: "Give **`candidate_retry`'s scoring path** its own dedicated 1-worker thread pool so it no longer shares `services/whalewatchers/_scoring_pool.py`'s 4-worker pool" → `whalewatchers/`, `candidate_retry.py` → Lane 2. Status: `services/whalewatchers/_candidate_retry_pool.py` PRESENT; `9b55c1b` "route candidate-retry scoring off the shared WS scoring pool (#563)" merged. | |
| `README.md` | index | 9 | stalled | Not a plan and not a companion — "# Plans index … This file is the map." Lane 9 by §3's "non-Kalshi top-level `docs/*.md`, loose `docs/superpowers/*.md` … this lane system's own upkeep"; design §6 step 5 rules explicitly on it ("**retire, not regenerate**"). Status: content-stale — its own header says "**22 plans**, ~22,300 lines" while 38 dated primary plans exist (24 rows in its table), and it omits every plan dated 2026-08-30 or later except three. | **RULE-GAP** — neither `kind` value fits an index file (recorded as `index`, a value the design does not define), and none of the five statuses describes a hand-maintained index whose design-mandated disposition is "retire". |

---

## Summary

### Counts per lane (38 lane-bearing rows: 37 dated plans + `README.md`; 25 companions write `inherits`)

| Lane | Name | Count |
|---|---|---|
| 1 | Kalshi & index data ingestion | **8** |
| 2 | Whale signal detection & calibration | **3** |
| 3 | Strategy, risk & execution | **5** |
| 4 | Analytics, advisory & research | **1** |
| 5 | Runtime infrastructure | **3** |
| 6 | Observability, quality & safety infra | **4** |
| 7 | Config & control plane | **0** |
| 8 | Frontend & dashboard | **1** |
| 9 | Tooling, CI & process governance | **13** |
| — | companions (`inherits`) | 25 |
| | **Total** | **63** |

Lane 7 has **zero** plan documents, and Lane 4 has one. Lane 9 holds 34% of all
plans — worth knowing before step 2 sizes the per-lane labeling batches.

### Counts per status

**Plans (37 dated primary plans):**

| Status | Count | Files |
|---|---|---|
| `done` | **27** | kalshi-integration ×3, quality-control-plane, aqc-investigation, realtime-investigation, aqc-report-only, economic-investigation, kanban-board-sync, aqc-workflow, backend-services-modularization, kanban-sync-milestones, workflow-remediation, kanban-sync-improvements, data-retention-pruning, entry-gate-me-pairing, kalshi-category-data-completeness, self-feeding-loop-provenance, event-loop-blocking-fix1, event-loop-blocking-fix2, whale-scoring-connection-reuse, ci-pipeline-audit-tier1-fixes, persistence-layer-db-migration, persistence-layer-implementation, strategy-edge-gate, tier1-backend-hygiene, scoring-pool-candidate-retry-isolation |
| `active` | **4** | realtime-data-plane-remediation, economic-strategy-remediation, whale-confidence-scoring-remediation, tier0-live-incident-remediation |
| `stalled` | **1** | active-tracks-board |
| `never-started` | **3** | frontend-modularization, autonomous-engineering-mode, claudesuperpower-plugin-pilot |
| `declined` | **2** | **weather-index-ingestion**, event-scoped-me-gate |
| | **37** | |

**Other rows:** 25 companions, all `done` (each is a committed artifact with a
recorded verdict — this describes the review artifact, not its parent).
`README.md`: `stalled`.

### RULE-GAP rows: **8**

| # | Row | Gap |
|---|---|---|
| G1 | *(global — recorded on no single row)* | The design mandates a DECLINED bucket but **defines none of the five statuses** and gives no staleness threshold separating `active` from `stalled`. Operational definitions were written above and applied uniformly; they are this table's addition, not the design's. |
| G2 | `2026-09-03-frontend-modularization-freshness-check.md` | Companion-shaped in content, none of the four companion filename suffixes → the design's own **24-companion count is off by one**. Real split is 25/37. |
| G3 | `2026-08-27-backend-services-modularization.md` | Subject is *the `services/` package layout itself*; **no lane owns repo structure**, tasks span Lanes 3/4/6/7, and §5 forbids the file-count tiebreak. |
| G4 | `2026-09-03-tier1-backend-hygiene.md` | Heterogeneous hygiene batch spanning Lanes 3/5/6/7/8 with no named subject; clause (d) resolves it by audit-list ordering, i.e. by accident. |
| G5 | `2026-08-26-economic-strategy-remediation.md` | Subject → Lane 3, but **all five** tasks land in Lane 4 files; §5 explicitly deletes the only tiebreak that would settle it. |
| G6 | `2026-08-29-event-scoped-me-gate.md` | "Retired because superseded by shipped code" is decline-shaped but is not a decline decision; no bucket covers supersession. |
| G7 | `2026-08-26-active-tracks-board.md` | Self-declares "not a plan in its own right"; `kind` has no third value, yet §6's arithmetic counts it as a plan. |
| G8 | `README.md` | Index file: no `kind` value, and its design-mandated disposition ("retire") is not one of the five statuses. |
| G9 | `2026-08-26-economic-strategy-effectiveness-investigation.md` | Lane 4's **name** ("Analytics, advisory & **research**") claims every investigation-shaped initiative, while §5's subject rule sends it to its subject's lane. §3 populates Lane 4 by *package* (`services/research/`), not doc genre, so subject wins — but the design never says so, and this ambiguity touches four investigation/audit-derived rows in this table. |

Note: **8 rows carry a `RULE-GAP` flag** (G2-G9). G1 is a genuine gap in the
design but is recorded only here, not in any row's `flag` column, because it
applies to all 63 rows equally — so the register has 9 entries against 8 flagged
rows.

### Files that could not be classified at all

**None.** All 63 rows carry a kind, a lane (or `inherits`), and a status. Eight
of them carry a `RULE-GAP` flag because the stated rule did not produce the
answer on its own; the value recorded in those rows is a judgment call, marked as
such, not a rule output.

### Two corrections this pass makes to numbers the design states

1. **Companion count is 25, not 24** (G2) — so the plan count is 37, not 38.
2. `docs/open-decisions.md:42`'s "PR #441 follow-ups … Tasks 7/9/10 still open"
   (dated 2026-09-06) is **stale on Tasks 7 and 9** — both verifiably shipped
   (`services/diagnostics/routes.py:577-588` and `:102-106,451`). Only Task 10
   remains. Not fixed here; this table is read-only.
