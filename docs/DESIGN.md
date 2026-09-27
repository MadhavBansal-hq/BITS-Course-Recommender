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

- **CS F320 (decided): follow the bulletin.** It is a discipline elective for
  COMPUTER SCIENCE (IV-110) and is not a named course in the CS semester-wise
  pattern (IV-9), so it is not a CDC in this system, whatever other tools show.
- **Dual-degree and 2+2 patterns** (IV-31–105, IV-142–223) are not parsed yet;
  single-degree patterns are.
- **OR alternatives** in core lists: either course satisfies the requirement,
  and units may be printed once per group.
- **HUEL pool (data gap).** The rules are extracted (IV-1, IV-2, Regulations
  2.05), but no document lists which courses are humanities electives. A
  course's HUEL status is "could not be verified" unless its handout says so.
- **Minors** use "core" for the minor's own core courses; tagged by
  `bulletin_section`, they need their own rules.
- **Joining timetable and bulletin**: split codes (`BITS F101-1` vs `BITS
  F101`), cross-listed codes (`CS G514` / `SS G514`), abbreviated timetable
  titles.
- **2026-only sections**: offerings with com_cod >= 5000 must be excluded for
  students admitted before 2026.
- **Prerequisites (decided): only what the dataset states** (31 of 540
  handouts, about ten bulletin mentions; timetable part VI is just a link).
  Everything else is "could not be verified".
