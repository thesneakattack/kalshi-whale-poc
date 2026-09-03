# PR-stage adversarial review — PR #439 (architecture audit second pass)

Independent review, no memory of the drafting/artifact-review session, per
CLAUDE.md's "nothing advances on one pass" HARD RULE and
`.claude/rules/branching-and-ci.md`'s PR-stage cycle. Scope: re-derive
load-bearing claims from primary sources (live app, container, git, GitHub
API, source) — not from the PR body, the diff's own prose, or the
companion review documents' summaries.

## Method (commands run, UTC timestamps)

All times UTC unless noted. Session start ~01:36:33 UTC 2026-09-03.

1. `gh pr view 439 --json body,commits,files,additions,deletions,...` (01:36) —
   PR metadata, body, file list.
2. `gh pr view 439 --json files --jq '...'` (01:36) — 7 changed files, with
   additions/deletions.
3. `curl -sk -m 90 https://kalshi-whale-poc.ddev.site:8443/api/health/pipeline`
   (started 01:36:33, ended 01:37:41 — **504 Gateway Time-out** from nginx
   after the full 90s budget).
4. `curl -sk -m 90 ".../api/health/faults?component=market_history&limit=15"`
   (started 01:37:41, ended 01:38:50 — **504 Gateway Time-out**, same
   pattern).
5. `git -C <primary> diff config/settings.yaml` (01:38) and `git status
   --short` (01:38) — read-only, primary checkout untouched.
6. `curl -sk -m 60 -o state.json -w "..." .../api/state` (01:39:59–01:40:32,
   HTTP 200 in 25.7s) — confirms the app is up but severely slow, and
   `"markets": []` in the response body.
7. `ddev describe` (01:39) — all core services (`web`, `db`, `fastapi`)
   report `OK`.
8. `ddev logs -s fastapi --tail=5000/--tail=100` (01:40–01:44) — most
   recent entries top out at `2026-09-03 01:35:19` (a `filters_by_sport`
   backfill call); no completion log line for my two 01:37–01:39 curl
   requests appears in the subsequent tail, i.e. those requests were
   still in flight server-side, not merely slow-then-served.
9. `ddev exec -s fastapi 'python3 -c "sqlite3.connect(\"file:...fault_log.db?mode=ro\"...)"'`
   (01:41–01:43) — read-only queries (no writes, `mode=ro`) against the
   live `fault_log.db` to independently re-derive the §4.1 fault counts,
   since the `/api/health/faults` endpoint itself was unreachable. This
   substitutes for step 1 of CLAUDE.md's "start investigations here" list
   only because that exact endpoint timed out twice in a row and the
   instructions capped retries at one per endpoint.
10. `ddev exec -s fastapi 'find /app/data -maxdepth 1 -iname "*.db" -exec ls -la {} \;'`
    (01:41) — file sizes/mtimes for every `data/*.db` file.
11. `gh pr view 436 --json mergedAt,number,title` (01:44) — independent,
    non-PR-body source for the corrected PR #436 timestamp claim.
12. Direct file reads of the pushed branch in the
    `.claude/worktrees/audit-second-pass` worktree: the second-pass
    document's §4.1/§4.4/§4.7/§8/§9/Appendix, `docs/next-action.md`,
    `docs/open-decisions.md`, the consolidation doc in full, and
    `git log --oneline -- <second-pass.md>` (single commit — no prior
    revision to diff against for the renumbering pass).
13. `git show HEAD:config/settings.yaml` on the primary checkout to
    independently re-count the original `kalshi.categories` list against
    the working-tree diff.

No writes were made to any file, the app, or the container. No
`git checkout/stash/reset/rebase/merge` was run anywhere.

## Findings

### 1. §4.7 "markets_watched: 0, tick stuck" — CONFIRMED, and now further degraded

Two independent primary-source checks corroborate the claim without
relying on the PR's own account:

- `/api/health/pipeline` — the exact endpoint the claim cites — did not
  return at all in 90s; nginx returned a 504. The fastapi access log shows
  no completion for this request in the ~7 minutes after I issued it,
  meaning the request was still being processed (or the tick-holding lock
  was still held) well past the document's own "44-minute-and-counting"
  and the PR body's "now over an hour" language. This is **more severe**
  than what the PR describes, not merely consistent with it: as of my
  check, the app's own health-diagnostic route is itself unreachable,
  where the document's most recent probe (00:50–01:30 UTC window) still
  got a `200` with `markets_watched: 0` back.
- `/api/state` (a different, lighter-weight, non-tick-dependent route)
  returned `200` after 25.7s (itself far slower than CLAUDE.md's "fastest
  live read" framing implies under normal conditions) with `"markets":
  []` in the body — independent corroboration of zero markets currently
  known/served, from a code path that does not go through the same tick
  logic as `/api/health/pipeline`.

**Verdict: CONFIRMED, UNDERSTATED relative to current live state.** The
PR's characterization was accurate as of its own last check and remains
directionally correct; if anything the author should be told the
situation has continued to degrade since the PR was opened (health
endpoints are now fully timing out, not just reporting a stuck tick).

### 2. §4.1 `market_history.db` "malformed" fault — CONFIRMED exactly

Read-only query against `fault_log.db` (bypassing the unreachable
`/api/health/faults` route) returns, for `component='market_history'`,
`exc_type='DatabaseError'`, message `'database disk image is malformed'`:

```
count=45, first_seen=2026-09-02T18:21:30.956151, last_seen=2026-09-02T20:44:11.565890
```

This is an **exact** match — count and last-seen down to the
microsecond — to what both the PR body ("45 occurrences, last seen
2026-09-02 20:44:11 UTC") and §4.1/§9/`docs/next-action.md`/
`docs/open-decisions.md` claim, independently re-derived from the
database itself rather than trusted from the document's own account.

**Verdict: CONFIRMED**, with one clarification worth surfacing to the
author: this fault's window (18:21–20:44 UTC) is later than, and
non-overlapping with, the 08:24–14:45 UTC fd-exhaustion incident window
described elsewhere in §4.1. The document does correctly treat this as a
separately-found, later fault ("found live, not in the original incident
sweep," `docs/next-action.md`) rather than folding it into the fd-leak
narrative — that distinction holds up.

### 3. The 1,187-line fd-exhaustion count — GAP (not independently re-derivable now), but internally consistent

The document's per-10-minute-cluster breakdown at line 421 (`08:20 192,
08:30 28, 08:40 53, 09:00 142, 09:10 223, 11:10 51, 12:40 34, 12:50 6,
13:00 352, 13:10 43, 14:40 63`) sums to exactly **1,187** — the arithmetic
is internally correct.

I could not independently re-derive this figure against raw logs: `ddev
logs -s fastapi` only retains a rolling buffer that currently starts
around `2026-09-03 00:36`, well after the 08:20–14:50 UTC window in
question, and re-running the historical grep is not possible from current
container state. A related but methodologically different measure — the
`fault_log.db` aggregate `count` column, summed across every row whose
message contains "unable to open database file" or "readonly database" —
totals **1,060** (1,058 + 2), not 1,187. This is not a contradiction: the
document's own consolidation flags exactly this class of risk ("prefer
`fault_log`/`observability` over raw stdout... a real methodology risk")
and the two totals plausibly diverge because `fault_log`'s per-signature
`count` field could itself under-count relative to raw log lines emitted
during the very fd-exhaustion window it describes (fault-log writes are
themselves SQLite opens, and could have failed silently during the
incident). Flagging as a **GAP**, not a falsification — the author should
know this specific number is not currently re-checkable from live
evidence and rests on the (unrecheckable, per the consolidation's own
"log fragility" should-fix) container log capture taken at drafting time.

### 4. §8 prioritized-plan numbering — sequential 0–28, no gaps or duplicates; two cross-references verified correct, one likely still stale

Read end-to-end (Tier 0: 0–6, Tier 1: 7–14, Tier 2: 15–24, Tier 3: 25–28).
Sequence is contiguous with no duplicate or skipped numbers.

Cross-reference checks:

- Item 19 ("`apply_suggestion()` extraction ... after 11") — item 11 is
  the `config_store` deep-merge/PATCH rework. This is a sensible
  dependency (extracting the 6-site config-write call sites makes more
  sense after the underlying write API changes shape) and matches the
  consolidation's own stated fix: "after 10" → "after 11" was one of the
  two cross-references the renumbering pass corrected. **Confirmed
  correct.**
- Item 16 ("Finish the `aiosqlite` migration ... on top of (15)") — item
  15 is the persistence module. Matches the consolidation's other stated
  fix ("on top of (14)" → "(15)"). **Confirmed correct.**
- Item 21 ("The Preact migration (unchanged; after 7)") — item 7 is
  *stall attribution* (§4.3), which has no stated causal relationship to
  the Preact migration. The original first-audit document's rationale for
  sequencing the Preact migration was explicitly tied to the
  **de-polling** fix ("sequenced after Tier 1's backend fixes given the
  causal link between the two" — the causal link being dashboard-load
  polling, not event-loop stalls; the second-pass document's own §6.8
  table entry echoes this: "the dashboard's own polling is one of two
  live degradation mechanisms"). In this document's current numbering,
  the de-poll fix is item **8**, not item 7.

  The consolidation document states explicitly that exactly **two**
  internal cross-references needed correcting after the Tier-0 insertion
  shifted Tier 1/2/3 by +1 ("after 10"→"after 11" and "on top of
  (14)"→"(15)") — and lists no third. Reconstructing the pre-shift
  numbering from those two confirmed examples (each shifted by exactly
  +1) implies the pre-shift Tier 1 started at item 6 (stall attribution)
  and the de-poll fix was pre-shift item 7 — meaning "(after 7)" reads as
  a **third, unfixed stale reference** left over from before the +1
  shift, that should now read "(after 8)". I could not confirm this
  against a prior revision directly (the document was pushed as a single
  commit — `git log` shows no earlier version to diff), so this is a
  tightly-constrained inference from the consolidation's own worked
  examples rather than a direct diff, but it is internally consistent and
  matches the stated rationale in both documents.

  **Verdict: likely FALSIFIED (should-fix).** Recommend the author check
  whether item 21 should reference item 8, not item 7, before merge — low
  stakes (a planning-doc pointer, not a live claim) but a genuine miss of
  the same class the review cycle was specifically checking for.

### 5. `next-action.md` / `open-decisions.md` consistency with the second-pass document — CONFIRMED consistent, but propagates one shared numeric error

Both files' new content (markets_watched:0/44-min tick, the malformed-db
fault at 45/20:44:11, the four-item `config/settings.yaml` diff, the
fd-leak summary) matches the second-pass document's §4.1/§4.4/§4.7/§9
content closely, including exact figures. No drift found between the
three.

However, **all four** occurrences of the `kalshi.categories` narrowing
claim — `docs/next-action.md:31`, `docs/open-decisions.md:50`, and
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`
at lines 636 and 1257 — state the list was "narrowed from **ten**
entries... to two". Independently re-counting from
`git show HEAD:config/settings.yaml`'s committed `kalshi.categories` list
(`Sports, Crypto, Climate and Weather, Entertainment, Economics, Politics,
Mentions, Commodities, Financials, Science and Technology, Elections`)
gives **eleven** entries, not ten — confirmed by a second, independent
source in the same PR diff's context: `docs/open-decisions.md`'s older,
unrelated, already-`RESOLVED 2026-08-30` entry (line 27) independently
describes "all 11 categories present in the app's own catalog" and
enumerates the identical eleven-item list. The document's own §4.4
paragraph at line 636 even spells out the full list right next to the
word "ten" — the list has eleven items in it.

**Verdict: FALSIFIED.** This is a small but concrete, reproducible
off-by-one that survived self-review, the artifact-stage adversarial
review, and consolidation — worth a must-fix given this repo's HARD RULE
on dimensional/arithmetic accuracy for exactly this kind of count claim.
Four call sites need the same one-word fix (ten → eleven).

### 6. Diff scope — CONFIRMED: exactly the 7 intended files, `config/settings.yaml` untouched

`gh pr view 439 --json files` lists exactly:
`docs/next-action.md`, `docs/open-decisions.md`,
`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`
(+14/-0, a short forward-pointer paragraph, read and confirmed accurate),
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass-adversarial-review.md`,
`-consolidation.md`, `-self-review.md`, and the main `-second-pass.md`
(+1364/-0). `config/settings.yaml` does not appear. The primary checkout's
`git diff config/settings.yaml` (read-only check, untouched) shows exactly
the four changes the PR describes:
`markets_watchlist_mode: merge→exclusive`, `max_children_per_parent:
5→0`, `categories` narrowed to `[Crypto, Commodities]` (per finding 5,
mislabeled as "ten→two" rather than "eleven→two"), and the 24-line
calibration-audit comment block removed. **Confirmed**, no drift beyond
what's described.

### 7. PR #436 merge-timestamp fix — CONFIRMED applied correctly and independently re-verified

`gh pr view 436 --json mergedAt` (a source neither the PR body nor the
companion review documents are) returns `"mergedAt":
"2026-09-02T17:31:31Z"`. The pushed second-pass document and
`docs/next-action.md` use "17:31 UTC" / "17:32 UTC" (merge vs. worker
start, one minute apart, correctly distinguished) consistently, with zero
remaining occurrences of the stale "16:38" figure in either file (`grep
-c "16:38"` = 0 for both). The six/one remaining "16:38" occurrences are
confined to the adversarial-review and consolidation documents
themselves, in contexts that are explicitly *describing the error found*
("Artifact states 'PR #436 (merged 16:38 UTC...)'... 16:38 UTC is commit
`09b2553`'s own author timestamp") — correct and expected, not a
lingering defect.

**Verdict: CONFIRMED**, the artifact-stage review's must-fix #1 was
genuinely applied and its correction is independently verifiable, not
just self-reported.

### 8. Other observation (out of assigned scope, not chased further)

`ddev exec -s fastapi 'find /app/data ...'` (finding 10 in Method) surfaced
`data/series_watcher.db` at **29,404,196,864 bytes (~29 GB)** and
`data/candidate_log.db` at **~3.67 GB** — both far larger than any other
file in `data/` (the next largest, `game_state.db`, is ~1.05 GB). Neither
file's size is mentioned anywhere in the second-pass document. This may or
may not be related to the live degradation in finding 1, or may be
expected/known growth; per this repo's "decide, don't over-investigate"
convention, I did not chase this further since it wasn't part of the
assigned checks, but it's a large enough anomaly that it seems worth the
author's attention independent of this PR (not a reason to block it).

## Verdict: GO-AFTER-FIXES

The document's central, load-bearing claims hold up well against
independent re-derivation from primary sources — the two most
time-pressured, most-recently-added findings (§4.1's malformed-database
fault and §4.7's stuck-tick/zero-markets incident) both check out exactly
against direct, non-PR-sourced evidence, and the PR's own account of the
artifact-stage review cycle (self-review → adversarial review →
consolidation, must-fix #1 in particular) is independently verifiable,
not merely self-reported. Diff scope is clean: exactly the 7 intended
files, `config/settings.yaml` genuinely untouched.

Two concrete issues came out of this pass that the prior cycle missed:

**Must-fix (before merge):**

1. "Narrowed from ten entries to two" → should read "eleven entries to
   two" in all four locations: `docs/next-action.md:31`,
   `docs/open-decisions.md:50`,
   `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md:636`
   and `:1257`. Confirmed by direct count of the enumerated list, by
   `git show HEAD:config/settings.yaml`, and by an independent,
   already-resolved decision entry elsewhere in the same file that states
   the same eleven-item list explicitly.

**Should-fix (low stakes, recommend before merge but not blocking):**

2. §8 item 21 ("The Preact migration ... after 7") likely should read
   "(after 8)" — the de-poll fix, not stall attribution, is what the
   original audit's rationale ties the Preact sequencing to, and the
   consolidation's own account of its renumbering pass only claims to
   have fixed two cross-references, not three. Recommend the author
   spend one minute confirming intent before merge; this is a
   planning-document pointer with no live-system consequence either way.

**Not blocking, but worth relaying to the author for awareness, not for
this PR's scope:**

3. Live app state has degraded further since the PR's own last check:
   `/api/health/pipeline` and `/api/health/faults` are now fully
   unresponsive (504 after 90s), not merely reporting a stuck tick. The
   PR's characterization was accurate when written and remains
   directionally correct, but understates current severity. This doesn't
   change anything about the PR's docs-only diff, but the reader should
   know the Tier 0 top item is, if anything, more urgent now than the PR
   text conveys.
4. `data/series_watcher.db` (~29 GB) and `data/candidate_log.db` (~3.67
   GB) are unusually large and unmentioned in either audit document — not
   this PR's concern, flagged for a future investigation.
5. The 1,187-line fd-exhaustion count (§4.1) could not be independently
   re-derived from currently available container logs (the rolling log
   buffer no longer covers that window) — internally consistent
   (cluster breakdown sums exactly to 1,187) but currently unverifiable
   from primary evidence; the document's own consolidation already flags
   this class of risk generally. No action needed beyond awareness.

Recommendation: apply must-fix #1 (a trivial one-word-times-four text
correction) and resolve should-fix #2 before running `gh pr merge`; both
are small enough to fix directly without a new full review cycle, per
CLAUDE.md's fix-list-recheck provision ("check the revision against the
fix list item by item... This recheck is scoped to the fix list; it is
not a second full self-review-plus-adversarial-review pass" — neither fix
changes the document's scope or introduces a new claim the prior two
reviews never saw).
