# Extraction Notes

Findings from inspecting the actual source documents before writing any
parser. Kept here so the reasoning behind the pipeline's design isn't lost —
and so nobody re-discovers these the hard way.

## Academic-Regulations-2023.pdf (70 pages)

Clean embedded text layer, no OCR needed. 13 numbered chapters, consistent
heading style (`pdftotext` picks up "1. General", "2. Some Structural
Features", etc. directly). Good candidate for straightforward
section-by-number extraction.

Defines *concepts* precisely (prerequisite = pair of courses with a specific
grade requirement; prior preparation = a set of courses with a minimum
grade; backlog = burden of past incomplete courses) but explicitly says the
actual prerequisite *pairs* are catalogued in the Bulletin, not here.

## bulletin.pdf (951 pages, 34 MB)

Clean text layer. Structure (Table of Contents, Parts I–VIII):

- Part IV ("Details of Programmes") has the real per-branch degree-plan
  tables: course code, title, L-P-U units, grouped under "CORE COURSES" /
  "DISCIPLINE ELECTIVE COURSES" headers, and a semester-by-semester layout
  for the full degree. This is the primary source for `category` and
  `units` in the Course schema.
- Part VI/VII ("Course Descriptions", on-campus and off-campus) are **not
  in this PDF** — literally printed as "See enclosed CD for Contents". Full
  syllabi never existed in this file; that's what the handouts folder is
  for.
- Prerequisite pairs are almost never listed against a course in the
  degree-plan tables. A full-text search across all 951 pages turns up on
  the order of 10 explicit mentions (e.g. "Prerequisite: GER N101T",
  "(with prerequisite: CE F211: Mechanics of Solids...)"), scattered as ad
  hoc footnotes rather than a systematic column. Treat any prerequisite
  claim sourced from the bulletin as high-confidence *when found*, but do
  not expect to find one for most courses — that's expected, not a parser
  bug.
- Most of the page count is governance listings, admissions/fee schedules,
  hostel/warden rosters, and ISU (international exchange) program tables —
  irrelevant to recommendation and should be skipped/discarded during
  ingestion rather than parsed.

## timetable.pdf (153 pages)

Clean text layer, regular tabular structure. First ~9 pages are the
academic calendar (holidays, exam windows) — useful for a "dates" lookup but
not per-course. From "II. COURSEWISE TIMETABLE" onward, each course block
repeats a fixed column header (COM COD / COURSE NO / COURSE TITLE / CREDIT
L-P-T-S-U / SEC / INSTRUCTOR / ROOM / DAYS & HOURS / MIDSEM / COMPRE), with
one row per section (lecture, tutorial, practical) and "CANCLED" (sic) used
verbatim for sections not running. Reliable to parse row-by-row with a
regex once the header block for a course is located; no rasterization or
OCR needed.

## handouts/ (540 files, `<NNN>_<DEPT>_<CODE>.pdf`)

Clean text layer throughout the sample checked (~24 files across 9+
departments — BIO, CS, EEE, ECON, ME, CHEM, GS, MEL, CE, EE, MF, ECE, PHA,
PHY, MATH, MAC, DE, BITS, FIN, MPBA). No OCR needed.

**Section labels are semantically consistent but positionally inconsistent.**
Every handout has *some* form of "Evaluation Scheme" and, in most cases,
"Make-up Policy" and "Notices" — but:

- Numbering varies: "Attendance Policy" appears as section 7, 8, 9, or 11
  depending on the handout; some handouts have no numbers at all.
- Section presence varies: "Attendance Policy" and "Prerequisites" sections
  are frequently **absent entirely**, not just unlabelled. Of the sampled
  handouts, prerequisites were stated in roughly 1 in 8; across the full
  540, a full-text scan found the words "prerequisite"/"pre-requisite" in
  only 31 files (~6%).
- Label spelling/casing varies ("Make-up Policy", "Makeup Policy", "MAKE-UP",
  "Make up policy").
- A few handouts (observed in ~3/24 sampled) use bulleted lists built with a
  zero-width space (`\u200b`) after the number instead of a period+space,
  e.g. `2.\u200b Text Book` — this silently breaks naive `^\d+\.\s` regexes.
  **Normalize by stripping `\u200b` (and other zero-width/invisible
  Unicode characters) before running any section-header regex.**
- A few handouts have no numbered sections at all and rely purely on
  bold/keyword prose headers ("Course Description:", with no leading
  number) — the extractor must match on the keyword phrase, with the
  number treated as optional, not required.

**Design consequence:** section extraction must be
keyword-anchored (match on the section title text — "Attendance Policy",
"Make-up", "Prerequisite(s)", "Evaluation Scheme", case-insensitive, ignoring
leading numbering/bullets) and must tolerate a section being genuinely
absent. A missing section is `confidence: "unverified"`, not an extraction
failure to retry.

**Prerequisite phrasing is not uniform in strength** even when present:
some are a hard course-code gate ("Pre-requisite of the Course: Electronic
Devices (F-214)"), others are advisory background reading ("a basic
understanding of probability theory... would be helpful" / "is self-
contained, however..."). The extractor should classify these into
`hard_course_code` vs `soft_recommendation` (see `docs/SCHEMA.md`) rather
than treating every match as a hard eligibility gate — an advisory
prerequisite must not block a student from an otherwise-eligible course.

**Evaluation Scheme** is the most reliably structured section — present in
effectively every handout sampled, and formatted as a numbered/tabular list
of components with a `%` weightage each. This is the best field to build
confidence-scoring around first.

## Practical implication for parsing order

Given the above, the sensible build order is:
1. Timetable (most regular, lowest ambiguity) — validates the basic PDF→
   structured-record pipeline end to end.
2. Bulletin Part IV degree-plan tables (regular per-branch, but needs
   department-section boundary detection across ~950 pages).
3. Handouts (least regular — needs the keyword-anchored, tolerant-of-absence
   extractor described above).
4. Regulations (clean but prose-heavy — mostly feeds deterministic rule
   logic in `src/retrieval/`, not the Course/Handout schema).
