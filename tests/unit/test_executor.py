"""Tests for agentforge_core/executor.py (Day 5 tasks 1 and 2).

Fake tools for the executor itself, plain dicts for the per-tool empty rules.
No filings and no LLM needed.
"""

import json

import pytest
from pydantic import ValidationError

from agentforge_core import executor
from agentforge_core.executor import EMPTY_RULES, NO_EMPTY_RULE, Observation, empty_reason, execute_step
from agentforge_core.plan import PlannedStep
from agentforge_core.tools import base
from agentforge_core.tools.base import Tool, ToolError, ToolInput, ToolOutput, register_tool


class EchoIn(ToolInput):
    text: str


class EchoOut(ToolOutput):
    echoed: str


class NoIn(ToolInput):
    pass


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch):
    """Register fake tools for each test, then restore the real registry."""
    saved = dict(base.REGISTRY)

    @register_tool
    class Echo(Tool):
        name = "fake_echo"
        description = "Returns the text it was given."
        Input, Output = EchoIn, EchoOut

        def run(self, args):
            return EchoOut(echoed=args.text)

    @register_tool
    class Boom(Tool):
        name = "fake_boom"
        description = "Always crashes."
        Input, Output = NoIn, EchoOut

        def run(self, args):
            raise RuntimeError("parser fell over")

    @register_tool
    class InnerValidation(Tool):
        name = "fake_inner_validation"
        description = "Valid args, but the tool's own code raises a ValidationError."
        Input, Output = NoIn, EchoOut

        def run(self, args):
            return EchoOut(echoed=123)  # wrong type inside the tool

    @register_tool
    class Raises(Tool):
        name = "fake_tool_error"
        description = "Raises ToolError, like a missing filing."
        Input, Output = NoIn, EchoOut

        def run(self, args):
            raise ToolError("'nope' isn't downloaded yet")

    # fake_echo counts as empty when it echoes an empty string
    monkeypatch.setitem(EMPTY_RULES, "fake_echo",
                        lambda out, args: "nothing to echo" if not out["echoed"] else None)
    yield
    base.REGISTRY.clear()
    base.REGISTRY.update(saved)


def step(tool="fake_echo", args=None, i=1):
    return PlannedStep(step_id=i, tool_name=tool, tool_args={"text": "hi"} if args is None else args,
                       expected_outcome="the text comes back unchanged")


def unchecked(tool, args, i=2):
    """A step built without validation, like a Day 6 retry step might be."""
    return PlannedStep.model_construct(step_id=i, tool_name=tool, tool_args=args,
                                       expected_outcome="whatever the tool should return")


# ---------- ok ----------

def test_ok_step():
    obs = execute_step(step())
    assert (obs.status, obs.success_flag_from_tool) == ("ok", True)
    assert obs.raw_output == {"echoed": "hi"}
    assert obs.error_kind is obs.error_type is obs.empty_reason is None
    assert obs.latency_ms >= 0 and obs.step_id == 1


def test_observation_is_plain_json():
    json.dumps(execute_step(step()).model_dump())  # traces store this as-is


# ---------- empty ----------

def test_empty_result_is_not_ok():
    obs = execute_step(step(args={"text": ""}))
    assert obs.status == "empty"
    assert obs.success_flag_from_tool is False
    assert obs.empty_reason == "nothing to echo"
    assert obs.raw_output == {"echoed": ""}  # the output is kept, so the critic can see it
    assert obs.error_kind is None


def test_tool_without_rule_is_ok():
    assert empty_reason("some_new_tool", {}, {}) is None


# ---------- error: bad_args (the step's fault) ----------

def test_bad_args():
    obs = execute_step(unchecked("fake_echo", {"txet": "typo"}))
    assert (obs.status, obs.error_kind, obs.error_type) == ("error", "bad_args", "ValidationError")
    assert "txet" in obs.error and obs.raw_output is None


def test_unknown_tool_is_bad_args():
    obs = execute_step(unchecked("no_such_tool", {}))
    assert (obs.error_kind, obs.error_type) == ("bad_args", "UnknownTool")
    assert "fake_echo" in obs.error  # tells Day 6's retry what IS allowed


# ---------- error: tool_failed (the tool's fault) ----------

def test_crash_is_tool_failed():
    obs = execute_step(step("fake_boom", args={}))
    assert (obs.error_kind, obs.error_type) == ("tool_failed", "RuntimeError")
    assert "parser fell over" in obs.error


def test_tool_error_is_tool_failed():
    obs = execute_step(step("fake_tool_error", args={}))
    assert (obs.error_kind, obs.error_type) == ("tool_failed", "ToolError")


def test_validation_error_inside_tool_is_not_blamed_on_step():
    obs = execute_step(step("fake_inner_validation", args={}))
    assert (obs.error_kind, obs.error_type) == ("tool_failed", "ValidationError")


def test_long_errors_are_trimmed(monkeypatch):
    monkeypatch.setattr(executor, "call_tool", lambda n, a: (_ for _ in ()).throw(RuntimeError("x" * 5000)))
    assert len(execute_step(step()).error) <= 1000


# ---------- the Observation model guards itself ----------

BASE = dict(step_id=1, tool_name="x", latency_ms=1, started_at="2026-10-07T00:00:00Z")


@pytest.mark.parametrize("bad, match", [
    (dict(status="ok", raw_output={}, success_flag_from_tool=False), "success_flag_from_tool"),
    (dict(status="error", success_flag_from_tool=False, error_type="X"), "error_kind"),
    (dict(status="empty", raw_output={}, success_flag_from_tool=False), "empty_reason"),
    (dict(status="ok", raw_output={}, success_flag_from_tool=True, empty_reason="hm"), "empty_reason"),
    (dict(status="ok", success_flag_from_tool=True), "raw_output"),
])
def test_impossible_observations_rejected(bad, match):
    with pytest.raises(ValidationError, match=match):
        Observation(**BASE, **bad)


# ---------- the real tools' empty rules ----------

def test_every_registered_tool_has_an_empty_rule():
    real = {n for n in base.REGISTRY if not n.startswith("fake_")}
    missing = real - set(EMPTY_RULES) - NO_EMPTY_RULE
    assert not missing, f"add an EMPTY_RULES entry for: {sorted(missing)}"


@pytest.mark.parametrize("tool, out, args, empty", [
    # whole-document emptiness
    ("read_document", {"total_sections": 0, "sections": []}, {}, True),
    ("extract_section_map", {"section_count": 0, "entries": []}, {}, True),
    ("extract_definitions", {"total_terms": 0, "definitions": []}, {}, True),
    ("extract_cross_references", {"total": 0, "references": []}, {}, True),
    ("extract_dates", {"total": 0, "mentions": []}, {}, True),
    ("fetch_related_filing", {"section_count": 0}, {}, True),
    # asked for one thing that isn't there
    ("read_document", {"total_sections": 40, "sections": []}, {"section": "99.9"}, True),
    ("extract_definitions", {"total_terms": 120, "definitions": []}, {"term": "Purchaser"}, True),
    # "no problems found" is a real answer, NOT empty
    ("extract_cross_references", {"total": 88, "references": []}, {"only_problems": True}, False),
    ("extract_dates", {"total": 30, "mentions": []}, {"only_issues": True}, False),
    ("extract_definitions", {"total_terms": 120, "definitions": []}, {"only_issues": True}, False),
    # normal results
    ("extract_section_map", {"section_count": 75, "entries": [{}]}, {}, False),
    ("fetch_related_filing", {"section_count": 75}, {}, False),
])
def test_real_tool_rules(tool, out, args, empty):
    assert (empty_reason(tool, out, args) is not None) == empty