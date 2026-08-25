"""
Upgrades docs-drift-check.yml from "does each URL still 200" to real
content-drift detection against docs/kalshi/ - Quality Control Plane
Task 12 (docs/superpowers/plans/2026-08-24-quality-control-plane.md); see
CLAUDE.md's "Kalshi API documentation" section for why docs/kalshi/ is
treated as ground truth in this repo, and docs/kalshi/upstream-manifest.json
for the mirror's own provenance record.

Two independent signals per mirrored page, both derived from the same
fetch:
- availability: did the URL return HTTP 200 at all (the old check's whole
  job, preserved as-is - a non-200 here means Kalshi moved or removed a
  page).
- content drift: does a normalized-line-endings SHA256 of the freshly
  fetched body match what's committed in docs/kalshi/upstream-manifest.json?
  That manifest records "this is what we last confirmed matches upstream,"
  and `--check` re-verifies that hasn't silently drifted.

**Update, 2026-08-24 (Kalshi Integration Phase A, Task A2 -
docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md):** this
module used to build the manifest itself by parsing docs/kalshi/README.md's
"## Source Pages" section (a real, live-found parser bug from that era -
see git history and tests/test_kalshi_docs_drift.py's
`test_build_manifest_does_not_bleed_urls_from_prose_after_a_bullet` - is
exactly the kind of fragility that motivated this split). That
responsibility has moved to `tools/kalshi_docs_sync.py`, which owns
building/refreshing the manifest from `docs/kalshi/llms.txt`'s real index
plus current on-disk content, and generates README.md from the manifest
instead of the other way around. This module now only *consumes* the
committed manifest (`load_manifest`) - it has no README dependency left at
all, satisfying the design spec's "the mirror's machine manifest, not
README prose, should become the provenance source of truth."

Not every locally mirrored page is a literal byte-for-byte copy of its
source URL, and this module must not silently pretend otherwise - a real,
live-verified finding from building this checker, not a hypothetical: one
page (`docs/kalshi/get-game-stats.md`, as of 2026-08-24) is a hand-written
LLM *summary*, not a verbatim copy, and is not a production-used contract
(see docs/kalshi/README.md's own note on that entry) - deliberately not
yet replaced. Hashing a curated summary against a fresh raw fetch would
drift permanently even with zero real upstream change - a permanent false
positive, not a signal.

Detected structurally, not by a hardcoded filename list: a curated
summary's first line is a `Source: <url>` citation (a hand-written
distillation's own convention); a real raw mirror instead starts with the
fetch tool's own literal `> ## Documentation Index` boilerplate header.
This 100%-clean split was verified against every per-page file in the
mirror before relying on it - see `_looks_like_curated_summary`. Entries
with `sha256: null` in the manifest (this curated-summary page, plus
`llms.txt` itself - see `tools/kalshi_docs_sync.py`'s own docstring for
why the index file is a deliberate exception) get availability-only
checking, same "unknown is better than a fabricated result" discipline the
rest of this initiative uses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Callable

import httpx

_DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs" / "kalshi"
_DEFAULT_MANIFEST_PATH = _DOCS_ROOT / "upstream-manifest.json"

_CURATED_SUMMARY_MARKER = "Source: "  # see this module's own docstring


def _normalize(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _normalized_sha256(content: str) -> str:
    return hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()


def _looks_like_curated_summary(content: str) -> bool:
    """True for a hand-written LLM distillation of a page rather than a
    verbatim raw mirror of it - see this module's docstring for how this
    was discovered and verified as a 100%-clean structural split across
    every currently-mirrored page."""
    return content.startswith(_CURATED_SUMMARY_MARKER)


def load_manifest(path: Path) -> dict:
    """The manifest is tool/hand-maintained JSON now (built by
    `tools.kalshi_docs_sync`), not something this module derives from
    README prose - this is a thin, deliberately dumb load, not a parser."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


Fetcher = Callable[[str], tuple[int, str]]


def _http_fetch(url: str, timeout: float = 15.0) -> tuple[int, str]:
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        return 0, str(exc)
    return resp.status_code, resp.text


def compare_remote(manifest: dict, fetch: Fetcher) -> dict:
    """fetch(url) -> (status_code, body_text) - injectable so tests never
    hit the network (see tests/test_kalshi_docs_drift.py). A non-200
    response is recorded as unavailable and never reaches the content-hash
    comparison. sha256=None entries (curated summaries / llms.txt, see
    this module's docstring) are availability-only by design. Only
    manifest["resources"] are checked - manifest["unsupported"] entries
    (OpenAPI/AsyncAPI specs deliberately not mirrored, see
    tools/kalshi_docs_sync.py) have no local file to drift-check against."""
    changed = []
    unavailable = []
    for entry in manifest["resources"]:
        for url in entry["source_urls"]:
            status, body = fetch(url)
            if status != 200:
                unavailable.append({"local_path": entry["local_path"], "url": url, "status": status})
                continue
            if entry["sha256"] is None:
                continue
            actual = _normalized_sha256(body)
            if actual != entry["sha256"]:
                changed.append({
                    "local_path": entry["local_path"], "url": url,
                    "expected_sha256": entry["sha256"], "actual_sha256": actual,
                })
    return {"ok": not changed and not unavailable, "changed": changed, "unavailable": unavailable}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.kalshi_docs_drift")
    parser.add_argument(
        "--check", action="store_true",
        help="fetch every mirrored resource's source URL and compare against the committed manifest",
    )
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST_PATH)
    parser.add_argument("--json-out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.check:
        print("kalshi-docs-drift: pass --check (manifest construction/refresh moved to tools.kalshi_docs_sync)")
        return 2

    manifest = load_manifest(args.manifest)
    report = compare_remote(manifest, fetch=_http_fetch)

    print(
        f"kalshi-docs-drift: {len(manifest['resources'])} resources checked, "
        f"{len(report['changed'])} changed, {len(report['unavailable'])} unavailable"
    )
    for c in report["changed"]:
        print(f"  [content-drift] {c['local_path']} <- {c['url']}")
    for u in report["unavailable"]:
        print(f"  [unavailable:{u['status']}] {u['local_path']} <- {u['url']}")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2))

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
