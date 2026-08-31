# `measurement_valid` Safety-Fix Review (post-Stage-4-revision verification)

**Task:** independently verify commit `755cd3e`'s revision to
`docs/superpowers/specs/2026-08-30-whale-confidence-scoring-remediation-design.md`, which was
written to close both blocking defects Stage 4's review (`5e092d2`) found in §9's
`measurement_valid` auto-apply gate. This is a re-verification pass, not a re-review of
anything Stage 4 already marked sound — CLAUDE.md's "never guess, verify or falsify" applies:
every claim below traces to a direct read of current worktree source, not to the design
document's or the revision's own restatement of itself.

## Verdict

**Sound. Go for Stage 5.** Both blocking defects are genuinely closed, not merely
re-worded. The three-way `data_status` distinction is well-formed with no fourth uncovered
case, `measurement_valid`'s "across both lists" definition correctly handles the concrete
cross-contamination scenario this review was asked to stress-test (and that scenario is real,
not hypothetical — `agreement_factor`/`depth_factor` are genuine members of both the accuracy
and edge factor sets per §7.1's YAML), the dangling `§6.3` reference is now a resolved `§9`
reference to a section that actually defines the field, and both minor findings landed as
described.

## 1. Both write paths — confirmed real, confirmed now covered

Read `main.py:430-465` and `services/whale_calibration/routes.py:100-167` directly (not the
design's restatement):

- `main.py:438` gates `_maybe_run_auto_apply` on `cc_cfg.get("auto_apply_enabled")`; the write
  is `config_store.update({"whale_confidence_weights": blended})` at `main.py:459`, fed by
  `confidence_calibration.blended_weights_for_auto_apply(...)` at `main.py:454`. Matches the
  design's `main.py:453-459` citation exactly.
- `services/whale_calibration/routes.py:116` (`apply_confidence_calibration_suggestion`,
  `POST /api/confidence-calibration/apply`) calls the same
  `blended_weights_for_auto_apply(...)` (`:149`) and the same `config_store.update(...)`
  (`:155`). Read the full function body (`:116-166`): **no `auto_apply_enabled` check exists
  anywhere in it** — confirmed by `grep -n auto_apply_enabled` over the file, which returns
  hits only in the unrelated enable/disable-toggle endpoints and a comment, never inside this
  route. The route's own comment ("always available regardless of `auto_apply_enabled`") is
  accurate, not aspirational.
- The dashboard button is real: `frontend/src/js/advisory-calibration.js:113` calls
  `fetchJSON('/api/confidence-calibration/apply', {method: 'POST'})` from a plain button
  click, no confirmation dialog.

The revision's "Touched files" section now names both `main.py` and
`services/whale_calibration/routes.py` explicitly (with line numbers), §9 states the block
applies "at **both** of `whale_confidence_weights`'s real write paths," §11's GitNexus
impact-check list gained a fourth required item naming both call sites, and §10's Phase 5 row
requires an integration test proving **both** refuse independently (`main.py`'s path with
`auto_apply_enabled: true` forced so the test can't pass by coincidence of the other gate
being closed; `routes.py`'s path called directly, since it reads no flag at all). Finding 1 is
closed.

## 2. The three-way `data_status` distinction

Read the current `_bucket_win_rates` source (`confidence_calibration.py:67-116`) directly: its
existing early exit is `if n < _BUCKET_COUNT or len(distinct_values) < _BUCKET_COUNT: return
{}` (line ~104-105) — exactly the branch the design labels `"insufficient_variance"`, trigger
condition unchanged. The new boundary-in-tie contamination check is necessarily a separate,
later branch: it only runs once a factor already has `>= _BUCKET_COUNT` distinct values (i.e.,
has *passed* the insufficient-variance check) and instead finds the specific tertile cut
sitting inside a large tied run. These two branches are structurally disjoint by construction
— a factor can't hit both in the same evaluation — and the design's own §3 text states the
partition as `"ok"` / `"insufficient_variance"` / `"contaminated"` with `"ok"` implicitly
being "neither of the other two fired," which is exhaustive given the if/elif structure
described.

**(a) Permanence of `analyst_factor`/`block_trade_factor`.** Read the audit directly
(`docs/superpowers/research/2026-08-30-whale-confidence-weights-factor-audit.md:369-404`):
§1.8 — "All 95,355 rows carry exactly 0.5 — 1 distinct value, 100% of the mass," "structurally
never populated." §1.9 — explicitly upgraded from a 2M-row sample to a **full census**:
`SELECT is_block_trade, COUNT(*) FROM raw_trades GROUP BY is_block_trade` returns "a single
group, `(0, 33,221,747)`" — every captured raw trade, without exception, because block trades
are matched off-book and never appear on the public trade flow this app consumes. Both are
census-confirmed structural facts, not "currently sparse" — "permanent" is the correct word,
not an overstatement.

**(b) `"contaminated"` fires only on the materiality-floor predicate, not ordinary small
ties.** This was already Stage 4's own "Verified sound #1" (the `max(30, 0.005*n)` floor
arithmetic, independently re-derived) and the revision didn't touch that arithmetic — it only
added the label. Re-confirmed the design's own framing is unchanged: `depth_factor`'s 2–6-row
incidental ties stay under the floor and must NOT trip the guard; only a tie `>= max(30,
0.005*total_n)` rows does.

**(c) No fourth, uncovered case.** The specific scenario posed — enough distinct values to
pass the variance check, but the actual low/high split tie count sits between 1 and the floor
— is explicitly the `depth_factor` reference case the design cites by name, and by the
predicate's own definition (`"contaminated"` requires `n_tied >= floor`) a sub-floor tie
correctly falls through to `"ok"`. This is not a new conclusion; it's the same floor logic
Stage 4 already verified, now correctly wired to a label rather than collapsed into the same
`{}` as the permanent-sparsity case.

Finding 2 is closed: `analyst_factor`/`block_trade_factor` now resolve to
`"insufficient_variance"` (never blocking), and only a real, materially-sized tie resolves to
`"contaminated"` (always blocking) — the two states finding 2 needed separated are separated.

## 3. `measurement_valid`'s "across both lists" definition — stress-tested against the concrete cross-score case

The task asked whether a factor reporting different `data_status` values between
`accuracy.per_factor` and `edge.per_factor` (e.g., `"insufficient_variance"` in one,
`"contaminated"` in the other) is handled correctly, or whether checking "both" hides an
OR/AND or single-list-only bug. This scenario is not hypothetical: read §7.1's YAML
(`whale_accuracy_weights` includes `agreement_factor`, `depth_factor`;
`whale_edge_weights` also includes both) — these two factors are genuine members of **both**
factor sets, and §7.5 confirms the two scores are computed over different row populations
(`accuracy` over the full resolved set, `edge` over "the priced subset only"). A tertile split
that lands clean in one population and inside a large tie in the other is a real possibility
this design has to handle, not a contrived edge case.

§9's definition: `measurement_valid` is `True` only when **none** of the report's per-factor
entries, **across both** `accuracy.per_factor` and `edge.per_factor`, carry
`data_status == "contaminated"`. Read as an aggregation: this is a check over the union of
both lists' entries — every entry from `accuracy.per_factor` and every entry from
`edge.per_factor` is checked independently, and any single contaminated entry anywhere in
either list flips the result to `False`. This is the safe (fail-closed) direction: it cannot
miss a contamination that shows up only in `edge.per_factor` for a factor that happened to be
clean in `accuracy.per_factor`, or vice versa — there is no scenario under this definition
where checking "both" degrades to checking only one list or silently ORs the wrong way.
§7.5 explicitly restates this too ("`edge.per_factor`'s entries carry `data_status` through
the same way `accuracy.per_factor`'s do — §9's `measurement_valid` reads both lists"), so the
"both" language is consistent everywhere it's used, not asserted once and contradicted
elsewhere. Finding-3-equivalent stress test passes: no aggregation bug found.

## 4. The `§6.3`→`§9` cross-reference fix

Grepped the full document for `§6.3` and `§9`: zero remaining `§6.3` hits, and `§6` in the
current document only has subsections `6.1`/`6.2` (`## 6.2 Pipeline-side` is the last
subsection before `## 7`) — confirming `§6.3` never existed as a real subsection in this
document at all; the original reference was dangling from the start, not merely stale. The
replacement (`"measurement_valid": <bool, §9>` at line 478) points at the section that
actually defines the field (§9, confirmed at line 571: `generate_calibration_report()`'s
`report` gains `"measurement_valid": bool`). §14's self-review section explicitly discusses
and justifies the forward-reference choice (§7.5 is where the field is *seen*, §9 is where
it's *justified*) rather than silently patching the number. No other section's numbering or
cross-references were disturbed — `§10`/`§11`/`§12` all still resolve to Phased rollout/
GitNexus/Testing as before.

## 5. The two minor findings

- **Citation softening (finding 3):** §5's text now reads "mirrors the same underlying idea
  ... worth naming as precedent for the *shape* of this decision, though it is a different
  operation over a different input shape" — matches the review's recommended language
  near-verbatim, and correctly preserves the distinction (config-level dict with all keys
  always present and renormalized over the whole set, vs. per-row dict with absent keys
  dropped from the sum entirely).
- **`config-field-edit` skill (finding 4):** confirmed present in two places — §7.1 gained an
  "Implementation-plan note" naming the skill and both write paths by name, and §10's Phase 4
  rollout row now reads "via the `config-field-edit` skill given the two live write paths
  named in §9." Verified the skill file exists at
  `.claude/skills/config-field-edit/SKILL.md`, so the reference resolves to something real.

## What Stage 5 should do with this

Proceed. §9's `measurement_valid` gate, as revised, is a real, evidence-traceable, fail-closed
block on both live write paths of `whale_confidence_weights`, and its own definition can
distinguish the one failure mode it exists to catch (transient tie-contamination) from the one
it must never falsely trip on (permanent structural sparsity in `analyst_factor`/
`block_trade_factor`). Nothing found in this pass reopens Stage 4's findings or introduces a
new one; this review found no additional defect.
