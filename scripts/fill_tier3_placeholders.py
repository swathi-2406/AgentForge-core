"""Fill the Tier 3 TODOs with PLACEHOLDER text, so the pipeline can be tested end to end.

    python scripts/fill_tier3_placeholders.py

Every file it touches gets this line, so the drafts are easy to find and rewrite later:
    notes: "DRAFT: task and naive_failure are Claude's placeholders. Rewrite in your own words."

Find them later with:
    Select-String -Path evals\\tasks\\tier3_amendment_contradiction\\*.yaml -Pattern "DRAFT:"

Only lines that still say TODO are replaced, so your own edits are never overwritten.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "evals" / "tasks" / "tier3_amendment_contradiction"
DRAFT = 'notes: "DRAFT: task and naive_failure are Claude\'s placeholders. Rewrite in your own words."'

TEXT = {
    "t3_rwa_covenant_7_11": (
        "Check whether the amended financial covenant in Section 7.11 contradicts itself or the original agreement.",
        "A plain diff reports every raised leverage ratio and the March 30 to March 31 table dates as "
        "contradictions, and misses that the lead-in still says March 30."),
    "t3_rwa_lenders_1_01": (
        "Check whether the amended definitions in Section 1.01 are consistent with the defined terms of the original agreement.",
        "A plain diff shows the Lenders definition as ordinary new text and misses that the original only "
        "defines Lender, so the clause (b) reference points at nothing."),
    "t3_rwa_notice_2_02": (
        "Check whether the amended notice provisions in Section 2.02(a) contradict the original agreement.",
        "A plain diff flags the new one-day carve-out and the added (3) numerals as contradictions, and "
        "misses that Eurocurrency Loans is not a defined term."),
    "t3_rwa_proceeds_6_15": (
        "Check whether the amended use-of-proceeds clause in Section 6.15(a) is consistent with the original agreement.",
        "A plain diff reports only the added clause for the new term loans and misses that the new outer "
        "(ii) collides with the inner (ii)."),
    "t3_rwa_whole": (
        "Review this amendment against the original credit agreement and report every place where the "
        "amended text contradicts itself or the original.",
        "A plain diff of the whole amendment produces a wall of intended changes, such as raised ratios and "
        "new loan terms, and buries the four real contradictions."),
    "t3_rwa_clean_2_06": (
        "Check whether the amended commitment reduction provisions in Section 2.06(b) contradict the original agreement.",
        "A plain diff reports the new clause (iii) for First Amendment Term Commitments as a contradiction, "
        "although it is an intended addition."),
    "t3_rwa_clean_2_07": (
        "Check whether the amended repayment schedule in Section 2.07(a) contradicts the original agreement.",
        "A plain diff reports the new amortization clause (B) and the relettered maturity clause (C) as "
        "contradictions, although both are intended."),
    "t3_mmlp_covenants_7_14": (
        "Check whether the amended financial covenants in Section 7.14 contradict the original credit agreement.",
        "A plain diff reports the lowered 2025 ratios as contradictions, and may treat the different "
        "quarter-boundary wording in 7.14(b) as one too."),
    "t3_mmlp_loan_documents_1_01": (
        "Check whether the restated definition of Loan Documents contradicts the original credit agreement.",
        "A plain diff reports the added reference to the First Amendment as a contradiction, although adding "
        "it is the whole purpose of the restatement."),
    "t3_mmlp_whole": (
        "Review this amendment against the original credit agreement and report any place where it "
        "contradicts the original.",
        "A plain diff reports every restated covenant and definition as a contradiction, although every "
        "change in this amendment is intended."),
}


def block(key: str, text: str) -> str:
    body = textwrap.fill(text, width=90, initial_indent="  ", subsequent_indent="  ")
    return f"{key}: >-\n{body}"


def main() -> None:
    for tid, (task, naive) in TEXT.items():
        path = DIR / f"{tid}.yaml"
        if not path.exists():
            print(f"  missing {path.name}: run make_tier3_skeletons.py first")
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        changed = False
        for i, line in enumerate(lines):
            if line.startswith("task: TODO"):
                lines[i], changed = block("task", task), True
            elif line.startswith("naive_failure: TODO"):
                lines[i], changed = block("naive_failure", naive), True
        if changed and not any(l.startswith("notes:") for l in lines):
            while lines and lines[-1] == "":
                lines.pop()
            lines += [DRAFT, ""]
        if changed:
            path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  {'filled' if changed else 'kept  '} {tid}")


if __name__ == "__main__":
    main()