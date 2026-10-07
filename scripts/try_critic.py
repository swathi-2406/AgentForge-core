"""Run 3 real steps and send each to the real critic. Uses 3 LLM calls.

    python -m scripts.try_critic

What you want to see:
    1 diff_filings              partial or failure   (noise: rewording reported as differences)
    2 compare_amended_clauses   success
    3 extract_section_map       success
"""

from agentforge_core.critic import critique
from agentforge_core.executor import execute_step
from agentforge_core.plan import PlannedStep

PAIR = {"original_id": "redwire_credit_original", "amendment_id": "redwire_credit_amend1"}
AMEND_TASK = "Does the First Amendment contradict the original credit agreement?"

CASES = [
    (AMEND_TASK, "partial/failure",
     PlannedStep(step_id=1, tool_name="diff_filings", tool_args=PAIR,
                 expected_outcome="a list of differences between the amendment and the original")),
    (AMEND_TASK, "success",
     PlannedStep(step_id=1, tool_name="compare_amended_clauses", tool_args=PAIR,
                 expected_outcome="each amended clause compared with the original, with real changes listed")),
    ("Check this contract for cross-references to sections that don't exist.", "success",
     PlannedStep(step_id=1, tool_name="extract_section_map", tool_args={"filing_id": "tva_facility_lease"},
                 expected_outcome="a non-empty section map for tva_facility_lease")),
]

for n, (task, want, st) in enumerate(CASES, 1):
    obs = execute_step(st)
    c = critique(st, obs, task=task)
    got_ok = c.verdict in want.split("/")
    print(f"{'OK ' if got_ok else 'X  '} {n} {st.tool_name:24} want {want:16} got {c.verdict:8} "
          f"conf {c.confidence:.2f}  [{c.source}]")
    print(f"      {c.reasoning}")
    if c.call:
        print(f"      {c.call.get('served_model')} · {c.call.get('prompt_tokens')}+"
              f"{c.call.get('completion_tokens')} tokens · {c.call.get('latency_ms')} ms")
    print()