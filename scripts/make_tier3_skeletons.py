"""Day 8 task 3: write the Tier 3 task skeletons. Run once from the repo root:

    python scripts/make_tier3_skeletons.py

The `expected:` block of each file is copied from your verified ground_truth.md rows.
The two parts that need your words are left as TODO:
    task:           the question, the way a lawyer would ask it
    naive_failure:  what a plain text diff would wrongly report here (one sentence)

The loader rejects a TODO, so until you fill them in, this works as your to-do list:
    python -c "from evals.harness.task_format import load_all; print(len(load_all()), 'tasks load cleanly')"

Existing files are never overwritten. It also adds a Tier 3 section to evals/tasks/PLAN.md.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "tasks" / "tier3_amendment_contradiction"
PLAN = ROOT / "evals" / "tasks" / "PLAN.md"

RA, RO = "redwire_credit_amend1", "redwire_credit_original"
MA, MO = "martin_credit_amend1", "martin_credit_original"

# Traps reused by several tasks
RATIOS_7_11 = {"location": "7.11", "filing": RA, "mentions": ["6.00", "5.00", "4.00"],
               "reason": "The leverage ratios were raised on purpose. Intended change, not a contradiction."}
CARVE_OUT = {"location": "2.02", "filing": RA, "mentions": ["one (1) Business Day"],
             "reason": "The one-day notice carve-out (y) is stated openly by the amendment."}
MARTIN_RELIEF = {"location": "7.14", "filing": MA, "mentions": ["1.75", "1.25"],
                 "reason": "Deliberate covenant relief for Q1-Q3 2025. Intended change."}
MARTIN_UNTIL = {"location": "7.14", "filing": MO, "mentions": ["until"],
                "reason": "'until' in 7.14(b) vs 'through and including' in (a)/(c): same boundary, different words."}

# One entry per task: (id, filings, scope, must_flag, must_not_flag, refs, optimal_steps, max_extra, plan note)
TASKS = [
    # ---- real contradictions (Redwire) ----
    ("t3_rwa_covenant_7_11", [RA, RO], "7.11",
     [{"type": "date_inconsistency", "location": ["7.11", "Article II"], "filing": RA}],
     [RATIOS_7_11], ["RWA-1"], 5, 0, "RWA-1 · lead-in March 30 vs table March 31"),
    ("t3_rwa_lenders_1_01", [RA, RO], "1.01",
     [{"type": ["term_mismatch", "dangling_reference"], "location": "1.01", "filing": RA}],
     [], ["RWA-4"], 5, 0, "RWA-4 · semantic equivalence: restates 'Lenders', original defines 'Lender'"),
    ("t3_rwa_notice_2_02", [RA, RO], "2.02",
     [{"type": "undefined_term", "location": "2.02", "filing": RA}],
     [CARVE_OUT], ["RWA-5"], 5, 0, "RWA-5 · 'Eurocurrency Loans' undefined · trap: carve-out"),
    ("t3_rwa_proceeds_6_15", [RA, RO], "6.15",
     [{"type": "number_mismatch", "location": "6.15", "filing": RA}],
     [], ["RWA-6"], 4, 0, "RWA-6 · two different (ii)"),
    ("t3_rwa_whole", [RA, RO], "whole_document",
     [{"type": "date_inconsistency", "location": ["7.11", "Article II"], "filing": RA},
      {"type": ["term_mismatch", "dangling_reference"], "location": "1.01", "filing": RA},
      {"type": "undefined_term", "location": "2.02", "filing": RA},
      {"type": "number_mismatch", "location": "6.15", "filing": RA}],
     [RATIOS_7_11, CARVE_OUT], ["RWA-1", "RWA-4", "RWA-5", "RWA-6"], 10, 1,
     "headline task · all 4 contradictions · traps: ratios, carve-out"),
    # ---- clean negatives (Redwire) ----
    ("t3_rwa_clean_2_06", [RA, RO], "2.06",
     [], [], [], 4, 0, "clean · adds (iii) for First Amendment commitments"),
    ("t3_rwa_clean_2_07", [RA, RO], "2.07",
     [], [], [], 4, 0, "clean · adds (B) amortization, (C) maturity"),
    # ---- clean negatives (Martin) ----
    ("t3_mmlp_covenants_7_14", [MA, MO], "7.14",
     [], [MARTIN_RELIEF, MARTIN_UNTIL], [], 4, 0, "clean · traps: ratio relief, 'until' wording"),
    ("t3_mmlp_loan_documents_1_01", [MA, MO], "1.01",
     [], [], [], 4, 0, "clean · only adds 'the First Amendment'"),
    ("t3_mmlp_whole", [MA, MO], "whole_document",
     [], [MARTIN_RELIEF, MARTIN_UNTIL], [], 6, 0, "clean · whole amendment · same traps"),
]


class _Flow(list):
    """A list YAML writes on one line: [a, b]."""


yaml.SafeDumper.add_representer(_Flow, lambda d, v: d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=True))


def flow(x):
    if isinstance(x, list):
        return _Flow(flow(i) for i in x) if all(isinstance(i, str) for i in x) else [flow(i) for i in x]
    if isinstance(x, dict):
        return {k: flow(v) for k, v in x.items()}
    return x


def body(tid, filings, scope, must, traps, refs, steps, extra) -> str:
    head = (f"id: {tid}\ntier: 3\nfilings: [{filings[0]}, {filings[1]}]   # [amendment, original]\n"
            f"task: TODO            # your question, the way a lawyer would ask it\n"
            f"scope: '{scope}'\n")
    expected = yaml.safe_dump({"expected": flow({"must_flag": must, "must_not_flag": traps,
                                                 "max_extra_findings": extra})},
                              sort_keys=False, allow_unicode=True, width=100)
    tail = (f"ground_truth_ref: [{', '.join(refs)}]\noptimal_steps: {steps}\n"
            f"naive_failure: TODO   # one sentence: what a plain diff would wrongly report here\n")
    return head + expected + tail


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for tid, filings, scope, must, traps, refs, steps, extra, _ in TASKS:
        path = OUT / f"{tid}.yaml"
        if path.exists():
            print(f"  skip  {tid} (exists)")
            continue
        path.write_text(body(tid, filings, scope, must, traps, refs, steps, extra), encoding="utf-8")
        print(f"  wrote {tid}")

    text = PLAN.read_text(encoding="utf-8")
    if "t3_rwa_whole" not in text:
        rows = ["", "## Tier 3", "", "| # | id | Filing | Kind | Rows | Scope | Expect | Status |",
                "|---|---|---|---|---|---|---|---|"]
        for n, (tid, filings, scope, must, traps, refs, *_ , note) in enumerate(TASKS, start=27):
            kind = "real" if must else "clean"
            who = "RWA" if filings[0] == RA else "MMLP"
            rows.append(f"| {n} | {tid} | {who} | {kind} | {', '.join(refs) or '-'} | {scope} | {note} | planned |")
        PLAN.write_text(text.rstrip("\n") + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
        print("  added Tier 3 section to PLAN.md")


if __name__ == "__main__":
    main()