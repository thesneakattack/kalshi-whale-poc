# Adversarial review: planning-lanes design

Date: 2026-09-06. Stage 2 of the required cycle. Independent Agent call
with no memory of the authoring conversation; every load-bearing claim
below was re-derived from the working tree, `git`, and `gh`, never from
the design doc's own tables or the self-review's summary. Where I could
not re-derive something, I say so.

Reviewed: `docs/archive/lane-9-tooling-ci-process-governance/specs/2026-09-06-planning-lanes-design.md`
(the design) and `...-design-self-review.md` (stage 1).

## 0. Method

- Package inventory: `ls services/`, `ls tools/`, top-level dirs, and
  each candidate package's `__init__.py`/module docstring, README, or
  CHEATSHEET. Ten units checked beyond the self-review's `whale_stream/`.
- Track A/B/C: read the board file itself (found under `plans/`, not
  where the design says), `gh issue view 75 76 77`, ROADMAP.md's "Path to
  production", the execution-program doc's headings, and every live
  pointer to the board (`grep -rn active-tracks-board`).
- Labels/Projects: `tools/kanban_sync/labels.py` (full read),
  `project_status.py` header, `gh project field-list 3`.
- Branch policy: `gh pr list --limit 40`, `git branch -r`, `git worktree
  list`, `docs/branch-audit-2026-09-05.md`, the `checkpoint` skill.
- Counts: `gh issue list --state open --limit 500`, `ls` + `git ls-files`
  on each `docs/superpowers/*/` dir, `git ls-tree` on the parent of PR
  #635's merge for the pre-cleanup `docs/*.md` count.
- Lane 5's "all live here" claim: `gh pr view <n> --json files` for every
  PR the cited issues produced (#624 #625 #627 #630 #632 #636 #637).
- Migration mechanics: `tools/kanban_sync/{__main__,sources_plan,
  sources_tracks,sync}.py`, and a repo-wide grep for
  `docs/superpowers/plans/` path references.

## 1. Confirmed accurate

| Claim | Evidence |
|---|---|
| `phase:*` vocabulary exists in `labels.py` (`PHASE_RESEARCH`…`PHASE_DONE`) | `tools/kanban_sync/labels.py:54-59`, plus `ALL_PHASE_LABELS` at 61-64. Self-review's line citation is correct. |
| 62 plan docs | `docs/superpowers/plans/` has 63 `.md` = 62 dated files + `README.md`. |
| 79 spec docs | `git ls-files docs/superpowers/specs/` = 79 (working tree shows 81: the two extra are the design doc and its self-review, untracked). |
| `plan_tasks.py` decomposes plans at `### Task N:` granularity | `tools/kanban_sync/plan_tasks.py:19` regex, confirmed via `plans/README.md`'s "Constraints on editing" section. |
| `#401`–`#408` labeled `status:done` but open | 7 open (`#401`–`#406`, `#408`); `#407` is CLOSED. Label census: `status:done` on exactly 7 open issues. |
| `#488` says "not-started" despite merged work | Body: "Code classification: not-started… zero of its 9 tasks' code implemented". PR #500 (`feat/tier1-backend-hygiene`) merged 2026-09-03 (`cc6bae3`). Confirmed stale. |
| `#322`/`#326` decided, still open | `docs/open-decisions.md:35` + issue comments dated 2026-09-05T22:48Z. Both `status:claimable`, OPEN. (But see §3.F: `#326` is DECLINED, not GO.) |
| Two `#576` PROTOTYPE worktrees hold uncommitted work | `agent-a025fbb863ef969ed` and `agent-a57cf8e0fd682f79f`: both ` M services/kalshi/websocket.py`, the second also 5 untracked `*_scratch.py` files. Both at `023705d`. |
| `docs/archive-2026-08-27/` exists as tonight's established pattern | 8 files, created by PR #635. |
| Project #3 exists and `tools/kanban_sync` can drive it | `gh project list --owner thesneakattack` → #3 "kalshi-whale-poc — Personal Todo Board"; `project_status.py` holds its real node IDs. |
| The 3 orphaned research docs exist and are orphaned | All three present under `research/`; `session-tooling-friction-log` has zero inbound refs anywhere; the other two are referenced only from `next-action.md` and sibling research docs, never from a spec/plan. |
| `whale_stream/` straddles transport and decision | `services/whale_stream/CHEATSHEET.md` "Owns:" line and its "Downstream — the real cross-boundary call, preserved deliberately" section say so explicitly. Self-review's flag is correct. |

## 2. Wrong or unsupported

### A. Lane boundaries — the table mis-assigns at least six units against their own docstrings, and omits others

The design's method is "primary packages, closest primary fit". Reading
each unit's own docstring, the fit is wrong or split for:

1. **`services/index_feed/` → Lane 1 "Kalshi ingestion"**. Its
   `__init__.py`: "Live CF Benchmarks / Pyth index feed… streamed over
   websocket… plus the settlement-projection math built on top of it
   (`settlement_algebra.py`)". It is not Kalshi data, and half of it is
   projection math consumed by `settlement_edge.py` (Lane 3). Either the
   lane is misnamed ("exchange & index ingestion") or the package splits.
2. **`services/market_analyst_agent/` → Lane 3 "Strategy, risk &
   execution"**. Its `__init__.py`: "Deliberately advisory-only… never
   calls create_order, never opens a paper position, and nothing here is
   wired into strategy_engine.evaluate()". But
   `services/whalewatchers/kalshi_trade_tape.py:212` calls
   `market_analyst_agent.analyst_lean()` on the whale-scoring hot path
   (`_db.py:93` "analyst_lean()'s call from the whale-scoring hot path").
   So it is a Lane 2 scoring input and a Lane 4 LLM-advisory module; Lane
   3 is the one lane it demonstrably is *not* in. (The unmerged
   `feat/candlestick-volatility` branch even carries a commit "correct
   per_market.py docstring to remove stale 'advisory-only' label".)
3. **`services/candidate_retry.py` → Lane 4 "Analytics"**. Docstring:
   "realtime data-plane remediation plan, P2 Task 12… run from exactly one
   place (main.py's tick loop)… routes it through… `decision_bridge.
   _handle_signal`". This is the whale-signal pipeline on the tick loop
   (Lane 2/5), not offline analytics.
4. **`services/series_evaluator.py` → Lane 4**. Docstring: "a cheap check
   (ineligible_series) filters the discovery candidate pool each tick —
   this is what actually stops the flapping". That is watchlist admission,
   i.e. `market_watch` (Lane 1). Open issue `#621` is about exactly this
   file and would be filed in the wrong lane.
5. **`services/settlement_edge.py` → Lane 3**. Docstring: "this module
   says so and nothing should be wired to it… never trades… records and
   scores". By the design's own criterion that puts `backtest/` and
   `research/` in Lane 4, the measurement half is Lane 4; only
   `settlement_edge_entry.py` ("the deliberate, separate 'act on it'
   half") is Lane 3.
6. **`services/game_state.py` → Lane 5 "hot path & reliability"**.
   Docstring: persists `client.get_live_datas` payloads ("costs ZERO
   additional API calls"). It is Kalshi-sourced market-data persistence —
   Lane 1 by the design's own definition.
7. **`services/candidate_log.py` → Lane 4**. Written by every entry gate
   on the decision path (Lane 3), read by advisory (Lane 4), and the site
   of two of tonight's event-loop fixes (`#601` PR #617, `#605` PR #636).
   Three-way straddle with no internal file split to draw a line at.
8. **`services/kalshi/websocket.py`** is Lane 1 by the table, yet the
   `#576` prototypes the design assigns to Lane 5 ("fairness/scheduling
   is a hot-path concern") are uncommitted diffs to *this file*. The
   design's own §4 contradicts its own §2 here.
9. **`services/whalewatchers/kalshi_trade_tape.py`** reads Kalshi's trade
   feed (transport) and does the detection — the same shape as
   `whale_stream/`, unflagged.

**Omitted entirely:** `services/history_push.py` (exists, in no lane);
`.claude/hooks/` and `.claude/settings.json` (the actual enforcement
code — Lane 9 lists only `rules/`, `skills/`, `.woodpecker/`);
`.github/workflows/` (5 files); `scripts/`; `tests/`; `bench/`;
`ui_samples/`; the five loose `docs/superpowers/*.md` kickoff/portfolio
files; non-Kalshi `docs/*.md`. Cosmetic: `series_cache/`, `title_cache/`
are `.py` files; Lane 7 lists `config/` and then four files that already
live inside `services/config/`, and never says whether top-level
`config/settings.yaml` is Lane 7.

**The structural cause.** Lanes 1–4 are defined by pipeline stage, Lanes
6–9 by infrastructure type, but Lane 5 is defined by a *property*
("hot path & reliability"). A property-lane overlaps every stage-lane by
construction. Evidence: the design's own P0 justification for Lane 5 —
"tonight's `#150`/`#530`/`#585`/`#586`/`#605`/`#629` all live here" — is
false against the table:

| PR | Issue | Files | Lane per table |
|---|---|---|---|
| #624 | #585 | `services/backtest/routes.py` | 4 |
| #625 | #585 | `main.py` | unowned |
| #627 | #586 | `services/diagnostics/_aio_db.py` | 6 |
| #630 | #629 | `services/observability/{observability,routes}.py` | 6 |
| #632 | #605 | `services/{db,fault_log,loop_watchdog}.py` | **5** |
| #636 | #605 | `services/candidate_log.py` | 4 |
| #637 | #605 | `services/whale_stream/index_stream_handlers.py` | 1 |

One of seven PRs sits in Lane 5's packages. "Event-loop blocking" is a
cross-cutting *concern* (CLAUDE.md's data-plane HARD RULE), which is
exactly why it cannot be a non-overlapping *lane*.

The self-review's proposed fix — draw the boundary at module level where
a package already has an internal split — handles `whale_stream/` and
`index_feed/` but none of the single-file straddlers above
(`candidate_log`, `candidate_retry`, `series_evaluator`,
`settlement_edge`, `game_state`, `kalshi/websocket.py`).

### B. Track A/B/C retirement — the concept can go, but the design's account of where its content lives is wrong in two places

- **Path.** The board is `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-active-tracks-board.md`
  (`git log --all --name-status` shows it was created there, `c385f9a`,
  and never lived at `docs/superpowers/active-tracks-board.md`). The
  design cites the wrong path in §1 and §2; `next-action.md:346` has the
  same error. `tools/kanban_sync/__main__.py:37` hard-codes the real one.
- **"Track C = ROADMAP.md's existing Program sequencing, unchanged."**
  Wrong location. `grep -n 'Program 3R\|Program 4\|Program 8\|Stages A' ROADMAP.md`
  returns nothing. The Program 3R→8 gate table exists only in the board
  itself (lines 149-157) and in
  `docs/kalshi-personal-production-execution-program-2026-08-26.md`
  (Program 3R at line 861). ROADMAP.md:66 *links to the board* for
  "which of these tracks is next and which can run in parallel". So
  archiving the board (a) orphans ROADMAP's pointer and (b) leaves the
  execution-program doc, not ROADMAP, as the only home of the sequencing
  — the design must say that.
- **"Track A ≈ Lanes 1+5."** Track A's canonical doc
  (`2026-08-25-realtime-data-plane-remediation.md`) names, by count:
  `services/kalshi/websocket.py` ×27 (L1), `whalewatchers/kalshi_trade_tape.py`
  ×10 (L2), `http_client.py` ×9 (L5), `whale_stream_handlers.py` ×8 (L1),
  `observability/` ×8 (L6), `settlement_resolver.py` ×6 (L3),
  `tick_executor.py` ×5 (L5), `diagnostics/` ×5 (L6), `candidate_ledger.py`
  ×5 (L4), `exits/` ×3 (L3), `position/` ×2 (L3). The single most
  important live initiative (4 ACTIVELY-IN-PROGRESS, ~45-60%, 40 tasks,
  the largest `depends-on` chain on the board) spans six of nine lanes.
  §3 says such an initiative "splits at the boundary — each half stays
  single-lane". The design does not say how a 40-task plan with 86
  sub-issues gets split, or whether it does. This is the first real test
  of the lane model and it is unaddressed.
- **Real content that would be lost without a stated home.** The board's
  "Standing human decisions" section (lines 220-237) lists five open
  human calls. ROADMAP.md has deployment target (204) and
  position-size/kill-switch (232); auth model only in prose (48);
  shadow-mode sustained run and auto-apply governance appear in neither
  ROADMAP.md nor `docs/open-decisions.md` (grep). CLAUDE.md names
  `docs/open-decisions.md` as "the single list of parked decisions" —
  the design should route this section there explicitly.
- **Gating edges in GitHub.** `#77` carries `depends-on:#75` and
  `depends-on:#76`; the board's "Concurrency ground truth" (A ∥ B safe;
  C sequential behind both, per the execution-program doc §8) is real
  cross-initiative information. Lanes carry no inter-lane ordering. The
  design's priority column (P0/P1/P2) partially encodes it, but "safe to
  run concurrently" is not the same claim as "P1 is downstream of P0".
  Not lost (the execution-program doc keeps it), but the design should
  name that doc as the surviving owner, not ROADMAP.
- **Tooling the retirement touches, none mentioned in §7:**
  `tools/kanban_sync/sources_tracks.py` (whole module),
  `__main__.py:37` and `:134` (`ACTIVE_TRACKS_BOARD_PATH.read_text()` —
  a plain `FileNotFoundError` once the file moves),
  `labels.py:68` `SYNC_MARKER_KIND_TRACK`, the `<!-- autotrade-sync:
  track:A -->` markers in `#75`–`#77`, `sources_plan.py:23-30` (the board
  text is what *excludes* its four referenced plans from becoming plan
  candidates — retiring it makes them candidates), the `checkpoint`
  skill's `sync --sources worktree,roadmap,track` line (SKILL.md:69),
  `kanban-board-sync/SKILL.md:3,36`, `tools/quality_coordination.py:71`,
  ROADMAP.md:66. Eight touchpoints; §7 names two files and neither is
  one of these.

Verdict on this section: retiring "Track" as a *vocabulary* is fine and
I agree with it. The design's mapping is loose (A spans six lanes), its
"already lives in ROADMAP" claim is false, and the retirement is
presented as archival when it is a small tooling change with a known
crash path.

### C. `labels.py` and GitHub Projects — §7's board claim is contradicted by the repo's own code comments

- `labels.py:39-46`: "Label-based, NOT because this repo's board can group
  its view by Labels — it can't: GitHub Projects V2 board/table views can
  only be grouped by a single-select or iteration *field* on the Project
  itself, confirmed against GitHub's own current docs 2026-08-27 (a prior
  version of this comment claimed the opposite; that was wrong)."
- `project_status.py:2-5` repeats it.
- `gh project field-list 3 --owner thesneakattack`: the only
  single-select field is `Status`. There is no `Lane` field.

So §7's "GitHub Projects v2, grouped by the lane label/field… zero new
infrastructure" is half wrong: a `lane:N` **label** gives `gh issue list
--label` filtering (true, cheap); a **board grouped by lane** requires
creating a `Lane` single-select field on Project #3 (GraphQL mutation),
recording its field/option node IDs in `project_status.py`, and adding a
set-field call in `github_client.py` — the same shape `Status` already
needed. That is new infrastructure, small but not zero, and the repo
already recorded once that assuming otherwise was a mistake.

Also unmentioned: nine open issues already carry ad hoc `area:*` labels
(`area:realtime` ×7, `area:kalshi-integration`, `area:workflow-tooling`),
none defined in `labels.py`. `lane:*` is a second lane-like family; the
design should say it supersedes `area:*`.

### D. §5 branch-vs-issue — restates the status quo; its one new trigger has a day-one false positive

Current state: 4 non-`main` remote branches. Three have PRs (#631 draft,
#636 draft, #637 draft — all live work checkpointed during tonight's
pause). The fourth, `feat/candlestick-volatility`, last commit
2026-08-30, 821 behind / 13 ahead, no PR — and
`docs/branch-audit-2026-09-05.md:53-57` records a *decision* to keep it:
"gets pushed so it cannot be lost, stays unmerged". Of the last 40 PR
branches, 27 already carry an issue number. So "branch = in-flight work,
default to an issue" is already practice; §5 adds nothing enforceable
beyond §6 rule 3, and rule 3's ">7 days idle" fires on `candlestick-
volatility` at the very next checkpoint against a recorded keep decision.
The rule needs a "parked by recorded decision" exemption or it re-triages
the same branch every session.

The actual drift pattern the audit found is not idle branches — it is
docs landing without a home (24 review-companion files inside `plans/`,
34 flat `docs/*2026-09-03*.md` files). §5 does not touch that.

### E. §6 anti-drift rules — independent judgment

1. **Lane token in branch name.** Cheap, no tooling conflict
   (`sources_worktree.py` derives status from PR state, not the name;
   `guard_workflow.py` has no branch-name regex). But redundant for the
   27/40 branches that already carry an issue number once issues carry
   `lane:N`; it adds information only for the 13/40 that don't. Fine as
   a rule; nothing validates it, so a wrong token is silent.
2. **Check the lane for overlap before starting.** The check's *input*
   is the label. `gh issue list --label lane:N` returns nothing useful
   until all 155 open issues are labeled. This is not a rule that
   escalates to a label if it fails — the label is its precondition. The
   design's "written rule first, escalate only if provably not followed"
   ordering is inverted for this rule: the label is day-one. (I agree
   with the self-review's skip-under-pressure concern; the deeper problem
   is that the rule is un-runnable without the tooling it defers.)
3. **Idle >7 days → triage at checkpoint.** Duplicates a pre-built tool:
   the `checkpoint` skill already runs `python -m tools.quality_coordination`
   ("AQC: stale branches/worktrees, plans with unfinished tasks") and
   `scripts/cleanup-worktrees.sh`. Per the design's own "pre-built tool
   over hand-rolled" preference, the rule should point at AQC's existing
   stale-branch output, not add a manual 7-day count. Plus the D false
   positive.
4. **Reconcile when 5+ items change state in one session.** A session has
   no counter for this; the condition is unobservable as written. The
   `checkpoint` skill already runs `kanban_sync sync` (SKILL.md:69), which
   mechanically closes stale worktree/roadmap issues and completed plan
   parents every time. The rule reduces to "run the sync at checkpoint",
   which exists. The "5+" threshold is noise.
5. **Lane list changes only via this doc's successor.** Fine. Gap: the
   lane→package map lives only in a prose table, so "closest primary
   fit" is re-judged by every session. The pre-built-tool-consistent fix
   is a machine-readable map next to the labels it names (a `LANES`
   constant in `labels.py`), so `lane:N` has one source of truth.

Net: rules 1 and 5 are realistic as rules. Rule 2 needs the label from
day one (not an escalation). Rules 3 and 4 duplicate tooling the
`checkpoint` skill already invokes and should be rewritten as pointers to
it, not as new manual checks.

### F. Numeric and factual citations

| Design says | Actual | Note |
|---|---|---|
| 154 open issues | **155** (`gh issue list --state open --limit 500`) | `#634` opened 2026-09-06T04:07Z; off by one, stale at writing time. |
| ~65 top-level `docs/*.md` | **67** before PR #635, **47** now | The design was written after #635 (it cites `archive-2026-08-27/`) but quotes the pre-cleanup number without saying so. |
| 98 research docs | **97** `.md` + 1 `.py` (`2026-08-29-full-settings-table-generator.py`) | A script was counted as a research doc. |
| 62 plan docs | 62 dated files — but **24 are review-companion artifacts** (`*-review.md`, `*-self-review.md`, `*-consolidation.md`), not plans | "51 FULLY-DONE-ARCHIVABLE plans" necessarily includes review records, which are not "done", they are records. `plans/README.md` itself indexes 23 plans. |
| 51 / 4 / 5 / 2 plan buckets | Cannot re-derive: the per-file classification exists nowhere in the repo (grep for the bucket names hits only `next-action.md` and the design). **One falsifier found:** `2026-08-31-weather-index-ingestion.md` is in none of the 4/5/2 named lists, so it is inside the 51 — but `services/weather_index/` does not exist and `docs/open-decisions.md:36` records it as **declined 2026-09-05**, "plan doc needs a 'Declined' header". A declined plan archived as FULLY-DONE loses the decline. | The design has a DECLINED bucket for research/specs (§4) but none for plans. |
| 24-file 2026-09-03 bundle | **34** files match `docs/*2026-09-03*.md`, in 10 distinct bundles | |
| `#377` "says not started despite merged work" | `#377` **CLOSED 2026-09-01**, five days before the audit | Half of that bullet was already false when written. `#488` half is correct. |
| `#322`/`#326` "decided GO but open" | `#322` GO (scoped); `#326` **declined** (codspeed, no account) | Both should close, with different `state_reason`s. |
| `#576` worktrees → Lane 5 | `#576` is **CLOSED** (PR #597 merged); the diffs are to `services/kalshi/websocket.py` (Lane 1 by the table) | |
| "full list is the 5 completed inventory reports" (§4) | No such reports exist as files. Only `next-action.md`'s summary of them survives. | §4's migration cannot be executed from a list that was never persisted. |

### G. Migration mechanics (§4) — the archive move as written breaks tooling and orphans issues

- **No stale-plan closer exists.** `sync.py` has `close_stale_worktree_issues`,
  `close_stale_roadmap_issues`, `close_completed_plan_parents` — nothing
  that notices a plan doc vanished. `sources_plan.list_plan_candidates`
  globs `plans_dir` only; a plan moved to `archive/` silently drops out,
  and its `Plan: <file>` issue (10 open, `phase:plan`) is never
  reclassified or closed. Its sub-issues (86 open `type:plan-task`) carry
  `docs/superpowers/plans/<file>` in their bodies (`plan_tasks.py:120`).
  Moving 51 files first and labeling later strands all of that.
- **378 in-repo references** to `docs/superpowers/plans/…` outside the
  plans dir (111 in `services/ tools/ tests/ .claude/hooks/ main.py`, 3
  in `.github/workflows/*.yml`, plus CLAUDE.md, ROADMAP.md,
  `.claude/rules/kalshi-integration-authority.md`, `docs/kalshi/CHEATSHEET.md`).
  PR #635 moved 8 files and needed 5 reference fixes; 51 files is not
  "extending the pattern", it is a different order of magnitude and the
  design does not budget for it.
- **Review-companion files** must move in the same commit as their parent
  plan or every `…-consolidation.md` → `…-review.md` cross-link breaks.
- `docs/archive-2026-08-27/` is named for the date of the consolidation
  doc it executed; putting 2026-09-06 archiving under that name is a
  date lie the next reader will trip on. Use `docs/archive/` (or per-lane
  subdirs) and leave the 08-27 dir as the record of that one PR.

### H. Internal inconsistencies

- §1: "conflates two different things (see §3)" — the reconciliation is
  in §2, not §3.
- §7: "earns the escalation past step 1 in the §0b ordering" — there is
  no §0b.
- §2 vs §4 on `services/kalshi/websocket.py` (Lane 1 vs Lane 5), above.
- §2 Lane 5's P0 rationale vs the PR file lists, above. The self-review's
  alternative rationale (Lane 5 is a prerequisite, not downstream) is
  sound and should replace the false one — but note that "prerequisite
  infrastructure" is a package-defined lane (`db.py`, `http_client.py`,
  `tick_executor.py`, `task_supervisor.py`, `loop_watchdog.py`,
  `fault_log.py`, `capture_writer.py`, `latency_agg.py`, `pagination.py`),
  which is not what "hot path & reliability" as a property means.

## 3. Missing

1. A decision on how the realtime remediation plan (6 lanes) is handled:
   split, or an explicit "multi-lane initiative" exception with a named
   primary lane. §3's split rule cannot be applied to it as written.
2. A resolution rule for single-file straddlers (no internal split to
   draw on): e.g. "a file is owned by the lane of its *writer*; its
   readers file cross-lane issues in the owner's lane".
3. A cross-cutting *concern* mechanism distinct from lanes (e.g. a
   `concern:hotpath` label, or reuse of `area:*`) so the data-plane HARD
   RULE's property can tag work in any lane without needing a lane of
   its own.
4. Where the board's "Standing human decisions" go
   (`docs/open-decisions.md`, per CLAUDE.md).
5. The persisted per-file classification the migration depends on. Until
   the 62-plan / 98-research / 79-spec buckets exist as a checked-in
   table, §4 is not executable and not reviewable.
6. A DECLINED bucket for plans (weather-index-ingestion; likely
   claudesuperpower-plugin-pilot's codspeed task).
7. Ordering constraint for §4: label issues → update kanban_sync
   (retire `track`, teach `plan` source about `archive/` or pre-close
   moved plans' issues) → then move files. The design implies file moves
   can go first.
8. The `Lane` Project field as an explicit deliverable if §7's board view
   is wanted; or an explicit statement that the label alone suffices and
   the board view is deferred.
9. `lane:*` vs existing `area:*` labels.
10. A note that `main.py` is 1,354+ lines of composition root touched by
    #625 tonight; "not owned by any lane" means `main.py` changes never
    get a lane label — say how they are tagged (by the route/loop they
    wire, per §2, which needs stating as a rule, not a parenthetical).

## 4. On the self-review

- Its `labels.py:54-59` verification: correct, independently confirmed.
- Its `whale_stream/` flag: correct, and understated — it is one of ≥9
  straddlers, and its proposed module-level fix covers ≤3 of them.
- Its P0-for-Lanes-1-and-5 reasoning: I agree with the *prerequisite*
  framing and disagree that it rescues the design's Lane 5 as written,
  because the design's stated evidence for Lane 5 is false (§2.A table).
- Its rule-2 concern: agree, and the more decisive problem is that
  rule 2 is un-runnable without labels (§2.E).
- Its "no self-identified blocker" verdict: I disagree. The self-review
  did not check any path, count, issue state, or PR file list, and the
  design's migration section and Lane 5 justification both fail on those.

## 5. Verdict: NO-GO (revise the lane table and §4/§7, then re-review the revised table)

The concept is right and I endorse it: a fixed set of package-owned
lanes, a `lane:N` label as the single query key, Lane > Initiative > Task
vocabulary replacing "Track", written rules over tooling where a rule is
actually runnable. None of that needs to change.

The document, as written, cannot drive the destructive migration it
authorizes: its central table mis-assigns ≥6 units against their own
docstrings and rests Lane 5's P0 on a claim the PR file lists falsify;
its biggest live initiative crosses six lanes with no stated handling;
its migration step would crash `kanban_sync`'s `track` source, orphan
10 plan issues and 86 sub-issues, and break several hundred path
references; and its board claim contradicts the repo's own recorded
finding. Because the required fixes change the lane list itself, which
§3 says is exactly the change that must go through the full cycle, this
is a revision-and-re-review, not a fix-list recheck.

### Blocking fixes (must land before a GO)

1. **Redefine Lane 5 by package, not property.** Name it "Runtime
   infrastructure" (or similar) with the explicit file list; move the
   hot-path *concern* to a cross-cutting label. Drop the "#150/#530/…
   all live here" sentence; use the prerequisite rationale instead.
2. **Fix the straddlers with a stated rule**, then re-assign:
   `index_feed/` (rename Lane 1 or split), `market_analyst_agent/` (out
   of Lane 3), `candidate_retry.py`, `series_evaluator.py`,
   `settlement_edge.py` (split from `_entry`), `game_state.py`,
   `candidate_log.py`, `kalshi/websocket.py`, `kalshi_trade_tape.py`.
   Add `history_push.py`, `.claude/hooks/`, `.github/workflows/`,
   `scripts/`, `tests/` to the table.
3. **Decide the realtime remediation plan's handling** (split vs.
   multi-lane exception with a primary lane) and state the general rule
   it instantiates.
4. **Correct §7's Projects claim** to match `labels.py:39-46`: label =
   filtering; board grouping = a new `Lane` single-select field, listed
   as a deliverable or explicitly deferred.
5. **Re-sequence §4**: persist the per-file classification as a
   checked-in table first (with a DECLINED bucket for plans; fix
   weather-index); label issues; update `kanban_sync` (retire
   `sources_tracks.py`/`SYNC_MARKER_KIND_TRACK`, `__main__.py:37,134`,
   both SKILL.md files, `quality_coordination.py:71`, ROADMAP.md:66;
   pre-close or re-point moved plans' issues); only then move files,
   review companions in the same commit as their parent, with the
   reference-fix count budgeted.
6. **Correct the Track C claim**: the sequencing lives in
   `docs/kalshi-personal-production-execution-program-2026-08-26.md`
   (Program 3R at line 861), not ROADMAP.md; route "Standing human
   decisions" to `docs/open-decisions.md`.
7. **Fix the board path** (`docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-active-tracks-board.md`)
   in §1/§2 and in `next-action.md:346`.

### Required, non-blocking

8. Rule 2: state that `lane:N` labels are a day-one precondition, not an
   escalation. Rules 3/4: rewrite as pointers to the `checkpoint` skill's
   existing AQC and `kanban_sync` runs; add a "parked by recorded
   decision" exemption (the `candlestick-volatility` case). Rule 5: add a
   machine-readable lane→package map in `labels.py`.
9. Correct the counts: 155 issues; 67→47 top-level docs (say pre/post
   #635); 97 research `.md`; 62 plan files of which 24 are review
   records; 34-file 2026-09-03 bundle; `#377` already closed; `#326`
   declined not GO; `#576` closed.
10. State `lane:*` supersedes `area:*`; state how `main.py` changes get
    tagged; rename the archive target away from `archive-2026-08-27/`.
11. Fix the §3/§0b dangling references.

### Optional

12. Note that `docs/superpowers/plans/README.md` says "22 plans" and
    indexes 23 — it is itself stale and should be regenerated as part of
    the migration, or retired in favor of the lane label.

What would change my verdict: a revised table where each of the nine
units above is placed by a stated rule I can re-apply and get the same
answer, and a §4 that names the `kanban_sync` changes before the file
moves. With those two, the rest is a fix-list recheck.
