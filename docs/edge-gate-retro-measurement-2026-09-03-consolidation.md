# Consolidation: `edge-gate-retro-measurement-2026-09-03.md`

Reconciles two full review rounds, plus PM-directed follow-up questions
answered between them, into a single GO verdict and fix-list recheck, per
CLAUDE.md's "nothing advances on one pass" HARD RULE.

## Full review history

1. `-self-review.md` — GO, caught and fixed a real presentation ambiguity
   (a subset miscounted as a disjoint bucket in §1) before adversarial
   review.
2. Three PM-requested follow-up measurements added (§3.5: in-band
   coverage of the un-evaluable set, config-vs-data-limit test, `min_edge`
   sensitivity) — decisive on the question that mattered most to the PM's
   original framing (the un-evaluable set is not disproportionately
   in-band).
3. `-adversarial-review.md` — **GO WITH REQUIRED FIXES.** 2 CRITICAL, 2
   HIGH, 8 MEDIUM/LOW. Found the doc's root-cause explanation for the
   coverage gap was backwards (blamed "markets too new to have history"
   when the actual driver was `market_history.retention_hours: 168`
   pruning older history — the doc's own falsifier test couldn't
   distinguish the two hypotheses), named the wrong exemplar markets,
   overreached on §2's "neither condition is currently occurring" and
   §4's "transfers to observe-only unchanged," misframed §3's sample as a
   lifetime shortfall, and — most consequentially — found that nothing in
   the doc had checked whether the gate would have rejected losing
   trades. An indicative P&L check found the would-reject cohort had the
   *highest* win rate of any bucket.
4. `-self-review-round2.md` — GO, after applying all of round 1's fixes.
   Independently re-verified (not trusted) the two most consequential
   findings before applying them: re-checked `market_history.db` directly
   (oldest snapshot age corroborating the retention mechanism) and wrote
   an independent P&L-matching script that reproduced the qualitative
   finding (would-reject cohort not worse) on a later data snapshot.
5. PM raised two decision-critical questions about the new §3.6 finding
   (is the P&L net of fees; what does a 20-hour window support) plus a
   framing note (lead with the dollar EV figure, not win rate). Answered
   directly from source: `realized_pnl = mark_to_market(...) - entry_fee
   - close_fee`, and `mark_to_market()` is pure gross price-delta with no
   fee term — confirmed net of fees, not gross. Regime-exposure caveat
   added explicitly. Doc reframed to lead with the dollar figure.
6. PM separately flagged that the doc's own reproduction (+$7,159.49) and
   the round-1 review's own reproduction (+$5,456.90) disagreed in
   magnitude and needed reconciling explicitly rather than left for a
   reader to notice as unaddressed inconsistency. Fixed: stated plainly
   that two independent scripts on different snapshots aren't expected to
   agree exactly, and that the finding is the sign/ordering, not either
   specific number. Opportunity-cost named as a stated limitation.
7. `-adversarial-review-round2.md` — **GO WITH REQUIRED FIXES.** Explicitly
   instructed not to rubber-stamp round 1's fixes and to independently
   reproduce the P&L finding itself. No load-bearing conclusion inverted
   — every central number reproduced, and the P&L finding *strengthened*
   under a capital-normalized-return check the doc hadn't run (would-
   reject cohort led at 20.7% return vs 18.2%/11.1%). But found: (HIGH #1)
   a genuine self-contradiction this doc introduced while fixing round
   1 — fusing §1's retention root cause onto §3's separate, non-retention-
   contaminated 61.4% figure inside one Summary bullet; (HIGH #2) the
   §1 exemplar-market correction had repeated the exact volume-vs-
   coverage-rate conflation it was written to fix, presenting all-window
   statistics under a within-retention label; (MEDIUM #3-7) an unmeasured
   staleness-frequency claim, the 81.7%-no-category figure being an
   offline-table artifact misapplied to live runtime risk, a real
   double-counting bug in this doc's own §3.6 P&L-matching script, a
   wrong function/line citation for the live category source, and a
   line-citation staleness note; (LOW #8-11) an overbroad "zero
   consumers" grep claim, an imprecise fee-margin-in-band claim, an
   incomplete exemplar characterization, and an untimestamped bankroll
   figure.
8. This consolidation, with the fix-list recheck below.

## Adjudication

No disagreement between any two review passes to adjudicate on the
merits. Round 2 found genuinely new defects (a self-contradiction and a
repeated conflation error) rather than contesting round 1's or the PM's
conclusions — every finding round 2 raised was accepted and fixed
outright, since each was independently checkable and held up (e.g., the
P&L table's arithmetic defect: `closed + still_open ≠ entries` in every
row of the original table, an objective, unambiguous bug).

## Round 2 fix-list recheck (item by item, against the current file)

1. **HIGH #1 (Summary self-contradiction)** — Summary's coverage-gap
   bullet split into a clean statement: §1's 30-day gap is ~60%
   retention-driven; §3's <1-day gap is a genuine capture hole (per
   §3.5's own conclusion), with correct, non-contradictory fix
   implications stated for each. Verified by re-reading the full bullet
   for internal consistency after the edit.
2. **HIGH #2 (exemplar paragraph repeats the conflation)** — §1's exemplar
   section rewritten with a table restricted specifically to
   within-retention (≤7d) shares and zero-snapshot rates, correctly
   identifying `KXGOLD15M` as the largest *volume* contributor
   (30.1% of within-retention misses, not "well covered and dropped") and
   `KXMVECROSSCATEGORY` as the worst *per-signal coverage* (100% at every
   age) — two distinct claims, not conflated. Sports-family zero-snapshot
   rates corrected from the wrong all-window 83-99% down to the real
   within-retention 23.9-55.7%. The inherited-but-wrong "~47% of all
   misses" figure removed.
3. **MEDIUM #3 (unmeasured staleness claim)** — §2 corrected: states the
   measured distribution (median 17.4s, p99 199.9s, 0% exceeding 600s)
   in place of the false "sits near the stale end" speculation.
4. **MEDIUM #4 (81.7% misapplied to live risk)** — §2 and its lead-in
   corrected: states the 81.7% figure is specific to the offline
   `trade_category.db` table, and that the live gate's actual no-category
   rate (via `event_titles`) is closer to ~27%.
5. **MEDIUM #5 (§3.6 arithmetic bug)** — found and fixed the actual bug
   (a close event could be matched to more than one entry on the same
   ticker since nothing marked it consumed); rewrote the matching script
   with proper consume-once pairing; re-ran and got a fully reconciling
   table (`closed + still_open = entries` in all three rows, `sum/closed`
   matching each stated mean exactly). This is now a third independent
   measurement, on a third data snapshot, alongside round 1's review and
   this doc's own first (buggy) attempt — all three agree on sign and
   ordering, stated explicitly rather than picking one number to feature.
6. **MEDIUM #6 (wrong category-source citation)** — §3 corrected: the
   live category source is `event_info.get("category")` from
   `state["event_titles"]` (`decision_bridge.py:104-105`), not
   `_category_by_ticker()` (which serves the fill-confirmation paths, not
   the signal-evaluation path this doc's simulation mirrors). Round 2's
   own "0 verdict flips" cross-check attributed honestly (not re-run a
   third time here, since it requires live in-memory app state a
   standalone script can't query the way it queries a `.db` file).
7. **MEDIUM #7 (stale line citation)** — noted explicitly: correct as of
   this branch's base commit, `origin/main` has since moved
   `record_rejection` via a merged migration PR; the argument-list claim
   holds in both versions.
8. **LOW #8 (overbroad grep claim)** — scoped to "zero production
   consumers," naming the one test-only reader found.
9. **LOW #9 (imprecise band-margin claim)** — corrected to state the
   margin varies across the 0.60-0.95 band (~0.062 at 0.60, ~0.045 by
   0.95) rather than a single "~0.06."
10. **LOW #10 (KXETHD miscategorized)** — added its real figures (51.2%
    covered / 26.8% zero-snapshot) rather than implying it matches
    gold/silver's 62-64%.
11. **LOW #11 (untimestamped bankroll)** — timestamped both the doc's own
    reading and round 2's later one.

All eleven confirmed present and correct in the current file — each
re-read against the review's specific required-fix text after editing,
not accepted on the edit's own completion claim.

## GO / no-go

**GO.** Two independent, fresh adversarial-review passes plus the PM's
own direct questioning have now examined this doc's load-bearing claims
— the retention-driven coverage gap, the 18/18 calibrated cells, the
delta=0.0 strict-filter algebra, the ~75-78% rejection rate among
evaluable entries with its ~38% evaluability ceiling, the not-
concentrated-in-band result, and (most consequentially for the actual
decision) the §3.6 outcome finding — and every one of them reproduced
independently, several under conditions specifically designed to try to
break them (round 2 was explicitly told not to trust round 1's fixes and
to re-derive the P&L finding itself; it did, on a third data snapshot,
after finding and helping surface a real bug in this doc's own script,
and the finding not only survived but *strengthened* under a
capital-normalized check nobody had run before).

This is ready for the PM to take to the user. The central, decision-
shaping message this doc now delivers plainly: the volume-level case for
enabling `edge_gate_enabled` is real (the gate would reject ~75-78% of
what it can evaluate, itself only ~38% of entries), but nothing in this
research shows that rejection would improve results — the one outcome
check available, run three independent times, points the other way. That
is not a recommendation to flip the switch or leave it off; it is a
statement that the research commissioned to inform the decision found the
volume question answerable and the EV question still open.
