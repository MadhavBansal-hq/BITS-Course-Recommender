"""
Collect the programme-level rules the supplied documents actually state:

- bulletin IV-1: the category-wise structure of first-degree programmes
  (units and courses per category: Humanities Electives, Discipline Core /
  Elective, Open Electives, ...), found by its heading, not a page number;
- bulletin IV-2: the four heads whose courses count as Humanities Electives;
- Academic Regulations 2.04-2.08: named courses vs. electives, how an
  elective becomes an Open Elective, the four-extra-electives rule
  (verbatim, with page numbers);
- timetable parts VII-IX: registration rules (lunch hours, compre dates,
  extra electives), the Humanities-pool note, and the equivalent-courses
  table (old codes that count as current ones).

Per-programme numbers (discipline core / electives) come from the semester
patterns (parse_semester_patterns.py). Gaps are recorded, not filled: the
dataset names the humanities heads but lists no humanities courses
(timetable part VIII points to Bulletin Part IV; Part IV lists none).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

CODE = r"[A-Z]{2,5}\s+[A-Z]?\d{3}[A-Z]?T?"


def _text(pdf: Path) -> list[str]:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    return out.split("\f")


def category_structure(bulletin_pages: list[str]) -> dict:
    for i, page in enumerate(bulletin_pages, start=1):
        # The table of contents quotes the heading too; the table page has the column header.
        if "category-wise structure of each program" not in page or "Number of Units" not in page:
            continue
        rows, group = [], None
        for line in page.splitlines():
            g = re.match(r"^\s*\((I{1,3}|IV)\)\s+(.+?)\s*$", line)
            m = re.match(r"^\s*(?:\((?:I{1,3}|IV)\)\s+)?(?P<cat>[A-Za-z][A-Za-z /\-]+?)\s{2,}"
                         r"(?P<units>\d+(?:\s*to\s*\d+)?(?:\s*\(min\))?)\s{2,}"
                         r"(?P<courses>\d+(?:\s*to\s*\d+)?(?:\s*\(min\))?)\s*$", line)
            if g and not m:
                group = g[2]
            if m:
                rows.append({"group": group, "category": m["cat"].strip(),
                             "units": m["units"], "courses": m["courses"]})
                if line.lstrip().startswith("("):
                    rows[-1]["group"] = rows[-1]["category"]
        label = re.findall(r"\bIV-\d+\b", page)
        return {"rows": rows, "source": {"doc": "bulletin", "page": i, "label": label[-1] if label else None}}
    return {"rows": [], "source": None, "note": "category-wise structure table not found"}


def humanities_heads(bulletin_pages: list[str]) -> dict:
    for i, page in enumerate(bulletin_pages, start=1):
        if "head of Humanities" not in " ".join(page.split()):
            continue
        # Two text columns share each line; the (a)-(d) list is in the left one.
        lines = page.splitlines()
        heads = [m[2].strip() for l in lines
                 if (m := re.match(r"^\s*\(([a-d])\)\s+(.+?)(?:\s{2,}|$)", l))]
        if heads:
            label = re.findall(r"\bIV-\d+\b", page)
            return {"heads": heads, "source": {"doc": "bulletin", "page": i, "label": label[-1] if label else None}}
    return {"heads": [], "source": None}


def regulation_clauses(reg_pages: list[str], wanted=("2.04", "2.05", "2.06", "2.07", "2.08")) -> list[dict]:
    """Clause numbers sit at the right margin of each clause's first line."""
    clauses, cur = [], None
    for pno, page in enumerate(reg_pages, start=1):
        for line in page.splitlines():
            m = re.search(r"\s(\d\.\d{2})\s*$", line)
            if m:
                if cur:
                    clauses.append(cur)
                cur = {"clause": m[1], "page": pno, "text": line[:m.start()].strip()}
            elif cur and line.strip() and not re.fullmatch(r"\s*\d+\s*", line):
                cur["text"] += " " + line.strip()
    if cur:
        clauses.append(cur)
    first = {}
    for c in clauses:  # later "2.07"-style cross-references are not clause starts
        if c["clause"] in wanted and c["clause"] not in first:
            first[c["clause"]] = c
    return [dict(c, text=" ".join(c["text"].split()), doc="regulations") for c in first.values()]


def timetable_parts(tt_pages: list[str]) -> dict:
    full, page_of = [], []
    for pno, page in enumerate(tt_pages, start=1):
        for line in page.splitlines():
            full.append(line)
            page_of.append(pno)
    heads = [(i, m[1]) for i, l in enumerate(full)
             if (m := re.match(r"^\s*(V|VI|VII|VIII|IX|X)\.\s+[A-Z]{3}", l))]
    parts = {}
    for n, (i, roman) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(full)
        if roman not in parts:
            parts[roman] = (i, end)

    def text(roman):
        i, e = parts[roman]
        return " ".join(" ".join(full[i:e]).split()), page_of[i]

    out = {}
    if "VII" in parts:
        t, p = text("VII")
        lunch = re.search(r"lunch hours are ([\d, and]+)", t)
        extra = re.search(r"maximum of (\w+) more electives", t)
        out["registration"] = {
            "text": t, "source": {"doc": "timetable", "page": p},
            "lunch_hours": [int(x) for x in re.findall(r"\d+", lunch[1])] if lunch else None,
            "must_keep_a_lunch_hour_free": bool(re.search(r"provision for lunch hour on all days", t)),
            "compre_dates_must_not_clash": bool(re.search(r"Comprehensive Examination Dates are not clashing", t)),
            "max_extra_electives": extra[1] if extra else None,
        }
    if "V" in parts:
        t, p = text("V")
        areas = re.search(r"take a few (.+?) courses as electives", t)
        hd = re.search(r"Registration of Higher Degree Courses as Elective.*?(Students must.*?semester\.)", t)
        out["elective_guidance"] = {
            "text": t, "source": {"doc": "timetable", "page": p},
            "humanities_areas": re.findall(r"\(([A-Z]{2,5})\)", areas[1]) if areas else [],
            "humanities_areas_sentence": areas[0] if areas else None,
            "higher_degree_rule": hd[1] if hd else None,
            "higher_degree_one_per_semester": bool(re.search(r"Only one higher degree course can be taken as elective in a semester", t)),
        }
    if "VIII" in parts:
        t, p = text("VIII")
        out["humanities_pool"] = {"courses_listed": False, "text": t, "source": {"doc": "timetable", "page": p}}
    if "IX" in parts:
        i, e = parts["IX"]
        eq = []
        for k in range(i, e):
            m = re.match(rf"^\s*({CODE})\s{{2,}}(.+?)\s{{2,}}((?:{CODE}\s*)+)$", full[k])
            if m:
                eq.append({"course_code": " ".join(m[1].split()), "title": m[2].strip(),
                           "equivalents": [" ".join(c.split()) for c in re.findall(CODE, m[3])],
                           "source": {"doc": "timetable", "page": page_of[k]}})
        out["equivalent_courses"] = eq
    return out


def hours_and_sessions(tt_pages: list[str]) -> dict:
    """The timetable's own legend: hour numbers -> clock times, exam sessions -> times."""
    lines = [l for page in tt_pages for l in page.splitlines()]
    hours, sessions, days = {}, {}, {}
    for i, l in enumerate(lines):
        if re.search(r"\bDAYS\b", l):
            days.update({code: name for code, name in re.findall(r"\b([A-Z][a-z]?)\s*=\s*([A-Z][a-z]+day)\b", l)})
        if re.search(r"\bHOURS\b", l) and "timings" in l:
            for nums, times in ((lines[i + 1], lines[i + 2]), (lines[i + 3], lines[i + 4])):
                ns = re.findall(r"\b\d{1,2}\b", nums)
                ts = re.findall(r"\d{1,2}(?::\d{2})?-\d{1,2}:\d{2}\s*[AP]M", times)
                if len(ns) == len(ts):
                    hours.update({int(n): t for n, t in zip(ns, ts)})
        for m in re.finditer(r"\b(FN\d?|AN\d?):\s*([\d.:]+\s*[AP]\.?M\.?\s*to\s*[\d.:]+\s*[AP]\.?M\.?)", l):
            sessions.setdefault(m[1], m[2])
    return {"hours": hours, "days": days, "exam_sessions": sessions, "source": {"doc": "timetable", "section": "legend (8. DAYS, 9. HOURS, 10-11. exam sessions)"}}


def department_names(bulletin_pages: list[str]) -> dict:
    """The bulletin's legend of abbreviations ("HSS  Humanities and Social
    Sciences"), found by its own "Suffixed to a course number" entry."""
    for i, page in enumerate(bulletin_pages):
        if "Suffixed to a course number" in page:
            names = {}
            for p in bulletin_pages[max(0, i - 1):i + 1]:
                for line in p.splitlines():
                    if (m := re.match(r"^\s*([A-Z][A-Za-z.]{1,7})\s{3,}([A-Z][A-Za-z ,&:()\-]+?)\s*$", line)):
                        names[m[1]] = m[2]
            return {"names": names, "source": {"doc": "bulletin", "page": i + 1}}
    return {"names": {}, "source": None}


def build(root: Path) -> dict:
    raw = root / "data" / "raw"
    bulletin, regs, tt = _text(raw / "bulletin.pdf"), _text(raw / "Academic-Regulations-2023.pdf"), _text(raw / "timetable.pdf")
    parts = timetable_parts(tt)
    campus = re.search(r"\b(PILANI|GOA|HYDERABAD|DUBAI)\s+CAMPUS\b", " ".join(tt[:3]), re.I)
    return {
        "data_campus": {"campus": campus[1].title() if campus else None, "source": {"doc": "timetable", "page": "1-3"}},
        "category_structure": category_structure(bulletin),
        "humanities": {**humanities_heads(bulletin), "pool": parts.get("humanities_pool")},
        "regulations": regulation_clauses(regs),
        "registration": parts.get("registration"),
        "elective_guidance": parts.get("elective_guidance"),
        "timetable_legend": hours_and_sessions(tt),
        "equivalent_courses": parts.get("equivalent_courses", []),
        "department_names": department_names(bulletin),
        "gaps": ["No document in the dataset lists which courses are Humanities Electives: the bulletin "
                 "names four heads, and timetable part VIII refers back to Bulletin Part IV, which lists none. "
                 "A course's HUEL status is therefore 'could not be verified' unless its own handout states it."],
    }


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    rules = build(root)
    out = root / "data" / "processed" / "programme_rules.json"
    out.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    print(f"{len(rules['category_structure']['rows'])} category rows, {len(rules['regulations'])} clauses, "
          f"{len(rules['equivalent_courses'])} equivalences -> {out.name}")


if __name__ == "__main__":
    main()
