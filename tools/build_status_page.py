"""
Assembles static/status.html from docs/status-src/ fragments.

static/status.html (the historical build-timeline page served at /status,
see CLAUDE.md) grew to 6358 lines / 156 chronological phase entries before
this existed, all in one file - expensive for a session to grep/Read/Edit
and only getting bigger, since the timeline is append-only by design and
never pruned. docs/status-src/ is now the edited source of truth: small,
per-section fragment files, plus a timeline/ directory chunked at a fixed
25 phases per file so no single file grows without bound. This module's
job is purely mechanical concatenation in a fixed order - it has no
opinion about content, matching tools/project_manifest.py's own "generates
structure, never narrative" posture.

Fragment layout (docs/status-src/):
  head.html            - DOCTYPE/<head>/<style>/<body> open, headline
                          section, and the <section id="timeline"> open
                          through the <div class="timeline"> wrapper.
  timeline/*.html       - phase blocks only (<div class="t-phase">...),
                          chunked _CHUNK_SIZE phases per file, filenames
                          "{start:03d}-{end:03d}.html" by 0-indexed phase
                          number (e.g. 150-174.html). Concatenated in
                          filename-sorted order, which is phase order.
  timeline-close.html   - closes </div> (.timeline) and </section>
                          (#timeline). Static, never edited.
  <section>.html         - one file per remaining top-level <section>, each
                          self-contained (own open/close tag), for every
                          name in _SECTION_ORDER.
  tail.html              - footer + </div></body></html>.

No live execution, no network - pure filesystem read/concatenate, same
"no live execution" posture as tools/project_manifest.py and
tools/kalshi_docs_drift.py.
"""
import argparse
import sys
from pathlib import Path

_SECTION_ORDER = [
    "pipeline",
    "components",
    "api",
    "config",
    "limitations",
    "stages",
    "checklist",
]

_PHASE_MARKER = '<div class="t-phase">'
_CHUNK_SIZE = 25


def _status_src(repo_root: Path) -> Path:
    return repo_root / "docs" / "status-src"


def _timeline_chunks(repo_root: Path) -> list[Path]:
    chunk_dir = _status_src(repo_root) / "timeline"
    chunks = sorted(chunk_dir.glob("*.html"))
    if not chunks:
        raise FileNotFoundError(f"no timeline chunk files found in {chunk_dir}")
    return chunks


def build(repo_root: Path) -> str:
    """Concatenate docs/status-src/ fragments into a single status.html string."""
    src = _status_src(repo_root)
    parts = [(src / "head.html").read_text()]
    parts.extend(chunk.read_text() for chunk in _timeline_chunks(repo_root))
    parts.append((src / "timeline-close.html").read_text())
    parts.extend((src / f"{name}.html").read_text() for name in _SECTION_ORDER)
    parts.append((src / "tail.html").read_text())
    return "".join(parts)


def active_timeline_chunk(repo_root: Path) -> tuple[Path, int]:
    """Return (path, phase_count) for the highest-numbered timeline chunk.

    Used by the sync-status-docs skill instead of a fragile `ls | tail -1` -
    the phase count comes from counting real markers in the file, not from
    parsing the filename, so it stays correct even if a chunk was hand-edited.
    """
    active = _timeline_chunks(repo_root)[-1]
    count = active.read_text().count(_PHASE_MARKER)
    return active, count


def next_chunk_path(repo_root: Path, first_phase_number: int) -> Path:
    """Path a new timeline chunk should be created at, starting at first_phase_number."""
    last = first_phase_number + _CHUNK_SIZE - 1
    return _status_src(repo_root) / "timeline" / f"{first_phase_number:03d}-{last:03d}.html"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.build_status_page")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--write", type=Path, default=None, help="assemble fragments and write the result to PATH")
    parser.add_argument("--check", type=Path, default=None, help="fail if PATH differs from a fresh assembly")
    parser.add_argument(
        "--active-chunk", action="store_true",
        help="print the active timeline chunk's path and current phase count, then exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.active_chunk:
        path, count = active_timeline_chunk(args.repo_root)
        full = count >= _CHUNK_SIZE
        print(f"{path}\t{count} phases{' (full - start a new chunk)' if full else ''}")
        return 0

    content = build(args.repo_root)

    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(content)
        print(f"build-status-page: wrote {args.write}")
        return 0

    if args.check is not None:
        if not args.check.exists():
            print(f"build-status-page: {args.check} does not exist - run --write first")
            return 2
        committed = args.check.read_text()
        if committed == content:
            print("build-status-page: up to date")
            return 0
        print(f"build-status-page: {args.check} is stale relative to docs/status-src/ fragments")
        print(
            "Regenerate with:\n"
            f"  python3 -m tools.build_status_page --write {args.check} --repo-root ."
        )
        return 1

    print("build-status-page: pass --write PATH, --check PATH, or --active-chunk")
    return 2


if __name__ == "__main__":
    sys.exit(main())
