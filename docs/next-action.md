# Next action

**Coordinator:** `autotrade-36`, one continuous session since `1f`.
**Fleet RESUMED (2026-09-07, David lifted the pause).** All 4 peers
active again. Verify identity by direct reply before trusting a name,
in either direction.

**PR #654: MERGED.** Fix-list recheck finished correctly (23/11 count
verified, all 16 fixes re-checked intact, 3 review comments, CI green).
PR #655 (Lane 6) still genuinely mid-review.

**Coordinator error, corrected**: initially told `49`/`0d` that 5
specific citations (routes.py:195, history/__init__.py:6,
evidence_provenance.py:5, test_candidate_log.py:1055+1313, ROADMAP.md:275)
were a "genuine gap, separate from #654's scope" — wrong. That check ran
against local `main` *before* #654's merge landed; all 5 were already
inside #654's own fix set (line-wrap-blind citations, exactly its
scope) and are now fixed. Memory:
`check-against-current-state-not-stale-local-during-concurrent-merge` —
don't grep a stale local checkout to judge whether an in-flight PR
covers a finding; check its diff directly or wait for the pull.

**Both re-scoped questions resolved by `0d`, closed:**
(1) `2026-08-27-backend-services-modularization-design.md` is
deliberately `UNDECIDED` (step1-specs-research-classification.md, the
"Unlaned / UNDECIDED" section) — genuinely splits into 4 co-equal groups
with no stated primary (services/history→4, config→7, position→3,
reset→6), verified directly against the table. Not a miss; correctly
left unmoved. (2) Fresh sweep against current (post-#654) main: 37 raw
hits, all either point-in-time classification-table snapshots (never
touched by any lane's sweep, confirmed by checking Lane 5's own
already-completed one has zero `docs/archive/` updates in its table
either) or expected forward-references from not-yet-moved lanes
(self-heals on schedule, already documented above). **Zero real
remaining gaps** in services/, tests/, tools/, main.py, or ROADMAP.md —
Lane 8 is now actually ruled out, not just unchecked. This entire
follow-up line is closed.

**Also surfaced, not an incident requiring action**: that same
classification table documents `ea` correctly resisting two
"coordinator"-claiming messages that arrived through an unverifiable
channel (a system-reminder narrating a claimed instruction, not a
verified direct message) during the original drafting — one carried a
demonstrably false claim about her own work, the other asserted what
turned out to be the actually-correct final rule but she still
re-derived it independently rather than trust the channel. Good
real-world confirmation the "verify identity before trusting a name"
discipline works; see the table's own "Note on a mid-task message
claiming to be from 'the coordinator'" section for the full account.

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

## Peer status (resumed 2026-09-07)

- **`49`** — merging PR #654; continuing PR #655/Lane 6's review cycle.
- **`c4`** — resumed, synced, correctly deferring on #654/#655 (both
  actively owned by 49). Standing by for next verification ask.
- **`0d`** — assigned the Lane 4/8 stale-citation follow-up sweep (see
  above). `#642`'s adversarial review was already posted (00:20:26Z) —
  reframes the original ~90s stall as likely one instance of this app's
  already-documented 4-21/hr unattributed stall pattern, not
  backup-caused; `tick_phase_timings` named as the right tool if it
  recurs. Nothing further needed on #642 itself.
- **`ea`** — standing watch, resumed. Noted `last_tick_duration_sec:
  7.45` (mildly above the usual ~2-3s) on one reading, correctly
  withheld judgment pending a second data point rather than call it a
  finding — pointed at `tick_phase_timings` if it turns out sustained.

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
