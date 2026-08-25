"""
tools/kalshi_docs_sync.py (Kalshi Integration Phase A Task A2 -
docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md) - builds/
refreshes docs/kalshi/upstream-manifest.json from docs/kalshi/llms.txt's
real index plus current on-disk mirror content, and generates
docs/kalshi/README.md from the manifest - replacing the old direction
(README parsed as machine input, see tools/kalshi_docs_drift.py's git
history). All HTTP-free: every test either works on parsed-text/local-file
fixtures directly or injects a fetch callable, same convention as
tests/test_kalshi_docs_drift.py.
"""
import json

import pytest

from tools import kalshi_docs_sync as sync

_SAMPLE_INDEX = """Source: https://docs.kalshi.com/llms.txt

# API Documentation

## Docs

- [Get Market](https://docs.kalshi.com/api-reference/market/get-market.md): Endpoint for getting a market.
- [Introduction](https://docs.kalshi.com/welcome/index.md): Welcome page.
- [Changelog](https://docs.kalshi.com/changelog/index.md): Changelog page.

## OpenAPI Specs

- [openapi](https://docs.kalshi.com/openapi.yaml)

## AsyncAPI Specs

- [asyncapi](https://docs.kalshi.com/asyncapi.yaml)

## Notes

Some trailing prose that is not a bullet.
"""


# --- parse_index ---------------------------------------------------------


def test_parse_index_reads_docs_section_bullets():
    entries = sync.parse_index(_SAMPLE_INDEX)
    docs = [e for e in entries if e["section"] == "Docs"]

    assert len(docs) == 3
    assert docs[0] == {
        "title": "Get Market",
        "url": "https://docs.kalshi.com/api-reference/market/get-market.md",
        "description": "Endpoint for getting a market.",
        "section": "Docs",
    }


def test_parse_index_reads_openapi_and_asyncapi_sections():
    entries = sync.parse_index(_SAMPLE_INDEX)
    by_section = {e["section"] for e in entries}

    assert "OpenAPI Specs" in by_section
    assert "AsyncAPI Specs" in by_section
    openapi = [e for e in entries if e["section"] == "OpenAPI Specs"]
    assert openapi[0]["url"] == "https://docs.kalshi.com/openapi.yaml"


def test_parse_index_ignores_non_bullet_prose():
    entries = sync.parse_index(_SAMPLE_INDEX)

    assert len(entries) == 5  # 3 Docs + 1 OpenAPI + 1 AsyncAPI, not the trailing Notes prose


def test_parse_index_total_matches_real_llms_txt_bullet_count():
    """Sanity check against the real committed docs/kalshi/llms.txt - not a
    network test, just confirms the parser's bullet count matches a plain
    grep count (215 markdown pages + 5 spec files, per that file's own
    '## Notes' section)."""
    from pathlib import Path
    real_text = (Path(__file__).resolve().parent.parent / "docs" / "kalshi" / "llms.txt").read_text()

    entries = sync.parse_index(real_text)

    assert len(entries) == 231  # +11 2026-08-25: upstream added weather-index, target-balance-allocation x2, margin exit-triggers x8


# --- resource_kind ---------------------------------------------------------


def test_resource_kind_markdown():
    assert sync.resource_kind("https://docs.kalshi.com/api-reference/market/get-market.md", "Docs") == "markdown"


def test_resource_kind_openapi():
    assert sync.resource_kind("https://docs.kalshi.com/openapi.yaml", "OpenAPI Specs") == "openapi"


def test_resource_kind_asyncapi():
    assert sync.resource_kind("https://docs.kalshi.com/asyncapi.yaml", "AsyncAPI Specs") == "asyncapi"


# --- local_name_for ---------------------------------------------------------


def test_local_name_for_no_collision_uses_plain_basename():
    assert sync.local_name_for("https://docs.kalshi.com/api-reference/market/get-market.md", taken=set()) == "get-market.md"


def test_local_name_for_prefixes_the_second_url_on_a_basename_collision():
    """Sequential, single-pass naming (matches llms.txt's own real listed
    order: welcome/index.md is listed long before changelog/index.md) -
    the first URL to claim a basename keeps it plain; a later URL wanting
    the same basename gets deterministically prefixed with its own first
    path segment. Reproduces the real historical
    welcome-index.md/changelog-index.md resolution."""
    taken = {"index.md"}  # already claimed by welcome/index.md, processed earlier

    assert sync.local_name_for("https://docs.kalshi.com/changelog/index.md", taken) == "changelog-index.md"


def test_local_name_for_reproduces_real_margin_rest_collision():
    taken = {"get-market.md"}

    assert sync.local_name_for(
        "https://docs.kalshi.com/margin-rest/market/get-market.md", taken,
    ) == "margin-rest-get-market.md"


def test_local_name_for_reproduces_real_margin_ws_collision():
    taken = {"market-ticker.md"}

    assert sync.local_name_for(
        "https://docs.kalshi.com/margin-ws/websockets/market-ticker.md", taken,
    ) == "margin-ws-market-ticker.md"


def test_local_name_for_reproduces_real_fix_margin_collision():
    taken = {"authentication.md"}

    assert sync.local_name_for(
        "https://docs.kalshi.com/fix-margin/authentication.md", taken,
    ) == "fix-margin-authentication.md"


def test_local_name_for_falls_back_to_two_segments_on_repeat_collision():
    """Synthetic 3-way collision: two different URLs would both want
    "margin-rest-get-market.md" - the second one falls back to including
    another path segment rather than silently overwriting the first."""
    taken = {"get-market.md", "margin-rest-get-market.md"}

    result = sync.local_name_for("https://docs.kalshi.com/margin-rest/legacy/get-market.md", taken)

    assert result == "margin-rest-legacy-get-market.md"
    assert result not in taken


# --- sync_manifest -----------------------------------------------------------


def _existing_manifest():
    return {
        "version": 2,
        "resources": [{
            "local_path": "docs/kalshi/get-market.md",
            "source_urls": ["https://docs.kalshi.com/api-reference/market/get-market.md"],
            "sha256": "stale-hash-to-be-refreshed",
        }],
        "unsupported": [],
    }


def test_sync_manifest_refreshes_hash_for_existing_resource_from_disk(tmp_path):
    docs_root = tmp_path
    (docs_root / "get-market.md").write_text(
        "> ## Documentation Index\n\n# Get Market\n\nreal verbatim body\n", encoding="utf-8",
    )
    index = [{"title": "Get Market", "url": "https://docs.kalshi.com/api-reference/market/get-market.md",
              "description": "d", "section": "Docs"}]

    manifest = sync.sync_manifest(index, docs_root, _existing_manifest())

    entry = manifest["resources"][0]
    assert entry["local_path"] == "docs/kalshi/get-market.md"
    assert entry["sha256"] is not None
    assert entry["sha256"] != "stale-hash-to-be-refreshed"


def test_sync_manifest_keeps_null_hash_for_curated_summary_on_disk(tmp_path):
    docs_root = tmp_path
    (docs_root / "get-market.md").write_text(
        "Source: https://docs.kalshi.com/api-reference/market/get-market.md\n\nsummary\n", encoding="utf-8",
    )
    index = [{"title": "Get Market", "url": "https://docs.kalshi.com/api-reference/market/get-market.md",
              "description": "d", "section": "Docs"}]

    manifest = sync.sync_manifest(index, docs_root, _existing_manifest())

    assert manifest["resources"][0]["sha256"] is None


def test_sync_manifest_never_invents_a_file_for_an_unmirrored_new_url(tmp_path):
    """A brand-new upstream page that isn't locally mirrored yet must not
    appear in resources at all - --write never fetches page bodies or
    creates files on its own; that stays a deliberate, separate human
    action (mirroring an actually-wanted new page)."""
    docs_root = tmp_path
    index = [{"title": "Get Weather Index", "url": "https://docs.kalshi.com/api-reference/live-data/get-weather-index.md",
              "description": "d", "section": "Docs"}]

    manifest = sync.sync_manifest(index, docs_root, {"version": 2, "resources": [], "unsupported": []})

    assert manifest["resources"] == []


def test_sync_manifest_preserves_existing_resource_missing_from_fresh_index(tmp_path):
    """A page temporarily absent from a fresh index fetch (removed, or the
    index fetch itself is stale/partial) must not silently drop the
    existing manifest entry - that's index-removal *drift*, surfaced by
    --check, not something --write auto-deletes."""
    docs_root = tmp_path
    (docs_root / "get-market.md").write_text("> ## Documentation Index\n\nbody\n", encoding="utf-8")

    manifest = sync.sync_manifest([], docs_root, _existing_manifest())

    assert len(manifest["resources"]) == 1
    assert manifest["resources"][0]["local_path"] == "docs/kalshi/get-market.md"


def test_sync_manifest_classifies_known_openapi_asyncapi_urls_as_unsupported(tmp_path):
    index = [
        {"title": "openapi", "url": "https://docs.kalshi.com/openapi.yaml", "description": "", "section": "OpenAPI Specs"},
        {"title": "asyncapi", "url": "https://docs.kalshi.com/asyncapi.yaml", "description": "", "section": "AsyncAPI Specs"},
    ]

    manifest = sync.sync_manifest(index, tmp_path, {"version": 2, "resources": [], "unsupported": []})

    urls = {e["source_urls"][0] for e in manifest["unsupported"]}
    assert urls == {"https://docs.kalshi.com/openapi.yaml", "https://docs.kalshi.com/asyncapi.yaml"}
    for entry in manifest["unsupported"]:
        assert entry["reason"]  # every unsupported entry must carry a real, non-empty reason


def test_sync_manifest_classifies_unknown_new_yaml_spec_as_unsupported_with_a_review_flag(tmp_path):
    index = [{"title": "new_spec", "url": "https://docs.kalshi.com/new_spec.yaml", "description": "", "section": "OpenAPI Specs"}]

    manifest = sync.sync_manifest(index, tmp_path, {"version": 2, "resources": [], "unsupported": []})

    assert len(manifest["unsupported"]) == 1
    assert "not yet reviewed" in manifest["unsupported"][0]["reason"].lower()


def test_sync_manifest_is_sorted_and_deterministic(tmp_path):
    (tmp_path / "zzz.md").write_text("> ## Documentation Index\n\nz\n", encoding="utf-8")
    (tmp_path / "aaa.md").write_text("> ## Documentation Index\n\na\n", encoding="utf-8")
    existing = {
        "version": 2,
        "resources": [
            {"local_path": "docs/kalshi/zzz.md", "source_urls": ["https://docs.kalshi.com/z.md"], "sha256": "x"},
            {"local_path": "docs/kalshi/aaa.md", "source_urls": ["https://docs.kalshi.com/a.md"], "sha256": "y"},
        ],
        "unsupported": [],
    }

    manifest = sync.sync_manifest([], tmp_path, existing)

    assert [e["local_path"] for e in manifest["resources"]] == ["docs/kalshi/aaa.md", "docs/kalshi/zzz.md"]


# --- render_readme -----------------------------------------------------------


def test_render_readme_lists_every_resource_and_unsupported_entry():
    manifest = {
        "version": 2,
        "resources": [{
            "local_path": "docs/kalshi/get-market.md",
            "source_urls": ["https://docs.kalshi.com/api-reference/market/get-market.md"],
            "sha256": "abc",
        }],
        "unsupported": [{
            "local_path": None,
            "source_urls": ["https://docs.kalshi.com/openapi.yaml"],
            "kind": "openapi",
            "reason": "not consumed by any production code path",
        }],
    }

    readme = sync.render_readme(manifest)

    assert "get-market.md" in readme
    assert "https://docs.kalshi.com/api-reference/market/get-market.md" in readme
    assert "openapi.yaml" in readme
    assert "not consumed by any production code path" in readme


# --- CLI ---------------------------------------------------------------------


def test_cli_check_reports_added_and_removed(tmp_path, monkeypatch):
    manifest_path = tmp_path / "upstream-manifest.json"
    manifest_path.write_text(json.dumps({
        "version": 2,
        "resources": [{
            "local_path": "docs/kalshi/gone.md",
            "source_urls": ["https://docs.kalshi.com/gone.md"],
            "sha256": "x",
        }],
        "unsupported": [],
    }), encoding="utf-8")

    monkeypatch.setattr(sync, "_fetch_index", lambda: (
        "## Docs\n\n- [New Page](https://docs.kalshi.com/new.md): d\n"
    ))

    exit_code = sync.main(["--check", "--manifest", str(manifest_path)])

    assert exit_code == 1  # drift found: one added, one removed


def test_check_drift_never_flags_the_index_url_itself_as_removed():
    """Real bug, found live 2026-08-24 running --check against the real
    index: llms.txt is a manifest resource (mapped to itself, sha256=None)
    but is never listed as a bullet *inside* its own body, so a naive
    URL-set diff always reported it as spuriously "removed" every single
    run. The index URL must be excluded from both sides of the diff."""
    manifest = {
        "version": 2,
        "resources": [{
            "local_path": "docs/kalshi/llms.txt",
            "source_urls": [sync._INDEX_URL],
            "sha256": None,
        }],
        "unsupported": [],
    }

    report = sync.check_drift([], manifest)

    assert report == {"ok": True, "added": [], "removed": []}


def test_cli_check_exits_zero_when_index_matches_manifest(tmp_path, monkeypatch):
    manifest_path = tmp_path / "upstream-manifest.json"
    manifest_path.write_text(json.dumps({
        "version": 2,
        "resources": [{
            "local_path": "docs/kalshi/x.md",
            "source_urls": ["https://docs.kalshi.com/x.md"],
            "sha256": "abc",
        }],
        "unsupported": [],
    }), encoding="utf-8")

    monkeypatch.setattr(sync, "_fetch_index", lambda: "## Docs\n\n- [X](https://docs.kalshi.com/x.md): d\n")

    exit_code = sync.main(["--check", "--manifest", str(manifest_path)])

    assert exit_code == 0


def test_cli_check_never_writes_to_disk(tmp_path, monkeypatch):
    manifest_path = tmp_path / "upstream-manifest.json"
    original = json.dumps({"version": 2, "resources": [], "unsupported": []})
    manifest_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(sync, "_fetch_index", lambda: "## Docs\n\n- [New](https://docs.kalshi.com/new.md): d\n")

    sync.main(["--check", "--manifest", str(manifest_path)])

    assert manifest_path.read_text(encoding="utf-8") == original


def test_cli_write_rebuilds_manifest_and_readme(tmp_path, monkeypatch):
    docs_root = tmp_path / "docs" / "kalshi"
    docs_root.mkdir(parents=True)
    (docs_root / "get-market.md").write_text("> ## Documentation Index\n\nbody\n", encoding="utf-8")
    manifest_path = docs_root / "upstream-manifest.json"
    manifest_path.write_text(json.dumps({
        "version": 2,
        "resources": [{
            "local_path": "docs/kalshi/get-market.md",
            "source_urls": ["https://docs.kalshi.com/api-reference/market/get-market.md"],
            "sha256": "stale",
        }],
        "unsupported": [],
    }), encoding="utf-8")
    readme_path = docs_root / "README.md"

    monkeypatch.setattr(sync, "_fetch_index", lambda: (
        "## Docs\n\n- [Get Market](https://docs.kalshi.com/api-reference/market/get-market.md): d\n"
    ))

    exit_code = sync.main([
        "--write", "--docs-root", str(docs_root),
        "--manifest", str(manifest_path), "--readme", str(readme_path),
    ])

    assert exit_code == 0
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written["resources"][0]["sha256"] != "stale"
    assert "get-market.md" in readme_path.read_text(encoding="utf-8")


def test_cli_write_never_touches_network_for_body_fetches(tmp_path, monkeypatch):
    """--write only reads current on-disk files and the index listing - it
    must never fetch an individual page's body itself (that stays a
    separate, deliberate mirroring action)."""
    docs_root = tmp_path / "docs" / "kalshi"
    docs_root.mkdir(parents=True)
    manifest_path = docs_root / "upstream-manifest.json"
    manifest_path.write_text(json.dumps({"version": 2, "resources": [], "unsupported": []}), encoding="utf-8")
    readme_path = docs_root / "README.md"
    monkeypatch.setattr(sync, "_fetch_index", lambda: "## Docs\n\n- [X](https://docs.kalshi.com/x.md): d\n")

    def _boom(*a, **k):
        raise AssertionError("--write must never fetch a page body")

    import httpx
    monkeypatch.setattr(httpx, "get", _boom)

    exit_code = sync.main([
        "--write", "--docs-root", str(docs_root),
        "--manifest", str(manifest_path), "--readme", str(readme_path),
    ])

    assert exit_code == 0
