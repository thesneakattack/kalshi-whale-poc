# Self-review — step-1 issues classification (147 rows)

Date: 2026-09-06
Reviews: `docs/superpowers/lanes/step1-issues-classification.md`, same author/
context per CLAUDE.md's "nothing advances on one pass" HARD RULE — this is the
cheap first layer, not a substitute for the independent adversarial pass that
follows in its own document.

## Checks performed

- **Count verified independently twice**: the 147-issue master list
  (`gh issue list --state open --limit 200 --json number --jq '.[].number'`,
  re-run clean after an earlier corrupted attempt where embedded newlines in
  some titles broke a tab-joined `jq` one-liner) diffed byte-for-byte against
  this table's 147 compiled row numbers via a plain Python set/sort diff —
  exact match, no gap, no duplicate. The apparent gaps in the issue-number
  sequence (no #543-#545, no #601) were checked against the master list and
  are real absences (those numbers are simply not open issues right now, most
  likely already closed by merged fixes), not omissions from the batching.
- **Every RULE-GAP row was re-derived from the live issue body**
  (`gh issue view <N> --json body`), not accepted on the originating
  subagent's characterization alone: #49, #51, #56, #69, #412, #491, #496,
  #530, #634 were all re-read directly during this consolidation pass. #56's
  and #530's bodies are quoted verbatim in the main table's ruling-3 section
  and RULE-GAP register from that direct read, not paraphrased from a
  subagent's summary.
- **The retracted Lane-9 ruling was caught before being written to any
  committed file.** A coordinator cross-session message mid-session asserted
  "cross-cutting hygiene work → Lane 9" and was applied in-progress to three
  rows (#491, #496, #530) plus a pre-existing subagent call on #56, before a
  second cross-session message retracted it the same session (David caught
  it: Lane 9 has no claim on `services/`, and §3's own "an initiative that
  grows to touch a second lane splits at the boundary" already answered the
  case that prompted the bad ruling). All four rows were reverted to
  RULE-GAP — their pre-ruling state for #491/#496/#530, and a fresh
  RULE-GAP writeup for #56, which the retraction email didn't name directly
  but matches its exact defect shape (application-code reorg, not process
  governance). No git history exists showing the wrong version, because the
  correction landed before the first commit, not via a follow-up fix.
- **Every other Lane 9 row was spot-verified against its real issue body**
  (#59, #74, #81, #497, #513, #525, #590, #608, #613, #615 were read directly
  during this pass; #249, #321/#323-328, #386, #411, #447, #459, #462, #463
  already carried grep/source-verified file paths from their originating
  subagents' own "files consulted" lists) to confirm each sits inside Lane
  9's own literal package list (`tools/`, `.claude/`, CI config, `tests/`,
  non-Kalshi docs) or is a genuine audit/report/process-governance deliverable
  — none is an application-code reorg mislabeled as hygiene under the
  retracted ruling's shape.
- **Cross-batch consistency checked** on the one pair that looked
  superficially contradictory on first read — #458 and #497, both "full
  regression suite + live validation" tasks, classified into different lanes
  (6 and 9 respectively) by two different subagents. Read both issue bodies
  directly rather than trusting either batch's own framing: #458's text ties
  its re-verification narrowly to "both routes this plan exists to fix" (a
  single-lane plan, tier0-live-incident-remediation, whose primary subject is
  the Lane-6 diagnostics routes), while #497's text covers "the whole plan"
  (tier1-backend-hygiene, an 8-task bundle spanning six lanes with no single
  subject). Confirmed as a real, justified distinction, not an inconsistency
  to fix.
- **Internal consistency**: lane-count table sums to 147 (138 lane-bearing +
  9 RULE-GAP); the `concern:hotpath` list was regenerated programmatically
  from the merged table rather than hand-counted, to avoid the same kind of
  transcription slip a first hand-written draft of that list actually made
  (it listed a stray "#494" with a self-correcting parenthetical before this
  pass replaced it with the script-verified list — caught and fixed here,
  not shipped).
- **Scope discipline**: confirmed no issue was labeled, closed, edited, or
  commented on via `gh` during this exercise, and no file under `services/`,
  `tools/kanban_sync/`, or elsewhere was moved — this is step 1 only
  (persist a classification table), not step 2 (apply labels) per the
  design's own §6 migration order.

## Unaddressed / left for adversarial review

- No independent, memory-less pass has yet re-derived any row's lane
  assignment from the design doc and issue body without reference to this
  table's own reasoning. Every check above was performed by the same
  session/context that produced the table — real value (it caught the
  retracted-ruling contamination and a hand-counting slip before either
  reached a committed file) but not a substitute for genuine independent
  adversarial review, which CLAUDE.md's HARD RULE and the design's own §6
  step 1 both require before this table counts as reviewed.
- The RULE-GAP register's completeness (are there other rows that *should*
  carry the flag but don't) was checked only by re-reading the rows already
  flagged by the originating subagents plus the four retraction-affected
  rows — not by independently re-deriving all 138 non-flagged rows from
  scratch. A full independent re-derivation of a sample (not necessarily all
  138) is exactly what the adversarial pass should do next.
- The "Note for the coordinator's own plans-slice table" section raises a
  cross-table observation (G3/G4 sharing this table's #56-shaped gap) but
  does not itself fix or re-open the plans-slice table — that document is
  outside this session's ownership and is flagged, not edited.

## Verdict

Self-review passes: the table is internally consistent (147/147, no
duplicates, no gaps unaccounted for), the retracted ruling left no trace in
the committed version, and every flagged uncertainty is stated rather than
silently resolved. **Not yet a GO for step 2** — that requires the
independent adversarial review and consolidation this document is not a
substitute for.
