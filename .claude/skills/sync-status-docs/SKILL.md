---
name: sync-status-docs
description: This skill should be used when the user says a roadmap item is "done", "shipped", or "finished", asks to "update the roadmap", "sync status docs", "check off a roadmap item", or after completing work that resolves an item in this project's ROADMAP.md. Keeps ROADMAP.md and static/status.html in sync, the way every prior shipped item in this repo (which has no git history) has been recorded.
---

# Sync status docs (ROADMAP.md + static/status.html)

This project is not a git repository, so there is no commit history to
reconstruct "what shipped and why" from later. `ROADMAP.md` (forward-looking
checklist) and `static/status.html` (backward-looking build timeline, served
at `/status`) are the substitute — see `CLAUDE.md` for the full rationale.
Both must be updated together when a roadmap item ships, or `status.html`
quietly goes stale (as it already had once before this skill existed).

## Steps

1. Identify which `ROADMAP.md` bullet(s) the just-finished work resolves.
   Read `ROADMAP.md` and locate the exact `- [ ]` line(s) — do not guess from
   memory, the wording matters for a clean edit.

2. Check the item off in place: change `- [ ]` to `- [x]`. If the original
   bullet was written as a forward-looking task description, extend it with
   a short factual note on what actually shipped (file names, the specific
   mechanism used, any bug the work surfaced along the way) — match the
   level of detail in already-checked-off items elsewhere in the file, not
   just a bare checkbox flip.

3. Add a new timeline phase to `static/status.html`, inside `#timeline`,
   as a new `<div class="t-phase">` block appended right before that
   section's closing tags (after the last existing phase). Follow the
   established format exactly:
   - `<div class="tag">phase N · <two-or-three-word theme></div>` — N is the
     next integer after the current highest phase number.
   - `<h4>` — a short, specific title (not "misc fixes").
   - `<p>` — past tense, prose (not a bullet list), naming real files inline
     with `<code>` tags. Explain not just *what* changed but *why* — most
     existing phases describe a bug or gap the work surfaced, not just the
     feature itself.
   - `<div class="files">` — every touched/created file, `<code>`-wrapped,
     joined with ` · `.

   Use the "phase 8 · durability" block already in the file (the paper
   broker/risk manager persistence work) as the concrete template for tone,
   length, and structure — copy its shape, not its content.

4. If the shipped work changed or added files with their own row in the
   `#components` Component Reference table, update those rows: line counts
   (`wc -l`), status pill (`good` / `dormant` / `gated` / `planned`), and the
   note column. Add a new row only for a genuinely new top-level module —
   not for every helper function.

5. Sanity check before finishing: the `<div class="t-phase">` count in
   `static/status.html` should have increased by exactly the number of
   phases added (grep for it), and no unrelated section should have been
   touched.

## Scope note

This skill only syncs the two docs. It does not decide whether something
counts as "done," write the implementation, or run tests — that judgment
call and the underlying work happen first; this skill runs once there's a
concrete, verified change to record.
