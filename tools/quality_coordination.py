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
#
# +1 2026-08-27 (Task 9 Step 2 re-measurement): git log --merges --format="%cI" -60 main
# against this repo's real history showed 59 consecutive-merge gaps, median 0.29h, max
# 7.34h (2026-08-25T04:21:56 -05:00 -> 2026-08-25T11:42:03 -05:00, an ordinary overnight
# gap for a single-operator repo, not an anomaly). 7.34h materially exceeds the prior 6.0h
# floor, so raised to 8.0h - comfortably above the largest observed gap in this still-
# smaller-than-ideal 60-merge sample (spec §10 point 1 calls for a fuller 30-90-day pull).
FLOOR_HOURS_BRANCH = 8.0

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


# Spec §6.2's second identity kind: a numbered-plan file path. This repo's orchestrator
# skills deliberately never create .superpowers/sdd ledgers ("reconstruct progress from
# HEAD, do not keep a separate ledger"), so until 2026-08-28 this domain had no input at
# all and "plans never run to completion" had no detector. Plan docs under
# docs/superpowers/plans/ use `- [ ]` / `- [x]` task checkboxes; an unchecked box is the
# plan's own statement that a task is not done. Commit age comes from the plan file's own
# history (unlike SDD ledgers, plan docs are committed), which is the cheapest honest
# proxy for "is anyone still working this."
_CHECKBOX_RE = re.compile(r"^\s*- \[( |x|X)\] (.*)$")


def collect_plan_doc_signals(
    plan_paths: list[Path], *, repo_root: Path, git_runner: Runner, at: datetime,
) -> list[Signal]:
    signals: list[Signal] = []
    for path in plan_paths:
        if not path.exists():
            continue
        unchecked: list[str] = []
        checked = 0
        for line in path.read_text().splitlines():
            m = _CHECKBOX_RE.match(line)
            if not m:
                continue
            if m.group(1) == " ":
                unchecked.append(m.group(2).strip())
            else:
                checked += 1
        if not unchecked:
            continue
        try:
            rel = str(path.relative_to(repo_root))
        except ValueError:
            rel = path.name
        out = git_runner(["git", "log", "-1", "--format=%ct", "--", str(path)]).stdout.strip()
        last_commit_days = int((at.timestamp() - int(out)) // 86400) if out.isdigit() else None
        signals.append(Signal(
            identity=f"ledger:plan:{rel}",
            domain="ledger",
            payload={
                "unchecked": len(unchecked),
                "checked": checked,
                "first_unchecked": unchecked[0][:120],
                "last_commit_days": last_commit_days,
            },
            still_present=True,
        ))
    return signals


"""Standing-rule and process-hygiene compliance signal domain (spec §6.3). Identity: a
specific rule-instance key. Concrete instance implemented here: tools/quality_audit/
baseline.json's accepted_finding_ids entries lacking a dated notes addendum
(CLAUDE.md's "Baseline-ratchet semantics" convention) - spec §6.3's own worked example.
Floor is 0.0 per spec's own rationale: a structural check, not a timing question.
"""
FLOOR_HOURS_PROCESS_HYGIENE = 0.0


def _baseline_missing_dated_notes(baseline_text: str) -> list[str]:
    """`notes` is a dict keyed by check-prefix (e.g. "api-usage:*"), not a flat string -
    confirmed against the real tools/quality_audit/baseline.json (found in review,
    2026-08-27: an earlier draft treated it as a flat string, which would have flagged
    every accepted ID as missing a note, always, against the real file). This checks
    whether an accepted ID's own prefix has any notes entry at all - a presence check, not
    a verification that the ID's own specific addition within a shared note is itself
    individually dated (see this task's own note above on that narrower scope)."""
    data = json.loads(baseline_text)
    accepted = data.get("accepted_finding_ids", [])
    notes = data.get("notes", {})
    missing = []
    for finding_id in accepted:
        check = finding_id.split(":", 1)[0]
        prefix_key = f"{check}:*"
        if prefix_key not in notes:
            missing.append(finding_id)
    return missing


def collect_process_hygiene_signals(baseline_path: Path, baseline_text: str) -> list[Signal]:
    missing = _baseline_missing_dated_notes(baseline_text)
    return [
        Signal(
            identity=f"process_hygiene:{baseline_path.name}:{finding_id}",
            domain="process_hygiene",
            payload={"finding_id": finding_id, "reason": "accepted_finding_ids entry has no dated notes addendum"},
            still_present=True,
        )
        for finding_id in missing
    ]


"""Documentation/ROADMAP drift data feed (spec §6.5). NOT run through
coordination_engine.apply_observation - these functions never construct a Signal and are
never fed to the engine. Judging whether an open item is actually done is left to a human
or a Claude session reading this feed; this module never asserts it.
"""
_ROADMAP_BULLET_RE = re.compile(r"^- \[([ x])\] (.+)$")
_WORD_RE = re.compile(r"[a-z]{4,}")


def _open_roadmap_bullets(text: str) -> list[str]:
    """Collects each top-level `- [ ]` bullet's full text, including 6-space-indented
    continuation lines up to the next top-level bullet or a blank line - mirroring
    docs/superpowers/plans/2026-08-26-kanban-board-sync.md's own proven `_iter_bullets`
    approach for the identical problem. A bare single-line regex (found in review,
    2026-08-27) truncates virtually every real ROADMAP.md bullet to its first physical line
    - this repo's bullets routinely wrap onto continuation lines, confirmed against
    ROADMAP.md's own real content, which a MULTILINE-but-not-DOTALL `(.+)$` cannot see past.
    """
    lines = text.splitlines()
    bullets: list[str] = []
    current_lines: list[str] | None = None
    current_is_open = False

    for line in lines:
        match = _ROADMAP_BULLET_RE.match(line)
        if match:
            if current_lines is not None and current_is_open:
                bullets.append(" ".join(current_lines))
            current_is_open = match.group(1) == " "
            current_lines = [match.group(2).strip()]
            continue
        if current_lines is not None and line.startswith("      "):
            current_lines.append(line.strip())
            continue
        if current_lines is not None:
            if current_is_open:
                bullets.append(" ".join(current_lines))
            current_lines = None
            current_is_open = False

    if current_lines is not None and current_is_open:
        bullets.append(" ".join(current_lines))

    return bullets


def _significant_words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def collect_docs_roadmap_feed(roadmap_text: str, recent_commit_subjects: list[str]) -> list[dict]:
    feed = []
    for bullet in _open_roadmap_bullets(roadmap_text):
        bullet_words = _significant_words(bullet)
        related = [
            subject for subject in recent_commit_subjects
            if bullet_words & _significant_words(subject)
        ]
        feed.append({"roadmap_bullet": bullet, "possibly_related_commits": related})
    return feed


"""Cleanup/janitor action layer (spec §8). Detect and act are always separate phases
(spec §5); callers only invoke these when Task 8's CLI is run with --clean, and even then
only for identities Task 8's own eligibility check already confirmed. Every action is
deterministic, idempotent, path-contained, and independently verifiable
(the "Remediation authority rule" recorded in docs/superpowers/specs/
2026-08-27-autonomous-quality-coordination-workflow-design.md, generalized from
GitHub-write to git/filesystem-write per spec §3). main is never a target
under any code path (spec §11).
"""
_MAIN_BRANCH_PROTECTED_NAMES = {"main"}


def prune_worktrees(*, git_runner: Runner, repo_root: Path) -> str:
    """spec §8 action 1. No precondition beyond git's own built-in safety - this command
    only reconciles bookkeeping against worktrees already removed from disk."""
    result = git_runner(["git", "worktree", "prune"])
    return "succeeded" if result.returncode == 0 else f"failed:{result.stderr.strip()}"


def delete_merged_branch(branch: str, *, git_runner: Runner, main_branch: str = "main") -> str:
    """spec §8 action 2. Preconditions: (a) branch is an ancestor of main_branch via
    git merge-base --is-ancestor, (b) not the protected main branch itself, (c) `git branch
    -d` (never -D) as an independent second guard beyond (a) - git's own merge-check backs
    this up rather than being the only check."""
    if branch in _MAIN_BRANCH_PROTECTED_NAMES or branch == main_branch:
        return "refused:protected-main"

    check = git_runner(["git", "merge-base", "--is-ancestor", branch, main_branch])
    if check.returncode != 0:
        return "refused:not-merged"

    result = git_runner(["git", "branch", "-d", branch])
    if result.returncode != 0:
        return f"refused:{result.stderr.strip()}"
    return "succeeded"


def delete_sdd_scratch(
    plan_branch_candidates: list[str], plan_dir: Path, *, git_runner: Runner, main_branch: str = "main",
) -> str:
    """spec §8 action 3. Branch-mapping derivation: see this task's own note above -
    plan_branch_candidates is whatever the caller resolved from the ledger's first line
    plus the repo's <prefix>/<name> naming-convention fallback; this function itself never
    guesses beyond refusing when the candidate set isn't exactly one confirmed branch."""
    if len(plan_branch_candidates) != 1:
        return "refused:ambiguous-branch-mapping"
    branch = plan_branch_candidates[0]

    check = git_runner(["git", "merge-base", "--is-ancestor", branch, main_branch])
    if check.returncode != 0:
        return "refused:branch-not-merged"

    if not plan_dir.exists():
        return "refused:already-removed"
    import shutil
    shutil.rmtree(plan_dir)
    return "succeeded"


def run_cleanup_actions(
    eligible: list[tuple[str, str]], *, git_runner: Runner, repo_root: Path, sdd_root: Path,
    conn, at: datetime, dry_run: bool,
) -> None:
    """Executes (or, if dry_run, only reports) each eligible (identity, action_type) pair.
    Every attempt is logged regardless of outcome (spec §8: "identity, action type,
    timestamp, outcome, and whether it ran in --clean or was merely reported as eligible -
    before/after state for auditability")."""
    for identity, action_type in eligible:
        if dry_run:
            outcome = "not-executed:dry-run"
        elif action_type == "worktree_prune":
            outcome = prune_worktrees(git_runner=git_runner, repo_root=repo_root)
        elif action_type == "delete_merged_branch":
            branch = identity.removeprefix("branch:")
            outcome = delete_merged_branch(branch, git_runner=git_runner)
        elif action_type == "delete_sdd_scratch":
            outcome = "refused:not-implemented-in-this-caller"  # Task 8 wires real candidates
        else:
            outcome = f"failed:unknown-action-type-{action_type}"

        ce.log_cleanup_action(conn, identity, action_type, at, dry_run=dry_run, outcome=outcome)
    conn.commit()


"""CLI entrypoint (spec §5). Mirrors tools/project_manifest.py's --check/--write
convention: default invocation is detect + report only (safe); --clean also executes
eligible cleanup actions, always after a fresh detect pass in the same invocation."""
import argparse
import subprocess as _subprocess
from datetime import timezone


def _make_real_runner(cwd: Path) -> Runner:
    """The one place this module constructs a real, concrete Runner (found in review,
    2026-08-27 - see this task's own "Injectability gap" note above for what was wrong
    with the version this replaces). Bound to `cwd` so every git/gh/woodpecker call made
    through it actually runs against --repo-root, not the process's own working
    directory."""
    def _runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
        return _subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return _runner


_FLOOR_HOURS = {
    "branch": FLOOR_HOURS_BRANCH,
    "ledger": FLOOR_HOURS_LEDGER,
    "process_hygiene": FLOOR_HOURS_PROCESS_HYGIENE,
}

# Which domains have a defined cleanup action at all (spec §8's three actions map onto a
# subset of signal identities - e.g. process_hygiene findings have no corresponding
# cleanup action, they are surfaced for a human to fix the baseline.json note by hand).
_CLEANUP_ACTION_FOR_DOMAIN = {
    "branch": "delete_merged_branch",
}


def run_detect_cycle(
    repo_root: Path, *, at: datetime | None = None,
    git_runner: Runner | None = None, http_getter: HttpGetter | None = None,
) -> dict:
    at = at or datetime.now(timezone.utc)
    git_runner = git_runner or _make_real_runner(repo_root)
    conn = ce._connect()

    branch_list = git_runner(["git", "branch", "--format=%(refname:short)"]).stdout.splitlines()
    branch_names = [b.strip() for b in branch_list if b.strip() and b.strip() != "main"]

    branch_signals, suppressed, immediate = collect_branch_signals(
        branch_names, git_runner=git_runner, gh_runner=git_runner,
        woodpecker_runner=git_runner, conn=conn,
        worktrees_root=repo_root / ".claude" / "worktrees", at=at,
    )

    ledger_paths = sorted((repo_root / ".superpowers" / "sdd").glob("*/progress.md")) \
        if (repo_root / ".superpowers" / "sdd").exists() else []
    ledger_signals = collect_ledger_signals(ledger_paths, at=at)
    plans_dir = repo_root / "docs" / "superpowers" / "plans"
    plan_paths = sorted(plans_dir.glob("*.md")) if plans_dir.exists() else []
    ledger_signals += collect_plan_doc_signals(
        plan_paths, repo_root=repo_root, git_runner=git_runner, at=at,
    )

    baseline_path = repo_root / "tools" / "quality_audit" / "baseline.json"
    process_hygiene_signals = (
        collect_process_hygiene_signals(baseline_path, baseline_path.read_text())
        if baseline_path.exists() else []
    )

    all_signals = branch_signals + ledger_signals + process_hygiene_signals
    states = ce.apply_observation(
        conn, all_signals, at, _FLOOR_HOURS, suppressed_keys=suppressed, immediate_keys=immediate,
    )

    roadmap_path = repo_root / "ROADMAP.md"
    docs_feed = (
        collect_docs_roadmap_feed(roadmap_path.read_text(), [])
        if roadmap_path.exists() else []
    )

    app_report = fetch_app_report("http://fastapi:8000", getter=http_getter)

    escalated = sum(1 for s in states.values() if s == "escalation_eligible")
    ce.record_run(conn, at, signals_observed=len(all_signals), signals_escalated=escalated)
    conn.commit()

    return {
        "branch": {k: v for k, v in states.items() if k.startswith("branch:")},
        "ledger": {k: v for k, v in states.items() if k.startswith("ledger:")},
        "process_hygiene": {k: v for k, v in states.items() if k.startswith("process_hygiene:")},
        "docs_roadmap_feed": docs_feed,
        "app_report": app_report,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.quality_coordination")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--clean", action="store_true", help="also execute eligible cleanup actions")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None, *,
    git_runner: Runner | None = None, http_getter: HttpGetter | None = None,
) -> int:
    args = _parse_args(argv)
    git_runner = git_runner or _make_real_runner(args.repo_root)
    report = run_detect_cycle(args.repo_root, git_runner=git_runner, http_getter=http_getter)

    if args.clean:
        eligible = [
            (identity, _CLEANUP_ACTION_FOR_DOMAIN[identity.split(":", 1)[0]])
            for identity, state in report["branch"].items()
            if state == "escalation_eligible" and identity.split(":", 1)[0] in _CLEANUP_ACTION_FOR_DOMAIN
        ]
        conn = ce._connect()
        run_cleanup_actions(
            eligible, git_runner=git_runner, repo_root=args.repo_root,
            sdd_root=args.repo_root / ".superpowers" / "sdd", conn=conn,
            at=datetime.now(timezone.utc), dry_run=False,
        )

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
