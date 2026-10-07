"""Day 5 definition of done: a real Day 4 plan runs end to end on a real filing.

    planner output (saved plan) ──► orchestrator.run_plan ──► executor ──► real tools ──► trace

No LLM call: the plan is a fixture copied from data/plans/, so this test is free and repeatable.
Traces go to a tmp folder (tests/conftest.py), never to your real data/traces.
"""

from pathlib import Path

import pytest

from agentforge_core.orchestrator import load_plan, run_plan
from agentforge_core.tools import ToolError
from agentforge_core.tracing import trace_writer as tw
from agentforge_core.tracing.tool_calls import read_tool_calls

FIXTURE = Path(__file__).parent / "fixtures" / "tva_plan.json"


@pytest.fixture(scope="module")
def saved_plan():
    if not FIXTURE.exists():
        pytest.fail(f"Missing {FIXTURE}. Copy one saved TVA plan from data/plans/ there (Day 5 task 4).")
    plan = load_plan(FIXTURE)
    from agentforge_core.tools.read_document import resolve_filing_path
    for fid in plan.filing_ids:
        try:
            resolve_filing_path(fid)
        except ToolError:
            pytest.skip(f"{fid} isn't downloaded; run scripts/fetch_edgar_filing.py first")
    return plan


def test_fixture_is_a_tva_plan(saved_plan):
    assert saved_plan.filing_ids == ["tva_facility_lease"]
    assert len(saved_plan.plan.steps) >= 2


def test_real_plan_runs_end_to_end(saved_plan):
    steps = saved_plan.plan.steps
    result = run_plan(saved_plan.task, saved_plan.filing_ids, steps)

    # every step ran, none crashed
    assert len(result.observations) == len(steps)
    errors = [f"step {o.step_id} {o.tool_name}: {o.error_kind} {o.error}" for o in result.observations
              if o.status == "error"]
    assert not errors, "\n".join(errors)
    assert result.status == "completed"

    # JSON trace: plan + every step, with inputs, outputs and latency
    trace = tw.read_run(result.run_id)
    assert trace["status"] == "completed" and trace["plan"]
    assert len(trace["steps"]) == len(steps)
    for entry in trace["steps"]:
        obs = entry["observation"]
        assert entry["step"]["tool_args"] == obs["tool_args"]
        assert obs["raw_output"] is not None and obs["latency_ms"] > 0

    # SQLite: one row per step
    rows = tw.step_rows(result.run_id)
    assert [r["step_id"] for r in rows] == [s.step_id for s in steps]

    # Day 2 tool-call log: every call tagged with this run
    assert len(read_tool_calls(result.run_id)) == len(steps)