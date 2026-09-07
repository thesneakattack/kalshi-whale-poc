## Independent adversarial review — implementation plan (2026-09-07)

Reviewer: a fresh Agent-tool call with no memory of the session that wrote
the plan. Method: the plan's Python (Task 1 constants, Task 2 scanner, Task 3
classifier, Task 4 counter) was extracted **verbatim** into a scratch package
and its own tests run against it with a stdlib runner (pytest is not installed
on the host and the scratchpad is not mounted in the ddev container; the
runner only supplies `tmp_path`, so assertion semantics are unchanged). Every
merged PR in the 200-window had its changed-file list fetched live via
`gh api .../pulls/N/files --paginate`, and every comment's first line via the
plan's own Task 4 jq. Line anchors were read with `sed -n` at `a1d2fc0`
(= `a39d5f9` + the plan commit; no rule file changed between them).

### Verdict: **NO-GO**

The rule set and the line anchors survive; the simulation reproduces exactly.
What does not survive is the artifact counter (a plan test fails against the
plan's own implementation, and the failure is the false-positive class the
merge gate exists to exclude), the Lane 3 import scanner (blind to the
codebase's dominant import form, so the "every module Lane 3 imports directly"
rule is not what ships), and the committed-file counting path (this branch's
own PR would print `PASS` with zero PR-stage comments). Four blocking findings,
each with a required fix; none needs a redesign.

---

### Blocking findings

**B1. Task 4's test suite fails against Task 4's implementation; the plan
claims 27 PASS.**
Ran the plan's 34 Task 1–4 tests verbatim: 33 pass, 1 fails —
`test_count_review_artifacts_uses_only_the_first_line_of_a_comment`.
Mechanism: its second fixture comment, `"Merging now — the adversarial review
found nothing worth blocking on."`, is a *single-line* comment, so its first
line is the whole body, and `\badversarial\b` matches it.
`count_review_artifacts(...)` returns `(2, ['## Self-review', 'Merging now — the
adversarial review found nothing worth blocking on.'])`, not `1`. The test's
docstring ("a body that narrates a review counts for nothing, spec D2")
states the *correct* requirement; the regex does not implement it. Either the
regex changes (B4) or the test is wrong — the plan cannot ship both.
Falsifier: run the test as written; it passes.

**B2. `lane3_direct_imports()` cannot see `from services import a, b, c`, which
is how Lane 3 actually imports; six direct dependencies are not Tier A, and
Deviation 4's re-derivation used the same blind regex.**
`_LANE3_IMPORT_RE` matches only `from services.X` / `import services.X`.
Re-derived from source at `a1d2fc0` (`grep -rn 'from services import'` over
the 16 Lane 3 files): `services/strategy_engine.py:9` imports
`candidate_log, fault_log, kalshi_fees, market_history, series_cache,
signal_log`; `:506` lazily imports `market_lookup`;
`services/exits/exit_engine.py:17` imports `fault_log, kalshi_fees,
market_analyst_agent, market_history, signal_log`;
`services/settlement_resolver.py:38-39` imports `candidate_log,
market_analyst_agent, market_history, settlement_edge, signal_log, fault_log,
http_client, tick_executor`; `services/position/account_positions.py:13`
imports `http_client`; `services/paper_broker.py:19` imports `db,
history_push, kalshi_fees`; `services/settlement_edge_entry.py:61` imports
`index_feed, kalshi_fees, settlement_edge`. Resolving those with the plan's own
`_resolve_import_target` and checking against `REVIEW_TIER_A_PATHS`, the
targets that are **neither Tier A nor visible to the scanner** are:
`services/fault_log.py`, `services/history_push.py`,
`services/http_client.py`, `services/index_feed/`,
`services/market_analyst_agent/`, `services/market_lookup.py`.
Consequences the plan does not state:
- Task 2's `test_every_lane3_direct_import_is_covered_by_review_tier_a_paths`
  passes vacuously for these — the liveness test (`>= 10` targets, app_state
  present) is satisfied by the 13 dotted-form imports and does not detect the
  gap.
- Deviation 4's claim "only `services/app_state.py` is genuinely added by this
  rule" is false; the spec's own §3.1 list was derived the same way (its text
  says "scans Lane 3 sources for `from services.X` / `import services.X`"),
  so the spec's rule *as worded* ("every module a Lane 3 module imports
  directly") and its stated *mechanism* disagree, and the plan inherited the
  disagreement instead of catching it. Spec §3.2 even names `fault_log.py` as
  a Tier B module while `strategy_engine.py:385` calls `fault_log.record_fault`.
- Re-simulated with the six paths added: **#614** (`services/fault_log.py`,
  merged with Self-review + Consolidation only — no adversarial pass) and
  **#498** (`tests/test_index_feed_backfill.py`, via the new `index_feed` test
  stem) move B→A; plan-window counts become 144/56, and #498 is one of the
  seven PRs Task 3's fixture corpus asserts is Tier B. So "the Lane-3-import
  rule moves no further PR in this window" holds only for the blind scanner.
Required fix: implement the scan with `ast` (covers all three import forms and
skips docstrings/comments for free — the regex would also match a docstring
line beginning `from services.x`), re-run the coverage test, and **decide**
each newly surfaced path on the merits (add to `_REVIEW_TIER_A_EXTRA_PATHS`,
or narrow the rule text and say why). `services/index_feed/` in particular
should resolve to what `index_feed/__init__.py` actually exposes, not the whole
package (Lane 4's `settlement_algebra.py` lives there). Then update the fixture
corpus, spec §3.2's numbers, and Task 7 step 3's "modules Lane 3 imports"
clause to whatever is decided. Falsifier: show a Lane 3 file that imports one
of the six via the dotted form, or show they are not imported at all.

**B3. The committed-file counting path lets prior-stage artifacts satisfy the
PR-stage requirement — this plan's own PR prints `PASS` with zero PR-stage
comments.**
`git diff --name-only origin/main...HEAD` on this branch lists the research
and spec stages' `-self-review.md`, `-adversarial-review.md`,
`-consolidation.md` documents (eight such files). `count_review_artifacts([],
<those files>)` returns **6** with an empty comment list; `review_tier` says A;
`_REVIEW_TIER_REQUIREMENT["A"] == 3`; verdict `PASS`. That contradicts
CLAUDE.md L43 as it stands and as the plan rewrites it ("nothing is shared,
reused, or 'already covered' across stages") and Task 9 step 6's own
requirement of "three distinct persisted comments" for this PR. Every
planning-pipeline PR that bundles or edits earlier stages (105 of the 200
merged PRs are docs-typed) is exposed. Required fix: count only files whose
name matches *and* that are new in the PR *and* whose stage matches the
artifact being gated — or simplest, count committed documents only for
non-PR stages and require PR-stage artifacts to be PR comments, and say so
in the rule text. Falsifier: run Task 6's command against this PR once it is
open, before any comment is posted; the plan predicts `FAIL`, this review
predicts `PASS`.

**B4. `REVIEW_ARTIFACT_FIRST_LINE` has a measured false-positive class, and one
real Tier A PR in the window reaches its requirement only through it.**
Live over the 200 PRs: 265 comment first lines, 229 match, 36 miss (the plan's
261/224/37 does not reproduce; the research itself, line 222, says 265). All
36 misses are non-artifacts, as claimed. But among the 229 matches, these are
not review artifacts either: #646 `## Fix-list recheck (adversarial review
returned NO-GO)`; #632 `**Response to the independent adversarial review's
finding**`; #603 `Merging as the durable #601 benchmark record ...
Consolidation GO is above`; #597 `## Clarifying the ... discrepancy the
adversarial review flagged`; #581 `## Correction to the self-review's own
claim`; #574 `**Status ...:** standing by, waiting on 71's independent
adversarial pass`; #516 `**Coordinator check ...** This is not the adversarial
review` (the comment says so in its own first line); #383 `## Addendum to
self-review gap #2`. Per-PR impact: **#632 (Tier A) counts 3 with the regex
and 2 without the false positive** — it would `PASS` today on a response
comment, not a review. #414 and #409 (`**PR review cycle complete** (self-review
+ adversarial review + ...)`) are single narrative summaries that count as one
artifact each; whether a summary is an artifact is a decision the plan should
make explicitly. Judgment: an ~3.5% false-positive rate is not acceptable for a
gate whose stated purpose is to make "a body that narrates a review count for
nothing" — the narrative simply moves from the body to a comment's first
line. Required fix: anchor to a heading/bold prefix and exclude
`response|correction|recheck|addendum|clarif|status|waiting` before the
keyword (the spec's first-revision form matched 170 because it demanded `#`
and the exact word; a prefix-optional form with a negative lookahead keeps
#625's three `**...**` lines and drops all eight above — verify against the
snapshot rather than trusting this sentence), then fix the Task 4 test
fixture count (B1) to whatever the new pattern yields and re-check #625, #614,
#632 by hand. Falsifier: show the eight lines above are review artifacts.

---

### Non-blocking observations

- N1. Task 1 step 4: "`grep -rn 'kalshi_client' tests/` returns nothing" is
  false — ten hits (`tests/test_run_tests_hook.py:79,85`,
  `tests/test_quality_audit.py:486,727,735`, `tests/test_kalshi_census.py:119-145`,
  `tests/test_observability.py:619`). None reads `guard_workflow`'s tuples
  (`grep -rn 'KALSHI_PATHS\|HOT_PATHS\|MONEY_UI_PATHS' tests/` is empty), so
  the substantive claim holds; the sentence should say that instead.
  `tools/quality_audit/kalshi_boundary.py:66-68` and
  `.claude/hooks/run_tests.py:60` also carry the deleted names as strings, by
  design (legacy-import detection / test-name maps) — unaffected by the edit.
- N2. Task 3 note: "#663 ... is Tier A through the pipeline-directory prose
  rule" — #663 also changed `tools/kanban_sync/labels.py`, so the path rule
  fires too. Harmless, but the sentence implies a single trigger.
- N3. Task 7 step 15's expected grep output omits a survivor: L38's
  unchanged second sentence still reads "every in-scope PR". It is coherent
  after the Scope rewrite (in scope = tiered), but the plan's "every surviving
  hit is scoped to Tier A (lines 43, 48, 49)" prediction is wrong on its face
  and an executor following it literally would "fix" L38 unnecessarily.
- N4. CLAUDE.md L130 ("a handspun tool defaults to disabled until its own run
  history proves real value") is cited by Deviation 5 to cut `outcomes`, but
  applies equally to `review-tier`, which Task 7 makes mandatory before every
  merge on day one. The plan's "Why any code at all" paragraph argues the
  exception; the rule text should state it (one clause: "`review-tier` is the
  measured exception, decided 2026-09-07") or L130 contradicts L40/L48 as
  rewritten.
- N5. Task 8 steps 2–3 quote phrases that wrap across lines 77–78 and 92–94 in
  the file; a literal single-line search will not find them. Say "spans lines
  N–M" or supply the wrapped form.
- N6. Test-stem over-inclusion is upward-only and measured to move zero PRs in
  the window (verified: disabling `_test_file_stem_match` changes no
  classification), but stems `config`, `settings`, `history`, `main`, `db`,
  `auth`, `position`, `kalshi` are broad (`tests/test_history_push.py` is Tier A
  via stem `history`), and `ci-`/`woodpecker-` produce dead stems. Cosmetic.
- N7. Task 3's fixture docstring says the seven Tier B PRs touch "nothing ...
  the data plane"; #273 writes recovered `raw_trades` rows into a data DB (its
  diff, lines 44-50). Spec §3.2 already moves #273 to A via rule 3 on its diff,
  so the file-only fixture is consistent, but the docstring overstates.
- N8. `_LANE3_IMPORT_RE` is a regex over raw text; a docstring line starting
  `from services.x` would count (none exists today —
  `services/position/routes.py:8` starts with "convention:" and is skipped).
  The `ast` fix in B2 removes this class.

---

### What was checked and found correct

- **Code compiles and runs (attack 1):** 33 of 34 Task 1–4 tests pass against
  the verbatim implementation; no NameError/AttributeError; `str.startswith`
  is given tuples everywhere; `path.endswith(REVIEW_TIER_A_CODE_SUFFIXES)` is a
  tuple; the underscore-boundary stem logic behaves as claimed
  (`test_index_feed_backfill.py` → B, `test_strategy_engine_gate.py` → A);
  `reasons == ["path: services/risk_manager.py (under services/risk_manager.py)"]`
  exactly.
- **Exists-on-disk (attack 2):** every one of the 53 `REVIEW_TIER_A_PATHS`
  entries resolves (`scripts/ci-*` and `scripts/woodpecker-*` via glob);
  `config/settings.yaml` and `services/config/` are present in the constant.
- **Hook subset (attack 3):** the hook's tuples at `guard_workflow.py:76-89`
  are exactly as the plan quotes; today the uncovered entries are precisely
  `services/kalshi_client.py`, `services/kalshi_account_client.py`,
  `services/kalshi_trade_ws.py` (five tuple slots, three files); after the
  plan's removal the subset relation holds entry by entry; all three files are
  absent from disk; `services/kalshi/account_client.py` (Task 7 step 10's
  replacement) exists.
- **Line anchors (attack 4):** all thirteen CLAUDE.md anchors (3, 38, 40, 43,
  46, 48, 49, 60, 100, 103, 107, 117, 120) are the lines the plan says, and every
  quoted "replace this phrase" string is present verbatim (checked with
  `grep -nF`); L104 and L108 are blank/heading lines so "after 103"/"after 107"
  land at section ends. `branching-and-ci.md` 63, 76–78, 88–94, 96–116 are
  the lines described (`## Git history` begins at 118). Checkpoint step 9's
  quoted text is at `SKILL.md:79-81`.
- **Simulation (attack 5):** independent re-run over live file lists:
  plan window #255–#663 → 142 A / 58 B, code-typed (fix/feat/chore/refactor =
  84) 68/16; spec window #254–#662 → 141/59, 68/16; the 30 unreviewed PRs
  (research lines 230/232) → 23 A / 7 B with B = {273, 301, 308, 415, 445,
  498, 623}. #254 is `docs/next-action.md` + `docs/open-decisions.md` (B);
  #663 is A. `Dockerfile` (#415) is the only extensionless file in the window
  (Deviation 3 holds).
- **Dangerous Tier B (attack 6):** every Tier B code PR in the window was
  listed with its files (#273 #301 #308 #383 #415 #445 #498 #540 #548 #559
  #561 #594 #614 #623 #624 #627 #630 #650). None changes trading, risk,
  sizing, calibration, strategy, settlement, money display, auth, or reset
  behaviour. #614 (`fault_log.summary()`) is a direct Lane 3 dependency (B2)
  but not trading behaviour; #594 adds observability metrics only. The spec's
  own NO-GO trigger is not hit.
- **gh shapes (attack 8):** `gh api .../pulls/660/files --paginate --jq
  '.[].filename' | wc -l` → 124; `gh pr view 660 --json files` → 100;
  `gh api ... --repo ...` → `unknown flag: --repo`; `gh pr view 660 --json
  labels,closingIssuesReferences` returns both fields (closing #661);
  `GithubClient._run` appends `--repo` at `github_client.py:111`;
  `_is_transient` (`:60-72`) treats `HTTP 404` as non-transient, so Task 5's
  404 test raises `GithubCliError` on the first call as claimed;
  `FakeRunner.queue(stdout, returncode, stderr)` matches the tests' usage;
  `main()` dispatches `args.func(args)` (`__main__.py:313-322`);
  `_cmd_plan_candidates` exists at `:223` for the "add after" anchor.
- **Branch protection:** `gh api .../branches/main` → `protected: false`,
  `enforcement_level: off`; #615 is open and says exactly that; #613 is open
  with the two edits Task 7 steps 5 and 9 fold in.
- **Dangling references (attack 9):** `get_pr_meta`, `list_merged_prs`,
  `outcomes` appear only in the Deviations text, Task 5's explanatory
  paragraph, Task 8 step 5, Task 9 step 3, and the self-review — never in code
  or a Consumes/Produces block. Every symbol a later task uses is defined
  earlier. `tools/project_manifest` accepts `--check/--write/--repo-root`.
  `docs/open-decisions.md:26` carries the section Task 9 step 3 replaces.
- **Deviations 1–3, 5:** justified as stated. Deviation 4 is wrong (B2).
- **Contradiction sweep (attack 11):** `protect` appears only at CLAUDE.md:117
  and branching-and-ci.md:98/115, all rewritten by Tasks 7–8; the
  adversarial-review definition bullets (CLAUDE.md:44–45) define the pass, not
  when it is owed, and need no change; `.claude/skills/kanban-board-sync/
  SKILL.md:37` mentions companion review docs only descriptively.

### What could not be verified

- Tasks 5–6 were checked by reading `FakeRunner`, `_invoke`/`_run`,
  `_is_transient`, and `main()` against the tests, not by executing them (they
  need the repo's pytest environment). B3 was reproduced with the pure
  functions, which is what Task 6 calls.
- The plan's 261/224/37 snapshot: not reproducible here (265/229/36 live);
  whether four comments landed after the plan's measurement or the plan's
  window differed is unknown.
- "12 of the 24 unreviewed code PRs narrate a review in the body" is taken
  from research line 231, not re-derived.
- Why branch protection lapsed — the plan says it is not recoverable from the
  API; not re-investigated.
