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

**Lane 1 status as of 2026-09-07 (`ea`, post-compaction resync,
independently re-verified live against the actual PR branch content,
not relayed):** head still `9f4e1baf8b078d00e01caf2d298d14a3b8362369`
(unchanged since the finding below was first relayed — fix not yet
applied), only 2 PR comments so far (self-review + an arithmetic
correction to the PR body's citation count), **no adversarial-review or
consolidation comment posted yet.** The confirmed cross-lane
back-citation gap (5 files/7 occurrences, listed below) was re-checked
directly against the branch tip and is still present, unfixed. Pinged
`0d` directly with the exact file/line list (msg `fbc40ade`); no reply
yet. Do not re-derive this finding again if resuming after another
compaction — re-check `gh pr view 657` fresh instead, per the lesson
below about re-asking after compaction.

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

## Peer status (updated 2026-09-07 by `ea`, post-compaction resync — this file is the durable record, not chat memory)

Only Lane 9 remains once Lane 1 lands — it contains the design doc
governing the whole migration. Lane 3 is done; Lane 1 is now the only
blocker.

- **`0d`** — Lane 1 (PR #657, OPEN, head `9f4e1ba`, 2 comments as of
  last check): two required fixes outstanding, **still not confirmed
  applied** (head SHA unchanged, no adversarial-review/consolidation
  comment posted yet — re-verified directly, not relayed):
  1. Re-check for the Lane-2-style occurrence-vs-presence bug (not yet
     confirmed either way).
  2. **Confirmed-real cross-lane back-citation gap, 5 files/7
     occurrences, re-verified against the branch tip on 2026-09-07**:
     - `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-remediation.md`
       → cites Lane 8's `2026-08-25-frontend-modularization.md` (1x, ~line 2512)
     - `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-30-kalshi-category-data-completeness-implementation.md`
       → cites Lane 4's `2026-08-27-backend-services-modularization.md` (1x, ~line 566)
     - `docs/archive/lane-1-kalshi-ingestion/specs/...diagnostics-widening-plan-consolidation.md`,
       `...plan-review.md`, `...pr420-adversarial-review.md` (3 files)
       → all cite Lane 6's `2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md`
       (2+2+1 = 5 occurrences)
  Pinged `0d` directly 2026-09-07 with this exact list (msg `fbc40ade`);
  no reply received yet as of this writeup.
- **`49`** — Lane 3 (PR #658): **MERGED** 2026-09-07T05:50:58Z, 3
  comments (self-review/adversarial/consolidation all present), fixed
  the 10-miss third-sweep-blind-spot finding and the deliberate-gaps
  count correction before merging. Also got a late-arriving post-merge
  corroboration from the originally-dispatched adversarial reviewer's
  own recheck — fully consistent, nothing further on Lane 3. Now free;
  **assigned by coordinator (`36`) to independently re-sweep #657**
  using its own proven shape-agnostic de-wrap method (strip leading
  whitespace, try both space- and no-space-joined rejoins), read-only
  against the PR's actual committed tree — specifically hunting the
  Lane-2-style occurrence-vs-presence bug and any residual wrap-detection
  blind spot like the one it just found on Lane 3. Not editing the
  PR/branch; `0d` still owns applying any fixes.
- **`ea`** (this session) — Lane 2 (PR #656): **MERGED** (`0fe413b`),
  fully closed out. Re-verified PR #657's outstanding findings live
  against the branch tip post-compaction (2026-09-07) rather than
  trusting the prior writeup; independently confirmed #658 merged clean.
- **`c4`** — standing watch; **assigned by coordinator (`36`) to
  independently verify the GitHub-issue-citation sweep for Lane 1** — all
  335 tracked issues (147 open + 188 closed), full-path-vs-bare-filename
  filter, per standing methodology point 2. This is currently a
  self-review-only claim (0d says it checked issues, but nobody
  independent has verified it, unlike the file-citation side `ea` already
  checked). Read-only against the PR's branch content, not local `main`.

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
