"""
Merge the parser outputs into one consistent record per course
(data/processed/courses.json) and validate them (validation.json).

Each field carries where it came from, and handout-derived properties carry
a confidence: "extracted" when the source states it, "unverified" when it
does not (the dashboard then says "could not be verified").

Validation (task brief: "validate extracted course codes, prerequisites,
categories and programme requirements before using them"):
- course codes: format, and codes named in a semester pattern that appear
  in no course list and no timetable;
- categories: a code listed as both core and discipline elective under the
  same heading;
- units: bulletin and timetable disagree;
- prerequisites: codes a handout names that exist nowhere in the data;
- programme requirements: pattern footers vs. elective slots, missing
  footers, programmes with no course list;
- joins: offered courses without a handout, handout files naming another
  course.
Failures are reported, not silently fixed.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

CODE_FORMAT = re.compile(r"^[A-Z]{2,6} [A-Z]?\d{3}[A-Z]?T?(-\d)?$")


def build(root: Path) -> tuple[list[dict], dict]:
    p = root / "data" / "processed"
    read = lambda n: json.loads((p / n).read_text(encoding="utf-8"))
    bulletin, tt, handouts = read("bulletin_courses.json"), read("timetable.json"), read("handouts.json")
    patterns, rules = read("semester_patterns.json"), read("programme_rules.json")
    depts = (rules.get("department_names") or {}).get("names", {})
    courses: dict[str, dict] = defaultdict(lambda: {"listings": [], "named_in": [], "offerings": [], "handouts": []})

    for b in bulletin:
        if b["bulletin_section"] != "list_of_courses":
            continue
        c = courses[b["course_code"]]
        c.setdefault("title", {"value": b["title"], "source": f"bulletin {b['source_page_label']}"})
        c.setdefault("units", {"value": {"l": b["credit_l"], "p": b["credit_p"], "u": b["credit_u"]},
                               "source": f"bulletin {b['source_page_label']}"})
        c["listings"].append({"list_heading": b["list_heading"], "category": b["category"],
                              "alternative_to": b["alternative_to"], "source": f"bulletin {b['source_page_label']}"})
    for pat in patterns:
        for s in pat["named"]:
            for o in s["options"]:
                courses[o]["named_in"].append({"programme": pat["programme"], "year": s["year"], "term": s["term"],
                                               "alternatives": [x for x in s["options"] if x != o],
                                               "source": f"bulletin {pat['source_page_label']}"})
    by_off = defaultdict(list)
    for s in tt:
        by_off[(s["course_code"], s["com_cod"])].append(s)
    for (code, com), secs in by_off.items():
        c = courses[code]
        c.setdefault("title", {"value": secs[0]["title"], "source": f"timetable p{secs[0]['source_page']}"})
        c.setdefault("units", {"value": {"l": secs[0]["credit_l"], "p": secs[0]["credit_p"], "u": secs[0]["credit_u"]},
                               "source": f"timetable p{secs[0]['source_page']}"})
        c["offerings"].append({"com_cod": com, "sections": [x["section"] for x in secs],
                               "only_for_2026_admissions": secs[0]["only_for_2026_admissions"],
                               "midsem": " ".join(filter(None, [secs[0]["midsem_date"], secs[0]["midsem_session"]])) or None,
                               "compre": " ".join(filter(None, [secs[0]["compre_date"], secs[0]["compre_session"]])) or None,
                               "source": f"timetable p{secs[0]['source_page']}"})
    for h in handouts:
        conf = lambda v: "extracted" if v is not None else "unverified"
        courses[h["course_code"]]["handouts"].append({
            "file": h["file"], "code_in_text": h["course_code_in_text"], "instructor": h["instructor_in_charge"],
            "has_midsem": {"value": h["has_midsem"], "basis": h["has_midsem_basis"], "confidence": conf(h["has_midsem"])},
            "has_compre": {"value": h["has_compre"], "basis": h["has_compre_basis"], "confidence": conf(h["has_compre"])},
            "evaluation": {"components": h["evaluation"], "complete": h["evaluation_complete"], "page": h["evaluation_page"],
                           "confidence": "extracted" if h["evaluation_complete"] else "unverified"},
            "attendance_policy": h["attendance_policy"], "makeup_policy": h["makeup_policy"],
            "prerequisites": h["prerequisites"], "topics": (h.get("description") or {}).get("text"),
            "text_layer": h["text_layer"]})

    out = []
    for code in sorted(courses):
        c = courses[code]
        out.append({"course_code": code, "department": depts.get(code.split()[0]), **c})

    # ---- validation
    listed = {b["course_code"] for b in bulletin if b["bulletin_section"] == "list_of_courses"}
    offered = {s["course_code"] for s in tt}
    known = set(courses)
    v = {
        "malformed_codes": sorted(c for c in known if not CODE_FORMAT.match(c)),
        "pattern_codes_not_in_any_list_or_timetable": sorted({o for pat in patterns for s in pat["named"] for o in s["options"]}
                                                             - listed - offered),
        "core_and_elective_in_same_list": sorted({f"{b['course_code']} ({b['list_heading']})" for b in bulletin
                                                  if b["bulletin_section"] == "list_of_courses" and b["category"] == "core"}
                                                 & {f"{b['course_code']} ({b['list_heading']})" for b in bulletin
                                                    if b["bulletin_section"] == "list_of_courses" and b["category"] == "discipline_elective"}),
        "units_disagree_bulletin_vs_timetable": sorted(
            f"{c['course_code']}: bulletin {c['units']['value']['u']}, timetable {tt_u}"
            for c in out if c.get("units") and c["units"]["source"].startswith("bulletin")
            for tt_u in {s["credit_u"] for s in tt if s["course_code"] == c["course_code"]}
            if tt_u not in ("-", c["units"]["value"]["u"]) and c["units"]["value"]["u"] not in (None, "-")),
        "prerequisite_codes_unknown": sorted({f"{h['course_code']} -> {pc}" for h in handouts for p in h["prerequisites"]
                                              for pc in p["course_codes"] if pc not in known}),
        "programme_requirement_issues": [f"{pat['programme']} ({pat['source_page_label']}): {u}"
                                         for pat in patterns for u in pat["unresolved"] if "footer" in u],
        "offered_without_handout": sorted(offered - {h["course_code"] for h in handouts}),
        "handout_file_names_another_course": sorted(u for h in handouts for u in h["unresolved"] if u.startswith("file name")),
        "handouts_from_another_campus": sorted(f"{h['file']} ({h['campus']})" for h in handouts
                                               if h.get("campus") and h["campus"] != (rules.get("data_campus") or {}).get("campus")),
    }
    # Anti-fabrication checks on handout evaluation tables.
    # 1. grounding: every weight cell must appear, as printed, in the handout
    ungrounded = []
    for h in handouts:
        f = root / "data" / "raw" / "handouts" / h["file"]
        if not h["evaluation"] or not f.exists():
            continue
        import subprocess
        text = " ".join(subprocess.run(["pdftotext", "-layout", str(f), "-"], capture_output=True, text=True).stdout.split())
        for c in h["evaluation"]:
            if c.get("weight_raw") and " ".join(c["weight_raw"].split()) not in text:
                ungrounded.append(f"{h['file']}: '{c['weight_raw']}' ({c['name'][:30]}) not found in the source")
    v["evaluation_weights_not_in_source"] = ungrounded
    # 3. every evaluation scheme not fully read, with the reason: a weight
    #    column summing to ~100 exists (parser failure) or not (irregular or
    #    incomplete scheme in the handout). None of these ever yields "no midsem".
    from src.ingest.parse_handouts import _lines, _weight_column
    not_read = []
    for h in handouts:
        f = root / "data" / "raw" / "handouts" / h["file"]
        if h["evaluation_complete"] or not f.exists():
            continue
        if not h["text_layer"]:
            not_read.append(f"{h['file']}: no text layer (scanned)")
            continue
        L = [l for _, l in _lines(f)]
        col = any(_weight_column(L[i:i + 40]) is not None for i, l in enumerate(L)
                  if re.search(r"evaluation|weightage|\bwt\b", l, re.I))
        read = f"{h['weight_total']:g}% read" if h["evaluation"] else "nothing read"
        not_read.append(f"{h['file']}: {read}; " + ("PARSER FAILURE: a weight column summing to ~100% exists"
                                                     if col else "no weight column summing to ~100% in the text"))
    v["evaluation_not_fully_read"] = not_read
    # 2. cross-source: handout midsem verdict vs. the timetable's midsem slot
    tt_mid = defaultdict(set)
    for s_ in tt:
        tt_mid[s_["course_code"]].add(bool(s_["midsem_date"]))
    v["handout_says_no_midsem_but_timetable_schedules_one"] = sorted(
        f"{h['course_code']} ({h['file']})" for h in handouts
        if h["has_midsem"] is False and True in tt_mid.get(h["course_code"], set()))
    v["handout_lists_midsem_but_timetable_has_none"] = sorted(
        f"{h['course_code']} ({h['file']})" for h in handouts
        if h["has_midsem"] and tt_mid.get(h["course_code"]) == {False})
    summary = {k: len(x) for k, x in v.items()}
    return out, {"summary": summary, "details": v}


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    courses, validation = build(root)
    p = root / "data" / "processed"
    (p / "courses.json").write_text(json.dumps(courses, indent=2), encoding="utf-8")
    (p / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    print(f"{len(courses)} courses -> courses.json; validation.json: {validation['summary']}")


if __name__ == "__main__":
    main()
