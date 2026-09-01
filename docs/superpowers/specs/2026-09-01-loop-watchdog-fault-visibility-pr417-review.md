# PR #417 review — `loop_watchdog` fault visibility

Reviewer: fresh adversarial pass (Agent tool, no memory of the PR's own
authoring session). Branch `feat/loop-watchdog-fault-visibility` →
`feat/realtime-data-plane-remediation`. Everything below was re-derived from
`gh pr view 417`/`gh pr diff 417`, the current source tree, and a real
`ddev exec -s fastapi python -m pytest` run — nothing here is copied from the
task prompt's own summary without independent verification.

## Verdict: GO

No blocking issues. Six minor findings below — all either pre-existing
patterns this PR faithfully extends (not new regressions), or narrow
first-pass tradeoffs worth a documented follow-up. None of them makes the
PR unsafe to merge, and the PR closes a real, previously-unaddressed gap
(severe event-loop stalls sitting in `observability.db` with zero
alerting).

## What I checked, and how

- `gh pr view 417 --json body,commits` and `gh pr diff 417` for the actual
  diff (5 files: `services/observability/observability.py`,
  `static/project-manifest.json`, `tests/test_observability.py`,
  `tests/test_soak_analyzer.py`, `tools/soak_analyzer.py`).
- Read `services/observability/observability.py`'s full `maybe_capture`
  (lines 427-495ish), `services/loop_watchdog.py` in full, and
  `services/fault_log.py`'s `record_fault`/`_write`/`summary` in full.
- Traced `maybe_capture`'s only caller: `main.py:123` imports it as
  `_maybe_capture_observability`, called once, at `main.py:1231`, inline
  in `trading_loop`'s async body (plain synchronous call, not awaited, not
  offloaded). Grepped the whole repo (`grep -rn "maybe_capture\b"`,
  excluding tests and the unrelated `storage_health.maybe_capture_sizes`)
  and confirmed there is exactly one production call site — no route
  handler reaches it.
- Read `tools/soak_analyzer.py` in full for `Check`, `run_checks`,
  `verdict`, `check_exit_engine_faults` (the function this PR says it
  mirrors), and the new `check_event_loop_stalls`.
- Read `services/diagnostics/routes.py`'s `_blocking_extras`/
  `/api/health/pipeline` to see exactly how `faults_last_24h` reaches
  `soak_analyzer` in production (`fault_log.summary(since_ts=now-86400)`),
  and `fault_log.summary`'s SQL (`GROUP BY component`) to determine what
  `by_component` can and cannot contain.
- Ran `ddev exec -s fastapi python -m pytest tests/test_observability.py
  tests/test_soak_analyzer.py tests/test_loop_watchdog.py -q` — **115
  passed**, matching the PR body's claim exactly.
- Ran `ddev exec -s fastapi python -m tools.project_manifest --check
  static/project-manifest.json --repo-root .` — **passes** ("up to date, no
  drift beyond 10%").
- Diffed `git diff --stat <old_manifest_head>..<new_manifest_head> --
  '*.py'` against `git show --stat` of this PR's own feature commit alone,
  to independently size how much of the manifest drift is this PR's doing
  vs. inherited from earlier merges.
- Confirmed no circular import (`ddev exec -s fastapi python -c "from
  services.observability import observability"` succeeds).

## Point-by-point findings

### 1. `snapshot()` before `reset_window()` — CORRECT, verified

`services/observability/observability.py:470` calls
`lw_snapshot = loop_watchdog.snapshot()`, and `reset_window()` is not
called until line 478, eight lines later, with the `fault_log.record_fault`
call sitting between them. Order is right; a swapped order would indeed
have zeroed every fault, but that didn't happen.

### 2. `fault_log` write safety on this call path — SAFE, verified

- `maybe_capture` is `def maybe_capture(...)`, **not** `async def` — no
  `await` anywhere in its body. It is a plain synchronous function.
- Its one production caller (`main.py:1231`) invokes it as a plain
  (non-awaited) call inline inside `trading_loop`'s `async def` body, right
  before `await asyncio.sleep(_tick_interval_sec(cfg))` — i.e. it already
  runs on the event loop thread, synchronously, once per tick when its own
  ~60s `sample_interval_sec` gate is due. This is the same call site every
  other `maybe_capture` side effect already uses (`reset_ingest_window`,
  `whale_pipeline_perf.perf.reset_window()`, `http_client.reset_rest_
  latency_window()`, `loop_watchdog.reset_window()` itself, etc.) — the
  fault_log write is one more synchronous op alongside several that were
  already there. It does not introduce a new blocking-hot-path exposure
  that wasn't already accepted for this call site.
- Grepped the whole repo for `maybe_capture\b`: the only production
  reference is the one import/one call in `main.py`. No route handler
  (`services/observability/routes.py`, `services/diagnostics/routes.py`,
  etc.) calls `maybe_capture` — the `/api/observability/*` and
  `/api/health/*` endpoints are documented in the file itself as pure
  reads that must never trigger a window reset from an on-demand request
  (see the `reset_ingest_window`/`reset()` comment block at lines
  448-458). This reasoning holds.

### 3. `fault_log.record_fault`'s signature — MATCHES, verified

Actual signature (`services/fault_log.py:111`):

```python
def record_fault(component: str, operation: str, message: str,
                 context: str | None = None, severity: str = "warn",
                 now: float | None = None) -> bool:
```

The call site passes `component="loop_watchdog"`, `operation=
"event_loop_stall"`, `message=f"..."` positionally, and `severity=` as a
keyword — an exact match. `record_fault`'s entire body is wrapped in
`try/except Exception: return False` (line 118-122), and the module
docstring states the "never raises, ever" contract explicitly (line 34).
Confirmed: a `fault_log` failure (e.g. a locked `fault_log.db`) cannot
propagate out of `maybe_capture`.

### 4. Wired into `run_checks()` — YES; real UNKNOWN-not-PASS gap, pre-existing pattern

`check_event_loop_stalls(faults)` is in the `run_checks()` list
(`tools/soak_analyzer.py:599`), between `check_price_completeness` and
`check_capture_writer_health`. The `test_json_mode_emits_parseable_
output_with_every_check` assertion bump (10 → 11) is correct — I counted
11 entries in the actual `run_checks()` list.

**Real finding (minor, not a regression unique to this PR):** In
production, `faults_last_24h` is `fault_log.summary(since_ts=now-86400)`
(`services/diagnostics/routes.py:336`), and `summary()`'s `by_component`
comes from a plain SQL `GROUP BY component`
(`services/fault_log.py:207-209`). Standard SQL semantics mean a component
with **zero** matching rows in the window produces **no key at all** in
the result — there is no way for `by_component` to ever contain
`"loop_watchdog": 0` from real data. It can only be **absent** (zero
stalls logged — the healthy case) or **present with n ≥ 1** (at least one
stalled window — the unhealthy case).

`check_event_loop_stalls` treats "key absent" as `UNKNOWN`, not `PASS`
(`tools/soak_analyzer.py:235-237`). Combined with `verdict()`'s precedence
(`BLIND > FAIL > UNKNOWN > PASS`, `tools/soak_analyzer.py:606-613`), this
means: **on a genuinely healthy 24h window with zero event-loop stalls —
exactly the outcome the whole realtime-data-plane-remediation initiative
this branch belongs to is working toward — the overall `soak_analyzer`
verdict reports `UNKNOWN`, never `PASS`, on account of this check alone**
(assuming nothing else fails). `PASS` for this check is architecturally
unreachable from live data.

This is **not a new bug** — `check_exit_engine_faults` already has the
identical shape (`if "exit_engine" not in by_comp: return UNKNOWN`,
`tools/soak_analyzer.py:559-561`), and the PR explicitly says it mirrors
that function's discipline. It does it faithfully, including inheriting
this same ambiguity. `tests/test_soak_analyzer.py`'s shared `_payload()`
fixture has carried `"exit_engine": 0` as a hardcoded default since before
this PR (line 34) — the identical shape `check_event_loop_stalls`'s new
`test_event_loop_stalls_passes_on_zero` now also relies on
(`tests/test_soak_analyzer.py:341-345`) — a payload shape the real
`fault_log.summary()` can never produce. So this PR neither introduces nor
worsens the ambiguity; it extends an existing, already-accepted pattern to
a new component. Worth a follow-up issue against both checks together
(e.g. seed a zero-value sentinel, or special-case "queried and found
zero" vs. "field doesn't exist in this payload shape at all"), but not a
reason to block this PR.

The `FAIL` path is sound: it only fires when `n != 0`, i.e. a real stall
was actually logged — no false positives there.

### 5. Test coverage — real behavior tested; two honest gaps

Read the actual diff (not paraphrased) for all 6 new tests.

- `test_maybe_capture_logs_a_fault_when_the_watchdog_recorded_a_stall`:
  monkeypatches `loop_watchdog`'s underlying globals (`_stall_max_ms`,
  `_stall_count`, `_samples`) rather than mocking `snapshot()` itself, so
  the real `snapshot()` code path executes. Asserts exactly one
  `record_fault` call, correct component/operation, message contains both
  numbers, `severity == "error"` for `stall_max_ms=1500.0`. Genuine.
- `test_maybe_capture_logs_a_warn_severity_fault_for_a_smaller_stall`:
  `stall_max_ms=120.0` → asserts `"warn"`. Genuine.
- `test_maybe_capture_logs_no_fault_when_the_watchdog_saw_no_stall`:
  `stall_count=0` → asserts `recorded == []` (no call at all, not a call
  with a zero value). Genuine, and correctly distinguishes "no fault
  logged" from "fault logged with a zero count."
- **Gap (minor):** neither test exercises the exact boundary
  `stall_max_ms == 1000.0`. The implementation is `severity="error" if
  lw_snapshot["stall_max_ms"] >= 1000.0 else "warn"` — inclusive `>=`, so
  exactly 1000.0ms should be `"error"`. I confirmed this reading the code,
  but no test pins it; the two tests use 1500.0 (well above) and 120.0
  (well below). This is exactly the boundary the review task asked about,
  and it is genuinely untested. Low risk (the code is simple and
  unambiguous), but a one-line addition (`stall_max_ms=1000.0` →
  `assert severity == "error"`) would close it.
- `test_event_loop_stalls_fails_on_any_recorded_fault` and
  `test_event_loop_stalls_unknown_when_component_absent`: genuine, and the
  UNKNOWN test's payload shape (`by_component` missing the key entirely)
  does match what real `fault_log.summary()` output looks like when there
  have been zero stalls — but its docstring ("An app predating this check
  exposes no loop_watchdog key") is misleading about **why** that would
  happen in practice: the far more common real-world reason is "zero
  stalls occurred," not version skew, per Finding 4 above. Worth a comment
  fix, not a functional issue.
- `test_event_loop_stalls_passes_on_zero`: exercises `PASS` with
  `"loop_watchdog": 0` explicitly present in `by_component` — a shape that
  (per Finding 4) the real `/api/health/pipeline` → `fault_log.summary()`
  path can never produce. The test is internally consistent with the
  function's code (which does handle an explicit `0` correctly if it ever
  received one), but it does not actually cover the real-world "zero
  stalls" case, which in production takes the `UNKNOWN` branch instead.
  Same shape as the pre-existing `check_exit_engine_faults` test gap
  (there isn't even a dedicated "exit_engine passes on zero" test in the
  existing suite — only the shared fixture's default implicitly reaches
  it). Not new to this PR.

### 6. Severity threshold / "once per window" design — real first-pass tradeoff, acceptable, not blocking

Confirmed: `maybe_capture`'s severity decision reads only
`stall_max_ms` (the single worst stall in the window) — it never looks at
`stall_count` (how many stalls) for severity purposes, only for the
message text. So yes: a night with, say, 50 sub-1000ms stalls every
60-second window for hours (a serious, systemic problem in aggregate)
would log `severity="warn"` on every single fault_log row, never
`"error"`, because no *individual* stall in any window ever crosses
1000ms.

This is a real, verifiable gap, and I did not find a code comment
anywhere in the diff that specifically justifies magnitude-over-frequency
as the severity signal — the existing comment
(`services/observability/observability.py:461-469`) explains why the read
happens in `maybe_capture` rather than in `loop_watchdog._tick()` (sound,
verified under Finding 2), but says nothing about why `stall_max_ms`
alone, rather than `stall_count` or total stalled time, drives severity.

That said, this is a reasonable first pass, not a regression: before this
PR there was **no alerting on stalls at all** (the PR's whole premise).
The automated `soak_analyzer` gate (`check_event_loop_stalls`) is
unaffected by this gap — it fails on any nonzero `by_component.
loop_watchdog` count regardless of severity, so the pass/fail signal used
for automated gating is not weakened by the coarse severity split. The
gap only affects the `severity` label surfaced to a human via `GET
/api/health/faults` for triage/prioritization, and the raw `stall_count`
is still visible in the fault's `message` text for anyone reading it
directly. Worth a documented follow-up (e.g. also flag `"error"` when
`stall_count` exceeds some frequency threshold within the window), not a
blocker.

### 7. `project-manifest.json` regen — claim checks out, verified

- `ddev exec -s fastapi python -m tools.project_manifest --check
  static/project-manifest.json --repo-root .` → `"project-manifest: up to
  date (no drift beyond 10%)"` against the current tree (this PR's code
  included).
- Sized the drift's real source: `git diff --stat <old-manifest-head>..
  <new-manifest-head> -- '*.py'` (spanning PR #414's several fixup
  commits + PR #415 + this PR's own feature commit) shows **135 files
  changed, 13992 insertions, 592 deletions**. This PR's own single feature
  commit (`39cd284`) alone touched **4 files, 150 insertions, 3
  deletions** — roughly 1% of the total churn. The claim that the drift is
  "cumulative from tonight's several merged PRs... unrelated to this PR's
  actual content" checks out quantitatively, not just narratively.

### 8. Other bugs — none found

- `loop_watchdog.snapshot()` (`services/loop_watchdog.py:20-21`) always
  returns all three keys (`stall_max_ms`, `stall_count`, `samples`) with
  real values — `_stall_max_ms` is a module-level float initialized to
  `0.0` and only ever updated via `max(_stall_max_ms, late * 1000)`; it is
  **never `None`**. The task prompt's own hypothesis ("`stall_max_ms`
  could be `None` in some snapshot shape") is **false** — verified
  directly against the function body, not assumed.
- `lw_snapshot['stall_max_ms']`, `['stall_count']`, `['samples']` are
  accessed by direct indexing (not `.get()`) after the `if
  lw_snapshot.get("stall_count"):` guard — safe, since `snapshot()`'s
  return contract guarantees all three keys are always present.
- No thread/coroutine race between `snapshot()`'s read and
  `reset_window()`'s write: asyncio is single-threaded/cooperative,
  `maybe_capture` contains no `await` between the two calls, so no other
  coroutine (including `loop_watchdog._tick()`) can interleave and mutate
  `_stall_max_ms`/`_stall_count`/`_samples` mid-sequence.
- Import change (`services/observability/observability.py:9-12`) just
  adds `fault_log` into the existing alphabetized `from services import
  (...)` tuple, correctly positioned alphabetically
  (`candidate_retry, capture_writer, fault_log, http_client,
  loop_watchdog, strategy_engine, whale_pipeline_perf`). No unused import,
  no duplicate. Confirmed no circular import (`from services.observability
  import observability` imports cleanly).
- Message formatting: manually verified `f"{stall_max_ms:.0f}ms"` renders
  correctly (e.g. `1500.333` → `"1500ms"`), well under `fault_log`'s
  500-char message cap. Units are explicit in the message text (`ms`), no
  dimensional ambiguity.
- Dedup side note (non-blocking, not asked for but noticed while reading
  `fault_log._write`'s `UNIQUE (component, operation, exc_type, message)`
  constraint): because the fault message embeds per-window numbers
  (`stall_count`, `stall_max_ms`, `samples`), it will almost never
  exactly match a prior message, so `fault_log`'s dedup-and-increment
  design (built for identical-message storms) rarely collapses
  `loop_watchdog` rows — each stalled window typically inserts its own new
  row rather than incrementing an existing row's `count`. This doesn't
  break anything (`by_component`'s `SUM(count)` still sums correctly
  regardless of row count vs. per-row count), and pruning
  (`fault_log.prune`, wired hourly) still bounds total growth — just
  noting the dedup mechanism isn't really exercised here the way it is for
  a repeating identical exception.
- `services/observability/README.md`'s existing `loop_watchdog.*` section
  (lines 337-367) documents the read-only snapshot fields and the
  window-reset ownership rule, but was **not updated by this PR** to
  mention the new fault_log write or the `soak_analyzer` gate — the "Use."
  paragraph (line 364-367) still describes only the manual `/api/health/
  pipeline` read path. Minor doc-completeness gap; CLAUDE.md asks for
  confirmed findings to be cross-posted to the relevant README with a
  date, and this PR's own new behavior isn't reflected there yet.

## Summary table

| # | Finding | Severity | New to this PR? |
|---|---|---|---|
| 1 | snapshot()/reset_window() ordering | none — correct | — |
| 2 | maybe_capture call-site safety | none — safe | — |
| 3 | record_fault signature/never-raises | none — matches | — |
| 4 | UNKNOWN-not-PASS on a healthy zero-stall 24h window | minor | No — mirrors `check_exit_engine_faults`'s existing shape |
| 5a | No test at exact `stall_max_ms == 1000.0` boundary | minor | Yes (new test suite's own gap) |
| 5b | `passes_on_zero` test payload shape unreachable from real API | minor | No — same shape as existing `_payload()` default for `exit_engine` |
| 5c | UNKNOWN test's docstring mischaracterizes typical cause | minor | Yes (new test's comment) |
| 6 | Severity keyed on max magnitude only, not frequency | minor | Yes, but doesn't weaken the automated gate |
| 7 | project-manifest regen claim | none — verified accurate | — |
| 8 | `README.md`'s loop_watchdog section not updated for new behavior | minor | Yes |

No blocking findings. Recommend merging as-is, with 4/5a/5c/6/8 tracked as
a small follow-up (or simply fixed in a 2-minute amend if the branch is
still open) rather than reason to hold the PR.
