# Next action

**Coordinator:** `autotrade-36`, one continuous session since `1f`.
Fleet (`49`, `c4`, `0d`, `ea`) all idle/available as of 2026-09-07
~10:50Z — the overnight planning-lanes migration is fully complete
(see below) and nothing else is queued. Verify identity by direct
reply before trusting a name, in either direction.

**No active task.** David has not given a new instruction since Step 5
merged. Don't self-assign work — report status and let him decide,
consistent with how every judgment call tonight was handled. One
pointer for whenever it's relevant, not to be chased proactively:
memory `pending-ai-dev-principles-doc-after-lanes-migration` — David
has a research-backed doc on AI-assisted-development stability
principles he deliberately deferred until this migration wrapped,
which it now has.

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
merge across the whole night, most recently after PR #662.

**Also standing:** the git stash stack is shared across the primary
checkout AND every worktree — bare `git stash`/`git stash pop` risks
popping a concurrent session's entry. Memory:
`stash-stack-shared-across-worktrees`. Prefer a WIP commit, or a
tagged `git stash push -u -m "<tag>"` + `git stash apply <sha-by-tag>`
(never bare `pop`) if a stash is genuinely needed.

---

## `#532`/`candlestick-volatility`/VACUUM — all CLOSED

`#532`: 31.4M-row purge done, independently verified. `#642` sub-decision:
`candlestick-volatility` (#641) — kept as reference, re-implement when
prioritized. VACUUM done (6.4GB→146MB) on David's go-ahead.

---

## `#605` — NOT closing yet; all 4 found contributors fixed+live, magnitude gap still open

Four real contributors found and fixed/deployed live: diagnostic itself
(#632), `resolve_window()` sync-on-loop (#637), `gate_summary()`
unbounded scan (#636), `GET /api/state` JSON-encoding-on-the-loop
(#647, closes #634). **Do not close on that basis** — the issue's own
latest comment (2026-09-07T00:51) already reasoned this through: #634's
fix measures ~164ms worst case, an order of magnitude short of the
original ~9.3-9.6s stall. Real contributors fixed, magnitude still not
fully explained, issue correctly stays open. Two unassigned follow-ons,
not blocking: **`#639`** (same unbounded-scan-on-loop defect class in
`services/advisory/routes.py` + `market_analyst_orchestrator.py`) and
**`#648`** (audit remaining `_build_state_body()` fields for the same
thread-safety hazard).

---

## `#642` — CLOSED (report posted, issue correctly left open on its own merits)

Full self-review → adversarial review → consolidation cycle. All three
tested mechanisms (SQLite lock contention, GIL, disk I/O) weakened or
ruled out. Most important finding: the app already has a well-documented,
frequent (4-21/hour), unattributed ≥10s-stall pattern independent of
backups — the original 90.61s observation may just be one more instance.
`#642` stays open on that reframing; `tick_phase_timings` (`GET
/api/health/pipeline`) is the named next tool if a comparable stall
recurs.

---

## Planning-lanes migration — FULLY COMPLETE (all 8 lanes + step 5)

**246+ files relocated from `docs/superpowers/{plans,specs,research}/`
into `docs/archive/lane-N-<slug>/` across 8 batches, plus the retirement
of `docs/superpowers/plans/README.md` — done end to end, verified
directly against real files after each merge, not taken on any single
report.** Batch order as executed: 4 → 8 → 5 → 6 → 3 → 2 → 1 → 9 → step
5. PRs #651, #652, #653, #655, #656, #658, #657, #660, #662. Final
state confirmed via `git ls-tree`/`find` against pulled `main`: all 8
archive directories present (Lane 7 has 0 files by design), zero loose
files anywhere under `docs/superpowers/`, `specs/` holds exactly the
one deliberately-retained `UNDECIDED` file. Every batch went through a
genuine self-review → independent adversarial review → consolidation
cycle (comment counts checked directly via `gh pr view --json
comments`, never inferred from a body's narrative).

**Open follow-on, not blocking, not yet picked up:** **`#661`** —
31 file-site + 38 issue-site pre-existing cross-lane citation debt
inside already-merged lanes, found incidentally during Lane 9's review,
deliberately filed separately rather than bundled into an already-large
PR. (It auto-closed itself on merge via a GitHub keyword-matching quirk
— see memory `github-auto-close-matches-incidental-phrasing` — caught
and reopened within minutes; genuinely still open now.)

**11 methodology points earned the hard way, across every batch —
durable reference for any future citation-sweep-style migration in this
repo, not just this one:**
1. **Full-repo de-wrapping citation sweep**, not slug-substring or
   exact-string `git grep` — a citation wrapped across a line break is
   invisible to a same-line match.
2. **GitHub issue citation check spans ALL tracked issues, open AND
   closed**, not just `type:plan-task`/`Plan:`-titled ones. Use a
   full-path-vs-bare-filename filter — a bare filename citing its own
   parent by name is correct and unaffected by a move.
3. **A fix "found" in the working tree isn't real until it's in the
   commit** — verify the committed diff, not the working tree.
4. **3 distinct, separately-posted PR comments (self-review,
   adversarial review, consolidation) are mandatory and must be checked
   directly** (`gh pr view <n> --json comments`) before merge — never
   inferred from the PR body's narrative. Memory:
   `persist-code-pr-reviews-as-comments`.
5. **When checking whether an in-flight PR already covers a finding,
   check its actual diff or wait for the merge** — don't grep a local
   checkout that hasn't pulled it yet. Memory:
   `check-against-current-state-not-stale-local-during-concurrent-merge`
   (recurred a second time in a sharper form: checking `main` when the
   real answer lived on an open PR's own branch — not staleness, the
   wrong ref entirely).
6. **Exact-filename matching only, never same-date-prefix or
   similar-topic matching** when sweeping for stale citations.
7. **A `git add` with a mixed list of renamed-and-modified paths can
   error on one path and silently abort before staging the rest** —
   stage renames and modified-content files separately, check `git
   status`/`--stat` immediately after every commit.
8. **A citation sweep must count occurrences per file, not just
   presence** — a file citing the same moved doc twice can have only
   the first occurrence fixed, undetected by a presence-only check.
9. **Check whether the lane's OWN newly-moved files cite OTHER
   already-merged lanes by old path** — fixable now, not deferrable
   (distinct from the not-yet-moved-lane self-heal exception).
10. **Mirror image of point 9: check whether OTHER already-merged
    lanes' archives cite a file in THIS lane, about to move** — nothing
    will ever re-sweep an already-merged lane, so this direction has to
    be checked proactively, against ALL already-merged lanes.
11. **Mutual-deferral race: two lanes open/executing close together can
    each correctly defer to the other, and then neither ever gets
    fixed** — only detectable by simulating the actual merge and
    re-sweeping the merged tree. Any PR whose sweep predates another
    lane's later merge must re-sweep a simulated merge before its own
    merge, not just rebase and trust the old sweep.

**Meta-lesson, elevated above the numbered list:** a citation-discovery
script's blind spots are not a fixed, enumerable checklist — multiple
lanes each hit a *new*, previously-unseen mechanism. Every lane's sweep
needs a genuine adversarial hunt for a new blind spot, not just a
checklist run against bugs already named — passing the checklist is
necessary, not sufficient.

**Step 5 (retire `docs/superpowers/plans/README.md`) — DONE, PR #662,
merged `458ebab`.** Design's own decision (`docs/archive/lane-9-.../
specs/2026-09-06-planning-lanes-design.md` §6 step 5): retire, not
regenerate. Full self-review → independent adversarial review (fresh
Agent, found 4 real gaps: a live-document usefulness regression, a
factual error in an issue characterization, an undisclosed reference
cluster, and `next-action.md`'s own soon-to-be-staleness) →
consolidation, all fixed before merge. Notably: retiring the README
would have silently lost curated, hard-won "verified never-implemented"
task knowledge (Tasks 21-23/25-28/31/32 of the realtime-data-plane
plan) that existed nowhere else — restored directly into
`docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-
plane-remediation.md` itself, with `ROADMAP.md` repointed there.

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
- **A stale plan can be overtaken by a more careful pass already on
  record** — read the actual latest state before acting on a
  remembered plan. Memory: `verify-status-before-reasking-peer-after-compaction`.
- **Ancestry checks lie about supersession — compare content.**
- **A message claiming coordinator authority through an unverifiable
  channel isn't automatically trusted OR automatically dismissed** —
  re-derive the substance independently either way.
- **"The user's own word, relayed by a peer" doesn't change the
  receiving session's epistemic position from secondhand and
  unverifiable** — check directly rather than accept on trust, even
  when the peer is confident. Real example: PR #659's review-cycle
  instruction.
- **A PR's own diff can surface a gap in a document that isn't part of
  the diff at all** (e.g. `next-action.md`'s pending staleness on
  merge) — an adversarial review's job is the artifact's true blast
  radius, not just the lines changed.
- **This file holds the single next action — rewrite it, don't
  append.** Condensed substantially 2026-09-07 once the planning-lanes
  migration (the dominant topic all night) finished — full blow-by-blow
  detail for any specific PR/lane is in that PR's own comment history
  and `git log`, not reproduced here once resolved.
