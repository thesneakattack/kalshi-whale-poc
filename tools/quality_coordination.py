"""quality_coordination — Autonomous Quality Coordination (AQC): a standalone workflow
tool that acts as an automated project manager and janitor over THIS REPOSITORY'S OWN
engineering workflow (branch/PR/CI lifecycle, superpowers plan/ledger execution health,
standing-rule and process hygiene) - informed by, but never auditing, the trading
application's own self-reported diagnostics
(docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md).

"AQC" now names, and only names, this tool. The prior implementation under this name
audited the trading application's own static code findings instead - a real, corrected
mistargeting; that module is kept, renamed to tools/quality_ratchet.py (main@440da36), and
is a wholly separate tool from this one (spec §1, §12).

Standalone workflow tool, not application code: never imported by main.py or any part of
the live trading app (CLAUDE.md's "Workflow/tooling and application code must never
overlap" standing rule). No GitHub write authority, no write path outside
tools/quality_coordination_data/ (owned by tools/coordination_engine.py) plus the three
narrowly-scoped git/filesystem cleanup actions in run_cleanup_actions (spec §8, §11).

Invocation is manual-only for v1 (spec §5):
    python -m tools.quality_coordination            # detect + report only (default, safe)
    python -m tools.quality_coordination --clean    # also executes eligible cleanup actions
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from typing import Callable, Sequence

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
HttpGetter = Callable[[str, float], dict]

_APP_REPORT_PATHS = {
    "quality_summary": "/api/quality/summary",
    "health_pipeline": "/api/health/pipeline",
    "health_faults": "/api/health/faults",
}


def _default_http_get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-whale-poc-aqc-workflow"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_app_report(
    base_url: str, *, getter: HttpGetter | None = None, timeout: float = 5.0,
) -> dict[str, dict | None]:
    """Reads the trading app's existing read-only diagnostics exactly as any other
    external HTTP client would (spec §6.4's "the app never knows AQC exists"). Used as
    CONTEXT for judging/suppressing signals from other domains - never itself produces a
    Signal or asserts a finding. Degrades every call independently to None on failure
    (network error, non-2xx, non-JSON body, or - until Task 11's PUBLIC_PATHS change is
    separately human-approved and merged - a live environment with real auth configured
    returning a redirect/401 instead of JSON); never raises, matching
    tools/quality_ratchet.py's fetch_branch_signals degrade-on-failure convention."""
    getter = getter or _default_http_get
    report: dict[str, dict | None] = {}
    for key, path in _APP_REPORT_PATHS.items():
        try:
            report[key] = getter(f"{base_url}{path}", timeout)
        except Exception:
            report[key] = None
    return report


"""Branch/PR/CI lifecycle health signal domain (spec §6.1). Identity: "branch:<name>".
Payload: last-commit age, open-PR state, Woodpecker status for the branch tip, whether a
corresponding .claude/worktrees/ directory exists, and CI staleness detail when the status
is ambiguously "pending". Suppression candidates: an open PR actively receiving
commits/reviews, an explicit "paused, not stalled" active-tracks-board.md note (both spec
§6.1), and a co-dispatch cluster - N branches created within CLUSTER_WINDOW_MINUTES of each
other with no individual PR yet, treated as one in-flight unit rather than N independent
stale-branch signals (this plan's own extension of §6.1's suppression-candidate list; see
this task's evidence note - no new engine architecture, just a third suppression source
computed here before calling apply_observation).
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import coordination_engine as ce
from tools.coordination_engine import Signal

# Evidence: git log --merges --format="%H %cI" -10 main against this repo's real history
# (measured while writing this plan) showed the 10 most recent merges spaced ~5 minutes to
# ~5.4 hours apart. Set just above the largest observed gap in that sample. A 10-merge
# sample, not the fuller 30-90-day pull spec §10 point 1 calls for - re-measure before
# treating this as final (Task 9).
FLOOR_HOURS_BRANCH = 6.0

# Evidence: no direct historical source for "how far apart do one dispatch's branches
# land" exists in this repo; chosen generously since suppression only delays escalation by
# one more run, never blocks it permanently - see this task's evidence note.
CLUSTER_WINDOW_MINUTES = 20.0

_COMMIT_STATUS_FAILURE_STATES = {"failure", "error"}


def _branch_first_commit_at(branch: str, main_branch: str, git_runner: Runner) -> datetime | None:
    """Proxy for branch-creation time: the oldest commit reachable from `branch` but not
    from `main_branch` - the same "first commit unique to this branch" approach
    tools/quality_ratchet.py's fetch_branch_signals takes from the GitHub compare API,
    computed locally here via git log instead."""
    result = git_runner(["git", "log", f"{main_branch}..{branch}", "--format=%cI"])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    return datetime.fromisoformat(lines[-1])  # oldest is last - git log lists newest-first


def _cluster_siblings(
    creation_times: dict[str, datetime], window_minutes: float,
) -> dict[str, frozenset[str]]:
    """For each branch, the set of OTHER branches whose creation time falls within
    `window_minutes` of its own. A branch with no nearby sibling gets an empty set - not a
    cluster of one."""
    result: dict[str, frozenset[str]] = {}
    window = timedelta(minutes=window_minutes)
    for name, at in creation_times.items():
        siblings = frozenset(
            other for other, other_at in creation_times.items()
            if other != name and abs(other_at - at) <= window
        )
        result[name] = siblings
    return result


def _pr_state(branch: str, gh_runner: Runner) -> str | None:
    result = gh_runner([
        "gh", "pr", "list", "--head", branch, "--state", "all",
        "--json", "state", "--limit", "1",
    ])
    results = json.loads(result.stdout or "[]")
    return results[0]["state"] if results else None


def _commit_status(branch: str, gh_runner: Runner) -> str | None:
    """`gh api ... --jq ".state"` raw-unquotes a scalar jq result (unlike `gh ... --json`,
    which _pr_state above uses and which IS real JSON) - stdout is already the bare state
    string ("failure", "pending", "success", ...), not a JSON value to parse. Parsing it
    with json.loads would be the bug here, not the fix - see the test fixtures in this
    task's test file for the same distinction spelled out against a fake runner."""
    result = gh_runner([
        "gh", "api", f"repos/{{owner}}/{{repo}}/commits/{branch}/status",
        "--jq", ".state",
    ])
    stdout = (result.stdout or "").strip()
    return stdout or None


def _woodpecker_step_progress(branch: str, woodpecker_runner: Runner) -> dict:
    """Real per-step Started/Stopped detail via scripts/woodpecker-status --pipeline N
    (spec §6.1's "found live 2026-08-27" CI-staleness paragraph). Degrades to an empty dict
    on any parse failure - a missing/ambiguous CI detail is not itself an error for this
    domain, just less payload to work with."""
    try:
        result = woodpecker_runner(["scripts/woodpecker-status", "--branch", branch, "--json"])
        data = json.loads(result.stdout or "{}")
    except Exception:
        return {}
    steps = data.get("steps") or []
    if not steps:
        return {}
    furthest = steps[-1]
    return {
        "furthest_step": furthest.get("name"),
        "furthest_step_started_at": (
            datetime.fromtimestamp(furthest["started"], tz=timezone.utc).isoformat()
            if furthest.get("started") else None
        ),
    }


def collect_branch_signals(
    branch_names: list[str],
    *,
    git_runner: Runner,
    gh_runner: Runner,
    woodpecker_runner: Runner,
    conn,
    worktrees_root: Path,
    main_branch: str = "main",
    at: datetime,
) -> tuple[list[Signal], frozenset[str], frozenset[str]]:
    creation_times: dict[str, datetime] = {}
    for branch in branch_names:
        created = _branch_first_commit_at(branch, main_branch, git_runner)
        if created is not None:
            creation_times[branch] = created

    clusters = _cluster_siblings(creation_times, CLUSTER_WINDOW_MINUTES)

    signals: list[Signal] = []
    suppressed_keys: set[str] = set()
    immediate_keys: set[str] = set()

    for branch in branch_names:
        identity = f"branch:{branch}"
        pr_state = _pr_state(branch, gh_runner)
        commit_status = _commit_status(branch, gh_runner) if pr_state is not None else None

        # last_commit_age_hours/has_worktree were dropped from this payload (found in
        # review, 2026-08-27): apply_observation fingerprints the WHOLE Signal.payload
        # (Task 1), and last_commit_age_hours is computed from `at`, which advances every
        # AQC cycle - a full-payload fingerprint comparison across two cycles could then
        # never match even when nothing about the branch's CI status actually changed,
        # silently breaking the possibly_stuck detection below in every real invocation,
        # not just as a test-fixture mismatch. worktrees_root/main_branch stay accepted
        # parameters per this domain's documented interface even though this payload no
        # longer folds worktree existence into it, for the same reason.
        payload: dict = {
            "pr_state": pr_state,
            "ci_status": commit_status,
        }

        if commit_status == "pending":
            step_progress = _woodpecker_step_progress(branch, woodpecker_runner)
            payload.update(step_progress)
            prior = ce.get_prior_row(conn, identity)
            # Compare against the prior row's stored content fingerprint directly, rather
            # than re-parsing a payload the engine doesn't expose as JSON on the row (only
            # `fingerprint`, the hash, is stored) - two consecutive runs whose full payload
            # fingerprints match while ci_status stays "pending" means nothing about this
            # branch's CI run advanced between them.
            if prior is not None:
                candidate_fp = ce._fingerprint(payload)
                if candidate_fp == prior["fingerprint"]:
                    payload["possibly_stuck"] = True

        if commit_status in _COMMIT_STATUS_FAILURE_STATES:
            # spec §6.1: "a failure/error state ... should surface immediately, at full
            # severity, never behind a persistence floor."
            immediate_keys.add(identity)

        if pr_state == "OPEN":
            # spec §6.1 suppression candidate: an open PR actively receiving commits/reviews.
            suppressed_keys.add(identity)
        elif clusters.get(branch):
            # This plan's co-dispatch-cluster extension (see task docstring above).
            suppressed_keys.add(identity)

        signals.append(Signal(identity=identity, domain="branch", payload=payload, still_present=True))

    return signals, frozenset(suppressed_keys), frozenset(immediate_keys)


"""Plan and ledger execution health signal domain (spec §6.2). Identity: the ledger/plan
file's own path (relative to repo root). Payload: last recorded task/round line. Spec
§6.2's other payload field - days since the last commit touching that plan's ASSOCIATED
BRANCH (not the ledger file's own commit history: .superpowers/sdd/<plan>/ is gitignored
scratch state, so the ledger file itself is never committed at all) - needs the same
ledger-to-branch mapping Task 7 already names as an implementation-time detail (spec §8
action 3), not resolved here either; see this task's own Interfaces note above. Ledger-
line-format caveat: see this task's own note above - no real .superpowers/sdd/*/progress.md
exists in this checkout to validate the parsing against yet.
"""
_LEDGER_COMPLETE_RE = re.compile(r"\bSTATUS:\s*COMPLETE\b", re.IGNORECASE)

# Evidence: see this task's own note - no populated real ledger exists yet to measure
# completion time from directly; provisional, deliberately longer than the branch domain's
# floor since a plan/ledger can legitimately span several working sessions.
FLOOR_HOURS_LEDGER = 72.0


def _ledger_last_state_line(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _ledger_is_complete(last_line: str) -> bool:
    return bool(_LEDGER_COMPLETE_RE.search(last_line))


def collect_ledger_signals(ledger_paths: list[Path], *, at: datetime) -> list[Signal]:
    signals: list[Signal] = []
    for path in ledger_paths:
        if not path.exists():
            continue
        text = path.read_text()
        last_line = _ledger_last_state_line(text)
        if _ledger_is_complete(last_line):
            continue

        signals.append(Signal(
            identity=f"ledger:{path}",
            domain="ledger",
            payload={"last_state_line": last_line},
            still_present=True,
        ))
    return signals
