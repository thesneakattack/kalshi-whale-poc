from tools.kanban_sync.plan_tasks import parse_canonical_tasks

_CANONICAL = """# Some Plan

### Task 1: `labels.py`: rename + 2 new constants

body text here

### Task 2: `sources_plan.py` rename

more body
"""

_SHALLOWER_LEVEL = """# Some Plan

## Task 1: Schema module and connection helper

body
"""

_LETTERED_SUBTASKS = """# Some Plan

## T1a — Guards first, stale docs

body
"""


def test_parse_canonical_tasks_extracts_number_and_title_in_order():
    result = parse_canonical_tasks(_CANONICAL)

    assert result == [
        (1, "`labels.py`: rename + 2 new constants"),
        (2, "`sources_plan.py` rename"),
    ]


def test_parse_canonical_tasks_returns_empty_for_shallower_heading_level():
    """## Task N: (one level shallower than the canonical ### Task N:) is
    a real, different convention found in this repo's own plan docs -
    must not match, not fall back to a looser pattern."""
    assert parse_canonical_tasks(_SHALLOWER_LEVEL) == []


def test_parse_canonical_tasks_returns_empty_for_lettered_subtask_scheme():
    """## T1a - <title> is a third real convention (frontend-modularization's
    own plan) - no "Task N:" text at all, must not match."""
    assert parse_canonical_tasks(_LETTERED_SUBTASKS) == []


def test_parse_canonical_tasks_returns_empty_for_plain_text():
    assert parse_canonical_tasks("Just some prose, no headings at all.") == []


def test_parse_canonical_tasks_does_not_capture_subsequent_paragraphs_as_titles():
    r"""Regression test: a title-less canonical heading (e.g. '### Task 3:' with
    nothing after it) must NOT capture text from the following paragraph as the
    title. The old regex used \s* which matches newlines, causing cross-line
    capture. The fix uses [ \t]* (horizontal whitespace only), which prevents
    capturing into subsequent lines."""
    text = """# Plan

### Task 3:

Actual body paragraph that should never be treated as a title.

### Task 4: real title

More body text.
"""
    result = parse_canonical_tasks(text)
    # Task 3 has no inline title, so it should NOT appear in results.
    # Task 4 has a real title and should be correctly extracted.
    assert result == [(4, "real title")]
