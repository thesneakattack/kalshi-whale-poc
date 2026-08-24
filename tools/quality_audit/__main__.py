"""CLI entrypoint and scanner registry for the Quality Control Plane's
static audit framework (docs/superpowers/plans/2026-08-24-quality-control-
plane.md, Task 3). `python -m tools.quality_audit` runs every registered
scanner over the current repo and reports new/existing/resolved findings
against tools/quality_audit/baseline.json, exiting nonzero only for new
high-confidence errors in default mode - an already-baselined error, and
any warning/info finding whether new or not, are surfaced but don't fail
the gate. That's what keeps this a CI ratchet instead of an all-or-nothing
gate: `--strict` additionally fails on new warnings for a stricter local
check.

"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from services.quality.models import QualityFinding, QualityReport
from tools.quality_audit.api_usage import scan_api_usage
from tools.quality_audit.background import scan_background_wiring
from tools.quality_audit.baseline import BaselineComparison, compare_to_baseline, load_baseline
from tools.quality_audit.config_usage import scan_config_usage
from tools.quality_audit.frontend_contract import scan_frontend_contract
from tools.quality_audit.persistence import scan_persistence_isolation
from tools.quality_audit.resources import scan_resource_lifecycle
from tools.quality_audit.routers import scan_router_registration

Scanner = Callable[[Path], list[QualityFinding]]

_SCANNERS: list[Scanner] = [
    scan_router_registration,
    scan_background_wiring,
    scan_persistence_isolation,
    scan_resource_lifecycle,
    scan_config_usage,
    scan_api_usage,
    scan_frontend_contract,
]

_DEFAULT_BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"


def run_audit(repo_root: Path) -> QualityReport:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []
    for scanner in _SCANNERS:
        findings.extend(scanner(repo_root))
    return QualityReport(findings=findings)


def compute_exit_code(comparison: BaselineComparison, strict: bool = False) -> int:
    if comparison.new_high_confidence_errors():
        return 1
    if strict and any(f.severity == "warning" for f in comparison.new):
        return 1
    return 0


def _print_summary(comparison: BaselineComparison) -> None:
    print(
        f"quality-audit: {len(comparison.new)} new, "
        f"{len(comparison.existing)} existing (baselined), "
        f"{len(comparison.resolved)} resolved"
    )
    for finding in comparison.new:
        if finding.severity in ("error", "warning"):
            print(f"  [{finding.severity}/{finding.confidence}] {finding.finding_id}: {finding.summary}")
    for finding_id in comparison.resolved:
        print(f"  [resolved] {finding_id}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.quality_audit")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--baseline", type=Path, default=_DEFAULT_BASELINE_PATH)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail the gate on new warnings, not only new high-confidence errors",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = args.repo_root.resolve()
    report = run_audit(repo_root)
    baseline_ids = load_baseline(args.baseline)
    comparison = compare_to_baseline(report, baseline_ids)

    _print_summary(comparison)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "findings": [f.to_dict() for f in report.findings],
            "new": [f.finding_id for f in comparison.new],
            "existing": [f.finding_id for f in comparison.existing],
            "resolved": comparison.resolved,
        }
        args.json_out.write_text(json.dumps(payload, indent=2))

    return compute_exit_code(comparison, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
