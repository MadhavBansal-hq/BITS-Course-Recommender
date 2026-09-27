"""
Parse course handouts (data/raw/handouts/NNN_DEPT_CODE.pdf) into the
properties the recommender filters on: evaluation scheme, midsem / compre,
open or closed book, make-up and attendance policy, stated prerequisites,
and whether the handout calls the course a humanities elective.

Findings this is built on (docs/EXTRACTION_NOTES.md, "handouts/"):
- Section labels are consistent in meaning, not in number or spelling
  ("7. Evaluation Scheme", "6. Evaluation Scheme:", "Make-up Policy",
  "Makeup", "MAKE-UP"), and some handouts are unnumbered, so sections are
  found by keyword, and zero-width spaces (U+200B, in 23 handouts) are
  stripped first.
- The evaluation scheme is a table with a header row (Component / Duration
  / Weightage / Date & Time / Nature of component). Component names wrap
  over several lines and a percentage can appear in the Duration column
  ("70% classes" in GS F212), so every token is assigned to the header
  column it sits under, not by what it looks like.
- Absence is never evidence by itself. `has_midsem` is True when a
  mid-semester component is listed; False only when the evaluation table is
  complete (weights sum to 95-105%) and lists none; otherwise None ("could
  not be verified"). Every value records its basis and page.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
CODE_RE = re.compile(r"\b([A-Z]{2,5})\s?([FGCU]\d{3}[A-Z]?)\b")
SHORT_CODE_RE = re.compile(r"\(\s*[FGC]\s*-?\s*\d{3}\s*\)")   # "(F-214)"
HEADING_RE = re.compile(r"^\s*(?:\d{1,2}\s*[.)]\s*)?(?P<title>[A-Z][A-Za-z &/\-()]{2,45}?)\s*(?::|\s{2,}|$)")
BOILERPLATE_RE = re.compile(r"BIRLA INSTITUTE|Pilani Campus|Goa Campus|Hyderabad Campus|Dubai Campus|AUGS|AGSR|Division$|^\s*\d{1,3}\s*$")
WEIGHT_RE = re.compile(r"^(\d{1,3}(?:\.\d+)?)\s*%?\s*[*#]*$")  # "25 %", "7.5%", "20*", "5#"
KINDS = [  # first match wins; "Mid - semester", "Mid. Semester", "Mid Examination"
    ("midsem", r"\bmid\b|mid\s*[\-.]?\s*(?:sem|term|exam|test)"),
    ("compre", r"compre|comprehensive|end\s*[\-.]?\s*sem|final\s+exam"),
    ("attendance", r"attendance|participation"),
    ("lab", r"\blab|practical|experiment"),
    ("project", r"project"),
    ("assignment", r"assignment|homework|home work"),
    ("quiz", r"quiz"),
    ("presentation", r"presentation|seminar|viva"),
    ("test", r"\btest|exam|evaluation"),
]
SECTION_KEYS = {
    "evaluation": r"evaluation\s+(scheme|components?|pattern)",
    "makeup": r"make[\s\-]?up",
    "attendance": r"attendance",
    "prerequisite": r"pre[\s\-]?requisites?",
}


@dataclass
class Component:
    name: str
    kind: str
    duration: str = ""
    weight_pct: float | None = None
    date: str = ""
    nature: str = ""


@dataclass
class Handout:
    file: str
    course_code: str
    course_code_in_text: str | None = None
    course_title: str | None = None
    instructor_in_charge: str | None = None
    pages: int = 0
    text_layer: bool = True
    evaluation: list[Component] = field(default_factory=list)
    evaluation_page: int | None = None
    weight_total: float | None = None
    evaluation_complete: bool = False
    has_midsem: bool | None = None
    has_midsem_basis: str | None = None
    has_compre: bool | None = None
    has_compre_basis: str | None = None
    open_book_components: list[str] = field(default_factory=list)
    makeup_policy: dict | None = None
    attendance_policy: dict | None = None
    prerequisites: list[dict] = field(default_factory=list)
    humanities_elective_statement: dict | None = None
    unresolved: list[str] = field(default_factory=list)


def _kind(name: str) -> str:
    low = name.lower()
    return next((k for k, pat in KINDS if re.search(pat, low)), "other")


def _lines(pdf: Path) -> list[tuple[int, str]]:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    return [(pno, ZERO_WIDTH.sub("", line)) for pno, page in enumerate(out.split("\f"), start=1)
            for line in page.splitlines()]


def _is_heading(line: str) -> bool:
    m = HEADING_RE.match(line)
    return bool(m) and re.match(r"^\s*\d{1,2}\s*[.)]", line) is not None


def _section(lines, key: str):
    """(page, text) of the first section whose heading matches `key`, up to
    the next numbered heading; or of the first sentence mentioning it."""
    pat = re.compile(SECTION_KEYS[key], re.I)
    for i, (pno, line) in enumerate(lines):
        if pat.search(line) and (_is_heading(line) or re.match(r"^\s*[A-Z][A-Za-z\- ]{2,30}(Policy)?\s*:", line)):
            body = [line.strip()]
            for _, nxt in lines[i + 1:i + 25]:
                if _is_heading(nxt) or BOILERPLATE_RE.search(nxt.strip()):
                    if _is_heading(nxt):
                        break
                    continue
                if nxt.strip():
                    body.append(nxt.strip())
            return {"page": pno, "text": " ".join(" ".join(body).split()), "basis": "section"}
    for pno, line in lines:
        if pat.search(line):
            return {"page": pno, "text": " ".join(line.split()), "basis": "mention"}
    return None


HEADER_LABELS = (("name", r"(?:Evaluation\s+)?Components?|EC\s*No"), ("duration", r"Duration"),
                 ("weight", r"Wei\w*age|Weight|Marks"), ("date", r"Date"), ("nature", r"Nature|Remarks|Mode"))


def _header(lines):
    """Find the evaluation table header. Its labels may be spread over up to
    three lines ("EC / Evaluation / Component" beside "Weightage (%)"), so
    labels are collected from a small window around the weight label.
    Returns (index of the last header line, columns) or None."""
    for i, (_, line) in enumerate(lines):
        if not re.search(HEADER_LABELS[2][1], line, re.I):
            continue
        window = range(max(0, i - 2), min(len(lines), i + 3))
        cols, last = {}, i
        for j in window:
            for label, pat in HEADER_LABELS:
                if label not in cols and (m := re.search(pat, lines[j][1], re.I)):
                    cols[label] = ((m.start() + m.end()) / 2, m.start())
                    last = max(last, j)
        # A real header names the weight column and at least two others;
        # "marks" in running prose does not.
        if "weight" in cols and len(cols) >= 3 and (
                "name" in cols or re.search(r"evaluation", " ".join(lines[j][1] for j in window), re.I)):
            ordered = sorted(((k, *v) for k, v in cols.items()), key=lambda c: c[2])
            if ordered[0][0] != "name":           # names sit left of the first label
                ordered.insert(0, ("name", 0.0, 0))
            return last, ordered
    return None


def _assign(token_start: int, token_end: int, cols, token: str = "") -> str:
    centre = (token_start + token_end) / 2
    if WEIGHT_RE.match(token.replace(" ", "")):
        for label, _, start in cols:
            if label == "weight" and start - 4 <= centre <= start + 14:
                return "weight"
    if len(cols) > 1 and token_end <= cols[1][2] + 1:
        return "name"
    return min(cols, key=lambda c: abs(c[1] - centre))[0]


def _evaluation(lines, h: Handout) -> None:
    found = _header(lines)
    if found is None:
        h.unresolved.append("no evaluation table header found")
        return
    start, cols = found
    h.evaluation_page = lines[start][0]
    header_text = " ".join(l for _, l in lines[max(0, start - 2):start + 1])
    uses_marks = bool(re.search(r"\bMarks\b", header_text)) and "%" not in header_text
    comps: list[Component] = []
    buffer: dict[str, list[str]] = {}    # wrapped text not yet given to a row

    def flush_to(c):
        for fld, parts in buffer.items():
            if fld == "name":
                c.name = f"{c.name} {' '.join(parts)}".strip()
            elif fld in ("duration", "date", "nature"):
                setattr(c, fld, f"{getattr(c, fld)} {' '.join(parts)}".strip())
        buffer.clear()

    for pno, line in lines[start + 1:start + 70]:
        s = line.strip()
        if not s or BOILERPLATE_RE.search(s) or re.search(r"^\(%\)$|\((?:CB|Close|Closed|Open)\s*/?\s*(?:Book\s*)?/?\s*(?:OB|Open)", s):
            continue
        cells: dict[str, list[str]] = {}
        for m in re.finditer(r"\S+(?:\s\S+)*", line):
            cells.setdefault(_assign(m.start(), m.end(), cols, m.group()), []).append(m.group())
        weight = next((float(w[1]) for tok in cells.get("weight", [])
                       if (w := WEIGHT_RE.match(tok.replace(" ", "")))), None)
        # Rows are often numbered ("2. Comprehensive Exam ..."); a numbered
        # line is only the next section if it carries no weight.
        if weight is None and (_is_heading(line) or re.match(r"^(\*|note\b|#)", s, re.I)):
            break
        name = re.sub(r"^\(?\d{1,2}[.)]?\s*", "", " ".join(cells.get("name", []))).strip()
        if weight is None:
            for fld, parts in cells.items():
                buffer.setdefault(fld, []).extend(parts)
            continue
        if re.search(r"\btotal\b", name, re.I):
            buffer.clear()
            continue
        row = Component(name=name, kind="other", weight_pct=weight,
                        duration=" ".join(cells.get("duration", [])), date=" ".join(cells.get("date", [])),
                        nature=" ".join(cells.get("nature", [])))
        # Cells are vertically centred: text between two rows belongs to the
        # new row if that row has no name of its own, else to the previous one.
        if buffer:
            if not name:
                pending = buffer.pop("name", [])
                row.name = " ".join(pending)
                flush_to(row)
            elif comps:
                flush_to(comps[-1])
            else:
                buffer.clear()
        comps.append(row)
    if buffer and comps:
        flush_to(comps[-1])
    comps = [c for c in comps if not re.search(r"\btotal\b", c.name, re.I)]
    for c in comps:
        c.kind = _kind(c.name)
    total = sum(c.weight_pct for c in comps if c.weight_pct is not None)
    if uses_marks and total and not 95 <= total <= 105:
        for c in comps:
            c.weight_pct = round(c.weight_pct * 100 / total, 1) if c.weight_pct is not None else None
        h.unresolved.append(f"weights given as marks (total {total:g}); converted to percent")
        total = 100.0
    h.evaluation = comps
    h.weight_total = round(total, 1) if comps else None
    h.evaluation_complete = bool(comps) and 95 <= total <= 105
    if comps and not h.evaluation_complete:
        h.unresolved.append(f"evaluation weights sum to {total:g}%, not ~100%")
    h.open_book_components = [c.name for c in comps if re.search(r"open\s*book|\bOB\b", c.nature, re.I)
                              and not re.search(r"clos|\bCB\b", c.nature, re.I)]


def _clean_table(h: Handout) -> bool:
    """A table read well enough to support an absence claim ("no midsem")."""
    return (h.evaluation_complete and len(h.evaluation) >= 2
            and all(len(re.sub(r"[^A-Za-z]", "", c.name)) >= 3 and not re.match(r"^[a-z]\s", c.name)
                    # a "component" called "Closed Book" means the nature
                    # column was read as a name: the table is misread
                    and not re.match(r"^(closed?|open)\b.*\bbook\b", c.name, re.I)
                    for c in h.evaluation))


def _exam_flags(h: Handout) -> None:
    for kind, attr in (("midsem", "has_midsem"), ("compre", "has_compre")):
        hit = next((c for c in h.evaluation if c.kind == kind), None)
        if hit:
            setattr(h, attr, True)
            setattr(h, attr + "_basis", f"evaluation component '{hit.name}' (p{h.evaluation_page})")
        elif _clean_table(h):
            setattr(h, attr, False)
            setattr(h, attr + "_basis", f"complete evaluation scheme (sums to {h.weight_total:g}%) "
                                        f"lists no {kind} component (p{h.evaluation_page})")


def _prerequisites(lines, h: Handout) -> None:
    sec = _section(lines, "prerequisite")
    if not sec:
        return
    text = sec["text"]
    codes = [f"{d} {n}" for d, n in CODE_RE.findall(text)]
    hard = bool(codes or SHORT_CODE_RE.search(text))
    none = re.search(r"pre[\s\-]?requisites?[^:]{0,20}[:\-]?\s*(none|nil|n\.?a\.?|no\b|not\s+applicable)", text, re.I)
    kind = "none_stated" if none and not hard else "hard_course_code" if hard else "soft_recommendation"
    h.prerequisites.append({"text": text[:600], "course_codes": codes, "kind": kind,
                            "page": sec["page"], "basis": sec["basis"]})


def parse_handout(pdf: Path) -> Handout:
    parts = pdf.stem.split("_")
    h = Handout(file=pdf.name, course_code=f"{parts[1]} {'_'.join(parts[2:])}" if len(parts) >= 3 else pdf.stem)
    lines = _lines(pdf)
    h.pages = max((p for p, _ in lines), default=0)
    if len(" ".join(l for _, l in lines).split()) < 50:
        h.text_layer = False
        h.unresolved.append("almost no extractable text (scanned?); nothing parsed")
        return h
    for _, line in lines[:80]:
        if h.course_code_in_text is None and (m := re.search(r"Course\s*(?:No\.?|Number|Code)\s*[:\-]\s*(.+)", line, re.I)):
            codes = CODE_RE.findall(m[1])
            h.course_code_in_text = " / ".join(f"{d} {n}" for d, n in codes) or m[1].strip()
        if h.course_title is None and (m := re.search(r"Course\s*Title\s*[:\-]\s*(.+)", line, re.I)):
            h.course_title = " ".join(m[1].split())
        if h.instructor_in_charge is None and (m := re.search(r"Instructor[\s\-]*in[\s\-]*[Cc]harge\s*[:\-]\s*(.+)", line)):
            h.instructor_in_charge = " ".join(m[1].split())
    if h.course_code_in_text and h.course_code.replace(" ", "") not in h.course_code_in_text.replace(" ", ""):
        h.unresolved.append(f"file name says {h.course_code}, handout says {h.course_code_in_text}")
    _evaluation(lines, h)
    _exam_flags(h)
    h.makeup_policy = _section(lines, "makeup")
    h.attendance_policy = _section(lines, "attendance")
    _prerequisites(lines, h)
    for pno, line in lines:
        if re.search(r"humanities\s+elective|\bHUEL\b", line, re.I):
            h.humanities_elective_statement = {"page": pno, "text": " ".join(line.split())}
            break
    return h


def parse_handouts(folder: Path) -> list[Handout]:
    return [parse_handout(p) for p in sorted(folder.glob("*.pdf"))]


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out = root / "data" / "processed"
    out.mkdir(parents=True, exist_ok=True)
    handouts = parse_handouts(root / "data" / "raw" / "handouts")
    (out / "handouts.json").write_text(json.dumps([asdict(h) for h in handouts], indent=2), encoding="utf-8")
    verify = [{"field": "handout", "course_code": h.course_code, "reason": u,
               "source_hint": {"doc": "handout", "file": h.file}} for h in handouts for u in h.unresolved]
    (out / "handouts_needs_verification.json").write_text(json.dumps(verify, indent=2), encoding="utf-8")
    print(f"Parsed {len(handouts)} handouts -> handouts.json; {len(verify)} items flagged")


if __name__ == "__main__":
    main()
