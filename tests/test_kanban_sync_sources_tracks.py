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
