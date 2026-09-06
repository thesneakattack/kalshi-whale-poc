# Consolidation: planning-lanes design (round 2)

Date: 2026-09-06. Reconciles round 2's design, its self-review, the
independent Fable adversarial review of round 2, and the two scoped
rechecks that followed.

## Verdict: GO — for the design only, not for migration

The design is approved as the lane taxonomy, naming hierarchy, straddler
rules, anti-drift rules, and migration *sequence*. **Migration itself
does not start on this GO.** It starts when step 1's classification
tables exist and have passed their own review cycle (required fix #9,
adopted below as a hard gate). That separation is the single most
important thing this cycle produced and it came from the reviewer, not
from me.

## The path here, honestly

- **Round 1: NO-GO.** Lane 5 was defined by a *property* ("hot path"),
  which overlaps every package-defined lane by construction — and its
  own supporting evidence ("tonight's `#150`/`#530`/`#585`/`#586`/`#605`/
  `#629` all live here") was false: of the seven PRs those issues
  produced, one touched Lane 5's packages. Nine packages were assigned
  against their own docstrings. The Track C claim, the GitHub Projects
  claim, and the board's file path were each wrong. Migration would have
  orphaned plan-tracking issues and broken hundreds of path references.
- **Round 2: GO-WITH-REQUIRED-FIXES**, 10 required. The lane list
  survived unchanged; every failure was assignment-level or citation-
  level. The reviewer's own framing — a fix-list recheck, not a round 3,
  *unless* a fix forces a lane change — was adopted, and no fix did.
- **Recheck 1: FAILED.** Two real defects. I had Lane 7's file layout
  exactly inverted (claimed four `config_*.py` files were flat and
  un-moved; they are all inside `services/config/`, moved 2026-08-27 —
  I'd trusted `services/config/README.md:8`'s stale line over
  `__init__.py`'s own record). And my round-2 rewrite **silently dropped
  the entire Track A/B/C reconciliation section** that the round-2
  review had already confirmed correct, leaving a forward reference
  pointing at text that no longer existed. That is precisely the
  "revision that silently drops a requested fix is itself a defect"
  failure CLAUDE.md names — caught only because the recheck re-derived
  instead of trusting.
- **Recheck 2: PASSED.** All three blocking items resolved, every
  `§`/rule/clause/fix reference resolves, 9 lanes unchanged. Five
  cosmetic notes it raised were folded afterward rather than left known-
  wrong.

## Adjudication where reviews disagreed

- **Round 1 self-review vs. round 1 adversarial:** the adversarial
  review was right to overrule "no self-identified blocker." A
  self-review that checks its own *reasoning* but none of its own
  *facts* will pass artifacts whose factual base is rotten.
- **Round 2 self-review's four "did not do" admissions:** all four were
  load-bearing, exactly as flagged. Recorded in that document rather
  than edited away, because admitting them is what made them findable.
- **Two rules or one?** The round-2 reviewer's position adopted: the
  multi-lane-initiative rule is the straddler rule at initiative
  granularity, not a second mechanism. Its only novel content was a
  file-reference-count metric that produced three different answers
  under three counting methods — deleted, replaced by declared purpose.
  The conclusion it supported (realtime plan → Lane 1) survives on the
  plan's own Goal line.

## What is approved

The 9 lanes and their membership; Lane > Initiative > Task; the
straddler rule with clauses (a)-(d); `concern:*` as a cross-cutting
label distinct from lanes, anchored in `labels.py` with a stated
applier; `depends-on:#N` for blocking cross-lane links and plain body
mentions for informational ones; the branch-vs-issue default with the
parked-by-decision exemption (now backed by a real
`docs/open-decisions.md` line, committed `69925c2`); the five anti-drift
rules, three of which are pointers to tooling `/checkpoint` already
runs; label-first visibility with the Projects `Lane` field deferred;
and the migration sequence.

## Hard gates carried out of this cycle

1. **Migration step 1's classification tables get their own full review
   cycle** before step 2 labels anything. Evidence this is necessary,
   not ceremony: a sweep of 66 `services/` units found 1 omission and 7
   rule-application inconsistencies *by the author who wrote the rule*.
   The tables are ~238 rows.
2. **No file moves before the `kanban_sync` touchpoints are fixed** —
   deleting `sources_tracks.py` while `__main__.py:26` still imports it
   breaks every subcommand at import, including the sync that
   `/checkpoint` runs.
3. **If populating the tables forces a lane to be added, merged, or
   removed, that is a scope change** and returns to a full cycle, per
   §8 rule 5.

## Next action

Land these artifacts as a PR (own PR-level review cycle before merge),
then migration step 1: generate the four classification tables, split by
lane group across peer sessions and subagents, then review them as their
own artifact.
