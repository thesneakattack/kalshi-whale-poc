"""
Upgrades docs-drift-check.yml from "does each URL still return 200" to real
content-drift detection against docs/kalshi/ - Quality Control Plane
Task 12 (docs/superpowers/plans/2026-08-24-quality-control-plane.md); see
CLAUDE.md's "Kalshi API documentation" section for why docs/kalshi/ is
treated as ground truth in this repo, and docs/kalshi/README.md for the
mirror's own provenance record.

Two independent signals per mirrored page, both derived from the same
fetch:
- availability: did the URL return HTTP 200 at all (the old check's whole
  job, preserved as-is - a non-200 here means Kalshi moved or removed a
  page).
- content drift: does a normalized-line-endings SHA256 of the freshly
  fetched body match what's committed in docs/kalshi/upstream-manifest.json?
  That manifest is built from the CURRENT local mirror at generation time
  (`--write-manifest`), not some independent external source of truth - it
  records "this is what we last confirmed matches upstream," and `--check`
  re-verifies that hasn't silently drifted.

The URL <-> local-file mapping is never re-derived from the URL itself -
docs/kalshi/README.md's own "## Source Pages" section already records it
explicitly per file, including real disambiguation exceptions this module
must not reinvent (a same-basename collision resolved as
welcome-index.md/changelog-index.md, and a margin-rest-/margin-ws-/
fix-margin- prefix convention for the parallel margin-trading API surface -
see README.md's "2026-08-16 - full-index gap-fill" section for the full
story). build_manifest parses that provenance directly.

Not every locally mirrored page is a literal byte-for-byte copy of its
source URL, and this module must not silently pretend otherwise - a real,
live-verified finding from building this checker, not a hypothetical:

- Two pages (get-live-data.md, rate_limits.md) were hand-merged from TWO
  source URLs each, per their own README "Sources:" (plural) entries - not
  a 1:1 mirror of either URL alone.
- 16 pages fetched during the original 2026-08-15 session (before the
  2026-08-16 "mirror literally every page" pass - see README.md's own
  "full-index gap-fill" section) were hand-written LLM *summaries* of the
  page, not verbatim copies - e.g. the real
  websockets/websocket-connection.md is 73KB; the local mirror was 2.6KB of
  condensed prose. Live-verified while building this tool: fetching these
  16 pages fresh and hashing them against the committed manifest produced
  drift on effectively all of them, every single run, regardless of
  whether Kalshi's page had actually changed - a permanent false positive,
  not a signal.

**Update, 2026-08-24 (Kalshi Integration Phase A, Task A1 -
docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md):** 15 of
those 16 curated summaries were production-used contracts (per
docs/kalshi/used-contracts.json's census) and have since been replaced
with real verbatim mirrors, fetched fresh via direct HTTP the same way the
other real mirrors were - both merged files were split into one file per
source URL (get-live-data.md -> get-live-data-with-type.md +
get-multiple-live-data.md; rate_limits.md -> rate_limits.md, now
single-source, + list-non-default-endpoint-costs.md), and the app-specific
analysis that used to live inside those two files' bodies (real account
rate-limit tier data, the SDK method-name/legacy-endpoint findings) moved
to docs/kalshi/CHEATSHEET.md instead, per the boundary design spec's "keep
application commentary in CHEATSHEET rather than inside the mirrored
body." Only get-game-stats.md remains a curated summary now - deliberately:
it is not a production-used contract (see docs/kalshi/README.md's own note
on that entry), so replacing it is deferred per Finding A's own scope note
rather than blocking unrelated migration work.

Detected structurally, not by a hardcoded filename list: a curated
summary's first line is a `Source: <url>` citation (a hand-written
distillation's own convention); a real raw mirror instead starts with the
fetch tool's own literal `> ## Documentation Index` boilerplate header.
This 100%-clean split was verified against every per-page file in the
mirror before relying on it - see `_looks_like_curated_summary`.

`llms.txt` (the 214th entry - the index itself, not a per-endpoint page)
is a known, deliberate imprecision in that same heuristic: live-verified
to actually be a genuine verbatim re-fetch as of 2026-08-16 (README's own
"full-index gap-fill" section says so explicitly), with a 2-line
`Source: <url>` citation header manually prepended on top of the real
body - so it also matches the "starts with Source:" rule and gets
excluded from content-hashing even though, underneath that header, real
comparison is possible. A live diff against a fresh fetch during this
task's own verification found genuinely new upstream pages (e.g. Get
Weather Index, Get Isolated Exit Triggers) that appeared after the mirror
was taken - real signal this exclusion gives up. Accepted as a deliberate,
documented scope trade-off for one low-stakes file rather than adding a
second, single-file-only content-normalization rule; `.claude/hooks/
session_orient.sh`'s own 90-day staleness age-note (based on `llms.txt`'s
last git commit, printed at every session start per CLAUDE.md) is the
existing backstop for a fully-stale index going unnoticed indefinitely.
Revisit if llms.txt's own drift turns out to matter enough to justify the
extra complexity.

All three cases above carry `sha256=None` in the manifest and get
availability-only checking (same "unknown is better than a fabricated
result" discipline the rest of this initiative uses) - see
compare_remote's own handling below. If one of the 16 curated pages is
ever manually refreshed into a real verbatim mirror later, re-running
--write-manifest picks that up automatically (the file would no longer
start with `Source: `) with no code change needed here.

Scope note: the OLD workflow's curl loop also swept the 5 OpenAPI/AsyncAPI
`.yaml` spec URLs llms.txt lists. Those are deliberately NOT mirrored
locally (llms.txt's own "## Notes" section: "this snapshot covers markdown
documentation only") and nothing in this app reads them, so this rewrite's
manifest - built from what's actually mirrored - does not cover them
either. A real, small, deliberate scope narrowing, not an oversight.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Callable

import httpx

_DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs" / "kalshi"
_DEFAULT_MANIFEST_PATH = _DOCS_ROOT / "upstream-manifest.json"

_SOURCE_PAGES_HEADER = "## Source Pages"
_RESPONSE_SNAPSHOTS_HEADER = "## Fetched Response Snapshots"
_BULLET_RE = re.compile(r"^- `([^`]+)`.*$", re.MULTILINE)
_URL_RE = re.compile(r"`(https://docs\.kalshi\.com/[^`]+)`")


def _normalize(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _normalized_sha256(content: str) -> str:
    return hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()


_CURATED_SUMMARY_MARKER = "Source: "  # see this module's own docstring


def _looks_like_curated_summary(content: str) -> bool:
    """True for a hand-written LLM distillation of a page rather than a
    verbatim raw mirror of it - see this module's docstring for how this
    was discovered and verified as a 100%-clean structural split across
    all 213 currently-mirrored pages."""
    return content.startswith(_CURATED_SUMMARY_MARKER)


def _parse_readme_provenance(readme_text: str) -> list[tuple[str, list[str]]]:
    """Every (local_filename, [source_url, ...]) pair recorded under
    README.md's own "## Source Pages" section - not re-derived from URL
    shape. Almost every file lists exactly one Source: URL; two list two
    Sources: (see this module's own docstring). Bullets outside that
    section (e.g. "## Fetched Response Snapshots") are ignored."""
    if _SOURCE_PAGES_HEADER not in readme_text:
        return []
    section = readme_text.split(_SOURCE_PAGES_HEADER, 1)[1]
    section = section.split(_RESPONSE_SNAPSHOTS_HEADER, 1)[0]
    bullets = list(_BULLET_RE.finditer(section))
    entries = []
    for i, bullet in enumerate(bullets):
        name = bullet.group(1)
        start = bullet.end()
        end = bullets[i + 1].start() if i + 1 < len(bullets) else len(section)
        # A bullet's own continuation lines never contain a blank line - one
        # blank line always separates it from whatever comes next (another
        # bullet, or a subheading/prose paragraph like the real README's
        # "### 2026-08-16 - full-index gap-fill" section intro). Clamping
        # to the first blank line, not just the next bullet, stops a
        # same-scanned URL in that unrelated prose from silently attaching
        # itself to the preceding bullet - a real bug, found live 2026-08-24
        # (see this function's own test for the exact reproduction).
        blank_line = section.find("\n\n", start, end)
        if blank_line != -1:
            end = blank_line
        urls = _URL_RE.findall(section[start:end])
        if urls:
            entries.append((name, urls))
    return entries


def build_manifest(docs_root: Path) -> dict:
    """Reads docs_root/README.md's own recorded provenance plus the
    CURRENT on-disk content of each mirrored page - no network access.
    This is what --write-manifest runs; re-run it any time docs/kalshi/ is
    deliberately refreshed so the manifest's hashes track the new
    baseline, the same "the committed file is the source of truth for
    drift comparison" idea tools/quality_audit/baseline.json uses for the
    static audit."""
    readme_text = (docs_root / "README.md").read_text(encoding="utf-8")
    entries = []
    for name, urls in _parse_readme_provenance(readme_text):
        local_file = docs_root / name
        if not local_file.exists():
            continue  # documented but not actually mirrored here (e.g. a JSON response-snapshot bullet)
        content = local_file.read_text(encoding="utf-8")
        content_check = len(urls) == 1 and not _looks_like_curated_summary(content)
        entries.append({
            "local_path": f"docs/kalshi/{name}",
            "source_urls": urls,
            "sha256": _normalized_sha256(content) if content_check else None,
        })
    entries.sort(key=lambda e: e["local_path"])
    return {"version": 1, "entries": entries}


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
    comparison. sha256=None entries (multi-source, see this module's
    docstring) are availability-only by design."""
    changed = []
    unavailable = []
    for entry in manifest["entries"]:
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
    parser.add_argument("--docs-root", type=Path, default=_DOCS_ROOT)
    parser.add_argument(
        "--check", action="store_true",
        help="fetch every source URL and compare against the committed manifest",
    )
    parser.add_argument(
        "--write-manifest", type=Path, default=None, metavar="PATH",
        help="regenerate the manifest from the current local mirror and write it to PATH",
    )
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST_PATH)
    parser.add_argument("--json-out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.write_manifest is not None:
        manifest = build_manifest(args.docs_root)
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"kalshi-docs-drift: wrote {len(manifest['entries'])} entries to {args.write_manifest}")
        return 0

    if not args.check:
        print("kalshi-docs-drift: pass --check or --write-manifest PATH")
        return 2

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = compare_remote(manifest, fetch=_http_fetch)

    print(
        f"kalshi-docs-drift: {len(manifest['entries'])} entries checked, "
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
