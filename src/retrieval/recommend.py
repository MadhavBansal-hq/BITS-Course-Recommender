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
- nothing is generated freely: explanations are assembled from fields,
  each with its source.
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
from src.retrieval.timetable import offering_options, parse_slots

PROJECT_KINDS = {"project", "assignment", "presentation"}
PROJECT_THRESHOLD = 30.0   # % of the grade from project/assignment/presentation components


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


def recommend(report: dict, ds, query_text: str, matcher: Matcher, top: int = 10) -> dict:
    legend = ds.rules["timetable_legend"]
    q = parse_query(query_text, legend)
    valid = {int(k) for k in legend["hours"]}
    heads = ds.rules["humanities"]["heads"]
    if not matcher.acronyms:
        matcher.learn_acronyms(list(ds.titles.values()) + [h["description"]["text"] for hs in ds.handouts.values()
                                                           for h in hs if h.get("description")])
    results = []
    for o in report["offered"]:
        if not o["eligible"]:
            continue
        cat = o["category"]
        if q.category == "HUEL":
            # verified HUELs, then courses in the areas timetable V(A)(f)
            # advises as humanities electives (the engine labels them)
            if cat != "HUEL" and not cat.startswith("OPEL or HUEL"):
                continue
        elif q.category and not cat.startswith(q.category):
            continue
        full = next(iter(ds.handouts.get(o["course_code"], [])), None)
        h = None if not full else {
            **full, "complete": full["evaluation_complete"],
            "evaluation": [(c["name"], c["weight_pct"], c["kind"]) for c in full["evaluation"]],
            "attendance_policy": (full["attendance_policy"] or {}).get("text"),
            "makeup_policy": (full["makeup_policy"] or {}).get("text")}
        text = " ".join(filter(None, [o["title"], ((full or {}).get("description") or {}).get("text")]))
        checks = {c: _check(c, o, h, q, valid) for c in q.constraints}
        if any(v == "no" for v, _ in checks.values()):
            continue
        secs = [s for s in ds.offerings.get(o["course_code"], []) if not s["cancelled"]]
        if q.avoid_hours or q.free_day:
            ok = any(not any(hh in q.avoid_hours for _, hh in sl) and not (q.free_day and any(d == q.free_day for d, _ in sl))
                     for sl in (set().union(*(parse_slots(s["days_hours"], valid) for s in pick)) for pick in offering_options(secs)))
            if not ok:
                continue
            checks["time_preferences"] = ("yes", "has sections avoiding "
                                          + ", ".join(filter(None, [f"hours {q.avoid_hours}" if q.avoid_hours else "",
                                                                    legend["days"].get(q.free_day, "") if q.free_day else ""])))
        topic_score, matched = (matcher.score(q.topic, o["title"] or "")[0] * 0.65 +
                                matcher.score(q.topic, text)[0] * 0.35, matcher.score(q.topic, text)[1]) if q.topic else (0.0, [])
        results.append({"o": o, "checks": checks, "topic": topic_score, "matched": matched, })

    def rank(r):
        # verified matches first, then partial ("partly" lenient), then
        # unverified; courses no first-degree list mentions go last
        doubt = sum({"unverified": 1.0, "partly": 0.5}.get(v, 0) for v, _ in r["checks"].values())
        return (not r["o"]["listed_for_first_degree"], doubt, -r["topic"], r["o"]["course_code"])
    results.sort(key=rank)

    out = []
    for r in results[:top]:
        o = r["o"]
        why = [f"{o['category']}: {o['category_basis']}"]
        if q.topic:
            strength = "strong" if r["topic"] >= 0.6 else "partial" if r["topic"] >= 0.3 else "weak"
            via = ", ".join(dict.fromkeys(r["matched"])) or "no word matched"
            why.append(f"{strength} match for '{matcher.expand(q.topic)}' ({r['topic']:.2f}) via: {via}")
        why += [f"{c}: {v} — {reason}" for c, (v, reason) in r["checks"].items()]
        if not o["listed_for_first_degree"]:
            why.append("caveat: not listed for any first-degree programme in the bulletin; it may be restricted "
                       "to other programmes (Regulations 2.07)")
        if o["pattern_slot"]:
            why.append(f"named in your pattern at {o['pattern_slot']}")
        pre = o["prerequisite"]
        why.append(f"prerequisite: {pre['status']}" + (f" ({pre.get('source')})" if pre.get("source") else ""))
        out.append({"course_code": o["course_code"], "title": o["title"], "units": o["units"],
                    "category": o["category"], "topic_score": round(r["topic"], 3),
                    "unverified": [c for c, (v, _) in r["checks"].items() if v in ("unverified", "partly")],
                    "listed_for_first_degree": o["listed_for_first_degree"], "why": why})
    expanded = matcher.expand(q.topic) if q.topic else None
    notes = []
    if q.category == "HUEL":
        g = ds.rules.get("elective_guidance") or {}
        notes.append("No document in the dataset lists humanities electives. Candidates are courses from the areas "
                     f"timetable part V(A)(f) advises as humanities electives ({', '.join(g.get('humanities_areas', []))}); "
                     "confirm their HUEL status before counting on it.")
    return {"query": {**vars(q), "topic_expanded": expanded if expanded != q.topic else None}, "notes": notes,
            "matching": matcher.mode, "results": out, "total_matches": len(results)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Recommend courses for a free-text request")
    ap.add_argument("profile", type=Path)
    ap.add_argument("query")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--report", type=Path, help="cached engine report (created if missing)")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    ds = load(root)
    cache = args.report or root / "data" / "processed" / f"report_{args.profile.stem}.json"
    if cache.exists() and cache.stat().st_mtime > args.profile.stat().st_mtime:
        report = json.loads(cache.read_text(encoding="utf-8"))
    else:
        report = Engine(ds, StudentProfile(**json.loads(args.profile.read_text(encoding="utf-8")))).report()
        cache.write_text(json.dumps(report), encoding="utf-8")
    res = recommend(report, ds, args.query, Matcher(), top=args.top)
    print(f"Query: {res['query']}\nMatching: {res['matching']}\n{res['total_matches']} eligible matches; top {len(res['results'])}:")
    for i, r in enumerate(res["results"], 1):
        print(f"\n{i}. {r['course_code']} {r['title']} ({r['units']} units)")
        for w in r["why"]:
            print(f"   - {w}")


if __name__ == "__main__":
    main()
