from services.quality.models import QualityFinding, QualityReport, severity_rank


def test_quality_finding_serializes_stably():
    finding = QualityFinding(
        finding_id="router-unmounted:services.foo.routes",
        check="router-registration",
        severity="error",
        confidence="high",
        source="ci",
        scope="services.foo.routes",
        summary="router exists but is not mounted",
        evidence={"path": "services/foo/routes.py"},
        remediation="include foo_routes.router in main.py",
    )
    assert finding.to_dict()["finding_id"] == "router-unmounted:services.foo.routes"
    assert finding.to_dict()["severity"] == "error"


def test_quality_report_counts_by_severity():
    report = QualityReport(findings=[
        QualityFinding("a", "x", "warning", "high", "ci", "a", "A", {}),
        QualityFinding("b", "x", "error", "high", "ci", "b", "B", {}),
    ])
    assert report.counts() == {"info": 0, "warning": 1, "error": 1}
    assert report.overall_status() == "error"


def test_severity_rank_orders_error_highest():
    assert severity_rank("error") > severity_rank("warning") > severity_rank("info")
