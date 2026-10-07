"""Tests for agentforge_core/executor.py (Day 5 task 1).

Uses throwaway fake tools, so no filings and no LLM are needed.
Task 2 adds tests for 'empty' results and the bad_args / tool error split.
"""

import pytest
from pydantic import ValidationError

from agentforge_core.executor import Observation, execute_step
from agentforge_core.plan import PlannedStep
from agentforge_core.tools import base
from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool


@pytest.fixture(autouse=True)
def fake_tools():
    """Register three fake tools for each test, then restore the real registry."""
    saved = dict(base.REGISTRY)

    class EchoIn(ToolInput):
        text: str

    class EchoOut(ToolOutput):
        echoed: str

    @register_tool
    class Echo(Tool):
        name = "fake_echo"
        description = "Returns the text it was given."
        Input, Output = EchoIn, EchoOut

        def run(self, args):
            return EchoOut(echoed=args.text)

    class NoIn(ToolInput):
        pass

    @register_tool
    class Boom(Tool):
        name = "fake_boom"
        description = "Always crashes."
        Input, Output = NoIn, EchoOut

        def run(self, args):
            raise RuntimeError("parser fell over")

    yield
    base.REGISTRY.clear()
    base.REGISTRY.update(saved)


def step(tool="fake_echo", args=None, i=1):
    return PlannedStep(step_id=i, tool_name=tool, tool_args={"text": "hi"} if args is None else args,
                       expected_outcome="the text comes back unchanged")


# ---------- happy path ----------

def test_ok_step():
    obs = execute_step(step())
    assert obs.status == "ok"
    assert obs.success_flag_from_tool is True
    assert obs.raw_output == {"echoed": "hi"}
    assert obs.error_type is None
    assert obs.latency_ms >= 0
    assert obs.step_id == 1 and obs.tool_name == "fake_echo"


def test_raw_output_is_plain_json():
    import json
    obs = execute_step(step())
    json.dumps(obs.model_dump())  # must not raise: traces store this as-is


# ---------- never raises ----------

def test_crashing_tool_becomes_error_observation():
    obs = execute_step(step("fake_boom", args={}))
    assert obs.status == "error"
    assert obs.success_flag_from_tool is False
    assert obs.error_type == "RuntimeError"
    assert "parser fell over" in obs.error
    assert obs.raw_output is None


def test_bad_args_become_error_observation():
    # model_construct skips validation, like a Day 6 retry step might
    bad = PlannedStep.model_construct(step_id=2, tool_name="fake_echo", tool_args={"txet": "typo"},
                                      expected_outcome="the text comes back unchanged")
    obs = execute_step(bad)
    assert obs.status == "error"
    assert obs.error_type == "ValidationError"
    assert "txet" in obs.error


def test_unknown_tool_becomes_error_observation():
    bad = PlannedStep.model_construct(step_id=3, tool_name="no_such_tool", tool_args={},
                                      expected_outcome="this should never run at all")
    obs = execute_step(bad)
    assert obs.status == "error"
    assert obs.error_type == "ToolError"


# ---------- the Observation model guards itself ----------

def test_success_flag_must_match_status():
    with pytest.raises(ValidationError, match="success_flag_from_tool"):
        Observation(step_id=1, tool_name="x", status="ok", raw_output={}, success_flag_from_tool=False,
                    latency_ms=1, started_at="2026-10-07T00:00:00Z")


def test_error_needs_error_type():
    with pytest.raises(ValidationError, match="error_type"):
        Observation(step_id=1, tool_name="x", status="error", success_flag_from_tool=False,
                    latency_ms=1, started_at="2026-10-07T00:00:00Z")


def test_long_errors_are_trimmed():
    class In(ToolInput):
        pass

    class Out(ToolOutput):
        pass

    @register_tool
    class Loud(Tool):
        name = "fake_loud"
        description = "Crashes with a huge message."
        Input, Output = In, Out

        def run(self, args):
            raise RuntimeError("x" * 5000)

    obs = execute_step(step("fake_loud", args={}))
    assert len(obs.error) <= 1000