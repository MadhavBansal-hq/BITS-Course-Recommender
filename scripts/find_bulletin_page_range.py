"""
Print where bulletin Part IV starts and ends and its sections, as found from
the PDF by src/ingest/bulletin_pages.py (page footers IV-n and Part IV's own
table of contents). Usage: python scripts/find_bulletin_page_range.py
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingest.bulletin_pages import locate_part_iv  # noqa: E402

iv = locate_part_iv(Path(__file__).resolve().parents[1] / "data" / "raw" / "bulletin.pdf")
print(f"Part IV: physical pages {iv.first_page}-{iv.last_page} (IV-1..IV-{iv.iv(iv.last_page)}); TOC on page {iv.toc_page}")
for a, b, kind in iv.sections:
    print(f"  IV-{a}..IV-{b}  (physical {a + iv.first_page - 1}-{b + iv.first_page - 1})  {kind}")
