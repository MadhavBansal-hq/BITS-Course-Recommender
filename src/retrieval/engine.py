"""
Requirement analysis and eligibility, fully deterministic (no LLM).

Given a student profile, it answers from the processed dataset:
1. named courses (CDCs and institutional courses) from the programme's
   semester-wise pattern: done, overdue, due this semester, due later;
2. discipline, humanities and open elective requirements vs. progress;
3. for every course offered this semester: its category for this student,
   whether they can take it (and why not), handout properties, and whether
   it fits beside the courses they are already taking.

Rules applied, each with its source:
- named courses are compulsory (Regulations 2.05, 2.07);
- DEL requirement: the pattern page footer (else the sum of its DEL slots);
  DEL pool: the programme's DISCIPLINE ELECTIVE list in List of Courses;
- HUEL requirement: bulletin IV-1; the pool is not listed anywhere in the
  dataset, so a course is HUEL only if its handout says so, and a completed
  course counts as HUEL only if the student declares it;
- an elective becomes an Open Elective once DEL and HUEL are accounted for
  (Regulations 2.05); the OPEL requirement is derived as the IV-1
  course-work minimum minus named, DEL and HUEL units, floored at IV-1's
  stated minimum;
- a higher-degree course counts as an open elective unless it is in the DEL
  pool, subject to an AGC CGPA rule whose value is not in the dataset
  (Regulations 2.08);
- equivalent courses (timetable part IX) satisfy each other;
- offerings with com_cod >= 5000 are only for 2026 admissions;
- prerequisites: only what a handout states (decided with the author).
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.retrieval.dataset import Dataset, load
from src.retrieval.timetable import find_schedules, offering_options, parse_slots

CODE_RE = re.compile(r"\b([A-Z]{2,5})\s?([FGCU]\d{3}[A-Z]?)\b")
SHORT_CODE_RE = re.compile(r"\(\s*([FGC])\s*-?\s*(\d{3})\s*\)")


@dataclass
class StudentProfile:
    programme: str                      # as in the semester-wise pattern, e.g. "B.E. Computer Science"
    admission_year: int
    semester: str                       # "2-1" = year 2, first semester
    campus: str = "Pilani"
    completed: list[str] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    completed_categories: dict[str, str] = field(default_factory=dict)  # code -> "HUEL" | "DEL" | "OPEL"
    dual_degree: str | None = None
    minor: str | None = None
    interests: list[str] = field(default_factory=list)


def _units(val) -> int:
    m = re.match(r"(\d+)", str(val or ""))
    return int(m[1]) if m else 0


def _range_min(text: str | None) -> int | None:
    m = re.match(r"(\d+)", text or "")
    return int(m[1]) if m else None


def _term_key(year: int, term: str) -> tuple[int, int]:
    return (year, {"1": 1, "2": 2, "summer": 3}[term])


class Engine:
    def __init__(self, ds: Dataset, profile: StudentProfile):
        self.ds, self.p = ds, profile
        if profile.programme not in ds.patterns:
            raise ValueError(f"unknown programme {profile.programme!r}; known: {sorted(ds.patterns)}")
        self.pattern = ds.patterns[profile.programme]
        self.heading, self.heading_score = ds.programme_heading(profile.programme)
        lists = ds.lists.get(self.heading, {}) if self.heading else {}
        self.core_pool = {c["course_code"] for c in lists.get("core", [])}
        self.del_pool = {c["course_code"] for c in lists.get("discipline_elective", [])}
        self.done = self._expand(profile.completed)
        self.now = self._expand(profile.current)
        y, t = profile.semester.split("-")
        self.term = _term_key(int(y), t)

    def _expand(self, codes) -> set[str]:
        out = set()
        for c in codes:
            out.add(c)
            out |= self.ds.equivalent_to.get(c, set())
        return out

    # 1. named courses -------------------------------------------------------
    def named(self) -> list[dict]:
        rows = []
        for s in self.pattern["named"]:
            opts = s["options"]
            done = next((o for o in opts if o in self.done), None)
            taking = next((o for o in opts if o in self.now), None)
            when = _term_key(s["year"], s["term"])
            status = ("completed" if done else "in progress" if taking else
                      "overdue" if when < self.term else "this semester" if when == self.term else "later")
            rows.append({"options": opts, "titles": [self.ds.titles.get(o) for o in opts],
                         "year": s["year"], "term": s["term"], "units": s["units"],
                         "category": "CDC" if any(o in self.core_pool for o in opts) else "named (institutional)",
                         "status": status, "satisfied_by": done or taking,
                         "source": f"bulletin {self.pattern['source_page_label']} (semester-wise pattern)"})
        return rows

    # 2. elective requirements ----------------------------------------------
    def electives(self, named_rows: list[dict]) -> dict:
        pat, rules = self.pattern, self.ds.rules
        iv1 = {r["category"]: r for r in rules["category_structure"]["rows"]}
        del_units = pat["discipline_elective_units"]
        del_courses = pat["discipline_elective_courses"]
        del_src = f"bulletin {pat['source_page_label']} footer"
        if del_units is None:
            del_units = sum(_units(s["units"]) for s in pat["elective_slots"] if s["kind"] == "discipline") or None
            del_src = f"bulletin {pat['source_page_label']}: sum of discipline-elective slots (no footer)"
        hu = iv1.get("Humanities Electives", {})
        hu_units, hu_courses = _range_min(hu.get("units")), _range_min(hu.get("courses"))

        declared = self.p.completed_categories
        del_done = sorted(c for c in self.done & self.del_pool if c in self.p.completed and declared.get(c, "DEL") == "DEL")
        hu_done = sorted(c for c, k in declared.items() if k == "HUEL" and c in self.p.completed)
        named_codes = {o for r in named_rows for o in r["options"]}
        other = sorted(c for c in self.p.completed if c not in named_codes and c not in del_done and c not in hu_done)

        # OPEL: course-work minimum (IV-1) - named course-work - DEL - HUEL,
        # floored at IV-1's stated Open Elective minimum. PS / thesis slots
        # are not course-work (IV-1 lists them separately).
        cw = iv1.get("Course-work Sub-Total", {})
        cw_units, cw_courses = _range_min(cw.get("units")), _range_min(cw.get("courses"))
        is_ps = lambda r: r["term"] == "summer" or any(o.endswith("T") for o in r["options"]) or _units(r["units"]) >= 9
        named_cw = [r for r in named_rows if not is_ps(r)]
        named_units = sum(_units(r["units"]) for r in named_cw)
        op = iv1.get("Open Electives", {})
        op_min_u, op_min_c = _range_min(op.get("units")), _range_min(op.get("courses"))
        opel_units = opel_courses = None
        if None not in (cw_units, del_units, hu_units, op_min_u):
            opel_units = max(op_min_u, cw_units - named_units - del_units - hu_units)
        if None not in (cw_courses, del_courses, hu_courses, op_min_c):
            opel_courses = max(op_min_c, cw_courses - len(named_cw) - del_courses - hu_courses)
        units_of = lambda codes: sum(_units(self.ds.units.get(c)) for c in codes)
        return {
            "DEL": {"required_units": del_units, "required_courses": del_courses, "source": del_src,
                    "completed": del_done, "completed_units": units_of(del_done),
                    "pool_size": len(self.del_pool), "pool_source": f"bulletin List of Courses: {self.heading}"},
            "HUEL": {"required_units": hu_units, "required_courses": hu_courses,
                     "source": f"bulletin {rules['category_structure']['source']['label']}",
                     "heads": rules["humanities"]["heads"], "completed": hu_done,
                     "completed_units": units_of(hu_done),
                     "note": "The dataset lists no humanities courses; completed ones count only if you "
                             "declare them, and offered ones are HUEL only if their handout says so."},
            "OPEL": {"required_units": opel_units, "required_courses": opel_courses,
                     "method": f"IV-1 course-work minimum ({cw_units} units / {cw_courses} courses) minus named "
                               f"course-work ({named_units} / {len(named_cw)}), DEL and HUEL; at least IV-1's "
                               f"{op.get('units')} units / {op.get('courses')} courses",
                     "rule": "Regulations 2.05: an elective counts as Open once DEL and HUEL are accounted for",
                     "completed_or_unclassified": other},
        }

    # 3. offered courses ------------------------------------------------------
    def _handout(self, code):
        hs = self.ds.handouts.get(code, [])
        return next((h for h in hs if "joined_via" not in h), hs[0] if hs else None)

    def _prereq(self, code, h) -> dict:
        if not h:
            return {"status": "could not be verified", "detail": "no handout for this course"}
        if not h["prerequisites"]:
            return {"status": "could not be verified", "detail": "handout states no prerequisite section"}
        pr = h["prerequisites"][0]
        if pr["kind"] == "none_stated":
            return {"status": "none", "detail": pr["text"][:160], "source": f"handout {h['file']} p{pr['page']}"}
        if pr["kind"] == "soft_recommendation":
            return {"status": "advisory only", "detail": pr["text"][:160], "source": f"handout {h['file']} p{pr['page']}"}
        dept = code.split()[0]
        need = {f"{d} {n}" for d, n in CODE_RE.findall(pr["text"])} | \
               {f"{dept} {a}{n}" for a, n in SHORT_CODE_RE.findall(pr["text"])}
        missing = sorted(c for c in need if c not in self.done)
        return {"status": "met" if not missing else "not met", "requires": sorted(need), "missing": missing,
                "detail": pr["text"][:160], "source": f"handout {h['file']} p{pr['page']}"}

    def _category(self, code, named_codes, h) -> tuple[str, str]:
        if code in named_codes:
            return ("CDC" if code in self.core_pool else "named (institutional)",
                    f"named in {self.pattern['source_page_label']}")
        if code in self.del_pool:
            return "DEL", f"DISCIPLINE ELECTIVE under {self.heading}"
        if h and h.get("humanities_elective_statement"):
            return "HUEL", f"handout {h['file']}: {h['humanities_elective_statement']['text'][:80]}"
        note = "Regulations 2.05: any other elective counts as Open"
        if re.match(r"[A-Z]+ G\d", code):
            note += "; higher-degree course, allowed subject to an AGC CGPA rule not in the dataset (Reg 2.08)"
        return "OPEL (HUEL status could not be verified)", note

    def offered(self, named_rows) -> list[dict]:
        named_codes = {o for r in named_rows if r["status"] != "completed" for o in r["options"]}
        slot_of = {o: f"Y{r['year']} T{r['term']} ({r['status']})" for r in named_rows for o in r["options"]}
        valid = {int(k) for k in self.ds.rules["timetable_legend"]["hours"]}
        lunch = self.ds.rules["registration"]["lunch_hours"]
        cur_offs = {c: self._live(c) for c in self.p.current if self._live(c)}
        base = find_schedules(cur_offs, valid, lunch, limit=40) if cur_offs else [{"sections": {}}]
        base_slots = []
        for sch in base:
            slots = set()
            for c, secs in sch["sections"].items():
                for s in cur_offs[c]:
                    if s["section"] in secs:
                        slots |= parse_slots(s["days_hours"], valid)
            base_slots.append(slots)
        cur_exams = {(w, s[f"{w}_date"], s[f"{w}_session"]): c for c, secs in cur_offs.items()
                     for s in secs[:1] for w in ("midsem", "compre") if s[f"{w}_date"]}
        out = []
        for code in sorted(self.ds.offerings):
            secs = self._live(code)
            h = self._handout(code)
            cat, why = self._category(code, named_codes, h)
            blockers = []
            if code in self.done:
                blockers.append("already completed (or an equivalent)")
            if code in self.now:
                blockers.append("currently registered")
            if not secs:
                every = self.ds.offerings[code]
                blockers.append("only for 2026 admissions (com_cod >= 5000)"
                                if all(s["only_for_2026_admissions"] for s in every) else "no open section")
            pre = self._prereq(code, h)
            if pre["status"] == "not met":
                blockers.append(f"prerequisite not met: {', '.join(pre['missing'])}")
            fits, exam_clash = None, []
            if secs and not blockers:
                opts = [set().union(*(parse_slots(s["days_hours"], valid) for s in o)) for o in offering_options(secs)]
                fits = any(not (o & b) for o in opts for b in base_slots)
                exam_clash = [f"{w} {d} {sess} with {cur_exams[(w, d, sess)]}"
                              for w in ("midsem", "compre") if (d := secs[0][f"{w}_date"])
                              and (sess := secs[0][f"{w}_session"]) and (w, d, sess) in cur_exams]
            out.append({
                "course_code": code, "title": self.ds.titles.get(code), "units": secs[0]["credit_u"] if secs else None,
                "category": cat, "category_basis": why, "pattern_slot": slot_of.get(code),
                "eligible": not blockers, "blocked_by": blockers,
                "prerequisite": pre, "fits_current_timetable": fits, "exam_clashes": exam_clash,
                "handout": None if not h else {
                    "file": h["file"], "joined_via": h.get("joined_via"),
                    "has_midsem": h["has_midsem"], "has_midsem_basis": h["has_midsem_basis"],
                    "has_compre": h["has_compre"], "open_book_components": h["open_book_components"],
                    "evaluation": [(c["name"], c["weight_pct"]) for c in h["evaluation"]],
                    "makeup_policy": (h["makeup_policy"] or {}).get("text"),
                    "attendance_policy": (h["attendance_policy"] or {}).get("text")},
            })
        return out

    def _live(self, code):
        adm2026 = self.p.admission_year >= 2026
        return [s for s in self.ds.offerings.get(code, [])
                if not s["cancelled"] and (adm2026 or not s["only_for_2026_admissions"])]

    def report(self) -> dict:
        named = self.named()
        return {"profile": asdict(self.p),
                "programme": {"name": self.p.programme, "pattern": self.pattern["source_page_label"],
                              "list_heading": self.heading, "heading_match": self.heading_score,
                              "discipline_core": [self.pattern["discipline_core_units"], self.pattern["discipline_core_courses"]],
                              "unresolved": self.pattern["unresolved"]},
                "named": named, "electives": self.electives(named), "offered": self.offered(named),
                "notes": self.ds.rules.get("gaps", [])}


def main() -> None:
    ap = argparse.ArgumentParser(description="Requirement and eligibility report for one student profile")
    ap.add_argument("profile", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    prof = StudentProfile(**json.loads(args.profile.read_text(encoding="utf-8")))
    rep = Engine(load(root), prof).report()
    if args.out:
        args.out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    todo = [r for r in rep["named"] if r["status"] in ("overdue", "this semester", "later")]
    print(f"{prof.programme} ({rep['programme']['pattern']}, list '{rep['programme']['list_heading']}'), semester {prof.semester}")
    print(f"Named courses still to do: {len(todo)}")
    for r in todo:
        print(f"  [{r['status']:>13}] {' or '.join(r['options'])}  {r['category']}  (Y{r['year']} T{r['term']})")
    for k, e in rep["electives"].items():
        done = e.get("completed_units", "?")
        print(f"{k}: need {e['required_units']} units / {e['required_courses']} courses; done {done} units")
    ok = [o for o in rep["offered"] if o["eligible"]]
    print(f"Offered this semester: {len(rep['offered'])} courses, {len(ok)} eligible for you")


if __name__ == "__main__":
    main()
