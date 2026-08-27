from tools.kanban_sync import labels
from tools.kanban_sync.sources_roadmap import parse_roadmap_items

SAMPLE = """## Path to production

Some prose paragraph that isn't a bullet at all.

- [x] **The event loop stalled for 17-38+ seconds at a stretch, live,
      post-P0-P2.** Found 2026-08-26 gathering Program 1's evidence.
      Fixed via a SQL rewrite. PR #35.
- [ ] `services/shadow_mode.py` logs what the strategy *would* trade
      against real signal data, but hasn't been run for a real
      evaluation stretch and reviewed.
- [ ] Consider a dedicated charts module for the Advanced view.
"""


def test_parse_roadmap_items_skips_checked_items():
    items = parse_roadmap_items(SAMPLE)

    keys = [i.key for i in items]
    assert not any("event-loop-stalled" in k for k in keys)


def test_parse_roadmap_items_returns_one_item_per_open_checkbox():
    items = parse_roadmap_items(SAMPLE)

    assert len(items) == 2


def test_parse_roadmap_items_uses_bold_lead_in_as_title_when_present():
    items = parse_roadmap_items(
        "- [ ] **Shadow-mode sustained run + review** - never happened.\n"
    )

    assert items[0].title == "Shadow-mode sustained run + review"


def test_parse_roadmap_items_falls_back_to_plain_text_title():
    items = parse_roadmap_items(SAMPLE)

    charts_item = [i for i in items if "charts" in i.title.lower()][0]
    assert charts_item.title == "Consider a dedicated charts module for the Advanced view."


def test_parse_roadmap_items_captures_multiline_continuation_in_body():
    items = parse_roadmap_items(SAMPLE)

    shadow_item = [i for i in items if "shadow_mode" in i.context_body][0]
    assert "evaluation stretch and reviewed" in shadow_item.context_body


def test_parse_roadmap_items_marks_status_claimable_and_type_feature():
    items = parse_roadmap_items(SAMPLE)

    assert all(i.status_label == labels.STATUS_CLAIMABLE for i in items)
    assert all(i.type_label == labels.TYPE_FEATURE for i in items)
    assert all(i.done is False for i in items)


def test_parse_roadmap_items_always_has_acceptance_criteria():
    items = parse_roadmap_items(SAMPLE)

    assert all(len(i.acceptance_criteria) >= 1 for i in items)
