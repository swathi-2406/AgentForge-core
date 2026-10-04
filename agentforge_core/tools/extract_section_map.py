# """Tool for building a structural map of document sections."""
# """extract_section_map: the table of what actually exists in a filing.

#     13.2 -> heading "Procedure for Termination...", subsections [a, b, c, d, e]

# The cross-reference tool (task 4) diffs every "Section 13.2(d)" mention against this map.
# """

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.read_document import ParsedDocument, load_document

# A clause marker that starts a line, or follows a sentence: "\n(a) " or ". (b) "
# Lookarounds don't consume the newline, so "(a)\n(b)" finds both.
MARKER_RE = re.compile(r"(?m)(?:^|(?<=[.:;] ))\(([a-z]{1,2})\)(?=\s|$)")
LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)]
SEQUENCE = LETTERS + [c * 2 for c in LETTERS]            # a..z, then aa, bb, ... zz
ROMAN_LOOKALIKES = {"i", "v", "x"}                        # (i) could be letter i or roman one
ROMAN_NEXT = {"i": "ii", "v": "vi", "x": "xi"}


def find_subsections(text: str) -> tuple[list[str], list[str]]:
    """Top-level lettered clauses, in order, plus any letters the sequence skipped.

    Rules:
      - Letters must move forward (a, b, c...). Anything else is a nested or cross-reference marker.
      - (i), (v), (x) only count as letters if they're exactly the next letter AND
        the next marker isn't (ii)/(vi)/(xi). Otherwise they're roman numerals.
      - A forward jump, e.g. (b) then (d), is kept and the gap is reported.
    """
    markers = MARKER_RE.findall(text)
    found, skipped = [], []
    pos = 0  # index in SEQUENCE of the next expected letter
    for i, m in enumerate(markers):
        if m not in SEQUENCE:
            continue  # roman numerals like (ii), (iv)
        idx = SEQUENCE.index(m)
        if idx >= 26 and pos < 26:
            continue  # (ii) before reaching (z) is roman two, not double-letter "ii"
        if idx < pos:
            continue  # going backwards = nested list or a repeat
        if m in ROMAN_LOOKALIKES:
            nxt = markers[i + 1] if i + 1 < len(markers) else None
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
        subs, skipped = find_subsections(s.text) if s.kind == "section" else ([], [])
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