"""Shared quality-finding contract used by both runtime diagnostics and CI
audit tooling (Quality Control Plane,
docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md,
moved there 2026-09-06, planning-lanes migration, Task 1). Every quality-facing checker in the
codebase should produce `QualityFinding`s and roll them into a
`QualityReport` rather than inventing its own ad-hoc shape, so runtime and CI
consumers can share one severity/serialization contract.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warning", "error"]
Confidence = Literal["high", "medium", "low"]
Source = Literal["runtime", "ci"]

_SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "error": 2}


@dataclass(frozen=True)
class QualityFinding:
    finding_id: str
    check: str
    severity: Severity
    confidence: Confidence
    source: Source
    scope: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    findings: list[QualityFinding]

    def counts(self) -> dict[str, int]:
        return {
            "info": sum(f.severity == "info" for f in self.findings),
            "warning": sum(f.severity == "warning" for f in self.findings),
            "error": sum(f.severity == "error" for f in self.findings),
        }

    def overall_status(self) -> str:
        counts = self.counts()
        if counts["error"]:
            return "error"
        if counts["warning"]:
            return "warning"
        return "ok"


def severity_rank(value: Severity) -> int:
    return _SEVERITY_RANK[value]
