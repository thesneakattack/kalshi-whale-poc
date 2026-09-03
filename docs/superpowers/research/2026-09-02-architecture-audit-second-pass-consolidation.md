# Consolidation — architecture audit second-pass review cycle (2026-09-02/03)

Reconciles the second-pass artifact
(`docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`),
its self-review, and an independent adversarial review, per CLAUDE.md's
"nothing advances on one pass" HARD RULE.

## Verdict: GO, after this revision

The adversarial review's own verdict was **GO-AFTER-FIXES**: "The artifact's
central, load-bearing claims hold up against independent re-derivation to
an unusually high standard... This is genuinely strong work." No
disagreement to adjudicate between self-review and adversarial review —
the self-review caught 8 precision/wording issues before the adversarial
review ran; the adversarial review confirmed every mechanical count in the
Appendix exactly (167/142/32/30/13/0/11/67/67/14/26), independently
re-derived the `hours=` diagnostics bug and the 237-fault historical
misread down to the minute from a fresh query, and confirmed every
source-code mechanism claim (event-loop sync write, config shallow-merge,
alerting scope, PR #429/#436 mechanics) against current source. Both are
additive; this revision closes every must-fix and should-fix item, plus
folds in two new live findings the review's own probing surfaced.

## Merged fix list, applied

**Must-fix (3/3 applied):**

1. **PR #436 merge timestamp.** Artifact said "16:38 UTC" (in fact commit
   `09b2553`'s own author timestamp, not the merge). `gh pr view 436
   --json mergedAt` (the authoritative source) gives 17:31 UTC, confirmed
   independently by the merge commit's own `git log --merges` timestamp.
   Fixed in both locations (intro list, §2 table). No ordering conclusion
   in the document depended on the wrong number — every before/after
   argument involving PR #436 still holds at the corrected time.
2. **The "2.5-hour steady state" recurrence claim.** Rested on two data
   points (the original incident, ~60 min in; one recurrence in the same
   worker, ~154–156 min in) generalized to "every 2–3 hours indefinitely."
   The adversarial review's own third live census — the same worker at
   215.5 minutes, past the claimed period, holding only 223 of 1,024 fds
   despite serving two heavy diagnostic queries moments before — directly
   contradicts a fixed-clock reading. Rewritten: the leak is real and
   urgent (unchanged), but the specific hours-until-recurrence number is
   withdrawn in favor of a request-volume-coupled mechanism, explicitly
   labeled as not established by three same-session samples under
   diagnostic load.
3. **A newer, more severe fault class was missing.** `market_history`
   throwing `DatabaseError: database disk image is malformed` (45
   occurrences, last seen 20:44:11 UTC) — a corruption signature, not a
   failed-open signature — was absent from the artifact entirely, found
   by the adversarial review's own fresh fault query. Added to §4.1 as its
   own paragraph, to Tier 0 as item 2 (`PRAGMA integrity_check` before
   further writes), and to §9's open questions.

**Should-fix (4/4 applied):**

- Log fragility: the container restarted at 21:38:39Z between the
  artifact's session and the adversarial review, wiping `docker logs` and
  nginx's `access.log` — the exact evidence §3.3 and part of §4.1 are
  built on became permanently unrecheckable from primary evidence within
  hours. Not a falsification (the self-review's arithmetic checks on that
  data were internally consistent, and `fault_log`/`observability`
  corroborate the shape of the same events), but a real methodology risk.
  Added to the Method section as a standing caution: prefer the
  SQLite-persisted `fault_log`/`observability` stores over raw container
  stdout when both are available, and archive a raw excerpt at the moment
  a log-based finding is made.
- `title_cache.py` citation corrected from "one `with _connect()` site" to
  the actual five (lines 139, 157, 182, 220, 282) sharing the same
  non-closing `_connect()`. Doesn't change the finding.
- §4.4's config-diff description corrected from "two changes" to the four
  actually present in the primary checkout's working tree
  (`markets_watchlist_mode`, `max_children_per_parent: 5→0`, `categories`
  narrowed from ten entries to two, and the wiped comment block), with an
  honest note that this document cannot establish whether the extra two
  were present when §4.4 was first written or added afterward, since an
  uncommitted diff carries no history of its own.
- Cross-referenced §4.4 forward to the new §4.7 finding rather than
  leaving the config-narrowing and the live `markets_watched: 0` incident
  as two unconnected sections.

**Should-add (the review's one genuinely new finding, added in full):**

- **§4.7 — live, ongoing at review time: `markets_watched: 0`, a
  44-minute-and-frozen tick, 45 of 46 open positions stale.** Found by the
  adversarial review's own live probing, independently re-confirmed by
  this session with a fresh probe ten minutes later returning the
  identical `last_tick_duration_sec` (evidence of a stuck tick, not a
  merely-large one). This is a complete, current completeness failure —
  qualitatively worse than anything else measured in either audit, which
  are latency and partial-loss findings. Added as: a new executive-summary
  item 0; a new §4.7 section with the two uneliminated hypotheses the
  review chased one layer into source (`market_catalog.db`/
  `market_history.db` corruption vs. a genuinely empty pinned watchlist
  right now) and explicitly did not resolve further, correctly, given
  review budget; the new top item in Tier 0 (§8), ahead of the fd leak
  itself; and three new §9 open questions. Tier 0's remaining items were
  renumbered 0–6 to make room without reordering their own internal
  sequence; Tier 1/2/3 were renumbered +1 throughout to stay contiguous,
  and the two internal cross-references that named old item numbers
  ("after 10", "on top of (14)", "(14)'s measurements") were corrected to
  their new numbers.

## What was not changed

The document's central conclusions — the persistence-idiom fix is real,
urgent, and correctly diagnosed at the mechanism level; the loop-stall/
polling-timeout distinction; the ten corrections to the first audit (C1–
C10); the re-derivation of §5/§8/§9 under the current rule set (§6); the
process findings (§7) — the adversarial review explicitly confirmed all of
these from source and did not ask for changes to any of them.

## Gaps the adversarial review flagged as its own limits, not the artifact's defects

Six items (G1–G6 in the adversarial review) — the nginx/container-log
unverifiability, three metric families that timed out during the review
itself (loop-stall distribution, whale-pipeline latency, `/api/state`
gzip size), P2's non-reproducibility (a claim about a different session's
process), and the Chromium-throttling/ruamel-attachment inferences the
artifact already labels as inference — are limits of what a single review
pass at a given moment can re-check, not claims the review disputes. None
required a document change beyond the log-fragility should-fix above,
which generalizes the lesson.

## PR-stage review (separate cycle, still to run)

Per `.claude/rules/branching-and-ci.md`, once this PR is pushed and opened,
a second full review cycle (self-review, independent adversarial review,
consolidation) runs against the PR as submitted, before merge. That is a
distinct, later step from this artifact-stage consolidation and is not
recorded here.
