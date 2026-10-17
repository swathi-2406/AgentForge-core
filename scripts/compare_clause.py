"""Day 8 task 1: show an amended clause next to the original text it changes.

    python scripts/compare_clause.py redwire_credit_amend1 "2.02(a)"
    python scripts/compare_clause.py martin_credit_amend1 "Loan Documents"   # a defined term
    python scripts/compare_clause.py --all --out docs/day8_compare.txt       # every target below

For each target it prints four blocks:
    ORIGINAL   the clause as the original agreement has it
    AMENDMENT  where the amendment mentions it, plus the new text that follows
    DIFF       word-level changes, [-removed-] {+added+}. This is the naive diff: expect noise.
    CHECKS     section numbers and defined terms in the new text that the original doesn't have

You read it and decide: contradiction, or intended change. The script decides nothing.
The original is found through the amendment's `amends:` link in manifest.yaml.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools.read_document import load_document, manifest_entry  # noqa: E402

# The clauses each amendment changes (ground_truth.md, "Hard-tier comparison targets").
# §7.11 is left out: RWA-1 was settled on Day 3.
TARGETS = {
    "redwire_credit_amend1": ["1.01", "2.01(a)(i)", "2.02(a)", "2.06(b)", "2.07(a)", "6.15(a)", "Lenders"],
    "martin_credit_amend1": ["Loan Documents", "First Amendment", "7.14(a)", "7.14(c)", "7.14(b)"],
}

ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"]
Q_OPEN, Q_CLOSE = "[\"“]", "[\"”]"
DEFINES = r"\s+(?:means|shall mean|has the meaning|shall have the meaning)"
NEXT_INSTRUCTION = re.compile(
    r"\n[^\n]{0,200}?\b(?:is|are|shall be)\s+(?:hereby\s+)?(?:further\s+)?"
    r"(?:amended|restated|deleted|replaced|added|modified)\b"
    r"|\nSection\s+\d+\.\s+[A-Z]")                # next numbered section of the amendment itself
WIDTH = 1800          # max characters printed per block


# ---------- small helpers ----------

def sec_text(s) -> str:
    return getattr(s, "text", None) or getattr(s, "body", "") or ""


def short(text: str, limit: int = WIDTH) -> str:
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + f" …[+{len(text) - limit} chars]"


def parse_target(target: str):
    """'2.01(a)(i)' -> ('2.01', ['a', 'i']); anything else is a defined term."""
    m = re.fullmatch(r"(\d+\.\d+)((?:\([a-zA-Z0-9]+\))*)", target.strip())
    if not m:
        return None, None
    return m.group(1), re.findall(r"\(([a-zA-Z0-9]+)\)", m.group(2))


def next_label(label: str, depth: int) -> str | None:
    if depth >= 1 and label in ROMAN:                 # (i) at depth 2 is roman, not the letter i
        i = ROMAN.index(label)
        return ROMAN[i + 1] if i + 1 < len(ROMAN) else None
    if label.isdigit():
        return str(int(label) + 1)
    if len(label) == 1 and label.isalpha():
        return chr(ord(label) + 1)
    return None


def narrow(text: str, path: list[str]) -> tuple[str, str]:
    """Cut a section down to (a), then (i) inside it. Returns (text, note)."""
    for depth, label in enumerate(path):
        start = None
        for pat in (rf"(?m)^\s*\({re.escape(label)}\)", rf"(?<![\w])\({re.escape(label)}\)\s+[A-Z]"):
            m = re.search(pat, text)                  # prefer a marker at line start
            if m:
                start = m.start()
                break
        if start is None:
            return text, f"could not find ({label}); showing the whole enclosing text"
        rest = text[start + 3:]
        nxt = next_label(label, depth)
        end = None
        if nxt:
            for pat in (rf"(?m)^\s*\({re.escape(nxt)}\)", rf"(?<![\w])\({re.escape(nxt)}\)\s+[A-Z]"):
                m = re.search(pat, rest)
                if m:
                    end = m.start()
                    break
        text = text[start:start + 3 + end] if end is not None else text[start:]
    return text, ""


def definition(text: str, term: str) -> str | None:
    m = re.search(Q_OPEN + re.escape(term) + Q_CLOSE + DEFINES, text)
    if not m:
        return None
    nxt = re.search(r"\n\s*" + Q_OPEN + r"[A-Z]", text[m.end():])
    return text[m.start(): m.end() + (nxt.start() if nxt else 1500)]


def defined_terms(text: str) -> set[str]:
    return set(re.findall(Q_OPEN + r"([A-Z][^\"”“\n]{0,60})" + Q_CLOSE + DEFINES, text))


def word_diff(old: str, new: str) -> str:
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            seg = a[i1:i2]
            out.append(" ".join(seg) if len(seg) <= 12 else " ".join(seg[:5]) + " … " + " ".join(seg[-5:]))
        if op in ("replace", "delete"):
            out.append("[-" + " ".join(a[i1:i2]) + "-]")
        if op in ("replace", "insert"):
            out.append("{+" + " ".join(b[j1:j2]) + "+}")
    return short(" ".join(out), WIDTH)


# ---------- the two sides ----------

def original_side(orig, target: str) -> tuple[str, str]:
    section, path = parse_target(target)
    if section is None:                               # defined term
        d = definition(orig.text, target)
        if d:
            return d, ""
        close = difflib.get_close_matches(target, sorted(defined_terms(orig.text)), n=3, cutoff=0.75)
        return "", f"“{target}” is not defined in the original. Closest defined terms: {close or 'none'}"
    hits = [s for s in orig.sections if s.number == section]
    if not hits:
        return "", f"No section {section} in the original"
    note = f"{len(hits)} sections numbered {section} (wrapper + annex?); using the longest" if len(hits) > 1 else ""
    text = sec_text(max(hits, key=lambda s: len(sec_text(s))))
    if not path:
        return text, note or f"whole section {section}; try a defined term to narrow it"
    text, n = narrow(text, path)
    return text, "; ".join(x for x in (note, n) if x)


def amendment_hits(amend, target: str, orig_len: int) -> list[tuple[str, str]]:
    """(context before, new text after) for each place the amendment mentions the target."""
    section, path = parse_target(target)
    if section is None:
        pat = Q_OPEN + re.escape(target) + Q_CLOSE
    else:
        pat = r"Section\s*" + re.escape(section) + "".join(rf"\s*\({re.escape(p)}\)" for p in path)
        pat += r"(?![\d.])"
    redefined = section is None and re.search(pat + DEFINES, amend.text)
    if redefined:
        pat += r"(?=" + DEFINES + ")"               # prefer the place the term is (re)defined
    hits, last_end = [], -1
    for m in re.finditer(pat, amend.text):
        if m.start() < last_end:                      # inside the window we already showed
            continue
        if redefined:                                 # the definition itself is the new text
            f, new = None, amend.text[m.start(): m.start() + 4000]
        elif section is None:                         # a term that's only used or defined inline: show its sentence
            f, new = None, amend.text[max(0, m.start() - 300): m.end() + 300]
        else:
            after = amend.text[m.end(): m.end() + 4000]
            f = re.search(r"as follows\s*:", after[:500])
            new = after[f.end():] if f else after
        stop = NEXT_INSTRUCTION.search(new, 20) if (section or redefined) else None       # stop where the amendment's next instruction starts
        if stop:
            new = new[: stop.start()]
        if redefined:                                 # one definition ends where the next one starts
            nd = re.search(r"\n\s*" + Q_OPEN + r"[A-Z]", new[5:])
            new = new[: nd.start() + 5] if nd else new
        new = new[: min(len(new), int(orig_len * 1.3) + 300, 3000)]
        hits.append((amend.text[max(0, m.start() - 200): m.end()], new))
        last_end = m.end() + len(new)
    return hits[:3]


def checks(orig, new_text: str) -> list[str]:
    out = []
    numbers = {s.number for s in orig.sections}
    for ref in sorted(set(re.findall(r"Sections?\s+(\d+\.\d+)", new_text))):
        if ref not in numbers:
            out.append(f"cites Section {ref}, which is not in the original's section map")
    known = defined_terms(orig.text)
    for term in sorted(set(re.findall(Q_OPEN + r"([A-Z][^\"”“\n]{0,60})" + Q_CLOSE, new_text))):
        if term not in known:
            close = difflib.get_close_matches(term, sorted(known), n=2, cutoff=0.75)
            out.append(f"uses “{term}”, not defined in the original" + (f" (closest: {close})" if close else ""))
    return out or ["nothing missing from the original"]


# ---------- output ----------

def compare(amend_id: str, target: str) -> str:
    orig_id = manifest_entry(amend_id).get("amends")
    if not orig_id:
        return f"{amend_id} has no `amends:` link in manifest.yaml"
    orig, amend = load_document(orig_id), load_document(amend_id)
    o_text, o_note = original_side(orig, target)
    hits = amendment_hits(amend, target, len(o_text) or 800)

    bar = "─" * 70
    lines = [bar, f"  {target}   {amend_id}  vs  {orig_id}", bar,
             "ORIGINAL" + (f"   ({o_note})" if o_note else ""), "  " + (short(o_text) or "—"), ""]
    if not hits:
        lines += ["AMENDMENT", "  not mentioned in the amendment. If you expected it, search by hand.", ""]
    for n, (before, new) in enumerate(hits, 1):
        lines += [f"AMENDMENT  hit {n}/{len(hits)}", "  …" + short(before, 400),
                  "  NEW TEXT → " + short(new), ""]
        if o_text:
            lines += ["DIFF", "  " + word_diff(o_text, new), ""]
        lines += ["CHECKS"] + ["  • " + c for c in checks(orig, new)] + [""]
    lines += ["YOUR CALL  contradiction / intended change / not amended", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("amendment_id", nargs="?")
    ap.add_argument("target", nargs="?", help='a clause like "2.02(a)" or a defined term like "Loan Documents"')
    ap.add_argument("--all", action="store_true", help="every target in TARGETS")
    ap.add_argument("--out", help="write to this file (UTF-8) instead of the screen")
    a = ap.parse_args()

    if a.all:
        jobs = [(f, t) for f, ts in TARGETS.items() for t in ts]
    elif a.amendment_id and a.target:
        jobs = [(a.amendment_id, a.target)]
    else:
        ap.error('give an amendment id and a target, or --all')

    blocks = []
    for f, t in jobs:
        try:
            blocks.append(compare(f, t))
        except Exception as e:                        # one bad target shouldn't hide the rest
            blocks.append(f"── {t} ({f}): ERROR {type(e).__name__}: {e}\n")
    report = "\n".join(blocks)

    if a.out:
        Path(a.out).write_text(report, encoding="utf-8")
        print(f"Wrote {len(jobs)} comparisons to {a.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")      # Windows consoles default to cp1252
        print(report)


if __name__ == "__main__":
    main()