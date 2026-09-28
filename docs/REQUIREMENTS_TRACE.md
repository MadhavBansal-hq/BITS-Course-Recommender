# Task brief → implementation

Every requirement in the Postman Round 2 brief, where it is implemented, and
its status. "Partial" and "gap" rows say what is missing.

| Brief section | Requirement | Where | Status |
|---|---|---|---|
| 1 Objective | Determine what the student is required and eligible to take before recommending | `src/retrieval/engine.py`, then `recommend.py` | Done |
| 2 Input data | Use Regulations, Bulletin, timetable, handouts, profile directly; invent nothing | `src/ingest/*`; every fact carries a page / clause / file | Done; the few judgment thresholds (project-based ≥ 30%, match strengths) are ours and shown in the output |
| 3 Pre-processing | Clean structured dataset; retrieve from it, not from PDFs | `data/processed/*.json`, merged `courses.json` (`build_dataset.py`) | Done |
| 3 | Student / Course / Programme rules / Handout / Timetable / Source metadata entities | `docs/SCHEMA.md` | Done; course *department* is known only where the bulletin legend defines the code |
| 3 | Normalise codes and categories; mark unreliable items for verification | parsers + `*_needs_verification.json`, `validation.json` | Done |
| 4 Profile | Campus, admission year, degree / dual degree, semester, completed and current courses, minor, interests; create and update | dashboard sidebar → `profiles/*.json` | Done; campus checked against the data's campus (Pilani); minor and dual degree are stored but **not analysed** (rules not parsed) |
| 5 Behaviour | Remaining CDC / DEL / HUEL / OPEL | engine: pattern (IV-3..30), List of Courses, IV-1, Regulations 2.05 | Done for 27 of 28 first-degree programmes (BBA has no course list); HUEL membership unverifiable (no list in the data) |
| 5 | Prerequisites, restrictions, programme rules | engine: handout prerequisites, equivalences, 2026-only, higher-degree rule, campus | Done as far as the data states them (prerequisite table is only a web link) |
| 5 | Flow: requirements → eligible set → preference matching → policy validation → recommendations | `engine.report()` → `recommend()` | Done; nothing is recommended unless eligible |
| 6 NL queries | Translate queries, retrieve eligible courses, explain why | `query.py`, `semantic.py`, `recommend.py`; dashboard "Ask" | Done; all four example queries in the brief work |
| 7 Handouts | Attendance, evaluation, midsem / compre, projects, quizzes, make-up, instructor, topics; say "could not be verified" | `parse_handouts.py`; recommendation "Properties" | Done |
| 8 Bonus | Class, tutorial, lab, midsem, compre clashes | `timetable.py` | Done |
| 8 | No 8 AM, free weekday, avoid long gaps, compact, choose a feasible section | `timetable.py`, `recommend.py`, dashboard "Timetable" | Done |
| 9 Guidance | Deterministic rules; LLM for intent, matching, explanation | rules are deterministic; intent, matching and explanation are local and deterministic (no LLM, no API key) | Deviation by decision: word vectors instead of an LLM |
| 9 | Keep source references | every record, requirement and recommendation | Done |
| 9 | Validate codes, prerequisites, categories, programme requirements | `build_dataset.py` → `validation.json` | Done (issues reported, not auto-fixed) |
| 9 | New timetable / handouts without changing logic | no page numbers or course lists in code; Part IV located from the PDF | Done |
| 9 | Concise recommendation: requirement, eligibility, properties, why | `recommend.py` output fields | Done |
| 10 | Working dashboard, profile, NL queries, live, nothing hardcoded | `src/dashboard/app.py` | Done |
| 10 | Clean repo with parsing, dataset build, runtime retrieval, dashboard; README setup and run | repo, `README.md` | Done |

## Known gaps

- Dual-degree semester patterns (IV-31..105), 2+2 programmes (IV-142..223) and minors (IV-129..141) are not parsed, so those students' requirements are not analysed.
- No supplied document lists humanities electives or a prerequisite table; both are reported as "could not be verified" rather than guessed.
- The timetable prints some split courses with a part suffix (`BITS F101-1`) that the bulletin writes without it; `validation.json` lists these joins.
