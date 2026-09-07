# Recheck — AI-assisted engineering principles design, revision 2 (2026-09-07)

Scoped to the thirteen-item merged fix list in
`…-design-consolidation.md`, per CLAUDE.md's recheck clause (not a
second full review pass: scope is unchanged — the same seven principles,
the same three homes — and no claim the two reviews never saw was
introduced). Each item verified by `grep` against the revised file, not
by recollection.

| Fix | Verified by | Result |
|---|---|---|
| 1 Six sibling lines amended (CLAUDE.md:38 :43 :48 :49 :120; `branching-and-ci.md:76-78`, `:88-94`); merge bullet exemption-first with `--exempt`/`--tier A` | "Line 38 (rule preamble)"; "Every planning-pipeline stage and every Tier A PR produces"; "For a Tier A PR: after it's pushed"; "three for Tier A (…), one for Tier B"; "a Tier A PR's fresh-Agent adversarial review, a Tier B PR's self-review comment"; "a Tier A PR still needs its own fresh, memory-less Agent call"; "AI-executed review its tier requires"; "`--exempt \"<reason>\"` for a mechanical change, `--tier A` to escalate" | present (8/8) |
| 2 Prose-always as rule 2; counts from that wording with the #663 note | "2. **Prose-always rule.**"; "its window included" | present |
| 3 D3 → 3 PRs (#389 #397 #596); rule-3 flips include #273 #559 | literals | present |
| 4 `settlement_edge`/`candidate_log`/`history/` + Lane-3-import rule; counts 141/59, code 68/16 | literals | present |
| 5 `labels.py` Tier A; tests by stem; `tests/` wholesale rejected with the measurement (D8) | literals | present |
| 6 First-line regex validated 224/261, strict form's failure named | "224 match, and the 37" | present |
| 7 (c) demoted to a cited-follow-ups column; (a) via local `git log`; `--since/--until`; retire rule as a dated judgment | literals | present |
| 8 `--exempt`/`--tier A` inputs; files reader paginates | literals | present |
| 9 #613(2) replacement from "not yet by the hook"; line 117 without the cause; `:100` path fix | literals | present |
| 10 Lanes citations (§1 line 30, §9 lines 359–360); D6 restated honestly | literals | present |
| 11 Dead hook paths removed (§5.4); exists-on-disk test (§6.1) | literals | present |
| 12 D8–D12 present | "| D12 |" | present |
| 13 Merge `origin/main` before the PR; plan re-anchors | literal | present |

Stale strings checked absent: `22** | 8`, `133 | 67` as the headline,
"Lanes §8 rule 5", "sole trigger for zero", the strict heading regex —
none found.

**Stage 2 verdict: GO.** The spec is ready for David's review before
the plan stage starts (brainstorming skill's "user reviews written
spec" gate). Twelve decisions are tabled in §9 for him to confirm or
overturn.
