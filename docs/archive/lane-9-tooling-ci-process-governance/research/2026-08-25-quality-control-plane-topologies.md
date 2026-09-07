# Quality Control-Plane Topologies — I4 (event and credential comparison)

**Task:** I4 of `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `4fb265e` (worktree
`.claude/worktrees/aqc-investigation`, base `origin/main` @ `8d1796b`). No open PRs; the realtime branch
is unchanged since I3's re-ground.

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live API/runtime · **[E5]** upstream docs · **[E6]** inference. Every platform claim below cites
the page it came from; every live claim was read from this repository with the existing user token or
anonymously. **No credential was created, stored, or exercised.**

---

## 1. Question and what would settle it

**Question.** Which event/credential topology gives the coordinator the *minimum* authority its
selected actions need, with no write credential reachable from an untrusted PR lane and the least
operational burden — or is report-only the right topology for now?

A candidate is **vetoed** (not merely scored down) if any of the following holds on the evidence:
(V1) a pull-request-event lane can reach a write credential; (V2) a derived credential can land in a
log that is publicly readable; (V3) it requires a long-lived, user-scoped token (PAT) as the write
identity; (V4) it cannot be disabled or staged by removing one permission/secret.

## 2. Platform semantics established (with sources)

### 2.1 Woodpecker (installed server: **3.17.0**, `GET /version` [E4]; docs: woodpecker-ci.org [E5])

- **Events** (`WebhookEvent` enum from the server's own OpenAPI, [E4]): `push`, `pull_request`,
  `pull_request_closed`, `pull_request_metadata`, `tag`, `release`, `deployment`, `cron`, `manual`.
- **`branch:` matches PR targets.** Docs: "The step now triggers on main branch, but also if the target
  branch of a pull request is `main`" [E5]; live: pipelines 92/94/96 are `event: pull_request` with
  `branch: main` (I2 §7) [E4]. **Any privileged step must carry an explicit `event:` filter.**
- **Secrets and events.** Docs: "By default, secrets are not exposed to pull requests"; default events are
  `push`, `tag`, `deployment`; pull requests must be explicitly enabled; warning: "If your repository is
  public and accepts pull requests from everyone, your secrets may be at risk" [E5]. The `Secret` schema
  has `events` and `images` [E4] — a secret can be limited to named events **and to a pinned plugin
  image** ("If enabled, they are not available to any other plugins" [E5]).
- **Masking.** "Woodpecker can mask secrets from its own secrets store, but it cannot apply the same
  protection to external secrets" [E5] — a token *derived* in-step (e.g. an App installation token
  minted from a stored private key) is not a stored secret and is **not masked**.
- **Cron.** `event: cron` with an optional `cron:` name filter; created in repo settings by anyone with
  push access [E5]; the `Cron` schema carries `branch`, `schedule`, `timezone`, `enabled` [E4] — a cron
  runs against a configured branch. Whether a cron pipeline posts a commit status to GitHub is **not
  documented** → I9.
- **This repository's Woodpecker settings** (anonymous `GET /api/repos/1`, [E4]): `visibility: public`,
  `allow_pr: true`, `require_approval: "forks"`, `cancel_previous_pipeline_events: [push,
  pull_request]`, `trusted: {network: true, volumes: true, security: true}`, `timeout: 60`. Secrets and
  cron lists return **401** anonymously — not readable, and (I0) none are referenced by any pipeline.
- **Secrets on `push` are branch-agnostic.** The `Secret` schema restricts by event and image only
  [E4]; a secret allowed for `push` is offered to a push on *any* branch. Because the pipeline YAML is
  authored by whoever pushes the branch, `when:` filters in the YAML are not a security boundary for
  secrets — only the `images` allowlist (and the fact that only the repository owner can push
  non-fork branches to this public repo) is.

### 2.2 GitHub App installation tokens [E5]

- Authenticate as the App with a JWT "signed using the `RS256` algorithm" with the App's private key;
  "The time must be no more than 10 minutes into the future" for `exp`; the JWT is used "to
  authenticate as an app or generate an installation access token".
- `POST /app/installations/{id}/access_tokens` → "The installation access token will expire after 1
  hour"; it can be narrowed at mint time with `repositories`/`repository_ids` and `permissions`.
- Private key: GitHub "only stores the public portion"; "You should not hard-code your private key in
  your app, even if your code is stored in a private repository"; "Consider storing the key in a key
  vault"; up to 25 keys "to rotate keys without downtime"; only the App owner can generate/delete keys.
- Permissions are fine-grained, "no default permissions", "select the minimum permissions required":
  Issues r/w (issues), Pull requests r/w (PRs/comments), Contents r/w (push branches), Code scanning
  alerts r/w or Security events (SARIF), Checks / Commit statuses (read), Actions (read). Permission
  changes require installation-owner approval.
- Events created with an App token **do** trigger workflow runs — the docs recommend "a GitHub App
  installation access token" precisely to "trigger workflows from within workflow runs".

### 2.3 GitHub Actions job-scoped `GITHUB_TOKEN` [E5 + E4]

- "The `GITHUB_TOKEN` expires when the job finishes or after its effective maximum lifetime" (24 h
  refresh cap on self-hosted runners); "permissions are limited to the repository that contains your
  workflow".
- Repository default: "Read repository contents and packages permissions" for new personal repos; this
  repo's live setting is **`default_workflow_permissions: "read"`, `can_approve_pull_request_reviews:
  false`** (`GET /actions/permissions/workflow`, [E4]). A workflow's `permissions:` key can raise
  specific scopes; "Allow GitHub Actions to create and approve pull requests" is a separate setting,
  off by default.
- Required names: `issues: write`, `pull-requests: write`, `contents: write` (push), `security-events:
  write` (SARIF upload — "required for all workflows"), `actions: read`/`contents: read` (private repos
  only for SARIF).
- "Events triggered by the `GITHUB_TOKEN` will not create a new workflow run", except
  `workflow_dispatch`/`repository_dispatch`, and PRs opened/synchronised by `GITHUB_TOKEN` land in an
  "approval-required" state. Whether the *webhook to Woodpecker* fires for a `GITHUB_TOKEN`-created PR
  is **not stated** (the rule is about Actions runs) → the single most important I9 fault-injection.
- Hardening page: `pull_request_target`/`workflow_run` "are privileged … may have repository write
  access and access to referenced secrets" — never check out untrusted PR code under them; put untrusted
  text (PR title/body) into an environment variable before use; pin third-party actions to a full SHA.
- Fork-PR workflows for public repos are gated by the "Approval for running fork pull request
  workflows" settings [E5]; this repo's current workflows are `workflow_dispatch`/`schedule` only [E1].

### 2.4 SARIF / code scanning [E5]

Two upload paths: `github/codeql-action/upload-sarif` (needs `security-events: write`) and
`POST /repos/{owner}/{repo}/code-scanning/sarifs`. Code scanning is free on public repositories (this repo
is public; `code-scanning/alerts` currently returns "no analysis found", I0 §4). I1 §7 already covers
fingerprint semantics.

### 2.5 Live authority state of this repository (all read-only) [E4]

| Surface | State |
|---|---|
| Write identity available today | the developer's OAuth token (`gist, read:org, repo`) via `gh` — human only |
| GitHub Actions secrets | 0 |
| GitHub App installations | not enumerable with a user token (403 by design); none referenced anywhere |
| Woodpecker secrets / crons | 401 anonymously; none referenced by any `.woodpecker/*.yml` |
| Woodpecker webhook | one `web` hook (push, pull_request, pull_request_review, deployment) whose URL carries Woodpecker's hook bearer token as a query parameter; the hook list is readable with the `repo` scope. Not reproduced here; recorded as an exposure class for I6 (a leaked `repo`-scoped token can forge pipeline triggers). |
| Code scanning | enabled-capable, never used |
| Branch protection | `enforce_admins`, five required `ci/woodpecker/pr/*` contexts, `strict: false` (I0 §1) |

## 3. The four candidates

Common to A–C: the detector is `tools.quality_audit` (deterministic, ~2 s on host, ~60 s in CI with
dependency install); the coordinator logic is the I3 precedence over the I1 `automation_key`; every
write is idempotent by key. The differences are *where the trusted event comes from* and *what
credential the write step holds*.

### Candidate A — Woodpecker detector + Woodpecker write lane

```
push main ──► Woodpecker (event=push, branch=main) ──► audit ──► coordinator step
cron      ──► Woodpecker (event=cron,  branch=main) ──┘            │ App private key (Woodpecker secret,
PR ───────► Woodpecker (event=pull_request, branch=main) — audit only, no secret   events:[push,cron], images:[pinned])
                                                                    ▼ mint 1-h installation token in-step
                                                            GitHub REST/GraphQL writes
```
- **Trust boundaries:** the PR lane is protected by Woodpecker's default (secrets not offered to
  `pull_request`) plus `require_approval: forks`. The **`push` lane is the weak edge**: the key is
  offered to *any* push whose YAML asks for it, so the only enforceable boundary is the secret's
  `images` allowlist (a pinned coordinator image that itself checks `CI_PIPELINE_EVENT ∈ {push, cron}`
  and `CI_COMMIT_BRANCH == main`) — YAML `when:` filters are convenience, not security.
- **Credential at rest:** the App private key lives in Woodpecker's database on the shared portfolio
  host (`portfolio/ci-cd`), outside GitHub's control; rotation is manual.
- **Token in flight:** a derived 1-h installation token — **not masked** by Woodpecker, and this repo's
  pipeline logs are **publicly readable** (I0/I2 fetched them anonymously). Any accidental print is a
  public 1-hour write token → veto V2 unless the coordinator image never emits it.
- **Retry/idempotence point:** the coordinator step; re-runs happen on every main push and cron.
- **Failure:** GitHub outage makes the *main* pipeline red unless the step is `failure: ignore`;
  either way the noise lands on `main`'s status.
- **Staging/disable:** delete the secret or the step — one action; but the key must be re-created to
  re-enable.
- **Verdict:** viable **only** with (key in Woodpecker) + (`images` allowlist) + (`event` filter in
  both YAML and image) + (explicit log hygiene) — four controls that all live outside GitHub.

### Candidate B — Woodpecker detector + GitHub-native write controller

Handoff options, each measured against what exists:
1. **Woodpecker → `repository_dispatch`** carrying the detector result: requires a token in Woodpecker
   with `contents: write` (or an App token) — reintroduces a Woodpecker-held secret, narrower than A but
   the same trust edge.
2. **GitHub `status` event** (Woodpecker already posts commit statuses) → an Actions workflow reads
   Woodpecker's *public* API/log for `build/quality-audit.json` (the `print-report` step already cats it
   into the log): secretless, but couples the control plane to log scraping of a third-party server and
   to public-log availability.
3. **The Actions workflow re-runs the detector itself** — at which point B is C.
- **Verdict:** dominated. Every secretless variant degenerates into C; every secretful one inherits A's
  weak edge without A's simplicity.

### Candidate C — GitHub-native coordination + Woodpecker verifier

```
push main / schedule / workflow_dispatch ──► GitHub Actions workflow (permissions: per job)
      job "observe":  contents:read           → run tools.quality_audit (≈60 s), persist state
      job "report":   security-events:write   → SARIF upload (source-located rules only)
      job "escalate": issues:write            → durable-finding issues (post I10 decision)
      job "propose":  contents:write, pull-requests:write → deterministic draft PRs (post I7/I10)
pull_request (any) ──► Woodpecker only (exhaustive verifier, required contexts) — no Actions write job
```
- **Trust boundaries:** no write job is bound to `pull_request`; the only events are `push` to `main`,
  `schedule`, and `workflow_dispatch` — all owner-controlled. Fork PRs never reach a write job.
- **Credential at rest:** **none.** `GITHUB_TOKEN` is minted per job, expires at job end, and is
  capped by the repository's `read` default unless a job's `permissions:` block raises exactly the
  scopes it needs — the staging ladder is literally the `permissions:` key, one job at a time.
- **Known constraints (from the docs):** a PR/commit created with `GITHUB_TOKEN` does not start new
  *Actions* runs; PRs it opens sit in "approval-required" state; creating PRs at all needs the
  "Allow GitHub Actions to create and approve pull requests" repo setting (off today). Whether the
  **webhook to Woodpecker** fires for such a PR — so the five required `ci/woodpecker/pr/*` contexts
  ever post — is **undocumented** and decides whether the draft-PR stage can use `GITHUB_TOKEN` or needs
  an App token for that one step. → **I9 fault-injection #1**.
- **Retry/idempotence:** the workflow run; re-run via `workflow_dispatch`.
- **Failure:** an Actions failure is visible on the `main` commit as its own check, separate from the
  Woodpecker contexts; it never blocks a PR (not a required context).
- **Observability:** Actions logs (public repo → also public); the same log-hygiene rule applies, but
  `GITHUB_TOKEN` is masked by Actions automatically as a first-class secret.
- **Duplication cost:** the audit runs twice per `main` push (Woodpecker + Actions), ≈60 s of free
  public-repo minutes; Woodpecker remains the only *required* verifier.

### Candidate D — report-only control

QCP output stays where it is (status contexts, public logs, `build/quality-audit.json`), optionally
enriched with a persisted observation series (I2's replay shows what that series looks like) and a
SARIF export for source-located rules. No issues, no PRs, no credentials. Humans/Claude decide what
becomes work.
- **Trust boundaries / credentials:** none beyond today's. (SARIF upload alone needs
  `security-events: write` in a `push`-to-`main` Actions job — a C-shaped job with one scope.)
- **What it cannot do:** file or reopen an issue for a durable finding. I2 measured **zero** durable
  finding episodes on `main` in the ratchet's lifetime; the only durable problem was a CI-status
  incident that a status-watching human fixed in 12 h.

## 4. Comparison matrix

Scores 1–5 (5 = best), with the evidence they rest on; vetoes marked separately as the spec requires.

| Criterion | A Woodpecker write lane | B hybrid | C GitHub-native + Woodpecker verifier | D report-only |
|---|---|---|---|---|
| Contention avoidance (I3 policy is topology-independent) | 4 | 4 | 4 | 5 (no writes to contend) |
| False-positive escalation risk (policy, not topology) | 3 | 3 | 3 | 5 |
| False-negative / hidden-defect risk | 3 | 3 | 3 | 2 (relies on humans reading logs) |
| **Credential exposure** | **1** — private key at rest on a third-party host; derived token unmasked in public logs (V2 unless engineered away) | 2 — a narrower secret in the same place | **5** — nothing at rest; job-scoped, auto-masked | 5 |
| **Untrusted-PR isolation** | 3 — PR lane secretless by default + fork approval; `push`-lane edge needs `images` allowlist | 3 | **5** — no write job on `pull_request` at all | 5 |
| Implementation complexity | 3 — App registration, key handling, coordinator image | 2 | 3 — App-less; workflow + `permissions:`; one open webhook question | 5 |
| Operational complexity | 2 — key rotation, Woodpecker secret store, public-log hygiene | 2 | 4 — repo settings only | 5 |
| Recovery / idempotence | 3 — re-run on next push/cron | 3 | 4 — `workflow_dispatch` re-run | 5 |
| Observability / explainability | 3 — public pipeline logs (double-edged) | 3 | 4 — per-job logs + checks | 3 |
| Maintenance burden | 2 | 2 | 4 | 5 |
| Fit with current branch/CI policy | 4 — Woodpecker stays authoritative | 3 | 5 — Woodpecker stays the required verifier; Actions already exists as fallback | 5 |
| Ability to stage / disable safely | 3 — delete secret | 3 | **5** — one `permissions:` line per capability | 5 |
| **Veto conditions** | V2 unless proven otherwise (derived token, public logs); V1 only if `images` allowlist omitted | inherits A's | none identified; open question (webhook) is a *capability* gap, not a security veto | none |

Averages hide the important thing, so it is stated plainly: **A can only be made safe by stacking
four controls outside GitHub; C needs zero controls that do not already exist as repository settings.**
D is the strongest on every safety axis and loses only on "can it act", which I2's data has not yet
shown to be needed.

## 5. What the evidence supports for I10 (provisional, not a decision)

1. **D is the baseline** and must be beaten on *demonstrated need*, not on capability. The measured
   need so far is zero durable finding escalations; the one durable incident was a status problem.
2. **If a write lane is ever justified, C is the least-privilege shape**: no key at rest, per-job
   scopes, PR lanes structurally excluded, staged by editing one `permissions:` block. Its one real
   unknown (Woodpecker webhooks on `GITHUB_TOKEN`-created PRs) is testable without any credential
   (I9), and its fallback (an App token for the single PR-creation step) is narrower than A's.
3. **A is not rejected on capability but on exposure geometry**: the key sits outside GitHub, the
   derived token is unmasked, and the logs are public. Each is mitigable; together they are the
   operational burden the spec asks to minimise.
4. **B is dominated** and can be dropped from I10 unless a new handoff mechanism appears.
5. **A scheduled `main` audit** (I2 §9's cadence gap) is free under C (`schedule:`) and under A
   (`cron`), and irrelevant under D only if humans are the observers.

## 6. Sketches (documentation only — nothing here is a proposal to commit)

**A — privileged Woodpecker step, showing every control it needs:**
```yaml
# .woodpecker/quality-coordinate.yml (illustrative)
when:
  - event: [push, cron]      # never pull_request; branch alone would match PRs targeting main
    branch: main
steps:
  - name: coordinate
    image: ghcr.io/<owner>/qcp-coordinator@sha256:<pinned>   # the ONLY image the secret is allowed for
    environment:
      APP_PRIVATE_KEY: { from_secret: qcp_app_private_key }  # secret: events [push, cron], images [that digest]
    commands:
      - test "$CI_PIPELINE_EVENT" != pull_request && test "$CI_COMMIT_BRANCH" = main   # defence in depth
      - qcp-coordinate --never-print-tokens                   # derived token is NOT masked by Woodpecker
```

**C — least-privilege Actions workflow, showing the staging ladder:**
```yaml
# .github/workflows/quality-coordinate.yml (illustrative)
on:
  push: { branches: [main] }
  schedule: [{ cron: "17 */6 * * *" }]   # observation cadence decoupled from commit cadence (I2 §5)
  workflow_dispatch:
permissions: { contents: read }          # repository default is already "read"
jobs:
  observe:                                # stage 1: dry-run / persisted state only
    permissions: { contents: read }
  report:                                 # stage 2: SARIF for source-located rules
    permissions: { contents: read, security-events: write }
  escalate:                               # stage 3: issues (only after I10 says so)
    permissions: { contents: read, issues: write }
  propose:                                # stage 4: deterministic draft PRs (only after I7/I10)
    permissions: { contents: write, pull-requests: write }
```
Each later stage is one more `permissions:` line; removing the line removes the authority.

## 7. Rejected on evidence

- **PAT as the write identity** (any topology): long-lived, user-scoped, not per-job — V3.
- **Storing an App private key anywhere in the repository or in a Woodpecker secret offered to
  `pull_request`**: V1.
- **`branch: main` without `event:`** for any privileged Woodpecker step: matches PRs targeting main
  (docs + live pipelines 92/94/96).
- **Relying on YAML `when:` as the secret boundary in Woodpecker**: the pusher writes the YAML; only
  `Secret.images`/`Secret.events` are server-enforced.
- **B via log scraping**: couples correctness to public-log retention of a third-party server.

## 8. Bounded unknowns → I9 fault-injection list

1. Does GitHub deliver the Woodpecker **webhook** for a PR opened/updated with `GITHUB_TOKEN`? (Decides
   whether C's draft-PR stage can be App-less.) Test with a throwaway branch/PR and no secret.
2. Does a Woodpecker **cron** pipeline post a commit status? (Decides whether a scheduled Woodpecker
   audit is visible on GitHub at all.) Testable with a cron on this repo — a CI-config change, so a
   deliberate, reviewed experiment.
3. Woodpecker secret availability to `push` on non-`main` branches with a **harmless dummy secret**:
   confirm the `events`/`images` semantics above before any real key exists.
4. Effect of "Allow GitHub Actions to create and approve pull requests" on required-context posting.

## 9. Handoff

**Next: I5 — compare reporting surfaces and noise economics.** Inputs ready: the SARIF permission and
upload facts (§2.4), the I1 identity/fingerprint contract, I2's episode series for churn simulation, and
the public-log observation that today's "report" is already world-readable.
