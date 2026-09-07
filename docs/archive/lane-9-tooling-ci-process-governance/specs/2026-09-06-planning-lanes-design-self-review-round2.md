# Self-review: planning-lanes design (round 2)

Date: 2026-09-06. Same author/context as round 2 (stage 1). Checked
against the consolidation's full 12-item merged fix list, item by item,
not just re-read for general quality — round 1's self-review was
explicitly faulted for not doing this.

**Fix-list check (all 12):**
1. Lane 5 redefined by package (Runtime infrastructure), `concern:*`
   introduced for the property. ✓
2. All 9 flagged straddlers reassigned with a stated, reapplicable rule;
   `history_push.py`, `.claude/hooks/`, `.github/workflows/`, `scripts/`,
   `tests/`, `bench/`, `ui_samples/`, loose kickoff docs added to Lane 8/9;
   `config/settings.yaml` explicitly placed in Lane 7. ✓
3. Realtime-remediation plan given a stated primary-lane rule (Lane 1)
   instead of an unaddressed 6-lane span. ✓
4. Projects/label claim corrected (label = day-one, board field =
   deferred named follow-up). ✓
5. Migration re-sequenced (table → label → fix tooling → move in
   batches → regenerate README). ✓
6. Track C corrected to the execution-program doc; standing decisions
   routed to open-decisions.md. ✓
7. Board path fixed in the design AND in `next-action.md:346` (checked
   and edited directly, not just noted). ✓
8. Rule 2/3/4/5 corrected; §5 exemption added. ✓
9. Numeric citations corrected — **caught two I'd initially missed on
   first pass**: the 34-file (not 24) 2026-09-03 bundle count, and
   `#576`'s Lane 5→Lane 1 correction. Both added in a follow-up edit
   before this self-review, not left for the adversarial pass to
   rediscover.
10. `lane:*` supersedes `area:*` stated; `main.py` tagging rule stated
    (not a parenthetical); archive target renamed to `docs/archive/`. ✓
11. No dangling `§0b`/`§3-vs-§2` references carried forward — round 2
    doesn't reuse round 1's section numbering, so these specific
    dangling refs don't exist in round 2. **Not independently verified
    that round 2 doesn't introduce its own new dangling reference** —
    flagging for the adversarial reviewer to check rather than claiming
    it clean.
12. `plans/README.md` regeneration named as part of migration step 5. ✓

**What I did NOT do, stated plainly (this is exactly where round 1's
self-review failed — not repeating that):**
- I did not re-derive the persisted classification tables (§4 step 1) —
  I explicitly deferred that to a delegated mechanical pass, on the
  reasoning that generating it requires no further design judgment once
  the lane rules are fixed. This is a real gap if the reviewer disagrees
  that the rules are actually mechanical to apply — the straddler rule
  in particular has edge cases (e.g., what does "primary declared
  purpose" mean for a file with no docstring at all?) that I have not
  stress-tested against anything beyond the 9 units round 1 flagged.
- I did not verify the 155-issue count myself — I took round 1's
  adversarial review's own re-derivation (`gh issue list --state open
  --limit 500`) as ground truth rather than re-running it. If it drifted
  again since that check, this doc's number is stale too.
- I did not check whether any OTHER package beyond the 9 flagged units
  has the same straddling problem — I fixed exactly the units the
  adversarial review named and no others. It's plausible more exist.
- I did not verify my own new claims about `.claude/hooks/`'s actual
  contents, `bench/`'s existence, or `ui_samples/`'s existence beyond
  the adversarial review's own listing of them as omitted — I trusted
  that list rather than re-deriving it myself.

**Consistency check:** the straddler rule (§2) and the multi-lane-
initiative rule (§2's Track A discussion) are two different rules for
two different problems (a single file with a dual purpose, vs. a whole
initiative spanning many files across lanes) — worth the adversarial
reviewer confirming these don't need to be the same rule, or that having
two separate rules isn't itself a design smell.

**Verdict:** ready for adversarial review. Two self-identified risks
worth the reviewer's specific attention: (1) whether the straddler rule
is actually reapplicable to units beyond the 9 already checked, (2)
whether deferring the persisted classification table to a "mechanical"
delegated pass is correct or whether generating even a partial version
now would have caught more problems before this review, the way it did
for weather-index-ingestion in round 1.

> Post-script (2026-09-06, after the round-2 adversarial review): every
> one of the four "did not do" admissions above turned out to be
> load-bearing — the count had drifted (146), the unchecked units held
> one omission and seven rule inconsistencies, and the no-owner edge
> case (`market_lookup.py`) was real. Recorded here, not edited away,
> because the admissions were the useful part.
