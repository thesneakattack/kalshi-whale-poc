---
name: config-field-edit
description: This skill should be used when adding, renaming, or restructuring a field in config/settings.yaml while the live dev server may have its own concurrent tuning applied (dashboard Controls-panel edits, applied Advisory suggestions) that must not be clobbered or silently lost in the commit. Not needed for a plain one-off value change the user explicitly asked for — only for schema-level edits that need to ship as a clean, isolated commit.
---

# Editing config/settings.yaml without losing live tuning

`config/settings.yaml` is committed to git *and* live-edited by the running
dashboard at the same time (Controls panel, applied Advisory suggestions —
see `CLAUDE.md`'s persistence idiom and safety-invariants sections). A
schema change (new field, rename, restructure) needs to land as a commit
containing only that change — not a diff polluted with whatever values the
user's live tuning currently holds, and not one that silently drops that
tuning either.

## Steps

1. `cp config/settings.yaml /tmp/settings.yaml.bak` (or the scratchpad) —
   snapshot the current live-tuned file before touching anything.
2. `git show HEAD:config/settings.yaml > config/settings.yaml` — reset the
   working file to the last committed state. This discards live tuning
   *from the working copy only* — the backup from step 1 still has it.
3. Make the schema edit via `Edit` against this clean, reset file.
4. `git add config/settings.yaml && git commit` — commits *only* the
   schema change, with no live-tuning noise in the diff.
5. Restore live tuning: `cp /tmp/settings.yaml.bak config/settings.yaml`.
6. **Re-apply the same schema edit to the just-restored file too**, at the
   same position/value. The backup predates the edit, so restoring it
   verbatim silently reintroduces the pre-edit shape — skipping this step
   is the exact bug this skill exists to prevent.
7. Sanity check: `git diff config/settings.yaml` should show *only* the
   user's live-tuned values differing from the new `HEAD` — no lines
   related to the field you just added/changed. If the new field looks
   different between this diff and what you committed in step 4, stop and
   reconcile before considering the tree clean.

## Doing this more than once in the same session

Re-snapshot the backup (step 1) fresh, immediately before each restore —
don't reuse a backup taken before an earlier round's field existed, or
that earlier field silently vanishes from the restored file the moment you
overwrite it in step 5. This has happened in practice: two sequential
rounds in one session, where the second restore used a backup taken after
round one's field existed but before round two's own field did — round
one's field survived, round two's own field silently disappeared until
caught by step 7's `git diff` check. That check is what catches this class
of mistake — never skip it, and treat any unexpected deletion it shows as
a signal to re-verify the backup's provenance, not just re-add the missing
line.
