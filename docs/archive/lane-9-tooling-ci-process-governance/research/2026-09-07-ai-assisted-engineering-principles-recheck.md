# Recheck — AI-assisted engineering principles research, revision 2 (2026-09-07)

Scoped to the eleven-item merged fix list in `…-consolidation.md`, per
CLAUDE.md's recheck clause (not a second full review pass; scope did not
change and no claim the two reviews never saw was introduced). Each item
was verified by `grep` against the revised file, not by the author's
recollection of having written it. The exact grep command is in the
commit that lands this file.

| Fix | Verified by | Result |
|---|---|---|
| 1 P1 baseline (one cycle at PR), Tier B one artifact, P3 counts match | "owes one cycle at the PR"; "no adversarial Agent, no consolidation"; "Tier A: three … Tier B: one" | present |
| 2 citation mechanism, both banded splits, P4 defect definition, P1 falsifier | "measures citation behaviour, not defects"; adversarial's files-band figures; "review-cited follow-ups excluded"; "On the defect measure P4 defines" | present |
| 3 decision recorded and cited | `docs/open-decisions.md` line present (1 match); artifact cites it | present |
| 4 six untyped PRs; 31/25 compliance; 12/17; reviews 0/200 | each literal | present |
| 5 one classifier, 317 sum incl. `test`/`ci`; 321/38%; docs-PR reclassification | each literal; `312`/`39%` absent | present |
| 6 `KALSHI_PATHS` deny in §3.3 and §7.1; stale protection text flagged | ":342 to"; "one list, not four"; "branching-and-ci.md:96-110" | present |
| 7 "neither was caught by the suite"; `price_fabrication.py`; six-dimensions credit | each literal | present |
| 8 human hours not measurable; P4 cannot | "does **not** measure human hours" | present |
| 9 five dropped principles named; lanes §5 vs tier note | each literal | present |
| 10 first-commit→PR-open measurement | table row present | present |
| 11 P4 falsifier window | "two consecutive 14-day windows, retire it" | present |

Stale strings checked absent: `312`, `39%)`, `34 linked`, "double
cycle, unchanged", `POST /api/backtest` — none found.

**Stage 1 verdict: GO.** The spec stage may start.
