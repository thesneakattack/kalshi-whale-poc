"""Baseline-ratchet comparison for the Quality Control Plane's static audit
CLI (docs/superpowers/plans/2026-08-24-quality-control-plane.md, Task 3
Step 4). The baseline stores stable finding IDs only, not full finding
evidence, precisely so it stays reviewable in a PR diff and so a finding
whose *evidence* changes (line numbers, wording) without a real fix doesn't
spuriously look "new."
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from services.quality.models import QualityFinding, QualityReport


@dataclass(frozen=True)
class BaselineComparison:
    new: list[QualityFinding]
    existing: list[QualityFinding]
    resolved: list[str]

    def new_high_confidence_errors(self) -> list[QualityFinding]:
        return [
            finding
            for finding in self.new
            if finding.severity == "error" and finding.confidence == "high"
        ]


def load_baseline(path: Path) -> set[str]:
    """Accepted finding IDs, or an empty set if no baseline file exists yet
    (a fresh checkout with no baseline.json means "everything is new," not
    an error)."""
    path = Path(path)
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    return set(data.get("accepted_finding_ids", []))


def compare_to_baseline(report: QualityReport, baseline_ids: set[str]) -> BaselineComparison:
    current_ids = {finding.finding_id for finding in report.findings}
    new = [f for f in report.findings if f.finding_id not in baseline_ids]
    existing = [f for f in report.findings if f.finding_id in baseline_ids]
    resolved = sorted(baseline_ids - current_ids)
    return BaselineComparison(new=new, existing=existing, resolved=resolved)
