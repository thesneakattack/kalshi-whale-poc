# Consolidation — Persistence Layer Redesign Design (2026-09-03)

Reconciling `docs/archive/lane-5-runtime-infrastructure/specs/2026-09-03-persistence-layer-redesign-design.md (moved there 2026-09-06, planning-lanes migration)`
(commit `bb64a7c`), its embedded Design self-review, and the independent adversarial review
(`docs/archive/lane-5-runtime-infrastructure/specs/2026-09-03-persistence-layer-redesign-design-review.md (moved there 2026-09-06, planning-lanes migration)`, commit
`12ae40c`, a fresh Agent call with no memory of the authoring session) per CLAUDE.md's
"nothing advances on one pass" HARD RULE.

## Verdict: **GO**

The adversarial review's independent verdict was GO-AFTER-FIXES: both headline benchmarks
(SQLAlchemy Core beating a hand-rolled closing connection per-call; DuckDB's `sqlite_scanner`
being *slower* than native SQLite because it never pushes the series filter into SQLite's own
index) were independently reproduced from scratch — the DuckDB number within 4% of the
original on a live, actively-written 29.6 GB file, and the "no index pushdown" mechanism
confirmed even more directly than the original document, via DuckDB's own `EXPLAIN` physical
plan. No claim was found fabricated. All 4 must-fix items and all 3 should-fix items are now
applied to the design document. None of the fixes reverse or materially alter any of the
document's seven top-level decisions — the adversarial review states this explicitly, and one
fix (the small-file-consolidation decline, §5) came out *strengthened* by evidence the review
surfaced that the original document hadn't cited. This clears the design stage; per the HARD
RULE's own text, a fix-list recheck is sufficient here and a full second self-review/
adversarial-review cycle is not required, since no fix changed the artifact's scope or
introduced a claim neither original review had seen.

## Disagreements between self-review and adversarial review

None outright, and one notable case where the self-review's own predictions were vindicated
rather than contradicted:

- The self-review's gap #6 explicitly named the "~14 files under 1MB" figure as unverified and
  predicted "an adversarial reviewer checking §5 should re-confirm... against current
  `data/*.db` sizes" — Finding 18 did exactly that and found 17, not 14. Agreement, not
  disagreement: the self-review correctly anticipated exactly the check that turned out to
  matter.
- The self-review's gap #2 (compression-ratio uniformity assumption) was flagged as
  theoretically unverified; the adversarial review's Finding 9 went further and measured it
  directly, finding it not just unverified but measurably false in the expected direction.
  Again agreement in direction, with the adversarial review adding the missing measurement.
- No finding in the adversarial review contradicts a self-review claim; where both touched the
  same ground, they align. No adjudication was needed.

## Merged fix list and disposition

| # | Source | Finding | Disposition |
|---|---|---|---|
| 1 | Adversarial, must-fix 1 (Finding 14) | Tick cadence stated as "6 seconds, verified live" is wrong for the currently-streaming app; real driving cadence is 30s (`safety_net_interval_sec`, via `_tick_interval_sec()`'s streaming-mode branch) | **Fixed** — §4.2 corrected in three places (the writer description, the collision-likelihood paragraph, and §4.3's fix-direction framing), plus §8's regression-test description now names the corrected number explicitly |
| 2 | Adversarial, must-fix 2 (Finding 9) | Compression-ratio calculation's "uniform per-row bytes across series" assumption is measurably false — `KXBTC15M` runs 14-17% smaller/row than most sampled series | **Fixed** — §3.3 now states the assumption is false, cites the measured byte-length variance, and reframes 10.0×/12.3× as an upper bound specific to `KXBTC15M`, not a whole-table estimate; notes the load-bearing query-speed figures (58-644×) are unaffected |
| 3 | Adversarial, must-fix 3 (Finding 10) | Export-time extrapolation ("40.5M/26.0M × 181.6s ≈ 283s") assumes linear-in-output-rows scaling, contradicted by the `EXPLAIN`-confirmed fixed-full-table-scan mechanism | **Fixed** — §3.3 now uses the review's two-point fixed-cost/marginal-cost model (≈72s fixed + ≈4.2µs/row), giving ≈243s/4.1min via the correct mechanism, noting the original number was close for the wrong reason |
| 4 | Adversarial, must-fix 4 (Finding 18) | "~14 files under 1MB" is actually 17; downstream "~42 fds" should be "~51 fds" | **Fixed** — §5 corrected on both numbers, plus the independently-relevant §4.1 contention evidence (Finding 19) added as further support for the decline decision |
| 5 | Adversarial, should-fix 1 (Finding 6) | Appendix's "fully specified in §1.2/§1.4" claim is false as originally written — the WAL pragma, the single most load-bearing detail for the fd-leak result, was never stated as part of the benchmarked variants | **Fixed** — §1.2 now states explicitly that every variant applies `PRAGMA journal_mode=WAL` per connect, matching the real app idiom; the Appendix's reproducibility claim is now accurate |
| 6 | Adversarial, should-fix 2 (Finding 5) | Docstring-quote misattribution: "gate every whale signal" is from `_scoring_pool.py`, not `candidate_ledger.py` | **Fixed** — §2.1 corrected with the right module named; underlying substantive claim (independently confirmed true from `decision_bridge.py`'s own code) is unchanged |
| 7 | Adversarial, should-fix 3 (Finding 17) | Optional footnote: ~2h43m gap between the C1 fix's merge time and its last observed fault-log occurrence, for timeline reconstruction | **Fixed** — §4.1 now includes the merge commit/timestamp and the footnote, framed as ordinary deploy lag, not an incomplete fix |

Every item in the adversarial review's must-fix and should-fix lists was applied; none were
deferred.

## Verification of the fix pass against the fix list

Checked item-by-item post-edit (not accepted on completion claim alone, per the HARD RULE's
"a revision that silently drops a requested fix is itself a defect" clause):

- `grep` for "6-second tick"/"once per 6-second"/the old "verified live" 6s phrasing → zero
  hits outside the now-corrected context (the self-review's own historical record of the
  original figure is left untouched by design — see note below).
- `grep` for "~14 files"/"~42 fds"/"≈ 42 fds" in the live document body → zero hits in §5
  (the only remaining "~14 files" mentions are in the Design self-review section, where they
  accurately describe what the self-review originally flagged as unverified — corrected in
  the review's own historical context, not left as a live, uncorrected claim in the document
  body).
- Code-fence count unchanged (8, balanced) — the fixes touched only prose and one Markdown
  table's surrounding text, no fenced blocks.
- §1.2's benchmark table description now states the WAL pragma explicitly; the Appendix's
  "fully specified" claim is downstream of that fix and did not need its own separate edit.
- §3.3's compression-ratio section and export-extrapolation section both now carry the
  corrected mechanism/assumption language immediately adjacent to the numbers they qualify,
  not as a disconnected caveat elsewhere in the document.

One deliberate scope note: the self-review section (end of document) is left as an accurate,
unedited historical record of what the design's author flagged as open at authoring time —
including the original "~14 files" and "6 seconds... verified live" figures in their original
self-review context, where they describe what was flagged as a gap rather than assert a
current fact. This mirrors how the adversarial review itself treats the self-review: as a
record of process, not a body of live claims. All *live* claims in the numbered sections (§1
through §9) were corrected.

## What GO means here

Per CLAUDE.md's "nothing advances on one pass" HARD RULE, "implementation plan" names a
later, distinct pipeline stage (a document under `docs/superpowers/plans/*.md`, itself
requiring its own full self-review + adversarial review + consolidation cycle before *it*
can be considered ready) — this consolidation clears the **design** stage only. The next
step is authoring that plan document from this now-GO design, not writing code: nothing in
this design was implemented, and the constraint carries forward until an implementation plan
exists and clears its own review cycle.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
