# Consolidation: frontend-modularization design+plan catch-up review (2026-08-31)

Per CLAUDE.md's "nothing advances on one pass" HARD RULE — unlike the other two plans in
this batch, NEITHER this plan's design nor the plan itself had ever been through any
review cycle (written 2026-08-25, zero companion `-review.md` files existed for either).
This catch-up covers both stages in one pass, appropriate given the smaller doc pair.

- Investigative pass: found a stale `services/config_bounds.py` path in both docs (moved
  to `services/config/config_bounds.py` on 2026-08-27, two days after the design was
  written) and a dead orchestration-skill pointer in both docs (`.claude/skills/
  plan-task/`, deleted 2026-08-28) — retargeted to `superpowers:executing-plans` per
  CLAUDE.md's current Toolchain section. Also found a genuine TDD-GAP: plan tasks T4a and
  T4b each pointed at "the spec's TDD list" for their numbered tests, but no such list
  exists anywhere in either document — fixed by writing out all 14 tests directly in the
  plan text instead of a phantom cross-reference.
- Independent adversarial review: re-derived every correction (path move, skill
  deletion, npm dependency versions all CONFIRMED), and re-verified the strangler-safety
  claim underlying the whole migration approach by checking T1c's byte-diff acceptance
  gate and T2's actual test coverage (`tests/test_browser_e2e.py`) directly — both hold.
  Found one real gap the first pass's TDD-list fix didn't fully resolve: test 13
  ("override scope respected") was too vague to pin which of two different, both-real
  codebase concepts it meant (per-field `overridable:true` vs. `config_overrides`'s
  category/series tiers) — tightened to name the specific behavior. Also flagged two
  cosmetic, non-blocking drifts (a stale field/section count in the design, two
  off-by-one line citations) left as-is per the review's own judgment that they don't
  gate execution.

No disagreement to adjudicate.

**GO.** T0 through T9 (the full plan) verified against current source as of 2026-08-31,
including live npm registry checks for all 5 new frontend dependencies. Ready to execute
starting with T1a, third in this batch's sequence (after kalshi and whale-confidence
merge to main).
