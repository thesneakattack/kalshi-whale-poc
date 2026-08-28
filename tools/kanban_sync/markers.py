"""Sync-identity marker and title/slug derivation (spec §6). The marker is
deliberately decoupled from anything that shifts under ordinary editing
(line numbers, surrounding prose) - see spec §6's reference to the
identity-stability concern in docs/superpowers/research/
2026-08-25-quality-finding-identity-audit.md, applied here to sync sources
instead of scanner findings.
"""
from __future__ import annotations

import re

_MARKER_RE = re.compile(r"<!--\s*autotrade-sync:\s*([a-z]+):(\S+?)\s*-->")
_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def build_marker(kind: str, key: str) -> str:
    return f"<!-- autotrade-sync: {kind}:{key} -->"


def parse_marker(text: str) -> tuple[str, str] | None:
    match = _MARKER_RE.search(text)
    if not match:
        return None
    return match.group(1), match.group(2)


def slugify(text: str, max_len: int = 60) -> str:
    lowered = text.strip().lower()
    slug = _NON_SLUG_RE.sub("-", lowered).strip("-")
    return slug[:max_len].rstrip("-")


def extract_roadmap_title(bullet_text: str) -> str:
    """Bold lead-in if present (`**Text** rest...`), else the bullet's own
    text up to a sentence break within the first 80 chars, else the first
    80 chars verbatim."""
    stripped = bullet_text.strip()
    bold_match = _BOLD_RE.match(stripped)
    if bold_match:
        return bold_match.group(1).strip()
    period = stripped.find(". ")
    if 0 < period <= 80:
        return stripped[:period].strip()
    return stripped[:80].strip()
