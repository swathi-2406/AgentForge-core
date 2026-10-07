"""Day 5 task 2: run a few real steps and confirm each lands in the right outcome.

    python -m scripts.check_outcomes

No LLM calls. Each row shows what we expected and what the executor said.
"""

from agentforge_core.executor import execute_step
from agentforge_core.plan import PlannedStep

TVA, FORD = "tva_facility_lease", "ford_arr_2026b"

CASES = [
    # (expected, tool, args)
    ("ok", "extract_section_map", {"filing_id": TVA}),
    ("ok", "extract_definitions", {"filing_id": FORD}),
    ("ok", "extract_cross_references", {"filing_id": TVA, "only_problems": True}),
    ("empty", "read_document", {"filing_id": TVA, "section": "99.9"}),
    ("empty", "extract_definitions", {"filing_id": FORD, "term": "Imaginary Widget Fee"}),
    ("error/bad_args", "extract_dates", {"filing": TVA}),
    ("error/bad_args", "diff_everything", {"filing_id": TVA}),
    ("error/tool_failed", "read_document", {"filing_id": "not_downloaded_yet"}),
]


def main() -> None:
    bad = 0
    print(f"{'expected':<18} {'got':<18} {'ms':>8}  tool / detail")
    for i, (want, tool, args) in enumerate(CASES, 1):
        step = PlannedStep.model_construct(step_id=i, tool_name=tool, tool_args=args,
                                           expected_outcome="checked by hand in this script")
        obs = execute_step(step)
        got = obs.status if obs.status != "error" else f"error/{obs.error_kind}"
        detail = obs.empty_reason or (f"{obs.error_type}: {obs.error.splitlines()[0][:60]}" if obs.error else "")
        mark = "  " if got == want else "X "
        bad += got != want
        print(f"{mark}{want:<16} {got:<18} {obs.latency_ms:>8.1f}  {tool}  {detail}")
    print("\nAll outcomes as expected." if not bad else f"\n{bad} unexpected outcome(s), marked X.")


if __name__ == "__main__":
    main()