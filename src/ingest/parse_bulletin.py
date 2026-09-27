"""
Parse Part IV ("Details of Programmes") of bulletin.pdf into per-course
records: course code, title, units, the list heading the course appears
under, and whether it is listed as a CORE COURSE or a DISCIPLINE ELECTIVE
COURSE there.

Why not pdftotext
-----------------
Most Part IV pages are two-column, and `pdftotext -layout` linearizes by
y-position across the whole page, interleaving the two columns' rows. This
module works from pdfplumber word positions instead.

1. Column anchors. A word that looks like a department prefix AND is
   followed by a course-number token ("CE" -> "F231") marks the left edge of
   a column on this page. The course-number check matters: an all-caps word
   in running text ("FRP" in "FRP Reinforced Concrete") otherwise creates a
   spurious column (physical page 317). Anchors are found per page because
   the split point drifts between pages and some pages are single-column.

2. Column assignment. A word belongs to the right-most column whose left
   edge (anchor minus a small margin) is at or left of the word. Three
   other rules were tried on physical pages 317-318 and rejected:
     - midpoint between anchors: a left-column row's unit number (x=244 for
       CE F231) lies right of the midpoint (164.5) and went to the wrong
       column;
     - cutting each row at its largest gap: on that row the largest gap
       (119pt, "Mechanics" -> its own unit "3") is inside the left course,
       not the 23pt gutter;
     - nearest anchor, then "inherit the previous word's column unless a
       new course code starts": a right-column title wrap sharing a
       y-position with a left-column row ("Engineering Laboratory" beside
       "CS F437 ... 3 0 3") was pulled into the left row.
   The left-edge rule needs no row-level heuristics: every word of a column
   (code, title, wrapped title, units) starts at or right of that column's
   anchor, and every word of the column to its left ends before it.

3. Lines. Each column's words are grouped into lines by y-position.

4. Rows. A line starting with a course code opens a new record; its
   trailing unit tokens become the units: an L P U triple or a single U,
   with "-" for a blank L or P and an optional "*" footnote marker. A line
   without a course code is a wrapped title fragment of the open record; if
   the record has no units yet and the fragment ends in unit tokens, those
   become its units ("PHA G532 Quality Assurance and" / "Regulatory Affairs
   5"). A course-code line is never merged into another course's title —
   before this rule, 848 records had another course inside their title.

5. Lists. CORE COURSES / DISCIPLINE ELECTIVE COURSES headers set the
   category; consecutive ALL-CAPS lines form the list heading (usually a
   branch, sometimes split over two lines: "ELECTRICAL AND ELECTRONICS" /
   "ENGINEERING"). Inside "List of Courses" (IV-106..IV-128) that state
   carries across columns and pages in reading order, because lists
   continue from the left column into the right one and onto the next page.
   Elsewhere it resets per column, and every record carries the Part IV
   section it came from: "core" means something else there (a minor's core
   courses), and semester-wise pattern grids are a different table shape
   that this parser does not claim to handle.

Lines that can't be placed with confidence (long prose, which a title
fragment never is) go to the verification queue instead of into a title.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pdfplumber

# Physical page range of Part IV in the supplied bulletin.pdf (footers IV-1 ..
# IV-254), found by scripts/find_bulletin_page_range.py.
PART_IV_FIRST_PAGE = 209
PART_IV_LAST_PAGE = 462

# Part IV's own table of contents (bulletin physical page 9), in IV pages.
PART_IV_SECTIONS = [
    (1, 2, "programme_structure"),
    (3, 30, "semester_wise_patterns"),
    (31, 105, "dual_degree_semester_patterns"),
    (106, 128, "list_of_courses"),
    (129, 141, "minor_programmes"),
    (142, 223, "international_2plus2_programmes"),
    (224, 251, "higher_degree_programmes"),
    (252, 254, "phd_programme"),
]
LIST_OF_COURSES = "list_of_courses"

EDGE_MARGIN = 5.0      # pt a column's words may start left of its anchor
LINE_TOLERANCE = 2.0   # pt of y-jitter still treated as the same line
WRAP_WORD_LIMIT = 6    # longest line accepted as a wrapped title fragment
MIN_ROWS_PER_COLUMN = 2  # course rows needed before an x-position counts as a column
WRAP_X_TOLERANCE = 6.0   # pt a title wrap may start left of its title

DEPT_PREFIX_RE = re.compile(r"^[A-Z]{2,6}$")
COURSE_NUM_RE = re.compile(r"^[A-Z]?\d{3}[A-Z]?T?\*?/?$")
CODE_START_RE = re.compile(
    r"^(?P<dept>[A-Z]{2,6})\s+(?P<num>[A-Z]?\d{3}[A-Z]?T?)(?P<star>\*)?(?P<slash>/)?"
    r"(?:\s+(?P<rest>.*))?$"
)
_LP = r"(?:\d{1,2}|-)"
_U = r"\d{1,2}(?:[-/]\d{1,2})?\*?"
UNITS_TAIL_RE = re.compile(
    # The title group is lazy-optional (??) so a units-only string ("0 0 4")
    # is read as L P U, not as title "0 0" plus unit "4".
    rf"^(?:(?P<body>.*?)\s+)??(?:(?P<l>{_LP})\s+(?P<p>{_LP})\s+(?P<u3>{_U})|(?P<u1>{_U}))$"
)
LPU_RE = re.compile(r"\bL\s+P\s+U\b")  # repeated column heading, can share a line with a header
CORE_HEADER_RE = re.compile(r"^CORE\s+COURSES?$", re.IGNORECASE)
ELECTIVE_HEADER_RE = re.compile(r"^DISCIPLINE\s+ELECTIVE\s+COURSES?$", re.IGNORECASE)
PAGE_FOOTER_RE = re.compile(r"^IV-\d+$")
# "OR" between two rows of a list: either course satisfies the requirement.
OR_LINE_RE = re.compile(r"^(?:or|OR)(?:\s+(?:or|OR))*$")
OR_START_RE = re.compile(r"^(?:or|OR)\b")
# A header can be split over two lines ("DISCIPLINE ELECTIVE" / "COURSES").
HEADER_PREFIX_RE = re.compile(r"^(?:DISCIPLINE(?:\s+ELECTIVE)?|CORE)$")
# Sub-headings inside some elective lists ("Track - 3: Emerging Technologies").
TRACK_RE = re.compile(r"^(?:Track|Pool)\s*-?\s*\d+", re.IGNORECASE)
FOOTNOTE_RE = re.compile(r"^\*")  # "* To be offered to B.Pharm. 2014 onwards..."
# "List of Audit Type Courses" (IV-128): those courses have ALL-CAPS titles
# whose wrapped lines would otherwise look like list headings.
LIST_TITLE_RE = re.compile(r"^List of .*Courses$")
LEADING_OR_RE = re.compile(r"^(?:(?:or|OR)\s+)+")
ROMAN_RE = re.compile(r"^(?:I|II|III|IV|V)$")


@dataclass
class BulletinCourse:
    course_code: str
    title: str
    credit_l: str | None
    credit_p: str | None
    credit_u: str | None
    category: str | None            # "core" | "discipline_elective" | None
    list_heading: str | None        # e.g. "COMPUTER SCIENCE"
    bulletin_section: str | None    # a PART_IV_SECTIONS label
    alternative_to: str | None = None                   # "X / OR / Y": Y records X
    aliases: list[str] = field(default_factory=list)    # cross-listed codes
    footnote_marker: bool = False                       # "*" on the code or units
    source_page: int = 0                                # physical PDF page
    source_page_label: str = ""                         # the page's own footer, "IV-109"


@dataclass
class _ListState:
    category: str | None = None
    heading: list[str] = field(default_factory=list)
    heading_open: bool = False
    pending_header: str | None = None
    caps_titles: bool = False  # inside a list whose titles are ALL CAPS


def iv_page(physical_page: int) -> int:
    return physical_page - PART_IV_FIRST_PAGE + 1


def section_for(physical_page: int) -> str | None:
    n = iv_page(physical_page)
    for first, last, label in PART_IV_SECTIONS:
        if first <= n <= last:
            return label
    return None


def _is_heading(s: str) -> bool:
    return re.search(r"[a-z0-9]", s) is None and re.search(r"[A-Z]{2}", s) is not None


def _column_boundaries(words: list[dict]) -> list[int]:
    xs = {
        round(words[i]["x0"])
        for i in range(len(words) - 1)
        if DEPT_PREFIX_RE.match(words[i]["text"]) and COURSE_NUM_RE.match(words[i + 1]["text"])
    }
    clusters: list[list[int]] = []
    for x in sorted(xs):
        if clusters and x - clusters[-1][-1] <= 15:  # same column, kerning noise
            clusters[-1].append(x)
        else:
            clusters.append([x])
    # A code mentioned once inside prose or a footnote ("... in place of PHA
    # F243 Nutraceuticals", IV-117) is not a column: real columns on the List
    # of Courses pages start 15-38 course rows, spurious ones exactly 1.
    counts = [sum(1 for i in range(len(words) - 1)
                  if c[0] <= round(words[i]["x0"]) <= c[-1]
                  and DEPT_PREFIX_RE.match(words[i]["text"])
                  and COURSE_NUM_RE.match(words[i + 1]["text"])) for c in clusters]
    return [c[0] for c, n in zip(clusters, counts) if n >= MIN_ROWS_PER_COLUMN or len(clusters) == 1]


def _column_lines(words: list[dict], anchors: list[int]) -> list[list[tuple]]:
    """Per column, its lines as (text, x0 of first word, x0 of third word);
    for a course row the third word is the start of the title."""
    columns: list[list[dict]] = [[] for _ in anchors]
    for w in words:
        col = 0
        for i in range(1, len(anchors)):
            if w["x0"] >= anchors[i] - EDGE_MARGIN:
                col = i
        columns[col].append(w)

    out = []
    for col in columns:
        lines, current, top = [], [], 0.0
        for w in sorted(col, key=lambda w: (w["top"], w["x0"])):
            if current and abs(w["top"] - top) > LINE_TOLERANCE:
                lines.append(current)
                current = []
            if not current:
                top = w["top"]
            current.append(w)
        if current:
            lines.append(current)
        rows = []
        for line in lines:
            ws = sorted(line, key=lambda w: w["x0"])
            rows.append((" ".join(w["text"] for w in ws), ws[0]["x0"],
                         ws[2]["x0"] if len(ws) > 2 else None))
        out.append(rows)
    return out


def _split_units(text: str):
    """(title, L, P, U, footnote) with trailing unit tokens split off."""
    m = UNITS_TAIL_RE.match(text)
    if not m:
        return text, None, None, None, False
    u = m["u3"] or m["u1"]
    return (m["body"] or ""), m["l"], m["p"], u.rstrip("*"), u.endswith("*")


def _clean_title(rec: BulletinCourse) -> None:
    # A footnote star can sit on the last title word ("... of the Cell*").
    if rec.title.endswith("*"):
        rec.title = rec.title.rstrip("*").rstrip()
        rec.footnote_marker = True


def _add_heading_line(state: _ListState, text: str) -> None:
    if not state.heading_open:
        state.heading, state.category, state.heading_open = [], None, True
    state.heading.append(text)


def _extend(rec: BulletinCourse, text: str) -> None:
    if not text:
        return
    if rec.credit_u is None:
        title, l, p, u, star = _split_units(text)
        if u is not None:
            rec.title = f"{rec.title} {title}".strip()
            rec.credit_l, rec.credit_p, rec.credit_u = l, p, u
            rec.footnote_marker = rec.footnote_marker or star
            _clean_title(rec)
            return
    rec.title = f"{rec.title} {text}".strip()
    _clean_title(rec)


def _parse_column(lines: list[tuple], state: _ListState, section: str | None, page: int):
    records: list[BulletinCourse] = []
    dropped: list[str] = []
    open_rec: BulletinCourse | None = None
    open_slash = False
    alternative_to: str | None = None
    title_x: float | None = None  # where the open record's title starts

    for raw, x0, third_x in lines:
        s = " ".join(raw.split())
        header = " ".join(LPU_RE.sub(" ", s).split())
        if not header:
            continue  # blank, or a bare "L P U" line
        if state.pending_header:
            pending, state.pending_header = state.pending_header, None
            combined = f"{pending} {header}"
            if ELECTIVE_HEADER_RE.match(combined) or CORE_HEADER_RE.match(combined):
                state.category = "discipline_elective" if ELECTIVE_HEADER_RE.match(combined) else "core"
                state.heading_open = False
                continue
            _add_heading_line(state, pending)  # it was a heading after all
        if HEADER_PREFIX_RE.match(header):
            state.pending_header = header
            open_rec = None
            continue
        if ELECTIVE_HEADER_RE.match(header) or CORE_HEADER_RE.match(header):
            state.category = "discipline_elective" if ELECTIVE_HEADER_RE.match(header) else "core"
            state.heading_open = False
            open_rec = None
            continue
        if PAGE_FOOTER_RE.match(s) or ROMAN_RE.match(s):
            continue
        if OR_START_RE.match(s):
            last = open_rec or (records[-1] if records else None)
            alternative_to = last.course_code if last else None
            remainder = LEADING_OR_RE.sub("", s).strip() if not OR_LINE_RE.match(s) else ""
            units_only = UNITS_TAIL_RE.match(remainder)
            if remainder and units_only and not units_only["body"]:
                # "or or 3 1 4" between CS F211 and BITS F232 (IV-120): units
                # printed on the OR line; which course they belong to is
                # ambiguous, so they are flagged rather than assigned.
                dropped.append(s)
            elif remainder and open_rec is not None:
                _extend(open_rec, remainder)  # "or" beside a title wrap: "or Engineering"
            elif remainder:
                dropped.append(s)
            open_rec = None
            continue
        if LIST_TITLE_RE.match(s):
            state.heading, state.category, state.heading_open = [s], None, False
            state.caps_titles = True
            open_rec = None
            continue
        if TRACK_RE.match(s) or FOOTNOTE_RE.match(s):
            dropped.append(s)  # kept for verification, not glued to a title
            open_rec = None
            continue

        m = CODE_START_RE.match(s)
        if m:
            code = f"{m['dept']} {m['num']}"
            if open_rec is not None and open_slash:
                # "CS G514/" then "SS G514 Design": one cross-listed course
                # printed across two lines.
                open_rec.aliases.append(code)
                open_slash = bool(m["slash"])
                _extend(open_rec, m["rest"] or "")
                continue
            rest = m["rest"] or ""
            if LEADING_OR_RE.match(rest):
                # "CHE G557 or 0 0 4": the "or" joins this row to the one above
                rest = LEADING_OR_RE.sub("", rest)
                if alternative_to is None and records:
                    alternative_to = records[-1].course_code
            title, l, p, u, star_u = _split_units(rest)
            rec = BulletinCourse(
                course_code=code, title=title.strip(),
                credit_l=l, credit_p=p, credit_u=u,
                category=state.category,
                list_heading=" ".join(state.heading) or None,
                bulletin_section=section,
                alternative_to=alternative_to,
                footnote_marker=bool(m["star"]) or star_u,
                source_page=page, source_page_label=f"IV-{iv_page(page)}",
            )
            _clean_title(rec)
            records.append(rec)
            open_rec, open_slash, alternative_to = rec, bool(m["slash"]), None
            title_x = third_x
            state.heading_open = False
            continue

        if _is_heading(s) and state.caps_titles:
            dropped.append(s)  # wrapped ALL-CAPS title; above or below its row
            continue
        if _is_heading(s):
            _add_heading_line(state, s)
            open_rec = None
            continue

        # A real title wrap starts under the title; prose starts at the
        # column edge. Long lines are prose even when indented.
        if (open_rec is None or len(s.split()) > WRAP_WORD_LIMIT
                or (title_x is not None and x0 < title_x - WRAP_X_TOLERANCE)):
            dropped.append(s)
            open_rec = None
            continue
        _extend(open_rec, s)

    return records, dropped


def parse_bulletin_part_iv(pdf_path: Path, first_page: int = PART_IV_FIRST_PAGE,
                           last_page: int = PART_IV_LAST_PAGE):
    """Return (courses, pages with column anchors but no course rows,
    dropped lines as {"page", "text"})."""
    courses: list[BulletinCourse] = []
    unparsed_pages: list[int] = []
    dropped: list[dict] = []
    carried = _ListState()  # carried across columns and pages in List of Courses

    with pdfplumber.open(pdf_path) as pdf:
        for page in range(first_page, last_page + 1):
            section = section_for(page)
            words = pdf.pages[page - 1].extract_words()
            anchors = _column_boundaries(words) if words else []
            if not anchors:
                continue  # prose page
            page_records: list[BulletinCourse] = []
            for lines in _column_lines(words, anchors):
                state = carried if section == LIST_OF_COURSES else _ListState()
                recs, drop = _parse_column(lines, state, section, page)
                page_records += recs
                dropped += [{"page": page, "text": t} for t in drop]
            if not page_records:
                unparsed_pages.append(page)
            courses += page_records
    return courses, unparsed_pages, dropped


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out_dir = root / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    courses, unparsed_pages, dropped = parse_bulletin_part_iv(root / "data" / "raw" / "bulletin.pdf")
    (out_dir / "bulletin_courses.json").write_text(
        json.dumps([asdict(c) for c in courses], indent=2), encoding="utf-8"
    )
    verify = [
        {"field": "bulletin_course_table", "course_code": None,
         "reason": "page has course-code columns but no row matched the course-row grammar",
         "source_hint": {"doc": "bulletin", "page": p}}
        for p in unparsed_pages
    ] + [
        {"field": "bulletin_course_table", "course_code": None,
         "reason": "line could not be attached to a course (too long for a title "
                   "fragment, or nothing open to attach to)",
         "source_hint": {"doc": "bulletin", "page": d["page"], "text": d["text"]}}
        for d in dropped
    ]
    (out_dir / "bulletin_needs_verification.json").write_text(
        json.dumps(verify, indent=2), encoding="utf-8"
    )
    print(f"Parsed {len(courses)} course records "
          f"({len({c.course_code for c in courses})} codes) -> bulletin_courses.json")
    print(f"{len(verify)} items flagged -> bulletin_needs_verification.json")


if __name__ == "__main__":
    main()
