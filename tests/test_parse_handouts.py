"""
Tests for src.ingest.parse_handouts. Expected values were read from the
handouts named. Requires data/raw/handouts/ (gitignored); skipped if missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ingest.parse_handouts import parse_handout

H = Path(__file__).resolve().parents[1] / "data" / "raw" / "handouts"
pytestmark = pytest.mark.skipif(not H.exists(), reason="handouts not present locally")


def _one(stem):
    # some courses have two handout files (e.g. 013_ and 014_BIO_G512); use the first
    return parse_handout(sorted(H.glob(f"*_{stem}.pdf"))[0])


def _table(h):
    return [(c.name, c.kind, c.weight_pct) for c in h.evaluation]


def test_plain_table():
    h = _one("CS_F213")
    assert _table(h) == [("Quizzes", "quiz", 7.5), ("Mid-Semester Exam", "midsem", 25.0),
                         ("Lab Test", "lab", 25.0), ("Comprehensive Exam", "compre", 35.0),
                         ("Weekly Labs", "lab", 7.5)]
    assert h.evaluation_complete and h.has_midsem and h.has_compre
    assert h.open_book_components == ["Lab Test", "Weekly Labs"]
    assert (h.course_title, h.instructor_in_charge) == ("Object Oriented Programming", "Dr. Pratik Narang")


def test_percentage_in_duration_column_is_not_a_weight():
    # GS F212: "Class participation and attendance | 70% classes | 10%"
    h = _one("GS_F212")
    (att,) = [c for c in h.evaluation if c.kind == "attendance"]
    assert att.weight_pct == 10.0 and "70%" in att.duration


def test_header_split_over_lines_and_centred_names():
    # BITS F113: "EC / Evaluation / Component" beside "Weightage (%)"; the
    # first name is printed above and below its row.
    h = _one("BITS_F113")
    assert _table(h) == [("Quizzes/Class Performance Tests", "quiz", 20.0),
                         ("Mid semester Exam", "midsem", 35.0), ("Comprehensive Exam", "compre", 45.0)]


def test_midsem_spellings():
    assert _one("ECON_F311").has_midsem          # "Mid - semester Test"
    assert _one("CHE_F214").has_midsem           # "Mid Examination"
    assert _one("BIO_G512").has_midsem           # "Weitage (%)" header, "Mid- Sem Test"


def test_no_midsem_only_from_a_complete_clean_table():
    h = _one("CE_F331")                           # Quiz, Project, Comprehensive
    assert h.has_midsem is False and "complete evaluation scheme" in h.has_midsem_basis
    assert _one("CHE_F415").has_midsem is None    # nature column misread as names


def test_total_rows_are_not_components():
    assert all("total" not in c.name.lower() for c in _one("CHE_F312").evaluation)


def test_prerequisites_classified():
    hard = _one("EEE_F437").prerequisites[0]
    assert hard["kind"] == "hard_course_code" and "F-214" in hard["text"]
    assert _one("CHE_F213").prerequisites[0]["kind"] == "none_stated"   # "Prerequisite: NA"
    assert _one("CS_F429").prerequisites[0]["kind"] == "soft_recommendation"


def test_scanned_handout_is_flagged_not_guessed():
    h = _one("MAC_F214")
    assert not h.text_layer and h.has_midsem is None and not h.evaluation


def test_weight_notations():
    from src.ingest.parse_handouts import weight_of
    cases = {"25": 25, "25 %": 25, "7.5%": 7.5, "[10%]": 10, "20*": 20, "5#": 5, "30 (10+20)": 30,
             "35 % (70 M)": 35, "30 % (Max. Marks 30)": 30, "30% (60)": 30, "50 (25%)": 25, "20+10": 30,
             "30*%": 30, "35% (CB)": 35}
    assert {t: weight_of(t) for t in cases} == {t: float(v) for t, v in cases.items()}
    assert all(weight_of(t) is None for t in ("90 min", "3 hrs", "05/10", "5-10", "Total"))


def test_exam_rows_saying_as_per_augsd_are_kept():
    # a bare "AUGS" letterhead filter deleted rows whose date cell reads "As per AUGSD"
    h = _one("ECE_F311")
    assert _table(h) == [("Quizzes", "quiz", 15.0), ("Weekly Labs", "lab", 16.67), ("Lab Project", "lab", 8.33),
                         ("Mid-Semester Test", "midsem", 25.0), ("Comprehensive Exam", "compre", 35.0)]


def test_marks_rescaled_only_against_a_stated_total():
    # ECE F211: "Marks (300)"; 75 marks of 300 is 25%. Rescaling a partial read
    # without a matching stated total once turned 15/16.67/8.33 into 37.5/41.7/20.8.
    h = _one("ECE_F211")
    assert h.evaluation_complete and dict((c.name.split()[0], c.weight_pct) for c in h.evaluation)["Midsem"] == 25.0
    assert any("marks out of 300; converted" in u for u in h.unresolved)


def test_weight_column_found_from_the_numbers():
    # pdftotext printed "Weightage" over "Remarks" (BIO F311) and far right of "%" (BIO G523)
    assert [c.weight_pct for c in _one("BIO_F311").evaluation] == [35.0, 25.0, 40.0]
    assert [c.weight_pct for c in _one("BIO_G523").evaluation] == [30.0, 15.0, 15.0, 40.0]


def test_headerless_fallback_reader():
    h = _one("BITS_F415")                         # "Wt (%)" header
    assert _table(h) == [("Quiz/ Assignment", "assignment", 10.0), ("Project work", "project", 25.0),
                         ("Mid-Semester Examination", "midsem", 25.0), ("Comprehensive Examination", "compre", 40.0)]
