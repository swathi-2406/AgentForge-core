"""Day 7: show the evidence for a Tier 1 task before you write its YAML.

    python scripts/prove_task.py redwire_credit_original dangling
    python scripts/prove_task.py redwire_credit_original dangling --scope 2.05
    python scripts/prove_task.py tva_facility_lease numbering --scope "Section 4"
    python scripts/prove_task.py redwire_credit_amend1 numbering --scope "Article II"

dangling   asks extract_cross_references (the agent's own tool) for problems in scope.
numbering  scans (i)(ii)(iii) and (a)(b)(c) lists in scope for jumps like (iv) -> (vi).

It prints CANDIDATES, not verdicts. You read each one and decide:
real -> ground_truth.md row · harmless -> a trap in the task · nothing printed -> clean.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentforge_core.tools import call_tool  # noqa: E402
from agentforge_core.tools.read_document import load_document  # noqa: E402

ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
         "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15}
ARTICLE = {k.upper(): v for k, v in ROMAN.items()}
# "(vi)" in "clauses (iv) and (vi)" or "Section 7.06(i)(iii)" is a reference, not a list item
REFERENCE_BEFORE = re.compile(
    r"(?:sub-?clauses?|clauses?|paragraphs?|items?|sections?\s*\d+(?:\.\d+)*[a-z]?|articles?\s+\w+)"
    r"\s*(?:\([a-z0-9]+\)\s*(?:,|and/or|and|or|through|to|-)?\s*)*$", re.I)


def in_scope(section: str, scope: str) -> bool:
    """'whole_document', '2.05', 'Section 15', 'Article VI', 'Schedule B' -> does this section count?"""
    if scope == "whole_document":
        return True
    s, sc = section.strip().lower(), scope.strip().lower()
    if s == sc:
        return True
    m = re.fullmatch(r"(?:section\s+)?(\d+(?:\.\d+)*)", sc)
    if m:
        return s == m.group(1) or s.startswith(m.group(1) + ".")
    m = re.fullmatch(r"article\s+([ivxl]+|\d+)", sc)
    if m:
        n = m.group(1)
        num = str(ARTICLE.get(n.upper(), n))
        return s == sc or s.startswith(num + ".")
    return False


def section_text(section) -> str:
    for name in ("text", "body", "content"):
        value = getattr(section, name, None)
        if isinstance(value, str):
            return value
    raise SystemExit("Can't find a section's text field; use the same name you set in find_text.py.")


def as_dict(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def prove_dangling(filing: str, scope: str) -> int:
    out = as_dict(call_tool("extract_cross_references", {"filing_id": filing, "only_problems": True, "limit": 5000}))
    rows = [r for r in out["references"] if in_scope(str(r["found_in"]), scope)]
    for r in rows:
        print(f"[§{r['found_in']}]  {r['status']:<18} {r['target']:<16} ...{' '.join(str(r['snippet']).split())[:90]}...")
    return len(rows)


def _kind(tok: str) -> str:
    """'b' -> letter, 'vi' -> roman, 'i'/'v'/'x' -> either (could be both)."""
    if len(tok) > 1 and tok in ROMAN:
        return "roman"
    if len(tok) == 1 and tok.isalpha():
        return "either" if tok in "ivx" else "letter"
    return "other"


def gaps(text: str):
    """Yield (kind, before, after, offset) for list jumps such as (iv) -> (vi) or (b) -> (d)."""
    last = {"roman": 0, "letter": 0}
    for m in re.finditer(r"\(([ivx]{1,4}|[a-z])\)", text):
        tok, at = m.group(1), m.start()
        prev = text[at - 1] if at else " "
        if prev.isalnum() or prev == ")":
            continue                       # "number(s)", "3.3(j)", "(b)(i)": part of a word or reference
        ref = REFERENCE_BEFORE.search(text[max(0, at - 60):at])
        if ref:
            chain = re.findall(r"\(([a-z0-9]+)\)", ref.group(0))
            # "Section 2.14(a) and (vi)": a roman item can't continue a letter chain, so it's a list item
            if not (chain and {_kind(chain[-1]), _kind(tok)} == {"letter", "roman"}):
                continue                   # "clauses (a), (b) and (d)"
        letter_next = len(tok) == 1 and last["letter"] > 0 and ord(tok) - 96 == last["letter"] + 1
        if len(tok) == 1 and tok in "wxyz" and not letter_next and not (tok == "x" and last["roman"] == 9):
            continue                       # inline alternatives: "(x) three days or (y) five"
        if len(tok) == 1 and (tok not in "ivx" or letter_next):
            kind, val = "letter", ord(tok) - 96
        elif tok in ROMAN:
            kind, val = "roman", ROMAN[tok]
        else:
            continue
        if val == 1 or val == last[kind] + 1:
            last[kind] = val
        elif val > last[kind] + 1 and last[kind] > 0:
            before, last[kind] = last[kind], val
            name = next(k for k, v in ROMAN.items() if v == before) if kind == "roman" else chr(96 + before)
            yield kind, name, tok, at


def prove_numbering(filing: str, scope: str) -> int:
    n = 0
    for s in load_document(filing).sections:
        if not in_scope(str(s.number), scope):
            continue
        text = section_text(s)
        for kind, before, after, at in gaps(text):
            n += 1
            ctx = " ".join(text[max(0, at - 70): at + 40].split())
            print(f"[§{s.number}]  {kind:<6} ({before}) -> ({after})   ...{ctx}...")
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Show the evidence for a Tier 1 eval task.")
    p.add_argument("filing_id")
    p.add_argument("check", choices=["dangling", "numbering"])
    p.add_argument("--scope", default="whole_document")
    a = p.parse_args(argv)
    n = (prove_dangling if a.check == "dangling" else prove_numbering)(a.filing_id, a.scope)
    print(f"\n{n} candidate(s) · {a.check} · {a.filing_id} · scope {a.scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())