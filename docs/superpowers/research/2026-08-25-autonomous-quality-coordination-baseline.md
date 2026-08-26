# Autonomous Quality Coordination — I0 Baseline (re-ground, concurrency map, toolchain)

**Task:** I0 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Orchestrator:** `.claude/skills/autonomous-quality-coordination-investigation/SKILL.md`
**Re-grounded:** 2026-08-25, 18:42–18:55 local (`-05:00`); all times below are local unless suffixed `Z`.
**Investigation branch:** `chore/autonomous-quality-coordination-investigation`, created from
`origin/main` @ `8d1796b` in an isolated worktree (`.claude/worktrees/aqc-investigation`).

Every claim below is tagged with the evidence class from
`.claude/rules/autonomous-quality-coordination-evidence.md`:
**[E1]** current source/test/CI behavior · **[E2]** git/PR/branch history · **[E3]** deterministic
experiment · **[E4]** live runtime evidence · **[E5]** authoritative upstream docs · **[E6]** inference.
Anything the known-findings document asserted that this re-ground changed is called out explicitly.

---

## 1. Repository state at re-ground

| Item | Value | Evidence |
|---|---|---|
| Primary checkout branch / HEAD | `chore/realtime-dp-investigation` @ `84f9697` | `git branch --show-current`, `git rev-parse HEAD` [E2] |
| `origin/main` | `8d1796b` — "Merge pull request #11 from thesneakattack/docs/frontend-modularization-design" (18:42:21) | `git fetch --prune` + `git log -2 origin/main` [E2] |
| Local `main` | `a0c1569`, **behind `origin/main` by 2** (not checked out anywhere; harmless) | `git branch -vv` [E2] |
| Known-findings baseline | `a0c1569` — **superseded**: PR #11 merged at 23:42:22Z, after the known-findings doc was written | `gh pr view 11` [E2] |
| Realtime branch vs `origin/main` | 9 commits ahead (`51f6866`…`84f9697`, 16:29→18:32), 0 behind; 37 files, +4183/−73 | `git log origin/main..HEAD`, `git diff --stat origin/main...HEAD` [E2] |
| Worktrees before I0 | exactly one (the primary checkout) | `git worktree list` [E2] |
| Open PRs | **none** | `gh pr list --state open` [E2] |
| Remote branches | `main` (protected), `chore/realtime-dp-investigation` (active), `chore/realtime-data-plane-investigation` (**stale: merged via PR #10, never deleted**) | `gh api repos/…/branches`, `git branch -r --merged origin/main` [E2] |
| Stash | empty | `git stash list` [E2] |
| Untracked in primary checkout | 11 paths — see §2.3 for ownership | `git status --short` [E2] |

**Branch protection on `main`** (`gh api …/branches/main/protection`) [E1]:
`enforce_admins: true`, `allow_force_pushes: false`, `allow_deletions: false`,
`required_pull_request_reviews: null`, `strict: false`, required contexts exactly:
`ci/woodpecker/pr/tests-pytest`, `ci/woodpecker/pr/tests-dependency-audit`,
`ci/woodpecker/pr/quality-architecture-audit`, `ci/woodpecker/pr/quality-browser-e2e`,
`ci/woodpecker/pr/kalshi-contract-fixtures`. Matches `.claude/rules/branching-and-ci.md` verbatim.
`strict: false` means a PR does **not** have to be up to date with `main` to merge — relevant to
stale-detector-result threat modeling in I6 [E6].

---

## 2. Active-work / contention map

### 2.1 A parallel session is live in the primary checkout right now

This is not hypothetical concurrency. At 18:46 [E4]:

- `ps` shows `python3 …/8712c31b-…/scratchpad/i7_sampler.py` (PID 337620, elapsed 13m54s — started
  ≈18:32, i.e. right after that branch's last commit) and a shell loop waiting for
  `i7_samples.jsonl` to reach 161 lines. The scratchpad UUID (`8712c31b-…`) is a different Claude
  session from this one (`7c6a9033-…`).
- Untracked files being written by that session: `docs/superpowers/research/2026-08-25-realtime-live-baseline.md`
  (mtime 18:37:41) and `…/2026-08-25-rest-demand-study.md` (18:39:01). Per the realtime plan those are the
  **I7** and **I8** deliverables (`docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`
  lines 277–305) [E1].
- Its last two pushes (Woodpecker pipelines 131 `killed` → 132 `success`) were 18:30 and 18:32.

**Consequence for this initiative:** the primary checkout's index/HEAD/working tree is owned by that
session. This initiative did not `checkout`, `stash`, `add`, or delete anything there; it works from
a separate worktree (§6). The memory note `use-scratchpad-worktree-when-checkout-busy` already records
the same rule from an earlier collision on `services/kalshi/websocket.py` [E2].

### 2.2 Paths owned by the realtime branch (do not touch from this initiative)

Changed on `chore/realtime-dp-investigation` vs `origin/main` (`git diff --name-only origin/main...HEAD`) [E2]:

- **QCP-adjacent, directly contested:** `tools/quality_audit/baseline.json` (+7/−7: `api-usage:account.flatten_all`
  → `api-usage:account.create_order`, `+backend-route-unused:GET:/api/diagnostics/trade-capture`, two
  `notes` addenda), `tests/conftest.py` (+26, two new autouse fixtures).
- **Observability/diagnostics surfaces this investigation may later want to read from:**
  `services/observability/observability.py` (+156), `services/observability/CHEATSHEET.md`,
  `services/diagnostics/routes.py`, `services/diagnostics/trade_capture_reconciliation.py`,
  `services/fault_log.py`, `services/latency_agg.py`, `services/whale_pipeline_perf.py`.
- **Hot-path/realtime code (out of this initiative's scope anyway):** `services/kalshi/websocket.py`,
  `services/kalshi/public.py`, `services/http_client.py`, `services/whale_stream/…`,
  `services/whalewatchers/kalshi_trade_tape.py`, `services/market_watch/*`, `services/account_positions.py`,
  `services/market_events/event_schedule.py`, `main.py` (+2/−1).
- **Docs/tools:** `docs/kalshi/CHEATSHEET.md`, `services/kalshi/CHEATSHEET.md`,
  `docs/superpowers/research/2026-08-25-realtime-data-plane-baseline.md`,
  `docs/superpowers/research/2026-08-25-realtime-replay-baseline.md`, `tools/realtime_pipeline_replay.py`,
  10 new `tests/test_*.py` files.

Paths the realtime plan will **still create** (I7–I14, from its plan's `**Create**` blocks) [E1]:
`docs/superpowers/research/2026-08-25-{realtime-live-baseline,rest-demand-study,realtime-solution-research,ws-solution-comparison,rest-solution-comparison,realtime-architecture-review,realtime-root-cause-report}.md`,
`docs/superpowers/specs/2026-08-25-realtime-data-plane-remediation-design.md`,
`docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`,
`tools/kalshi_rate_limit_probe.py`, `tests/test_kalshi_rate_limit_probe.py`. None collide with this
initiative's planned outputs (all named `…autonomous-quality-…`, `…quality-coordination-…`,
`…quality-control-plane-topologies…`, etc.; prototype location `tools/quality_coordination_sim/`).

**Rule for later tasks:** if I1/I5/I7 need to touch `tools/quality_audit/baseline.json`,
`tests/conftest.py`, or `services/observability/**`, first check whether the realtime branch has merged
(`gh pr list`, `git branch -r --merged origin/main`) and rebase onto the merged result; never edit the
contested version. Reading them is fine.

### 2.3 Untracked-path ownership in the primary checkout (11 paths)

| Path | Owner | Disposition in I0 |
|---|---|---|
| `.claude/rules/autonomous-quality-coordination-evidence.md` | this initiative (bundle) | copied into worktree, committed |
| `.claude/skills/autonomous-quality-coordination-investigation/` | this initiative (bundle) | copied, committed |
| `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md` | this initiative (bundle) | copied, committed |
| `docs/superpowers/research/2026-08-25-autonomous-quality-coordination-known-findings.md` | this initiative (bundle) | copied, committed |
| `docs/superpowers/specs/2026-08-25-autonomous-quality-coordination-investigation-design.md` | this initiative (bundle) | copied, committed |
| `START_AUTONOMOUS_QUALITY_COORDINATION.md` | this initiative (bundle) | committed as `docs/superpowers/autonomous-quality-coordination-investigation-kickoff.md`, matching PR #10's `realtime-data-plane-investigation-kickoff.md` precedent (root stays uncluttered) |
| `INSTALL_AUTONOMOUS_QUALITY_COORDINATION.md`, `PACKAGE_MANIFEST.json` | bundle install artifacts | **not committed** (same treatment PR #10 gave `BUNDLE_README.md`); left untracked in the primary checkout for the user to delete |
| `BUNDLE_README.md` | **realtime** bundle's leftover (its file list is the realtime initiative's) | untouched; not this initiative's |
| `docs/superpowers/research/2026-08-25-realtime-live-baseline.md`, `…/2026-08-25-rest-demand-study.md` | realtime session, in flight (I7/I8) | untouched |

All seven bundle files verified byte-for-byte against `PACKAGE_MANIFEST.json` SHA-256s both in the primary
checkout and after copying into the worktree [E3]. The originals were deliberately **copied, not moved**,
so the other session's `git status` is unchanged (still 11 untracked, still on its branch — re-checked after
the copy).

**Known follow-up:** once this branch merges, the primary checkout will hold untracked files identical
to newly tracked ones. Git accepts that silently when contents match; if any diverge (e.g. the plan is
edited on this branch), the next `git pull` in the primary checkout will refuse with "untracked working
tree files would be overwritten" until the stale copies are removed. Record it in the merge step.

### 2.4 What the known-findings document got right, and what changed

| Known-findings claim (§2/§3) | Status at re-ground |
|---|---|
| Open PR #11 owns `.claude/rules/quality-capabilities.md` | **Changed:** merged 23:42Z. The realtime branch does not touch that file. No open PRs. → **uncontested**, so I0's optional router entry is added in its own commit (§7). |
| Realtime branch four commits ahead | **Changed:** nine ahead; still modifies `baseline.json`; still active (§2.1). |
| Package must not touch `baseline.json`, `.woodpecker/**`, `.github/workflows/**`, QCP code | **Still true**; I0 touched none of them. |
| Contention is real | **Confirmed with a live process, not just history.** |

---

## 3. Current detector / CI control plane (what actually runs today)

### 3.1 Static detector: `tools/quality_audit` [E1]

- Registry `tools/quality_audit/__main__.py::_SCANNERS` (9 scanners, in order): `scan_router_registration`,
  `scan_background_wiring`, `scan_persistence_isolation`, `scan_resource_lifecycle`, `scan_config_usage`,
  `scan_api_usage`, `scan_frontend_contract`, `scan_kalshi_contract_docs`, `scan_kalshi_boundary`.
- Contract: `services/quality/models.py::QualityFinding` (frozen dataclass: `finding_id, check, severity,
  confidence, source, scope, summary, evidence, remediation`) + `QualityReport`. Also produced at runtime by
  `services/observability/observability.py::runtime_findings` and
  `services/storage_health/storage_health.py::storage_findings` (source grep: 18 files reference the
  class — 3 tests, the model itself, a docstring in `tools/quality_audit/__init__.py`, and 13 code sites).
- Gate semantics (`compute_exit_code`): exit 1 **only** for a *new* `severity=error` + `confidence=high`
  finding not in `baseline.json`'s `accepted_finding_ids`; `--strict` adds new warnings. Warnings/info never
  gate. Baseline stores IDs only (`baseline.py` docstring: "so a finding whose evidence changes… doesn't
  spuriously look new").
- Output: stdout summary + optional `--json-out` (`findings`, `new`, `existing`, `resolved`). No persistence,
  no history, no GitHub interaction of any kind.
- Runtime side: `GET /api/quality/summary` (`services/quality/routes.py`) composes read-only local sources;
  its own `CHEATSHEET.md` states "No automatic remediation… strictly read-only reporting… No scheduled
  polling of this route exists anywhere in this app." There is **no persisted finding state anywhere**
  today — neither for CI findings nor runtime findings (observability persists *metrics*, not findings).

### 3.2 Identity shapes actually emitted (16 constructor sites, `grep -n finding_id= tools/quality_audit/*.py`) [E1]

| Scanner / check | `finding_id` template | Identity class (provisional, for I1 to test) |
|---|---|---|
| routers `router-registration` | `router-unmounted:{dotted_module}` | semantic |
| background `background-wiring` | `background-unwired:{module}:{function}` | semantic |
| persistence `persistence-isolation` | `persistence-unisolated:{module}` | semantic |
| resources `resource-lifecycle` | `resource-unclosed:{module}:{func}:{var}` | semantic (symbol-name sensitive) |
| config_usage `config-usage` | `config-unread:{leaf_path}` | semantic (config key) |
| api_usage `api-usage-inventory` | `api-usage:{receiver}.{method}` | aggregate/inventory (info) |
| frontend_contract | `frontend-route-unknown:{file}:{line}` | **location-sensitive** (info/low) |
| frontend_contract | `frontend-route-missing:{method}:{path}` | semantic |
| frontend_contract | `backend-route-unused:{method}:{path}` | semantic (info) |
| kalshi_contract_docs | `kalshi-contract-docs-missing:{module}:{op}` / `-stale:{module}:{key}` / `-missing-file:{module}:{key}:{doc}` | semantic |
| kalshi_boundary (4 rules) | `kalshi-boundary-{sdk-import,host,legacy-import,deprecated-read}:{file}:{line}` | **location-sensitive** (error/high) |

Live census at `origin/main` (§3.4): 190 findings, 190 distinct IDs; **5 carry a line number** (all
`frontend-route-unknown`, info/low, all baselined). The four `kalshi-boundary` rules are line-sensitive
*and* error/high, but emit zero findings on a clean tree — they only matter for identity if a violation
is ever intentionally baselined. This confirms the known-findings §4 hypothesis at the "some IDs are
line-sensitive" level; I1 must still mutation-test the semantic ones (rename/move/duplicate cases).

### 3.3 CI topology [E1]

**Woodpecker** (authoritative; `.woodpecker/*.yml`, server `https://ci.webfoundry.dev`, v3.17.0):

| Pipeline | `when` | Path filter | Secrets | Notes |
|---|---|---|---|---|
| `tests-pytest` | `event: [push, pull_request]` | none | none | `pytest -n 4` |
| `tests-dependency-audit` | same | none | none | `pip-audit` |
| `quality-architecture-audit` | same | none | none | `tools.quality_audit` + `tools.project_manifest --check` + `mypy`; `print-report` step cats `build/quality-audit.json` on success **and** failure |
| `quality-browser-e2e` | same | none | none | Selenium + Playwright, `services: selenium` |
| `quality-frontend-build` | same | `frontend/**` | none | **posts no status when skipped** (why it's excluded from required contexts) |
| `kalshi-contract-fixtures` | same | none | none | fixture-only, no network |

- `grep -rn secret .woodpecker/` → nothing; `docs/woodpecker-ci.md` line 122 states the same. **No
  cron/manual events** anywhere (`docs/woodpecker-ci.md` lines 69–88; manual trigger confirmed not to match
  any `when:`). Every pipeline already runs on PRs, so any privileged lane added later must be *new*
  and must not inherit `event: [push, pull_request]`.
- Status contexts posted: `ci/woodpecker/push/<pipeline>` on pushes, `ci/woodpecker/pr/<pipeline>` on PR
  events (observed on `84f9697`: five `push/*` successes; on `8d1796b` at 23:44Z: four successes +
  `tests-pytest` pending — see §8 for the final state).
- The Woodpecker pipeline list is **readable anonymously** (`scripts/woodpecker-status` health + list
  succeeded with no `WOODPECKER_TOKEN`; the repo is public) [E4]. Recent rows (18:50): 134 push main ✔,
  132 push realtime ✔, **131 push realtime `killed`**, 130 ✔, 129 ✔, 128 pull_request main ✔ (PR #11),
  127 push docs/frontend… ✔, 126 push realtime ✔, **125 push realtime `failure`** ("quality: sync
  api-usage baseline…"), **124 push realtime `failure`** ("obs: measure Kalshi websocket queue…"),
  123 ✔, 122 push main ✔ … Three `killed` rows (108, 110, 131) are superseded pushes, which **confirms
  "cancel previous pipelines" is effectively on** — `docs/woodpecker-ci.md` lists that as unconfirmed;
  this is the first direct evidence [E4]. Rows 124→125→126 are a real instance of a *transitional*
  branch failure fixed by the next push within 27 minutes — exactly the class I2 must quantify.
- Local `woodpecker-cli` is **not installed** (`which` empty); `scripts/woodpecker-status` runs
  `woodpeckerci/woodpecker-cli:v3` in Docker. I9's `woodpecker-cli lint` therefore means that image.

**GitHub Actions** (`.github/workflows/`): `tests.yml`, `quality.yml` → `workflow_dispatch` only;
`docs-drift-check.yml`, `kalshi-contract.yml`, `performance.yml` → weekly `schedule` + `workflow_dispatch`.
**No `permissions:` block and no `secrets.*` reference in any workflow** (grep). Actions secrets:
`total_count: 0`.

### 3.4 Detector result at `origin/main` (worktree, host Python 3.12) [E3]

```
$ python3 -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json --json-out …
quality-audit: 1 new, 189 existing (baselined), 1 resolved
  [resolved] api-usage:account.flatten_all
exit=0
```
By check: 26 `api-usage-inventory` (info/high), 115 `config-usage` (warning/medium), 5 + 44
`frontend-api-contract` (info/low, info/medium). Zero errors. The single **new** finding is
`api-usage:account.create_order`; the single **resolved** is `api-usage:account.flatten_all`. Both are
the info-level inventory drift that PR #9 (Phase C, `6de0474`) introduced and that the realtime branch's
commit `aa1eb37` already fixed in *its* `baseline.json` — unmerged. So integrated `main` right now carries
a transitional, non-gating finding whose fix is in flight on another branch. **This is a live specimen of
the exact escalation false-positive the investigation exists to avoid** (an automation reacting to
"new finding on main" would open duplicate work). Keep the JSON for I2/I5 replay:
`quality-audit` output is deterministic and re-runnable from `8d1796b`.

`python3 -m tools.project_manifest --check static/project-manifest.json --repo-root .` → "up to date
(no drift beyond 10%)", sub-threshold drift printed (`lines.html +0.4%`, `lines.python −0.1%`,
`tests.count −0.1%`). Candidate for I7's determinism proof.

Targeted QCP tests, run **inside ddev against the worktree path** (`ddev exec -s fastapi bash -c "cd
/app/.claude/worktrees/aqc-investigation && python -m pytest -q -p no:cacheprovider tests/test_quality_audit.py
tests/test_quality_models.py tests/test_quality_routes.py"`): **62 passed in 6.35s** [E3]. This also
establishes that ddev *can* see an in-repo worktree (contrary to the memory note's assumption) because
`.claude/worktrees/` sits under the bind-mounted project root — `run_tests.py`'s per-edit hook, however,
still targets `/app` (the primary checkout), so it will run the *other* session's tree if a
`services/*.py` edit ever happens here. This initiative edits no `services/*.py` before I8.

---

## 4. GitHub-side authority state (what write power exists today) [E1/E4]

| Surface | State |
|---|---|
| `gh` auth | user OAuth token for `thesneakattack`, scopes `gist, read:org, repo`; git over SSH. This is a **human credential**, not a bot; it can write issues/PRs/branches and is what every current `gh pr create/merge` uses. |
| GitHub Actions secrets | 0 |
| GitHub App installations | not determinable with user OAuth (`/installation` → 401, expected: needs an App JWT). Nothing in the repo references one. |
| Code scanning | endpoint reachable, **"no analysis found"** (404) — SARIF upload has never happened; the feature is available on this public repo (inference [E6] from the 404-not-403 response; I5 verifies against docs). |
| Issues | enabled, **0 open**, 0 ever referenced by any doc — the tracker is currently empty, so any issue automation starts from a clean slate. |
| Repo settings | `allow_auto_merge: false`, `delete_branch_on_merge: false` (explains the stale merged branch), Dependabot security updates / secret scanning / push protection all **disabled**. |
| Webhooks | not readable (`admin:repo_hook` scope missing); Woodpecker's webhook was created by its OAuth activation per `docs/woodpecker-ci.md`. |

No privileged write path exists that is not a human's own token. Nothing in I0 changed that.

---

## 5. Toolchain inventory by **capability state** (verified this session, not read off a plugin list)

| Tool | State for this investigation | Evidence this session |
|---|---|---|
| Superpowers 6.3.0 (user scope) | **ACTIVE** — process owner | `using-superpowers`, `using-git-worktrees` loaded and followed [E1] |
| Claude Code 2.1.186 native `EnterWorktree`/`ExitWorktree` | **ACTIVE** | worktree entered by `path` after manual `git worktree add … origin/main` (exact base-ref control); `.claude/worktrees/` is ignored via `.git/info/exclude` (**local, uncommitted** — a fresh clone would not ignore it) [E3] |
| Worktree Bash guard | **ACTIVE constraint** | refuses "too complex" compound commands inside a worktree session (hit twice: a `set -e`/`time`/heredoc chain and a `2>&1 \| tail; ${PIPESTATUS}` chain). Later prototypes must use plain commands or script files. [E3] |
| Project hooks (`.claude/settings.json`) | **ACTIVE** | `SessionStart` (orient + post-compact), `UserPromptSubmit` checkpoint nudge, `PreToolUse Bash` `guard_data_db.py`, `PostToolUse Edit\|Write` syntax check + `run_tests.py` (scope: `main.py`, `services/*.py`) [E1] |
| User-global hooks | **changed since `tooling-plugins.md` was written:** only `PreToolUse Bash → sql_guard.py` remains; the GitNexus `PreToolUse Grep\|Glob\|Bash` / `PostToolUse Bash` hooks are **gone** (no `[GitNexus]` blocks appeared in any grep this session). The "Known unresolved" section of `.claude/rules/tooling-plugins.md` is now stale — noted, not edited in I0 (out of scope; separate small docs commit later). [E1/E3] |
| GitNexus | **ACTIVE (worktree index)** | primary index was stale (`c88eaa5`, branch `chore/claude-toolchain`). Rebuilt in the worktree: 19.9 s, 7,182 nodes / 15,004 edges, `static/status.html` skipped (>512 KB). Smell test passed: `impact QualityFinding --summary-only` → 19 impacted / MEDIUM / direct 14; `impact kelly_scaled_max_size --summary-only` → 4 / LOW / direct 1 with `trading_loop`+`evaluate` processes — distinct sets, and direct=14 agrees with the source grep (13–14 code references). **Two repos are now registered under the same name**; every query must pass `--repo /home/davidf/code/portfolio/showcase-projects/autotrade/.claude/worktrees/aqc-investigation`. [E3] |
| Context7 | **LIMITED (anonymous)**, working | `resolve-library-id FastAPI` returned `/websites/fastapi_tiangolo` (2607 snippets) [E4]. Not needed for I0 beyond the probe. |
| `gh` CLI | **ACTIVE** (read + human write) | §4 |
| Woodpecker API | **LIMITED** — anonymous read of health + pipeline list works; step logs/trigger need `WOODPECKER_TOKEN` (unset) | §3.3 [E4] |
| `woodpecker-cli` (local binary) | **NOT INSTALLED** — use the `woodpeckerci/woodpecker-cli:v3` image `scripts/woodpecker-status` already wraps | `which` [E1] |
| Host Python | 3.12.3 with `pyyaml`; **no** `fastapi`/`pytest`/`mypy` → `tools.quality_audit` and `tools.project_manifest` run on host; pytest must go through ddev (§3.4) | `python3 -c import …` [E3] |
| ddev (`fastapi` container, Python 3.13) | **ACTIVE**, sees worktrees under `/app/.claude/worktrees/…`; no `git` binary inside | §3.4 [E3] |
| Chrome DevTools MCP | **NOT_NEEDED** for I0 (backend/CI-only); connected per `claude mcp list`; not invoked | policy |
| dimensional-analysis | **NOT_NEEDED** (no trading math touched) | policy |
| 42Crunch / second-opinion | **BLOCKED_EXTERNAL** — skipped silently (`plugin:second-opinion:codex ✘ Failed to connect`, as `tooling-plugins.md` predicts) | `claude mcp list` [E1] |
| Devil's Advocate MCP | **BLOCKED_EXTERNAL** per memory (`devils-advocate-mcp-blocked-external`: paid subscription); shows "Connected" in `claude mcp list` but not retried — I10's adversarial pass is an internal/agent review | memory + policy |
| Wolfram MCP, PagerDuty MCP | NOT_NEEDED (PagerDuty additionally unauthenticated) | `claude mcp list` |
| Built-in Explore/Plan agents | not used in I0 — the surface was small enough (≈2,400 lines of QCP code, 6 pipelines) that direct reads cost less context than cold subagents, and no policy-sensitive conclusion was delegated | plan §I0 "only if doing so reduces context load" |

---

## 6. How I0 was executed (so a reviewer can reproduce the isolation)

```
git worktree prune
git worktree add .claude/worktrees/aqc-investigation \
    -b chore/autonomous-quality-coordination-investigation origin/main      # base = 8d1796b
cp <six bundle files> → worktree (never mv; primary checkout untouched)
EnterWorktree path=…/aqc-investigation                                    # session cwd switch
python3 -m tools.quality_audit --repo-root . …                             # §3.4
python3 -m tools.project_manifest --check …                                # §3.4
ddev exec -s fastapi bash -c "cd /app/.claude/worktrees/aqc-investigation && pytest <3 QCP files>"
GITNEXUS_WAL_CHECKPOINT_THRESHOLD=67108864 npx gitnexus@latest analyze --force --skip-agents-md
```
Nothing was written to GitHub, Woodpecker, `data/*.db`, `config/settings.yaml`, user-global settings,
or the primary checkout.

---

## 7. Decisions taken in I0

1. **Branch/worktree:** `chore/autonomous-quality-coordination-investigation` from `origin/main`
   (`8d1796b`), in `.claude/worktrees/aqc-investigation`. Reason: primary checkout is owned by a live
   session (§2.1). Remove the worktree after the investigation PR merges.
2. **Canonical files committed** (rule, skill, known-findings, spec, plan, kickoff) in their own
   scaffold commit, mirroring PR #10's "Plan realtime Kalshi data-plane investigation"; install
   artifacts not committed (§2.3).
3. **Router entry:** `.claude/rules/quality-capabilities.md` is uncontested (§2.4) → a single bullet for
   this orchestrator added in a separate focused commit, matching the `frontend-modularization-task`
   bullet PR #11 added. Nothing else in that file changed.
4. **Not done, deliberately:** no edit to `tooling-plugins.md` (stale GitNexus-hook section), no
   deletion of the stale `origin/chore/realtime-data-plane-investigation` branch, no cleanup of
   `BUNDLE_README.md`/`INSTALL_…`/`PACKAGE_MANIFEST.json` in the primary checkout — all are either
   another session's surface or a user-visible housekeeping choice, and none blocks I1.

---

## 8. Explicit unknowns (bounded, not guessed)

- **Woodpecker server-side secrets**: whether any repo/org secrets are configured on the Woodpecker side
  is not readable without a token; the repo's own pipelines reference none, which is what matters for the
  PR-lane threat model. (I4 checks the documented PR-event secret behavior against current docs.)
- **GitHub App presence**: cannot be enumerated with the user token; assume none until I4 needs one.
- **Whether the realtime session will touch `quality-capabilities.md` / `ROADMAP.md` later**: its plan
  does not list them, but I13 must re-check before syncing docs.
- **Final `origin/main` (`8d1796b`) status**: `tests-pytest` was still `pending` at 23:44Z when sampled;
  the investigation branch's own push status is what the I0 report records.
- **`strict: false` on branch protection**: means a green PR can merge onto a `main` that moved after its
  CI ran — the stale-result window for any future detector-driven write is therefore real and bounded
  only by human diligence today. Threat-modeled in I6, not addressed here.

---

## 9. Next task

**I1 — Audit every QCP finding for durable automation identity.** Inputs are ready: the 16 constructor
sites (§3.2), the 190-finding census JSON from `8d1796b` (§3.4, regenerable), a fresh GitNexus index for
`QualityFinding`/`run_audit` producer–consumer questions (remember `--repo …/aqc-investigation`), and the
contested-file list (§2.2) — I1 must **not** modify `tools/quality_audit/baseline.json`; mutation
experiments go in a temp directory or a second throwaway worktree, never in production identity code.
