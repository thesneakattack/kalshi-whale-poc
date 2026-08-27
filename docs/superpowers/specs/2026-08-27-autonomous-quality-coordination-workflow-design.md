# Autonomous Quality Coordination — Workflow-Health Design (Corrected Scope)

**Repository:** `thesneakattack/kalshi-whale-poc`
**Date:** 2026-08-27
**Status:** Brainstormed and approved in chat, section by section. Not yet
implemented. This document is the spec; implementation follows via
`superpowers:writing-plans`, not this document itself.

---

## 1. Purpose and the correction this document makes

**Direct correction (2026-08-27):** the original Autonomous Quality
Coordination (AQC) investigation and implementation (I0-I13, then Program 7
Tasks 1-9 on `feat/autonomous-quality-coordination`) targeted the wrong
subject. It built a persisted observation series over
`tools.quality_audit`'s findings about the *trading application's own code*
(unused routes, config-usage gaps, persistence-isolation gaps). That was
never the intended audit subject — direct user correction, verbatim:

> "the goal of this plan wasnt to audit the application, but to audit, be
> an expert project manager, and of course 'be a janitor' over the
> automated workflow itself. it should be able to get reports from the
> application to help inform itself."

**Disposition of the prior work (decided in this brainstorm, not
re-litigated here):** the existing implementation on
`feat/autonomous-quality-coordination` (PR #43) is a legitimate, working
capability in its own right — persisted, idempotent observation over
static app-code findings. It is kept, renamed away from "AQC"/"quality
coordination" to `tools/quality_ratchet.py` (see §12), and merges
independently of this design. "AQC" now names, and only names, the thing
specified here.

**What AQC actually is:** a standalone workflow tool that acts as an
automated project manager and janitor over *this repository's own
engineering workflow* — branch/PR/CI lifecycle, `superpowers` plan/ledger
execution health, standing-rule and process hygiene, and (as a data feed,
not an audited target) the trading application's own self-reported
diagnostics. It formalizes, and automates a version of, what
`docs/superpowers/plans/2026-08-26-active-tracks-board.md` already does by
hand.

## 2. Non-goals

- Does not audit the trading application's code quality. That is
  `tools/quality_ratchet.py`'s job, unchanged, and out of scope here.
- Does not implement, require, or depend on Autonomous Engineering Mode
  (AEM, `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-
  design.md`) or the `github-issues-kanban` skill. Whether AQC's
  escalation-eligible findings ever feed AEM's issue queue is explicitly
  deferred — this mirrors AEM's own §11 deferral of "which sources feed
  the queue," and was an explicit decision in this brainstorm (see §13).
- Gains **no GitHub write authority** of any kind — no issues, no PR
  creation, no comments. The only mutating authority this design grants is
  local git/filesystem cleanup, scoped to exactly three actions (§8).
- Does not modify `tools/quality_ratchet.py`'s internals. It is renamed
  and merged as-is. A future retrofit onto this design's shared
  coordination engine (§7) is a deferred, separate decision (§13), not
  bundled here.
- Does not resolve documentation/ROADMAP drift. It surfaces the raw
  comparison (open items vs. recent related activity) as a data feed;
  judging whether something is actually done is left to a human or a
  Claude session reading that feed, not asserted as a finding (§6.4).

## 3. Prior art and governing documents

- **`.claude/rules/autonomous-quality-coordination-evidence.md`** remains
  authoritative for AQC's escalation/action-authority discipline. This
  design generalizes its framing — written assuming GitHub write
  authority is the only kind of action being governed — to local
  git/filesystem cleanup authority. The rule's substance (narrow-authority
  bias, no guessed persistence thresholds, deterministic/idempotent/
  path-contained remediation criteria, evidence classes) applies
  unchanged; only its GitHub-specific framing needs a documentation
  update, tracked as an implementation task (§14), not resolved by
  reinterpretation here.
- **`docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-
  design.md`** — sibling mechanism, explicitly source-agnostic. This
  design does not wire into it (§2).
- **`docs/superpowers/plans/2026-08-26-active-tracks-board.md`** — the
  hand-maintained artifact whose job AQC automates a version of. AQC's
  branch/PR/ledger signal domain (§6.1-§6.2) should be read as "what would
  it take to keep this board's *concurrency ground truth* and *track
  status* sections accurate without a human re-deriving them each
  session."
- **The original AQC investigation** (`docs/superpowers/plans/2026-08-25-
  autonomous-quality-coordination-investigation.md`, its architecture
  decision, spec, and Program 7 plan) — retained as historical record of a
  real but mistargeted effort. Its *methodology* — mutation-based identity
  stability testing (I1), repo-cadence-derived persistence thresholds
  (I2), suppression-strategy comparison (I3), deterministic-remediation
  proof discipline (I7/I9) — is reused for this design's new signal
  domain (§10). Its *conclusions* (QualityFinding identity behavior,
  GitHub write-authority topology comparison, SARIF/issue reporting
  surfaces) targeted the wrong subject and do not carry over.

## 4. Architecture overview

```
tools/quality_coordination.py       (AQC itself — new)
       |
       |-- signal gatherers (§6): branch/PR/CI, ledger health,
       |     process hygiene, docs/ROADMAP data feed
       |-- app-report client (§6.4): reads the trading app's existing
       |     read-only diagnostics as CONTEXT, not as audited findings
       v
tools/coordination_engine.py        (new, shared, signal-shape-agnostic)
       |  identity + fingerprint dedupe + persistence-floor escalation
       |  + suppression + resolution state machine
       v
tools/quality_coordination_data/quality_coordination.db   (new SQLite store)


tools/quality_ratchet.py            (renamed from quality_coordination.py,
                                      PR #43's work, behavior unchanged)
tools/quality_ratchet_data/quality_ratchet.db             (renamed)
```

`coordination_engine.py` is written fresh for this design, modeled on the
state machine already proven in `quality_ratchet.py` (identity/fingerprint
dedupe, `FLOOR_HOURS`-based escalation floor, deburst counting, run-history
bookkeeping) but generalized past `QualityFinding` to an arbitrary
caller-supplied `Signal` (stable identity string, JSON-serializable
payload, a "still present" bool). `quality_ratchet.py` is **not** retrofit
onto it in this design (§2, §13) — the duplication this creates for one
implementation cycle is an accepted, explicitly-named trade-off, not an
oversight.

Two independent SQLite stores, one per tool, following this repo's
existing per-module persistence idiom (`CLAUDE.md`'s "Persistence idiom"
section) — no shared database.

## 5. Invocation model

Manual-only for v1, external and standalone per the repository's standing
"workflow/tooling and application code must never overlap" rule (`CLAUDE.md`):

```
python -m tools.quality_coordination            # detect + report only (default, safe)
python -m tools.quality_coordination --clean    # also executes any cleanup action
                                                 # the detect phase found eligible
```

This mirrors the existing `tools.project_manifest --check`/`--write`
convention already established in this repo. `--clean` always runs a fresh
detect pass first in the same invocation — it never acts on a stale or
separately-cached detection result. Scheduling (a cron job, a Woodpecker
scheduled pipeline — external to the trading application either way) is a
natural follow-on, explicitly deferred (§13): nothing here is wired into
`main.py`'s tick loop, `config/settings.yaml`, or any app-owned route, per
the standing rule and by design.

## 6. Signal domains

Each domain below produces zero or more `Signal` objects fed to
`coordination_engine.py`, **except** §6.4, which never produces a
`Signal` — it is read as context, not audited as a finding.

### 6.1 Branch / PR / CI lifecycle health

**Identity:** branch name.
**Payload:** last-commit age, open-PR state (via `gh pr list`/`gh pr
view`), Woodpecker status for the branch tip (via the existing
`scripts/woodpecker-status` mechanism), whether a corresponding
`.claude/worktrees/` directory still exists.
**"Still present":** branch still exists locally or on `origin`.
**Resolution:** branch deleted (locally and remotely) or its PR merged.
**Suppression candidates:** an open PR actively receiving commits/reviews;
an explicit "paused, not stalled" note for that track in
`active-tracks-board.md` (Program 7's own pause, recorded via PR #41, is
exactly the kind of state this must not misclassify as janitorial cruft).

### 6.2 Plan and ledger execution health

**Identity:** SDD ledger path (`.superpowers/sdd/<plan>/progress.md`) or
numbered-plan file path.
**Payload:** the ledger's last recorded task/round line, days since the
last commit touching that plan's associated branch.
**"Still present":** the ledger file still exists and its last recorded
state is not "complete."
**Resolution:** the ledger shows plan completion, or the plan's branch is
confirmed merged into `main`.

### 6.3 Standing-rule and process-hygiene compliance

**Identity:** a specific rule-instance key (e.g. a `baseline.json`
`accepted_finding_ids` entry lacking a dated `notes` addendum).
**Payload:** the structural check result (does a corresponding note exist
for this ID's prefix).
**"Still present":** the note is still missing on re-check.
This domain is largely a direct structural check rather than something
that needs a real persistence floor — it is still routed through the
engine for consistent history/reporting, not because escalation timing
matters much here.

### 6.4 Trading-application self-reported diagnostics (context, not a signal)

AQC calls the application's own existing read-only diagnostics —
`GET /api/quality/summary`, `GET /api/health/pipeline`,
`GET /api/health/faults` — exactly as any other external HTTP client
would (this is the one-directional "tool reads from app via a real API"
relationship the standing coupling rule explicitly permits; the app never
knows AQC exists). The response is used as **context** for judging or
suppressing signals from §6.1-§6.3, e.g.:
- Suppress a "this scheduler-adjacent branch looks abandoned" false
  positive if `/api/health/pipeline` shows the relevant scheduler firing
  normally.
- Annotate a stale-branch finding with "the app currently reports N active
  findings this branch's PR would have addressed" for a human's judgment,
  without asserting that the branch caused or must fix them.

**Required application-side change (discovered during this brainstorm,
not assumed away):** all three routes are `/api/*` paths, and
`services/auth.py`'s `AuthMiddleware` gates every `/api/*` path behind a
session **except** the ones listed in `PUBLIC_PATHS` (currently `/login`,
`/auth/login`, `/auth/callback`) whenever `auth_configured()` is true. It
happens to work unmodified today only because no Google OAuth credentials
are configured in this dev environment. Since the standing production
goal is to eventually turn real auth on, this design **requires** one
narrow application-side change, decided explicitly by the user (not
assumed by this design, since it touches auth/security policy — a
protected domain):

> Add `/api/quality/summary`, `/api/health/pipeline`, and
> `/api/health/faults` to `services/auth.py`'s `PUBLIC_PATHS`.

This is a deliberate, narrow exception for three already-read-only,
non-sensitive operational-diagnostics routes (finding counts, scheduler
timing, fault history — no trading data, no credentials, no order/position
detail). It does not touch the trading gate, kill switch, CORS policy, or
any other route's auth requirement, and is trivially reversible (remove
the three paths from the set again). It does mean these three routes
become reachable by anyone who can reach the app without a session at
all, even after real auth is configured for everything else — accepted
here as a proportionate trade-off for read-only operational diagnostics,
not a precedent for widening `PUBLIC_PATHS` further without its own
separate justification. This is the one and only application-code change
this entire design requires; every other signal domain (§6.1-§6.3, §6.5)
and the cleanup action layer (§8) touch only `tools/`, git, and the
filesystem.

### 6.5 Documentation / ROADMAP drift (data feed, not a finding)

**Not run through the coordination engine.** AQC gathers the raw
comparison — `ROADMAP.md`'s currently-open (`- [ ]`) items alongside
recently merged commits/PRs touching related paths — and surfaces it
as-is. Whether an item is actually done is a judgment call left to a human
or a Claude session reading the feed; AQC never asserts "this item is
stale" or "this item is done."

## 7. Coordination engine contract

```python
@dataclass
class Signal:
    identity: str            # stable key within its domain (§6.1-§6.3)
    domain: str               # "branch" | "ledger" | "process_hygiene"
    payload: dict             # JSON-serializable, feeds the fingerprint
    still_present: bool

def apply_observation(
    conn: sqlite3.Connection,
    signals: list[Signal],
    at: datetime,
) -> dict[str, str]:
    """Returns {identity: state} where state is one of:
    new | observed | escalation_eligible | suppressed | resolved."""
```

This is the same identity/fingerprint-dedupe/persistence-floor/
suppression/resolution contract `quality_ratchet.py` already implements
and already had one real bug fixed in (the fingerprint short-circuit that
defeated escalation for a stable, persisting finding) — reused here
deliberately rather than re-derived, per §3.

## 8. Cleanup / janitor action layer

Detect and act are always separate phases (§5). Each action below is
deterministic, idempotent, path-contained, and independently verifiable —
the same bar `.claude/rules/autonomous-quality-coordination-evidence.md`'s
"Remediation authority rule" sets for any autonomous action, generalized
from GitHub-write to git/filesystem-write (§3).

1. **`git worktree prune`** — precondition: none beyond git's own
   built-in safety. This command only reconciles worktree bookkeeping
   against worktrees already removed from disk; it cannot destroy
   anything that still exists.
2. **Delete a local branch, merged and remote-deleted** — precondition:
   (a) `git merge-base --is-ancestor <branch> main` succeeds, (b) `git
   fetch --prune` confirms no remaining `origin/<branch>` ref, and (c) the
   branch is not currently checked out in any worktree. Executed via `git
   branch -d` (never `-D`), so git's own merge-check is an independent
   second guard beyond (a). Never targets `main` under any condition.
3. **Delete a finished plan's SDD scratch workspace** — precondition: the
   plan's associated branch (mapping derived from the ledger's own first
   line plus this repo's branch-naming convention — the exact derivation
   logic is an implementation-time detail, not resolved in this spec) is
   confirmed merged into `main`. Only removes the gitignored
   `.superpowers/sdd/<plan>/` scratch directory — no git history or
   tracked source is affected.

Every executed action is logged into AQC's own SQLite store — identity,
action type, timestamp, outcome, and whether it ran in `--clean` or was
merely reported as eligible — before/after state for auditability.
Widening this allowlist beyond these three is explicitly deferred (§13),
matching AEM's own §7 posture on its merge-allowlist ("each extension is
its own explicit decision").

## 9. Data model

`tools/quality_coordination_data/quality_coordination.db`, following this
repo's persistence idiom (`DB_PATH` under the tool's own directory,
`CREATE TABLE IF NOT EXISTS`, additive schema changes only):

```sql
CREATE TABLE signal_state (
    identity TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    state TEXT NOT NULL,               -- new|observed|escalation_eligible|suppressed|resolved
    fingerprint TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 1,
    explanation TEXT                    -- human-readable why-this-state, per run
);

CREATE TABLE coordination_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    signals_observed INTEGER NOT NULL,
    signals_escalated INTEGER NOT NULL,
    error TEXT
);

CREATE TABLE cleanup_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL,
    action_type TEXT NOT NULL,          -- worktree_prune|delete_merged_branch|delete_sdd_scratch
    executed_at TEXT NOT NULL,
    dry_run INTEGER NOT NULL,           -- 0 if --clean actually executed it, 1 if detect-only reported it
    outcome TEXT NOT NULL               -- succeeded|refused:<reason>|failed:<error>
);
```

## 10. Verification approach (folded into the implementation plan, not a separate investigation)

Per §3's reuse of the original investigation's methodology, scoped down to
only what's genuinely new here (no GitHub credential/event topology, no
SARIF/issue surface — none of that applies):

1. **Cadence measurement** for the new signal domains (branch lifetimes,
   PR merge times, SDD ledger completion times) against this repo's real
   `git`/`gh` history, before any staleness threshold is hardcoded — the
   evidence rule's "No guessed quarantine period" applies here exactly as
   it did for the original investigation's I2.
2. **Identity-stability check** for the new signal identities (branch
   names, ledger paths, rule-instance keys) — expected to be materially
   simpler than `QualityFinding`'s line-sensitive IDs (a branch name
   doesn't shift when code changes), but verified, not assumed.
3. **Fault-injection proof** for the three cleanup actions specifically,
   run against a disposable synthetic git repository fixture — **never
   this repository** — covering: idempotence (running twice produces no
   second effect), refusal on each stated precondition failure, no-op
   against a branch checked out in any worktree, and no-op against `main`.
   This is higher-stakes than anything the original investigation ever
   activated: it is the first time this initiative ships real mutating
   authority rather than designing one that stays dormant.
4. Tests redirect any `DB_PATH` via `monkeypatch` to an isolated tmp path,
   per this repo's existing universal test convention — no test ever
   touches a real `data/*.db` or, here, a real
   `tools/quality_coordination_data/` file.

## 11. Safety and protected-path rules

- Never treats `main` as a deletion target under any code path.
- Never prunes or deletes the worktree the current invocation is running
  from.
- All mutating actions are limited to git plumbing/porcelain commands with
  the explicit preconditions in §8, plus deletion of a single, clearly
  scoped, gitignored scratch directory (`.superpowers/sdd/<plan>/`) — no
  arbitrary filesystem writes anywhere else in the repository.
- `--clean` always re-runs detection in the same invocation; it never
  consumes a separately cached/stale detect result.
- AQC itself has no GitHub write authority, no network write capability
  beyond the read-only app-diagnostics calls in §6.4, and no code path
  that touches `services/`, `main.py`, `config/settings.yaml`, or any
  trading-application file.

## 12. Disposition of `tools/quality_coordination.py` (PR #43)

Renamed, on its existing branch (`feat/autonomous-quality-coordination`),
as its own follow-up task, independent of this design:
- `tools/quality_coordination.py` → `tools/quality_ratchet.py`
- `tools/quality_coordination_data/` → `tools/quality_ratchet_data/`
- Internal identifiers, docstrings, and test module names updated to
  match; no behavior change.
- `.claude/rules/quality-capabilities.md`'s `quality-coordination-
  observation` bullet updated to point at the new name.
- PR #43 merges under this corrected name; this design's implementation
  proceeds independently, on its own branch, once this spec is approved.

## 13. Explicitly deferred (separate decisions, not part of this design)

- **AEM issue-queue hookup.** Whether AQC's `escalation_eligible` signals
  ever get filed as GitHub Issues for Autonomous Engineering Mode to
  claim is undecided — mirrors AEM's own §11 deferral. Revisit only once
  AQC's detection side has run for real and produced genuine escalations
  to decide about.
- **Scheduling.** Cron/Woodpecker-scheduled invocation is a natural
  follow-on to manual invocation, not designed here.
- **Retrofitting `quality_ratchet.py`** onto `coordination_engine.py` to
  eliminate the short-term duplication between the two tools' state
  machines. Accepted as-is for this implementation cycle (§4).
- **Widening the cleanup allowlist** beyond the three actions in §8 —
  each addition is its own explicit decision, matching AEM's own §7
  posture.
- **Resolving** documentation/ROADMAP drift — §6.5 surfaces data only.

## 14. Follow-up tasks (tracked here, executed during implementation)

- **Application-side change (the only one this design requires):** add
  `/api/quality/summary`, `/api/health/pipeline`, and `/api/health/faults`
  to `services/auth.py`'s `PUBLIC_PATHS` (§6.4). A one-line, explicitly
  user-approved change to the trading application itself — not covered by
  the "workflow/tooling and application code must never overlap" rule,
  since that rule governs *application dependence on tooling*, not an
  application security-policy change made to enable a tool's read access.
- Update `.claude/rules/autonomous-quality-coordination-evidence.md` to
  generalize its framing from GitHub-write authority specifically to
  local-mutation authority in general, so it correctly governs §8's
  cleanup actions rather than reading as GitHub-specific.
- Update `.claude/rules/quality-capabilities.md`'s AQC-related bullets to
  reflect this corrected scope and the `quality_ratchet` rename.
- Update `CLAUDE.md`'s references to AQC/quality coordination to point at
  this design rather than the original (now-superseded-in-scope)
  investigation, without deleting the historical record of the original
  investigation (struck through, not deleted, per this repo's established
  documentation-correction convention).

## 15. Self-review

**Placeholder scan:** no TODO/TBD left unresolved. The SDD-scratch branch-
mapping derivation (§8, action 3) is explicitly named as an
implementation-time detail rather than left as an accidental gap.

**Internal consistency:** §4's architecture diagram, §6's signal domains,
§7's engine contract, and §8's action layer all reference the same
`Signal`/identity/state vocabulary consistently.

**Scope check:** deliberately narrow — four signal domains (one of which,
§6.5, is explicitly a data feed rather than a finding type), three
cleanup actions, no GitHub write authority, no AEM integration. §13 names
exactly what's out of scope and why.

**Ambiguity check:** the one place two readings were possible — whether
`quality_ratchet.py` gets retrofitted onto the shared engine as part of
this work — is resolved explicitly in §4 and §13: not in this cycle, by
deliberate choice, not oversight.
