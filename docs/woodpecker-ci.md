# Woodpecker CI — operational reference

Living reference for how this repo's CI actually runs. The policy (what
Claude runs locally vs. what CI owns) lives in
`.claude/skills/ci-cd-guardrails/SKILL.md`'s "Local vs CI verification
policy" — this doc is the "how do I actually operate it" companion.

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
| `tests-pytest.yml` | `tests.yml` / `pytest` | no — full suite, cross-module regressions |
| `tests-dependency-audit.yml` | `tests.yml` / `dependency-audit` | no |
| `quality-frontend-build.yml` | `quality.yml` / `frontend-build` | yes — `frontend/**` only |
| `quality-architecture-audit.yml` | `quality.yml` / `architecture-audit` (now also covers `frontend-api-contract`, bundled into the same `tools.quality_audit` CLI call) | no — the frontend-contract scanner reads both sides |
| `kalshi-contract-fixtures.yml` | new — `services/kalshi_client.py` etc.'s existing tests plus `tests/test_kalshi_contracts.py` (QCP Task 13's fixture-JSON-driven contract tests), isolated for clearer failure attribution | no |
| `quality-browser-e2e.yml` | `quality.yml` / `browser-e2e` (QCP Task 8) — real headless-Chrome smoke against `tests/support/e2e_server.py`'s isolated ASGI harness | no — exercises served `static/` through the real backend routes |

Not built (the underlying capability doesn't exist in the repo yet, so
there is nothing real for a Woodpecker job to run — see
`docs/superpowers/plans/2026-08-24-quality-control-plane.md`'s Tasks 14,
19): `kalshi-contract/public-api-canary`, `performance/synthetic-
regressions`. Add the matching `.woodpecker/*.yml` file once each QCP task
actually ships the checker it would run — don't wire a pipeline stage
ahead of the capability it's supposed to gate.

`kalshi-docs/content-drift` (QCP Task 12) shipped as an upgrade to
`.github/workflows/docs-drift-check.yml` instead — real SHA256 content-
drift detection via `tools/kalshi_docs_drift.py`, not just the old URL-
availability curl loop. Deliberately stays on GitHub Actions rather than
becoming a `.woodpecker/*.yml` file: it's schedule-triggered (weekly cron),
and Woodpecker's cron-trigger mechanism isn't set up anywhere in this repo
today — every `.woodpecker/*.yml` file above is push/PR/manual-triggered
only (see "Pipeline topology" above and the manual-trigger note below).
Revisit if Woodpecker cron scheduling is ever configured for this project.

**A path-filtered workflow (`quality-frontend-build`) posts no status at
all when skipped** — if branch protection ever marks it "required," a
backend-only PR would block on a check that never runs. Don't mark it
required; the other four aren't path-filtered and are safe to require.

**A manually triggered pipeline (`scripts/woodpecker-trigger`, the "Run
pipeline" UI button, or a raw `POST /api/repos/{id}/pipelines`) carries
`event: manual`, which none of these workflows' `when: event: [push,
pull_request]` filters match** — confirmed live: the API call itself
succeeds (`204`), but the response carries a `pipeline-filtered: true`
header and no workflow actually runs. This is the filters working as
designed, not a bug - manual verification of these specific checks means
either a real push, or temporarily broadening a workflow's `when:` to
include `event: manual` while testing.

## Known limitations (observed, not assumed)

- The agent's startup log reports `"parallel workflows":1` — only one
  workflow runs at a time today despite five independent files existing.
  True parallelism needs `WOODPECKER_MAX_WORKFLOWS` raised on the shared
  agent (`portfolio/ci-cd/docker-compose.yml`) or a second agent added —
  either affects the whole portfolio, so raise it deliberately, not as a
  side effect of this repo's own pipeline count.
- `WOODPECKER_GRPC_SECRET` is unset on the shared server, so a restart
  regenerates a random one (`WOODPECKER_GRPC_SECRET is not set; generated
  a temporary random secret` in `docker logs woodpecker-server`). The
  agent still reconnected successfully in the case observed here, but
  persisting this secret in `portfolio/ci-cd/.env` would make that
  reconnection deterministic instead of relying on a fresh handshake.
- No GitHub branch protection is currently configured on `main`
  (`gh api repos/thesneakattack/kalshi-whale-poc/branches/main/protection`
  → 404), so nothing is silently broken by moving these checks off
  GitHub Actions' automatic triggers. If protection is added later, point
  required checks at the Woodpecker-reported contexts.

## One-time manual step: activate this repo in Woodpecker

Everything above is built and verified (pipeline files lint clean, and
were proven end-to-end via `woodpecker-cli exec --local` against the real
Docker backend — see the QCP/Woodpecker session's commit history for the
exact runs, including a deliberate isolated failure proven to report
correctly and a clean pipeline proven to pass afterward). What's left
needs a browser and the `thesneakattack` GitHub account's own OAuth login,
which cannot be done from here:

1. Open `https://ci.webfoundry.dev` (or the offline router, once its
   hostname resolves) and log in via GitHub OAuth.
2. Find `kalshi-whale-poc` in the repo list and enable it — this is what
   actually creates the GitHub webhook (`WOODPECKER_GITHUB` OAuth handles
   this automatically on activation, no manual webhook configuration
   needed).
3. Optional but recommended: in the repo's Woodpecker project settings,
   enable "Cancel previous pipelines" (handles superseded runs when
   corrective commits land quickly — Woodpecker's supported mechanism for
   this, no pipeline-file changes needed).
4. Generate a personal CLI/API token (Settings → CLI/API Access Token) and
   `export WOODPECKER_TOKEN=...` in your own shell profile (never in this
   repo) to use `scripts/woodpecker-status`/`woodpecker-trigger` beyond
   their unauthenticated health check.

After step 2, the next push to `main` (or any branch/PR) triggers every
`.woodpecker/*.yml` workflow automatically.

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
