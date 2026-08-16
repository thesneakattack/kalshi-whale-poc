---
name: sync-status-docs
description: This skill should be used when the user says a roadmap item is "done", "shipped", or "finished", asks to "update the roadmap", "sync status docs", "check off a roadmap item", or after completing work that resolves an item in this project's ROADMAP.md. Keeps ROADMAP.md and static/status.html in sync — this project's pre-git-history work has no commit-by-commit record, and even post-git shipped items are recorded here as the living human-readable layer, the way every prior shipped item in this repo has been.
---

# Sync status docs (ROADMAP.md + static/status.html)

This became a git repository partway through the project's life (see the
first commit's message for the cutover point) — everything built before
that has no commit-by-commit history, which is why `ROADMAP.md`
(forward-looking checklist) and `static/status.html` (backward-looking
build timeline, served at `/status`) exist and remain the primary source
for the *why* behind that pre-git work — see `CLAUDE.md` for the full
rationale. For anything committed going forward, `git log`/`git blame`/`git
diff` are the real history; these two docs stay the living, human-readable
layer on top regardless, and must still be updated together whenever a
roadmap item ships — that's a direct standing instruction, not a
workaround for missing history — or `status.html` quietly goes stale (as
it already had once before this skill existed).

## Steps

**Efficiency note before you start**: both target files are large
(`ROADMAP.md` is 1200+ lines; `static/status.html` is 4400+ lines, 110+
phase blocks, and its `#components` table has individual cells running
thousands of tokens each — a `git blame`-verified real cost, not a
guess: this skill was measured at ~21% of a week's total usage, almost
certainly from full-file reads of these two). Never `Read` either file
whole. Every step below is doable with `grep -n` to find the exact line(s)
you need, then a scoped `Read` with `offset`/`limit` around just that
region — this produces the identical edit quality (you still see a real
template phase and the real target bullet in full) at a fraction of the
context cost.

1. Identify which `ROADMAP.md` bullet(s) the just-finished work resolves.
   `grep -n` for a distinctive phrase from the bullet (you already know
   roughly what it says from the work just done) to get its line number,
   then `Read` with a tight `offset`/`limit` around just that bullet (plus
   enough trailing lines to capture its full multi-line body) — do not
   guess from memory, the wording matters for a clean edit, but do not open
   the whole file to find it either.

2. Check the item off in place: change `- [ ]` to `- [x]`. If the original
   bullet was written as a forward-looking task description, extend it with
   a short factual note on what actually shipped (file names, the specific
   mechanism used, any bug the work surfaced along the way) — match the
   level of detail in already-checked-off items elsewhere in the file (you
   already have examples in whatever context this session has accumulated
   naturally; don't re-read the file to go hunting for more).

3. Add a new timeline phase to `static/status.html`, inside `#timeline`,
   as a new `<div class="t-phase">` block appended right before that
   section's closing tags (after the last existing phase). Two scoped reads
   are enough — never the whole file:
   - `grep -n 'class="t-phase"' static/status.html | tail -1` to find where
     the last phase starts, then `Read` with `offset`/`limit` around just
     that one block, both for the current highest phase number (N; yours is
     N+1) and as a second, freshly-shipped example of the target format.
   - `grep -n 'phase 8 · durability'` for the fixed template block's line
     number, then `Read` with `offset`/`limit` around just that block — the
     concrete template for tone, length, and structure (copy its shape, not
     its content).

   Follow the established format exactly:
   - `<div class="tag">phase N · <two-or-three-word theme></div>`
   - `<h4>` — a short, specific title (not "misc fixes").
   - `<p>` — past tense, prose (not a bullet list), naming real files inline
     with `<code>` tags. Explain not just *what* changed but *why* — most
     existing phases describe a bug or gap the work surfaced, not just the
     feature itself.
   - `<div class="files">` — every touched/created file, `<code>`-wrapped,
     joined with ` · `.

4. If the shipped work changed or added files with their own row in the
   `#components` Component Reference table, update those rows: line counts
   (`wc -l`), status pill (`good` / `dormant` / `gated` / `planned`), and the
   note column. `grep -n` for the filename (e.g. `<td class="mod">main.py`)
   to jump straight to its row rather than scanning the table by eye. Add a
   new row only for a genuinely new top-level module — not for every helper
   function.

5. Sanity check before finishing: `grep -c 't-phase' static/status.html`
   should have increased by exactly the number of phases added versus the
   count you captured in step 3 — a cheap count, not a full-file re-read —
   and no unrelated section should have been touched (`git diff --stat`
   confirms scope without opening the files again).

## Scope note

This skill only syncs the two docs. It does not decide whether something
counts as "done," write the implementation, or run tests — that judgment
call and the underlying work happen first; this skill runs once there's a
concrete, verified change to record.
