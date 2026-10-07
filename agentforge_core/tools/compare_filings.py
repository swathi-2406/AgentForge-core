"""Compare an amendment against the original agreement it amends.

    diff_filings              NAIVE     whole text vs whole text, line by line
    compare_amended_clauses   TARGETED  only the clauses the amendment says it changes

Day 6 needs both. The naive tool is the first attempt that should fail the critic
(hundreds of "differences", almost all restated boilerplate). The targeted tool is
what retry.py switches to:

    amendment text
      └── find directives   "Section 2.02(a) is amended and restated ... as follows:"
            └── new text    the quoted replacement, up to the next directive
                  └── original block   orig.section("2.02") -> clause (a)
                        └── word diff after normalizing ("three (3)" == "three")

No LLM here. Both tools are deterministic and free to run.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher, get_close_matches
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.read_document import load_document

FILING_ID = r"^[a-z0-9][a-z0-9_]*$"
PREVIEW = 240
PAGE_MARK = re.compile(r"\|\s*\d+\s*\||^\s*-?\s*\d+\s*-?\s*$", re.M)


def _cut(s: str, n: int = PREVIEW) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


class _PairInput(ToolInput):
    original_id: str = Field(pattern=FILING_ID, description="id of the original agreement")
    amendment_id: str = Field(pattern=FILING_ID, description="id of the amendment")

    @model_validator(mode="after")
    def _different(self):
        if self.original_id == self.amendment_id:
            raise ValueError("original_id and amendment_id must be different filings")
        return self


# ============================================================ naive: diff_filings

class DiffHunk(BaseModel):
    kind: Literal["changed", "added"]
    amendment_sentence: str
    closest_original: str = Field(description="Most similar original sentence, empty if none")


class DiffFilingsInput(_PairInput):
    max_hunks: int = Field(default=25, ge=1, le=200, description="How many differences to return")


class DiffFilingsOutput(ToolOutput):
    original_id: str
    amendment_id: str
    original_sentences: int
    amendment_sentences: int
    identical: int
    total_differences: int = Field(description="Every amendment sentence not found word for word")
    changed: int
    added: int
    hunks: list[DiffHunk]


SENT_SPLIT = re.compile(r"(?<=[.;:])\s+")


def sentences(text: str) -> list[str]:
    flat = " ".join(PAGE_MARK.sub(" ", text).split())
    return [x for x in SENT_SPLIT.split(flat) if len(x.split()) >= 4]


@register_tool
class DiffFilings(Tool):
    name = "diff_filings"
    description = ("Sentence-by-sentence text diff of an amendment against the original agreement. "
                   "Every amendment sentence that does not appear word for word in the original is "
                   "reported as a difference, paired with the closest original sentence.")
    Input = DiffFilingsInput
    Output = DiffFilingsOutput

    def run(self, args: DiffFilingsInput) -> DiffFilingsOutput:
        a = sentences(load_document(args.original_id).text)
        b = sentences(load_document(args.amendment_id).text)
        known = {x.lower() for x in a}
        lowered = [x.lower() for x in a]
        hunks, same = [], 0
        for sent in b:
            if sent.lower() in known:
                same += 1
                continue
            best = get_close_matches(sent.lower(), lowered, n=1, cutoff=0.6)
            orig = a[lowered.index(best[0])] if best else ""
            hunks.append(DiffHunk(kind="changed" if best else "added",
                                  amendment_sentence=_cut(sent), closest_original=_cut(orig)))
        changed = sum(1 for h in hunks if h.kind == "changed")
        return DiffFilingsOutput(
            original_id=args.original_id, amendment_id=args.amendment_id,
            original_sentences=len(a), amendment_sentences=len(b), identical=same,
            total_differences=len(hunks), changed=changed, added=len(hunks) - changed,
            hunks=hunks[: args.max_hunks])


# ============================================================ targeted: helpers

NUMBER_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
                "twenty|thirty|forty|forty-five|sixty|ninety|hundred")
NUM_PAREN = re.compile(rf"\b({NUMBER_WORDS})\s*\(\s*\d+\s*\)", re.I)
TOKEN = re.compile(r"[a-z0-9$]+(?:[.,:/%-][a-z0-9]+)*%?")


def tokens(text: str, strict: bool = True) -> list[str]:
    """Words to compare. strict=True also treats 'three (3)' as 'three'."""
    t = PAGE_MARK.sub(" ", text)
    if strict:
        t = NUM_PAREN.sub(r"\1", t)
    return TOKEN.findall(t.lower())


# "Section 2.01(a)(i) is amended and restated" / "The second sentence of Section 2.02(a) is ..."
REF = r"\d+\.\d+[a-z]?(?:\s?\([a-zA-Z0-9]{1,5}\))*"
DIRECTIVE_RE = re.compile(
    rf"(?:\b[Tt]he\s+(?P<part>(?:first|second|third|fourth|fifth|last)\s+sentence)\s+of\s+)?"
    rf"\bSection\s+(?P<ref>{REF})\s+is\s+(?:hereby\s+)?"
    rf"(?P<verb>amended\s+and\s+restated|revised|amended|deleted|replaced)\b")
BODY_START_RE = re.compile(r"(?:as\s+follows|by)\s*:", re.I)
HEADING_RE = re.compile(r"\(\s*[a-z]\s*\)\s*Amendments?\s+of\s+Section", re.I)
ARTICLE_RE = re.compile(r"\bARTICLE\s+[IVX]+\b")

LEAD_LABEL = re.compile(r"^\s*\(([a-zA-Z0-9]{1,5})\)\s*")
ROMANS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"]


class Directive(BaseModel):
    ref: str
    base: str
    path: list[str]
    part: Optional[str]
    verb: str
    new_text: str


def find_directives(text: str) -> list[Directive]:
    """Every 'Section X is amended ...' in the amendment, with the replacement text after it."""
    matches = list(DIRECTIVE_RE.finditer(text))
    out = []
    for k, m in enumerate(matches):
        tail = text[m.end(): m.end() + 200]
        b = BODY_START_RE.search(tail)
        start = m.end() + (b.end() if b else 0)
        ends = [len(text), start + 30000]
        if k + 1 < len(matches):
            ends.append(matches[k + 1].start())
        for rx in (HEADING_RE, ARTICLE_RE):
            nxt = rx.search(text, start)
            if nxt:
                ends.append(nxt.start())
        body = text[start:min(ends)].strip().strip("“”\"").strip()
        ref = re.sub(r"\s+", "", m.group("ref"))
        out.append(Directive(ref=ref, base=re.match(r"\d+\.\d+[a-z]?", ref).group(0),
                             path=re.findall(r"\(([a-zA-Z0-9]{1,5})\)", ref),
                             part=m.group("part"), verb=" ".join(m.group("verb").split()),
                             new_text=body))
    return out


def _label_re(label: str) -> re.Pattern:
    # "(b)" at the start of a paragraph or right after a heading / sentence end
    return re.compile(rf"(?:^|(?<=[\n.:;])|(?<=[\n.:;]\s))\s*\({re.escape(label)}\)\s", re.M)


def _siblings(label: str) -> list[str]:
    nxt = []
    if label in ROMANS and ROMANS.index(label) + 1 < len(ROMANS):
        nxt.append(ROMANS[ROMANS.index(label) + 1])
    if label.isdigit():
        nxt.append(str(int(label) + 1))
    elif len(label) == 1 and label.isalpha() and label not in "zZ":
        nxt.append(chr(ord(label) + 1))
    return nxt


def find_clause(text: str, label: str) -> Optional[str]:
    m = _label_re(label).search(text)
    if not m:
        return None
    ends = [len(text)]
    for sib in _siblings(label):
        s = _label_re(sib).search(text, m.end())
        if s:
            ends.append(s.start())
    return text[m.start():min(ends)].strip()


def _window(orig: list[str], new: list[str], force: bool = False) -> tuple[list[str], bool]:
    """If the original block is much longer, or the amendment restates only part of it
    ('second sentence of 2.02(a)'), keep only the part that lines up with the new text."""
    if not force and len(orig) <= 1.5 * max(len(new), 1):
        return orig, False
    blocks = [b for b in SequenceMatcher(None, orig, new, autojunk=False).get_matching_blocks() if b.size >= 3]
    if not blocks:
        return orig, False
    return orig[blocks[0].a: blocks[-1].a + blocks[-1].size], True


class ClauseChange(BaseModel):
    kind: Literal["replace", "delete", "insert"]
    before: str
    after: str


def word_changes(old: str, new: str, limit: int, force: bool = False) -> tuple[list[ClauseChange], int, float, bool]:
    """(substantive changes, cosmetic differences, similarity, window_trimmed)"""
    a, trimmed = _window(tokens(old), tokens(new), force)
    b = tokens(new)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    changes = [ClauseChange(kind=t, before=_cut(" ".join(a[i1:i2]), 160), after=_cut(" ".join(b[j1:j2]), 160))
               for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal"]
    la, _ = _window(tokens(old, strict=False), tokens(new, strict=False), force)
    light = sum(1 for t, *_ in SequenceMatcher(None, la, tokens(new, strict=False), autojunk=False).get_opcodes()
                if t != "equal")
    return changes[:limit], max(0, light - len(changes)), round(sm.ratio(), 4), trimmed


# ---- definitions (Section 1.01 "revised by inserting / restating definitions")

DEF_RE = re.compile(r"[“\"]\s*(?P<term>[A-Z][\w .,'&/\-]{0,80}?)\s*[”\"]\s*"
                    r"(?=(?:\(a\)\s*)?(?:means|has\s+the\s+meaning|shall\s+mean|refers|includes))")
SUBLIST_TAIL = re.compile(r"\(\s*[ivx]+\s*\)\s*(?:Inserting|Amending|Deleting)[\s\S]*$")


MODE_RE = re.compile(r"\(\s*[ivx]+\s*\)\s*(?P<mode>Inserting|Amending|Deleting)")


def definition_modes(text: str) -> dict[str, str]:
    """term -> 'Inserting' / 'Amending' / 'Deleting', from the sub-list it sits in."""
    marks = [(m.start(), m.group("mode")) for m in MODE_RE.finditer(text)]
    out = {}
    for m in DEF_RE.finditer(text):
        mode = next((md for pos, md in reversed(marks) if pos < m.start()), "Inserting")
        out.setdefault(" ".join(m.group("term").split()), mode)
    return out


def definitions(text: str) -> dict[str, str]:
    ms = list(DEF_RE.finditer(text))
    out = {}
    for k, m in enumerate(ms):
        end = ms[k + 1].start() if k + 1 < len(ms) else len(text)
        out.setdefault(" ".join(m.group("term").split()), SUBLIST_TAIL.sub("", text[m.start():end]).strip())
    return out


# ---- dates that are one or two days apart in the same month (March 30 vs March 31)

DATE_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|"
                     r"November|December)\s+(\d{1,2}),\s*(\d{4})")


def near_mismatches(text: str) -> list[str]:
    seen: dict[tuple[str, str], set[int]] = {}
    for mo, d, y in DATE_RE.findall(text):
        seen.setdefault((mo, y), set()).add(int(d))
    out = []
    for (mo, y), days in seen.items():
        ds = sorted(days)
        for x, z in zip(ds, ds[1:]):
            if z - x <= 2:
                out.append(f"{mo} {x}, {y} vs {mo} {z}, {y}")
    return out


# ============================================================ targeted: the tool

class ClauseComparison(BaseModel):
    ref: str = Field(description='"2.02(a)", or "1.01 Term Loan" for one definition')
    directive: str
    status: Literal["changed", "unchanged", "added", "missing_in_original"]
    located_by: Literal["section", "clause", "parent_fallback", "definition", "not_found"]
    similarity: float
    window_trimmed: bool = False
    substantive_changes: list[ClauseChange]
    cosmetic_differences: int = Field(description="Wording-only differences ignored, e.g. 'three (3)'")
    new_text_preview: str


class InternalFlag(BaseModel):
    ref: str
    kind: Literal["date_near_mismatch"]
    detail: str
    in_original: bool = Field(description="False means the amendment introduced it")


class CompareAmendedClausesInput(_PairInput):
    max_changes_per_clause: int = Field(default=10, ge=1, le=50)


class CompareAmendedClausesOutput(ToolOutput):
    original_id: str
    amendment_id: str
    total_directives: int
    changed: int
    unchanged: int
    added: int
    missing_in_original: int
    clauses: list[ClauseComparison]
    internal_flags: list[InternalFlag]
    warnings: list[str]


def _compare_definitions(d: Directive, orig_block: str, limit: int) -> list[ClauseComparison]:
    old_defs, out = definitions(orig_block), []
    modes = definition_modes(d.new_text)
    for term, new_def in definitions(d.new_text).items():
        old = old_defs.get(term)
        if old is None and modes.get(term) == "Amending":
            out.append(ClauseComparison(ref=f"1.01 {term}",
                                        directive=f"{d.verb}: restates a definition the original doesn't have",
                                        status="missing_in_original", located_by="not_found", similarity=0.0,
                                        substantive_changes=[], cosmetic_differences=0,
                                        new_text_preview=_cut(new_def)))
            continue
        if old is None:
            out.append(ClauseComparison(ref=f"1.01 {term}", directive=f"{d.verb}: inserts definition",
                                        status="added", located_by="definition", similarity=0.0,
                                        substantive_changes=[], cosmetic_differences=0,
                                        new_text_preview=_cut(new_def)))
            continue
        ch, cos, sim, tr = word_changes(old, new_def, limit)
        out.append(ClauseComparison(ref=f"1.01 {term}", directive=f"{d.verb}: restates definition",
                                    status="changed" if ch else "unchanged", located_by="definition",
                                    similarity=sim, window_trimmed=tr, substantive_changes=ch,
                                    cosmetic_differences=cos, new_text_preview=_cut(new_def)))
    return out


@register_tool
class CompareAmendedClauses(Tool):
    name = "compare_amended_clauses"
    description = ("Find each clause the amendment says it changes ('Section X is amended and "
                   "restated ...'), locate that clause in the original, and compare only those "
                   "clauses word by word, ignoring rewording like 'three' vs 'three (3)'. Also "
                   "flags near-identical dates inside restated clauses.")
    Input = CompareAmendedClausesInput
    Output = CompareAmendedClausesOutput

    def run(self, args: CompareAmendedClausesInput) -> CompareAmendedClausesOutput:
        orig, amend = load_document(args.original_id), load_document(args.amendment_id)
        directives, warnings = find_directives(amend.text), []
        clauses: list[ClauseComparison] = []
        flags: list[InternalFlag] = []
        if not directives:
            warnings.append("No 'Section X is amended/restated/revised' directives found.")

        for d in directives:
            label = f"{d.part} of " if d.part else ""
            directive = f"{label}Section {d.ref}: {d.verb}"
            sec = orig.section(d.base)
            if sec is None:
                clauses.append(ClauseComparison(ref=d.ref, directive=directive, status="missing_in_original",
                                                located_by="not_found", similarity=0.0, substantive_changes=[],
                                                cosmetic_differences=0, new_text_preview=_cut(d.new_text)))
                continue

            if len(definitions(d.new_text)) >= 2:          # a definitions revision like §1.01
                clauses.extend(_compare_definitions(d, sec.text, args.max_changes_per_clause))
                continue

            # The new text may restate from a parent level: 2.01(a)(i) restated as "(a) ... (i) ...".
            lead = LEAD_LABEL.match(d.new_text)
            path = d.path
            if lead and lead.group(1) in path:
                path = path[: path.index(lead.group(1)) + 1]
            block, located = sec.text, "section"
            for lab in path:
                sub = find_clause(block, lab)
                if sub is None:
                    warnings.append(f"{d.ref}: clause ({lab}) not found in original; compared against parent.")
                    located = "parent_fallback"
                    break
                block, located = sub, "clause"

            if located == "clause" and not lead:          # "(b) Mandatory..." vs "Mandatory..."
                block = LEAD_LABEL.sub("", block, count=1)
            ch, cos, sim, tr = word_changes(block, d.new_text, args.max_changes_per_clause,
                                            force=bool(d.part) or len(path) < len(d.path))
            clauses.append(ClauseComparison(ref=d.ref, directive=directive,
                                            status="changed" if ch else "unchanged", located_by=located,
                                            similarity=sim, window_trimmed=tr, substantive_changes=ch,
                                            cosmetic_differences=cos, new_text_preview=_cut(d.new_text)))
            old_mm = set(near_mismatches(block))
            for mm in near_mismatches(d.new_text):
                flags.append(InternalFlag(ref=d.ref, kind="date_near_mismatch", detail=mm,
                                          in_original=mm in old_mm))

        count = lambda s: sum(1 for c in clauses if c.status == s)  # noqa: E731
        return CompareAmendedClausesOutput(
            original_id=args.original_id, amendment_id=args.amendment_id,
            total_directives=len(directives), changed=count("changed"), unchanged=count("unchanged"),
            added=count("added"), missing_in_original=count("missing_in_original"),
            clauses=clauses, internal_flags=flags, warnings=warnings)