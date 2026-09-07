# CI Pipeline Audit Tier 1 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the four mechanical, high-confidence fixes identified and
adversarially verified by the merged CI pipeline audit (PR #440): restore
real per-push test selection, remove proven sqlite fsync overhead from the
pytest suite, stop four tests duplicating an already-required CI job, and
correct three drifted claims in `docs/woodpecker-ci.md`.

**Architecture:** Four independent, non-overlapping file changes (a CI
shell script flag, a test-only sqlite pragma, four `@pytest.mark.slow`
decorators, three doc-prose corrections). No production code, trading
logic, or CI pipeline structure changes — every fix is additive/corrective
to an existing, already-designed mechanism. Each task is independently
testable and independently revertible; none depends on another.

**Tech Stack:** pytest / pytest-testmon 2.2.0, sqlite3, POSIX shell
(Woodpecker `commands:`), Markdown.

**Spec:** `docs/superpowers/research/2026-09-02-ci-pipeline-audit.md`
(merged PR #440) — the "Prioritized action plan / Tier 1" section is the
design specification this plan implements; each task below cites the
specific bullet it comes from. That document's own adversarial review
(`docs/superpowers/specs/2026-09-02-ci-pipeline-audit-consolidation.md`)
already independently re-derived and confirmed the mechanism behind every
fix here from primary sources (installed package source, live CI logs, a
live container `mount` check, and reproduced measurements) — this plan
does not re-litigate those findings, it implements them.

## Global Constraints

- No trading, risk, sizing, calibration, strategy, settlement, auth, or
  CI-credential code is touched by any task (CLAUDE.md safety invariant) —
  every file in scope is test/CI/doc infrastructure.
- The sqlite pragma change (Task 2) must only ever reach a test-issued
  connection, never a real `data/*.db` path — verified by construction
  (the injection point is inside `tests/support/runtime_isolation.py`'s
  `_guarded_connect`, whose installer `install_runtime_isolation()` is
  invoked only from `tests/conftest.py` and `tests/support/e2e_server.py`
  — both test-only infrastructure, confirmed by grep during this plan's
  adversarial review) and re-checked in Task 2's own verification step.
- Every task ends with the full local test suite passing
  (`3058 passed, 16 skipped` today under `-n 4 -m "not slow" -p no:testmon`
  inside the `fastapi` ddev container) before its commit — a regression in
  one task's change must not be masked by a later task's commit.
- One commit per task (this repo's convention for a small numbered plan on
  one branch), in the order below — Task 1 through Task 4 are file-disjoint
  so any order is safe, but committing in this order keeps the highest-
  value fix (Task 1) first in `git log` if only one lands.
- Branch: `chore/ci-audit-tier1-fixes` (already created, worktree at
  `.claude/worktrees/ci-audit-tier1-fixes`).

---

### Task 1: Restore real testmon selection on feature-branch pushes

**Files:**
- Modify: `scripts/ci-testmon-run.sh:53`

**Interfaces:** None — self-contained shell script change, no other task
depends on this one.

**Background (from the spec):** `pytest-testmon` fingerprints which lines
each test actually exercised and should only re-run tests whose exercised
lines changed since the last run on that branch. Since it shipped
(2026-08-26) it has never once selected — every push runs the full suite
anyway, plus ~24s of pure coverage-tracing overhead for nothing. Root
cause, confirmed against the installed `testmon==2.2.0` source
(`configure.py:65-85`, `_get_noselect_reasons`): passing `-m` (a marker
expression) unconditionally deactivates selection unless
`--testmon-forceselect` or `--testmon-noselect` is also passed. This
script already passes `-m "not slow"` without either flag.

- [x] **Step 1: Confirm current (broken) behavior locally**

Run inside the ddev `fastapi` container, from the repo root (this container
has no cached `.testmondata` for this branch, so this simulates a branch
with a stale/mismatched cache — the important thing is the header line,
not whether it selects on this particular run):

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && rm -f .testmondata && python3 -m pytest --testmon -n 4 -m "not slow" -v 2>&1 | head -8'
```

(`-v`, not `-q` — `-q` suppresses testmon's own `pytest_report_header` line
entirely, confirmed by direct isolation during this plan's own adversarial
review: a full-log grep for "testmon" over a `-q` run returns zero matches.
Without `-q` the header line is the first thing printed.)

Expected: the first line of output is
`testmon: selection automatically deactivated because -m was used, environment: default`
(or equivalent — the exact phrasing is testmon's own; the key fact is the
word "deactivated"). This confirms the bug reproduces on this exact branch
before the fix.

- [x] **Step 2: Apply the fix**

In `scripts/ci-testmon-run.sh`, line 53, change:

```sh
python -m pytest --testmon -n 4 -m "not slow"
```

to:

```sh
python -m pytest --testmon --testmon-forceselect -n 4 -m "not slow"
```

`--testmon-forceselect`'s own `--help` text (confirmed against the
installed package): "Run testmon and select only tests affected by changes
and satisfying pytest selectors at the same time" — this is the exact flag
`configure.py`'s deactivation check looks for first, before it ever gets to
the `-m`-triggered deactivation branch.

- [x] **Step 3: Confirm the fix locally**

Same command as Step 1, run again (still no cached `.testmondata`, so this
is testmon's honest "everything is new, run it all" first-run behavior —
which is correct and expected, not a bug):

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && rm -f .testmondata && python3 -m pytest --testmon --testmon-forceselect -n 4 -m "not slow" -v 2>&1 | head -8'
```

Expected: the deactivation line is **gone**. The header should instead show
something like `changed files: N, unchanged files: 0, environment: default`
(a first run with no prior `.testmondata` — everything is "changed" since
there is no baseline, so the full suite still runs this time, correctly).
This is the same first-push behavior the spec already documents as expected
and safe — the fix is about every push *after* the first one on a branch,
which needs a real prior `.testmondata` to demonstrate live (see Step 4).

- [x] **Step 4: Demonstrate real selection (not just the flag) locally**

Run once more with the `.testmondata` this second run just produced still
in place, but touch a file with no executable-line changes a covered test
would notice (a comment-only edit) to confirm testmon recognizes "nothing
relevant changed":

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && echo "# noop" >> tools/soak_analyzer.py && python3 -m pytest --testmon --testmon-forceselect -n 4 -m "not slow" -v 2>&1 | head -8'
```

Then revert the noop edit as a **separate, plain host command** — do not
chain a `git checkout` onto the `docker exec ... sh -c '...'` line above.
A linked worktree's `.git` file stores an absolute *host* path to its real
gitdir, which does not resolve inside the container's `/app` mount even
though the same content is reachable there — any `git` command run via
`docker exec` against this worktree fails with `fatal: not a git
repository`, exactly the trap this repo's own `ddev exec` guidance already
warns about for a different command (confirmed by direct reproduction
during this plan's adversarial review). Run this on the host instead,
exactly like Step 5's `git status --short` already does:

```
git checkout -- tools/soak_analyzer.py
```

Expected: header shows `changed files: 1, unchanged files: N` (or similar —
exact wording may vary by testmon version display, the key evidence is a
nonzero "unchanged"/"deselected" count now appearing, which never appeared
before this fix on any of the 27 CI logs the audit sampled) and a much
smaller subset of tests actually running, not all 3058. This is the
concrete "it now works" proof the audit's own recommendation asked for.

- [x] **Step 5: Clean up local experiment artifacts**

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && rm -f .testmondata'
git status --short
```

Expected: clean except for the one-line change to `scripts/ci-testmon-run.sh`
(the `.testmondata` file and `tools/soak_analyzer.py`'s noop edit are both
git-ignored/reverted — confirm neither shows up in `git status`).

- [x] **Step 6: Run the full local suite to confirm no regression**

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest -n 4 -m "not slow" -p no:testmon -q 2>&1 | tail -5'
```

Expected: `3058 passed, 16 skipped` (unchanged from the audit's own
baseline — this task changes nothing about which tests exist, only how
`--testmon` decides to run them on a real push).

- [x] **Step 7: Commit**

```bash
git add scripts/ci-testmon-run.sh
git commit -m "fix: restore real pytest-testmon selection on feature-branch pushes

-m \"not slow\" unconditionally deactivates testmon's test selection
(configure.py:65-85 in the installed testmon==2.2.0), confirmed against
27/27 sampled push-event CI logs showing zero selections since this
script shipped 2026-08-26. --testmon-forceselect re-enables selection
while keeping the marker exclusion. docs/superpowers/research/2026-09-02-ci-pipeline-audit.md
Tier 1 #1."
```

---

### Task 2: Remove sqlite fsync overhead from test connections

**Files:**
- Modify: `tests/support/runtime_isolation.py:245-250`

**Interfaces:** None — self-contained, test-infrastructure-only change.
Every other test file in the suite is an indirect beneficiary (any test
that opens a sqlite connection through the existing guard) but no other
task in this plan touches this file or depends on its internals.

**Background (from the spec):** 51.5% of the pytest suite's summed test
time (246.2s of 477.7s) is in 258 tests over 0.5s each, almost all
sqlite-backed. Direct falsification (run twice, independently, by two
different sessions in the audit) proved this is fsync-bound: the same
tests ran 45-49x faster under a tmpfs `--basetemp`. Raw tmpfs is not safe
here (this container's `/dev/shm` is `noexec`, which breaks 12 tests that
exec real subprocess shims from `tmp_path`) — the recommended fix is
`PRAGMA synchronous=OFF` on the connection itself, which achieves the same
removal of fsync cost without touching the filesystem. This is safe
specifically because `_guarded_connect` (the function being modified) is
the **only** function `install_runtime_isolation()` wires in place of
`sqlite3.connect` for the pytest process, and it already distinguishes and
hard-refuses any path under the real `data/` directory before returning a
connection — so a `PRAGMA` added inside it can only ever apply to a test
connection, never a live one. `PRAGMA synchronous` governs fsync timing on
commit only — it does not change SQLite's locking or `BEGIN
IMMEDIATE`/`SQLITE_BUSY` semantics, which is why `test_capture_writer.py`'s
lock-retention tests are unaffected (independently confirmed by the
audit's adversarial review).

- [x] **Step 1: Read the current function**

Read `tests/support/runtime_isolation.py` lines 240-256 (already open from
the audit — `_original_connect`, `_guarded_connect`, `_install_sqlite_guard`)
to confirm the exact current text before editing (line numbers may have
shifted by a line or two since the audit if anything else in this file
changed on `main` in the meantime — check with `grep -n "_guarded_connect"
tests/support/runtime_isolation.py` first and use the real line number, not
a hardcoded assumption).

- [x] **Step 2: Measure the current (slow) baseline for the two
representative files**

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest tests/test_fault_log.py tests/test_signal_resolution.py -p no:testmon -q --durations=3'
```

Record the two slowest call-phase durations shown (expect roughly the
audit's own last-measured 4-13s range for the heaviest test in each file —
exact numbers vary run to run under host load, that's expected and not a
regression signal).

- [x] **Step 3: Apply the fix**

In `tests/support/runtime_isolation.py`, inside `_guarded_connect` (the
function that currently reads, per the audit's citation of `:245-250`):

```python
def _guarded_connect(database, *args, **kwargs):
    if _is_repo_data_path(database):
        raise AssertionError(
            f"pytest attempted to open live repository data: {database}"
        )
    return _original_connect(database, *args, **kwargs)
```

change the return to open the connection, disable synchronous writes (test
speed only — this connection has already been proven not to be a live
`data/` path by the check immediately above), and return it:

```python
def _guarded_connect(database, *args, **kwargs):
    if _is_repo_data_path(database):
        raise AssertionError(
            f"pytest attempted to open live repository data: {database}"
        )
    conn = _original_connect(database, *args, **kwargs)
    conn.execute("PRAGMA synchronous=OFF")
    return conn
```

Do not add a `try/except` around the `PRAGMA` call — every path this
function returns from is a connection this process just opened itself, so
the pragma cannot fail in a way worth silently swallowing; a failure here
should surface exactly like any other test-infra breakage.

- [x] **Step 4: Re-measure the same two files**

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest tests/test_fault_log.py tests/test_signal_resolution.py -p no:testmon -q --durations=3'
```

Expected: both previously-slowest durations drop by roughly an order of
magnitude (the audit measured 49x and 45x; the adversarial review
independently reproduced a smaller but still order-of-magnitude drop under
different host load — treat "roughly 10x or more" as the pass bar, not an
exact number, per this repo's dimensional-analysis discipline: don't assert
a precise multiplier you haven't just measured on this exact run).

- [x] **Step 5: Run the full local suite and time it**

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && time python3 -m pytest -n 4 -m "not slow" -p no:testmon -q 2>&1 | tail -8'
```

(If the container's `sh` lacks a `time` builtin, as observed earlier this
session, wrap with `date +%s.%N` before/after instead — either way, capture
a real wall-clock number for the commit message.)

Expected: `3058 passed, 16 skipped` (identical pass/skip counts to the
pre-change baseline — this must not change which tests pass, only how fast
they run) with a materially shorter wall time than the ~127-136s baseline.
If any test fails that passed before, stop and investigate under
`superpowers:systematic-debugging` before proceeding — do not weaken the
assertion or catch the failure to force a green run.

- [x] **Step 6: Confirm the safety property by construction one more time**

```
grep -n "_is_repo_data_path\|PRAGMA synchronous" tests/support/runtime_isolation.py
```

Confirm the `PRAGMA` line appears strictly after the `_is_repo_data_path`
check in the function body (i.e., it can only execute once a connection has
already been proven not to target real repo data).

- [x] **Step 7: Commit**

```bash
git add tests/support/runtime_isolation.py
git commit -m "perf: disable sqlite fsync on test connections (PRAGMA synchronous=OFF)

~51%% of pytest's suite time is sqlite fsync overhead, confirmed by direct
falsification (45-49x speedup under tmpfs in the CI audit, PR #440) - this
applies the same fix at the connection level instead, since this
container's /dev/shm is noexec and breaks subprocess-shim tests under a
raw tmpfs basetemp. Scoped to _guarded_connect, which only ever returns
test connections (real data/ paths are hard-refused immediately above);
locking/SQLITE_BUSY semantics are governed by transaction mode, not this
pragma, so test_capture_writer's lock-retention tests are unaffected.
docs/superpowers/research/2026-09-02-ci-pipeline-audit.md Tier 1 #2."
```

---

### Task 3: Mark the four whole-repo-scan tests `slow`

**Correction (2026-09-03, found during this task's own review):** only 2
of these 4 tests turned out to be genuinely duplicated by required CI
coverage — see
`docs/superpowers/research/2026-09-02-ci-pipeline-audit.md`'s Tier 1 #3
addendum and this plan's own SDD ledger
(`.superpowers/sdd/2026-09-03-ci-pipeline-audit-tier1-fixes/progress.md`,
gitignored) for the full account. The task's steps below are preserved as
originally written for the historical record of what was planned; the
actual commit (`e5b43b2`) marks only `test_unit_cost_scanner_is_clean_on_this_repo`
and `test_module_never_reads_deprecated_direction_aliases_directly`.

**Files:**
- Modify: `tests/test_kalshi_census.py` (add `import pytest`; add
  `@pytest.mark.slow` above two tests)
- Modify: `tests/test_quality_audit.py` (add `@pytest.mark.slow` above one
  test — `import pytest` already present at line 18)
- Modify: `tests/test_historical_data_backfill.py` (add
  `@pytest.mark.slow` above one test — `import pytest` already present at
  line 27)

**Interfaces:** None — decorator-only change, no other task touches these
files.

**Background (from the spec):** `pytest.ini`'s `slow` marker is defined as
"scans the real repo tree end-to-end (redundant with a dedicated CI job);
excluded by default, run with `-m slow` to include." Four tests already
match this definition verbatim but were never marked: two in
`test_kalshi_census.py` (`build_census(REPO_ROOT)`, parsing every file in
the repo twice each), one in `test_quality_audit.py`
(`scan_unit_cost_derivations(REPO_ROOT)`), and one in
`test_historical_data_backfill.py` (a whole-repo `_scan_known_field_reads`
call to check a single file). All four duplicate checks that
`quality-architecture-audit.yml`'s own required, independent CI job already
runs on every push/PR — the adversarial review confirmed that job's
`_SCANNERS` registration covers the same scanners these tests re-invoke,
and confirmed none of the four are currently marked `slow`.

- [x] **Step 1: Confirm current (unmarked) state and current selected-test
count**

```
grep -n "^def test_real_repo_census_runs_and_legacy_caller_count_stays_zero\|^def test_real_repo_fixtures_all_have_existing_source_docs" tests/test_kalshi_census.py
grep -n "^def test_unit_cost_scanner_is_clean_on_this_repo" tests/test_quality_audit.py
grep -n "^def test_module_never_reads_deprecated_direction_aliases_directly" tests/test_historical_data_backfill.py
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest --collect-only -q -m "not slow" -p no:testmon 2>&1 | tail -3'
```

Confirm none of the four `grep` results are preceded by a `@pytest.mark.slow`
line (read a few lines above each match if unsure), and record the
"N/M tests collected (K deselected)" line from the collect-only run as the
before-count.

- [x] **Step 2: Add the missing import in `test_kalshi_census.py`**

That file has no `import pytest` today. Add it alongside the existing
imports near the top of the file:

```python
from __future__ import annotations

import json
import pytest
from pathlib import Path
```

(`pytest` is third-party, not stdlib — this file doesn't separate the two
into distinct blocks today, so just insert the line after `import json` as
shown above; check the exact current import block with
`sed -n '11,20p' tests/test_kalshi_census.py` before editing, since this
plan's line numbers are from the audit and may have drifted by the time
this task runs).

- [x] **Step 3: Mark the two `test_kalshi_census.py` tests**

Immediately above each of these two `def` lines, add `@pytest.mark.slow`
on its own line directly preceding the `def` (matching the exact style
already used at `tests/test_quality_audit.py:634` and `:767` — a bare
decorator line, no blank line between it and `def`):

```python
@pytest.mark.slow
def test_real_repo_census_runs_and_legacy_caller_count_stays_zero():
```

```python
@pytest.mark.slow
def test_real_repo_fixtures_all_have_existing_source_docs():
```

- [x] **Step 4: Mark the `test_quality_audit.py` test**

```python
@pytest.mark.slow
def test_unit_cost_scanner_is_clean_on_this_repo():
```

- [x] **Step 5: Mark the `test_historical_data_backfill.py` test**

```python
@pytest.mark.slow
def test_module_never_reads_deprecated_direction_aliases_directly():
```

- [x] **Step 6: Confirm the four tests are now excluded by default and
still pass under `-m slow`**

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest --collect-only -q -m "not slow" -p no:testmon 2>&1 | tail -3'
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest -m slow -p no:testmon -q 2>&1 | tail -8'
```

Expected: the first command's "deselected" count is exactly 4 higher than
Step 1's before-count (2 pre-existing `slow` tests → 6 total); the second
command runs and **passes** at least these four tests (it will also
include the two pre-existing `slow` tests from `test_quality_audit.py` —
that's correct, not a bug).

- [x] **Step 7: Run the full default-marker suite to confirm the expected
reduction and zero regressions**

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest -n 4 -m "not slow" -p no:testmon -q 2>&1 | tail -5'
```

Expected: pass count drops by exactly 4 from the `3058 passed` baseline
(the four now-excluded tests), skip count unchanged at 16, zero failures.

- [x] **Step 8: Confirm `quality-architecture-audit.yml` still covers the
same checks independently (no coverage loss)**

```
grep -n "_SCANNERS\|scan_kalshi_boundary\|scan_unit_cost_derivations" tools/quality_audit/__main__.py .woodpecker/quality-architecture-audit.yml
```

Confirm both scanners this task's tests exercise are still wired into
`quality-architecture-audit.yml`'s own step, unaffected by this change
(this task never touches that file) — the coverage these four tests
provided moves from "duplicated in two places" to "covered once, where it
was already independently required," it does not disappear.

- [x] **Step 9: Commit**

```bash
git add tests/test_kalshi_census.py tests/test_quality_audit.py tests/test_historical_data_backfill.py
git commit -m "test: mark four whole-repo-scan tests slow (duplicate an already-required CI job)

All four already match pytest.ini's own slow-marker definition
verbatim and duplicate checks quality-architecture-audit.yml's
required, independent job already runs on every push/PR
(tools.quality_audit's kalshi_boundary/unit_cost scanners) - recovers
43.6s of pytest suite time for zero coverage loss.
docs/superpowers/research/2026-09-02-ci-pipeline-audit.md Tier 1 #3."
```

---

### Task 4: Correct drifted claims in `docs/woodpecker-ci.md`

**Files:**
- Modify: `docs/woodpecker-ci.md` (three separate prose corrections, each
  cited below by current content to search for — line numbers may have
  shifted slightly since the audit if anything else in this doc changed;
  search by the quoted text, not a hardcoded line number)

**Interfaces:** None — pure documentation, no code or test impact.

**Background (from the spec):** Three claims in this doc no longer match
live reality, all confirmed independently by the audit's adversarial
review against the live Woodpecker API: (1) the manual-trigger section
says no workflow ever runs on a manual pipeline, which stopped being true
for `tests-pytest` specifically on 2026-08-28 (`event: manual` was added
to that one workflow's `when:`); (2) the doc claims `trusted: {network,
volumes, security}` are "all true," while the live API returns
`network: false`; (3) a historical incident narrative cites specific
pipeline numbers (240/241) that no longer resolve via the live API because
the server's pipeline-number sequence was reset on 2026-08-31 — the
narrative itself is still accurate history, it just can no longer be
verified by clicking through to those exact numbers today.

- [x] **Step 1: Locate and read the current text around each claim**

```
grep -n "A manually triggered pipeline\|trusted:.*network.*volumes.*security\|pipelines 240" docs/woodpecker-ci.md
```

Read 10 lines of context around each match before editing, since exact
wording/line numbers may have shifted since the audit.

- [x] **Step 2: Fix the manual-trigger claim**

Find the paragraph beginning "**A manually triggered pipeline
(`scripts/woodpecker-trigger`, the "Run pipeline" UI button, or a raw
`POST /api/repos/{id}/pipelines`) carries `event: manual`, which none of
these workflows' `when: event: [push, pull_request]` filters match**" and
replace it with:

```markdown
**A manually triggered pipeline (`scripts/woodpecker-trigger`, the "Run
pipeline" UI button, or a raw `POST /api/repos/{id}/pipelines`) carries
`event: manual`.** `tests-pytest.yml` has matched this event since
2026-08-28 (`when: event: [push, pull_request, manual]`) and runs its full,
unscoped suite on a manual trigger — see that file's own header comment for
why (an explicit ask for confidence, never testmon-scoped). The other five
workflows still only match `[push, pull_request]`, so a manual trigger
today produces exactly one workflow (`tests-pytest`) and posts exactly one
GitHub status (`ci/woodpecker/manual/tests-pytest`) — confirmed live: 0 of
314 retained pipelines have ever actually been triggered this way (CI
pipeline audit, 2026-09-02), so this capability is wired but unused.
Verifying the other five workflows still requires a real push, or
temporarily broadening a workflow's `when:` to include `event: manual`
while testing.
```

- [x] **Step 3: Fix the trusted-network claim**

Find "shows `trusted: {network, volumes, security}` all true)." and replace
with:

```markdown
shows `trusted: {"network": false, "volumes": true, "security": true}` —
this repo is trusted for volumes and security, but not network egress from
step containers; re-check live if a future step needs outbound network
access).
```

- [x] **Step 4: Add a resolvability caveat to the pipeline-number
narrative**

Find the sentence citing "pipelines 240 and 241" and add, immediately
after that paragraph (as its own short note, not edited into the existing
sentence — the historical narrative itself stays accurate and unchanged):

```markdown
  (Note, added 2026-09-03: the server's pipeline-number sequence was reset
  on 2026-08-31 — pipelines 240/241 and other pre-reset numbers cited
  elsewhere in this doc no longer resolve via the live API. The incident
  and its lesson above are still accurate history, just no longer
  independently re-verifiable by number.)
```

- [x] **Step 5: Confirm the edits render correctly and nothing else nearby
broke**

```
sed -n '75,95p' docs/woodpecker-ci.md
grep -n "trusted:" docs/woodpecker-ci.md
grep -n "pipelines 240" docs/woodpecker-ci.md
```

Read the output and confirm each section reads coherently in context (no
dangling markdown, no orphaned sentence fragments from the replacement).

- [x] **Step 6: Commit**

```bash
git add docs/woodpecker-ci.md
git commit -m "docs: correct three drifted claims in woodpecker-ci.md

Manual-trigger section was stale for tests-pytest since 2026-08-28
(event: manual added then); trusted.network is live-false, not true;
pipeline numbers 240/241 no longer resolve after the 2026-08-31 server
reset. All three confirmed against the live API in the CI pipeline
audit (PR #440, docs/superpowers/research/2026-09-02-ci-pipeline-audit-woodpecker-mechanics.md).
docs/superpowers/research/2026-09-02-ci-pipeline-audit.md Tier 1 #4."
```

---

## Final integration step (after all four tasks land)

- [x] Run the full local suite one more time on the fully-integrated
  branch (all four commits applied) to confirm the tasks compose cleanly:

```
docker exec ddev-kalshi-whale-poc-fastapi sh -c 'cd /app/.claude/worktrees/ci-audit-tier1-fixes && python3 -m pytest -n 4 -m "not slow" -p no:testmon -q 2>&1 | tail -5'
```

Expected: `3056 passed, 16 skipped` (corrected 2026-09-03 — see the
addendum after Task 3's heading below: only 2 of the originally-planned 4
tests were actually marked slow, not 4), zero failures.

- [ ] Push the branch, open a PR, confirm CI green on all 5 required
  contexts (`gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status`),
  then run the PR-stage "nothing advances on one pass" cycle (self-review +
  independent adversarial review + consolidation) before merging, per
  CLAUDE.md's HARD RULE — this plan's own review cycle covers the plan
  document, not the PR as submitted.
