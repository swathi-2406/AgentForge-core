# """extract_cross_references: find every "Section 13.2(d)" style mention and check it resolves.

# Each mention becomes one CrossReference with a status:
#     ok                  the section (and clause, if any) exists in this filing
#     missing_section     "Section 99.1" but there is no 99.1
#     missing_clause      "Section 13.2(f)" but 13.2 only has (a)-(e)
#     missing_attachment  "Schedule C" but no Schedule C is attached (common on EDGAR)
#     external            points into another document: "Section 9.1 of the Participation Agreement"
#     unverified          can't tell mechanically, e.g. "(ii)" may be a nested roman clause

# The tool does the mechanical diff so the agent spends its reasoning on what a reference
# MEANS (e.g. TVA 15.1 -> 13.2(d) resolves fine but points at the wrong clause).
# """

from __future__ import annotations

import bisect
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.extract_section_map import SectionMapEntry, build_section_map
from agentforge_core.tools.base import ToolError
from agentforge_core.tools.read_document import ParsedDocument, load_document, manifest_entry

Status = Literal["ok", "missing_section", "missing_clause", "missing_attachment", "external", "unverified"]
PROBLEMS = {"missing_section", "missing_clause", "missing_attachment", "unverified"}

# ---------- patterns ----------

NUM = r"\d+(?:\.\d+)*(?:[A-Za-z](?![A-Za-z]))?"   # 2.05, plus statute styles 1a, 5f, 4041A
CLAUSE = r"\((?:[a-z]{1,4}|[IVXL]{2,4}|[A-Z]|\d{1,2})\)"  # (d) (iv) (A) (II) (2) -- not "(Rent)"
CLAUSES = rf"(?:\s?{CLAUSE})*"
SEP = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and/or|and|or|through|to)\s+)"
LABEL = r"[A-Z0-9][A-Z0-9.\-]{0,4}\b"

SECTION_RE = re.compile(rf"(?:\b[Ss]ections?|\bSECTIONS?|§§?)\s*(?P<list>{NUM}{CLAUSES}(?:{SEP}{NUM}{CLAUSES})*)")
ARTICLE_RE = re.compile(rf"\b(?:Articles?|ARTICLES?)\s+(?P<list>(?:[IVXLC]+|\d+)\b(?:{SEP}(?:[IVXLC]+|\d+)\b)*)")
ATTACH_RE = re.compile(rf"\b(?P<word>Schedules?|Exhibits?|Appendix|Appendices|Annex(?:es)?)\s+(?P<list>{LABEL}(?:{SEP}{LABEL})*)")
CLAUSE_WORD = r"(?:[Cc]lauses?|[Pp]aragraphs?|[Ss]ubsections?)"
CLAUSE_LIST = rf"{CLAUSE}{CLAUSES}(?:{SEP}{CLAUSE}{CLAUSES})*"
LOCAL_RE = re.compile(rf"\b{CLAUSE_WORD}\s+(?P<list>{CLAUSE_LIST})")
# "clauses (a) and (b) of Section 2.05" -> look backwards from "Section"
CLAUSE_PREFIX_RE = re.compile(rf"{CLAUSE_WORD}\s+(?P<list>{CLAUSE_LIST})\s+(?:of|in|under)\s+(?:this\s+)?$")
DOC_NAME = (r"(?:(?:[A-Z0-9][\w'&\-]*|and|of|&)\s+){0,6}"
            r"(?:Agreement|Indenture|Lease|Code|Act|Regulations?|Notes?|Documents?|Trust|Rules?|"
            r"Guarant(?:ee|y)|Plan|Certificate|Supplement|Amendment)")
EXTERNAL_RE = re.compile(
    rf"\s*,?\s*(?:of|in|under|to)\s+(?:the\s+)?(?P<doc>{DOC_NAME}"
    r"|(?!ARTICLE|SECTION|THIS)[A-Z]{3,}"          # ERISA, UCC, FCPA
    r"|Title\s+\d+"                               # Title 11 (of the Bankruptcy Code)
    r"|(?:Directive|Regulation)\s+(?:\(\w+\)\s*)?[\w/]+"   # Directive 2014/59/EU
    r"|such\s+(?:[a-z]+\s+){0,3}?(?:act|agreement|document|instrument|indenture|lease|code))\b")
# "(Section references are to the Exchange Note Purchase Agreement)" changes the meaning of a whole section
SCOPE_RE = re.compile(rf"(?i:section\s+)?references?\s+(?:in\s+this\s+\w+\s+)?are\s+to\s+(?:the\s+)?(?P<doc>{DOC_NAME})")
THEREOF_RE = re.compile(r"\s*,?\s*(?:thereof|therein|thereunder)\b")
STATUTE_BEFORE = re.compile(r"(?:U\.S\.C\.|C\.F\.R\.|CFR|Code|ERISA|CPLR|UCC|Treasury Regulations?)\s*$")
TRAILING_CLAUSES = re.compile(rf"(?:{SEP}{CLAUSE}{CLAUSES})+")
TRAILING_END = re.compile(r"\s*(?:of|in|under|to|hereof|thereof|above|below|[,.;:)]|$)")
UNIT_AFTER = re.compile(r"\s*(?:days?|Business|years?|months?|hours?|percent|%)", re.I)

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def to_roman(n: int) -> str:
    out = ""
    for v, s in [(100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]:
        while n >= v:
            out, n = out + s, n - v
    return out

# ---------- model ----------

class CrossReference(BaseModel):
    kind: Literal["section", "article", "attachment", "local_clause"]
    target: str = Field(description='"13.2", "Article VII", "Schedule B"; for local_clause, the section it sits in')
    clauses: list[str] = Field(default_factory=list, description="First-level clauses referenced, e.g. ['d']")
    missing_clauses: list[str] = Field(default_factory=list)
    found_in: str = Field(description="Section where the mention appears")
    external_doc: Optional[str] = None
    resolved_in: Optional[str] = Field(default=None, description="Other filing the target was checked in (amendments)")
    status: Status
    snippet: str
    char_offset: int

# ---------- helpers ----------

def first_level_clauses(text: str) -> list[str]:
    """'(a)(i) and (b)' -> ['a', 'b']: a group directly after ')' is nested, so skip it."""
    return re.findall(r"(?<!\))\(([a-z]{1,4}|[IVXL]{2,4}|[A-Z]|\d{1,2})\)", text)


def check_clauses(clauses: list[str], entry: SectionMapEntry, markers: set[str]) -> tuple[Status, list[str]]:
    missing, unsure = [], False
    for c in clauses:
        if c in entry.subsections or c in markers:
            continue
        if re.fullmatch(r"[ivxl]+", c) or c.isupper() or c.isdigit():
            unsure = True  # roman, capital or numbered clause: likely nested, can't verify
        else:
            missing.append(c)
    if missing:
        return "missing_clause", missing
    return ("unverified" if unsure else "ok"), []


class _Ctx:
    def __init__(self, doc: ParsedDocument):
        self.doc = doc
        self.text = doc.text
        self.index = {e.number.lower(): e for e in build_section_map(doc)}
        self.starts = [s.char_start for s in doc.sections]
        self.heading_starts = set(self.starts)
        self.skip_preamble = doc.toc_entries_skipped > 0  # the TOC lives there
        self.section_text = {sec.number.lower(): sec.text for sec in doc.sections}
        self._markers: dict[str, set[str]] = {}

    def found_in(self, offset: int) -> str:
        i = bisect.bisect_right(self.starts, offset) - 1
        return self.doc.sections[i].number if i >= 0 else "preamble"

    def snippet(self, start: int, end: int) -> str:
        return self.text[max(0, start - 60):end + 60].replace("\n", " ").strip()

    def external_after(self, end: int) -> Optional[str]:
        m = EXTERNAL_RE.match(self.text, end)
        if m:
            return m.group("doc").strip()
        return "(another document: 'thereof')" if THEREOF_RE.match(self.text, end) else None

    def statute_before(self, start: int) -> Optional[str]:
        m = STATUTE_BEFORE.search(self.text[max(0, start - 25):start])
        return m.group(0).strip() if m else None

    def markers(self, number: str) -> set[str]:
        """Every (x) that appears in a section as a clause, not inside a reference to one.
        Covers inline lists too: 'by either (a) hand delivery or (b) courier'."""
        if number not in self._markers:
            text = self.section_text.get(number, "")
            text = LOCAL_RE.sub(" ", SECTION_RE.sub(" ", text))
            self._markers[number] = set(re.findall(r"\(([a-z]{1,4}|[IVXL]{2,4}|[A-Z]|\d{1,2})\)", text))
        return self._markers[number]

    def skip(self, start: int, where: str) -> bool:
        return start in self.heading_starts or (where == "preamble" and self.skip_preamble)


def lookup(ctx: "_Ctx", target: str, clauses: list[str]) -> tuple[Status, list[str]]:
    entry = ctx.index.get(target.lower())
    if not entry:
        return "missing_section", []
    return check_clauses(clauses, entry, ctx.markers(target.lower()))


def list_items(list_text: str, base: int, pattern: str) -> list[tuple[str, str, int, int]]:
    """Split 'a, b and c' into (value, clause_text, start, end), keeping absolute offsets."""
    out = []
    for m in re.finditer(rf"(?P<v>{pattern})(?P<c>{CLAUSES})", list_text):
        out.append((m.group("v"), m.group("c"), base + m.start(), base + m.end()))
    return out

# ---------- finders ----------

def find_section_refs(ctx: _Ctx) -> list[CrossReference]:
    refs = []
    for m in SECTION_RE.finditer(ctx.text):
        where = ctx.found_in(m.start())
        if ctx.skip(m.start(), where):
            continue
        statute = ctx.statute_before(m.start())
        items = list_items(m.group("list"), m.start("list"), NUM)
        if re.match(r"[-.]\d", ctx.text[items[0][3]:items[0][3] + 2]):
            statute = statute or "(regulation number)"   # "60-1.4" / "5f.103-1" are not contract sections
        first_dotted = "." in items[0][0]
        kept = [items[0]]
        for it in items[1:]:  # "Section 2.05 to 10 days" -> stop at "10"
            if ("." in it[0]) != first_dotted or UNIT_AFTER.match(ctx.text, it[3]):
                break
            kept.append(it)
        end = kept[-1][3]
        # "Section 4.2(e) or (f) of ..." continues the reference, but in
        # "Section 3.2, (ii) a reduction ..." the (ii) starts the next item of an outer list.
        trailing = TRAILING_CLAUSES.match(ctx.text, end)
        if trailing and not (kept[-1][1].strip() and TRAILING_END.match(ctx.text, trailing.end())):
            trailing = None
        if trailing:
            v, c, st, _ = kept[-1]
            kept[-1] = (v, c + trailing.group(0), st, trailing.end())
            end = trailing.end()
        ext = statute or ctx.external_after(end)
        prefix = CLAUSE_PREFIX_RE.search(ctx.text[max(0, m.start() - 120):m.start()])
        for n, (num, clause_text, s, e) in enumerate(kept):
            clauses = first_level_clauses(clause_text)
            if n == 0 and prefix:
                clauses = first_level_clauses(prefix.group("list")) + clauses
            ref = dict(kind="section", target=num, clauses=clauses, found_in=where, external_doc=ext,
                       snippet=ctx.snippet(s, e), char_offset=s)
            entry = ctx.index.get(num.lower())
            if ext:
                refs.append(CrossReference(**ref, status="external"))
            elif not entry:
                refs.append(CrossReference(**ref, status="missing_section"))
            else:
                status, missing = check_clauses(clauses, entry, ctx.markers(num.lower()))
                refs.append(CrossReference(**ref, status=status, missing_clauses=missing))
    return refs


def find_article_refs(ctx: _Ctx) -> list[CrossReference]:
    refs = []
    for m in ARTICLE_RE.finditer(ctx.text):
        where = ctx.found_in(m.start())
        if ctx.skip(m.start(), where):
            continue
        ext = ctx.external_after(m.end())
        for v in re.finditer(r"[IVXLC]+|\d+", m.group("list")):
            label = v.group(0)
            keys = [f"article {label.lower()}"] + ([f"article {to_roman(int(label)).lower()}"] if label.isdigit() else [])
            entry = next((ctx.index[k] for k in keys if k in ctx.index), None)
            status: Status = "external" if ext else ("ok" if entry else "missing_section")
            s = m.start("list") + v.start()
            refs.append(CrossReference(kind="article", target=entry.number if entry else f"Article {label}",
                                       found_in=where, external_doc=ext, status=status,
                                       snippet=ctx.snippet(s, s + len(label)), char_offset=s))
    return refs


def find_attachment_refs(ctx: _Ctx) -> list[CrossReference]:
    refs = []
    for m in ATTACH_RE.finditer(ctx.text):
        where = ctx.found_in(m.start())
        if ctx.skip(m.start(), where):
            continue
        word = m.group("word")
        word = {"Appendices": "Appendix", "Annexes": "Annex"}.get(word, word.rstrip("s") if word.endswith("s") else word)
        ext = ctx.external_after(m.end())
        for v in re.finditer(LABEL, m.group("list")):
            label = v.group(0)
            if where == "preamble" and "." in label:
                continue  # EDGAR cover label like "Exhibit 10.24"
            target = f"{word} {label}"
            status: Status = "external" if ext else ("ok" if target.lower() in ctx.index else "missing_attachment")
            s = m.start("list") + v.start()
            refs.append(CrossReference(kind="attachment", target=target, found_in=where, external_doc=ext,
                                       status=status, snippet=ctx.snippet(s, s + len(label)), char_offset=s))
    return refs


def find_local_clause_refs(ctx: _Ctx) -> list[CrossReference]:
    """'clause (a) above' / 'paragraph (b) of this Section' -> a clause of the section it sits in."""
    refs = []
    for m in LOCAL_RE.finditer(ctx.text):
        after = ctx.text[m.end():m.end() + 40]
        # "... of Section 2.05" is handled by find_section_refs; "... of the definition" isn't ours.
        if re.match(r"\s+(?:of|in|under)\s+", after) and not re.match(r"\s+(?:of|in|under)\s+this\s+Section(?!\s*\d)", after):
            continue
        where = ctx.found_in(m.start())
        entry = ctx.index.get(where.lower())
        if ctx.skip(m.start(), where) or not entry or entry.kind != "section":
            continue
        clauses = first_level_clauses(m.group("list"))
        status, _ = check_clauses(clauses, entry, ctx.markers(where.lower()))
        if status == "missing_clause":
            status = "unverified"  # inside a section, "(c)" may belong to a nested list
        refs.append(CrossReference(kind="local_clause", target=where, clauses=clauses, found_in=where,
                                   status=status, snippet=ctx.snippet(m.start(), m.end()), char_offset=m.start()))
    return refs


def apply_scope_statements(ctx: _Ctx, refs: list[CrossReference]) -> None:
    """Inside a section that says 'Section references are to the X Agreement', section refs point to X."""
    for sec in ctx.doc.sections:
        m = SCOPE_RE.search(ctx.text, sec.char_start, sec.char_end)
        if not m:
            continue
        for r in refs:
            if r.found_in == sec.number and r.kind == "section" and r.status != "external":
                r.status, r.external_doc, r.missing_clauses = "external", m.group("doc").strip(), []


def apply_attachment_memory(refs: list[CrossReference]) -> None:
    """'Appendix 1 to the Exchange Note Supplement' ... later 'Appendix 1 and Appendix A apply' = same outside doc."""
    seen: dict[str, str] = {}
    for r in refs:
        if r.kind != "attachment":
            continue
        if r.status == "external" and r.external_doc:
            seen.setdefault(r.target.lower(), r.external_doc)
        elif r.status == "missing_attachment" and r.target.lower() in seen:
            r.status, r.external_doc = "external", seen[r.target.lower()]


def find_references(doc: ParsedDocument) -> list[CrossReference]:
    ctx = _Ctx(doc)
    refs = find_section_refs(ctx) + find_article_refs(ctx) + find_attachment_refs(ctx) + find_local_clause_refs(ctx)
    refs.sort(key=lambda r: r.char_offset)
    apply_scope_statements(ctx, refs)
    apply_attachment_memory(refs)
    return refs


def resolve_against_original(refs: list[CrossReference], original: ParsedDocument) -> Optional[str]:
    """For an amendment: 'Section 2.01(a)(i) is amended...' points into the original agreement.

    - missing here but exists in the original      -> external, resolved_in=original
    - external to the amended doc (the most cited
      outside document) but absent in the original -> missing_section / missing_clause  (a real finding)
    Returns the name assumed for the amended document.
    """
    names = [r.external_doc for r in refs if r.status == "external" and r.external_doc and r.kind == "section"]
    amended_name = max(set(names), key=names.count) if names else None
    octx = _Ctx(original)
    for r in refs:
        if r.kind not in ("section", "article"):
            continue
        local_missing = r.status in ("missing_section", "missing_clause") and r.external_doc is None
        to_amended = r.status == "external" and amended_name and r.external_doc == amended_name
        if not (local_missing or to_amended):
            continue
        status, missing = lookup(octx, r.target, r.clauses)
        r.resolved_in = original.filing_id
        if status in ("ok", "unverified"):
            r.status, r.missing_clauses = "external", []
            r.external_doc = r.external_doc or amended_name or original.filing_id
        else:
            r.status, r.missing_clauses = status, missing
            r.external_doc = r.external_doc or amended_name
    return amended_name

# ---------- the tool ----------

class ExtractCrossReferencesInput(ToolInput):
    filing_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="id from manifest.yaml")
    only_problems: bool = Field(default=False, description="Return only missing_* and unverified references")
    limit: int = Field(default=500, ge=1, le=5000)


class ExtractCrossReferencesOutput(ToolOutput):
    filing_id: str
    total: int
    counts: dict[str, int]
    truncated: bool
    references: list[CrossReference]
    warnings: list[str]


@register_tool
class ExtractCrossReferences(Tool):
    name = "extract_cross_references"
    description = ("Find every Section / Article / Schedule / clause reference in a filing and check whether "
                   "it resolves to something that exists. Use only_problems=true to get just the broken ones.")
    Input = ExtractCrossReferencesInput
    Output = ExtractCrossReferencesOutput

    def run(self, args: ExtractCrossReferencesInput) -> ExtractCrossReferencesOutput:
        doc = load_document(args.filing_id)
        refs = find_references(doc)
        warnings = list(doc.warnings)
        entry = manifest_entry(args.filing_id)
        if entry.get("role") == "amendment" and entry.get("amends"):
            try:
                name = resolve_against_original(refs, load_document(entry["amends"]))
                warnings.append(f"Amendment: checked references against {entry['amends']}"
                                + (f" (assumed to be the '{name}')" if name else ""))
            except ToolError as e:
                warnings.append(f"Amendment, but couldn't load the original: {e}")
        counts: dict[str, int] = {}
        for r in refs:
            counts[r.status] = counts.get(r.status, 0) + 1
        chosen = [r for r in refs if r.status in PROBLEMS] if args.only_problems else refs
        return ExtractCrossReferencesOutput(filing_id=args.filing_id, total=len(refs), counts=counts,
                                            truncated=len(chosen) > args.limit, references=chosen[:args.limit],
                                            warnings=warnings)