"""read_document: turn a raw EDGAR exhibit into clean, section-addressable text.

Pipeline:  raw bytes -> plain text -> normalized lines -> heading candidates
           -> drop table-of-contents duplicates -> sections

Other tools reuse load_document(filing_id) directly, so a 2 MB filing is parsed
once per process (cached by file path + modified time).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from agentforge_core.tools.base import Tool, ToolError, ToolInput, ToolOutput, register_tool

# ---------- where filings live ----------

def get_root() -> Path:
    """Repo root. AGENTFORGE_ROOT overrides it (tests use this)."""
    env = os.getenv("AGENTFORGE_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


def manifest_entry(filing_id: str, root: Path | None = None) -> dict:
    root = root or get_root()
    manifest = root / "data" / "contracts" / "manifest.yaml"
    if not manifest.exists():
        raise ToolError(f"No manifest at {manifest}")
    entries = yaml.safe_load(manifest.read_text(encoding="utf-8")) or []
    by_id = {e["id"]: e for e in entries}
    if filing_id not in by_id:
        raise ToolError(f"Unknown filing_id '{filing_id}'. Known: {', '.join(sorted(by_id))}")
    return by_id[filing_id]


def resolve_filing_path(filing_id: str, root: Path | None = None) -> Path:
    root = root or get_root()
    rel = manifest_entry(filing_id, root).get("local_path")
    path = root / rel if rel else None
    if not path or not path.exists():
        raise ToolError(f"'{filing_id}' isn't downloaded yet. Run: python scripts/fetch_edgar_filing.py sync")
    return path

# ---------- step 1: raw bytes -> plain text ----------

BLOCK_TAGS = ["p", "div", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6",
              "blockquote", "pre", "center", "ul", "ol", "dd", "dt", "hr"]
CELL_TAGS = ["td", "th"]
SPACING_STYLE = re.compile(r"padding|margin|width|inline-block|text-indent", re.I)


def html_to_text(raw: bytes) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "head", "noscript", "ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        tag.decompose()
    for node in soup.find_all(string=True):
        if ("\n" in node or "\r" in node) and not node.find_parent("pre"):
            node.replace_with(re.sub(r"[\r\n]+", " ", str(node)))
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # Newlines only at block edges. Inline <span>/<font> runs stay on one line,
    # so "Section" and "13.2" in separate spans still read "Section 13.2".
    for tag in soup.find_all(BLOCK_TAGS):
        if tag.parent is not None:
            tag.insert_before("\n")
        tag.append("\n")
    for tag in soup.find_all(CELL_TAGS):
        tag.append(" ")
    # EDGAR often fakes a tab with a padded span: <span>(a)</span><span style="padding-left:18pt">If
    for tag in soup.find_all(style=SPACING_STYLE):
        tag.insert_before(" ")
    return soup.get_text()


def raw_to_text(raw: bytes) -> str:
    head = raw[:5000].lower()
    if any(m in head for m in (b"<html", b"<body", b"<p", b"<div", b"<table")):
        return html_to_text(raw)
    text = raw.decode("utf-8", errors="replace")
    return re.sub(r"</?(PAGE|TEXT|DOCUMENT|TYPE|SEQUENCE|FILENAME|DESCRIPTION)[^>]*>", "\n", text, flags=re.I)

# ---------- step 2: normalize ----------

CHAR_MAP = str.maketrans({
    "\xa0": " ", "\u2002": " ", "\u2003": " ", "\u2009": " ", "\t": " ", "\r": "",
    "\u200b": "", "\ufeff": "",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-",
})
PAGE_NUMBER_LINE = re.compile(r"^(?:-\s*)?(?:page\s+)?(?:\d{1,3}|[ivxl]{1,6})(?:\s*-)?$", re.I)


def normalize(text: str) -> str:
    lines = []
    for line in text.translate(CHAR_MAP).split("\n"):
        line = re.sub(r" {2,}", " ", line).strip()
        line = re.sub(r"^(\([a-zA-Z0-9]{1,5}\))(?=[A-Za-z])", r"\1 ", line)              # (a)If -> (a) If
        line = re.sub(r"^((?:Section|SECTION)\s*\d+(?:\.\d+)*\.?)(?=[A-Z\[])", r"\1 ", line)  # 13.2Procedure
        if PAGE_NUMBER_LINE.match(line):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

# ---------- step 3: find headings ----------

ARTICLE_RE = re.compile(r"^ARTICLE\s+([IVXLC]+|\d+)\b\.?\s*(.*)$", re.I)
SECTION_RE = re.compile(r"^(?:SECTION|Section|§)\s*(\d+(?:\.\d+)*)\.?(?=\s|$|[A-Z\[])\s*(.*)$")
BARE_SECTION_RE = re.compile(r"^(\d+\.\d+)\.?\s+(.+)$")
SCHEDULE_RE = re.compile(r"^(SCHEDULE|EXHIBIT|ANNEX|APPENDIX)\s+([A-Z0-9][A-Z0-9.\-]*)\b\.?\s*(.*)$", re.I)


def looks_like_heading_rest(rest: str) -> bool:
    """'Section 13.2(d) of this Lease...' is a sentence, not a heading."""
    return not rest or not re.match(r"[a-z(]", rest)


def clean_heading(rest: str, next_line: str) -> str:
    rest = rest.lstrip(" .:-")
    if not rest:  # heading on its own line, e.g. "ARTICLE I" then "DEFINITIONS"
        if (next_line and len(next_line) <= 100 and next_line[0].isupper()
                and not next_line.endswith((".", ":", ";")) and not SECTION_RE.match(next_line)):
            rest = next_line
        else:
            return ""
    m = re.match(r"^(.{1,150}?)\.(?:\s|$)", rest)
    heading = m.group(1) if m else (rest if len(rest) <= 150 else rest[:80])
    heading = re.sub(r"\s+\d{1,3}$", "", heading).strip()  # drop TOC page numbers
    return "" if is_sentence(heading) else heading


SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or",
               "the", "to", "under", "upon", "with", "etc"}


def is_sentence(heading: str) -> bool:
    """'Borrowings, Conversions and Continuations' is a title.
    'Subject to the terms and conditions set forth herein' is a sentence (untitled section)."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", heading)
    lowercase = [w for w in words if w[0].islower() and w.lower() not in SMALL_WORDS]
    return len(lowercase) >= 2


def find_candidates(text: str) -> list[dict]:
    lines = text.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    found = []
    for i, line in enumerate(lines):
        if not line:
            continue
        nxt = next((l for l in lines[i + 1:i + 3] if l), "")
        m = ARTICLE_RE.match(line)
        if m and looks_like_heading_rest(m.group(2)):
            found.append(dict(kind="article", number=f"Article {m.group(1).upper()}",
                              heading=clean_heading(m.group(2), nxt), start=offsets[i]))
            continue
        m = SECTION_RE.match(line)
        if m and looks_like_heading_rest(m.group(2)):
            found.append(dict(kind="section", number=m.group(1),
                              heading=clean_heading(m.group(2), nxt), start=offsets[i]))
            continue
        m = BARE_SECTION_RE.match(line)
        if m and m.group(2)[0].isupper() and (len(line) <= 150 or re.match(r"^[^.]{1,120}\.\s", m.group(2))):
            found.append(dict(kind="section", number=m.group(1),
                              heading=clean_heading(m.group(2), nxt), start=offsets[i]))
            continue
        m = SCHEDULE_RE.match(line)
        if m and len(line) <= 100 and (looks_like_heading_rest(m.group(3)) or not line.endswith(".")):
            found.append(dict(kind="schedule", number=f"{m.group(1).title()} {m.group(2)}",
                              heading=clean_heading(m.group(3), nxt), start=offsets[i]))
    return found


def drop_toc_duplicates(cands: list[dict]) -> tuple[list[dict], list[dict]]:
    """A number listed twice = once in the table of contents, once for real.
    The TOC always comes first, so keep the LAST occurrence of each number.
    (Body length doesn't work: a real ARTICLE heading has an almost empty body too.)"""
    last = {c["number"]: i for i, c in enumerate(cands)}
    kept = [c for i, c in enumerate(cands) if last[c["number"]] == i]
    dropped = [c for i, c in enumerate(cands) if last[c["number"]] != i]
    return kept, dropped


def heading_pattern(c: dict) -> re.Pattern:
    if c["kind"] == "article":
        num = rf"ARTICLE\s+{re.escape(c['number'].split()[1])}\b"
    elif c["kind"] == "schedule":
        word, label = c["number"].split(maxsplit=1)
        num = rf"(?i:{word})\s+{re.escape(label)}\b"
    else:
        num = rf"(?:Section|SECTION|§)\s*{re.escape(c['number'])}\.?(?![\d(])"
    first_word = re.match(r"[A-Za-z]+", c["heading"] or "")
    title = rf"\s*\W*{re.escape(first_word.group(0))}" if first_word else ""
    return re.compile(num + title)


def recover_toc_only(cands: list[dict], dropped: list[dict], text: str) -> tuple[list[dict], list[dict], list[str]]:
    """Entries still sitting inside the TOC never got a body heading on its own line.
    Look for the heading mid-line in the body (e.g. '...the Borrower. Section 7.02 Investments.')."""
    if not dropped:
        return cands, [], []
    toc_start = min(c["start"] for c in dropped)
    dup_numbers = {c["number"] for c in dropped}
    # The body starts at the first real heading that also appeared in the TOC.
    body_start = min((c["start"] for c in cands if c["number"] in dup_numbers), default=len(text))
    toc_only = [c for c in cands if toc_start <= c["start"] < body_start]
    toc_end = max(c["start"] for c in dropped + toc_only)
    kept = [c for c in cands if c not in toc_only]
    body_from = text.find("\n", toc_end) + 1 or len(text)
    recovered, missing = [], []
    for c in toc_only:
        m = heading_pattern(c).search(text, body_from)
        if m:
            recovered.append({**c, "start": m.start()})
        else:
            missing.append(c["number"])
    kept = sorted(kept + recovered, key=lambda c: c["start"])
    warnings = [f"Listed in the table of contents but no heading found in the body: {', '.join(missing)}"] if missing else []
    return kept, toc_only, warnings


def drop_cover_labels(cands: list[dict]) -> list[dict]:
    """'EXHIBIT 10.24' at the top of the filing labels the whole document, so it belongs in the preamble."""
    first_body = next((i for i, c in enumerate(cands) if c["kind"] != "schedule"), len(cands))
    return cands[first_body:]


# ---------- step 4: build sections ----------

class Section(BaseModel):
    number: str = Field(description='"13.2", "Article IV", "Schedule B", or "preamble"')
    kind: Literal["preamble", "article", "section", "schedule"]
    heading: str
    article: Optional[str] = None
    text: str
    char_start: int
    char_end: int


class ParsedDocument(BaseModel):
    filing_id: str
    source_path: str
    text: str
    sections: list[Section]
    toc_entries_skipped: int
    warnings: list[str]

    def section(self, number: str) -> Optional[Section]:
        return next((s for s in self.sections if s.number.lower() == number.lower()), None)


def parse_text(filing_id: str, source_path: str, text: str) -> ParsedDocument:
    cands, dropped = drop_toc_duplicates(drop_cover_labels(find_candidates(text)))
    cands, toc_only, warnings = recover_toc_only(cands, dropped, text)
    skipped = len(dropped) + len(toc_only)
    sections = []

    first = cands[0]["start"] if cands else len(text)
    if text[:first].strip():
        sections.append(Section(number="preamble", kind="preamble", heading="", text=text[:first].strip(),
                                char_start=0, char_end=first))
    article = None
    for i, c in enumerate(cands):
        end = cands[i + 1]["start"] if i + 1 < len(cands) else len(text)
        if c["kind"] == "article":
            article = c["number"]
        elif c["kind"] == "schedule":
            article = None
        sections.append(Section(number=c["number"], kind=c["kind"], heading=c["heading"],
                                article=article if c["kind"] == "section" else None,
                                text=text[c["start"]:end].strip(), char_start=c["start"], char_end=end))

    if not cands:
        warnings.append("No section headings found; whole document returned as 'preamble'.")
    if len(text) < 2000:
        warnings.append(f"Only {len(text)} characters of text. Is this an error page instead of a contract?")
    return ParsedDocument(filing_id=filing_id, source_path=source_path, text=text,
                          sections=sections, toc_entries_skipped=skipped, warnings=warnings)


@lru_cache(maxsize=16)
def _parse_cached(filing_id: str, path_str: str, mtime: float) -> ParsedDocument:
    text = normalize(raw_to_text(Path(path_str).read_bytes()))
    return parse_text(filing_id, path_str, text)


def load_document(filing_id: str, root: Path | None = None) -> ParsedDocument:
    """What other tools call. Parsed once per file version, then cached."""
    path = resolve_filing_path(filing_id, root)
    return _parse_cached(filing_id, str(path), path.stat().st_mtime)

# ---------- the tool ----------

class ReadDocumentInput(ToolInput):
    filing_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="id from manifest.yaml")
    section: Optional[str] = Field(default=None, min_length=1,
                                   description='Return only this section, e.g. "13.2" or "Schedule B". '
                                               '"13" also returns 13.1, 13.2, ...')


class ReadDocumentOutput(ToolOutput):
    filing_id: str
    char_count: int
    total_sections: int
    toc_entries_skipped: int
    sections: list[Section]
    warnings: list[str]


@register_tool
class ReadDocument(Tool):
    name = "read_document"
    description = ("Load a filing and return its text split into numbered sections "
                   "(articles, sections, schedules). Optionally return one section only.")
    Input = ReadDocumentInput
    Output = ReadDocumentOutput

    def run(self, args: ReadDocumentInput) -> ReadDocumentOutput:
        doc = load_document(args.filing_id)
        sections, warnings = doc.sections, list(doc.warnings)
        if args.section:
            q = args.section.strip().lower()
            sections = [s for s in sections if s.number.lower() == q or s.number.lower().startswith(q + ".")]
            if not sections:
                warnings.append(f"No section '{args.section}' in {args.filing_id}.")
        return ReadDocumentOutput(filing_id=doc.filing_id, char_count=len(doc.text),
                                  total_sections=len(doc.sections), toc_entries_skipped=doc.toc_entries_skipped,
                                  sections=sections, warnings=warnings)