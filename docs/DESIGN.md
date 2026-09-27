# Design

The planned system, mapped to the task brief, and the open questions the data
has raised so far. Nothing here is built yet beyond the two parsers (see the
status table in the README).

## Pipeline

Deterministic steps decide what a student must and may take; the LLM only
interprets the request, matches it semantically, and explains.

1. **Ingestion** (`src/ingest/`; done for timetable and bulletin): source PDFs
   to `data/processed/*.json`, every record with its source page, uncertain
   items in the verification files.
2. **Dataset build**: merge the parser outputs into the target schema in
   [SCHEMA.md](SCHEMA.md). The recommendation logic reads only this dataset,
   so a new semester's timetable or handouts means re-running ingestion, not
   changing logic.
3. **Student profile**: campus, admission year, degree or dual degree,
   current semester, completed and current courses, minor, interests.
4. **Requirement analysis** (deterministic): remaining CDCs, DELs, HUELs and
   OPELs for that programme and batch.
5. **Eligibility** (deterministic): completed courses, prerequisites where a
   source states them, restrictions such as the 2026-admissions-only
   sections. An unknown prerequisite is shown as "could not be verified",
   never as passed or failed.
6. **Preference matching** (LLM): turn the query ("DELs related to AI", "an
   OPEL with no attendance requirement", "no midsem, lenient make-up") into
   filters and topics, then match semantically over titles and handout
   content.
7. **Policy validation** (deterministic): timetable clashes, section choice,
   and the query's hard constraints (no 8 am classes, a free weekday).
8. **Recommendations**: each with its requirement bucket, why it matches, and
   the source page for every fact.

## Dashboard (agreed layout)

Three screens, with results laid out like the course cards of
timetable.bits-dvm.org:

1. **Profile setup**: the profile fields above; completed courses picked from
   the parsed course list.
2. **Preferences and query**: a free-text box plus toggles such as "open
   sections only", "no 8 am classes", "a free weekday", "compact schedule".
3. **Results**: a grid of course cards showing code, title, units (L-P-U), a
   requirement badge (CDC / DEL / HUEL / OPEL), eligibility status (including
   "prerequisite could not be verified"), handout properties (midsem, compre,
   attendance, make-up, each "could not be verified" where the handout is
   silent), a one-line reason it matches, and source citations. A persistent
   bar shows the courses and units picked so far.

## Open questions

- **Batch-specific CDCs.** The bulletin's List of Courses has CS F320
  (Foundations of Data Science) only as a discipline elective (COMPUTER
  SCIENCE, IV-110; ROBOTICS AND INDUSTRIAL AUTOMATION, IV-122), but
  timetable.bits-dvm.org shows it among the 2-1 CDCs of a current CS
  student. Requirements can change between admission batches, and the brief
  makes the supplied documents the source of truth. Plan: follow the
  bulletin, show a note when the student's own record disagrees, and
  re-check once the semester-wise patterns are parsed.
- **Semester-wise patterns (IV-3–105)** are needed for CDC timing and for
  common courses such as MATH F211, which appears there. Not parsed yet.
- **OR alternatives** in core lists: either course satisfies the requirement,
  and units may be printed once per group.
- **HUEL and OPEL rules.** IV-106 says which course groups count as
  Humanities electives, and List of Courses has GENERAL STUDIES stream
  lists; the open-elective rule still has to be read from the Regulations.
- **Minors** use "core" for the minor's own core courses; tagged by
  `bulletin_section`, they need their own rules.
- **Joining timetable and bulletin**: split codes (`BITS F101-1` vs `BITS
  F101`), cross-listed codes (`CS G514` / `SS G514`), abbreviated timetable
  titles.
- **2026-only sections**: offerings with com_cod >= 5000 must be excluded for
  students admitted before 2026.
- **Prerequisites** are stated in 31 of 540 handouts and about ten places in
  the bulletin; everything else is "could not be verified".
