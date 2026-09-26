# BITS Academic Course Recommender

Agentic course-recommendation dashboard for BITS Pilani students. Built for
Postman Round 2 (25 Batch AI/ML recruitment task).

## What it does

Given a student's profile (branch, semester, completed courses, interests) and
a natural-language query ("Suggest DELs related to AI with no midsem"), the
system:

1. Computes remaining CDC / DEL / HUEL / OPEL requirements deterministically
   from the structured academic data (no LLM guessing on eligibility).
2. Filters to the eligible course set for that student.
3. Uses an LLM to match the student's stated interests/preferences against
   course topics and handout-derived properties (attendance, evaluation
   pattern, midsem/compre presence, makeup policy).
4. Validates the shortlist against BITS policy rules.
5. Returns recommendations with the reasoning and source citations, and
   states "could not be verified" for any property not reliably extractable
   from the source documents.

## Source data

Everything is derived from BITS's own documents — nothing is invented:

- `data/raw/Academic-Regulations-2023.pdf` — the rulebook (registration,
  minimum academic requirements, prerequisite/prior-preparation/backlog
  definitions).
- `data/raw/bulletin.pdf` — Part IV has the per-branch degree plans (course
  code, title, units, Core/Discipline-Elective categorisation).
- `data/raw/timetable.pdf` — this semester's course-section-slot grid
  (instructor, room, days & hours, midsem/compre dates).
- `data/raw/handouts/*.pdf` — 540 per-course handouts (attendance policy,
  evaluation weightage, makeup policy, prerequisites where stated).

None of these are committed to the repo (see `.gitignore`) — see
[Setup](#setup) for how to supply them.

## Why some things are marked "unverified"

Prerequisites are formally defined in the Academic Regulations (as a strict
pair-relationship between two courses) but the regulations explicitly defer
the actual prerequisite *list* to the Bulletin — and the Bulletin does not
tabulate this systematically; only a handful of scattered footnote-style
mentions exist across 951 pages. Handouts mention prerequisites in only
~6% of cases, and even then the phrasing ranges from a hard course-code gate
to vague recommended background. Rather than inventing a prerequisite chain
that isn't backed by the source documents, the pipeline extracts what's
explicitly stated, tags everything else `unverified`, and the dashboard
says so plainly rather than guessing.

## Architecture

```
Raw PDFs (regulations, bulletin, timetable, handouts)
        │
        ▼
  Pre-processing / extraction  (src/ingest/)
        │  → structured JSON records, each with source + page + confidence
        ▼
  data/processed/*.json   (courses, programme_rules, timetable, students)
        │
        ▼
  Retrieval layer (src/retrieval/)   — deterministic filtering:
        │   requirement analysis → eligible course set → policy validation
        ▼
  Recommendation layer               — LLM used only for:
        │   intent parsing, semantic interest matching, explanation text
        ▼
  Dashboard (src/dashboard/)
```

See `docs/SCHEMA.md` for the full structured-data schema and
`docs/EXTRACTION_NOTES.md` for field-by-field extraction reliability notes.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Place the source PDFs (not included in this repo):
#   data/raw/Academic-Regulations-2023.pdf
#   data/raw/bulletin.pdf
#   data/raw/timetable.pdf
#   data/raw/handouts/<NNN>_<DEPT>_<CODE>.pdf   (540 files)

python -m src.ingest.build_dataset      # runs the full pre-processing pipeline
python -m src.dashboard.app             # launches the dashboard
```

## Repository layout

```
data/raw/            source PDFs (gitignored — supply your own copies)
data/processed/      structured JSON built by the ingestion pipeline
src/ingest/          PDF → structured-record extraction
src/retrieval/       requirement analysis, eligibility, policy validation
src/dashboard/       the student-facing app
tests/               correctness tests for extraction + eligibility logic
scripts/             one-off / maintenance scripts
docs/                schema reference, extraction reliability notes
```

## Status

Early scaffolding — pre-processing pipeline in progress. See commit history.
