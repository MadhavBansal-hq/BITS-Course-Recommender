# BITS Academic Course Recommender

A course recommender for BITS Pilani students, built for Postman Round 2
(AI/ML recruitment task). The goal: from BITS's own documents, work out what a
student still has to take and is allowed to take — deterministically — and only
then rank what is left by the student's interests and timetable preferences,
citing the source page behind every claim and saying "could not be verified"
instead of guessing.

## Status

Work in progress; see the table. Remaining coverage gaps: dual-degree and 2+2 patterns, minors, and the HUEL course list, which no supplied document contains. The full design is in [docs/DESIGN.md](docs/DESIGN.md).

| Stage | Status |
|---|---|
| Timetable → section records (`src/ingest/parse_timetable.py`) | Done: 1,718 sections in 723 offerings; 14 tests |
| Bulletin Part IV → course lists (`src/ingest/parse_bulletin.py`) | Done for *List of Courses* (per-branch core and discipline-elective lists); other Part IV tables only partly structured; 12 tests |
| Handouts → evaluation, midsem/compre, open book, make-up, attendance, prerequisites (`src/ingest/parse_handouts.py`) | Done: 418 of 540 evaluation schemes read (352 complete); midsem verified for 375, "no midsem" only from complete, clean tables (23); the rest flagged |
| Semester-wise patterns → named courses, elective slots, core and DEL requirements (`src/ingest/parse_semester_patterns.py`) | Done for all 28 first-degree programmes (IV-3 to IV-30); dual-degree and 2+2 patterns not yet |
| Programme rules (`src/ingest/parse_programme_rules.py`) | Done: IV-1 category table, the four humanities heads, Regulations 2.04–2.08, timetable registration rules and hour legend, 167 course equivalences |
| Timetable checks (`src/retrieval/timetable.py`) | Done: slot parsing, class and exam clashes, lunch-hour rule, avoid-hours / free-day, clash-free section search that explains why nothing fits |
| Requirement and eligibility engine (`src/retrieval/engine.py`) | Done: named courses (CDC / institutional) with status; DEL / HUEL / OPEL requirement vs. progress; for every offered course its category, eligibility with reasons (completed, current, 2026-only, cancelled, stated prerequisite, higher-degree course of another discipline), handout properties, and an exact timetable-fit check; 27 of 28 programmes mapped to their course lists (BBA has none) |
| Query layer, local and without an API key (`src/retrieval/query.py`, `semantic.py`, `recommend.py`) | Done: free-text requests → category, constraints (no midsem / compre, no attendance requirement, lenient make-up, open book, project-based, fits timetable, avoid hours, free day) and topic; abbreviations expanded from the data; word-vector matching; verified matches ranked before unverified ones; explanations from fields with sources |
| Dashboard | Deferred (decided); the CLI prints the same results |

`src/retrieval/` and `src/dashboard/` are empty packages reserved for the later
stages.

## Source data

Everything is derived from BITS's own documents, which are **not** committed
(see `.gitignore`):

- `Academic-Regulations-2023.pdf` — the rulebook: registration, academic
  requirements, and what a prerequisite, prior preparation and backlog are.
- `bulletin.pdf` — Part IV lists every programme's courses: code, title, L-P-U
  units, and whether a course is core or a discipline elective for each branch.
- `timetable.pdf` — this semester's sections: instructor, room, days & hours,
  midsem and compre dates.
- `handouts/` — 540 per-course handouts: evaluation scheme, make-up and
  attendance policy, prerequisites where stated.

## Setup

1. **Python 3.10 or newer** (developed on 3.12):

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Poppler** (a system package, not pip). The timetable parser and
   `scripts/find_bulletin_page_range.py` call its `pdftotext` and `pdfinfo`.
   - macOS: `brew install poppler`
   - Ubuntu / Debian: `sudo apt install poppler-utils`
   - Windows: `conda install -c conda-forge poppler`, or a prebuilt Poppler for
     Windows with its `bin` folder added to `PATH`

   Check with `pdftotext -v`.

3. **Data.** Copy the contents of the task's dataset folder into `data/raw/`:

   ```
   data/raw/
   ├── Academic-Regulations-2023.pdf
   ├── bulletin.pdf
   ├── timetable.pdf
   └── handouts/        # the 540 NNN_DEPT_CODE.pdf files
   ```

4. **Optional, for meaning-based matching:** `pip install -r requirements-nlp.txt`
   (spaCy and its medium English word vectors, about 40 MB, downloaded from
   GitHub; no API key). Without it, topics are matched by exact words.

## Running

From the repository root:

```bash
python -m src.ingest.parse_timetable   # a few seconds
python -m src.ingest.parse_bulletin    # about a minute (254 pages)
python -m src.ingest.parse_semester_patterns
python -m src.ingest.parse_programme_rules
python -m src.ingest.parse_handouts    # about a minute (540 PDFs)
python -m src.retrieval.engine examples/profile_cs_2-1.json --out report.json   # about 40 s
python -m src.retrieval.recommend examples/profile_cs_2-1.json "Suggest DELs related to AI"
python -m pytest                       # 61 tests, about 3 minutes
python -m pytest -m "not slow"         # skips the full-bulletin test
```

The tests skip themselves if the PDFs are not in `data/raw/`.

## Outputs

Written to `data/processed/` (not committed; regenerate with the commands above):

| File | One record per | Contents |
|---|---|---|
| `timetable.json` | section | offering (`com_cod`), course, credits, instructors, room, days & hours, midsem and compre date and session, cancelled, 2026-admissions-only flag, source page |
| `bulletin_courses.json` | course listing in Part IV | code, title, L-P-U, category (`core` / `discipline_elective`), the list heading it appears under (usually the branch), OR alternative, cross-listed codes, Part IV section, source page |
| `semester_patterns.json` | programme | named courses by year and term (with OR alternatives), elective slots and amounts, discipline core / elective requirement from the page footer, anything unresolved |
| `programme_rules.json` | whole dataset | IV-1 category table, humanities heads, Regulations 2.04–2.08 verbatim, registration rules, hour and exam-session legend, equivalent courses, known gaps |
| `handouts.json` | handout | evaluation components (name, kind, weight, duration, date, nature), has_midsem / has_compre with the basis for each, open-book components, make-up and attendance text, prerequisites (hard / soft / none stated), page references |
| `report_<profile>.json` | student | cached engine report used by `recommend` (rebuilt when the profile changes) |
| `*_needs_verification.json` (one per parser) | flagged item | what the parser saw but could not extract confidently, with page and raw text |

Nothing is silently dropped or guessed: anything uncertain lands in a
verification file. Field-by-field details: [docs/SCHEMA.md](docs/SCHEMA.md).

## Why some things are marked "unverified"

Prerequisites are defined in the Academic Regulations as a relationship between
two courses, but the Regulations leave the actual list to the Bulletin, and the
Bulletin does not tabulate it: a search of all 951 pages finds roughly ten
scattered mentions. Handouts mention prerequisites in 31 of 540 files (about
6%), in wording that ranges from a hard course-code requirement to suggested
background; attendance rules are mentioned in 219 of 540. Rather than invent
rules the documents don't state, the system extracts what is explicitly
stated, marks the rest unverified, and the dashboard will say "could not be
verified".

## Repository layout

```
src/ingest/        parsers: source PDFs → data/processed/*.json
src/retrieval/     dataset loader, requirement and eligibility engine, timetable checks
src/dashboard/     (planned) the student-facing app
scripts/           investigation scripts (locating bulletin Part IV)
examples/          example student profile
tests/             correctness tests against the real PDFs
docs/              extraction notes, schemas, design
data/raw/          source PDFs (not committed)
data/processed/    parser outputs (not committed)
```

## Documentation

- [docs/EXTRACTION_NOTES.md](docs/EXTRACTION_NOTES.md): what the source PDFs
  actually look like, and every quirk the parsers handle.
- [docs/SCHEMA.md](docs/SCHEMA.md): the current outputs field by field, and the
  target dataset.
- [docs/DESIGN.md](docs/DESIGN.md): the planned pipeline and dashboard, and
  open questions.
