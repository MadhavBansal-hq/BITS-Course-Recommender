"""Tests for the merged dataset and its validation report."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ingest.build_dataset import build

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "handouts.json").exists(),
                                reason="run the ingest parsers first")


@pytest.fixture(scope="module")
def built():
    return build(ROOT)


def test_merged_course_record(built):
    courses = {c["course_code"]: c for c in built[0]}
    oop = courses["CS F213"]
    assert oop["title"] == {"value": "Object Oriented Programming", "source": "bulletin IV-109"}
    assert {("COMPUTER SCIENCE", "core")} <= {(l["list_heading"], l["category"]) for l in oop["listings"]}
    assert any(n["programme"] == "B.E. Computer Science" and (n["year"], n["term"]) == (2, "1") for n in oop["named_in"])
    h = oop["handouts"][0]
    assert h["has_midsem"]["value"] is True and h["has_midsem"]["confidence"] == "extracted"


def test_validation_reports_known_cross_document_issues(built):
    v = built[1]["details"]
    assert v["malformed_codes"] == []
    assert "BITS F101" in v["pattern_codes_not_in_any_list_or_timetable"]   # timetable prints BITS F101-1
    assert any(s.startswith("BIO G512: bulletin 5, timetable 15") for s in v["units_disagree_bulletin_vs_timetable"])
    assert any("Robotics" in s and "footer used" in s for s in v["programme_requirement_issues"])


def test_other_campus_handouts_are_reported_and_not_used(built):
    from src.retrieval.dataset import load
    assert built[1]["details"]["handouts_from_another_campus"] == ["208_ECE_F314.pdf (Hyderabad)"]
    assert all(h["file"] != "208_ECE_F314.pdf" for h in load(ROOT).handouts.get("ECE F314", []))


def test_no_fabricated_weights_and_every_unread_scheme_is_listed(built):
    v = built[1]["details"]
    assert v["evaluation_weights_not_in_source"] == []          # every weight appears, as printed, in its handout
    assert all(("PARSER FAILURE" in x) or ("no weight column" in x) or ("scanned" in x)
               for x in v["evaluation_not_fully_read"])


def test_no_midsem_is_withheld_when_the_timetable_schedules_one():
    from src.retrieval.dataset import load
    ds = load(ROOT)
    h = ds.handouts["CE F331"][0]           # handout: quiz, project, compre (100%); timetable: a midsem slot
    assert h["has_midsem"] is None and "sources disagree" in h["has_midsem_basis"]
