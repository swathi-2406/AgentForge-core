"""The Day 6 story in one script, before the orchestrator exists. 3 LLM calls, ~2 minutes
(the naive diff is the slow part).

    python -m scripts.try_retry

    naive step ─► critic: partial ─► retry: new step 1 ─► run it ─► critic: success
"""

from agentforge_core.critic import critique
from agentforge_core.executor import execute_step
from agentforge_core.plan import PlannedStep
from agentforge_core.retry import replace_step, revise_step

PAIR = {"original_id": "redwire_credit_original", "amendment_id": "redwire_credit_amend1"}
TASK = "Does the First Amendment contradict the original credit agreement?"

plan = [PlannedStep(step_id=1, tool_name="diff_filings", tool_args=PAIR,
                    expected_outcome="a list of differences between the amendment and the original")]
first = plan[0]

print("1. run naive step (about a minute)...")
obs = execute_step(first)
crit = critique(first, obs, task=TASK)
print(f"   {first.tool_name:24} -> critic {crit.verdict} ({crit.confidence:.2f})")

if crit.verdict == "success":
    print("   critic accepted the naive diff, so there is nothing to retry. Paste this output.")
    raise SystemExit(1)

print("2. ask retry for ONE replacement step...")
r = revise_step(TASK, list(PAIR.values()), plan, first, obs, crit)
plan = replace_step(plan, r.step)
print(f"   step {r.step.step_id}: {r.step.tool_name}({r.step.tool_args})")
print(f"   why: {r.why_different}")

print("3. run the replacement...")
obs2 = execute_step(r.step)
crit2 = critique(r.step, obs2, task=TASK)
print(f"   {r.step.tool_name:24} -> critic {crit2.verdict} ({crit2.confidence:.2f})")
print(f"   {crit2.reasoning}")

ok = r.step.step_id == first.step_id and r.step.tool_name != first.tool_name and crit2.verdict == "success"
print("\nSELF-CORRECTION WORKED" if ok else "\nNot there yet; paste this output.")