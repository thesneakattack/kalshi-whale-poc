from tools.kanban_sync import labels
from tools.kanban_sync.sources_tracks import parse_track_items

SAMPLE = """## Track A — Realtime data plane (lead track)

**Status (2026-08-26):** CH1 is next. Not started.

Some more prose about Track A.

---

## Track B — Economic strategy validity

**Status (2026-08-26):** deferred behind Track A by explicit user decision.

---

## Track C — Downstream production programs (sequential, hard-gated)

Programs 3R -> 3 -> 4. Nothing here should start.

**Program 7 status (2026-08-26): paused after Task 3.**
"""


def test_parse_track_items_returns_one_item_per_track_heading():
    items = parse_track_items(SAMPLE)

    assert [i.key for i in items] == ["A", "B", "C"]


def test_parse_track_items_uses_full_heading_as_title():
    items = parse_track_items(SAMPLE)

    assert items[0].title == "Track A — Realtime data plane (lead track)"


def test_parse_track_items_captures_status_line_in_body():
    items = parse_track_items(SAMPLE)

    assert "CH1 is next" in items[0].context_body


def test_parse_track_items_marks_hard_gated_track_dependent_on_earlier_tracks():
    items = parse_track_items(SAMPLE)

    track_c = [i for i in items if i.key == "C"][0]
    assert set(track_c.depends_on_keys) == {("track", "A"), ("track", "B")}


def test_parse_track_items_ungated_tracks_have_no_dependencies():
    items = parse_track_items(SAMPLE)

    track_a = [i for i in items if i.key == "A"][0]
    track_b = [i for i in items if i.key == "B"][0]
    assert track_a.depends_on_keys == ()
    assert track_b.depends_on_keys == ()


def test_parse_track_items_all_claimable_type_investigation_not_done():
    items = parse_track_items(SAMPLE)

    assert all(i.status_label == labels.STATUS_CLAIMABLE for i in items)
    assert all(i.type_label == labels.TYPE_INVESTIGATION for i in items)
    assert all(i.done is False for i in items)


def test_parse_track_items_done_status_line_marks_item_done():
    text = "## Track D — Finished track\n\n**Status (2026-08-26):** complete, nothing left.\n"

    items = parse_track_items(text)

    assert items[0].done is True


def test_parse_track_items_does_not_false_positive_on_a_done_sub_step_mid_paragraph():
    """Real bug, found live 2026-08-27: a multi-line status paragraph whose FIRST
    line mentions a sub-step being "done" (CH1), while the track's own overall
    verdict - "not started" - appears several lines later in the same paragraph,
    was being read as the whole track being done. _STATUS_LINE_RE previously
    stopped at the first newline, so the "not started" text was never even seen by
    the done-check. This is the actual text from active-tracks-board.md's Track A
    section that got a live GitHub issue incorrectly auto-closed."""
    text = (
        "## Track A — Realtime data plane (lead track)\n\n"
        "**Status (2026-08-27):** CH1 done (PR #82) — measured negligible on every\n"
        "axis checked: frame count bounded, no snapshot cost on churn-add, no\n"
        "positive queue-depth/latency correlation, and no measured rate-limit\n"
        "pressure. Full measurement: H11 in the known-findings doc.\n"
        "**CH2 is next, not started** — root-cause the still-untraced third\n"
        "instability event; CH1's negligible-cost result makes churn-as-direct-cause\n"
        "less likely on priors but does not rule it out.\n\n"
        "**Canonical docs**\n"
        "- Investigation plan: some-plan.md\n"
    )

    items = parse_track_items(text)

    assert items[0].done is False


def test_parse_track_items_status_paragraph_spans_multiple_lines_in_context_body():
    """The full multi-line status paragraph (not just its first line) should be
    what a human reviewing the issue actually sees, so the "not started" qualifier
    is visible there too, not just used internally by the done-check."""
    text = (
        "## Track A — Realtime data plane (lead track)\n\n"
        "**Status (2026-08-27):** CH1 done (PR #82) — measured negligible on every\n"
        "axis checked.\n"
        "**CH2 is next, not started** — root-cause the instability event.\n\n"
        "**Canonical docs**\n"
    )

    items = parse_track_items(text)

    assert "CH2 is next, not started" in items[0].context_body
