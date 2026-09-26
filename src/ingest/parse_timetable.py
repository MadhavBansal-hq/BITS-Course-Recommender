"""
Parse the coursewise timetable section of timetable.pdf into structured
section-level records.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path

# Section codes come in two forms: a plain lecture/tutorial/practical code
# (L1, T2, P14) and a combined lecture+tutorial-group code (L1T1, L2T7 —
# "tutorial group 1 of lecture section L1"). The combined form must be
# tried first or it gets mis-split into "L1" + a garbage remainder — see
# docs/EXTRACTION_NOTES.md.
_SEC_CODE = r"L\d+T\d+|L\d+|T\d+|P\d+"

COURSE_ROW_RE = re.compile(
    rf"""^\s*
    (?P<com_cod>\d{{3,5}})\s+
    (?P<course_code>[A-Z]{{2,6}}\s+[A-Z]?\d{{3}}[A-Z]?T?)\s+
    (?P<title>.+?)\s+
    (?P<l>[\d\-])\s+
    (?P<p>[\d\-])\s+
    (?P<t>[\d\-])\s+
    (?P<s>[\d\-])\s+
    (?P<u>[\d\-]+)\s+
    (?P<sec>{_SEC_CODE})
    (?P<rest>.*)$
    """,
    re.VERBOSE,
)

SECTION_ROW_RE = re.compile(
    rf"""^\s*
    (?:Tutorial|Practical)?\s*
    (?P<sec>{_SEC_CODE})\s*
    (?P<rest>.*)$
    """,
    re.VERBOSE,
)

CANCELLED_RE = re.compile(r"^\s*CANCLED\s*$")

DETAIL_RE = re.compile(
    r"""^\s*
    (?P<instructor>[A-Za-z][A-Za-z.\s()'-]*?)\s{2,}
    (?P<room>\S+)\s+
    (?P<days_hours>[A-Za-z0-9 ]+?)
    (?:\s+(?P<midsem_date>\d{2}/\d{2})\s+(?P<midsem_session>\S+))?
    (?:\s+(?P<compre_date>\d{2}/\d{2})\s+(?P<compre_session>\S+))?
    \s*$
    """,
    re.VERBOSE,
)

# Fallback: an instructor name with nothing else after it (project/thesis
# style courses — no fixed room/slot, just an instructor-in-charge).
INSTRUCTOR_ONLY_RE = re.compile(r"^\s*(?P<instructor>[A-Za-z][A-Za-z.\s()'-]+?)\s*$")

CO_INSTRUCTOR_RE = re.compile(r"^\s{40,}(?P<name>[A-Za-z][A-Za-z.\s()'-]+)\s*$")

# Recurring column-header / boilerplate fragments that repeat at the top of
# every page and can land in the same indentation band as a co-instructor
# line when a course block spans a page break.
_HEADER_JUNK_RE = re.compile(
    r"^(CREDIT|MIDSEM|COMPRE|L\s+P\s+T\s+S|U/C|COURSE\s?(NO\.?|TITLE)|"
    r"INSTRUCTOR|COM\s?COD|SEC|ROOM|DAYS\s?&\s?HOURS|DATE\s?&|SESSION)\b",
    re.IGNORECASE,
)


def _is_header_junk(line: str) -> bool:
    collapsed = re.sub(r"\s+", " ", line.strip())
    return bool(_HEADER_JUNK_RE.match(collapsed))

COURSE_CODE_NORMALISE_RE = re.compile(r"\s+")


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
    source_page: int | None = None


def normalise_course_code(raw: str) -> str:
    return COURSE_CODE_NORMALISE_RE.sub(" ", raw.strip())


def _page_texts(pdf_path: Path) -> list[str]:
    n_pages = int(
        subprocess.run(
            ["pdfinfo", str(pdf_path)], capture_output=True, text=True, check=True
        ).stdout.split("Pages:")[1].split()[0]
    )
    pages = []
    for i in range(1, n_pages + 1):
        result = subprocess.run(
            ["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(pdf_path), "-"],
            capture_output=True,
            text=True,
            check=True,
        )
        pages.append(result.stdout)
    return pages


def parse_timetable(pdf_path: Path) -> list[TimetableSection]:
    pages = _page_texts(pdf_path)
    records: list[TimetableSection] = []
    current: TimetableSection | None = None

    for page_num, page_text in enumerate(pages, start=1):
        if "COURSEWISE TIMETABLE" not in page_text and current is None:
            continue

        for raw_line in page_text.splitlines():
            if not raw_line.strip():
                continue
            if raw_line.strip().startswith("Note:") or "COURSEWISE TIMETABLE" in raw_line:
                continue

            m = COURSE_ROW_RE.match(raw_line)
            if m:
                if current is not None:
                    records.append(current)
                current = TimetableSection(
                    com_cod=int(m.group("com_cod")),
                    course_code=normalise_course_code(m.group("course_code")),
                    title=m.group("title").strip(),
                    credit_l=m.group("l"),
                    credit_p=m.group("p"),
                    credit_t=m.group("t"),
                    credit_s=m.group("s"),
                    credit_u=m.group("u"),
                    section=m.group("sec"),
                    source_page=page_num,
                )
                _fill_detail(current, m.group("rest"))
                continue

            if current is None:
                continue

            sm = SECTION_ROW_RE.match(raw_line)
            if sm and sm.group("sec"):
                records.append(current)
                current = TimetableSection(
                    com_cod=None,
                    course_code=current.course_code,
                    title=current.title,
                    credit_l="-",
                    credit_p="-",
                    credit_t="-",
                    credit_s="-",
                    credit_u="-",
                    section=sm.group("sec"),
                    source_page=page_num,
                )
                _fill_detail(current, sm.group("rest"))
                continue

            co = CO_INSTRUCTOR_RE.match(raw_line)
            if co and current is not None and not _is_header_junk(raw_line):
                current.instructors.append(co.group("name").strip())
                continue

        if current is not None:
            pass

    if current is not None:
        records.append(current)

    return records


def _fill_detail(rec: TimetableSection, rest: str) -> None:
    rest = rest.strip()
    if not rest:
        return
    if CANCELLED_RE.match(rest):
        rec.cancelled = True
        return
    dm = DETAIL_RE.match(rest)
    if dm:
        if dm.group("instructor"):
            rec.instructors.append(dm.group("instructor").strip())
        rec.room = dm.group("room")
        rec.days_hours = dm.group("days_hours").strip() if dm.group("days_hours") else None
        rec.midsem_date = dm.group("midsem_date")
        rec.midsem_session = dm.group("midsem_session")
        rec.compre_date = dm.group("compre_date")
        rec.compre_session = dm.group("compre_session")
        return

    # No room/slot at all — likely a project/thesis-style course where only
    # an instructor-in-charge is listed (see docs/EXTRACTION_NOTES.md).
    io = INSTRUCTOR_ONLY_RE.match(rest)
    if io and not _is_header_junk(rest):
        rec.instructors.append(io.group("instructor").strip())


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    pdf_path = root / "data" / "raw" / "timetable.pdf"
    out_path = root / "data" / "processed" / "timetable.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records = parse_timetable(pdf_path)
    out_path.write_text(
        json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8"
    )
    print(f"Parsed {len(records)} section records -> {out_path}")


if __name__ == "__main__":
    main()
