# Planning lanes: design (round 2, fix-list recheck applied)

Date: 2026-09-06
Author: `autotrade-36` (coordinator)
Status: DRAFT — round 2 got GO-WITH-REQUIRED-FIXES from an independent
Fable adversarial review
(`2026-09-06-planning-lanes-design-adversarial-review-round2.md`, 10
required + 2 optional fixes, all applied below). Per that review's own
verdict, this is a fix-list recheck, not a round 3, because no fix
requires adding/merging/removing a lane — **if any fix below is found to
require that, this reverts to a full new cycle**, per round 2's own §6
rule 5. Every fix number in brackets refers to the round-2 review's
"Required fixes" list.

## 1. Problem (unchanged, endorsed by both review rounds)

Research docs that never became specs, specs that never became plans,
plans that shipped without a plan doc, top-level `docs/*.md` sprawl (67
before PR #635, 47 after), **146 open issues** (fix #7 — not 155; 14
closed 2026-09-06 between PM `#585 #586 #629 #398 #488 #401`-`#406 #408
#322 #326`, dated so this number is checked against a date, not treated
as permanent), and a "Track A/B/C" concept
(`docs/superpowers/plans/2026-08-26-active-tracks-board.md`) that is 9+
days stale.

David's ask, unchanged: fixed non-overlapping **lanes**; every plan/
branch/issue/doc assigned to exactly one; free movement *within* a lane;
continuous re-consolidation; Kalshi ingestion and its prerequisites
first; destructive rebuild authorized (scoped away from `data/*.db` and
safety gates); written rules over tools, pre-built tools over hand-
rolled ones.

## 2. The straddler resolution rule — with the gap clauses the review found (fix #2)

**Base rule, unchanged:** a file/module is assigned to the lane matching
its own **primary declared purpose** (its docstring/README's own framing
of what it exists to do), not everything it happens to touch or be
touched by. Where an internal module/file boundary already separates two
concerns, the boundary is drawn there.

**Three clauses added, closing the gaps the review demonstrated by
finding inconsistent applications:**

- **(a) A stale factual claim inside a docstring does not override what
  the code demonstrably does, but does not automatically change the
  lane either.** `market_analyst_agent/`'s docstring says "nothing here
  is wired into strategy_engine.evaluate()" — true — but also implied it
  is never called from any hot path, which `kalshi_trade_tape.py:212`'s
  `analyst_lean()` call contradicts. The specific false claim gets
  corrected in the same PR that touches the file (hygiene, not a lane
  question). The package's **lane** still follows its primary purpose
  (LLM-based advisory analysis → **Lane 4**), because that purpose isn't
  what was contradicted — only the "never called from anywhere hot"
  implication was. Lane 2 (`kalshi_trade_tape.py`, the caller) is a
  declared cross-lane dependent of Lane 4's `analyst_lean()`.
- **(b) A docstring that explicitly disclaims a single owner** (shared
  pure-helper modules depended on by multiple lanes, by design) is
  assigned to **Lane 5 (Runtime infrastructure)**, joining `app_state.py`
  /`state_view.py`, which already serve exactly this "shared plumbing,
  not owned by any one consumer" role. Applies to `market_lookup.py`
  (its own docstring: "built from the already-in-memory
  `services.app_state.state`... extracted into its own module... rather
  than owned by any one of [position management, history/analytics,
  main.py], since all three already depended on it" — both halves of
  that sentence are `market_lookup.py`'s own, and both point at the
  shared-plumbing-over-`app_state` role `state_view.py` already fills in
  Lane 5).
- **(c) A file's own docstring wins over the lane its containing
  directory's name would suggest**, when that file has its own distinct,
  separately-stated purpose (the rule's original first clause, made
  explicit rather than left implicit). Applies to `config_performance.py`
  (docstring: "so `services/advisory/advisory_engine.py` can score each
  config variant" — **Lane 4**, despite living inside `services/config/`).
- **(d) A docstring that states two purposes without marking one
  primary** is assigned by its **first-stated** purpose. The recheck
  found this tiebreak was used for `market_history.py` ("Real Kalshi
  market data, logged over time…" precedes the hypothetical-trades
  half) and `series_watcher.py` ("captures the raw exchange data…"
  precedes the reconciliation half) without being stated as a clause —
  it is now. The second-stated purpose's lane is a declared cross-lane
  dependent.

## 3. The lanes (9), corrected table (fix #1)

| # | Lane | Primary packages |
|---|---|---|
| 1 | **Kalshi & index data ingestion** | `services/kalshi/`, `market_catalog/`, `market_events/`, `market_watch/`, `whale_stream/whale_stream_handlers.py` + `index_stream_handlers.py` (primary purpose is transport/index-tick capture; **Lanes 2, 3 and 4 are declared cross-lane dependents** for the calls these files make outward — **both** files reach `decision_bridge` (L2); `index_stream_handlers.py` imports it at its own `:18`, which an earlier fix missed by attributing that dependent to `whale_stream_handlers.py` alone (caught by PR #640's adversarial review, re-verified against source). `index_stream_handlers.py` additionally reaches settlement-observation (L4, `:242`) and entry-decision (L3, `:245`) — not "transport-level only," which was false against the file), `index_feed/ingestion.py` + `backfill.py`, `series_cache.py`, `title_cache.py`, `series_evaluator.py`, `game_state.py`, `market_history.py` (primary purpose is Kalshi-sourced market-data capture, per the `game_state.py` precedent — its own secondary `compute_hypothetical_trades()` is a Lane 4 dependent use), `series_watcher.py` (its own docstring puts "captures the raw exchange data" before the reconciliation half — same ordering tiebreak as `market_history.py`; Lane 2/4 are declared dependents for the reconciliation half) |
| 2 | **Whale signal detection & calibration** | `whalewatchers/` incl. `kalshi_trade_tape.py`, `whale_simulator.py`, `confidence_scoring.py`, `whale_gate.py`, `whale_calibration/`, `signal_log.py`, `services/whale_stream/decision_bridge.py`, `candidate_retry.py` |
| 3 | **Strategy, risk & execution** | `strategy_engine.py`, `exits/`, `risk_manager.py`, `paper_broker.py`, `execution.py`, `shadow_mode.py`, `position/`, `mutual_exclusivity.py`, `settlement_edge_entry.py`, `settlement_resolver.py`, `kalshi_fees.py` |
| 4 | **Analytics, advisory & research** | `analytics/`, `advisory/`, `backtest/`, `history/`, `research/`, `stats_power.py`, `market_analyst_agent/` (clause a), `settlement_edge.py`, `candidate_log.py` (**corrected — was wrongly split into Lane 3 by an "owned by writer" rule round 2 didn't actually adopt as its stated rule; its docstring is word-for-word the same "only observes, never trades" shape as `settlement_edge.py`, so the same rule gives the same lane**; Lane 3's entry gates and Lane 5's `capture_writer.py` are declared cross-lane dependents/writers), `index_feed/settlement_algebra.py`, `candidate_ledger.py`, `trade_category.py`, `config_performance.py` (clause c), `ml_feed.py`, `data_quarantine.py` (docstring: "protection of the analytics dataset" — names the Lane 4 dataset directly) |
| 5 | **Runtime infrastructure** (package-bounded, not property-bounded — see `concern:hotpath` below for the property) | `capture_writer.py`, `task_supervisor.py`, `loop_watchdog.py`, `tick_executor.py`, `http_client.py`, `db.py`, `pagination.py`, `fault_log.py`, `app_state.py`, `state_view.py`, `market_lookup.py` (clause b — its docstring disclaims a single owner and names the in-memory `services.app_state.state` as what it reads, the same shared-plumbing role `state_view.py` fills here), `auth.py`, `accounts_store.py`, `logging_config.py` |
| 6 | **Observability, quality & safety infra** | `quality/`, `observability/`, `storage_health/`, `diagnostics/`, `alerting/`, `backup/`, `reset/` (no package-level `__init__` docstring or README — its three modules do carry their own docstrings, per the recheck, so the rule reads it at module level; kept here on the architecture map's original placement, "Danger Zone" reset/audit routes matching this lane's safety-infra scope, pending a direct docstring/README addition as its own small fix — optional #12), `latency_agg.py` (moved — "hot-path telemetry," Observability's own domain, not a Runtime-infrastructure concern despite being latency-related), `whale_pipeline_perf.py` (moved — "stage-by-stage timing and counters," same telemetry-not-detection distinction) |
| 7 | **Config & control plane** | `services/config/` — `routes.py`, `config_paths.py`, `config_store.py`, `config_bounds.py`, `config_overrides.py` (**all five are inside the subdirectory** — verified `ls services/config/`; there are no flat `services/config_*.py` files. Round 2 and the first recheck draft had this exactly inverted, sourced from `services/config/README.md:8`'s stale "stay flat for now" — `services/config/__init__.py`'s own docstring records the move-in on 2026-08-27. That README line is a hygiene fix for Lane 7's population work.) `config_performance.py` is inside the same subdirectory and still goes to **Lane 4 by clause c**; top-level `config/settings.yaml` |
| 8 | **Frontend & dashboard** | `frontend/`, `static/`, `history_push.py`, `ws_manager.py` (**moved from Runtime infrastructure — round 2 put the two dashboard-push modules in different lanes under the identical "pushes updates to the dashboard" criterion; `history_push.py`'s own docstring says it's "modeled on `services/ws_manager.py`'s own extraction shape" for that same job; same criterion → same lane**) |
| 9 | **Tooling, CI & process governance** | `tools/`, `.claude/rules/`, `.claude/skills/`, `.claude/hooks/`, `.github/workflows/`, `.woodpecker/`, `scripts/`, `tests/`, `bench/`, `ui_samples/`, non-Kalshi top-level `docs/*.md`, loose `docs/superpowers/*.md` kickoff files, this lane system's own upkeep |

`main.py` stays unowned; a change to it is tagged with the `lane:N` of
the route/loop/wiring it touches (stated rule, not a parenthetical).
`docs/kalshi/` stays out of scope entirely (reference resource).

**Borderline, no forced answer (review §2.A.11, accepted as genuinely
borderline rather than papered over):** `kalshi_fees.py` (contract
semantics vs. Lane 3 consumers) and `mutual_exclusivity.py` (detects a
Kalshi event-structure property, consumed by Lane 3) both stay in Lane 3
as listed — a "contract semantics vs. consumer" tiebreak is named as a
follow-up rule refinement, not resolved here, since neither assignment
was found wrong, only under-justified.

### Cross-cutting concerns are not lanes (fix #4)

A property like "runs on the event-loop hot path" cuts across every
lane and would break non-overlap as a lane (round 1's actual mistake).
**Concrete mechanism, not left abstract this time:**

- `CONCERN_HOTPATH = "concern:hotpath"` — a new constant in
  `tools/kanban_sync/labels.py`, alongside a `CONCERNS` dict mirroring
  the new `LANES` map (§8, rule 5). Applied *in addition to* exactly one
  `lane:N` label — an issue/PR can carry zero or more `concern:*` labels
  plus exactly one `lane:N`.
- **Applier, stated:** any issue/PR whose fix addresses CLAUDE.md's
  data-plane HARD RULE class of defect (sync-on-event-loop, blocking I/O
  on a hot path) gets `concern:hotpath` in addition to its real
  `lane:N`. Example, corrected against the actual PR file lists: #632
  → `lane:5` + `concern:hotpath`; #636 → `lane:4` + `concern:hotpath`
  (candidate_log.py's corrected lane, §3); #637 → `lane:1` +
  `concern:hotpath`.
- **Cross-lane reference form, concrete:** reuses the existing
  `depends-on:#N` marker convention `kanban_sync` already recognizes
  (confirmed on `#77`: `depends-on:#75`, `depends-on:#76`) rather than
  inventing a new label family. A declared cross-lane dependent files
  its own issue in *its* lane and links it `depends-on:#N` to the
  specific issue in the *owning* lane it depends on — never a second
  `lane:M` label on the same issue (that would violate exactly-one).
  **One consequence to use deliberately, not accidentally:** per the
  kanban plugin's own `dependency-chain.md`, `depends-on:#N` gates
  *claimability* — the dependent issue reads as not-claimable until the
  owner-lane issue closes. So `depends-on` is for genuine blocking
  dependencies only; an *informational* cross-lane reference (this Lane
  2 module reads Lane 4's `analyst_lean()`, nothing is blocked) is a
  plain `#N` mention in the issue body, queryable with
  `gh issue list --search`, and never `depends-on`.
- **The existing 8 `area:*` label definitions** (`architecture`,
  `frontend`, `kalshi-integration`, `misc`, `production-readiness`,
  `realtime`, `strategy`, `workflow-tooling`) **are deleted**, not just
  "not used going forward" — round 2 said the latter and the review
  correctly noted that leaves dead definitions around, the same shape as
  the stray `phase:implementation-plan` label still sitting on `#75`-
  `#77` (superseded 2026-08-27 per `labels.py:31-38`, never removed).
  Both get cleaned up in the same labeling pass (migration step 2).

### Reconciling "Track A/B/C" — restored (the fix-list recheck found this section silently dropped in the round-2 rewrite after the round-2 review had confirmed it correct; that is the "revision drops a requested fix" defect CLAUDE.md names, so it is restored here, not re-argued)

Board: `docs/superpowers/plans/2026-08-26-active-tracks-board.md`.
Retiring "Track" as a vocabulary stands. Where its content goes:

- **Track C is not ROADMAP.md's Program sequencing.** The Program 3R→8
  gate table lives in
  `docs/kalshi-personal-production-execution-program-2026-08-26.md`
  (Program 3R at `:861`) and in the board itself (`:142-157`); ROADMAP.md
  contains none of it (`grep 'Program 3R\|Program 4\|Program 8' ROADMAP.md`
  → nothing) and `ROADMAP.md:66` merely links to the board. **The
  execution-program doc becomes the sole surviving owner of
  production-sequencing gates** — which is why §6 step 3 must edit that
  doc's `:995`, where it currently defers *to* the board as "the
  authoritative live status tracker" and calls itself stale.
  `ROADMAP.md:66`'s link is repointed at the execution-program doc.
- **The board's "Standing human decisions"** (`:220-237`, 1 checked +
  5 unchecked) are ported into `docs/open-decisions.md` — the round-2
  review read that file in full and found zero of the 5 already there,
  so the port is five new lines, no duplicates to reconcile.
- **Track A/B's still-open items** fold into their owning lanes' tracked
  initiatives per §5 (Track A's canonical plan → primary Lane 1).
- **The `#576` prototype worktrees** (`agent-a025fbb863ef969ed`,
  `agent-a57cf8e0fd682f79f`, both uncommitted diffs to
  `services/kalshi/websocket.py`): `#576` is CLOSED (PR #597). If those
  diffs are ever formalized they are **Lane 1** work tagged
  `concern:hotpath` — round 1 had them in Lane 5, wrong by the table.
  Formalize-vs-discard remains a human call.

**Tonight's seven `concern:hotpath` PRs under the corrected table** (the
evidence that the concern label, not a lane, is what these share):
#624 `backtest/routes.py` → Lane 4; #625 `main.py` → unowned, tagged by
the loop it wires (Lane 4's auto-apply); #627 `diagnostics/_aio_db.py` →
Lane 6; #630 `observability/*` → Lane 6; #632 `db.py`/`fault_log.py`/
`loop_watchdog.py` → Lane 5; #636 — **five files, not three** (the
recheck's correction): `candidate_log.py` + `analytics/routes.py` → Lane
4, two `tests/` files → Lane 9 by the table, one `docs/*.md` → Lane 9,
labeled **Lane 4** by its own title per §5's small-PR rule; #637
`whale_stream/index_stream_handlers.py` → Lane 1. One label describes
what all seven share; seven different rows describe where each lives.

## 4. Naming hierarchy (unchanged, endorsed)

**Lane** → **Initiative** (a plan/spec/research chain, tracked issue, or
open PR) → **Task**.

## 5. Multi-lane initiatives — one principle, two scopes, not two rules (fix #3)

The review's own judgment, adopted: this is the straddler rule applied
at initiative granularity rather than a separate mechanism. **Corrected
statement:** a multi-lane initiative's primary lane is the lane matching
**what the initiative's own Goal/spec text names as its subject** — not
a file-reference count, which the review proved is both undefined (three
counting methods gave three different top files) and false under every
method for the specific claim round 2 made ("nearly 3x" does not hold
under any of them).

- **Realtime-remediation plan** (`2026-08-25-realtime-data-plane-
  remediation.md`): Goal line names "event-loop stalls and cross-
  blocking on the WebSocket and REST planes" — the Kalshi connection
  itself. Primary lane: **Lane 1**. Tasks touching other lanes' files
  get normal cross-lane-linked sub-issues in those lanes (`depends-on`),
  not a silent split.
- **Small-PR case, degrades to the same rule:** a PR/branch that
  legitimately touches multiple lanes' files in one atomic fix (PR
  #636's five files: `candidate_log.py` + `analytics/routes.py`, two
  `tests/` files, and a `docs/*.md`)
  is labeled by its own title/description's primary purpose, not a file
  count — which for #636 is now unambiguous anyway, since `candidate_log
  .py`'s corrected lane (§3) and `analytics/routes.py` are **both Lane
  4**; the `docs/*.md` touch (a census-doc correction) is incidental,
  noted in the PR body, not separately labeled.

## 6. Migration — order unchanged, touchpoint list completed (fix #5, #9, #10)

1. **Persist real classification tables as checked-in files** — one
   each for the 146 open issues, the 62 dated files under `plans/` (with
   the 24 review-companion files explicitly named as belonging to a
   specific parent, moving with it, never independently counted; the one
   companion with no filename-obvious parent,
   `2026-09-03-persistence-layer-task8-candidate-ledger-self-review.md`,
   is assigned to `2026-09-03-persistence-layer-db-migration-
   implementation.md` — Task 8 of that plan, per its own title), the 97
   research `.md` files, and the 79 spec docs. Include a DECLINED bucket
   for plans (fixes `weather-index-ingestion`'s round-1 miscounting).
   **These tables are themselves a reviewed artifact** (fix #9) — they
   get their own self-review + independent adversarial review +
   consolidation before step 2 starts, not accepted on a delegated
   subagent's completion claim. This review round's own sweep of the 66
   `services/` units found 1 omission and 7 rule-application
   inconsistencies at that scale; a 238-row table generated by "applying
   the stated rules row by row" will reproduce the same error rate
   unless checked the same way.
2. **Label every issue** `lane:N` (+ `concern:hotpath` where it
   applies), delete the 8 stale `area:*` definitions and the stray
   `phase:implementation-plan` labels on `#75`-`#77` in the same pass.
3. **Fix every `kanban_sync` Track-retirement touchpoint before any file
   moves** — the complete list, re-derived (round 1 and round 2 both
   under-counted this):
   - `tools/kanban_sync/sources_tracks.py` — retire the module.
   - `__main__.py:26` (`from ...sources_tracks import parse_track_items`
     — deleting the module breaks *every* subcommand at import time,
     before any path is even read), `:37` (the `ACTIVE_TRACKS_BOARD_PATH`
     constant itself — the recheck's correction: this is the definition,
     not a read site), `:134` and `:224` (the two
     `ACTIVE_TRACKS_BOARD_PATH.read_text()` call sites, each a bare
     `FileNotFoundError` once the file moves — round 1 found only
     `:134`), `:39` (`KNOWN_SOURCES` includes `"track"`), `:224`
     (`_cmd_plan_candidates` reads the board a *second* time — this is
     what `kanban-board-sync/SKILL.md`'s own step 3 tells the operator
     to run), `:275` (help text), `:231-236`/`:302-303`
     (`decompose-plan --parent-issue`'s rationale is the track marker).
   - `sources_plan.py`'s `list_plan_candidates(plans_dir,
     active_tracks_board_text)` signature itself changes (the board text
     is a parameter, not just referenced at lines 23-30) — and its glob
     needs an explicit exclusion pattern for the 24 review-companion
     filenames plus `README.md`, none of which should become "plan
     candidates" (today it returns 59 candidates including all of them —
     verified by running it live).
   - `labels.py:68`'s `SYNC_MARKER_KIND_TRACK`, the `<!-- autotrade-sync:
     track:A -->` markers in `#75`-`#77`.
   - Tests: `tests/test_kanban_sync_sources_tracks.py` (whole file, 15
     tests), `test_kanban_sync_main.py:391-412` (exercises
     `sources="track"`), `test_kanban_sync_sources_plan.py:6,20` (writes
     a fake board file), `test_kanban_sync_labels.py:28` (asserts the
     track marker kind — missed by both prior rounds), and the `kind="track"` fixtures in
     `test_kanban_sync_sync.py`/`_markers.py`/`_models.py` (these can
     stay only if `SYNC_MARKER_KIND_TRACK` is kept — it isn't).
   - `checkpoint/SKILL.md:69-70`'s `sync --sources worktree,roadmap,track`
     line (**corrected file — round 1 and round 2 both cited
     `kanban-board-sync/SKILL.md:69`, which is wrong; that file's own
     sync reference is at its own `:31`, uncorrected until now**; its
     `:3` and `:36` also name the track source and were listed in round
     2 but dropped in the rewrite — restored),
     `tools/quality_coordination.py:71` (names an "active-tracks-
     board.md note" as a suppression source), `ROADMAP.md:66`'s link,
     and `docs/kalshi-personal-production-execution-program-2026-08-
     26.md:995` (**missed by both prior rounds** — this line currently
     defers *to* the board as "the authoritative live status tracker...
     rather than this section's own stale requirements below"; since §3
     names this doc as the sole surviving owner of production-sequencing
     gates, `:995` must stop deferring to a file about to move into
     `docs/archive/`).
   - Cosmetic: `tools/kanban_sync/__init__.py:2-3`, `sync.py:279-282`
     docstrings name the board/`sources_tracks.py`.
4. **Move files, in lane-sized batches**, review companions atomic with
   their named parent, closing/re-pointing the **6** open `Plan:` issues
   and **76** open `type:plan-task` sub-issues that reference moved
   paths (dated 2026-09-06 PM; drop the unearned "verified count"
   framing round 2 used for numbers that were actually just carried
   forward). Path-reference fixes: **260 references outside `plans/`
   tracked by `git grep`, 118 of those inside `services/tools/tests/
   .claude/hooks/main.py`** (re-derived directly; round 2's 378/111 does
   not reproduce from tracked files under any method found — the order
   of magnitude and the "far bigger than PR #635's 8-file/5-reference
   scale" framing both still hold). Archive target: `docs/archive/`
   with per-lane subdirectories, not `docs/archive-2026-08-27/` (that
   name is specific to the PR that already used it).
5. **`docs/superpowers/plans/README.md`: retire, not regenerate** — no
   generator tool exists for it (it's hand-written); "regenerate"
   implied a tool that isn't there. Decision, stated plainly: retire it
   in favor of `lane:*` + the checked-in classification table (step 1)
   being the source of truth for "what plans exist."

## 7. Branch-vs-tracked-issue policy, exemption made real (fix #6)

Default to a tracked issue; a branch exists only for active in-flight
code. **The "parked by recorded decision" exemption now has an actual
record to check, not a dead-end lookup:** `feat/candlestick-volatility`
is added to `docs/open-decisions.md` as its own line (CLAUDE.md names
that file as the single list of parked decisions — round 2's exemption
pointed there but the line didn't exist, so the lookup found nothing).
Citation: `docs/branch-audit-2026-09-05.md`'s decision plus the actual
push (`git ls-remote origin` confirms `ad24098`), not the closed `#398`
comment round 2 cited, which is about a *different* branch. **The
exemption suppresses triage action, not AQC's signal** — `tools/
quality_coordination.py` still surfaces the branch as stale every
checkpoint (that's its own separate, correct job); the rule only stops a
session from *acting* on that signal for this specific branch without
re-checking the parked-decision line first.

## 8. Anti-drift / anti-bloat (fix #8, unchanged fixes carried from round 2, ref corrections applied)

1. Lane token in branch name — unchanged.
2. Check the lane for overlap before starting — `lane:N` labels are a
   day-one precondition (migration step 2), not an escalation.
3. Stale branch/worktree triage — pointer to `tools.quality_coordination`
   (already run at every `/checkpoint`), with the §7 exemption checked
   first.
4. Reconcile lane state regularly — pointer to `kanban_sync sync`
   (already run at every `/checkpoint`, `checkpoint/SKILL.md:69-70`).
5. This design's successor is the only way to add/rename/merge a lane.
   **A machine-readable `LANES` constant in `labels.py`** (lane number →
   name → package list) is the single source of truth for "closest
   primary fit," alongside the new `CONCERNS` dict (§3).

(Round 2's mislabeled "fix #6" reference in this section is corrected to
**fix #5** here — the tooling-touchpoints fix, not the Track C fix; "round
1 named none of these" specifically meant round 1's *design* doc, not
its review, which did name every touchpoint it found.)

## 9. Pre-built leverage (unchanged from round 2, confirmed accurate by the review)

`lane:*`/`concern:*` labels ship day one; `gh issue list --label lane:N`
is the day-one visibility answer. A GitHub Projects `Lane` single-select
field for board grouping is confirmed to require real (if bounded) new
work — `gh project field-list 3` shows only `Status` exists today — and
stays deferred until label-only visibility is shown to be insufficient.
Mermaid for the diagram; extend `tools/kanban_sync` rather than a
parallel tool.

## 10. Visualization deliverable (unchanged in shape)

Produced after this reaches GO: a Mermaid diagram of the 9 lanes, their
current initiative counts, the Lane→Initiative→Task hierarchy with one
worked example per lane, and `concern:hotpath` shown crossing lane
boundaries via its worked examples (#632/#636/#637). Live board view
decision follows §9.

## 11. Remaining citation corrections (fix #7)

146 open issues (dated 2026-09-06 PM — re-derived live, not by
subtracting from round 1's stale 155; the same-day closures include
`#585 #586 #629 #398 #488 #401`-`#406 #408 #322 #326`); 6 open `Plan:`
issues / 76 open `type:plan-task` sub-issues reference moved paths
(re-derived live, not carried forward — round 2's "10/86, verified
count" was the exact un-verified-carry-forward failure mode this
process exists to catch); 3 plans excluded by the board's text, not 4;
260/118 path references via `git grep` over tracked files, stated with
that method rather than round 2's unreproducible 378/111. `#326` closed
`COMPLETED` not decline-shaped — cosmetic, worth a follow-up `gh issue
close` state-reason correction (optional #11), not blocking this design.

## 12. Optional items noted, not blocking

- `services/reset/` has no package-level `__init__` docstring or README
  (its three modules do have docstrings — the earlier "cannot read"
  wording overstated it); kept in Lane 6, with a small follow-up to add
  a package README as part of Lane 6's own population work.
- `#326`'s `state_reason` correction (cosmetic GitHub bookkeeping).

## 13. What would still flip this to NO-GO (carried from the review, unchanged)

If any recheck of the fixes above turns out to actually require adding,
merging, or removing a lane — none of the fixes made here did that, all
were reassignments within the existing 9 — or if the step-1
classification tables (once generated and reviewed per §6 step 1)
reproduce this same inconsistency rate at their much larger scale,
meaning the straddler rule isn't yet mechanical enough to delegate.
