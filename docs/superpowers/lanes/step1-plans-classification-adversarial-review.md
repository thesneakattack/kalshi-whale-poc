# Adversarial review: step-1 plans classification

Date: 2026-09-06. Stage 2 of the review cycle design §6 step 1 mandates.
Independent Agent call with no memory of the authoring conversation. Every
load-bearing claim below was re-derived from the working tree, `git`, and
`gh` — never from the table's own summary, the self-review, or the design's
tables.

**Artifact reviewed:** `docs/superpowers/lanes/step1-plans-classification.md`
at `dcc2643` (HEAD at time of writing). The file changed under review:
I first read it at `2136a0c`; `dcc2643` ("retract the Lane 9 ruling")
landed directly on `main` mid-review and edited two rows' `flag` text
without touching their `lane` column or the summary counts. Findings below
are against `dcc2643`, with the delta called out where it matters (§D G3/G4).

Design read first, in full: `docs/superpowers/specs/2026-09-06-planning-lanes-design.md`
at `fdf9c45` (merged via PR #640, `8f4976a`). The table's header still says
the design is "**not merged to `main`**" at `f01fd59` — stale; `fdf9c45`'s
one-line change is Lane 1 cross-dependent text only and affects no row.

---

## A. The shared status vocabulary — FLAGGED for the other two tables

This section leads because the definitions have already been propagated to
the issues (147) and specs+research (79+97) tables. Verdict on the
vocabulary as a whole: **sound enough to keep, not sound enough to apply
unchanged.** It is not circular, and on this table it produces the right
answer for 36 of 37 plan rows. But it has three defects that will not stay
latent at the other two tables' scale, plus two that already surfaced here.

### A1. `stalled` and `never-started` overlap — REQUIRED FIX (all three tables)

- `stalled` — "partly shipped **or written-only**, no open tracker, no movement"
- `never-started` — "zero deliverables exist in the tree"

"Written-only" *is* "zero deliverables." A plan with no code and no tracker
satisfies both definitions, and no precedence is stated. This table never
hits that cell — all three `never-started` rows (#89, #81, #321) have open
`Plan:` issues, and the only `stalled` rows are the board and `README.md`,
neither of which is a plan. But the design's own §1 problem statement is
"research docs that never became specs, specs that never became plans" —
i.e. the specs/research tables are *mostly* written-only-with-no-tracker.
That cell will be the modal case there, and two builders will fill it
differently.

**Fix:** delete "or written-only" from `stalled`. Then the grid is clean:
zero deliverables → `never-started` (tracker or not); partial + tracker →
`active`; partial + no tracker → `stalled`; all → `done`; a recorded
decision (`declined`/`superseded`) overrides all of the above. State that
precedence explicitly.

### A2. "Deliverable" is undefined per document type — REQUIRED FIX (specs/research tables)

Every status is keyed on "deliverables verifiably present in source." For a
plan that means code. For a spec, is the deliverable the plan it should
spawn, or the code that plan produces? For a research doc, the spec? If the
other two builders read "deliverable" as "the doc itself exists," every
spec and research row is trivially `done` — the same vacuity this table
already exhibits for its 25 companion rows (§A5). If they read it as "the
downstream code," they need a research→spec→plan→code chain that mostly
doesn't exist, and `done` becomes unverifiable.

**Fix:** one sentence per table stating what counts as a deliverable for
that document kind. **Falsifier:** if the specs/research tables already
contain that sentence and it's consistent with this table's, this finding
is moot — I could not check (neither table is on disk or on any fetched
branch; `docs/step1-issues-classification` is at `2136a0c` with no table
file yet).

### A3. `done` was applied outside its own definition — REQUIRED FIX (row + definition)

`done` requires "every task's deliverable verifiably present ... or
explicitly closed as infeasible." `2026-08-26-economic-strategy-effectiveness-investigation.md`
is marked `done`, but the plan's own task table (read directly, `:26-28`
and `:241-260`) has **E8 "NOT DONE"** and **E9 "NOT DONE (assessed, not
built)"** — neither closed as infeasible (only E10 is). And an open tracker
exists: `#76 Track B — Economic strategy validity` is OPEN, the same shape
as `#75` which the table accepts as realtime-remediation's tracker. Under
the stated definitions this row is `active`, not `done`.

The author's reasoning ("the investigation concluded and produced its
remediation plan") is *correct in substance* — E8's gap became P2-4 and
E9's became P2-3 in the remediation plan — but it is not in the definition.
**Fix:** either add to `done`: "…or the initiative's own closing artifact
explicitly disposes of every remaining task (infeasible, descoped, or
handed to a named successor initiative)," and cite E12 + P2-3/P2-4 in the
row; or change the row to `active` citing #76. The clause is the better
fix — research/investigation docs in the other tables will hit this
constantly.

### A4. `active` — not circular, but "tracker" is undefined — REQUIRED FIX (definition only)

The self-review worried `active` is circular. It is not: "partly shipped"
is a source grep, "open tracking issue" is a `gh issue list`, "live
open-decisions line" is a file grep — three checks independent of the label.
What *is* missing is what counts as a tracker. This table used, without
saying so: the plan's `Plan:` issue, its `Task N:` sub-issues (#134/#136/
#140), a Track issue (#75), a topical issue (#616), and open-decisions
lines. Two builders will not converge on that set unaided.

**Fix:** "tracker = an open issue whose title or body names the document
(or is a sub-issue of its `Plan:` issue), or an open-decisions line naming
it or its issue." No time dimension is needed — `stalled` vs `active` is
"has a tracker," and the word "movement" in `stalled` should go (it
implies a staleness test the definition doesn't contain).

### A5. `superseded` vs `declined` — distinct, but the table and the ruling already disagree

The distinction is real on one test: **is the goal still unowned?**
`declined` (weather-index): goal unmet, nobody owns it, may reopen.
`superseded` (event-scoped-me-gate): root cause fixed by PR #298; residual
N-way scope re-homed as open issues. Anyone asking "is this goal still
open?" gets different answers, so a sixth status is defensible **if**
defined by that test. But:

- The table on disk (`dcc2643`) still says **`declined`** for the ME-gate
  row; the coordinator's self-review rules **`superseded`**. Two artifacts,
  two answers, before the other tables even land. **Fix:** pick one, write
  the definition, apply to all three tables.
- The ME-gate row's evidence is wrong on the citations: it names
  "#277/#289–293" as residual scope. **#277 is CLOSED** (it is the `Plan:`
  issue). **#289–#293 are OPEN**, on milestone "Unplanned," titled as this
  plan's Tasks 1–5 — including `#291 Task 3: The gate — replace me_complement
  with the event-scoped check`, the exact task the RETIRED banner says
  would regress shipped code. So "zero of its tasks will ever run" is
  contradicted by five open task issues bearing those tasks' names. That
  is a repo-hygiene defect (the issues need retitling or closing), not a
  classification error, but the row must cite it accurately.

### A6. Companion rows write `done` — label mismatch, RECOMMENDED FIX (all three tables)

25 rows carry `done` meaning "this review file is committed," in the same
column where `done` means "every deliverable shipped." The table documents
the difference in prose and separates the counts, so it is not wrong — but
it violates "a value means exactly what its label says." Companions should
write `inherits` (or `n/a`) in `status`, as they already do in `lane`.
Whatever is chosen must match across the three tables.

### A7. `stalled` for the board and `README.md` — gap in the table's vocabulary, not the design's

The design already disposes of both: the board is archived (§3 "Reconciling
Track A/B/C"), `README.md` is retired (§6 step 5). Neither is "stalled";
both are scheduled. G7/G8 are correctly flagged but mis-located: the
missing values are `kind: board` / `kind: index` and `status: retire`, and
they are missing from the *table's* vocabulary (G1), not from the design's
rule. Recommended: add them.

---

## B. Mechanical checks — all confirmed, one self-review claim refuted

| Check | Method | Result |
|---|---|---|
| Every `plans/` file appears exactly once | `ls` (63) vs table paths (63, 63 unique), `diff` | **IDENTICAL** |
| `kind` census | awk over column 2 | 25 `companion-of`, 37 `plan`, 1 `index` = 63 |
| Lane census | awk over column 3 | L1=8 L2=3 L3=5 L4=1 L5=3 L6=4 L7=0 L8=1 L9=13 = **38**; 25 `inherits` |
| Per-lane row lists | listed every row per lane (see below) | matches the summary table exactly |
| Status census over `plan` rows | awk | 27 done / 4 active / 1 stalled / 3 never-started / 2 declined = 37 |
| Companions by filename suffix | `grep -E -- '-(review\|self-review\|adversarial-review\|consolidation)\.md$'` | **24** |
| Companions by content | + `freshness-check.md` (Subject line names the plan; no tasks; "GO, with 5 corrections applied directly to the plan"; merged as PR #461) | **25** |

**38 vs 37 is not an inconsistency.** 62 dated = 25 companions + 37 plans;
38 lane-bearing rows = 37 plans + `README.md`. The table says exactly this
at its line 117. The self-review's conclusion that "one of the 38 lane
assignments is wrong" and "every lane count that included it is off by
one" is **false**: `freshness-check.md` already carries
`companion-of:2026-08-25-frontend-modularization.md` / `inherits` in the
table on disk, and Lane 8's single row is `2026-08-25-frontend-modularization.md`
alone. No lane is inflated. The self-review's arithmetic ("24 suffix
companions + 1 README, 38 remain") counted the suffix basis and then
assumed the table had used it — it hadn't.

**24 vs 25 (G2) — confirmed, with one wording correction.** The design's 24
was evidently suffix-derived, and `freshness-check.md` is a companion by
content. But the table says "the design defines companions by four
filename suffixes" — it does not; `grep -n suffix` over the design returns
nothing. The design only says "24 review-companion files." The finding
stands; the attribution should be softened to "the design's count matches
a suffix rule it never states."

Lane 1 rows verified: kalshi-integration ×3, realtime-investigation,
realtime-remediation, kalshi-category, weather-index, event-loop-fix1 (8).
Lane 9: aqc-investigation, active-tracks-board, autonomous-engineering-mode,
aqc-report-only, kanban-board-sync, aqc-workflow, backend-services-
modularization, kanban-sync-milestones, workflow-remediation, kanban-sync-
improvements, plugin-pilot, ci-pipeline-audit, README (13).

---

## C. Row spot-checks (24 rows, all lanes with rows, all five statuses)

Lane assignment was checked against the plan's own `**Goal:**`/title line
under §5 (subject, not file count) and §2(d) (first-stated). Status was
checked against source, `git`, and `gh` — never the plan's checkboxes.

| Row | Lane check | Status check | Verdict |
|---|---|---|---|
| kalshi-integration-dual-phase / phase-a / phase-c | Goals name "Kalshi semantic interpretation … integration boundary" → L1 ✓ | PR #3, #9 MERGED 2026-08-25; `services/kalshi/` present; `19ed74d`, `94bfbe1` exist; `tools/kalshi_census.py` present | ✓ done |
| frontend-modularization | Goal "dashboard frontend … Preact" → L8 ✓ | `frontend/src/js/{legacy,core,lib,charts,panels}` all absent; `package.json` has no `dependencies` block; #89 OPEN | ✓ never-started |
| realtime-data-plane-remediation | §5 assigns L1 explicitly ✓ | `_critical_queue`/`_consume_market` in `services/kalshi/websocket.py`; `trip_brake`/`on_loss_event`/`ws_state_verify`/`_connection_generation` 0 hits in `services/`+`main.py`; #134/#136/#140/#75 OPEN | ✓ active |
| autonomous-engineering-mode | Goal names `tools/autonomous_mode/` + skill → L9 ✓ | both absent; PR #42 is docs-only; #81 OPEN | ✓ never-started |
| aqc report-only (08-26) | Goal: observation series over `tools.quality_audit` findings — L9 defensible (final home `tools/`); L6 was arguable at authoring time (`services/quality_coordination.py` + API routes + scheduler tick) | rename chain verified: `services/quality_coordination.py` →R088→ `tools/quality_coordination.py` →R089→ `tools/quality_ratchet.py` (`6a387bb`, 08-26); `tools/quality_ratchet.py` present | ✓ done; lane is a judgment, noted |
| economic-strategy-effectiveness-investigation | title "Economic Strategy Effectiveness" → L3 ✓ | **E8 NOT DONE, E9 NOT DONE**, E10 infeasible, E12 DONE; #76 OPEN | **✗ `done` violates the stated definition** — see A3 |
| economic-strategy-remediation | title → L3 ✓ | `def population_gate_summary_banded` at `candidate_log.py:359` (row cites `:195,259,295`, which are comment/docstring mentions — imprecise, claim holds); open-decisions line 35 records D1 shipped PR #631; #616 OPEN; branches `feat/616-…` exist | ✓ active; citation nit |
| event-scoped-me-gate | title → `mutual_exclusivity.py` → L3 ✓ | RETIRED banner; PR #298 MERGED; **#277 CLOSED, #289–#293 OPEN** (row miscites) | status defensible; evidence wrong — see A5 |
| data-retention-pruning | Goal first-stated: backup tiers → L6 ✓ | PR #302 MERGED; `a3debd3` exists; `market_history.prune()` at `:466` | ✓ done |
| weather-index-ingestion | "index data ingestion" → L1 ✓ | `services/weather_index/`, `data/weather_index.db` absent; open-decisions line 38 "declined for now"; #331 OPEN | ✓ declined (precedence over tracker must be stated — A1) |
| whale-confidence-scoring-remediation | Goal names `whale_confidence_weights` → L2 ✓ | PR #388 MERGED titled "(Tasks 1-9)" of 16; #320 OPEN; open-decisions lines 39–41 live | ✓ active |
| tier0-live-incident-remediation | Goal first-stated `/api/health/pipeline` → L6 ✓ | `async def get_faults` with `asyncio.gather(asyncio.to_thread(…))` at `routes.py:577-588` (Task 7 ✓); `_current_fd_count` at `:102` (Task 9 ✓, `ecedade`); #448 OPEN; open-decisions line 42 **already carries the 2026-09-06 correction** — the table's "not fixed here" is now stale | ✓ active |
| claudesuperpower-plugin-pilot | "workflow/tooling config" → L9 ✓ | `enabledPlugins` = context7, dimensional-analysis, chrome-devtools only; #321 OPEN; open-decisions line 37 | ✓ never-started |
| self-feeding-loop-provenance | Goal "advisory/calibration auto-tuning loop" → L4 by first word ✓ (deliverable lives in L6's `services/quality/`, handled by §5) | `services/quality/evidence_provenance.py` present; `dbca2e1`, `16a807c` exist | ✓ done |
| strategy-edge-gate | Goal "the gate itself inside `_validate_entry_price`" → L3 ✓ | PR #502 MERGED "all 10 tasks"; `edge_gate_enabled: false` in `settings.yaml`; `strategy_engine.py:219` reads it | ✓ done |
| scoring-pool-candidate-retry-isolation | → L2 ✓ | `_candidate_retry_pool.py` present; `9b55c1b` | ✓ done |
| event-loop-blocking-fix1 | named functions in L1 files ✓ | `should_flush` in exactly the 9 files listed | ✓ done |
| event-loop-blocking-fix2 | Goal names `diagnostics/` → L6 ✓ | `_aio_db.py` present; `_diagnostics_pool.py` absent; `1f24571`; PR #420 MERGED | ✓ done |
| whale-scoring-connection-reuse | Goal's *first-stated subject* is "the write path can't sustain … nothing non-critical shares `tick_executor`'s pool" (L5); L2 rests on reading the numbered fixes as the subject list | `_scoring_pool.py` present; `3ff41d4`, `e3821d4` | ✓ done; **lane debatable (L2 vs L5)**, not wrong |
| ci-pipeline-audit-tier1-fixes | → L9 ✓ | `e546110`, `a624fa2`, `e5b43b2`, `042d124` all exist with matching subjects | ✓ done |
| kanban-sync-improvements | → L9 ✓ | `get_issue` at `github_client.py:282`; `classification` at `models.py:23` | ✓ done |
| entry-gate-me-pairing-and-netting | first-stated "entry-side bug … ME-pairing gate" → L3 ✓ | PR #298, #303 MERGED; `1aff28e`, `56ae320`; `fee_cost` at `account_positions.py:63,67` | ✓ done |
| quality-control-plane | first-stated "durable runtime services" → L6 ✓ | `76c9be1` "(QCP Task 22)"; all five packages present; plan's last heading is Task 22 | ✓ done |
| persistence-layer-db-migration | "db.py" → L5 ✓ | all 10 cited commits exist with matching Task numbers; PR #516 MERGED | ✓ done |
| backend-services-modularization / tier1-backend-hygiene | see §D G3/G4 | PR #101 / PR #500 MERGED; packages present | status ✓; lane unresolved after `dcc2643` |
| README.md | L9 ✓ | header "22 plans"; **24 data rows**; lists **four** post-08-30 plans (whale-confidence, kalshi-category, plugin-pilot, weather-index) — row says "except three" | minor evidence error |

All six open `Plan:` issues on GitHub (#89, #81, #320, #321, #331, #448)
map to rows the table marks `never-started`/`active`/`declined`; no `done`
row has an open `Plan:` issue. Consistent.

---

## D. The RULE-GAP register, adjudicated

| # | Table's claim | Adjudication |
|---|---|---|
| G1 | design defines no statuses | **Genuine.** See §A for what the invented definitions get wrong. |
| G2 | 24→25 companions | **Genuine**, wording fix per §B. |
| G3 | repo-structure plan: no lane owns it | **Genuine gap in the design**, and `dcc2643` made the row worse, not better — see below. |
| G4 | heterogeneous hygiene batch | **Genuine** (verified: tasks touch `risk_manager`/`shadow_mode` L3, `loop_watchdog`/`state_view`/`http_client`/`capture_writer` L5, `alerting`/`backup`/`diagnostics` L6, `config_store` L7, dashboard L8, plus L1/L4 files). Same defect as G3 after `dcc2643`. |
| G5 | subject L3 but "all five tasks land in Lane 4 files" | **Not a gap, and the evidence is wrong.** P2-2 names `services/diagnostics/capture_health.py` (L6); P2-3 names `series_watcher.py` (L1); P2-1/P2-4/P2-5 are L4. Zero in L3 — but §5 *deliberately* says subject wins and other-lane tasks become cross-lane sub-issues, "not a silent split." The rule answers this row: L3. Downgrade to a note. |
| G6 | no bucket for supersession | Real distinction; see §A5 for the definition it needs and the table/ruling divergence. |
| G7 | board is neither plan nor companion | Gap in the **table's** `kind`/`status` vocabulary; the design disposes of the board (archive). See §A7. |
| G8 | README has no kind/status | Same as G7 (retire). |
| G9 | Lane 4's name says "research" | **Not a gap.** §5 says subject; §3 populates L4 by package. A clarifying sentence in the design is optional. |

### G3/G4 and the Lane 9 ruling, including its retraction

**The original Lane 9 ruling does not hold against §3.** Lane 9's
definition is enumerated by path — `tools/`, `.claude/*`, `.github/`,
`.woodpecker/`, `scripts/`, `tests/`, `bench/`, `ui_samples/`, docs — plus
"this lane system's own upkeep." It contains no application code.
`git mv` of `services/*.py` is application work; tier1-hygiene changes
runtime behavior (stall logging, pagination, throttling) in five lanes'
code. Labeling either "Tooling, CI & process governance" would hide code
work from the lanes that own it. David's rejection was right. I reached
the same conclusion independently before reading `dcc2643`.

**But `dcc2643`'s replacement rule is not in the merged design.** It says
"§3's existing split rule already answers this — 'an initiative that grows
to touch a second lane splits at the boundary, each half stays
single-lane.'" `grep` over the merged design for that phrase, "second
lane," "each half," or "grows to touch" returns nothing. The phrase appears
only in the **round-1** adversarial review (`…-design-adversarial-review.md:170`),
quoting round-1's design text — which the round-2 fix (§5, "fix #3")
**deleted and replaced** with: primary lane by subject, other-lane tasks
as cross-lane sub-issues, "**not a silent split**" (`design.md:213`). The
retraction resurrects a rule the design removed and attributes it to a
section that says the opposite.

**Consequence, as of `dcc2643`:** both rows still carry `lane: 9` and
`lane: 5` in the lane column; the summary still says L9=13, L5=3; and the
flag text says each "splits per task." The *task issues* (#169–#172, all
CLOSED) can be labeled per task without controversy. The *plan document*
is one file that must move to exactly one lane's archive in step 4 —
"splits per task" gives it none. §1's ask is "every plan/branch/issue/doc
assigned to exactly one."

**Required fix:** pick one of the following and make the lane column, the
flag text, and the summary counts agree:

1. Apply §5 + §2(d) as written: the initiative's first-stated task decides
   the document's lane (backend-services-modularization → Task 1
   `services/history/` → **L4**; tier1-hygiene → Task 1 `loop_watchdog.py`
   → **L5**, as the table already had), flag both "multi-lane batch, lane by
   clause (d), tasks carry their own lanes." No design change. This is my
   recommendation — it is what the merged rule actually says, it keeps the
   document in one lane, and it leaves the closed task issues free to carry
   per-task labels.
2. Or propose a clause (e) to the design for "initiatives whose subject is
   repository structure / a cross-lane batch." That is a design edit and
   needs its own fix-list recheck per the design's header; it is *not* a
   lane add/merge/remove, so it does not trigger §13's NO-GO.

Either way, `dcc2643`'s citation of a "§3 split rule" must be corrected —
otherwise the next builder will apply a rule that contradicts §5.

---

## E. Other findings

- **Header stale:** design is merged (PR #640); update the "Applies" line.
- **README row:** "except three" → four (see §C). "24 rows" ✓, "22 plans" ✓.
- **tier0 row:** open-decisions line 42 already reflects this table's
  Task 7/9 correction; drop "not fixed here."
- **Process, one line, out of scope but material to what I reviewed:**
  the table, its self-review, and the retraction all landed as direct
  commits to `main` (branch protection is OFF per #615), and the artifact
  changed between my first and second read. Whoever consolidates should
  pin the commit they consolidate against.
- **`concern:hotpath` tagging** in rows (event-loop-fix1/fix2, tier0) is
  correct per §3's applier rule; not part of the lane count, correctly so.

---

## Verdict: GO-WITH-REQUIRED-FIXES

The table is structurally sound — 63/63 coverage, arithmetic consistent
(the self-review's "one lane is inflated" claim is wrong), and 23 of 24
spot-checked rows hold on both lane and status against primary evidence.
That is a lower error rate than the design's own 66-unit sweep, so §13's
"reproduces the same inconsistency rate" NO-GO trigger is not met. What
fails is definitional, and it fails in a way that is already replicated in
two other in-flight artifacts.

**Required before step 2 (this table):**

1. G3/G4: make lane column, flag text, and summary counts agree (§D, option
   1 recommended); correct `dcc2643`'s "§3 split rule" citation.
2. Economic-investigation row: either add the "closing artifact disposes of
   the remainder" clause to `done` and cite E12 + P2-3/P2-4, or mark
   `active` citing #76 (§A3).
3. ME-gate row: cite #289–#293 (OPEN) as the residual scope, not #277
   (CLOSED); settle `declined` vs `superseded` to match the ruling (§A5).
4. G5: correct the evidence ("all five in Lane 4" is false — L4/L6/L1) and
   downgrade from RULE-GAP to a note; §5 answers it.
5. G2 wording: the design states no suffix rule.
6. Header: design is merged; README row: four, not three; tier0 row: drop
   "not fixed here."

**Required for the shared vocabulary — propagate to the issues and
specs/research tables before either is reviewed:**

7. Remove "or written-only" from `stalled`; state precedence
   (decision > done > active > stalled > never-started) (§A1).
8. Define "deliverable" per document kind (§A2).
9. Define "tracker" (§A4); drop "movement" from `stalled`.
10. Define `superseded` by the "is the goal still unowned?" test, or drop it
    and record supersession in `basis` (§A5). One answer, three tables.
11. Companions: `inherits`/`n/a` in `status`, not `done` (§A6), or state the
    convention identically in all three.
12. Add `kind: board`/`index` and `status: retire` for non-plan files (§A7).

**What would flip this to NO-GO:** if the specs/research table, once
visible, applies `done` to written-only documents (i.e. §A2 was resolved
the vacuous way) — then that table's status column carries no information
and must be rebuilt, not fixed row by row.
