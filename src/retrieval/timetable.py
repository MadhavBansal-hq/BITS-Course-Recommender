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
                   avoid_hours: set[int] = frozenset(), free_day: str | None = None, limit: int = 5,
                   max_nodes: int = 100_000, stats: dict | None = None) -> list[dict]:
    """Clash-free combinations of sections, one valid pick per offering,
    with no exam clash, a free lunch hour every day, and the preferences
    honoured. Returns up to `limit`, most compact first (fewest teaching
    days, then fewest slots).

    Offerings with the fewest options are placed first and every constraint
    is checked as soon as a pick is added, so dead ends are cut early. The
    search stops after `max_nodes`; `stats["complete"]` then says whether
    an empty result means "impossible" (True) or "not determined" (False)."""
    names = list(offerings)
    options = {}
    for n in names:
        opts = []
        for pick in offering_options(offerings[n]):
            slots = frozenset().union(*(parse_slots(s.get("days_hours"), valid_hours) for s in pick))
            if any(h in avoid_hours for _, h in slots) or (free_day and any(d == free_day for d, _ in slots)):
                continue
            opts.append((pick, slots))
        options[n] = opts
    exams = {n: frozenset((w,) + k for w in ("midsem", "compre")
                          if offerings[n] and (k := exam_key(offerings[n][0], w))) for n in names}
    order = sorted(names, key=lambda n: len(options[n]))
    lunch = list(lunch_hours or [])
    enough = limit if limit <= 1 else limit * 40
    results: list[dict] = []
    state = {"nodes": 0, "complete": True}

    def lunch_ok(used, new):
        return not lunch or all(not all((d, h) in used for h in lunch) for d in {d for d, _ in new})

    def rec(i, chosen, used, ex):
        if len(results) >= enough:
            return
        if state["nodes"] >= max_nodes:
            state["complete"] = False
            return
        state["nodes"] += 1
        if i == len(order):
            results.append({"sections": {n: [s["section"] for s in p] for n, p in chosen},
                            "days": len({d for d, _ in used}), "slots": len(used)})
            return
        n = order[i]
        if ex & exams[n]:
            return
        for pick, slots in options[n]:
            if slots & used:
                continue
            new = used | slots
            if lunch_ok(new, slots):
                rec(i + 1, chosen + [(n, pick)], new, ex | exams[n])

    rec(0, [], frozenset(), frozenset())
    if stats is not None:
        stats.update(state)
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
