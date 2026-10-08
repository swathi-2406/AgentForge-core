"""Day 6 checkpoint: the hard-tier amendment task through the real orchestrator.

    python -m scripts.day6_checkpoint          ~2 minutes, about 4 LLM calls

The plan is seeded on purpose: step 1 is the naive whole-document diff, so the run has
something to self-correct. Step 2 is an unrelated step that must come through untouched.

    step 1  diff_filings (naive)  ──► critic partial/failure ──► retry ──► compare_amended_clauses ──► success
    step 2  extract_section_map   ──► success, unchanged

Then it checks the playbook's definition of done, prints ✓/✗ per check, and writes
docs/checkpoints/day6_checkpoint.md for the Sprint 6 walkthrough.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agentforge_core.orchestrator import run_self_correcting
from agentforge_core.plan import PlannedStep
from agentforge_core.tracing import trace_writer

ORIG, AMEND = "redwire_credit_original", "redwire_credit_amend1"
TASK = "Does the First Amendment contradict the original Redwire credit agreement?"
OUT = Path("docs/checkpoints/day6_checkpoint.md")

PLAN = [
    PlannedStep(step_id=1, tool_name="diff_filings",
                tool_args={"original_id": ORIG, "amendment_id": AMEND},
                expected_outcome="a list of differences between the amendment and the original"),
    PlannedStep(step_id=2, tool_name="extract_section_map", tool_args={"filing_id": ORIG},
                expected_outcome="a non-empty section map of the original credit agreement"),
]


def main() -> None:
    print("Running the checkpoint (about 2 minutes, the naive diff is the slow part)...\n")
    r = run_self_correcting(TASK, [ORIG, AMEND], PLAN)
    events = trace_writer.read_events(r.run_id)
    crits = [e for e in events if e["kind"] == "critique"]
    retries = [e for e in events if e["kind"] == "retry"]
    findings = [e for e in events if e["kind"] == "finding"]

    # ---- timeline
    lines = []
    for e in events:
        if e["kind"] == "critique":
            lines.append(f"step {e['step_id']} try {e['attempt']}  {e['tool_name']:24} -> {e['verdict']:8} "
                         f"({e['confidence']:.2f})")
            lines.append(f"    critic: {e['reasoning']}")
        elif e["kind"] == "retry":
            lines.append(f"step {e['step_id']} RETRY  {e['from_tool']} -> {e['to_tool']}")
            lines.append(f"    why: {e['why_different']}")
        elif e["kind"] == "finding":
            lines.append(f"step {e['step_id']} FINDING  §{e['location']}  {e['type']}")
        elif e["kind"] in ("give_up", "finding_rejected"):
            lines.append(f"{e['kind'].upper()}: {e.get('reason') or e.get('error')}")

    # ---- definition of done
    first = next((c for c in crits if c["step_id"] == 1 and c["attempt"] == 1), {})
    last1 = [c for c in crits if c["step_id"] == 1][-1:] or [{}]
    step2 = next((s for s in r.final_plan if s.step_id == 2), None)
    locs = {f["location"] for f in findings}
    checks = [
        ("naive diff rejected by the critic", first.get("verdict") in ("partial", "failure"),
         f"verdict: {first.get('verdict')}"),
        ("exactly one retry, on step 1 only", len(retries) == 1 and retries[0]["step_id"] == 1,
         f"{len(retries)} retries"),
        ("retry changed strategy", bool(retries) and retries[0]["to_tool"] != "diff_filings",
         f"-> {retries[0]['to_tool'] if retries else '-'}"),
        ("retried step succeeded", last1[0].get("verdict") == "success", f"verdict: {last1[0].get('verdict')}"),
        ("step 2 untouched", step2 is not None and step2 == PLAN[1], step2.tool_name if step2 else "missing"),
        ("task completed", r.status == "completed", r.status + (f" ({r.stop_reason})" if r.stop_reason else "")),
        ("found the §7.11 date mismatch (RWA-1)", "7.11" in locs, ", ".join(sorted(locs)) or "no findings"),
        ("found the §1.01 Lenders mismatch (RWA-4)", "1.01" in locs, ""),
    ]
    passed = all(ok for _, ok, _ in checks)

    print("\n".join(lines))
    print(f"\nrun {r.run_id}  [{r.status}]  retries={r.retries}  findings={len(r.findings)}\n")
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'}  {name:44} {detail}")
    print("\nCHECKPOINT PASSED" if passed else "\nCHECKPOINT NOT PASSED, paste this output")

    # ---- saved log
    OUT.parent.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    OUT.write_text(
        f"# Day 6 checkpoint: self-correction on the Redwire amendment\n\n"
        f"- When: {when}\n- Run: `{r.run_id}` ({r.status})\n- Trace: `{trace_writer.run_json_path(r.run_id)}`\n"
        f"- Task: {TASK}\n- Result: {'PASSED' if passed else 'NOT PASSED'}\n\n"
        f"## Timeline\n\n```\n" + "\n".join(lines) + "\n```\n\n## Definition of done\n\n"
        + "\n".join(f"- [{'x' if ok else ' '}] {name} {('— ' + detail) if detail else ''}" for name, ok, detail in checks)
        + "\n", encoding="utf-8")
    print(f"saved: {OUT}")


if __name__ == "__main__":
    main()