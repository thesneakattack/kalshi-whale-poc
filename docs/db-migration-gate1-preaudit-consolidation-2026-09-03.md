# Consolidation: Gate 1 pre-audit, general-bucket modules (PR #508)

Reconciling the self-review and the independent adversarial review (fresh Agent, no memory of
the authoring conversation, instructed to re-derive claims from primary sources).

## What the adversarial review actually did

Verified independently, with evidence: re-checked out the PR's merge-base to confirm the
`accounts_store.py` citation was wrong from the start rather than drift; re-ran the
`@contextlib.contextmanager` sweep across all 21 modules itself rather than trusting the
document's own grep output; re-verified every one of the 21 `_connect()` def-line citations
against source; independently traced the `data_quarantine.py` and `suggestion_decisions.py`
call chains line-by-line; re-derived the corrected 22-count's four-file breakdown; and — beyond
its assigned checklist — spot-checked two modules the self-review hadn't flagged
(`game_state.py`, `market_analyst_agent/_db.py`, both confirmed exact) plus two more
(`reset_log.py`, `trade_archive.py`) that turned out to carry a real, load-bearing error.

## Findings and disposition

| # | Finding | Disposition |
|---|---|---|
| 1 | `accounts_store.py` event-loop claim, substance | CONFIRMED — no change |
| 2 | `accounts_store.py` citation `main.py:1828-1850` | **INACCURATE** — correct range is `1926-1950`. **Fixed.** |
| 3 | `data_quarantine.py` call chain | CONFIRMED exact — no change (one severity nuance added: 1s cache on `is_active()`) |
| 4 | `suggestion_decisions.py` call sites | CONFIRMED exact — no change |
| 5 | Universal `@contextlib.contextmanager` absence, all 21 | CONFIRMED, independently re-run — no change |
| 6 | `coordination_engine` 22-count, all line numbers | CONFIRMED exact — no change |
| 7 | `game_state.py` spot check | CONFIRMED in full — no change |
| 8 | `market_analyst_agent/_db.py` spot check (structure) | CONFIRMED in full — but see #10 |
| 9 | `alerting.py` / "no test file" claims | CONFIRMED — no change |
| 10 | `reset_log.py` / `trade_archive.py` event-loop verdict | **INACCURATE, load-bearing** — both wrongly marked NO; the enclosing route (`services/reset/routes.py:151`, `async def reset_broker`) runs on the event loop directly, not thread-pooled. **Fixed to YES with evidence.** |

## Follow-up beyond the adversarial review's own scope (own initiative, verified before writing)

Tracing the same `reset_broker` handler the adversarial review used for finding #10 surfaced
that it also calls six more of this bucket's modules synchronously, with no dispatch:
`shadow_mode.py`, `series_evaluator.py`, `trade_category.py`,
`whale_calibration/calibration_history.py`, and `market_analyst_agent/_db.py` (all previously
NO or unconfirmed, now YES), plus `candidate_log.py` (previously flat NO, now documented as
MIXED — its main write path is genuinely off-loop, but `clear_range()` is not). Each was
independently confirmed against source (function existence, exact call site in `routes.py`,
absence of any `await`/`to_thread`/`tick_executor` in the handler body) before being written
into the document — not asserted from the pattern alone. All fixed, commit `0b28b02`.

No disagreement between self-review and adversarial review to adjudicate. The self-review
correctly scoped its own gaps and gave the adversarial reviewer a five-item checklist; the
adversarial reviewer used that checklist as a floor, not a ceiling, and its two additional
spot-checks (beyond the five) are exactly what caught the real error — a pattern worth noting
for future reviews: an adversarial reviewer that only checks the author's own named-uncertain
items will miss errors in the items the author was confident about, which is exactly where #10
lived.

## Required fixes (all applied and verified)

1. `accounts_store.py` citation corrected to `main.py:1926-1950`.
2. `reset_log.py` and `trade_archive.py` event-loop verdicts flipped to YES with route evidence.
3. (Own follow-up) `shadow_mode.py`, `series_evaluator.py`, `trade_category.py`,
   `calibration_history.py`, `market_analyst_agent/_db.py` flipped to YES;
   `candidate_log.py` corrected from flat NO to MIXED. All with specific route/line evidence,
   verified via direct grep/read against `services/reset/routes.py` before writing.
4. Document's opening and closing sections updated to reflect the corrected state rather than
   leaving stale "not confirmed" language standing.

None of these changes the document's fundamental scope (still the same 21 modules, same 5
data points per module) or introduces a claim neither review has now seen — this is corrections
within the existing structure, verified against the fix list before considering it done.

## Verdict

**GO for this document to feed the implementation-plan stage**, conditional on (and now
satisfied by) the corrections above. The document's core claims — the universal
`_connect()`-leak finding, the corrected `coordination_engine` count, the per-module
DDL/table/test-isolation facts — all survived independent re-derivation. The one real defect
found (the `reset_log.py`/`trade_archive.py`/related event-loop miscategorization) has been
fixed and, unusually for a "fix the cited examples" pass, extended to cover the full blast
radius of the same underlying mechanism rather than just the two lines the reviewer named —
worth the implementation plan treating the `/api/reset` handler's event-loop exposure as a
cross-cutting concern for seven modules, not seven independent findings.

Per this repo's "nothing advances on one pass" rule, reporting this verdict to autotrade-1d for
merge sequencing — not merging directly, as instructed.
