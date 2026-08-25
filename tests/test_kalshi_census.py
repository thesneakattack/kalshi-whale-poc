"""Tests for tools/kalshi_census.py (Kalshi Integration Phase A, Task A0 -
docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md). Everything
here runs against a tiny synthetic fixture tree under tmp_path, never the
real repository, so the census's detection logic can be proven
deterministic and independent of how the real codebase happens to look on
any given day (same convention as tests/test_quality_audit.py and
tests/test_project_manifest.py). test_real_repo_census_* below is the one
exception: it runs the census against this actual repo, to prove the tool
produces a usable, non-empty result on the codebase it exists to inventory.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools import kalshi_census

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- direct SDK imports ------------------------------------------------------


def test_direct_sdk_import_detected_outside_approved_file(tmp_path):
    _write(tmp_path / "services" / "rogue.py", "import kalshi_python_async as kpa\n")

    census = kalshi_census.build_census(tmp_path)

    sites = census["sdk_imports"]
    assert len(sites) == 1
    assert sites[0]["file"] == "services/rogue.py"
    assert sites[0]["line"] == 1
    assert sites[0]["inside_approved_integration_file"] is False
    assert census["counts"]["direct_access_count"] >= 1


def test_sdk_import_inside_approved_integration_file_is_flagged_approved(tmp_path):
    _write(tmp_path / "services" / "kalshi_client.py", "import kalshi_python_async as kpa\n")

    census = kalshi_census.build_census(tmp_path)

    sites = census["sdk_imports"]
    assert len(sites) == 1
    assert sites[0]["inside_approved_integration_file"] is True


def test_sdk_import_inside_the_boundary_package_is_approved_by_prefix(tmp_path):
    """services/kalshi/ (A4+) is the one place vendor access is supposed to
    live - any module under it is approved without per-file enumeration."""
    _write(tmp_path / "services" / "kalshi" / "transport.py", "import kalshi_python_async as kpa\n")

    census = kalshi_census.build_census(tmp_path)

    sites = census["sdk_imports"]
    assert len(sites) == 1
    assert sites[0]["file"] == "services/kalshi/transport.py"
    assert sites[0]["inside_approved_integration_file"] is True


def test_sdk_import_from_form_is_also_detected(tmp_path):
    _write(tmp_path / "services" / "rogue2.py", "from kalshi_python_async.exceptions import ApiException\n")

    census = kalshi_census.build_census(tmp_path)

    assert len(census["sdk_imports"]) == 1
    assert census["sdk_imports"][0]["imported"] == "kalshi_python_async.exceptions"


def test_unrelated_import_is_not_a_sdk_import(tmp_path):
    _write(tmp_path / "services" / "fine.py", "import os\nfrom foo import kalshi_python_async_lookalike\n")

    assert kalshi_census.build_census(tmp_path)["sdk_imports"] == []


# --- direct host usage --------------------------------------------------------


def test_direct_host_string_outside_approved_file_is_flagged(tmp_path):
    _write(tmp_path / "services" / "rogue.py", "URL = 'https://api.kalshi.com'\n")

    census = kalshi_census.build_census(tmp_path)

    sites = census["direct_host_usage"]
    assert len(sites) == 1
    assert sites[0]["inside_approved_integration_file"] is False


def test_direct_host_string_inside_approved_file_is_not_flagged_as_leak(tmp_path):
    _write(tmp_path / "services" / "kalshi_trade_ws.py", "_PROD_WS_URL = 'wss://external-api-ws.kalshi.com/trade-api/ws/v2'\n")

    census = kalshi_census.build_census(tmp_path)

    sites = census["direct_host_usage"]
    assert len(sites) == 1
    assert sites[0]["inside_approved_integration_file"] is True
    # approved-file host usage must not count toward the leak metric
    assert census["counts"]["direct_access_count"] == 0


def test_unrelated_string_is_not_host_usage(tmp_path):
    _write(tmp_path / "services" / "fine.py", "URL = 'https://example.com'\n")

    assert kalshi_census.build_census(tmp_path)["direct_host_usage"] == []


# --- legacy wrapper import/construction sites --------------------------------


def test_legacy_wrapper_import_and_construction_detected(tmp_path):
    _write(
        tmp_path / "services" / "consumer.py",
        "from services.kalshi_client import KalshiClient\n\n"
        "def make(cfg):\n"
        "    return KalshiClient(cfg['kalshi']['base_url'], 10)\n",
    )

    census = kalshi_census.build_census(tmp_path)

    imports = census["legacy_wrapper"]["imports"]
    constructions = census["legacy_wrapper"]["constructions"]
    assert len(imports) == 1
    assert imports[0]["module"] == "services.kalshi_client"
    assert imports[0]["symbol"] == "KalshiClient"
    assert len(constructions) == 1
    assert constructions[0]["symbol"] == "KalshiClient"
    assert census["counts"]["legacy_caller_count"] == 1


def test_legacy_wrapper_caller_count_is_distinct_files_not_call_sites(tmp_path):
    _write(
        tmp_path / "services" / "a.py",
        "from services.kalshi_client import KalshiClient\n\n"
        "def a(cfg):\n    return KalshiClient(cfg['x'], 1)\n\n"
        "def b(cfg):\n    return KalshiClient(cfg['y'], 1)\n",
    )
    _write(
        tmp_path / "services" / "b.py",
        "from services.kalshi_client import KalshiClient\n\n"
        "def c(cfg):\n    return KalshiClient(cfg['z'], 1)\n",
    )

    census = kalshi_census.build_census(tmp_path)

    assert len(census["legacy_wrapper"]["constructions"]) == 3
    assert census["counts"]["legacy_caller_count"] == 2


def test_non_legacy_class_construction_is_ignored(tmp_path):
    _write(tmp_path / "services" / "fine.py", "class Widget:\n    pass\n\nWidget()\n")

    assert kalshi_census.build_census(tmp_path)["legacy_wrapper"] == {"imports": [], "constructions": []}


# --- wrapper method call sites -------------------------------------------------


def test_wrapper_method_calls_grouped_by_receiver_and_method(tmp_path):
    _write(
        tmp_path / "services" / "consumer.py",
        "async def fetch():\n"
        "    await client.get_market('X')\n"
        "    await client.get_market('Y')\n"
        "    await account.get_positions()\n"
        "    await trade_stream.run(None, None)\n",
    )

    census = kalshi_census.build_census(tmp_path)
    calls = census["wrapper_method_calls"]

    assert calls["client.get_market"] == ["services/consumer.py:2", "services/consumer.py:3"]
    assert calls["account.get_positions"] == ["services/consumer.py:4"]
    assert calls["trade_stream.run"] == ["services/consumer.py:5"]
    assert census["counts"]["wrapper_method_call_sites"] == 4


def test_untracked_receiver_is_not_counted_as_wrapper_call(tmp_path):
    _write(tmp_path / "services" / "fine.py", "unrelated_object.get_market('X')\n")

    assert kalshi_census.build_census(tmp_path)["wrapper_method_calls"] == {}


# --- known (alias/deprecated) field reads --------------------------------------


def test_known_deprecated_field_reads_detected_via_get_and_subscript(tmp_path):
    _write(
        tmp_path / "services" / "parser.py",
        "def parse(trade, fill):\n"
        "    legacy = trade.get('taker_side')\n"
        "    mt = trade['market_ticker']\n"
        "    fid = fill.get('fill_id')\n",
    )

    census = kalshi_census.build_census(tmp_path)
    reads = census["known_field_reads"]

    assert reads["taker_side"] == ["services/parser.py:2"]
    assert reads["market_ticker"] == ["services/parser.py:3"]
    assert reads["fill_id"] == ["services/parser.py:4"]
    assert census["counts"]["known_field_read_sites"] == 3


def test_generic_field_name_is_not_in_known_field_set(tmp_path):
    """'ticker' alone is deliberately excluded - measured on the real repo
    at 166 production call sites, overwhelmingly internal/non-Kalshi usage
    (e.g. signal_log row access), so counting it here would fabricate
    semantic certainty this census cannot actually back up."""
    _write(tmp_path / "services" / "parser.py", "def parse(row):\n    return row['ticker']\n")

    assert kalshi_census.build_census(tmp_path)["known_field_reads"] == {}


# --- fixture source-doc inventory ----------------------------------------------


def test_fixture_with_existing_source_doc_is_recorded(tmp_path):
    _write(tmp_path / "docs" / "kalshi" / "public-trades.md", "# Public trades\n")
    _write(
        tmp_path / "tests" / "fixtures" / "kalshi" / "public_trade.json",
        json.dumps({"_meta": {"source_doc": "docs/kalshi/public-trades.md"}, "payload": {}}),
    )

    census = kalshi_census.build_census(tmp_path)
    fixtures = census["fixtures"]

    assert len(fixtures) == 1
    assert fixtures[0]["source_doc"] == "docs/kalshi/public-trades.md"
    assert fixtures[0]["source_doc_exists"] is True
    assert census["counts"]["fixtures_missing_source_doc"] == 0


def test_fixture_with_missing_source_doc_file_is_flagged(tmp_path):
    _write(
        tmp_path / "tests" / "fixtures" / "kalshi" / "ghost.json",
        json.dumps({"_meta": {"source_doc": "docs/kalshi/does-not-exist.md"}, "payload": {}}),
    )

    census = kalshi_census.build_census(tmp_path)

    assert census["fixtures"][0]["source_doc_exists"] is False
    assert census["counts"]["fixtures_missing_source_doc"] == 1


def test_fixture_with_no_meta_source_doc_is_flagged(tmp_path):
    _write(tmp_path / "tests" / "fixtures" / "kalshi" / "bare.json", json.dumps({"payload": {}}))

    census = kalshi_census.build_census(tmp_path)

    assert census["fixtures"][0]["source_doc"] is None
    assert census["fixtures"][0]["source_doc_exists"] is False


def test_no_fixtures_dir_produces_empty_list(tmp_path):
    assert kalshi_census.build_census(tmp_path)["fixtures"] == []


# --- hot/cold classification metadata -------------------------------------------


def test_hot_cold_classification_verifies_symbol_exists(tmp_path):
    _write(tmp_path / "services" / "hotmod.py", "async def _process_stream_trade(trade):\n    pass\n")
    table = (
        {"module": "services.hotmod", "symbol": "_process_stream_trade", "classification": "hot", "reason": "test"},
    )

    census = kalshi_census.build_census(tmp_path, hot_cold_table=table)

    assert census["hot_cold_classification"] == [
        {"module": "services.hotmod", "symbol": "_process_stream_trade", "classification": "hot",
         "reason": "test", "verified": True}
    ]


def test_hot_cold_classification_flags_stale_entry_as_unverified(tmp_path):
    _write(tmp_path / "services" / "hotmod.py", "async def _renamed(trade):\n    pass\n")
    table = (
        {"module": "services.hotmod", "symbol": "_process_stream_trade", "classification": "hot", "reason": "test"},
    )

    census = kalshi_census.build_census(tmp_path, hot_cold_table=table)

    assert census["hot_cold_classification"][0]["verified"] is False


# --- counts / determinism -------------------------------------------------------


def test_build_census_is_deterministic(tmp_path):
    _write(tmp_path / "services" / "rogue.py", "import kalshi_python_async as kpa\n")

    first = kalshi_census.build_census(tmp_path)
    second = kalshi_census.build_census(tmp_path)

    assert first == second


def test_empty_repo_produces_zeroed_counts(tmp_path):
    census = kalshi_census.build_census(tmp_path)

    assert census["counts"]["sdk_import_sites"] == 0
    assert census["counts"]["direct_access_count"] == 0
    assert census["counts"]["legacy_caller_count"] == 0
    assert census["schema_version"] == 1


# --- CLI ----------------------------------------------------------------------


def test_cli_writes_json_out(tmp_path):
    _write(tmp_path / "services" / "rogue.py", "import kalshi_python_async as kpa\n")
    out_path = tmp_path / "used-contracts.json"

    exit_code = kalshi_census.main(["--repo-root", str(tmp_path), "--json-out", str(out_path)])

    assert exit_code == 0
    written = json.loads(out_path.read_text())
    assert written["counts"]["sdk_import_sites"] == 1


# --- real repo smoke test -------------------------------------------------------


def test_real_repo_census_runs_and_produces_nonzero_legacy_caller_count():
    """The real repo, per the Phase A audit's Finding B/D/J, has KalshiClient
    imported across services/whale_stream/ and services/market_watch/ - this
    proves the census actually finds real, known-existing coupling rather
    than only passing against synthetic fixtures."""
    census = kalshi_census.build_census(REPO_ROOT)

    assert census["counts"]["legacy_caller_count"] > 0
    assert census["counts"]["wrapper_method_call_sites"] > 0
    assert all(entry["verified"] for entry in census["hot_cold_classification"])


def test_real_repo_fixtures_all_have_existing_source_docs():
    census = kalshi_census.build_census(REPO_ROOT)

    assert census["counts"]["fixtures_total"] > 0
    assert census["counts"]["fixtures_missing_source_doc"] == 0
