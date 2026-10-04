# """Tool for extracting dates and temporal references from a document."""
# """extract_dates: every date and every time period in a filing, with where it appears.

# Two kinds of mention:
#     date       "October 28, 2020", "the 15th day of March, 2021", "12/31/2020", "December 2020"
#     duration   "three (3) Business Days", "sixty (60) days", "eighteen months"

# Mechanical checks (the agent does the reasoning about what the dates MEAN):
#     invalid_date      "February 30, 2021" is not a real date
#     number_mismatch   "sixty (90) days": the words and the digits disagree

# For the hard tier, run it on an amendment and its original with the same `section`
# (e.g. "2.02") to compare notice periods side by side.
# """

from __future__ import annotations

import bisect
import calendar
import re
from collections import defaultdict
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.read_document import ParsedDocument, load_document

# ---------- dates ----------

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
MONTH = r"(?P<month>" + "|".join(sorted((m for m in MONTHS), key=len, reverse=True)) + r")\.?"
YEAR = r"(?P<year>(?:19|20)\d{2})"
DATE_PATTERNS = [
    re.compile(rf"\b{MONTH}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+{YEAR}\b", re.I),                  # October 28, 2020
    re.compile(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+day\s+of\s+{MONTH}\s*,?\s+{YEAR}\b", re.I),       # 15th day of March, 2021
    re.compile(rf"\b(?P<day>\d{{1,2}})\s+{MONTH}\s*,?\s+{YEAR}\b", re.I),                                   # 15 March 2021
    re.compile(r"\b(?P<mnum>\d{1,2})/(?P<day>\d{1,2})/(?P<year>(?:19|20)\d{2})\b"),                        # 12/31/2020
    re.compile(rf"\b{MONTH}\s+{YEAR}\b", re.I),                                                              # December 2020
]
CUE_RE = re.compile(r"(dated(?:\s+as\s+of)?|effective(?:\s+as\s+of)?|as\s+of|on\s+or\s+(?:before|after|prior\s+to)|"
                    r"no\s+later\s+than|prior\s+to|before|after|until|through|ending(?:\s+on)?|beginning(?:\s+on)?|"
                    r"commencing(?:\s+on)?|on)\s*(?:the\s+)?$", re.I)
DEF_LINE_RE = re.compile(r'(?:\([a-z0-9]{1,4}\)\s*)?"([^"\n]{1,80})"')

# ---------- durations ----------

ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
        "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NUMBER_WORDS = set(ONES) | set(TENS) | {"hundred", "and"}
UNIT_RE = re.compile(r"\b(?P<unit>(?:calendar\s+|consecutive\s+)?(?:Business\s+Days?|days?|months?|years?|weeks?|hours?))\b")
DIGITS_BEFORE_RE = re.compile(r"\(?(?P<num>\d{1,4})\)?\s*$")


def words_to_number(words: list[str]) -> Optional[int]:
    """['one', 'hundred', 'twenty'] -> 120; ['eighteen'] -> 18; anything else -> None."""
    total, current = 0, 0
    seen = False
    for w in words:
        if w in ONES:
            current += ONES[w]
        elif w in TENS:
            current += TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w == "and" and seen:
            continue
        else:
            return None
        seen = True
    return total + current if seen else None


def number_words_before(text: str, end: int) -> tuple[Optional[int], int]:
    """Read number words backwards from `end`: '...within eighteen months' -> (18, start)."""
    tokens = list(re.finditer(r"[A-Za-z]+", text[max(0, end - 60):end]))
    base = max(0, end - 60)
    best: tuple[Optional[int], int] = (None, end)
    for i in range(len(tokens) - 1, max(-1, len(tokens) - 5), -1):
        # tokens[i:] must be contiguous number words, joined only by spaces or hyphens
        chunk = tokens[i:]
        gaps_ok = all(re.fullmatch(r"[\s\-]+", text[base + a.end():base + b.start()]) for a, b in zip(chunk, chunk[1:]))
        tail_ok = re.fullmatch(r"[\s\-]*", text[base + chunk[-1].end():end]) is not None
        value = words_to_number([t.group(0).lower() for t in chunk])
        if not (gaps_ok and tail_ok) or value is None:
            break
        best = (value, base + chunk[0].start())
    return best


def canonical_unit(raw: str) -> str:
    u = raw.lower().replace("calendar ", "").replace("consecutive ", "")
    if u.startswith("business"):
        return "business_day"
    return u.rstrip("s")

# ---------- models ----------

class DateMention(BaseModel):
    kind: Literal["date", "duration"]
    text: str = Field(description="As written, e.g. 'three (3) Business Days'")
    value: str = Field(description="ISO date '2020-10-28' / month '2020-12', or a duration like '3 business_day'")
    section: str
    label: Optional[str] = Field(None, description="Defined term whose definition holds the date, or a cue like 'dated as of'")
    issue: Optional[Literal["invalid_date", "number_mismatch"]] = None
    snippet: str
    char_offset: int


class DateSummary(BaseModel):
    value: str
    count: int
    sections: list[str]

# ---------- finders ----------

class _Where:
    def __init__(self, doc: ParsedDocument):
        self.doc, self.starts = doc, [s.char_start for s in doc.sections]

    def __call__(self, offset: int) -> str:
        i = bisect.bisect_right(self.starts, offset) - 1
        return self.doc.sections[i].number if i >= 0 else "preamble"


def snippet(text: str, a: int, b: int) -> str:
    return text[max(0, a - 80):b + 80].replace("\n", " ").strip()


def date_label(text: str, start: int) -> Optional[str]:
    line_start = text.rfind("\n", 0, start) + 1
    m = DEF_LINE_RE.match(text, line_start)
    if m and start - line_start < 400:
        return m.group(1)                      # '"Closing Date" means October 28, 2020'
    cue = CUE_RE.search(text[max(0, start - 30):start])
    return cue.group(1).lower() if cue else None


def find_dates(doc: ParsedDocument) -> list[DateMention]:
    text, where, out, taken = doc.text, _Where(doc), [], []
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue  # "December 2020" inside "December 31, 2020" already found
            g = m.groupdict()
            month = int(g["mnum"]) if g.get("mnum") else MONTHS[g["month"].lower().rstrip(".")]
            year, day = int(g["year"]), g.get("day")
            issue = None
            if day:
                try:
                    value = date(year, month, int(day)).isoformat()
                except ValueError:
                    value, issue = f"{year:04d}-{month:02d}-{int(day):02d}", "invalid_date"
            else:
                value = f"{year:04d}-{month:02d}" if 1 <= month <= 12 else f"{year}-??"
            taken.append((m.start(), m.end()))
            out.append(DateMention(kind="date", text=m.group(0), value=value, section=where(m.start()),
                                   label=date_label(text, m.start()), issue=issue,
                                   snippet=snippet(text, m.start(), m.end()), char_offset=m.start()))
    return out


def find_durations(doc: ParsedDocument) -> list[DateMention]:
    text, where, out = doc.text, _Where(doc), []
    for m in UNIT_RE.finditer(text):
        before = text[max(0, m.start() - 12):m.start()]
        digits = DIGITS_BEFORE_RE.search(before)
        start, num = m.start(), None
        if digits:
            num = int(digits.group("num"))
            start = m.start() - len(before) + digits.start()
        word_value, word_start = number_words_before(text, start)
        if num is None and word_value is None:
            continue  # "the next Business Day" -- no amount
        amount = num if num is not None else word_value
        issue = "number_mismatch" if num is not None and word_value is not None and num != word_value else None
        if word_value is not None:
            start = word_start
        out.append(DateMention(kind="duration", text=text[start:m.end()], value=f"{amount} {canonical_unit(m.group('unit'))}",
                               section=where(start), issue=issue, snippet=snippet(text, start, m.end()),
                               char_offset=start))
    return out


def find_mentions(doc: ParsedDocument) -> list[DateMention]:
    return sorted(find_dates(doc) + find_durations(doc), key=lambda d: d.char_offset)

# ---------- the tool ----------

class ExtractDatesInput(ToolInput):
    filing_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="id from manifest.yaml")
    kind: Optional[Literal["date", "duration"]] = None
    section: Optional[str] = Field(None, min_length=1, description='Only mentions in this section, e.g. "2.02" (also 2.02.x)')
    only_issues: bool = Field(False, description="Only invalid dates and word/digit mismatches")
    limit: int = Field(1000, ge=1, le=5000)


class ExtractDatesOutput(ToolOutput):
    filing_id: str
    total: int
    counts: dict[str, int]
    dates: list[DateSummary] = Field(description="Each distinct date with how often and where it appears")
    mentions: list[DateMention]
    truncated: bool
    warnings: list[str]


@register_tool
class ExtractDates(Tool):
    name = "extract_dates"
    description = ("List every date and time period ('three (3) Business Days') in a filing with its section and "
                   "context. Flags impossible dates and word/digit mismatches like 'sixty (90) days'. Use section= "
                   "to compare the same clause across an amendment and its original.")
    Input = ExtractDatesInput
    Output = ExtractDatesOutput

    def run(self, args: ExtractDatesInput) -> ExtractDatesOutput:
        doc = load_document(args.filing_id)
        mentions = find_mentions(doc)
        counts: dict[str, int] = defaultdict(int)
        by_date: dict[str, list[str]] = defaultdict(list)
        for m in mentions:
            counts[m.kind] += 1
            if m.issue:
                counts[m.issue] += 1
            if m.kind == "date":
                by_date[m.value].append(m.section)
        summary = [DateSummary(value=v, count=len(s), sections=sorted(set(s))) for v, s in sorted(by_date.items())]

        chosen = mentions
        if args.kind:
            chosen = [m for m in chosen if m.kind == args.kind]
        if args.section:
            q = args.section.lower()
            chosen = [m for m in chosen if m.section.lower() == q or m.section.lower().startswith(q + ".")]
        if args.only_issues:
            chosen = [m for m in chosen if m.issue]
        return ExtractDatesOutput(filing_id=args.filing_id, total=len(mentions), counts=dict(counts), dates=summary,
                                  mentions=chosen[:args.limit], truncated=len(chosen) > args.limit,
                                  warnings=list(doc.warnings))