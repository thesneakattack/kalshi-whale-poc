"""Pure, in-memory, no-write quality-finding coordination state machine.

Encodes, executably, the policies already derived from real repo evidence in:
  - I1 (docs/archive/lane-9-tooling-ci-process-governance/research/2026-08-25-quality-finding-identity-audit.md #9):
    identity is `automation_key`, location-free; resolution only via absence from a fresh
    integrated-`main` audit; recurrence is a reopen of the same key, not a new item; rename is a
    new key (out of scope for this prototype -- no rename detection is implemented).
  - I2 (docs/archive/lane-9-tooling-ci-process-governance/research/2026-08-25-quality-coordination-cadence.md #9): persistence
    floors, de-bursted observation counts, and branch-signal staleness expiry, all derived from
    this repo's measured commit/PR/branch cadence, not chosen by intuition.
  - I3 (docs/archive/lane-9-tooling-ci-process-governance/research/2026-08-25-active-work-suppression-matrix.md #11): the
    precedence order exact-claim > path-overlap(PR or branch, merged excluded, expiry-bounded) >
    persistence-floor > escalation-eligible, and the invariant that a live signal only ever
    *extends* the wait (escalation time = max(floor, end_of_suppression), never min).

No network I/O, no GitHub client, no credentials. `Coordinator.audit()` takes a caller-supplied
`Observation` snapshot (a fresh `main` audit's finding set plus whatever remote branch/claim state
the caller already fetched) and returns a pure function of accumulated snapshots. Feeding it is the
caller's problem; deciding what to do with a snapshot is this module's problem.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# I2 #9 candidate ranges: "2-6h" for error/high, "6-24h" for warning/info. This prototype takes the
# conservative (fastest-to-escalate) end of each range, since a wider value only ever delays
# escalation further -- the low end is what the scenario matrix must survive without false-early
# escalation.
FLOOR_HOURS: dict[str, float] = {
    "error": 2.0,
    "warning": 6.0,
    "info": 6.0,
}

# I2 #9: "≥ 2 observations ≥ 1h apart (de-bursted)" as an alternative path to the floor, for a
# finding class whose severity isn't in FLOOR_HOURS or as a secondary signal. De-bursting means
# commits inside the same burst (< 1h gap) collapse to a single observation for this purpose.
DEBURST_GAP_HOURS = 1.0
DEBURST_COUNT = 2

# I2 #9: "idle ... >= 2x longest observed live-branch idle gap (~3h), hard-capped at <= 2x longest
# observed branch life (~24h)".
STALENESS_IDLE_HOURS = 3.0
STALENESS_HARD_CAP_HOURS = 24.0


class State(str, Enum):
    OBSERVED = "observed"
    SUPPRESSED = "suppressed_pending_work"
    ESCALATION_ELIGIBLE = "escalation_eligible"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class FindingFact:
    """One finding as reported by a fresh `main` audit. `location` is display-only and never
    participates in identity -- see I1 #9's location-free automation_key contract."""

    automation_key: str
    scope_paths: tuple[str, ...]
    level: str  # "error" | "warning" | "info"
    location: str = ""


@dataclass(frozen=True)
class BranchState:
    """An open PR or a pushed remote branch. I3 #11 unions PR-path-overlap (signal B) and live-
    branch-path-overlap (signal C) into one precedence tier -- both are modeled as this same
    shape; `merged` and staleness are evaluated identically for either."""

    name: str
    changed_paths: tuple[str, ...]
    last_commit_at: float
    merged: bool = False


@dataclass(frozen=True)
class Claim:
    """I3 signal A: an exact, structured claim naming this automation_key (e.g. PR metadata),
    the strongest and only override-precedence signal."""

    automation_key: str
    source: str


@dataclass
class Observation:
    """One fresh `main` audit event. `present` must contain ONLY findings the audit actually saw
    on integrated `main` -- a branch/PR-local finding must never appear here (see
    test_branch_only_finding_never_reaches_repo_state), which is what makes "branch-only findings
    cannot become repository escalation candidates" a structural property rather than a rule this
    module has to enforce."""

    at: float
    present: dict[str, FindingFact]
    branches: list[BranchState]
    claims: list[Claim]


@dataclass
class Item:
    automation_key: str
    state: State
    level: str
    first_observed_at: float
    last_observed_at: float
    observation_times: list[float] = field(default_factory=list)
    scope_paths: tuple[str, ...] = ()
    resolved_at: float | None = None
    reopen_count: int = 0
    log: list[str] = field(default_factory=list)

    def explain(self, at: float, msg: str) -> None:
        self.log.append(f"[t={at:.2f}h] {msg}")


class Coordinator:
    """No-write, in-memory coordinator. Call `audit()` once per fresh `main` observation, in
    timestamp order. Never mutates external state; the caller decides what a resulting
    ESCALATION_ELIGIBLE state means to do (report, SARIF, issue -- all out of scope here)."""

    def __init__(self) -> None:
        self.items: dict[str, Item] = {}

    def states(self) -> dict[str, State]:
        return {k: v.state for k, v in self.items.items()}

    def audit(self, obs: Observation) -> dict[str, State]:
        # Step 1 (evidence rule / I1 #9): only a fresh integrated-main audit may resolve a
        # finding. Anything currently tracked that is absent from THIS audit's present set is
        # resolved now, unconditionally -- suppression state does not block this.
        for item in self.items.values():
            if item.automation_key not in obs.present and item.state != State.RESOLVED:
                item.state = State.RESOLVED
                item.resolved_at = obs.at
                item.explain(obs.at, "resolved: absent from fresh main audit")

        # Step 2: process every finding this audit actually saw.
        for key, fact in obs.present.items():
            item = self.items.get(key)
            if item is None:
                item = Item(
                    automation_key=key,
                    state=State.OBSERVED,
                    level=fact.level,
                    first_observed_at=obs.at,
                    last_observed_at=obs.at,
                    scope_paths=fact.scope_paths,
                )
                item.observation_times.append(obs.at)
                item.explain(obs.at, "new item observed on main")
                self.items[key] = item
            elif item.state == State.RESOLVED:
                # I1 #9 recurrence semantics: reopen the SAME item (history retained), restart the
                # persistence clock -- a stale resolution shouldn't let the floor treat this as if
                # it had been open the whole time.
                item.reopen_count += 1
                item.state = State.OBSERVED
                item.first_observed_at = obs.at
                item.last_observed_at = obs.at
                item.observation_times.append(obs.at)
                item.level = fact.level
                item.scope_paths = fact.scope_paths
                item.explain(obs.at, f"reopened (recurrence #{item.reopen_count}); prior history retained")
            else:
                item.last_observed_at = obs.at
                item.observation_times.append(obs.at)
                item.level = fact.level
                item.scope_paths = fact.scope_paths
                item.explain(obs.at, "repeated observation on main")

            self._evaluate(item, obs)

        return self.states()

    # -- internal policy evaluation, I2/I3 -----------------------------------------------------

    def _deburst_count(self, item: Item) -> int:
        times = sorted(item.observation_times)
        if not times:
            return 0
        count = 1
        last = times[0]
        for t in times[1:]:
            if t - last >= DEBURST_GAP_HOURS:
                count += 1
                last = t
        return count

    def _floor_met(self, item: Item, at: float) -> bool:
        elapsed = at - item.first_observed_at
        floor = FLOOR_HOURS.get(item.level, FLOOR_HOURS["info"])
        if elapsed >= floor:
            return True
        return self._deburst_count(item) >= DEBURST_COUNT and elapsed >= DEBURST_GAP_HOURS

    def _suppressing_signal(self, item: Item, obs: Observation) -> BranchState | Claim | None:
        # I3 #11 precedence: exact claim (A) first -- strongest, overrides everything else.
        for claim in obs.claims:
            if claim.automation_key == item.automation_key:
                return claim
        # Then path-overlap (B union C): not merged (S12), not stale/hard-capped.
        for branch in obs.branches:
            if branch.merged:
                continue
            idle = obs.at - branch.last_commit_at
            if idle >= STALENESS_HARD_CAP_HOURS or idle >= STALENESS_IDLE_HOURS:
                continue
            if any(p in branch.changed_paths for p in item.scope_paths):
                return branch
        return None

    def _evaluate(self, item: Item, obs: Observation) -> None:
        signal = self._suppressing_signal(item, obs)
        if signal is not None:
            item.state = State.SUPPRESSED
            if isinstance(signal, Claim):
                item.explain(obs.at, f"suppressed: exact claim from {signal.source}")
            else:
                item.explain(obs.at, f"suppressed: path overlap with live branch {signal.name}")
            return
        if self._floor_met(item, obs.at):
            item.state = State.ESCALATION_ELIGIBLE
            item.explain(obs.at, "escalation-eligible: persistence floor met, no active-work signal (expired signal noted above if any)")
        else:
            item.state = State.OBSERVED
            item.explain(obs.at, "observed: below persistence floor, no active-work signal")
