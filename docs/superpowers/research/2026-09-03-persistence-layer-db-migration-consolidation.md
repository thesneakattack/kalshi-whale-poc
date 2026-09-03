# Consolidation: 2026-09-03 persistence-layer db-migration research doc

Reconciling the research doc, its self-review, and an independent adversarial review (fresh
Agent call, no memory of the authoring conversation, instructed to re-derive claims from
primary sources rather than trust the doc's own tables).

## What the adversarial review actually did (not taken on faith)

Verified genuinely independently, with evidence: re-ran the 30-module grep itself; read
`tick_executor.py`'s header directly; located `services/db.py`/`tests/test_db.py` on the
`feat/persistence-layer-unified-connect` branch/worktree and **actually ran** the 8-test suite
there (`8 passed in 0.19s`) rather than trusting the doc's "8 tests, passing" claim; read two
*additional* unmigrated modules (`series_watcher.py`, `settlement_edge.py`) beyond the ones the
original doc sampled, confirming the same leak shape; queried the live `fault_log.db` directly
for the lock-count figures instead of accepting the doc's numbers. This is exactly the kind of
re-derivation-from-primary-sources this stage is supposed to produce, not a rubber stamp.

## Findings and disposition

| # | Finding | Disposition |
|---|---|---|
| 1 | tick_executor.py precedent characterization | CONFIRMED — no change |
| 2 | test_db.py: 8 tests, passing, branch-only | CONFIRMED (actually run) — no change |
| 3 | "PRs #499/#500" for the happy-path fix wave | **INACCURATE** — all 5 commits are in PR #499 alone; #500 is unrelated (tier1-backend-hygiene, touches fault_log.py only for an unrelated DDL refactor). **Fix: "PR #499".** |
| 4 | grep count/list of 30 modules | CONFIRMED — no change |
| 5 | series_watcher.py / settlement_edge.py sampled independently | CONFIRMED same leak shape — no change |
| 6 | db.py not on `main` | CONFIRMED — no change |
| 7 | 5-already-migrated pattern sanity check | CONFIRMED — no change |
| 8 | "11... per db.py's own commit message" | **INACCURATE attribution** — the count (11) is right, independently reconfirmed by the reviewer's own grep, but the commit message never states "11." **Fix: drop the false citation, keep the number.** |
| 9 | series_watcher precedent cited as PR #394 | **INACCURATE** — PR #394 doesn't touch series_watcher.py at all (its files are candidate_log.py, game_state.py, index_feed/ingestion.py, settlement_edge.py, main.py). The real precedent, matching what tick_executor.py's own header cites, is **PR #23** ("Realtime data-plane remediation — Phase P0"). **Fix: PR #394 → PR #23, and correct the description to match what PR #23 actually did (series_watcher.py + tick_executor.py, cross-thread lock guard, commit 868dbf8).** |
| 10 | Fault-log lock counts | CONFIRMED, doc actually understates severity (omits the larger `capture_writer/flush`=237 bucket) — optional addition, not a correctness fix |

No disagreement between self-review and adversarial review to adjudicate — the self-review
correctly scoped its own gaps (paper_broker.py verification, and named the three areas the
adversarial pass should prioritize) and the adversarial review used exactly that list as its
starting checklist, then found three additional, independent citation errors beyond it.

## Required fixes (merged list)

1. "PRs #499/#500" → "PR #499" in the "5 of the 30 are already migrated" section.
2. Remove "per `db.py`'s own commit message" as the citation for the "11" count in the
   duplication paragraph; the number stays (independently reconfirmed twice now), the
   attribution goes.
3. Fix the series_watcher precedent citation: PR #394 → PR #23, with corrected description
   (PR #23, "Realtime data-plane remediation — Phase P0," merged 2026-08-26, commit `868dbf8`,
   guards series_watcher's capture buffers with a real cross-thread lock — not a
   candidate_log/series_watcher lock-contention incident from PR #394, which doesn't touch that
   file).

None of these three fixes changes the document's scope or introduces a new claim the two
reviews haven't already seen — they're narrow citation corrections. Per this repo's own
"nothing advances on one pass" rule, that means a fix-list recheck against the revised document
is sufficient; a full second self-review-plus-adversarial-review cycle is not required.

## Verdict

**GO, conditional on the three fixes above being applied and verified against this list before
the document is committed.** The document's core argument — 25 of 30 modules share a real,
verified connection-leak shape; a working, tested unified `db.py` prototype already exists but
is unmerged; the tick_executor precedent is real and correctly informs migration risk; two
safety-adjacent modules need extra scrutiny — survives all three corrections intact. This was a
citation-accuracy problem, not a structural one.
