# Consolidation: `db-migration-gate1-preaudit-2026-09-03.md`

Reconciles the self-review (commit 5a4f517) and the independent adversarial review (fresh, memory-less Agent dispatch) into a final verdict.

## Adversarial review summary

Independently re-derived every load-bearing claim from source, not from the doc: all 5 modules' `_connect()` bodies and their exact statement lists (PRAGMAs, indexes, `add_column_if_missing` calls, including the partial index's `WHERE` clause), the PR #23 lock-release-before-`_connect()` claim (grepped every `_buffer_lock` reference in `series_watcher.py`, confirmed no overlap), all 5 modules' test call-site shapes, the shared `RAW_TRADES_DDL_SQL` claim, and the async schema-init path (including independently pulling `services/db.py` off `fix/db-foundation-must-fix-tests` to confirm it has no async equivalent and does identity-check schema registrations). Every one of these held.

One issue found: `paper_broker.py`'s `_connect()` call-site count was stated as 9 ("`:294` and 8 more"); actual count is 10. Minor, non-load-bearing — doesn't change the qualitative claim (all `with`-wrapped) the implementation plan actually depends on.

## Fix applied

Corrected the call-site count and listed all 10 line numbers explicitly (`services/paper_broker.py`'s 10 `with self._connect()` sites: `:294, 414, 465, 477, 637, 709, 759, 785, 799, 808`) — verified directly via `grep -n "with self\._connect(" services/paper_broker.py` before writing the correction, matching the adversarial reviewer's own count.

## Second correction, post-consolidation recheck pass

After the above was written, the coordinator relayed a further finding (independently re-verified here before applying: `sed -n` on `tools/quality_coordination.py:580-590,653-661` confirms `conn = ce._connect()` at both `:584` and `:657`, and a repo-wide grep confirms zero `.close()` calls anywhere in that file): `tools/coordination_engine.py`'s `_connect()` is called bare-assignment from real production/CLI code (`quality_coordination.py`), not only from its own 22 test call sites as this doc originally framed it throughout (the intro, the `candidate_ledger.py` section, and the cross-module summary all described it as a test-only/test-fixture risk). Fixed all three mentions to state both call classes accurately — a genuine, if low-priority (short-lived CLI process), production leak, not only a test-migration risk. Also added a cross-reference the coordinator requested: `services/candidate_log.py:86,91` imports two more DDL strings from `capture_writer.py` the same way `series_watcher.py` imports `RAW_TRADES_DDL_SQL` — verified directly before adding, same D2-shaped migration risk, worth the plan seeing both cases together.

## Verdict

**GO.** No disagreement between self-review and adversarial review to adjudicate on the original content — both found the doc's substantive claims sound; the one fix from that pass was a small count correction. The coordinator's later finding required a real accuracy fix (production callers, not test-only) across three mentions, applied and independently re-verified before writing, plus one requested cross-reference addition. Ready to merge once the coordinator sequences it.
