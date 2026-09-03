# Consolidation — Strategy Edge Gate Design (2026-09-03)

Reconciling `docs/superpowers/specs/2026-09-03-strategy-edge-gate-design.md` (commit
`3733d73`), its embedded Design self-review, and the independent adversarial review
(`docs/superpowers/specs/2026-09-03-strategy-edge-gate-design-review.md`, commit `bd953e7`,
a fresh Agent call with no memory of the authoring session) per CLAUDE.md's "nothing
advances on one pass" HARD RULE.

## Verdict: **GO**

The adversarial review's independent verdict was GO-AFTER-FIXES: every headline claim
confirmed against primary sources (including two re-derived from scratch — a hand-written
PDF text extractor for the Kalshi fee-schedule PDF, and a full re-read of the live
`config/settings.yaml` in the *primary* repo, not just this worktree's committed copy),
no safety boundary crossed, no finding rising to NO-GO. All 4 must-fix items and all 3
should-fix items are now applied to the design document. Nothing in the fix pass changed
the design's scope, architecture, or recommendations — every fix is a citation, wording, or
internal-consistency correction. This document is the "GO" that clears the design for an
implementation-plan stage; no re-run of the full self-review/adversarial-review cycle is
needed per the HARD RULE's own text ("this recheck is scoped to the fix list... re-run the
full cycle from scratch only if the revision changes the artifact's scope or introduces a
claim the original two reviews never saw").

## Disagreements between self-review and adversarial review

None. The design's own self-review (§"Design self-review", end of the artifact) explicitly
flagged the exact risk category that the adversarial review's Finding 7 found already
realized ("these are this worktree's checkout at the time of writing... worth re-checking at
adversarial-review time if any time has passed") — the two reviews agree on the finding, one
anticipated it as a hypothetical, the other confirmed it as real. No adjudication was needed.

## Merged fix list and disposition

| # | Source | Finding | Disposition |
|---|---|---|---|
| 1 | Adversarial, must-fix 1 (Finding 2a) | `get-series-list.md:172-183` citation wrong; real content at 198-208/260-271 | **Fixed** — citation corrected in §1.2 |
| 2 | Adversarial, must-fix 2 (Finding 2b) | Fabricated "2026-08-16" date attached to the `flat`-fee-type claim; no such date exists in `kalshi_fees.py` | **Fixed** — both occurrences (§1.3 point 3, self-review) now state the claim is undated in the primary source |
| 3 | Adversarial, must-fix 3 (Finding 7) | §0's `config/settings.yaml` line citations stale against the live *primary-repo* file (an uncommitted edit already tracked as "the third data-wipe," second-pass audit §4.4, removes the comment §0 leans on as evidence) | **Fixed** — §0's line-number citations to this file dropped in favor of key names; explicit note added that the justifying comment is currently absent from the live file though the underlying `0.0` value is unaffected; a scoping note added explaining why line numbers are omitted throughout §0 specifically |
| 4 | Adversarial, must-fix 4 (Finding 6) | Tension between §5's blanket "every field defaults to no behavior change" framing and §8's (correct) disclosure that the markout-capture sweep runs unconditionally | **Fixed** — §5 now scopes the opt-in claim to its own 8 fields and states the sweep as a named, deliberate exception with its own rationale; §3.3's runtime-cost-measurement note now explicitly extends to the sweep, not only `_validate_entry_price`'s new DB reads |
| 5 | Adversarial, should-fix 1 (Finding 2c) | "Roughly a quarter" vs. actual ~37.4% ("roughly a third") | **Fixed** — corrected with the exact ratio shown |
| 6 | Adversarial, should-fix 3 (Finding 1) | Off-by-one citations: `evaluate()` "285-683" (should be 285-684, ×2 occurrences), `exit_engine.py:571-578` (should be 570-578), `confidence_scoring.py:311` (should be 312) | **Fixed** — all four corrected |
| 7 | Adversarial, should-fix 2 | Whether `edge_gate_fee_buffer_usd: 0.005` is still fairly "on the same order" as the worked example's $0.001361 rounding-fee component | **Not fixed — deliberately deferred.** The review itself calls this "defensible, worth tightening once real net-fee data exists" and notes §6 already earmarks the value for revisit. No primary-source fact changed (the worked example's numbers are unchanged by fix #5 above, only the prose ratio describing them was wrong). Revisiting the actual buffer default before any real net-fee data exists would be tuning a paper-mode placeholder without new evidence — out of scope for a citation-fix pass. Left as an explicit plan-stage/§6 follow-up, as the design itself already frames it. |

Fix #7 is the one should-fix item not applied verbatim; it is a "consider revisiting" note,
not a factual or internal-consistency error, and the design document already carries its own
honest placeholder-not-settled framing for this exact number (§5's table entry, §"Design
self-review" gaps list). Deferring it does not block GO.

## Verification of the fix pass against the fix list

Checked item-by-item post-edit (not accepted on completion claim alone, per the HARD RULE's
"a revision that silently drops a requested fix is itself a defect" clause):

- `grep -n "2026-08-16"` → zero hits (was 2, both fixed).
- `grep -n "roughly a quarter"` → zero hits (fixed to "roughly a third" with the exact ratio).
- `grep -n "172-183\|285-683\|confidence_scoring.py:311\|571-578"` → zero hits (all four stale
  citations corrected).
- Code-fence count unchanged (6, balanced) — the fixes touched only prose, no fenced blocks.
- §0's `config/settings.yaml` citations now key-name-only; the "this design's own weight
  values checked against the live primary file, not just this worktree" caveat is stated
  once, adjacent to the finding it qualifies, not scattered.
- §5's opt-in framing now explicitly excludes the markout sweep, matching §8's own (already
  correct) disclosure — the two sections no longer say different things about the same
  feature.
- §3.3's runtime-cost-measurement requirement now names the markout sweep by cross-reference,
  not only `_validate_entry_price`'s new reads.

## What GO means here

Per CLAUDE.md's "nothing advances on one pass" HARD RULE, "implementation plan" names a
later, distinct pipeline stage (a document under `docs/superpowers/plans/*.md`, itself
requiring its own full self-review + adversarial review + consolidation cycle before *it*
can be considered ready) — this consolidation clears the **design** stage only. The next
step is authoring that plan document from this now-GO design, not writing code: this design
document itself states "Zero code changes in this document — design only," and that
constraint carries forward until an implementation plan exists and clears its own review
cycle.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
