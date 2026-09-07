# Migration step 4 — file-move plan (lane batches, directory structure, batch order)

Date: 2026-09-06
Author: this session (branch `docs/step4-file-move-plan`)
Status: DRAFT — pending self-review, adversarial review, and consolidation
(this document does not execute anything; no file was moved, renamed, or
deleted, and no GitHub issue/label/PR was touched to produce it).

Applies: `docs/superpowers/specs/2026-09-06-planning-lanes-design.md` (read on
`origin/main`, current at the time of writing — commit `615c981` is
`origin/main`'s tip), specifically §6 step 4. Builds on the three merged
classification tables: `docs/superpowers/lanes/step1-issues-classification.md`
(not used here — issues aren't moved), `step1-plans-classification.md` (63
files), `step1-specs-research-classification.md` (185 files), and
`tools/kanban_sync/labels.py`'s `LANES` dict (lane names/numbers/packages).

## 0. Methodology

Every number in this document was re-derived live against the current
repository state on 2026-09-06, not carried forward from the design doc's own
2026-09-06-PM figures (which the task that produced this document explicitly
warned are already stale) or from either classification table's own
self-reported summary counts (both of which turned out to be stale in ways
detailed in §1). Concretely:

1. **The two classification tables were parsed programmatically** (not
   transcribed by hand) directly from the checked-in `.md` files, using a
   small Python script (`parse_lanes.py` in this session's scratchpad) that
   extracts every table row via regex, resolves `plans/`-table companion rows
   (`kind: companion-of:<parent>`, `lane: inherits`) to their named parent's
   own lane number, strips markdown emphasis (`**4**` → `4`) that a naive
   regex would otherwise miss, and cross-checks the result against every
   path's actual presence on disk. This caught one real parsing bug (see §1)
   before any count was trusted.
2. **Every one of the 248 classified paths was verified to exist on disk,
   exactly once, with zero missing and zero duplicates** — a direct
   `os.path.exists()` check per row, not an assumption from the tables' own
   claims.
3. **Reference counts are live `git grep -c -F` results**, one invocation per
   file's basename against all tracked files in the repo, excluding matches
   inside `docs/superpowers/plans/`, `docs/superpowers/specs/`, and
   `docs/superpowers/research/` (the three directories being reorganized —
   a sibling planning doc citing another doesn't need an external
   path-reference fix; only citations from outside those three directories
   do). This is a stated, deliberate scope choice for "the files' own
   directories" in the task's instruction, applied uniformly to all three
   source directories rather than narrowly to each file's single home
   directory, and is flagged explicitly as a judgment call in §6.
4. **One file's basename collided with unrelated matches and was corrected
   for, not silently included**: `docs/superpowers/plans/README.md`'s
   basename, `README.md`, is shared by ~20 other README.md files across the
   repo, so a basename-only `git grep` picked up generic prose mentioning
   *those* files, not this one (confirmed by inspecting the raw match list —
   entries like `services/http_client.py` and `tools/quality_audit/
   baseline.json` do not reference this specific plans-index file). This
   file is separately out of scope anyway (§6, its retirement is design §6
   step 5's job, not step 4's), so it is excluded from every table and total
   in this document rather than left in with a contaminated count.
5. **GitHub issue counts are live `gh issue list` results**, filtered to
   genuine title-prefix matches (a `--search "Plan:"` query returns 87 hits
   including many that merely mention the word; only 6 open issues actually
   have a title starting `Plan: `), and the *entire* set of 76 open
   `type:plan-task` issues was checked against the master file list for path
   references — not a sample, since the issue bodies were already fetched in
   one `gh` call and checking the full set programmatically cost nothing
   extra. Where a sample would have been necessary this is stated explicitly
   (it wasn't, here).
6. **Hard dependencies were found by grepping code for literal quoted
   filenames and directory globs**, not assumed from the design doc's own
   touchpoint list (which predates this document and does not mention the
   dependency found in §4).

All intermediate data (the parsed tables, the per-file reference counts, the
issue-to-lane mapping) exists as JSON in this session's scratchpad; the
per-lane summaries and file lists in Appendix A were generated directly from
that JSON, not retyped, to eliminate transcription error at the point where
the two source tables' own high row counts make transcription error likely.

## 1. Master file→lane resolution

**Total scope: 248 files** (63 in `docs/superpowers/plans/`, 88 in
`docs/superpowers/specs/`, 97 in `docs/superpowers/research/` — verified via
`ls` against the live filesystem, not the design doc's stated "79 specs + 97
research = 176," which is addressed below).

**Two files are excluded from this plan's batches, explicitly, not silently
dropped:**

- `docs/superpowers/plans/README.md` — the plans index. Its retirement is
  design §6 step 5's job ("retire, not regenerate"), a separate, later
  decision; it is not one of the 9 lanes and this plan does not schedule it
  for movement. (See §6.)
- `docs/superpowers/specs/2026-08-27-backend-services-modularization-design.md`
  — the one file `step1-specs-research-classification.md` itself marks
  **UNDECIDED**, genuinely split across 4 lanes (history→4, config→7,
  position→3, reset→6) with no stated primary. Per the task's own instruction,
  this plan does not force it into a lane; it stays excluded pending the
  design-rule refinement the classification table itself called for (its own
  `RULE-GAP`/`Unlaned` framing). (See §6.)

That leaves **246 files** actually batched into lanes 1–9 by this plan.
Per-lane counts (companions resolved to their parent's lane; Lane 7 has zero
matching documents in either source table):

| Lane | Name | Plans (primary) | Plan companions | Specs/research | **Total** |
|---|---|---|---|---|---|
| 1 | Kalshi & index data ingestion | 8 | 3 | 35 | **46** |
| 2 | Whale signal detection & calibration | 3 | 1 | 21 | **25** |
| 3 | Strategy, risk & execution | 5 | 2 | 6 | **13** |
| 4 | Analytics, advisory & research | 2 | 0 | 9 | **11** |
| 5 | Runtime infrastructure | 3 | 12 | 37 | **52** |
| 6 | Observability, quality & safety infra | 4 | 3 | 7 | **14** |
| 7 | Config & control plane | 0 | 0 | 0 | **0** |
| 8 | Frontend & dashboard | 1 | 2 | 3 | **6** |
| 9 | Tooling, CI & process governance | 11 | 2 | 66 | **79** |
| **Total** | | **37** | **25** | **184** | **246** |

(37 dated primary plans + 25 plan companions + `README.md` (excluded, §6) =
63, the whole `plans/` table; 184 specs/research files = the 185-file table
minus the 1 UNDECIDED row. 37 + 25 + 184 + 1 UNDECIDED + 1 README.md = 248,
the full scope.)

### Cross-checks against each source table's own summary

**`step1-plans-classification.md`'s own summary reproduces exactly**, once
two adjustments are made: the one markdown-formatting artifact, and this
document's own exclusion of `README.md`. That source table's per-lane
summary (Lane 1=8, 2=3, 3=5, 4=2, 5=3, 6=4, 7=0, 8=1, 9=12, totaling **38**
lane-bearing rows + 25 companions = 63) counts `README.md` itself as one of
Lane 9's 12 (it is `kind: index`, laned 9 by the same table, not a
`companion-of:`) — so this document's own Lane 9 count of **11** is that
same 12 minus the 1 `README.md` this document excludes per §1/§6, not a
disagreement. Before that reconciliation, matching the source table's cell
content literally (without stripping markdown emphasis) also undercounted
Lane 4 by one — the `2026-08-27-backend-services-modularization.md` row's
lane cell is written `**4**`, and a first parse pass matching that string
literally silently dropped the file from every combined total (247 instead
of 248). This is exactly the class of transcription/parsing error the
design's own §6 step 1 warned a row-by-row-generated table would reproduce;
it was caught here by a total-count sanity check (247 ≠ 248), not
proactively anticipated — and the README.md-inclusion mismatch above was
caught by the same kind of check during this document's own self-review,
not on the first pass either (see `step4-file-move-plan-self-review.md`).

**`step1-specs-research-classification.md`'s own methodology header is
stale relative to its own table content.** The header states "specs/ (79) +
research/ (97) = 176 files"; the table itself, however, actually contains
**185 rows** (88 specs + 97 research), which matches the current filesystem
exactly (zero missing, zero extra, verified by `comm` against a fresh `ls`).
The row-level content is complete and accurate — every one of the 88 specs
files currently on disk has its own row — only the table's self-reported
summary count in its opening methodology section undercounts by 9. This
table has no per-lane count summary section at all (unlike the plans table),
so this document's own §1 table above is the first place these per-lane
totals exist. The methodology section's companion-count claim ("87
companions... 89 rows stand as their own parent/standalone document," summing
to 176) is stale for the same reason; a full (not sampled) programmatic check
of every "companion to `<path>`" phrase in that table's own Reason column
found **95** such content-detected companion references, and confirmed **zero
lane mismatches** between every companion and its named parent — stronger
than the "spot check a few" the task allowed for, done because it was cheap
once the data was already parsed.

**Plans table companions**: all 25 resolved cleanly to a parent with a real,
numeric lane (no unresolved companion — the parser logs unresolved cases
explicitly and found none).

**Finding to report, not paper over**: both source tables' own self-reported
summary numbers were wrong in some way (the plans table's Lane 4 cell had a
markdown artifact skewing its own hand-computed total; the specs/research
table's methodology-section headline count of 176 undercounts its own table
by 9 rows, and its 87/89 companion split is off by the same pattern). Neither
error affects which lane any individual file is in — both are arithmetic/
summary-reporting errors in the tables' own prose, not misclassifications —
but both are exactly the "stale estimate reused" failure mode this task exists
to catch, one level up from the design doc's own already-flagged 260/118
and 6/76 figures. This document's own §1 table is the first place a verified,
reconciled, cross-checked total exists for both directories combined.

Full per-lane file listings are in Appendix A.

## 2. Target directory structure

**Precedent checked**: `docs/archive-2026-08-27/` exists in this repo (8
files: `prediction-market-strategy-alignment-plan.md`,
`position-management-findings-2026-08-17.md`,
`platform-deep-scan-findings-2026-08-10.md`, `next-session-pickup-2026-08-22.md`,
`todo-2026-08-14-heuristics-audit-and-exit-tuning.md`,
`next-steps-2026-08-15.md`, `advisory-engine-plan.md`,
`kalshi-whale-provider-and-strategy-porting-plan.md`). It is **completely
flat** — no subdirectories, no plans/specs/research split, no lane concept
(it predates lanes entirely; it was the destination for a single 2026-08-27
docs cleanup, not an ongoing per-lane archive). The design's own instruction
is "`docs/archive/` with per-lane subdirectories, not
`docs/archive-2026-08-27/` (that name is specific to the PR that already used
it)" — meaning both a different top-level name *and* internal structure the
old precedent never had.

**Recommendation: deliberately deviate from the flat 2026-08-27 precedent.**
Propose `docs/archive/lane-<N>-<slug>/` for each of the 9 lanes, using the
lane number plus a short slug derived from `labels.py`'s own `LANES[N]["name"]`
(so the directory name is traceable to the single source of truth for lane
naming, not an independently invented slug):

| Lane | Directory |
|---|---|
| 1 | `docs/archive/lane-1-kalshi-ingestion/` |
| 2 | `docs/archive/lane-2-whale-signal-calibration/` |
| 3 | `docs/archive/lane-3-strategy-risk-execution/` |
| 4 | `docs/archive/lane-4-analytics-advisory-research/` |
| 5 | `docs/archive/lane-5-runtime-infrastructure/` |
| 6 | `docs/archive/lane-6-observability-quality-safety/` |
| 7 | *(not created — zero files ever land here; an empty directory for a lane with no archived docs would be noise, not signal)* |
| 8 | `docs/archive/lane-8-frontend-dashboard/` |
| 9 | `docs/archive/lane-9-tooling-ci-process-governance/` |

Convention: `lane-<N>-<3-to-5-word-slug>`, numeric prefix first so the 8
directories sort in lane order in a plain `ls`, slug short enough to stay
legible in a terminal listing but distinctive enough not to need the number to
disambiguate. Internally consistent across all 8 (same prefix pattern, same
slug-derivation rule — first few meaningful words of `LANES[N]["name"]`,
lowercased, hyphenated).

**Within each lane directory: preserve the plans/specs/research split**
(e.g. `docs/archive/lane-1-kalshi-ingestion/plans/`, `.../specs/`,
`.../research/`), rather than flattening everything into one directory per
lane. Checked, not assumed, whether anything relies on the split for more
than human browsing:

- `tools/kanban_sync/__main__.py:36` defines `PLANS_DIR =
  Path("docs/superpowers/plans")`, consumed by `sources_plan.
  list_plan_candidates(plans_dir)` (`sources_plan.py:70`,
  `all_plans = {p.name for p in plans_dir.glob("*.md")}`) at `__main__.py:220`
  and `:256`. This globs **that one directory only**, non-recursively.
- `tools/quality_coordination.py:597-598` independently constructs its own
  `plans_dir = repo_root / "docs" / "superpowers" / "plans"` and globs
  `plans_dir.glob("*.md")` to feed `collect_plan_doc_signals()` — a second,
  separate hardcoded dependency on the same directory, not sharing the
  `PLANS_DIR` constant.
- No code was found globbing `docs/superpowers/specs/` or
  `docs/superpowers/research/` at all (`git grep` across `services/`,
  `tools/`, `tests/`, `main.py`, `.claude/` found citation comments only,
  never a glob or programmatic directory read).

So nothing in the codebase distinguishes specs from research functionally —
only "is this a plan doc, in this one specific directory" is load-bearing (see
the hard-dependency finding in §4, which this directly feeds). Given that,
**flattening plans/specs/research together per lane would not break anything
mechanically today**, but it would erase the one distinction (plan-doc-ness)
that two real code paths still key off of, making a future fix to either of
them (see §4) harder to write and easier to get wrong (a flattened directory
requires re-deriving "which of these files used to be a plan doc" from
content or a naming convention instead of a path segment). Recommendation is
therefore to **keep the three-way split inside each lane directory** — it
costs nothing (both `docs/archive/lane-N-.../plans/*.md` and a flattened
equivalent are equally easy for a human or a future glob to read), preserves
the existing human mental model, and keeps the one genuinely load-bearing
distinction (plan vs. not) visible as a path segment rather than requiring
it be reconstructed later.

## 3. Live reference-count re-derivation, per lane batch

Methodology in §0.4. Results (README.md and the UNDECIDED file excluded;
full per-file detail in Appendix A):

| Lane | Files | Total refs (incl. lanes-table self-cites) | Lanes-table self-cites | **External refs needing fixing** | — code/tests/hooks | — narrative docs |
|---|---|---|---|---|---|---|
| 1 | 46 | 136 | 69 | **67** | 27 | 40 |
| 2 | 25 | 46 | 40 | **6** | 6 | 0 |
| 3 | 13 | 35 | 22 | **13** | 12 | 1 |
| 4 | 11 | 25 | 17 | **8** | 8 | 0 |
| 5 | 52 | 123 | 96 | **27** | 27 | 0 |
| 6 | 14 | 64 | 22 | **42** | 34 | 8 |
| 7 | 0 | 0 | 0 | **0** | 0 | 0 |
| 8 | 6 | 32 | 15 | **17** | 10 | 7 |
| 9 | 79 | 171 | 126 | **45** | 29 | 16 |
| **Total** | **246** | **632** | **407** | **225** | **153** | **72** |

("Lanes-table self-cites" = matches inside `docs/superpowers/lanes/`, i.e. the
three classification tables' own Path columns and cross-references citing
each other — a real but low-risk, uniformly-distributed, mechanically
batchable category: updating those three tables' own path citations once the
real move happens is its own small follow-up, not folded into the "external
refs needing fixing" figure that sizes each lane's real work, matching how
the task's "excluding the files' own directories" instruction was applied.)

**This is the actual work-sizing number the task asked this document to
produce: 225 external references, not the design doc's stale 260/118** (that
figure was for `plans/` alone against a different, narrower "outside plans/"
definition; this document's scope is all three directories together, and its
own exclusion rule is stated in §0.4 rather than assumed to match). The
per-lane breakdown demonstrates the task's own point directly: **Lane 1 (46
files) has the highest reference burden (67) of any lane, higher than Lane 9
(79 files, 45 refs) despite having 33 fewer files** — file count and
reference-fix burden do not track each other, so batch size for ordering
purposes is defined by the reference column, not the file column (§4).

### GitHub issue counts (live, not carried forward)

- **6 open issues with a title genuinely starting `Plan: `** (a raw
  `--search "Plan:"` query returns 87 hits; filtering to an actual `Plan: `
  title prefix narrows it to 6): `#89` (frontend-modularization, Lane 8),
  `#331` (weather-index-ingestion, Lane 1), `#321` (claudesuperpower-plugin-
  pilot, Lane 9), `#81` (autonomous-engineering-mode, Lane 9), `#448`
  (tier0-live-incident-remediation, Lane 6), `#320` (whale-confidence-
  scoring-remediation-implementation, Lane 2).
- **76 open issues labeled `type:plan-task`.**
- Both numbers happen to **match** the design doc's own 2026-09-06-PM figures
  exactly — re-derived live and independently confirmed, not reused on the
  strength of the design doc's citation. (Method, not luck: fetched fresh via
  `gh issue list --state open`, filtered/labeled queries, at the time this
  document was written; a later re-check before execution should re-run the
  same two `gh` commands rather than trust this number either, per the same
  logic that produced it.)
- **Method for "how many reference a moving path, per lane": full check, not
  a sample.** All 76 issues' title+body text (already fetched in the one `gh`
  call above, so checking the full set cost nothing extra) were tested
  against every one of the 246 batched filenames. All 76 matched **exactly
  one** lane each (zero unmatched, zero double-matched):

| Lane | Open `type:plan-task` issues referencing this batch |
|---|---|
| 1 | 15 |
| 2 | 8 |
| 3 | 5 |
| 4 | 0 |
| 5 | 9 |
| 6 | 10 |
| 7 | 0 |
| 8 | **22** |
| 9 | 7 |
| **Total** | **76** |

Lane 8 carries the single largest GitHub-side re-pointing burden (22 of 76)
despite being the second-smallest lane by file count (6) — the
frontend-modularization plan spawned a long, still-open Task-N sub-issue
chain (spot-checked: `#465` "Task 4: T1c — Mechanical move to `legacy/`",
`#481` "Task 20: T9 — Cleanup, strict guards, docs sync," both carrying a
`lane:8` label already, confirming step 2's issue-labeling pass reached
them).

## 4. Batch order

**Proposed order: Lane 4 → Lane 8 → Lane 5 → Lane 6 → Lane 3 → Lane 2 → Lane 1
→ Lane 9.** (Lane 7 has no files and is not a batch.)

Justification, against the task's three named criteria plus one this
investigation found:

**Reference-count "smallness" (§3), not file count.** Sorted by external
refs needing fixing: Lane 2 (6) < Lane 4 (8) < Lane 3 (13) < Lane 8 (17) <
Lane 5 (27) < Lane 6 (42) < Lane 9 (45) < Lane 1 (67). Pure reference-count
order would start with Lane 2 — but Lane 2 is deliberately *not* first, for
the next reason.

**Active-status risk (a different axis from reference count, per the task's
own framing).** Per-lane active-fraction, counting every file (plan +
companion + spec/research) already classified `active` by the source tables:

| Lane | Active files / total | Active-fraction | Active **primary plan docs** (the ones that matter for §4's hard-dependency finding) |
|---|---|---|---|
| 1 | 2/46 | 4% | 1 (`2026-08-25-realtime-data-plane-remediation.md`) |
| 2 | 19/25 | **76%** | 1 (`2026-08-30-whale-confidence-scoring-remediation-implementation.md`) |
| 3 | 6/13 | 46% | 2 (`2026-08-26-economic-strategy-effectiveness-investigation.md`, `2026-08-26-economic-strategy-remediation.md`) |
| 4 | 0/11 | 0% | 0 |
| 5 | 10/52 | 19% | 0 |
| 6 | 1/14 | 7% | 1 (`2026-09-03-tier0-live-incident-remediation.md`) |
| 8 | 0/6 | 0% | 0 |
| 9 | 19/79 | 24% | 0 |

Lane 2 has the **lowest raw reference count of any populated lane (6) but the
highest active-fraction of any lane (76%)** — the task's "small batch, many
references = risky" caution has a mirror image here: a small-by-reference
batch that is nonetheless the single riskiest lane to move by a different
measure (an actively-worked initiative cluster is the most likely place a
*new* companion file appears between this table's snapshot and actual
execution, which would silently violate the atomicity rule in §5 if the move
executes off a stale file list). Lane 2 is therefore ordered well after the
numerically-larger but numerically-inert Lane 5, specifically because of this
axis, not despite it.

**Hard dependency found (new — not in the design doc's own touchpoint list):**
`tools/kanban_sync/__main__.py:36`'s `PLANS_DIR = Path("docs/superpowers/
plans")` (feeding `sources_plan.list_plan_candidates()`) and
`tools/quality_coordination.py:597-598`'s independently-constructed
`plans_dir` (feeding `collect_plan_doc_signals()`) both do a non-recursive
`.glob("*.md")` scoped to exactly that one directory. **Moving any plan file
out of `docs/superpowers/plans/` makes it permanently invisible to both
mechanisms** — fine for `done`/`declined`/`superseded`/`stalled`/
`never-started` plans (the doc has become historical; ceasing candidate-
tracking is the correct outcome), but a genuine functional regression for the
**5 primary plan docs still `active`**: Lane 1 (1), Lane 2 (1), Lane 3 (2),
Lane 6 (1). This is flagged as a judgment call in §6, not resolved here (a
runtime-code fix is beyond a file-move plan's scope), but it directly informs
ordering: **lanes with an active primary plan doc move later**, after the
question in §6 is answered, rather than earlier where the regression would be
locked in before anyone decided how to handle it. This also independently
found the one filename-literal dependency the task asked to check for:
`tools/kanban_sync/sources_plan.py:48`'s `_EXCLUDED_FILENAMES = frozenset({
"README.md", "2026-08-26-active-tracks-board.md"})` hardcodes the tracks-board
filename. Moving that file (Lane 9) does **not** break this — once it leaves
`docs/superpowers/plans/`, `list_plan_candidates()`'s glob simply never
returns it again, so the exclusion entry becomes inert, unreachable dead code
rather than a broken reference. It should still be deleted as a small hygiene
fix in the same commit as Lane 9's move (not a blocking dependency, just a
correctness cleanup due at the same time).

**Reasoned order, lane by lane:**

1. **Lane 4** — the only lane clean on every axis: 0% active anywhere, lowest
   plan-task-issue count (0), no `Plan:` issue, near-lowest reference count
   (8). True pilot batch: proves the move mechanism on inert content first.
2. **Lane 8** — 0% active, low reference count (17), but the **single
   largest GitHub re-pointing burden of any lane (22 plan-task issues)**.
   Second batch deliberately exercises the issue-re-pointing half of the
   mechanism (closing/re-pointing a long Task-N chain) while code-risk is
   still zero, before combining that work with an active-status lane.
3. **Lane 5** — the second-largest lane (52 files) but zero active *primary
   plan* docs (its 10 active files are specs/research only, so no PLANS_DIR
   exposure) and a moderate, self-contained reference count (27). Proves the
   mechanism scales to a large batch before tackling the harder, smaller-but-
   riskier lanes.
4. **Lane 6** — 14 files, the highest reference-fix density relative to its
   size (42, mostly code/test citations of `diagnostics/`), and exactly 1
   active primary plan (`tier0-live-incident-remediation`, tracked live by
   open issue `#448`). Moderate risk on every axis; a reasonable midpoint.
5. **Lane 3** — 13 files, low reference count (13), but 46% active including
   **2** active primary plans. Requires the §5 fresh-companion-rescan
   safeguard and the §6 PLANS_DIR decision to be settled first.
6. **Lane 2** — lowest reference count of any populated lane (6) but the
   **highest active-fraction of any lane (76%)** and 1 active primary plan.
   Ordered here, not earlier, specifically because of that risk (see above);
   needs the same rescan/PLANS_DIR prerequisites as Lane 3, more urgently.
7. **Lane 1** — the **highest reference-fix burden of any lane (67, driven by
   40 narrative-doc citations)** plus 1 active primary plan plus the
   second-highest plan-task-issue count (15). **Tension flagged explicitly**:
   the design doc quotes David's own standing ask that "Kalshi ingestion and
   its prerequisites first" — this plan orders Lane 1 second-to-last, the
   opposite of "first," because for a pure archival/reorganization operation
   its numerically highest blast radius argues for doing it once the
   mechanism is proven on 6 lower-risk lanes, not first. This may be reading
   "Kalshi first" too narrowly (it likely meant substantive engineering
   priority, not a housekeeping file move) — but it is exactly the kind of
   reading that shouldn't be decided unilaterally, so it's surfaced in §6
   rather than resolved here.
8. **Lane 9** — 79 files, the largest lane, moved **strictly last for a hard
   reason, not a risk-ranking preference**: Lane 9's own file set includes
   `2026-09-06-planning-lanes-design.md` and its 8 companions (all
   `status: active`) — the design document currently governing this exact
   migration. Archiving the document that describes an in-progress process
   before the process finishes would be incoherent regardless of any
   numeric score. The `sources_plan.py` hygiene cleanup (above) lands in this
   batch's commit.

## 5. Review-companion atomicity

**Rule this plan commits to: every companion moves in the exact same commit
as its named parent — never split across batches, never left behind.** This
is not a new rule; it restates the design's own step 4 text ("review
companions atomic with their named parent") as a concrete commitment this
document's own batching honors.

Confirmed, not assumed, that §1's per-lane file lists already group companions
with their parents:

- **All 25 `plans/`-table companions** were resolved to their parent's lane
  by direct lookup against that same parent's own row in the same table (the
  parser logs any companion whose named parent can't be found; it found
  none) — by construction, every companion in Appendix A's per-lane lists sits
  in the same lane as its parent.
- **All 95 content-detected "companion to `<path>`" references** in the
  specs/research table were checked — not sampled — against their named
  parent's own lane cell; **zero mismatches**. (This is the specs/research
  table's own author's inheritance, independently re-verified here rather
  than trusted on the table's own claim.)
- Spot-checked at the file-path level (do parent and companion actually sit
  in the same lane bucket in Appendix A, not just "were classified with the
  same lane number" in the abstract): `2026-08-30-kalshi-category-data-
  completeness-implementation.md` (Lane 1, plan) and its 1 plan-table
  companion + 3 specs-table companions all appear together in Lane 1's
  Appendix A listing; `2026-09-03-tier1-backend-hygiene.md` (Lane 5) and its
  5 plan-table companions all appear together in Lane 5's listing;
  `2026-09-06-planning-lanes-design.md` (Lane 9) and its 8 companions all
  appear together in Lane 9's listing (self-referentially — this is the
  document this task itself extended, see §4's Lane 9 ordering reason).

## 6. Explicitly out of scope / not decided here

Honest account of what this plan does **not** resolve, per the task's own
instruction not to paper over a judgment call by picking an answer now:

1. **`docs/superpowers/plans/README.md`'s retirement** (design §6 step 5) —
   a separate, later step. This document excludes it from every batch and
   every reference-count total (§0.4) rather than silently moving or
   retiring it under step 4's authority.
2. **The 1 UNDECIDED file**, `docs/superpowers/specs/2026-08-27-backend-
   services-modularization-design.md` — genuinely split across 4 lanes
   (history→4, config→7, position→3, reset→6) with no stated primary,
   per `step1-specs-research-classification.md`'s own "Unlaned / UNDECIDED"
   section. This plan does not force it into a lane to make its own
   arithmetic come out even; it stays excluded pending the straddler-rule
   refinement the classification tables themselves called for (a "no-subject
   batch" rule or an explicit split-into-per-task-issues escape hatch,
   per the design's own §13/step1-plans-classification's G3/G4 findings).
3. **The `PLANS_DIR` / `quality_coordination.py` hard dependency (§4)** — a
   genuine, newly-found functional regression for the 5 currently-`active`
   primary plan docs (Lanes 1, 2, 3×2, 6) once they move out of
   `docs/superpowers/plans/`. This plan does not propose a specific code fix
   (teach both consumers to glob `docs/archive/**/plans/*.md` too; hold
   active-status primary plans back from archiving until they settle; or
   something else) — that is a runtime-behavior design decision for
   `tools/kanban_sync`/`tools/quality_coordination.py`, adjacent to but
   beyond a file-move plan's scope, and David should weigh in on which
   approach before Lanes 1, 2, 3, or 6 execute.
4. **The "Kalshi ingestion first" tension (§4, Lane 1's position)** — flagged,
   not resolved. This plan reads David's standing instruction as being about
   substantive engineering priority, not this specific archival operation's
   batch order, and places Lane 1 second-to-last on that reading; David
   should confirm or correct that reading before execution.
5. **Whether "excluding the files' own directories" (§0.4) should have been
   applied per-file-type (a plan's own directory only) versus uniformly
   across all three planning directories (this document's actual choice)** —
   stated as a deliberate scope decision, not re-litigated here; a narrower
   reading would raise every lane's "external refs" number by counting
   cross-directory planning-doc citations (e.g. a spec citing a plan) as
   external. This document's total (225) is under the uniform-exclusion
   reading; the narrower reading's total was not separately computed.
6. **Actually moving any file, closing/re-pointing the 6 `Plan:` issues or 76
   `type:plan-task` issues, or creating the `docs/archive/lane-N-*/`
   directories** — none of that executes from this document. This is a
   planning artifact only, per the task's explicit instruction.
## Appendix A: per-lane file batches, reference counts, and issue re-pointing counts

Generated directly from the verified data files (`master_list.json`, `ref_counts.json`, `plan_task_lane_refs.json`, `plan_prefixed_issues.json`) produced during this document's investigation -- no manual transcription. `docs/superpowers/plans/README.md` is excluded throughout (out of scope, see §6).

### Lane 1 -- Kalshi & index data ingestion (46 files)

- Plan docs (primary): 8; plan companions: 3; specs/research docs: 35
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **67** total (27 in code/tests/hooks, 40 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **15** -> [134, 135, 136, 138, 139, 140, 331, 332, 333, 334, 335, 336, 337, 338, 339]
- Open `Plan: ` tracking issues in this lane: **1** -> #331 (2026-08-31-weather-index-ingestion.md)


Plan docs (primary):

- `docs/superpowers/plans/2026-08-24-kalshi-integration-dual-phase.md` (done)
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md` (done)
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-c.md` (done)
- `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md` (done)
- `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md` (active)
- `docs/superpowers/plans/2026-08-30-kalshi-category-data-completeness-implementation.md` (done)
- `docs/superpowers/plans/2026-08-31-weather-index-ingestion.md` (declined)
- `docs/superpowers/plans/2026-09-01-event-loop-blocking-fix1.md` (done)

Plan companions (move atomically with named parent):

- `docs/superpowers/plans/2026-08-30-kalshi-category-data-completeness-implementation-catchup-consolidation.md` -> companion of `docs/superpowers/plans/2026-08-30-kalshi-category-data-completeness-implementation.md`
- `docs/superpowers/plans/2026-08-31-weather-index-ingestion-consolidation.md` -> companion of `docs/superpowers/plans/2026-08-31-weather-index-ingestion.md`
- `docs/superpowers/plans/2026-08-31-weather-index-ingestion-review.md` -> companion of `docs/superpowers/plans/2026-08-31-weather-index-ingestion.md`

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/research/2026-08-24-kalshi-integration-audit.md` (done)
- `docs/superpowers/research/2026-08-25-realtime-architecture-review.md` (done)
- `docs/superpowers/research/2026-08-25-realtime-data-plane-baseline.md` (done)
- `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md` (done)
- `docs/superpowers/research/2026-08-25-realtime-live-baseline.md` (done)
- `docs/superpowers/research/2026-08-25-realtime-replay-baseline.md` (done)
- `docs/superpowers/research/2026-08-25-realtime-root-cause-report.md` (done)
- `docs/superpowers/research/2026-08-25-realtime-solution-research.md` (done)
- `docs/superpowers/research/2026-08-25-rest-demand-study.md` (done)
- `docs/superpowers/research/2026-08-25-rest-solution-comparison.md` (done)
- `docs/superpowers/research/2026-08-25-ws-solution-comparison.md` (done)
- `docs/superpowers/research/2026-08-27-application-wide-rest-vs-ws-inventory.md` (done)
- `docs/superpowers/research/2026-08-30-kalshi-category-data-shape-audit-review.md` (done)
- `docs/superpowers/research/2026-08-30-kalshi-category-data-shape-audit-revision-review.md` (done)
- `docs/superpowers/research/2026-08-30-kalshi-category-data-shape-audit.md` (done)
- `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md` (done)
- `docs/superpowers/specs/2026-08-25-realtime-data-plane-investigation-design.md` (done)
- `docs/superpowers/specs/2026-08-25-realtime-data-plane-remediation-design.md` (active)
- `docs/superpowers/specs/2026-08-30-kalshi-category-data-completeness-design-review.md` (done)
- `docs/superpowers/specs/2026-08-30-kalshi-category-data-completeness-design-revision-review.md` (done)
- `docs/superpowers/specs/2026-08-30-kalshi-category-data-completeness-design-revision2-review.md` (done)
- `docs/superpowers/specs/2026-08-30-kalshi-category-data-completeness-design.md` (done)
- `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design-consolidation.md` (declined)
- `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design-review.md` (declined)
- `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design.md` (declined)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-elimination-design.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix1-pr-review.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-plan-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-plan-review.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-pr-adversarial-review.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-pr-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-pr-self-review.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-pr420-adversarial-review.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-pr420-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-pr420-self-review.md` (done)

### Lane 2 -- Whale signal detection & calibration (25 files)

- Plan docs (primary): 3; plan companions: 1; specs/research docs: 21
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **6** total (6 in code/tests/hooks, 0 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **8** -> [320, 364, 365, 366, 367, 368, 369, 370]
- Open `Plan: ` tracking issues in this lane: **1** -> #320 (2026-08-30-whale-confidence-scoring-remediation-implementation.md)


Plan docs (primary):

- `docs/superpowers/plans/2026-08-30-whale-confidence-scoring-remediation-implementation.md` (active)
- `docs/superpowers/plans/2026-09-01-whale-scoring-connection-reuse.md` (done)
- `docs/superpowers/plans/2026-09-04-scoring-pool-candidate-retry-isolation-implementation.md` (done)

Plan companions (move atomically with named parent):

- `docs/superpowers/plans/2026-08-30-whale-confidence-scoring-remediation-implementation-catchup-consolidation.md` -> companion of `docs/superpowers/plans/2026-08-30-whale-confidence-scoring-remediation-implementation.md`

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/research/2026-08-30-whale-confidence-weights-factor-audit-review.md` (done)
- `docs/superpowers/research/2026-08-30-whale-confidence-weights-factor-audit-revision-review.md` (done)
- `docs/superpowers/research/2026-08-30-whale-confidence-weights-factor-audit.md` (done)
- `docs/superpowers/research/2026-09-03-seen-trade-ids-concurrency-race-root-cause-adversarial-review.md` (active)
- `docs/superpowers/research/2026-09-03-seen-trade-ids-concurrency-race-root-cause-consolidation.md` (active)
- `docs/superpowers/research/2026-09-03-seen-trade-ids-concurrency-race-root-cause-self-review.md` (active)
- `docs/superpowers/research/2026-09-03-seen-trade-ids-concurrency-race-root-cause.md` (active)
- `docs/superpowers/research/2026-09-03-trade-resolve-bounded-concurrency-implementation-self-review.md` (active)
- `docs/superpowers/research/2026-09-03-trade-resolve-consumer-blocking-solution-comparison-adversarial-review.md` (active)
- `docs/superpowers/research/2026-09-03-trade-resolve-consumer-blocking-solution-comparison-consolidation.md` (active)
- `docs/superpowers/research/2026-09-03-trade-resolve-consumer-blocking-solution-comparison-self-review.md` (active)
- `docs/superpowers/research/2026-09-03-trade-resolve-consumer-blocking-solution-comparison.md` (active)
- `docs/superpowers/research/2026-09-03-trade-stream-decoupling-and-history-event-driven-research-consolidation.md` (active)
- `docs/superpowers/research/2026-09-03-trade-stream-decoupling-and-history-event-driven-research-self-review.md` (active)
- `docs/superpowers/research/2026-09-03-trade-stream-decoupling-and-history-event-driven-research.md` (active)
- `docs/superpowers/specs/2026-08-30-whale-confidence-scoring-remediation-design-review.md` (active)
- `docs/superpowers/specs/2026-08-30-whale-confidence-scoring-remediation-design-safety-fix-review.md` (active)
- `docs/superpowers/specs/2026-08-30-whale-confidence-scoring-remediation-design.md` (active)
- `docs/superpowers/specs/2026-09-03-scoring-pool-candidate-retry-isolation-design-consolidation.md` (active)
- `docs/superpowers/specs/2026-09-03-scoring-pool-candidate-retry-isolation-design-self-review.md` (active)
- `docs/superpowers/specs/2026-09-03-scoring-pool-candidate-retry-isolation-design.md` (active)

### Lane 3 -- Strategy, risk & execution (13 files)

- Plan docs (primary): 5; plan companions: 2; specs/research docs: 6
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **13** total (12 in code/tests/hooks, 1 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **5** -> [289, 290, 291, 292, 293]
- Open `Plan: ` tracking issues in this lane: **0**


Plan docs (primary):

- `docs/superpowers/plans/2026-08-26-economic-strategy-effectiveness-investigation.md` (active)
- `docs/superpowers/plans/2026-08-26-economic-strategy-remediation.md` (active)
- `docs/superpowers/plans/2026-08-29-event-scoped-me-gate.md` (superseded)
- `docs/superpowers/plans/2026-08-30-entry-gate-me-pairing-and-netting-remediation.md` (done)
- `docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation.md` (done)

Plan companions (move atomically with named parent):

- `docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation-consolidation.md` -> companion of `docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation.md`
- `docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation-review.md` -> companion of `docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation.md`

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md` (active)
- `docs/superpowers/specs/2026-08-29-event-scoped-me-gate-design.md` (declined)
- `docs/superpowers/specs/2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md` (done)
- `docs/superpowers/specs/2026-09-03-strategy-edge-gate-design-consolidation.md` (active)
- `docs/superpowers/specs/2026-09-03-strategy-edge-gate-design-review.md` (active)
- `docs/superpowers/specs/2026-09-03-strategy-edge-gate-design.md` (active)

### Lane 4 -- Analytics, advisory & research (11 files)

- Plan docs (primary): 2; plan companions: 0; specs/research docs: 9
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **8** total (8 in code/tests/hooks, 0 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **0**
- Open `Plan: ` tracking issues in this lane: **0**


Plan docs (primary):

- `docs/superpowers/plans/2026-08-27-backend-services-modularization.md` (done)
- `docs/superpowers/plans/2026-08-30-self-feeding-loop-provenance.md` (done)

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/research/2026-08-26-economic-advisory-calibration-execution-audit.md` (done)
- `docs/superpowers/research/2026-08-26-economic-gate-marginal-contribution.md` (done)
- `docs/superpowers/research/2026-08-26-economic-population-and-replay-gaps.md` (done)
- `docs/superpowers/research/2026-08-26-economic-strategy-effectiveness-adversarial-review.md` (done)
- `docs/superpowers/research/2026-08-26-economic-strategy-effectiveness-status-report.md` (done)
- `docs/superpowers/research/2026-08-27-economic-e3-e5-reverification.md` (done)
- `docs/superpowers/research/2026-08-29-trade-performance-analysis.md` (done)
- `docs/superpowers/specs/2026-08-26-economic-strategy-effectiveness-investigation-design.md` (done)
- `docs/superpowers/specs/2026-08-30-self-feeding-loop-provenance-design.md` (done)

### Lane 5 -- Runtime infrastructure (52 files)

- Plan docs (primary): 3; plan companions: 12; specs/research docs: 37
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **27** total (27 in code/tests/hooks, 0 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **9** -> [489, 490, 491, 492, 493, 494, 495, 496, 497]
- Open `Plan: ` tracking issues in this lane: **0**


Plan docs (primary):

- `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation.md` (done)
- `docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md` (done)
- `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md` (done)

Plan companions (move atomically with named parent):

- `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation-consolidation.md` -> companion of `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation.md`
- `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation-self-review.md` -> companion of `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation.md`
- `docs/superpowers/plans/2026-09-03-persistence-layer-implementation-consolidation.md` -> companion of `docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md`
- `docs/superpowers/plans/2026-09-03-persistence-layer-implementation-pr-consolidation.md` -> companion of `docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md`
- `docs/superpowers/plans/2026-09-03-persistence-layer-implementation-pr-self-review.md` -> companion of `docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md`
- `docs/superpowers/plans/2026-09-03-persistence-layer-implementation-review.md` -> companion of `docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md`
- `docs/superpowers/plans/2026-09-03-persistence-layer-task8-candidate-ledger-self-review.md` -> companion of `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation.md`
- `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene-consolidation.md` -> companion of `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`
- `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene-pr-consolidation.md` -> companion of `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`
- `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene-pr-review.md` -> companion of `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`
- `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene-pr-self-review.md` -> companion of `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`
- `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene-review.md` -> companion of `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/research/2026-09-03-fault-log-null-exc-type-dedup-consolidation.md` (done)
- `docs/superpowers/research/2026-09-03-fault-log-null-exc-type-dedup-self-review.md` (done)
- `docs/superpowers/research/2026-09-03-persistence-layer-db-migration-consolidation.md` (done)
- `docs/superpowers/research/2026-09-03-persistence-layer-db-migration-pr-consolidation.md` (done)
- `docs/superpowers/research/2026-09-03-persistence-layer-db-migration-pr-review.md` (done)
- `docs/superpowers/research/2026-09-03-persistence-layer-db-migration-self-review.md` (done)
- `docs/superpowers/research/2026-09-03-persistence-layer-db-migration.md` (done)
- `docs/superpowers/research/2026-09-03-worker-cpu-pin-and-loop-stalls-adversarial-review.md` (active)
- `docs/superpowers/research/2026-09-03-worker-cpu-pin-and-loop-stalls-consolidation.md` (active)
- `docs/superpowers/research/2026-09-03-worker-cpu-pin-and-loop-stalls-self-review.md` (active)
- `docs/superpowers/research/2026-09-03-worker-cpu-pin-and-loop-stalls.md` (active)
- `docs/superpowers/research/2026-09-04-issue-410-tick-executor-measurement-self-review.md` (done)
- `docs/superpowers/research/2026-09-04-issue-410-tick-executor-measurement.md` (done)
- `docs/superpowers/research/2026-09-05-issue-150-fix-family-benchmark-consolidation.md` (done)
- `docs/superpowers/research/2026-09-05-issue-150-fix-family-benchmark-self-review.md` (done)
- `docs/superpowers/research/2026-09-05-issue-150-fix-family-benchmark.md` (done)
- `docs/superpowers/research/2026-09-05-issue-530-sync-dispatch-sweep-adversarial-review.md` (active)
- `docs/superpowers/research/2026-09-05-issue-530-sync-dispatch-sweep-consolidation.md` (active)
- `docs/superpowers/research/2026-09-05-issue-530-sync-dispatch-sweep-self-review.md` (active)
- `docs/superpowers/research/2026-09-05-issue-530-sync-dispatch-sweep.md` (active)
- `docs/superpowers/specs/2026-09-01-diagnostics-pool-addition-review.md` (done)
- `docs/superpowers/specs/2026-09-01-loop-watchdog-fault-visibility-pr417-consolidation.md` (active)
- `docs/superpowers/specs/2026-09-01-loop-watchdog-fault-visibility-pr417-review.md` (active)
- `docs/superpowers/specs/2026-09-01-whale-scoring-connection-reuse-design-review.md` (done)
- `docs/superpowers/specs/2026-09-01-whale-scoring-connection-reuse-design.md` (done)
- `docs/superpowers/specs/2026-09-01-write-path-capacity-fix-pr-review-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-01-write-path-capacity-fix-pr-review.md` (done)
- `docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design-pr-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design-pr-scoped-recheck.md` (done)
- `docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design-pr-self-review.md` (done)
- `docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design-self-review.md` (done)
- `docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design.md` (done)
- `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design-consolidation.md` (superseded)
- `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design-review.md` (superseded)
- `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md` (superseded)
- `docs/superpowers/specs/2026-09-04-issue-410-pool-vs-aiosqlite-design.md` (done)

### Lane 6 -- Observability, quality & safety infra (14 files)

- Plan docs (primary): 4; plan companions: 3; specs/research docs: 7
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **42** total (34 in code/tests/hooks, 8 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **10** -> [448, 449, 450, 451, 452, 453, 454, 455, 457, 458]
- Open `Plan: ` tracking issues in this lane: **1** -> #448 (2026-09-03-tier0-live-incident-remediation.md)


Plan docs (primary):

- `docs/superpowers/plans/2026-08-24-quality-control-plane.md` (done)
- `docs/superpowers/plans/2026-08-30-data-retention-pruning.md` (done)
- `docs/superpowers/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md` (done)
- `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md` (active)

Plan companions (move atomically with named parent):

- `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation-consolidation.md` -> companion of `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`
- `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation-plan-review.md` -> companion of `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`
- `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation-pr-review.md` -> companion of `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/research/2026-09-04-quality-summary-event-loop-fix-self-review.md` (done)
- `docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-adversarial-review.md` (done)
- `docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-full-branch-review.md` (done)
- `docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-pr424-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-pr424-self-review.md` (done)
- `docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-self-review.md` (done)

### Lane 7 -- Config & control plane (0 files)

*(zero files in this lane's batch -- nothing to move.)*

### Lane 8 -- Frontend & dashboard (6 files)

- Plan docs (primary): 1; plan companions: 2; specs/research docs: 3
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **17** total (10 in code/tests/hooks, 7 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **22** -> [58, 89, 462, 463, 464, 465, 466, 467, 468, 469, 470, 471, 472, 473, 474, 475, 476, 477, 478, 479, 480, 481]
- Open `Plan: ` tracking issues in this lane: **1** -> #89 (2026-08-25-frontend-modularization.md)


Plan docs (primary):

- `docs/superpowers/plans/2026-08-25-frontend-modularization.md` (never-started)

Plan companions (move atomically with named parent):

- `docs/superpowers/plans/2026-08-25-frontend-modularization-catchup-consolidation.md` -> companion of `docs/superpowers/plans/2026-08-25-frontend-modularization.md`
- `docs/superpowers/plans/2026-09-03-frontend-modularization-freshness-check.md` -> companion of `docs/superpowers/plans/2026-08-25-frontend-modularization.md`

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/research/2026-08-25-frontend-modularization-research.md` (done)
- `docs/superpowers/specs/2026-08-25-frontend-modularization-design.md` (stalled)
- `docs/superpowers/specs/2026-09-03-history-event-driven-design.md` (done)

### Lane 9 -- Tooling, CI & process governance (79 files)

- Plan docs (primary): 11; plan companions: 2; specs/research docs: 66
- External references needing path fixes (git grep, excluding self-cites within `docs/superpowers/lanes/`): **45** total (29 in code/tests/hooks, 16 in narrative docs)
- Open `type:plan-task` issues referencing a path in this batch: **7** -> [81, 321, 323, 324, 325, 327, 328]
- Open `Plan: ` tracking issues in this lane: **2** -> #321 (2026-08-31-claudesuperpower-plugin-pilot.md), #81 (2026-08-26-autonomous-engineering-mode.md)


Plan docs (primary):

- `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md` (done)
- `docs/superpowers/plans/2026-08-26-active-tracks-board.md` (stalled)
- `docs/superpowers/plans/2026-08-26-autonomous-engineering-mode.md` (never-started)
- `docs/superpowers/plans/2026-08-26-autonomous-quality-coordination.md` (done)
- `docs/superpowers/plans/2026-08-26-kanban-board-sync.md` (done)
- `docs/superpowers/plans/2026-08-27-autonomous-quality-coordination-workflow.md` (done)
- `docs/superpowers/plans/2026-08-27-kanban-sync-milestones-and-subissues.md` (done)
- `docs/superpowers/plans/2026-08-27-workflow-remediation.md` (done)
- `docs/superpowers/plans/2026-08-28-kanban-sync-improvements.md` (done)
- `docs/superpowers/plans/2026-08-31-claudesuperpower-plugin-pilot.md` (never-started)
- `docs/superpowers/plans/2026-09-03-ci-pipeline-audit-tier1-fixes.md` (done)

Plan companions (move atomically with named parent):

- `docs/superpowers/plans/2026-08-31-claudesuperpower-plugin-pilot-consolidation.md` -> companion of `docs/superpowers/plans/2026-08-31-claudesuperpower-plugin-pilot.md`
- `docs/superpowers/plans/2026-08-31-claudesuperpower-plugin-pilot-review.md` -> companion of `docs/superpowers/plans/2026-08-31-claudesuperpower-plugin-pilot.md`

Specs/research docs (companions already resolved to parent lane by the source table):

- `docs/superpowers/research/2026-08-25-active-work-suppression-matrix.md` (done)
- `docs/superpowers/research/2026-08-25-autonomous-quality-architecture-decision.md` (done)
- `docs/superpowers/research/2026-08-25-autonomous-quality-coordination-baseline.md` (done)
- `docs/superpowers/research/2026-08-25-autonomous-quality-coordination-known-findings.md` (done)
- `docs/superpowers/research/2026-08-25-autonomous-quality-threat-model.md` (done)
- `docs/superpowers/research/2026-08-25-ci-skip-heavy-suite-verification.md` (done)
- `docs/superpowers/research/2026-08-25-deterministic-remediation-inventory.md` (done)
- `docs/superpowers/research/2026-08-25-quality-control-plane-topologies.md` (done)
- `docs/superpowers/research/2026-08-25-quality-coordination-cadence.md` (done)
- `docs/superpowers/research/2026-08-25-quality-coordinator-simulation.md` (done)
- `docs/superpowers/research/2026-08-25-quality-event-fault-injection.md` (done)
- `docs/superpowers/research/2026-08-25-quality-finding-identity-audit.md` (done)
- `docs/superpowers/research/2026-08-25-quality-reporting-surfaces.md` (done)
- `docs/superpowers/research/2026-08-26-investigation-final-verification.md` (done)
- `docs/superpowers/research/2026-08-30-session-tooling-friction-log.md` (done)
- `docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md` (stalled)
- `docs/superpowers/research/2026-08-31-claudesuperpower-toolkit-assessment-consolidation.md` (done)
- `docs/superpowers/research/2026-08-31-claudesuperpower-toolkit-assessment-review.md` (done)
- `docs/superpowers/research/2026-08-31-claudesuperpower-toolkit-assessment.md` (done)
- `docs/superpowers/research/2026-08-31-followups-from-3-plan-implementation.md` (never-started)
- `docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md` (active)
- `docs/superpowers/research/2026-09-02-architecture-audit-consolidation.md` (active)
- `docs/superpowers/research/2026-09-02-architecture-audit-second-pass-adversarial-review.md` (active)
- `docs/superpowers/research/2026-09-02-architecture-audit-second-pass-consolidation.md` (active)
- `docs/superpowers/research/2026-09-02-architecture-audit-second-pass-pr-review.md` (active)
- `docs/superpowers/research/2026-09-02-architecture-audit-second-pass-self-review.md` (active)
- `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md` (active)
- `docs/superpowers/research/2026-09-02-ci-pipeline-audit-cost-analysis.md` (done)
- `docs/superpowers/research/2026-09-02-ci-pipeline-audit-pytest-profile.md` (done)
- `docs/superpowers/research/2026-09-02-ci-pipeline-audit-woodpecker-mechanics.md` (done)
- `docs/superpowers/research/2026-09-02-ci-pipeline-audit.md` (done)
- `docs/superpowers/research/2026-09-03-cleanup-worktrees-silent-deploy-adversarial-review.md` (done)
- `docs/superpowers/research/2026-09-03-cleanup-worktrees-silent-deploy-consolidation.md` (done)
- `docs/superpowers/research/2026-09-03-cleanup-worktrees-silent-deploy-pr-adversarial-review.md` (done)
- `docs/superpowers/research/2026-09-03-cleanup-worktrees-silent-deploy-pr-consolidation.md` (done)
- `docs/superpowers/research/2026-09-03-cleanup-worktrees-silent-deploy-pr-self-review.md` (done)
- `docs/superpowers/research/2026-09-03-cleanup-worktrees-silent-deploy-self-review.md` (done)
- `docs/superpowers/research/2026-09-03-cleanup-worktrees-silent-deploy.md` (done)
- `docs/superpowers/specs/2026-08-24-quality-control-plane-design.md` (superseded)
- `docs/superpowers/specs/2026-08-25-autonomous-quality-coordination-investigation-design.md` (done)
- `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md` (stalled)
- `docs/superpowers/specs/2026-08-26-autonomous-quality-coordination-design.md` (superseded)
- `docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md` (done)
- `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md` (stalled)
- `docs/superpowers/specs/2026-08-27-kanban-sync-milestones-and-subissues-design.md` (done)
- `docs/superpowers/specs/2026-08-27-kanban-sync-project-status-field-design.md` (done)
- `docs/superpowers/specs/2026-08-27-workflow-audit.md` (declined)
- `docs/superpowers/specs/2026-08-28-kanban-sync-improvements-design.md` (done)
- `docs/superpowers/specs/2026-08-31-claudesuperpower-plugin-pilot-design-consolidation.md` (active)
- `docs/superpowers/specs/2026-08-31-claudesuperpower-plugin-pilot-design-review.md` (active)
- `docs/superpowers/specs/2026-08-31-claudesuperpower-plugin-pilot-design.md` (active)
- `docs/superpowers/specs/2026-09-02-ci-pipeline-audit-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-02-ci-pipeline-audit-self-review.md` (done)
- `docs/superpowers/specs/2026-09-03-ci-pipeline-audit-tier1-fixes-plan-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-03-ci-pipeline-audit-tier1-fixes-plan-self-review.md` (done)
- `docs/superpowers/specs/2026-09-03-ci-pipeline-audit-tier1-fixes-pr-consolidation.md` (done)
- `docs/superpowers/specs/2026-09-03-ci-pipeline-audit-tier1-fixes-pr-self-review.md` (done)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-adversarial-review-round2.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-adversarial-review.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-consolidation-round2.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-consolidation.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-recheck-2.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-recheck.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-self-review-round2.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design-self-review.md` (active)
- `docs/superpowers/specs/2026-09-06-planning-lanes-design.md` (active)

### Grand totals (Lanes 1-9, README.md and the 1 UNDECIDED file excluded)

- Files batched for movement: **246**
- Total external path references needing fixing: **225** (153 code/tests/hooks, 72 narrative docs)
- Open `type:plan-task` issues referencing a moved path, summed across lanes (some issues could in principle reference paths in more than one lane, but the live check found zero such overlaps): **76** (of 76 open `type:plan-task` issues total -- all 76 matched exactly one lane)
