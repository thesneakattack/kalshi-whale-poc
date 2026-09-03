# PR-scoped recheck: PR #505 fix batch (`3a5bad8`) + coordinator sign-off gaps

2026-09-03. Fresh Agent, no memory of any prior review round on this PR. Scope per assignment
(CLAUDE.md's "nothing advances on one pass" fix-list-recheck provision): **not** a full
self-review-plus-adversarial-review cycle — three specific checks against PR #505
(`spec/persistence-layer-db-migration`) at its current head, `3a5bad8`. Verified in an isolated
worktree checked out to that exact commit (`git rev-parse` confirmed `3a5bad84...` before any
read below).

## 1. Did autotrade-a7's 6 fix-list items (I-A, I-B, M-A–M-D) actually land?

Read `git show 3a5bad8` in full (both changed files' diffs), then re-read the current file state
directly — not the diff, not the commit message's description of intent — for each item.
**All six genuinely landed, text matches the claim.** One is landed correctly but the citation
that accompanies it is imprecise by one line.

| Item | Claim | Verified against current text | Verdict |
|---|---|---|---|
| I-A | Cite `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md` §1.4 as the true origin of `register_ddl`/`_DDL_REGISTRY`, restate in provenance + sign-off section | `grep -n "persistence-layer-redesign-design"` → lines 15 (new provenance paragraph) and 464 (rewritten "Open question for explicit sign-off" section) | **Landed** |
| I-B | Add "Gate 0" language: unregistered-table `connect()` call raises a bare `KeyError` | Line 393, inside the `## Migration gates` → `**Gate 0 —...**` bullet list (not just gestured at elsewhere — literally in the Gate 0 bullets, a "**PR-stage addition**" bullet), states `_SCHEMAS[table](conn)` "raises a bare `KeyError`" for an unregistered table, and requires Task 1's own tests to cover the "connect before the registering module is imported" case | **Landed**, and confirmed to be *in* Gate 0 specifically, not just somewhere in the document |
| M-A | Citation precision: `capture_writer.py`'s `_retain()` def-vs-call-site line reference | Doc now reads "defined `:329`, invoked from the lock-error handler at `:403-414`... `:412`'s `fault_log.record(...)`". Read `services/capture_writer.py` directly: `_retain` is defined at line 329; `fault_log.record("capture_writer", "flush_retained_on_lock", ...)` is at line 412, inside the 403-414 range, and `_retain(...)` is invoked at line 405 (within that same range) | **Landed, and verified accurate against source**, not just internally consistent |
| M-B | Citation precision: exact quote from `_aio_db.py:187-191`'s `schema_init` docstring | Doc quotes: `"runs exactly once - only on this key's first-ever open, never on a cache hit"` (`:191`, quoted verbatim). Read `services/diagnostics/_aio_db.py` directly (`cat -n`): the docstring at lines 191-192 reads `"""schema_init, when given, runs exactly once - only on this key's` / `first-ever open, never on a cache hit - same contract as..."` — the quoted text is **word-for-word correct** | **Landed and accurate**, with one minor imprecision: the quote spans source lines 191-192 (a line-wrapped docstring), but the citation names only `:191`. Not a false claim — the text is verbatim — just a citation boundary that undersells its own span by one line. Not worth a further fix-list item on its own. |
| M-C | Citation precision: `_add_column_if_missing` is underscore-prefixed (not `add_column_if_missing`), and the real count is 7 calls / 4 indexes / 11 statements for `signal_log.py` | Read `services/signal_log.py` directly: `def _add_column_if_missing(...)` at line 36 (underscore-prefixed, confirmed); called at lines 88, 98, 99, 100, 120, 131, 147 = **7 calls**, exactly matching; `CREATE INDEX` at lines 71, 72, 78, 148 = **4 indexes**, exactly matching; 1 `CREATE TABLE` (`signals`) | **Landed and independently re-verified against source, exact** |
| M-D | Self-review's own file count: the PR-stage self-review is a 4th file, not one of "three files" | `docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design-pr-self-review.md` lines 25-28 now carry the correction inline, parenthetically, right where the original "three files" claim was made | **Landed** |

Verdict: **6/6 fix-list items genuinely landed**, verified against current committed text and,
where the claim cited a source file, against that source file directly — not accepted on the
commit message's own description.

## 2. Independent re-derivation of the two numeric claims (not trusted from either the spec or the coordinator)

### Scope: confirming the 26 modules myself

The spec's "Scope correction" section (lines 254-296) doesn't enumerate all 26 by name (only 8
"individually named" + "the remaining 17" + `tools/coordination_engine.py`, unnamed). Derived the
full list independently from the same mechanism the spec describes:

`grep -rln "def _connect" services/` → exactly 30 files. Per the spec's own text and the research
doc's own explicit list (`docs/superpowers/research/2026-09-03-persistence-layer-db-migration.md`
lines 14-19, 24-30), 5 of those 30 are Tier0-already-migrated (`market_history.py`,
`title_cache.py`, `market_catalog/market_catalog.py`, `signal_log.py`, `fault_log.py` — PR #499 +
PR #501) → 25 remain. Plus `tools/coordination_engine.py` (outside `services/`, "genuinely
leaking," lowest priority) → **26**. All 26 files confirmed to exist on disk. Full list written to
scratchpad and used for both censuses below; available on request but omitted here for length —
it is exactly: `accounts_store.py`, `alerting/alerting.py`, `backup/backup.py`,
`candidate_ledger.py`, `candidate_log.py`, `config/config_performance.py`, `data_quarantine.py`,
`game_state.py`, `history/suggestion_decisions.py`, `index_feed/ingestion.py`,
`market_analyst_agent/_db.py`, `market_events/event_schedule.py`, `observability/observability.py`,
`paper_broker.py`, `research/research.py`, `reset/reset_log.py`, `reset/trade_archive.py`,
`risk_manager.py`, `series_cache.py`, `series_evaluator.py`, `series_watcher.py`,
`settlement_edge.py`, `shadow_mode.py`, `trade_category.py`,
`whale_calibration/calibration_history.py` (all under `services/`), plus
`tools/coordination_engine.py`.

### Table-name census: **41 distinct, not 42** — 0 cross-file duplicates (confirmed) — the "42" is explained, not a phantom

Extracted every `CREATE TABLE`/`CREATE TABLE IF NOT EXISTS` from each of the 26 files' own
`_connect()`/schema-init code, by direct read (not a script alone — every result below was
hand-verified against `sed`/`cat -n` output on the actual source line). Two files needed extra
care because their real DDL is an **imported constant from `services/capture_writer.py`**, not a
literal `CREATE TABLE` string in their own file text — a naive grep of "CREATE TABLE" scoped to
just these 26 files' own text would silently miss these:

- `services/candidate_log.py` executes `capture_writer.REJECTED_CANDIDATES_DDL_SQL` and
  `capture_writer.REJECTION_EVENTS_DDL_SQL` (lines 86, 91) → tables `rejected_candidates`,
  `rejection_events`.
- `services/series_watcher.py` executes `capture_writer.RAW_TRADES_DDL_SQL` (lines 159, 201) →
  table `raw_trades`. It also declares `book_snapshots` **twice literally**, in its own file:
  once in the sync `_connect()` (line 164) and once in the async `_ensure_schema_aio()` (line
  206). Diffed both DDL blocks byte-for-byte — **identical**, same table, no schema drift between
  the two paths.

Full per-file distinct count (26 files, sums to 41):

`accounts_store`(1) `alerting`(1) `backup`(1) `candidate_ledger`(1) `candidate_log`(2)
`config_performance`(2) `data_quarantine`(1) `game_state`(1) `suggestion_decisions`(1)
`ingestion`(1) `market_analyst_agent/_db`(3) `event_schedule`(1) `observability`(1)
`paper_broker`(4) `research`(1) `reset_log`(1) `trade_archive`(3) `risk_manager`(1)
`series_cache`(3) `series_evaluator`(1) `series_watcher`(2, distinct — `book_snapshots` counted
once despite 2 statements) `settlement_edge`(1) `shadow_mode`(2) `trade_category`(1)
`calibration_history`(1) `coordination_engine`(3) = **41**.

Sorted across all 41: no name repeats between two different files. **0 cross-file duplicates,
confirmed independently** — this part of the prior claim holds.

**Where "42" came from**: if you count raw `CREATE TABLE` *statement occurrences* rather than
distinct table *names*, you get 42 — because `series_watcher.py`'s `book_snapshots` is declared
by two separate (identical) `CREATE TABLE IF NOT EXISTS` statements in that one file. 41 distinct
names + 1 same-file repeat = 42 statements. The prior claim ("42 distinct") conflates the two
quantities. This is not a dangerous error for the sign-off's own purpose — Gate 0's
global-uniqueness concern is about names colliding *across files*, and that's still 0 — but it is
a real, reproducible off-by-one in what was reported as "distinct," and the mechanism (a table
declared via two DDL statements for two connection paths in one file) is exactly the shape a
future genuine collision could hide inside if a *different* module tried the same "declared
twice, once per code path" pattern for a name another file also owns.

### Call-site census: within the 26 files' own text, **111 `with _connect()` shape, 0 bare-assignment, 0 `closing()` — confirmed exact match**. Outside that narrow scope, real gaps exist.

Per the coordinator's addendum (received mid-task): scope stated explicitly below, and every
bare/`closing()` site found anywhere — not just inside the 26 files — is reported, per that
addendum's instruction.

**Primary count — production code, call sites located within each of the 26 modules' own file
text (same scope the coordinator's original "111" claim used, confirmed by matching it exactly):**
grepped `_connect(` in all 26 files, excluded `def` lines, comments, and docstring mentions
(e.g. `game_state.py:363`, `series_evaluator.py:126,203`, `series_watcher.py:190-193` all
*mention* `_connect()` in prose, not calls). Two lines are neither with-shape, bare-assignment,
nor `closing()`: `paper_broker.py:338` and `risk_manager.py:125`, both `return _connect(self.db_path)`
— the internal body of a class's own `_connect(self)` wrapper method, which is what makes
`with self._connect() as conn:` work at the actual call sites; not counted as a separate usage
site, consistent with how the coordinator's own "111" must also have excluded them to land on the
same number.

Result: **111 `with _connect(...) as conn:` (or `with self._connect() as conn:`) sites, 0 bare
`x = _connect(...)`, 0 `closing(_connect(...))`** — independently reproduced, exact match to the
coordinator's claim, for this specific scope. Explicitly confirmed via targeted grep: zero
`closing(` and zero `<name> = _connect(`/`<name> = self._connect(` patterns anywhere in the 26
files.

**This scope does not include `tests/`, and does not include cross-file production callers
(other files that import and call one of these 26 modules' `_connect()`).** Both matter, and both
were checked, because the coordinator's own addendum and correction comment already flagged the
first gap and asked about the second:

- **`tests/` bare-assignment, `coordination_engine.py` only**: confirmed the coordinator's own
  correction-comment figure of **22** bare-assignment call sites, all in `tests/` —
  `tests/test_coordination_engine.py` (15: lines 17, 24, 25, 30, 42, 50, 61, 72, 84, 97, 111, 130,
  143, 155, 162), `tests/test_quality_coordination_branch_domain.py` (4: lines 80, 106, 126, 149),
  `tests/test_quality_coordination_cli.py` (2: lines 72, 88 — chained directly to `.execute(...)`,
  never even assigned to a name), `tests/test_quality_coordination_cleanup_actions.py` (1: line
  95). Matches exactly.
- **A production bare-assignment leak the coordinator's correction comment missed**:
  `tools/quality_coordination.py` — production code, not `tests/`, not one of the 26 modules, but
  imports `coordination_engine as ce` and calls `ce._connect()` bare, unassigned-to-context, at
  **line 584** (`run_detect_cycle()`) and **line 657** (`main()`'s `--clean` path). Grepped the
  whole file for `conn.close()`: zero hits. **This is a genuine production leak**, not a test-only
  one. It directly contradicts the coordinator's correction comment's own claim — "Its own
  production paths never call it... every production site is `with`-shaped" — which is true only
  in the narrow sense that `coordination_engine.py`'s *own file* has zero internal call sites
  (confirmed: `grep -n "_connect" tools/coordination_engine.py` returns only the `def`), but false
  in the broader sense the comment actually asserted. The correction comment even named its own
  falsification condition — *"a production caller of `coordination_engine._connect` I missed"* —
  and this is exactly that caller.
- **Cross-file with-shape production callers, correctly shaped but outside the 111's file-scoped
  count**: `services/market_analyst_agent/_db.py`'s `_connect()` is imported and called
  `with _connect() as conn:` from three sibling production files in the same package —
  `per_market.py` (6 sites: lines 209, 222, 240, 271, 304, 318), `per_series.py` (4 sites: 162,
  172, 188, 196), `full_spectrum.py` (4 sites: 179, 189, 203, 209) — 14 sites, all correctly
  with-shaped. `services/index_feed/ingestion.py`'s `_connect()` is similarly called from
  `services/index_feed/settlement_algebra.py:310`, also with-shape. None of these 15 are leaks,
  but none are captured by a census scoped to "grep inside each of the 26 files' own text" —
  they're real production call sites of in-scope modules' `_connect()` functions, living in
  *other* files.
- **A fourth observed shape, not asked about but worth naming**: `<name>._connect().close()` —
  a bare call immediately chained to an explicit `.close()`, neither a context manager nor a
  leak nor `contextlib.closing()`. 7 occurrences, all in `tests/`: `test_diagnostics.py` (5:
  lines 129, 150, 197, 248, 274), `test_observability.py` (1: line 53), `test_quality_ratchet.py`
  (1: line 22). Confirmed **zero** genuine `contextlib.closing(_connect(...))` usage anywhere in
  the repo, production or tests.

**Bottom line on the call-site census**: the "111/0/0" figure is real and reproduces exactly for
its stated scope (26 modules' own production text). Broadened even slightly — to the actual set
of files that call these functions in production, which is what matters for a real migration —
the true picture is: 111 (in-file) + 15 (cross-file, still with-shape) = 126 with-shape production
call sites, **plus 2 bare-assignment production leaks in `tools/quality_coordination.py`** that
neither the spec, the original census, nor the coordinator's own correction comment caught. This
is not a reason to distrust the "111" number itself — it's accurate for what it measured — it's
direct, concrete evidence for why sign-off condition (b) below (per-module call-site verification
at migration time, not trusting a census) is necessary, not a formality.

## 3. Do the three sign-off conditions already live in the spec, or still need to land?

Read the coordinator's sign-off comment and its correction comment in full
(`gh api .../issues/505/comments`), then searched the current spec text (`3a5bad8`, 513 lines)
end to end for equivalent language. **All three are absent from the committed spec text as of
this commit** — none is a "gestured at, arguably covered" case; each is a clean zero-hit on
direct search for the concept, not just the phrase.

**(a) Gate 0 — permanent CI/test invariant for global table-name uniqueness.** Absent.
`grep -in "unique\|globally\|CI \|permanent detection\|invariant"` across the whole document
returns nothing relevant (one unrelated hit: "safety invariants" in a different section, about
CLAUDE.md's safety rules, not about this). What Gate 0 *does* have is item 1 of "Required fixes to
the prototype" — `register_schema` raises on a genuine registration conflict — which is a
**runtime guard**, not a **CI-time detection mechanism**: it only fires if a real conflict is
actually exercised (e.g., by a test that imports every migrated module together), and nothing in
the current text names a dedicated test whose job is specifically to assert "all registered table
names are globally unique" as a standing, permanent check. The sign-off's own framing — "turns
check 1 above into permanent detection for the hybrid's one theoretical false-conflict mode" — is
explicitly a step beyond the raise-on-conflict guard, and that step isn't written down anywhere in
the spec. **Recommend**: a new bullet in Gate 0, immediately after the existing "PR-stage
addition" `KeyError` bullet (line ~398) — something to the effect of "a standing test (run in the
normal suite, not opt-in) that imports every migrated module and asserts no `register_schema`
conflict is raised, turning this census into permanent regression detection, not an incidental
side effect of however pytest happens to collect tests."

**(b) Gate 1 — per-module call-site-shape verification at actual migration time, not trusting the
census.** Absent. Gate 1's first bullet ("Read that module's actual `_connect()` ... body in full")
is about reading the *definition*, not about grepping *call sites* of that function for their
shape. No bullet in Gate 1 mentions `grep`, `bare`, `closing(`, or checking where a module's
`_connect()` is actually invoked from before treating its migration as mechanical. This gap is not
hypothetical — section 2 above is a live demonstration of exactly the failure this condition
exists to prevent: a careful, honest census (111/0/0, independently reproduced exact) still missed
two real production bare-assignment call sites because they live in a different file
(`tools/quality_coordination.py`) than the module being migrated
(`tools/coordination_engine.py`). **Recommend**: a new bullet in Gate 1, and it should be written
to require checking *beyond the migrating module's own file* — `grep -rn` for
`<module_alias>\._connect(` and `=\s*_connect(` and `closing(_connect(` across the whole repo
(services/, tools/, tests/), not just within the module being migrated — since the one confirmed
real leak in this codebase (`quality_coordination.py`) is a cross-file caller the module's own
source would never reveal. Should also explicitly say "including `tests/`" per the coordinator's
own correction comment (`coordination_engine.py`'s 22 test-only bare sites would silently break on
migration if Gate 1 checked only production code).

**(c) `fix/db-foundation-must-fix-tests` (`e74096a`) is input to Task 1, not Task 1 itself.**
Absent. `grep -n "fix/db-foundation-must-fix-tests\|e74096a"` across the spec doc and all three
companion docs (self-review, design-self-review, consolidation) in the PR: zero hits, in any of
them. Confirmed via git that `e74096a` is one commit ahead of `17b2e8f`
(`git merge-base --is-ancestor 17b2e8f e74096a` → true) — i.e., `fix/db-foundation-must-fix-tests`
is a follow-on branch off the *same* prototype the spec already discusses as "the prototype at
`17b2e8f`" / `feat/persistence-layer-unified-connect`, adding (per its own commit message) "3
must-fix tests for db.py foundation + the fixes they require." The spec's own "Required fixes to
the prototype" section (lines 179-231) discusses exactly the kind of fixes this branch's commit
message claims to add (must-fix #1 schema-conflict, must-fix #2 corrupted-DB test) — but never
once acknowledges the branch exists, that someone already attempted these fixes, or that its
result is still path-keyed and still carries C2 (the sign-off's own re-verification: "still
path-keyed and still carries C2 ... `_SCHEMAS: dict[Path, list[...]]`, `register_schema(db_path,
table_name, init_fn)`, 0 tests touching monkeypatched `DB_PATH`"). Without this note, a future
implementation-plan session that discovers this branch independently (a plain `git branch -a`
would surface it) has no way to know from the spec alone that it must be mined for its sound parts
(identity-check conflict test, `busy_timeout_ms` parameter, lock-contention test,
close-on-setup-failure test — all named as sound by the sign-off) rather than merged or adopted
wholesale — which would silently reintroduce C2. **Recommend**: a new item (or an addendum to
existing items 1-2) in "Required fixes to the prototype before any module migrates onto it"
(around line 200), naming the branch and commit explicitly, stating what's reusable from it and
what must not be carried over (the path-keyed registry shape), so Task 1 has this in the spec
itself rather than only in a PR comment thread that an implementation-plan-stage session may never
read.

**None of the three is a merge blocker for this PR** — the sign-off comment itself frames them as
"additions to the gates, not changes to the spec" and names the next step as the PR-stage
adversarial review, not a spec rewrite. But all three are currently real gaps: an
implementation-plan stage drafted directly against the current spec text, without independently
re-reading the PR comment thread, would proceed without any of them. That is the condition this
recheck was asked to characterize precisely, and the answer for all three is the same: **not yet
in the spec, needs to land before (or alongside) the implementation-plan stage**, not merely
"gestured at."

## Addendum: branch moved during this recheck, and a worktree-sharing hazard found along the way

While this recheck was in progress (all analysis above performed against the pinned commit
`3a5bad8`, confirmed via `git rev-parse`/`git show` before any read), the branch
`spec/persistence-layer-db-migration` advanced past that commit to a new commit, `a0548a0`
("fix: incorporate autotrade-1d's coordination_engine.py call-site-shape finding," authored
2026-09-03T11:49:55Z — landed roughly 2 minutes after `3a5bad8` and ~1 minute after the
coordinator's correction comment). This was discovered mid-task when this worktree's `git
rev-parse HEAD` unexpectedly returned `a0548a0` instead of the assigned `3a5bad8`.

Root cause, confirmed via `git worktree list --porcelain`: this task's assigned worktree
(`.claude/worktrees/agent-a20b8eaa6381b5d6d`) and a separate, apparently live worktree
(`.claude/worktrees/persistence-db-migration-spec`) both had `refs/heads/spec/persistence-layer-db-migration`
checked out simultaneously — which standard `git worktree` branch-exclusivity should prevent, so
this reflects an anomaly in how these two worktrees were set up, not an action taken during this
recheck. Because branch refs are shared repo-wide even when checkout exclusivity is (normally)
per-branch, a commit landed in the other worktree updated `HEAD` for this one too, without any
fetch/pull/merge run here. **This is exactly the situation CLAUDE.md's peer-coordination
guidance addresses ("never checkout/stash/reset/rebase/merge under another session's work"/"never
edit a file another session names as in use") — flagging it explicitly since this worktree
should not have been sharing a branch with a live peer in the first place; the orchestrating
session should confirm this doesn't recur for other in-flight worktrees on this branch.**

Consequence caught before it caused damage: this worktree's index and working-tree copy of the
main spec document had gone **stale relative to the true current HEAD** — `HEAD` had advanced via
the shared ref, but this worktree's checked-out files hadn't been resynced, so they still reflected
the pre-`a0548a0` content, and `git status` showed the file as **staged-modified** (i.e., about to
be *reverted* to the older content on the next commit here). Committing at that point would have
silently undone the coordinator's just-landed `a0548a0` changes. Corrected via
`git restore --source=HEAD --staged --worktree -- <path>` (a pure sync-to-current-HEAD operation,
not a content edit) before creating this document's own commit; verified `git diff HEAD -- <path>`
is empty afterward. No other file was affected; `git status` before that restore showed only this
one file plus this document itself.

**Effect on this recheck's own findings**: none of the Task 1/2/3 analysis above changes — it was
correctly performed against the assigned `3a5bad8`, via `git show`/`git diff` of that specific
commit object plus direct reads of that commit's tree, independent of whatever the branch tip
drifted to afterward. One item is worth updating in light of `a0548a0`, in the interest of not
handing the orchestrator a stale verdict on something now easy to check:

- **Sign-off condition (b) status, given `a0548a0`**: `a0548a0` adds a new Gate 1 bullet
  ("**PR-stage addition — call-site *shape*, not just DDL preservation, and covering `tests/` as
  well as production.** Grep every caller of the module's `_connect()` (production **and** test)
  ... before assuming the migration is call-site-transparent") — phrased generally (per-module,
  at migration time), not only about `coordination_engine.py`. This substantially lands condition
  (b) in spirit. **However**, the same commit's new scope-correction text asserts
  `coordination_engine.py`'s "callers are entirely in `tests/`, not production" — and section 2
  above already found this is factually incomplete: `tools/quality_coordination.py` (production,
  not `tests/`, not one of the 26) has 2 bare-assignment calls into `coordination_engine.py`'s
  `_connect()` (lines 584, 657, no `.close()` anywhere in that file) that neither the coordinator's
  census, correction comment, nor `a0548a0`'s new text caught. So: condition (b)'s *general*
  requirement is now present in Gate 1, but the *specific* claim `a0548a0` uses to motivate it is
  incomplete in exactly the way this recheck's own call-site census independently found — worth a
  follow-up correction to that claim, separate from and smaller than "add condition (b) from
  scratch." Conditions (a) and (c) are unaffected by `a0548a0` (neither commit touches Gate 0's
  uniqueness-invariant gap or the `fix/db-foundation-must-fix-tests` reference gap) and remain
  fully absent as characterized in section 3.

## Summary verdicts

1. **I-A, I-B, M-A, M-B, M-C, M-D: all 6 genuinely landed**, verified against current committed
   text and, for every item with a source-file citation, against that source file directly. One
   trivial imprecision noted (M-B's `:191` citation for a quote spanning `:191-192`) — not worth
   a further fix-list item.
2. **Table-name census: 41 distinct, not 42** (the "42" is 41 distinct names + 1 same-table
   repeat-statement in `series_watcher.py`, byte-identical between its sync and async paths — not
   a real second table, and not a cross-file collision). **0 cross-file duplicates, confirmed.**
   **Call-site census: 111/0/0 confirmed exact**, for its actual scope (production code, within
   the 26 modules' own files). Outside that scope: 22 bare-assignment sites in `tests/`
   (confirmed, all `coordination_engine.py`), **2 bare-assignment production leaks in
   `tools/quality_coordination.py`** that were not previously caught by the coordinator, and 15
   additional with-shape (non-leaking) production call sites living in sibling files.
3. **All three sign-off conditions (a), (b), (c) are absent from the current spec text** — clean
   zero-hits on direct search, not merely under-emphasized. Recommended landing spots: (a) new
   Gate 0 bullet after the existing `KeyError` addition; (b) new Gate 1 bullet, scoped repo-wide
   (not per-module-file) and explicitly including `tests/`; (c) new item in "Required fixes to the
   prototype," naming `fix/db-foundation-must-fix-tests` (`e74096a`) explicitly.
