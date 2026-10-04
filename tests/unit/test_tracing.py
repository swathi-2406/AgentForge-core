"""Tests for the tool-call tracing decorator."""

import pytest
from pydantic import ValidationError

from agentforge_core.tools import base
from agentforge_core.tools.base import Tool, ToolError, ToolInput, ToolOutput, call_tool, register_tool
from agentforge_core.tracing.tool_calls import read_tool_calls, tool_calls_path, trace_context


@pytest.fixture(autouse=True)
def echo_tool():
    saved = dict(base.REGISTRY)

    class EchoIn(ToolInput):
        text: str

    class EchoOut(ToolOutput):
        echoed: str
        parts: list[str]

    @register_tool
    class Echo(Tool):
        name = "echo_traced"
        description = "Echo."
        Input, Output = EchoIn, EchoOut

        def run(self, args):
            if args.text == "boom":
                raise RuntimeError("tool blew up")
            return EchoOut(echoed=args.text, parts=args.text.split())

    yield
    base.REGISTRY.clear()
    base.REGISTRY.update(saved)


def test_successful_call_is_logged():
    out = call_tool("echo_traced", {"text": "a b c"})
    [row] = read_tool_calls()
    assert out.echoed == "a b c"
    assert (row["tool"], row["status"], row["args"]) == ("echo_traced", "ok", {"text": "a b c"})
    assert row["latency_ms"] >= 0 and row["output"]["counts"] == {"parts": 3} and row["output"]["bytes"] > 0


@pytest.mark.parametrize("args, error_type, raises", [
    ({}, "ValidationError", ValidationError),                 # bad args
    ({"text": "boom"}, "RuntimeError", RuntimeError),         # the tool itself fails
])
def test_errors_are_logged_and_reraised(args, error_type, raises):
    with pytest.raises(raises):
        call_tool("echo_traced", args)
    [row] = read_tool_calls()
    assert row["status"] == "error" and row["error_type"] == error_type and "output" not in row


def test_unknown_tool_is_logged():
    with pytest.raises(ToolError):
        call_tool("no_such_tool", {})
    assert read_tool_calls()[0]["error_type"] == "ToolError"


def test_run_and_step_context():
    with trace_context(run_id="run1"):
        with trace_context(step_id="s1"):
            call_tool("echo_traced", {"text": "x"})
        call_tool("echo_traced", {"text": "y"})
    call_tool("echo_traced", {"text": "z"})
    rows = read_tool_calls()
    assert [(r["run_id"], r["step_id"]) for r in rows] == [("run1", "s1"), ("run1", None), (None, None)]
    assert len(read_tool_calls(run_id="run1")) == 2


def test_long_args_are_shortened_in_the_log_only():
    out = call_tool("echo_traced", {"text": "x" * 2000})
    assert len(out.echoed) == 2000
    assert read_tool_calls()[0]["args"]["text"].endswith("[2000 chars]")


def test_tracing_failure_never_breaks_the_call(tmp_path, monkeypatch):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("I am a file, so no folder can be created here")
    monkeypatch.setenv("AGENTFORGE_TRACES_DIR", str(blocker / "traces"))
    assert call_tool("echo_traced", {"text": "still works"}).echoed == "still works"


def test_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AGENTFORGE_TRACE", "0")
    call_tool("echo_traced", {"text": "quiet"})
    assert not tool_calls_path().exists()


def test_tests_never_touch_real_traces():
    assert "traces" in str(tool_calls_path()) and "data" not in tool_calls_path().parts[-3:-1]