# """Day 3 task 4: show the evidence for every ground-truth row on one screen.

#     python scripts/verify_ground_truth.py            # all rows
#     python scripts/verify_ground_truth.py TVA-1      # one row

# For each finding it prints the claim and the matching text from the filing.
# You read it and decide: verified, or delete the row. The script decides nothing.
# """

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentforge_core.tools.read_document import load_document  # noqa: E402

# id: (filing_id, claim, [(section or None for whole filing, regex), ...])
FINDINGS = {
    "TVA-1": ("tva_facility_lease", "15.1 cites 13.2(d); the buy-out election is 13.2(e)",
              [("15.1", r"13\.2\(d\)"), ("13.2", r"\(d\)"), ("13.2", r"\(e\)")]),
    "TVA-2": ("tva_facility_lease", "4.1(a) roman list jumps from (iv) to (vi)",
              [("4.1", r"\(iv\)"), ("4.1", r"\(v\)"), ("4.1", r"\(vi\)")]),
    "TVA-3": ("tva_facility_lease", '"Supplement Lease Rent" is not the defined term',
              [("13.2", r"Supplement Lease Rent"), (None, r"Supplemental Lease Rent")]),
    "TVA-4": ("tva_facility_lease", '"Indenture Trustee" is not the defined term',
              [("3.3", r"Indenture Trustee"), (None, r"[\"“]\w*\s*Indenture Trustee[\"”]")]),
    "TVA-5": ("tva_facility_lease", '"Discount Value" near-miss: where is it, and what is the real term?',
              [(None, r"Discount(ed)? Value")]),
    "FORD-1": ("ford_arr_2026b", "3.8(b) says Tests are in Schedule A; they're in Schedule B",
               [("3.8", r"Schedule [AB]"), (None, r"Schedule B")]),
    "FORD-2": ("ford_arr_2026b", '"Review Receivable" where "Review Lease" is meant',
               [("3.3", r"Review Receivables?"), (None, r"Review Leases?")]),
    "FORD-3": ("ford_arr_2026b", '"Contract" points at Schedule A, which never defines it',
               [(None, r"[\"“]Contracts?[\"”]"), ("Schedule A", r"Contracts?")]),
    "RWO-1": ("redwire_credit_original", '1.01 cites "Section 1.1", which does not exist',
              [("1.01", r"Section 1\.1\b(?!\d)"), (None, r"Pro Forma Calculations|Pro Forma Basis[\"”]")]),
    "RWO-2": ("redwire_credit_original", "Investment / Investments Basket used for the same term",
              [(None, r"Unrestricted Subsidiary Investments? Basket")]),
    "RWO-3": ("redwire_credit_original", '"Securitization Repurchasing Obligations" vs the defined term',
              [(None, r"Securitization Repurchas\w+ Obligations?")]),
    "RWA-2": ("redwire_credit_amend1", '"Amended Credit Agreement" is used but never defined',
              [(None, r"Amended Credit Agreement")]),
    "RWA-3": ("redwire_credit_amend1", "1.03(a) roman list jumps from (iv) to (vi)",
              [("1.03", r"\(iv\)"), ("1.03", r"\(v\)"), ("1.03", r"\(vi\)")]),
}

MAX_HITS = 3
WIDTH = 110


def section_texts(doc, number: str | None) -> tuple[list[tuple[str, str]], str]:
    """(section label, text) pairs to search, and a label saying where they came from."""
    every = [(f"§{s.number}" if s.number else s.kind, s.text) for s in doc.sections]
    if number is None:
        return every, "whole filing"
    for s in doc.sections:
        if str(s.number).lower() == number.lower():
            return [(f"§{number}", s.text)], f"§{number}"
    return every, f"§{number} NOT FOUND, searched whole filing"


def show(fid: str, claim: str, checks: list, cache: dict) -> None:
    doc = cache.setdefault(fid, load_document(fid))
    print(f"  claim: {claim}")
    for number, pattern in checks:
        parts, where = section_texts(doc, number)
        hits = [(label, text, m) for label, text in parts for m in re.finditer(pattern, text)]
        print(f"\n  /{pattern}/  in {where}: {len(hits)} hit(s)")
        for label, text, m in hits[:MAX_HITS]:
            a, b = max(0, m.start() - WIDTH // 2), min(len(text), m.end() + WIDTH // 2)
            marked = text[a:m.start()] + ">>" + m.group(0) + "<<" + text[m.end():b]
            tag = f"[{label}] " if len(parts) > 1 else ""
            print("    " + tag + "…" + " ".join(marked.split()) + "…")
        if len(hits) > MAX_HITS:
            print(f"    (+{len(hits) - MAX_HITS} more)")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    wanted = [a.upper() for a in sys.argv[1:]] or list(FINDINGS)
    unknown = [w for w in wanted if w not in FINDINGS]
    if unknown:
        print(f"Unknown ids: {unknown}. Known: {', '.join(FINDINGS)}")
        return 1
    cache: dict = {}
    for fid_key in wanted:
        fid, claim, checks = FINDINGS[fid_key]
        print(f"\n{'─' * 4} {fid_key}  {fid} {'─' * max(4, 60 - len(fid_key) - len(fid))}")
        show(fid, claim, checks, cache)
        print("\n  → verified / delete / fix the row?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())