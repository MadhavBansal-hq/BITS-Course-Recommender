"""
Parse Part IV ("Details of Programmes") of bulletin.pdf into per-course
records: course_code, title, units (L/P/U), and category (core /
discipline_elective).

Why this can't be a text-based (pdftotext) parser
--------------------------------------------------
Most Part IV pages are laid out in **two columns**, and `pdftotext -layout`
linearizes by y-position across the whole page width — so it interleaves
lines from both columns into a single stream that reads as plausible
prose to a human but is structurally wrong for row-level parsing (a course
title from the left column ends up adjacent to a units triple from the
right column). See docs/EXTRACTION_NOTES.md.

This module uses pdfplumber's word-level positions instead:
  1. Find words that look like a course-code department prefix (2-6
     uppercase letters) to anchor where columns actually start on THIS
     page — the boundary is not a fixed pixel value across all 951 pages
     (confirmed by sampling: some pages are single-column prose, and
     the two-column split point itself drifts page to page).
  2. Split all words into columns using those anchor x-positions.
  3. Within each column, group words into lines by y-position, sort
     left-to-right, and reconstruct line text.
  4. Parse course rows from the reconstructed per-column line stream with
     a single-column-shaped regex (much simpler than the timetable's,
     since the row grammar here is regular once column interleaving is
     removed).

Not every Part IV page has this two-column course-table shape (some are
prose, some are single-column semester plans) — pages that don't match
are skipped and left for manual/`needs_verification` follow-up rather than
forced through a parser built for a different layout.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import defaultdict

import pdfplumber

DEPT_PREFIX_RE = re.compile(r"^[A-Z]{2,6}$")

# A course row, once column-separated and reconstructed as one line:
#   <DEPT> <CODE><suffix?> <TITLE...> <L> <P> <U>
# Units are usually a clean L P U triple; a few rows print only a single
# combined unit number (e.g. "AUE F211 ... 3") when L/P aren't split out —
# handle both.
COURSE_LINE_RE = re.compile(
    r"""^
    (?P<dept>[A-Z]{2,6})\s+
    (?P<num>[A-Z]?\d{3}[A-Z]?T?)\s+
    (?P<title>.+?)\s+
    (?:(?P<l>\d)\s+(?P<p>\d)\s+(?P<u_triple>\d)|(?P<u_single>\d))
    $
    """,
    re.VERBOSE,
)

\
# The "CORE COURSES" / "DISCIPLINE ELECTIVE COURSES" header line sometimes
# has the repeated column headings "L P U" trailing on the same visual
# line (pdfplumber groups by y-position, and the two happen to sit on the
# same row on some pages) — confirmed on physical page 317
# ("CORE COURSES L P U"). Match on a leading anchor, not exact-line.
SECTION_HEADER_CORE = re.compile(r"^CORE\s+COURSES?\b", re.IGNORECASE)
SECTION_HEADER_ELECTIVE = re.compile(
    r"^DISCIPLINE\s+ELECTIVE\s+COURSES?\b", re.IGNORECASE
)

# Page-footer marker (e.g. "IV-109") — must never merge into a title.
PAGE_FOOTER_RE = re.compile(r"^IV-\d+$")

# A branch/department name header (e.g. "COMPUTER SCIENCE", "ELECTRICAL
# AND ELECTRONICS" split across two lines as "ELECTRICAL AND" /
# "ENGINEERING") or the "L P U" column-heading repeat, or an "OR OR"
# alternative-course marker. All share one distinguishing shape on this
# page layout: fully uppercase, no digits anywhere. A real course title
# fragment always has at least one lowercase letter (course titles in
# this bulletin are title-case, e.g. "Fluid Mechanics"), so this test
# does not risk swallowing a genuine wrapped title line. Confirmed
# necessary on physical page 317 ("COMPUTER SCIENCE" was merging onto
# the preceding course's title) and page 318/320 ("IV-109" footer doing
# the same to CS F314).
ALL_CAPS_NO_DIGIT_RE = re.compile(r"^[A-Z][A-Z &,]*$")


@dataclass
class BulletinCourse:
    course_code: str
    title: str
    credit_l: str | None
    credit_p: str | None
    credit_u: str | None
    category: str | None  # "core" | "discipline_elective" | None (unclassified)
    source_page: int
    branch_context: str | None  # nearest preceding ALL-CAPS branch/dept header, best-effort


\
# A course number token: F211, 211, F421T, U102, G527 etc.
COURSE_NUM_RE = re.compile(r"^[A-Z]?\d{3}[A-Z]?T?$")


def _column_boundaries(words: list[dict]) -> list[float]:
    """
    Return sorted x0 anchor positions where course-code department prefixes
    start on this page. Two distinct clusters ~ two columns; one cluster ~
    single column; empty ~ not a course table.

    A bare "2-6 uppercase letters" test is not enough to identify a
    department-prefix word — ordinary all-caps terms in running prose
    ("FRP" in "FRP Reinforced Concrete", 3 uppercase letters) match it by
    coincidence and create a spurious extra column anchor, which then
    misassigns real words to the wrong column and truncates/splits titles.
    Confirmed on physical page 317: "FRP" produced a false third anchor
    that fragmented the CE Discipline Elective column. Requiring the very
    next word to look like an actual course number (e.g. "F421", "211")
    is what actually distinguishes a department prefix from incidental
    prose — a real course-code table never has a bare uppercase word
    followed by a bare number to mean anything else.
    """
    code_x = set()
    for i, w in enumerate(words):
        if not DEPT_PREFIX_RE.match(w["text"]):
            continue
        if i + 1 >= len(words):
            continue
        nxt = words[i + 1]
        if COURSE_NUM_RE.match(nxt["text"]):
            code_x.add(round(w["x0"]))

    if not code_x:
        return []
    ordered = sorted(code_x)
    # cluster x-positions that are within 15pt of each other (font/kerning
    # noise) into single anchors
    clusters: list[float] = [ordered[0]]
    for x in ordered[1:]:
        if x - clusters[-1] > 15:
            clusters.append(x)
    return clusters


def _reconstruct_columns(words: list[dict], anchors: list[float]) -> list[list[str]]:
    """
    Split words into len(anchors) columns and reconstruct each column's
    lines top-to-bottom, left-to-right.

    Two approaches were tried and rejected before this one — both
    confirmed wrong on physical page 317's
    "CE F231 Fluid Mechanics ... 3 | CE F430 Design of Advanced Concrete
    3 0 3" row:

    1. A single global midpoint between column anchors. Rejected: a
       left-column row's own unit numbers print much further right than
       its title text — closer to the NEXT column's anchor than to its
       own — so the midpoint put the "3" belonging to CE F231 into CE
       F430's column.
    2. Cutting at the single largest inter-word gap on each row. Rejected:
       the largest gap on that exact row (119pt, "Mechanics" -> "3") is
       INSIDE CE F231's own row (a short title leaves a wide trailing gap
       before its unit number) — it is not the column boundary. The true
       column gutter on that row (23pt, "3" -> "CE") is smaller than that
       internal gap, so "largest gap" picks the wrong cut point entirely.

    What actually works: assign each word to its NEAREST anchor by raw
    x-distance, one word at a time (not by a fixed boundary line, and not
    by row-level gap analysis) — then, within each column, reconstruct
    lines by grouping consecutive same-column words that share a
    y-position. Nearest-anchor-per-word is robust here specifically
    because department-prefix words (which start every course's row) are
    always genuinely close to their own column's anchor and far from the
    other column's anchor — it's only the trailing unit numbers that
    drift rightward, and by the time a unit number appears, the words
    immediately preceding it on the same row (its own title) have already
    anchored that segment to the correct column; treating every word
    independently rather than trying to cut the row into contiguous
    segments avoids needing to locate a boundary at all.
    """
    n = len(anchors)

    # First pass: assign each word independently to its nearest anchor.
    word_col = [
        min(range(n), key=lambda i: abs(w["x0"] - anchors[i])) for w in words
    ]

    # Second pass, over words in their ORIGINAL (reading) order — NOT
    # bucketed by row yet, since row-bucketing itself is what caused the
    # cross-column collision fixed below. A unit number that drifted to
    # the wrong column, purely because it happens to sit numerically
    # closer to the OTHER column's anchor, needs correcting back to
    # whatever column the word immediately preceding it (in reading
    # order) was assigned to — UNLESS this word is itself the start of a
    # new course (a department-prefix word immediately followed by a
    # course-number word, e.g. "CE" -> "F430"), which really is allowed
    # to start a new column segment. This does NOT need a gap-size
    # threshold: on this document a short title can leave a wider
    # trailing gap before its own unit number (confirmed on physical page
    # 317, "CE F231 Fluid Mechanics" -> 119pt gap -> "3") than the actual
    # column gutter itself (23pt on that same row), so gap size alone
    # cannot distinguish "new column" from "same course, wide trailing
    # gap" — only the department-prefix+course-number test can.
    # Reading order alone is not enough to decide "same row as the
    # previous word": pdfplumber's word order across two columns can put
    # a word from a DIFFERENT row (e.g. a one-word title-wrap on the row
    # below, or a nearby row in the other column) right after a word from
    # the row we're trying to correct. Confirmed on physical page 317:
    # after correcting "in" (top=282.41492...) back onto column 0, the
    # very next word in reading order is "Languages" (top=281.69...) —
    # visually close to 282.41 but NOT the same row; it's the wrapped
    # continuation of an unrelated right-column course several rows
    # above. Blindly inheriting column-by-adjacency pulled "Languages",
    # and then everything after it, into column 0.
    #
    # A tolerance window doesn't work here — measured jitter within a
    # CONFIRMED single row on this page is exactly 0.0 (pdfplumber's
    # 'top' is bit-identical for every word placed on the same text
    # line), while the genuinely-different row above is only ~0.7pt away
    # — closer than some real cross-page row spacing can be relied on to
    # exceed. Since same-row jitter is zero, exact equality is both
    # necessary and sufficient to test "same row".
    for idx in range(1, len(words)):
        if word_col[idx] == word_col[idx - 1]:
            continue
        same_row = words[idx]["top"] == words[idx - 1]["top"]
        if not same_row:
            continue  # different row entirely — leave this word's own
            # nearest-anchor assignment alone; it needs its own row's
            # context (handled independently when its row is processed),
            # not this correction pass.
        starts_new_course = bool(
            DEPT_PREFIX_RE.match(words[idx]["text"])
            and idx + 1 < len(words)
            and COURSE_NUM_RE.match(words[idx + 1]["text"])
        )
        if not starts_new_course:
            word_col[idx] = word_col[idx - 1]

    # Row grouping must happen PER COLUMN, not globally by rounded y —
    # two words from genuinely different rows in different columns can
    # round to the same integer y-bucket (confirmed on physical page 317:
    # "CE F331 ... in" at top=282.41 in the left column, and "Languages"
    # — the wrapped continuation of an unrelated right-column course two
    # rows above — at top=281.69; both round to 282). Splitting into
    # per-column word lists FIRST (by word_col, already assigned above by
    # nearest anchor), and only THEN grouping each column's own words by
    # rounded y, avoids that cross-column collision: "Languages" and
    # "CE F331 ... in" are never compared against each other for row
    # membership, because they were never in the same column's word list
    # to begin with.
    columns: list[list[dict]] = [[] for _ in range(n)]
    for idx, w in enumerate(words):
        columns[word_col[idx]].append(w)

    out: list[list[str]] = []
    for col in columns:
        col_lines_by_y: dict[int, list[dict]] = defaultdict(list)
        for w in col:
            col_lines_by_y[round(w["top"])].append(w)
        lines = []
        for y in sorted(col_lines_by_y):
            row = sorted(col_lines_by_y[y], key=lambda w: w["x0"])
            lines.append(" ".join(w["text"] for w in row))
        out.append(lines)
    return out


def _parse_column_lines(
    lines: list[str],
) -> tuple[list[tuple[str, dict]], list[str]]:
    """
    Parse a single column's reconstructed lines into (category, match)
    pairs. A title that wraps onto its own line (a bare word or short
    phrase with no course code and no units — e.g. "Technology",
    "Structures") is appended onto the immediately preceding course's
    title. This is a reasonable assumption specifically because
    _reconstruct_columns already guarantees each reconstructed line
    belongs to a single column, and within one column, course rows and
    their own title-wrap continuations are the only two kinds of line
    that appear between one "CORE COURSES"/"DISCIPLINE ELECTIVE COURSES"
    header and the next course code.

    Returns (results, dropped_lines) — dropped_lines is every line this
    function could not confidently place (too long to be a title-wrap
    fragment, or with nothing preceding to attach to), so the caller can
    surface them to the verification queue instead of the data silently
    going missing. See the WRAP_WORD_LIMIT comment below for why "too
    long" is the right test.
    """
    results: list[tuple[str, dict]] = []
    dropped_lines: list[str] = []
    current_category: str | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Elective must be checked first: "DISCIPLINE ELECTIVE COURSES"
        # would also satisfy a careless "core" pattern if checked second
        # (it doesn't here, but keep the order defensive/explicit).
        if SECTION_HEADER_ELECTIVE.match(stripped):
            current_category = "discipline_elective"
            continue
        if SECTION_HEADER_CORE.match(stripped):
            current_category = "core"
            continue

        m = COURSE_LINE_RE.match(stripped)
        if m:
            results.append((current_category, m.groupdict()))
            continue

        # Page footers and branch/department headers must never merge
        # into a title — they're structurally similar to a wrapped-title
        # continuation (no course code, no units) but semantically
        # unrelated to the course above them. A branch header also marks
        # a new branch's listing starting, so reset the category rather
        # than carry the previous branch's CORE/DISCIPLINE ELECTIVE state
        # across the boundary.
        if PAGE_FOOTER_RE.match(stripped):
            continue
        if ALL_CAPS_NO_DIGIT_RE.match(stripped) and stripped not in ("OR OR", "OR"):
            current_category = None
            continue

        # No course code and no units on this line — usually a wrapped
        # title continuation for the previous row (a short fragment like
        # "Technology" or "Structures"). But not every Part IV page uses
        # the CORE/DISCIPLINE ELECTIVE table shape this parser targets —
        # some (e.g. M.E./M.Tech semester-wise plans with "Elective I/II"
        # markers and footnotes) interleave genuine course rows with
        # explanatory prose paragraphs. Confirmed on physical page 439
        # (SAN G513): a full multi-sentence paragraph about programme
        # tracks got silently appended onto the last course's title.
        # A real wrapped title fragment on this document is always SHORT
        # (a partial course name, at most a few words); a long line is a
        # signal this page doesn't match the expected table shape at all
        # — better to drop it and let the caller's unparsed-page tracking
        # flag the page for manual verification than to silently corrupt
        # a title with prose.
        WRAP_WORD_LIMIT = 6
        if results and len(stripped.split()) <= WRAP_WORD_LIMIT:
            prev_category, prev_match = results[-1]
            prev_match["title"] = f"{prev_match['title']} {stripped}"
        else:
            dropped_lines.append(stripped)

    return results, dropped_lines


def parse_bulletin_part_iv(pdf_path: Path, page_start: int, page_end: int):
    """
    page_start/page_end are 1-based physical PDF page numbers (inclusive)
    bounding Part IV in this specific bulletin.pdf. Returns
    (courses, unparsed_pages) — unparsed_pages lists pages that had course
    codes present but didn't match the expected row grammar, for the
    verification queue rather than silent loss.
    """
    courses: list[BulletinCourse] = []
    unparsed_pages: list[int] = []
    dropped_by_page: dict[int, list[str]] = {}

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in range(page_start, page_end + 1):
            page = pdf.pages[page_num - 1]
            words = page.extract_words()
            if not words:
                continue

            anchors = _column_boundaries(words)
            if not anchors:
                continue  # prose page, nothing to extract here

            columns = _reconstruct_columns(words, anchors)
            page_had_any_match = False
            page_had_code_words = True

            for col_lines in columns:
                parsed, dropped = _parse_column_lines(col_lines)
                if dropped:
                    dropped_by_page.setdefault(page_num, []).extend(dropped)
                if parsed:
                    page_had_any_match = True
                for category, m in parsed:
                    l = m.get("l")
                    p = m.get("p")
                    u = m.get("u_triple") or m.get("u_single")
                    courses.append(
                        BulletinCourse(
                            course_code=f"{m['dept']} {m['num']}",
                            title=m["title"].strip(),
                            credit_l=l,
                            credit_p=p,
                            credit_u=u,
                            category=category,
                            source_page=page_num,
                            branch_context=None,  # filled in a later pass
                        )
                    )

            if page_had_code_words and not page_had_any_match:
                unparsed_pages.append(page_num)

    return courses, unparsed_pages, dropped_by_page


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    pdf_path = root / "data" / "raw" / "bulletin.pdf"
    out_path = root / "data" / "processed" / "bulletin_courses.json"
    verify_path = root / "data" / "processed" / "needs_verification.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Part IV physical page range — located via the IV-xxx footer markers
    # (see scripts/find_bulletin_page_range.py for how these were derived).
    PART_IV_START = 209
    PART_IV_END = 462

    courses, unparsed_pages, dropped_by_page = parse_bulletin_part_iv(
        pdf_path, PART_IV_START, PART_IV_END
    )

    out_path.write_text(
        json.dumps([asdict(c) for c in courses], indent=2), encoding="utf-8"
    )

    verify_entries = [
        {
            "field": "bulletin_course_table",
            "course_code": None,
            "reason": "page had course-code-like tokens but no row matched the "
            "expected grammar — needs manual check",
            "source_hint": {"doc": "bulletin", "page": p},
        }
        for p in unparsed_pages
    ]
    verify_entries += [
        {
            "field": "bulletin_course_table",
            "course_code": None,
            "reason": "line on this page could not be confidently attached to "
            "any course (too long to be a title-wrap fragment) — likely "
            "explanatory prose or a table shape this parser doesn't yet "
            "handle; dropped rather than risk corrupting a title",
            "source_hint": {"doc": "bulletin", "page": page, "text": line},
        }
        for page, lines in dropped_by_page.items()
        for line in lines
    ]
    verify_path.write_text(json.dumps(verify_entries, indent=2), encoding="utf-8")

    print(f"Parsed {len(courses)} course records -> {out_path}")
    print(f"{len(unparsed_pages)} pages fully unparsed + "
          f"{sum(len(v) for v in dropped_by_page.values())} dropped lines "
          f"flagged for verification -> {verify_path}")


if __name__ == "__main__":
    main()
