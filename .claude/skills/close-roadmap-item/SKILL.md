---
name: close-roadmap-item
description: This skill should be used when the user says a roadmap item is "done", "shipped", or "finished", asks to "update the roadmap", or "check off a roadmap item", or after completing work that resolves an item in this project's ROADMAP.md.
---

# Close a roadmap item

`ROADMAP.md` is a living to-do list — check items off in place, add a short
factual note on what actually shipped, and keep the file itself short (only
genuinely open items live there; shipped work becomes a one-line pointer at
most). This skill covers exactly that: closing out one item once real,
verified work resolves it.

**This used to also sync `static/status.html`** (the historical build
timeline page, formerly served at `/status`). That page retired 2026-08-26 —
frozen as `docs/status-archive-2026-08-26.html`, not maintained further. See
`CLAUDE.md`'s "Git history + supplementary docs" section for the full
reasoning: this project has been continuously git-tracked since 2026-08-07,
`git log`/`git blame`/`git diff` are already the authoritative "what
happened and why" for everything committed since, and hand-narrating a
second, ever-growing copy of that record stopped paying for itself once the
per-invocation cost of reading/updating it became a measured problem (see
CLAUDE.md's session-efficiency review). The frozen archive still holds the
genuinely irreplaceable pre-git narrative — nothing was lost, the ongoing
maintenance burden was just dropped.

## Steps

1. Identify which `ROADMAP.md` bullet(s) the just-finished work resolves.
   `grep -n` for a distinctive phrase from the bullet (you already know
   roughly what it says from the work just done) to get its line number,
   then `Read` with a tight `offset`/`limit` around just that bullet (plus
   enough trailing lines to capture its full multi-line body) — do not
   guess from memory, the wording matters for a clean edit, but do not open
   the whole file to find it either. `ROADMAP.md` is 1200+ lines — same
   discipline as always: grep first, read a scoped window, never the whole
   file.

2. Check the item off in place: change `- [ ]` to `- [x]`. If the original
   bullet was written as a forward-looking task description, extend it with
   a short factual note on what actually shipped (file names, the specific
   mechanism used, any bug the work surfaced along the way) — match the
   level of detail in already-checked-off items elsewhere in the file (you
   already have examples in whatever context this session has accumulated
   naturally; don't re-read the file to go hunting for more).

3. If the item has stayed open long enough to accumulate real narrative
   (measurements, false starts, numbers), that detail belongs in the commit
   message and `git log`, not back into `ROADMAP.md`'s own prose — resist
   the urge to leave a factual trail in this file itself; that's exactly how
   it grew long three times before being condensed (837 → 1300+ → 979
   lines, see `docs/roadmap-archive-*.md` for the frozen snapshots).

## Scope note

This skill only closes out `ROADMAP.md` items. It does not decide whether
something counts as "done," write the implementation, or run tests — that
judgment call and the underlying work happen first; this skill runs once
there's a concrete, verified change to record.
