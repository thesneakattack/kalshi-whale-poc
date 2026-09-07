# PR #417 review consolidation — `loop_watchdog` fault visibility

Reconciles the implementation's own self-review (inline, during development)
with the independent adversarial review
(`2026-09-01-loop-watchdog-fault-visibility-pr417-review.md`, fresh Agent
call, no memory of the authoring session).

## Verdict: GO

The adversarial review found zero blocking issues after independently
re-deriving every load-bearing claim from source (snapshot/reset ordering,
call-site safety, `record_fault`'s real signature and never-raises
contract, `run_checks()` wiring, a real `pytest`/`project_manifest --check`
run) rather than trusting the PR body. No disagreement between self-review
and adversarial review to adjudicate — the adversarial pass surfaced
findings the self-review had not caught, all minor.

## Disposition of each finding

| # | Finding | Disposition |
|---|---|---|
| 4 | `check_event_loop_stalls` architecturally cannot report PASS from real data (absent key → UNKNOWN, not PASS, on a genuinely healthy window) | **Deferred, tracked below.** Pre-existing shape shared with `check_exit_engine_faults` — fixing it properly means changing both checks' zero-vs-absent semantics together, a small design decision, not a mechanical fix. Filed as a follow-up rather than expanding this PR's scope. |
| 5a | No test pins the exact `stall_max_ms == 1000.0` boundary | **Fixed** — added `test_maybe_capture_logs_error_severity_at_the_exact_1000ms_boundary`. |
| 5b | `test_event_loop_stalls_passes_on_zero`'s payload shape (`"loop_watchdog": 0`) is unreachable from real `fault_log.summary()` output | **Not fixed** — same shape as the pre-existing `exit_engine`/`capture_writer` defaults in `_payload()`; changing it is really Finding 4's fix (the real question is whether `by_component` should ever contain an explicit 0), not a standalone test bug. Tracked with Finding 4. |
| 5c | `test_event_loop_stalls_unknown_when_component_absent`'s docstring blames "an app predating this check" instead of naming the more common real cause (zero stalls occurred) | **Fixed** — docstring corrected. |
| 6 | Severity keyed on `stall_max_ms` (peak) only, never `stall_count` (frequency) — a night of many sub-1000ms stalls always logs `"warn"` | **Deferred, tracked below.** Real design tradeoff (what should drive severity), not a bug; doesn't weaken the automated FAIL/PASS gate (unaffected by severity). Needs its own decision on a frequency-based rule, not a reflexive threshold pick. |
| 8 | `services/observability/README.md`'s `loop_watchdog.*` section not updated for the new fault_log write / soak_analyzer gate | **Fixed** — README updated. |

## Follow-up to track (not blocking this PR)

Both deferred items (4, 6) belong to the same soak_analyzer/loop_watchdog
severity-and-completeness question and are worth one combined design pass
rather than two independent patches:

- Should `check_event_loop_stalls` (and `check_exit_engine_faults`, which
  has the identical shape) distinguish "queried and found zero" from "field
  absent from this payload" so a genuinely healthy window can report PASS?
- Should `maybe_capture`'s severity decision also consider `stall_count`
  (e.g. `"error"` when a window has both `stall_max_ms >= 1000` OR
  `stall_count` exceeds some frequency threshold), not `stall_max_ms` alone?

Recorded in `docs/open-decisions.md` for a future design pass; not a reason
to hold this PR, which closes a real, previously-total gap (zero alerting
on measured, severe event-loop stalls).

## Result

Proceeding to apply the three mechanical fixes (5a, 5c, 8), re-run the
affected tests, then merge.
