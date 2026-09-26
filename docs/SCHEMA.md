# Structured Data Schema

All records produced by `src/ingest/` share two conventions:

- **`source`**: always present, points back to the originating document —
  `{"doc": "bulletin", "page": 106}` or `{"doc": "handout", "file":
  "165_CS_F317.pdf", "section": "Evaluation Scheme"}`. Every fact must be
  traceable.
- **`confidence`**: one of `"extracted"` (found and parsed cleanly),
  `"partial"` (found but ambiguous/incomplete), or `"unverified"` (not
  reliably found — the field is `null` and this is why). Nothing is guessed
  to fill a gap; `unverified` is a valid, expected value, not a bug.

## Course

```json
{
  "course_code": "CS F317",
  "title": "Reinforcement Learning",
  "department": "CS",
  "units": {"L": 3, "P": 0, "U": 3},
  "category": "discipline_elective",       // "cdc" | "discipline_elective" | "huel" | "opel" | null
  "branch_scope": ["B.E. Computer Science"],
  "topics": ["reinforcement learning", "markov decision processes", "..."],
  "prerequisites": {
    "value": ["Linear Algebra", "Probability and Statistics", "Python"],
    "type": "soft_recommendation",          // "hard_course_code" | "soft_recommendation" | null
    "confidence": "extracted",
    "source": {"doc": "handout", "file": "165_CS_F317.pdf", "section": "Pre-requisites"}
  },
  "restrictions": {"value": null, "confidence": "unverified", "source": null},
  "handout": { "...": "see Handout schema below, embedded or joined by course_code" }
}
```

`category` is assigned from Bulletin Part IV per-branch tables (Core Courses
section vs Discipline Elective section headers) — deterministic, not
inferred by an LLM. `prerequisites.type` distinguishes a hard gate ("with
prerequisite: CE F211") from advisory language ("would be helpful") — these
must not be treated the same way by the eligibility engine.

## Handout (per course, keyed by course_code)

```json
{
  "course_code": "BIO F101",
  "attendance_policy": {
    "value": "Each student is expected to attend all classes...",
    "no_attendance_requirement": false,
    "confidence": "extracted",
    "source": {"doc": "handout", "file": "002_BIO_F101.pdf", "section": "Attendance Policy"}
  },
  "midsem": {
    "present": true,
    "weightage_pct": 25,
    "duration_min": 90,
    "nature": "closed_book",
    "confidence": "extracted"
  },
  "compre": {
    "present": true,
    "weightage_pct": 35,
    "nature": "closed_open_book",
    "confidence": "extracted"
  },
  "evaluation_components": [
    {"name": "Mid-Semester Test", "weightage_pct": 25},
    {"name": "Quizzes", "weightage_pct": 15},
    {"name": "Class Participation", "weightage_pct": 5},
    {"name": "Lab", "weightage_pct": 15},
    {"name": "Lab Viva", "weightage_pct": 5},
    {"name": "Comprehensive Examination", "weightage_pct": 35}
  ],
  "makeup_policy": {
    "value": "Make-up only for genuine reasons (medical, family emergency)...",
    "lenient": null,
    "confidence": "extracted"
  },
  "instructor": "Shashi Prakash Singh",
  "project_based": false
}
```

Section extraction is **keyword-anchored, not position-anchored** — handouts
across departments do not use consistent section numbers (e.g. "Attendance
Policy" is section 11 in one handout, section 8 in another, and simply
absent in a third). See `docs/EXTRACTION_NOTES.md`.

## ProgrammeRules

```json
{
  "branch": "B.E. Computer Science",
  "campus": "Pilani",
  "requirements": {
    "cdc_count": 34,          // from Bulletin Part IV degree-plan table, counted
    "del_count": 6,
    "huel_count": 3,
    "opel_count": 5
  },
  "source": {"doc": "bulletin", "page": "IV-xx"}
}
```

## Timetable entry

```json
{
  "course_code": "BIO F101",
  "section": "L1",
  "instructor": "Shashi Prakash Singh",
  "room": "5102",
  "days_hours": "MW 2",
  "midsem": {"date": "09/10", "session": "AN2"},
  "compre": {"date": "14/12", "session": "AN"},
  "com_code": 2863,
  "source": {"doc": "timetable", "page": 4}
}
```

## Student profile

```json
{
  "campus": "Pilani",
  "admission_year": 2025,
  "degree": "B.E. Computer Science",
  "dual_degree": null,
  "current_semester": 3,
  "completed_courses": ["CS F111", "MATH F111", "..."],
  "current_courses": ["CS F211", "..."],
  "minor": null,
  "interests": ["machine learning", "systems"]
}
```

## Verification queue

Anything the ingestion pipeline can't extract reliably is written to
`data/processed/needs_verification.json` instead of being silently dropped
or guessed — a flat list of `{field, course_code, reason, source_hint}`
records a human can later resolve.
