# Self-review, round 2: `reload-watcher-polling-research-2026-09-03.md`

Own-context review of the revision made after round 1's adversarial review
(`-adversarial-review-round1.md`, NO-GO, 8 findings). Per CLAUDE.md's
"nothing advances on one pass" rule, a revision that changes scope or
introduces a claim neither prior review saw gets the full cycle re-run from
scratch, not just a fix-list recheck — round 1's Findings 1-3 both changed
this doc's central conclusion (`--reload-dirs` inert, not merely risky) and
removed a claim (the `main.py` gotcha) neither the original self-review nor
round 1 itself had reason to expect going in. This is that from-scratch
self-review of the revised doc.

## Fix-list check against round 1's 8 findings

1. **CRITICAL (`--reload-dirs` inert)** — rewrote §2's "What `--reload-dirs`
   would actually do" subsection entirely, replacing the false conclusion
   with the traced-and-verified one (`self.reload_dirs` collapses to
   `[/app]` for any candidate under cwd). Re-verified the underlying claim
   myself independently of round 1's reviewer, instantiating the same logic
   in-container against three inputs — matched round 1's numbers exactly.
2. **HIGH (`main.py` gotcha doesn't exist)** — removed the subsection
   entirely; the corrected §2 explains why the gotcha was never real
   (`/app` is always a walk root, so `main.py` was never at risk).
3. **HIGH (recommendation reasoning invalid)** — rewrote the Recommendation
   section to stop presenting `--reload-dirs` as a riskier-but-viable
   alternative; it's now described as "not on the table," matching round
   1's instruction not to frame this as a risk-weighted choice.
4. **MEDIUM (missing `WATCHFILES_POLL_DELAY_MS` alternative)** — added as
   §4D, with its own mechanism/tradeoff distinct from §4C's, per CLAUDE.md's
   "competing solution families compared on mechanism" requirement.
5. **MEDIUM (units error, ~18,000 "directories" was actually files)** —
   fixed in both the main doc (§4C now says ~1,540-1,640 directories,
   ~18,000 files, and states the <1% max_user_watches headroom) and the
   self-review (round 1's own copy of the error, now corrected there too).
6. **LOW-MEDIUM (§3 self-contradiction on "structural")** — rewrote §3's
   closing paragraph to state plainly that the worktree share is not a
   floor (most current worktrees are provably-merged leftovers, not live
   workspaces) and to soften "structural" to describe the polling
   mechanism's behavior (scales with whatever's in the tree) rather than
   claim a fixed, unrecoverable cost.
7. **LOW (misleading `watch_filter=None` comment)** — fixed the annotation
   to point at watchfiles' own post-hoc filter application
   (`watchfiles/main.py:146`) instead of implying a filter would help if
   passed.
8. **LOW (condensed code block not flagged)** — added the same "condensed,
   logic unchanged" note §1's block already carried.

All 8 confirmed present and correct in the current file — checked by
re-reading each changed section against round 1's specific required-fix
text, not accepted on the edit's own completion claim.

## New content introduced in the fix — checked for its own errors

- The `working_dir`-change alternative (mentioned as the only way to make
  `--reload-dirs` actually work) is explicitly scoped as "out of scope for
  this research pass" and "not recommended as a same-session fix" — does
  not overclaim a solution the doc didn't evaluate.
- §3's rewrite adds three successive file-count snapshots (9,601→10,613→
  11,763 worktree files; worktree count 9→10→11) gathered across this
  session — re-verified each number traces to an actual `docker exec`
  measurement taken during this session, not invented to illustrate a
  point. The framing ("treat every absolute count as point-in-time, not
  fixed") is itself the honest response to watching the numbers visibly
  drift across three checks within about an hour.
- The `-adversarial-review-round1.md` sibling document was written to
  preserve the original reviewing agent's report close to verbatim (light
  reformatting only, no content changes) — spot-checked against the
  original tool result text, matches.

## What this revision does NOT do, stated explicitly

Still does not measure `WATCHFILES_FORCE_POLLING=false` or
`WATCHFILES_POLL_DELAY_MS`'s actual steady-state cost against the live
tree, still does not perform a `ddev restart`, still does not implement
anything — this remains research-stage only. Does not evaluate the
`working_dir`-change path beyond naming it as the only way `--reload-dirs`
could work; that's flagged as a separate, unevaluated initiative, not
something this doc claims to have ruled in or out.

## Verdict

GO. Ready for a second, independent adversarial-review pass (round 2, its
own fresh Agent dispatch with no memory of round 1's findings or this
fix).
