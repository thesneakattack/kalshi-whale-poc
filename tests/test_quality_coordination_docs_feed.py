from tools.quality_coordination import _open_roadmap_bullets, collect_docs_roadmap_feed

SAMPLE = """## Path to production

- [x] Something already shipped.
- [ ] Consider a dedicated charts module for the Advanced view.
- [ ] `services/shadow_mode.py` logs what the strategy would trade against
      real signal data, but hasn't been run for a real evaluation stretch
      and reviewed.
"""


def test_open_roadmap_bullets_skips_checked_items():
    bullets = _open_roadmap_bullets(SAMPLE)

    assert len(bullets) == 2
    assert all(not b.startswith("[x]") for b in bullets)


def test_open_roadmap_bullets_captures_indented_continuation_lines():
    """Regression test (found in review): this repo's real ROADMAP.md bullets routinely
    wrap onto 6-space-indented continuation lines - a bare single-line regex truncates
    them to their first physical line, silently dropping most of a real bullet's text."""
    bullets = _open_roadmap_bullets(SAMPLE)

    shadow_bullet = [b for b in bullets if "shadow_mode.py" in b][0]
    assert "evaluation stretch and reviewed" in shadow_bullet


def test_collect_docs_roadmap_feed_flags_vocabulary_overlap():
    commits = ["feat: add charts module skeleton to Advanced view", "fix: unrelated typo"]

    feed = collect_docs_roadmap_feed(SAMPLE, commits)

    charts_entry = [f for f in feed if "charts" in f["roadmap_bullet"].lower()][0]
    assert charts_entry["possibly_related_commits"] == ["feat: add charts module skeleton to Advanced view"]


def test_collect_docs_roadmap_feed_never_asserts_done():
    feed = collect_docs_roadmap_feed(SAMPLE, [])

    for entry in feed:
        assert "done" not in entry
        assert "resolved" not in entry
        assert set(entry.keys()) == {"roadmap_bullet", "possibly_related_commits"}
