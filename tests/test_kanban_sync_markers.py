from tools.kanban_sync.markers import (
    build_marker, extract_roadmap_title, parse_marker, slugify,
)


def test_build_marker_format():
    assert build_marker("roadmap", "shadow-mode-review") == (
        "<!-- autotrade-sync: roadmap:shadow-mode-review -->"
    )


def test_parse_marker_round_trips_with_build_marker():
    marker = build_marker("track", "A")
    assert parse_marker(marker) == ("track", "A")


def test_parse_marker_returns_none_when_absent():
    assert parse_marker("just a normal issue body, no marker here") is None


def test_parse_marker_finds_marker_inside_longer_body():
    body = (
        "## Context\nsome text\n\n"
        "<!-- autotrade-sync: plan:2026-08-25-frontend-modularization.md -->"
        "\nmore text"
    )
    assert parse_marker(body) == ("plan", "2026-08-25-frontend-modularization.md")


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Shadow-mode sustained run + review") == "shadow-mode-sustained-run-review"


def test_slugify_truncates_to_max_len():
    assert len(slugify("a" * 100, max_len=60)) == 60


def test_slugify_collapses_repeated_separators():
    assert slugify("a   b---c") == "a-b-c"


def test_extract_roadmap_title_uses_bold_lead_in():
    bullet = "**Shadow-mode sustained run + review** - `services/shadow_mode.py` has never..."
    assert extract_roadmap_title(bullet) == "Shadow-mode sustained run + review"


def test_extract_roadmap_title_falls_back_to_plain_text_when_no_bold_lead_in():
    bullet = "Consider a dedicated charts module for the Advanced view."
    assert extract_roadmap_title(bullet) == bullet
