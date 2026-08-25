"""
tools/kalshi_docs_drift.py (QCP Task 12, restructured Kalshi Integration
Phase A Task A2) - upgrades docs-drift-check.yml from "does each URL still
200" to real content-drift detection against docs/kalshi/. All HTTP-free:
compare_remote takes an injectable fetch callable so these tests never
touch the network. As of A2, this module only *consumes* a committed
manifest (`load_manifest`) - manifest construction from docs/kalshi/
llms.txt's real index moved to tools/kalshi_docs_sync.py (see
tests/test_kalshi_docs_sync.py), which is also what generates
docs/kalshi/README.md now, instead of README being a parsed input.
"""
import json

import pytest

from tools import kalshi_docs_drift as drift


# --- load_manifest -----------------------------------------------------


def test_load_manifest_reads_committed_json(tmp_path):
    manifest_path = tmp_path / "upstream-manifest.json"
    manifest_path.write_text(json.dumps({"version": 2, "resources": [], "unsupported": []}), encoding="utf-8")

    assert drift.load_manifest(manifest_path) == {"version": 2, "resources": [], "unsupported": []}


# --- normalization -----------------------------------------------------


def test_normalize_converts_crlf_and_bare_cr_to_lf():
    assert drift._normalize("a\r\nb\rc\n") == "a\nb\nc\n"


def test_normalized_sha256_is_identical_for_crlf_vs_lf_only_difference():
    lf = "line one\nline two\nline three\n"
    crlf = "line one\r\nline two\r\nline three\r\n"

    assert drift._normalized_sha256(lf) == drift._normalized_sha256(crlf)


def test_looks_like_curated_summary_detects_source_prefix():
    assert drift._looks_like_curated_summary("Source: https://docs.kalshi.com/x.md\n\nbody\n") is True
    assert drift._looks_like_curated_summary("> ## Documentation Index\n\nbody\n") is False


# --- compare_remote (Step 2's four required cases, now against `resources`) -


def _manifest_for(local_path: str, url: str, content: str) -> dict:
    return {
        "version": 2,
        "resources": [{"local_path": local_path, "source_urls": [url], "sha256": drift._normalized_sha256(content)}],
        "unsupported": [],
    }


def test_compare_remote_no_drift_when_fetched_content_matches():
    content = "# Page\n\nunchanged body\n"
    manifest = _manifest_for("docs/kalshi/x.md", "https://docs.kalshi.com/x.md", content)

    def fetch(url):
        return 200, content

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report == {"ok": True, "changed": [], "unavailable": []}


def test_compare_remote_detects_content_drift_on_http_200():
    manifest = _manifest_for("docs/kalshi/x.md", "https://docs.kalshi.com/x.md", "original body\n")

    def fetch(url):
        return 200, "a completely different body now\n"

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report["ok"] is False
    assert report["unavailable"] == []
    assert len(report["changed"]) == 1
    assert report["changed"][0]["local_path"] == "docs/kalshi/x.md"
    assert report["changed"][0]["url"] == "https://docs.kalshi.com/x.md"


def test_compare_remote_reports_availability_failure_on_non_200():
    manifest = _manifest_for("docs/kalshi/x.md", "https://docs.kalshi.com/x.md", "body\n")

    def fetch(url):
        return 404, ""

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report["ok"] is False
    assert report["changed"] == []
    assert report["unavailable"] == [{"local_path": "docs/kalshi/x.md", "url": "https://docs.kalshi.com/x.md", "status": 404}]


def test_compare_remote_no_drift_when_only_difference_is_line_endings():
    manifest = _manifest_for("docs/kalshi/x.md", "https://docs.kalshi.com/x.md", "line one\nline two\n")

    def fetch(url):
        return 200, "line one\r\nline two\r\n"

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report == {"ok": True, "changed": [], "unavailable": []}


def test_compare_remote_skips_content_comparison_for_null_sha256_entries():
    """A curated-summary or index-file entry (sha256=None) is
    availability-only by design - see this module's docstring."""
    manifest = {
        "version": 2,
        "resources": [{
            "local_path": "docs/kalshi/get-game-stats.md",
            "source_urls": ["https://docs.kalshi.com/api-reference/live-data/get-game-stats.md"],
            "sha256": None,
        }],
        "unsupported": [],
    }

    def fetch(url):
        return 200, "whatever content, unrelated to the curated local file"

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report == {"ok": True, "changed": [], "unavailable": []}  # availability-only, 200


def test_compare_remote_ignores_unsupported_entries_entirely():
    """manifest["unsupported"] (OpenAPI/AsyncAPI specs, see
    tools/kalshi_docs_sync.py) have no local mirror file - compare_remote
    must not try to drift-check them."""
    manifest = {
        "version": 2,
        "resources": [],
        "unsupported": [{
            "local_path": None,
            "source_urls": ["https://docs.kalshi.com/openapi.yaml"],
            "kind": "openapi",
            "reason": "not consumed by any production code path",
        }],
    }

    def fetch(url):
        raise AssertionError("compare_remote must never fetch an unsupported entry's URL")

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report == {"ok": True, "changed": [], "unavailable": []}


def test_compare_remote_still_flags_unavailability_for_a_resource():
    manifest = _manifest_for("docs/kalshi/x.md", "https://docs.kalshi.com/x.md", "body\n")

    def fetch(url):
        return 404, ""

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report["ok"] is False
    assert report["unavailable"] == [{"local_path": "docs/kalshi/x.md", "url": "https://docs.kalshi.com/x.md", "status": 404}]


# --- CLI wiring ----------------------------------------------------------


def test_cli_check_exits_nonzero_and_writes_json_report_on_drift(tmp_path, monkeypatch):
    manifest_path = tmp_path / "upstream-manifest.json"
    manifest_path.write_text(json.dumps(_manifest_for(
        "docs/kalshi/x.md", "https://docs.kalshi.com/x.md", "original\n",
    )), encoding="utf-8")
    json_out = tmp_path / "report.json"

    monkeypatch.setattr(drift, "_http_fetch", lambda url, timeout=15.0: (200, "changed\n"))

    exit_code = drift.main(["--check", "--manifest", str(manifest_path), "--json-out", str(json_out)])

    assert exit_code == 1
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert len(report["changed"]) == 1


def test_cli_check_exits_zero_when_everything_matches(tmp_path, monkeypatch):
    manifest_path = tmp_path / "upstream-manifest.json"
    manifest_path.write_text(json.dumps(_manifest_for(
        "docs/kalshi/x.md", "https://docs.kalshi.com/x.md", "same\n",
    )), encoding="utf-8")

    monkeypatch.setattr(drift, "_http_fetch", lambda url, timeout=15.0: (200, "same\n"))

    exit_code = drift.main(["--check", "--manifest", str(manifest_path)])

    assert exit_code == 0


def test_cli_without_check_exits_nonzero_without_network():
    exit_code = drift.main([])

    assert exit_code == 2


def test_real_manifest_loads_and_has_the_new_schema_shape():
    """Sanity check against the real committed manifest - not a network
    test (no fetch), just confirms the on-disk file matches the schema
    this module now expects (resources/unsupported, not entries)."""
    manifest = drift.load_manifest(drift._DEFAULT_MANIFEST_PATH)

    assert manifest["version"] == 2
    assert isinstance(manifest["resources"], list)
    assert len(manifest["resources"]) > 0
    assert isinstance(manifest["unsupported"], list)
    assert len(manifest["unsupported"]) == 5  # the 3 OpenAPI + 2 AsyncAPI specs llms.txt lists
