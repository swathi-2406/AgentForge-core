"""Tool implementations available to the executor."""
"""Importing this package registers every tool (each module runs its @register_tool)."""

from agentforge_core.tools import (  # noqa: F401
    extract_cross_references,
    extract_dates,
    extract_definitions,
    extract_section_map,
    fetch_related_filing,
    flag_inconsistency,
    read_document,
)
from agentforge_core.tools.base import (
    REGISTRY,
    Tool,
    ToolError,
    ToolInput,
    ToolOutput,
    call_tool,
    get_tool,
    list_tool_specs,
    register_tool,
)

__all__ = [
    "REGISTRY",
    "Tool",
    "ToolError",
    "ToolInput",
    "ToolOutput",
    "call_tool",
    "get_tool",
    "list_tool_specs",
    "register_tool",
]