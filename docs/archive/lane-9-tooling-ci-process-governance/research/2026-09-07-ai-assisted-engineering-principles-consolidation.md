# Consolidation — AI-assisted engineering principles research (2026-09-07)

Reconciles the stage-1 artifact
(`2026-09-07-ai-assisted-engineering-principles.md`, as committed at
`c028a05`), its same-session self-review (`…-self-review.md`), and the
independent adversarial review (`…-adversarial-review.md`, fresh Agent,
no session memory; it did not read the self-review) per CLAUDE.md's
"nothing advances on one pass" HARD RULE. Every adversarial finding
adopted below was re-derived by the authoring session before adoption
(#613's rule: a finding is a claim, not a verdict).

## Where the two reviews agree

- The central measurements reproduce exactly: 317 merged PRs, the 24-of-84
  zero-artifact code-PR list, latency percentiles, path-list locations,
  branch protection off, quotes verbatim on the live page.
- §3.1's PR-level reading over-attributes docs PRs to review artifacts
  (self-review ran the reclassification: 3 of 105 docs PRs are titled as
  review artifacts; the file-level share is the defensible number).
- The size-confound explanation in §3.5 fails: both reviews ran the banded
  split independently (self-review: 3 bands; adversarial: 4 additions
  bands + 4 files bands) and the inversion persists in every band. The
  mechanism the data supports is that a review *generates* cited
  follow-ups. The proxy measures citation behaviour, not defects.
- P1 and P3 disagree with each other on Tier B's artifact count.

## Where they differ, adjudicated

| Point | Self-review | Adversarial | Adjudication |
|---|---|---|---|
| P1's baseline | Treated "full double cycle" as today's rule and only flagged the P1/P3 count mismatch | Showed from CLAUDE.md's Scope bullet (`41f5b6d`, reaffirmed `b994a5a` 2026-09-07 01:29) that a code PR owes **one** cycle at the PR, so draft Tier B (three artifacts) was no lighter than today | **Adversarial is right.** Re-read the Scope bullet: "the PR once, before merge — never an individual commit or push". The "double cycle" was the 09-03 memory's description of *practice*, not the rule. P1 is rewritten: Tier A = today's one cycle, unchanged; Tier B = one persisted artifact. |
| Full-compliance figure | Not measured | 24 of 84 have ≥3 review-worded comments | Re-derived: 31 of 84 have ≥3 artifacts counting review-named files in the diff and all comments; 25 counting comments only; 12 have one, 17 have two. Both figures reported with method; the point stands — full and zero compliance are similar-sized groups. |
| Docs-file denominator | Not re-derived | 321 excl. `docs/kalshi/` at `6a63309` → 122/321 = 38% | Re-derived with `git ls-tree`: 321 and 122. Adopted. The draft's 312 came from a glob that missed subdirectories. |
| Prefix classifier | Flagged the 104-vs-105 docs mismatch only | Showed the all-time row summed to 315 and mixed two regexes | Re-derived with one scope-tolerant classifier: all-time docs 145 · fix 94 · untyped 32 · feat 23 · chore 14 · refactor 4 · perf 2 · config/test/ci 1 each = 317; last 200 docs 104 · fix 66 · untyped 11 · feat 10 · chore 7 · config 1 · refactor 1 = 200. Adopted. |
| Six untyped code PRs with zero artifacts | Not found | #272 #273 #297 #302 #421 #422 | Re-derived: all six have 0 comments and 2–14 code files. Adopted; the 29% is a floor. The adversarial pass's 34/110 "touches a code file" figure is cited as its figure, not re-derived here. |
| `KALSHI_PATHS` | Not found | Fourth path list, `guard_workflow.py:76-80`, the only one that *denies* (`:342`) | Re-derived. Adopted into §3.3 and §7.1. |
| Stale protection text | Not found | `CLAUDE.md:117` and `branching-and-ci.md:96-110` assert protection #615 shows is off | Re-derived (`CLAUDE.md:117` reads "`main` is protected"). Adopted as a spec-stage edit. |
| Decision record | Not raised | The 2026-09-07 tiering decision exists only in chat | Adopted. David's answer came through a direct three-option question in this session; it is now recorded in `docs/open-decisions.md` on this branch and will be in the PR body. |
| Human-hours measure | Flagged as unaddressed | Not raised | Adopted from the self-review. |
| Dropped-principles rationale; lanes §5 conflation | Flagged | Not raised | Adopted from the self-review. |
| Branch-to-PR interval | Named as a falsifier, not run | Named as cheap and not run | **Run now:** first commit → PR open p50 1 min, p90 88 min (n=84; with-artifact p50 1 / p90 95; without p50 1 / p90 50; commits per PR p50 2). Branch work is committed at the end, so pre-commit time is invisible to both git and GitHub. The "hour-plus" cost cannot be measured from timestamps; P4 cannot answer it either. Added to §3.4 and §8. |

## Merged fix list (applied in one revision, then rechecked item by item)

1. P1: state today's baseline from the Scope bullet (one cycle per code
   PR, no push gate); Tier B = one persisted artifact, genuinely lighter;
   P3's counts match P1.
2. §3.5: drop the size-confound claim; report both banded splits; state
   the citation mechanism; define P4's defect measure (revert, incident-
   tagged fix, or a fix that changes behavior the original PR claimed,
   excluding follow-ups that cite a review finding); restate P1's
   falsifier on that measure.
3. Record the tiering decision in `docs/open-decisions.md` (this branch)
   and cite it from the artifact.
4. §3.4: add the six untyped zero-artifact PRs; add the full-compliance
   figures (31/84 and 25/84 by method; 12 one, 17 two); note formal
   GitHub reviews are 0 of 200 and drop `reviews` as a signal in P3.
5. §3.1: one classifier for both rows, sums 317/200; docs denominator
   321 → 38%; add the docs-PR reclassification and restate the reading.
6. §3.3/§7.1: `KALSHI_PATHS` (deny); §3.2/§6/§7: stale protection text
   in `CLAUDE.md:117` and `branching-and-ci.md:96-110` as a spec edit.
7. §3.6: "neither was caught by the suite; incident 1's discovery predates
   maintained history"; cite `tools/quality_audit/price_fabrication.py`;
   six-dimensions credit → the instructions draft.
8. §3.7 and P4: say plainly that the article's third question (human
   hours) has no measure here and only David can supply one.
9. §5 closing: one sentence on the five ChatGPT principles not carried
   and why; §7: note that P1's "any Tier A file → Tier A PR" and lanes
   §5's "label by primary purpose" answer different questions.
10. §3.4/§8: add the first-commit→PR-open measurement and its limit.
11. P4's falsifier gets a defined window (two consecutive 14-day windows).

## Verdict

**NO-GO on `c028a05`; GO expected on the revision** once the eleven
items above are checked one by one (`…-recheck.md`). No fix changes the
candidate principle set's membership or the decision on record; fix 1
changes P1's *content* materially (Tier B is now a reduction) and is the
one to read closely. Scope did not change and no claim the two reviews
never saw is introduced, so the fix-list recheck is sufficient; a second
full cycle is not required (CLAUDE.md's own recheck clause).
