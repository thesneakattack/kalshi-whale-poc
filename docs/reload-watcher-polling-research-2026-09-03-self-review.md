# Self-review: `reload-watcher-polling-research-2026-09-03.md`

Own-context review before adversarial review, per this repo's "nothing
advances on one pass" HARD RULE.

## Citation spot-check

Re-verified every `file:line` citation in the doc against the actual
installed source inside the running `ddev-kalshi-whale-poc-fastapi`
container (uvicorn 0.32.0, watchfiles 1.2.0) a second time, independently
of the reads used to write the doc:

- `watchfilesreload.py:55-79` (class + `__init__`), `:13-52` (`FileFilter`),
  `:72-73` (the `watch()` call), `:81-88` (`should_restart`) — all match.
- `config.py:131-164` (`resolve_reload_patterns`), `:145-147`, `:150-152`,
  `:290`, `:294-302`, `:308-315` — all match.
- `watchfiles/main.py:325` (`_default_force_polling`) and `:358` (`_auto_
  force_polling`) — match. Caught one real error here during self-review:
  the doc's summary section originally cited the function range as
  `:358-364`, which was a guess (I had only grepped the `def` line, never
  confirmed the closing line). Re-checked directly (`sed -n '355,370p'`) —
  the function actually ends at line 367, not 364. Fixed in place before
  this self-review; the body-text quote of the function elsewhere in the
  doc was already presented without a specific end-line so it wasn't
  affected.

## Measurement methodology, stated plainly for the reviewer

- The inotify-fd checks (`/proc/1/fd`, `/proc/1/fdinfo/*`,
  `/proc/1/environ`) and the `uname -r` / `mountinfo` / `docker context`
  checks are direct, unmodified reads against the live container — no
  script deleted evidence, nothing extrapolated.
- The "inotify works in 58ms" claim comes from one throwaway Python script
  (deleted after use, not committed) that called `watchfiles.watch(...,
  force_polling=False)` against a temp directory created under `/app`,
  wrote a file, and measured the time to the first yielded change. Ran
  once. This establishes *that* inotify functions and roughly how fast, not
  a statistically rigorous latency distribution — the doc's phrasing
  ("58ms", "empirically verified functional and fast") is calibrated to
  that scope and doesn't claim more.
- File-count corroboration (§3) is my own independent recount minutes after
  1d/84's measurement, not a re-run of their exact method or window — the
  doc says this explicitly and attributes the CPU deltas to them alone,
  since I did not re-derive `utime`/`stime` deltas myself.

## Internal consistency

- The recommendation (§ Recommendation) follows from, and only from,
  findings actually established earlier in the doc (§1's empirical inotify
  test, §2's `main.py` gotcha) — checked each recommendation sentence
  against the section it cites.
- The doc explicitly separates "confirmed" (polling premise, `--reload-
  dirs` mechanism, inotify functional test) from "not yet established"
  (force_polling=false's steady-state cost under real worktree churn) —
  re-read specifically to make sure no unverified claim reads as settled.
- Checked that the recommendation doesn't quietly contradict the PM's
  binary framing ("if paths, worth a restart; if filter, it buys nothing")
  — it doesn't: §2 answers "paths" plainly, and the recommendation still
  argues for spending the restart, just on a different (lower-risk) lever
  first. This is a refinement of the PM's question, not a dodge of it —
  worth the adversarial reviewer checking whether that refinement is fair
  or is quietly avoiding the question asked.

## What this doc does NOT do, stated explicitly

Does not measure `WATCHFILES_FORCE_POLLING=false`'s steady-state CPU cost
against the real ~18,000-directory tree, and does not perform a `ddev
restart` — both are explicitly deferred to the fix/plan stage, since a real
restart is a live-app interruption this research task wasn't authorized to
take. Does not implement `--reload-dirs` or move `main.py`.

## Verdict

GO, with one correction already applied (the `:358-364`→`:358-367` line
range). Ready for adversarial review.
