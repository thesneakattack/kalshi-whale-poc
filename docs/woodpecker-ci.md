# Woodpecker CI — operational reference

Living reference for how this repo's CI actually runs. The policy (what
Claude runs locally vs. what CI owns) lives in
`.claude/rules/branching-and-ci.md` — this doc is the "how do I actually
operate it" companion.

## Architecture

```text
portfolio/ci-cd/                    <- outside this repo, shared portfolio-wide
  docker-compose.yml                   woodpecker-server + woodpecker-agent
  .env                                 GitHub OAuth client/secret, agent secret (gitignored)

autotrade/ (this repo)
  .woodpecker/*.yml                 <- pipeline definitions, one file per named check
  .github/workflows/                <- workflow_dispatch-only manual fallbacks
  scripts/woodpecker-status         <- repo-scoped CLI wrapper: health + pipeline status
  scripts/woodpecker-trigger        <- repo-scoped CLI wrapper: manual pipeline trigger
```

One Woodpecker server + one Docker-backed agent already run for the whole
`portfolio/` workspace — not duplicated per-project. `WOODPECKER_GITHUB`
OAuth is already configured there for the `thesneakattack` GitHub account
(`WOODPECKER_ADMIN: thesneakattack`), reachable at `https://ci.webfoundry.dev`
(and an offline/local Traefik router at `https://ci.webfoundry.localhost`,
same shared-`traefik_proxy`-network pattern this repo's own
`autotrade.webfoundry.dev` tunnel uses — see CLAUDE.md's dev-workflow
section). Container-to-container on the `traefik_proxy` Docker network
(`http://woodpecker-server:8000`) is what actually works reliably today —
direct host access to the container's bridge IP is refused (no port
published in the compose file, by design; only Traefik and other
containers on that network can reach it), and the `ci.webfoundry.localhost`
Traefik route currently 404s (likely a local DNS/hosts entry that was
never added for this specific hostname, unlike `*.ddev.site`'s automatic
resolution — untested further since it wasn't required for any
verification here). `scripts/woodpecker-status`/`woodpecker-trigger` both
default to the working container-network path.

## Pipeline topology

Each `.woodpecker/*.yml` file is an independent workflow — Woodpecker runs
each as its own job and reports its own status back to GitHub, so these
run in parallel rather than one serialized script (bounded today by the
shared agent's configured concurrency — see "Known limitations" below).

| File | Mirrors former GitHub Actions job | Path-filtered? |
|---|---|---|
| `tests-pytest-app.yml` | `tests.yml` / `pytest` (app half, 2026-09-03 split — see `tools/classify_pytest_app_vs_tooling.py`) | no within its half — full unscoped app-code suite, cross-module regressions |
| `tests-pytest-tooling.yml` | `tests.yml` / `pytest` (tooling half) | no within its half — full unscoped tooling suite; proven non-interacting with the app half by the CI pipeline audit's pytest-profile doc |
| `tests-dependency-audit.yml` | `tests.yml` / `dependency-audit` | yes (2026-09-03) — skips `pip-audit` internally (still posts its required status) unless `requirements*.txt` changed; see `scripts/ci-skip-if-unaffected.sh` |
| `quality-frontend-build.yml` | `quality.yml` / `frontend-build` | yes — `frontend/**` only |
| `quality-architecture-audit.yml` | `quality.yml` / `architecture-audit` (now also covers `frontend-api-contract` and `tools.project_manifest --check`, QCP Task 17, bundled into the same step) | no — the frontend-contract scanner reads both sides |
| `kalshi-contract-fixtures.yml` | new — `services/kalshi_client.py` etc.'s existing tests plus `tests/test_kalshi_contracts.py` (QCP Task 13's fixture-JSON-driven contract tests), isolated for clearer failure attribution | no |
| `quality-browser-e2e.yml` | `quality.yml` / `browser-e2e` (QCP Task 8) — real headless-Chrome smoke against `tests/support/e2e_server.py`'s isolated ASGI harness | no — exercises served `static/` through the real backend routes |

`kalshi-docs/content-drift` (QCP Task 12) shipped as an upgrade to
`.github/workflows/docs-drift-check.yml` instead — real SHA256 content-
drift detection via `tools/kalshi_docs_drift.py`, not just the old URL-
availability curl loop. `kalshi-contract/public-api-canary` (QCP Task 14)
shipped the same way, as new `.github/workflows/kalshi-contract.yml` —
a live, read-only check against Kalshi's real unauthenticated
`/exchange/status` and `/markets` endpoints via
`tools/kalshi_public_canary.py`. `performance/synthetic-regressions`
(QCP Task 19) shipped the same way too, as new
`.github/workflows/performance.yml` — `tests/test_performance_regressions.py`
(opt-in via `RUN_PERFORMANCE_REGRESSIONS=1`, so the default `pytest` run
never pays for its 10k/50k/100k-row synthetic datasets). All three
deliberately stay on GitHub Actions rather than becoming `.woodpecker/*.yml`
files: they're schedule-triggered (weekly cron), and Woodpecker's
cron-trigger mechanism isn't set up anywhere in this repo today — every
`.woodpecker/*.yml` file above is push/PR/manual-triggered only (see
"Pipeline topology" above and the manual-trigger note below). Revisit if
Woodpecker cron scheduling is ever configured for this project.

**A path-filtered workflow (`quality-frontend-build`) posts no status at
all when skipped** — if branch protection ever marks it "required," a
backend-only PR would block on a check that never runs. Don't mark it
required; the other four aren't path-filtered and are safe to require.

**A manually triggered pipeline (`scripts/woodpecker-trigger`, the "Run
pipeline" UI button, or a raw `POST /api/repos/{id}/pipelines`) carries
`event: manual`.** `tests-pytest-app.yml`/`tests-pytest-tooling.yml` (the
2026-09-03 split of the former single `tests-pytest.yml`) have matched
this event since 2026-08-28 (`when: event: [push, pull_request, manual]`)
and each run their own full, unscoped half on a manual trigger — see
either file's own header comment for why (an explicit ask for confidence,
never testmon-scoped). The other five workflows still only match `[push,
pull_request]`, so a manual trigger today produces exactly two workflows
and posts exactly two GitHub statuses (`ci/woodpecker/manual/tests-pytest-app`,
`.../tests-pytest-tooling`) — confirmed live as of 2026-09-02 (before the
split, one workflow/status): 0 of 314 retained pipelines had ever actually
been triggered this way (CI pipeline audit), so this capability was wired
but unused; not re-checked live since the split. Verifying the other five
workflows still requires a real push, or
temporarily broadening a workflow's `when:` to include `event: manual`
while testing.

## Final job taxonomy (QCP Task 20)

Every deterministic check this initiative built or touched, in one place —
the target list `docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md` (moved there 2026-09-06, planning-lanes migration)
Task 20 names, mapped to where each one actually lives (not always a literal
"GitHub Actions job name," since Woodpecker owns push/PR here — see
"Architecture" above):

| Conceptual check | Where it actually runs |
|---|---|
| `tests / pytest` | `.woodpecker/tests-pytest-app.yml` + `tests-pytest-tooling.yml` (push/PR, split 2026-09-03) · `tests.yml` (manual fallback) |
| `tests / dependency-audit` | `.woodpecker/tests-dependency-audit.yml` (push/PR) · `tests.yml` (manual fallback) |
| `quality / architecture-audit` | `.woodpecker/quality-architecture-audit.yml` (push/PR) · `quality.yml` (manual fallback) |
| `quality / frontend-build` | `.woodpecker/quality-frontend-build.yml` (push/PR, `frontend/**` path-filtered) · `quality.yml` (manual fallback) |
| `quality / frontend-api-contract` | bundled into `quality-architecture-audit.yml`'s single `tools.quality_audit` step, not a separately-named check — deliberate (QCP Tasks 3-7), not a taxonomy gap; the CLI's own output names which scanner found what |
| `quality / browser-e2e` | `.woodpecker/quality-browser-e2e.yml` (push/PR) · `quality.yml` (manual fallback) |
| `quality / project-manifest` | also bundled into `quality-architecture-audit.yml`'s same step (QCP Task 17), same reasoning as frontend-api-contract above |
| `kalshi-contract / fixture-contracts` | `.woodpecker/kalshi-contract-fixtures.yml` (push/PR) |
| `kalshi-contract / public-api-canary` | `.github/workflows/kalshi-contract.yml` (scheduled weekly + manual) |
| `kalshi-docs / content-drift` | `.github/workflows/docs-drift-check.yml` (scheduled weekly + manual) |
| `performance / synthetic-regressions` | `.github/workflows/performance.yml` (scheduled weekly + manual) |

All 11 conceptual checks exist and are wired in. The two bundled pairs
(frontend-api-contract/project-manifest into architecture-audit) were a
deliberate efficiency choice each landed with, not something this
finalization pass split apart — doing so would mean either duplicating the
`tools.quality_audit`/`tools.project_manifest` CLI invocations (real extra
runtime cost per push/PR) or teaching those CLIs a `--only` filter neither
currently has, which is out of scope for a finalization pass. Revisit only
if failure-attribution ambiguity between the bundled checks actually causes
real confusion in practice.

No workflow — Woodpecker or GitHub Actions — references `secrets.*`
anywhere in this repo (confirmed via `grep -rn "secrets\." .github/workflows/
*.yml`); the public API canary in particular is fixture-proven to only ever
call `GET /exchange/status` and `GET /markets`, never an account/order
endpoint (`tests/test_kalshi_public_canary.py::
test_main_never_touches_account_or_order_endpoints`).

## Checking CI results (no personal token needed)

Woodpecker posts a commit status back to GitHub for every workflow, so the
fastest way to check a push's result — in any Claude session, with zero
setup — is `gh` (already authenticated), not `scripts/woodpecker-status`:

```bash
gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status
```

Read each entry's `context` (`ci/woodpecker/push/<workflow-name>`) and
`state` independently — a `failure` on any one context means that change
isn't verified, regardless of the others. Each entry's `target_url` opens
the Woodpecker web UI for that run; `scripts/woodpecker-status --pipeline
N --log STEP` (needs a personal `WOODPECKER_TOKEN`) pulls the same failing
step's log text without a browser. **Actually run this after every push —
found live 2026-08-25 that `quality-architecture-audit` sat red across
three real pushes (`d644034`, `21a303a`, `b28a380`, a stale
`static/project-manifest.json`) with nobody noticing, because this check
was never exercised.** See `.claude/skills/checkpoint/SKILL.md` step 6 for
where this is now the documented default.

## Dependency cache (`wp-uv-cache`)

Every Python step installs with `uv` rather than `pip`, and the five that do
share one named Docker volume mounted at `/root/.cache/uv`.

The split matters: `uv` makes *installing* effectively free, but not
*downloading*. A real CI log read `Prepared 64 packages in 50.50s / Installed
64 packages in 110ms` — so on a fresh container the whole cost was the
network. The volume makes that download happen once per package version
instead of once per step per push (measured locally on this requirements set:
4.5s cold, 0.875s warm).

Notes:

- Safe under concurrency — `uv` locks its cache, which matters because the
  agent runs `WOODPECKER_MAX_WORKFLOWS=4` and several of these steps install
  simultaneously.
- Needs no settings change: this repo is already trusted for volumes
  (`gh`-equivalent check: `curl -s https://ci.webfoundry.dev/api/repos/1`
  shows `trusted: {"network": false, "volumes": true, "security": true}` —
  this repo is trusted for volumes and security, but not network egress from
  step containers; re-check live if a future step needs outbound network
  access).
- To reset it: `docker volume rm wp-uv-cache`. Losing it costs exactly one
  slow run; Docker recreates it on next use.
- `tests-dependency-audit` deliberately stays on `pip` and has no cache
  mount — it installs only `pip-audit`, and having the tool that audits
  dependency provenance be installed by a different resolver, from a shared
  cache, is not a tradeoff worth ~30s.

Requirements are also split so a step downloads only what it imports:
`requirements-dev.txt` (shared), `requirements-selenium.txt` (browser-e2e
only — selenium is a 9.1MB wheel), `requirements-playwright.txt`
(playwright-e2e only).

## Known limitations (observed, not assumed)

- `WOODPECKER_MAX_WORKFLOWS` on the shared agent
  (`portfolio/ci-cd/docker-compose.yml`) went from Woodpecker's default of
  1 → 2 (2026-08-24, commit `81068e2`) → **4** (2026-08-25), each time
  confirmed live in the agent's own startup log (`"parallel workflows":N`).
  The 2 was set while the host was genuinely memory-starved (~768MB free,
  2.9GB already in swap); a `.wslconfig` repair left it at 16 CPU / 15.6GB
  with ~9.8GB available and swap essentially unused, which is what changed.
  4 was chosen from measurement, not the pipeline count: sampling every
  workflow step container at 4s intervals across a full three-PR run (27
  containers, including `quality-browser-e2e`, which installs and runs
  Chromium *inside* its own step container) put the peak at 258MB, most
  sitting at 100-250MB. Still less than this repo's six independent
  `.woodpecker/*.yml` files, so some queuing under concurrent pipelines is
  still expected — deliberately, since this agent is portfolio-wide and the
  same host runs the live ddev stack (`ddev-kalshi-whale-poc-fastapi` alone
  is ~4.2GB), where CI memory pressure would disturb running application
  state rather than merely fail a build. Raise it further only with a fresh
  measurement under real concurrent load, not as a side effect of this
  repo's own pipeline count.
- `WOODPECKER_GRPC_SECRET` is unset on the shared server, so a restart
  regenerates a random one (`WOODPECKER_GRPC_SECRET is not set; generated
  a temporary random secret` in `docker logs woodpecker-server`). The
  agent still reconnected successfully in the case observed here, but
  persisting this secret in `portfolio/ci-cd/.env` would make that
  reconnection deterministic instead of relying on a fresh handshake.
- **A GitHub webhook delivery can fail with `"failure to parse token from
  hook"` (HTTP 400 from Woodpecker), silently leaving a commit with zero
  PR-event pipeline** — confirmed 2026-09-04 via `gh api
  repos/<owner>/<repo>/hooks/<id>/deliveries` (per-delivery detail needs
  the `admin:repo_hook` token scope this repo's default `gh` auth lacks
  for the listing view, but works for individual delivery lookups by ID):
  PR #553's own `pull_request: opened` delivery got exactly this 400,
  0.33s round-trip (too fast to be a payload-size/timeout issue — a
  same-window successful delivery for a different PR's `opened` event took
  5.91s for a similarly-sized ~30KB payload), while sibling PRs opened
  within the same few minutes succeeded. A same-delivery-ID redelivery
  (`POST .../deliveries/<id>/attempts`) failed identically once, then
  succeeded on a second attempt seconds later — consistent with transient
  token-validation flakiness on Woodpecker's side, not anything specific to
  the PR's content or size. **This is not the same incident as the
  "Cancel previous pipelines" mutual-cancellation race documented above**
  (that one leaves a stale `pending` status from a real, accepted
  delivery; this one is the delivery itself being rejected before a
  pipeline is ever created, so the commit shows no PR-event status at all,
  pending or otherwise). Recourse when `gh pr view <n> --json
  mergeStateStatus` shows `BLOCKED` with the required `ci/woodpecker/pr/*`
  contexts entirely absent (not merely pending/red): check
  `gh api repos/<owner>/<repo>/hooks` for the repo's webhook, list its
  recent `.../deliveries`, find the `pull_request` delivery matching the
  PR's `createdAt`, and redeliver it via the `attempts` endpoint —
  retrying once or twice resolved it live. No `WOODPECKER_TOKEN` needed
  for this path (it's a `gh` GitHub-API call against the webhook, not the
  Woodpecker API).
  **Update 2026-09-04 (PR #559, Task 12): this is not an isolated
  occurrence — three separate sessions hit the same underlying symptom
  (push event never reaches Woodpecker) on three different PRs the same
  day (#544, #553, #559), with two distinct error bodies (`"failure to
  parse token from hook"` above; PR #559's was a bare `"Invalid HTTP
  Response: 400"` with no parseable message via `gh api`) and, unlike the
  single `pull_request: opened` case above, also hit plain `push` events
  and `pull_request: closed`/`edited`/`reopened` actions.** A ~25-minute
  window of this repo's own delivery log (03:35-03:59 UTC) showed roughly
  2 in 5 deliveries failing this way regardless of event type, correlated
  with heavy concurrent push/PR volume across ~30 simultaneously active
  worktree sessions that day — consistent with load-related flakiness on
  Woodpecker's ingress, not a per-PR defect. **The documented `attempts`
  redelivery endpoint is not always usable**: it needs the `admin:repo_hook`
  token scope, which this session's default `gh` auth did not have
  (`gh api -X POST .../deliveries/<id>/attempts` returned a 404 first,
  then an explicit scope error on retry) — the "no `WOODPECKER_TOKEN`
  needed" framing above is correct but incomplete, since a *different*
  scope is required and isn't guaranteed present. **Working fallback that
  needs no special scope**: since the failure is intermittent rather than
  deterministic for a given payload, a *fresh* delivery for the same PR
  (not a redelivery of the identical failed one) has a good chance of
  landing — `gh pr close <n>` then `gh pr reopen <n>` generates a new
  `pull_request` webhook each time; it took two retries to get a
  successful delivery live on 2026-09-04. For a `push` event with the
  same symptom (`ci/woodpecker/push/*` contexts entirely absent), the
  manual pipeline (`scripts/woodpecker-trigger`) does NOT substitute for
  this — verified live: it posts to `ci/woodpecker/manual/*` contexts
  only, which do not satisfy branch protection's required `ci/woodpecker/
  pr/*`/`ci/woodpecker/push/*` contexts, so a green manual run does not by
  itself make a PR mergeable.
- The agent log periodically shows `"queue: task not found"` /
  `"failed to extend workflow lease"` (with a matching server-side
  `"stream: not found"` / `"cannot close log stream"`) for a handful of
  pipeline/workflow IDs scattered hours apart (observed 2026-08-24 around
  14:20, 19:25, 21:33 for pipelines 8, 15, 23/26). Consistent with a
  workflow being superseded/canceled by a rapid follow-up push while it's
  mid-run — exactly what "Cancel previous pipelines" below is for — rather
  than a systemic failure: pipeline numbers kept climbing normally into
  the high 20s across the same window with real passing/failing statuses
  reported throughout.
  **Confirmed 2026-08-26** (this doc's own "re-open this" trigger fired):
  `curl -H "Authorization: Bearer $WOODPECKER_TOKEN"
  .../pipelines/<n>` on two consecutive push-event pipelines for the same
  commit/branch (`docs/reconcile-execution-program-state`, pipelines 240
  and 241, created one second apart — a duplicate GitHub webhook delivery
  for the same push, not two real pushes) showed `"cancel_info":
  {"superseded_by": <the other one's number>}` on **both** — a genuine
  mutual-cancellation race, not a one-sided supersede. Net effect: the
  push-event pytest/architecture-audit/etc. checks never ran at all for
  that commit (GitHub's status API is left showing stale `pending`
  indefinitely, not a real failure or the actual `canceled` state).
  Harmless for merge purposes — the PR-event pipeline for the same commit
  ran and posted a clean pass, and PR-event is what branch protection
  actually requires (see below) — but confirms this repo's
  "Cancel previous pipelines" setting really is on and really can
  self-cancel a legitimate single push under a duplicate-webhook race, not
  just a rapid double-push. If a *push-only* signal (no matching PR yet)
  ever needs to be trusted and shows stale `pending`, check the pipeline's
  own `cancel_info` via the API before assuming it failed or is still
  running.

  (Note, added 2026-09-03, corrected 2026-09-03: the server's pipeline-
  number sequence was reset on 2026-08-31. Checked live: pipeline 241 now
  404s, but pipeline 240 has been reused and returns a real, unrelated
  `pull_request`/`main` pipeline from 2026-09-01 — not this incident. So
  pre-reset numbers cited in this doc are worse than merely unresolvable;
  240 specifically now silently points at different content. The incident
  and its lesson above are still accurate history, just no longer
  independently re-verifiable by number — don't follow either number as a
  live link.)
- `main` has real GitHub branch protection, configured 2026-08-25 (was
  unconfigured/404 before that) — see
  `.claude/rules/branching-and-ci.md`'s "Integration lifecycle" section
  for the exact settings and how to change them. The five required
  status-check contexts are the `ci/woodpecker/pr/*` names (not
  `ci/woodpecker/push/*`) since PR-triggered runs are what actually gate
  a PR's merge button; `quality-frontend-build` is deliberately excluded
  since it's path-filtered and posts nothing when skipped.

## Repo activation in Woodpecker — done

`kalshi-whale-poc` is activated in the shared Woodpecker instance
(confirmed live 2026-08-25: real GitHub commit statuses posting per push —
see "Checking CI results" above — and pipeline numbers into the high 20s,
not just this doc's earlier claim that activation was still pending). The
GitHub webhook this required was created automatically by
`WOODPECKER_GITHUB` OAuth on activation; no manual webhook configuration
was needed.

One sub-item from the original activation checklist is now confirmed, one
remains open:

- **"Cancel previous pipelines" is confirmed enabled** — see the
  2026-08-26 update above (real `cancel_info` evidence from the API, not
  circumstantial).
- A personal `WOODPECKER_TOKEN` does exist and works (used to pull the
  `cancel_info` evidence above) — whether that's the same token for
  everyone who might work in this repo, or something to (re)generate
  per-operator, remains unconfirmed.

## Operating the shared instance itself

Out of scope for this repo's own scripts (affects every project in
`portfolio/`, not just this one) — from `portfolio/ci-cd/`:

```bash
cd portfolio/ci-cd
docker compose ps                    # status
docker compose logs -f woodpecker-server woodpecker-agent   # logs
docker compose restart               # restart both
docker compose pull && docker compose up -d   # upgrade (check release notes for breaking changes first)
```

Adding a second agent: add another `woodpecker-agent` service block to
that compose file with the same `WOODPECKER_AGENT_SECRET`, or raise
`WOODPECKER_MAX_WORKFLOWS` on the existing one — no changes needed on
this repo's side either way, per Woodpecker's own design (this is exactly
what the pipeline-file-per-check structure above was built to make free).
