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
