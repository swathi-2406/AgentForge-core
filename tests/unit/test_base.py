"""Tests for tools/base.py, using a throwaway Echo tool so real tools aren't needed yet."""

import pytest
from pydantic import ValidationError

from agentforge_core.tools import base
from agentforge_core.tools.base import (
    Tool,
    ToolError,
    ToolInput,
    ToolOutput,
    call_tool,
    list_tool_specs,
    register_tool,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Keep test tools out of the real registry."""
    saved = dict(base.REGISTRY)
    base.REGISTRY.clear()
    yield
    base.REGISTRY.clear()
    base.REGISTRY.update(saved)


def make_echo():
    class EchoIn(ToolInput):
        text: str

    class EchoOut(ToolOutput):
        echoed: str

    @register_tool
    class Echo(Tool):
        name = "echo"
        description = "Returns the text it was given."
        Input = EchoIn
        Output = EchoOut

        def run(self, args):
            return EchoOut(echoed=args.text)

    return Echo


def test_call_by_name_only():
    make_echo()
    assert call_tool("echo", {"text": "hi"}).echoed == "hi"


def test_unknown_tool_names_known_ones():
    make_echo()
    with pytest.raises(ToolError, match="Known tools: echo"):
        call_tool("nope", {})


def test_missing_arg_fails():
    make_echo()
    with pytest.raises(ValidationError):
        call_tool("echo", {})


def test_extra_arg_fails():
    make_echo()
    with pytest.raises(ValidationError):
        call_tool("echo", {"text": "hi", "txet": "typo"})


def test_wrong_type_fails():
    make_echo()
    with pytest.raises(ValidationError):
        call_tool("echo", {"text": ["not", "a", "string"]})


def test_duplicate_name_rejected():
    make_echo()
    with pytest.raises(ToolError, match="already registered"):
        make_echo()


def test_missing_attribute_rejected():
    with pytest.raises(ToolError, match="missing 'description'"):

        @register_tool
        class Bad(Tool):
            name = "bad"

            def run(self, args):
                return None


def test_wrong_output_type_rejected():
    class In(ToolInput):
        pass

    class Out(ToolOutput):
        ok: bool

    @register_tool
    class Liar(Tool):
        name = "liar"
        description = "Returns the wrong type."
        Input = In
        Output = Out

        def run(self, args):
            return {"ok": True}

    with pytest.raises(ToolError, match="expected Out"):
        call_tool("liar")


def test_spec_has_schemas():
    make_echo()
    spec = list_tool_specs()[0]
    assert spec["name"] == "echo"
    assert "text" in spec["input_schema"]["properties"]
    assert "echoed" in spec["output_schema"]["properties"]