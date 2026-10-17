"""Day 8 task 1: write the settled hard-tier calls into docs/ground_truth.md.

    python scripts/apply_day8_task1.py --rwa6 finding     # §6.15(a) is a real numbering finding
    python scripts/apply_day8_task1.py --rwa6 style       # §6.15(a) is drafting style

Safe to run more than once: a row whose ID (or "What" text) is already there is
replaced, not duplicated. A backup is written to docs/ground_truth.md.bak first.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

GT = Path(__file__).resolve().parents[1] / "docs" / "ground_truth.md"

ORIGINAL_ROWS = [
    "| RWO-5 | §2.01(a)(ii) | wrong_reference | Calls itself \"Section 2.01(b)\" twice; the clause is 2.01(a)(ii). "
    "§2.01(b) exists (Revolving Credit Borrowings), so an existence check won't catch it. | 2 | verified |",
]

AMEND_ROWS = [
    "| RWA-4 | §1.01 (amendment §(a)(ii)) | defined_term | Restates \"Lenders\"; the original's term is \"Lender\". "
    "Read literally, \"First Amendment Term Lender\" cites clause (b) of a \"Lender\" definition that has no clause (b). "
    "Scorer accepts defined_term or dangling_reference here. | 3 | verified |",
    "| RWA-5 | §2.02(a), clause (II) | defined_term | New sentence says \"Eurocurrency Loans\"; the original defines only "
    "\"Eurocurrency Rate Loan\" and \"Eurocurrency Rate\". | 3 | verified |",
]
RWA6_ROW = (
    "| RWA-6 | §6.15(a) | list_numbering | Restated text wraps an inner (i)(ii)(iii) in a new outer (i)/(ii), "
    "so \"(ii)\" means two different things. Any reference to clause (ii) of 6.15(a) is ambiguous. | 3 | verified |"
)

NOT_FINDINGS = [
    "| redwire_credit_amend1 | §2.02(a) one-day notice carve-out (y) | Stated openly by the amendment: intended. |",
    "| redwire_credit_amend1 | §2.02(a) \"three (3) Business Day\" (singular) | Inherited from the original; grammar, not a contradiction. |",
    "| redwire_credit_amend1 | §2.02(a) mention in amendment §3.02 | A condition that cites the clause, not a change to it. |",
    "| redwire_credit_amend1 | §2.01(a)(i) no-reborrow narrowed to (a)(i) | (a)(ii) keeps its own no-reborrow sentence. |",
    "| martin_credit_amend1 | §7.14(a), §7.14(c) ratio changes | Deliberate covenant relief. A naive diff will flag these. |",
    "| martin_credit_original | §7.14(b) \"until\" vs (a)/(c) \"through and including\" | Different wording for the same "
    "quarter boundary. Not a contradiction: keep it as a must-not-flag trap. |",
]
RWA6_STYLE_ROW = ("| redwire_credit_amend1 | §6.15(a) nested (i)/(ii) inside (i)/(ii) | Drafting style; nothing in "
                  "either agreement cites a clause of 6.15(a). |")


def comparison_section(rwa6: str) -> str:
    call_615 = "contradiction | RWA-6" if rwa6 == "finding" else "intended | Nested (i)/(ii) is drafting style (see Not findings)."
    return f"""## Hard-tier comparison targets

Clause-by-clause calls from `scripts/compare_clause.py --all` (Day 8).

### redwire_credit_amend1 vs redwire_credit_original

| Amended clause | Call | Notes |
|---|---|---|
| §1.01 new definitions (DSS, First Amendment …) | intended | New terms for the new loans. |
| §1.01 "Lenders" | contradiction | RWA-4 |
| §2.01(a)(i) | intended | Adds First Amendment Term Loans. The no-reborrow rule narrows to (a)(i), but (a)(ii) has its own. |
| §2.02(a) | contradiction | RWA-5. The one-day carve-out (y) is intended. |
| §2.06(b) | intended | Adds (iii) for First Amendment Term Commitments. |
| §2.07(a) | intended | Adds (B) amortization and (C) maturity for First Amendment Term Loans. |
| §6.15(a) | {call_615} |
| §7.11 | contradiction | RWA-1 (settled Day 3) |

### martin_credit_amend1 vs martin_credit_original

| Amended clause | Call | Notes |
|---|---|---|
| §1.01 "Loan Documents" | intended | Only adds "the First Amendment". |
| §1.01 "First Amendment" (new) | intended | New term. |
| §7.14(a) | intended | Interest coverage 2.00 → 1.75 for Q1–Q3 2025, then back to 2.00. |
| §7.14(c) | intended | First lien leverage 1.50 → 1.25 for Q1–Q3 2025, then back to 1.50. |
| §7.14(b), not amended | control | Untouched. See Not findings. |

Martin has no contradictions. Its tasks are clean negatives that test for false positives.

"""


def table_bounds(lines: list[str], heading_prefix: str) -> tuple[int, int]:
    """(first row index, index after last row) of the first table under a '## ' heading."""
    h = next((i for i, l in enumerate(lines) if l.startswith(heading_prefix)), None)
    if h is None:
        raise SystemExit(f"Heading not found: {heading_prefix!r}")
    start = next(i for i in range(h + 1, len(lines)) if lines[i].startswith("|"))
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return start, end


def upsert_rows(lines: list[str], heading_prefix: str, rows: list[str], key=lambda r: r.split("|")[1].strip()) -> list[str]:
    for row in rows:
        start, end = table_bounds(lines, heading_prefix)
        k = key(row)
        hit = next((i for i in range(start, end) if key(lines[i]) == k), None)
        if hit is not None:
            lines[hit] = row
        else:
            lines.insert(end, row)
    return lines


def drop_rows(lines: list[str], heading_prefix: str, keys: set[str], key) -> list[str]:
    start, end = table_bounds(lines, heading_prefix)
    return lines[:start] + [l for l in lines[start:end] if key(l) not in keys] + lines[end:]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rwa6", choices=["finding", "style"], required=True, help="your call on §6.15(a)")
    a = ap.parse_args()

    shutil.copy(GT, GT.with_suffix(".md.bak"))
    text = GT.read_text(encoding="utf-8")

    # 1) replace the whole comparison section, up to the next '## ' heading
    m = re.search(r"^## Hard-tier comparison targets\n.*?(?=^## )", text, re.S | re.M)
    if not m:
        raise SystemExit("Section '## Hard-tier comparison targets' not found")
    text = text[: m.start()] + comparison_section(a.rwa6) + text[m.end():]

    lines = text.split("\n")
    what = lambda r: r.split("|")[2].strip() if r.count("|") > 3 else ""

    # 2) finding rows
    lines = upsert_rows(lines, "## redwire_credit_original", ORIGINAL_ROWS)
    amend = AMEND_ROWS + ([RWA6_ROW] if a.rwa6 == "finding" else [])
    lines = upsert_rows(lines, "## redwire_credit_amend1", amend)
    if a.rwa6 == "style":
        lines = drop_rows(lines, "## redwire_credit_amend1", {"RWA-6"}, key=lambda r: r.split("|")[1].strip())

    # 3) not-findings rows (matched on the "What" column)
    nf = NOT_FINDINGS + ([RWA6_STYLE_ROW] if a.rwa6 == "style" else [])
    lines = upsert_rows(lines, "## Not findings", nf, key=what)
    if a.rwa6 == "finding":
        lines = drop_rows(lines, "## Not findings", {what(RWA6_STYLE_ROW)}, key=what)

    GT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Updated {GT.name} (backup: {GT.name}.bak). §6.15(a) recorded as: {a.rwa6}")


if __name__ == "__main__":
    main()