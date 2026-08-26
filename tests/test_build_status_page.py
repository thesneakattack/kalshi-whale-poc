"""
tools/build_status_page.py - assembles static/status.html from
docs/status-src/ fragments (head, chunked timeline, and one file per
reference section). Everything here runs against a tiny synthetic
docs/status-src/ under tmp_path, never the real fragments - deterministic
regardless of how many real phases/chunks this repo happens to have on any
given day.
"""
import pytest

from tools import build_status_page


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_mini_fragments(root, timeline_chunks):
    src = root / "docs" / "status-src"
    _write(src / "head.html", "<html><body><section id=\"timeline\">\n")
    for name, text in timeline_chunks.items():
        _write(src / "timeline" / name, text)
    _write(src / "timeline-close.html", "</section>\n")
    for name in build_status_page._SECTION_ORDER:
        _write(src / f"{name}.html", f"<section id=\"{name}\">{name}</section>\n")
    _write(src / "tail.html", "</body></html>\n")
    return src


def test_build_concatenates_head_timeline_chunks_sections_tail_in_order(tmp_path):
    _build_mini_fragments(
        tmp_path,
        {
            "000-001.html": '<div class="t-phase">phase 0</div>\n',
            "002-003.html": '<div class="t-phase">phase 2</div>\n',
        },
    )

    result = build_status_page.build(tmp_path)

    head_idx = result.index('<section id="timeline">')
    phase0_idx = result.index("phase 0")
    phase2_idx = result.index("phase 2")
    close_idx = result.index("</section>")
    pipeline_idx = result.index('<section id="pipeline">')
    tail_idx = result.index("</body></html>")

    assert head_idx < phase0_idx < phase2_idx < close_idx < pipeline_idx < tail_idx
    # every reference section present, in _SECTION_ORDER
    section_positions = [result.index(f'<section id="{name}">') for name in build_status_page._SECTION_ORDER]
    assert section_positions == sorted(section_positions)


def test_build_reads_timeline_chunks_in_filename_sorted_order_not_write_order(tmp_path):
    _build_mini_fragments(
        tmp_path,
        {
            "025-049.html": "SECOND\n",
            "000-024.html": "FIRST\n",
        },
    )

    result = build_status_page.build(tmp_path)

    assert result.index("FIRST") < result.index("SECOND")


def test_build_raises_when_no_timeline_chunks_exist(tmp_path):
    src = tmp_path / "docs" / "status-src"
    _write(src / "head.html", "<html>\n")

    with pytest.raises(FileNotFoundError):
        build_status_page.build(tmp_path)


def test_active_timeline_chunk_reports_highest_numbered_file_and_real_phase_count(tmp_path):
    _build_mini_fragments(
        tmp_path,
        {
            "000-024.html": '<div class="t-phase">a</div><div class="t-phase">b</div>\n',
            "025-049.html": '<div class="t-phase">c</div>\n',
        },
    )

    path, count = build_status_page.active_timeline_chunk(tmp_path)

    assert path.name == "025-049.html"
    assert count == 1


def test_next_chunk_path_names_file_by_start_and_chunk_size(tmp_path):
    path = build_status_page.next_chunk_path(tmp_path, first_phase_number=175)

    assert path.name == "175-199.html"


def test_main_check_passes_when_committed_file_matches_fresh_build(tmp_path, capsys):
    _build_mini_fragments(tmp_path, {"000-024.html": '<div class="t-phase">x</div>\n'})
    committed = tmp_path / "static" / "status.html"
    rc = build_status_page.main(["--write", str(committed), "--repo-root", str(tmp_path)])
    assert rc == 0

    rc = build_status_page.main(["--check", str(committed), "--repo-root", str(tmp_path)])

    assert rc == 0
    assert "up to date" in capsys.readouterr().out


def test_main_check_fails_when_committed_file_is_stale(tmp_path, capsys):
    _build_mini_fragments(tmp_path, {"000-024.html": '<div class="t-phase">x</div>\n'})
    committed = tmp_path / "static" / "status.html"
    _write(committed, "stale content that does not match fragments\n")

    rc = build_status_page.main(["--check", str(committed), "--repo-root", str(tmp_path)])

    assert rc == 1
    assert "stale" in capsys.readouterr().out


def test_main_check_reports_missing_file_instead_of_crashing(tmp_path, capsys):
    _build_mini_fragments(tmp_path, {"000-024.html": '<div class="t-phase">x</div>\n'})
    missing = tmp_path / "static" / "status.html"

    rc = build_status_page.main(["--check", str(missing), "--repo-root", str(tmp_path)])

    assert rc == 2
    assert "does not exist" in capsys.readouterr().out
