"""
Parse the single-degree semester-wise patterns in bulletin Part IV (one page
per programme: "Semester-wise Pattern for Students Admitted to B. E. Computer
Science Programme") into named courses per year and term, elective slots,
and the programme's stated discipline requirements.

Why these pages matter: Academic Regulations 2.04-2.07 define a programme's
compulsory ("named") courses as those named in its semester-wise pattern,
and 2.08 says the number of electives per category is prescribed through the
Bulletin; the pattern's elective slots and footer
("Discipline Core -48 Units (14 Courses)", "Discipline Electives-12 Units(4
Courses)") are where that happens.

Nothing is hardcoded to this edition: pattern pages are found by their title
(composite / dual-degree patterns are excluded — a different grid, not
handled yet), the IV-n label is read from each page's footer, and column
positions come from each page's own "U" column headings.

Grid layout (bulletin physical p217, IV-9, word positions):
  - two terms side by side, each "code | title | U"; the code is two words
    ("CS" "F214"); a title can wrap ABOVE and BELOW its code row, so titles
    are NOT taken from here — join course_code to bulletin_courses.json for
    titles;
  - a row holding exactly two unit totals, one per U column ("18" "19",
    "20(min)" "21(min)", "18/21" "18/21"), closes a year;
  - "Summer" rows (Practice School I) sit between years II and III;
  - "or" marks alternatives, either as a row of its own between two codes
    (ECON F211 / or / MGTS F211) or before a code whose department is
    omitted ("or F425T" = BITS F425T);
  - elective slots ("Humanities Electives 3(min)", "Open Electives 6to12",
    "Open/Humanities Electives 3 to 6") may split kind, "Electives" and the
    amount over up to three rows.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pdfplumber

TITLE_RE = re.compile(r"Semester-?\s*wise\s+[Pp]attern\s+for\s+Students\s+Admitted\s+to\s+(?P<prog>.+?)\s+(?:Programme|Yea)")  # "Year" is split "Yea"/"r" on IV-29
DEPT_RE = re.compile(r"^[A-Z]{2,5}$")
NUM_RE = re.compile(r"^[A-Z]?\d{3}[A-Z]?T?$")
UNIT_RE = re.compile(r"^\d{1,2}\*?$|^\d{1,2}-\d{1,2}$")
TOTAL_RE = re.compile(r"^\d{1,2}(\(min\))?$|^\d{1,2}/\d{1,2}$")
SLOT_RE = re.compile(r"(Open/Humanities|Humanities|Discipline|Open)(?:\s+Electives?)?\b")
CORE_FOOTER_RE = re.compile(r"Discipline\s+Core\s*-?\s*(\d+)\s*Units?\s*\(\s*(\d+)\s*Courses?\)")
DEL_FOOTER_RE = re.compile(r"Discipline\s+Electives?\s*-?\s*(\d+)\s*Units?\s*\(\s*(\d+)\s*Courses?\)")
FIRST_DEGREE_RE = re.compile(r"^(B\.\s?E\.|B\.\s?Pharm|M\.\s?Sc\.|Bachelor)")
ROW_TOLERANCE = 2.5
NEAR_ROW = 14.0  # pt: how far a unit or slot amount may sit from its row
SLOT_KINDS = {"Open/Humanities": "open_or_humanities", "Humanities": "humanities",
              "Discipline": "discipline", "Open": "open"}


@dataclass
class NamedSlot:
    year: int
    term: str                      # "1", "2" or "summer"
    options: list[str]             # one code, or alternatives ("X or Y")
    units: str | None              # as printed in the pattern
    source_page: int


@dataclass
class ElectiveSlot:
    year: int
    term: str
    kind: str                      # humanities | discipline | open | open_or_humanities
    units: str | None              # as printed: "3(min)", "6to12", "3 to 6"
    source_page: int


@dataclass
class ProgrammePattern:
    programme: str
    source_page: int
    source_page_label: str | None
    discipline_core_units: int | None = None
    discipline_core_courses: int | None = None
    discipline_elective_units: int | None = None
    discipline_elective_courses: int | None = None
    named: list[NamedSlot] = field(default_factory=list)
    elective_slots: list[ElectiveSlot] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def find_pattern_pages(pdf_path: Path) -> dict[int, str]:
    """Physical page -> programme name, for single-degree pattern pages."""
    text = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"],
                          capture_output=True, text=True, check=True).stdout
    found = {}
    for i, page in enumerate(text.split("\f"), start=1):
        flat = " ".join(page.split())
        m = TITLE_RE.search(flat)
        # The title must head the page (the table of contents also quotes
        # it) and name a first-degree programme: higher-degree, M.Phil. and
        # 2+2 pages ("... under BITS - RMIT Academy") reuse the wording for different structures.
        if (m and m.start() < 250 and FIRST_DEGREE_RE.match(m["prog"])
                and not re.search(r"dual\s+degree|composite|\bat\s+BITS\b|\bunder\s+BITS\b", flat[:300], re.I)):
            found[i] = re.sub(r"\s+", " ", m["prog"]).replace("B. E.", "B.E.").replace("M. Sc.", "M.Sc.")
    return found


def _rows(words: list[dict]) -> list[list[dict]]:
    rows, cur, top = [], [], 0.0
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if cur and abs(w["top"] - top) > ROW_TOLERANCE:
            rows.append(sorted(cur, key=lambda w: w["x0"]))
            cur = []
        if not cur:
            top = w["top"]
        cur.append(w)
    if cur:
        rows.append(sorted(cur, key=lambda w: w["x0"]))
    return rows


def parse_pattern_page(page, page_num: int, programme: str) -> ProgrammePattern:
    text = " ".join((page.extract_text() or "").split())
    labels = re.findall(r"\bIV-\d+\b", text)
    pat = ProgrammePattern(programme, page_num, labels[-1] if labels else None)
    if m := CORE_FOOTER_RE.search(text):
        pat.discipline_core_units, pat.discipline_core_courses = int(m[1]), int(m[2])
    if m := DEL_FOOTER_RE.search(text):
        pat.discipline_elective_units, pat.discipline_elective_courses = int(m[1]), int(m[2])

    rows = _rows(page.extract_words())
    hdr = next((i for i, r in enumerate(rows) if sum(w["text"] == "U" for w in r) == 2), None)
    if hdr is None:
        pat.unresolved.append("no 'U' column headings found; grid not parsed")
        return pat
    u1, u2 = [w["x0"] for w in rows[hdr] if w["text"] == "U"]
    split = u1 + 15            # terms divide just right of the first U column
    zone = [(u1 - 20, split), (u2 - 20, 10_000)]

    def col_of(w):
        return 0 if w["x0"] < split else 1

    def in_u(w, c):
        return zone[c][0] <= w["x0"] < zone[c][1]

    body = []
    for r in rows[hdr + 1:]:
        t = " ".join(w["text"] for w in r)
        if CORE_FOOTER_RE.search(t) or DEL_FOOTER_RE.search(t) or re.fullmatch(r"IV-\d+", t):
            break
        body.append(r)

    # first pass: codes (with alternatives), totals rows, slot rows
    year, summer = 1, False
    last_slot: list[NamedSlot | None] = [None, None]
    pending_or = [False, False]
    last_dept = [None, None]
    claimed: set[int] = set()        # id() of unit words already assigned
    pending_units: list[tuple[NamedSlot, float, int]] = []
    slot_rows: list[tuple[float, int, str, int, bool]] = []

    unit_words: list[tuple[dict, float, int]] = []
    for r in body:
        texts = [w["text"] for w in r]
        top = r[0]["top"]
        if len(r) == 2 and all(TOTAL_RE.match(t) for t in texts) and {col_of(w) for w in r} == {0, 1}:
            year += 1
            summer = False
            last_slot = [None, None]
            continue
        if "Summer" in texts:
            summer = True
        term_of = (lambda c: "summer") if summer and "Summer" in texts else (lambda c: str(c + 1))
        slot_year = year - 1 if summer and "Summer" in texts else year
        for c in (0, 1):
            cw = [w for w in r if col_of(w) == c]
            ct = [w["text"] for w in cw]
            if ct and all(t == "or" for t in ct):
                pending_or[c] = True
                continue
            i = 0
            while i < len(cw) - 1:
                a, b = cw[i]["text"], cw[i + 1]["text"]
                if (DEPT_RE.match(a) or a == "or") and NUM_RE.match(b):
                    dept = last_dept[c] if a == "or" else a
                    code = f"{dept} {b}"
                    last_dept[c] = dept
                    if (a == "or" or pending_or[c]) and last_slot[c] is not None:
                        last_slot[c].options.append(code)
                    else:
                        s = NamedSlot(slot_year, term_of(c), [code], None, page_num)
                        pat.named.append(s)
                        last_slot[c] = s
                        same = [w for w in cw if in_u(w, c) and UNIT_RE.match(w["text"])]
                        if same:
                            s.units = same[-1]["text"]
                            claimed.add(id(same[-1]))
                        else:
                            pending_units.append((s, top, c))
                    pending_or[c] = False
                    i += 2
                    continue
                i += 1
            if not any(DEPT_RE.match(x["text"]) or x["text"] == "or" for x in cw
                       if not in_u(x, c)) or not any(NUM_RE.match(x["text"]) for x in cw):
                unit_words += [(w, top, c) for w in cw if in_u(w, c) and UNIT_RE.match(w["text"])]
            joined = " ".join(w["text"] for w in cw if not in_u(w, c))
            m = SLOT_RE.search(joined)
            if m and ("Electives" in joined or m[1] == "Open/Humanities") and "Thesis" not in joined:
                amount = " ".join(w["text"] for w in cw if in_u(w, c)) or None
                slot_rows.append((top, c, SLOT_KINDS[m[1]], year, amount))

    # units printed on a title-wrap row near the code row
    for s, top, c in pending_units:
        cands = [(abs(t - top), w) for w, t, cc in unit_words
                 if cc == c and id(w) not in claimed and abs(t - top) <= NEAR_ROW]
        if cands:
            w = min(cands, key=lambda x: x[0])[1]
            s.units = w["text"]
            claimed.add(id(w))
        else:
            pat.unresolved.append(f"no units found for {'/'.join(s.options)}")

    # elective slots; an amount may sit on a neighbouring row
    amounts = [(r[0]["top"], c, " ".join(w["text"] for w in r if col_of(w) == c and in_u(w, c)))
               for r in body for c in (0, 1)
               if any(col_of(w) == c and in_u(w, c) for w in r)
               and all(not (col_of(w) == c and not in_u(w, c)) or w["text"] in ("to",) for w in r)]
    for top, c, kind, yr, amount in slot_rows:
        if amount is None:
            near = [(abs(t - top), a) for t, cc, a in amounts if cc == c and abs(t - top) <= NEAR_ROW]
            amount = min(near)[1] if near else None
        pat.elective_slots.append(ElectiveSlot(yr, str(c + 1), kind, amount, page_num))
    slot_sum = del_units_from_slots(pat)
    if pat.discipline_elective_units is None:
        pat.unresolved.append("no Discipline Electives footer; requirement falls back to the "
                              f"sum of discipline-elective slots ({slot_sum})")
    elif slot_sum is not None and slot_sum != pat.discipline_elective_units:
        pat.unresolved.append(f"discipline-elective slots sum to {slot_sum} units but the footer "
                              f"states {pat.discipline_elective_units}; footer used")
    if pat.discipline_core_units is None:
        pat.unresolved.append("no Discipline Core footer")
    return pat


def parse_semester_patterns(pdf_path: Path) -> list[ProgrammePattern]:
    pages = find_pattern_pages(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        return [parse_pattern_page(pdf.pages[p - 1], p, name) for p, name in sorted(pages.items())]


def del_units_from_slots(p: ProgrammePattern) -> int | None:
    """Sum of the minimum units in the pattern's discipline-elective slots."""
    total = 0
    for s in p.elective_slots:
        if s.kind == "discipline":
            m = re.match(r"(\d+)", s.units or "")
            if not m:
                return None
            total += int(m[1])
    return total or None


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out = root / "data" / "processed"
    out.mkdir(parents=True, exist_ok=True)
    patterns = parse_semester_patterns(root / "data" / "raw" / "bulletin.pdf")
    (out / "semester_patterns.json").write_text(json.dumps([asdict(p) for p in patterns], indent=2), encoding="utf-8")
    print(f"Parsed {len(patterns)} programme patterns -> semester_patterns.json")


if __name__ == "__main__":
    main()
