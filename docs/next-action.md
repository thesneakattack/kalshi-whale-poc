# Next action

## ▶ PAUSE ENDED 2026-09-07T10:05Z — fleet resuming, verify per-peer before assuming active

3-hour URGENT pause (David, issued 07:05:27Z UTC) ended at the written
target. **`49` independently verified real time (`date -u`, 10:12Z) and
resumed first** — its assigned first-incomplete unit, an independent
read-only sweep of PR #660 (Lane 9), confirmed correct by the
coordinator against live PR state before green-lighting. `c4`, `0d`,
`ea` had not yet messaged back as of this writeup — **don't assume any
of them are still paused OR already resumed; check `ListAgents` plus a
direct message before acting on an assumption either way**, same
discipline as any other unverified claim. Each was told to verify real
time against this target itself before resuming, not trust its own
wakeup mechanism — if one hasn't surfaced in a while, a check-in is
reasonable, but redundant status-pings aren't (memory
`verify-status-before-reasking-peer-after-compaction` applies here too,
substituting "post-pause" for "post-compaction").

Coordinator's own wake mechanism: one-shot `CronCreate` job `73bde554`
at local `05:07` (`7 5 7 9 *`), prompted to re-verify time and re-read
this file fresh before resuming — not chained `ScheduleWakeup` calls,
specifically to avoid the known chaining-fragility failure mode. Session-
only (lost if this session exits before firing) — the written target
above is the real fallback, not the cron job itself.

**Peer checkpoint status, all confirmed no-earlier-than-10:05Z:**
- `49` — checkpointed, nothing to commit (all work already merged/
  persisted). Last unit: verified/approved Lane 1 (#657) merge. First
  incomplete: Lane 9 review — noted PR #660 exists but deliberately
  did not start reviewing it before pausing.
- `c4` — checkpointed, nothing to commit (all work already posted as
  GitHub comments/issue edits). Last unit: Lane 9 file-list + citation-
  debt prep. Nothing queued.
- `0d` — checkpointed, HEAD `6337a87` on `docs/lane-9-file-move`,
  pushed, CI green (12/12). Last unit: Lane 9 file moves (81 files, 3
  commits) + citation fixes (223 inbound + 25 outbound + 53 issues),
  PR #660 open with self-review posted. First incomplete: a dispatched
  adversarial-review subagent was already running read-only in the
  background when the pause arrived — left running (no risk, no
  action taken on its result), consolidation/merge not started. Not
  self-scheduling a wakeup (not a `/loop` session) — waiting to be
  resumed directly.
- `ea` — checkpointed, nothing to commit (working tree clean besides
  the standing safety override). Last unit: PR #659 merge/closeout
  (already covered by coordinator's `51c6afd`). Nothing queued.
  Scheduled its own session-local cron backstop toward ~10:05Z.

**All four peers confirmed checkpointed and paused as of 07:07Z** — no
gaps, nothing left mid-task.

**Coordinator's own checkpoint:** on `main`, `51c6afd`, fully pushed
(`git log origin/main..HEAD` empty), CI `success`. Nothing uncommitted
besides the standing `config/settings.yaml` safety override (correct,
must stay uncommitted). Last completed unit: PR #659 (CLAUDE.md scope
clarification) merged and fully closed out, 7 of 8 lanes merged. First
incomplete unit (not started, per the pause): independent review of
Lane 9 / PR #660 — explicitly not begun, matching the fleet-wide hold.

---

**Coordinator:** `autotrade-36`, one continuous session since `1f`.
Fleet active (`49`, `c4`, `0d`, `ea`). Verify identity by direct reply
before trusting a name, in either direction — this isn't hypothetical
caution, see the Planning Lanes section's note on `ea`'s original
classification task.

---

## Safety (check every session start — grep the values, don't trust `git status`)

`strategy.auto_exit_enabled: false` and `risk.max_daily_loss_pct: 0` in
`config/settings.yaml`, both **uncommitted** (David's own edits) — must
stay uncommitted and unchanged. `kalshi_account.trading_enabled` stays
`false`. Kill switch TRIPPED by design; David confirmed that's fine.

**Real incident, 2026-09-06:** both lines were found silently reverted
to their unsafe defaults with **zero git diff** — a bare `git checkout
<branch>` on the primary (for read-only inspection, not a commit) can
drop them if unstashed. Fixed, verified live, impact confirmed nil.
Memory: `bare-checkout-can-drop-uncommitted-safety-config`. **Standing
check:** `grep -n 'auto_exit_enabled\|max_daily_loss_pct'
config/settings.yaml` after any checkout on the primary — a clean `git
status` is not proof these survived. Re-confirmed correct through every
merge tonight, most recently at `c1583a5`.

---

## `#532`/`candlestick-volatility`/VACUUM — all CLOSED

`#532`: 31.4M-row purge done, independently verified. `#642` sub-decision:
`candlestick-volatility` (#641) — kept as reference, re-implement when
prioritized. VACUUM done (6.4GB→146MB) on David's go-ahead.

---

## `#605` — NOT closing yet; all 4 found contributors fixed+live, magnitude gap still open

Four real contributors found tonight, **all fixed and confirmed deployed
live** (`c4` independently confirmed #634/PR #647's live deploy — commit
`780a44b` an ancestor of synced HEAD, plus a real `/api/state`
ETag/Content-Length check, not inferred): diagnostic itself (#632),
`resolve_window()` sync-on-loop (#637), `gate_summary()` unbounded scan
(#636), `GET /api/state` JSON-encoding-on-the-loop (#647, closes #634).

**Do not close on that basis.** The issue's own most recent comment
(2026-09-07T00:51, already posted, don't duplicate it) already reasoned
this through carefully: #634's fix measures ~164ms worst case, an order
of magnitude short of the original ~9.3-9.6s stall that opened this
investigation. A prior comment speculatively pinned the residual gap on
#150's "leaked ThreadPoolExecutor worker" — checked directly against
#150's own closing text, which explicitly says **"that link is a lead,
not a mechanism"** and falsifies the permanent-leak framing. So: real
contributors fixed, magnitude still not fully explained, issue correctly
stays open on that unconfirmed basis. Nothing to do here right now — no
active investigation thread, just don't close it on deploy-confirmation
alone if that instinct comes up again.

Two unassigned follow-ons filed, not blocking, not yet picked up:
**`#639`** (same unbounded-`gate_summary()`-on-loop defect class, found
during #636's own fix, in `services/advisory/routes.py` (2 routes) +
`market_analyst_orchestrator.py`, deliberately left out of #636's scope)
and **`#648`** (audit remaining `_build_state_body()` fields for the
same thread-safety hazard #634/PR #647 fixed, found during that PR's own
review).

---

## `#642` — CLOSED (report posted, issue correctly left open on its own merits)

Report posted (00:20:26Z) after full self-review → independent
adversarial review → consolidation. All three tested mechanisms
weakened: SQLite lock contention (corrected reasoning: WAL mode, not the
backup-API's between-steps clause, is why writes probably weren't
blocked), GIL contention (no real effect), disk I/O contention (a real
but small ~50%-mean effect, ~12x short of the observed magnitude).
`last_tick_duration_sec`'s own "baseline" phase turned out to be a
cold-start outlier once compared against temporally-adjacent phases
instead. **Most important finding**: this app already has a
well-documented, frequent (4-21/hour, every hour), unattributed
≥10s-stall pattern (`docs/superpowers/research/2026-09-02-architecture-
audit-second-pass.md`) independent of backups — the original 90.61s
observation may simply be one more instance of that pattern, not
something the backup specifically caused. `#642` stays open on this
reframing, not closed; `tick_phase_timings` (`GET /api/health/pipeline`)
is the named next tool if a comparable stall recurs. Nothing further to
do here.

---

## Planning lanes — 7 of 8 batches MERGED (4, 8, 5, 6, 2, 3, 1); only Lane 9 left

167 of 246 files moved (67.9%); Lane 9 (79 files, 32.1%) is the only
one remaining. **`0d` assigned to execute it**, using `49`'s and `c4`'s
already-completed prep (below) as a running start.

**On `main`:** design (PR #640), all 3 step-1 classification tables (PRs
#643/#644 + a direct commit), step 3 `kanban_sync` retooling (PR #645),
`LANES`/`CONCERNS` infrastructure + full step-2 labeling (PR #646).
Batch order: **4 → 8 → 5 → 6 → 3 → 2 → 1 → 9**. Lanes 4 (#651), 8
(#652), 5 (#653), 6 (#655), 2 (#656), 3 (#658, MERGED
2026-09-07T05:50:58Z, confirmed clean by `ea`'s independent sweep before
merge), and now **1 (#657, `0d`) — MERGED 2026-09-07T06:18:21Z**
(`96361be`, independently verified via `gh pr view 657
--json state,mergedAt` before accepting). Lane 1 went through the most
thorough review cycle of the night: self-review, a fix-list recheck, an
independent file-citation sweep (`49`), an independent issue-citation
sweep (`c4`, 27 issues fixed), a dispatched adversarial-review agent
(found 5 more real defects including the new mutual-deferral hazard,
methodology point 11), and a final consolidation — 7 distinct PR
comments total, all independently verified before the merge went ahead.
**Only Lane 9 remains.**

**Lane 1 update, 2026-09-07 (`ea`, independently verified against the
pushed branch content, not taken on `0d`'s word alone):** `0d` fixed and
pushed at `c08ef9f`. Re-checked all 5 previously-flagged files directly
— the stale `docs/superpowers/...` citations are gone from all of them
and the corrected `docs/archive/lane-{6,8,4}-.../...` paths are present
(1/1/2/2/1 occurrences respectively, matching the original finding
exactly). `0d` also reported a rewritten non-deduping sweep
(`lane1_sweep_v2.py`) caught 13 further sites the original sweep
silently dropped (9 outside Lane 1, 4 Lane-1-internal self-citations),
corroborated by a third independent regex scan finding zero remaining
stale citations outside the 14 already-known DEFER cases. Confirmed via
`gh pr view 657 --json comments`: all 3 comments genuinely posted
(self-review, arithmetic correction, and the full writeup at
`issuecomment-5565722451`, body length 5751 chars — not a stub).
**CI was still `pending` at the time of this check** (not yet green);
`0d` said it will confirm green before merging — don't treat this as
merged until `gh pr view 657 --json state,mergedAt` says so.

**Confirmed real gap, 2026-09-07 ~05:56Z (coordinator `36`, independently
double-checked by `ea` separately, identical result — not a labeling
ambiguity):** `0d`'s fix-list-recheck comment (`issuecomment-5565722451`)
references "the PR's existing adversarial review" as the source of the
occurrence-dedup finding, but **no adversarial-review comment has ever
been posted on #657** — `gh pr view 657 --json reviews` returns 0 review
objects, and the PR body's own checklist still has `- [ ] Adversarial
review (independent, memory-less) — pending.` unchecked (also `- [ ] CI
... pending push.`). This is the exact `persist-code-pr-reviews-as-
comments` compliance-drift shape (Lane 4/8, #637) — a review may have
genuinely run (e.g. a dispatched subagent) but was never posted as its
own artifact, only referenced later. Flagged to `0d` (post it now,
labeled honestly, or say plainly it's unrecoverable); `49` and `c4`
redirected to sweep against the new head (`c08ef9f`, not the stale
`9f4e1ba`) and post their findings **directly as PR comments** on #657
so the missing artifact gets filled by a genuinely independent check
either way. A consolidation comment (explicit GO/no-go) still has to
land before merge regardless of how the adversarial-review gap resolves.

**`49`'s independent sweep posted directly to #657** (`issuecomment-
5565762808`, 2026-09-07T06:00:03Z — genuinely fills part of the missing
adversarial-review artifact): occurrence-vs-presence bug **confirmed
NOT present** in this PR's own fix (3 count>1 hits exist but are all
inside not-yet-moved Lane 9 files this PR never touches — legitimate
deferred forward-refs, not a dedup miss). Wrap-detection blind spot
**confirmed present, 3 real new misses** — see methodology point 10
above for the mechanism and exact locations (Lane 4's merged archive +
`services/market_watch/CHEATSHEET.md` + `ROADMAP.md`, none previously
known). Separately: this branch predates Lane 2/3's merges and hasn't
been rebased — one more hit the sweep flagged is a staleness artifact
of that (not a real defect, resolves on rebase), but the PR's own
"swept everything" claim was necessarily computed against a stale
`main`, so **rebase before the final re-sweep and merge**. `0d` still
owns applying fixes; waiting on `c4`'s issue-citation check and then a
consolidation comment before this is mergeable.

**`c4`'s independent issue-citation sweep posted directly to #657**
(`issuecomment-5565810132`, 2026-09-07T06:05:44Z — coordinator
spot-checked 5 of the 27 fixed issues directly via `gh issue view`,
confirmed corrected paths present, no stale paths in any): found **27
closed issues, 31 occurrences, genuinely stale** — 100% missed by `0d`'s
self-review, because that self-review's own text says "ran the broader
all-**147**-open-issues sweep," explicitly covering only open issues
despite methodology point 2 above already requiring all **335** (147
open + 188 closed). Not a new blind-spot mechanism — a concrete instance
of an already-documented methodology point not being applied literally.
Root cause: all 27 are `kanban_sync`-generated Task/Plan-tracker issues
predating `sources_plan.py`'s own 2026-09-06 fix, and `sync_pass_one`
never re-renders an existing issue's body — these would have stayed
broken **permanently**, not self-healed. Fixed directly via `gh issue
edit --body-file` (same mechanical/trivial precedent as #54/#621/#613 —
no review cycle required per CLAUDE.md's Scope carve-out), re-verified
after the fix: 0 remaining stale hits across all 335. **CI is now green
on all 12 required contexts** (`c08ef9f`) as of this check.

**PR #657 status: still NOT GO.** 5 comments now exist (self-review,
arithmetic correction, fix-list-recheck, `49`'s file-citation sweep,
`c4`'s issue-citation sweep). Issue-citation side clean. Still
outstanding before merge: `0d` fixes the 3 file misses + rebases onto
current `main` + re-sweeps, then a **consolidation comment** (explicit
GO/no-go reconciling self-review + both independent sweeps + the fixes)
— nobody has written one yet.

**Standing methodology, earned the hard way tonight — apply to every
remaining lane (3, 2, 1, 9) without re-deriving:**
1. **Full-repo de-wrapping citation sweep**, not slug-substring or
   exact-string `git grep` — a citation wrapped across a line break in a
   comment/docstring is invisible to a same-line match. Found 14 files
   Lane 4/8's original sweeps missed (fixed via PR #654).
2. **GitHub issue citation check spans ALL tracked issues, open AND
   closed** (335 total, not just the 147 open ones), not just
   `type:plan-task`/`Plan:`-titled ones — a general `type:feature` issue
   (#54) had a real broken citation the narrower search missed, and
   Lane 3's sweep found 9 more hits among closed issues (2 closed
   `Plan:` trackers plus one not even `type:plan-task`-labeled) that an
   open-issues-only search would never see. Use the full-path-vs-
   bare-filename filter (a bare filename citing its own parent plan by
   name is correct and unaffected by a move; only a full
   `docs/superpowers/<dir>/<filename>` path is genuinely stale) or a
   naive full-text search buries real hits in false-positive noise.
3. **A fix "found" in the working tree isn't real until it's in the
   commit** — Lane 6's own self-review measured "zero unexpected hits"
   against the working tree while 3 `git mv`-staged citation fixes were
   never `git add`ed; the actual commit still had 10 stale citations.
   Verify the committed diff, not the working tree, before trusting a
   completion claim.
4. **3 distinct, separately-posted PR comments (self-review, adversarial
   review, consolidation) are mandatory and must be checked directly**
   (`gh pr view <n> --json comments`) before `gh pr merge` — never
   inferred from the PR body's own narrative. Two batches (Lane 4/8)
   merged without this the first time; fully remediated retroactively.
   Memory: `persist-code-pr-reviews-as-comments` (4 occurrences now).
5. **When checking whether an in-flight PR already covers a finding,
   check its actual diff or wait for the merge** — don't grep a local
   checkout that hasn't pulled it yet and call the result "separate."
   Memory: `check-against-current-state-not-stale-local-during-concurrent-merge`
   (a coordinator error tonight, corrected before anyone acted on it).
6. **Exact-filename matching only, never same-date-prefix or
   similar-topic matching**, when sweeping for stale citations — several
   false positives tonight were a different, not-yet-moved file that
   merely shared a date or subject with a real moved one.
7. **A `git add` with a mixed list of renamed-and-modified paths can
   error on one path and silently abort before staging the rest** — Lane
   2 (PR #656) hit this: the commit "succeeded" but `--stat` showed only
   the renames, 0 insertions, meaning every citation-fix edit had been
   dropped. Stage renames and modified-content files in separate `git
   add` calls, and check `git status`/`--stat` immediately after every
   commit — don't trust a non-erroring `git commit` actually staged
   everything intended.
8. **A citation sweep must count occurrences per file, not just presence
   per file** — Lane 2's sweep recorded "did this file match at all,"
   so a file citing the same moved doc **twice** (two separate
   docstrings, same filename) only got its first occurrence fixed; the
   second sat stale, undetected by its own adversarial review's first
   pass. Caught only by a dedicated occurrence-counting re-sweep after
   the fact. Every sweep from Lane 9 onward counts occurrences
   (`grep -c`-style, not existence), and re-sweeps with occurrence
   counting specifically after any fix-list round, not just presence.
9. **Check whether the lane's OWN newly-moved files cite OTHER
   already-merged lanes by old path** — distinct from the documented
   self-heal-later exception (which only covers citing a *not-yet-moved*
   lane). Found 3 times now (Lane 5/PR #655, Lane 2/PR #656, Lane
   1/PR #657 — 5 files/7 occurrences citing Lanes 8/4/6) — a moved
   file's own content can reference a sibling lane that already landed,
   and that citation is fixable right now, not deferrable. Sweep for
   this explicitly on every remaining lane, don't rely on the
   already-moved lanes having self-corrected it themselves.
10. **Mirror image of point 9: check whether OTHER already-merged lanes'
    archives cite a file in THIS lane, about to move** — found by `49`'s
    independent sweep of #657 (2026-09-07 ~06:00Z): Lane 4's already-
    merged archive (`docs/archive/lane-4-.../specs/2026-08-26-economic-
    strategy-effectiveness-investigation-design.md:121-122`) has a
    wrapped citation to a Lane 1 file, correct when Lane 4 merged (Lane 1
    hadn't moved yet) but stale the moment Lane 1 moves it — and Lane 4
    won't be revisited by anything once merged. Unlike point 9 (this
    lane's own outgoing refs, fixable in this lane's own commit), THIS
    direction requires checking every already-merged lane's archive for
    forward references into the lane currently moving, since nothing
    else will ever re-sweep an already-merged lane. Also found in the
    same sweep: 2 live, actively-read docs never touched by any lane's
    diff so far — `services/market_watch/CHEATSHEET.md:260-261` and
    `ROADMAP.md:114-116` — both still citing a Lane 1 file by old path.
    Apply this check on Lane 9 (the last lane) against ALL 7 already-
    merged lanes, not just the ones it happens to cite.
11. **Mutual-deferral race: two lanes open/executing close together can
    each correctly defer to the other, and then neither ever gets
    fixed** — found by `0d`'s dispatched adversarial-review agent on
    #657 (2026-09-07 ~06:10Z, verdict NO-GO as submitted). Lane 3
    (#658) merged into `main` *after* Lane 1's original citation sweep
    but *before* Lane 1 itself merged. At Lane 3's sweep time, Lane 1
    hadn't moved yet, so Lane 3 correctly deferred its Lane-1 citations;
    at Lane 1's sweep time, Lane 3 hadn't moved yet either, so Lane 1
    correctly deferred its Lane-3 citations. Both deferrals were correct
    when made — but both lanes now consider it "the other lane's job,"
    and neither's original sweep will ever re-run, so both citations go
    permanently stale unless something explicitly re-checks. Only
    detectable by simulating the actual merge (`git checkout -b sim
    origin/main && git merge <pr-branch>`) and re-sweeping the merged
    tree, not either branch alone. **Standing fix: any PR whose citation
    sweep predates another lane's later merge must re-sweep a simulated
    merge with current `main` before its own merge**, not just rebase
    and trust the old sweep. Distinct from point 10 (which is about an
    already-*merged* lane's stale archive) — this is two lanes *racing*,
    both still in flight relative to each other at sweep time.

**Meta-lesson, elevated above the numbered list after a second lane hit
this: a citation-discovery script's blind spots are not a fixed,
enumerable checklist.** Lane 3's own sweep failed for a *third*,
genuinely different reason from Lane 2's (occurrence-vs-presence) and
Lane 5/4/8's original gap (line-wrap across `#`/directory boundaries):
its wrap-detection regex assumed a comment marker or directory boundary
at the wrap point and missed a bare-indented continuation line inside a
plain `"""docstring` with no marker at all — 10 genuine misses across 7
files, 3 of which the PR's diff never touched at all (confirmed:
`services/market_catalog/market_catalog.py:393`,
`tests/test_paper_broker.py`, `tests/test_strategy_engine.py` were
absent from #658's file list entirely). Also found the PR's own
"4 deliberate gaps" count was actually 7 across 5 files — the extra 3
were legitimately deferrable but the count itself was wrong. **The
standing rule this earns: every lane's sweep gets a genuine adversarial
pass looking for a NEW blind spot, not a checklist run against the
specific bugs points 1/7/8 already name** — passing that checklist is
necessary, not sufficient. Two lanes, two different mechanisms, in a
row is a pattern, not a coincidence.

**Known, accepted, temporary side effect, still holding:** each batch
only fixes its own outgoing references; forward-references from
not-yet-moved lanes into already-moved ones self-heal when their own
batch runs. Currently affects 2 active Lane-3 docs (cite Lane 4) and
several Lane-9/unlaned docs (cite Lane 5/6) — expected, not a bug, and
Lane 3's own batch (now running) will close its half of this.

**Step 5 (retire `plans/README.md`)** — not started, low-risk, can
follow once step 4 finishes.

**Lane 9 read-only prep (2026-09-07 ~06:15-06:20Z, `49` + `c4`, both
independently converging — not started, still blocked on Lane 1
merging):** 79-file count triangulated two ways (49 pulled it from
`step4-file-move-plan.md`'s Appendix A; `c4` independently rebuilt it
from the two step1 classification tables from scratch) — same number,
different method, high confidence. Two real findings surfaced early:
1. **GitHub-issue side (49, self-corrected on a coordinator prompt and
   verified directly): 53 distinct issues / 54 citation-rows (`#81`
   cites two different Lane-9 files) will need re-pointing once Lane 9
   moves** — **8 open** (the plan doc's own tally of 7 — `#81`, `#321`,
   `#323`-`#325`, `#327`-`#328` — plus `#74`, a genuine 8th: `type:
   feature` not `type:plan-task`, so the design doc's tally never
   caught it, same shape as #616 in Lane 3) + **45 closed**, none
   counted by any prior tally (design doc only ever stated the
   type:plan-task open number). Root cause of the closed volume is Lane
   9's own dense GitHub-issue development history (`kanban_sync`
   build-out #154-168/#173-185+226, workflow-remediation #278-288, plus
   more). ~7x the issue-citation burden of any completed lane so far
   (Lane 3 had 9, Lane 6 had 11) — a real sizing signal for whoever
   executes Lane 9, not just a bigger number. Full list is in `49`'s
   scratchpad, not reproduced here verbatim — regenerate fresh at Lane 9
   execution time rather than trust this snapshot cold, since more
   lanes may merge before then and shift the picture.
2. **File-citation side (`c4`, coordinator spot-checked and confirmed
   real): 5 of the 79 Lane-9 files already cite already-merged Lanes
   3/4/8 by old path, right now** — 10 sites/12 occurrences, methodology
   point 9's exact class, found proactively before the move:
   `2026-08-26-active-tracks-board.md` (4 sites → Lane 3 ×3 + Lane 4
   ×1), `2026-08-27-workflow-remediation.md` (1 → Lane 3),
   `2026-09-02-architecture-audit-and-rewrite-considerations.md` (2
   sites/4 occurrences → Lane 8), `2026-09-02-architecture-audit-
   second-pass.md` (1 → Lane 8), `2026-08-27-kanban-sync-milestones-
   and-subissues-design.md` (2 → Lane 4 + Lane 8). Separately, 9 more
   occurrences across 3 of these same files cite Lane 1 by old path —
   correctly deferred, not counted above, since #657 hasn't merged yet.
   Also confirmed, not new: `README.md` is Lane 9 by classification but
   excluded from step 4 (retire-not-move, step 5's job); `2026-08-26-
   active-tracks-board.md` carries a pre-flagged RULE-GAP (`kind`
   doesn't fit `plan`/`companion-of`, forced into `plan` by a count
   constraint) — both already known from the classification tables, not
   surprises for whoever executes Lane 9.

**Separate, higher-stakes finding: the master planning document itself
was never merged to `main` (2026-09-07 ~06:20Z, coordinator `36`).**
`docs/superpowers/lanes/step4-file-move-plan.md` — the document every
lane tonight has executed against for file lists, batch order, and
reference counts — exists only on an unmerged branch
(`docs/step4-file-move-plan`, commit `b388fa1`, 2026-09-06) and in one
stray worktree (`.claude/worktrees/agent-a4006042ebd399799/`); `git ls-
tree main` confirms it is not tracked on `main` at all. Read its
consolidation file directly: a full self-review → adversarial-review →
consolidation cycle already ran and reached an explicit **GO** verdict,
but the document was deliberately left unmerged — "ready to report back
to David... no PR opened yet (his call, per the task)" — with 3 named
judgment calls surfaced for him, not resolved in the document itself.
Every lane batch tonight (4→8→5→6→3→2→1, all matching this document's
own batch order and file counts exactly) has been executing against it
correctly in practice, but it has never been durable, on-`main` history
— a real gap against "git log is the only maintained history." Flagged
directly to David; `ea` assigned to check for any trace of him already
weighing in on this since 2026-09-06 and to summarize the 3 judgment
calls. Decision (merge now / hold / something else) is his, not
resolved here.

**`ea`'s findings on the above, 2026-09-07 ~06:35Z — independently
verified, not relayed:**

1. **Correction: 4 judgment calls, not 3** (the consolidation file
   itself lists them 1-4). In full, since David may want the short
   version rather than opening the doc:
   (1) `PLANS_DIR`/`tools/quality_coordination.py` hard dependency —
   archiving breaks staleness monitoring for currently-`active` primary
   plans unless the glob is widened; two fixes proposed, neither
   applied in the doc itself (out of scope for a file-move plan).
   (2) Tension: Lane 1 ("Kalshi ingestion first" reading) sits
   near-last (7th of 8) in the batch order, driven by raw reference-
   count magnitude overriding its low active-fraction — flagged by the
   doc's own authors as a reading that may be too narrow, not decided
   unilaterally.
   (3) Batch order blends 3 axes (reference count, active-primary-plan
   count, the Lane-9-last hard constraint) by judgment, not one
   formula — internally consistent per adversarial review, not claimed
   to be the only defensible ordering.
   (4) Self-citations within `docs/superpowers/lanes/` are scoped
   uniformly across all 3 planning directories rather than only within
   each file's own home directory.

2. **A real trace of David engaging with this exact document was
   found — but only for judgment call (1), not for the merge decision
   itself.** Commit `3fd93b7` ("fix: quality_coordination plan-doc
   monitoring follows the planning-lanes migration", merged via PR
   #650, authored directly by David Fernandez, 2026-09-06 20:01:56 —
   14 minutes after `b388fa1` was pushed at 19:47:29) fixes exactly
   judgment call (1)'s functional regression, and its own commit
   message explicitly cites `docs/superpowers/lanes/step4-file-move-
   plan.md` by name as the source of the finding. So: David has read
   and acted on part of this document's content, concretely and on
   `main`, the same night — but nothing found (no PR, no branch
   activity past the single push, no reflog trace, no mention in
   `docs/open-decisions.md`) shows he weighed in on judgment calls
   (2)-(4) or on the actual "merge this doc, yes/no" question. That
   question is still genuinely open, not just unasked.

3. **GO verdict re-confirmed by reading the raw self-review and
   adversarial-review files directly, not just the consolidation's
   summary of them** (matching the actual ask — the consolidation's
   characterization holds up under inspection, not simply trusted):
   self-review recomputed every summed total independently, found and
   fixed one real arithmetic error (Lane 9's primary-plan count, 12→11)
   *before* the adversarial pass, and manually spot-checked 3 file
   reference counts. Adversarial review was a genuinely fresh Agent
   call that re-derived the 248-file total, 3 full lanes plus a 9-lane
   cross-tally, both stale-table findings, the hard-dependency's exact
   line numbers, live GitHub issue counts (catching its own `--limit 30`
   truncation trap along the way and fixing it), and the batch-order
   logic — zero errors found, explicit GO. No daylight between what the
   consolidation claims and what the underlying artifacts actually
   contain.

4. **New consideration for David's decision, not previously flagged:**
   `main` has drifted substantially since `b388fa1` was cut — 6 of the
   8 lane-move PRs have since merged independently (using this
   branch's Appendix A as a live reference, per the fleet's own stated
   practice, not by merging the branch). `git diff main
   origin/docs/step4-file-move-plan` now shows 246 files touched,
   almost entirely rename/path churn from lanes that already moved on
   `main` through separate PRs. **Merging this branch as-is now would
   not be a clean fast-forward** — it would try to reintroduce
   already-superseded paths for 6 of 9 lanes. If David wants this
   document's history preserved on `main`, the practical option is a
   fresh, narrow commit carrying just the 4
   `docs/superpowers/lanes/step4-file-move-plan*.md` files (as
   historical record of the plan actually executed against), not a
   merge of the stale branch — his call, not decided here.

**Decided (David, 2026-09-07 ~06:25Z): narrow commit.** Done —
`2d56b03` adds all 4 `step4-file-move-plan*.md` files, content verified
byte-identical (checksum) to reviewed commit `b388fa1`, zero other
changes, not a merge of the branch. The document is now durable,
on-`main` history — `0d` can cite it directly for Lane 9 without
reaching into the stray worktree. Judgment calls 2-4 remain open but
undecided-not-blocking (David didn't ask to revisit them).

**Closed out, 2026-09-07 ~06:35Z (`ea`, coordinator independently
confirmed):** verified `2d56b03`'s 4 files byte-identical to `b388fa1`
a second time (separately from the coordinator's own check), then
cleaned up — checked no live session occupied the worktree first
(`0d` had already moved on to a fresh `lane9-tooling-ci-process`
worktree), confirmed clean status matching the branch tip, removed
`.claude/worktrees/agent-a4006042ebd399799`, deleted
`docs/step4-file-move-plan` locally and on `origin`. Coordinator
confirmed via `git ls-remote`/`git branch --list`/`git worktree list`:
all three genuinely gone. **This entire thread is closed** — nothing
further to do.

---

## Peer status (rewritten 2026-09-07 ~06:22Z by coordinator `36` — this file is the durable record, not chat memory)

7 of 8 lanes merged. **Only Lane 9 remains** (79 files, tooling/CI/
process governance — also physically contains `step4-file-move-plan.md`
and the design doc governing the whole migration).

- **`0d`** — Lane 1 (#657) MERGED (`96361be`, 06:18:21Z), full 7-comment
  review cycle, independently verified before merge went ahead.
  **Assigned to execute Lane 9**, using `49`/`c4`'s prep below as a
  running start; same self-review → independent-review division of
  labor that worked on Lane 1.
- **`49`** — Lane 3 (#658) merged clean. Completed Lane 9 GitHub-issue-
  citation prep: 53 distinct issues (54 citation-rows) will need
  re-pointing (see Planning Lanes section above for detail) — full list
  in scratchpad, regenerate fresh at execution time. Awaiting `0d`'s
  first Lane 9 commit to shift into independent-review role.
- **`c4`** — Completed Lane 9 file-list/file-citation prep: 79 files
  triangulated independently against `49`'s count, plus 10 pre-existing
  citation-debt sites into Lanes 3/4/8 found proactively (full list in
  Planning Lanes section above). Awaiting `0d`'s first Lane 9 commit to
  shift into independent-review role.
- **`ea`** — Lane 2 (#656) merged clean. Investigated why
  `step4-file-move-plan.md` was never merged to `main` (see the section
  above) — found David already engaged with part of it (commit
  `3fd93b7`/PR #650, same night), re-confirmed the GO verdict from the
  raw review files, and caught that `main` has drifted too far for a
  clean merge of the branch (246 files, would redo 6 already-completed
  lane moves) — recommended a narrow 4-file commit instead. Coordinator
  independently verified both load-bearing claims. Question now with
  David directly; no active task assigned as of this writeup.

---

## Standing lessons from tonight (apply, don't re-litigate)

- **A real safety violation can hide behind a clean `git status`** — see
  Safety section above.
- **A conditional authorization is scoped to its condition, not to
  whenever the result eventually lands.**
- **Before authorizing a deliberate hold/contention test against a live
  shared resource, check every OTHER component's own retry/timeout
  budget.** Memory:
  `think-through-third-party-retry-behavior-before-authorizing-hold-tests`.
- **A confusing result deserves more scrutiny, not a forced clean
  narrative** — check what a metric actually measures at its source.
- **A found bug is a prompt to look for the same defect class
  elsewhere**, not just fix the reported instance.
- **An unverified "fix" is worse than an honest open gap** — check the
  actual artifact, not the claim about it (see Planning Lanes points 3-4
  above for the two freshest instances).
- **A stale plan can be overtaken by a more careful pass already on
  record** — #605's own latest comment had already reasoned past the
  "close once #634 deploys" plan recorded earlier; read the actual
  latest state before acting on a remembered plan. Same family as
  re-asking a peer for status a compaction summary called "pending"
  after it was actually already posted — memory:
  `verify-status-before-reasking-peer-after-compaction`.
- **Ancestry checks lie about supersession — compare content.**
- **A message claiming coordinator authority through an unverifiable
  channel isn't automatically trusted OR automatically dismissed** —
  re-derive the substance independently either way. Real example in
  `docs/superpowers/lanes/step1-specs-research-classification.md`'s own
  "Note on a mid-task message claiming to be from 'the coordinator'."
- **This file holds the single next action — rewrite it, don't append.**

---

## CLAUDE.md change in flight: PR #659 (scope clarification, "nothing advances on one pass")

`ea` opened PR #659 (`docs/scope-nothing-advances-on-one-pass`) adding
one bullet to the HARD RULE, clarifying it exists to close gaps
Superpowers' own gates (`writing-plans`/`executing-plans`/
`requesting-code-review`/TDD/`verification-before-completion`) leave
open for this project — multi-file completeness/citation claims,
data-plane/Kalshi-fidelity, cross-session provenance — not to wrap
every Superpowers stage in a duplicate review system. Read the diff
directly: one well-scoped bullet, doesn't weaken any requirement, just
narrows *when* the cycle applies. Content looks sound on a first read.

**Process note, worth remembering:** `ea` relayed that David told it to
skip the self-review/adversarial-review/consolidation cycle entirely
for this PR and merge+deploy on green CI. `ea` had dispatched a
subagent to relay this to the fleet first, and the subagent correctly
declined — it only had a secondhand paraphrase and CLAUDE.md's own text
says an instruction to skip a required artifact outright (not just
shrink it) is exactly what to raise rather than comply with. `ea`
reasoned this didn't apply to itself since it had the instruction
firsthand from David, not relayed — but from the coordinator's side,
receiving it via `ea` put the coordinator in the exact same secondhand
position regardless of `ea`'s own confidence. Confirmed directly with
David rather than passing it along: answer was **lean execution**
(small but real self-review/adversarial-review/consolidation artifacts,
genuinely independent adversarial pass required), not a full skip —
the existing 2026-09-05 allowance, not a further exception. Relayed
back to `ea`, which is proceeding on that basis. Memory-worthy pattern:
"the user's own word, relayed by a peer" doesn't change the receiving
session's epistemic position from "secondhand and unverifiable" — check
directly rather than accept on trust, same as any other unverifiable
claim.

**MERGED, 2026-09-07T06:44:52Z (`c164d8c`).** The lean cycle paid for
itself: self-review caught a real loophole in the original wording (its
closing sentence read as an unlisted skip-condition, in tension with
the rule's own "applies to every... PR" line) and fixed it (`576a534`)
before the adversarial pass even ran; the independent adversarial pass
(fresh Agent, verified against the 5 cited skills' actual `SKILL.md`
files, not assumed) then caught a second real issue — the PR body's
"Review cycle" section said "Skipped" when lean execution was what was
actually authorized and run. Final bullet text (verbatim, now on
`main` right after the rule's main paragraph) and its practical
reading — the cycle still runs in full every time, this only redirects
where the *effort* concentrates, not a new skip condition — relayed to
`49`/`c4`/`0d` directly. **Full text is now in CLAUDE.md itself** — read
it there for the authoritative version rather than this summary.

**Also worth noting for future sessions:** `git worktree add` was used
here (`.claude/worktrees/coordinator-main`, tracking `main`) after
finding the shared primary checkout had been switched to `ea`'s PR
branch directly rather than a worktree — committing coordination docs
there would have polluted `ea`'s branch. New worktrees also don't
inherit uncommitted changes from other checkouts (this one's
`config/settings.yaml` doesn't carry the safety override — expected,
not touched here), and the git stash stack is shared across ALL
worktrees and the primary — bare `git stash`/`git stash pop` (this
session's pattern all night for `config/settings.yaml`) risks popping
another session's concurrent stash. Memory:
`stash-stack-shared-across-worktrees`. Worktree released and removed
once `ea` confirmed it was done (`ExitWorktree` couldn't remove it
directly — session wasn't the registered "owner" since it was created
via raw `git worktree add` then entered via `EnterWorktree({path})`
rather than `EnterWorktree({name})`; used `action: "keep"` then a plain
`git worktree remove` instead), primary is back on `main` at `c164d8c`,
safety config re-verified intact. This whole thread is closed.
