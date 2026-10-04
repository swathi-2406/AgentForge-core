# """extract_definitions: every defined term, where it's defined, how often it's used, and near-misses.

# Three kinds of definition:
#     definitions_section   "Tax" or "Taxes" shall mean ...        (a definition at the start of a line)
#     inline                ... (the "Lead Borrower") ...          (defined in passing)
#     points_elsewhere      "Review Lease" has the meaning stated in Section 3.2.

# Scope: a term whose definition points into ANOTHER document ("...in Section 1.01 of the Credit
# Agreement") is scope="external". Terms never defined in this filing (Ford imports most of its terms
# from Appendix 1) aren't in the map at all, so consistency checks only cover terms this filing owns.

# Near-misses are the mechanical part of a defined-term consistency check:
#     spelling_variant  "Supplement Lease Rent"  vs  "Supplemental Lease Rent"   (high confidence)
#     truncated         "Indenture Trustee"      vs  "Lease Indenture Trustee"   (medium)
#     word_swap         "Review Receivable"      vs  "Review Lease"              (low: the agent must read it)
# """

from __future__ import annotations

import bisect
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.read_document import ParsedDocument, load_document

# ---------- patterns ----------

TERM = r'"(?P<term>[A-Za-z][^"\n]{0,79})"'
ALT = r'(?:\s*,?\s+(?:or|and)\s+"(?P<alt>[A-Za-z][^"\n]{0,79})")?'
VERB = r"(?:shall\s+)?(?:means?|mean|has|have|refers?\s+to|includes?|shall\s+include)\b"
LINE_DEF_RE = re.compile(rf'(?m)^(?:\([a-z0-9]{{1,4}}\)\s*)?{TERM}{ALT}\s*,?\s*{VERB}')
MID_DEF_RE = re.compile(rf'(?<!^){TERM}{ALT}\s+{VERB}', re.M)
PAREN_RE = re.compile(r'\((?P<body>[^()]{0,200}?"[A-Za-z][^"\n]{0,79}"[^()]{0,200}?)\)')
NOT_A_DEFINITION = re.compile(r"as defined|within the meaning|as such term|referred to as|so-called", re.I)
POINTS_RE = re.compile(
    r"meanings?\s+(?:set\s+forth|stated|specified|assigned|given|provided|ascribed)(?:\s+(?:to\s+)?(?:it|such\s+term|them))?"
    r"\s+(?:in|to\s+it\s+in|on)\s+(?P<ref>(?:the\s+)?(?:preamble|recitals?|introductory\s+paragraph(?:\s+hereto|\s+hereof)?|(?:Section|Article|Schedule|Exhibit|Appendix|Annex)\s+[\w.()\-]+))"
    r"(?P<ext>\s+(?:of|to)\s+(?:the\s+)?(?!this\b)(?:[A-Z0-9][\w'&\-]*\s+|and\s+|of\s+){0,6}"
    r"(?:Agreement|Indenture|Lease|Supplement|Code|Act|Note|Trust|Amendment))?", re.I)

CONNECTORS = {"of", "and", "for", "the", "to", "in", "on", "under"}
STARTERS = {"the", "this", "that", "such", "any", "each", "all", "no", "every", "either", "neither", "a", "an",
            "if", "in", "upon", "on", "with", "by", "for", "other", "its", "their", "our", "same", "said"}

# ---------- models ----------

class Definition(BaseModel):
    term: str
    aliases: list[str] = Field(default_factory=list)
    source: Literal["definitions_section", "inline", "points_elsewhere"]
    scope: Literal["local", "external"] = "local"
    defined_in: str
    points_to: Optional[str] = None
    text: str
    char_offset: int
    usage_count: int = 0
    glued_uses: int = Field(0, description='Uses with no space after, e.g. "Componentshall" (formatting damage)')


class NearMiss(BaseModel):
    phrase: str
    likely_term: str
    kind: Literal["spelling_variant", "truncated", "word_swap"]
    confidence: Literal["high", "medium", "low"]
    count: int
    found_in: list[str]
    snippet: str


class DuplicateDefinition(BaseModel):
    term: str
    defined_in: list[str]
    note: Literal["restated_in_glossary", "defined_twice"] = Field(
        description="restated_in_glossary: body + appendix/schedule (normal). defined_twice: worth a look")

# ---------- helpers ----------

class _Where:
    def __init__(self, doc: ParsedDocument):
        self.doc, self.starts = doc, [s.char_start for s in doc.sections]

    def __call__(self, offset: int) -> str:
        i = bisect.bisect_right(self.starts, offset) - 1
        return self.doc.sections[i].number if i >= 0 else "preamble"

    def section_end(self, offset: int) -> int:
        i = bisect.bisect_right(self.starts, offset)
        return self.starts[i] if i < len(self.starts) else len(self.doc.text)


def singular(w: str) -> str:
    if len(w) <= 3:
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us")):
        return w[:-1]
    return w


def norm(phrase: str) -> str:
    """Compare key: lowercase, possessive and plural on the last word removed."""
    words = re.sub(r"'s?$", "", phrase.strip()).lower().split()
    if words:
        words[-1] = singular(words[-1])
    for i in range(len(words) - 1):          # "Events of Default" -> "event of default"
        if words[i + 1] == "of":
            words[i] = singular(words[i])
    return " ".join(words)


CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def camel_split(text: str) -> tuple[str, list[int]]:
    """'withPrudent Industry' -> 'with Prudent Industry', plus where spaces were inserted (to map offsets back)."""
    inserted = [m.start() for m in CAMEL.finditer(text)]
    return CAMEL.sub(" ", text), inserted


def to_original(pos: int, inserted: list[int]) -> int:
    return pos - bisect.bisect_left([p + i for i, p in enumerate(inserted)], pos)


PLURAL_SUFFIXES = ("'s", "s'", "es", "s", "")
# "Componentshall" is glue; "Governmental" / "Monthly" are real words.
FUNCTION_WORDS = {"shall", "and", "or", "of", "the", "to", "in", "for", "is", "be", "with", "by", "as", "on",
                  "at", "any", "all", "such", "that", "which", "under", "will", "may", "has", "have", "not",
                  "from", "its", "this", "are", "was", "were", "an", "a", "if", "upon", "hereunder", "thereof"}


def find_uses(text: str, names: list[str]) -> tuple[list[int], int]:
    """Positions where any name is used as a word, plus a count of glued uses ('Componentshall').

    str.find is C-speed; boundaries are checked by hand. A lowercase letter right before a
    capitalised term counts as a boundary, so 'withPrudent Industry Practice' is a use.
    """
    uses, glued = set(), 0
    for name in names:
        capital = name[0].isupper()
        pos = text.find(name)
        while pos != -1:
            before = text[pos - 1] if pos else " "
            if before != '"' and (not before.isalnum() or (capital and before.islower())):
                end = pos + len(name)
                for suffix in PLURAL_SUFFIXES:
                    if text.startswith(suffix, end) and not text[end + len(suffix):end + len(suffix) + 1].isalnum():
                        uses.add(pos)
                        break
                else:
                    rest = re.match(r"[a-z]+", text[end:end + 20])
                    if rest and rest.group(0) in FUNCTION_WORDS:
                        glued += 1
            pos = text.find(name, pos + 1)
    return sorted(uses), glued


# ---------- 1. find definitions ----------

def find_definitions(doc: ParsedDocument) -> list[Definition]:
    text, where = doc.text, _Where(doc)
    defs: list[Definition] = []
    line_starts = [m.start() for m in LINE_DEF_RE.finditer(text)]

    def add(m: re.Match, source: str, start: int, end: int):
        body = text[start:end].strip()
        points = POINTS_RE.search(text, m.end() - 12, min(m.end() + 250, end))
        points_to, scope = None, "local"
        if points and points.start() < m.end() + 40:
            source = "points_elsewhere"
            points_to = (points.group("ref") + (points.group("ext") or "")).strip().rstrip(".,;")
            scope = "external" if points.group("ext") else "local"
        defs.append(Definition(term=m.group("term").strip(), aliases=[a for a in [m.group("alt")] if a],
                               source=source, scope=scope, defined_in=where(m.start()), points_to=points_to,
                               text=body[:4000], char_offset=m.start()))

    for i, m in enumerate(LINE_DEF_RE.finditer(text)):
        nxt = line_starts[i + 1] if i + 1 < len(line_starts) else len(text)
        add(m, "definitions_section", m.start(), min(nxt, where.section_end(m.start())))
    covered = [(m.start(), m.end()) for m in LINE_DEF_RE.finditer(text)]
    line_defs = [(d.char_offset, d.char_offset + len(d.text) + 2, d.term) for d in defs]
    for m in MID_DEF_RE.finditer(text):           # 'such that "run-rate" means ...'
        if any(a <= m.start() < b for a, b in covered):
            continue                              # e.g. the alias "Taxes" in: "Tax" or "Taxes" shall mean
        if any(a <= m.start() < b and t == m.group("term").strip() for a, b, t in line_defs):
            continue                              # '"Trust Indenture Act" means ... the "Trust Indenture Act" means ...' 
        end = text.find("\n", m.end())
        add(m, "inline", max(0, m.start() - 150), end if end > 0 else len(text))
    for m in PAREN_RE.finditer(text):             # (the "Lead Borrower") / (each, a "Lender" and ... "Lenders")
        body = m.group("body")
        if NOT_A_DEFINITION.search(body):
            continue
        quoted = [q.strip() for q in re.findall(r'"([A-Za-z][^"\n]{0,79})"', body)]
        if not quoted or len(body) - sum(len(q) + 2 for q in quoted) > 60:
            continue  # a long parenthetical that merely quotes something
        sentence_start = max(text.rfind(". ", 0, m.start()) + 2, text.rfind("\n", 0, m.start()) + 1, m.start() - 300)
        defs.append(Definition(term=quoted[0], aliases=quoted[1:], source="inline", defined_in=where(m.start()),
                               text=text[sentence_start:m.end()].strip()[:4000], char_offset=m.start()))
    return sorted(defs, key=lambda d: d.char_offset)

# ---------- 2. usage counts ----------

def name_forms(name: str) -> list[str]:
    """The name plus its singular, so 'Mortgaged Properties' also finds 'Mortgaged Property'."""
    words = name.split()
    if not words or not words[-1][:1].isalpha():
        return [name]
    last = words[-1]
    sing = singular(last.lower())
    if sing == last.lower():
        return [name]
    sing = last[:len(sing)] if last.lower().startswith(sing) else (last[:-3] + "y" if last.endswith("ies") else last)
    return [name, " ".join(words[:-1] + [sing])]


def count_usage(doc: ParsedDocument, defs: list[Definition]) -> None:
    for d in defs:
        uses, glued = find_uses(doc.text, [f for n in [d.term] + d.aliases for f in name_forms(n)])
        d.usage_count = len([u for u in uses if u != d.char_offset])
        d.glued_uses = glued

# ---------- 3. near-misses ----------

SPLIT_WORDS = {"and", "or"}
INNER_CONNECTORS = CONNECTORS - SPLIT_WORDS


def excluded_ranges(doc: ParsedDocument) -> list[tuple[int, int]]:
    """Headings and the table of contents are Title Case on purpose; don't mine them for phrases."""
    out = []
    for sec in doc.sections:
        if sec.kind == "preamble":
            if doc.toc_entries_skipped:
                out.append((sec.char_start, sec.char_end))
            continue
        nl = doc.text.find("\n", sec.char_start)
        line_end = nl if nl != -1 else sec.char_end
        # Only the heading itself: "Section 13.2 Procedure for Termination." -- the paragraph may share the line.
        idx = doc.text.find(sec.heading, sec.char_start, line_end) if sec.heading else -1
        end = idx + len(sec.heading) if idx != -1 else min(line_end, sec.char_start + 20)
        if sec.kind != "section" and sec.heading.isupper():
            end = line_end
        out.append((sec.char_start, end))
    return out


def in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in ranges)


def capital_runs(doc: ParsedDocument) -> dict[str, dict]:
    """Whole capitalised phrases ('the Indenture Trustee shall' -> 'Indenture Trustee'), counted.

    Only maximal runs count, so 'Equity Pledge' inside 'Equity Pledge Agreement' is never a candidate.
    'and'/'or' split runs; 'of'/'the' may sit inside ('Event of Default'). ALL-CAPS words end a run.
    """
    split, inserted = camel_split(doc.text)
    skip = excluded_ranges(doc)
    starts = [a for a, _ in skip]
    runs: dict[str, dict] = {}
    run: list[tuple[str, int, int]] = []
    joined_split = False

    def in_skip(pos: int) -> bool:
        i = bisect.bisect_right(starts, pos) - 1
        return i >= 0 and pos < skip[i][1]

    def flush():
        toks = list(run)
        while toks and (toks[0][0].lower() in STARTERS or toks[0][0] in INNER_CONNECTORS):
            toks.pop(0)
        while toks and toks[-1][0] in INNER_CONNECTORS:
            toks.pop()
        if len(toks) < 2:
            return
        pos = to_original(toks[0][1], inserted)
        if in_skip(pos):
            return
        phrase = " ".join(t[0] for t in toks)
        g = runs.setdefault(norm(phrase), {"phrase": phrase, "count": 0, "pos": [], "fragment": False})
        g["count"] += 1
        g["fragment"] = g["fragment"] or fragment[0]
        if len(g["pos"]) < 5:
            g["pos"].append(pos)

    fragment = [False]   # True when this run follows "Capitalised and ..." (e.g. "Conservation and Recovery Act")
    prev_word = ""
    for m in re.finditer(r"[A-Za-z][A-Za-z'.\-]*[A-Za-z']|[A-Za-z]", split):
        word = m.group(0)
        caps = len(word) > 1 and word.replace("'", "").replace(".", "").isupper() and not re.fullmatch(r"(?:[A-Z]\.)+[A-Z]?", word)
        joined = run and split[run[-1][2]:m.start()] == " "
        if caps:
            flush(); run = []; fragment[0] = False
        elif word[0].isupper() or (word in INNER_CONNECTORS and run and joined):
            if not joined and run:
                flush(); run = []; fragment[0] = False
            if not run:
                fragment[0] = fragment[0] and joined_split
            run.append((word, m.start(), m.end()))
        else:
            split_after_name = word in SPLIT_WORDS and bool(run) and joined
            flush(); run = []
            fragment[0] = split_after_name
        joined_split = fragment[0]
        prev_word = word
    flush()
    return runs


def find_near_misses(doc: ParsedDocument, defs: list[Definition]) -> list[NearMiss]:
    text = doc.text
    where = _Where(doc)
    local = [d for d in defs if d.scope == "local"]
    defined = {norm(n) for d in defs for n in [d.term] + d.aliases}
    term_words = {w for k in defined for w in k.split()}
    usage = {norm(d.term): d.usage_count for d in local}
    runs = capital_runs(doc)
    skip = excluded_ranges(doc)
    out: dict[tuple[str, str], NearMiss] = {}

    def snippet(pos: int) -> str:
        return text[max(0, pos - 60):pos + 80].replace("\n", " ").strip()

    # spelling_variant / word_swap: same length, exactly one word different
    wildcard: dict[tuple, list[str]] = defaultdict(list)
    for key in runs:
        words = key.split()
        for i in range(len(words)):
            wildcard[(len(words), i, tuple(words[:i] + words[i + 1:]))].append(key)
    for d in local:
        tkey = norm(d.term)
        twords = tkey.split()
        if len(twords) < 2:
            continue
        for i, w in enumerate(twords):
            if w in CONNECTORS:
                continue
            for key in wildcard.get((len(twords), i, tuple(twords[:i] + twords[i + 1:])), []):
                if key == tkey or key in defined:
                    continue
                other = key.split()[i]
                if other in STARTERS or other in CONNECTORS:
                    continue
                g = runs[key]
                if SequenceMatcher(None, w, other).ratio() >= 0.8:
                    kind, conf = "spelling_variant", "high"
                elif (g["count"] <= 2 and usage.get(tkey, 0) >= 5 and other not in term_words
                      and not g["fragment"] and "'" not in g["phrase"]):
                    kind, conf = "word_swap", "low"   # rare phrase, swapped word appears in no defined term
                else:
                    continue
                out[(key, tkey)] = NearMiss(phrase=g["phrase"], likely_term=d.term, kind=kind, confidence=conf,
                                            count=g["count"], found_in=sorted({where(p) for p in g["pos"]}),
                                            snippet=snippet(g["pos"][0]))

    # truncated: "the Indenture Trustee" where "Lease Indenture Trustee" is the term
    for d in local:
        words = d.term.split()
        if len(words) < 3 or words[1] in CONNECTORS:
            continue
        tail = " ".join(words[1:])
        if norm(tail) in defined or tail not in text:
            continue
        own_def = (d.char_offset, d.char_offset + len(d.text) + 5)
        positions = []
        for pos in find_uses(text, [tail])[0]:
            if own_def[0] <= pos < own_def[1] or in_ranges(pos, skip):
                continue  # own definition text, headings, table of contents
            # "<word> <connector?> Tail": the word right before, and an optional "of"/"the" between
            before = re.search(r"([A-Za-z][\w'.\-]*)\s+(?:([a-z]+)\s+)?$", text[max(0, pos - 60):pos])
            word, connector = (before.group(1), before.group(2)) if before else ("", None)
            if connector in CONNECTORS and word[:1].isupper():
                continue  # "Solicitation of Discounted Prepayment Offers": inside a longer name
            prev = "" if connector else word
            after = text[pos + len(tail):pos + len(tail) + 25]
            if prev == words[0] or prev[:1].isupper() or re.match(r"(?:s|es|'s)?\s+[A-Z]", after):
                continue  # the full term, or part of a longer capitalised name ("...Institutions Reform")
            positions.append(pos)
        if positions:
            conf = "medium" if len(positions) <= 5 else "low"   # used 14x on its own = probably its own concept
            out[(norm(tail), norm(d.term))] = NearMiss(
                phrase=tail, likely_term=d.term, kind="truncated", confidence=conf, count=len(positions),
                found_in=sorted({where(p) for p in positions[:5]}), snippet=snippet(positions[0]))
    order = {"high": 0, "medium": 1, "low": 2}
    best: dict[str, NearMiss] = {}   # one entry per phrase, at its most confident match
    for n in sorted(out.values(), key=lambda n: (order[n.confidence], -usage.get(norm(n.likely_term), 0))):
        best.setdefault(norm(n.phrase), n)
    return list(best.values())

# ---------- 4. duplicates ----------

def pointer_target(points_to: Optional[str]) -> Optional[str]:
    """'Section 2.05(a)(v)(D)(2)' -> '2.05';  'the preamble' -> 'preamble'."""
    if not points_to:
        return None
    if re.search(r"preamble|recital|introductory", points_to, re.I):
        return "preamble"
    m = re.search(r"(?:Section|Article)\s+([\dIVXL]+(?:\.\d+)*)", points_to)
    return m.group(1) if m else None


def find_duplicates(defs: list[Definition], doc: ParsedDocument) -> list[DuplicateDefinition]:
    kinds = {sec.number: sec.kind for sec in doc.sections}
    by_term: dict[str, list[Definition]] = defaultdict(list)
    for d in defs:
        by_term[d.term].append(d)
    out = []
    for t, group in by_term.items():
        places = [d.defined_in for d in group]
        real = [d for d in group
                if not (d.source == "points_elsewhere" and any(
                    (tgt := pointer_target(d.points_to)) and (o.defined_in == tgt or o.defined_in.startswith(tgt + "."))
                    for o in group if o is not d))]
        if len(real) < 2:
            continue  # "Review Fee" has the meaning in Section 4.3 + defined in 4.3 = one definition
        places = [d.defined_in for d in real]
        glossary = [p for p in places if kinds.get(p) == "schedule"]
        note = "restated_in_glossary" if len(glossary) == 1 and len(set(places)) == len(places) else "defined_twice"
        out.append(DuplicateDefinition(term=t, defined_in=places, note=note))
    return sorted(out, key=lambda d: d.note)

# ---------- the tool ----------

class ExtractDefinitionsInput(ToolInput):
    filing_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="id from manifest.yaml")
    term: Optional[str] = Field(default=None, min_length=1, description="Look up one term (full definition text)")
    only_issues: bool = Field(default=False, description="Skip the full term list; return only near-misses, "
                                                         "duplicates, unused and glued terms")
    limit: int = Field(default=1000, ge=1, le=5000)


class ExtractDefinitionsOutput(ToolOutput):
    filing_id: str
    total_terms: int
    counts: dict[str, int]
    definitions: list[Definition]
    near_misses: list[NearMiss]
    duplicates: list[DuplicateDefinition]
    unused_terms: list[str]
    truncated: bool
    warnings: list[str]


@register_tool
class ExtractDefinitions(Tool):
    name = "extract_definitions"
    description = ("Map every defined term in a filing (definitions section and inline), with usage counts, "
                   "and flag near-miss phrases like 'Supplement Lease Rent' vs the defined 'Supplemental Lease Rent'.")
    Input = ExtractDefinitionsInput
    Output = ExtractDefinitionsOutput

    def run(self, args: ExtractDefinitionsInput) -> ExtractDefinitionsOutput:
        doc = load_document(args.filing_id)
        defs = find_definitions(doc)
        count_usage(doc, defs)
        near = find_near_misses(doc, defs)
        dups = find_duplicates(defs, doc)
        unused = sorted({d.term for d in defs if d.scope == "local" and d.usage_count == 0})
        counts: dict[str, int] = defaultdict(int)
        for d in defs:
            counts[d.source] += 1
            counts[f"scope_{d.scope}"] += 1
        warnings = list(doc.warnings)
        if any(d.glued_uses for d in defs):
            warnings.append("Some terms appear glued to the next word (e.g. 'Componentshall'). That's formatting "
                            "damage in the filing, not a drafting error.")

        if args.term:
            q = args.term.strip().lower()
            chosen = [d for d in defs if q in [n.lower() for n in [d.term] + d.aliases]]
            if not chosen:
                warnings.append(f"'{args.term}' isn't defined in {args.filing_id}.")
        elif args.only_issues:
            chosen = [d for d in defs if d.glued_uses]
        else:
            chosen = [d.model_copy(update={"text": d.text[:200]}) for d in defs]
        return ExtractDefinitionsOutput(
            filing_id=args.filing_id, total_terms=len({d.term for d in defs}), counts=dict(counts),
            definitions=chosen[:args.limit], near_misses=near, duplicates=dups, unused_terms=unused,
            truncated=len(chosen) > args.limit, warnings=warnings)