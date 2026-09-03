# Self-review: `order-dependent-test-failures-2026-09-03.md`

Own-context review before adversarial review, per CLAUDE.md's "nothing
advances on one pass" HARD RULE.

## Citation spot-check

Re-verified every `file:line` citation a second time, directly against
the worktree's checkout, independent of the reads used while writing the
doc: `services/capture_writer.py:92-96` (`_STORE_PATHS`),
`services/candidate_log.py:65` (`DB_PATH`), `tests/test_trading_gate.py:
74, 82-87` (the sync'd redirect block), `tests/test_main_tick_executor_
wiring.py:29, 43` (the un-synced redirect). All match exactly, including
the surrounding lines quoted in the reproduced code blocks.

## Methodology, stated plainly for the reviewer

- The minimal reproducer (2 files) and the reversed-order control (0
  failures) were both actually run inside the live `ddev-kalshi-whale-
  poc-fastapi` container via `python3 -m pytest`, not inferred from
  reading source — pasted output is real terminal output, not
  reconstructed.
- The "predates tonight's changes" claim rests on two things: reproducing
  identically on current `main` (`b1b8b49`, confirmed by running the
  issue's exact 9-file command fresh in this investigation) and checking
  that the two specific responsible line ranges are unchanged relative to
  the issue's cited commit — not a full `git log` archaeology of when the
  pattern was first introduced, which wasn't asked for and wasn't
  attempted.
- The "does not touch a real `data/*.db`" claim is stated with full
  confidence, not hedged, because it follows from construction (both
  paths are `tempfile.mkdtemp()`-created, and `mkdtemp()` cannot return a
  path under the repo's `data/` directory) rather than from an empirical
  scan — checked that this reasoning is sound rather than treating "no
  evidence of harm" as "confirmed harmless."

## What this investigation does NOT do, stated explicitly

Does not implement either candidate fix named in the Disposition section
— sizing them for comparison was in scope, choosing/implementing one
wasn't, per the assignment's own "don't fix it in the same pass"
instruction. Does not chase whether the sibling `raw_trades`-key-dropping
risk currently causes any observable failure — flagged honestly as
unconfirmed, not investigated to a conclusion, since doing so wasn't part
of issue #525's own scope and would have been scope creep beyond what was
asked. Does not audit every other test file in the suite for the same
class of bare, unguarded module-level `DB_PATH`/`_STORE_PATHS`
assignment pattern beyond the 9 files already in the issue's own
reproduction set — a broader audit is implied as valuable by the fix
options discussed but wasn't attempted here.

## Addendum: PM decision + db.py migration coupling note (added after this self-review, before the adversarial review's result was known)

Two additions to the Disposition section: the PM's own choice between the
two fix candidates (narrow per-file sync, paired with a fail-loud CI
guard — not the systemic `runtime_isolation.py` centralization, to avoid
risking a regression that lands on every suite at once) with its
disposition classified per CLAUDE.md's investigation-to-guard rule; and a
coupling note for whoever tracks the `db.py` persistence-migration's later
tasks. The migration-coupling claim was verified before writing it, not
asserted from the PM's framing alone: confirmed directly that
`capture_writer.py` imports raw `sqlite3` (no `from services import db`,
no `db.connect()`/`register_schema()` anywhere in the file), so its write
path is genuinely independent of whatever `services/db.py`'s unified
layer does — the coupling risk described is real, not speculative.

## Verdict

GO. No correction needed. Ready for adversarial review.
