# Self-review — cleanup-worktrees silent-deploy design note

Reviews `2026-09-03-cleanup-worktrees-silent-deploy.md`. Same author, same
context; checks internal consistency, unverified claims, and unaddressed scope
before independent effort is spent on it. Stage 2 of 4.

## Method

Every load-bearing claim in the note was re-checked against its source, and two
were re-probed rather than re-read. Findings are listed whether or not they
change the recommendation.

## Findings

**SR-1 — `git fetch origin main:main` claim was asserted, now verified.**
§5 claimed this refuses on a checked-out branch. It had not been run when
written. Probed: `fatal: refusing to fetch into branch 'refs/heads/main'
checked out at '<primary>'`. Claim stands; it is no longer an assumption.
*No change needed.*

**SR-2 — §7 item 5's second half is a no-op, and the note should say so
rather than defer it.** The note said to check whether
`.claude/skills/checkpoint/SKILL.md` step 9 implies the script keeps `main`
current. Checked all three places the script is described:

- `.claude/skills/checkpoint/SKILL.md:83` — "removes provably merged worktrees
  + branches; reports everything else"
- `.claude/hooks/orient.sh:40` — "reports the stale ones; /checkpoint removes
  the provably merged ones after a merge"
- `.claude/rules/branching-and-ci.md:86` — "does both for provably merged
  worktrees" (i.e. local + remote branch deletion)

None mentions local `main`. **This strengthens the case materially and belongs
in the note's argument, not its to-do list:** the fast-forward was never
documented anywhere a session would read, so no session could have been
depending on it deliberately. The only *knowing* consumer was the script's own
`branch -d`. *Fix: fold this into §6 as support for option A and delete the
speculative half of §7 item 5.*

**SR-3 — the note does not say what happens on the very first run after the
change, and it is the one moment the notice matters most.** Today's primary is
at `cb5d26b`, equal to `origin/main`, so the notice is silent. The first time
it fires will be after some future merge. A reader of the note cannot tell
whether the notice is expected to appear immediately. *Fix: state the current
state (0 behind → silent) explicitly, so nobody reads a silent first run as the
notice being broken.*

**SR-4 — "3 deploys in nine hours" is precise but its denominator is not.**
§2's table covers only the last 30 reflog entries, so "three times today" is a
floor, not a count of the day. Two of the three (12:32:14Z, 16:40:05Z) are new
information not in #535, which reported one. *Fix: label it a floor. Do not
inflate it into a rate.*

**SR-5 — §3's live-shape evidence is weaker than it reads.** The four branches
with no `refs/remotes/origin/<branch>` were checked, and none currently has a
PR, so none would reach `branch -d` on today's run. The claim "this shape is
live" is still true — the refs really are absent, and open-decisions #35 records
the abort happening for real — but as written it implies today's run would hit
it, which is not established. *Fix: state both halves. The 2026-08-31 incident,
not today's branch list, is the load-bearing evidence.*

**SR-6 — the `-D` change deserves an explicit statement of what safety is
given up.** §7 item 3 justifies it by the `is_ancestor` precondition, which is
correct, but does not say plainly that `git branch -d`'s independent second
merge-check is being traded away — the same second guard
`tools/quality_coordination.py:474`'s `delete_merged_branch()` deliberately
keeps ("`git branch -d` (never -D) as an independent second guard"). Two
callers in this repo will now disagree about that tradeoff, on purpose, for
different reasons: AQC has no `origin/main` ancestry proof of its own, this
script does. *Fix: name the tradeoff and the divergence in the note and in the
script's comment, so the next reader does not "fix" the inconsistency.*

**SR-7 — no dimensional-analysis pass is called for, and the note should say
why rather than stay silent.** CLAUDE.md's HARD RULE covers any arithmetic. The
change introduces exactly one quantity: `git rev-list --count
main..refs/remotes/origin/main`, dimension *commits*, displayed as a count of
commits with no conversion, scaling, or unit change. *Fix: record that
one-line disposition explicitly; an unstated skip is indistinguishable from an
overlooked one.*

**SR-8 — scope check: nothing in the change touches trading, risk, sizing,
calibration, settlement, auth, or a safety gate.** It removes a deploy trigger
and makes a branch deletion succeed where it previously aborted. No kill switch,
no `data/*.db`, no `trading_enabled`. *No change needed; recorded so the
adversarial pass can challenge it.*

## Unaddressed scope found

**SR-9 — the note never asks whether `--dry-run` currently deploys.** Reading
`scripts/cleanup-worktrees.sh:49-51` and `:164-167`: `DRY_RUN` is parsed at
`:49` but the fast-forward at `:166` is **not** guarded by it. So
`scripts/cleanup-worktrees.sh --dry-run` — documented at `:38` as "report only,
never mutates anything" — currently deploys the live app. That is a second,
sharper instance of the same bug, contradicting the script's own usage text,
and it is not in #535. It is fixed automatically by option A (the merge is gone
in both modes), but it must be stated, tested, and named in the PR body rather
than fixed by accident. *Fix: add to the note as §3a, and add a dry-run
no-deploy assertion to the test list.*

## Verdict

No finding overturns the recommendation. Option A plus the notice plus `-D`
stands, and SR-2 and SR-9 strengthen it. Nine fixes to the note, all
clarifications or additions; SR-9 is the one that adds testable behavior.
