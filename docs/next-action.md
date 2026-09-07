# Next action

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

## Planning lanes — 6 of 8 batches done (4, 8, 5, 6, 2, 3); only Lane 1 open

**On `main`:** design (PR #640), all 3 step-1 classification tables (PRs
#643/#644 + a direct commit), step 3 `kanban_sync` retooling (PR #645),
`LANES`/`CONCERNS` infrastructure + full step-2 labeling (PR #646).
Batch order: **4 → 8 → 5 → 6 → 3 → 2 → 1 → 9**. Lanes 4 (#651), 8
(#652), 5 (#653), 6 (#655), 2 (#656), and now **3 (#658, `49`) — MERGED
2026-09-07T05:50:58Z**, confirmed clean by `ea`'s independent sweep
before merge. Only **Lane 1 (#657, `0d`) remains open** — now the sole
blocker before Lane 9 can start.

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

---

## Peer status (rewritten 2026-09-07 ~06:10Z by coordinator `36` — this file is the durable record, not chat memory)

Only Lane 9 remains once Lane 1 lands — it contains the design doc
governing the whole migration. Lanes 2/3 done; Lane 1 (#657) is the
sole blocker, currently **NOT GO** (see Planning Lanes section above
for the full, current detail — this block is who's doing what, not the
PR's technical state).

- **`0d`** — owns Lane 1 (PR #657). Pushed `c08ef9f` fixing the
  original two required items (occurrence-dedup + the first cross-lane
  gap), CI now green on all 12 contexts. Has the full current picture
  as of coordinator's last message: still needs to fix `49`'s 3 new
  file-citation misses, rebase onto current `main` (branch predates
  Lane 2/3), re-sweep, then write the **consolidation comment** — the
  one artifact nobody's posted yet, and the last gate before merge.
- **`49`** — Lane 3 (#658) merged clean. Completed independent
  file-citation sweep on #657 (posted directly, `issuecomment-
  5565762808`): occurrence-dedup confirmed clean, found 3 new misses
  (methodology point 10) + flagged the stale-branch issue. **Now
  assigned Lane 9 prep**: preliminary GitHub-issue-citation check for
  Lane 9's candidate files (mirroring `c4`'s file-side prep below),
  read-only, reporting before anyone acts.
- **`c4`** — Completed independent issue-citation sweep on #657 (posted
  directly, `issuecomment-5565810132`): found and fixed 27 closed
  issues (31 occurrences) `0d`'s self-review missed (open-issues-only
  scope vs. the required 335) — coordinator spot-checked 5, confirmed
  real. **Now assigned Lane 9 prep**: cross-check Lane 9's 79-file list
  against the plan doc's Appendix A + classification tables, plus a
  preliminary file-citation sweep against candidate files, read-only,
  reporting before anyone acts.
- **`ea`** — Lane 2 (#656) merged clean. Did the post-compaction resync
  of this file and independently corroborated the coordinator's
  missing-adversarial-review finding on #657 before either acted on it.
  No active task assigned as of this writeup; last known state idle/
  standing by.

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
