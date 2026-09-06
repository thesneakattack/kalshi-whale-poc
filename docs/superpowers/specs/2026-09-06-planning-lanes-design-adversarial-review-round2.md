# Adversarial review, round 2: planning-lanes design

Date: 2026-09-06. Stage 2 of the required cycle for round 2. Independent
Agent call with no memory of either authoring conversation. Every
load-bearing claim below was re-derived from the working tree, `git`,
and `gh` on 2026-09-06 (afternoon, after `#322`/`#326`/`#488`/`#401`-
`#408` closed at 13:36Z). Nothing was accepted from round 1's review,
round 1's consolidation, or round 2's self-review on faith; where a
number matches round 1, that is because I got the same number, not
because I copied it.

Reviewed: `2026-09-06-planning-lanes-design.md` (round 2) and
`...-design-self-review.md` (round 2, stage 1), against the merged
12-item fix list in `...-design-consolidation.md`.

## 0. Method

- **Table membership:** `ls services/` (66 units) diffed by hand against
  every package/file named in round 2's §2 table; `ls` of
  `index_feed/`, `whale_stream/`, `config/`, `whalewatchers/`,
  `.claude/hooks/`, `.github/workflows/`, `docs/superpowers/*.md`,
  top-level dirs. Every unit's module docstring (`ast.get_docstring`)
  or `README.md`/`CHEATSHEET.md` read for the straddler test — the 9
  round-1 units plus 26 others.
- **Lane 5 / PR evidence:** `gh pr view <n> --json files` for #624
  #625 #627 #630 #632 #636 #637, each file mapped against round 2's
  table (not round 1's).
- **Realtime plan primary lane:** `grep -o | wc -l` over
  `2026-08-25-realtime-data-plane-remediation.md` under three counting
  methods (full `services/…` path, bare filename, backticked name).
- **Projects/labels:** `gh project field-list 3 --owner thesneakattack`;
  full read of `tools/kanban_sync/labels.py`; `gh label list`.
- **Migration touchpoints:** full read of `tools/kanban_sync/__main__.py`
  and `sources_plan.py`; `grep -rn` for `track`, `sources_tracks`,
  `ACTIVE_TRACKS_BOARD_PATH`, `active-tracks-board` across `tools/`,
  `tests/`, `.claude/skills/`, `docs/`; `list_plan_candidates()` run
  live against the real board text.
- **Track C / decisions / branch policy:** `grep` ROADMAP.md, the
  execution-program doc, the board's own headings and lines 142-237;
  full read of `docs/open-decisions.md`; `docs/branch-audit-2026-09-05.md`
  §2; `gh issue view 398 --json comments`; `git ls-remote origin`.
- **Counts:** `gh issue list --state open --limit 500`, `--label`,
  `--search`; `ls`/`git ls-files`/`git ls-tree <#635 merge>^1` on each
  docs dir; `git grep` vs `grep -rn` for path references; `git worktree
  list` + `git status` in the two `#576` prototype worktrees.
- **Internal refs:** every `§N`, `fix #N`, and `file:line` token in
  round 2 extracted with `grep -o` and resolved by hand.

## 1. Confirmed fixed (independently re-derived)

| Fix | Evidence |
|---|---|
| #1 Lane 5 is a package list, not a property | 16 named files; all 16 exist in `services/`. The false "#150/#530/… all live here" sentence is gone and its falsity is stated in the row. `concern:hotpath` is introduced as a label, not a lane. |
| #1 the 7 PRs under the new table | #624 `backtest/routes.py`→L4; #625 `main.py`→unowned, tagged by wiring; #627 `diagnostics/_aio_db.py`→L6; #630 `observability/*`→L6; #632 `db/fault_log/loop_watchdog`→L5; #636 `analytics/routes.py`+`candidate_log.py`+a `docs/*.md`→L4+L3+L9; #637 `whale_stream/index_stream_handlers.py`→L1. Every file lands in a real lane; `concern:hotpath` correctly names their shared trait without being a lane. (#636's three-lane spread is a separate finding, §2.C.) |
| #2 `index_feed/` split | `ingestion.py` docstring: "tick capture"; `settlement_algebra.py`: "settlement-projection math… the other half". Split at the existing file boundary, as the rule says. |
| #2 `settlement_edge.py`→L4 / `_entry.py`→L3 | Both docstrings say so verbatim ("never trades… records and scores" / "the deliberate, separate 'act on it' half"). |
| #2 `game_state.py`→L1, `series_evaluator.py`→L1, `candidate_retry.py`→L2, `kalshi/websocket.py`→L1, `kalshi_trade_tape.py`→L2 | Each docstring's first sentence names the assigned lane's concern. Re-applying the rule, I get the same answer for all five. |
| #2 `market_analyst_agent/`→L4 | Package docstring: "Deliberately advisory-only… never calls create_order… nothing here is wired into strategy_engine.evaluate()". By the rule *as stated* (docstring wins), L4. See §2.A.2 for the rule's gap this exposes. |
| #2 omitted units added | `history_push.py` (docstring: "The History tab's event-driven push mechanism") →L8 fits. `.claude/hooks/` exists (6 files), `bench/`, `ui_samples/`, `scripts/`, `tests/`, `.github/workflows/` (5 files), the 5 loose `docs/superpowers/*.md` — all exist and all now appear in L9. `config/settings.yaml` explicitly L7. |
| #4 Projects claim | `gh project field-list 3`: 15 fields, exactly one `ProjectV2SingleSelectField` (`Status`). `labels.py:39-46` says what round 2 quotes. "Decision: defer" is stated with a trigger. Matches reality. |
| #5 order | table → label → fix tooling → move in batches → README. Correct order; the crash-before-move risk is addressed *in principle* (but see §2.D for the touchpoint list). |
| #6 Track C | `grep 'Program 3R\|Program 4\|Program 8\|Stages A' ROADMAP.md` → nothing. `ROADMAP.md:66` links the board. Execution-program doc `:861` is `## Program 3R`. Board `:142-157` holds the gate table. Round 2's statement is correct. |
| #6 standing decisions | Board `:220-237`: 1 checked + 5 unchecked items. `docs/open-decisions.md` (read in full, 42 lines) contains none of the 5. "Port, checking for duplicates first" is correct and the duplicate count is zero. |
| #7 board path | Round 2 `:17`, `:78` = `docs/superpowers/plans/2026-08-26-active-tracks-board.md`, which exists. `docs/next-action.md:346` = same path. `:208` uses the bare filename. No wrong path survives. |
| #8 rule 2 day-one, rule 3/4 as pointers, rule 5 `LANES` | All stated. `checkpoint/SKILL.md:69-70` really does run `kanban_sync sync` and `tools.quality_coordination`; `quality_coordination.py:66-76,182+` really does compute last-commit age / PR state / worktree presence per branch. The pointers point at real code. |
| #9 (partial) | 67→47 top-level docs (`git ls-tree 80f8abb^1 docs/` = 67; `ls docs/*.md` = 47). 97 research `.md` + exactly one `.py`. 63 `plans/*.md` = 62 dated + README; 24 match `-(review\|self-review\|consolidation)\.md$`. 79 tracked specs (working tree 83 = +4 untracked from this cycle). 34 files match `docs/*2026-09-03*.md`. `#576` CLOSED 2026-09-05T20:07Z; both prototype worktrees at `023705d` with ` M services/kalshi/websocket.py`. `#377` CLOSED 2026-09-01. `#322`/`#326` CLOSED 2026-09-06T13:36Z by `thesneakattack` with "Verified before closing" comments — round 2's "a peer closed them" is true. `#634` created 04:07Z. 9 open issues carry `area:*` (7/1/1). `docs/open-decisions.md:36` is the weather-index decline. |
| #10 | `main.py` rule is its own paragraph with a stated criterion. Archive target is `docs/archive/` with per-lane subdirs; `archive-2026-08-27/` appears only as the thing being avoided. `lane:*` supersedes `area:*` stated. |
| #11 | No `§0b`; no "see §3" pointing at §2. Every `§N` in round 2 resolves to a real section. (Two mislabels remain — §2.F.) |
| #12 | `plans/README.md` regeneration is numbered step 5 of §4. Its header really says "22 plans". |

## 2. Still wrong, or newly introduced

### A. The straddler rule is stated once but applied three ways; re-application changes ≥1 of the 9 and ≥7 units outside the 9

Round 1's stated flip condition: "a revised table where each of the
nine units above is placed by a stated rule I can re-apply and get the
same answer." I re-applied "primary declared purpose = what its own
docstring/README says it exists to do" to every `services/` unit.

**1. `candidate_log.py` → L3 is not the rule's answer.** Its docstring:
"This does NOT act on rejected candidates (no trade is ever placed from
this module) - it only observes", and it exists because "advisory_engine's
own entry-threshold/longshot recommendations return None… this module is
what that data would come from." That is word-for-word the criterion
round 2 used to put `settlement_edge.py` in L4 ("never trades… records
and scores"). Round 2 instead assigns it L3 with a *different* rule —
"owned by its writer" — which round 1 §3.2 offered as an *example* of a
possible rule, not the one round 2 adopted. Two structurally identical
files, two rules, two lanes. And the writer claim doesn't resolve to L3
uniquely anyway: the docstring names `whale_watcher_kalshi.min_contracts`
(an L2 detection gate) among its writers; `kalshi_trade_tape.py` references
it 6 times; and since the writer-thread work the durable write of
`candidate_log.db`'s `rejection_events`/`rejected_candidates` stores is
done by `capture_writer.py` (L5) — `capture_writer.py:94-95,112,178-191`
own those stores' DDL and INSERT. Under the stated rule: **L4**.

**2. `market_analyst_agent/` → L4 is right by the letter of the rule and
the rule has no clause for a docstring the repo already knows is stale.**
`_db.py:93` documents `analyst_lean()`'s "call from the whale-scoring hot
path"; `kalshi_trade_tape.py:212` makes that call on every real print; and
`origin/feat/candlestick-volatility` tip `ad24098` is titled "correct
per_market.py docstring to remove stale 'advisory-only' label". The rule
says the docstring wins. It needs one sentence for the case where the
docstring is contradicted by the code (code wins; the docstring is a
defect to fix in the same PR), or every future stale docstring silently
mis-lanes a file.

**3. `services/series_watcher.py` is in no lane.** It is the only one of
the 66 `services/` units absent from the table (I diffed the full
listing). Its docstring is itself a two-half straddler: "captures the raw
exchange data this app otherwise throws away" (L1) "then reconciles 'how
often were the whales right' against 'how often did I actually win'"
(L2/L4). The self-review said it did not look for more straddlers; this
is the first one `ls services/` turns up.

**4. `market_history.py` → L4 contradicts the `game_state.py` → L1
precedent.** Docstring: "Real Kalshi market data, logged over time…
Settlement outcomes (real, from Kalshi's market.result field)". Round 2
moved `game_state.py` into L1 on exactly the criterion "Kalshi-sourced
market-data persistence". Re-applied: **L1**. (Its second job,
`compute_hypothetical_trades()`, is L4 — but the rule says primary
purpose, and the docstring's own ordering puts capture first.)

**5. `market_lookup.py` → L4: the docstring explicitly refuses to name a
primary owner.** "Shared by position management…, history/analytics…,
and main.py's own _handle_signal - extracted into its own module… rather
than owned by any one of those, since all three already depended on it."
The rule as stated yields no answer; L4 is one of three equally-supported
picks and the row gives no reason. This is the self-review's "what if
there's no docstring" worry in a sharper form: a docstring that
disclaims ownership. The rule needs a tiebreak (e.g. shared pure helpers
over `app_state` → L5 with `state_view.py`/`app_state.py`, which is
where the docstring's own "already-in-memory services.app_state.state"
points).

**6. `whale_stream/index_stream_handlers.py` → L1 "transport-level only"
is false against the file.** Lines 207-250: `_record_settlement_observations`
calls `settlement_edge.record_observation` (L4 measurement), then
`settlement_edge_entry.evaluate_entry(…, cfg, broker, risk)` and
`decision_bridge.handle_settlement_edge_entry(decision, …)` — an L3
*entry decision* taken on the index-tick callback. Docstring: "feeding
settlement-edge observation capture". PR #637 (round 2's own L1 example)
is precisely about moving `settlement_edge.resolve_window()` inside this
file. The qualifier "transport-level only" was added to make the row
fit; the file is the same shape as `whale_stream_handlers.py`, which the
`whale_stream/` CHEATSHEET already flags as a deliberate cross-boundary
call. Either the row drops the qualifier and accepts L1-by-primary-purpose
with L3/L4 as declared dependents, or the boundary is drawn inside the
file — but not "transport-only".

**7. `config/config_performance.py` → L7 by directory, L4 by docstring.**
"Config-variant fingerprinting and the audit trail for advisory-engine
config changes… so services/advisory/advisory_engine.py can score each
config variant". The rule's own first clause — "where an internal
module/file boundary already exists, draw the lane boundary there" —
applies (it is its own file) and was not applied.

**8. `ws_manager.py` → L5 vs `history_push.py` → L8 is an inconsistent
pair.** `ws_manager.py`: "The dashboard's own push-update websocket
connection registry (GET /api/ws in main.py)". `history_push.py` is
"Modeled on services/ws_manager.py's own extraction shape" and imports it
7 times. Round 2 put the copy in L8 for "feeds the dashboard's push" and
the original in L5. Same criterion → same lane.

**9. Telemetry primitives are in L2 and L5, not L6.** `whale_pipeline_perf.py`
(→L2): "Stage-by-stage timing and counters for the whale-trade pipeline".
`latency_agg.py` (→L5): "Bounded, allocation-free latency accumulators
for hot-path telemetry… Design constraints (services/observability/README.md
'Hot-path impact')". Both are observability by declared purpose; the
table has an Observability lane and neither is in it.

**10. `data_quarantine.py` → L5.** Docstring: "Non-destructive protection
of the analytics dataset from deliberate test windows" — that is L4 (the
dataset) or L6 (quality & safety), not runtime infrastructure.

**11. Borderline, rule gives no confident answer:** `kalshi_fees.py` → L3
("Real Kalshi taker-fee formula" — `.claude/rules/kalshi-integration-authority.md`
names "fee/rate-limit rule" as Kalshi contract behavior, i.e. L1-shaped,
while its consumers are all L3); `mutual_exclusivity.py` → L3 (detects a
Kalshi event-structure property, consumed by strategy). I would not fail
the table on these two, but they show the rule needs a "contract
semantics vs. consumer" tiebreak.

**12. Cosmetic, carried unfixed from round 1 §2.A:** Lane 7 still lists
`config_store.py`, `config_bounds.py`, `config_overrides.py`,
`config_performance.py` as if they were top-level `services/*.py`; all
four are inside `services/config/`, which the same row lists separately
with only `routes.py`, `config_paths.py`.

Tally: of the 9 round-1 units, 8 re-derive to the same lane and 1
(`candidate_log`) does not. Outside the 9: 1 omission, 6 units where the
stated rule gives a different lane (items 4, 6, 7, 8, 9×2, 10), 1 where
it gives none (5), 2 borderline. Lane 5, the lane round 2 rebuilt, holds
three of them (`ws_manager`, `latency_agg`, `data_quarantine`).

### B. The multi-lane-initiative rule's metric is undefined and its numeric claim is false

Round 2: "primary lane — the lane owning its single most-referenced
file… `kalshi/websocket.py` at ×27 is nearly 3x the next file".

Re-counted three ways over the realtime plan:

| method | top entries |
|---|---|
| full path `services/…` | `main.py` 49, `services/kalshi/websocket.py` **27**, `services/observability/` **18**, `kalshi_trade_tape.py` 10, `http_client.py` 9, `whale_stream_handlers.py` 8, `exits/` 8 |
| bare name | `capture_writer` **54**, `main.py` 49, `loop_watchdog` **43**, `whale_stream` 32, `kalshi/websocket.py` 28, `candidate_retry` 28, `observability/` 26, `http_client.py` 21 |
| backticked filename | `websocket.py` 16, `http_client.py` 10, `kalshi_trade_tape.py` 8 |

"Nearly 3x the next file" holds under none of them: full-path gives 27
vs 18 (1.5x; round 1 listed `observability/` at ×8 and round 2 compared
against `kalshi_trade_tape.py` ×10, skipping it); bare-name puts two L5
files *above* `websocket.py`; `main.py` (unowned) beats it under every
method. The **conclusion** (Lane 1) still stands — but on the plan's own
Goal line ("event-loop stalls and class-blocking on the WebSocket and
REST planes"), i.e. by declared purpose, not by a count nobody specified
how to compute. The rule should say that, which also makes it the same
principle as the straddler rule at initiative granularity (see §4.2).

The rule also does not degrade to small initiatives: PR #636 touches
`candidate_log.py` (L3), `analytics/routes.py` (L4), and a `docs/*.md`
(L9) once each. "Single most-referenced file" is a three-way tie; the
design's "every branch… exactly one lane" has no answer for tonight's
own open PR.

### C. `concern:*` and the cross-lane reference have no concrete form

- "labelled with both `lane:N` for the owner and a reference to the
  dependent lane" — a second `lane:M` label would violate exactly-one; a
  prose mention is unqueryable. The design must pick (a `depends-on:#N`
  to an owner-lane issue, a distinct `xlane:M` family, or a body marker).
- `concern:hotpath` is not in `labels.py`, not in the proposed `LANES`
  constant, and has no stated applier. The repo already shows what an
  unanchored family does: 8 `area:*` label *definitions* exist
  (`gh label list`: architecture, frontend, kalshi-integration, misc,
  production-readiness, realtime, strategy, workflow-tooling), and `#75`-
  `#77` still carry `phase:implementation-plan`, a name `labels.py:31-38`
  says was renamed away on 2026-08-27. "`area:*` is not used going
  forward" without deleting the 8 definitions repeats that.

### D. Migration touchpoints — the list is incomplete and contains a wrong file citation; a second live crash path was missed twice

Round 2 says "All 8 get fixed in the same PR". Re-derived from source,
the list is missing:

1. **`__main__.py:224`** — `_cmd_plan_candidates` calls
   `ACTIVE_TRACKS_BOARD_PATH.read_text()` a *second* time. This is the
   `plan-candidates` subcommand that `kanban-board-sync/SKILL.md` step 3
   (line 35-36) tells the operator to run. Same `FileNotFoundError`
   class round 1 found at `:134`; both rounds cited `:37` and `:134`
   only.
2. **`__main__.py:26`** — `from tools.kanban_sync.sources_tracks import
   parse_track_items`. Deleting the module makes *every* subcommand
   (`sync`, `push-status`, `backfill-status`, …) fail at import, before
   any path is read. Also `:39` (`KNOWN_SOURCES` includes `"track"`),
   `:3` (docstring), `:275` (help text), `:231-236`/`:302-303`
   (`decompose-plan --parent-issue` rationale is the track marker).
3. **Tests:** `tests/test_kanban_sync_sources_tracks.py` (15 tests —
   ImportError = CI red the moment the module goes),
   `tests/test_kanban_sync_main.py:391-412` (exercises `sources="track"`),
   `tests/test_kanban_sync_sources_plan.py:6,20` (writes a fake board
   file to test `list_plan_candidates`), plus `kind="track"` fixtures in
   `test_kanban_sync_sync.py`, `_markers.py`, `_models.py` (these can stay
   only if `SYNC_MARKER_KIND_TRACK` stays — which round 2 lists as a thing
   to retire).
4. **`sources_plan.py`'s signature** `list_plan_candidates(plans_dir,
   active_tracks_board_text)` — the board text is a parameter; retiring
   the board changes the function, not just lines 23-30.
5. **Wrong file:** round 2 (§2 tooling paragraph and §6 rule 4) cites
   "the `checkpoint` skill's `sync --sources worktree,roadmap,track` line
   (`kanban-board-sync/SKILL.md:69`)". That line is
   **`.claude/skills/checkpoint/SKILL.md:69`**. `kanban-board-sync/SKILL.md`'s
   own sync line is `:31` (not listed); its `:3` and `:36` are listed
   correctly. Round 1 wrote the ambiguous "SKILL.md:69"; round 2
   resolved the ambiguity to the wrong file.
6. **`docs/kalshi-personal-production-execution-program-2026-08-26.md:995`**:
   "see `…active-tracks-board.md`'s Track C table, which is the
   authoritative live status tracker for this program going forward
   rather than this section's own stale requirements below." Round 2
   makes this doc "the sole stated owner of production-sequencing gates";
   it currently defers *to the board* and calls itself stale. Retiring
   the board without editing `:995` leaves the new sole owner pointing at
   a file in `docs/archive/`.
7. Cosmetic: `tools/kanban_sync/__init__.py:2-3`, `sync.py:279-282`
   docstrings name `sources_tracks.py`/the board.

Also in §2/§4:

- "the board's text currently excludes its **4** referenced plans" —
  running `_PLAN_DOC_REF_RE` on the real board text: **3** (realtime
  remediation, economic-strategy-effectiveness-investigation,
  economic-strategy-remediation). Carried from round 1 unverified.
- `list_plan_candidates()` run live returns **59** candidates, including
  **all 24 review-companions and `README.md`** (`plans_dir.glob("*.md")`,
  `sources_plan.py:29`). Round 2's "companions move WITH their parent,
  never counted separately" is a file-move rule; the tool counts them
  today and the design never says to change the glob or the
  classification-JSON handling.
- One companion has no parent by filename:
  `2026-09-03-persistence-layer-task8-candidate-ledger-self-review.md`
  (23 of 24 map cleanly). "Move with parent" needs the parent named for
  that one.
- "10 open `Plan:` issues, 86 open `type:plan-task` sub-issues… **verified
  count, not estimated**": live, **6** open `Plan:` issues (#89 #331 #321
  #81 #448 #320), 9 open `phase:plan` (incl. #75-77), **76** open
  `type:plan-task` (all 76 bodies reference `docs/superpowers/plans/`).
  `#488` and `#401`-`#406`/`#408` closed at 13:36Z today. The phrase
  "verified count" attached to a number that was copied is the exact
  failure mode round 1's consolidation named.
- "~378 in-repo path references (111 inside services/tools/tests/
  .claude/hooks/main.py…)": `git grep` over tracked files gives **260**
  outside `plans/` (**118** in that file set; 3 in `.github/workflows`
  matches). Neither 378 nor 111 reproduces from tracked files; the order
  of magnitude and the "75x PR #635" framing survive.

### E. §5 exemption does not work for the branch it was written for

Round 2: a parked branch is exempt "checked against `docs/open-decisions.md`
and any linked decision record before triaging".

- `grep -i candlestick docs/open-decisions.md docs/next-action.md
  CLAUDE.md` → **zero hits**. The file CLAUDE.md names as "the single
  list of parked decisions" does not know the branch is parked.
- The decision exists in exactly two places: `docs/branch-audit-2026-09-05.md:53-57`
  (a dated top-level `docs/*.md` that round 2's own Lane 9 slates for
  `docs/archive/`), and a comment on **`#398`** — an issue about a
  *different* branch (`feat/frontend-realtime-push`), **CLOSED
  2026-09-06T03:25Z**, whose closing comment says "Separately,
  `feat/candlestick-volatility` has now been pushed to origin per the
  same decision (5 commits preserved, stays unmerged)."
- `git ls-remote origin` confirms the push (`ad24098`); a
  `.claude/worktrees/candlestick-volatility` also exists.
- `quality_coordination.py:71`'s own listed suppression source is "an
  explicit 'paused, not stalled' active-tracks-board.md note" — the file
  being retired.

So the exemption's lookup path finds nothing, the only records are one
archival-bound doc and one closed issue about another branch, and the
tool's own suppression hook points at the board. The fix is one line in
`docs/open-decisions.md` (that is where CLAUDE.md says it goes) and a
sentence that the rule suppresses *action*, not AQC's *signal* — the
branch will still print every checkpoint.

### F. Internal references

- §2 tooling paragraph: "**(fix #6**; round 1 named none of these)". The
  touchpoints are consolidation fix **#5** ("fix `kanban_sync`'s
  Track-retirement touchpoints"); fix #6 is Track C / standing decisions.
- "round 1 named none of these" is ambiguous: round 1's *review* (§2.B
  last bullet) named every one of them; round 1's *design* named none.
- `kanban-board-sync/SKILL.md:69` ×2 → `checkpoint/SKILL.md:69` (§2.D.5).
- Line 8 says "fix numbers in headers refer to it"; the fix-#6 tag above
  is in a bold paragraph, not a header — trivial.

### G. Numbers that drifted or were never re-derived

| Round 2 says | Actual (2026-09-06 PM) | Status |
|---|---|---|
| 155 open issues | **146** | 14 closed today (`#585 #586 #629 #398 #488 #401-#406 #408 #322 #326`). Self-review admitted not re-running the count. |
| 10 open `Plan:` / 86 `type:plan-task` | **6** / **76** | Labelled "verified count". |
| 4 board-referenced plans | **3** | |
| ~378 / 111 path refs | **260** / **118** (tracked) | Method not stated; not reproducible. |
| 13 of last 40 branches without an issue number | 12 (28/40 carry one) | Immaterial. |
| `#326` closed | closed, `stateReason: COMPLETED` | Round 1 asked for a decline-shaped reason; cosmetic. |

Everything else in §1's #9 row re-derived exactly.

### H. Step 5 "regenerate" has nothing to regenerate with

`grep -rln 'plans/README.md\|Plans index' tools/ scripts/ .claude/` →
only worktree copies of the README itself. The file is hand-written
("22 plans, ~22,300 lines", two prose gotcha sections). "Regenerate or
retire" is (a) an undecided either/or and (b) "regenerate" implies a
tool that does not exist. Name the decision (retire in favour of
`lane:*`, per §4 step 5's own last clause) or name the hand-edit.

## 3. Missing

1. A "docstring contradicted by code" clause in the straddler rule
   (§2.A.2) and a tiebreak for docstrings that disclaim ownership
   (§2.A.5) or that are absent (`services/reset/` has no `__init__`
   docstring and no README; only `routes.py`'s route list).
2. `series_watcher.py` in the table.
3. A concrete form for the cross-lane reference and for `concern:*`
   (§2.C), plus deletion of the 8 `area:*` definitions and the stray
   `phase:implementation-plan` on `#75`-`#77`.
4. The full touchpoint list (§2.D items 1-7) — in particular `:224`,
   `:26`, the test files, and execution-program doc `:995`.
5. What `list_plan_candidates` does with companions and README once the
   board text parameter is gone (§2.D).
6. An `open-decisions.md` line for `feat/candlestick-volatility`, or the
   exemption cannot be exercised (§2.E).
7. A statement that the step-1 classification tables are themselves a
   reviewed artifact, not a subagent output accepted on completion — my
   sweep of 66 `services/` units found 1 omission and 7 rule
   inconsistencies; 62+97+79 doc rows will find more of the same shape.
   The self-review's own worry (a partial table now would have caught
   more) is correct; `services/` alone proves it.
8. What lane a PR/branch gets when it legitimately touches two lanes'
   files in one atomic fix (#636). The straddler rule handles *files*;
   the multi-lane rule handles *plans*; nothing handles a 3-file PR.

## 4. Independent judgment

**1. Is the straddler rule sound and reapplicable?** The rule is sound
as a *principle* — "declared purpose, not everything it touches" is the
right cut, and 8 of the 9 hard cases re-derive cleanly. It is not yet
reapplicable as *practice*, for two reasons I can demonstrate rather than
argue: (a) round 2 itself applied it inconsistently in at least four
pairs (`settlement_edge`/`candidate_log`, `game_state`/`market_history`,
`history_push`/`ws_manager`, `latency_agg`+`whale_pipeline_perf` vs the
Observability lane), and (b) I applied it to two files it never named —
`market_lookup.py` (docstring disclaims an owner → no answer) and
`config_performance.py` (own file inside a package whose lane differs →
the rule's first clause says L4, the table says L7) — and got a clean
answer for one and none for the other. A rule whose author gets
different answers on identical inputs is not yet mechanical enough to
hand to a subagent for 238 doc rows, which is what §4 step 1 proposes.

**2. Two rules or one?** They solve different problems (static file
ownership vs. per-initiative primary), so having two is not a smell by
itself. But the second rule's only novel content is its metric, and the
metric is undefined (§2.B). Restate it as "the primary lane is the lane
of the subsystem the initiative's own Goal/spec names as its subject"
and it becomes the straddler rule at initiative granularity: one
principle (declared purpose), two scopes (file, initiative), and it
degrades correctly to a 3-file PR (the PR title says what it's for).
That is cleaner and it removes a metric nobody can compute the same way
twice.

**3. Lane 5's redefinition.** Package-bounded now, genuinely. But the
membership was assembled from round 1's "prerequisite infrastructure"
list plus six more files without running the rule on them, and three of
those six (`ws_manager`, `latency_agg`, `data_quarantine`) fail it. The
lane is right; its edges were not checked.

**4. `concern:hotpath`.** The right mechanism. It is exactly the thing
CLAUDE.md's data-plane rule needs a handle for. It just needs an anchor
in `labels.py` and an applier, or it becomes `area:*` #2.

**5. Deferring the Lane Project field.** Agree with the deferral, and
the stated trigger is concrete enough. The open question in §10 (does
"I want to see the lanes" need the board on day one) is David's, not a
reviewer's; `gh issue list --label lane:N` is a real day-one answer.

**6. On the migration.** The order is now right and I would not reopen
it. What is not yet safe is executing step 3 from round 2's touchpoint
list: it would delete `sources_tracks.py`, leave `__main__.py:26`
importing it, and break every `kanban_sync` subcommand at import — the
checkpoint skill's routine sync included — before anyone reaches the
crash paths round 1 found.

## 5. On the self-review

- Its item-by-item fix-list check is real and better than round 1's;
  every ✓ it claims is at least *present* in the text.
- Its four "did not do" admissions were all load-bearing: the count
  drifted (146), the unchecked units contain a missing one and seven
  inconsistencies, and the straddler-rule edge case it worried about
  (`market_lookup`) exists.
- Its "did not verify `.claude/hooks/`, `bench/`, `ui_samples/`" — all
  three exist; that trust happened to be safe.
- Its "two rules — smell?" question: answered in §4.2 (not a smell; the
  second rule's metric is the problem).
- Its verdict "ready for adversarial review" was correct; its implied
  verdict that the 9 assignments are stable under the rule was not (1
  of 9 fails).

## 6. Verdict: GO-WITH-REQUIRED-FIXES

Nothing found changes the lane *list* — no lane needs adding, merging,
or removing — and round 2's own §6 rule 5 draws the full-cycle line
there. The concept, priorities, migration order, Projects correction,
Track C correction, board path, archive name, `main.py` rule, and every
number I could re-derive except five are right. What fails is
assignment-level and enumerable, so per CLAUDE.md's own procedure this
is a fix-list recheck, not a round 3 — **with two conditions**: each
changed table row is re-derived against the rule by the rechecker (not
accepted on the revision's say-so), and if fixing §2.A pushes the author
to split or merge a lane, that is a scope change and it goes back to a
full cycle. Migration does not start on this GO; it starts when step 1's
tables exist and have been reviewed (required fix 9).

### Required fixes (all must land before the recheck signs off)

1. **Table, under one rule.** Re-assign `candidate_log.py` (→L4 by the
   stated rule, or restate the rule so `settlement_edge.py` moves with
   it — one criterion for both). Add `series_watcher.py`. Re-derive
   `market_history.py` (→L1 by the `game_state` precedent),
   `market_lookup.py` (state the tiebreak), `index_stream_handlers.py`
   (drop "transport-level only"; declare L3/L4 dependents),
   `config_performance.py` (→L4 by the file-boundary clause, or say why
   not), `ws_manager.py` (→L8 with `history_push.py`, or move both),
   `latency_agg.py`/`whale_pipeline_perf.py` (→L6, or state why
   telemetry primitives are not Observability), `data_quarantine.py`
   (→L4/L6). Fix Lane 7's listing. Each changed row cites the docstring
   sentence it rests on.
2. **Rule clauses.** Add: (a) code contradicts docstring → code wins,
   docstring fixed in the same PR (the `market_analyst_agent` case);
   (b) docstring disclaims/omits an owner → stated tiebreak; (c) a file
   inside a package whose declared purpose belongs to another lane is
   placed by its own docstring (the rule's first clause, made explicit).
3. **Multi-lane rule.** Replace "single most-referenced file" with
   declared purpose (the initiative's own Goal/spec subject); drop the
   "nearly 3x" sentence; state the small-PR case (#636).
4. **Cross-lane reference form** and `concern:*` anchoring: name the
   mechanism; add `CONCERN_HOTPATH` (or equivalent) to `labels.py`
   alongside `LANES`; delete the 8 `area:*` definitions and the stray
   `phase:implementation-plan` in the same labelling step.
5. **Touchpoints.** Add `__main__.py:26`, `:39`, `:224`, `:275`,
   `:231-236`/`:302-303`; `tests/test_kanban_sync_sources_tracks.py`,
   `test_kanban_sync_main.py:391-412`, `test_kanban_sync_sources_plan.py:6,20`
   and the `kind="track"` fixtures; `list_plan_candidates`'s signature;
   execution-program doc `:995`. Correct `kanban-board-sync/SKILL.md:69`
   → `checkpoint/SKILL.md:69` (both places) and add
   `kanban-board-sync/SKILL.md:31`. Say what the plan glob does with the
   24 companions + README, and name the parent for
   `…-task8-candidate-ledger-self-review.md`.
6. **§5 exemption.** Add the `feat/candlestick-volatility` line to
   `docs/open-decisions.md` (as CLAUDE.md requires), cite it instead of
   the closed `#398`, and state that AQC will still print the branch —
   the rule suppresses triage, not the signal.
7. **Numbers.** 146 open issues (dated); 6 `Plan:` / 76 `type:plan-task`
   (drop "verified count" or make it true with a date and command); 3
   board-referenced plans; 260/118 with the `git grep` method stated,
   or drop the precise figures for "hundreds".
8. **Refs.** "fix #6" → "fix #5" in the tooling paragraph; disambiguate
   "round 1 named none of these" (the round-1 *design*).
9. **Step 1 tables are a reviewed artifact.** State that the
   classification tables get the same self-review/adversarial/consolidation
   treatment before step 2 labels anything — §2.A is the evidence that a
   subagent applying "the stated rules row by row" will reproduce these
   inconsistencies at 4x the scale.
10. **Step 5.** Decide retire vs. hand-rewrite for `plans/README.md`;
    "regenerate" has no generator.

### Optional

11. Close `#326` with a decline-shaped `state_reason` (cosmetic).
12. Note `services/reset/` as the one package with neither an `__init__`
    docstring nor a README — the only unit the rule literally cannot
    read.

### What would flip this to NO-GO

Fix 1 requiring a new lane or a lane merge (e.g. a "Shared runtime
helpers" lane for `market_lookup`/`state_view`/`app_state`, or folding
telemetry into L6 by moving files rather than rows) — that is a lane-list
change and round 2's own §6 rule 5 sends it through the full cycle. Or
the step-1 tables, once generated, showing the §2.A inconsistency rate
at the doc level, which would mean the rule is not yet mechanical and
the migration cannot be delegated as §4 step 1 proposes.
