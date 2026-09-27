"""
Correctness tests for src.ingest.parse_timetable.

Each test pins a real defect found by comparing the parser's output with the
raw `pdftotext -layout` text of the supplied timetable (see
docs/EXTRACTION_NOTES.md, "timetable.pdf"). Requires data/raw/timetable.pdf
locally (gitignored); skipped if it is missing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.ingest.parse_timetable import DAYS_HOURS_RE, ROOM_RE, parse_timetable

TIMETABLE_PDF = Path(__file__).resolve().parents[1] / "data" / "raw" / "timetable.pdf"

pytestmark = pytest.mark.skipif(
    not TIMETABLE_PDF.exists(),
    reason="data/raw/timetable.pdf not present locally (gitignored source file)",
)


@pytest.fixture(scope="module")
def parsed():
    return parse_timetable(TIMETABLE_PDF)


@pytest.fixture(scope="module")
def records(parsed):
    return parsed[0]


@pytest.fixture(scope="module")
def unparsed(parsed):
    return parsed[1]


def _by(records, code, section=None):
    return [r for r in records if r.course_code == code and (section is None or r.section == section)]


def test_page_break_headers_and_trailing_sections_never_become_instructors(records):
    # Column headings repeated at page breaks ("CREDIT", "L P T S"), and text
    # from the textbook section after the timetable ("EQUIVALENT", "under
    # Timetable."), used to be appended as instructor names.
    for r in records:
        for name in r.instructors:
            assert re.search(r"[A-Za-z]{2}", name), (r.course_code, name)
            for junk in ("CREDIT", "MIDSEM", "COMPRE", "EQUIVALENT", "Timetable"):
                assert junk not in name, (r.course_code, r.section, name)


def test_parsing_stops_at_the_textbook_section(records):
    assert [r.instructors for r in _by(records, "SW E102")] == [["SUBHASIS PRADHAN"]]


def test_project_course_instructor_not_dropped(records):
    # BIO F266 lists an instructor-in-charge and nothing else.
    rows = _by(records, "BIO F266")
    assert rows and rows[0].instructors == ["RAJDEEP CHOWDHURY"] and rows[0].room is None


def test_section_with_room_but_no_instructor_keeps_its_details(records):
    # FIN F212 L1 has a room, slot and exam dates but a blank instructor
    # column; positional parsing used to drop all of them.
    (row,) = _by(records, "FIN F212", "L1")
    assert row.instructors == []
    assert (row.room, row.days_hours) == ("1201", "M W 4 T 10")
    assert (row.midsem_date, row.compre_date) == ("05/10", "11/12")


def test_columns_are_classified_by_shape(records):
    # Positional parsing put day letters and exam dates into `room` and
    # sessions into `days_hours` whenever a column was blank.
    for r in records:
        if r.room is not None:
            assert ROOM_RE.match(r.room), (r.course_code, r.section, r.room)
        if r.days_hours is not None:
            assert DAYS_HOURS_RE.match(r.days_hours), (r.course_code, r.section, r.days_hours)
        for d in (r.midsem_date, r.compre_date):
            assert d is None or re.fullmatch(r"\d{2}/\d{2}", d)


def test_run_together_hours_and_room_suffixes(records):
    # "M 789" is Monday hours 7-9; "3254_I" is a room.
    (p1,) = [r for r in _by(records, "BIO G523", "P1") if r.com_cod == 2009]
    assert (p1.room, p1.days_hours) == ("3222", "M 789")
    assert any(r.room == "3254_I" and r.days_hours == "T 789" for r in records)


def test_combined_and_suffixed_section_codes(records):
    sections = [r.section for r in _by(records, "MGTS U102")]
    for sec in ("L1", "L2", "L1T1", "L1T4", "L2T3", "L2T7"):
        assert sections.count(sec) == 1, sec
    (l1t4,) = _by(records, "MGTS U102", "L1T4")
    assert l1t4.instructors == ["Priya Christina Sande"] and l1t4.room == "6162"
    assert {r.section for r in _by(records, "BITS F240")} == {"L1AJ", "L2RM"}


def test_split_course_codes_are_parsed(records):
    assert _by(records, "BITS F101-1", "L1")[0].instructors == ["RAJNEESH CHOUBISA"]


def test_every_section_carries_its_offering(records):
    assert all(r.com_cod is not None for r in records)
    rows = _by(records, "MGTS U102")
    assert len({(r.com_cod, r.midsem_date, r.compre_date, r.credit_u) for r in rows}) == 1


def test_no_duplicate_sections_within_an_offering(records):
    keys = [(r.com_cod, r.course_code, r.section) for r in records]
    assert len(keys) == len(set(keys))


def test_2026_only_flag_follows_com_cod(records):
    assert all(r.only_for_2026_admissions == (r.com_cod >= 5000) for r in records)
    assert any(r.only_for_2026_admissions for r in records)


def test_unparseable_rows_are_reported_not_misattributed(records, unparsed):
    # EEE G593 has two offerings. 2144 (page 63) parses, including its own P1.
    # 6301's credit columns ("5 - - 15") don't fit L P T S U, so the row is
    # reported, and the "Practical P1" row under it on page 64 must be
    # reported too, not attached to 2144.
    assert any("6301" in u["text"] and "EEE G593" in u["text"] for u in unparsed)
    rows = _by(records, "EEE G593")
    assert {r.com_cod for r in rows} == {2144}
    assert [(r.section, r.source_page) for r in rows if r.section == "P1"] == [("P1", 63)]
    assert any(u["page"] == 64 and u["text"].startswith("Practical") for u in unparsed)


def test_cancelled_sections_have_no_room_or_slot(records):
    # A cancelled section can still print an instructor name on the next
    # line (ME G532, offering 6441), so only room and slot are asserted.
    for r in records:
        if r.cancelled:
            assert r.room is None and r.days_hours is None, (r.course_code, r.section)


def test_course_code_format(records):
    code_re = re.compile(r"^[A-Z]{2,6} [A-Z]?\d{3}[A-Z]?T?(?:-\d)?$")
    assert not [r.course_code for r in records if not code_re.match(r.course_code)]
