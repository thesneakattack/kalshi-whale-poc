# Consolidation: `reload-watcher-polling-research-2026-09-03.md`

Reconciles two full review rounds into a single GO verdict and fix-list
recheck, per CLAUDE.md's "nothing advances on one pass" HARD RULE.

## Full review history

1. `-self-review.md` — GO, no correction needed (own-context check of the
   original draft).
2. `-adversarial-review-round1.md` — **NO-GO.** Found the doc's central
   claim ("`--reload-dir` restricts the walk") was wrong in its operative
   form: `--reload-dir` is verified *inert* while the container's
   `working_dir` is `/app`, not merely riskier than an alternative. Also
   found the doc's headline `main.py`-coverage-loss "gotcha" didn't exist
   (a direct consequence of the same missed logic) and that the
   Recommendation's risk-weighted framing was therefore invalid, plus 5
   lower-severity findings: a missing `WATCHFILES_POLL_DELAY_MS`
   alternative, a directories-vs-files units error, a §3 self-contradiction
   on whether the residual cost was "structural," a misleading source-code
   annotation, and an uncondensed code block presented as verbatim.
3. `-self-review-round2.md` — GO, after applying all of round 1's fixes
   and checking each against the original fix list item by item.
4. `-adversarial-review-round2.md` — **GO WITH REQUIRED FIXES.** Explicitly
   instructed to try to break round 1's central correction rather than
   rubber-stamp it; instantiated the real `uvicorn` classes across 18 input
   shapes (symlinks, absolute/relative paths, multiple flag occurrences,
   `--app-dir`, exclude-based pruning attempts) and could not find a case
   where `--reload-dir` removes `/app` from the walk roots while cwd stays
   `/app`. **The central claim survived a genuine, deliberate attempt to
   break it.** Found 8 supporting-detail issues, one of them (F1) a real
   surprise: this doc's own round-1-derived claim that issue #513 used an
   invalid PID was itself wrong — the cited host-namespace PID and the
   container's PID 1 are the same process viewed through two different
   `/proc` mount namespaces, and the issue's original zero-inotify result
   was valid all along.
5. This consolidation, with the fix-list recheck below.

## Adjudication

No disagreement between any two review passes to adjudicate on the merits
— each round's findings were either accepted outright (matching the
pattern established in round 1) or, in F1's case, corrected a claim this
doc itself had introduced during round 1's fix, not a disagreement between
reviewers. Round 2 was explicitly asked to try to overturn round 1's
central correction and could not, which is the strongest form of
confirmation this process can produce for that claim.

## Round 2 fix-list recheck (item by item, against the current file)

1. **F1 (HIGH) — false "wrong PID" framing.** §1 rewritten: no longer
   claims issue #513's PID was invalid; states plainly that the
   host-namespace PID and container PID 1 are the same process, the
   issue's original result was valid, and this doc's own earlier framing
   (introduced during the round-1 fix) was the thing that needed
   correcting. Fixed in both §1's opening paragraphs and the Summary's
   first bullet.
2. **F2 (MEDIUM-HIGH) — §4C/§4D falsely presented as combinable.** §4D
   rewritten to state they're mutually exclusive (`WATCHFILES_POLL_
   DELAY_MS` has zero effect while `force_polling` is off, confirmed
   live by the reviewer). Every "and/or" replaced with "first... fallback
   if that doesn't pan out" framing, in §4D, the Recommendation (two
   places), and the Summary's fourth bullet.
3. **F3 (MEDIUM) — falsified "not a growing one."** §4A rewritten: states
   the cost scales with tree size, cites the reviewer's own fresh
   measurement (29.3% against a 24,443-file tree, up from 20.8% against
   17,877), and reframes "do nothing" as "accept whatever the tree
   happens to be, which drifts upward between cleanup runs" rather than a
   stable state.
4. **F4 (MEDIUM) — stale absolute directory/file counts.** §4C and §2 both
   reworded to state the headroom as a ratio ("every count taken... stayed
   under 0.4% of `max_user_watches`") rather than a specific range that
   was already out of date by the time round 2 ran.
5. **F5 (MEDIUM) — wrong CLI flag name.** Every `--reload-dirs` (25
   occurrences) replaced with the correct `--reload-dir` throughout the
   file; confirmed via `grep -c -- "--reload-dirs"` returning 0 after the
   fix. The Python attribute name (`reload_dirs`, `config.reload_dirs`,
   `self.reload_dirs`) was correct all along and left unchanged.
6. **F6 (MEDIUM) — nonexistent `environment:` block asserted.** Recommendation
   rewritten to state the compose file has no `environment:` block today
   (citing the file's own comment confirming this) and that one needs to
   be added, rather than implying it already exists to be edited.
7. **F7 (LOW-MEDIUM) — lifetime-average-vs-instantaneous-window compared
   without caveat.** §4A rewritten to state the 42.9%→20.8% comparison
   mixes a lifetime average with an instantaneous reading and that "~52%"
   is an estimate of unknown bias, not a measured share — folded into the
   same edit as F3 since both live in §4A.
8. **F8 (LOW) — imprecise `working_dir` follow-up description.** §2's
   closing paragraph rewritten: clarifies the binding constraint is the
   supervisor process's cwd (not specifically the compose key), notes
   `--app-dir` would additionally be needed if cwd changed via `command:`,
   and warns that the replacement cwd is itself unconditionally added to
   the walk roots too (so a careless choice like `/` would make things
   worse, not better).

All eight confirmed present and correct in the current file, not accepted
on the edit's own completion claim — each was re-grepped or re-read after
editing to confirm the fix landed where intended and no stale phrasing
survived elsewhere in the document (`grep` sweep for "and/or", "complement
or fallback", "not a growing one", "the correct PID", and the stale
directory-count range all returned zero matches outside of the intentional
in-doc references to the corrections themselves).

## GO / no-go

**GO.** Two independent, fresh adversarial-review passes have now examined
this doc's central claim — that `--reload-dir` cannot narrow the uvicorn
reload watcher's walk while the container's `working_dir` is `/app`, and
that the recommended fix is `WATCHFILES_FORCE_POLLING=false` rather than
`--reload-dir` scoping — and neither could overturn it; round 2 was
explicitly tasked with trying. Every supporting-detail issue either round
found has been corrected in the current file. This is ready for the
fix/plan stage to consume: the concrete next action is adding a new
`environment:` block to `.ddev/docker-compose.fastapi.yaml`'s `fastapi`
service with `WATCHFILES_FORCE_POLLING=false`, a `ddev restart`, and the
three-point verification the Recommendation section already specifies.
