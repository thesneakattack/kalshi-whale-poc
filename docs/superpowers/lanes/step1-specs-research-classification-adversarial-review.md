# Adversarial review — lanes classification table (specs/ + research/, 176 rows)

Reviewer context: fresh Agent call, no memory of the drafting session. Every claim below was re-derived from `git log`, `gh issue view`, `docs/open-decisions.md`, or the file itself; nothing was taken from the table's Reason column or the self-review.

## Item 1 — Is the three-part "deliverable" test sound? Partially. It is a valid rule-out for `never-started`, but it cannot produce `done`/`active`/`superseded`, and the table filled that gap with a convention that contradicts the design.

Ten rows re-tested from scratch:

| Row (table status) | Test clause hit | Independent status | Verdict on test |
|---|---|---|---|
| `2026-08-24-kalshi-integration-boundary-design` (superseded) | (b) — `.claude/rules/kalshi-integration-authority.md`: "Phase A merged 2026-08-25; Phase C finalized the boundary the same day" | **done** — its own proposal shipped | Test correctly rules out never-started; says nothing about superseded-vs-done. Table picked superseded with no evidence anything *different* overtook it. |
| `2026-08-25-realtime-root-cause-report` (superseded) | (a) — cited by `plans/2026-08-25-realtime-data-plane-remediation.md`, `plans/2026-08-26-economic-strategy-effectiveness-investigation.md`, `research/2026-08-26-economic-population-and-replay-gaps.md` | **done** — research record consumed by the next stage | Same gap. |
| `2026-08-26-economic-population-and-replay-gaps` (active) | (a) + open tracker #616 | **active** ✓ | OK — but `active` came from a *separate* convention, not from the test. |
| `2026-09-02-architecture-audit-and-rewrite-considerations` (active) | (a) — edge-gate design: "Research this design implements: ...architecture-audit-and-rewrite-considerations.md §3" | **active** is weakly defensible (via #616 → edge-gate → audit); no open issue body cites the audit directly (`gh issue view 563 --json body \| grep -c architecture-audit` → `0`) | Test is silent on trackers; the table's `active` rests on an indirect chain. |
| `2026-09-05-issue-150-fix-family-benchmark` (done) | (c) — own text l.16-17 "Fix Family 1 ... is already shipped", l.123 "Family 2 ... no gap to fix"; `#150 CLOSED closed=2026-09-05T23:45:12Z` | **done** ✓ | OK. |
| `2026-08-27-workflow-audit` (declined) | (b) — PR #147 `b575288 Merge pull request #147 ... chore/workflow-remediation` shipped its prescriptions; budgets withdrawn next day (memory "Effort caps are kneecapping") | borderline **superseded** (shipped, then a later decision overtook it) vs `declined` | Test has no branch for "shipped then reversed". Not a required fix; either is arguable. |
| `2026-08-26-economic-strategy-remediation-design` (**never-started**) | (b) satisfied — `services/candidate_log.py:359 def population_gate_summary_banded(...)`, `:195 # ...issue #616 D1 (docs/superpowers/...`, `0436532 Merge pull request #631 ... feat/616-d1-banded-gate-diagnostic`; #616 OPEN; `open-decisions.md`: "spec D1 ... **shipped** (PR #631)" | **active** | Test rules out never-started correctly. **The table did not actually apply clause (b)** — it asserted "no evidence the remediation itself was implemented" without checking code. |
| `2026-08-30-test-coverage-audit-handoff` (stalled) | (b) satisfied — BUG 1: `610480d 2026-08-30 fix: position_netting treated a 0.0 volatility reading as calm` (`position_netting.py:287 vols = [v for v in vols if v is not None and v > 0]`); BUG 2: `series_watcher.py:782 # ...Fee-free breakeven was displayed here until 2026-08-30` | **stalled** stands (P0 test gaps + 3 "user decisions still open" have no tracker), **but the Reason is false** ("no later evidence found of the two bugs being fixed" — both fixed the same day) | Clause (b) again not actually run. |
| `2026-08-26-autonomous-engineering-mode-design` (superseded) | (a) satisfied by a *plan doc* only; zero code (`grep -rln autonomous-engineering-mode tools .claude` → nothing; no claim loop in `tools/kanban_sync`); `#81 OPEN` body: "Classification: not-started. Spec's own header: 'Not yet implemented.'" | **active** | **Clause (a) cannot distinguish "consumed by a plan doc" from "implemented."** The table's cited evidence, `0714509 Merge pull request #42 ... docs/autonomous-engineering-mode-spec`, is the PR that merged the spec *itself*. |
| `2026-09-03-persistence-layer-redesign-design` (superseded) | overtaken — db-migration design l.17/51/87 re-decides the `register_ddl`/`_DDL_REGISTRY` API | **superseded** ✓ | The one case where the narrow definition genuinely applies. |

**Specific defects in the test as used:**
1. It is a rule-out (not never-started) that was used as a rule-in for `superseded`. The table's own remap convention (a) says `superseded` = "the document's own proposal, *or* a clearly-later document that overtook it, has shipped" — the first disjunct is the design's definition of `done`. That sentence is the mechanism behind 146/176.
2. Clause (a) treats a docs-only PR (merging the plan/spec) as "shipped" — three rows were marked superseded on docs-PR merges: PR #42 (spec itself), PR #314 (`docs/toolkit-plugin-pilot-plan`), PR #570 (`docs/scoring-pool-isolation-plan`).
3. Clause (b) was asserted, not run, in at least three rows (economic remediation, test-coverage handoff, backend modularization — see item 3).
4. No tracker check at all, so the test can never yield `active`; rows with open `Plan:` trackers (#320, #321, #81, #89, #75) were labeled superseded/never-started.

## Item 2 — `superseded` sample: 3 of 19 survive the narrow definition (16%)

| Row | Evidence read | Survives? → correct status |
|---|---|---|
| `kalshi-category-data-completeness-design` | `887c653 Merge PR #374`; Tasks 1–14 in log; no open issue matches `kalshi-category-data-completeness` | No → **done** |
| `whale-confidence-scoring-remediation-design` | `fa2a267 Merge PR #388` = Tasks 1–9 only; `#364 OPEN Task 10`, `#370 OPEN Task 16`, `#320 OPEN Plan:` | No → **active** |
| `strategy-edge-gate-design` | `3630876 Merge PR #502`; #616 body: "Edge/EV gate shipped inert: `_edge_gate_check` (PR #502 Tasks 8-9), `edge_gate_enabled: false` ... Enable decision not yet made"; #616 cites the design's own §9 success criterion as unmet | No → **active** |
| `persistence-layer-db-migration-design` | PR #505 (spec) + `6e6bddc Merge PR #516 ... plan/persistence-layer-db-migration-implementation`; no open tracker | No → **done** |
| `history-event-driven-design` | `0198c54 Merge PR #573`; `services/history_push.py:1-2` docstring cites the design by path | No → **done** |
| `self-feeding-loop-provenance-design` | `8c398a2 Merge PR #297`, branch commits `01a1c49 feat: add evidence-completeness signal ... (#214)`, `a27c082 fix: refuse ... auto-apply while a completeness defect is open (#214)`, `5bb29be ... (#60)`; `evidence_provenance.py:5-6` cites the design | No → **done** |
| `autonomous-quality-coordination-design` (08-26) | Its own Amendment: "as originally written and implemented (Tasks 1-9, PR #43) ... Direct user correction, same day: this was a real misunderstanding"; replaced by the 08-27 workflow design + `quality_ratchet` rename | **Yes** → superseded |
| `quality-control-plane-design` (08-24) | `5b73163 Merge PR #45 ... autonomous-quality-coordination-redesign-spec` | **Yes** → superseded |
| `persistence-layer-redesign-design` | see item 1 | **Yes** → superseded |
| `realtime-data-plane-remediation-design` | P0–P2 shipped; design §10 defines P3; `#134`…`#140 OPEN` ("Part of `plans/2026-08-25-realtime-data-plane-remediation.md`"), `#75 OPEN` | No → **active** |
| `autonomous-engineering-mode-design` | see item 1 | No → **active** |
| `scoring-pool-candidate-retry-isolation-design` | PR #570 is docs-only; real code is `c6f3295 feat: dedicated 1-worker pool for candidate-retry scoring (#563)`, `9b55c1b fix: route candidate-retry scoring off the shared WS scoring pool (#563)`; `_candidate_retry_pool.py:9` cites the spec; **`#563 OPEN`** | No → **active** (or done once #563 closes) |
| `seen-trade-ids-concurrency-race-root-cause` | `kalshi_trade_tape.py:273 self._seen_lock = threading.Lock()`, `:284 "(issue #546 ...)"`; **`#546 OPEN`** | No → **active** (done once #546 closes) |
| `cleanup-worktrees-silent-deploy` | `1c3ebcc Merge PR #544`; `#535 CLOSED` | No → **done** |
| `whale-scoring-connection-reuse-design` | `3ff41d4 feat: add dedicated worker pool + connection cache for whale-scoring reads`, `7ced121` | No → **done** |
| `issue-410-pool-vs-aiosqlite-design` | `6a0584c fix: move calibration report off tick_executor, both halves off-loop (#410)`; `#410 CLOSED 2026-09-05`; `#582 CLOSED` (its own Verdict correction) | No → **done** |
| `kanban-board-sync-design` | `plans/2026-08-26-kanban-board-sync.md` Goal: "Build `tools/kanban_sync`..."; `051012d feat: scaffold tools/kanban_sync`; PRs #124/#187/#230/#246/#383 iterate on it; two later designs *extend* it | No → **done** |
| `issue-530-sync-dispatch-sweep` | self-resolving ("the sweep this issue asks for already happened") but **`#530 OPEN`**, #639 filed from PR #636 | No → **active** |
| `claudesuperpower-toolkit-assessment` | consumed by the pilot design (which is `active`, below) | No → **done** |

Because 87 companion rows inherit their parent's status, every reclassification above propagates.

## Item 3 — the 9 flagged rows, re-derived under the corrected definitions

| Row | Table | Independent | Evidence |
|---|---|---|---|
| `application-wide-rest-vs-ws-inventory` | stalled | **done** (or `stalled` with a corrected Reason) | Consumed: architecture audit §6 l.724-727 "Prior art exists and was re-verified rather than inherited: `...application-wide-rest-vs-ws-inventory.md`. Two of its findings are now stale (fixed)..."; also cited by `realtime-data-plane-known-findings.md`. No open tracker. Its own deliverable (the inventory) is complete and consumed → not never-started; `stalled` only if the standing REST-decoupling instruction it serves is treated as its scope. |
| `economic-strategy-remediation-design` | never-started | **active** | D1 shipped (PR #631, `candidate_log.py:359`); #616 OPEN tracks fail-closed + D5; `open-decisions.md` line on the plan doc. |
| `frontend-modularization-design` | never-started | **active** (per the definitions as given) | Downstream artifacts: `plans/2026-08-25-frontend-modularization.md`, `-catchup-consolidation.md`, `2026-09-03-frontend-modularization-freshness-check.md`, issues #462–#481; `#89 OPEN`. Zero code: `grep -n schema services/config/routes.py` → nothing; `frontend/src` contains only `js`. **Definitional weakness to raise, not a table error:** the rule "stalled = no open tracker" makes 9+-day-dormant work `active` on the strength of an unclaimed `Plan:` issue. |
| `kanban-board-sync-design` | superseded | **done** | See item 2. |
| `claudesuperpower-plugin-pilot-design` | superseded | **active** | `#321 OPEN Plan:`, `#322 CLOSED Task 1: Obtain go-ahead`, #323–#328 OPEN; `open-decisions.md`: "Plugin pilot Task 1: GO for `pr-review-toolkit` + `claude-security`". **The table's Reason is factually wrong:** it names dimensional-analysis/chrome-devtools/context7/superpowers as "plugins evaluated by this pilot"; the design l.13-14 names `pr-review-toolkit`, `claude-security`, `claude-md-management`, `codspeed`, none of which is in `.claude/settings.json` `enabledPlugins` (`context7`, `dimensional-analysis`, `chrome-devtools-mcp` only). |
| `session-tooling-friction-log` | stalled | **done** | `b3065ab docs: close out the friction log with tonight's resolution`; `fc6e06b fix: retire R6 ... outright` edits `guard_workflow.py` (−35), `tests/test_guard_workflow.py` and this doc in one commit — its item 2 produced a code change; tail records resolutions; cited by the lanes adversarial review. |
| `test-coverage-audit-handoff` | stalled | **stalled** (Reason must be rewritten) | Both headline bugs fixed 2026-08-30 (`610480d`; `series_watcher.py:782`). Remaining "Test gaps" P0 and 3 "User decisions still open" have no tracker (no new test file in 08-30..09-01 matching netting/breakeven; modified-file test additions not checked). |
| `followups-from-3-plan-implementation` | stalled | **never-started** | Single entry (SDK pin); `requirements.txt:48 kalshi-python-async==3.27.0` unchanged; zero citations; no issue (`gh issue list --search "kalshi-python-async 3.29"` → none); not in `open-decisions.md`. Per CLAUDE.md its content belongs as an open-decisions line. |
| `backend-services-modularization-design` | never-started (UNDECIDED lane) | **done** | `cb396a5 Merge PR #101 ... refactor/backend-services-modularization`; `91dda5c` history/, `6e4338f` config/, `27c9570` position/, `7e8ce04 refactor: extract POST /api/reset ... into services/reset/`; `ls services/reset` → `reset_log.py routes.py trade_archive.py`; `services/config/__init__.py` docstring: "moved in here 2026-08-27 (backend services modularization) ... per `plans/2026-08-27-backend-services-modularization.md`'s Task 2". **The table's "ls services/ confirms" and the self-review's F1 "confirmed via the row's own cited ls services/ check" are both false — neither ran `ls services/reset`.** Lane: still a coordinator call; the plans-table precedent `dcc2643 docs: retract the Lane 9 ruling - repo-structure plans split per task` applies. |

## Item 4 — five additional spot-checks (Lane + Status + evidence)

| Row | Lane check (docstring/title) | Status |
|---|---|---|
| L2 `trade-stream-decoupling-and-history-event-driven-research` | First-stated purpose (l.3-5) "decouple the critical trade stream" → clause (d) → Lane 2 ✓ | superseded → **done/active** (both halves shipped: `c6f3295`/`9b55c1b`, PR #573; #563 open) |
| L3 `entry-gate-me-pairing-and-netting-remediation-design` | Title/§Origin: entry-gate + position_netting → Lane 3 ✓ | superseded → **done** (`fd6a811 fix: bound find_open_confirmed_conflict to 2-outcome events`; fee visibility `313fd71`/`3e5ff2c`; the third part, milestone coverage, shipped under category-data-completeness Task 10 `269ba9d` — partial supersession of one section, not of the design) |
| L6 `run-offline-cooperative-yield-self-review` | `diagnostics.py:856 async def run_offline` → Lane 6 ✓ | superseded → **done** (`f82d402 Merge PR #424`) |
| L8 `history-event-driven-design` | `history_push.py` docstring cites it; Lane 8 ✓ | superseded → **done** |
| L4 `self-feeding-loop-provenance-design` | `evidence_provenance.py:1-2` "for the advisory/calibration auto-tuning loop" → clause (c) → Lane 4 ✓ | superseded → **done** |
| L1 `event-loop-blocking-elimination-design` | PR #414 diffstat: `index_feed/ingestion.py`, `market_watch/*`, `series_watcher.py`, `whale_stream/*` (+`settlement_edge.py`) → Lane 1 ✓ | superseded → **done** |

**All six Lane assignments verified correct against primary sources.** Every Status in the spot-check was wrong in the same direction.

## Incidental findings
- `2026-08-29-trade-performance-analysis` (superseded): its consumer (event-scoped-ME-gate) was declined, but nothing overtook the analysis itself → **done**.
- Stale open issues the classification exposes (follow-ups, not table defects): #289–#293 are task sub-issues of the plan retired by PR #372; #546 and #563 have their fixes merged.
- The self-review's "Evidence spot-checks — 5 claims PASS" all hold (re-confirmed #372, #297, #414/#420/#424), but its structural PASS on the UNDECIDED row asserted a check it did not perform.

## Bottom line: NO-GO for consolidation (as submitted; addressed in the revision round, see consolidation doc)

Lane column: no error found in 15 rows checked. Status column: 22 of 27 non-companion rows examined were wrong (or right for a false reason), all from one root cause. Required fixes:

1. **Restate the methodology.** The three-part test is a rule-out for `never-started` only. Add explicit rule-in criteria: `done` = the doc's own proposal shipped (code/PR cites the doc, or all plan tasks merged, or research consumed by the next stage) with no open tracker; `active` = an open `Plan:`/task issue, `open-decisions.md` line, or issue body cites the doc or its still-open work; `superseded` only when a later, different decision retired the doc's own proposal and that later artifact says so (the three survivors above are the pattern). A docs-only PR merge never counts as "shipped"; "code shipped" is verified by reading the code, not a merge subject line.
2. **Re-derive every one of the 146 `superseded` rows** under fix 1. Sample rate says ~85% move to `done`/`active`.
3. **Correct the rows with false evidence** (status → and Reason): backend-services-modularization → done; economic-strategy-remediation → active; claudesuperpower-plugin-pilot → active (wrong plugins named); autonomous-engineering-mode → active (PR #42 is the spec's own PR); whale-confidence-scoring-remediation → active; realtime-data-plane-remediation-design → active; scoring-pool-candidate-retry-isolation → active; seen-trade-ids → active; issue-530 sweep → active; kanban-board-sync-design → done; session-tooling-friction-log → done; followups-from-3-plan → never-started; test-coverage-audit-handoff → stalled with a truthful Reason; rest-vs-ws-inventory → done or stalled with the audit-§6 citation; frontend-modularization-design → active; toolkit-assessment → done; trade-performance-analysis → done.
4. **Recompute all 87 companion rows** after their parents change.
5. **Amend the self-review** to record that F1's `ls services/` confirmation was not actually performed.
6. **Raise with the coordinator, not fix in the table:** the definitions make a dormant-but-tracked item `active` (#89, #81). Either accept that explicitly or add a movement criterion — a table-level decision the drafter should not make alone.
