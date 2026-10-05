"""extract_section_map: the table of what actually exists in a filing.

    13.2 -> heading "Procedure for Termination...", subsections [a, b, c, d, e]

The cross-reference tool diffs every "Section 13.2(d)" mention against this map.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.read_document import ParsedDocument, load_document

# A clause marker that starts a line, or follows a sentence: "\n(a) " or ". (b) "
# The named group `line` is set only for line starts, which are trusted more (see find_subsections).
# Lookarounds don't consume the newline, so "(a)\n(b)" finds both.
MARKER_RE = re.compile(r"(?m)(?:(?P<line>^[ \t]*)|(?<=[.:;] ))\((?P<label>[a-z]{1,2})\)(?=\s|$)")
LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)]
SEQUENCE = LETTERS + [c * 2 for c in LETTERS]            # a..z, then aa, bb, ... zz
ROMAN_LOOKALIKES = {"i", "v", "x"}                        # (i) could be letter i or roman one
ROMAN_NEXT = {"i": "ii", "v": "vi", "x": "xi"}
MAX_GAP = 1                                               # letters a line-start marker may skip
DEFINITIONS_HEADING = re.compile(r"defin", re.I)          # "Defined Terms", "Usage and Definitions"


def find_subsections(text: str, heading: str = "") -> tuple[list[str], list[str]]:
    """Top-level lettered clauses, in order, plus any letters the sequence skipped.

    Rules:
      - Letters must move forward (a, b, c...). Anything else is a nested or cross-reference marker.
      - A marker at the start of a line may skip at most MAX_GAP letters; the gap is reported.
        A marker after a sentence (". (b)", "; (y)") must be exactly the next letter.
        This stops inline alternatives like "Section 10.08); (y) all Borrower Materials"
        (Redwire 6.01, found on Day 3) from being read as subsection (y).
      - (i), (v), (x) only count as letters if they're exactly the next letter AND
        the next marker isn't (ii)/(vi)/(xi). Otherwise they're roman numerals.
      - Definitions sections have no subsections: each definition's own (a), (b) list
        would otherwise merge into one fake sequence.
    """
    if DEFINITIONS_HEADING.search(heading):
        return [], []
    markers = [(m.group("label"), m.group("line") is not None) for m in MARKER_RE.finditer(text)]
    found, skipped = [], []
    pos = 0  # index in SEQUENCE of the next expected letter
    for i, (m, line_start) in enumerate(markers):
        if m not in SEQUENCE:
            continue  # roman numerals like (iv)
        idx = SEQUENCE.index(m)
        if idx >= 26 and pos < 26:
            continue  # (ii) before reaching (z) is roman two, not double-letter "ii"
        if idx < pos:
            continue  # going backwards = nested list or a repeat
        if idx - pos > (MAX_GAP if line_start else 0):
            continue  # big jump = inline marker, or a page break pushed one to a line start
        if m in ROMAN_LOOKALIKES:
            nxt = markers[i + 1][0] if i + 1 < len(markers) else None
            if idx != pos or nxt == ROMAN_NEXT[m]:
                continue
        skipped += SEQUENCE[pos:idx]
        found.append(m)
        pos = idx + 1
    return found, skipped


class SectionMapEntry(BaseModel):
    number: str
    kind: Literal["article", "section", "schedule"]
    heading: str
    article: Optional[str] = None
    subsections: list[str] = Field(description="Top-level lettered clauses, e.g. ['a', 'b', 'c']")
    skipped_subsections: list[str] = Field(description="Letters missing from the sequence, e.g. ['c'] for (a),(b),(d)")


def build_section_map(doc: ParsedDocument) -> list[SectionMapEntry]:
    entries = []
    for s in doc.sections:
        if s.kind == "preamble":
            continue
        subs, skipped = find_subsections(s.text, s.heading) if s.kind == "section" else ([], [])
        entries.append(SectionMapEntry(number=s.number, kind=s.kind, heading=s.heading,
                                       article=s.article, subsections=subs, skipped_subsections=skipped))
    return entries


class ExtractSectionMapInput(ToolInput):
    filing_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="id from manifest.yaml")


class ExtractSectionMapOutput(ToolOutput):
    filing_id: str
    section_count: int
    entries: list[SectionMapEntry]
    warnings: list[str]

    def get(self, number: str) -> Optional[SectionMapEntry]:
        return next((e for e in self.entries if e.number.lower() == number.lower()), None)


@register_tool
class ExtractSectionMap(Tool):
    name = "extract_section_map"
    description = ("List every article, section and schedule that exists in a filing, with its heading "
                   "and its lettered subsections, e.g. 13.2 -> [a, b, c, d, e].")
    Input = ExtractSectionMapInput
    Output = ExtractSectionMapOutput

    def run(self, args: ExtractSectionMapInput) -> ExtractSectionMapOutput:
        doc = load_document(args.filing_id)
        entries = build_section_map(doc)
        warnings = list(doc.warnings)
        gaps = [f"{e.number} skips ({', '.join(e.skipped_subsections)})" for e in entries if e.skipped_subsections]
        if gaps:
            warnings.append("Subsection letters skipped: " + "; ".join(gaps))
        return ExtractSectionMapOutput(filing_id=args.filing_id, section_count=len(entries),
                                       entries=entries, warnings=warnings)