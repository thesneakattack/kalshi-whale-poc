# Active-Work Suppression Matrix — I3 (detection and suppression strategies)

**Task:** I3 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `39f0655` (worktree
`.claude/worktrees/aqc-investigation`, base `origin/main` @ `8d1796b`). Re-ground 02:12Z: no open PRs;
`origin/chore/realtime-dp-investigation` at `4cb0f0b` ("regenerate project manifest from a clean
checkout" — it now also touches `static/project-manifest.json`, the I7 candidate; nothing this branch
owns).

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live API/runtime · **[E5]** upstream docs · **[E6]** inference.

---

## 1. Question and falsification criteria

**Question.** Which active-work signals should delay escalation of a persistent `main` finding, in what
precedence, with what false-positive/false-negative behaviour — and does a local Claude session/worktree
registry add anything a remote coordinator cannot get from GitHub state?

A signal earns a place only if, over the scenario matrix, it (a) suppresses when work on the finding is
genuinely in flight, (b) does **not** suppress when it is not (a "blind delay"), and (c) never claims
resolution. A signal that scores well only on scenarios this repository never produces (I2) is
demoted, not adopted.

## 2. Method

An executable scenario matrix (Appendix A, ~170 lines, pure Python, no network): each strategy is a
function from a synthetic snapshot (finding, PRs with state/draft/body/files, remote branches with
tip age/first-commit age/merged flag, a local registry, a clock) to `SUPPRESS` or `ELIGIBLE`. A
separate `audit()` step is the **only** function allowed to return `RESOLVED`; the harness asserts no
strategy ever does. Thresholds are the I2 §9 candidate values (persistence floor 6 h, branch idle
expiry 3 h, hard cap 24 h, local TTL 1 h) as *parameters*, not decisions.

Scenarios: the plan's eleven, plus two from this repository's own history — the merged-but-undeleted
branch (I2 §8) and I2's episode B replayed at +3 h / +7 h / +30 h. Truth labels ("work in flight?")
are stated per scenario in the table so a reviewer can dispute them individually.

Evidence gathered before scoring:
- **GitHub API surface** [E4]: a live GraphQL read on PR #11 returned `isDraft`, `state`, `headRefName`,
  `files`, `closingIssuesReferences{totalCount}`; schema introspection confirms `Issue.
  closedByPullRequestsReferences`, `Issue.linkedBranches`, `Issue.timelineItems`, `PullRequest.
  closingIssuesReferences`, `PullRequest.timelineItems`. Everything an exact-claim or path-overlap
  reader needs exists, read-only, under the user token already in use.
- **GitHub linking semantics** [E5] (docs.github.com "Linking a pull request to an issue"): closing
  keywords `close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved` in the PR description or a
  commit message; manual linking from either sidebar; **keywords are honoured only for PRs targeting the
  default branch** ("If the pull request targets any other branch … no links are created"); up to ten
  issues per PR; draft behaviour and link-creation timing not stated on that page.
- **Claude Code hooks** [E5] (code.claude.com/docs/en/hooks): 31 events including `SessionStart`
  (matcher `startup|resume|clear|compact|fork`), `SessionEnd` (matcher `clear|resume|logout|
  prompt_input_exit|other`), `WorktreeCreate` (`worktree_path`, `worktree_name`, `branch`; non-zero exit
  aborts creation), `WorktreeRemove` (fires at session exit, subagent finish, or manual deletion; exit
  ignored), `SubagentStart/Stop` (`agent_id`, `agent_type`). All are shell commands with filesystem
  access. **"SessionEnd is NOT guaranteed to fire"** on crash/kill — the docs say not to rely on it for
  cleanup. Project hooks live in `.claude/settings.json` (committable); hooks merge across levels.
- **Commit→push latency in this repo** [E3] (Appendix B; every one of the 105 push pipelines matched to
  its commit's committer time): median **0.1 min**, p90 0.2 min, **max 2.3 min**; 105/105 pushed within
  5 minutes. The window during which work exists locally but not on `origin` — the only window a local
  registry could cover for a remote coordinator — has never exceeded 2.3 minutes here.
- I2's cadence facts: PRs open a median 3 min before merging (31 % of branch-life coverage outside
  PR #3, 0 % of current work); one merged-but-undeleted branch; transitional lifetimes ≤ 0.23 h.

## 3. Signals evaluated

| id | signal | reads | implementation surface | cost |
|---|---|---|---|---|
| **A** | exact claim: open PR body carries `quality-claim: <automation_key>` (or, once findings are issues, a closing keyword → `closingIssuesReferences`) | PR body / linked issues | GraphQL, one query per finding path or one bulk query | needs authors (human or Claude) to write the marker; zero today |
| **B** | open-PR changed-path overlap (drafts included) | `PullRequest.files`, `isDraft`, `state` | GraphQL | free; but PRs barely exist while work is in flight here |
| **C** | live remote-branch changed-path overlap: not merged into `main`, tip younger than idle expiry, first commit younger than hard cap | `git ls-remote` + `git diff --name-only main...branch` + `git branch -r --merged` | git only, no GitHub API | free; matches how this repo actually works |
| C′ | C without the merged-branch exclusion | same | same | control for S12 |
| **D** | directory overlap (PR or live branch touches a sibling file) | as B/C | same | free |
| **E** | persistence-only floor (control): eligible once age ≥ T | clock + first observation | none | free |
| **F** | local advisory registry written by Claude hooks | `~/.claude/...` or `.claude/...` JSON | hooks + TTL cleanup | new moving part; invisible to remote CI |
| **G** | combined precedence A > (B ∨ C) > E; D excluded; F excluded | all of the above | union | – |

## 4. Scenario matrix (harness output, verbatim) [E3]

`✗fs` = false suppression (suppressed, nothing in flight — a blind delay); `✗fe` = false eligible
(escalation-eligible while work is in flight — duplicate-work risk); blank = resolution decided by the
audit, strategy output moot.

| scenario | work in flight? | audit | A claim | B PR path | C branch path | C' branch (no merged excl.) | D dir overlap | E persist 6h | F local reg. | G combined |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 exact claim in open PR | yes | PRESENT | SUPPRESS ✓ | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ |
| S2 same-path unrelated open PR | yes | PRESENT | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ |
| S3 directory overlap only | no | PRESENT | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | SUPPRESS ✗fs | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ |
| S4 remote branch, no PR | yes | PRESENT | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ | SUPPRESS ✓ | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ |
| S5 draft PR touches path | yes | PRESENT | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ |
| S6 stale branch (idle 30h) | no | PRESENT | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ |
| S7 PR closed unmerged, branch gone | no | PRESENT | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ |
| S8 merge fixes finding | no | RESOLVED | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| S9 merge does not fix | no | PRESENT | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ |
| S10 local unpushed work | yes | PRESENT | ELIGIBLE ✗fe | ELIGIBLE ✗fe | ELIGIBLE ✗fe | ELIGIBLE ✗fe | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe |
| S11 two PRs overlap, one merged | yes | PRESENT | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ |
| S12 merged-but-undeleted branch (I2 §8) | no | PRESENT | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | SUPPRESS ✗fs | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ |
| S13a episode B at +3h (no signal yet) | no | PRESENT | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | SUPPRESS ✗fs | ELIGIBLE ✓ | SUPPRESS ✗fs |
| S13b episode B at +7h (branch pushed at +5.4h) | yes | PRESENT | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ | SUPPRESS ✓ | SUPPRESS ✓ | ELIGIBLE ✗fe | ELIGIBLE ✗fe | SUPPRESS ✓ |
| S13c episode B at +30h, branch idle 20h | no | PRESENT | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ | ELIGIBLE ✓ |

| strategy | false suppressions (blind delay) | false eligibles (duplicate-work risk) |
|---|---|---|
| A claim | 0 | 6 |
| B PR path | 0 | 3 |
| C branch path | 0 | 5 |
| C' branch (no merged excl.) | 1 | 5 |
| D dir overlap | 1 | 1 |
| E persist 6h | 1 | 7 |
| F local reg. | 0 | 6 |
| G combined | 1 | 1 |

Invariant held: no strategy emitted `RESOLVED`; S8 resolved only through the audit step.

## 5. Reading the scores — two kinds of "false suppression"

D (directory overlap) and G (combined) both score 1 / 1, and that equality is misleading:

- G's false suppression is **S13a**: the persistence floor holding an 3-hour-old finding back because
  no signal has appeared yet. It is bounded by `T` (6 h here) and is the delay I2 priced deliberately.
- D's false suppression is **S3**: a PR touching an unrelated sibling file silences the finding **for as
  long as that PR stays open** — unbounded, and invisible to the finding's eventual owner. That is the
  "blind delay" the spec (§7) warns about.

The same asymmetry decides the false-eligible column: G's only miss is **S10** (unpushed local work), and
§9 shows that window has measured ≤ 2.3 min in this repo; D's only miss is also S10. So D buys nothing
over G and adds an unbounded blind spot. **Excluded.**

## 6. Signal A — exact claims

- **Behaviour:** the only signal that distinguishes "this PR is *about* this finding" (S1) from "this
  PR happens to touch the file" (S2). Zero false suppressions in every scenario.
- **Coverage:** zero in this repository today — no PR body has ever carried a finding reference, and
  there are no issues to link. Six false-eligibles in the matrix are all "nobody wrote a claim".
- **Mechanism options** (both read-only under the existing token):
  1. a structured marker in the PR body, `quality-claim: <automation_key>` (I1's key) — works before
     any issue exists, trivially parseable, needs the I1 identity contract;
  2. GitHub-native linking once a finding has become an issue: `Fixes #N` in the body → readable as
     `PullRequest.closingIssuesReferences` / `Issue.closedByPullRequestsReferences`; also
     `Issue.linkedBranches` for a branch linked from the issue sidebar without a PR. Note the docs'
     default-branch rule — linking only fires for PRs targeting `main`, which matches this repo's
     "main is truth" policy exactly.
- **Verdict:** highest precedence when present; must never be the *only* signal (coverage).

## 7. Signal B — open-PR path overlap

- Correct on S1, S2, S5 (draft counts as active), S11 (a second open PR keeps suppressing after the
  first merges without fixing). Zero blind delays.
- Misses S4 and S13b — **the dominant real case in this repository**, where the fix sits on a pushed
  branch for hours before a PR exists (I2 §3: PRs open a median 3 minutes before merge).
- Cheap (one GraphQL query) and complementary to C; keep it, but as a peer of C, not above it.

## 8. Signal C — live remote-branch path overlap, with staleness

- Correct on S4, S13b (suppression begins the moment the fix is pushed, 5.4 h into episode B), S6
  (idle 30 h → expired), S13c (idle 20 h → expired), and S12 **only with the merged-branch exclusion**
  (C′ false-suppresses on the real merged-but-undeleted `chore/realtime-data-plane-investigation`).
- Misses S1/S2/S5/S11 only because those scenarios put the work in a PR whose branch the harness did
  not also list; in reality every open PR *has* a live branch, so B ∪ C covers both. The harness keeps
  them separate to show what each signal contributes alone.
- Two parameters, both derived in I2 §9: **idle expiry** (candidate 3 h ≈ 2× the longest idle gap inside
  a live branch) and a **hard cap** (candidate 24 h ≈ 2× the longest observed branch life) so a
  forgotten branch cannot suppress indefinitely. Expiry re-opens escalation; it does not resolve.
- Reads git only (`ls-remote`, `diff --name-only main...branch`, `branch -r --merged`) — no GitHub API,
  no token beyond clone access. That is the cheapest possible signal and the one this repo actually
  emits while work is in flight.

## 9. Signal F — local advisory registry (Claude hooks), and why not to build it

**Paper prototype** (what it would take): a `SessionStart`/`WorktreeCreate` hook appends
`{session_id, cwd, branch, started}` to a per-project registry file; `UserPromptSubmit` heartbeats
`last_seen`; `SessionEnd`/`WorktreeRemove` remove the entry. Because the docs state `SessionEnd` is not
guaranteed to fire, entries must expire on heartbeat age (TTL) and readers must tolerate stale rows —
which is another cleanup path to test and operate.

**What it could cover:** exactly S10 — work committed or edited locally and not yet on `origin`. A
remote coordinator (Woodpecker, a GitHub-native workflow) cannot read it at all unless it is published,
and the moment it is published by pushing the branch, signal C already exists.

**Measured size of that window:** median 0.1 min, max 2.3 min over 105 pushes (Appendix B). Every
persistence floor under consideration (≥ 2 h) is ≥ 50× larger than the worst observed unpushed window,
so the floor already absorbs S10 for the remote coordinator without any registry.

**The one thing it would genuinely help:** two *local* Claude sessions colliding on the same checkout —
which is a workspace-ownership problem this repo already mitigates with worktrees (memory note
`use-scratchpad-worktree-when-checkout-busy`, applied in I0), not a finding-escalation problem.

**Verdict: no registry.** YAGNI wins on measured evidence. Re-open only if commit→push latency ever grows
past the persistence floor (i.e. hours of unpushed work become normal), which would show up in the I2
measurement when re-run.

## 10. Signal E — persistence floor as the control

Alone it cannot see work (7 false-eligibles) and it is the one thing that suppresses S13a. That is the
correct division of labour: A/B/C answer "is someone on it?", E answers "has it been around long enough
to be worth anyone's attention?". E is the floor beneath the others, never a substitute for them.

## 11. Precedence and invariants (what the evidence supports)

```
1. exact claim (A)                       → SUPPRESS (strongest; zero blind delays)
2. open-PR path overlap (B)  ∪  live-branch path overlap (C, merged excluded, idle/hard-cap expiry)
                                         → SUPPRESS ("someone is in the file"; bounded by expiry)
3. persistence floor (E)                 → SUPPRESS until age ≥ T (bounded by T)
4. otherwise                             → ESCALATION-ELIGIBLE
directory overlap (D): not a suppressor  — unbounded blind delay (S3)
local registry (F): not built            — ≤ 2.3-min window, remote-invisible
```

Invariants, asserted by the harness and required by the evidence rule:
- **Suppression never resolves.** Every strategy's range is `{SUPPRESS, ELIGIBLE}`; only a fresh audit of
  integrated `main` returns `RESOLVED` (S8), and S9 shows a merge that does not fix simply hands the
  finding back to the floor.
- **Expiry re-opens, it does not resolve** (S6, S13c).
- **Merged branches are not active work** (S12).
- **A `killed` pipeline is not an observation** (I2 §5) — carried forward unchanged.

Precedence between (2) and (3) is deliberate: a live signal should *extend* the wait, so escalation
time = max(floor, end of suppression), never min.

## 12. Rejected hypotheses and alternatives

- *Open PRs are the primary active-work signal.* Rejected for this repository (I2 §3, S4/S13b): the
  pushed branch is.
- *Directory/scope overlap is a useful weak signal.* Rejected: its only unique effect is an unbounded
  blind delay (S3).
- *A local session/worktree registry is worth building now.* Rejected on measurement (§9); design
  recorded so the decision can be revisited with data, not re-argued.
- *Exact claims can carry the system alone.* Rejected: zero coverage today; it is a precedence override,
  not a foundation.
- *Branch-overlap without merged-exclusion.* Rejected by the live S12 specimen.

## 13. Bounded unknowns

- Draft PRs and GitHub's closing-keyword linking: the docs page fetched does not say whether links are
  created for drafts or at open time vs merge; the matrix treats a draft as active work via path overlap
  (B), which does not depend on linking. Verify in I5 if issue-linking becomes the claim mechanism.
- Truth labels are the author's; S2's "yes" (an unrelated PR touching the file counts as work) is the
  most debatable — flipping it to "no" turns B and G's S2 into a bounded false suppression (the PR's own
  lifetime, median 0.14 h here) and changes no conclusion.
- The scenario set is synthetic plus two historical specimens; I8's simulator should replay the same
  cases with recorded GitHub snapshots.

## 14. Handoff

**Next: I4 — compare event and credential control-plane topologies.** Inputs ready: every signal in
§11 is readable with git plus a read-only GitHub token (or none, for C); the "default branch only"
linking rule and the `branch: main`-matches-PRs pipeline evidence (I2 §7); and the I2 note that every
persistence floor assumes an observation occurs, which makes a scheduled `main` audit a topology
question.

---

## Appendix A — scenario matrix harness (verbatim)

```python
"""I3 active-work suppression scenario matrix. Pure, deterministic, no
network: each strategy is a function from a synthetic snapshot (finding,
PRs, branches, local registry, clock) to SUPPRESS or ELIGIBLE. Resolution
is NEVER a strategy output - a separate audit step is the only thing that
can say RESOLVED, and the matrix asserts that invariant.

Scenarios S1-S13 are the plan's list plus two drawn from this repo's own
history (the merged-but-undeleted branch, and I2 episode B replayed as a
time series). Thresholds used here are the I2 candidate values and are
parameters, not decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

H = 3600.0
IDLE_EXPIRY_H = 3.0      # I2 §9 candidate: 2x longest observed live-branch idle gap
HARD_CAP_H = 24.0        # I2 §9 candidate: 2x longest observed branch life
PERSIST_T_H = 6.0        # I2 §9 candidate floor (error/high band upper end)
LOCAL_TTL_H = 1.0


@dataclass
class Finding:
    key: str
    path: str
    first_seen: float          # seconds
    present_on_main: bool = True


@dataclass
class PR:
    number: int
    state: str                 # open | merged | closed
    files: set[str]
    draft: bool = False
    body: str = ""
    head: str = ""


@dataclass
class Branch:
    name: str
    files: set[str]
    tip_age_h: float           # hours since last commit at "now"
    first_commit_age_h: float
    merged: bool = False


@dataclass
class LocalEntry:
    session: str
    files: set[str]
    last_seen_age_h: float


@dataclass
class Snapshot:
    finding: Finding
    now: float
    prs: list[PR] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)
    local: list[LocalEntry] = field(default_factory=list)


# ---------------------------------------------------------------- strategies
def s_claim(s: Snapshot):
    for pr in s.prs:
        if pr.state == "open" and f"quality-claim: {s.finding.key}" in pr.body:
            return "SUPPRESS", f"PR #{pr.number} claims {s.finding.key}"
    return "ELIGIBLE", "no exact claim"


def s_pr_path(s: Snapshot):
    for pr in s.prs:
        if pr.state == "open" and s.finding.path in pr.files:
            return "SUPPRESS", f"open PR #{pr.number}{' (draft)' if pr.draft else ''} touches {s.finding.path}"
    return "ELIGIBLE", "no open PR touches the path"


def s_branch_path(s: Snapshot, exclude_merged=True):
    for b in s.branches:
        if exclude_merged and b.merged:
            continue
        if s.finding.path not in b.files:
            continue
        if b.tip_age_h > IDLE_EXPIRY_H:
            continue
        if b.first_commit_age_h > HARD_CAP_H:
            continue
        return "SUPPRESS", f"branch {b.name} touches path, tip {b.tip_age_h:.1f}h old"
    return "ELIGIBLE", "no live branch touches the path"


def s_branch_path_naive(s: Snapshot):
    return s_branch_path(s, exclude_merged=False)


def s_dir(s: Snapshot):
    d = str(PurePosixPath(s.finding.path).parent)
    for pr in s.prs:
        if pr.state == "open" and any(str(PurePosixPath(f).parent) == d for f in pr.files):
            return "SUPPRESS", f"PR #{pr.number} touches directory {d}"
    for b in s.branches:
        if not b.merged and b.tip_age_h <= IDLE_EXPIRY_H and any(str(PurePosixPath(f).parent) == d for f in b.files):
            return "SUPPRESS", f"branch {b.name} touches directory {d}"
    return "ELIGIBLE", "no directory overlap"


def s_persist(s: Snapshot):
    age_h = (s.now - s.finding.first_seen) / H
    if age_h < PERSIST_T_H:
        return "SUPPRESS", f"age {age_h:.1f}h < {PERSIST_T_H:.0f}h floor"
    return "ELIGIBLE", f"age {age_h:.1f}h >= floor"


def s_local(s: Snapshot):
    for e in s.local:
        if s.finding.path in e.files and e.last_seen_age_h <= LOCAL_TTL_H:
            return "SUPPRESS", f"local session {e.session} has the path open"
    return "ELIGIBLE", "no local registry entry"


def s_combined(s: Snapshot):
    """Precedence: exact claim > PR path / live branch path > persistence.
    Directory overlap deliberately NOT a suppressor. Local registry not
    visible to the remote coordinator."""
    for fn in (s_claim, s_pr_path, s_branch_path):
        d, why = fn(s)
        if d == "SUPPRESS":
            return d, why
    return s_persist(s)


STRATEGIES = [
    ("A claim", s_claim), ("B PR path", s_pr_path), ("C branch path", s_branch_path),
    ("C' branch (no merged excl.)", s_branch_path_naive), ("D dir overlap", s_dir),
    ("E persist 6h", s_persist), ("F local reg.", s_local), ("G combined", s_combined),
]


def audit(s: Snapshot):
    return "RESOLVED" if not s.finding.present_on_main else "PRESENT"


# ---------------------------------------------------------------- scenarios
P = "tools/quality_audit/baseline.json"
SIB = "tools/quality_audit/api_usage.py"
NOW = 100 * H


def f(age_h=8.0, present=True):
    return Finding("api-usage/inventory|account.create_order|", P, NOW - age_h * H, present)


SCENARIOS = [
    ("S1 exact claim in open PR", Snapshot(f(), NOW, prs=[PR(1, "open", {P}, body="quality-claim: api-usage/inventory|account.create_order|")]), True),
    ("S2 same-path unrelated open PR", Snapshot(f(), NOW, prs=[PR(2, "open", {P, "docs/x.md"})]), True),
    ("S3 directory overlap only", Snapshot(f(), NOW, prs=[PR(3, "open", {SIB})]), False),
    ("S4 remote branch, no PR", Snapshot(f(), NOW, branches=[Branch("chore/x", {P}, 0.5, 4.0)]), True),
    ("S5 draft PR touches path", Snapshot(f(), NOW, prs=[PR(5, "open", {P}, draft=True)]), True),
    ("S6 stale branch (idle 30h)", Snapshot(f(), NOW, branches=[Branch("old/x", {P}, 30.0, 40.0)]), False),
    ("S7 PR closed unmerged, branch gone", Snapshot(f(), NOW, prs=[PR(7, "closed", {P})]), False),
    ("S8 merge fixes finding", Snapshot(f(present=False), NOW, prs=[PR(8, "merged", {P})]), False),
    ("S9 merge does not fix", Snapshot(f(), NOW, prs=[PR(9, "merged", {P})]), False),
    ("S10 local unpushed work", Snapshot(f(), NOW, local=[LocalEntry("sess-a", {P}, 0.2)]), True),
    ("S11 two PRs overlap, one merged", Snapshot(f(), NOW, prs=[PR(11, "merged", {P}), PR(12, "open", {P})]), True),
    ("S12 merged-but-undeleted branch (I2 §8)", Snapshot(f(), NOW, branches=[Branch("chore/done", {P}, 0.5, 5.0, merged=True)]), False),
    ("S13a episode B at +3h (no signal yet)", Snapshot(f(age_h=3.0), NOW), False),
    ("S13b episode B at +7h (branch pushed at +5.4h)", Snapshot(f(age_h=7.0), NOW, branches=[Branch("chore/realtime-dp-investigation", {P}, 0.4, 5.4)]), True),
    ("S13c episode B at +30h, branch idle 20h", Snapshot(f(age_h=30.0), NOW, branches=[Branch("chore/realtime-dp-investigation", {P}, 20.0, 28.0)]), False),
]

# ---------------------------------------------------------------- run
names = [n for n, _ in STRATEGIES]
print("| scenario | work in flight? | audit | " + " | ".join(names) + " |")
print("|---|---|---|" + "---|" * len(names))
score = {n: {"false_suppress": 0, "false_eligible": 0} for n in names}
for label, snap, in_flight in SCENARIOS:
    cells = []
    a = audit(snap)
    for n, fn in STRATEGIES:
        d, why = fn(snap)
        assert d in ("SUPPRESS", "ELIGIBLE"), "strategies must never output RESOLVED"
        if a == "RESOLVED":
            mark = ""  # resolution is decided by the audit, strategy output moot
        elif d == "SUPPRESS" and not in_flight:
            score[n]["false_suppress"] += 1
            mark = " ✗fs"
        elif d == "ELIGIBLE" and in_flight:
            score[n]["false_eligible"] += 1
            mark = " ✗fe"
        else:
            mark = " ✓"
        cells.append(f"{d}{mark}")
    print(f"| {label} | {'yes' if in_flight else 'no'} | {a} | " + " | ".join(cells) + " |")
print()
print("| strategy | false suppressions (blind delay) | false eligibles (duplicate-work risk) |")
print("|---|---|---|")
for n in names:
    print(f"| {n} | {score[n]['false_suppress']} | {score[n]['false_eligible']} |")
print("\nInvariant held: no strategy ever emitted RESOLVED; S8 resolved only by the audit step.")
```

## Appendix B — commit→push latency (verbatim)

```python
"""Commit->push latency: for every Woodpecker push pipeline, the gap between
the pushed commit's committer time (git) and the pipeline's creation time
(Woodpecker receives the push webhook). This bounds the window in which
work exists locally but is invisible to remote CI - the only window a local
advisory registry could cover."""
import json
import statistics
import subprocess
import sys
from pathlib import Path

WT = sys.argv[1]
P = json.loads((Path(__file__).parent / "pipelines_all.json").read_text())
rows = []
for p in P:
    if p["event"] != "push" or not p.get("commit") or not p.get("created"):
        continue
    r = subprocess.run(["git", "-C", WT, "log", "-1", "--format=%ct", p["commit"]], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        continue  # commit no longer reachable (force-pushed/rebased branch)
    ct = int(r.stdout.strip())
    rows.append((p["number"], p["branch"], (p["created"] - ct) / 60))
lat = sorted(m for _, _, m in rows)
print(f"push pipelines with reachable commit: {len(rows)} of {sum(1 for p in P if p['event']=='push')}")
print(f"commit->push latency min: median {statistics.median(lat):.1f}, p75 {lat[int(len(lat)*.75)]:.1f}, p90 {lat[int(len(lat)*.9)]:.1f}, max {lat[-1]:.1f}")
print(f"pushed within 5 min of commit: {sum(1 for m in lat if m <= 5)}/{len(lat)}; within 30 min: {sum(1 for m in lat if m <= 30)}/{len(lat)}; within 60 min: {sum(1 for m in lat if m <= 60)}/{len(lat)}")
```

Output (2026-08-26 02:12Z): `push pipelines with reachable commit: 105 of 105` · `median 0.1, p75 0.1,
p90 0.2, max 2.3` minutes · `within 5 min: 105/105`.

## Appendix C — commands

```
gh api graphql -f query='{ repository(owner:"thesneakattack", name:"kalshi-whale-poc") { pullRequest(number: 11) { isDraft state headRefName closingIssuesReferences(first:5){ totalCount } files(first:3){ totalCount nodes { path } } } } }'
gh api graphql -f query='{ issue: __type(name:"Issue"){ fields{ name } } pr: __type(name:"PullRequest"){ fields{ name } } }'
python3 suppression_matrix.py
python3 push_latency.py <worktree>
```
