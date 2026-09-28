# Data schemas

Two parts: what the parsers write **today**, field by field, and the
**target** dataset the recommender will read once the remaining parsers and a
dataset-building step exist. Target shapes use `<placeholders>`; no values in
them are real.

## Current outputs

### `data/processed/timetable.json`

One record per section (L1, T2, P3, L1T1, ...). Course-level fields are copied
onto every section of an offering, so each record stands alone.

| Field | Type | Meaning |
|---|---|---|
| `com_cod` | int | Offering id. One course code can have two offerings (e.g. EEE G593: 2144 and 6301), so this, not the course code, identifies an offering. |
| `course_code` | str | Normalised spacing, e.g. `CS F213`. Split courses keep their suffix: `BITS F101-1`. |
| `title` | str | As printed; timetable titles are abbreviated (`FUNDA OF FIN AND ACCOUNT`). |
| `credit_l`, `credit_p`, `credit_t`, `credit_s`, `credit_u` | str | As printed; `-` means blank. |
| `section` | str | `L1`, `T2`, `P3`, `L1T1` (tutorial group 1 of L1), `L1AJ`. |
| `instructors` | list[str] | In print order; may be empty. |
| `room` | str or null | `5102`, `3254_I`, or day-specific `6108(T)`. |
| `days_hours` | str or null | `M W 4 T 10` = Mon and Wed hour 4, Tue hour 10; `T 789` = Tue hours 7-9. |
| `midsem_date`, `midsem_session`, `compre_date`, `compre_session` | str or null | `DD/MM` and session (`FN`, `AN`, `FN1`, `AN2`, ...). |
| `cancelled` | bool | Section printed as `CANCLED` (sic). |
| `only_for_2026_admissions` | bool | `com_cod >= 5000`, per the note printed on every coursewise page. |
| `unparsed_fields` | list[str] | Columns in this row that fit no known shape; also flagged for verification. |
| `source_page` | int | Physical page of `timetable.pdf`. |

A real record:

```json
{
  "com_cod": 2144, "course_code": "EEE G593", "title": "POWER QUALITY",
  "credit_l": "-", "credit_p": "-", "credit_t": "-", "credit_s": "-", "credit_u": "5",
  "section": "L1", "instructors": ["ASHISH PATEL ."],
  "room": "1205", "days_hours": "T Th F 3",
  "midsem_date": "10/10", "midsem_session": "AN2",
  "compre_date": "16/12", "compre_session": "AN",
  "cancelled": false, "only_for_2026_admissions": false,
  "unparsed_fields": [], "source_page": 63
}
```

### `data/processed/bulletin_courses.json`

One record per course listing in bulletin Part IV. A course listed in several
places has several records.

| Field | Type | Meaning |
|---|---|---|
| `course_code` | str | e.g. `CS F213`. |
| `title` | str | As printed, wrapped lines joined. |
| `credit_l`, `credit_p`, `credit_u` | str or null | L P U as printed (`-` = blank); a single-number row gives only `credit_u`. Null when the row prints none, e.g. later members of an OR group whose units are printed once for the group. |
| `category` | str or null | `core` or `discipline_elective`, from the list header. Meaningful only with `list_heading`, and only under `list_of_courses`; under `minor_programmes`, `core` means the minor's core. |
| `list_heading` | str or null | The heading the course is listed under, usually the branch: `COMPUTER SCIENCE`. |
| `bulletin_section` | str | Part IV section from its own table of contents: `programme_structure`, `semester_wise_patterns`, `dual_degree_semester_patterns`, `list_of_courses`, `minor_programmes`, `international_2plus2_programmes`, `higher_degree_programmes`, `phd_programme`. |
| `alternative_to` | str or null | Set on the second course of an "X / OR / Y" pair: either one satisfies the requirement. |
| `aliases` | list[str] | Cross-listed codes: `CS G514` has `["SS G514"]`. |
| `footnote_marker` | bool | A `*` on the code, units or title refers to a footnote on that page. |
| `source_page` | int | Physical page of `bulletin.pdf`. |
| `source_page_label` | str | The page's own footer, e.g. `IV-109`. |

A real record (the same course is listed again on IV-110 as a
`discipline_elective` under `ELECTRICAL AND ELECTRONICS ENGINEERING`):

```json
{
  "course_code": "CS F213", "title": "Object Oriented Programming",
  "credit_l": "3", "credit_p": "1", "credit_u": "4",
  "category": "core", "list_heading": "COMPUTER SCIENCE",
  "bulletin_section": "list_of_courses",
  "alternative_to": null, "aliases": [], "footnote_marker": false,
  "source_page": 317, "source_page_label": "IV-109"
}
```

### `data/processed/*_needs_verification.json`

A list of flagged items, one file per parser:

```json
{"field": "<what kind of thing>", "course_code": "<code or null>",
 "reason": "<why it could not be extracted confidently>",
 "source_hint": {"doc": "timetable|bulletin", "page": "<physical page>", "text": "<raw line>"}}
```

### Engine report and recommendations

`python -m src.retrieval.engine PROFILE` returns `programme` (pattern page,
matched list heading), `named` (each named slot: options, titles, year/term,
category CDC or institutional, status, source), `electives` (DEL / HUEL / OPEL
required vs. completed, with sources and, for OPEL, the derivation), `offered`
(every course in the timetable: category and its basis, eligible, blocked_by,
prerequisite status and source, fits_current_timetable True / False / None,
exam clashes, handout properties, listed_for_first_degree) and `notes`.
`python -m src.retrieval.recommend PROFILE "QUERY"` returns the parsed query,
the matching mode, and ranked results with `unverified` constraints and a
`why` list of reasons, each with its source.

## Target (planned)

**Source and confidence convention.** Every extracted fact will carry where it
came from and whether the source actually states it:

```json
"source": {"doc": "<regulations|bulletin|timetable|handout>", "page": "<n>", "label": "<e.g. IV-109>"},
"confidence": "extracted | unverified"
```

`unverified` means the source doesn't state it, or it couldn't be parsed; the
dashboard shows "could not be verified". Today the parsers express the same
thing by routing uncertain items to the verification files, and carry
`source_page` (plus `source_page_label` for the bulletin) instead of a
`source` object.

**Course** (merged view, one per course code):

```json
{
  "course_code": "<DEPT NNNN>",
  "title": "<bulletin title, else timetable title>",
  "units": {"l": "<int or null>", "p": "<int or null>", "u": "<int>"},
  "listings": [{"category": "<core|discipline_elective>", "list_heading": "<branch>",
                "alternative_to": "<code or null>", "source": {}}],
  "offerings": ["<com_cod>"],
  "handout": "<Handout or null>"
}
```

**Handout:**

```json
{
  "course_code": "<code>",
  "evaluation": [{"component": "<name>", "weight_pct": "<number>", "notes": "<e.g. closed book>"}],
  "has_midsem": "<bool or null>", "has_compre": "<bool or null>",
  "makeup_policy": "<text or null>", "attendance_policy": "<text or null>",
  "prerequisites": [{"text": "<as printed>", "kind": "<hard_course_code|soft_recommendation>",
                     "course_codes": ["<code>"]}],
  "source": {"doc": "handout", "file": "<NNN_DEPT_CODE.pdf>", "page": "<n>"}
}
```

**ProgrammeRules** (per programme and admission batch; every value to be
extracted from the Regulations and Bulletin Part IV, none assumed):

```json
{
  "programme": "<programme>", "admission_year": "<yyyy>",
  "cdc": ["<codes, with OR alternatives>"],
  "del_required": "<n>", "huel_required": "<n>", "opel_required": "<n>",
  "source": ["<source>"]
}
```

**StudentProfile** (implemented in `src/retrieval/engine.py`; see
`examples/profile_cs_2-1.json`; `completed_categories` optionally tags
completed electives as HUEL / DEL / OPEL):

```json
{
  "campus": "<campus>", "admission_year": "<yyyy>",
  "degree": "<programme>", "dual_degree": "<programme or null>",
  "current_semester": "<e.g. 2-1>",
  "completed_courses": ["<code>"], "current_courses": ["<code>"],
  "minor": "<name or null>", "interests": ["<free text>"]
}
```
