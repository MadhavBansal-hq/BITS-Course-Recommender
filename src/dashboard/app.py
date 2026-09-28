"""
Dashboard: create or update a student profile, see the calculated academic
requirements, ask natural-language course questions, and plan a clash-free
timetable. Everything is computed live from data/processed/ by the same
engine and recommender as the CLI; nothing here holds a course list or rule.

Run from the repository root:  streamlit run src/dashboard/app.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from src.retrieval.dataset import load  # noqa: E402
from src.retrieval.engine import Engine, StudentProfile  # noqa: E402
from src.retrieval.recommend import recommend  # noqa: E402
from src.retrieval.semantic import Matcher  # noqa: E402
from src.retrieval.timetable import blocked_by_preferences, find_schedules, parse_slots  # noqa: E402

PROFILES = ROOT / "profiles"
EXAMPLES = ROOT / "examples"
st.set_page_config(page_title="BITS Course Recommender", layout="wide")


@st.cache_resource(show_spinner="Loading the processed dataset...")
def dataset():
    return load(ROOT)


@st.cache_resource(show_spinner="Loading the local word vectors...")
def matcher():
    return Matcher()


@st.cache_data(show_spinner="Analysing requirements and checking every offered course against your timetable...")
def report_for(profile_json: str, check_fit: bool) -> dict:
    return Engine(dataset(), StudentProfile(**json.loads(profile_json)), check_fit=check_fit).report()


if not (ROOT / "data" / "processed" / "handouts.json").exists():
    st.error("No processed data found. Run the ingestion steps in the README first (python -m src.ingest.build_all).")
    st.stop()
ds = dataset()
legend = ds.rules["timetable_legend"]
days_legend = legend["days"]
hours_legend = {int(k): v for k, v in legend["hours"].items()}
all_codes = sorted(set(ds.titles) | {o for p in ds.patterns.values() for s in p["named"] for o in s["options"]})
label = lambda c: f"{c}  {ds.titles.get(c) or ''}".strip()

# ------------------------------------------------------------------ profile
st.sidebar.title("Student profile")
saved = sorted(PROFILES.glob("*.json")) + sorted(EXAMPLES.glob("*.json"))
choice = st.sidebar.selectbox("Load a profile", ["(new profile)"] + [str(p.relative_to(ROOT)) for p in saved])
base = StudentProfile(programme=sorted(ds.patterns)[0], admission_year=2025, semester="1-1",
                      campus=(ds.rules.get("data_campus") or {}).get("campus") or "")
if choice != "(new profile)":
    base = StudentProfile(**json.loads((ROOT / choice).read_text(encoding="utf-8")))

with st.sidebar.form("profile"):
    name = st.text_input("Profile name", value=Path(choice).stem if choice != "(new profile)" else "my_profile")
    programmes = sorted(ds.patterns)
    programme = st.selectbox("Degree", programmes, index=programmes.index(base.programme) if base.programme in programmes else 0)
    dual = st.selectbox("Dual degree (if any)", ["(none)"] + programmes,
                        index=(programmes.index(base.dual_degree) + 1) if base.dual_degree in programmes else 0)
    c1, c2 = st.columns(2)
    # only campuses the supplied data covers (read from the timetable)
    campuses = [c for c in [(ds.rules.get("data_campus") or {}).get("campus")] if c]
    campus = c1.selectbox("Campus", campuses, help="The supplied timetable and handouts cover only this campus.")
    year = c2.number_input("Admission year", 2000, 2100, value=base.admission_year)
    semester = st.text_input("Current semester (year-term, e.g. 2-1)", value=base.semester)
    completed = st.multiselect("Completed courses", all_codes, default=[c for c in base.completed if c in all_codes], format_func=label)
    current = st.multiselect("Current courses", all_codes, default=[c for c in base.current if c in all_codes], format_func=label)
    huel = st.multiselect("Completed courses that counted as HUEL (the data has no HUEL list)", all_codes,
                          default=[c for c, k in base.completed_categories.items() if k == "HUEL"], format_func=label)
    minor = st.text_input("Minor (if any)", value=base.minor or "")
    interests = st.text_input("Academic interests (comma separated)", value=", ".join(base.interests))
    submitted = st.form_submit_button("Save profile")

profile = StudentProfile(programme=programme, admission_year=int(year), semester=semester.strip(), campus=campus.strip(),
                         completed=completed, current=current, completed_categories={c: "HUEL" for c in huel},
                         dual_degree=None if dual == "(none)" else dual, minor=minor.strip() or None,
                         interests=[i.strip() for i in interests.split(",") if i.strip()])
if submitted:
    PROFILES.mkdir(exist_ok=True)
    (PROFILES / f"{name.strip() or 'my_profile'}.json").write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
    st.sidebar.success(f"Saved profiles/{name.strip() or 'my_profile'}.json")
if not __import__("re").fullmatch(r"\d-(1|2)", profile.semester):
    st.sidebar.error("Semester must look like 2-1 (year 2, first semester).")
    st.stop()
check_fit = st.sidebar.checkbox("Check timetable fit for every course (about 40 s)", value=True)
report = report_for(json.dumps(asdict(profile), sort_keys=True), check_fit)

st.title("BITS Academic Course Recommender")
st.caption(f"{profile.programme} · admitted {profile.admission_year} · semester {profile.semester} · "
           f"pattern {report['programme']['pattern']} · course list '{report['programme']['list_heading']}'")
for n in report["notes"]:
    st.info(n)

tab_req, tab_ask, tab_tt, tab_all = st.tabs(["1 · Requirements", "2 · Ask", "3 · Timetable", "All offered courses"])

# ------------------------------------------------------------- requirements
with tab_req:
    e = report["electives"]
    cols = st.columns(3)
    for col, key in zip(cols, ("DEL", "HUEL", "OPEL")):
        d = e[key]
        col.metric(key, f"{d.get('completed_units', 0)} / {d['required_units']} units",
                   help=f"{d['required_courses']} courses required. Source: {d.get('source') or d.get('method')}")
    st.caption(e["HUEL"]["note"])
    doing = [r["satisfied_by"] for r in report["named"] if r["status"] == "in progress"]
    todo = [r for r in report["named"] if r["status"] not in ("completed", "in progress")]
    st.subheader(f"Named courses still to do ({len(todo)})")
    if doing:
        st.caption(f"In progress this semester: {', '.join(doing)}")
    st.dataframe([{"when": f"Y{r['year']} {'summer' if r['term'] == 'summer' else 'T' + r['term']}", "status": r["status"],
                   "course": " or ".join(r["options"]), "title": " / ".join(t or "?" for t in r["titles"]),
                   "category": r["category"], "units": r["units"], "source": r["source"]} for r in todo],
                 width='stretch', hide_index=True)

# --------------------------------------------------------------------- ask
with tab_ask:
    st.write("Ask in plain English, e.g. *Suggest an AI-related DEL with no midsem*, *Find an OPEL with no "
             "attendance requirement*, *I need a HUEL and prefer project-based evaluation*, *DEL on machine "
             "learning with no 8am classes and Friday free*.")
    query = st.text_input("Your question", key="query")
    top = st.slider("How many suggestions", 3, 15, 6)
    if query:
        res = recommend(report, ds, query, matcher(), top=top)
        q = res["query"]
        st.caption(f"Understood: category **{q['category'] or 'any'}**, constraints **{', '.join(q['constraints']) or 'none'}**"
                   + (f", avoid hours {q['avoid_hours']}" if q["avoid_hours"] else "")
                   + (f", free day {days_legend.get(q['free_day'], q['free_day'])}" if q["free_day"] else "")
                   + (f", topic **{q['topic_expanded'] or q['topic']}**" if q["topic"] else "")
                   + f" · matching: {res['matching']} · {res['total_matches']} eligible matches")
        for n in res["notes"]:
            st.warning(n)
        for r in res["results"]:
            with st.container(border=True):
                a, b = st.columns([4, 1])
                a.markdown(f"**{r['course_code']} · {r['title']}** ({r['units']} units)")
                b.markdown(f"`{r['category'].split(' (')[0]}`")
                st.markdown(f"**Requirement:** {r['requirement']}  \n**Eligibility:** {r['eligibility']}  \n"
                            f"**Why:** {'; '.join(r['why'])}")
                with st.expander("Course properties (from the handout)"):
                    st.write("\n".join(f"- {p}" for p in r["properties"]))
                if r["unverified"]:
                    st.caption(f"Could not be verified: {', '.join(r['unverified'])}")

# ---------------------------------------------------------------- timetable
with tab_tt:
    offered_ok = sorted({o["course_code"] for o in report["offered"] if o["eligible"]} | set(profile.current))
    picks = st.multiselect("Courses to fit together (your current courses are preselected)", offered_ok,
                           default=[c for c in profile.current if c in offered_ok], format_func=label)
    c1, c2, c3 = st.columns(3)
    avoid = c1.multiselect("Avoid hours", sorted(hours_legend), format_func=lambda h: f"{h} ({hours_legend[h]})")
    free = c2.selectbox("Keep a day free", ["(none)"] + list(days_legend), format_func=lambda d: days_legend.get(d, d))
    n_opts = c3.slider("Options to show", 1, 5, 3)
    if picks:
        valid = set(hours_legend)
        lunch = ds.rules["registration"]["lunch_hours"]
        adm = profile.admission_year
        offs = {c: [s for s in ds.offerings.get(c, []) if not s["cancelled"] and (adm >= 2026 or not s["only_for_2026_admissions"])]
                for c in picks}
        stats = {}
        plans = find_schedules(offs, valid, lunch, set(avoid), None if free == "(none)" else free, limit=n_opts,
                               max_nodes=100_000, stats=stats)
        if not plans:
            st.error("No clash-free combination (classes, exams and a free lunch hour every day)"
                     + ("" if stats.get("complete") else " was found within the search budget") + ".")
            for c, why in blocked_by_preferences(offs, valid, set(avoid), None if free == "(none)" else free).items():
                st.write(f"- {c}: {why}")
        for i, plan in enumerate(plans, 1):
            st.markdown(f"**Option {i}:** {plan['days']} teaching days, {plan['gaps']} free hours between classes · "
                        + " · ".join(f"{c} {'/'.join(s)}" for c, s in plan["sections"].items()))
            grid = {h: {d: "" for d in days_legend} for h in sorted(hours_legend)}
            for c, secs in plan["sections"].items():
                for s in offs[c]:
                    if s["section"] in secs:
                        for d, h in parse_slots(s["days_hours"], valid):
                            grid[h][d] = (grid[h][d] + " " if grid[h][d] else "") + f"{c} {s['section']}"
            st.dataframe([{"hour": f"{h} ({hours_legend[h]})", **{days_legend[d]: grid[h][d] for d in days_legend}}
                          for h in grid], width='stretch', hide_index=True)

# ------------------------------------------------------------- all offered
with tab_all:
    only_ok = st.checkbox("Only courses I am eligible for", value=True)
    rows = [{"course": o["course_code"], "title": o["title"], "units": o["units"], "category": o["category"],
             "eligible": o["eligible"], "blocked by": "; ".join(o["blocked_by"]), "fits my timetable": o["fits_current_timetable"],
             "midsem": (o["handout"] or {}).get("has_midsem"), "prerequisite": o["prerequisite"]["status"]}
            for o in report["offered"] if o["eligible"] or not only_ok]
    st.dataframe(rows, width='stretch', hide_index=True)
