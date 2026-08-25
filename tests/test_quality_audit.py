"""Tests for the Quality Control Plane's static audit framework: baseline-
ratchet comparison, the CLI's exit-code gate, the source-file walking
helpers, the router-registration/background-wiring scanners, and the
persistence/resource-lifecycle/config-usage/API-usage scanners
(docs/superpowers/plans/2026-08-24-quality-control-plane.md, Tasks 3-5).
Baseline-gate and CLI-plumbing tests use synthetic QualityFindings and
temporary fixture trees rather than depending on real repo content
triggering (or not triggering) a finding; the scanner tests use small
synthetic fixture repos for the same reason (real repo content shouldn't
need to change to keep a unit test green).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from services.quality.models import QualityFinding, QualityReport
from tools.quality_audit import __main__ as audit_cli
from tools.quality_audit import (
    api_usage,
    background,
    config_usage,
    kalshi_contract_docs,
    persistence,
    resources,
    routers,
    source,
)
from tools.quality_audit.baseline import compare_to_baseline, load_baseline

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _finding(finding_id: str, severity: str = "error", confidence: str = "high") -> QualityFinding:
    return QualityFinding(
        finding_id=finding_id,
        check="fixture-check",
        severity=severity,
        confidence=confidence,
        source="ci",
        scope=finding_id,
        summary=f"synthetic finding {finding_id}",
    )


# --- Step 1: baseline-ratchet gate behavior ---------------------------------


def test_existing_error_in_baseline_does_not_fail_default_gate():
    finding = _finding("existing-error:a")
    report = QualityReport(findings=[finding])
    comparison = compare_to_baseline(report, baseline_ids={finding.finding_id})

    assert comparison.new == []
    assert comparison.existing == [finding]
    assert audit_cli.compute_exit_code(comparison) == 0


def test_new_high_confidence_error_fails_default_gate():
    finding = _finding("new-error:a", severity="error", confidence="high")
    report = QualityReport(findings=[finding])
    comparison = compare_to_baseline(report, baseline_ids=set())

    assert comparison.new == [finding]
    assert audit_cli.compute_exit_code(comparison) == 1


def test_new_warning_is_reported_but_does_not_fail_default_gate():
    finding = _finding("new-warning:a", severity="warning", confidence="high")
    report = QualityReport(findings=[finding])
    comparison = compare_to_baseline(report, baseline_ids=set())

    assert comparison.new == [finding]
    assert audit_cli.compute_exit_code(comparison) == 0
    assert audit_cli.compute_exit_code(comparison, strict=True) == 1


def test_resolved_baseline_id_is_reported_as_resolved():
    report = QualityReport(findings=[])
    comparison = compare_to_baseline(report, baseline_ids={"stale-finding:a"})

    assert comparison.resolved == ["stale-finding:a"]
    assert audit_cli.compute_exit_code(comparison) == 0


# --- baseline.py: file I/O ---------------------------------------------------


def test_load_baseline_missing_file_returns_empty_set(tmp_path):
    assert load_baseline(tmp_path / "does-not-exist.json") == set()


def test_load_baseline_reads_accepted_finding_ids(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"version": 1, "accepted_finding_ids": ["a", "b"]}))
    assert load_baseline(path) == {"a", "b"}


# --- source.py: file discovery ----------------------------------------------


def test_iter_python_files_excludes_known_noise_dirs(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "real.py").write_text("x = 1\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "should_not_exist.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("x = 1\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("x = 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hook.py").write_text("x = 1\n")
    (tmp_path / ".ddev").mkdir()
    (tmp_path / ".ddev" / "conf.py").write_text("x = 1\n")

    found = source.iter_python_files(tmp_path)

    assert found == [tmp_path / "services" / "real.py"]


def test_iter_python_files_excludes_tests_by_default(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("x = 1\n")

    assert source.iter_python_files(tmp_path) == []
    assert source.iter_python_files(tmp_path, include_tests=True) == [tmp_path / "tests" / "test_x.py"]


def test_parse_python_and_relative_path(tmp_path):
    module_path = tmp_path / "example.py"
    module_path.write_text("VALUE = 1\n")

    tree = source.parse_python(module_path)
    assert isinstance(tree, ast.Module)
    assert len(tree.body) == 1
    assert source.relative_path(tmp_path, module_path) == "example.py"


# --- routers.py: router-registration scanner --------------------------------


def test_mounted_router_produces_no_finding(tmp_path):
    _write(tmp_path / "services" / "foo" / "routes.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n")
    _write(
        tmp_path / "main.py",
        "from services.foo import routes as foo_routes\n\napp = None\napp.include_router(foo_routes.router)\n",
    )

    assert routers.scan_router_registration(tmp_path) == []


def test_unmounted_router_produces_high_confidence_error(tmp_path):
    _write(tmp_path / "services" / "foo" / "routes.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n")
    _write(tmp_path / "main.py", "app = None\n")

    findings = routers.scan_router_registration(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "router-unmounted:services.foo.routes"
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


def test_standalone_router_marker_suppresses_finding(tmp_path):
    _write(
        tmp_path / "services" / "foo" / "routes.py",
        "# quality-audit: standalone-router\nfrom fastapi import APIRouter\n\nrouter = APIRouter()\n",
    )
    _write(tmp_path / "main.py", "app = None\n")

    assert routers.scan_router_registration(tmp_path) == []


def test_router_scan_with_no_main_py_treats_every_router_as_unmounted(tmp_path):
    _write(tmp_path / "services" / "foo" / "routes.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n")

    findings = routers.scan_router_registration(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "router-unmounted:services.foo.routes"


# --- background.py: background-wiring scanner -------------------------------

_SCHEDULER_SOURCE = (
    "import task_supervisor\n\n\n"
    "def _maybe_do_work(cfg):\n"
    "    state = {}\n"
    '    state["task"] = task_supervisor.supervise(lambda: None)\n'
)


def test_scheduler_function_called_from_main_produces_no_finding(tmp_path):
    _write(tmp_path / "services" / "foo" / "scheduler.py", _SCHEDULER_SOURCE)
    _write(tmp_path / "main.py", "from services.foo.scheduler import _maybe_do_work\n\n_maybe_do_work({})\n")

    assert background.scan_background_wiring(tmp_path) == []


def test_scheduler_function_with_zero_external_references_fails_high_confidence(tmp_path):
    _write(tmp_path / "services" / "foo" / "scheduler.py", _SCHEDULER_SOURCE)
    _write(tmp_path / "main.py", "x = 1\n")

    findings = background.scan_background_wiring(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "background-unwired:services.foo.scheduler:_maybe_do_work"
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


def test_scheduler_function_called_from_same_file_it_is_defined_in_produces_no_finding(tmp_path):
    """Real-repo shape: main.py's own _maybe_check_signal_resolutions is
    defined and called both inside main.py itself - same-file wiring must
    still count, or the scanner would flag legitimate entrypoint-local
    schedulers."""
    _write(
        tmp_path / "main.py",
        _SCHEDULER_SOURCE + "\n\n_maybe_do_work({})\n",
    )

    assert background.scan_background_wiring(tmp_path) == []


def test_non_scheduler_maybe_function_is_ignored(tmp_path):
    _write(tmp_path / "services" / "foo" / "scheduler.py", "def _maybe_prune(cfg):\n    return None\n")
    _write(tmp_path / "main.py", "x = 1\n")

    assert background.scan_background_wiring(tmp_path) == []


# --- persistence.py: persistence-isolation scanner --------------------------


def test_registered_db_path_owner_produces_no_finding(tmp_path, monkeypatch):
    _write(tmp_path / "services" / "known_store.py", 'DB_PATH = "known_store.db"\n')
    monkeypatch.setattr(persistence, "PERSISTENCE_MODULE_PATHS", ("services.known_store",))

    assert persistence.scan_persistence_isolation(tmp_path) == []


def test_unregistered_db_path_owner_fails_high_confidence(tmp_path, monkeypatch):
    _write(tmp_path / "services" / "new_store.py", 'DB_PATH = "new_store.db"\n')
    monkeypatch.setattr(persistence, "PERSISTENCE_MODULE_PATHS", ())

    findings = persistence.scan_persistence_isolation(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "persistence-unisolated:services.new_store"
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


# --- resources.py: resource-lifecycle scanner --------------------------------

_KALSHI_CLIENT_STUB = "class KalshiClient:\n    async def close(self):\n        pass\n"


def test_unclosed_client_fails_high_confidence(tmp_path):
    _write(
        tmp_path / "services" / "leaky.py",
        _KALSHI_CLIENT_STUB + '\n\nasync def leak():\n    client = KalshiClient()\n    return await client.get_market("X")\n',
    )

    findings = resources.scan_resource_lifecycle(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "resource-unclosed:services.leaky:leak:client"
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


def test_client_closed_in_finally_produces_no_finding(tmp_path):
    _write(
        tmp_path / "services" / "clean.py",
        _KALSHI_CLIENT_STUB
        + '\n\nasync def fetch():\n    client = KalshiClient()\n    try:\n'
        '        return await client.get_market("X")\n'
        "    finally:\n"
        "        await client.close()\n",
    )

    assert resources.scan_resource_lifecycle(tmp_path) == []


def test_client_returned_bare_produces_no_finding(tmp_path):
    """Ownership transfer: services/whale_stream/whale_stream_handlers.py's
    real _stream_market_client returns a cached client instead of closing
    it - a deliberate, documented long-lived-instance pattern."""
    _write(
        tmp_path / "services" / "factory.py",
        _KALSHI_CLIENT_STUB + "\n\ndef make():\n    client = KalshiClient()\n    return client\n",
    )

    assert resources.scan_resource_lifecycle(tmp_path) == []


def test_client_stored_in_cache_produces_no_finding(tmp_path):
    _write(
        tmp_path / "services" / "cache.py",
        _KALSHI_CLIENT_STUB
        + "\n\n_cache = {}\n\n\ndef make(key):\n    client = KalshiClient()\n    _cache[key] = client\n    return _cache[key]\n",
    )

    assert resources.scan_resource_lifecycle(tmp_path) == []


def test_unclosed_transport_builder_call_fails_high_confidence(tmp_path):
    """A5: services/kalshi/transport.py's build_*_client factories return
    SDK clients that own an aiohttp session - a bare-name builder call
    constructed in a function body and never closed is the same leak class
    as a direct KalshiClient() construction."""
    _write(
        tmp_path / "services" / "leaky_builder.py",
        "def build_public_client(base_url):\n    pass\n"
        '\n\nasync def leak():\n    client = build_public_client("https://x")\n'
        '    return await client.get_markets()\n',
    )

    findings = resources.scan_resource_lifecycle(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "resource-unclosed:services.leaky_builder:leak:client"
    assert findings[0].severity == "error"


def test_unclosed_attribute_form_transport_builder_call_fails(tmp_path):
    """`transport.build_public_client(...)` (the attribute form real
    callers use) must be caught too - the original bare-Name-only matching
    predates the boundary's builder functions."""
    _write(
        tmp_path / "services" / "leaky_attr.py",
        "from services.kalshi import transport\n"
        '\n\nasync def leak():\n    client = transport.build_public_client("https://x")\n'
        '    return await client.get_markets()\n',
    )

    findings = resources.scan_resource_lifecycle(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "resource-unclosed:services.leaky_attr:leak:client"


def test_closed_transport_builder_call_produces_no_finding(tmp_path):
    _write(
        tmp_path / "services" / "clean_builder.py",
        "from services.kalshi import transport\n"
        '\n\nasync def fetch():\n    client = transport.build_public_client("https://x")\n'
        "    try:\n        return await client.get_markets()\n"
        "    finally:\n        await client.close()\n",
    )

    assert resources.scan_resource_lifecycle(tmp_path) == []


def test_module_level_client_construction_is_not_scanned(tmp_path):
    """services/app_state.py's real eager singletons (account =
    KalshiAccountClient(...), trade_stream = KalshiTradeWebSocketClient(...))
    are a deliberate process-lifetime pattern, not a per-call resource -
    module-scope constructions are out of scope entirely."""
    _write(tmp_path / "services" / "singleton.py", _KALSHI_CLIENT_STUB + "\n\nclient = KalshiClient()\n")

    assert resources.scan_resource_lifecycle(tmp_path) == []


# --- config_usage.py: config-usage scanner -----------------------------------


def test_read_leaf_via_subscript_chain_produces_no_finding(tmp_path):
    _write(tmp_path / "config" / "settings.yaml", "strategy:\n  entry_threshold: 0.6\n")
    _write(tmp_path / "app.py", 'value = cfg["strategy"]["entry_threshold"]\n')

    assert config_usage.scan_config_usage(tmp_path) == []


def test_read_leaf_via_get_chain_produces_no_finding(tmp_path):
    _write(tmp_path / "config" / "settings.yaml", "strategy:\n  take_profit_pct: 0.2\n")
    _write(tmp_path / "app.py", 'value = cfg.get("strategy", {}).get("take_profit_pct")\n')

    assert config_usage.scan_config_usage(tmp_path) == []


def test_unread_leaf_produces_medium_confidence_warning(tmp_path):
    _write(tmp_path / "config" / "settings.yaml", "strategy:\n  entry_threshold: 0.6\n")
    _write(tmp_path / "app.py", "x = 1\n")

    findings = config_usage.scan_config_usage(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "config-unread:strategy.entry_threshold"
    assert findings[0].severity == "warning"
    assert findings[0].confidence == "medium"


def test_unread_leaf_warning_never_fails_default_gate(tmp_path):
    _write(tmp_path / "config" / "settings.yaml", "strategy:\n  entry_threshold: 0.6\n")
    _write(tmp_path / "app.py", "x = 1\n")

    findings = config_usage.scan_config_usage(tmp_path)
    comparison = compare_to_baseline(QualityReport(findings=findings), baseline_ids=set())

    assert audit_cli.compute_exit_code(comparison) == 0


# --- api_usage.py: API-usage inventory scanner -------------------------------


def test_client_calls_produce_info_findings_grouped_by_method(tmp_path):
    _write(
        tmp_path / "app.py",
        'async def fetch():\n    await client.get_market("X")\n    await client.get_market("Y")\n    await account.get_positions()\n',
    )

    findings = api_usage.scan_api_usage(tmp_path)
    by_id = {f.finding_id: f for f in findings}

    assert by_id["api-usage:client.get_market"].evidence["call_sites"] == [
        "app.py:2",
        "app.py:3",
    ]
    assert all(f.severity == "info" for f in findings)
    assert "api-usage:account.get_positions" in by_id


def test_untracked_receiver_is_not_inventoried(tmp_path):
    _write(tmp_path / "app.py", 'unrelated_object.get_market("X")\n')

    assert api_usage.scan_api_usage(tmp_path) == []


# --- __main__.py: CLI plumbing ----------------------------------------------


def test_run_audit_against_empty_repo_returns_empty_report(tmp_path):
    """An empty fixture dir has no routes.py/main.py/scheduler functions for
    the registered scanners to find anything in - distinct from Task 3's
    now-obsolete "no scanners registered" case."""
    report = audit_cli.run_audit(tmp_path)
    assert report.findings == []


def test_main_writes_json_out_and_exits_zero_with_no_findings(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"version": 1, "accepted_finding_ids": []}))
    json_out = tmp_path / "out.json"

    exit_code = audit_cli.main(
        [
            "--repo-root", str(tmp_path),
            "--baseline", str(baseline_path),
            "--json-out", str(json_out),
        ]
    )

    assert exit_code == 0
    payload = json.loads(json_out.read_text())
    assert payload == {"findings": [], "new": [], "existing": [], "resolved": []}


def test_main_exits_nonzero_for_synthetic_new_high_confidence_error(tmp_path, monkeypatch):
    finding = _finding("new-error:cli")
    monkeypatch.setattr(audit_cli, "_SCANNERS", [lambda repo_root: [finding]])
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"version": 1, "accepted_finding_ids": []}))

    exit_code = audit_cli.main(["--repo-root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


# --- kalshi_contract_docs.py: Kalshi CONTRACT_DOCS scanner -------------------


def test_no_services_kalshi_package_produces_no_findings(tmp_path):
    """A3 ships before A4 creates services/kalshi/ - the scanner must be
    inert against every pre-A4 repo state, not error or warn on a package
    that doesn't exist yet."""
    _write(tmp_path / "services" / "kalshi_client.py", "def get_markets():\n    pass\n")

    assert kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path) == []


def test_documented_operation_produces_no_finding(tmp_path):
    _write(tmp_path / "docs" / "kalshi" / "get-markets.md", "# Get Markets\n")
    _write(
        tmp_path / "services" / "kalshi" / "public.py",
        'CONTRACT_DOCS = {\n    "get_markets": ("docs/kalshi/get-markets.md",),\n}\n'
        "\n\ndef get_markets():\n    pass\n",
    )

    assert kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path) == []


def test_operation_missing_contract_docs_entry_fails_high_confidence(tmp_path):
    _write(
        tmp_path / "services" / "kalshi" / "public.py",
        "CONTRACT_DOCS = {}\n\n\ndef get_markets():\n    pass\n",
    )

    findings = kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "kalshi-contract-docs-missing:services.kalshi.public:get_markets"
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


def test_private_operation_is_not_required_to_have_contract_docs(tmp_path):
    _write(
        tmp_path / "services" / "kalshi" / "public.py",
        "CONTRACT_DOCS = {}\n\n\ndef _internal_helper():\n    pass\n",
    )

    assert kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path) == []


def test_contract_docs_entry_pointing_at_nonexistent_file_fails_high_confidence(tmp_path):
    _write(
        tmp_path / "services" / "kalshi" / "public.py",
        'CONTRACT_DOCS = {\n    "get_markets": ("docs/kalshi/does-not-exist.md",),\n}\n'
        "\n\ndef get_markets():\n    pass\n",
    )

    findings = kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == (
        "kalshi-contract-docs-missing-file:services.kalshi.public:get_markets:docs/kalshi/does-not-exist.md"
    )
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


def test_stale_contract_docs_key_reports_medium_confidence_warning(tmp_path):
    """A key with no matching public def (e.g. a renamed/removed operation
    left behind in CONTRACT_DOCS) is reported, not gated - a static AST
    scan can't rule out every legitimate reason a key doesn't literally
    match a def name, per A3's "report according to provable context"."""
    _write(tmp_path / "docs" / "kalshi" / "get-markets.md", "# Get Markets\n")
    _write(
        tmp_path / "services" / "kalshi" / "public.py",
        'CONTRACT_DOCS = {\n    "get_market": ("docs/kalshi/get-markets.md",),\n}\n'
        "\n\ndef get_markets():\n    pass\n",
    )

    findings = kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path)

    stale = [f for f in findings if f.check == "kalshi-contract-docs" and "stale" in f.finding_id]
    assert len(stale) == 1
    assert stale[0].finding_id == "kalshi-contract-docs-stale:services.kalshi.public:get_market"
    assert stale[0].severity == "warning"
    assert stale[0].confidence == "medium"


def test_close_lifecycle_method_needs_no_contract_docs(tmp_path):
    """close() releases the SDK session - a lifecycle method, not a wire
    operation; there's no Kalshi doc page it could honestly map to (A6)."""
    _write(
        tmp_path / "services" / "kalshi" / "public.py",
        "CONTRACT_DOCS = {}\n\n\nclass Gateway:\n    async def close(self):\n        pass\n",
    )

    assert kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path) == []


def test_annotated_contract_docs_assignment_is_recognized(tmp_path):
    """`CONTRACT_DOCS: dict[str, ContractDocs] = {...}` (AnnAssign) is how
    real boundary modules declare the mapping - the scanner's original
    plain-Assign-only matching flagged transport.py's documented operations
    as undocumented, caught live at A5."""
    _write(tmp_path / "docs" / "kalshi" / "get-markets.md", "# Get Markets\n")
    _write(
        tmp_path / "services" / "kalshi" / "public.py",
        "CONTRACT_DOCS: dict = {\n"
        '    "get_markets": ("docs/kalshi/get-markets.md",),\n'
        "}\n"
        "\n\ndef get_markets():\n    pass\n",
    )

    assert kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path) == []


def test_infrastructure_marker_exempts_module_from_missing_mapping(tmp_path):
    """services/kalshi/provenance.py (A4) is metadata infrastructure, not a
    vendor adapter - its public functions have no Kalshi doc to map. The
    explicit `# quality-audit: kalshi-infrastructure` marker exempts a
    module from the missing-mapping requirement (same reviewable-marker
    pattern as routers.py's standalone-router), while file-existence checks
    on any CONTRACT_DOCS it does declare still apply."""
    _write(
        tmp_path / "services" / "kalshi" / "provenance.py",
        "# quality-audit: kalshi-infrastructure\n"
        "\n\ndef collect_operations():\n    pass\n",
    )

    assert kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path) == []


def test_infrastructure_marker_does_not_exempt_bad_doc_paths(tmp_path):
    _write(
        tmp_path / "services" / "kalshi" / "infra.py",
        "# quality-audit: kalshi-infrastructure\n"
        'CONTRACT_DOCS = {\n    "helper": ("docs/kalshi/missing.md",),\n}\n'
        "\n\ndef helper():\n    pass\n",
    )

    findings = kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path)

    assert len(findings) == 1
    assert "missing-file" in findings[0].finding_id
    assert findings[0].severity == "error"


def test_documented_method_on_class_produces_no_finding(tmp_path):
    _write(tmp_path / "docs" / "kalshi" / "get-market.md", "# Get Market\n")
    _write(
        tmp_path / "services" / "kalshi" / "account.py",
        'CONTRACT_DOCS = {\n    "get_balance": ("docs/kalshi/get-market.md",),\n}\n'
        "\n\nclass AccountGateway:\n    async def get_balance(self):\n        pass\n"
        "\n    async def _internal(self):\n        pass\n",
    )

    assert kalshi_contract_docs.scan_kalshi_contract_docs(tmp_path) == []


def test_real_repo_audit_has_no_new_high_confidence_errors():
    """Task 4 Step 6: run the real (now non-empty) scanner set against this
    repo and confirm no new high-confidence router/background-wiring
    findings slipped in unbaselined - both scanners were manually verified
    clean against current HEAD before this task was committed (all 12
    routers are mounted in main.py, all 5 real _maybe_* schedulers have a
    live external caller). Task 5 Step 8 extends this same shape to the
    persistence/resource/config/API scanners it adds."""
    exit_code = audit_cli.main(
        [
            "--repo-root", str(REPO_ROOT),
            "--baseline", str(REPO_ROOT / "tools" / "quality_audit" / "baseline.json"),
        ]
    )
    assert exit_code == 0


def test_unclosed_stream_gateway_construction_fails(tmp_path):
    """A11: the websocket transport class moved behind the boundary as
    KalshiStreamGateway - the new name must not silently escape the leak
    scanner the old KalshiTradeWebSocketClient name is tracked under."""
    _write(
        tmp_path / "services" / "leaky_stream.py",
        "from services.kalshi.websocket import KalshiStreamGateway\n"
        '\n\nasync def leak():\n    stream = KalshiStreamGateway("https://x")\n'
        "    return await stream.run(None, None)\n",
    )

    findings = resources.scan_resource_lifecycle(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "resource-unclosed:services.leaky_stream:leak:stream"


# ---- A15: Kalshi integration-boundary ratchet ------------------------------


def test_boundary_sdk_import_outside_the_package_fails(tmp_path):
    from tools.quality_audit import kalshi_boundary
    _write(tmp_path / "services" / "rogue_sdk.py", "import kalshi_python_async as kpa\n")
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    ids = [f.finding_id for f in findings]
    assert any(i.startswith("kalshi-boundary-sdk-import:services/rogue_sdk.py") for i in ids)


def test_boundary_sdk_import_inside_the_package_passes(tmp_path):
    from tools.quality_audit import kalshi_boundary
    _write(tmp_path / "services" / "kalshi" / "adapter.py", "import kalshi_python_async as kpa\n")
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    assert findings == []


def test_boundary_raw_host_string_outside_the_boundary_fails(tmp_path):
    from tools.quality_audit import kalshi_boundary
    _write(
        tmp_path / "services" / "rogue_host.py",
        'URL = "https://external-api.kalshi.com/trade-api/v2"\n',
    )
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    assert any(f.finding_id.startswith("kalshi-boundary-host:services/rogue_host.py") for f in findings)


def test_boundary_host_mention_in_a_docstring_is_not_usage(tmp_path):
    from tools.quality_audit import kalshi_boundary
    _write(
        tmp_path / "services" / "prose_only.py",
        '"""Verified against docs.kalshi.com by hand."""\n\n\ndef f():\n    """See api.elections.kalshi.com."""\n    return 1\n',
    )
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    assert findings == []


def test_boundary_any_legacy_facade_import_is_a_hard_error(tmp_path):
    """C9: the migration ratchet became a final invariant - importing a
    deleted legacy module path fails, whether or not someone also
    reintroduced the file itself."""
    from tools.quality_audit import kalshi_boundary
    _write(
        tmp_path / "services" / "new_consumer.py",
        "from services.kalshi_client import KalshiClient\n",
    )
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    banned = [f for f in findings if f.finding_id.startswith("kalshi-boundary-legacy-import:")]
    assert len(banned) == 1
    assert banned[0].severity == "error"

    # ...and with a reintroduced facade file present, still an error
    _write(tmp_path / "services" / "kalshi_client.py", "class KalshiClient:\n    pass\n")
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    assert any(f.finding_id.startswith("kalshi-boundary-legacy-import:") for f in findings)


def test_boundary_deprecated_direction_read_outside_boundary_fails(tmp_path):
    from tools.quality_audit import kalshi_boundary
    _write(
        tmp_path / "services" / "rogue_alias.py",
        'def side(t):\n    return t.get("taker_side") or t["taker_outcome_side"]\n',
    )
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    ids = [f.finding_id for f in findings]
    assert any(i.startswith("kalshi-boundary-deprecated-read:services/rogue_alias.py") for i in ids)


def test_boundary_deprecated_read_in_the_archival_allowlist_passes(tmp_path):
    from tools.quality_audit import kalshi_boundary
    _write(
        tmp_path / "services" / "series_watcher.py",
        'def row(t):\n    return (t.get("taker_outcome_side"), t.get("taker_book_side"), t.get("taker_side"))\n',
    )
    findings = kalshi_boundary.scan_kalshi_boundary(tmp_path)
    assert findings == []


def test_boundary_scanner_is_registered_and_real_tree_is_clean():
    from tools.quality_audit import kalshi_boundary
    from tools.quality_audit import __main__ as audit_main
    assert kalshi_boundary.scan_kalshi_boundary in audit_main._SCANNERS
    real_findings = kalshi_boundary.scan_kalshi_boundary(Path(__file__).resolve().parent.parent)
    assert [f for f in real_findings if f.severity == "error"] == []
