"""
tools/kalshi_docs_drift.py (QCP Task 12) - upgrades docs-drift-check.yml
from "does each URL still 200" to real content-drift detection against
docs/kalshi/. All HTTP-free: compare_remote takes an injectable fetch
callable so these tests never touch the network, and build_manifest tests
use small synthetic docs_root fixtures under tmp_path rather than the real
213-page docs/kalshi/ mirror.
"""
import json

import pytest

from tools import kalshi_docs_drift as drift


def _write_readme(docs_root, body: str) -> None:
    (docs_root / "README.md").write_text(body, encoding="utf-8")


# --- _parse_readme_provenance / build_manifest -----------------------------


def test_build_manifest_reads_single_source_entries(tmp_path):
    _write_readme(tmp_path, """# Kalshi Docs Snapshot

## Source Pages

- `get-market.md`
  Source: `https://docs.kalshi.com/api-reference/market/get-market.md`

## Fetched Response Snapshots

- `manifest.json`
""")
    (tmp_path / "get-market.md").write_text("# Get Market\n\nbody text\n", encoding="utf-8")

    manifest = drift.build_manifest(tmp_path)

    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["local_path"] == "docs/kalshi/get-market.md"
    assert entry["source_urls"] == ["https://docs.kalshi.com/api-reference/market/get-market.md"]
    assert entry["sha256"] == drift._normalized_sha256("# Get Market\n\nbody text\n")


def test_build_manifest_skips_content_hash_for_multi_source_entries(tmp_path):
    _write_readme(tmp_path, """# Kalshi Docs Snapshot

## Source Pages

- `get-live-data.md` (2026-08-15)
  Sources: `https://docs.kalshi.com/api-reference/live-data/get-live-data-with-type.md`,
  `https://docs.kalshi.com/api-reference/live-data/get-multiple-live-data.md` -
  single + batch milestone-keyed live data.

## Fetched Response Snapshots
""")
    (tmp_path / "get-live-data.md").write_text("merged content\n", encoding="utf-8")

    manifest = drift.build_manifest(tmp_path)

    entry = manifest["entries"][0]
    assert entry["source_urls"] == [
        "https://docs.kalshi.com/api-reference/live-data/get-live-data-with-type.md",
        "https://docs.kalshi.com/api-reference/live-data/get-multiple-live-data.md",
    ]
    assert entry["sha256"] is None  # not a 1:1 mirror - content comparison would always false-positive


def test_build_manifest_skips_documented_entries_with_no_actual_local_file(tmp_path):
    _write_readme(tmp_path, """# Kalshi Docs Snapshot

## Source Pages

- `get-market.md`
  Source: `https://docs.kalshi.com/api-reference/market/get-market.md`

## Fetched Response Snapshots

- `manifest.json`
  (a live response snapshot, not a doc page)
""")
    (tmp_path / "get-market.md").write_text("content\n", encoding="utf-8")
    # deliberately no manifest.json on disk, and it's outside "## Source Pages" anyway

    manifest = drift.build_manifest(tmp_path)

    assert [e["local_path"] for e in manifest["entries"]] == ["docs/kalshi/get-market.md"]


def test_build_manifest_skips_content_hash_for_curated_summary_pages(tmp_path):
    """A hand-written LLM distillation (first line 'Source: <url>') is not
    a verbatim mirror - hashing it against a fresh raw fetch would drift
    permanently even with zero real upstream change. Live-verified while
    building this tool: see the module docstring."""
    _write_readme(tmp_path, """# Kalshi Docs Snapshot

## Source Pages

- `get-market.md`
  Source: `https://docs.kalshi.com/api-reference/market/get-market.md`

## Fetched Response Snapshots
""")
    (tmp_path / "get-market.md").write_text(
        "Source: https://docs.kalshi.com/api-reference/market/get-market.md\n\n"
        "# Get Market\n\nhand-written condensed summary, not a raw mirror\n",
        encoding="utf-8",
    )

    manifest = drift.build_manifest(tmp_path)

    assert manifest["entries"][0]["sha256"] is None


def test_build_manifest_keeps_content_hash_for_a_verbatim_mirror(tmp_path):
    _write_readme(tmp_path, """# Kalshi Docs Snapshot

## Source Pages

- `get-market.md`
  Source: `https://docs.kalshi.com/api-reference/market/get-market.md`

## Fetched Response Snapshots
""")
    (tmp_path / "get-market.md").write_text(
        "> ## Documentation Index\n> Fetch the complete index at: https://docs.kalshi.com/llms.txt\n\n"
        "# Get Market\n\nverbatim raw fetch content\n",
        encoding="utf-8",
    )

    manifest = drift.build_manifest(tmp_path)

    assert manifest["entries"][0]["sha256"] is not None


def test_build_manifest_is_sorted_by_local_path(tmp_path):
    _write_readme(tmp_path, """# Kalshi Docs Snapshot

## Source Pages

- `zzz.md`
  Source: `https://docs.kalshi.com/z.md`
- `aaa.md`
  Source: `https://docs.kalshi.com/a.md`

## Fetched Response Snapshots
""")
    (tmp_path / "zzz.md").write_text("z\n", encoding="utf-8")
    (tmp_path / "aaa.md").write_text("a\n", encoding="utf-8")

    manifest = drift.build_manifest(tmp_path)

    assert [e["local_path"] for e in manifest["entries"]] == ["docs/kalshi/aaa.md", "docs/kalshi/zzz.md"]


# --- normalization -----------------------------------------------------


def test_normalize_converts_crlf_and_bare_cr_to_lf():
    assert drift._normalize("a\r\nb\rc\n") == "a\nb\nc\n"


def test_normalized_sha256_is_identical_for_crlf_vs_lf_only_difference():
    lf = "line one\nline two\nline three\n"
    crlf = "line one\r\nline two\r\nline three\r\n"

    assert drift._normalized_sha256(lf) == drift._normalized_sha256(crlf)


# --- compare_remote (Step 2's four required cases) --------------------------


def _manifest_for(local_path: str, url: str, content: str) -> dict:
    return {
        "version": 1,
        "entries": [{"local_path": local_path, "source_urls": [url], "sha256": drift._normalized_sha256(content)}],
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


def test_compare_remote_skips_content_comparison_for_multi_source_entries():
    manifest = {
        "version": 1,
        "entries": [{
            "local_path": "docs/kalshi/get-live-data.md",
            "source_urls": ["https://docs.kalshi.com/a.md", "https://docs.kalshi.com/b.md"],
            "sha256": None,
        }],
    }

    def fetch(url):
        return 200, "whatever content, unrelated to the merged local file"

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report == {"ok": True, "changed": [], "unavailable": []}  # availability-only, both 200


def test_compare_remote_still_flags_unavailability_for_multi_source_entries():
    manifest = {
        "version": 1,
        "entries": [{
            "local_path": "docs/kalshi/get-live-data.md",
            "source_urls": ["https://docs.kalshi.com/a.md", "https://docs.kalshi.com/b.md"],
            "sha256": None,
        }],
    }

    def fetch(url):
        return (200, "ok") if url.endswith("a.md") else (404, "")

    report = drift.compare_remote(manifest, fetch=fetch)

    assert report["ok"] is False
    assert report["unavailable"] == [{"local_path": "docs/kalshi/get-live-data.md", "url": "https://docs.kalshi.com/b.md", "status": 404}]


# --- CLI wiring ----------------------------------------------------------


def test_cli_write_manifest_writes_valid_json(tmp_path):
    docs_root = tmp_path / "docs" / "kalshi"
    docs_root.mkdir(parents=True)
    _write_readme(docs_root, """# Kalshi Docs Snapshot

## Source Pages

- `get-market.md`
  Source: `https://docs.kalshi.com/api-reference/market/get-market.md`

## Fetched Response Snapshots
""")
    (docs_root / "get-market.md").write_text("content\n", encoding="utf-8")
    out_path = tmp_path / "upstream-manifest.json"

    exit_code = drift.main(["--docs-root", str(docs_root), "--write-manifest", str(out_path)])

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["entries"][0]["local_path"] == "docs/kalshi/get-market.md"


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


@pytest.mark.parametrize("argv", [[], ["--manifest", "x.json"]])
def test_cli_with_neither_check_nor_write_manifest_exits_nonzero_without_network(argv):
    exit_code = drift.main(argv)

    assert exit_code == 2
