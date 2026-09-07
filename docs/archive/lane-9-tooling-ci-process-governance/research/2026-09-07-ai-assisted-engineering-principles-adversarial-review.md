# Adversarial review — AI-assisted engineering principles research (2026-09-07)

Independent Agent-tool pass, no memory of the authoring session. Date: 2026-09-07.
Artifact: `docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-07-ai-assisted-engineering-principles.md`
(reviewed at worktree HEAD `c028a05`, base `6a63309` = the `main` the artifact was
written against; `origin/main` was `fe3699f`, 4 commits / 1 merged PR (#663) newer).
Every number below was re-derived from `gh` (REST + GraphQL), `git`, the live
article, the hook/label source, the memory directory, or the two ChatGPT input
docs — never from the artifact's tables. A companion `*-self-review.md` appeared
in the directory during this pass; it was not read.

## 1. §3.1 counts

**Artifact says:** 317 merged PRs; all-time prefix mix docs 145 · fix 90 · other/untyped 38 · feat 22 · chore 13 · refactor 4 · perf 2 · config 1; last 200 = 2026-08-30 → 09-07, ~22/day, 54 on 09-03, docs 104 · fix 66 · feat 10 · chore 7 · refactor 1 · other 12; additions p50 +200 / p90 +1,527 / max +6,045; 1,925 commits, 270 `fix`; 312 docs md, 122 (39%) review artifacts; 47 top-level; rule files 134+147+54=335.

**I found:**
- 317 merged PRs at the artifact's base (318 now; #663 merged 15:11Z). First merge 2026-08-25T04:30Z. CONFIRMED.
- All-time prefix row: the artifact's eight numbers sum to **315**, not 317 (`ci` 1 and `test` 1 dropped). Reproduced exactly with a strict `^(word):` regex: docs 145 · fix 90 · other 38 · feat 22 · chore 13 · refactor 4 · perf 2 · ci 1 · test 1 · config 1. The strict regex files 4 scoped `fix(...)` titles under "other"; a scope-tolerant regex gives fix 94 · feat 23 · chore 14 · untyped 32.
- Last-200 row: **only reproducible with the scope-tolerant regex** (docs 104 · fix 66 · feat 10 · chore 7 · refactor 1 · untyped 11 · config 1 = 200); the strict regex gives fix 63 · feat 9 · chore 6 · other 16. The two rows of one table used two different classifiers.
- Span 2026-08-30T15:35Z → 09-07T14:43Z (9 calendar days); 54 on 09-03. CONFIRMED.
- Additions last 200: p50 201 · p90 1,537 (linear interp) · max 6,045. CONFIRMED within rounding.
- `git rev-list --count 6a63309` = 1,925; `^fix[:(!]` subjects 270. CONFIRMED exactly.
- Docs md: `git ls-tree -r 6a63309 docs | grep .md$` = **551 incl. `docs/kalshi/` (230), 321 excluding it**. 312 is not reproducible by either count (321 − 312 = 9; `docs/superpowers/` holds 15, so excluding it doesn't explain it either). Review-named files (`self-review|adversarial-review|consolidation`) = 122 → **122/321 = 38%**, not 39%.
- Top-level `docs/*.md` = 47; lanes design line 18 cites 67 before. CONFIRMED. `wc -l` 134/147/54. CONFIRMED.

**Method:** `gh pr list --state merged --limit 1000 --json …` → scratchpad JSON; two regexes as stated; `git ls-tree` at base commit.
**Verdict:** CORRECTED: prefix row sums to 315 and mixes two classifiers; docs denominator is 321 (38%), not 312 (39%). Falsifier for my numbers: rerun the listed commands at `6a63309`.

## 2. §3.4 — 24 of 84 code-typed PRs with no artifact

**Artifact says:** 84 code-typed (fix|feat|refactor|chore) PRs in the last 200; 24 with zero comments, zero reviews, no review/consolidation file in the diff (list given); ≥10 of 21 fix/feat/refactor bodies narrate a review; 3 chore bodies unclassified; 0 hits across cited issues; #499 cited by three.

**I found:**
- GraphQL over all 200 (`comments.totalCount`, `reviews.totalCount`, `files.nodes.path`, `closingIssuesReferences`): **84 code-typed, 24 with none — identical list**, under both a strict (`self-review|adversarial|consolidation`) and a broad (`review|consolidat`) filename filter. CONFIRMED exactly.
- Elsewhere-sweep, all 24 (the artifact skipped the 3 chores): every issue cited in the body plus every GraphQL closing reference — **0 issue comments** mention the PR together with a review word. `git grep` over `docs/` + `.claude/` on `origin/main` for `#N` + review word: hits for #298, #499, #500, #502, #558, #573, #623, **all incidental mentions inside other initiatives' review docs** (lane-classification tables, a lane-5 consolidation correcting "PRs #499/#500" to "#499", a lane-2 consolidation noting #558 was omitted from a research doc, the branch audit). None is a review *of* the PR. #499 is cited by #500, #502, #623 → three. CONFIRMED.
- Body narration: 10 of 21 fix/feat/refactor (#301 #308 #446 #498 #499 #500 #502 #558 #573 #623) plus chores **#445 and #531** (not #389). "At least 10" CONFIRMED; the chore gap is closed: 12 of 24.
- **Definition hides code PRs.** 11 untyped titles in the window; 6 are code PRs with zero artifacts: #272 (account diagnostics), #273 (trades backfill tool), #297 (advisory/calibration provenance), #302 (backup retention), #421, #422 (trade-log P&L UI). #556/#559/#572 (DB migrations) are untyped but have comments. 17 `docs:`-prefixed PRs touch non-docs files (mostly `labels.py`/tooling in the lane migration); 5 have zero artifacts (#306 #429 #600 #649 #663). By "touches a non-`docs/` code/config file" the rate is **34 of 110 (31%)** — the artifact's 29% is a floor, not an overstatement.
- **Zero formal GitHub reviews on all 200 PRs** (0 of 200 have `reviews.totalCount > 0`; all 265 comments are by `thesneakattack`). "Zero formal reviews" is true of every PR in the repo and carries no information about the 24.

**Method:** GraphQL batch (20 PRs/query), `gh api repos/…/issues/N/comments --paginate`, `git grep -E '#N\b' origin/main -- docs .claude`.
**Verdict:** CONFIRMED (list exact, no hidden artifact found), CORRECTED: the prefix-based definition undercounts by ≥6 code PRs; state the rate as ≥29% and name the 6 untyped ones. Falsifier: a review comment on an issue none of these bodies cite — not checked, same gap the artifact declares.

## 3. §3.4 latency and "not a queue problem"

**Artifact says:** all p50 18 / p90 154 / max 813 min; docs 12/153; code 23/190.
**I found:** same window: all p50 18 / p90 154 / max 813; docs p50 13; code p50 23. p90 docs/code = 138/169 by linear interpolation, **153/190 by nearest-rank** — the artifact used nearest-rank; both defensible. Zero-artifact code PRs merge at **p50 10 min** vs 27 min for reviewed code PRs (p90 312). Inference holds: the PR stage is minutes; the "hour-plus" cost the 09-03 memory documents was ~2 h branch-to-commit plus ~30 min PR stage on #482, i.e. mostly pre-PR and invisible here — the artifact's §8 already says so.
**Verdict:** CONFIRMED. Falsifier: branch-creation-to-merge timing (first commit on branch → mergedAt) would measure the invisible part; not computed by either of us.

## 4. §3.5 follow-up-fix proxy and the confound argument

**Artifact says:** later `fix:` PR citing "PR #N": 24/135 (18%) all-time; last 200 split with-artifact 15/60 (25%) vs without 2/24 (8%); "confounded: large, risky PRs both get reviewed and get follow-ups"; falsifier row: "a size-banded recomputation showing the inversion persists within a band would mean something else is going on."

**I found:** strict `PR #N` citation: **25/135**; split **16/60 vs 2/24** (±1 from one newer fix). Size-banded, additions (strict): +0–100 with 2/12 vs without 0/8 · +100–400 5/25 vs 0/5 · +400–1000 4/14 vs 1/7 · +1000 5/9 vs 1/4. Files-banded: 0–3 4/17 vs 0/7 · 3–8 6/26 vs 0/6 · 8–20 2/12 vs 1/9 · 20+ 4/5 vs 1/2. **The inversion persists in every band.** The unreviewed PRs are not smaller (additions p50 +286 vs +266 reviewed) and are older (median 6 vs 3 days since merge, i.e. more time to accrue follow-ups). Size is not the confound. The mechanism the data supports is the one the artifact mentions in passing and then drops: a review artifact *generates* cited follow-ups ("gaps the review itself found"), so the proxy measures citation behaviour, not defect rate. Loose `#N` citation: 19/60 vs 3/24, same shape.
**Method:** all-time `fix`-prefixed PRs with `mergedAt` later than the target, regex `PR\s*#N\b` (strict) or `(?<![\w/])#N\b` (loose) on title+body; bands on `additions` and `changedFiles`.
**Verdict:** proxy numbers CONFIRMED; the size-confound explanation is **REFUTED by the artifact's own stated falsifier**. Required rewrite: the proxy is unusable as a defect rate because review causes citations; a defect measure must exclude follow-ups that cite a review finding, or count reverts/incident-tagged fixes only. (n is small — 24 — so no band is significant on its own; the *direction* in 8 of 8 bands is the finding.)

## 5. §3.6 — the two incidents are the "tests passed, real data broke it" class

**Artifact says:** the no-side `1 − price` cost inversion (49× artifact) and the 2026-09-04 NO-side exit valuation; "Both passed tests. Both were found by looking at real recorded data."

**I found:**
- Incident 2: memory `paper-10x-run-is-no-side-exit-valuation-artifact` — found from the equity curve, confirmed against raw ticker rows in `series_watcher.db`; "bookkeeping itself always reconciled to the cent". Fix PR #574 needed six rounds; memory notes one defect in the *fix* "was caught by a test, not by review". Real-data discovery CONFIRMED.
- Incident 1: CLAUDE.md at `5745d9a` (2026-08-09, pre-cutover) already describes it: "frontend spots reimplemented `cost = size * price` without the `1 - price` no-side inversion … same root shape as the earlier, independently-found 'no-side dollar math'" — so the `1 − price` inversion and the 49× cost bug are the same class, consistent with the artifact. **How it was originally found is not in git** (predates the 2026-08-07 cutover's maintained history); the 2026-08-17 593-signal measurement in memory quantified its effect after the fact. Display-side fixes landed 2026-08-17 (`edc2fdf`, `a44f41d`).
- "Both passed tests": no primary source establishes that tests covering those paths existed and passed; what is established is that **the suite did not catch either**. The article's own wording is "passed every test we had".
- Omitted: the repo's standing response is not only the two prose rules the section names — `tools/quality_audit/price_fabrication.py` (CI guard, `34badf1`, 2026-09-05, issue #577) is a *code* guard for the fabricated-price class born of incident 2. §3.2 mentions it; §3.6 should, since P2 is being justified as "cheap to adopt" against a gap that is partly already closed in code.
**Verdict:** CORRECTED: restate as "neither was caught by the suite; incident 2 was found from recorded data; incident 1's discovery predates maintained history" and cite the existing CI guard. Falsifier: a pre-cutover record (status-archive HTML) showing a failing test found incident 1.

## 6. §3.3 — path lists

**Artifact says:** `HOT_PATHS`/`MONEY_UI_PATHS` at `guard_workflow.py:81-89`, used only for the dimensional nudge; `LANES`/`CONCERNS` at `labels.py:85-`; no path list behind CLAUDE.md's "automation never edits …" sentence.
**I found:** `HOT_PATHS` lines 81-87, `MONEY_UI_PATHS` 89; uses at 397 (nudge), 334 (`_is_money_ui`, feeds the same nudge), 351 (commented-out R4). CONFIRMED. `LANES` at 85, `CONCERNS` at 81, `concern:hotpath` at 79; lane 3 = strategy_engine/exits/risk_manager/paper_broker/execution/shadow_mode/position/mutual_exclusivity/settlement_edge_entry/settlement_resolver/kalshi_fees; lane 5 holds `db.py`, `auth.py`, `accounts_store.py`. CONFIRMED. `grep -i 'protected\|PROTECTED_DOMAIN'` → only `_MAIN_BRANCH_PROTECTED_NAMES` in `quality_coordination.py:508`. CONFIRMED.
**Missed:** the same file has a **third list, `KALSHI_PATHS` (lines 76-80)**, used at line 342 to *deny* an edit (`KALSHI_DOCS_REQUIRED`) — the only path list in the repo that already varies enforcement rather than a nudge, and it overlaps P1's "services/kalshi/ boundary" Tier-A entry. "One list, not three" in §7.1 is therefore "one list, not four".
**Verdict:** CONFIRMED, with the `KALSHI_PATHS` omission to add.

## 7. §3.2 — no coverage gate; `main` protection off

**I found:** `gh api …/branches/main/protection` → 404 "Branch not protected"; `branches/main` → `protected: false`, `enforcement_level: off`, `contexts: []`. `docs/open-decisions.md:20` records #615 as David's open call. `grep -rn -i 'coverage\|--cov' .woodpecker/ scripts/ pyproject.toml` → 2 comment lines only. Seven `.woodpecker/*.yml`; six named as required plus the path-filtered `quality-frontend-build`. CONFIRMED.
**Missed:** `CLAUDE.md:117` ("`main` is protected") and `branching-and-ci.md:96-110` ("`enforce_admins: true` … required_status_checks.contexts = …") **still assert server-side protection that no longer exists**. P1's stated home is `branching-and-ci.md`; the spec that edits it should fix the stale paragraph or it ships a rule file contradicting `open-decisions.md`.
**Verdict:** CONFIRMED.

## 8. §1 — quotes and attribution

**I found (live page, two fetches):** author Rodrigo Gardin, Built In, 2026-07-29. Every phrase attributed to Gardin is present verbatim: "delivery speed had barely moved"; "Pull requests got bigger and more frequent, review queues backed up and the share of changes that needed a second fix crept up"; "Generating code faster didn't remove our bottleneck. Instead, it relocated it" (artifact lower-cases the G; immaterial); the currency-field sentence; "because the queue was long that week"; the second-reviewer/real-production-data rule; "coverage on the changed lines slipped"; "add pagination … human skim"; "Pull requests got capped in size and a reviewer was named before the code was even written"; "confident-looking edge case"; "20 to 30 percent"; "lower headcount"; the three outcome questions; "orchestration role"; "Faster code was never the goal." The "tests passed" gloss is supported: "passed every test we had and read cleanly in review". Cui/METR/DORA and the 3×-on-30% arithmetic are **not** in the article and are correctly attributed to the ChatGPT analysis (its lines 57-61, 83-89). "Verification can itself become wasteful" = analysis §6.7; "charge process against its benefit" = §7 table line 203; the "six autonomy dimensions" wording is the *instructions* doc's ("clarity of the goal, quality of context, strength of verification, consequences of an error, reversibility, and existing authority"), while the analysis doc lists five (line 20) — the artifact credits the analysis; minor mis-source.
**Verdict:** CONFIRMED (one minor mis-source: six dimensions → instructions doc, not analysis).

## 9. §5 — duplicates, dated decisions, lanes §8

**I found:**
- **P1 misstates the current rule and is inconsistent with P3.** CLAUDE.md's Scope bullet (since `41f5b6d`, 2026-08-31; reaffirmed `b994a5a`, 2026-09-07 01:29) says a code PR is in scope "once, before merge — never an individual commit or push … Do not invent an extra gate at commit or push granularity." A code PR today therefore owes **one** cycle (three artifacts at the PR). P1 labels Tier A "full double cycle, unchanged" (that is the 09-03 memory's description of *practice*, not the rule text) and defines Tier B as "self-review + CI before push; one adversarial pass + consolidation at the PR" — **three artifacts, i.e. equal to or heavier than today's requirement for the very change class that prompted the complaint**. P3 then says Tier B = two artifacts. The artifact's own §3.4 opening ("every in-scope PR gets a self-review, an independent adversarial review …, and a consolidation") describes one cycle. As drafted, P1 delivers no reduction for a one-line code change; the reduction only exists for planning-pipeline stages, which the 09-03 complaint was not about.
- Effort-caps memory: P1 is a depth tier, not a budget — no contradiction. `decide-dont-over-investigate` / `deliver-dont-generate-adjacent-work`: P4 (a new report) is adjacent work unless David asked for it; the artifact hedges it correctly as "extends `quality_coordination`/`kanban_sync`", per lanes §8 rule 3-5 (confirmed at design lines 332-350). P6 restates CLAUDE.md's 2026-08-30 "disabled until proven" line and the header sentence; it says so. P3 duplicates the mechanical merge gate already written into memory `persist-code-pr-reviews-as-comments` (2026-09-07 update: "confirms 3 distinct, separately-posted stage comments exist"); it says so. P7 is a pointer. No verbatim duplication of a CLAUDE.md line found.
- "Decision already on record (David, 2026-09-07, this session)": **no durable record** — absent from `docs/open-decisions.md`, `docs/next-action.md`, and every merged PR body. `branching-and-ci.md`: "nothing that matters may live only in chat." The `scale-review-effort-to-blast-radius` memory explicitly requires "an actual decision on record, not an inference from tone."
**Verdict:** CORRECTED (P1 baseline and P1/P3 inconsistency); COULD NOT VERIFY the tiering decision from any primary source.

## 10. Unmeasured items and non-falsifiers

- **Partial compliance was not measured.** Of the 60 code PRs "with an artifact", 30 have fewer than 3 distinct artifacts (14 have one, 16 two); only **24 of 84 (29%) have ≥3 review-worded comments**. Full compliance and full non-compliance are the same number; the artifact reports only the latter and lets "60 with" read as compliant. List of <3: #374 #385 #387 #388 #394 #409 #414 #417 #423 #482 #501 #515 #518 #522 #524 #540 #545 #548 #550 #552 #555 #569 #570 #593 #596 #598 #604 #614 #620 #646.
- P1's falsifier ("if Tier B follow-up-fix rate rises above Tier A's, the boundary is wrong") is **not a falsifier** given §4 above: Tier A will carry more cited follow-ups by construction because reviewed PRs generate citations. It must be stated on a citation-normalised or revert/incident-only measure, or it will read as Tier B succeeding no matter what.
- P4's falsifier ("if nobody changes a decision because of the report in two windows") has no defined window; P2's ("a year") is fine.
- "Review is not a queue problem" rests on PR-stage timing only; the unmeasured branch-to-PR interval is where the memory puts the cost. Cheap to compute from the first commit on each PR's branch (`commits[0].committedDate`); not done by either side.
- The docs-share falsifier (reclassify the 104 docs PRs by subject) was not run; the file-based 38% is the number to lead with.

## Findings the artifact missed

1. `KALSHI_PATHS` is a fourth path list and the only one that already *denies*; P1/§7.1 must include it (evidence: `guard_workflow.py:76-80, 342`; falsifier: none — it is source).
2. `CLAUDE.md:117` and `branching-and-ci.md:96-110` assert branch protection that #615 shows is off; the spec editing those files must correct them (evidence: `gh api …/branches/main`; falsifier: protection re-enabled before the spec lands).
3. Zero GitHub formal reviews on 200/200 PRs — drop `reviews` from P3's check or say it is always zero (evidence: GraphQL `reviews.totalCount`).
4. The prefix classifier is inconsistent across §3.1's two rows and drops 2 PRs from the 317 sum (evidence: §1 above).
5. Six untyped code PRs with zero artifacts (#272 #273 #297 #302 #421 #422) are outside every count in §3.4/§3.5; #297 touches advisory/calibration provenance (a P1 Tier-A path).
6. The 2026-09-07 tiering decision has no artifact; the research inherits a decision it cannot cite.

## Overall verdict

**NO-GO** — the measurements are sound (the 24/84 list, latency, counts all reproduce), but two load-bearing arguments fail on re-derivation and the headline principle is inconsistent with the rule it amends. Required fixes, one line each:

1. P1: restate the current baseline from CLAUDE.md's Scope bullet (one cycle per code PR, no push gate); define Tier B so it is actually lighter than today, and make P3's artifact counts match P1.
2. §3.5: replace the size-confound claim with the citation mechanism; report the banded split; redefine the outcome proxy to exclude review-cited follow-ups (or use reverts/incident fixes only), and restate P1's falsifier on that measure.
3. Record the 2026-09-07 tiering decision durably (PR body of this research PR or `docs/open-decisions.md`) and cite it, or label it unverified.
4. §3.4: add the 6 untyped zero-artifact code PRs and the "≥3 review-worded comments = 24/84" full-compliance figure; note formal reviews are 0/200.
5. §3.1: fix the prefix row (sum to 317, one classifier for both rows) and the docs denominator (321 → 38%).
6. §3.3/§7.1: add `KALSHI_PATHS` (deny, not nudge); §3.2/§6: flag the stale protection text in `CLAUDE.md:117` and `branching-and-ci.md:96-110` as a spec-stage edit.
7. §3.6: reword "both passed tests" to "neither was caught by the suite; incident 1's discovery predates maintained history"; cite `tools/quality_audit/price_fabrication.py` as the existing code guard; move the six-dimensions credit to the instructions doc.

Tally: CONFIRMED 5 (items 3, 6, 7, 8, and the §3.4 list itself) · CORRECTED 4 (items 1, 2-definition, 5, 9) · REFUTED 1 (item 4's confound argument) · COULD NOT VERIFY 1 (the tiering decision record).
