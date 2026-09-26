"""
Correctness tests for src.ingest.parse_timetable.

These pin down real bugs found by manually checking the parser's output
against the raw pdftotext output (see docs/EXTRACTION_NOTES.md and the
commit history of parse_timetable.py for how each was found):

  1. Column-header boilerplate ("CREDIT", "L  P  T  S", ...) leaking into
     an instructor list when a course block spans a page break.
  2. Instructor-only rows (project/thesis courses with no room/slot) being
     silently dropped instead of recording the instructor.
  3. Combined lecture+tutorial-group section codes ("L1T1", "L2T7") being
     mis-split into a bogus "L1" section plus garbage remainder.

Requires data/raw/timetable.pdf to be present locally (gitignored — not
committed). Skipped automatically if it's missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ingest.parse_timetable import parse_timetable

TIMETABLE_PDF = Path(__file__).resolve().parents[1] / "data" / "raw" / "timetable.pdf"

pytestmark = pytest.mark.skipif(
    not TIMETABLE_PDF.exists(),
    reason="data/raw/timetable.pdf not present locally (gitignored source file)",
)


@pytest.fixture(scope="module")
def records():
    return parse_timetable(TIMETABLE_PDF)


def _by_course(records, code):
    return [r for r in records if r.course_code == code]


def test_no_header_boilerplate_in_instructors(records):
    junk_markers = ("CREDIT", "MIDSEM", "COMPRE", "U/C")
    for r in records:
        for name in r.instructors:
            assert not any(marker in name.upper() for marker in junk_markers), (
                f"header junk leaked into instructors for {r.course_code} "
                f"{r.section}: {name!r}"
            )


def test_project_course_instructor_not_dropped(records):
    # BIO F266 (STUDY PROJECT) has no room/slot in the timetable — only an
    # instructor-in-charge name. Regression test for bug #2 above.
    rows = _by_course(records, "BIO F266")
    assert rows, "BIO F266 missing entirely from parsed timetable"
    assert rows[0].instructors == ["RAJDEEP CHOWDHURY"]
    assert rows[0].room is None
    assert not rows[0].cancelled


def test_combined_section_codes_parsed_distinctly(records):
    # MGTS U102 has both plain (L1, L2) and combined (L1T1..L1T6, L2T1..L2T7)
    # section codes. Regression test for bug #3 above: each must appear
    # exactly once, with its own room/instructor, not merged into a
    # duplicated/garbled "L1" entry.
    rows = _by_course(records, "MGTS U102")
    sections = [r.section for r in rows]
    assert sections.count("L1") == 1
    assert sections.count("L2") == 1
    for expected in ("L1T1", "L1T4", "L2T3", "L2T7"):
        assert sections.count(expected) == 1, f"{expected} not parsed exactly once"

    l1t4 = next(r for r in rows if r.section == "L1T4")
    assert l1t4.instructors == ["Priya Christina Sande"]
    assert l1t4.room == "6162"


def test_no_duplicate_offerings(records):
    # (com_cod, course_code, section) should be unique — com_cod identifies
    # a specific offering (the timetable can list the same course code
    # twice under different com_cods, e.g. a regular offering and a
    # 2026-admission-only "6xxx" offering; that's legitimate, not a dupe).
    seen = set()
    for r in records:
        if r.com_cod is None:
            continue
        key = (r.com_cod, r.course_code, r.section)
        assert key not in seen, f"duplicate offering parsed: {key}"
        seen.add(key)


def test_cancelled_sections_have_no_instructor_or_room(records):
    for r in records:
        if r.cancelled:
            assert r.instructors == []
            assert r.room is None


def test_course_code_format(records):
    import re

    code_re = re.compile(r"^[A-Z]{2,6} [A-Z]?\d{3}[A-Z]?T?$")
    bad = [r.course_code for r in records if not code_re.match(r.course_code)]
    assert not bad, f"malformed course codes: {bad[:10]}"
