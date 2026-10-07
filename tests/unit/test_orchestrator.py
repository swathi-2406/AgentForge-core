"""Tests for the Day 5 orchestrator loop (no critic yet), using fake tools. No filings, no LLM."""

import json

import pytest

from agentforge_core import orchestrator
from agentforge_core.executor import EMPTY_RULES
from agentforge_core.orchestrator import latest_plan_for, run_plan, run_saved_plan
from agentforge_core.plan import PlannedStep
from agentforge_core.tools import base
from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tracing import trace_writer as tw
from agentforge_core.tracing.tool_calls import read_tool_calls


class EchoIn(ToolInput):
    text: str


class EchoOut(ToolOutput):
    echoed: str


@pytest.fixture(autouse=True)
def fake_tool(monkeypatch):
    saved = dict(base.REGISTRY)

    @register_tool
    class Echo(Tool):
        name = "fake_echo"
        description = "Returns the text it was given."
        Input, Output = EchoIn, EchoOut

        def run(self, args):
            return EchoOut(echoed=args.text)

    monkeypatch.setitem(EMPTY_RULES, "fake_echo", lambda out, args: None if out["echoed"] else "nothing")
    yield
    base.REGISTRY.clear()
    base.REGISTRY.update(saved)


def step(i, text="hi", tool="fake_echo"):
    return PlannedStep.model_construct(step_id=i, tool_name=tool, tool_args={"text": text},
                                       expected_outcome="the text comes back unchanged")


def test_all_ok_run_completes():
    r = run_plan("t", ["x"], [step(1), step(2)])
    assert r.status == "completed" and r.count("ok") == 2
    assert tw.read_run(r.run_id)["status"] == "completed"
    assert len(tw.step_rows(r.run_id)) == 2
    assert len(read_tool_calls(r.run_id)) == 2


def test_empty_does_not_fail_the_run():
    r = run_plan("t", ["x"], [step(1), step(2, text="")])
    assert r.status == "completed" and r.count("empty") == 1


def test_error_fails_the_run_but_later_steps_still_run():
    r = run_plan("t", ["x"], [step(1, tool="nope"), step(2)])
    assert r.status == "failed"
    assert [o.status for o in r.observations] == ["error", "ok"]
    assert len(tw.read_run(r.run_id)["steps"]) == 2


def test_plan_is_stored_in_the_trace():
    r = run_plan("t", ["x"], [step(1)])
    assert tw.read_run(r.run_id)["plan"][0]["tool_name"] == "fake_echo"


def test_saved_plan_round_trip(tmp_path):
    saved = {"task": "echo", "filing_ids": ["x"], "prompt_version": "test", "call": {},
             "plan": {"steps": [{"step_id": 1, "tool_name": "fake_echo", "tool_args": {"text": "a"},
                                 "expected_outcome": "the text comes back"}]}}
    p = tmp_path / "20261007T000000Z_x.json"
    p.write_text(json.dumps(saved), encoding="utf-8")
    r = run_saved_plan(p)
    assert r.status == "completed"
    assert tw.read_run(r.run_id)["task"] == "echo"


def test_latest_plan_picks_newest(tmp_path):
    for stamp in ["20261007T100000Z", "20261007T120000Z", "20261007T110000Z"]:
        (tmp_path / f"{stamp}_tva_facility_lease.json").write_text("{}")
    (tmp_path / "20261007T130000Z_ford_arr_2026b.json").write_text("{}")
    assert latest_plan_for("tva_facility_lease", tmp_path).name.startswith("20261007T120000Z")


def test_latest_plan_explains_when_missing(tmp_path):
    with pytest.raises(SystemExit, match="--run"):
        latest_plan_for("tva_facility_lease", tmp_path)