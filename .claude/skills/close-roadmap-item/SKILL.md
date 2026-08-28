---
name: close-roadmap-item
description: This skill should be used when the user says a roadmap item is "done", "shipped", or "finished", asks to "update the roadmap", or "check off a roadmap item", or after completing work that resolves an item in this project's ROADMAP.md.
---

# Close a roadmap item

`ROADMAP.md` is the living to-do: check items off in place with a short factual
note. Shipped detail lives in `git log`, not here — the file grew to 837 → 1300+
lines three times before being condensed (`docs/roadmap-archive-*.md`).

1. `grep -n` a distinctive phrase from the bullet, then `Read` a tight window
   around it — never the whole file (1200+ lines).
2. `- [ ]` → `- [x]`; if the bullet was forward-looking, append what actually
   shipped (files, mechanism, any bug it surfaced), matching the detail level
   of the neighbouring checked items.
3. Measurements, false starts, and numbers go in the commit message, not back
   into the bullet.

This skill only records. Deciding what counts as "done" and doing the work
happen first.
