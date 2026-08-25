"""
Builds/refreshes docs/kalshi/upstream-manifest.json from docs/kalshi/
llms.txt's real index plus current on-disk mirror content, and generates
docs/kalshi/README.md from the manifest - Kalshi Integration Phase A Task
A2 (docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md).

Reverses the direction tools/kalshi_docs_drift.py used to own: README.md
used to be parsed as machine input for the manifest (a real, live-found
parser bug - a bullet's URL scan bleeding into unrelated prose past it -
lived in that code; see git history and
tests/test_kalshi_docs_drift.py's `test_load_manifest_reads_committed_json`
docstring). The manifest is now the provenance source of truth
(design spec: "the mirror's machine manifest, not README prose, should
become the provenance source of truth"); README.md is generated output for
human navigation, never parsed back in.

Two responsibilities, deliberately kept separate from
tools/kalshi_docs_drift.py: this module owns *structure* (which URLs map
to which local files, which are deliberately unmirrored/unsupported);
drift.py owns *content* (does a mirrored file's hash still match a fresh
fetch of its source URL). Both are network-dependent and both stay
scheduled/manual (see .github/workflows/docs-drift-check.yml) rather than
running on every push/PR.

--check (network: fetches llms.txt only, never a page body) compares the
live index's URL set against what's currently recorded in the manifest
(resources + unsupported) and reports additions/removals. Exits nonzero on
any drift, same convention as kalshi_docs_drift.py's --check. Never writes.

--write (network: fetches llms.txt only) rebuilds the manifest from the
live index plus CURRENT on-disk file content - it never fetches an
individual page's body and never creates a new local file on its own. A
genuinely new upstream page is surfaced by --check as "added," not
auto-mirrored by --write; deliberately mirroring a new page stays a
separate, deliberate human action (fetch it, commit it, then --write picks
it up because a local file now exists for that URL).

Local-name collision resolution (naming-collision requirement from the
design spec: "naming collisions use deterministic, documented local
names") is sequential and single-pass, processing index entries in their
own listed order and growing a `taken` set of already-assigned basenames
as it goes - the same order that produced the real historical
welcome-index.md/changelog-index.md and margin-rest-/margin-ws-/
fix-margin- prefixed names (the standard API's entries are listed before
the parallel margin surface in the real llms.txt). Existing resources
already in the manifest keep their current local_path unconditionally -
this function only ever assigns names to genuinely new URLs; it never
renames or re-derives anything already committed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

from tools.kalshi_docs_drift import _looks_like_curated_summary, _normalized_sha256

_DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs" / "kalshi"
_DEFAULT_MANIFEST_PATH = _DOCS_ROOT / "upstream-manifest.json"
_DEFAULT_README_PATH = _DOCS_ROOT / "README.md"
_INDEX_URL = "https://docs.kalshi.com/llms.txt"

_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_BULLET_RE = re.compile(r"^- \[([^\]]*)\]\(([^)]+)\)(?::\s*(.*))?$", re.MULTILINE)

# The 5 OpenAPI/AsyncAPI spec files llms.txt's own index lists, and why
# each is deliberately not mirrored: this app consumes Kalshi's API
# exclusively through the official kalshi_python_async SDK plus the
# per-endpoint Markdown pages (whose own inline OpenAPI snippets already
# carry the request/response shape actually needed), never a raw spec
# file at runtime - and the aggregate specs are large (openapi.yaml alone
# is ~320KB, confirmed 2026-08-24) machine-generated surfaces covering
# margin/perps/FCM/RFQ endpoints this app never calls. Mirroring them
# would violate the design spec's own non-goals ("do not implement unused
# Kalshi endpoints merely because docs exist") for zero practical benefit.
_KNOWN_UNSUPPORTED_REASONS: dict[str, str] = {
    "https://docs.kalshi.com/openapi.yaml": (
        "Not consumed by any production code path - this app uses the official "
        "kalshi_python_async SDK plus per-endpoint docs/kalshi/*.md pages, never "
        "the raw aggregate OpenAPI spec (~320KB, covers many endpoints this app "
        "never calls). Confirmed 2026-08-24, Phase A Task A2."
    ),
    "https://docs.kalshi.com/perps_openapi.yaml": (
        "Perpetual-futures API surface - this app trades standard event contracts "
        "only, never perps. Confirmed 2026-08-24, Phase A Task A2."
    ),
    "https://docs.kalshi.com/perps_scm_openapi.yaml": (
        "Perpetual-futures (SCM) API surface - this app trades standard event "
        "contracts only, never perps. Confirmed 2026-08-24, Phase A Task A2."
    ),
    "https://docs.kalshi.com/asyncapi.yaml": (
        "Not consumed by any production code path - services/kalshi_trade_ws.py's "
        "WebSocket handling is built against the per-channel docs/kalshi/*.md "
        "pages (market-ticker.md, public-trades.md, ...), never the raw aggregate "
        "AsyncAPI spec. Confirmed 2026-08-24, Phase A Task A2."
    ),
    "https://docs.kalshi.com/perps_asyncapi.yaml": (
        "Perpetual-futures WS API surface - this app trades standard event "
        "contracts only, never perps. Confirmed 2026-08-24, Phase A Task A2."
    ),
}
_UNREVIEWED_REASON = (
    "New spec resource, not yet reviewed since the last docs sync - flagged for "
    "human decision (mirror vs. explicitly classify unsupported) rather than "
    "silently dropped or assumed unused."
)


def parse_index(text: str) -> list[dict]:
    """Every `- [Title](url): description` bullet in llms.txt's real
    format, tagged with the `## Section` it falls under. A bullet with no
    trailing `: description` (rare, but the OpenAPI/AsyncAPI Specs
    entries in the real file have none) gets description="" rather than
    failing to match."""
    section_starts = [(m.start(), m.group(1).strip()) for m in _SECTION_RE.finditer(text)]

    def _section_for(pos: int) -> str:
        current = ""
        for start, name in section_starts:
            if start <= pos:
                current = name
            else:
                break
        return current

    entries = []
    for m in _BULLET_RE.finditer(text):
        entries.append({
            "title": m.group(1),
            "url": m.group(2),
            "description": (m.group(3) or "").strip(),
            "section": _section_for(m.start()),
        })
    return entries


def resource_kind(url: str, section: str) -> str:
    if url.endswith(".md"):
        return "markdown"
    if section == "OpenAPI Specs":
        return "openapi"
    if section == "AsyncAPI Specs":
        return "asyncapi"
    return "yaml"


def local_name_for(url: str, taken: set[str]) -> str:
    path = url.split("docs.kalshi.com/", 1)[-1]
    segments = path.split("/")
    basename = segments[-1]
    prefix_segments = segments[:-1]
    for n in range(0, len(prefix_segments) + 1):
        candidate = "-".join(prefix_segments[:n] + [basename]) if n else basename
        if candidate not in taken:
            return candidate
    return path.replace("/", "-")  # exhausted every real segment - should not happen in practice


def _content_check(text: str) -> bool:
    return not _looks_like_curated_summary(text)


def sync_manifest(index_entries: list[dict], docs_root: Path, existing_manifest: dict) -> dict:
    docs_root = Path(docs_root)
    existing_by_url = {
        e["source_urls"][0]: e for e in existing_manifest.get("resources", [])
    }
    taken_names = {Path(e["local_path"]).name for e in existing_manifest.get("resources", [])}
    index_by_url = {e["url"]: e for e in index_entries}

    resources: list[dict] = []
    seen_urls: set[str] = set()

    for entry in index_entries:
        url = entry["url"]
        kind = resource_kind(url, entry["section"])
        if kind != "markdown":
            continue
        seen_urls.add(url)
        existing = existing_by_url.get(url)
        if existing is not None:
            local_name = Path(existing["local_path"]).name
        else:
            local_name = local_name_for(url, taken_names)
            taken_names.add(local_name)
        local_file = docs_root / local_name
        if not local_file.exists():
            continue  # never invent a file - see module docstring
        content = local_file.read_text(encoding="utf-8")
        resources.append({
            "local_path": f"docs/kalshi/{local_name}",
            "source_urls": [url],
            "sha256": _normalized_sha256(content) if _content_check(content) else None,
        })

    # Preserve existing resources whose URL is no longer in the fresh
    # index (or whose index entry wasn't markdown-kind for some reason) -
    # index removal is drift for --check to surface, not something --write
    # silently deletes. Re-hash from current disk content either way.
    for url, existing in existing_by_url.items():
        if url in seen_urls:
            continue
        local_file = docs_root / Path(existing["local_path"]).name
        if not local_file.exists():
            resources.append(existing)  # can't refresh what isn't on disk - keep as recorded
            continue
        content = local_file.read_text(encoding="utf-8")
        resources.append({
            "local_path": existing["local_path"],
            "source_urls": [url],
            "sha256": _normalized_sha256(content) if _content_check(content) else None,
        })

    resources.sort(key=lambda e: e["local_path"])

    unsupported: list[dict] = []
    for entry in index_entries:
        url = entry["url"]
        kind = resource_kind(url, entry["section"])
        if kind == "markdown":
            continue
        reason = _KNOWN_UNSUPPORTED_REASONS.get(url, _UNREVIEWED_REASON)
        unsupported.append({
            "local_path": None,
            "source_urls": [url],
            "kind": kind,
            "reason": reason,
        })
    # Keep any previously-recorded unsupported entry whose URL still isn't
    # in the fresh index's markdown set, even if the fresh index fetch
    # didn't include it this run (e.g. an offline/partial --write) - same
    # "never silently drop a classification" principle as resources above.
    seen_unsupported_urls = {e["source_urls"][0] for e in unsupported}
    for existing in existing_manifest.get("unsupported", []):
        url = existing["source_urls"][0]
        if url in seen_unsupported_urls or url in index_by_url:
            continue
        unsupported.append(existing)
    unsupported.sort(key=lambda e: e["source_urls"][0])

    return {"version": 2, "resources": resources, "unsupported": unsupported}


def render_readme(manifest: dict) -> str:
    lines = [
        "# Kalshi Docs Snapshot",
        "",
        "Generated from docs/kalshi/upstream-manifest.json by "
        "`python -m tools.kalshi_docs_sync --write` - do not hand-edit the "
        "Source Pages list below, it will be overwritten on the next sync.",
        "",
        "## Source Pages",
        "",
    ]
    for entry in manifest["resources"]:
        name = Path(entry["local_path"]).name
        lines.append(f"- `{name}`")
        lines.append(f"  Source: `{entry['source_urls'][0]}`")
    lines += ["", "## Deliberately Unsupported (OpenAPI/AsyncAPI specs)", ""]
    for entry in manifest["unsupported"]:
        lines.append(f"- `{entry['source_urls'][0]}` ({entry['kind']})")
        lines.append(f"  Reason: {entry['reason']}")
    lines.append("")
    return "\n".join(lines)


def _fetch_index(timeout: float = 15.0) -> str:
    resp = httpx.get(_INDEX_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def check_drift(index_entries: list[dict], manifest: dict) -> dict:
    """Compares the live llms.txt index's URL set against what the
    manifest currently records as known (mirrored + explicitly
    unsupported). _INDEX_URL itself is excluded from both sides - llms.txt
    describes every OTHER resource but is never listed as a bullet entry
    within its own body, so treating it like any other manifest resource
    would always false-flag it as "removed" (real bug, found live
    2026-08-24 running this exact check against the real index - it isn't
    a resource *in* the index, it *is* the index)."""
    live_urls = {e["url"] for e in index_entries} - {_INDEX_URL}
    known_urls = ({e["source_urls"][0] for e in manifest.get("resources", [])} | {
        e["source_urls"][0] for e in manifest.get("unsupported", [])
    }) - {_INDEX_URL}
    added = sorted(live_urls - known_urls)
    removed = sorted(known_urls - live_urls)
    return {"ok": not added and not removed, "added": added, "removed": removed}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.kalshi_docs_sync")
    parser.add_argument("--check", action="store_true", help="fetch llms.txt and report index drift; never writes")
    parser.add_argument("--write", action="store_true", help="rebuild the manifest and README from current local files")
    parser.add_argument("--docs-root", type=Path, default=_DOCS_ROOT)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST_PATH)
    parser.add_argument("--readme", type=Path, default=_DEFAULT_README_PATH)
    parser.add_argument("--json-out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.check and not args.write:
        print("kalshi-docs-sync: pass --check or --write")
        return 2

    index_entries = parse_index(_fetch_index())

    if args.check:
        existing = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = check_drift(index_entries, existing)
        print(f"kalshi-docs-sync: {len(report['added'])} added, {len(report['removed'])} removed")
        for url in report["added"]:
            print(f"  [added] {url}")
        for url in report["removed"]:
            print(f"  [removed] {url}")
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    existing = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest.exists() else {
        "version": 2, "resources": [], "unsupported": [],
    }
    manifest = sync_manifest(index_entries, args.docs_root, existing)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    args.readme.parent.mkdir(parents=True, exist_ok=True)
    args.readme.write_text(render_readme(manifest), encoding="utf-8")
    print(
        f"kalshi-docs-sync: wrote {len(manifest['resources'])} resources, "
        f"{len(manifest['unsupported'])} unsupported to {args.manifest} and {args.readme}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
