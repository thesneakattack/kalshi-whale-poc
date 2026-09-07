# CI cost analysis — pipeline dataset + local-vs-CI execution model

Supporting data for `2026-09-02-ci-pipeline-audit.md`. Two sub-reports,
concatenated verbatim from the sessions that produced them (a top-level
coordinating session reading the Woodpecker REST API directly, and a
"Local-vs-CI test execution model" subagent), each already self-citing its
method and unverified assumptions. Nothing here has been altered except
this header and the removal of a duplicate title line.

---

## Part 1 — Live pipeline dataset (main session, Woodpecker REST API)


Source: Woodpecker REST API at $WOODPECKER_SERVER (v3.18.0), repo id 1.
`GET /api/repos/1/pipelines?page=1..4&perPage=50` → 200 most recent pipelines
(#133–#355, created 2026-08-31 → 2026-09-02, 2.03 days), then
`GET /api/repos/1/pipelines/{n}` for each (workflows + steps with
started/finished epoch seconds), then `GET /api/repos/1/logs/{n}/{step_id}`
(base64 lines) for 60 push-event pytest steps. Raw files: scratchpad
`pipelines_all.json`, `pl/*.json`, `workflow_rows.json`, `logs/pytest_*.txt`.

## Volume

| | count |
|---|---|
| pipelines | 200 (push 131, pull_request 69) |
| workflows | 1021 (5 per pipeline in 179, 6 when `frontend/**` changed in 21) |
| statuses | push: 113 success / 5 failure / 12 killed / 1 canceled; PR: 63 success / 4 failure / 2 killed |
| total workflow-minutes | 1037 (2026-09-01 alone: 127 pipelines, 686 min) |
| PRs merged per day (gh, last 8 days) | 31, 32, 14, 9, 35, 22, 21, 15 |

## Per-workflow wall time, successful runs, median (p90)

| workflow | push→feature branch | pull_request→main | push→main |
|---|---|---|---|
| tests-pytest | 202s (232) n=82 | 18s (201) n=63 | 161s (178) n=34 |
| quality-browser-e2e | 81s (97) | 34s (95) | 75s (88) |
| kalshi-contract-fixtures | 50s (60) | 51s (63) | 47s (53) |
| quality-architecture-audit | 38s (43) | 37s (43) | 37s (40) |
| tests-dependency-audit | 33s (40) | 36s (41) | 33s (38) |
| quality-frontend-build | 13s (16) n=8 | 12s (16) n=8 | 12s (14) n=4 |

Pipeline wall (created → last workflow finished), successes+failures:
push→feature median 248s (p90 287s, max 467s); PR median 152s (p90 336s,
max 512s); push→main median 201s (p90 226s).

## Fast-path (SKIP) share and what SKIP still pays

Verdict inferred from the pytest step's duration (≤5s = the `echo ... skipping`
branch; confirmed by decoded logs).

| event | RUN | SKIP | RUN workflow-min | SKIP workflow-min | median wall RUN / SKIP |
|---|---|---|---|---|---|
| push→feature | 66 | 25 | 465 | 64 | 262s / 55s |
| pull_request | 31 | 32 | 217 | 82 | 291s / 83s |
| push→main | 21 | 14 | 135 | 35 | 212s / 52s |

A SKIP pipeline still runs, at median: kalshi-contract-fixtures 47s,
quality-architecture-audit 37s, tests-dependency-audit 32s,
quality-browser-e2e 22s (selenium service boots, npm/pytest skipped),
tests-pytest 8s (clone + verdict). ≈2.4 workflow-min per docs-only pipeline;
71 SKIP pipelines = 181 workflow-min = 17% of all compute in the window.

## testmon tier has never selected anything (deterministic)

27 of 27 decoded push-event pytest logs that mention testmon print
`testmon: selection automatically deactivated because -m was used, environment: default`
and then `3053–3058 passed, 16–17 skipped ... in 143–190s`. Zero logs show a
selection. `scripts/ci-testmon-run.sh` runs `pytest --testmon -n 4 -m "not slow"`.
Git: testmon landed in 6ec23c0 (2026-08-26); `-m "not slow"` was added the
same day in 9ae0452. The per-branch `.testmondata` cache volume
(`wp-testmon-cache`) is therefore written and copied on every push for no
effect.

## Push + PR twin pipelines for the same commit

67 of the 69 PR pipelines' commits also had a push pipeline (the other two
PR pipelines were killed before comparison); PR created a median 9s after the
push. Every PR-event workflow therefore queued behind its push twin:
PR workflow queue wait median 58s, p90 117s, max 300s, versus push workflow
queue wait median 7s. Mechanism: 2 pipelines × 5 workflows = 10 workflow
slots requested against `WOODPECKER_MAX_WORKFLOWS=4`; branch protection reads
only the `ci/woodpecker/pr/*` contexts, so the push twin's statuses gate
nothing once a PR exists.

## Post-merge main push re-tests an already-tested tree

36 push→main pipelines. For 30 of them the merge commit's tree equals the
PR head's tree (`git rev-parse <merge>^{tree}` == `<merge>^2^{tree}`), i.e.
main had not moved, so the PR event's `refs/pull/N/merge` run had already
tested byte-identical content. 136 workflow-min (13% of the window) spent
re-running the full battery on identical trees. (Over the last 120 merges
on main the split is 60 identical / 60 different, so the share varies with
how many PRs land concurrently.)

## Duplicated and fixed costs per pipeline

- kalshi-contract-fixtures: 271 tests, run serially (no `-n`), 22–37s of
  pytest inside a 36–53s step; every one of those tests also runs inside
  tests-pytest. 188 runs ≈ 161 workflow-min ≈ 15.5% of the window. Header
  says the duplication is deliberate for failure attribution.
- tests-dependency-audit: `pip install pip-audit` + `pip-audit` 27–33s per
  run, 190 runs ≈ 112 workflow-min ≈ 11%. Its inputs (requirements*.txt)
  changed in 9 of 1,095 commits in the last 14 days (0.8%).
- quality-frontend-build: `frontend/**` changed in 19 of 1,095 commits;
  correctly path-filtered already.
- clone step: 1021 × median 4s = 101 workflow-min ≈ 10%. Every workflow
  clones independently (5–6 clones per pipeline); tests-pytest and
  quality-browser-e2e clone full history (`depth: 0`) for the fast-path check.
- `apt-get update && apt-get install git` inside the pytest step on every
  RUN: measured 11s in a fresh python:3.13-slim container (docker run,
  includes container start) — 137 RUN pipelines × ~10s ≈ 23 min.
- `uv pip install` after the shared cache: "Installed 64 packages in
  1.3–1.8s" — effectively free (the earlier 50s download problem is solved).
- Superseded (killed/canceled) workflows: 30 workflow-min of partial work
  discarded; `cancel_previous_pipeline_events` is working as designed.

## Failures in the window (signal exists)

9 failed pipelines: quality-architecture-audit ×3 (138/139, 263, 264),
tests-pytest ×4 (158, 235, 264, 266), quality-browser-e2e ×2 (282/283).
Note 138/139, 282/283 are push+PR twins failing identically — the twin
found nothing the other did not.

## Local baseline

Full suite inside the ddev fastapi container on this host:
`pytest -n 4 -m "not slow" -p no:testmon --durations=80` →
3058 passed, 16 skipped in 128s (wall 2:17 incl. ddev exec).

---

## Part 2 — Local-vs-CI test execution model (subagent report)


(Report authored by the "Local-vs-CI test execution model" subagent; saved verbatim by the coordinating session because the subagent harness refused report-file writes. HTML entities from the transport unescaped; nothing else changed.)

Date: 2026-09-02. Repo `/home/davidf/code/portfolio/showcase-projects/autotrade`, worktree `.claude/worktrees/ci-pipeline-audit` (branch `docs/ci-pipeline-audit`, HEAD `f12bb46` = `origin/main`; cited files were sha256-identical between worktree and primary). Read-only: nothing edited, committed, pushed, or triggered; pytest not run by this audit (per-file durations come from the sibling test-profiling agent's files in the shared scratchpad, cited as such).

## Summary

1. Outside a push, the only automated test run is `.claude/hooks/run_tests.py`: name-mapped `tests/` subset, serial, 55 s budget, inside the ddev `fastapi` container on this host, on every Edit/Write. Skills/rules/superpowers only *say* "run targeted tests locally; push for the full suite". No skill, rule, or hook ever invokes `scripts/woodpecker-trigger`.
2. The Woodpecker manual pipeline has **never run** in retained history: `?event=manual` → `[]` (filter proven working); history restarts at pipeline #1 on 2026-08-31 04:02 UTC (server table evidently reset), so the 2026-08-28 enabling commit `642f3e6` has no recorded exercise. `woodpecker-trigger` takes only a branch; a manual run is always the full suite.
3. The manual path cannot replace the hook: it tests a **pushed branch tip**, not the working tree, and the push already runs all six workflows; CPU stays on this same host (agent + ddev side by side). It can only *add* a run.
4. Hook mapping defects are the real local problem: `services/kalshi/websocket.py` and `public.py` (26 commits/7d) map to 18 files / 387 tests / 55.5 s serial — over budget before start-up, so every such edit hits the exit-2 "let CI own it" path; `services/app_state.py` (18 commits, 2nd most edited) maps to **no test**; `main.py → test_routes*` matches nothing.
5. `docs/woodpecker-ci.md` stale: L80-88 manual→filtered (stale for tests-pytest), L168-170 trusted "all true" (live `network:false`), L49 "full suite" (three tiers since 08-26), L17 "dispatch-only" (3 of 5 are cron), pipeline numbers 231/240/241 no longer resolve.
6. GitHub Actions: `tests.yml` dispatched 3×, `quality.yml` 2× since going manual-only (2026-08-24); `tests.yml` runs `pytest -v` serial without `-m "not slow"`; `quality.yml` browser-e2e failed its last dispatch (2026-08-31 04:12 UTC, `No module named 'selenium'`), fixed 17 min later (`59b2eea`), never re-dispatched. Dead weight as duplicates — their only unique property is running off-host. Three weekly crons could move to Woodpecker cron (capability owned by another agent; my `GET /api/repos/1/cron` → 401).
7. CI wall: top-5 branches by commits (161 commits ≈ pushes) paid ≈665 min @248 s (≈553 min @206 s); only 6 of those 161 were on the SKIP fast path, though 40 were docs-only commits (cumulative-diff rule). Woodpecker's retained 2.6-day window: 205 push pipelines, `main` alone 57.

## 1. Inventory — every place a session or human is told/automated to run tests locally

| Where | Trigger | What exactly runs | Scope | Executor / location |
|---|---|---|---|---|
| `.claude/hooks/run_tests.py`, wired in `.claude/settings.json:75-80` (`PostToolUse`, matcher `Edit\|Write`, `timeout: 60`) | Every Edit/Write of an in-scope file: `main.py`; `.py`/`.sh` under `services/`, `.claude/hooks/`, `tools/`, `scripts/`; extension-less `scripts/*`; `tests/test_*.py` (`_in_scope`, L25-35) | `cd <checkout> && python3 -m pytest -q -p no:testmon -m "not slow" tests/test_a.py ...` (L91-93) via `ddev exec -s fastapi sh -c`, launched from the primary root (`cwd=str(primary)`, L95) with a `cd` to the worktree path in-container (docstring L10-12). Serial, no `-n`. | `test_<stem>*.py` plus `test_<package>*.py` when the file is one level inside `services/<pkg>/` or `tools/<pkg>/`; `main.py` → `test_main*` + `test_routes*` (`tests_for`, L38-50) | ddev `fastapi` container, this host. `BUDGET_SEC = 55` (L19); timeout → exit 2 "exceeded 55s budget ... let CI own it" (L96-101); failure → exit 2 with pytest output (L105-107); no mapping → exit 0 + `additionalContext` "write one" (L83-86). `tests/test_hooks_wiring.py:34-47` asserts harness timeout (60) > budget (55). |
| `.claude/hooks/guard_workflow.py` | Pre/PostToolUse | **No rule about pytest, full suite, or CI.** Active: `GIT_ADD_ALL_BLOCKED`, `DDEV_EXEC_WRONG_WORKTREE`, `KALSHI_DOCS_REQUIRED` (L5-8, L32-34); R2/R4/R6 disabled/retired (L10-31, L36-40). Only CI-adjacent behaviour: `checkpoint_nudge` (L359-377) — every ≥600 s, if ≥5 files or ≥150 lines uncommitted, appends "run /checkpoint (commit verified units, push, confirm CI)". | — | Memory `effort-caps-are-kneecapping.md`: "never add ... a no-local-full-suite guard". |
| `.claude/skills/checkpoint/SKILL.md` | checkpoint / "offload testing to CI" (L3) | Step 2 (L14-16): "The per-edit hook already ran each edited module's tests. Run whatever else the change warrants — the full suite locally is fine when the change is broad or risky; CI runs it regardless." Step 5 (L26-28): push triggers Woodpecker; GH workflows dispatch-only. Step 6 (L30-51): confirm via `gh api .../commits/<sha>/status`; logs via `scripts/woodpecker-status --pipeline N --log STEP`. | Session judgement | "Offload to CI" = **push**. `grep -n -i 'manual\|woodpecker-trigger'` over the skill, `CLAUDE.md`, `.claude/rules/branching-and-ci.md` matched only the unrelated `CLAUDE.md:93`. |
| `.claude/skills/run/SKILL.md` | run the app / browser check | No pytest instruction (`grep -n -i 'pytest\|test'` → L3, L50, L62, L66, all live-app/browser) | — | ddev app |
| `.claude/rules/branching-and-ci.md` | policy | Lifecycle "implementation → **targeted local checks** → commit → push → Woodpecker"; "A push triggers every `.woodpecker/*.yml` on any branch"; no manual-pipeline mention | targeted | ddev / Woodpecker |
| `CLAUDE.md:116` | policy | "CI (Woodpecker) is the only full-suite owner. Locally run only the targeted test files; the per-edit hook already does this." | targeted | same |
| `docs/woodpecker-ci.md:80-88` | reference | manual trigger → `pipeline-filtered: true`, nothing runs — **stale for tests-pytest since 2026-08-28** (§3) | — | — |
| superpowers `test-driven-development/SKILL.md` (`~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0/skills/`) | any feature/bugfix | Verify RED "**MANDATORY. Never skip.** `npm test path/to/test.test.ts`" (L113-126); Verify GREEN "**MANDATORY** ... Other tests still pass" (L168-180) | one file explicitly; "other tests" unscoped | wherever the session runs pytest — here `ddev exec -s fastapi ...` |
| superpowers `verification-before-completion/SKILL.md` | before any "done" | "NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE ... Execute the FULL command" (L17-33); "Tests pass — requires: Test command output: 0 failures" (L42) | whatever backs the claim | same |
| superpowers `executing-plans/SKILL.md` | executing a plan | "Run verifications as specified" (L30); hands off to `finishing-a-development-branch` to "verify tests" (L35-38); "Don't skip verifications" (L61) | per plan | same |
| CI contrast: `scripts/ci-testmon-run.sh` from `.woodpecker/tests-pytest.yml:203` | Woodpecker pytest step | PR / main push / **manual** → `exec python -m pytest -n 4 -m "not slow"` (L25-37); other pushes → `--testmon` variant (L53), a confirmed no-op | full | Woodpecker step container, this host |

Local full-suite baseline (sibling `ci_dataset_findings.md` / `junit_run.log`): `pytest -n 4 -m "not slow" -p no:testmon` in the `fastapi` container → 3058 passed, 16 skipped in 127.6 s. Static: 2,996 `def test_` across 168 `tests/test_*.py` (helper CMD 5).

## 2. Hook mapping accuracy on the 30 most-edited files (7 days)

Method: `git log --since='7 days ago' --name-only --format= -- services main.py tools | sort | uniq -c | sort -rn | head -30`; `run_tests._in_scope()`/`tests_for()` imported from the hook (`mapping.json`); tests counted by `^\s*(async )?def test_`; serial seconds = sum of per-file junit times from the sibling's `pytest_per_file.csv` (`budget.json`). `*` = integration-style file (TestClient/httpx/subprocess/sqlite/sleep).

| commits | file | scope | selected | tests | serial s | note |
|---|---|---|---|---|---|---|
| 32 | `main.py` | yes | `test_main_scheduler_loops.py`*, `test_main_tick_executor_wiring.py` | 36 | 2.0 | tight; `test_routes*` special case (L44) matches nothing |
| 18 | `services/app_state.py` | yes | **none** | 0 | 0 | exits 0 with "no tests cover" context |
| 17 | `services/kalshi/websocket.py` | yes | 18 files: every `test_kalshi*.py` | 387 | **55.5** | over budget; `test_kalshi_census.py` = 36.6 s (one test 18.7 s); docs_drift/docs_sync/canary unrelated |
| 17 | `services/diagnostics/routes.py` | yes | `test_diagnostics.py`*, `test_diagnostics_routes.py`* | 26 | 9.1 | ok |
| 15 | `tools/kanban_sync/github_client.py` | yes | 13 `test_kanban_sync*.py` | 231 | 0.4 | over-broad, cheap |
| 14 | `services/quality/routes.py` | yes | 16 `test_quality*.py` (incl. coordination, ratchet, audit) | 183 | 11.1 | prefix collision |
| 13 | `services/diagnostics/diagnostics.py` | yes | 2 diagnostics | 26 | 9.1 | ok |
| 12 | `tools/quality_coordination.py` | yes | 11 `test_quality_coordination*` | 72 | 1.6 | ok |
| 11 | `tools/quality_audit/baseline.json` | no | — | — | — | silent |
| 11 | `tools/kanban_sync/sync.py` | yes | 13 kanban | 231 | 0.4 | cheap |
| 11 | `tools/kanban_sync/__main__.py` | yes | 13 kanban | 231 | 0.4 | cheap |
| 11 | `services/whale_stream/whale_stream_handlers.py` | yes | 2 (`decision_bridge`, `stage_timing`*) | 14 | 3.2 | ok |
| 11 | `services/quality_coordination.py` | yes | 11 | 72 | 1.6 | ok |
| 11 | `services/market_watch/CHEATSHEET.md` | no | — | — | — | silent |
| 10 | `services/signal_log.py` | yes | `test_signal_log.py`* | 51 | 17.9 | ok |
| 10 | `services/observability/observability.py` | yes | `test_observability.py`* | 68 | 5.8 | ok |
| 10 | `services/market_watch/milestone_live_data.py` | yes | 3 | 37 | 0.5 | ok |
| 10 | `services/market_watch/catalog_scan.py` | yes | 3 | 19 | 2.9 | ok |
| 9 | `services/series_watcher.py` | yes | 1* | 32 | 12.0 | ok |
| 9 | `services/observability/README.md` | no | — | — | — | silent |
| 9 | `services/kalshi/public.py` | yes | same 18 kalshi | 387 | **55.5** | over budget |
| 9 | `services/exits/README.md` | no | — | — | — | silent |
| 8 | `tools/soak_analyzer.py` | yes | 1 | 46 | 0.1 | ok |
| 8 | `services/whale_calibration/confidence_calibration.py` | yes | 2 | 37 | 0.0 | ok |
| 8 | `services/market_watch/live_status.py` | yes | 2 | 4 | 0.1 | thin |
| 8 | `services/diagnostics/_aio_db.py` | yes | 2 | 26 | 9.1 | ok |
| 7 | `tools/kanban_sync/sources_plan.py` | yes | 13 | 231 | 0.4 | cheap |
| 7 | `services/whalewatchers/kalshi_trade_tape.py` | yes | 2 | 66 | 18.9 | ok |
| 7 | `services/market_history.py` | yes | 1 | 29 | 3.7 | ok |
| 7 | `services/kalshi/CHEATSHEET.md` | no | — | — | — | silent |

Findings: 1 of 25 in-scope files selects nothing (`app_state.py`, 18 commits); 5 non-`.py` files are correctly out of scope. 2 of 25 (`kalshi/websocket.py`, `kalshi/public.py`) select 55.5 s serial — over the 55 s budget before `ddev exec`/interpreter/conftest/collection overhead (unmeasured for single-file runs; estimate "a few seconds") — so every edit there ends in exit-2 "let CI own it" and is untested locally. Those two are 26 of 293 commit-touches across the in-scope files (8.9%), `app_state.py` another 18 (6.1%); commit-touches under-count Edit events. Nothing else is near the budget (next: 18.9 s, 17.9 s, 12.0 s; none in 40–55 s). Over-selection is the `test_<package>*` rule (L45-49) colliding on prefixes (`kalshi`, `quality`, `kanban_sync`); harmful only for kalshi.

## 3. Manual pipeline path today

**Scripts.** `scripts/woodpecker-trigger:23-31`: `BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"` → `docker run ... woodpeckerci/woodpecker-cli:v3 pipeline create thesneakattack/kalshi-whale-poc --branch "$BRANCH"`. Branch is the only input; requires `WOODPECKER_TOKEN` (L18-21). `scripts/woodpecker-status:61-107`: health via curl container, then `pipeline ls|ps N|log show N STEP`. Both default to `http://woodpecker-server:8000` on `traefik_proxy` (trigger L14, status L57) but honour `$WOODPECKER_SERVER`; the user's shell exports `https://ci.webfoundry.dev`, which works from the host (`/version` → `3.18.0`). CLI image present locally; the read-only `pipeline create --help` probe was denied by the auto-mode permission classifier (helper CMD 4).

**Pipeline side.** `tests-pytest.yml:56-57` `event: [push, pull_request, manual]` since `642f3e6` (2026-08-28 15:17 UTC; +14/−1 in the yml, +4/−4 in `ci-testmon-run.sh`). The other five accept `[push, pull_request]` only (`quality-architecture-audit.yml:34`, `quality-browser-e2e.yml:21`, `kalshi-contract-fixtures.yml:22`, `quality-frontend-build.yml:22`+path, `tests-dependency-audit.yml:7`). Manual → `ci-skip-heavy-suite.sh` returns RUN (L62-67) and `ci-testmon-run.sh` runs the full suite.

**Live API.** `GET /api/repos/1`: `visibility: public`, `trusted: {network:false, volumes:true, security:true}`, `cancel_previous_pipeline_events: [push, pull_request, tag]`, `timeout: 60`. Anonymous `GET .../pipelines?perPage=1` → 200 (no token needed for reads). `GET .../cron` (token) → 401. `?event=manual` → `[]`; filter sanity `?event=pull_request_closed` → `[(3,..),(1,..)]`, `?event=push&perPage=1` → `[(355,'push')]`. Full dump: **314 pipelines, numbers 1–355 (41 gaps), push 205 / pull_request 107 / pull_request_closed 2, zero manual; `created` 2026-08-31 04:02 UTC → 2026-09-02 17:36 UTC (119/127/68 per day); pipeline #1 dated 2026-08-31 04:02 UTC.** Doc-cited `#231` now = push on `fix/write-path-capacity-fix` 2026-09-01, `#240` = PR on `main` 2026-09-01, `#241` absent. Hypothesis (unverified): server DB/repo registration recreated ~04:02 UTC 2026-08-31 — the GH fallbacks were all dispatched 04:11–04:12 UTC that morning. **Manual pipelines ever run: 0 in retained history**; one pre-reset attempt recorded in the doc (2026-08-24) was filtered and persisted nothing; 2026-08-28→08-31 unknown.

**`docs/woodpecker-ci.md` claims vs reality:**

| Lines | Claim | Status |
|---|---|---|
| 80-88 | manual → `pipeline-filtered: true`, nothing runs | **Stale** for tests-pytest since `642f3e6`; true for the other five |
| 68-73 | "push/PR/manual-triggered only" | Contradicts 80-88; today true only for tests-pytest |
| 168-170 (+ `tests-pytest.yml:116-117`) | trusted network/volumes/security all true | **Contradicted**: `network: false` |
| 49 | tests-pytest "no — full suite" | Outdated: three tiers since 2026-08-26 (`tests-pytest.yml:10-55`), SKIP/testmon never mentioned |
| 17 | `.github/workflows/` = dispatch-only | 3 of 5 are cron+dispatch (doc says so itself at 56-73) |
| 100-105 | tests.yml/quality.yml working fallbacks | Drifted (§5) |
| 210-239 | pipelines 8/15/23/26/231/240/241 | Numbers no longer resolve (history reset) |
| 22-38 | container-to-container "is what works" | Outdated emphasis: host reaches `https://ci.webfoundry.dev` directly (Traefik route, compose L30-33; `ports:` still commented L6-7) |
| 122-127, 153-154, 185-209, 240-247, 262-264, 129-149 | no `secrets.*`; five cache mounts; MAX_WORKFLOWS 4 / GRPC secret unset; branch protection; cancel-previous; `gh api` status shape | All still true (grep rc=1; 5 mounts across 4 files; compose L85; protection API; live repo flags; main HEAD shows five `ci/woodpecker/push/*` success) |

## 4. What a targeted manual run would need — and where it would not help

Repo side: (1) `ci-testmon-run.sh` manual branch reads `TEST_PATHS`; non-empty → `exec python -m pytest -n 4 -m "not slow" $TEST_PATHS`, else today's full run (keeps the 2026-08-28 "bare manual = full confidence" intent). Must be read inside the script file — Woodpecker blanks unknown `${VAR}` in inline YAML `commands:` (script header L3-18). (2) `ci-skip-heavy-suite.sh` unchanged. (3) `woodpecker-trigger --tests <paths>` passing through the CLI's per-pipeline variable mechanism and printing the pipeline number. (4) optional `woodpecker-status --wait N`. **To be confirmed by the Woodpecker docs research, not asserted:** how `variables` reach step env; the CLI's variable flag; commit vs branch-tip targeting; the manual event's GitHub status context (by analogy `ci/woodpecker/manual/tests-pytest` — unverified, none ever ran).

Waiting/result: today `gh pr checks <PR> --watch --fail-fast` in background Bash (memory 2026-09-02) or `gh api .../status` (checkpoint step 6); for a manual run, `gh pr checks` helps only if a status is posted on the PR head (unverified), else poll `GET /api/repos/1/pipelines/<n>` (anonymous OK). Expected wall for e.g. `tests/test_paper_broker.py` (66 tests, 28.45 s serial): clone ~4 s + uv install 1.3–1.8 s warm + `apt-get git` ~11 s + queue median 7 s (sibling measurements) ≈ 25–40 s fixed + ~8–10 s tests at `-n 4` (estimate) → **~40–50 s**, vs ~30 s serially in the hook on the same host. Full push pipeline: tests-pytest workflow median 202 s; pipeline 248 s created→finished (sibling) / 206 s started→finished (mine, n=201).

Where it does **not** help: a manual pipeline runs a **pushed tip**, the hook/TDD loop test the **working tree** — you must commit+push first, and the push already runs all six workflows, so a targeted run is always additive. CPU stays on this host (`docker ps`: woodpecker-server/agent beside the four ddev containers; compose L56-85 caps concurrency precisely because "the same host runs that project's live ddev stack"). It does not fix the §2 mapping defects. The only genuine CI gain — off-host execution — is something Woodpecker here cannot offer; the GitHub-hosted `tests.yml` can.

## 5. GitHub Actions side (`gh run list --limit 500`, complete counts)

| Workflow | Triggers | Runs | Push-era | Dispatches | Schedules | Last | Pass/fail |
|---|---|---|---|---|---|---|---|
| `tests.yml` | dispatch only (L12-13; dispatch added `2fec9ae` 2026-08-16, push removed `402b6b1` 2026-08-24) | 278 | 275 (2026-08-07→08-24, 118 failures) | 3: 08-16, 08-24, 08-31 04:11Z, all success | 0 | 2026-08-31 | 3/3 dispatches |
| `quality.yml` | dispatch only (L10-11) | 9 | 7 (2026-08-24) | 2: 08-24 success; 08-31 04:12Z **failure** (browser-e2e `No module named 'selenium'`, 4 errors in 0.17 s; other 2 jobs passed) | 0 | 2026-08-31 | 1/2 |
| `docs-drift-check.yml` | cron `0 6 * * 1` + dispatch | 5 | 0 | 2 (08-16 ok, 08-24 fail) | 3 (08-17, 08-24, 08-31 ok) | 2026-08-31 | 4/5 |
| `kalshi-contract.yml` | cron `0 7 * * 1` + dispatch | 3 | 0 | 2 ok | 1 ok | 2026-08-31 | 3/3 |
| `performance.yml` | cron `0 8 * * 1` + dispatch | 2 | 0 | 1 ok | 1 ok | 2026-08-31 | 2/2 |

Drift: `tests.yml:39` `python -m pytest -v` — serial, no `-n`, no `-m "not slow"` (runs the two slow scans Woodpecker excludes), no `-p no:testmon`; nothing asserts equivalence. `quality.yml` broke when `requirements-selenium.txt` split out (2026-08-25, `requirements-dev.txt:23-28`); caught only by the 2026-08-31 04:12 UTC dispatch; fix `59b2eea` at 2026-08-30 23:29:32 −0500 = **04:29:32 UTC, 17 min after the failure**, on `origin/main`, never re-dispatched → current state unverified. Verdict: as duplicates, dead weight (5 dispatches in 9 days, one finding the fallback itself broken); unique value is off-host execution. Options: (a) delete `quality.yml`, keep `tests.yml` as the off-host fallback with a wiring test pinning its pytest line to `ci-testmon-run.sh`'s; (b) delete both; (c) keep both + equivalence tests. Human call on whether an off-host executor is wanted. The three crons could move to Woodpecker cron (doc L68-73 says they stayed only because cron wasn't set up); they'd run on this host weekly, two need Kalshi egress (`trusted.network:false` interaction unverified); cron capability is another agent's item.

## 6. Session wall-time cost of CI waits (7 days)

Wait mechanism: checkpoint step 6 (`gh api` status reads) → superseded by memory `ci-wait-use-background-not-monitor.md`: `gh pr checks <PR> --watch --fail-fast` in background Bash, one notification. Verification is gated on completion either way.

Approximation: **one push per non-merge commit** (real pushes fewer — commits batch). Method (`branch_commits.json`): `origin/main` first-parent merges since 7 days (157 merges, 147 branches from PR-merge subjects), commits `merge-base(^1,^2)..^2`, plus unmerged `origin/*`; 578 commits. Docs-only per `ci-skip-heavy-suite.sh` `SAFE_PATTERN` (L56); SKIP requires the *cumulative* branch diff to be docs-only (L46-51).

| branch | commits≈pushes | per-commit docs-only | on SKIP path | @248 s | @206 s |
|---|---|---|---|---|---|
| `feat/realtime-data-plane-remediation` | 71 | 30 | 6 | 293 min | 244 min |
| `feat/kalshi-category-data-completeness` | 27 | 0 | 0 | 112 | 93 |
| `fix/entry-gate-netting-remediation` | 22 | 4 | 0 | 91 | 76 |
| `feat/whale-confidence-scoring-remediation` | 21 | 4 | 0 | 87 | 72 |
| `feat/autonomous-quality-coordination` | 20 | 2 | 0 | 83 | 69 |
| **top-5** | **161** | 40 | 6 | **665 min (11.1 h)** | **553 min (9.2 h)** |
| all 147 branches | 578 | 215 | 135 | 2,389 min | 1,984 min |

248 s = sibling's created→last-workflow-finished median for feature pushes; 206 s = my `finished−started` median over 201 finished push pipelines (success-only 205; non-main success 232.5; PR 192). SKIP pipelines still cost ~55 s (sibling) → correcting the 6 SKIP commits moves the top-5 total ~19 min. Most docs-only commits were not on the fast path because their branch had already touched code. The two `worktree-agent-*` branches (17, 14 commits) were 100% SKIP.

Woodpecker cross-check (retained window 2026-08-31 04:02→09-02 17:36 UTC ≈ 2.6 d): 205 push pipelines (174 success, 19 killed, 8 failure, 2 canceled, 2 error); `main` 57 (7,490 s), `feat/realtime-data-plane-remediation` 33 (4,042 s, median 66 s — many SKIP/killed), `feat/whale-confidence-scoring-remediation` 14 (3,316 s), `fix/event-loop-blocking-elimination` 9 (1,540 s), `fix/write-path-capacity-fix` 8 (1,342 s) — 17,730 s ≈ 296 min pipeline wall. Structural multipliers the sibling quantified from the same data (untouched by any manual path): every PR pipeline has a push twin created ~9 s earlier (10 slots vs `WOODPECKER_MAX_WORKFLOWS=4`), and 30 of 36 post-merge `main` pushes re-tested a byte-identical tree.

## Assumptions / unverified

- Commit≈push (§6); true 08-26→08-31 push counts unrecoverable from Woodpecker.
- §2 serial sums use per-test junit times from a `-n 4` run (possible contention inflation); single-file start-up/`ddev exec` overhead unmeasured.
- §2 percentages count file-commits, not Edit events (lower bound on hook firings).
- History reset at 2026-08-31 04:02 UTC inferred from pipeline #1's `created` and numbering; cause not checked via `docker inspect`/volume history.
- `ci/woodpecker/manual/<workflow>` context name by analogy only.
- Woodpecker variables→env, CLI variable flag (help denied by classifier), commit targeting, cron 401 reason — left to the docs-research agent.
- `trusted.network:false` vs outbound network for canary/docs-drift not tested.
- Pipeline list payload omits `workflows`; per-workflow durations are the sibling's.
- `ci.webfoundry.localhost` 404 claim not re-tested; `tests/test_routes*.py` absence inferred from `tests_for()` output.

Artefacts (scratchpad): `mapping.json`, `budget.json`, `budget.py`, `pipe_check.py`, `pipelines_all.json`, `branch_commits.json`, `helper_outputs.txt`, `top30.txt`; sibling: `ci_dataset_findings.md`, `pytest_per_file.csv`, `junit_run.log`, `repo.json`.
