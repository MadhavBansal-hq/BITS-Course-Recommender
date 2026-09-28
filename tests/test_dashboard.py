"""Smoke test: the dashboard loads the example profile, shows requirements
and answers a query, without errors. Requires streamlit and data/processed/."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
st_testing = pytest.importorskip("streamlit.testing.v1")
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "handouts.json").exists(),
                                reason="run the ingest parsers first")


def test_dashboard_answers_a_query():
    at = st_testing.AppTest.from_file(str(ROOT / "src" / "dashboard" / "app.py"), default_timeout=240)
    at.run()
    at.sidebar.selectbox[0].set_value("examples/profile_cs_2-1.json").run()
    at.sidebar.checkbox[0].set_value(False).run()          # skip the 40 s fit search in tests
    assert not at.exception
    assert any("Named courses still to do (14)" in s.value for s in at.subheader)
    at.text_input(key="query").set_value("Suggest DELs related to AI").run()
    assert not at.exception
    assert any("CS F407" in m.value for m in at.markdown)
