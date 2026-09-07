# Autonomous Quality Coordination Investigation — Final Verification (I13)

**Task:** I13 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
— the investigation's final task.
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `c73371e`.
Re-grounded: synced with `origin/main`, no open PRs, no other active branch touching any file this
investigation owns.

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live runtime.

---

## 1. Evidence checklist re-run

### 1.1 Design spec §14 — the 8 "genuinely open" research questions

| # | Question | Answer | Task |
|---|---|---|---|
| 1 | What fraction of QCP findings have durable enough identity for external state? | Semantic rules (10 of 16) are durable as-is; 4 location-sensitive rules need 2 scanner-side additions this investigation deliberately deferred (I11 §3); 2 are aggregate/inventory (no external-state role) | I1 |
| 2 | How often would active-work overlap have suppressed a valid escalation in recent history? | Once, in the only durable specimen (episode C) — suppressed correctly, then resolved on merge | I2/I3, replayed executably in I8 |
| 3 | Are exact finding claims worth their manual/agent metadata overhead? | No — 0% historical coverage; `derive_claims()` is a named, deferred extension point (I11 §4), not built | I3 |
| 4 | Is local session/worktree advisory state useful enough to build? | No — ≤2.3-minute real coverage window measured; rejected | I3 |
| 5 | Which control-plane topology minimizes long-lived secrets/operational burden? | D (report-only) now; C (GitHub-native, App-less) is the least-privileged write-lane shape if ever justified later | I4, decided I10 |
| 6 | Which finding types gain value from SARIF vs. existing CI output? | Class-1 source-located static findings only, and even that stays a candidate pending a real-upload dry run this investigation deliberately did not perform (I5 §9, I10 §2 item 2) | I5 |
| 7 | Which deterministic generators are truly idempotent/path-contained? | Exactly one, `tools/project_manifest.py --write`, proven under 3 wrapper conditions the tool itself doesn't enforce | I7 |
| 8 | What staged activation proves value before granting write authority? | dry-run → reporting only, activated now; issue/draft-PR require a future decision citing new evidence, not a timer | I10 §2, I11, I12 |

All 8 answered with a task citation, none left as a bare "TBD."

### 1.2 Design spec §15 — the 11 required investigation outputs

Measured concurrency/cadence report (I2) · full QCP identity/automation-suitability matrix (I1) ·
active-work strategy experiment matrix (I3) · platform credential/event research record with
official citations (I4/I9, both citing `woodpecker-ci.org` fetched live) · reporting-surface
decision (I5) · threat model (I6, 11 threats / 8 vetoes) · deterministic-remediation candidate
inventory (I7) · no-write coordinator simulator with fault results (I8, 15 mutation-tested
scenarios) · architecture decision record with adversarial review (I10) · production design
specification (I11) · production implementation plan written only after the architecture was
chosen (I12, dated 2026-08-26, one day after I10's 2026-08-25 decision). **All 11 present.**

### 1.3 Evidence rule's "Investigation completion rule" — all 10 bullets

Every bullet in `.claude/rules/autonomous-quality-coordination-evidence.md`'s closing checklist maps
onto the same task set above one-to-one, plus: "rejected alternatives have evidence-backed reasons"
— Candidate B dominated at I4 (every secretless variant degenerates into C, every secretful variant
inherits A's weak edge without A's simplicity); Candidate A carries two open vetoes (V1/V2) unless
four external controls are added, never fully closed in this investigation because A was never
selected. Nothing here required inventing a new answer for this task — re-running the checklist
against the already-written record is what task actually verifying.

## 2. Integration audit — no production runtime/CI write behavior enabled [E1/E2]

Full branch diff against `origin/main`:

```
29 files changed, 7791 insertions(+), 9 deletions(-)
```

`git diff --stat origin/main...HEAD -- data/ .env config/ .woodpecker/ .github/ main.py services/`
returns **empty** — zero touches to any production runtime file, CI configuration, secret file, or
live data file, across the entire investigation. The only non-doc code is
`tools/quality_coordination_sim/` (explicitly EXPERIMENTAL/THROWAWAY, no import from `main.py` or
any production module) and its tests, plus two pre-existing test-timing fixes
(`tests/test_risk_manager.py`, `tests/test_shadow_mode.py`, from I0/I1, unrelated to the
coordination feature itself — daily-loss-rollover tests pinned to the risk manager's own seeded UTC
day instead of wall-clock `time.time()`, fixing a real midnight-boundary flake).

A full-diff grep for credential-shaped strings (`ghp_`, `github_pat_`, `api[_-]?key`, `secret`,
`password`, `token\s*=`) turned up only analytical prose *about* credential/secret policy — the
investigation's actual subject matter — never a real value. No `.db` or `.env` path appears
anywhere in the diff stat.

**Local worktree housekeeping (not a repo/git finding):** `build/i7-tree/` and
`build/midnight_repro.py` were found sitting in this worktree's filesystem, leftover scratch
artifacts from an earlier pre-compaction I7 pass — both `git check-ignore`-confirmed inside
`build/` (`.gitignore` line 29), so neither was ever staged or reachable by any commit, and neither
would exist in a fresh Woodpecker clone. Removed except two root-owned `.pyc` bytecode cache files
(`build/i7-tree/tools/__pycache__/*.pyc`, permission-denied) — compiled cache, not source, gitignored,
no security or correctness implication; left in place rather than escalating privileges, matching
this session's own earlier precedent for an equivalent root-owned artifact.

## 3. QCP baseline debt review [E3, live]

`tools/quality_audit/baseline.json`: **untouched** by this branch (`git diff --stat
origin/main...HEAD -- tools/quality_audit/baseline.json` empty). A live re-run after the worktree
cleanup above:

```
quality-audit: 0 new, 193 existing (baselined), 0 resolved
```

Zero new findings — the investigation's own new files (`tools/quality_coordination_sim/`, its
tests, the two test fixes) trip nothing. `tools/project_manifest.py --check` reports drift within
the existing 10% tolerance on every measured field (`files.python` +2.6%, `lines.python` +1.5%,
`tests.count` +1.6%, `tests.files` +3.1%) — all attributable to this investigation's own added
files, no regeneration required per that tool's own documented tolerance policy. No baseline edit
of any kind occurred anywhere in this investigation, so there is no possibility this investigation
hid a real error by baselining it away.

## 4. Final verification proportional to changed code

Every commit in this investigation (I0 through I12) was pushed and independently verified green on
Woodpecker's real push contexts before the next task began — not a claim, a matter of record: every
push in this branch's history shows 5/5 required contexts (`tests-pytest`,
`tests-dependency-audit`, `quality-architecture-audit`, `quality-browser-e2e`,
`kalshi-contract-fixtures`) at `success`, checked via `gh api
repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status` after each push throughout this session.
The two runnable-code prototypes (I8's `Coordinator`, I9's `WriteGate`) each received a deliberate
mutation-injection proof beyond ordinary green tests — a real invariant disabled, the test suite
re-run and confirmed to catch it for the right reason, then restored — satisfying `final-
verification`'s fault-injection requirement without needing a fresh pass here, since nothing in I13
changes any of that code.

This task's own changes (ROADMAP.md, `.claude/rules/quality-capabilities.md`, this doc) are
docs-only — no test run is applicable beyond the CI push this task itself triggers (§6).

## 5. Final fresh-eyes review

- **Credential assumptions:** D activates zero credentials. Every doc that ever discusses what a
  future write lane *would* need (I4 §3 Candidate C, I9's `write_gate.py`/`when_filter.py`, I11 §7)
  is consistently marked not-provisioned-now, cross-checked here one more time — no doc anywhere in
  the final tree claims a credential exists or is pre-staged.
- **Event filters:** I9's `matches()` evaluator and its real-pipeline-validated conclusion (`branch:
  main` alone matches a `pull_request` targeting `main`; `event: push, branch: main` correctly
  excludes it) is cited consistently by I10 §1 update #2 and I11 §7 — no contradicting claim found
  anywhere else in the tree.
- **Active-work handling:** I3 §11's precedence (exact claim → path-overlap → persistence floor →
  escalation-eligible, never min) is the literal implementation in I8's `Coordinator._evaluate` and
  I11 §4/I12 Task 3's SQLite port — same precedence order, same "merged excluded, staleness-bounded"
  rule, checked line-by-line against I3's table one more time for this review; no drift found.
- **Stable identity:** I1 §9's `automation_key` contract, I11 §3's externally-computed variant (with
  its two named fallbacks), and I12 Task 2's test suite all agree on the same
  `<check>|<scope-without-line>|<subject>` shape — re-checked the three documents side by side, no
  inconsistency.
- **Protected paths:** I7's denylist (`tools/quality_audit/`, `.woodpecker/`, `.github/`, `.claude/`,
  `services/`, `config/`, `main.py`, ...), I6's veto V7, and I11 §8's "one write path, structurally"
  design are consistent — I11's module has no capability to touch any denylisted path at all, which
  is a strictly stronger guarantee than enforcing a denylist against a capability that exists.
- **Outage semantics:** I11 §10 (GitHub read failure → `branches=[]`, degrade to floor-only
  evaluation, run still completes and records `error`) and I12 Task 5's test
  (`test_fetch_branch_signals_degrades_to_empty_list_on_network_error`) implement the identical
  contract — checked against each other, matching.
- **Rollout gates:** I10 §2 item 4 (any future write-stage activation must re-run I2's cadence
  replay against then-current `main`, not cite this investigation's numbers secondhand) is now
  additionally reflected in the ROADMAP.md entry added by this task (§6 below), so it survives even
  outside the research-doc tree, in the one place a future session is most likely to look first for
  "what's next."

No contradiction, drift, or silently-abandoned claim found across the final tree.

## 6. ROADMAP / capability-router sync

- `ROADMAP.md`: added one P4 ("nice-to-haves") entry pointing at the I12 plan, stating plainly that
  the persisted observation series is designed and planned but not implemented, and that report-only
  was a deliberate, evidence-backed conclusion rather than a stopping point reached by default.
- `.claude/rules/quality-capabilities.md`: the investigation's own router entry (added at I0) updated
  from "investigating (not implementing)" to **complete**, now pointing at the decision record and
  the spec/plan pair for whoever picks up implementation later — no bespoke orchestrator skill is
  needed for that future work (I12's plan uses the generic `superpowers:executing-plans`/
  `subagent-driven-development`, unlike this investigation, which needed the custom evidence-
  discipline orchestrator this rule file pointed at).
- `static/status.html`: **not touched**, on confirmed precedent — the comparable prior investigation
  (`chore/realtime-dp-investigation`, PR #12, also research/spec/plan-only with no shipped
  production code) received no `status.html` phase entry either (`grep` confirms zero mentions).
  `status.html` tracks shipped build phases; this investigation shipped a decision and a plan, not
  running code, matching that precedent rather than inventing a new one.

No parallel branch owns any of these three files at task time (confirmed via `gh pr list --state
open` and `git branch -r`, both empty/stale as recorded in every prior task's re-grounding step).
