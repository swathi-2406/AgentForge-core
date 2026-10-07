# """Executes planned steps by invoking tools and collecting results."""
# """Executor: run ONE planned step and report what happened as an Observation.

#     PlannedStep ──► execute_step() ──► call_tool(name, args) ──► Observation

# Rules:
#   - execute_step never raises. Every outcome, good or bad, comes back as an
#     Observation, so the orchestrator (Day 6) never needs a try/except.
#   - Tools are called ONLY through call_tool, so the Day 2 @traced log keeps
#     recording every call. call_tool also validates args against the tool's
#     Input schema, so a step built without validation (Day 6 retry) is still safe.
#   - No LLM here. This file costs nothing to run.

# Task 2 refines the outcome kinds (empty vs ok, bad_args vs tool errors).
# """

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentforge_core.plan import PlannedStep
from agentforge_core.tools import call_tool

Status = Literal["ok", "empty", "error"]


class Observation(BaseModel):
    """What happened when one step ran."""

    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(ge=1)
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    status: Status
    raw_output: Optional[dict[str, Any]] = None  # tool output as plain JSON; None on error
    success_flag_from_tool: bool
    latency_ms: float = Field(ge=0)
    error_type: Optional[str] = None  # e.g. "ValidationError", "ToolError"
    error: Optional[str] = None  # short message, safe to show the critic
    started_at: str  # UTC ISO time, for ordering steps in a trace

    @model_validator(mode="after")
    def fields_agree(self) -> Observation:
        if self.success_flag_from_tool != (self.status == "ok"):
            raise ValueError("success_flag_from_tool must be True exactly when status is 'ok'")
        if self.status == "error" and not self.error_type:
            raise ValueError("an error observation needs error_type")
        if self.status != "error" and self.raw_output is None:
            raise ValueError("a non-error observation needs raw_output")
        return self


MAX_ERROR_CHARS = 1000


def execute_step(step: PlannedStep) -> Observation:
    """Run one step through the tool registry. Never raises."""
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    args = dict(step.tool_args or {})
    start = time.perf_counter()

    try:
        output = call_tool(step.tool_name, args)
    except Exception as e:  # noqa: BLE001  any failure becomes an Observation
        return Observation(
            step_id=step.step_id,
            tool_name=step.tool_name,
            tool_args=args,
            status="error",
            raw_output=None,
            success_flag_from_tool=False,
            latency_ms=_ms_since(start),
            error_type=type(e).__name__,
            error=str(e)[:MAX_ERROR_CHARS],
            started_at=started_at,
        )

    return Observation(
        step_id=step.step_id,
        tool_name=step.tool_name,
        tool_args=args,
        status="ok",
        raw_output=output.model_dump(mode="json"),
        success_flag_from_tool=True,
        latency_ms=_ms_since(start),
        started_at=started_at,
    )


def _ms_since(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)