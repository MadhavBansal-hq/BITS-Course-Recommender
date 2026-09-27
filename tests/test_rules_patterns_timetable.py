"""
Tests for the semester-pattern parser, the programme-rules extractor and the
timetable checks. Expected values were read off the source pages cited.
Requires data/raw/ (gitignored); skipped when missing.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from src.ingest.parse_programme_rules import build
from src.ingest.parse_semester_patterns import parse_semester_patterns
from src.ingest.parse_timetable import parse_timetable
from src.retrieval.timetable import (check_selection, find_schedules, hours_starting_at,
                                     offering_options, parse_slots)

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
pytestmark = pytest.mark.skipif(not (RAW / "bulletin.pdf").exists(), reason="source PDFs not present")


@pytest.fixture(scope="module")
def patterns():
    return parse_semester_patterns(RAW / "bulletin.pdf")


@pytest.fixture(scope="module")
def rules():
    return build(RAW.parents[1])


@pytest.fixture(scope="module")
def sections():
    from dataclasses import asdict
    return [asdict(r) for r in parse_timetable(RAW / "timetable.pdf")[0]]


def _prog(patterns, name):
    (p,) = [p for p in patterns if p.programme == name]
    return p


def test_all_first_degree_patterns_found(patterns):
    # IV-3 .. IV-30: 28 first-degree programmes, one page each.
    assert [p.source_page_label for p in patterns] == [f"IV-{n}" for n in range(3, 31)]


def test_cs_pattern_matches_iv9(patterns):
    cs = _prog(patterns, "B.E. Computer Science")
    assert (cs.discipline_core_units, cs.discipline_core_courses) == (48, 14)
    assert (cs.discipline_elective_units, cs.discipline_elective_courses) == (12, 4)
    named = {(s.year, s.term): [] for s in cs.named}
    for s in cs.named:
        named[(s.year, s.term)].append(s.options)
    assert sorted(o[0] for o in named[(2, "1")]) == ["CS F213", "CS F214", "CS F215", "CS F222", "MATH F211"]
    assert ["ECON F211", "MGTS F211"] in named[(2, "2")]
    assert ["BITS F412", "BITS F421T", "BITS F425T", "BITS F424T"] in named[(4, "2")]
    assert all("CS F320" not in o for opts in named.values() for o in opts)
    slots = [(s.year, s.term, s.kind, s.units) for s in cs.elective_slots]
    assert (2, "1", "humanities", "3(min)") in slots and (4, "1", "open", "6to12") in slots
    assert sum(int(u.split("(")[0]) for _, _, k, u in slots if k == "discipline") == 12


def test_footer_disagreements_are_flagged_not_hidden(patterns):
    rob = _prog(patterns, "B.E. Robotics & Industrial Automation")
    assert any("sum to 21" in u and "footer used" in u for u in rob.unresolved)


def test_category_structure_iv1(rules):
    rows = {r["category"]: (r["units"], r["courses"]) for r in rules["category_structure"]["rows"]}
    assert rows["Humanities Electives"] == ("8", "3")
    assert rows["Open Electives"] == ("15 to 27", "5 to 9")
    assert rules["category_structure"]["source"]["label"] == "IV-1"


def test_humanities_heads_and_gap_are_recorded(rules):
    assert rules["humanities"]["heads"] == ["Languages and Literature", "History and Philosophy",
                                            "Political and Social Sciences", "Fine Arts and Professional Arts"]
    assert rules["humanities"]["pool"]["courses_listed"] is False
    assert rules["gaps"]


def test_regulation_clauses(rules):
    clauses = {c["clause"]: c["text"] for c in rules["regulations"]}
    assert list(clauses) == ["2.04", "2.05", "2.06", "2.07", "2.08"]
    assert "treated as an Open Elective" in clauses["2.05"]
    assert "maximum number of four electives" in clauses["2.08"]


def test_registration_rules_and_legend(rules):
    reg = rules["registration"]
    assert reg["lunch_hours"] == [4, 5, 6] and reg["must_keep_a_lunch_hour_free"]
    assert reg["compre_dates_must_not_clash"] and reg["max_extra_electives"] == "four"
    hours = {int(k): v for k, v in rules["timetable_legend"]["hours"].items()}
    assert sorted(hours) == list(range(1, 11)) and hours[1] == "8-8:50AM"
    assert hours_starting_at(hours, "8") == {1}


def test_equivalent_courses(rules):
    eq = {e["course_code"]: e["equivalents"] for e in rules["equivalent_courses"]}
    assert "IS F213" in eq["CS F213"]


def test_slot_parsing():
    valid = set(range(1, 11))
    assert parse_slots("M W 4 T 10", valid) == {("M", 4), ("W", 4), ("T", 10)}
    assert parse_slots("T 789", valid) == {("T", 7), ("T", 8), ("T", 9)}
    assert parse_slots("MWF 2", valid) == {("M", 2), ("W", 2), ("F", 2)}
    assert parse_slots("Th 34", valid) == {("Th", 3), ("Th", 4)}


def test_combined_tutorial_groups_stay_with_their_lecture(sections):
    mgts = [s for s in sections if s["course_code"] == "MGTS U102"]
    for pick in offering_options(mgts):
        codes = {s["section"] for s in pick}
        lec = next(c for c in codes if c in ("L1", "L2"))
        assert all(c.startswith(lec) for c in codes if "T" in c)


def test_schedules_have_no_clashes(sections, rules):
    wanted = {"MATH F211", "CS F214", "CS F222", "CS F213", "CS F215"}
    offs = defaultdict(list)
    for s in sections:
        if s["course_code"] in wanted and not s["only_for_2026_admissions"]:
            offs[s["course_code"]].append(s)
    valid = {int(h) for h in rules["timetable_legend"]["hours"]}
    lunch = rules["registration"]["lunch_hours"]
    found = find_schedules(dict(offs), valid, lunch)
    assert found
    by = {(s["course_code"], s["section"]): s for s in sections if s["course_code"] in wanted}
    for sched in found:
        chosen = [by[(c, sec)] for c, secs in sched["sections"].items() for sec in secs]
        report = check_selection(chosen, valid, lunch)
        assert not report["class_clashes"] and not report["exam_clashes"] and not report["no_free_lunch_hour_on"]
