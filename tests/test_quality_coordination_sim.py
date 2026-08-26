"""I8 scenario matrix for the throwaway no-write quality coordination simulator.

Prototype status: EXPERIMENTAL / THROWAWAY, per
docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md I8. Not wired
into production CI or any GitHub API. Encodes the policies I1 (identity), I2 (persistence floors),
and I3 (suppression precedence) already derived from real repo evidence -- this test file is the
executable proof those policies compose into an explainable, non-duplicating, no-write coordinator
before I10 selects a production architecture. Every timestamp is hours on an arbitrary synthetic
epoch unless a test says "historical" (real repo timestamps, converted to hours since a fixed
origin).
"""
from __future__ import annotations

from tools.quality_coordination_sim.coordinator import (
    BranchState,
    Claim,
    Coordinator,
    FindingFact,
    Observation,
    State,
)


def _fact(key: str, level: str = "info", paths=("services/x.py",), location: str = "x.py:10") -> FindingFact:
    return FindingFact(automation_key=key, scope_paths=tuple(paths), level=level, location=location)


# 1. branch-only finding never becomes a repository escalation candidate ----------------------


def test_branch_only_finding_never_reaches_repo_state():
    """A finding that only ever appears on a branch/PR audit -- never on a fresh `main` audit --
    must never get a coordinator Item at all, per the evidence rule: 'A finding that exists only
    on a feature/initiative branch is owned by that branch... it must not create a global
    repository issue.' The coordinator's `audit()` only accepts a `main`-observation snapshot, so
    the invariant is structural: nothing calls `audit()` for branch-local findings in the first
    place, and a key that never appears in any `present` set simply has no Item."""
    coord = Coordinator()
    # A branch/PR observation is never fed to audit() -- only main snapshots are. Confirm main
    # audits that don't include this key produce no item for it whatsoever.
    coord.audit(Observation(at=0.0, present={}, branches=[], claims=[]))
    coord.audit(Observation(at=1.0, present={}, branches=[], claims=[]))
    assert "branch-only-finding" not in coord.items
    assert coord.states() == {}


# 2. main observation -----------------------------------------------------------------------


def test_main_observation_creates_item_in_observed_state():
    coord = Coordinator()
    result = coord.audit(Observation(at=0.0, present={"k1": _fact("k1")}, branches=[], claims=[]))
    assert result["k1"] == State.OBSERVED
    assert coord.items["k1"].first_observed_at == 0.0


# 3. repeated main observation is the same item, not a duplicate ----------------------------


def test_repeated_main_observation_is_same_item_not_duplicate():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1")}, branches=[], claims=[]))
    coord.audit(Observation(at=0.2, present={"k1": _fact("k1")}, branches=[], claims=[]))
    coord.audit(Observation(at=0.4, present={"k1": _fact("k1")}, branches=[], claims=[]))
    assert len(coord.items) == 1
    item = coord.items["k1"]
    assert item.observation_times == [0.0, 0.2, 0.4]
    assert item.first_observed_at == 0.0  # unchanged by repeats


# 4. line-shift identity: automation_key is location-free (I1 recommendation #2) ------------


def test_line_shift_does_not_change_identity():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1", location="x.py:10")}, branches=[], claims=[]))
    # same automation_key, different location string (a line inserted above it) -> same item
    coord.audit(Observation(at=0.5, present={"k1": _fact("k1", location="x.py:14")}, branches=[], claims=[]))
    assert len(coord.items) == 1
    assert coord.items["k1"].observation_times == [0.0, 0.5]
    assert coord.items["k1"].reopen_count == 0


# 5. exact PR claim suppresses (I3 signal A, strongest) --------------------------------------


def test_exact_claim_suppresses():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1")}, branches=[], claims=[]))
    result = coord.audit(Observation(
        at=0.1, present={"k1": _fact("k1")}, branches=[],
        claims=[Claim(automation_key="k1", source="PR#42")],
    ))
    assert result["k1"] == State.SUPPRESSED
    assert "exact claim" in coord.items["k1"].log[-1]


# 6. path-overlap PR/branch suppresses (I3 signal B/C, second tier) --------------------------


def test_path_overlap_pr_suppresses():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1", paths=("services/x.py",))}, branches=[], claims=[]))
    result = coord.audit(Observation(
        at=0.1, present={"k1": _fact("k1", paths=("services/x.py",))},
        branches=[BranchState(name="fix/x", changed_paths=("services/x.py",), last_commit_at=0.1)],
        claims=[],
    ))
    assert result["k1"] == State.SUPPRESSED
    assert "path overlap" in coord.items["k1"].log[-1]


# 7. stale branch: suppression expires, item falls back to floor/eligible evaluation ---------


def test_stale_branch_signal_expires_and_reopens_to_eligible_evaluation():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1", level="warning", paths=("services/x.py",))}, branches=[], claims=[]))
    # branch active early -> suppressed
    r1 = coord.audit(Observation(
        at=1.0, present={"k1": _fact("k1", level="warning", paths=("services/x.py",))},
        branches=[BranchState(name="stale/x", changed_paths=("services/x.py",), last_commit_at=1.0)],
        claims=[],
    ))
    assert r1["k1"] == State.SUPPRESSED
    # same branch, now idle 4h (> 3h idle expiry) and floor (6h, warning) already met by t=7 ->
    # suppression must have expired, and the item must be independently eligible on its own
    # persistence floor, not "stuck suppressed" by a dead branch.
    r2 = coord.audit(Observation(
        at=7.0, present={"k1": _fact("k1", level="warning", paths=("services/x.py",))},
        branches=[BranchState(name="stale/x", changed_paths=("services/x.py",), last_commit_at=1.0)],
        claims=[],
    ))
    assert r2["k1"] == State.ESCALATION_ELIGIBLE
    assert "expired" in "\n".join(coord.items["k1"].log) or "floor met" in "\n".join(coord.items["k1"].log)


# 8. merge resolves the finding (real historical replay lives in test_i2_historical_replay) --


def test_merge_resolves_finding():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1", paths=("tools/quality_audit/baseline.json",))}, branches=[], claims=[]))
    coord.audit(Observation(
        at=1.0, present={"k1": _fact("k1", paths=("tools/quality_audit/baseline.json",))},
        branches=[BranchState(name="fix/k1", changed_paths=("tools/quality_audit/baseline.json",), last_commit_at=1.0)],
        claims=[],
    ))
    # branch merges; fresh main audit no longer shows k1 present at all
    result = coord.audit(Observation(
        at=2.0, present={},
        branches=[BranchState(name="fix/k1", changed_paths=("tools/quality_audit/baseline.json",), last_commit_at=1.0, merged=True)],
        claims=[],
    ))
    assert result["k1"] == State.RESOLVED
    assert coord.items["k1"].resolved_at == 2.0


# 9. merge does NOT resolve: falls back to floor/eligible evaluation, never silently cleared --


def test_merge_does_not_resolve_finding_falls_back_to_floor():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1", level="error", paths=("services/x.py",))}, branches=[], claims=[]))
    coord.audit(Observation(
        at=1.0, present={"k1": _fact("k1", level="error", paths=("services/x.py",))},
        branches=[BranchState(name="fix/x", changed_paths=("services/x.py",), last_commit_at=1.0)],
        claims=[],
    ))
    # branch merges but the finding is STILL present on the fresh main audit (the fix didn't
    # actually fix it) -- per the evidence rule, a claimed finding is resolved only when a fresh
    # audit no longer reports it. Merged branches are also excluded from suppression (I3 S12), so
    # the item must fall back to floor evaluation, not stay suppressed and not get silently marked
    # resolved.
    result = coord.audit(Observation(
        at=3.0, present={"k1": _fact("k1", level="error", paths=("services/x.py",))},
        branches=[BranchState(name="fix/x", changed_paths=("services/x.py",), last_commit_at=1.0, merged=True)],
        claims=[],
    ))
    assert result["k1"] in (State.OBSERVED, State.ESCALATION_ELIGIBLE)
    assert result["k1"] != State.SUPPRESSED
    assert result["k1"] != State.RESOLVED


# 10. recurrence: a resolved item reappearing is a reopen, history retained ------------------


def test_recurrence_reopens_same_item_with_history_attached():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1")}, branches=[], claims=[]))
    coord.audit(Observation(at=1.0, present={}, branches=[], claims=[]))  # resolved
    assert coord.items["k1"].state == State.RESOLVED
    prior_log_len = len(coord.items["k1"].log)

    coord.audit(Observation(at=5.0, present={"k1": _fact("k1")}, branches=[], claims=[]))  # reappears
    item = coord.items["k1"]
    assert item.state == State.OBSERVED
    assert item.reopen_count == 1
    # same Item object / same key -- not a fresh identity -- and the prior resolution is still in
    # the log, not discarded.
    assert len(item.log) > prior_log_len
    assert any("resolved" in line for line in item.log[:prior_log_len])


# checklist: "prove a claim/overlap can only suppress/delay, never mark resolved" -------------


def test_claim_never_marks_resolved_while_finding_stays_present():
    """Distinct from the merge scenarios: this asserts the general invariant directly. A claim
    (or a live path-overlap branch) is a suppression signal only -- it has no code path to
    State.RESOLVED at all. RESOLVED is set in exactly one place (`audit()` step 1), gated purely
    on absence from `present`, never inside `_evaluate`. Replayed 50 times with the same exact
    claim present and the finding never absent, to make sure persistence/idempotence machinery
    doesn't accidentally launder suppression into resolution over many audits."""
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1")}, branches=[], claims=[]))
    for i in range(1, 51):
        result = coord.audit(Observation(
            at=float(i), present={"k1": _fact("k1")}, branches=[],
            claims=[Claim(automation_key="k1", source="PR#1")],
        ))
        assert result["k1"] == State.SUPPRESSED
    assert coord.items["k1"].state == State.SUPPRESSED
    assert coord.items["k1"].resolved_at is None


# 11. two concurrent claims: still exactly one suppressed state, no duplicate escalation -----


def test_two_concurrent_claims_still_single_suppression_no_duplicate_escalation():
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1")}, branches=[], claims=[]))
    result = coord.audit(Observation(
        at=0.1, present={"k1": _fact("k1")}, branches=[],
        claims=[Claim(automation_key="k1", source="PR#1"), Claim(automation_key="k1", source="PR#2")],
    ))
    assert result["k1"] == State.SUPPRESSED
    assert len(coord.items) == 1  # never two items/two escalations for one key


# 12. ambiguous local-only work: no remote signal exists, so none is applied (accepted gap) --


def test_ambiguous_local_only_work_provides_no_suppression_signal():
    """I3 rejected building a local Claude session/worktree registry (remote-invisible, <=2.3-min
    coverage window measured). This test documents the resulting, accepted behavior rather than
    treating it as a bug: with no pushed branch and no claim, a human quietly fixing the finding
    only on their own machine is invisible to the coordinator, which proceeds on the persistence
    floor alone and may reach ESCALATION_ELIGIBLE even though work is already underway locally."""
    coord = Coordinator()
    coord.audit(Observation(at=0.0, present={"k1": _fact("k1", level="warning")}, branches=[], claims=[]))
    # 7h pass, well past the warning floor (6h); no branch, no claim was ever fed in, because none
    # exists remotely -- this is the whole point of the scenario.
    result = coord.audit(Observation(at=7.0, present={"k1": _fact("k1", level="warning")}, branches=[], claims=[]))
    assert result["k1"] == State.ESCALATION_ELIGIBLE


# --- idempotence -----------------------------------------------------------------------------


def test_idempotent_on_repeated_identical_audit():
    coord = Coordinator()
    obs0 = Observation(at=0.0, present={"k1": _fact("k1", level="error")}, branches=[], claims=[])
    coord.audit(obs0)
    obs1 = Observation(
        at=3.0, present={"k1": _fact("k1", level="error")},
        branches=[BranchState(name="fix/x", changed_paths=("services/x.py",), last_commit_at=3.0)],
        claims=[],
    )
    r1 = coord.audit(obs1)
    r2 = coord.audit(obs1)  # identical snapshot replayed
    r3 = coord.audit(obs1)
    assert r1 == r2 == r3 == {"k1": State.SUPPRESSED}
    # replaying doesn't fabricate reopens or duplicate items
    assert coord.items["k1"].reopen_count == 0
    assert len(coord.items) == 1


# --- I2 historical replay: does the recommended policy create duplicate work? ----------------


def test_i2_historical_replay_no_false_escalation():
    """Real repo evidence, not synthetic: docs/superpowers/research/2026-08-25-quality-
    coordination-cadence.md Section 6 + the actual PR #12 merge timestamp. Hours since a fixed
    origin of 2026-08-24T00:00Z.

    Episode A: config-unread:alerting.crash_auto_resolve_after_sec -- first seen 08-24 20:57Z
    (t=20.95), 3 observations, cleared 08-24 21:11Z (t=21.18), warning/medium, lifetime 0.23h.
    Episode B: backend-route-unused:POST:/api/alerts/*/resolve -- first seen 08-24 21:05Z
    (t=21.08), 2 observations, cleared same commit as A (t=21.18), info/medium, lifetime 0.10h.
    Episode C: api-usage:account.create_order -- first seen 08-25 16:42Z (t=40.70), a remote
    branch (chore/realtime-dp-investigation) touching tools/quality_audit/baseline.json pushed
    08-25 22:05Z (t=46.08, +5.4h) and merged via PR #12 at 2026-08-26T03:00:45Z (t=51.01, +10.3h).

    I2 Section 9's own conclusion, reproduced here executably: with a 6h floor (this finding class
    is info/warning, never error), the +5.4h branch push arrives BEFORE the 6h floor is reached
    (+6h) -- so escalation never fires; the finding stays suppressed until the branch merges and
    the fix is confirmed absent from the next main audit. Zero duplicate work, zero false-early
    escalation, matching the historical record exactly (the fix genuinely was in flight and the
    developer never saw a duplicate issue for it).
    """
    origin_config = _fact("config-unread:alerting.crash_auto_resolve_after_sec", level="warning")
    origin_route = _fact("backend-route-unused:POST:/api/alerts/*/resolve", level="info")
    origin_api = _fact("api-usage:account.create_order", level="info", paths=("tools/quality_audit/baseline.json",))

    coord = Coordinator()

    # Episode A/B: first observed together at t=21.08 (route appears after config in real history,
    # but both are present by the second replay point) -- 3 and 2 observations respectively inside
    # a burst, both cleared at t=21.18.
    coord.audit(Observation(at=20.95, present={origin_config.automation_key: origin_config}, branches=[], claims=[]))
    coord.audit(Observation(at=21.08, present={
        origin_config.automation_key: origin_config,
        origin_route.automation_key: origin_route,
    }, branches=[], claims=[]))
    r_cleared = coord.audit(Observation(at=21.18, present={}, branches=[], claims=[]))
    assert coord.items[origin_config.automation_key].state == State.RESOLVED
    assert coord.items[origin_route.automation_key].state == State.RESOLVED
    # neither ever left OBSERVED before resolving -- both lifetimes (0.23h, 0.10h) are far under
    # any floor in the 6-24h warning/info candidate range, so no escalation-eligible state was
    # ever reached for either.
    for key in (origin_config.automation_key, origin_route.automation_key):
        assert "escalation" not in "\n".join(coord.items[key].log).lower()

    # Episode C: first observed t=40.70; branch signal arrives t=46.08 (+5.4h, before the 6h
    # floor at t=46.70); a mid-window audit at the floor instant proves suppression already wins.
    coord.audit(Observation(at=40.70, present={origin_api.automation_key: origin_api}, branches=[], claims=[]))
    r_signal = coord.audit(Observation(
        at=46.08, present={origin_api.automation_key: origin_api},
        branches=[BranchState(name="chore/realtime-dp-investigation",
                               changed_paths=("tools/quality_audit/baseline.json",),
                               last_commit_at=46.08)],
        claims=[],
    ))
    assert r_signal[origin_api.automation_key] == State.SUPPRESSED
    r_at_floor = coord.audit(Observation(
        at=46.70, present={origin_api.automation_key: origin_api},
        branches=[BranchState(name="chore/realtime-dp-investigation",
                               changed_paths=("tools/quality_audit/baseline.json",),
                               last_commit_at=46.08)],
        claims=[],
    ))
    assert r_at_floor[origin_api.automation_key] == State.SUPPRESSED  # not escalated at the floor instant

    # merge at t=51.01; next main audit confirms the fix landed (finding absent)
    r_merged = coord.audit(Observation(
        at=51.01, present={},
        branches=[BranchState(name="chore/realtime-dp-investigation",
                               changed_paths=("tools/quality_audit/baseline.json",),
                               last_commit_at=46.08, merged=True)],
        claims=[],
    ))
    assert r_merged[origin_api.automation_key] == State.RESOLVED
    # across the whole replay: exactly 3 items, none duplicated, none escalated
    assert len(coord.items) == 3
    assert all(s in (State.RESOLVED,) for s in coord.states().values())
