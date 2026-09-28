"""
Locate bulletin Part IV and its sections from the PDF itself, so nothing
about this edition's page numbers is hardcoded:

- Part IV's physical page range: pages whose footer is "IV-n" (exactly one
  IV-n footer line per page); the offset physical - n must be constant;
- its sections: Part IV's own table of contents, where every entry ends in
  its IV page or range ("List of Courses for B.E. / M.Sc. / B.Pharm.
  Programmes ... IV-106-128"). Entries are classified by their wording and
  consecutive entries of the same kind are merged; a section runs until
  the next one starts.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FOOTER_RE = re.compile(r"^\s*IV-(\d+)\s*$")
TOC_ENTRY_RE = re.compile(r"^(?P<text>.*?[A-Za-z)].*?)[\s.…]+IV-(?P<a>\d+)(?:-(?P<b>\d+))?\s*$")
KINDS = [  # first match wins
    ("list_of_courses", r"List of Courses"),
    ("minor_programmes", r"Minor"),
    ("international_2plus2_programmes", r"2\+2|International"),
    ("higher_degree_programmes", r"Higher Degree"),
    ("phd_programme", r"Ph\.?\s?D"),
    ("dual_degree_semester_patterns", r"(Semester-wise|Pattern).*(Dual|Composite)|(Dual|Composite).*Pattern"),
    ("semester_wise_patterns", r"Semester-wise"),
    ("programme_structure", r"."),
]


@dataclass(frozen=True)
class PartIV:
    first_page: int                                  # physical page of IV-1
    last_page: int
    sections: tuple[tuple[int, int, str], ...]       # (first IV page, last IV page, kind)
    toc_page: int | None

    def iv(self, physical: int) -> int:
        return physical - self.first_page + 1

    def section_for(self, physical: int) -> str | None:
        n = self.iv(physical)
        return next((k for a, b, k in self.sections if a <= n <= b), None)


@lru_cache(maxsize=4)
def locate_part_iv(pdf_path: Path) -> PartIV:
    text = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"],
                          capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    footers = {}
    for i, page in enumerate(pages, start=1):
        hits = [int(m[1]) for line in page.splitlines() if (m := FOOTER_RE.match(line))]
        if len(hits) == 1:
            footers[i] = hits[0]
    offsets = {p - n for p, n in footers.items()}
    if len(offsets) != 1:
        raise ValueError(f"Part IV footers do not give one constant page offset: {sorted(offsets)[:5]}")
    first, last = min(footers), max(footers)
    iv_last = footers[last]

    toc_page, entries = None, []
    for i, page in enumerate(pages, start=1):
        if i >= first:
            break
        found = [m for line in page.splitlines() if (m := TOC_ENTRY_RE.match(line.strip()))]
        if any("List of Courses" in m["text"] for m in found) and len(found) >= 5:
            toc_page, entries = i, found
            break
    if not entries:
        raise ValueError("Part IV table of contents not found")
    starts = []
    for m in entries:
        kind = next(k for k, pat in KINDS if re.search(pat, m["text"]))
        if not starts or starts[-1][1] != kind:
            starts.append((int(m["a"]), kind))
    sections = tuple((a, (starts[j + 1][0] - 1) if j + 1 < len(starts) else iv_last, kind)
                     for j, (a, kind) in enumerate(starts))
    return PartIV(first, last, sections, toc_page)
