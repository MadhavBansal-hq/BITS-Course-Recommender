"""
Correctness tests for src.ingest.parse_bulletin.

Every expected value was checked by eye against the bulletin page it cites;
each test pins a defect the parser had at some point (see the module
docstring and docs/EXTRACTION_NOTES.md, "bulletin.pdf"). Requires
data/raw/bulletin.pdf locally (gitignored); skipped if it is missing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber
import pytest

from src.ingest.parse_bulletin import _column_boundaries, parse_bulletin_part_iv

BULLETIN_PDF = Path(__file__).resolve().parents[1] / "data" / "raw" / "bulletin.pdf"
CODE_RE = re.compile(r"\b[A-Z]{2,6} [A-Z]?\d{3}[A-Z]?T?\b")

pytestmark = pytest.mark.skipif(
    not BULLETIN_PDF.exists(),
    reason="data/raw/bulletin.pdf not present locally (gitignored source file)",
)


@pytest.fixture(scope="module")
def list_of_courses():
    # Physical 314-336 = IV-106..IV-128. Parsed as a whole because category
    # and heading carry across pages, as they do in the full run.
    courses, _, _ = parse_bulletin_part_iv(BULLETIN_PDF, 314, 336)
    return courses


def _rows(courses, code):
    return [c for c in courses if c.course_code == code]


def _in_list(courses, code, heading):
    (row,) = [c for c in _rows(courses, code) if c.list_heading == heading]
    return row


def test_code_mentioned_in_prose_does_not_create_a_column():
    # IV-109: "FRP" in a title; IV-117: "PHA F243" inside a footnote.
    with pdfplumber.open(BULLETIN_PDF) as pdf:
        for page in (317, 325):
            assert len(_column_boundaries(pdf.pages[page - 1].extract_words())) == 2, page


def test_left_column_keeps_its_own_units(list_of_courses):
    # CE F231's unit "3" sits at x=244, right of the old midpoint split.
    row = _in_list(list_of_courses, "CE F231", "CIVIL ENGINEERING")
    assert (row.title, row.credit_u, row.category) == ("Fluid Mechanics", "3", "core")


def test_title_wrap_in_the_other_column_is_not_pulled_in(list_of_courses):
    # "Engineering Laboratory" (right column) shares a y-position with
    # CS F437's row (left column) on IV-110.
    for heading in ("COMPUTER SCIENCE", "ELECTRONICS AND COMPUTER ENGINEERING"):
        assert _in_list(list_of_courses, "CS F437", heading).title == "Generative Artificial Intelligence"
    assert _in_list(list_of_courses, "CS F301", "COMPUTER SCIENCE").title == \
        "Principles of Programming Languages"


def test_category_and_heading_carry_across_columns_and_pages(list_of_courses):
    # CE F430 continues the CE elective list in the right column of IV-109;
    # CS F320 continues the CS elective list on IV-110; AN F313 follows a
    # header split over two lines ("DISCIPLINE ELECTIVE" / "COURSES").
    assert _in_list(list_of_courses, "CE F430", "CIVIL ENGINEERING").category == "discipline_elective"
    assert _in_list(list_of_courses, "CS F320", "COMPUTER SCIENCE").category == "discipline_elective"
    assert _in_list(list_of_courses, "AN F313", "MECHANICAL ENGINEERING").category == "discipline_elective"


def test_same_course_is_core_for_one_branch_and_elective_for_another(list_of_courses):
    lists = {(c.category, c.list_heading) for c in _rows(list_of_courses, "CS F213")}
    assert ("core", "COMPUTER SCIENCE") in lists
    assert ("discipline_elective", "ELECTRICAL AND ELECTRONICS ENGINEERING") in lists


def test_unusual_unit_tokens(list_of_courses):
    cs_f434 = _in_list(list_of_courses, "CS F434", "COMPUTER SCIENCE")      # "3*"
    assert (cs_f434.title, cs_f434.credit_u, cs_f434.footnote_marker) == ("Data science for Healthcare", "3", True)
    cs_f441 = _in_list(list_of_courses, "CS F441", "COMPUTER SCIENCE")      # "- - 3"
    assert (cs_f441.credit_l, cs_f441.credit_p, cs_f441.credit_u) == ("-", "-", "3")
    assert cs_f441.title == "Selected Topics from Computer Science"


def test_or_alternatives(list_of_courses):
    assert _in_list(list_of_courses, "ME F344", "ELECTRICAL AND ELECTRONICS ENGINEERING").alternative_to == "MATH F212"
    heading = "CHEMICAL ENGINEERING WITH SPECIALIZATION IN ENERGY, ENVIRONMENT, AND SUSTAINABILITY"
    # "CHE F317 Energy Systems 3 0 3" / "or Engineering" / "CHE G557 or 0 0 4" / ...
    assert _in_list(list_of_courses, "CHE F317", heading).title == "Energy Systems Engineering"
    che_g557 = _in_list(list_of_courses, "CHE G557", heading)
    assert (che_g557.title, che_g557.credit_u, che_g557.alternative_to) == \
        ("Energy Systems Engineering", "4", "CHE F317")


def test_cross_listed_code(list_of_courses):
    row = _in_list(list_of_courses, "CS G514", "COMPUTER SCIENCE")
    assert (row.aliases, row.title) == (["SS G514"], "Object Oriented Analysis and Design")


def test_list_of_courses_titles_are_clean(list_of_courses):
    for c in list_of_courses:
        assert not CODE_RE.search(c.title), (c.course_code, c.title)
        assert not re.search(r"IV-\d|^(or|OR)\b|\*", c.title), (c.course_code, c.title)


def test_audit_course_titles_are_not_list_headings(list_of_courses):
    # IV-128: "PERSONALITY INTEGRATION" / "AND TEAMWORK" are the wrapped
    # ALL-CAPS title of an audit course, not a list heading.
    headings = {c.list_heading for c in list_of_courses}
    assert not headings & {"PERSONALITY INTEGRATION", "AND TEAMWORK", "DISCIPLINE ELECTIVE", "COURSES"}
    assert _rows(list_of_courses, "BITS N301T")[0].list_heading == "List of Audit Type Courses"
    uncategorised = {c.list_heading for c in list_of_courses if c.category is None}
    assert uncategorised == {"List of Audit Type Courses"}


def test_long_prose_is_dropped_not_merged():
    # IV-231: a ~20-word Senate-approval sentence sits under SAN G513.
    courses, _, dropped = parse_bulletin_part_iv(BULLETIN_PDF, 439, 439)
    san = _rows(courses, "SAN G513")
    assert san, "SAN G513 not parsed at all"
    assert all("Senate" not in c.title for c in san)
    assert any("Senate" in d["text"] for d in dropped)


@pytest.mark.slow
def test_full_part_iv():
    courses, _, _ = parse_bulletin_part_iv(BULLETIN_PDF)
    assert len(courses) > 7000
    assert all(re.fullmatch(r"[A-Z]{2,6} [A-Z]?\d{3}[A-Z]?T?", c.course_code) for c in courses)
    assert all(c.bulletin_section for c in courses)
    loc = [c for c in courses if c.bulletin_section == "list_of_courses"]
    assert all(not CODE_RE.search(c.title) for c in loc)
