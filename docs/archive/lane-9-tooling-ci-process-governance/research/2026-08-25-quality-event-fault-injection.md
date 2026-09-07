# Quality Control Event/Credential Fault Injection (I9)

**Task:** I9 of `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `f720e89`
(worktree `.claude/worktrees/aqc-investigation`). Re-grounded: `git fetch origin` then
`git log HEAD..origin/main` empty (still fully synced with `main` from I7's merge-forward); no open
PRs; `origin/chore/realtime-data-plane-investigation` remains the same stale, inactive tip
(`f59dac9`) noted in I8 — reconciled, not active work.

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live runtime · **[E5]** upstream docs · **[E6]** inference.

**No privileged filter, App credential, or write secret exists anywhere in this repo's Woodpecker
configuration as of this task.** `grep -n "when:\|event:\|branch:" .woodpecker/*.yml` shows every
current pipeline uses `event: [push, pull_request]` with **no `branch:` key at all** — there is
nothing to fault-inject in production, by design (the plan requires "current active branches are
reconciled" before touching real workflow files, and no coordinator has been built yet to need
one). This task therefore proves the semantics a *future* privileged filter would need, using real
platform behavior and real historical data, not a deployed change.

---

## 1. What was fault-injected and how

Three properties, each mapped to a checklist line, each proven with real evidence rather than an
assumption:

1. **Event/branch filter semantics** — does a candidate privileged filter correctly exclude
   `pull_request` events targeting `main` while matching the intended trusted push? [E5 + E4 + E3]
2. **Stale-decision write refusal** — does a decision computed against an old `main` SHA get
   refused rather than silently acting on stale state? [E3]
3. **Duplicate/retry safety** — does a retried or raced write produce exactly one GitHub-side
   effect, never two? [E3]

No real App credential, PR write, or issue write was created or attempted anywhere in this task.

## 2. Event/branch filter semantics

### 2.1 Authoritative platform documentation [E5]

Fetched live 2026-08-25 from `https://woodpecker-ci.org/docs/usage/workflow-syntax`:

> "A condition is evaluated to true if *all* sub-conditions are true" (AND within one `when:`
> entry) — demonstrated by an example with both `event` and `branch` keys in one entry, and "The
> [step] is executed if one of these conditions is met" for multiple list entries (OR across
> entries).
>
> "The step now triggers on main branch, but also if the target branch of a pull request is
> `main`. Add an event condition to limit it further to pushes on main only." — the documented
> hazard the evidence rule already warns about (`.claude/rules/autonomous-quality-coordination-
> evidence.md`: "a `branch: main` condition also matches a PR whose target is `main`"), confirmed
> here as the platform's own stated behavior, not an inference from this repo's data alone.
>
> Documented event types: `push`, `pull_request`, `pull_request_closed`,
> `pull_request_metadata`, `tag`, `release`, `deployment`, `cron`, `manual`. "Branch conditions are
> not applied to tags" (a caveat outside this task's scope but worth recording for I11).

And from `https://woodpecker-ci.org/docs/usage/secrets` (checklist: "prove PR lane operates with no
write secret requirement"):

> "By default, secrets are not exposed to pull requests. However, you can change this behavior by
> creating the secret and enabling the `pull_request` event type." … "Be careful when exposing
> secrets for pull requests. If your repository is public and accepts pull requests from everyone,
> your secrets may be at risk."

This confirms, at the platform level, that a PR-triggered pipeline runs with **zero** secret access
unless a secret is *individually and explicitly* opted in — matching I6 §2's threat-model finding
for the current repo, now re-verified against the primary docs rather than cited secondhand.

### 2.2 Real live pipeline specimens [E4]

Rather than trust the documented hazard on faith, or re-derive I2's summary from memory, three real
event/branch pairs were fetched fresh this task directly from Woodpecker's anonymous public API
(`curl -H "User-Agent: curl/8.5.0" https://ci.webfoundry.dev/api/repos/1/pipelines/<n>`):

| Pipeline | `event` | `branch` | `ref` | Real-world meaning |
|---|---|---|---|---|
| 163 | `push` | `main` | `refs/heads/main` | PR #13's merge commit `fed3fee` landing on `main` — the intended trusted event |
| 92 / 94 / 96 | `pull_request` | `main` | `refs/pull/3/merge` | PR #3 (`refactor/kalshi-integration-boundary`) targeting `main` — the documented hazard, live |
| 164 / 165 | `push` | `chore/autonomous-quality-coordination-investigation` | `refs/heads/chore/...` | this task's own I7/I8 pushes — a push that is NOT to `main` |

Confirms two things beyond the docs' prose: Woodpecker's `branch` field for a `pull_request` event
really does resolve to the PR's **target**, not source, branch (`92/94/96`'s `refspec` is
`refactor/kalshi-integration-boundary:main` — head:base — while `branch` reports `main`, the base);
and a `push` event's `branch` field is the pushed ref's own branch, confirmed by `164/165` reporting
this investigation's own branch name, not `main`.

### 2.3 Deterministic evaluator, validated against the real specimens [E3, grounded in E4/E5]

`tools/quality_coordination_sim/when_filter.py` implements exactly the documented AND/OR algorithm
(`matches()` — OR across a `when:` entry list, AND within one entry's keys) and is validated, not
merely asserted, against the three real specimens above before being trusted for the counterfactual
(a filter that has never actually been deployed in this repo).
`tests/test_quality_coordination_when_filter.py`, 8/8 green:

```
PASS test_naive_branch_only_filter_matches_real_main_push
PASS test_naive_branch_only_filter_wrongly_matches_real_pr_targeting_main
PASS test_naive_branch_only_filter_excludes_push_to_other_branch
PASS test_trusted_filter_excludes_real_pr_targeting_main
PASS test_trusted_filter_matches_real_main_push
PASS test_trusted_filter_excludes_push_to_other_branch
PASS test_or_across_entries_cron_or_trusted_push
PASS test_and_within_entry_both_keys_must_match

8/8 passed, 0 failed
```

**Checklist item "prove a candidate privileged filter is NOT selected on pull_request events
targeting main":** `matches(TRUSTED, REAL_PR_TARGETING_MAIN) is False`, where `TRUSTED =
(WhenEntry(event=("push",), branch=("main",)),)` and `REAL_PR_TARGETING_MAIN` is pipeline 92/94/96's
real recorded event — not synthetic. The naive `branch: main`-only filter, run against the exact
same real specimen, returns `True` — reproducing the documented hazard against live data, in one
test file, with both outcomes visible side by side.

**Checklist item "prove the same filter IS selected on the intended trusted event":**
`matches(TRUSTED, REAL_MAIN_PUSH) is True` against pipeline 163's real recorded event.

## 3. Stale-decision write refusal and duplicate/retry safety [E3]

`tools/quality_coordination_sim/write_gate.py` adds two pieces downstream of I8's `Coordinator`:
`Decision` (an escalation-eligible outcome plus the `main` SHA it was computed against),
`FakeGitHubTransport` (in-memory, records every attempt including suppressed duplicates), and
`WriteGate` (the only thing allowed to call the transport). No network call is possible from this
module — there is no HTTP client anywhere in it.

`tests/test_quality_coordination_write_gate.py`, 6/6 green:

```
PASS test_stale_decision_is_refused_before_touching_transport
PASS test_fresh_decision_matching_current_sha_writes
PASS test_duplicate_submit_is_idempotent_no_second_write
PASS test_retry_after_transient_failure_succeeds_without_duplicate
PASS test_retry_exhausted_raises_and_leaves_no_partial_write
PASS test_two_different_decisions_for_same_key_update_not_duplicate
```

- **Stale SHA (checklist item):** `test_stale_decision_is_refused_before_touching_transport`
  changes "current main" between decision creation and submission, and asserts not just that no
  issue was created but that `transport.call_log == []` — the refusal happens *before* the
  transport is ever touched, so it can never be confused with a failed write attempt in a real
  audit log.
- **Duplicate/retry (checklist item):** three separate failure shapes, not one — an exact duplicate
  resubmission (idempotent no-op, one real write total), a transient-failure-then-retry (one real
  write, not two, despite the retry), and retries exhausted (raises, and leaves **no** partial
  record — `"k1" not in transport.issues`). A fourth test
  (`test_two_different_decisions_for_same_key_update_not_duplicate`) checks the adjacent case the
  checklist doesn't name explicitly but I1's identity contract requires: a *genuinely* updated
  decision for the same `automation_key` (e.g. occurrence count changed) must still update the same
  GitHub-side record, not fork a second one — two real writes for two real content changes, zero
  spurious duplicates.

### Mutation checks — both modules' tests have teeth [E3]

Following the same discipline as I8: one real invariant was deliberately broken in each module and
the relevant test suite re-run before being trusted.

- `write_gate.py`: the stale-SHA guard (`if decision.main_sha != self._current_main_sha():`)
  replaced with `if False:`. Result: **5/6, exactly `test_stale_decision_is_refused_before_
  touching_transport` failed** (`assert result is None` → `AssertionError`, since the mutated gate
  wrote through instead of refusing). Restored and re-verified at 6/6.
- `when_filter.py` was not separately mutation-tested — its only two branches (`event` mismatch,
  `branch` mismatch) are each independently exercised to `False` by
  `test_and_within_entry_both_keys_must_match`, and every other test already asserts both a `True`
  and a `False` outcome from the same filter object against different real specimens, which is
  already the mutation-check property (a constant-`True` or constant-`False` implementation would
  fail at least one of the 8 assertions).

## 4. What was deliberately NOT done

- **No real Woodpecker workflow file was added or modified.** `.woodpecker/*.yml` is untouched by
  this task — the plan's own "Create/modify only after current active branches are reconciled" is
  satisfied by *not* needing to modify it at all, since a pure-Python evaluator validated against
  real historical data answers the checklist without deploying anything.
- **No throwaway PR was opened against `main`** to manufacture a live `pull_request` event. The
  real historical specimens (92/94/96) already exist and already carry exactly the event/branch
  combination the checklist needs; opening a PR purely to regenerate data already on record would
  have added review noise for zero new evidence.
- **No App credential, installation token, or GitHub write secret was created, requested, or
  referenced anywhere in this task's code.** `FakeGitHubTransport` cannot make a network call by
  construction — it has no `requests`/`httpx`/`gh` dependency at all.

## 5. Handoff

**Next: I10 — architecture decision and adversarial retort.** Inputs ready: I4's four topology
candidates, I5's reporting-surface mapping, I6's threat model, I7's one surviving deterministic
fixer, I8's proven coordination policy (15 tests, real historical replay), and this task's proof
that the event/credential boundary a real topology would need is achievable with a documented,
live-validated filter and a stale-SHA-refusing, duplicate-safe write gate — all without ever having
deployed a privileged step. I10 scores Candidates A–D against this accumulated evidence and
dispatches an independent adversarial reviewer before any decision is recorded.
