# Extraction notes

What the source documents actually look like, found by inspecting them before
and while writing the parsers. Every number here was measured on the supplied
files. The parsers' docstrings and tests point back to these sections.

## Academic-Regulations-2023.pdf (70 pages)

Clean embedded text layer, no OCR needed. 13 numbered chapters with a
consistent heading style (`pdftotext` picks up "1. General", "2. Some
Structural Features", etc. directly), so section-by-number extraction is
straightforward.

It defines concepts precisely (prerequisite = a pair of courses with a
specific grade requirement; prior preparation = a set of courses with a
minimum grade; backlog = the burden of past incomplete courses) but says the
actual prerequisite pairs are catalogued in the Bulletin, not here.

## bulletin.pdf (951 pages)

Clean text layer, Parts I–VIII per its table of contents. Outside Part IV,
most pages are institutional material (governance, admissions and fees,
hostel listings) the recommender does not need.

**Part IV ("Details of Programmes") is physical pages 209–462** (footers IV-1
to IV-254, constant offset: IV-n is physical page n + 208).
`scripts/find_bulletin_page_range.py` finds this from the footers (exactly
one `IV-n` footer line per page). Part IV's own table of contents (physical
page 9) splits it into sections; the parser tags every record with one:

| IV pages | Section |
|---|---|
| 1–2 | programme structure (B.E. and dual degree) |
| 3–30 | semester-wise patterns (B.E., B.Pharm., M.Sc., BBA) |
| 31–105 | semester-wise patterns for dual degrees |
| 106–128 | **List of Courses for B.E. / M.Sc. / B.Pharm.**: per-branch CORE COURSES and DISCIPLINE ELECTIVE COURSES |
| 129–141 | minor programmes |
| 142–223 | 2+2 international collaboration programmes |
| 224–251 | higher degree programmes |
| 252–254 | Ph.D. |

**Two-column layout.** Most Part IV pages have two columns, and `pdftotext
-layout` interleaves them row by row, so the parser uses pdfplumber word
positions. How columns are found and assigned, and the three rules that were
tried and rejected (with the page and word that broke each), is in the
`parse_bulletin.py` module docstring. Two traps: an ALL-CAPS word in running
text ("FRP") looks like a department prefix, and a course code mentioned once
in prose or a footnote ("... in place of PHA F243" on IV-117) looks like the
start of a column.

**List of Courses (IV-106–128)** is the source for CDCs and discipline
electives:

- Lists continue from the left column into the right one and onto the next
  page. A list header can be split over two lines ("DISCIPLINE ELECTIVE" /
  "COURSES") or share a line with the "L P U" column heading.
- The same course is listed under several branches with different
  categories: CS F213 is core under COMPUTER SCIENCE and a discipline
  elective under EEE, ECE, EIE and Robotics. A category means nothing
  without its list heading.
- Units appear as an L P U triple, a single U, `- - 3` (blank L and P), or
  with a footnote star (`3*`).
- "OR" rows mark alternatives (EEE core: MATH F212 or ME F344). In some OR
  groups the units are printed once for the whole group (CHE F366 / F376 /
  F491), so later members have none of their own. On IV-120 a line
  "or or 3 1 4" between CS F211 and BITS F232 carries units whose owner is
  ambiguous; it is flagged.
- Cross-listed codes print as "CS G514/" followed by "SS G514".
- Some elective lists have "Track - n" or "Pool-n" sub-headings.
- IV-128 ends with a "List of Audit Type Courses" (N-coded, non-credit).
  Their ALL-CAPS titles wrap above and below the row and look like list
  headings. These 30 courses have no category.
- The intro paragraph on IV-106 says which kinds of courses count towards
  the Humanities electives requirement: courses normally listed under
  languages and literature, history and philosophy, political and social
  sciences, and fine and professional arts. A starting point for HUEL.

Result on the supplied bulletin: 1,589 List of Courses records (1,153
discipline electives, 406 core, 30 audit), none with another course's code
inside its title; 7,297 records across all of Part IV.

**Semester-wise patterns (IV-3–30, one page per first-degree programme)** are
parsed by `parse_semester_patterns.py` (grid layout in its docstring): named
courses by year and term, OR alternatives, elective slots, and a footer stating
the programme's requirement, e.g. B.E. Computer Science (IV-9): Discipline Core
48 units (14 courses), Discipline Electives 12 units (4 courses). Some pages
have no footer (the DEL requirement then falls back to the sum of the page's
discipline-elective slots), and on four pages the slots and the footer disagree
(Robotics: 21 vs 12); both cases are recorded in `unresolved`, footer used.
Dual-degree (IV-31–105) and 2+2 (IV-142–223) patterns use other layouts and are
not parsed yet.

**Course descriptions (Parts VI/VII) are not in this PDF**; they are printed
as "See enclosed CD for Contents". Handouts are the only syllabus source.

**Prerequisites are almost never tabulated.** A full-text search of all 951
pages finds on the order of ten explicit mentions (e.g. "Prerequisite: GER
N101T", "(with prerequisite: CE F211: Mechanics of Solids...)"), scattered as
footnotes. Treat a bulletin prerequisite as reliable when found, but expect
none for most courses.

**Requirements can differ by batch.** See the CS F320 question in
[DESIGN.md](DESIGN.md).

## Where the elective rules come from

- **Bulletin IV-1**: the category-wise structure of every first-degree
  programme (units, courses): Humanities Electives 8 (3); Discipline Core 33–48
  (10–16) and Electives 12–27 (4–9); Open Electives 15–27 (5–9); course-work
  129 (41) minimum. Per-programme exact core and DEL numbers are on each
  semester-wise pattern page.
- **Bulletin IV-2**: courses under four heads meet the Humanities Electives
  requirement: Languages and Literature; History and Philosophy; Political and
  Social Sciences; Fine Arts and Professional Arts.
- **Regulations 2.04–2.08**: named courses (those in the semester-wise pattern)
  are compulsory; electives are Discipline, Humanities or Open; an elective is
  treated as Open once the DEL and HUEL requirements are accounted for; up to
  four electives beyond the prescribed number; a higher-degree course counts as
  an Open Elective unless it is in the programme's DEL pool.
- **Timetable part VII**: keep a lunch hour (4, 5 or 6) free every day, compre
  dates must not clash, at most four extra electives. The legend maps hours
  1–10 to clock times (hour 1 = 8–8:50 AM) and gives exam-session times.
- **Timetable part IX**: 167 equivalences (e.g. IS F213 counts as CS F213).
- **Gaps.** No document lists the humanities courses themselves: timetable
  part VIII refers to Bulletin Part IV, and Part IV names only the four heads.
  Timetable part VI (prerequisites and restrictions) is only a link to the
  academic website. So HUEL membership and most prerequisites can only be
  what individual handouts state.

## timetable.pdf (153 pages)

Clean text layer, parsed from `pdftotext -layout` one page at a time. Its
index lists four parts: I. Course Handout, II. Course wise Timetable,
III. Textbooks, IV. courses that charge a course-wise fee. Only part II is
parsed: the parser starts at "COURSEWISE TIMETABLE" and stops at "III. TEXT
BOOKS". (Reading on used to attach textbook-list text such as "EQUIVALENT"
to the last course, SW E102, as instructors.)

Row grammar and quirks, each pinned by a test:

- An offering starts with a 3–5 digit COM COD, then course code, title,
  L P T S U, section, and details. Later rows of the same offering (L2, T1,
  P3, ...) carry only a section and details, so the parser copies the
  offering's com_cod, credits and exam dates down to them.
- COM COD, not course code, identifies an offering: EEE G593 has 2144 and
  6301.
- Every coursewise page carries the note "Courses with com cod >=5000 are
  meant only for 2026 admissions into FD, HD and PHD and not for others".
  442 of 1,718 sections fall under it.
- Section codes: `L1`, `T2`, `P14`; combined lecture+tutorial-group codes
  (`L1T1` = tutorial group 1 of L1, e.g. MGTS U102); suffixed codes (`L1AJ`,
  `L2RM` on BITS F240).
- Split course codes: `BITS F101-1`, `BITS K101-1` (the bulletin writes
  `BITS F101`, so joining the two needs the suffix handled).
- The details are columns separated by 2+ spaces, and any can be blank:
  project courses list only an instructor-in-charge (BIO F266), some sections
  list a room and slot but no instructor (FIN F212 L1), some only a slot.
  Positional parsing shifted columns into the wrong fields, so each column
  is classified by its shape.
- Hours can run together: `M 789` is Monday hours 7, 8 and 9.
- Rooms: `5102`, `6159A`, `3254_I`, and day-specific rooms like `6108(T)`. A
  day-specific room cell can span three lines ("6161(T)" above CHE F335's
  row, "6151(M" on it, "W)" below). The off-row lines are flagged (37 of
  them) because the section they belong to is ambiguous.
- "CANCLED" (sic) marks a cancelled section; one still prints an instructor
  on the next line (ME G532, offering 6441).
- Co-instructors appear on their own deeply indented lines; the column
  headings repeated at page breaks land in the same band and are filtered.
- Exam dates: the first date+session is the midsem, the second the compre.
  No course row here has only one; three practical sections (BITS F110 P4,
  CS F303 P1, PHA F342 P1) print one extra date+session of their own, which
  is flagged rather than interpreted.
- One course row doesn't fit L P T S U (EEE G593, offering 6301: credits
  "5 - - 15"). It and the practical row under it are flagged, not attached to
  the 2144 offering.

Result: 1,718 sections, 723 offerings, 585 course codes. All 724 course rows
in part II are accounted for (723 parsed, 1 flagged); 48 items flagged in
total.

## handouts/ (540 files, `NNN_DEPT_CODE.pdf`)

540 handouts across 33 department codes, all with a text layer except two
that yield almost no text (`348_MAC_F214.pdf`, `362_MATH_F214.pdf`; they will
need OCR or a flag).

Keyword mentions across all 540. A mention is not a structured section, but
it bounds how often a property can be extracted at all:

| Keyword (case-insensitive) | Handouts |
|---|---|
| evaluation | 537 |
| mid-sem / midsem / mid sem | 498 |
| compre / comprehensive | 484 |
| make-up / makeup / make up | 456 |
| attendance | 219 |
| prerequisite / pre-requisite | 31 |

So attendance rules can be stated for well under half of the courses and
prerequisites for about 6%; everything else must surface as "could not be
verified". A handout that never says "mid-sem" is not evidence that a course
has no midsem (other wording exists), so "no midsem" needs a positive
statement or the timetable's exam columns.

**Section labels are consistent in meaning, not in position** (from reading a
sample of handouts by hand):

- Numbering varies ("Attendance Policy" is section 7, 8, 9 or 11 depending
  on the handout), and some handouts are unnumbered, relying on keyword
  headers ("Course Description:").
- Label spelling varies: "Make-up Policy", "Makeup Policy", "MAKE-UP", "Make
  up policy".
- 23 handouts contain zero-width spaces (U+200B). In 180_CS_G527 one sits
  after each list number (`2.\u200b Text Book`), which breaks naive
  `^\d+\.\s` regexes. Strip zero-width characters before matching headers.

**Design consequence:** section extraction must be keyword-anchored (match the
section title text, case-insensitive, ignoring numbering and bullets) and must
tolerate a section being genuinely absent. A missing section is
`confidence: "unverified"`, not a failure to retry.

**Prerequisite wording varies in strength.** Some are a hard course-code gate
("Pre-requisite of the Course: Electronic Devices (F-214)"), others advisory
("a basic understanding of probability theory... would be helpful"). The
extractor should classify them as `hard_course_code` or `soft_recommendation`
(see `docs/SCHEMA.md`); an advisory note must not block an otherwise eligible
student.

**The evaluation scheme is the most reliably structured section**: mentioned
in 537 of 540 handouts, usually a numbered or tabular list of components with
a percentage weight each. It is the natural first target for the handout
parser.

## Build order

1. Timetable: the most regular document. Done.
2. Bulletin Part IV, List of Courses. Done. Semester-wise patterns next.
3. Handouts: least regular; needs the keyword-anchored extractor above.
4. Regulations: clean but prose-heavy; feeds the deterministic rule logic
   rather than the course schema.
