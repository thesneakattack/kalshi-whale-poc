# No-Write Quality Coordination Simulator (I8)

**Task:** I8 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `78da66e`
(worktree `.claude/worktrees/aqc-investigation`). No open PRs; `origin/chore/realtime-data-plane-
investigation` exists remotely but is stale (tip `f59dac9`, dated before the real realtime
investigation that became PR #12 — not active work, confirmed by inspection, not assumed).

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E6]** inference.

**Prototype status: EXPERIMENTAL / THROWAWAY**, exactly as the plan requires. Nothing in this task
touches production CI, `.woodpecker/**`, `.github/workflows/**`, GitHub credentials, or any network
call. `tools/quality_coordination_sim/` exists only to prove the I1/I2/I3 policies compose into an
explainable, non-duplicating state machine before I10 selects a real architecture.

---

## 1. What this is

A pure, in-memory Python module (`tools/quality_coordination_sim/coordinator.py`, ~150 lines) that
takes a stream of `Observation` snapshots — one per fresh `main` audit, each carrying the finding
set that audit saw plus whatever remote branch/PR/claim state the caller already fetched — and
computes, deterministically, which findings are `OBSERVED`, `SUPPRESSED_PENDING_WORK`,
`ESCALATION_ELIGIBLE`, or `RESOLVED`. It never calls GitHub, never calls Woodpecker, never writes a
file outside its own package, and the caller supplies every snapshot — the module has no way to
fetch state on its own, which is the actual mechanism (not just a promise) behind "no-write."

## 2. Design mapping — every policy came from a prior task's measured evidence, not intuition

| Coordinator constant/rule | Source | Value used |
|---|---|---|
| `FLOOR_HOURS["error"]` | I2 §9 candidate range "2-6h" | 2.0h (conservative/fast end — a wider value only delays escalation further, so the floor of the range is what the scenario matrix has to survive) |
| `FLOOR_HOURS["warning"/"info"]` | I2 §9 candidate range "6-24h" | 6.0h |
| De-burst gap/count | I2 §9 "≥2 observations ≥1h apart (de-bursted)" | 1.0h gap, 2 observations |
| Staleness idle / hard cap | I2 §9 "idle ≥2× longest live-branch idle gap (~3h), hard-capped ≤2× longest branch life (~24h)" | 3.0h / 24.0h |
| Precedence order | I3 §11: exact claim (A) → path-overlap PR∪branch (B∪C, merged excluded, expiry-bounded) → persistence floor (E) → escalation-eligible | implemented exactly, in `Coordinator._evaluate` |
| "escalation time = max(floor, end of suppression), never min" | I3 §11 | implemented by re-evaluating suppression **and** floor on every audit, never caching an earlier "eligible" verdict past a later suppression |
| Merged branches excluded from suppression | I3 §11 invariant / I2 §8 S12 specimen (`chore/realtime-data-plane-investigation` merged-but-undeleted) | `BranchState.merged` checked before path overlap |
| Automation key is location-free | I1 §9 recommendation #2 | `FindingFact.location` exists but never participates in identity (`automation_key` is the only dict key) |
| Resolution only via absence from a fresh integrated audit | evidence rule, I1 §9 #4 | Step 1 of `Coordinator.audit()`, unconditional, runs before any suppression logic |
| Recurrence is a reopen, not a new item | I1 §9 #5 | `Item.reopen_count`, same dict key, prior `log` entries retained |
| Directory-overlap (signal D) and local registry (signal F) are NOT implemented | I3 §11/§9 — D is an unbounded blind delay, F is remote-invisible with a ≤2.3-min real coverage window | absent from `BranchState`/`Claim` by design, not an oversight — see scenario 12 |

## 3. TDD process actually followed

Per the plan's `test-driven-development` capability: the full 12-scenario test file
(`tests/test_quality_coordination_sim.py`) was written and run **before** `coordinator.py` existed
— confirmed red with `ModuleNotFoundError: No module named
'tools.quality_coordination_sim.coordinator'` — then the implementation was written once against
all 14 tests (12 named scenarios + idempotence + historical replay) and reached green on the first
full implementation pass.

Host `python3` has neither `pytest` nor `pip` installed, and `ddev exec` refuses to run from this
worktree (its running instance is bound to the primary checkout's path — confirmed live: `ddev
describe`/`ddev exec` both fail with "a project (web container) in running state already exists...
created at /home/davidf/.../autotrade", the primary checkout, not this worktree). Since the
prototype and its tests use only the stdlib (`dataclasses`, `enum`) with plain bare-`assert`
functions and no pytest-specific features, a minimal stdlib-only runner
(scratchpad `run_i8_tests.py`, not committed) executed the same test file directly. **This is a
real Woodpecker `tests-pytest` file, not a runner-specific format** — the next push exercises it
under the repo's actual pytest in a fresh container, which resolves the host/ddev friction by
construction (Woodpecker clones fresh; it doesn't share this worktree's bind-mount collision).

### Result — 15/15 green

```
PASS test_branch_only_finding_never_reaches_repo_state
PASS test_main_observation_creates_item_in_observed_state
PASS test_repeated_main_observation_is_same_item_not_duplicate
PASS test_line_shift_does_not_change_identity
PASS test_exact_claim_suppresses
PASS test_path_overlap_pr_suppresses
PASS test_stale_branch_signal_expires_and_reopens_to_eligible_evaluation
PASS test_merge_resolves_finding
PASS test_merge_does_not_resolve_finding_falls_back_to_floor
PASS test_recurrence_reopens_same_item_with_history_attached
PASS test_claim_never_marks_resolved_while_finding_stays_present
PASS test_two_concurrent_claims_still_single_suppression_no_duplicate_escalation
PASS test_ambiguous_local_only_work_provides_no_suppression_signal
PASS test_idempotent_on_repeated_identical_audit
PASS test_i2_historical_replay_no_false_escalation

15/15 passed, 0 failed
```

`test_claim_never_marks_resolved_while_finding_stays_present` proves the checklist's general
invariant directly (distinct from the merge scenarios, which only cover the branch-merge path):
`State.RESOLVED` is assigned in exactly one place in the module — `audit()` step 1, gated purely on
a key's absence from the current `present` set — and never inside `_evaluate`, so no suppression
signal has a code path to resolution. Replayed 50 audits with the same exact claim present and the
finding never absent to rule out the persistence/idempotence machinery accidentally laundering
suppression into resolution over a long run.

### Mutation check — the suite has teeth, not vacuous assertions [E3]

Before trusting a 14/14 first-pass green, one real invariant was deliberately broken (the
`if branch.merged: continue` exclusion in `_suppressing_signal`, removed) and the suite re-run
against the mutated module. Result: **13/14, exactly `test_merge_does_not_resolve_finding_falls_
back_to_floor` failed** (`AssertionError` on `result["k1"] in (OBSERVED, ESCALATION_ELIGIBLE)` —
the mutated coordinator kept the item `SUPPRESSED` by a branch that had already merged, which is
precisely the bug this test exists to catch). The real module was restored immediately after and
re-verified at 14/14 — this mutation check ran before `test_claim_never_marks_resolved_while_
finding_stays_present` (§3's 15th test) was added; that test was written and verified separately
and does not change the mutation result above. This is the same discipline I1's mutation harness
applied to scanner identity — proving a test fails for the right reason, not just that it currently
passes.

## 4. Scenario-by-scenario mapping to the plan's checklist

All twelve named scenarios from I8's checklist are implemented as named tests (§3 above); mapping
notes for the three least literal ones:

- **"Branch-only finding" (scenario 1):** implemented as a *structural* property, not a special-
  cased rule. `Coordinator.audit()` only ever receives `main`-observation snapshots; nothing in
  this prototype (or a real caller) would feed a branch-local finding into `present`. The test
  proves the negative directly: two audits with an empty `present` produce zero items, and the key
  used in every other test never appears without first passing through a `present` dict.
- **"Line-shift identity" (scenario 4):** modeled by observing the same `automation_key` twice with
  a different `location` string (simulating a line inserted above the finding) and asserting
  continuity (same item, `reopen_count == 0`, two observations recorded) — directly exercises I1
  §9's location-free identity contract rather than re-deriving it.
- **"Ambiguous local-only work" (scenario 12):** deliberately proves the *accepted gap*, not a bug.
  I3 §9 rejected building a local Claude session/worktree registry on measurement (≤2.3-minute real
  coverage window, remote-invisible). The test shows the consequence executably: with no branch and
  no claim ever fed in (because none exists remotely), the coordinator reaches
  `ESCALATION_ELIGIBLE` purely on the persistence floor, even though — in the story the test name
  describes — a human could be mid-fix on their own machine. Recorded as an explicit, evidence-
  backed tradeoff for I9/I10, not silently absorbed into "it works."

## 5. Explainability (checklist: "record decision explanations so every suppression/escalation is
   inspectable")

Every state transition appends a human-readable line to `Item.log` with the triggering timestamp
and reason (`"suppressed: exact claim from PR#42"`, `"resolved: absent from fresh main audit"`,
`"reopened (recurrence #1); prior history retained"`, etc.). `test_recurrence_reopens_same_item_
with_history_attached` explicitly asserts the pre-resolution log lines are still present after a
reopen — nothing is truncated or reset on a state change, which is what "inspectable" has to mean
if a human is ever going to trust an escalation decision without re-deriving it by hand.

## 6. Duplicate-work measurement against the I2 historical sample

`test_i2_historical_replay_no_false_escalation` does not use synthetic timestamps for this part —
it replays the three real `main` finding episodes from I2 §6 (`config-unread:alerting.
crash_auto_resolve_after_sec`, `backend-route-unused:POST:/api/alerts/*/resolve`,
`api-usage:account.create_order`) at their actual observed times, plus the real branch-push
timestamp (`chore/realtime-dp-investigation`, 2026-08-25 22:05Z) and the real PR #12 merge
timestamp fetched live for this task (`gh pr view 12 --json mergedAt` → `2026-08-26T03:00:45Z`),
converted to hours since a fixed origin.

Result: the recommended policy would have created **zero duplicate-work events** across all three
episodes.
- Episodes A and B (lifetimes 0.23h and 0.10h) never leave `OBSERVED` before resolving — both are
  far under the 6h info/warning floor, so no escalation-eligible state is ever reached, matching
  the historical fact that neither ever needed a fix beyond the baseline-sync commit that already
  landed.
- Episode C reaches the 6h floor instant (`t=46.70`) still `SUPPRESSED`, because the branch signal
  arrived at `t=46.08` — **0.62h before** the floor — so suppression wins per the "max, never min"
  rule and the item never escalates. This reproduces I2 §9 point 3's finding
  ("with T=6h the escalation at +7.0h falls inside the suppression window → no escalation, which is
  the correct outcome (fix in flight)") as an executable assertion rather than a table entry. The
  item resolves cleanly at the real merge timestamp once the post-merge audit no longer reports it.

No architecture or credential decision follows from this result by itself — I8's job was only to
prove the *policy*, not to select or build the system that would run it. That selection is I10's.

## 7. What is intentionally NOT in this prototype

- No rename detection (I1 §9 #6 marks this "a heuristic for I3/I8 to evaluate, not part of the base
  contract" — evaluated here by omission: nothing in the I2/I3 evidence showed a rename actually
  occurring yet, so building detection for a latent-only case would be exactly the "invent
  because a category exists" failure mode the evidence rule warns against elsewhere).
- No SARIF/issue/PR output — I5 already decided reporting surface *selection*; wiring an actual
  writer is explicitly out of scope until I10+ picks an architecture with write authority.
- No GitHub/Woodpecker API client of any kind — `Observation` is caller-supplied by design, so nothing
  in this package can make an outbound call even by accident.

## 8. Handoff

**Next: I9 — fault-inject event and workflow semantics without privileged writes.** This task's
`Coordinator` is the "decision logic" I9's fault-injection should sit downstream of: I9 studies
*whether an event/snapshot ever reaches the coordinator reliably* (killed pipelines, PR-vs-push
event aliasing, replay/idempotence under a flaky feed), not the decision logic itself, which this
task already proved separately. Reuse `Observation`/`BranchState`/`Claim` as the fixture shapes if
I9 needs synthetic snapshots.
