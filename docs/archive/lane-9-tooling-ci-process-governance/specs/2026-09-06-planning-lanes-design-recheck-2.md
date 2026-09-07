# Fix-list recheck 2: planning-lanes design (three blocking items + reference sweep)

Date: 2026-09-06 (late). Scoped second recheck of
`2026-09-06-planning-lanes-design.md` against the three blocking items in
`2026-09-06-planning-lanes-design-recheck.md` §2 (items 1-3), a
present/absent report on the non-blocking folds it named, a mechanical
reference sweep, and the lane-list check. Independent Agent call, no
memory of the authoring or prior-recheck conversation. Not a full review.
Every verdict below was re-derived from the file/command named, not from
the design's own text. The design file is untracked (`git status` `??`),
so there is no committed prior version to diff against; the round-2
review doc is the only record of the pre-revision numbering.

## 1. Blocking items

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Lane 7 row | **RESOLVED** | `ls services/config/` → `README.md __init__.py config_bounds.py config_overrides.py config_paths.py config_performance.py config_store.py routes.py`; `ls services/config_*.py` → `no matches found`. Design `:90` now says `routes.py`, `config_paths.py`, `config_store.py`, `config_bounds.py`, `config_overrides.py` are "all five inside the subdirectory", "no flat `services/config_*.py` files", and `config_performance.py` "is inside the same subdirectory and still goes to Lane 4 by clause c". Clause (c) at `:65-70` reads "despite living inside `services/config/`" — the "among flat files" wording is gone. Staleness attribution verified: `services/config/README.md:8` = "and stay flat for now — see the modularization plan's" (stale); `ast.get_docstring(services/config/__init__.py)` = "config_store.py, config_bounds.py, config_overrides.py, and config_performance.py moved in here 2026-08-27" (current). The design names the README as the stale one — correct. |
| 2 | Track A/B/C section + §6 step 3 forward ref | **RESOLVED** | `### Reconciling "Track A/B/C" — restored` exists at `:148`, under §3 (`:80`), before §4 (`:189`). Contents checked line by line: **Track C** (`:153-163`) — exec-program doc "becomes the sole surviving owner of production-sequencing gates"; `ROADMAP.md:66` repointed; `:995` "must" be edited. Cited lines verified live: exec-program `:861` = `## Program 3R — Canonical Decision + Live Execution investigation`; `:995` = "active-tracks-board.md's Track C table, which is the authoritative live status tracker for this program going forward rather than this section's own stale requirements below"; `ROADMAP.md:66` = the board link (`docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-active-tracks-board.md`); `grep -n 'Program 3R\|Program 4\|Program 8' ROADMAP.md` → no hits; board `:142-157` = the Track C gate table (3R→8). **Standing decisions** (`:164-167`) — board `:220-237` = 1 checked (sports legal) + 5 unchecked (shadow-mode run, deployment target, auth model, auto-apply governance, kill-switch numbers); `cat docs/open-decisions.md` (44 lines) contains none of the 5 → "five new lines, no duplicates" holds. **`#576` → Lane 1** (`:170-175`): `gh issue view 576` → `CLOSED`, `COMPLETED`, closed by PR `597` — matches. **7-PR mapping** (`:177-187`): #624/#625/#627/#630/#632/#636/#637 all present. `gh pr view 636 --json files` → 5 files: `docs/event-loop-blocking-routes-census-2026-09-03.md`, `services/analytics/routes.py`, `services/candidate_log.py`, `tests/test_analytics_routes.py`, `tests/test_candidate_log.py`; design `:182-185` says "five files, not three … two `tests/` files → Lane 9 by the table, one `docs/*.md` → Lane 9" — correct. **§6 step 3 forward ref**: `:286-287` "since §3 names this doc as the sole surviving owner of production-sequencing gates" → §3's `:159-160` says exactly "The execution-program doc becomes the sole surviving owner of production-sequencing gates". Resolves to text that says what it claims. |
| 3 | `LANES` reference | **RESOLVED** | Design `:113-114`: "a `CONCERNS` dict mirroring the new `LANES` map (§8, rule 5)". §8 rule 5 at `:338-341`: "**A machine-readable `LANES` constant in `labels.py`** (lane number → name → package list) is the single source of truth". §6 (`:220`) is Migration with steps 1-5 and no rules. Resolves. |

## 2. Non-blocking folds (present/absent — informational, not blocking either way)

| Item | Status | Evidence |
|---|---|---|
| Clause (d) first-stated-purpose tiebreak | **PRESENT** | `:71-78`, tied by name to `market_history.py` and `series_watcher.py`. Note: the Lane 1 row (`:84`) still justifies `market_history.py` by "the `game_state.py` precedent" and `series_watcher.py` by "same ordering tiebreak as `market_history.py`" — neither row cites clause (d) by letter, and `(clause d)` appears nowhere outside its definition. The tiebreak now has a home in the rule; the rows were not restated to point at it. |
| `__main__.py:37` as constant, `:134`/`:224` as read sites | **PRESENT, correct** | `:37` = `ACTIVE_TRACKS_BOARD_PATH = Path("docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-active-tracks-board.md")`; `grep -n ACTIVE_TRACKS_BOARD_PATH` → exactly `:37`, `:134` (`parse_track_items(ACTIVE_TRACKS_BOARD_PATH.read_text())`), `:224` (`list_plan_candidates(PLANS_DIR, ACTIVE_TRACKS_BOARD_PATH.read_text())`). Design `:249-254` says this. Also re-verified `:26` (import), `:39` (`KNOWN_SOURCES` has `"track"`), `:231-236` (track-marker comment), `:275` (help text), `:302-303` (`--parent-issue` help). |
| `tests/test_kanban_sync_labels.py:28` + `kanban-board-sync/SKILL.md:3,:36` in touchpoints | **PRESENT, relevant** | Design `:271-272` and `:278-280`. `test_kanban_sync_labels.py:28` = `labels.SYNC_MARKER_KIND_TRACK, labels.SYNC_MARKER_KIND_PLAN,` (breaks when the constant goes; `labels.py:68` = `SYNC_MARKER_KIND_TRACK = "track"`). `SKILL.md:3` = the description line naming `active-tracks-board.md`; `:31` = `sync --sources worktree,roadmap,track --dry-run`; `:36` = "referenced by `active-tracks-board.md`." (step 3 at `:35-36`). |
| `depends-on:#N` claimability nuance | **PRESENT** | `:131-138`: "`depends-on:#N` gates *claimability* … for genuine blocking dependencies only; an *informational* cross-lane reference … is a plain `#N` mention in the issue body … never `depends-on`." `gh issue view 77` labels → `depends-on:#75 depends-on:#76` (labels, as the design says). Plugin file exists: `~/.claude/plugins/mcpmarket-me/skills/github-issues-kanban/references/dependency-chain.md`. The text still calls it a "marker convention" at `:125` (recheck item 10's second half, "label family, not a marker") — not folded; cosmetic. |
| `services/reset/` softened | **PRESENT, accurate** | `:89` and `:381-384` say "no package-level `__init__` docstring or README — its three modules do carry their own docstrings". Verified: `services/reset/` = `__init__.py reset_log.py routes.py trade_archive.py`, no README; `ast.get_docstring` → `__init__.py` None; `reset_log.py` "Audit trail for every Danger Zone reset…"; `routes.py` "Danger Zone routes: preview, history…"; `trade_archive.py` "Permanent, append-only archive of paper-trading history…". |

Other non-blocking items from the prior recheck, checked in passing (none in my required list, none blocking):

- Item 5 (§2(b) attribution): **NOT FOLDED.** `:63-64` still says the "already-in-memory `services.app_state.state`" framing is `state_view.py`'s. It is `services/market_lookup.py:3`'s own sentence; `state_view.py:8`'s parallel is "shared home rather than being owned by any one of those". The Lane 5 row (`:88`) attributes it correctly, so the doc is internally inconsistent on this one phrase.
- Item 6 residual: §3's 7-PR mapping has the five-file census, but §5 (`:211-213`) still enumerates only three of #636's files (`candidate_log.py`, `analytics/routes.py`, `docs/*.md`). It no longer says "three", and the `lane:4` label still follows from the title; the two sections just don't list the same files.
- Item 9 (`decision_bridge` as L2 dependent of `index_stream_handlers.py`): **NOT FOLDED.** `:84` still says "Lane 3 and Lane 4 are declared cross-lane dependents"; `index_stream_handlers.py:249` calls `decision_bridge.handle_settlement_edge_entry` (Lane 2 by `:85`).
- Item 11 (§8 parenthetical wording): **NOT FOLDED.** `:343-346` still says "in this section" and "not the Track C fix".
- Item 13 (155 → 146 via 14 closed): **NOT FOLDED.** `:19-21` lists 14 issue numbers; 155 − 14 = 141 ≠ 146. Live `gh issue list --state open` → **146** now, so the 146 is right and the 155/14 pairing is what doesn't reconcile.

## 3. Reference sweep (mechanical)

`grep -noE '§[0-9]+(\.[A-Z](\.[0-9]+)?)?( ?(step|rule) [0-9]+)?|clauses? \(?[a-d]|fix #[0-9]+|optional #[0-9]+|\bstep [0-9]+\b|\brule [0-9]+\b'` over the design, every hit resolved against the current numbering (§1 `:15`, §2 `:33`, §3 `:80`, §4 `:189`, §5 `:194`, §6 `:220` steps 1-5 at `:222/:240/:243/:292/:305`, §7 `:311`, §8 `:328` rules 1-5 at `:330-338`, §9 `:348`, §10 `:358`, §11 `:366`, §12 `:379`, §13 `:387`; clauses (a)-(d) at `:44/:56/:65/:71`; round-2 review Required fixes 1-10 at its `:485-533`, Optional 11-12 at `:538-539`):

| Token | Line(s) | Resolves to | OK |
|---|---|---|---|
| `§3` | 122, 216, 286, 341 | 122/216 → Lane 4 row `:87` (`candidate_log.py`); 286 → Track C subsection `:159-160`; 341 → `CONCERNS` in the concern subsection `:112-114` | yes |
| `§5` | 169, 185 | 169 → §5 primary-lane rule; 185 → §5 "Small-PR case" `:211` | yes |
| `§6 step 1` | 392 | `:222` (classification tables) | yes |
| `§6 step 3` | 160 | `:243` (touchpoints), whose `:283-289` bullet is the `:995` edit | yes |
| `§7` | 334 | §7 exemption `:314-326` | yes |
| `§8`, `rule 5` | 114 | §8 rule 5 `:338-341` defines `LANES` | yes |
| `§9` | 364 | §9 Projects-field deferral `:351-354` | yes |
| `review §2.A.11` | 98 | round-2 review `:104` "**11. Borderline, rule gives no confident answer:** `kalshi_fees.py` → L3" | yes |
| `round 2's own §6 rule 5` | 11-12 | **Not the current numbering** (current §6 is Migration; it has no rules). Explicitly qualified as the round-2-as-reviewed version's numbering, and the round-2 review uses the identical phrase at its `:471` and `:545` for the same full-cycle line. That version is not on disk (design untracked, no git history). Equivalent current location: §8 rule 5 `:338`. Not dangling — the qualifier names the target — but a reader of this file alone cannot follow it; appending "(now §8 rule 5)" would close that. | yes, with note |
| `step 1` | 308 | §6 step 1 | yes |
| `step 2` | 146, 234, 332 | §6 step 2 `:240` (label pass, `area:*` deletion — `gh label list` confirms all 8 `area:*` + `phase:implementation-plan` still exist to be deleted; `#77` still carries `phase:implementation-plan`) | yes |
| `step 3` | 256 | `kanban-board-sync/SKILL.md`'s own step 3 (`:35-36`, `plan-candidates`) — external file, verified | yes |
| `(clause a)`, `clause b`, `(clause c)`, `clause c` | 87, 88, 87, 90 | §2 (a) `:44`, (b) `:56`, (c) `:65` | yes |
| `clauses a` | 41 | false positive ("Three clauses added") | n/a |
| `fix #1` … `fix #9`, `#10` | 80, 33, 194, 106, 220/344, 311/343, 19/366, 328, 232, 220 | round-2 review Required fixes 1 table / 2 clauses / 3 multi-lane / 4 cross-lane+concern / 5 touchpoints / 6 §5 exemption / 7 numbers / 8 refs / 9 step-1 tables / 10 step 5 — each used for its own subject; `:343`'s "mislabeled fix #6 → fix #5" is what the review's fix 8 asked for | yes |
| `optional #11`, `optional #12` | 377, 89 | review Optional 11 (`#326` state_reason), 12 (`services/reset/`) | yes |

`grep -nE 'Section [0-9]|section [0-9]|§ [0-9]'` → none; no alternate section-reference forms to check. **No dangling reference in the current numbering.** The one historical-numbering token (`:11-12`) is explicitly labelled as such and corroborated by the review.

## 4. Lane-list check

`grep -cE '^\| [0-9] \| \*\*'` on the §3 table → **9**: 1 Kalshi & index data ingestion · 2 Whale signal detection & calibration · 3 Strategy, risk & execution · 4 Analytics, advisory & research · 5 Runtime infrastructure · 6 Observability, quality & safety infra · 7 Config & control plane · 8 Frontend & dashboard · 9 Tooling, CI & process governance. Same 9, same names as the prior recheck's §3 list. The three blocking fixes were: a listing correction inside Lane 7 (no membership change — `config_performance.py` was already Lane 4 and still is), a restored prose subsection under §3, and a section-number correction. **No lane was added, merged, or removed. The flip-to-full-cycle condition is not met.** Restoring the Track A/B/C subsection did not change what the migration archives (the board still goes to `docs/archive/` per step 4; its gate table and standing decisions now have stated destinations), so it is the same content the round-2 review confirmed at its §1, not new scope.

## 5. Verdict

**RECHECK PASSED.** All three blocking items are resolved against source (`ls`, `ast.get_docstring`, `gh pr view 636`, `gh issue view 576`, `sed -n` on every cited line); no reference dangles in the current numbering; the lane list is unchanged at 9. The five named non-blocking folds are all present and accurate. Four prior-recheck cosmetic items (5, 9, 11, 13) and one residual (§5 still enumerates three of #636's five files) were not folded; none changes a lane, a claim, or a migration step, and none blocks sign-off. Recommended one-line edits, at the author's discretion: append "(now §8 rule 5)" at `:12`; change "`state_view.py`'s" to "`market_lookup.py`'s own" at `:63`; add "Lane 2 (`decision_bridge`)" to `:84`'s dependents.
