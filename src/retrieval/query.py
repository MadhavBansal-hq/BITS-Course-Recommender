"""
Turn a free-text request into structured filters, deterministically.

Every constraint maps onto a field the pipeline already extracts, so what a
query can ask for is exactly what the data can answer; words that are not a
recognised constraint or category become the topic for meaning-based
matching. Day names and clock times are resolved through the timetable's
own legend (programme_rules.json), not built in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CATEGORY_PATTERNS = [
    ("DEL", r"\bdels?\b|discipline\s+electives?"),
    ("HUEL", r"\bhuels?\b|humanities(\s+electives?)?"),
    ("OPEL", r"\bopels?\b|open\s+electives?"),
    ("CDC", r"\bcdcs?\b|compulsory|core\s+courses?"),
]
CONSTRAINTS = {
    "no_midsem": r"\b(no|without(\s+a)?|skip)\s+mid[\s\-]?sem(ester)?s?(\s+(exam|test))?",
    "no_compre": r"\b(no|without(\s+a)?)\s+compre(hensive)?s?(\s+(exam|examination))?",
    "no_attendance": r"\b(no|without)\s+attendance(\s+requirements?)?|attendance\s+(is\s+)?not\s+(required|mandatory|compulsory)",
    "lenient_makeup": r"\b(lenient|easy|flexible|liberal|relaxed)\s+make[\s\-]?ups?(\s+polic(y|ies))?|make[\s\-]?ups?\s+(is\s+)?(allowed|available|lenient)",
    "open_book": r"\bopen[\s\-]?book",
    "project_based": r"\bproject[\s\-]?(based|heavy|oriented)|\bprojects?\b",
    "compact": r"\bcompact\b|\b(avoid|no|without|minimi[sz]e)\s+(long\s+)?gaps?",
    "fits_timetable": r"\bfits?\b(\s+(in|into|with))?(\s+my)?(\s+(current\s+)?timetable)?|\bno\s+clash(es)?|\bclash[\s\-]?free",
}


@dataclass
class Query:
    raw: str
    category: str | None = None
    constraints: list[str] = field(default_factory=list)
    avoid_hours: list[int] = field(default_factory=list)
    free_day: str | None = None
    topic: str | None = None


def parse_query(text: str, legend: dict) -> Query:
    q = Query(raw=text)
    rest = text
    for cat, pat in CATEGORY_PATTERNS:
        if re.search(pat, rest, re.I):
            q.category = q.category or cat
            rest = re.sub(pat, " ", rest, flags=re.I)
    for name, pat in CONSTRAINTS.items():
        if re.search(pat, rest, re.I):
            q.constraints.append(name)
            rest = re.sub(pat, " ", rest, flags=re.I)
    hours = {int(h): t for h, t in legend.get("hours", {}).items()}
    for m in re.finditer(r"\b(?:no|avoid|not?\s+before|nothing\s+at)\s+(?:classes\s+)?(?:at\s+)?(\d{1,2})\s*(?:am|a\.m\.|pm|p\.m\.)?\s*(?:classes?)?", rest, re.I):
        q.avoid_hours += sorted(h for h, t in hours.items() if t.split("-")[0].split(":")[0] == m[1])
    rest = re.sub(r"\b(?:no|avoid|not?\s+before|nothing\s+at)\s+(?:classes\s+)?(?:at\s+)?\d{1,2}\s*(?:am|a\.m\.|pm|p\.m\.)?\s*(?:classes?)?", " ", rest, flags=re.I)
    days = {name.lower(): code for code, name in legend.get("days", {}).items()}
    if days:
        dpat = "|".join(days)
        m = re.search(rf"\b(?:free|off|no\s+classes?\s+on|nothing\s+on)\s+({dpat})s?\b|\b({dpat})s?\s+(?:free|off)\b", rest, re.I)
        if m:
            q.free_day = days[(m[1] or m[2]).lower()]
            rest = rest[:m.start()] + " " + rest[m.end():]
    # hyphenated phrases split into words ("AI-related" -> "AI", "related")
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z+]*", rest)
             if w.lower() not in {"a", "an", "the", "and", "or", "with", "that", "have", "has", "me", "suggest",
                                  "some", "any", "courses", "course", "related", "to", "for", "about", "in",
                                  "which", "show", "find", "i", "want", "is", "are", "on", "of", "my", "classes",
                                  "timetable", "schedule", "need", "prefer", "evaluation", "policy", "please",
                                  "semester", "requirement", "requirements", "something", "one"}]
    q.topic = " ".join(words) or None
    return q
