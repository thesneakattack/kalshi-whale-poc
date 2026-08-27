"""Shared data model for one syncable unit of work (spec §5-§9). Every
source parser produces a list[SyncItem]; sync.py consumes it uniformly
regardless of which source produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SyncItem:
    kind: str  # one of labels.SYNC_MARKER_KIND_*
    key: str  # stable within kind; must survive line-number/edit churn
    title: str
    status_label: str  # labels.STATUS_*
    type_label: str  # labels.TYPE_*
    context_body: str
    acceptance_criteria: tuple[str, ...]
    scope_paths: tuple[str, ...] = ()
    depends_on_keys: tuple[tuple[str, str], ...] = ()  # (kind, key) pairs
    done: bool = False


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    flagged_mismatches: list[str] = field(default_factory=list)
    dry_run: bool = False
