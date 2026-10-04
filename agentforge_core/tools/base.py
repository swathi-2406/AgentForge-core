# """Tool base class and registry: the one place planner and executor learn which tools exist.

# Every tool:
#     1. subclasses Tool,
#     2. defines Input and Output as ToolInput / ToolOutput subclasses,
#     3. implements run(args) -> Output,
#     4. is decorated with @register_tool.

# Calling code never imports a tool directly. It calls:
#     call_tool("extract_section_map", {"filing_id": "tva_facility_lease"})
# """

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from agentforge_core.tracing.tool_calls import traced


class ToolInput(BaseModel):
    """Base for tool args. Unknown fields fail loudly instead of being silently dropped."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolOutput(BaseModel):
    """Base for tool results."""

    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """Raised when a tool is missing, misdefined, or returns the wrong shape."""


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[ToolInput]]
    Output: ClassVar[type[ToolOutput]]

    @abstractmethod
    def run(self, args: ToolInput) -> ToolOutput:
        """Do the work. args is already validated against self.Input."""

    @classmethod
    def spec(cls) -> dict[str, Any]:
        """What the planner sees: name, description, and JSON schemas."""
        return {
            "name": cls.name,
            "description": cls.description,
            "input_schema": cls.Input.model_json_schema(),
            "output_schema": cls.Output.model_json_schema(),
        }


REGISTRY: dict[str, type[Tool]] = {}


def register_tool(cls: type[Tool]) -> type[Tool]:
    """Class decorator: check the tool is well formed, then add it to REGISTRY."""
    if not (isinstance(cls, type) and issubclass(cls, Tool)):
        raise ToolError(f"{cls!r} must subclass Tool")
    for attr in ("name", "description", "Input", "Output"):
        if not hasattr(cls, attr):
            raise ToolError(f"{cls.__name__} is missing '{attr}'")
    if not issubclass(cls.Input, ToolInput) or not issubclass(cls.Output, ToolOutput):
        raise ToolError(f"{cls.__name__}: Input/Output must subclass ToolInput/ToolOutput")
    if cls.name in REGISTRY and REGISTRY[cls.name] is not cls:
        raise ToolError(f"Tool name '{cls.name}' is already registered")
    REGISTRY[cls.name] = cls
    return cls


def get_tool(name: str) -> type[Tool]:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "(none registered)"
        raise ToolError(f"Unknown tool '{name}'. Known tools: {known}") from None


@traced
def call_tool(name: str, args: dict[str, Any] | None = None) -> ToolOutput:
    """Look up a tool by name, validate args, run it, and check the output shape.

    Raises:
        ToolError: unknown tool, or the tool returned the wrong type.
        pydantic.ValidationError: args don't match the tool's Input schema.
    """
    tool_cls = get_tool(name)
    parsed = tool_cls.Input.model_validate(args or {})
    result = tool_cls().run(parsed)
    if not isinstance(result, tool_cls.Output):
        raise ToolError(
            f"{name} returned {type(result).__name__}, expected {tool_cls.Output.__name__}"
        )
    return result


def list_tool_specs() -> list[dict[str, Any]]:
    """All registered tools, for the planner prompt (Day 4)."""
    return [REGISTRY[n].spec() for n in sorted(REGISTRY)]