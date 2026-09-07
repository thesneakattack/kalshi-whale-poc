# Adversarial review: `step4-file-move-plan.md`

Date: 2026-09-06. Independent pass — a fresh `Agent` tool call carrying no
memory of the session that produced the plan document, per CLAUDE.md's
"nothing advances on one pass" HARD RULE (adversarial review must re-derive
load-bearing claims from primary sources, never from the artifact's own
tables or summary). Reproduced verbatim below from that agent's own reply.

---

## Checks performed (independent re-derivation)

1. **Total file counts** — `ls docs/superpowers/plans/*.md`=63, `specs/*.md`=88, `research/*.md`=97 (sum 248). **MATCH** to the plan's 63/88/97=248.

2. **Lane spot-checks (Lane 4, Lane 8, Lane 6)** — grepped both classification tables directly (not Appendix A):
   - Lane 4: plans table gives 2 primary plans (`2026-08-27-backend-services-modularization.md` w/ bold `**4**` cell + `2026-08-30-self-feeding-loop-provenance.md`), 0 companions; specs/research table gives exactly 9 rows tagged `| 4 |`, matching filenames listed in Appendix A. Total 11. **MATCH**.
   - Lane 8: 1 primary plan (`2026-08-25-frontend-modularization.md`), 2 companions (both companion-of that plan), 3 specs/research rows. Total 6. **MATCH**.
   - Lane 6: 4 primary plans, 3 companions (all companion-of `tier0-live-incident-remediation`), 7 specs/research rows. Total 14. **MATCH**.
   - Full-table cross-check beyond the 3 required lanes: raw `grep -oE "\| plan \| [0-9]+ \|"` tally across all 9 lanes = 8/3/5/1/3/4/0/1/11 (Lane 4 undercounts to 1 because the regex misses the bold `**4**` cell — this independently *reproduces* the parsing artifact the plan describes); adding the bold row back gives 37, +25 companions +1 README = 63. Specs/research per-lane tally (35/21/6/9/37/7/0/3/66) sums to 184, matching §1's table exactly. **MATCH** across the board.

3. **Two stale-header findings** — (a) line 106 of `step1-plans-classification.md` literally reads `| ... | plan | **4** | done | ...` for the backend-services-modularization row. **CONFIRMED**. (b) `step1-specs-research-classification.md` line 1 header states "specs/ (79) + research/ (97) = 176 files"; `grep -cE '^\| docs/superpowers/(specs|research)/'` on the actual table = 185 rows (88 specs + 97 research), matching live `ls` exactly. **CONFIRMED** — both findings verified precisely as claimed.

4. **Hard-dependency claim** — `tools/kanban_sync/__main__.py:36` is exactly `PLANS_DIR = Path("docs/superpowers/plans")`, consumed at `:220`/`:256`; `sources_plan.py:70` is exactly `all_plans = {p.name for p in plans_dir.glob("*.md")}` inside `list_plan_candidates` (def at :69); `quality_coordination.py:597-598` is exactly `plans_dir = repo_root / "docs" / "superpowers" / "plans"` / `plan_paths = sorted(plans_dir.glob("*.md"))`, feeding `collect_plan_doc_signals(plan_paths, ...)` at line 599. `sources_plan.py:48` is exactly `_EXCLUDED_FILENAMES = frozenset({"README.md", "2026-08-26-active-tracks-board.md"})`. **CONFIRMED** — mechanism and every cited line number match exactly, not approximately.

5. **Live GitHub issue counts** — `gh issue list --search "Plan:" --state open` with default `--limit 30` returned only 30 rows and the label query also returned exactly 30 — a **methodology trap** (gh's default page size), not a real discrepancy. Re-running both with `--limit 500`: raw "Plan:" search = 87 hits, narrowed to literal `Plan: ` prefix = 6, exact same 6 issue numbers (#89, #331, #321, #81, #448, #320) as Appendix A; `type:plan-task` open count = 76. **MATCH** on all figures (87, 6, 76), including the specific issue numbers.

6. **Reference-count spot check** — `git grep -c -F` (same exclude pattern the plan specifies) for `2026-08-24-kalshi-integration-dual-phase.md` (Lane 1) → 6 total hits (1 lanes-self-cite, 5 external, all narrative docs); for `2026-08-24-quality-control-plane.md` (Lane 6) → 34 total hits (1 lanes-self-cite, 33 external, split across ~7 narrative + ~26 code/test/README). Both are plausible, non-contradictory contributions toward their lane's aggregate totals (Lane 1 external=67, Lane 6 external=42) — Appendix A doesn't publish per-file breakdowns, so exact reconciliation is **UNVERIFIABLE-BY-YOU-IN-SCOPE** at the single-file level, but nothing here contradicts the lane totals.

7. **Batch-order logic (§4)** — re-derived the sort order 6<8<13<17<27<42<45<67 (Lanes 2,4,3,8,5,6,9,1) — matches §4's stated sorted list exactly. The actual proposed order (4→8→5→6→3→2→1→9) is not monotonic in any single axis, but is internally coherent once read closely: Lane 5 precedes Lane 6 despite a *higher* whole-lane active-fraction (19% vs 7%) because Lane 5 has **zero active primary plan docs** (no PLANS_DIR exposure) while Lane 6 has one — the plan is implicitly using "active primary plan count" (the axis tied to the hard dependency) rather than raw active-fraction as the deciding criterion between those two, and says so in prose. Lane 1's near-last placement is explicitly argued on raw reference-count magnitude overriding its low (4%) active-fraction — flagged by the plan itself as a deliberate tension, not hidden. **No arithmetic error found**; the reasoning is a defensible multi-axis judgment call, not a mechanically-derived single ranking — worth a reader knowing that up front, but not a defect.

8. **Scope-exclusion check** — `README.md` appears only in exclusion-explaining prose (§0/§1/§6/Appendix-A header), never in a per-lane count or file listing. The UNDECIDED file (`2026-08-27-backend-services-modularization-design.md`) appears exactly once, in §6's exclusion explanation, absent from every lane list including Lane 4's 9-row specs/research set. **CONFIRMED** genuinely excluded, not just claimed.

## Errors found

None. Every numeric claim, line-number citation, and mechanism claim checked was re-derived from primary sources and matched the document exactly, including two claims (the `**4**` bold artifact and the 176-vs-185 stale header) that are unusual enough that a fabricated or careless document would likely have gotten at least one detail wrong. The only near-miss was the reviewing agent's own tooling (gh's default `--limit 30` silently truncating both issue queries to the same round number) — resolved by re-running with `--limit 500`.

## Verdict: GO

This is an unusually well-substantiated planning document — every load-bearing claim checked (file counts, lane assignments across 3 spot-checked lanes plus a full 9-lane cross-tally, both stale-table findings, the `PLANS_DIR`/`quality_coordination.py` hard dependency down to exact line numbers, GitHub issue counts, and scope exclusions) reproduced independently from primary sources with no divergence. The one genuinely soft spot — batch-order §4 blends three axes via judgment rather than one formula — is disclosed by the document itself and doesn't constitute an error. No fixes required before consolidation.
