"""Find text in a parsed filing and show which section each hit sits in.

    python scripts/find_text.py redwire_credit_original "Admaicntistrative"
    python scripts/find_text.py tva_facility_lease "[a-z]shall\\b" --regex
    python scripts/find_text.py redwire_credit_original "Section \\d" --regex --by-section

Searches the clean text read_document produces, which is what the agent sees.
The raw EDGAR HTML is no good for this: each file is basically one giant line.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentforge_core.tools.read_document import load_document  # noqa: E402

TEXT_FIELDS = ("text", "body", "content")


def section_text(section) -> str:
    for name in TEXT_FIELDS:
        value = getattr(section, name, None)
        if isinstance(value, str):
            return value
    fields = sorted(getattr(section, "model_fields", None) or vars(section))
    raise SystemExit(f"Can't find a section's text. Its fields are {fields}; add the right one to TEXT_FIELDS.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Find text in a parsed filing, tagged by section.")
    p.add_argument("filing_id")
    p.add_argument("pattern")
    p.add_argument("--regex", action="store_true", help="treat pattern as a regular expression")
    p.add_argument("--max", type=int, default=10, help="hits to print (default 10)")
    p.add_argument("--width", type=int, default=60, help="characters of context each side")
    p.add_argument("--by-section", action="store_true", help="only count hits per section, busiest first")
    a = p.parse_args(argv)

    rx = re.compile(a.pattern if a.regex else re.escape(a.pattern))
    sections = load_document(a.filing_id).sections
    if a.by_section:
        counts = [(len(rx.findall(section_text(s))), s.number) for s in sections]
        counts = sorted((c for c in counts if c[0]), key=lambda c: -c[0])
        for n, number in counts[:a.max]:
            print(f"{n:5}  §{number}")
        print(f"\n{sum(n for n, _ in counts)} hit(s) in {len(counts)} section(s) of {a.filing_id}")
        return 0

    hits = 0
    for s in sections:
        text = section_text(s)
        for m in rx.finditer(text):
            hits += 1
            if hits <= a.max:
                ctx = " ".join(text[max(0, m.start() - a.width): m.end() + a.width].split())
                print(f"[§{s.number}]  ...{ctx}...")
    more = f", showing the first {a.max}" if hits > a.max else ""
    print(f"\n{hits} hit(s) in {a.filing_id}{more}")
    return 0


if __name__ == "__main__":
    sys.exit(main())