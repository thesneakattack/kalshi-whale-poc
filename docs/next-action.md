# Next action

**Coordinator:** `autotrade-36`, one continuous session since `1f`.
**Fleet PAUSED on David's instruction (2026-09-07 ~03:50)** — all 4
peers confirmed holding, nothing mid-merge. Resume on his signal, not on
a timer. Verify identity by direct reply before trusting a name, in
either direction.

**Nothing is one step from a merge right now** (David asked explicitly):
PR #654 (Lane 4/8 wrap-citation follow-up) failed its own adversarial
review (body undercounted the fix scope 11/8 vs. real 23/11, plus 4 of
16 fixes recreated the exact line-wrap defect being fixed) and is mid a
narrow fix-list recheck, not done. PR #655 (Lane 6) has zero review-cycle
comments yet, ~28 min into its own dispatch. Both in-flight subagents
will finish their current step (can't be frozen mid-turn) but `49` is
holding both short of `gh pr merge` until told to resume.

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
status` is not proof these survived. Re-confirmed still correct as of
`c4`'s pull just now (2026-09-07 ~03:00).

---

## `#532`/`candlestick-volatility`/VACUUM — all CLOSED

`#532`: 31.4M-row purge done, independently verified. `#642` sub-decision:
`candlestick-volatility` (#641) — kept as reference, re-implement when
prioritized. VACUUM done (6.4GB→146MB) on David's go-ahead.

---

## `#605` — NOT closing yet; all 4 found contributors fixed+live, magnitude gap still open

Four real contributors found tonight, **all fixed and confirmed deployed
live** (`c4` independently confirmed #634/PR #647's live deploy
2026-09-07 ~03:00 — commit `780a44b` is an ancestor of synced HEAD, plus
a real `/api/state` ETag/Content-Length check, not inferred):
diagnostic itself (#632), `resolve_window()` sync-on-loop (#637),
`gate_summary()` unbounded scan (#636), `GET /api/state` JSON-encoding-
on-the-loop (#647, closes #634).

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

## `#642` — mechanism still genuinely unexplained, adversarial review pending from `0d`

All three tested mechanisms (SQLite lock contention, GIL contention, disk
I/O contention) weakened; `last_tick_duration_sec` baseline reframed as
ordinary variance once traced to its real definition
(`main.py:1366`). A memory-pressure hypothesis is an untested named
candidate. `0d` was holding an independent adversarial review of this
whole interpretation before posting — status re-requested this session,
no reply yet as of this writeup. Report pending; nothing to do until it
lands.

---

## Planning lanes — design + migration steps 1-3 done; step 4 executing (Lane 5 of 8 in flight)

**On `main`:** design (PR #640), all 3 step-1 classification tables (PRs
#643/#644 + a direct commit), step 3 `kanban_sync` retooling (PR #645),
`LANES`/`CONCERNS` infrastructure + full step-2 labeling (PR #646,
independently verified 3 ways). Batch order (decided):
**4 → 8 → 5 → 6 → 3 → 2 → 1 → 9**.

**Lane 4 (PR #651) and Lane 8 (PR #652): merged, files correctly moved**
(`c4` spot-checked #652's reference-fix mechanics — sound), **but both
have a real, confirmed review-cycle compliance gap, currently being
retroactively remediated by `c4`:**
- PR #651: `gh pr view --json comments,reviews` returns **zero and
  zero** — no self-review, no adversarial review, no consolidation exist
  as artifacts at all. Needs the full 3-stage cycle from scratch,
  retroactive, against current `main` state.
- PR #652: self-review + consolidation are real, but the consolidation's
  own text only *asserts* "an independent adversarial review" happened —
  no standalone comment for it exists. Needs just that missing artifact
  plus a short addendum.
- This is the **third/fourth occurrence** of the same defect shape
  (memory: `persist-code-pr-reviews-as-comments`, now generalized past
  "code PRs" to docs/migration PRs too). **New standing gate as a direct
  result, effective immediately for Lane 6 onward and any future
  in-scope PR:** before `gh pr merge`, the merging session runs `gh pr
  view <n> --json comments` itself and confirms 3 distinct, separately-
  posted stage comments exist — never infers compliance from the PR
  body's own narrative. `49` already independently re-verified this
  standard against PR #653 (3 distinct comments, confirmed) before this
  gate was even communicated to it.

**Lane 5 (PR #653): MERGED** 2026-09-07T03:15, 89 files (52 renames + 37
modified — grew from the pre-merge 76 as the adversarial review found
more real citation sites before merge, a sign the cycle worked, not a
discrepancy). Full 3-comment review cycle confirmed, CI green, `49`
independently spot-checked diff scope + one citation + one issue edit
post-merge.

**Real methodological finding from Lane 5's own adversarial review,
already being acted on**: the slug-substring grep approach used for
citation discovery is blind to citations wrapped across a line break. A
full-repo de-wrapping sweep against the already-merged Lane 4 + Lane 8
tips found **14 more stale files (6 + 8)** missed by their original
sweeps. `49` has dispatched a retroactive fix for those 14 (own full
review cycle, explicitly told to verify 3 *posted* comments before
reporting done) running in parallel with Lane 6 (disjoint file sets).
**Full-repo de-wrapping sweep is now the standing citation-discovery
method for Lanes 6/3/2/1/9**, not slug-substring or exact-string grep.

**Second standing-methodology addition, from `c4`'s retroactive #651
pass and its generalization sweep**: the original per-lane GitHub-issue
citation check (Lane 4/5/8 all used it) only searched `type:plan-task`/
`Plan:`-titled issues — missed **#54** (a general `type:feature` issue
with a genuinely broken citation to a moved Lane 4 file). Generalized
the check across **all 147 open issues** (not just plan-task/`Plan:`-
titled) using the old-full-path-vs-bare-filename distinction (a bare
filename mention, e.g. a plan-task citing its own parent plan by name,
is correct and unaffected by the move — only a full
`docs/superpowers/<dir>/<filename>` path is genuinely stale; a naive
full-text search without this distinction buries real findings in false
positives from legitimate self-citations). Found and fixed 2 more:
**#621**, **#613** (both Lane 5). No further Lane 4/8 hits. **Standing
method for Lane 6 onward: search all open issues, not just plan-task/
`Plan:`-titled ones, and apply the full-path-vs-bare-filename filter.**

**Known, accepted, temporary side effect, still holding:** each batch
only fixes its own outgoing references; forward-references from
not-yet-moved lanes into already-moved ones self-heal when their own
batch runs. Currently affects 2 active Lane-3 docs (cite Lane 4) and
several Lane-9/unlaned docs (cite Lane 5) — expected, not a bug.

**Step 5 (retire `plans/README.md`)** — not started, low-risk, can
follow once step 4 finishes.

---

## Peer status (all PAUSED, confirmed holding as of 2026-09-07 ~03:50)

- **`49`** — PR #654 mid fix-list recheck (not merge-ready, see above);
  PR #655/Lane 6 in early dispatch (not merge-ready). Neither pushed
  toward merge; holding both short of `gh pr merge`.
- **`c4`** — clean, nothing mid-flight. Completed retroactive review-cycle
  artifacts for PR #651/#652 and generalized the #54 finding across all
  147 open issues (found + fixed #621, #613 too) before this pause.
- **`0d`** — `#642`'s adversarial review was already posted (00:20:26Z,
  well before this session resumed) — reframes the original ~90s stall
  as likely just one instance of this app's already-documented 4-21/hr
  unattributed stall pattern, not backup-caused; `tick_phase_timings`
  named as the right tool if it recurs. **Coordinator error, corrected**:
  asked `0d` for this status 3 times after it was already answered,
  across a compaction boundary — memory:
  `verify-status-before-reasking-peer-after-compaction`. Nothing further
  needed on #642.
- **`ea`** — clean, standing watch. Resolved its own labeling-count
  concern earlier (9 unlabeled = 8 stable `RULE-GAP` cases + 1 brand-new
  untriaged issue, not a stuck cohort).

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
  actual artifact, not the claim about it.
- **A PR body's narrative claiming the review cycle happened is not
  itself evidence it happened** — count the actual comments. Now a
  mechanical pre-merge gate, not just a reminder. Memory:
  `persist-code-pr-reviews-as-comments` (4 occurrences now).
- **A stale plan can be overtaken by a more careful pass already on
  record** — #605's own latest comment had already reasoned past the
  "close once #634 deploys" plan recorded earlier; read the actual
  latest state before acting on a remembered plan.
- **Ancestry checks lie about supersession — compare content.**
- **This file holds the single next action — rewrite it, don't append.**
