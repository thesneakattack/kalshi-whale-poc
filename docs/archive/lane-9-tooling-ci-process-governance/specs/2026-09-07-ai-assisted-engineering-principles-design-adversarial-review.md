# Adversarial review — AI-assisted engineering principles design (stage 2)

Independent Agent-tool pass, no memory of the session that wrote the spec. Date 2026-09-07.
Artifact: `docs/archive/lane-9-tooling-ci-process-governance/specs/2026-09-07-ai-assisted-engineering-principles-design.md`
at worktree HEAD `afbb516` (branch `docs/lane9-ai-assisted-engineering-principles`, based on `6a63309`; `origin/main` has since moved to `fe3699f` — only `tools/kanban_sync/labels.py`'s docstring changed, none of the spec's anchored files). Companion self-review not read. Every count below was re-derived from `gh` output saved to the session scratchpad (`merged200.json`, `sim.py`), from the files at HEAD, or from the memory directory — never from the spec's tables.

## 1. §5.1 Scope-bullet replacement vs. CLAUDE.md line 40 and the rest of the rule

**Spec says:** the replacement keeps everything not about the tier.
**I found:** clause by clause — exemption list: identical. "each stage boundary … plus the PR once, before merge — never an individual commit or push": preserved. TDD/systematic-debugging/verification clause: preserved (drops "not by this cycle", no meaning change). "including this rule's own PR": preserved. "Do not invent an extra gate at commit or push granularity": preserved. **Dropped:** "Anything asserting a claim, a design decision, new logic, or a process/rule change is in scope regardless of diff size" — replaced by the path list. Consequence the spec does not state: a claim-asserting document outside `docs/archive/lane-*/{research,specs,plans}/` and `docs/superpowers/` (e.g. `docs/event-loop-blocking-routes-census-2026-09-03.md` in PR #636's file list, module `README.md` findings, `docs/kalshi/` mirror edits, `docs/open-decisions.md`) becomes Tier B.
**Unchanged lines the new text contradicts** (the spec edits none of them):
- CLAUDE.md:38 — "neither does a PR carrying a claim, design decision, new logic, or a process/rule change to `main`" never advances "on one pass alone". A Tier B PR with new logic advances on exactly one same-author pass.
- CLAUDE.md:43 — "Every in-scope stage produces its own artifact, then three more … self-review, adversarial review, consolidation". Tier B is in scope (not exempt) and produces one.
- CLAUDE.md:48 — "For an in-scope PR: … one more full review cycle of the same shape (self-review, adversarial review, consolidation, each its own artifact) runs against the PR as submitted before `gh pr merge`". Direct contradiction for Tier B; the spec inserts a bullet after this line but leaves it.
- CLAUDE.md:49 — "every required artifact — self-review, adversarial review, consolidation — still exists … A coordinator or peer instruction that would skip a required artifact outright … is still an instruction that appears to relax a HARD RULE". Tier B skips two outright.
- CLAUDE.md:120 and `branching-and-ci.md:76-78` — the peer ping is "not a substitute for … adversarial-review requirement (that still needs its own fresh, memory-less Agent call regardless of what a peer says)".
- `branching-and-ci.md:88-94` — "the AI-executed self-review/adversarial-review/consolidation cycle still runs before `gh pr merge`".
- Internal: the new bullet after line 48 says "Before `gh pr merge` on **any** PR … merges only on `PASS`", but §3.1 says the mechanical exemption "is evaluated first" and rule 5 lets a session escalate B→A; `review-tier` (§6.2) has no exemption or escalation input, so a CLAUDE.md typo fix computes Tier A / 0 artifacts / `FAIL` and an escalated PR prints `B PASS` with one artifact.
**Method:** `cat -n CLAUDE.md`, `cat -n .claude/rules/branching-and-ci.md`, read against §5.1/§5.2 text.
**Verdict: CORRECTED** — six unchanged lines contradict the new Scope bullet; the "any PR / PASS only" bullet contradicts §3.1's exemption-first and escalation rules.

## 2. Line-number anchors

**Spec says:** CLAUDE.md 3, 40, 46, 48, 60, 103, 107, 117; branching-and-ci.md 63, 96–116, at `6a63309`.
**I found:** all eleven anchors match at HEAD (3 header; 40 Scope; 46 Consolidation; 48 "For an in-scope PR"; 60 dimensional last bullet; 103 Automation bullet, last of Safety invariants; 107 displayed-value bullet; 117 branching line; 63 "Read the PR body before merging"; 96–116 GitHub-side enforcement paragraph). `git diff 6a63309 origin/main --stat` touches none of these files.
**Method:** `cat -n` both files; `git log 6a63309..origin/main`.
**Verdict: CONFIRMED.**

## 3. §3.1 boundary re-simulated over the last 200 merged PRs

**Spec says:** 133 A / 67 B; code 60/24; docs 64/41; the 30 unreviewed → 23/7; settings.yaml sole trigger 0, `main.py` 3, `frontend/src/js/` 3, LANES 1+2 beyond hook tuples 1 (#545); prose under a Tier A dir moves no PR.
**I found** (window #255–#663, same 84 code-typed / 105 docs-typed split, so the same window):
- §3.1 as literally written (rule 1 requires a code suffix for *every* entry, including `CLAUDE.md`, `.claude/rules/`, `.claude/skills/`): **121 A / 79 B; code 58/26; docs 54/51.** Adding §6.1's `REVIEW_TIER_A_PROSE_ALWAYS` (CLAUDE.md, `.claude/rules/`, `.claude/skills/`) gives **134/66; code 60/24; docs 65/40** — the spec's numbers ±1. So the spec's table follows §6.1, not §3.1; §3.1 rule 1 + rule 2's "Tier A by rule 1's explicit listing" is self-contradictory (rule 1's suffix list excludes `.md`), and a CLAUDE.md-only PR is Tier B under §3.1 as worded (13 PRs flip on this: #257 #304 #305 #307 #311 #330 #340 #385 #434 #587 #635 #645 #659).
- Unreviewed 30 → **23 A / 7 B, same seven** (#301 #308 #415 #445 #498 #623 #273). Confirmed.
- Marginals: `main.py` sole trigger 3 (#625 #647 #649) confirmed; `frontend/src/js/` 3 (#390 #422 #598) confirmed; LANES 1+2 beyond hook tuples → #545 confirmed. **`config/settings.yaml`: sole trigger for 3 PRs, not 0** — #596 (settings.yaml only), #389 (settings.yaml + a PNG), #397 (settings.yaml + next-action.md). The parenthetical "every PR that touched it also touched other Tier A code" is false; D3's "sole trigger for zero PRs" is false.
- Prose-under-Tier-A-dir exemption moves no PR: confirmed (0).
- Rule 3 (not simulated by the spec) applied to the 28 Tier B code PRs via `gh pr diff`: **#273 #522 #540 #557 #559 #561** add `register_schema(`/`CREATE TABLE` lines → A. Spec named 522/540/557/561; #273 and #559 are additional.
- **Consequential Tier B PRs:** #423 `services/history/trade_analytics.py` "History-tab Fees reconciles exactly with Realized P&L" — a fees/P&L computation (money display, the exact class P2 targets) is Tier B because `history/` is Lane 4. Tier B PRs that changed a module Lane 3 imports (import graph from strategy_engine/risk_manager/paper_broker/execution/exits/position/settlement_edge_entry): **#557 `settlement_edge.py`** — `settlement_edge_entry.py:108` calls `settlement_edge.projected_probability()`, i.e. the probability the Lane 3 entry acts on lives in a Tier B module; **#522 #617 #620 #631 #636 `candidate_log.py`** — `strategy_engine.py` calls `candidate_log.record_rejection()` at eight sites inside the decision path; **#614 `fault_log.py`**. Lanes 4/5/6/8/9 Tier B modules that matter: `settlement_edge.py`, `candidate_log.py`, `history/` (P&L, fees), `analytics/routes.py`, `backtest/`, `backup/`, `db.py`-adjacent `fault_log.py`/`app_state.py`/`http_client.py`/`state_view.py`, `quality/`, `observability/`, `static/` (the served bundle), `tests/` (25 Tier B PRs), `tools/kanban_sync/` (5 Tier B PRs — the module that will hold `REVIEW_TIER_A_PATHS` and `review_tier()` is itself Tier B).
- `gh pr list/view --json files` caps at 100: PR #660 has 124 files (REST page 2 = 24), and the hidden 24 include `scripts/ci-testmon-run.sh` (a Tier A path) and `services/observability/observability.py`. A classifier on that field mis-sees large PRs; the spec's simulation used it.
**Method:** `gh pr list --state merged --limit 200 --json number,title,files,additions,labels,mergedAt,comments,body`; `sim.py` with `LANES` resolved as `tests/test_kanban_sync_labels.py::_resolve_package_path` does, hook tuples copied from `guard_workflow.py:76-89`; `gh pr diff` per Tier B PR; `grep -n "^from services" services/<lane3>.py`; `gh api pulls/660/files?page=2`. Falsifier: a re-run of `sim.py` producing 133/67 from §3.1's literal wording.
**Verdict: CORRECTED** — headline counts not reproducible from §3.1's wording (they need §6.1's prose list); D3's zero is 3; two more rule-3 flips; #423 and the `settlement_edge`/`candidate_log` exposure are §10-shaped triggers the spec's own list omits (money display; a Lane 3-consumed probability model).

## 4. §6.1 hook launch / `sys.path` / importlib subset test

**Spec says:** `run_hook.py` launches the hook as a bare script with cwd = repo root but root not on `sys.path`; a `tools/` import would be fragile; subset test loads the hook by path.
**I found:** `run_hook.py:60-61` runs `[sys.executable, str(hook)]` with `cwd=str(root)`; the settings.json prelude sets only `CLAUDE_HOOK_ROOT`, no `PYTHONPATH`; Python puts the script's directory (`.claude/hooks/`) at `sys.path[0]`, not cwd — claim correct. Precedent for the subset test: `tests/test_guard_workflow.py:11-16` already loads `guard_workflow.py` via `importlib.util.spec_from_file_location`. Two corrections: (a) "the hook already draws that line (`_is_code`)" — `guard_workflow.py:329-330` `_is_code` is `.py`-only (plus `.js` via `_is_money_ui`); the spec's 12-suffix list is new, not the hook's line; (b) the hook tuples contain three paths that no longer exist — `services/kalshi_client.py`, `services/kalshi_account_client.py`, `services/kalshi_trade_ws.py` (moved under `services/kalshi/`; CLAUDE.md:100 is stale the same way) — a subset test will enshrine dead entries unless `REVIEW_TIER_A_PATHS` also gets the exists-on-disk test `LANES` has.
**Method:** read `run_hook.py`, `.claude/settings.json` hook command, `guard_workflow.py`; `ls services/`.
**Verdict: CORRECTED** (mechanism sound; `_is_code` claim wrong; dead hook paths unaddressed).

## 5. §6.2 artifact-count regex and §6.3 defect predicate against real comments

**Spec says:** count comments whose first line matches `^#+\s*(self-review|adversarial review|consolidation|tier b self-review)`; §7: tested on "the exact comment headings this repo has used (`# Self-review — …` …)".
**I found:** 265 comments across the 200 PRs; the regex matches 173. Among the 53 PRs merged since 2026-09-05 (the lean era the rule will run in), **8 PRs have ≥3 review comments by a loose match but <3 by the regex** (#651 #645 #640 #632 #631 #625 #624 #594) and 10 have ≥1 loose / 0 strict. PR #625 is a complete Tier A cycle — `**Self-review (lean, per PR #587)**`, `**Adversarial review (independent, fresh Agent call …)**`, `**Consolidation — GO**` — and scores 0 → `FAIL`. Real first lines the regex misses: bold-only headings (no `#`), `## Independent adversarial review`, `## PR-stage adversarial review`, `## Dispatching-session self-review`, `## PR-level self-review`, `## Final consolidation`, `**PR self-review …**`. The most common real forms are `## Self-review` (20) and `## Consolidation` (13) — `##`, not the `# … —` shapes §7 lists. Gaming: the count is heading-only; three headings from one author satisfy "A ≥ 3"; nothing distinguishes an independent Agent's comment from the author's.
**Defect predicate:** (c)'s exclusion (`adversarial|self-review|consolidation|review found|review finding` in the body) matches **45 of 66 `fix` PR bodies overall and all 16 `fix` PRs merged since 09-05** — under P3 every compliant body will mention its review, so (c) counts ~nothing and less as compliance improves; also gameable by one word in a body. `PR #N` literal appears in 35/66 fix bodies vs bare `#N` in 47 (the bare form is ambiguous with issues, so the literal is defensible but loses a third). (b) `live incident|outage|regression` matches 51 of 200 titles/bodies (four Lane file-move docs PRs among them) — only the citation makes it usable. (a) needs a `main` commit reader: only two `fix: revert` commits exist in history (`93460a8`, `6e79898`) and neither of the five §6.2 readers lists commits.
**Method:** Python over `merged200.json` comment bodies; `git log origin/main -i --grep=revert`.
**Verdict: REFUTED** for the regex as specified and for predicate (c); (a) needs a reader the spec does not name.

## 6. §6.3 windows and retire rule

**Spec says:** `outcomes --days 14`; falsifier = windows 2026-09-07→09-21 and 09-21→10-05 with no decision citing the report → retire.
**I found:** every input (merged PRs, files, diffs, labels, comments, `createdAt`/`mergedAt`) persists on GitHub, so the first window is computable after the fact — with two caveats: `--days 14` is relative to run time and cannot name fixed windows (needs `--since/--until`), and in the first window "artifacts ≥ requirement" measures PRs merged under a rule (Tier B comment) that did not yet exist. "No decision cites the report" is a human judgment, not a computed condition; fine, but say so.
**Method:** read §6.3; `gh pr list --json createdAt,mergedAt` fields exist.
**Verdict: CORRECTED** (computable; interface can't express the windows as written).

## 7. §8/§9 D6 — #613 folded in

**Spec says:** the two §5.1 edits match #613's decisions; the PR closes #613; no competing PR.
**I found:** `gh issue view 613` (OPEN): (1) new bullet — spec text matches in substance. (2) #613 says the last dimensional bullet is edited "replacing 'not yet by the hook … a separate, in-scope-for-review follow-up'"; the spec replaces only "the clause after the em-dash", which leaves CLAUDE.md:60 reading "…enforced by the session reading and following it, **not yet by the hook** — this rule's wider scope is deliberately session-enforced (…)": "not yet" survives and contradicts the new clause. `gh pr list --state all --search 613` → #643 #655 #657 only, all merged, all incidental mentions; no #613 PR exists. #615 (`gh issue view 615`) says the cause of the lapse "is not recoverable from the API"; the spec's line-117 text asserts "off since the Free-plan change" as fact. `gh api branches/main` today: `protected: false`, `enforcement_level: off` — §5.2's paragraph is accurate.
**Method:** `gh issue view 613/615 --json body`, `gh pr list --search`, `gh api`.
**Verdict: CORRECTED** — edit (2) must replace from "not yet by the hook" onward; line-117 must not assert a cause #615 labels unknown.

## 8. Memories and planning-lanes §8

**Spec says:** consistent with prior decisions; §1 and D5 cite "lanes design §8 (written rules over tools, pre-built over hand-rolled, extend `kanban_sync`)".
**I found:** `effort-caps-are-kneecapping` — Tier B is a floor, not a ceiling ("doubt escalates to A"): no conflict. `nothing-advances-on-one-pass` — conflicts only through the unchanged CLAUDE.md lines in §1 above. `scale-review-effort-to-blast-radius` — demands a decision on record: `docs/open-decisions.md:28` records it (CONFIRMED); its suggested criterion "widely-used code" is absent from the boundary (`fault_log.py`, `app_state.py`, `http_client.py`, `state_view.py` are Lane 5 Tier B and Lane 3 imports four of them). `deliver-dont-generate-adjacent-work` rule 3 ("anything else noticed … never folded into the PR"): D6's "same bullets" holds only for #613(1); #613(2) edits a different HARD RULE (line 60) and #615 edits a different file — those are adjacent work folded in, and the merge-race rationale does not apply to them. `persist-code-pr-reviews-as-comments` — the mechanical count is what that memory asked for: CONFIRMED. Planning-lanes design: "extend `tools/kanban_sync` rather than a parallel tool" is §9 (lines 359–360), and "written rules over tools, pre-built tools over hand-rolled" is §1 (line 30); §8 rule 5 says only that `LANES` is the single source of truth for lane fit.
**Method:** read the five memory files and `2026-09-06-planning-lanes-design.md` §§1, 8, 9.
**Verdict: CORRECTED** (citations to the wrong sections; D6 rationale false for two of the three folded edits).

## 9. One plan? Subcommand structure?

**Spec says:** all code in `tools/kanban_sync`; five thin `gh` readers; two subcommands.
**I found:** `tools/kanban_sync/__main__.py:271-317` uses argparse subparsers (`sync`, `backfill-status`, `push-status`, `plan-candidates`, `decompose-plan`) — `review-tier`/`outcomes` fit; `github_client.py` has `_run`/`_invoke` retry shape and a `Runner` injection for fakes (`tests/test_kanban_sync_github_client.py`). Hidden beyond the five readers: paginated PR-files (REST, §3), a `main` commit reader for defect (a), `createdAt` for p50, `--since/--until`, an exemption/escalation input (§1), the 30-PR fixture corpus, plus rule-text edits in three files and `docs/open-decisions.md`/`docs/next-action.md`. One plan, but larger than §6.2 describes.
**Method:** read `__main__.py`, `github_client.py` signatures, tests directory listing.
**Verdict: CORRECTED** (one plan; the reader list and inputs are incomplete).

## 10. §9 rationales and silent decisions

- D3 "sole trigger for zero PRs": false (3, §3 above). D5 "Lanes §8 rule 5": wrong section (§9). D6 "same bullets": false for #613(2) and #615. D1, D2, D4, D7: rationale holds on inspection.
- Silent decisions David should see: (i) claim-asserting docs outside the pipeline directories drop to Tier B; (ii) `tools/kanban_sync/` — the tier decider — is Tier B, so `REVIEW_TIER_A_PATHS` and `review_tier()` can be changed under one self-review; (iii) `tests/` is Tier B, so weakening or deleting a Tier A module's tests is Tier B; (iv) `.ddev/` (the Basic-Auth-gated public proxy config), `Dockerfile`, `requirements*.txt` are Tier B; (v) `settlement_edge.py`'s `projected_probability()` and `candidate_log.py` (called inside the strategy tick) are Tier B; (vi) `services/history/` fees/P&L is Tier B while P2 names "P&L, fees"; (vii) the artifact count cannot tell an independent Agent's comment from the author's.
**Verdict: CORRECTED.**

## Findings the spec missed

1. Hook tuples reference three deleted files (`services/kalshi_client.py`, `kalshi_account_client.py`, `kalshi_trade_ws.py`); the subset test would freeze them; `REVIEW_TIER_A_PATHS` needs the same exists-on-disk test `LANES` has (`tests/test_kanban_sync_labels.py:122-135`).
2. `gh … --json files` truncates at 100 entries (PR #660: 124 files, 24 hidden, one a Tier A path).
3. `review-tier` has no input for the mechanical exemption or upward escalation, yet the rule text says "any PR … merges only on `PASS`".
4. The heading regex fails a fully compliant Tier A PR (#625) and 7 others since 09-05.
5. Defect predicate (c) is inert under P3 (16/16 recent `fix` bodies excluded) and gameable by one word.
6. `settlement_edge.py`/`candidate_log.py`/`fault_log.py` are consumed inside Lane 3 code paths and are Tier B.
7. `services/history/` (fees, realized P&L) is Tier B; #423 is a real fees/P&L fix that would need no P2 evidence.
8. `tools/kanban_sync/` and `tests/` are Tier B, including the tier definition itself.
9. Spec base "`6a63309`" is four commits stale (`fe3699f`); anchors unaffected, but the plan should say which it re-anchors against.

## Overall verdict: NO-GO

Required fixes, one line each:
1. Rewrite or amend CLAUDE.md:38, :43, :48, :49, :120 and `branching-and-ci.md:76-78`, :88-94 so Tier B is not contradicted by an unchanged line; quote each new text in §5.
2. Make §3.1 rule 1 and §6.1 agree: state the prose-always list in the definition, and re-run §3.2 from that wording (expect ≈134/66, not 133/67).
3. Correct D3 and the settings.yaml marginal to 3 (#389 #397 #596) or justify each as "dictated"; add #273 and #559 to the rule-3 flips.
4. Add `settlement_edge.py`, `candidate_log.py`, `services/history/` (or a "consumed by Lane 3 / computes money" rule) to Tier A, or state in §9 why not, with the #423/#557 evidence.
5. Put `tools/kanban_sync/labels.py` (or the whole `tools/kanban_sync/`) and `tests/` of Tier A modules in Tier A, or record the self-referential hole as a decision.
6. Replace the heading regex with one validated against the real first lines (bold forms, `Independent/PR-stage/Dispatching-session` prefixes) and include those in §7's fixture list.
7. Drop or redesign predicate (c); add a commit reader for (a); name pagination for files and `--since/--until` for windows.
8. Give `review-tier` an explicit `--exempt <reason>`/`--tier A` input and make the "any PR" bullet say the exemption is applied first.
9. #613(2): replace from "not yet by the hook" onward; line 117: drop "since the Free-plan change".
10. Fix the lanes-design citations (§9 lines 359–360 for "extend kanban_sync"; §1 line 30 for "written rules over tools") and restate D6 honestly (only #613(1) shares the bullet neighbourhood).
11. Address the dead hook paths (item 4b) before the subset test enshrines them.
