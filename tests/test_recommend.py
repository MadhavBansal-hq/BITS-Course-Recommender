"""
Tests for the local query layer: parsing, handout policy rules, acronym
expansion, and recommendations for the task brief's example queries on the
example CS 2-1 profile. Requires data/processed/ (run the parsers first).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.retrieval.dataset import load
from src.retrieval.engine import Engine, StudentProfile
from src.retrieval.query import parse_query
from src.retrieval.recommend import attendance_rule, makeup_rule, recommend
from src.retrieval.semantic import Matcher

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "handouts.json").exists(),
                                reason="run the ingest parsers first")


@pytest.fixture(scope="module")
def ds():
    return load(ROOT)


@pytest.fixture(scope="module")
def report(ds):
    prof = StudentProfile(**json.loads((ROOT / "examples" / "profile_cs_2-1.json").read_text()))
    return Engine(ds, prof, check_fit=False).report()


@pytest.fixture(scope="module")
def matcher():
    return Matcher()


def test_parse_brief_queries(ds):
    legend = ds.rules["timetable_legend"]
    q = parse_query("Suggest DELs related to AI", legend)
    assert (q.category, q.constraints, q.topic) == ("DEL", [], "AI")
    q = parse_query("OPEL with no attendance requirement", legend)
    assert (q.category, q.constraints, q.topic) == ("OPEL", ["no_attendance"], None)
    q = parse_query("a course with no midsem and lenient makeup", legend)
    assert q.constraints == ["no_midsem", "lenient_makeup"]
    q = parse_query("HUEL that is project-based", legend)
    assert (q.category, q.constraints) == ("HUEL", ["project_based"])
    q = parse_query("DEL on machine learning, no 8am classes, Saturday free", legend)
    assert (q.avoid_hours, q.free_day, q.topic) == ([1], "S", "machine learning")   # from the timetable legend


def test_policy_rules():
    # CS F213: "no marks for attendance" but an 80% lab-attendance rule -> not "none"
    assert attendance_rule("we do not have any marks for attendance in lectures. No makeup request will be "
                           "entertained if the student has not attended at-least 80% lab sessions") == "unknown"
    assert attendance_rule("Attendance is not mandatory for this course.") == "none"
    status, conds = makeup_rule("No make-up for quizzes. Make-up for the mid-semester will be given to genuine cases.")
    assert status == "granted" and conds == ["genuine"]
    assert makeup_rule("No make-up will be given under any circumstances.")[0] == "none"


def test_acronyms_learned_from_the_data(ds, matcher, report):
    recommend(report, ds, "anything", matcher)          # learns acronyms on first use
    assert matcher.expand("AI") == "Artificial Intelligence"


def test_dels_related_to_ai(ds, report, matcher):
    res = recommend(report, ds, "Suggest DELs related to AI", matcher, top=5)
    assert res["results"][0]["course_code"] == "CS F407"          # Artificial Intelligence
    assert all(r["category"] == "DEL" for r in res["results"])


def test_verified_before_unverified(ds, report, matcher):
    res = recommend(report, ds, "OPEL with no midsem", matcher, top=50)
    flags = [bool(r["unverified"]) for r in res["results"] if r["listed_for_first_degree"]]
    assert flags == sorted(flags)                                  # all verified ones first
    assert not flags[0]


def test_huel_candidates_come_from_the_timetables_humanities_areas(ds, report, matcher):
    # timetable V(A)(f): "take a few Humanities (HUM), Humanities and Social Science (HSS) courses as electives"
    assert ds.rules["elective_guidance"]["humanities_areas"] == ["HUM", "HSS"]
    res = recommend(report, ds, "HUEL that is project-based", matcher, top=10)
    assert res["notes"] and res["results"]
    for r in res["results"]:
        assert r["course_code"].split()[0] in ("HUM", "HSS")
        assert "HUEL status could not be verified" in r["category"]


def test_higher_degree_course_of_another_discipline_is_blocked(report):
    blocked = [o for o in report["offered"] if any("higher-degree course of another discipline" in b for b in o["blocked_by"])]
    assert blocked and all(not o["course_code"].startswith("CS G") for o in blocked)
