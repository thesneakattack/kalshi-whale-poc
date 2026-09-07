# Autonomous Quality Authority — Threat Model (I6)

**Task:** I6 of `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `dcaf475` (worktree
`.claude/worktrees/aqc-investigation`, base `origin/main` @ `8d1796b`). No open PRs.

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live API/runtime · **[E5]** upstream docs · **[E6]** inference. 42Crunch and second-opinion
remain BLOCKED_EXTERNAL and were skipped; this is an internal review pass over the I4 candidates.
**No credential was created or exercised; every live fact is a read.**

---

## 1. Scope and attacker model

**System under review:** the I4 candidates A (Woodpecker write lane), C (GitHub-native coordinator +
Woodpecker verifier) and D (report-only), carrying the I1 identity, I2 persistence, I3 suppression and I5
reporting policies. B is dominated (I4 §3) and not modelled separately.

**Assets:** (1) any write credential to `thesneakattack/kalshi-whale-poc`; (2) the integrity of `main`
and of the guards that protect it (branch protection, required contexts, `tools/quality_audit`,
`tools/kalshi_census.py`, `tests/support/runtime_isolation.py`); (3) the protected economic domains
(evidence rule §"Remediation authority rule"); (4) the signal-to-noise of the repository's work queue.

**Attackers, with the live preconditions that bound them [E4]:**

| Attacker | Precondition today |
|---|---|
| Untrusted PR author (fork) | can open PRs; Woodpecker `require_approval: "forks"` gates their pipelines; Woodpecker secrets are not offered to `pull_request` by default [E5]; GitHub Actions has no PR-triggered workflow at all [E1] |
| Non-owner collaborator pushing a branch | **none exist** — the collaborator list is exactly `thesneakattack` (admin) [E4] |
| The coordinator itself (buggy or subverted) | the only in-scope automated actor; today it does not exist |
| Holder of a leaked `repo`-scoped user token | can read the Woodpecker hook URL (bearer token in query string) [E4], push branches, open PRs, but **cannot** bypass branch protection (`enforce_admins: true`; bypass actors impossible on a personal repo [E5]) |
| The public | reads every pipeline log and `build/quality-audit.json` [E4]; reads `GET /api/repos/1` on Woodpecker; reads Actions logs |

## 2. Threats

Each threat states the mechanism, impact, controls that already exist (with evidence), what each
topology must add, and whether a failure is a **veto** for I10.

### T1 — Credential exfiltration by untrusted PR code

- **Mechanism.** A PR modifies `.woodpecker/*.yml` (Woodpecker runs the PR's own YAML) or a workflow
  file to `from_secret`/`echo` a credential, or to exploit `trusted` (volume/network/security all
  **true** on this repo [E4]) to reach the host.
- **Existing controls.** Fork PRs need manual approval before any pipeline runs [E4/E5]; Woodpecker
  secrets are excluded from `pull_request` unless explicitly enabled [E5]; Actions has no PR trigger;
  no Actions secrets exist (count 0) [E4]; Woodpecker has no referenced secrets [E1].
- **Topology A.** Adds a key at rest in Woodpecker. The `Secret.events` list must **exclude**
  `pull_request`, and the `Secret.images` allowlist must pin the coordinator image, because YAML
  `when:` filters are authored by the pusher and are not a boundary (I4 §2.1). A secret allowed for
  `push` is offered to pushes on *any* branch — today only the owner can push, so the exposure is
  "owner error", not "attacker"; that changes the day a collaborator is added. **Veto V1 if the key is
  reachable from any `pull_request` lane or if `images` is unrestricted.**
- **Topology C.** No secret at rest; `GITHUB_TOKEN` is per-job and is never issued to a
  `pull_request`-triggered job because none exists. Residual: none beyond the existing repo settings.
- **Topology D.** Nothing to exfiltrate.

### T2 — PR-target-branch filter mistakes

- **Mechanism.** A privileged Woodpecker step keyed on `branch: main` alone. Docs: the step "also
  [triggers] if the target branch of a pull request is `main`" [E5]; live: pipelines 92/94/96 are
  `event: pull_request` with `branch: main` [E4].
- **Control.** `when: event: [push, cron]` **and** `branch: main`, plus the coordinator image checking
  `CI_PIPELINE_EVENT` itself (defence in depth, I4 §6). Proven by I9 fault injection before any key
  exists. **Veto V1 for A** without both.
- **C/D.** Not applicable — Actions `on: push: branches: [main]` is an event filter by construction
  (a `pull_request` trigger is a different event, and none is defined).

### T3 — Issue/PR storms from unstable identity

- **Mechanism.** Keying durable state on a line-numbered `finding_id`: one harmless insertion above
  `n` findings produces `n` closes + `n` opens (I1 §5.2); a different defect on the same line inherits
  the old item (identity theft); recurrence at a new line opens a duplicate.
- **Quantified.** I5 §6: even with *stable* keys, issue-on-first-observation would open/close 16
  issues/week on this cadence; with raw IDs every refactor of a file with `n` boundary findings adds
  `2n` events on top.
- **Control.** Use the I1 `automation_key` (position-free, subject-carrying), aggregate identical
  defects as occurrences, reopen-not-duplicate on recurrence, and the I5 silent-intermediate rule.
  **Veto V8: any design that uses raw `finding_id` as a write key for classes with line-numbered IDs.**

### T4 — Stale detector result / replay after `main` moves

- **Mechanism.** `strict: false` on branch protection [E4] lets a green PR merge onto a `main` that has
  moved; a coordinator acting on an audit of SHA *X* after `main` is at *Y* may escalate a finding *Y*
  already fixed, resolve one *Y* reintroduced, or open a fixer PR against a stale base.
- **Control.** Bind every decision to the audited SHA; before any write, re-read `origin/main` and
  **abort if it differs** (compare-and-act); resolution only from a fresh audit of the *current* `main`.
  A replayed or retried decision for an old SHA must be a no-op. I9 tests this without credentials.
  **Veto V6 if writes are not SHA-bound.**

### T5 — Self-modification: the remediator weakens the guard that flagged it

- **Mechanism.** A fixer edits `tools/quality_audit/baseline.json` (accepting its own finding), a
  scanner, `tools/kalshi_census.py` (shared detection), `tests/support/runtime_isolation.py`,
  `.woodpecker/*.yml`, `.github/workflows/*.yml`, branch protection, or the coordinator's own policy.
- **Existing controls [E4/E5].** `GITHUB_TOKEN` has **no `administration` scope** (the `permissions`
  list is actions, artifact-metadata, attestations, checks, code-quality, contents, deployments,
  discussions, id-token, issues, packages, pages, pull-requests, security-events, statuses,
  vulnerability-alerts) — a C-topology token structurally cannot touch branch protection or repository
  settings. `enforce_admins: true`, no bypass actors possible on a personal repository, `allow_auto_merge:
  false`, `can_approve_pull_request_reviews: false`: nothing an automated actor holds can merge to
  `main` without a human clicking merge after the five required contexts pass. The CLAUDE.md
  baseline-ratchet rule already forbids "silently add a new real error to the baseline".
- **The self-referential hole.** A PR that edits `.woodpecker/*.yml` runs *its own* YAML; a PR that
  edits a scanner is audited by *that* scanner. CI cannot prove a guard-weakening PR wrong; **the human
  merge is the boundary**, and it holds only if the human is not conditioned to rubber-stamp bot PRs.
- **Controls to add.** (1) A **protected-path denylist** in the fixer (§3) — any touched path outside the
  fixer's explicit allowlist aborts the run, before a branch is even created; (2) draft PRs only,
  never merge, never `contents: write` to `main`; (3) an App (topology A) must be registered
  **without** the Administration permission; (4) the denylist and allowlist live in the coordinator's
  own protected path, so the coordinator cannot widen them through its own channel. **Veto V5 if any
  automated actor can merge, bypass, or hold administration.**

### T6 — Accidental real-money semantic change

- **Mechanism.** A "mechanical" fix touches a file that decides orders, sizing, risk, fees, settlement,
  calibration, config bounds, or `trading_enabled`. The live grep [E1] for `trading_enabled|kill_switch|
  max_daily_loss|kelly_fraction_of_cap|create_order` spans 24 files, including `main.py`,
  `services/execution.py`, `services/risk_manager.py`, `services/shadow_mode.py`,
  `services/strategy_engine.py`, `services/config_bounds.py`, `services/config/routes.py`,
  `services/kalshi/{account_client,orders,contracts/order,interfaces,websocket}.py`,
  `services/exits/*`, `services/whale_stream/*`, `services/account_positions.py`.
- **Control.** The protected-path list in §3 is a **denylist enforced before allowlist**; the only
  fixer targets are generated artifacts proven idempotent in I7. Safety invariants in CLAUDE.md
  (`trading_enabled` default false + typed confirmation) are unchanged by anything here. **Veto V7:
  no fixer may touch a denylisted path, and no LLM-driven fixer exists in the autonomous path.**

### T7 — Prompt injection via issue/PR text

- **Mechanism.** If an AI remediator ever reads an issue body, PR description, or commit message
  authored by a third party, that text is an instruction channel. GitHub's own hardening guidance
  treats PR title/body as untrusted input to be isolated in an environment variable before use [E5].
- **Control.** The autonomous path is deterministic only (I7 candidates: allowlisted generators run
  twice, diff must be empty). A human-directed Claude session acting on an issue treats issue text as
  data (this repository's rules already say comment/thread text is untrusted). If an AI fixer is ever
  proposed, it is a new design with its own threat model, not an extension of this one. **Veto V7.**

### T8 — Derived credentials in public logs

- **Mechanism.** Topology A mints a 1-hour installation token from the stored key; Woodpecker masks
  only stored secrets [E5]; the logs are public [E4]. One stray `echo`/traceback = a public write token.
- **Control.** Coordinator image never prints tokens, redacts exceptions, and is the only image the key
  is offered to. For C, `GITHUB_TOKEN` is masked by Actions as a first-class secret. **Veto V2 for A
  unless log hygiene is proven by test, not policy.**

### T9 — Webhook token exposure

- **Mechanism.** The Woodpecker hook URL carries its bearer token; the hook list is readable with the
  `repo` scope [E4]. A leaked user token therefore also yields the ability to forge webhook deliveries
  to Woodpecker (pipeline spam for arbitrary refs; not secret access, which is gated by event/image).
- **Control.** Rotate by re-activating the repository in Woodpecker if a user token is ever exposed;
  keep the user token off any automation. Low impact, recorded for completeness.

### T10 — Trusted-mode escalation in Woodpecker

- **Mechanism.** `trusted: {network, volumes, security}` all true lets pipeline steps mount volumes,
  use host networking, and run privileged [E5]; only a server admin can set it [E5]. Combined with a
  new collaborator or a leaked push-capable token, a branch push could reach the CI host.
- **Control.** Precondition monitoring: the collaborator list is the tripwire (currently one). The
  investigation does not change `trusted` — it is there for the uv cache volume — but any A-topology
  design must assume the CI host is reachable by anyone who can push a branch.

### T11 — Coordinator as a denial-of-work channel

- **Mechanism.** Even a correct coordinator that escalates too early (I2 P0/k-based policies) or on
  branch findings (I5 P3: 148 emissions/week) buries the one real item; a suppressed-forever finding
  (I3 D directory overlap) hides it.
- **Control.** The I2 floor, I3 precedence with expiry, I5 silent-intermediate rule; the emission
  table is the regression baseline (0 emissions/week at zero durable defects).

## 3. Protected paths and capabilities (for any later fixer or coordinator)

**Deny before allow.** A fixer computes its diff, and if *any* path matches the denylist the run aborts
with a report and no branch. The lists are data the coordinator reads from its own protected path.

| Domain (evidence rule) | Concrete paths (from the live tree, [E1]) |
|---|---|
| Real trading / order execution | `main.py`, `services/execution.py`, `services/kalshi/**` (`account_client.py`, `orders.py`, `contracts/order.py`, `interfaces.py`, `websocket.py`, `public.py`, `transport.py`), `services/account_positions.py`, `services/accounts_store.py` |
| Risk limits / kill switches | `services/risk_manager.py`, `services/shadow_mode.py`, `services/alerting/**` |
| Sizing / bankroll / exposure | `services/strategy_engine.py`, `services/paper_broker.py`, `services/position/**`, `services/exits/**`, `services/config_bounds.py`, `services/config_overrides.py`, `services/config_store.py`, `services/config/**`, `config/settings.yaml` |
| Whale / advisory / confidence / calibration | `services/whale_calibration/**`, `services/advisory/**`, `services/confidence_scoring.py`, `services/whale_stream/**`, `services/whalewatchers/**`, `services/whale_simulator.py` |
| Strategy / EV / fee / P&L | `services/kalshi_fees.py`, `services/trade_analytics.py`, `services/analytics/**`, `services/backtest/**`, `services/settlement_edge.py`, `services/settlement_edge_entry.py` |
| Settlement / lifecycle | `services/market_events/**`, `services/market_watch/**`, `services/series_*.py`, `services/index_feed/**` |
| Security / auth / authz | `services/auth.py`, `.ddev/**`, `.env*`, `services/http_client.py` |
| CI / branch protection / credentials | `.woodpecker/**`, `.github/**`, `mypy.ini`, `requirements*.txt`, `scripts/**`, `docs/woodpecker-ci.md` |
| The guards themselves | `tools/quality_audit/**` (including `baseline.json`), `tools/kalshi_census.py`, `tools/project_manifest.py`, `tests/support/runtime_isolation.py`, `tests/conftest.py`, `.claude/**` |
| The coordinator's own policy | its future package, allow/deny lists, state schema, workflow file |
| **Allowed fixer targets (I7 must prove)** | `static/project-manifest.json` (generated by `tools.project_manifest --write`); nothing else until proven |

**Capabilities never granted to an automated actor:** merge to `main`; approve a PR; modify branch
protection, rulesets, secrets, webhooks, collaborators, or repository settings (`administration`);
push to `main`; delete branches it did not create; edit a denylisted path; act on a SHA other than the
one it audited.

## 4. Fail-open / fail-closed

"Fail closed" here means *no external write*; observation state may still be recorded as `unknown`.

| Failure | Behaviour | Rationale |
|---|---|---|
| Detector crash / import error / scanner exception | **closed** — no escalation, no resolution; record `unknown`; the CI gate itself is already red (existing) | a missing audit is not a clean audit (evidence rule: unknown beats fabricated healthy) |
| GitHub API outage / rate limit | **closed** — retry on next run; never mark resolved; never open a duplicate on retry (idempotent by key) | writes must be re-derivable from state |
| Token failure (mint/permission) | **closed** — abort before any write; surface as the coordinator's own status, not an issue | a token problem is an operator problem |
| `origin/main` moved since the audited SHA | **closed** — abort; re-observe (T4) | stale decisions are wrong decisions |
| Active-work signals unreadable (git/GitHub) | **silent** — treat as suppressed, bounded by the I2 hard cap; emit a coordinator-degraded status | silence is cheaper than a wrong issue; the cap prevents indefinite blindness |
| Identity ambiguity (same key, multiple occurrences; subject missing) | **aggregate** — one item, occurrence list; never split, never resolve | I1 collision semantics |
| Fixer produces a diff touching a denylisted path, or a non-empty second run | **closed** — abort, no branch, report | I7 determinism proof is a precondition, not a hope |
| Suppression expiry with no fresh audit available | **closed** — eligible, but escalation waits for the next observation | escalation needs evidence, not a timer |

Nothing fails *open* into a write. The only fail-open is the CI gate's existing behaviour of treating
warnings/info as non-blocking, which is unchanged.

## 5. Veto conditions for the I10 matrix

A candidate architecture fails outright — regardless of its weighted score — if any holds:

| id | veto | source |
|---|---|---|
| V1 | a `pull_request`-event lane can reach a write credential (secret events include `pull_request`, or `images` unrestricted, or a PR-triggered write job) | T1, T2 |
| V2 | a derived credential can appear in a publicly readable log without a tested redaction | T8 |
| V3 | the write identity is a long-lived user-scoped token (PAT) | I4 |
| V4 | authority cannot be removed by deleting one permission/secret | I4 |
| V5 | any automated actor can merge to `main`, bypass protection, approve PRs, or hold administration | T5 |
| V6 | writes are not bound to the audited `main` SHA with a compare-and-act check | T4 |
| V7 | a fixer can touch a denylisted path, or an LLM sits in the autonomous remediation path | T6, T7 |
| V8 | durable state is keyed on a line-numbered `finding_id` | T3 |

Applied to I4's candidates on current evidence: **A** trips V1/V2 unless four out-of-GitHub controls
are added and tested (I4 §3); **C** trips none, with V6/V7 being design obligations for I11 rather than
platform gaps; **D** trips none by construction.

## 6. Rejected mitigations

- *"CI will catch a guard-weakening PR."* It cannot: the PR runs its own YAML and its own scanner
  (T5). The human merge is the control; do not design as if CI were self-protecting.
- *"Restrict secrets by branch in Woodpecker."* Not a feature — `Secret` has `events` and `images`
  only [E4].
- *"Give the App Administration to let it fix branch protection drift."* Inverts the trust model (T5).
- *"Comment on the PR when suppressing."* Turns the coordinator into the storm it exists to prevent
  (I5 P2); also feeds T7 if text is ever consumed by an AI.
- *"Auto-merge mechanical PRs after green."* Out of scope by the evidence rule; would collapse T5's
  only real boundary.

## 7. Bounded unknowns

- Whether GitHub delivers Woodpecker's webhook for a `GITHUB_TOKEN`-created PR (I4 §8 #1) — a capability
  question, but it also decides whether C's draft-PR stage needs an App token at all (and thereby
  whether T8 applies to C).
- Woodpecker secret availability semantics on `push` to non-default branches are inferred from the
  schema and docs [E4/E5], not yet exercised; I9 tests with a harmless dummy secret before any real key.
- The protected-path list is a snapshot of today's tree; I11 must pin it to a checked-in file with a
  test that fails when a denylisted directory is renamed without updating the list.

## 8. Handoff

**Next: I7 — inventory and prove deterministic remediation candidates.** Inputs ready: the allow/deny
lists (§3) with `static/project-manifest.json` as the single allowed target to prove; the abort
semantics (§4: dirty tree, non-empty second run, denylisted path); and the I2 evidence that two
concurrent PRs already collided on that exact file.
