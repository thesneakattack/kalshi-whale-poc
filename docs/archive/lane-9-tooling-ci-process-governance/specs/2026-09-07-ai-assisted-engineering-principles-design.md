# AI-assisted engineering principles — design (2026-09-07)

Lane 9. Stage 2 (design/spec) of the research → spec → plan pipeline
whose stage 1 is
`../research/2026-09-07-ai-assisted-engineering-principles.md` (GO after
its own self-review, adversarial review, consolidation, and recheck).
Revision 2: the first version (`afbb516`) went through self-review and
an independent adversarial review (NO-GO, thirteen merged fixes in
`…-design-consolidation.md`); this revision applies them and is
rechecked item by item in `…-design-recheck.md`.

This document turns the seven research principles into exact rule text,
one mechanical tier boundary, one merge-time check, and one read-only
outcome report. It changes no trading, risk, safety-gate, or `data/*.db`
behaviour.

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
`checkpoint` skill, `tools/kanban_sync` — per the planning-lanes
design's §1 ("written rules over tools, pre-built tools over
hand-rolled ones", line 30) and §9 ("extend `tools/kanban_sync` rather
than a parallel tool", lines 359–360).

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
| P1 | Review depth follows consequence, decided by the paths a PR touches, never by feel. Tier A owes today's full cycle; Tier B owes one persisted self-review plus green CI. Doubt escalates to A, never down. | CLAUDE.md Scope bullet and the five sibling lines §5.1 amends; `branching-and-ci.md` |
| P2 | A Tier A change to pricing, P&L, fees, sizing, or settlement cites in its PR one read-only query or replay over recorded `data/*.db` history that exercises the changed path, and what it showed. Fixtures alone are not evidence. | CLAUDE.md "A displayed value must match its label" |
| P3 | A review that is not a persisted artifact did not happen. The merging session counts artifacts with `review-tier`; a PR body's narrative counts for nothing. | CLAUDE.md new merge bullet; `branching-and-ci.md` merge step; `checkpoint` step 9 |
| P4 | Outcomes are measured the same way every checkpoint: `python -m tools.kanban_sync outcomes`. It reports defects per tier and size band, never volume, and never human hours. | `checkpoint` step 8; `tools/kanban_sync` |
| P5 | For Tier A tests and PR evidence, the expected value's source is named — a `docs/kalshi/` page, a recorded row, an invariant, an independent calculation — never the code's own output. | CLAUDE.md, same section as P2 |
| P6 | A new rule, hook, or tool names the failure it prevents and what would retire it. | CLAUDE.md header sentence |
| P7 | Tiering never lowers a safety gate; `KALSHI_PATHS` stays a deny in every tier. | CLAUDE.md "Safety invariants" |

## 3. The tier boundary

### 3.1 Definition

The mechanical/trivial exemption in the Scope bullet (typo, CI
re-trigger, a config value edited exactly as dictated, a revert) is
evaluated **first** and is unchanged; an exempt PR owes no artifact and
`review-tier` records the stated reason (§6.2). Otherwise a PR is
**Tier A** if any rule below fires, else **Tier B**.

1. **Code-path rule.** It changes a *code or config* file (suffix in
   `.py .js .ts .mjs .yaml .yml .json .toml .sh .sql .html .css`) whose
   path starts with an entry of `REVIEW_TIER_A_PATHS`
   (`tools/kanban_sync/labels.py`, §6.1), which is the union of:
   - `LANES[1]`, `LANES[2]`, `LANES[3]`, `LANES[7]` package lists, each
     bare name resolved as `tests/test_kanban_sync_labels.py`'s
     `_resolve_package_path` resolves it — data in, signal,
     decision/execution, and the config store that writes safety flags;
   - `guard_workflow.py`'s `KALSHI_PATHS`, `HOT_PATHS`, `MONEY_UI_PATHS`
     (kept in the hook; subset-of-constant is CI-enforced, §6.1), minus
     the three entries that name deleted files (§6.1);
   - **money and decision inputs outside Lanes 1–3:**
     `services/settlement_edge.py` (its `projected_probability()` is
     called from `settlement_edge_entry.py:108`), `services/candidate_log.py`
     (`record_rejection()` at eight sites in `strategy_engine.py`),
     `services/history/` (fees and realized P&L — #423 is a real
     fees/P&L fix that was Tier B in revision 1);
   - **every module a Lane 3 module imports directly**, enforced by a test
     that scans Lane 3 sources and asserts each target is covered — the
     09-03 memory's "widely-used code" criterion, made mechanical.
     **Corrected 2026-09-07 by the implementation plan's adversarial review
     (see `../plans/2026-09-07-ai-assisted-engineering-principles.md`,
     deviation 4):** this bullet originally listed five paths and specified a
     scan for `from services.X` / `import services.X`. That misses Lane 3's
     dominant import form, `from services import a, b, c`
     (`services/strategy_engine.py:9`, `services/exits/exit_engine.py:17`,
     `services/settlement_resolver.py:38-39`). An `ast` re-derivation finds
     **27** direct imports, not 13; four of the five originally listed were
     already Tier A through `LANES`, and **six were not Tier A at all** —
     `services/fault_log.py`, `services/history_push.py`,
     `services/http_client.py`, `services/index_feed/`,
     `services/market_analyst_agent/`, `services/market_lookup.py`. All six
     are Tier A as of the plan; the scan is `ast`-based;
   - `main.py`; `services/db.py`, `services/capture_writer.py`,
     `services/task_supervisor.py`, `services/tick_executor.py` (the
     data-plane plumbing the HARD RULE's six properties ride on);
     `services/auth.py`, `services/accounts_store.py`; `services/reset/`
     (resets live data); `config/settings.yaml`; `.ddev/` (the
     Basic-Auth-gated public proxy);
   - process and CI: `.claude/hooks/`, `.claude/settings.json`,
     `.mcp.json`, `.woodpecker/`, `.github/workflows/`,
     `tools/quality_audit/`, `scripts/ci-*`, `scripts/woodpecker-*`, and
     **`tools/kanban_sync/labels.py`** (the tier definition itself);
   - `tests/test_<stem>*.py` where `<stem>` is the basename of any
     Tier A module or package above (a test of Tier A code is Tier A; the
     `tests/` directory as a whole is not — D8).
2. **Prose-always rule.** It changes any file, whatever its suffix,
   under `CLAUDE.md`, `.claude/rules/`, `.claude/skills/`,
   `docs/superpowers/`, or `docs/archive/lane-*/{research,specs,plans}/`
   (a process rule or a planning-pipeline stage, which already owes a
   cycle per stage). Other prose under a Tier A directory (a `README.md`
   or `CHEATSHEET.md` inside `services/kalshi/`) does not fire rule 1;
   in the 200-PR window this moves no PR, and it is stated so a
   docs-only touch of a Tier A package is Tier B by rule.
3. **Data-model rule.** Its diff adds or changes a line matching
   `register_schema\(|CREATE TABLE|ALTER TABLE|PRAGMA user_version`
   (anywhere, any file) — the article's "data model" boundary, which no
   path list catches when a Lane 4 module changes its own table.
4. **Label rule.** The PR, or an issue it closes, carries
   `concern:hotpath`.
5. **Escalation.** The author or the merging session says A
   (`review-tier --tier A`, reason recorded). No rule moves a PR from A
   to B.

### 3.2 What the boundary does to the last 200 merged PRs

Simulated over every merged PR's changed-file list at `6a63309` (rules 1
and 2 as worded above; rules 3–5 need diff text and labels the file-list
simulation does not fetch). The adversarial pass re-ran the same
simulation independently and matched within ±1 (its window included
#663, merged during the review).

| Population | Tier A | Tier B |
|---|---|---|
| All 200 | 141 | 59 |
| Code-typed (84) | 68 | 16 |
| Docs-typed (105) | 64 | 41 |
| The 30 PRs the research found with **no** review artifact | **23** | 7 |

> **Corrected 2026-09-07 (implementation-plan stage).** These counts were
> measured against §3.1's Lane-3 import list as originally derived, which the
> correction above shows was produced by an incomplete scan. With the six
> missing Lane 3 dependencies added, **two PRs move B → A (#614, #498)** and
> the table becomes: all 200 → 143/57; code-typed → **70 / 14**; unreviewed →
> **24 / 6** (the Tier B set loses #498, leaving #273 #301 #308 #415 #445
> #623). The rows above are kept as the record of what was measured when;
> the corrected figures are what the boundary actually does. Re-derived twice
> in this session and independently re-simulated by the plan's adversarial
> reviewer.

Revision 1's boundary gave 133 / 67 (code 60 / 24); the eight code PRs
that moved to Tier A in this revision are #423 (history fees/P&L),
#522 #617 #620 #631 #636 (`candidate_log.py`), #557 (`settlement_edge.py`),
#646 (`labels.py`). The Lane-3-import rule and the test-file rule move
no further PR in this window but stand as rules.

The last row is the point: the tier does not excuse the compliance
failures the research found. Twenty-three of the thirty — including #502
(strategy gate), #298, #499, #500, #558, #297 — remain Tier A and were
simply never reviewed. The seven that become Tier B (#301 #308 #415 #445
#498 #623 #273: a test tightening, a backup-overlap guard, a container
image line, a gitignore, a test flush-race, a comment fix, a one-off
backfill tool) are exactly the low-blast-radius changes the 09-03
complaint was about. Applying rule 3 to the Tier B code PRs' diffs
(the adversarial pass's `gh pr diff` sweep) moves #273 #522 #540 #557
#559 #561 to Tier A because they add `register_schema(`/`CREATE TABLE`
lines — the intended effect: the data model is Tier A even when the file
is Lane 4.

The 16 Tier B code PRs that remain are Lane 6 observability/quality
modules, `fault_log.py`, `tools/` other than the tier definition, tests
of Tier B modules, and container/CI plumbing.

Known coarse spots, accepted and listed so they are decisions rather
than accidents, each with its *marginal* effect measured by re-running
the simulation with that entry removed from every list it appears in:
`config/settings.yaml` is the sole Tier A trigger for **3** PRs (#389
#397 #596 — a watchlist widening, a settings-only tuning, and a
settings + next-action edit; the dictated-value exemption covers such
edits when David dictates them, and nothing else does); `main.py` for
**3** (#625 #647 #649); `frontend/src/js/` — all of the dashboard's
JavaScript, drawn that wide for the dimensional nudge — for **3** (#390
#422 #598); `LANES[1]`+`LANES[2]` beyond what `KALSHI_PATHS`/`HOT_PATHS`
already cover, for **1** (#545). Narrowing `MONEY_UI_PATHS` to the files
that actually derive money is a separate, measurable decision and is not
made here.

## 4. What each tier owes

**Tier A — unchanged.** Exactly the current Scope bullet: for a planning
pipeline, self-review + adversarial review + consolidation at every
stage boundary and once more at the PR; for a code PR, that cycle once,
at the PR, before merge — three distinct persisted artifacts (PR comments
or committed documents). No commit- or push-level gate.

**Tier B — one artifact.** Before merge the author posts **one PR
comment** (a committed document is also acceptable; a section of the PR
body is **not** — the body narrative is what stood in for a review on 12
of the 24 unreviewed code PRs) whose first line is `Tier B self-review`,
with five fields, each one to a few lines:

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

**The merge-time check (P3), both tiers.** After the exemption question
is answered, the merging session runs

```
python -m tools.kanban_sync review-tier --pr <N> [--exempt "<reason>"] [--tier A]
```

which prints the tier and the rules that fired (matched paths,
data-model lines, labels, or the escalation/exemption given), the count
of distinct review artifacts found (§6.2), the requirement (A ≥ 3, B ≥ 1,
exempt 0), and `PASS` / `FAIL` / `EXEMPT`. `FAIL` means do not merge;
the author or a fresh Agent supplies what is missing. The output is
pasted into the final PR comment or the merge commit so the decision is
on record. This is inserted before `gh pr merge` in `branching-and-ci.md`
and the `checkpoint` skill; nothing else in the merge step changes. It
is a command in a rule file, not a hook: a hook cannot see a PR, and the
2026-08-27 audit's "enforcement in prose" root cause is answered by
making the check one command whose output is recorded, not by another
interceptor.

## 5. Verbatim rule edits

Each edit replaces or inserts exactly the text shown. Line numbers are at
`a39d5f9`, the commit at which this branch merged `origin/main` (§8), and
every one was re-verified there with `awk` after that merge: the three
intervening `origin/main` commits touched neither `CLAUDE.md` nor
`.claude/rules/branching-and-ci.md`, so all fourteen CLAUDE.md anchors and
all four `branching-and-ci.md` anchors are unchanged from `6a63309`. Where
a long line is edited, the replaced phrase is quoted so the edit is
checkable with `grep`.

### 5.1 `CLAUDE.md`

**Line 3 (header paragraph)**, append one sentence:

> A new rule, hook, or tool names the failure it prevents and what would retire it.

**Line 38 (rule preamble)**, replace the phrase
"and neither does a PR carrying a claim, design decision, new logic, or a process/rule change to `main`: submission is not the final gate, merge is."
with:

> and neither does a **Tier A** PR (the Scope bullet below defines the tiers): submission is not the final gate, merge is. A Tier B PR owes one persisted self-review and green CI, never nothing.

**Line 40 (Scope bullet)**, replace in full:

> - Scope: a mechanical/trivial change (typo fix, CI re-trigger, a config value edited exactly as the user dictated, a revert) is exempt — decided first, reason recorded by `review-tier --exempt`. Everything else is **Tier A** or **Tier B** by what it touches (`REVIEW_TIER_A_PATHS` in `tools/kanban_sync/labels.py`; `python -m tools.kanban_sync review-tier --pr N` decides it; tiered by consequence by David's decision, 2026-09-07 — `git log -S'review-tier'`). Tier A — Lanes 1–3 and 7, the Kalshi/hot-path/money-UI lists, modules Lane 3 imports, `settlement_edge`/`candidate_log`/`history`, `main.py`, `db.py` and any schema change, auth, reset, `config/settings.yaml`, `.ddev/`, CLAUDE.md/rules/hooks/skills/CI, the tier definition and tests of Tier A code, `concern:hotpath`, and every planning-pipeline stage — is in scope regardless of diff size, including this rule's own PR: each stage boundary in the planning pipeline, plus the PR once, before merge — never an individual commit or push inside a branch; implementation-time work (writing the code a gated plan already called for) is covered by TDD, systematic-debugging, and verification-before-completion instead. Tier B — everything else — owes one persisted `Tier B self-review` PR comment (tier, change, evidence, falsifier, left undone) plus green CI; no adversarial pass, no consolidation. Doubt escalates to A, never down. Do not invent an extra gate at commit or push granularity.

**Line 43 ("Every in-scope stage…")**, replace the opening phrase
"Every in-scope stage produces its own artifact, then three more before the next stage starts"
with:

> Every planning-pipeline stage and every Tier A PR produces its own artifact, then three more before the next stage (or the merge)

**New bullet after line 46 (Consolidation)**:

> - A finding first raised in an adversarial review is a new claim, not a verdict: it meets the same evidence standard (primary source, falsifier stated) before consolidation adopts it (decided 2026-09-05, #613; a wrong finding entered a consolidated report at exactly that step on 2026-09-02).

**Line 48 ("For an in-scope PR…")**, replace in full:

> - For a Tier A PR: after it's pushed and opened, one more full review cycle of the same shape (self-review, adversarial review, consolidation, each its own artifact) runs against the PR as submitted before `gh pr merge` runs. For a Tier B PR the one `Tier B self-review` comment is that cycle. `.claude/rules/branching-and-ci.md`'s "read the PR body before merging" step is a floor, not a substitute for either.

**New bullet after line 48**:

> - Before `gh pr merge` on any PR, the merging session runs `python -m tools.kanban_sync review-tier --pr N` (with `--exempt "<reason>"` for a mechanical change, `--tier A` to escalate) and merges only on `PASS` or `EXEMPT`: Tier A needs three distinct persisted artifacts, Tier B one; a PR body that *narrates* a review counts for nothing (12 of the 24 unreviewed code PRs in the 2026-09-07 research did exactly that; the four dated recurrences behind memory `persist-code-pr-reviews-as-comments` are the same shape).

**Line 49 (Lean execution)**, replace the phrase
"every required artifact — self-review, adversarial review, consolidation — still exists as its own document or PR comment, and the adversarial pass still runs from a context genuinely independent of the one that produced the artifact under review."
with:

> every artifact the tier requires — three for Tier A (self-review, adversarial review, consolidation), one for Tier B — still exists as its own document or PR comment, and a Tier A adversarial pass still runs from a context genuinely independent of the one that produced the artifact under review.

**Line 60 (dimensional-analysis rule, last bullet)**, replace from
"not yet by the hook" to the end of the line with:

> deliberately session-enforced, not by the hook (decided 2026-09-05, #613: a hook cannot recognize arithmetic without firing on nearly every edit, and a nudge that always fires trains sessions to dismiss the money/probability nudge that has real incidents behind it).

**Line 100 (Safety invariants, first bullet)**, replace
"`services/kalshi_account_client.py`" with "`services/kalshi/account_client.py`"
(the file moved under `services/kalshi/`; the old path does not exist).

**After line 103 (Safety invariants, last bullet)**, append:

> - Review tiering (the Scope bullet above) never lowers a safety gate; `KALSHI_PATHS` stays a deny in every tier, and nothing about Tier B touches `trading_enabled`, the kill switch, or `data/*.db` handling.

**Line 107 ("A displayed value must match its label")**, append two bullets:

> - A Tier A change to pricing, P&L, fees, sizing, or settlement cites in its PR one read-only query or replay over recorded `data/*.db` history that exercises the changed path, and what it showed; fixtures alone are not evidence (the no-side cost inversion and the 2026-09-04 NO-side exit valuation both passed the suite and were found in recorded data).
> - For Tier A tests and PR evidence, name where the expected value comes from — a `docs/kalshi/` page, a recorded row, an invariant, an independent calculation — never the code's own output.

**Line 117 (Branching)**, replace the opening clause "`main` is protected;" with:

> `main` takes no direct work (server-side protection is off — #615, David's open call, cause not recoverable from the API — so the six required contexts are read from `commits/<sha>/status` before every merge);

**Line 120 (Parallel sessions)**, replace the phrase
"not a substitute for the "nothing advances on one pass" adversarial-review requirement (2026-08-31)"
with:

> not a substitute for the "nothing advances on one pass" requirement — a Tier A PR's fresh-Agent adversarial review, a Tier B PR's self-review comment (2026-08-31, tiered 2026-09-07)

### 5.2 `.claude/rules/branching-and-ci.md`

**Lines 76–78**, replace the parenthetical
"(that still needs its own fresh, memory-less Agent call regardless of what a peer says)"
with:

> (a Tier A PR still needs its own fresh, memory-less Agent call regardless of what a peer says; a Tier B PR still needs its `Tier B self-review` comment)

**Line 63**, insert immediately before "Read the PR body before merging":

> Decide the exemption question first, then run `python -m tools.kanban_sync review-tier --pr <n>` (`--exempt "<reason>"` for a mechanical change, `--tier A` to escalate) and paste its output into the final PR comment or the merge commit; merge only on `PASS` or `EXEMPT`. It decides Tier A/B from the changed paths, data-model lines, and `concern:hotpath`, and counts the persisted review artifacts (three for A, one for B). A `FAIL` is not a formality — supply the missing artifact (a fresh Agent for an adversarial pass, the author for a Tier B self-review) or do not merge.

**Lines 88–94 (Single-developer repo bullet)**, replace the phrase
"but the AI-executed self-review/adversarial-review/consolidation cycle still runs before `gh pr merge` — that is rigor, not approval ceremony."
with:

> but the AI-executed review its tier requires — the self-review/adversarial-review/consolidation cycle for Tier A, the one self-review comment for Tier B — still runs before `gh pr merge`, and `review-tier` records that it did — that is rigor, not approval ceremony.

**Lines 96–116 (GitHub-side enforcement paragraph)**, replace in full:

> **GitHub-side enforcement (configured 2026-08-25; found off 2026-09-05, #615):** `gh api repos/thesneakattack/kalshi-whale-poc/branches/main` reports `protected: false`, `enforcement_level: off`; whether it lapsed via a plan change or the repo going private is not recoverable from the API. Until #615 is decided (GitHub Pro, a public repo, or accepting this as permanent), the six contexts below are enforced by the merging session reading `commits/<sha>/status` and treating any state other than `success` on any of them as not merged: `ci/woodpecker/pr/tests-pytest-app`, `.../tests-pytest-tooling`, `.../tests-dependency-audit`, `.../quality-architecture-audit`, `.../quality-browser-e2e`, `.../kalshi-contract-fixtures`. `quality-frontend-build` is path-filtered to `frontend/**` and posts no status when skipped, so it is read only when it ran. If protection is re-enabled, restore the 2026-09-03 contexts list with `gh api -X PUT .../protection --input <file>` and rewrite this paragraph.

### 5.3 `.claude/skills/checkpoint/SKILL.md`

Step 8's command block gains one line:

```
python -m tools.kanban_sync outcomes --since <date> --until <date>      # defects per tier/size band, review compliance; read-only, never gating
```

Step 9 gains, before `gh pr merge --merge`: "decide the exemption
question, run `python -m tools.kanban_sync review-tier --pr <n>`, merge
only on `PASS` or `EXEMPT`."

### 5.4 `.claude/hooks/guard_workflow.py`

Remove the three entries that name deleted files —
`services/kalshi_client.py`, `services/kalshi_account_client.py`,
`services/kalshi_trade_ws.py` — from `KALSHI_PATHS` and `HOT_PATHS`
(the moved code lives under `services/kalshi/`, already listed). No
other hook change. This is a Tier A edit and is why the subset test in
§6.1 can be strict.

## 6. Mechanism

All code lives in `tools/kanban_sync`, which already owns the label
vocabulary, the `LANES` map, and an authenticated `gh` client with
`Runner` injection for fakes (`tests/test_kanban_sync_github_client.py`).

### 6.1 `labels.py`

```python
REVIEW_TIER_A_PATHS: tuple[str, ...]        # §3.1 rule 1 union, normalised to repo-relative prefixes
REVIEW_TIER_A_CODE_SUFFIXES: tuple[str, ...] # .py .js .ts .mjs .yaml .yml .json .toml .sh .sql .html .css
REVIEW_TIER_A_PROSE_ALWAYS: tuple[str, ...]  # CLAUDE.md, .claude/rules/, .claude/skills/, docs/superpowers/, docs/archive/lane-*/{research,specs,plans}/
REVIEW_TIER_A_DIFF_PATTERN: re.Pattern       # register_schema\(|CREATE TABLE|ALTER TABLE|PRAGMA user_version
REVIEW_TIER_A_LABELS = frozenset({CONCERN_HOTPATH})
REVIEW_ARTIFACT_FIRST_LINE: re.Pattern       # §6.2

def review_tier(files, *, diff_text="", labels=(), escalate=False) -> tuple[str, list[str]]:
    """Return ("A" | "B", reasons). Pure; no I/O."""
def lane3_direct_imports(repo_root) -> set[str]:
    """Scan LANES[3] sources for `from services.X` / `import services.X`; used by the test below."""
```

Tests added to `tests/test_kanban_sync_labels.py`:

- every entry of `REVIEW_TIER_A_PATHS` exists on disk (the same test
  `LANES` has at lines 122–135) — this is what makes the dead hook
  entries a blocking finding rather than a note;
- `guard_workflow.py`'s three tuples, loaded by path with
  `importlib.util.spec_from_file_location` exactly as
  `tests/test_guard_workflow.py:11-16` already does, are each a subset
  of `REVIEW_TIER_A_PATHS`;
- every `lane3_direct_imports()` target is covered by
  `REVIEW_TIER_A_PATHS`;
- `review_tier()` on the fixture corpus (§7).

The hook keeps its own tuples: `run_hook.py:60-61` launches it as
`[sys.executable, str(hook)]` with cwd = repo root but the script's own
directory as `sys.path[0]`, so a `tools/` import would need a
`sys.path` insert, and an import failure would silently disable the
Kalshi deny. One authoritative list, consistency enforced by CI, no new
runtime coupling.

### 6.2 `review-tier` subcommand

`python -m tools.kanban_sync review-tier --pr N [--exempt "<reason>"]
[--tier A] [--json]`, fitting `__main__.py`'s existing argparse
subparsers. Readers added to `github_client.py`, each a thin `gh` call in
the client's retrying `_run` shape: `get_pr_files` (REST
`pulls/N/files` with `--paginate` — the `--json files` field caps at
100 and PR #660 had 124), `get_pr_diff`, `get_pr_labels` (PR and
closing-issue labels), `list_pr_comments`, `get_pr_meta` (`createdAt`,
`mergedAt`, `additions`), `list_merged_prs(since, until)`.

> **Corrected 2026-09-07 (implementation-plan stage), twice.** (1) The
> "plus files in the diff" clause below is **withdrawn**: counting
> review-named files lets a planning-pipeline PR's earlier-stage documents
> satisfy its PR-stage requirement — this initiative's own branch carries
> seven such files and would have printed `PASS` with zero PR-stage comments,
> which is the "already covered across stages" CLAUDE.md forbids. The counter
> takes comments only. (2) "anywhere in that line" is **withdrawn** for an
> anchored match: the keyword must *begin* the line after optional markdown
> noise and an optional qualifier. Measured — the unanchored form counts eight
> real comments that only discuss a review, and PR #632 (Tier A) reached its
> required three through one of them. See the plan's deviations 6 and 7.

Artifact count: PR comments whose **first line** matches
`REVIEW_ARTIFACT_FIRST_LINE` = optional `#`/`**`/`*`/`_` prefix, then
the words `self-review`, `self review`, `adversarial`, or
`consolidation` anywhere in that line, case-insensitive — validated
against all 261 comments on the window's PRs: 224 match, and the 37
that do not are CI re-triggers, corrections, checkpoints, and rechecks,
none a review artifact; the stricter `^#+\s*(self-review|…)` form the
first revision proposed matched 170 and failed a fully compliant PR
(#625) — plus files in the diff whose name contains
`self-review|adversarial-review|consolidation`. Output: tier, reasons,
artifact count, requirement, `PASS`/`FAIL`/`EXEMPT`; exit 0 on
`PASS`/`EXEMPT`, 1 on `FAIL`, 2 on a fetch error (never silently
`PASS`). `--exempt` requires a non-empty reason and prints `EXEMPT` with
it; `--tier A` overrides a computed B and prints the reason.

What the count cannot do (D11): tell an independent Agent's comment from
the author's — every comment in the window is by the same GitHub login.
Independence rests on the session's honesty, as the rule already does;
the count catches the failure mode that actually recurred (no artifact
at all), not a fabricated one.

### 6.3 `outcomes` subcommand (P4)

`python -m tools.kanban_sync outcomes --since YYYY-MM-DD --until
YYYY-MM-DD [--json]`, read-only:

- Population: PRs merged in the window, each with tier (from
  `review_tier()` on its files, diff, labels), size band by additions
  (`<100`, `100–500`, `>500`), artifact count, and open→merge minutes.
- **Defect** for a merged PR *P*, two predicates only: (a) a later
  commit on `origin/main` whose subject starts `revert`, `fix: revert`,
  or `chore: revert` and cites *P* — read with `git log origin/main`
  locally, since the tool runs inside the repo; (b) a later merged PR
  whose title or body contains `live incident|outage|regression` and
  cites `PR #P`.
- **Cited follow-ups** (not a defect): a later `fix`-prefixed PR citing
  `PR #P`. Reported in its own column because the research showed a
  review *generates* such citations; the first revision's attempt to
  exclude review-generated ones by body words was inert (under P3 every
  compliant body names its review) and gameable, so no exclusion is
  attempted and the column is labelled for what it is.
- Output: one table — rows by tier × size band; columns merged, with
  artifacts ≥ requirement, defects (a) and (b), cited follow-ups,
  open→merge p50 — plus the list of PRs failing P3's count, and the
  predicates printed under the table so the numbers are never mistaken
  for a measured defect rate. Markdown to stdout; `--json` for tooling.
- Home: `checkpoint` step 8, alongside `quality_coordination`; never
  gating; no file written.
- Windows and retirement: first window 2026-09-07 → 2026-09-21
  (computed retroactively once the tool exists — every input persists on
  GitHub — with the caveat that its "artifacts ≥ requirement" column
  measures PRs merged before Tier B existed), second 2026-09-21 →
  2026-10-05. On 2026-10-05 a line in `docs/open-decisions.md` records
  David's judgment: did any decision cite the report? If not, retire it.
- What it does not do: measure human hours (only David can), attribute
  causation, or replace the trading-system quality endpoints.

## 7. Verification approach (the plan details it)

- `review_tier()` on a fixture corpus: the thirty research PRs with
  their real file lists (23 A / 7 B), the eight PRs that moved in this
  revision, #625/#647/#649 (A only via `main.py`), #390/#422/#598 (A
  only via `frontend/src/js/`), #545 (A only via Lanes 1/2), a
  `register_schema(` diff on a Lane 4 file → A, a cheatsheet under
  `services/kalshi/` alone → B, `concern:hotpath` alone → A, `escalate`
  → A, a `tests/test_strategy_engine_*.py` file → A.
- Exists-on-disk, subset-of-constant, and Lane-3-imports tests (§6.1).
- `REVIEW_ARTIFACT_FIRST_LINE` against the real first lines: `##
  Self-review`, `## Consolidation`, `## Independent adversarial review`,
  `**Consolidation — GO**`, `**Self-review (lean, per PR #587)**`,
  `**Adversarial review** (independent Agent-tool call, …)`, `## PR-stage
  adversarial review (…)`, `## Dispatching-session self-review (…)`,
  `Tier B self-review`; and non-matches `Re-triggering CI - …`, `##
  Fix-list recheck …`, `## Checkpoint — …`.
- `review-tier` and `outcomes` against recorded `gh` JSON via the
  `Runner` fake, including a >100-file PR to prove pagination.
- The rule-text edits are Tier A by definition and get the full cycle at
  the PR.
- A live dry run: `review-tier` against the last ten merged PRs, output
  pasted into the PR, compared with the §3.2 simulation.

## 8. Rollout and coordination

- Done 2026-09-07 before the plan stage: the branch merged `origin/main`
  in its own worktree (`a39d5f9`; never a rebase of shared history) and §5's
  line anchors were re-verified at that commit.
- One PR, `lane:9`, labels `phase:research` + `phase:spec` now and
  `phase:plan` when the plan doc joins it; the mechanism and the rule
  text land **together** because the rule text names the command.
- #613's two decided edits are included (§5.1 lines 46 and 60) and the
  PR closes #613 — see D6 for the honest framing.
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
| D3 | Any code-file touch of `config/settings.yaml` is Tier A | Only strategy/risk keys | Safety flags live there; the dictated-value exemption covers David's own edits; key-level rules need a parser this spec does not want to add. Marginal cost measured: sole trigger for 3 PRs in the window (#389 #397 #596), all tuning edits a session made without a dictated value — exactly the case that should be Tier A. |
| D4 | The hook keeps its own tuples; a CI test enforces subset-of-constant and exists-on-disk | Hook imports the constant | A hook import failure would silently disable the Kalshi deny (§6.1). |
| D5 | `outcomes` and `review-tier` are `kanban_sync` subcommands | A new `tools/process_outcomes.py` | Lanes design §9 (lines 359–360): extend `kanban_sync`; it already has the `gh` client, `Runner` fakes, and the label vocabulary. |
| D6 | #613's two edits, the line-117 protection text, and the `:100` stale path are in this PR | Separate docs PRs | #613 *is* a decided CLAUDE.md docs PR awaiting its own cycle; this PR is a CLAUDE.md docs PR with that cycle, so folding it is not adjacent work. The line-117 and `:100` fixes **are** adjacent by file: they correct two known-false statements in sections this PR rewrites, three lines from the new text. David may strike them. |
| D7 | `MONEY_UI_PATHS` stays as wide as it is | Narrow to money-deriving JS files | Out of scope; needs its own measurement of which files derive money. |
| D8 | Tests of Tier A modules are Tier A by filename stem; `tests/` as a whole is Tier B | All of `tests/` Tier A | Measured: `tests/` wholesale leaves 3 of 84 code PRs in Tier B — the uniform rule by the back door. A test that weakens a Tier A module's coverage is caught by the stem rule; a test of a Tier B module is Tier B like the module. |
| D9 | A claim-asserting document outside the pipeline directories (a top-level `docs/*.md` census, a module `README.md` finding, `docs/open-decisions.md`) is Tier B unless escalated | Keep "asserting a claim" as a Tier A trigger | The 11-of-12 catch-rate evidence behind the rule came from pipeline research docs, which the lanes migration now houses in the Tier A directories; a claim doc written elsewhere is misplaced first and under-reviewed second, and rule 5 exists for the author who knows it matters. |
| D10 | `.ddev/` is Tier A; `Dockerfile`, `requirements*.txt` are Tier B | All infra Tier A | `.ddev/nginx/` holds the public tunnel's Basic-Auth gate. Dependencies are already gated by the `tests-dependency-audit` CI context. |
| D11 | Artifact independence is not verified mechanically | Require a distinct login or Agent marker | Every comment in the window is by one login; the rule already rests on the session's honesty for the adversarial pass, and the recurring failure was absence, not forgery. |
| D12 | Rule 3 (data-model diff pattern) widens Tier A into Lane 4 | Path-only boundary | The article's "data model" boundary; measured: six Tier B PRs carry `register_schema(`/`CREATE TABLE` lines, four of them `db.py` migrations of Lane 4 modules. |

## 10. What would flip this to NO-GO

- A merged PR in the last 200 that the boundary classifies Tier B and
  that changed trading, risk, sizing, calibration, strategy, settlement,
  money display, auth, or live-data-reset behaviour — the revision-1
  finding (#423, `history/`) is now Tier A; none remains known.
- `review_tier()` giving a different answer from §3.2 on any fixture PR
  once implemented.
- The Tier B comment turning out to cost as much as the Tier A cycle in
  practice (the first `outcomes` window will show open→merge by tier; it
  cannot show pre-commit time).
- A rule-text edit in §5 that contradicts a line the PR does not also
  change — the adversarial pass found six such lines in revision 1, all
  now amended; the recheck greps the whole of CLAUDE.md and
  `branching-and-ci.md` for "self-review, adversarial review,
  consolidation" and "adversarial-review requirement" to confirm no
  unqualified instance remains.
