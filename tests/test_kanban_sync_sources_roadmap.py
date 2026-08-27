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


def test_parse_roadmap_items_includes_checked_items_as_done():
    """A checked item still produces a SyncItem (done=True), not none at
    all - sync_pass_one's own create/close logic already handles both ends
    correctly (no existing issue + done => nothing created; existing open
    issue + done => closed). Skipping checked items outright would lose the
    second case: a bullet open when its tracking issue was first created,
    then later checked off, would never get that issue auto-closed. Same
    bug shape as sources_plan.py's build_plan_items - found and fixed
    together 2026-08-27."""
    items = parse_roadmap_items(SAMPLE)

    done_items = [i for i in items if "event-loop-stalled" in i.key]
    assert len(done_items) == 1
    assert done_items[0].done is True


def test_parse_roadmap_items_returns_one_item_per_checkbox_open_or_checked():
    items = parse_roadmap_items(SAMPLE)

    assert len(items) == 3


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
    open_items = [i for i in items if not i.done]

    assert all(i.status_label == labels.STATUS_CLAIMABLE for i in open_items)
    assert all(i.type_label == labels.TYPE_FEATURE for i in open_items)
    assert len(open_items) == 2


def test_parse_roadmap_items_marks_checked_item_status_done():
    items = parse_roadmap_items(SAMPLE)

    done_items = [i for i in items if i.done]
    assert len(done_items) == 1
    assert done_items[0].status_label == labels.STATUS_DONE
    assert done_items[0].type_label == labels.TYPE_FEATURE


def test_parse_roadmap_items_always_has_acceptance_criteria():
    items = parse_roadmap_items(SAMPLE)

    assert all(len(i.acceptance_criteria) >= 1 for i in items)
