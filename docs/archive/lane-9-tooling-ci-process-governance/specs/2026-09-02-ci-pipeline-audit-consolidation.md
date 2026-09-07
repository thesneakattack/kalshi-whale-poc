# Consolidation — CI pipeline audit (2026-09-02)

Reconciles the research artifact (`docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-ci-pipeline-audit*.md`,
4 documents), its same-session self-review
(`docs/archive/lane-9-tooling-ci-process-governance/specs/2026-09-02-ci-pipeline-audit-self-review.md`), and
an independent adversarial review (fresh Agent call, no memory of this
session, full transcript below) per CLAUDE.md's "nothing advances on one
pass" HARD RULE.

## Self-review findings

No internal contradiction found across the four documents; five load-bearing
claims (testmon fix location, `PRAGMA` injection site, the `slow`-marker
docstring, the `run_tests.py` mapping gaps for `app_state.py`/`kalshi/*`,
and the required-branch-protection-contexts list) were independently
re-derived from current source rather than trusted from the original
subagent reports. Full detail in the self-review document.

## Adversarial review findings

Independent Agent call, given Bash access to the same repo/worktree/live
Woodpecker server and told to assume the artifact wrong until re-derived.
It re-verified, from primary sources (not the document's own tables):

- The testmon deactivation mechanism, byte-for-byte, in the installed
  `testmon==2.2.0` source, plus a **live** decoded pipeline log (#348)
  showing the exact deactivation message and passing test count/timing the
  document cites.
- The `run_tests.py` mapping gaps for `services/app_state.py` (traced the
  code by hand, confirmed zero matching test files on disk) and
  `services/kalshi/{websocket,public}.py` (confirmed exactly 18 matching
  files, exactly 387 `def test_` occurrences across them).
- The `/dev/shm` `noexec` mount (confirmed directly) and **independently
  reproduced** both the tmpfs speedup (on `test_fault_log.py`, different
  absolute number than the original run — 4.39s→0.24s vs. 12.79s→0.26s,
  expected given different host load / single-file vs full-suite scope,
  same order of magnitude and identical mechanism) and the exact 12-test
  `test_cleanup_worktrees.py` breakage with the identical `gh CLI is not
  authenticated` error text.
- That `PRAGMA synchronous=OFF`'s injection point (`_guarded_connect`,
  confirmed at the correct `runtime_isolation.py:245-250`) is genuinely
  test-only (only ever invoked from `tests/conftest.py`) and does not
  interact with `test_capture_writer`'s lock-retention tests, since SQLite's
  locking/`SQLITE_BUSY` behavior under WAL is governed by transaction mode,
  not the `synchronous` pragma.
- Three of the four cost-analysis structural-waste claims (testmon
  inertness live, zero manual-pipeline runs live, no `when.path` filter on
  `tests-dependency-audit.yml`), plus the required-branch-protection-
  contexts list cross-checked against **both** the repo's own rule file and
  a live `gh api .../protection` call.
- That none of the four whole-repo-scan tests targeted by Tier 1 #3 are
  currently marked `slow` — confirming that recommendation is a real,
  unapplied fix rather than already-done busywork.

**Verdict returned: GO WITH FIXES.** One fix required: a stale line-number
citation (`runtime_isolation.py:207-216`, which is actually an unrelated
function) in the pytest-profile companion document's §3.1 — the main
synthesis document already cited the correct `:245-250` throughout, so this
did not affect any recommendation, only a supporting citation.

One process note the adversarial review raised for explicit acknowledgment
rather than as a defect: whether injecting a `PRAGMA` into an existing
function's body is as unambiguously "mechanical" (CLAUDE.md's exemption
from the full research→design→plan cycle) as adding a CLI flag or a marker
decorator. Judgment: yes — it is a single, well-scoped statement inside a
function whose only caller is `tests/conftest.py`, adds no branching, and
cannot reach live data (confirmed independently by both reviews), so it
stays classified as mechanical. Recorded here rather than silently accepted,
per the reviewer's ask.

## Fix applied

`docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-ci-pipeline-audit-pytest-profile.md`
§3.1: corrected `tests/support/runtime_isolation.py:207-216` →
`tests/support/runtime_isolation.py:245-250`. Verified post-fix: the only
remaining occurrence of `:245-250` in that file is the corrected line, and
the main synthesis document's own citation of the same location was already
correct and unchanged.

## GO / no-go

**GO.** No open finding blocks merge. The one required fix has been applied
and re-checked; no disagreement between the two reviews needed adjudication
(both reached GO/GO-with-fixes independently, and the one fix the
adversarial review flagged was accepted outright, not contested). Proceeding
to commit, push, and open the PR with the `phase:research` label.

---

## Full adversarial review transcript (verbatim)

Verdict: GO WITH FIXES

Every load-bearing claim I independently re-derived checked out — several to the exact line, exact byte, or exact error string. I found one real defect (a stale cross-reference in a companion doc) and one process point worth flagging in consolidation, neither of which undermines the audit's recommendations. This is unusually well-verified work.

### Item 1 — testmon claim: CONFIRMED

Read `configure.py` directly inside the `fastapi` container (`testmon==2.2.0`, confirmed via `importlib.metadata`). `_get_noselect_reasons` at exactly lines 65-85 returns `["-m was used"]` whenever `options["markexpr"]` is set, unless `testmon_forceselect` or `testmon_noselect` fires first — byte-for-byte what the doc quotes. Traced the message assembly through `_header_collect_select` → `_formulate_deactivation("selection", noselect_reasons)` in `pytest_testmon.py`, and confirmed `--testmon-forceselect`'s help text matches the doc's quote verbatim.

Read `scripts/ci-testmon-run.sh:53` in the worktree right now: `python -m pytest --testmon -n 4 -m "not slow"` — no `--testmon-forceselect`/`--testmon-noselect`. Current state matches the claim exactly.

Then went further than the doc asked and pulled a live log: `GET /api/repos/1/logs/348/5167` for pipeline #348 (a real feature-branch push) shows, verbatim: `testmon: selection automatically deactivated because -m was used, environment: default`, followed by `3057 passed, 17 skipped ... in 159.66s` — matching the doc's own citation of that exact pipeline/timing number.

### Item 2 — run_tests.py mapping gap: CONFIRMED

Read `.claude/hooks/run_tests.py` and hand-traced `tests_for()`:
- `services/app_state.py`: `p.parts = ('services','app_state.py')`, so `len(p.parts) > i+2` is `2 > 2` = False — the package-name branch never fires, only the bare `app_state` stem is globbed. `ls tests/ | grep -i app_state` → no matches. Zero tests, confirmed.
- `services/kalshi/websocket.py`: `len(p.parts) > i+2` is `3 > 2` = True, adds `"kalshi"` to the stem set, globs `test_kalshi*.py`. `ls tests/test_kalshi*.py` → exactly 18 files. Counted `def test_` across all 18 → exactly 387. Both numbers match the doc precisely; `public.py` hits the identical code path.

### Item 3 — fsync/tmpfs/noexec claim: CONFIRMED (and reproduced live)

- `mount | grep shm` in the fastapi container: `tmpfs (rw,nosuid,nodev,noexec,relatime,size=65536k)` — noexec confirmed.
- `chmod +x` on a script placed in `/dev/shm` and executed it directly: `Permission denied`, exit 126 — exact match.
- Reproduced the speedup myself: `tests/test_fault_log.py`'s slow test dropped from 4.39s → 0.24s call time (default basetemp vs `/dev/shm`) — different absolute numbers than the doc's 12.79s→0.26s (different point-in-time host load, single-file vs full-suite run), but same order-of-magnitude, same mechanism.
- Reproduced the breakage myself: ran `tests/test_cleanup_worktrees.py` under the same tmpfs basetemp → exactly 12 tests failed, every one with `error: gh CLI is not authenticated - cannot check PR state` — the identical error text and identical count the doc reports.
- Confirmed `_guarded_connect` sits at lines 245-250 of `tests/support/runtime_isolation.py` exactly as the main doc cites, and that `install_runtime_isolation()` is only ever invoked from `tests/conftest.py` (never production code) — so the recommended `PRAGMA synchronous=OFF` genuinely cannot reach live data.
- Checked the "lock-retention tests unaffected by `synchronous`" claim: `_hold_write_lock()` in `test_capture_writer.py` acquires its lock via `BEGIN IMMEDIATE` under WAL — `PRAGMA synchronous` only governs fsync timing on commit, not SQLite's locking/`SQLITE_BUSY` semantics. The claim is technically sound.
- Confirmed the "36ms floor" docstring text at `test_capture_writer.py:265-266` verbatim.

One real defect found: the pytest-profile companion doc's §3.1 recommendation cites `runtime_isolation.py:207-216` for `_guarded_connect` — but lines 207-216 are actually `pinned_config_get`/`_redirect_config_store`, an unrelated function. The main synthesis doc (the actual deliverable) cites the correct `:245-250` and that's what I verified as accurate. This is a stale citation in a supporting doc only — it doesn't affect the main doc's Tier 1 recommendation, which points at the right place.

### Item 4 — CI structural-waste claims: CONFIRMED (3 spot-checks, exceeding the "at least 2" ask)

- (a) Live pipeline #348's decoded log carries the deactivation header verbatim (see Item 1).
- (b) `GET /api/repos/1/pipelines?event=manual` → `[]`. Also confirmed the token's permissions live: `{"pull":true,"push":false,"admin":false}`, and server version `3.18.0` — matching the doc's stated environment exactly.
- (c) Read `.woodpecker/tests-dependency-audit.yml` directly: `when: - event: [push, pull_request]`, no `path` sub-key anywhere in the 15-line file.
- Cross-checked the required-contexts list against both `.claude/rules/branching-and-ci.md` (read directly) and the live GitHub API (`gh api .../branches/main/protection --jq '.required_status_checks.contexts'`): both list the same 5 contexts, both exclude `quality-frontend-build`. Matches the doc exactly.
- Bonus: confirmed one of the doc's cited `docs/woodpecker-ci.md` drift claims — the doc's own line 170 says "trusted... all true," while the live API returns `{'network': False, 'volumes': True, 'security': True}`. Drift confirmed.
- Bonus: confirmed the "duplicate scanner" reasoning behind Tier 1 #3 — `tools/quality_audit/__main__.py` imports and registers both `scan_kalshi_boundary` and `scan_unit_cost_derivations` in `_SCANNERS`, which `quality-architecture-audit.yml` already runs unconditionally on every push/PR. I also confirmed none of the four whole-repo-scan tests are currently marked `@pytest.mark.slow` (checked each definition site directly), so Tier 1 #3 is a real, unapplied fix, not something already done.

### Item 5 — Overall assessment

The Tier 1/Tier 2 split holds up: every Tier 1 item is genuinely mechanical (a flag, a pragma in test-only code already gated against live data, a marker decorator, a doc correction) and every Tier 2 item names a real, previously-decided tradeoff (required CI context, deliberate test duplication, an infra change) rather than dressing up a design decision as mechanical. I found no fabricated file path, line number, command output, or number across the ~15 specific citations I checked — an unusually clean hit rate for this kind of report.

One process point for the consolidation stage to note explicitly (not a factual defect): whether "add a PRAGMA execution inside an existing function" clears CLAUDE.md's "mechanical... not new logic" bar as cleanly as "add a CLI flag" or "add a marker decorator" does — it's a one-line, well-scoped, test-only change with no branching, so I'd call it mechanical too, but the two other Tier 1 items are more unambiguously so. Worth a one-line acknowledgment in the PR rather than silence, not a reason to reclassify it.

### Fixes required before merge

1. Correct the stale `runtime_isolation.py:207-216` citation in `docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-ci-pipeline-audit-pytest-profile.md` §3.1 to `:245-250` (the location the main doc already cites correctly).

No other corrections needed. I did not find grounds to reproduce or verify anything beyond what's listed here that would change the verdict — the two named "could not answer" gaps (cron token scope, the never-started-workflow edge case) are honestly disclosed as blocked on a higher-scoped token, not glossed over.
