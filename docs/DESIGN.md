# Design

The planned system, mapped to the task brief, and the open questions the data
has raised so far. Everything is built; see the README status table and REQUIREMENTS_TRACE.md.

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

## How the engine applies the rules

`src/retrieval/engine.py` takes a profile (programme as named in its semester
pattern, admission year, semester such as "2-1", completed and current
courses, optionally the category of completed electives) and reports:

- named courses from the pattern, labelled CDC when they are also in the
  programme's CORE list, else institutional, with status completed / in
  progress / overdue / this semester / later; completed and current courses
  are expanded through the equivalence table;
- DEL from the pattern footer, HUEL from IV-1, OPEL derived as IV-1's
  course-work minimum minus named course-work, DEL and HUEL (for B.E.
  Computer Science: 129 - 94 - 12 - 8 = 15 units, IV-1's own minimum);
- for each course offered this semester: DEL if in the programme's elective
  list, HUEL only if its handout says so, otherwise an Open Elective with
  HUEL status unverified (Regulations 2.05); blocked if completed, current,
  2026-only, fully cancelled, or a stated prerequisite is missing; plus
  handout properties and whether any section fits beside the current
  courses.

## Query layer (decided: local, no API key)

The brief allows an LLM for intent, semantic matching and explanation. We
chose a local, deterministic layer instead, so the system runs without a key
and gives the same answer every time:

- **Intent** (`query.py`): category words and constraint phrases map onto
  fields the pipeline already extracts; day names and clock times resolve
  through the timetable's own legend ("no 8am" = hour 1, "Saturday" = S).
  Whatever is left is the topic.
- **Matching** (`semantic.py`): spaCy's medium English word vectors, word by
  word, with weak similarities (< 0.45) discounted. Abbreviations are
  expanded first using long forms learned from the dataset's titles and
  handouts (the vector for "AI" matched "Fuzzy" and "garbage"; "Artificial
  Intelligence" is in the titles). Averaging whole phrases was rejected:
  "Principles of Economics" outscored "Data Mining" for "artificial
  intelligence and machine learning".
- **Filtering** (`recommend.py`): a course that verifiably breaks a
  constraint is dropped; one whose property could not be verified is kept,
  ranked after the verified ones, and labelled. "Lenient make-up" ranks by
  how few conditions the handout attaches, because nearly every handout
  attaches some ("genuine cases only").
- **Explanations** are assembled from fields, each with its source; nothing
  is generated freely.

**HUEL.** No supplied document lists humanities electives (bulletin IV-2
names four heads; timetable part VIII points back to the bulletin). Timetable
part V(A)(f) advises taking "Humanities (HUM), Humanities and Social Science
(HSS) courses as electives", so a HUEL request returns those areas' courses,
labelled "HUEL status could not be verified". Matching department names to
the four heads with word vectors was tried and rejected: Physics scored 0.50
against "Languages and Literature", English 0.11.

**Higher-degree courses.** Timetable part V(A): a first-degree student may
take one per semester, after clearing or registering in the CDC of that
course's discipline; the engine blocks other disciplines' G courses.

## Open questions

- **CS F320 (decided): follow the bulletin.** It is a discipline elective for
  COMPUTER SCIENCE (IV-110) and is not a named course in the CS semester-wise
  pattern (IV-9), so it is not a CDC in this system, whatever other tools show.
- **Dual-degree and 2+2 patterns** (IV-31–105, IV-142–223) are not parsed yet;
  single-degree patterns are.
- **OR alternatives** in core lists: either course satisfies the requirement,
  and units may be printed once per group.
- **HUEL pool (data gap).** The rules are extracted (IV-1, IV-2, Regulations
  2.05) and timetable part V names HUM and HSS as the humanities areas, but no
  document lists which courses count. HUEL status stays "could not be
  verified" unless a handout states it.
- **Programme-restricted courses (Regulations 2.07).** Some courses are named
  for specific programmes and debarred to others; the dataset does not say
  which. Courses that no first-degree list or pattern mentions (e.g. the PhD
  seminar BITS C797T) carry a caveat and rank last.
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
