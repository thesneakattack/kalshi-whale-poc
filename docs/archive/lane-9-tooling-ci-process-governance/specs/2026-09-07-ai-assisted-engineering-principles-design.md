# AI-assisted engineering principles — design (2026-09-07)

Lane 9. Stage 2 (design/spec) of the research → spec → plan pipeline
whose stage 1 is
`../research/2026-09-07-ai-assisted-engineering-principles.md` (GO after
self-review, adversarial review, consolidation, and an eleven-item
recheck — all in that directory). This document turns the seven
candidate principles into exact rule text, one mechanical tier boundary,
one merge-time check, and one read-only outcome report. It changes no
trading, risk, safety-gate, or `data/*.db` behaviour.

**Decision inherited (David, 2026-09-07, recorded in
`docs/open-decisions.md`):** review depth under CLAUDE.md's "nothing
advances on one pass" HARD RULE is tiered by consequence. This spec
defines the boundary and what each tier owes.

## 1. Purpose and non-goals

Purpose: make the repo's verification effort follow blast radius instead
of applying uniformly; make review compliance countable; make the
article's first outcome question ("is change-failure holding?")
answerable; adopt the one rule Gardin derives from his own incident
(money-path changes checked against real data). Every change lands in an
existing home — CLAUDE.md, `.claude/rules/branching-and-ci.md`, the
`checkpoint` skill, `tools/kanban_sync` — per the lanes design's §8
(written rules over tools, pre-built over hand-rolled, extend
`kanban_sync` rather than a parallel tool).

Non-goals, stated so the tiering is not blamed for them later: the
"no flake classification" mandate and the skill-chain ceremony on
bug-shaped tasks (the other two hour-plus contributors the 09-03 memory
names) are untouched; #615 (`main` protection) stays David's call; no
changed-lines coverage gate; no PR size number; no effort budget; no new
hook; no retroactive review of the 30 unreviewed PRs the research lists
(they are recorded there; P3 applies from merge of this work onward).

## 2. The principles, final wording

| # | Rule (as it will read) | Home |
|---|---|---|
| P1 | Review depth follows consequence, decided by the paths a PR touches, never by feel. Tier A owes today's full cycle; Tier B owes one persisted self-review plus green CI. Doubt escalates to A, never down. | CLAUDE.md Scope bullet; `branching-and-ci.md` |
| P2 | A Tier A change to pricing, P&L, fees, sizing, or settlement cites in its PR one read-only query or replay over recorded `data/*.db` history that exercises the changed path, and what it showed. Fixtures alone are not evidence. | CLAUDE.md "A displayed value must match its label" |
| P3 | A review that is not a persisted artifact did not happen. The merging session counts artifacts with `review-tier`; a PR body's narrative counts for nothing. | CLAUDE.md Scope bullet; `branching-and-ci.md` merge step; `checkpoint` step 9 |
| P4 | Outcomes are measured the same way every checkpoint: `python -m tools.kanban_sync outcomes`. It reports defects per tier and size band, never volume, and never human hours. | `checkpoint` step 8; `tools/kanban_sync` |
| P5 | For Tier A tests and PR evidence, the expected value's source is named — a `docs/kalshi/` page, a recorded row, an invariant, an independent calculation — never the code's own output. | CLAUDE.md, same section as P2 |
| P6 | A new rule, hook, or tool names the failure it prevents and what would retire it. | CLAUDE.md header sentence |
| P7 | Tiering never lowers a safety gate; `KALSHI_PATHS` stays a deny in every tier. | CLAUDE.md "Safety invariants" |

## 3. The tier boundary

### 3.1 Definition

A PR is **Tier A** if any of the following holds; otherwise it is
**Tier B**. The mechanical/trivial exemption in the Scope bullet (typo,
CI re-trigger, a config value edited exactly as dictated, a revert) is
evaluated first and is unchanged.

1. **Path rule.** It changes a *code or config* file (`.py .js .ts .mjs
   .yaml .yml .json .toml .sh .sql .html .css`) whose path starts with
   any entry of `REVIEW_TIER_A_PATHS`:
   - `LANES[1]`, `LANES[2]`, `LANES[3]`, `LANES[7]` package lists
     (`tools/kanban_sync/labels.py`), each bare `services/` name resolved
     as the labels tests already resolve them — data in, signal,
     decision/execution, and the config store that writes safety flags;
   - `guard_workflow.py`'s `KALSHI_PATHS`, `HOT_PATHS`, `MONEY_UI_PATHS`
     (kept in the hook; equality with the constant is CI-enforced, §6);
   - `main.py`; `services/db.py`, `services/capture_writer.py`,
     `services/task_supervisor.py`, `services/tick_executor.py`
     (the data-plane plumbing the HARD RULE's six properties ride on);
     `services/auth.py`, `services/accounts_store.py`; `services/reset/`
     (resets live data); `config/settings.yaml`;
   - process and CI: `CLAUDE.md`, `.claude/rules/`, `.claude/hooks/`,
     `.claude/skills/`, `.claude/settings.json`, `.mcp.json`,
     `.woodpecker/`, `.github/workflows/`, `tools/quality_audit/`,
     `scripts/ci-*`, `scripts/woodpecker-*`.
2. **Pipeline rule.** It changes any file under
   `docs/archive/lane-*/{research,specs,plans}/` or `docs/superpowers/`
   (a planning-pipeline stage; the rule already owes a cycle per stage).
   Rule files under `.claude/rules/` and `CLAUDE.md` are prose and are
   Tier A by rule 1's explicit listing.
3. **Data-model rule.** Its diff adds or changes a line matching
   `register_schema\(|CREATE TABLE|ALTER TABLE|PRAGMA user_version`
   (anywhere, any file) — the article's "data model" boundary, which no
   path list catches when a Lane 4 module changes its own table.
4. **Label rule.** The PR, or an issue it closes, carries
   `concern:hotpath`.
5. **Escalation.** The author or the merging session says A. There is no
   rule that moves a PR from A to B.

Prose files under a Tier A directory (a `README.md` or `CHEATSHEET.md`
inside `services/kalshi/`) do **not** trigger rule 1 — the hook already
draws that line (`_is_code`). In the 200-PR window this exemption moves
no PR (every PR that touched such prose also touched Tier A code); it is
stated so a docs-only touch of a Tier A package is Tier B by rule, not
by someone's reading.

### 3.2 What the boundary does to the last 200 merged PRs

Simulated over every merged PR's changed-file list (rules 1 and 2 only;
rules 3–5 need diff text and labels the simulation did not fetch, so the
true Tier A count is slightly higher):

| Population | Tier A | Tier B |
|---|---|---|
| All 200 | 133 | 67 |
| Code-typed (84) | 60 | 24 |
| Docs-typed (105) | 64 | 41 |
| The 30 PRs the research found with **no** review artifact | **23** | 7 |

The last row is the point: the tier does not excuse the compliance
failures the research found. Twenty-three of the thirty — including #502
(strategy gate), #298, #499, #500, #558, #297 — remain Tier A and were
simply never reviewed. The seven that become Tier B (#301 #308 #415 #445
#498 #623 #273: a test tightening, a backup-overlap guard, a container
image line, a gitignore, a test flush-race, a comment fix, a one-off
backfill tool) are exactly the low-blast-radius changes the 09-03
complaint was about.

The 24 Tier B code PRs in the window are Lane 4/6 modules, `tools/`,
`tests/`, container/CI plumbing, and the `services/db.py` *migrations of
other modules* (#522 #540 #557 #561). The last group would move to Tier
A under rule 3 if their diffs touch `register_schema(` — which is the
intended effect: the data model is Tier A even when the file is Lane 4.

Known coarse spots, accepted for now and listed so they are decisions
rather than accidents, each with its *marginal* effect measured by
re-running the simulation without that entry: `config/settings.yaml`
is the sole Tier A trigger for **0** PRs in the window (every PR that
touched it also touched other Tier A code); `main.py` is the sole
trigger for **3** (#625 #647 #649); `frontend/src/js/` — all of the
dashboard's JavaScript, drawn that wide for the dimensional nudge — is
the sole trigger for **3** (#390 #422 #598); `LANES[1]`+`LANES[2]`
beyond what `KALSHI_PATHS`/`HOT_PATHS` already cover, for **1** (#545).
Narrowing `MONEY_UI_PATHS` to the files that actually derive money is a
separate, measurable decision and is not made here.

## 4. What each tier owes

**Tier A — unchanged.** Exactly the current Scope bullet: for a planning
pipeline, self-review + adversarial review + consolidation at every
stage boundary and once more at the PR; for a code PR, that cycle once,
at the PR, before merge — three distinct persisted artifacts (PR comments
or committed documents). No commit- or push-level gate.

**Tier B — one artifact.** Before merge the author posts **one PR
comment** (a committed document is also acceptable; a section of the PR
body is **not** — the body narrative is what stood in for a review on 12
of the 24 unreviewed code PRs) titled `Tier B self-review`, with five
fields, each one to a few lines:

1. *Tier:* the changed paths and the statement that none matches
   `REVIEW_TIER_A_PATHS`, no data-model line, no `concern:hotpath`.
2. *What changed and why* (one paragraph, the behaviour, not the diff).
3. *Evidence:* which checks ran (targeted tests, a live read, a replay)
   and what they showed; the CI status URL.
4. *Falsifier:* what observation after merge would show this was wrong.
5. *Left undone:* anything noticed and not done, or "nothing".

Plus green CI on all six required contexts. No adversarial Agent, no
consolidation, no self-review document. The lean-execution clause
already allows a Tier A artifact to be a few lines; Tier B removes two
of the three, not the rigor of the remaining one.

**The merge-time check (P3), both tiers.** The merging session runs

```
python -m tools.kanban_sync review-tier --pr <N>
```

which prints the tier, the rules that fired (matched paths, data-model
lines, labels), the count of distinct review artifacts found (PR
comments whose body contains a review heading, plus review-named files
in the diff), and `PASS` / `FAIL` against the tier's requirement (A ≥ 3,
B ≥ 1). `FAIL` means do not merge; the author or a fresh Agent supplies
what is missing. This replaces nothing in `branching-and-ci.md`'s merge
step (read the body, grep for task-list items, read CI status, ping a
live peer); it is inserted before `gh pr merge`. It is a command in a
rule file and a skill, not a hook: a hook cannot see a PR, and the
2026-08-27 audit's "enforcement in prose" root cause is answered by
making the check one command whose output is pasted into the merge, not
by another interceptor.

## 5. Verbatim rule edits

Each edit below replaces or inserts exactly the text shown. Line numbers
are at `main` = `6a63309`; the plan re-anchors them.

### 5.1 `CLAUDE.md`

**Header paragraph** (line 3), append one sentence:

> A new rule, hook, or tool names the failure it prevents and what would retire it.

**Scope bullet** (line 40), replace in full:

> - Scope: a mechanical/trivial change (typo fix, CI re-trigger, a config value edited exactly as the user dictated, a revert) is exempt from the full cycle. Everything else is **Tier A** or **Tier B** by the paths it touches (`REVIEW_TIER_A_PATHS` in `tools/kanban_sync/labels.py`; `python -m tools.kanban_sync review-tier --pr N` decides it; tiered by consequence by David's decision, 2026-09-07 — `git log -S'review-tier'`). Tier A — Lanes 1–3 and 7, the Kalshi/hot-path/money-UI lists, `main.py`, `db.py`/schema changes, auth, reset, `config/settings.yaml`, CLAUDE.md/rules/hooks/skills/CI, `concern:hotpath`, and every planning-pipeline stage — is in scope regardless of diff size, including this rule's own PR: each stage boundary in the planning pipeline, plus the PR once, before merge — never an individual commit or push inside a branch; implementation-time work (writing the code a gated plan already called for) is covered by TDD, systematic-debugging, and verification-before-completion instead. Tier B — everything else — owes one persisted `Tier B self-review` PR comment (tier, change, evidence, falsifier, left undone) plus green CI; no adversarial pass, no consolidation. Doubt escalates to A, never down. Do not invent an extra gate at commit or push granularity.

**New bullet**, inserted after the Consolidation bullet (line 46):

> - A finding first raised in an adversarial review is a new claim, not a verdict: it meets the same evidence standard (primary source, falsifier stated) before consolidation adopts it (decided 2026-09-05, #613; a wrong finding entered a consolidated report at exactly that step on 2026-09-02).

**New bullet**, inserted after the "For an in-scope PR" bullet (line 48):

> - Before `gh pr merge` on any PR, the merging session runs `python -m tools.kanban_sync review-tier --pr N` and merges only on `PASS`: Tier A needs three distinct persisted artifacts, Tier B one; a PR body that *narrates* a review counts for nothing (12 of the 24 unreviewed code PRs in the 2026-09-07 research did exactly that; the four dated recurrences behind memory `persist-code-pr-reviews-as-comments` are the same shape).

**Dimensional-analysis rule, last bullet** (line 60), replace the clause
after the em-dash:

> — this rule's wider scope is deliberately session-enforced (decided 2026-09-05, #613: a hook cannot recognize arithmetic without firing on nearly every edit, and a nudge that always fires trains sessions to dismiss the money/probability nudge that has real incidents behind it).

**"A displayed value must match its label"** (line 107), append two bullets:

> - A Tier A change to pricing, P&L, fees, sizing, or settlement cites in its PR one read-only query or replay over recorded `data/*.db` history that exercises the changed path, and what it showed; fixtures alone are not evidence (the no-side cost inversion and the 2026-09-04 NO-side exit valuation both passed the suite and were found in recorded data).
> - For Tier A tests and PR evidence, name where the expected value comes from — a `docs/kalshi/` page, a recorded row, an invariant, an independent calculation — never the code's own output.

**Safety invariants** (after line 103), append:

> - Review tiering (the Scope bullet above) never lowers a safety gate; `KALSHI_PATHS` stays a deny in every tier, and nothing about Tier B touches `trading_enabled`, the kill switch, or `data/*.db` handling.

**Branching line** (line 117), replace the first clause:

> - `main` takes no direct work (server-side protection has been off since the Free-plan change — #615, David's open call — so the six required contexts are read from `commits/<sha>/status` before every merge); work on `feat/|fix/|refactor/|chore/|docs/` branches; …

### 5.2 `.claude/rules/branching-and-ci.md`

**Merge step**, insert immediately before the sentence beginning "Read
the PR body before merging":

> Run `python -m tools.kanban_sync review-tier --pr <n>` and paste its output into the merge commit or the final PR comment; merge only on `PASS`. It decides Tier A/B from the changed paths, data-model lines, and `concern:hotpath`, and counts the persisted review artifacts (three for A, one for B). A `FAIL` is not a formality — supply the missing artifact (a fresh Agent for an adversarial pass, the author for a Tier B self-review) or do not merge.

**GitHub-side enforcement paragraph** (lines 96–116), replace in full:

> **GitHub-side enforcement (configured 2026-08-25; found off 2026-09-05, #615):** `gh api repos/thesneakattack/kalshi-whale-poc/branches/main` reports `protected: false`, `enforcement_level: off` — the repo is private on a Free plan and the required-status-check gate no longer exists server-side. Until #615 is decided (GitHub Pro, a public repo, or accepting this as permanent), the six contexts below are enforced by the merging session reading `commits/<sha>/status` and treating any state other than `success` on any of them as not merged: `ci/woodpecker/pr/tests-pytest-app`, `.../tests-pytest-tooling`, `.../tests-dependency-audit`, `.../quality-architecture-audit`, `.../quality-browser-e2e`, `.../kalshi-contract-fixtures`. `quality-frontend-build` is path-filtered to `frontend/**` and posts no status when skipped, so it is read only when it ran. If protection is re-enabled, restore the 2026-09-03 contexts list with `gh api -X PUT .../protection --input <file>` and rewrite this paragraph.

### 5.3 `.claude/skills/checkpoint/SKILL.md`

Step 8's command block gains one line:

```
python -m tools.kanban_sync outcomes --days 14                       # defects per tier/size band, review compliance; read-only, never gating
```

Step 9 gains, before `gh pr merge --merge`: "run `python -m
tools.kanban_sync review-tier --pr <n>`; merge only on `PASS`."

## 6. Mechanism

All code lives in `tools/kanban_sync`, which already owns the label
vocabulary, the `LANES` map, and an authenticated `gh` client. No hook
changes.

### 6.1 `labels.py`

```python
REVIEW_TIER_A_PATHS: tuple[str, ...]      # built from LANES[1,2,3,7] + the §3.1 extras, normalised to repo-relative prefixes
REVIEW_TIER_A_CODE_SUFFIXES: tuple[str, ...]   # .py .js .ts .mjs .yaml .yml .json .toml .sh .sql .html .css
REVIEW_TIER_A_PROSE_ALWAYS: tuple[str, ...]    # CLAUDE.md, .claude/rules/, .claude/skills/, docs/archive/lane-*/{research,specs,plans}/, docs/superpowers/
REVIEW_TIER_A_DIFF_PATTERN: re.Pattern          # register_schema\(|CREATE TABLE|ALTER TABLE|PRAGMA user_version
REVIEW_TIER_A_LABELS = frozenset({CONCERN_HOTPATH})

def review_tier(files: Iterable[str], *, diff_text: str = "", labels: Iterable[str] = ()) -> tuple[str, list[str]]:
    """Return ("A" | "B", reasons). Pure; no I/O."""
```

The hook's `KALSHI_PATHS`, `HOT_PATHS`, `MONEY_UI_PATHS` stay where they
are (a hook must not import from `tools/` at runtime: `run_hook.py`
launches it as a bare script with the repo root as cwd but not on
`sys.path`, and an import failure would silently disable the Kalshi
deny). Instead `tests/test_kanban_sync_labels.py` gains one test that
loads `guard_workflow.py` by path with `importlib` and asserts each of
the three tuples is a subset of `REVIEW_TIER_A_PATHS`. One authoritative
list, consistency enforced by CI, no new runtime coupling. The
alternative — the hook importing the constant with a `sys.path` insert —
was rejected for the failure mode just named.

### 6.2 `review-tier` subcommand

`python -m tools.kanban_sync review-tier --pr N [--json]`: fetches the
PR's files, diff, labels, closing issues' labels, and comments via the
existing `github_client` (which today has issue, comment, project, and
`find_pr_state` methods but no PR-files/diff/comments readers — the plan
adds `get_pr_files`, `get_pr_diff`, `get_pr_labels`, `list_pr_comments`,
`list_merged_prs`, each a thin `gh` call in the client's existing
retrying `_run` shape); calls `review_tier()`; counts artifacts —
comments whose first line matches `^#+\s*(self-review|adversarial
review|consolidation|tier b self-review)` (case-insensitive) plus files
in the diff whose name contains `self-review|adversarial-review|
consolidation`; prints tier, reasons, artifact count, requirement, and
`PASS`/`FAIL`; exit code 0 on `PASS`, 1 on `FAIL`, 2 on a fetch error
(never silently `PASS`).

### 6.3 `outcomes` subcommand (P4)

`python -m tools.kanban_sync outcomes --days 14 [--json]`, read-only:

- Population: PRs merged in the window, each with tier (from
  `review_tier()` on its files, diff, labels), size band by additions
  (`<100`, `100–500`, `>500`), and artifact count (as above).
- **Defect** for a merged PR *P*: (a) a later commit on `main` whose
  subject starts `revert` or `fix: revert` and whose body or subject
  cites *P*; or (b) a later merged PR titled or bodied `live incident|
  outage|regression` that cites *P*; or (c) a later `fix`-prefixed PR
  that cites `PR #P` **and** whose body does not contain `adversarial|
  self-review|consolidation|review found|review finding` — the exclusion
  the research showed is necessary because a review generates citations.
  The predicate is a heuristic and is printed with the report so it is
  never mistaken for a measured defect rate.
- Output: one table — rows by tier × size band; columns merged, with
  artifacts ≥ requirement, defects (a/b/c separately), PR-open→merge
  p50 — plus the list of PRs failing P3's count. Markdown to stdout;
  `--json` for tooling.
- Home in the workflow: `checkpoint` step 8, alongside `quality_
  coordination`; never gating; no file written. Its own falsifier: two
  consecutive 14-day windows (2026-09-07 → 09-21, 09-21 → 10-05) in
  which no decision cites the report → retire it; the review is a line
  in `docs/open-decisions.md` dated 2026-10-05.
- What it does not do: measure human hours (only David can), attribute
  causation, or replace the trading-system quality endpoints.

## 7. Verification approach (the plan details it)

- `review_tier()` unit tests on fixture file lists: the seven Tier B and
  twenty-three Tier A PRs from §3.2 become fixtures with their real file
  lists, so the boundary's behaviour on known PRs is pinned; a
  `register_schema(` diff on a Lane 4 file → A; a cheatsheet under
  `services/kalshi/` alone → B; `concern:hotpath` alone → A.
- Subset test: hook tuples ⊆ `REVIEW_TIER_A_PATHS`.
- `review-tier` and `outcomes` tested against recorded `gh` JSON
  fixtures (the `github_client` already has a fake for `sync`); the
  artifact-count regex tested on the exact comment headings this repo
  has used (`# Self-review — …`, `# Adversarial review — …`,
  `# Consolidation — …`).
- The rule-text edits are Tier A by definition and get the full cycle at
  the PR.
- A live dry run: `review-tier` against the last ten merged PRs, output
  pasted into the PR, compared with the §3.2 simulation.

## 8. Rollout and coordination

- One PR, `lane:9`, labels `phase:spec` now and `phase:plan` when the
  plan doc joins it; the mechanism and the rule text land **together**
  because the rule text names the command.
- #613's two decided edits are folded into the same CLAUDE.md change
  (§5.1) rather than raced by a separate PR to the same bullets; the PR
  closes #613. The stale branch-protection text is corrected in the same
  edit (§5.1 line 117, §5.2), citing #615 without deciding it.
- After merge: `docs/next-action.md` names the first outcomes window;
  `docs/open-decisions.md` keeps the 2026-09-07 tiering line until this
  PR merges, then replaces it with the 2026-10-05 outcomes-review line.
- The 30 unreviewed PRs stay listed in the research doc; no backfill.

## 9. Decisions this spec makes, for review

Each is made with a recommendation so review can overturn it rather
than discover it.

| # | Decision | Alternative considered | Why this way |
|---|---|---|---|
| D1 | Lanes 1 and 2 (ingestion, whale signal) are Tier A, not only Lane 3 | Tier A = Lane 3 + hot paths only | The data-plane HARD RULE makes ingestion and signal *the product*; a dropped message is a defect. Marginal cost measured: beyond `KALSHI_PATHS`/`HOT_PATHS`, the two lanes are the sole trigger for one PR in the window (#545). |
| D2 | Tier B's artifact must be a PR comment or committed doc, never a body section | Allow a body section | The body narrative is exactly what failed 12 times; a comment is countable, a body section is not without parsing. |
| D3 | Any code-file touch of `config/settings.yaml` is Tier A | Only strategy/risk keys | Safety flags live there; the dictated-value exemption still covers David's own edits; key-level rules need a parser this spec does not want to add. Marginal cost measured: sole trigger for zero PRs in the window. |
| D4 | The hook keeps its own tuples; a CI test enforces subset-of-constant | Hook imports the constant | A hook import failure would silently disable the Kalshi deny (§6.1). |
| D5 | `outcomes` and `review-tier` are `kanban_sync` subcommands | A new `tools/process_outcomes.py` | Lanes §8 rule 5: extend `kanban_sync`; it already has the `gh` client and label vocabulary. |
| D6 | Fold #613's two edits into this PR | Separate docs PR first | Same bullets, both decided; a separate PR is a merge race for no rigor gain. |
| D7 | `MONEY_UI_PATHS` stays as wide as it is | Narrow to money-deriving JS files | Out of scope; needs its own measurement of which files derive money. |

## 10. What would flip this to NO-GO

- A merged PR in the last 200 that the boundary classifies Tier B and
  that touched trading, risk, sizing, calibration, strategy, settlement,
  or auth behaviour — the simulation found none, but rules 3–5 were not
  simulated.
- `review_tier()` giving a different answer from the §3.2 simulation on
  any of the 30 fixture PRs once implemented.
- The Tier B comment turning out to cost as much as the Tier A cycle in
  practice (the first `outcomes` window will show the open→merge split
  by tier; it cannot show pre-commit time).
- A rule-text edit in §5 that contradicts a line the PR does not also
  change (the adversarial pass should diff the proposed CLAUDE.md against
  the whole file, not the quoted bullets).
