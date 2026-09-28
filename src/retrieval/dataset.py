"""
Load data/processed/*.json and join them into the lookups the engine needs.
Nothing here holds a course list or a rule of its own: every value comes
from the parser outputs, so re-running ingestion on a new timetable,
bulletin or handout set changes the answers without touching this code.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

STOP = {"AND", "WITH", "IN", "OF", "THE", "ENGINEERING", "PROGRAMME", "SPECIALIZATION",
        "SPECIALISATION", "HONOURS", "STREAM", "B", "E", "M", "SC", "BE", "MSC"}
DEGREE_PREFIX = re.compile(r"^(B\.\s?E\.|M\.\s?Sc\.|Bachelor of)\s*", re.I)


def _tokens(name: str) -> set[str]:
    name = DEGREE_PREFIX.sub("", name).replace("&", " AND ").replace("–", " ")
    return {t for t in re.findall(r"[A-Z]+", name.upper()) if t not in STOP}


def _similarity(a: set[str], b: set[str]) -> float:
    """Word overlap; a word matches its own prefix ("PHARM" ~ "PHARMACY")."""
    def hit(t, other):
        return any(t == o or (min(len(t), len(o)) >= 4 and (t.startswith(o) or o.startswith(t))) for o in other)
    if not a or not b:
        return 0.0
    matched = sum(hit(t, b) for t in a) + sum(hit(t, a) for t in b)
    return matched / (len(a) + len(b))


@dataclass
class Dataset:
    patterns: dict[str, dict]
    lists: dict[str, dict[str, list[dict]]]        # heading -> category -> records
    titles: dict[str, str]
    units: dict[str, str]
    handouts: dict[str, list[dict]]
    offerings: dict[str, list[dict]]               # course_code -> sections
    rules: dict
    equivalent_to: dict[str, set[str]]             # any code -> codes it counts as
    heading_for: dict[str, tuple[str | None, float]] = field(default_factory=dict)

    def programme_heading(self, programme: str) -> tuple[str | None, float]:
        return self.heading_for.get(programme, (None, 0.0))


def load(root: Path) -> Dataset:
    p = root / "data" / "processed"
    read = lambda n: json.loads((p / n).read_text(encoding="utf-8"))
    patterns = {x["programme"]: x for x in read("semester_patterns.json")}
    bulletin = read("bulletin_courses.json")
    lists: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    titles, units = {}, {}
    for c in bulletin:
        if c["bulletin_section"] == "list_of_courses" and c["list_heading"]:
            lists[c["list_heading"]][c["category"] or "uncategorised"].append(c)
        if c["bulletin_section"] == "list_of_courses":
            titles.setdefault(c["course_code"], c["title"])
            if c["credit_u"]:
                units.setdefault(c["course_code"], c["credit_u"])
    rules = read("programme_rules.json")
    data_campus = (rules.get("data_campus") or {}).get("campus")
    handouts: dict[str, list[dict]] = defaultdict(list)
    for h in read("handouts.json"):
        # another campus's handout (208_ECE_F314 is Hyderabad's) says nothing
        # reliable about this campus's offering; build_dataset reports these
        if data_campus and h.get("campus") and h["campus"] != data_campus:
            continue
        handouts[h["course_code"]].append(h)
        # A file named for one code may hold another code's handout (the
        # BIO_U101 file is BIO F101's); index it under both, noted.
        for code in re.findall(r"[A-Z]{2,5} [FGCU]\d{3}[A-Z]?", h.get("course_code_in_text") or ""):
            if code != h["course_code"]:
                handouts[code].append(dict(h, joined_via=f"file {h['file']} (named for {h['course_code']})"))
    offerings: dict[str, list[dict]] = defaultdict(list)
    for s in read("timetable.json"):
        offerings[s["course_code"]].append(s)
        titles.setdefault(s["course_code"], s["title"])
    equivalent_to: dict[str, set[str]] = defaultdict(set)
    for e in rules.get("equivalent_courses", []):
        for other in e["equivalents"]:
            equivalent_to[other].add(e["course_code"])
            equivalent_to[e["course_code"]].add(other)
    ds = Dataset(patterns, lists, titles, units, handouts, offerings, rules, equivalent_to)
    heads = {h: _tokens(h) for h in lists}
    for prog in patterns:
        scored = sorted(((_similarity(_tokens(prog), t), h) for h, t in heads.items()), reverse=True)
        best = scored[0] if scored else (0.0, None)
        tie = len(scored) > 1 and scored[1][0] == best[0]
        ds.heading_for[prog] = (best[1] if best[0] >= 0.5 and not tie else None, round(best[0], 2))
    return ds
