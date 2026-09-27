"""
Deterministic timetable checks over data/processed/timetable.json:
slot parsing, class and exam clashes, the lunch-hour rule, preference
filters (avoid given hours, keep a weekday free), and a search for
clash-free section combinations.

Slots are "days then hours" groups: "M W 4 T 10" = Mon & Wed hour 4, Tue
hour 10. Hours can be run together ("T 789" = Tue 7, 8, 9). The valid hour
numbers, their clock times (hour 1 = 8-8:50AM) and the lunch hours all come
from the timetable itself via programme_rules.json, not from this code: a
token is one hour if it is a valid hour number, otherwise its digits are
separate hours ("34" = 3, 4).
"""
from __future__ import annotations

import itertools
import re
from collections import defaultdict

DAY_RE = re.compile(r"Th|M|T|W|F|S")


def parse_slots(days_hours: str | None, valid_hours: set[int]) -> set[tuple[str, int]]:
    slots, days, prev_digit = set(), [], False
    for tok in (days_hours or "").split():
        if tok.isdigit():
            hs = [int(tok)] if int(tok) in valid_hours else [int(c) for c in tok]
            slots |= {(d, h) for d in days for h in hs if h in valid_hours}
            prev_digit = True
        else:
            if prev_digit:
                days = []
            days += DAY_RE.findall(tok)
            prev_digit = False
    return slots


def hours_starting_at(hours: dict[int, str], clock: str) -> set[int]:
    """Hour numbers whose printed time starts at `clock`, e.g. "8" -> {1}."""
    return {h for h, t in hours.items() if t.split("-")[0].split(":")[0] == clock}


def section_kind(code: str) -> str:
    return "T" if re.fullmatch(r"L\d+T\d+", code) else code[0]


def offering_options(sections: list[dict]) -> list[list[dict]]:
    """All valid picks for one offering: one section of each kind it has
    (lecture, tutorial, practical); combined codes like L1T3 are tutorial
    groups of lecture L1 and must go with it. Cancelled sections are out."""
    live = [s for s in sections if not s.get("cancelled")]
    by_kind = defaultdict(list)
    for s in live:
        by_kind[section_kind(s["section"])].append(s)
    picks = []
    for combo in itertools.product(*by_kind.values()):
        lec = next((s["section"] for s in combo if section_kind(s["section"]) == "L"), None)
        tut = next((s["section"] for s in combo if re.fullmatch(r"L\d+T\d+", s["section"])), None)
        if tut and lec and not tut.startswith(lec + "T"):
            continue
        picks.append(list(combo))
    return picks


def exam_key(s: dict, which: str):
    d, sess = s.get(f"{which}_date"), s.get(f"{which}_session")
    return (d, sess) if d and sess else None


def check_selection(selected: list[dict], valid_hours: set[int], lunch_hours: list[int] | None) -> dict:
    """Class clashes, exam clashes and lunch-hour violations for a set of sections."""
    occupied = defaultdict(list)
    for s in selected:
        for slot in parse_slots(s.get("days_hours"), valid_hours):
            occupied[slot].append(f"{s['course_code']} {s['section']}")
    class_clashes = {f"{d} {h}": who for (d, h), who in occupied.items() if len(set(w.split()[0] + w.split()[1] for w in who)) > 1}
    exams = defaultdict(set)
    for s in selected:
        for which in ("midsem", "compre"):
            if (k := exam_key(s, which)):
                exams[(which, k)].add(s["course_code"])
    exam_clashes = {f"{w} {d} {sess}": sorted(c) for (w, (d, sess)), c in exams.items() if len(c) > 1}
    lunch_problems = []
    if lunch_hours:
        for day in sorted({d for d, _ in occupied}):
            if all((day, h) in occupied for h in lunch_hours):
                lunch_problems.append(day)
    return {"class_clashes": class_clashes, "exam_clashes": exam_clashes, "no_free_lunch_hour_on": lunch_problems}


def find_schedules(offerings: dict[str, list[dict]], valid_hours: set[int], lunch_hours: list[int] | None,
                   avoid_hours: set[int] = frozenset(), free_day: str | None = None, limit: int = 5) -> list[dict]:
    """Clash-free combinations of sections, one valid pick per offering,
    honouring the lunch rule and the preferences. Returns up to `limit`,
    most compact first (fewest distinct teaching days, then fewest slots)."""
    names = list(offerings)
    options = []
    for n in names:
        opts = []
        for pick in offering_options(offerings[n]):
            slots = set().union(*(parse_slots(s.get("days_hours"), valid_hours) for s in pick))
            if any(h in avoid_hours for _, h in slots) or (free_day and any(d == free_day for d, _ in slots)):
                continue
            opts.append((pick, slots))
        options.append(opts)
    results = []

    def rec(i, chosen, used):
        if len(results) >= 200:
            return
        if i == len(names):
            flat = [s for pick, _ in chosen for s in pick]
            report = check_selection(flat, valid_hours, lunch_hours)
            if not report["exam_clashes"] and not report["no_free_lunch_hour_on"]:
                results.append({"sections": {n: [s["section"] for s in p] for n, (p, _) in zip(names, chosen)},
                                "days": len({d for d, _ in used}), "slots": len(used)})
            return
        for pick, slots in options[i]:
            if not (slots & used):
                rec(i + 1, chosen + [(pick, slots)], used | slots)

    rec(0, [], set())
    results.sort(key=lambda r: (r["days"], r["slots"]))
    return results[:limit]


def blocked_by_preferences(offerings: dict[str, list[dict]], valid_hours: set[int],
                           avoid_hours: set[int] = frozenset(), free_day: str | None = None) -> dict[str, str]:
    """Courses that cannot meet the preferences on their own, with the reason,
    so an empty search result can be explained ("MATH F211: every lecture
    section meets in hour 1")."""
    blocked = {}
    for name, secs in offerings.items():
        ok = False
        for pick in offering_options(secs):
            slots = set().union(*(parse_slots(s.get("days_hours"), valid_hours) for s in pick))
            if not any(h in avoid_hours for _, h in slots) and not (free_day and any(d == free_day for d, _ in slots)):
                ok = True
                break
        if not ok:
            why = []
            if avoid_hours:
                why.append(f"every section combination meets in hour(s) {sorted(avoid_hours)}")
            if free_day:
                why.append(f"or on {free_day}")
            blocked[name] = " ".join(why) or "no live sections"
    return blocked
