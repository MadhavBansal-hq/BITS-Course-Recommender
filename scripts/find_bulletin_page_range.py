"""
One-off script used to determine the physical PDF page range of
bulletin.pdf's Part IV ("Details of Programmes") section, which is what
src/ingest/parse_bulletin.py operates on.

The bulletin's own page footers use a per-Part numbering scheme
("IV-1", "IV-2", ..., "IV-254") rather than a single document-wide page
number, so this scans for the first and last physical page carrying an
"IV-<n>" footer marker.

Result (bulletin.pdf as supplied for this task):
    Part IV physical pages: 209-462  (footer-numbered IV-1 .. IV-254)
    Part V begins at physical page 463 ("PART V / OFF-CAMPUS WORK-
    INTEGRATED LEARNING PROGRAMMES") — confirms the boundary is correct.

Re-run this if bulletin.pdf is ever replaced with a different edition —
the physical page range is NOT guaranteed stable across bulletin
revisions, only the "IV-<n>" footer convention is.
"""
import re
import subprocess
from pathlib import Path


def page_text(pdf_path: Path, page: int) -> str:
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), str(pdf_path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


# A genuine page-footer marker is its OWN line ("IV-109" with nothing else
# on that line). A table-of-contents page ALSO contains bare "IV-<n>"
# lines (confirmed on physical page 9 — a section-label line, not a
# footer) but always several of them on one page, as part of a dotted
# TOC listing; a real content page has exactly one. "Last non-blank line"
# is not a reliable discriminator instead: on two-column degree-plan
# pages the linear text stream can trail off with column content (unit
# numbers, credit ranges) printed after the footer line, since pdftotext
# interleaves the two columns by y-position (see docs/EXTRACTION_NOTES.md
# and parse_bulletin.py's column-handling for why). Requiring the marker
# to appear EXACTLY ONCE on the page is what actually distinguishes a
# real footer from a TOC page — a TOC page always has many.
FOOTER_MARKER_RE = re.compile(r"^\s*IV-(\d+)\s*$", re.MULTILINE)


def find_part_iv_range(pdf_path: Path, search_start=1, search_end=951):
    first_page = None
    max_num = 0
    max_page = None
    consecutive_misses = 0

    for p in range(search_start, search_end + 1):
        text = page_text(pdf_path, p)
        matches = FOOTER_MARKER_RE.findall(text)
        if len(matches) == 1:
            if first_page is None:
                first_page = p
            n = int(matches[0])
            if n > max_num:
                max_num, max_page = n, p
            consecutive_misses = 0
        elif first_page is not None:
            consecutive_misses += 1
            if consecutive_misses > 15:
                break

    return first_page, max_page, max_num


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    pdf_path = root / "data" / "raw" / "bulletin.pdf"
    first_page, last_page, max_num = find_part_iv_range(pdf_path)
    print(f"Part IV: physical pages {first_page}-{last_page} "
          f"(footer IV-1 .. IV-{max_num})")
