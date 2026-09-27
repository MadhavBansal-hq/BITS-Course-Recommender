"""
Parse the coursewise timetable section of timetable.pdf into structured
section-level records.

Grammar, reverse-engineered from the `pdftotext -layout` output of the
supplied timetable (details and examples in docs/EXTRACTION_NOTES.md):

  <COM_COD> <COURSE_NO> <TITLE> <L> <P> <T> <S> <U> <SEC> <detail...>
      <co-instructor line>*
  [Tutorial|Practical] <SEC> <detail...>
      <co-instructor line>*
  ...

- A course OFFERING starts with a 3-5 digit COM_COD. The same course code
  can appear under two COM_CODs (a regular offering and a 6xxx one), so
  COM_COD, not the course code, identifies an offering.
- Later section rows (L2, T1, P3, and combined lecture+tutorial-group codes
  such as L1T1) belong to the offering above them and repeat none of its
  course-level fields; those fields are copied down so every section
  record is self-contained.
- The detail part of a row is a run of columns separated by 2+ spaces:
  instructor, room, days & hours, midsem date+session, compre
  date+session. Any of them can be blank: project courses list only an
  instructor-in-charge, some sections list a room and slot but no
  instructor, some only days & hours. Assigning columns by POSITION put
  days into the room field and exam dates into room/days whenever a column
  was blank, so each column is classified by its SHAPE instead, and
  anything that fits no shape is kept in `unparsed_fields`, never dropped.
- A section without an instructor may print literally "CANCLED" (sic).
- Extra instructors for a section appear as bare, deeply indented lines.
- Course-looking rows that don't fit the grammar (a practice-school
  coordinator table with a different layout; a row whose credit columns
  don't fit L P T S U) are returned for the verification queue, and the
  section rows under them are NOT attached to the previous course.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Course numbers seen here: F111, G527, U102, C790T, and a split-course
# form with a part suffix (BITS F101-1, BITS K101-1).
COURSE_CODE = r"[A-Z]{2,6}\s+[A-Z]?\d{3}[A-Z]?T?(?:-\d)?"

# Plain section codes (L1, T2, P14), lecture codes with a letter suffix
# (L1AJ), or combined lecture+tutorial-group codes (L1T1 = tutorial group 1
# of lecture section L1). The combined form must be tried first, or "L1T1"
# is mis-split into "L1" plus a garbage remainder.
SEC_CODE = r"L\d+T\d+|L\d+[A-Z]{0,2}|T\d+|P\d+"

COURSE_ROW_RE = re.compile(
    rf"""^\s*
    (?P<com_cod>\d{{3,5}})\s+
    (?P<course_code>{COURSE_CODE})\s+
    (?P<title>.+?)\s+
    (?P<l>[\d\-])\s+
    (?P<p>[\d\-])\s+
    (?P<t>[\d\-])\s+
    (?P<s>[\d\-])\s+
    (?P<u>[\d\-]+)\s+
    (?P<sec>{SEC_CODE})\b
    (?P<rest>.*)$
    """,
    re.VERBOSE,
)

# Starts like a course row (COM_COD + course code) whether or not the rest fits.
COURSE_LIKE_RE = re.compile(rf"^\s*\d{{3,5}}\s+{COURSE_CODE}\b")

SECTION_ROW_RE = re.compile(
    rf"""^\s*
    (?:Tutorial|Practical)?\s*
    (?P<sec>{SEC_CODE})\b
    (?P<rest>.*)$
    """,
    re.VERBOSE,
)

CO_INSTRUCTOR_RE = re.compile(r"^\s{40,}(?P<name>[A-Za-z][A-Za-z.\s()'-]+)\s*$")

# Column-heading fragments repeated at the top of every page; they can land
# in the co-instructor indentation band when a course block spans a page.
_HEADER_JUNK_RE = re.compile(
    r"^(CREDIT|MIDSEM|COMPRE|L\s+P\s+T\s+S|U/C|COURSE\s?(NO\.?|TITLE)|"
    r"INSTRUCTOR|COM\s?COD|SEC|ROOM|DAYS\s?&\s?HOURS|DATE\s?&|SESSION)\b",
    re.IGNORECASE,
)

# Shapes of the detail columns.
DATE_SESSION_RE = re.compile(r"^(?P<date>\d{2}/\d{2})\s+(?P<session>[A-Z]{2}\d?)$")
_DAY_LIST = r"(?:Th|M|T|W|F|S)(?:\s+(?:Th|M|T|W|F|S))*"
# 5102, 6159A, 3254_I, and day-specific rooms: 6108(T), 6151(M W)
ROOM_RE = re.compile(rf"^[A-Z]{{0,4}}\d{{3,5}}[A-Z]?(?:_[A-Z0-9]+)?(?:\({_DAY_LIST}\)?)?$")
# A day-specific room cell can spill onto its own line above or below the
# row ("6161(T)" above CHEM F335's row, "W)" below it). Which section such
# a line belongs to is ambiguous, so it is flagged rather than attached.
ROOM_SPILL_RE = re.compile(rf"^\s{{40,}}(?:\d{{3,5}}\({_DAY_LIST}\)?|{_DAY_LIST}\))\s*$")
# The coursewise section is followed by "III. TEXT BOOKS"; nothing after it
# is a timetable row (reading on attached its text to the last course).
END_OF_COURSEWISE_RE = re.compile(r"^\s*III\.\s*TEXT\s*BOOKS", re.IGNORECASE)
_DAY = r"(?:Th|M|T|W|F|S)+"  # also matches run-together forms: MWF, MW, TTh
# Hours may be run together: "T 789" is Tuesday hours 7, 8 and 9.
_SLOT = rf"{_DAY}(?:\s+{_DAY})*(?:\s+\d{{1,4}})+"
DAYS_HOURS_RE = re.compile(rf"^{_SLOT}(?:\s+{_SLOT})*$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z.\s()'-]*$")


@dataclass
class TimetableSection:
    com_cod: int | None
    course_code: str
    title: str
    credit_l: str
    credit_p: str
    credit_t: str
    credit_s: str
    credit_u: str
    section: str
    instructors: list[str] = field(default_factory=list)
    room: str | None = None
    days_hours: str | None = None
    midsem_date: str | None = None
    midsem_session: str | None = None
    compre_date: str | None = None
    compre_session: str | None = None
    cancelled: bool = False
    # Every coursewise page carries the note "Courses with com cod >=5000
    # are meant only for 2026 admissions into FD, HD and PHD and not for
    # others" — an eligibility rule, so it is recorded per section.
    only_for_2026_admissions: bool = False
    unparsed_fields: list[str] = field(default_factory=list)
    source_page: int | None = None


def normalise_course_code(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip())


def _is_header_junk(line: str) -> bool:
    return bool(_HEADER_JUNK_RE.match(re.sub(r"\s+", " ", line.strip())))


def _kind(text: str):
    if text == "CANCLED":
        return ("cancelled", text)
    m = DATE_SESSION_RE.match(text)
    if m:
        return ("dates", (m["date"], m["session"]))
    if ROOM_RE.match(text):
        return ("rooms", text)
    if DAYS_HOURS_RE.match(text):
        return ("days", text)
    # A name needs two letters in a row and must not be a bare day token
    # ("T", "Th", "MWF"), or a slot whose hours failed to parse would turn
    # its day letters into an instructor.
    if (NAME_RE.match(text) and re.search(r"[A-Za-z]{2}", text)
            and not re.fullmatch(_DAY, text) and not _is_header_junk(text)):
        return ("names", text.strip())
    return None


def _split_field(tokens: list[str]):
    """Classify one column; if it is really two adjacent columns separated by
    a single space, split it (longest left part first, so a multi-word name
    is never cut in two)."""
    for i in range(len(tokens), 0, -1):
        k = _kind(" ".join(tokens[:i]))
        if k is None:
            continue
        if i == len(tokens):
            return [k]
        rest = _split_field(tokens[i:])
        if rest is not None:
            return [k] + rest
    return None


def _fill_detail(rec: TimetableSection, rest: str) -> None:
    found = {"names": [], "rooms": [], "days": [], "dates": []}
    for col in re.split(r"\s{2,}", rest.strip()):
        if not col:
            continue
        pieces = _split_field(col.split())
        if pieces is None:
            rec.unparsed_fields.append(col)
            continue
        for kind, value in pieces:
            if kind == "cancelled":
                rec.cancelled = True
            else:
                found[kind].append(value)

    rec.instructors.extend(found["names"])
    if found["rooms"]:
        rec.room = found["rooms"][0]
        rec.unparsed_fields.extend(found["rooms"][1:])
    if found["days"]:
        rec.days_hours = found["days"][0]
        rec.unparsed_fields.extend(found["days"][1:])
    dates = found["dates"]
    if len(dates) >= 2:
        rec.midsem_date, rec.midsem_session = dates[0]
        rec.compre_date, rec.compre_session = dates[1]
        rec.unparsed_fields.extend(f"{d} {s}" for d, s in dates[2:])
    elif len(dates) == 1:
        # A lone exam date can't be told apart as midsem vs compre by shape,
        # so it is not guessed. (No such row exists in the supplied
        # timetable; this guards future editions.)
        rec.unparsed_fields.append(f"{dates[0][0]} {dates[0][1]}")


def _page_texts(pdf_path: Path) -> list[str]:
    info = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, check=True)
    n_pages = int(info.stdout.split("Pages:")[1].split()[0])
    return [
        subprocess.run(
            ["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(pdf_path), "-"],
            capture_output=True, text=True, check=True,
        ).stdout
        for i in range(1, n_pages + 1)
    ]


def parse_timetable(pdf_path: Path) -> tuple[list[TimetableSection], list[dict]]:
    """Return (section records, rows that looked like courses but could not
    be parsed)."""
    records: list[TimetableSection] = []
    unparsed_rows: list[dict] = []
    current: TimetableSection | None = None
    offering: TimetableSection | None = None  # primary row of the current block
    in_coursewise = False

    for page_num, page_text in enumerate(_page_texts(pdf_path), start=1):
        if "COURSEWISE TIMETABLE" in page_text:
            in_coursewise = True
        if not in_coursewise:
            continue  # academic calendar and general notes

        for raw_line in page_text.splitlines():
            if END_OF_COURSEWISE_RE.match(raw_line.replace("\f", "")):
                if current is not None:
                    records.append(current)
                return records, unparsed_rows
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("Note:") or "COURSEWISE TIMETABLE" in raw_line:
                continue

            m = COURSE_ROW_RE.match(raw_line)
            if m:
                if current is not None:
                    records.append(current)
                com_cod = int(m["com_cod"])
                current = offering = TimetableSection(
                    com_cod=com_cod,
                    course_code=normalise_course_code(m["course_code"]),
                    title=m["title"].strip(),
                    credit_l=m["l"], credit_p=m["p"], credit_t=m["t"],
                    credit_s=m["s"], credit_u=m["u"],
                    section=m["sec"],
                    only_for_2026_admissions=com_cod >= 5000,
                    source_page=page_num,
                )
                _fill_detail(current, m["rest"])
                continue

            if COURSE_LIKE_RE.match(raw_line):
                if current is not None:
                    records.append(current)
                current = offering = None
                unparsed_rows.append({
                    "page": page_num, "text": stripped,
                    "reason": "course-like row does not fit the COM_COD / course / "
                              "L P T S U / section grammar",
                })
                continue

            sm = SECTION_ROW_RE.match(raw_line)
            if sm:
                if offering is None:
                    unparsed_rows.append({
                        "page": page_num, "text": stripped,
                        "reason": "section row under a course row that could not be parsed",
                    })
                    continue
                if current is not None:
                    records.append(current)
                current = TimetableSection(
                    com_cod=offering.com_cod,
                    course_code=offering.course_code,
                    title=offering.title,
                    credit_l=offering.credit_l, credit_p=offering.credit_p,
                    credit_t=offering.credit_t, credit_s=offering.credit_s,
                    credit_u=offering.credit_u,
                    section=sm["sec"],
                    only_for_2026_admissions=offering.only_for_2026_admissions,
                    source_page=page_num,
                )
                _fill_detail(current, sm["rest"])
                for f in ("midsem_date", "midsem_session", "compre_date", "compre_session"):
                    if getattr(current, f) is None:
                        setattr(current, f, getattr(offering, f))
                continue

            if ROOM_SPILL_RE.match(raw_line):
                unparsed_rows.append({
                    "page": page_num, "text": stripped,
                    "reason": "day-specific room cell on its own line; ambiguous which "
                              "section (row above or below) it belongs to",
                })
                continue
            co = CO_INSTRUCTOR_RE.match(raw_line)
            if co and current is not None and (_kind(co["name"].strip()) or ("",))[0] == "names":
                current.instructors.append(co["name"].strip())

    if current is not None:
        records.append(current)
    return records, unparsed_rows


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out_dir = root / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    records, unparsed_rows = parse_timetable(root / "data" / "raw" / "timetable.pdf")
    (out_dir / "timetable.json").write_text(
        json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8"
    )

    verify = [
        {"field": "timetable_row", "course_code": None, "reason": u["reason"],
         "source_hint": {"doc": "timetable", "page": u["page"], "text": u["text"]}}
        for u in unparsed_rows
    ] + [
        {"field": "timetable_section_detail", "course_code": r.course_code,
         "reason": "column fits none of: instructor, room, days & hours, exam date+session",
         "source_hint": {"doc": "timetable", "page": r.source_page, "section": r.section, "text": f}}
        for r in records for f in r.unparsed_fields
    ]
    (out_dir / "timetable_needs_verification.json").write_text(
        json.dumps(verify, indent=2), encoding="utf-8"
    )
    print(f"Parsed {len(records)} section records "
          f"({len({r.course_code for r in records})} courses) -> timetable.json")
    print(f"{len(verify)} items flagged -> timetable_needs_verification.json")


if __name__ == "__main__":
    main()
