"""
Tests for the requirement and eligibility engine on the example CS 2-1
profile. Expected values come from bulletin IV-1, IV-9 (B.E. Computer
Science pattern), IV-110 (CS lists), the timetable and handouts.
Requires data/processed/ built from data/raw/; skipped if missing.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.retrieval.dataset import load
from src.retrieval.engine import Engine, StudentProfile

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "handouts.json").exists(),
                                reason="run the ingest parsers first")


@pytest.fixture(scope="module")
def ds():
    return load(ROOT)


@pytest.fixture(scope="module")
def profile():
    return StudentProfile(**json.loads((ROOT / "examples" / "profile_cs_2-1.json").read_text()))


@pytest.fixture(scope="module")
def report(ds, profile):
    return Engine(ds, profile).report()


def _offered(rep, code):
    return next(o for o in rep["offered"] if o["course_code"] == code)


def test_programmes_map_to_their_course_lists(ds):
    unmapped = [p for p, (h, _) in ds.heading_for.items() if h is None]
    assert unmapped == ["Bachelor of Business Administration (Honours)"]   # no BBA list in IV-106..128
    assert ds.programme_heading("B.E. Electrical & Electronics")[0] == "ELECTRICAL AND ELECTRONICS ENGINEERING"


def test_named_courses(report):
    todo = {tuple(r["options"]): (r["category"], r["status"]) for r in report["named"]
            if r["status"] not in ("completed", "in progress")}
    assert len(todo) == 14
    assert todo[("CS F211",)] == ("CDC", "later")
    assert todo[("ECON F211", "MGTS F211")] == ("named (institutional)", "later")
    in_progress = {r["satisfied_by"] for r in report["named"] if r["status"] == "in progress"}
    assert in_progress == {"MATH F211", "CS F214", "CS F222", "CS F213", "CS F215"}


def test_elective_requirements(report):
    e = report["electives"]
    assert (e["DEL"]["required_units"], e["DEL"]["required_courses"]) == (12, 4)
    assert "IV-9 footer" in e["DEL"]["source"]
    assert (e["HUEL"]["required_units"], e["HUEL"]["required_courses"]) == (8, 3)
    assert (e["OPEL"]["required_units"], e["OPEL"]["required_courses"]) == (15, 5)


def test_cs_f320_is_a_discipline_elective(report):
    assert _offered(report, "CS F320")["category"] == "DEL"


def test_equivalent_courses_count(ds, profile):
    # IS F213 is listed as equivalent to CS F213 (timetable part IX)
    p = replace(profile, current=[c for c in profile.current if c != "CS F213"], completed=profile.completed + ["IS F213"])
    rep = Engine(ds, p).report()
    (oop,) = [r for r in rep["named"] if r["options"] == ["CS F213"]]
    assert oop["status"] == "completed"
    assert "currently registered" in _offered(Engine(ds, profile).report(), "EEE F215")["blocked_by"]


def test_2026_only_offerings(ds, profile):
    code = next(c for c, secs in ds.offerings.items() if all(s["only_for_2026_admissions"] for s in secs))
    assert "only for 2026 admissions" in " ".join(_offered(Engine(ds, profile).report(), code)["blocked_by"])
    assert not any("2026" in b for b in _offered(Engine(ds, replace(profile, admission_year=2026)).report(), code)["blocked_by"])


def test_stated_prerequisite(ds, profile, report):
    # EEE F437's handout: "Pre-requisite of the Course : Electronic Devices (F-214)"
    o = _offered(report, "EEE F437")
    assert o["prerequisite"]["missing"] == ["EEE F214"] and not o["eligible"]
    met = Engine(ds, replace(profile, completed=profile.completed + ["EEE F214"])).report()
    assert _offered(met, "EEE F437")["prerequisite"]["status"] == "met"


def test_unknown_programme_is_an_error(ds, profile):
    with pytest.raises(ValueError):
        Engine(ds, replace(profile, programme="B.E. Imaginary"))
