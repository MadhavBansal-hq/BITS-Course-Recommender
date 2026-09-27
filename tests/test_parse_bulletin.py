"""
Correctness tests for src.ingest.parse_bulletin.

Each test pins down a real bug found by manually inspecting pdfplumber's
word-level output against the actual rendered page and comparing to
`pdftotext` output, all on physical page 317 (Civil Engineering /
Computer Science degree-plan page) and page 439 (a differently-shaped
M.E./M.Tech semester-plan page) unless noted. See
docs/EXTRACTION_NOTES.md and the parse_bulletin.py module docstrings for
the full reasoning behind each fix.

Requires data/raw/bulletin.pdf to be present locally (gitignored — not
committed). Skipped automatically if it's missing.
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from src.ingest.parse_bulletin import (
    _column_boundaries,
    _reconstruct_columns,
    _parse_column_lines,
    parse_bulletin_part_iv,
)

BULLETIN_PDF = Path(__file__).resolve().parents[1] / "data" / "raw" / "bulletin.pdf"

pytestmark = pytest.mark.skipif(
    not BULLETIN_PDF.exists(),
    reason="data/raw/bulletin.pdf not present locally (gitignored source file)",
)

PAGE_317 = 316  # 0-indexed
PAGE_439 = 438


@pytest.fixture(scope="module")
def pdf():
    with pdfplumber.open(BULLETIN_PDF) as doc:
        yield doc


def _reconstruct(pdf, page_index):
    page = pdf.pages[page_index]
    words = page.extract_words()
    anchors = _column_boundaries(words)
    return anchors, _reconstruct_columns(words, anchors)


def test_column_anchor_ignores_incidental_caps_word(pdf):
    # "FRP" (from "FRP Reinforced Concrete Structures") is 3 uppercase
    # letters and satisfies a bare dept-prefix pattern by coincidence,
    # but is not followed by a course-number token — must not create a
    # spurious third column anchor.
    anchors, _ = _reconstruct(pdf, PAGE_317)
    assert len(anchors) == 2, f"expected 2 columns, got anchors={anchors}"


def test_left_column_row_keeps_its_own_single_unit_number(pdf):
    # CE F231 prints only a single unit number ("3"), not an L/P/U
    # triple, and that number sits at an x-position numerically closer to
    # the RIGHT column's anchor than to its own row's title text — a
    # naive nearest-anchor-per-word assignment puts it in the wrong
    # column entirely, silently truncating the row.
    _, columns = _reconstruct(pdf, PAGE_317)
    assert any(l.strip() == "CE F231 Fluid Mechanics 3" for l in columns[0])


def test_cross_column_row_collision_does_not_corrupt_title(pdf):
    # "CE F331 In-situ Testing Methods in ..." (left column) and
    # "Languages" (the wrapped continuation of CS F301 in the RIGHT
    # column, several rows above) sit at nearly-but-not-exactly the same
    # y-position. Grouping rows by rounded y before separating columns
    # merged them into one line and appended "Languages" onto CE F331's
    # title. Exact (not rounded/tolerant) y-equality, checked before
    # column separation, is what actually distinguishes them.
    _, columns = _reconstruct(pdf, PAGE_317)
    ce_f331 = [l for l in columns[0] if "F331" in l]
    assert ce_f331, "CE F331 missing from left column"
    assert "Languages" not in ce_f331[0]

    cs_f301 = [l for l in columns[1] if "F301" in l]
    assert cs_f301 and cs_f301[0].strip() == "CS F301 Principles of Programming 2 0 2"


def test_section_header_with_trailing_column_labels_still_sets_category(pdf):
    # The header line is literally "CORE COURSES L P U" on this page (the
    # repeated column headings trail on the same visual row) — matching
    # only the exact string "CORE COURSES" misses it entirely.
    _, columns = _reconstruct(pdf, PAGE_317)
    results, _dropped = _parse_column_lines(columns[1])
    cs_f211 = next(m for cat, m in results if m["dept"] == "CS" and m["num"] == "F211")
    idx = next(i for i, (cat, m) in enumerate(results) if m is cs_f211)
    assert results[idx][0] == "core"


def test_wrapped_title_merges_but_section_header_does_not(pdf):
    # CE F435's title wraps onto its own line ("Methods") AND is
    # immediately followed by the branch header "COMPUTER SCIENCE" before
    # the next real course code — the header must not also merge into
    # the title just because it's short.
    _, columns = _reconstruct(pdf, PAGE_317)
    results, _dropped = _parse_column_lines(columns[1])
    ce_f435 = next(m for cat, m in results if m["dept"] == "CE" and m["num"] == "F435")
    assert ce_f435["title"].strip() == "Introduction to Finite Element Methods"


def test_page_footer_does_not_merge_into_title(pdf):
    # CS F314's title is immediately followed by the page footer "IV-109"
    # (a bare page-number marker) before the page break.
    _, columns = _reconstruct(pdf, PAGE_317)
    results, _dropped = _parse_column_lines(columns[1])
    cs_f314 = next(m for cat, m in results if m["dept"] == "CS" and m["num"] == "F314")
    assert "IV-109" not in cs_f314["title"]
    assert cs_f314["title"].strip() == "Software Development for Portable Devices"


def test_long_prose_paragraph_is_dropped_not_merged(pdf):
    # Physical page 439 (M.E. Sanitation Science semester plan) ends its
    # course-table column with a multi-sentence explanatory paragraph
    # about programme tracks. It has no course code and no units, so it
    # looks like a "wrapped title continuation" by the same test that
    # correctly merges "Technology" or "Structures" elsewhere — but at
    # 6+ words it must be dropped (flagged for verification) instead of
    # silently appended onto SAN G513's title.
    _, columns = _reconstruct(pdf, PAGE_439)
    for col_lines in columns:
        results, dropped = _parse_column_lines(col_lines)
        san_g513 = [m for cat, m in results if m.get("dept") == "SAN" and m.get("num") == "G513"]
        if san_g513:
            assert "track 3" not in san_g513[0]["title"]
            assert "Senate" not in san_g513[0]["title"]
            assert any("Senate" in d for d in dropped)


def test_full_part_iv_run_produces_wellformed_course_codes():
    import re

    courses, _unparsed, _dropped = parse_bulletin_part_iv(BULLETIN_PDF, 209, 462)
    code_re = re.compile(r"^[A-Z]{2,6} [A-Z]?\d{3}[A-Z]?T?$")
    bad = [c.course_code for c in courses if not code_re.match(c.course_code)]
    assert not bad, f"malformed course codes: {bad[:10]}"
    assert len(courses) > 5000, (
        f"expected several thousand courses from Part IV, got {len(courses)} "
        "-- did the page range or column detection regress?"
    )
