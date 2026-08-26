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

**`static/status.html` is a generated file (2026-08-26).** It's assembled
from small fragment files under `docs/status-src/` by
`tools/build_status_page.py` — see that module's docstring for the full
fragment layout. **Never edit `static/status.html` directly** — edit the
relevant fragment(s), then regenerate. A Woodpecker/CI step
(`quality-architecture-audit.yml`) runs `--check` and fails the build if
the two drift, so a forgotten regenerate doesn't ship silently, but don't
rely on CI to catch it — regenerate as the last step below, every time.

## Steps

**Efficiency note before you start**: `ROADMAP.md` is 1200+ lines — same
discipline as always: `grep -n` for a distinctive phrase to find the exact
line(s), then a scoped `Read` with `offset`/`limit`, never a whole-file
read. `docs/status-src/` fragments are now individually small by design
(the whole reason this skill's own file-layout changed) — a scoped
`grep -n`/`Read` is still the right habit, but you're no longer fighting
a single 6000+-line file to do it.

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

3. Add a new timeline phase, as a new `<div class="t-phase">` block, to the
   **active timeline chunk** — not `static/status.html`:
   - `python -m tools.build_status_page --active-chunk --repo-root .` to
     find the active chunk's path and its current phase count in one call
     (counts real `<div class="t-phase">` markers in the file, not the
     filename, so it's correct even after a hand edit).
   - If that count is already 25 (the fixed chunk size — flagged
     `(full - start a new chunk)` in the command's own output): create a
     new empty chunk file first. Its path is
     `docs/status-src/timeline/{next_first:03d}-{next_first+24:03d}.html`
     where `next_first` is one past the highest phase number in the now-full
     chunk (e.g. after `150-174.html` fills, the next file is
     `175-199.html`). Append the new phase there instead.
   - `Read` the active (or newly-created) chunk file directly — it's a
     small fragment now, a full `Read` of just this one file is fine and
     cheap. Use its last phase block as a live example of the target
     format; if the chunk is brand new and empty, use
     `grep -n 'phase 8 · durability' docs/status-src/timeline/000-024.html`
     for the fixed template block's line number, then `Read` with
     `offset`/`limit` around just that block instead.

   Follow the established format exactly:
   - `<div class="tag">phase N · <two-or-three-word theme></div>`
   - `<h4>` — a short, specific title (not "misc fixes").
   - `<p>` — past tense, prose (not a bullet list), naming real files inline
     with `<code>` tags. Explain not just *what* changed but *why* — most
     existing phases describe a bug or gap the work surfaced, not just the
     feature itself.
   - `<div class="files">` — every touched/created file, `<code>`-wrapped,
     joined with ` · `.

   Append the new `<div class="t-phase">...</div>` block at the end of the
   chunk file (after its last existing phase, no section-wrapper tags in
   this file — those live in `docs/status-src/timeline-close.html` and are
   never touched here).

4. If the shipped work changed or added files with their own row in the
   Component Reference table, edit `docs/status-src/components.html`
   directly (not `static/status.html`): line counts (`wc -l`), status pill
   (`good` / `dormant` / `gated` / `planned`), and the note column.
   `grep -n` for the filename (e.g. `<td class="mod">main.py`) within that
   one file to jump straight to its row. Add a new row only for a
   genuinely new top-level module — not for every helper function. (The
   other reference sections — `api.html`, `config.html`,
   `limitations.html`, `stages.html`, `checklist.html` — follow the same
   pattern if the shipped work touches them; each is its own small file
   under `docs/status-src/`.)

5. **Regenerate `static/status.html`** — this is not optional, it's the
   step that makes steps 3-4 actually visible on the served page:
   `python -m tools.build_status_page --write static/status.html --repo-root .`

6. Sanity check before finishing:
   - `python -m tools.build_status_page --check static/status.html --repo-root .`
     should report "up to date" — confirms the regenerate in step 5 landed
     and nothing else drifted.
   - `grep -c 't-phase' docs/status-src/timeline/*.html | awk -F: '{s+=$2} END {print s}'`
     should have increased by exactly the number of phases added.
   - `git diff --stat` confirms scope: fragment file(s) under
     `docs/status-src/`, the regenerated `static/status.html`, and
     `ROADMAP.md` — no unrelated file touched.

## Scope note

This skill only syncs the two docs. It does not decide whether something
counts as "done," write the implementation, or run tests — that judgment
call and the underlying work happen first; this skill runs once there's a
concrete, verified change to record.
