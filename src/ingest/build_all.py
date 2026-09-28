"""
Run the whole ingestion pipeline in order, from data/raw/ to
data/processed/: timetable, bulletin Part IV, semester patterns, programme
rules, handouts, then the merged dataset and its validation report.
Usage (from the repository root):  python -m src.ingest.build_all
"""
from __future__ import annotations

import time

from src.ingest import (build_dataset, parse_bulletin, parse_handouts, parse_programme_rules,
                        parse_semester_patterns, parse_timetable)

STEPS = [("timetable", parse_timetable), ("bulletin Part IV", parse_bulletin),
         ("semester-wise patterns", parse_semester_patterns), ("programme rules", parse_programme_rules),
         ("handouts", parse_handouts), ("merged dataset + validation", build_dataset)]


def main() -> None:
    for name, module in STEPS:
        t = time.time()
        print(f"== {name}")
        module.main()
        print(f"   ({time.time() - t:.0f} s)")


if __name__ == "__main__":
    main()
