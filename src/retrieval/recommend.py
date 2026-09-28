"""
Answer a free-text request for one student: parse it (query.py), filter the
engine's eligible courses by category and constraints, rank by topic match
(semantic.py), and explain each result from the report's own fields.

Honesty rules:
- a course that verifiably breaks a constraint is dropped; one whose
  property could not be verified is kept, ranked after verified matches,
  and labelled "could not be verified";
- the dataset lists no humanities courses, so a HUEL request returns
  verified HUELs (handout says so) and courses from the areas the timetable
  itself advises as humanities electives (part V(A)(f): HUM, HSS), labelled
  unverified. (Word-vector resemblance of department names to the four
  IV-2 heads was tried and rejected: Physics scored 0.50, English 0.11.)
- nothing is generated freely: each result is the four things the brief asks
  for (requirement satisfied, eligibility, relevant course properties, why
  it matches), assembled from fields with their sources;
- time preferences (avoided hours, a free weekday, compactness) are checked
  jointly with the student's current courses, and a feasible section is
  named: a course is not rejected because one section clashes if another
  does not.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from src.retrieval.dataset import load
from src.retrieval.engine import Engine, StudentProfile
from src.retrieval.query import parse_query
from src.retrieval.semantic import Matcher
from src.retrieval.timetable import blocked_by_preferences, find_schedules, offering_options, parse_slots

PROJECT_KINDS = {"project", "assignment", "presentation"}
PROJECT_THRESHOLD = 30.0   # % of the grade from project/assignment/presentation components (our threshold, shown in output)
MIN_TOPIC = 0.25           # below this a course does not match the requested topic at all
NEAR_MISS = 0.6            # a strong topic match excluded by a constraint is reported as a near miss


NO_ATTENDANCE_RE = re.compile(r"no\s+(marks|weightage|credit)\s+for\s+attendance|attendance\s+(is\s+)?not\s+(mandatory|compulsory|required)|do\s+not\s+have\s+any\s+marks\s+for\s+attendance")
ATTENDANCE_REQUIRED_RE = re.compile(r"mandatory|compulsory|must\s+attend|required|expected\s+to\s+attend|\d{2}\s*%|debar|minimum\s+attendance")
MAKEUP_GRANTED_RE = re.compile(r"make[\s\-]?ups?\s+(test|exam\w*\s+)?(will|shall|may|can|would)\s+(only\s+)?be\s+(given|granted|allowed|conducted|held|considered|entertained)")
MAKEUP_NONE_RE = re.compile(r"\bno\s+make[\s\-]?ups?\b(?![^.]{0,40}\b(quiz|quizzes|surprise|tutorial|lab|class\s*test|assignment))")
MAKEUP_CONDITIONS = {"genuine": r"genuine", "medical": r"medical|hospital|illness|sick", "prior permission": r"prior\s+permission|prior\s+intimation|in\s+advance",
                     "certificate": r"certificate|document", "exceptional cases only": r"exceptional|emergenc", "application": r"application|request"}


def attendance_rule(text: str | None) -> str:
    """'none' only when the handout says so and says nothing requiring
    attendance (CS F213 has "no marks for attendance" and an 80% lab rule)."""
    if not text:
        return "unknown"
    t = text.lower()
    none = bool(NO_ATTENDANCE_RE.search(t))
    req = bool(ATTENDANCE_REQUIRED_RE.search(NO_ATTENDANCE_RE.sub(" ", t)))   # "not mandatory" is not "mandatory"
    return "none" if none and not req else "required" if req and not none else "unknown"


def makeup_rule(text: str | None) -> tuple[str, list[str]]:
    """('granted' | 'none' | 'unknown', conditions attached). "No make-up
    for quizzes" is not "no make-up"; almost every handout attaches some
    condition, so lenience is how few conditions there are."""
    if not text:
        return "unknown", []
    t = text.lower()
    conds = [name for name, pat in MAKEUP_CONDITIONS.items() if re.search(pat, t)]
    none_hit = MAKEUP_NONE_RE.search(t)
    # drop "no make-up ..." sentences before looking for a grant: "No make-up
    # will be given" contains "make-up will be given"
    rest = re.sub(r"\bno\s+make[\s\-]?ups?\b(?![^.]{0,40}\b(quiz|quizzes|surprise|tutorial|lab|class\s*test|assignment))[^.]*\.?", " ", t)
    if MAKEUP_GRANTED_RE.search(rest) or (conds and not none_hit):
        return "granted", conds
    if none_hit:
        return "none", conds
    return "unknown", conds


def _check(name, o, h, q, valid):
    """('yes' | 'no' | 'unverified', reason) for one constraint on one course."""
    if name == "no_midsem":
        v = h and h["has_midsem"]
        return ("yes", h["has_midsem_basis"]) if v is False else ("no", "has a midsem") if v else ("unverified", "midsem could not be verified")
    if name == "no_compre":
        v = h and h["has_compre"]
        return ("yes", h["has_compre_basis"]) if v is False else ("no", "has a compre") if v else ("unverified", "compre could not be verified")
    if name == "no_attendance":
        r = attendance_rule(h and h["attendance_policy"])
        return {"none": ("yes", "handout: no attendance requirement"), "required": ("no", "attendance required")}.get(
            r, ("unverified", "attendance rule could not be verified"))
    if name == "lenient_makeup":
        r, conds = makeup_rule(h and h["makeup_policy"])
        if r == "none":
            return "no", "handout: no make-up"
        if r == "granted":
            return ("yes" if len(conds) <= 1 else "partly",
                    "make-up granted" + (f"; conditions: {', '.join(conds)}" if conds else " with no stated conditions"))
        return "unverified", "make-up policy could not be verified"
    if name == "open_book":
        if h and h["open_book_components"]:
            return "yes", f"open book: {', '.join(h['open_book_components'][:3])}"
        return ("no", "no open-book component") if h and h["evaluation"] else ("unverified", "no evaluation table read")
    if name == "project_based":
        if not h or not h["evaluation"]:
            return "unverified", "no evaluation table read"
        w = sum(c[1] or 0 for c in h["evaluation"] if c[2] in PROJECT_KINDS)
        if w >= PROJECT_THRESHOLD:
            return "yes", f"{w:g}% of the grade from projects/assignments/presentations"
        return ("no", f"only {w:g}% from projects") if h.get("complete") else ("unverified", f"{w:g}% from projects; table incomplete")
    if name == "fits_timetable":
        f = o["fits_current_timetable"]
        return ("yes", "a clash-free set of sections exists beside your current courses") if f else \
            ("no", "clashes with your current courses") if f is False else ("unverified", "timetable fit not determined")
    return "unverified", name


def _handout(ds, code):
    full = next(iter(ds.handouts.get(code, [])), None)
    if not full:
        return None
    return {**full, "complete": full["evaluation_complete"],
            "evaluation": [(c["name"], c["weight_pct"], c["kind"]) for c in full["evaluation"]],
            "attendance_policy": (full["attendance_policy"] or {}).get("text"),
            "makeup_policy": (full["makeup_policy"] or {}).get("text")}


def _live(ds, code, admission_year):
    return [s for s in ds.offerings.get(code, [])
            if not s["cancelled"] and (admission_year >= 2026 or not s["only_for_2026_admissions"])]


def _requirement(o, report) -> str:
    cat, e = o["category"], report["electives"]
    if cat in ("CDC", "named (institutional)"):
        return f"{cat}: named in your semester-wise pattern at {o['pattern_slot']}"
    if cat == "DEL":
        d = e["DEL"]
        return (f"DEL: counts toward {d['required_units']} units / {d['required_courses']} courses "
                f"({d['completed_units']} units done; {d['source']})")
    h, op = e["HUEL"], e["OPEL"]
    if cat == "HUEL" or cat.startswith("OPEL or HUEL"):
        return (f"{'HUEL' if cat == 'HUEL' else 'OPEL, or HUEL if confirmed'}: HUEL needs {h['required_units']} units / "
                f"{h['required_courses']} courses ({h['completed_units']} done, {h['source']}); {o['category_basis']}")
    return (f"OPEL: counts toward {op['required_units']} units / {op['required_courses']} courses "
            f"(bulletin IV-1 and Regulations 2.05)")


def _eligibility(o) -> str:
    pre = o["prerequisite"]
    text = f"eligible; prerequisite: {pre['status']}"
    if pre.get("source"):
        text += f" ({pre['source']})"
    if not o["listed_for_first_degree"]:
        text += "; caveat: not listed for any first-degree programme in the bulletin, may be restricted (Reg 2.07)"
    return text


def _properties(h) -> list[str]:
    if not h:
        return ["no handout in the dataset: midsem, compre, attendance and make-up could not be verified"]
    yn = lambda v: "yes" if v else "no" if v is False else "could not be verified"
    props = [f"midsem: {yn(h['has_midsem'])}", f"compre: {yn(h['has_compre'])}"]
    att = attendance_rule(h["attendance_policy"])
    props.append("attendance: " + {"none": "no requirement stated", "required": "required",
                                   "unknown": "could not be verified"}[att])
    mk, conds = makeup_rule(h["makeup_policy"])
    props.append("make-up: " + {"granted": "granted" + (f" ({', '.join(conds)})" if conds else ""),
                                "none": "none", "unknown": "could not be verified"}[mk])
    if h["open_book_components"]:
        props.append(f"open book: {', '.join(h['open_book_components'][:3])}")
    if h["evaluation"]:
        w = sum(c[1] or 0 for c in h["evaluation"] if c[2] in PROJECT_KINDS)
        props.append(f"projects/assignments/presentations: {w:g}% of the grade")
    if h.get("instructor_in_charge"):
        props.append(f"instructor-in-charge: {h['instructor_in_charge']}")
    props.append(f"source: handout {h['file']}")
    return props


def recommend(report: dict, ds, query_text: str, matcher: Matcher, top: int = 10) -> dict:
    legend = ds.rules["timetable_legend"]
    q = parse_query(query_text, legend)
    valid = {int(k) for k in legend["hours"]}
    lunch = ds.rules["registration"]["lunch_hours"]
    if not matcher.acronyms:
        matcher.learn_acronyms(list(ds.titles.values()) + [h["description"]["text"] for hs in ds.handouts.values()
                                                           for h in hs if h.get("description")])
    notes = list(report.get("notes", [])[:1]) if not report["offered"] or not any(o["eligible"] for o in report["offered"]) else []
    if q.category == "HUEL":
        g = ds.rules.get("elective_guidance") or {}
        notes.append("No document in the dataset lists humanities electives. Candidates are courses from the areas "
                     f"timetable part V(A)(f) advises as humanities electives ({', '.join(g.get('humanities_areas', []))}); "
                     "confirm their HUEL status before counting on it.")

    adm = report["profile"]["admission_year"]
    cur = {c: _live(ds, c, adm) for c in report["profile"]["current"] if _live(ds, c, adm)}
    compact = "compact" in q.constraints
    time_prefs = bool(q.avoid_hours or q.free_day or compact)
    avoid = set(q.avoid_hours)
    joint = bool(cur)
    if time_prefs and cur and not find_schedules(cur, valid, lunch, avoid, q.free_day, limit=1, max_nodes=50_000):
        joint = False
        blocked = {c: w.replace(f"on {q.free_day}", f"on {legend['days'].get(q.free_day, q.free_day)}")
                   for c, w in blocked_by_preferences(cur, valid, avoid, q.free_day).items()}
        notes.append("Your current courses cannot meet these time preferences"
                     + (f" ({'; '.join(f'{c}: {w}' for c, w in blocked.items())})" if blocked else "")
                     + "; each suggested course is checked on its own sections instead.")

    candidates, near_misses = [], []
    for o in report["offered"]:
        if not o["eligible"]:
            continue
        cat = o["category"]
        if q.category == "HUEL":
            if cat != "HUEL" and not cat.startswith("OPEL or HUEL"):
                continue
        elif q.category and not cat.startswith(q.category):
            continue
        h = _handout(ds, o["course_code"])
        checks = {c: _check(c, o, h, q, valid) for c in q.constraints if c != "compact"}
        text = " ".join(filter(None, [o["title"], ((h or {}).get("description") or {}).get("text")]))
        topic, matched = 0.0, []
        if q.topic:
            t_title, _ = matcher.score(q.topic, o["title"] or "")
            t_all, matched = matcher.score(q.topic, text)
            topic = 0.65 * t_title + 0.35 * t_all
        failed = [f"{c.replace('_', ' ')}: {reason}" for c, (v, reason) in checks.items() if v == "no"]
        if failed:
            if q.topic and topic >= NEAR_MISS:
                near_misses.append((topic, f"{o['course_code']} {o['title']} matches '{matcher.expand(q.topic)}' "
                                           f"({topic:.2f}) but is excluded: {'; '.join(failed)}"))
            continue
        if q.topic and topic < MIN_TOPIC:
            continue
        candidates.append({"o": o, "h": h, "checks": checks, "topic": topic, "matched": matched})

    def doubt(r):
        return sum({"unverified": 1.0, "partly": 0.5}.get(v, 0) for v, _ in r["checks"].values())
    candidates.sort(key=lambda r: (not r["o"]["listed_for_first_degree"], doubt(r), -r["topic"], r["o"]["course_code"]))

    picked = []
    for r in candidates:
        if len(picked) >= (3 * top if compact else top):
            break
        o, code = r["o"], r["o"]["course_code"]
        r["schedule"] = None
        if time_prefs or "fits_timetable" in q.constraints:
            secs = _live(ds, code, adm)
            if joint:
                found = find_schedules({**cur, code: secs}, valid, lunch, avoid, q.free_day,
                                       limit=3 if compact else 1, max_nodes=20_000)
                if not found:
                    continue
                r["schedule"] = found[0]
            else:
                ok = [pick for pick in offering_options(secs)
                      if not any(hh in avoid or (q.free_day and d == q.free_day)
                                 for d, hh in set().union(*(parse_slots(s["days_hours"], valid) for s in pick)))]
                if not ok:
                    continue
                r["schedule"] = {"sections": {code: [s["section"] for s in ok[0]]}, "days": None, "gaps": None}
        picked.append(r)
    if compact:
        picked.sort(key=lambda r: (doubt(r), (r["schedule"] or {}).get("gaps") or 0, -r["topic"]))
    picked = picked[:top]

    results = []
    for r in picked:
        o = r["o"]
        why = []
        if q.topic:
            strength = "strong" if r["topic"] >= 0.6 else "partial" if r["topic"] >= 0.3 else "weak"
            via = ", ".join(dict.fromkeys(r["matched"])) or "no word matched"
            why.append(f"{strength} match for '{matcher.expand(q.topic)}' ({r['topic']:.2f}) via: {via}")
        why += [f"{c.replace('_', ' ')}: {v} ({reason})" for c, (v, reason) in r["checks"].items()]
        sched, secs = r["schedule"], None
        if sched:
            secs = sched["sections"].get(o["course_code"])
            if sched.get("days") is not None:
                why.append(f"clash-free with your current courses using sections {', '.join(secs)}: "
                           f"{sched['days']} teaching days, {sched['gaps']} free hours between classes")
            else:
                why.append(f"its sections {', '.join(secs)} meet your time preferences")
        elif o.get("suggested_sections") and o["fits_current_timetable"]:
            secs = o["suggested_sections"]
            why.append(f"fits beside your current courses using sections {', '.join(secs)}")
        if not why:
            why.append("meets the requested category")
        results.append({"course_code": o["course_code"], "title": o["title"], "units": o["units"],
                        "category": o["category"], "requirement": _requirement(o, report),
                        "eligibility": _eligibility(o), "properties": _properties(r["h"]), "why": why,
                        "sections": secs, "topic_score": round(r["topic"], 3),
                        "unverified": [c for c, (v, _) in r["checks"].items() if v in ("unverified", "partly")],
                        "listed_for_first_degree": o["listed_for_first_degree"]})
    if near_misses and len(results) < top:
        notes += [n for _, n in sorted(near_misses, reverse=True)[:3]]
    if q.topic and not results:
        notes.append(f"No eligible course matches '{matcher.expand(q.topic)}' and meets every constraint.")
    expanded = matcher.expand(q.topic) if q.topic else None
    return {"query": {**vars(q), "topic_expanded": expanded if expanded != q.topic else None}, "notes": notes,
            "matching": matcher.mode, "results": results, "total_matches": len(candidates)}


def load_report(root: Path, profile_path: Path, ds) -> dict:
    """Engine report for a profile file, cached in data/processed until the
    profile or the processed data changes."""
    cache = root / "data" / "processed" / f"report_{profile_path.stem}.json"
    newest_input = max([profile_path.stat().st_mtime] + [f.stat().st_mtime for f in (root / "data" / "processed").glob("*.json")
                                                          if not f.name.startswith("report_")])
    if cache.exists() and cache.stat().st_mtime > newest_input:
        return json.loads(cache.read_text(encoding="utf-8"))
    report = Engine(ds, StudentProfile(**json.loads(profile_path.read_text(encoding="utf-8")))).report()
    cache.write_text(json.dumps(report), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Recommend courses for a free-text request")
    ap.add_argument("profile", type=Path)
    ap.add_argument("query")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    ds = load(root)
    res = recommend(load_report(root, args.profile, ds), ds, args.query, Matcher(), top=args.top)
    q = res["query"]
    print(f"Understood: category={q['category']} constraints={q['constraints']} avoid_hours={q['avoid_hours']} "
          f"free_day={q['free_day']} topic={q['topic_expanded'] or q['topic']!r}   [matching: {res['matching']}]")
    for n in res["notes"]:
        print(f"Note: {n}")
    print(f"{res['total_matches']} eligible matches; top {len(res['results'])}:")
    for i, r in enumerate(res["results"], 1):
        print(f"\n{i}. {r['course_code']} {r['title']} ({r['units']} units)")
        print(f"   Requirement: {r['requirement']}")
        print(f"   Eligibility: {r['eligibility']}")
        print(f"   Properties:  {'; '.join(r['properties'])}")
        print(f"   Why:         {'; '.join(r['why'])}")


if __name__ == "__main__":
    main()
