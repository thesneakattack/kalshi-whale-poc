# Self-review: `db-migration-gate1-preaudit-2026-09-03.md`

Own-context review of the pre-audit doc (commit 93b156c) before it goes to adversarial review. Checked internal consistency and re-verified a sample of file:line citations against current source, not just re-read the prose.

## File:line spot-check

Re-ran 5 citations spanning all 5 modules directly against current source (not from memory of writing them):

- `services/risk_manager.py:49` — `def _connect(db_path: Path) -> sqlite3.Connection:` — matches.
- `services/paper_broker.py:337-338` — `def _connect(self): return _connect(self.db_path)` — matches.
- `services/series_watcher.py:414-419` — lock swap-and-clear then `with _connect() as conn:` — matches, confirms the "lock released before connect" claim structurally, not just by reading the surrounding prose.
- `services/settlement_edge.py:76-82` — both indexes, including the partial index's `WHERE settled_yes IS NULL` clause — matches exactly, including the clause text the doc's own §"settlement_edge.py" claims must be preserved.
- `tests/test_candidate_ledger.py:56` — `with candidate_ledger._connect() as conn:` — matches, confirms the "hybrid-compatible, with-wrapped" claim for this one test call site.

All 5 held. No fabricated or stale citation found in this sample.

## Internal consistency

- The cross-module summary table's five rows agree with each module's own prose section on every column (busy_timeout, schema evolution count, index count, async path, test-calls-connect) — cross-checked each cell against its source section rather than trusting the table was transcribed correctly.
- The two "applies across all 5" findings at the bottom (no module sets busy_timeout; zero bare-assignment test calls) are each independently true for all 5 per their own per-module sections — not asserted without the per-module evidence actually supporting them.

## One gap, not a defect, worth naming explicitly

The doc doesn't independently re-verify the coordinator's own consolidated decisions (D1/D2/D3) on the two flagged open questions (series_watcher's async path, the shared `raw_trades` DDL) — those came back as coordinator decisions after this doc was submitted, not something this doc itself needed to resolve. Recorded here so a reader doesn't expect this pre-audit to already reflect them; it predates them by design (research stage, not a synthesis of the plan-stage response).

## Verdict

GO — no correction needed to the original doc's claims. Ready for adversarial review.
