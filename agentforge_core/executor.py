# """Executor: run ONE planned step and report what happened as an Observation.

#     PlannedStep ──► execute_step() ──► call_tool(name, args) ──► Observation

# Three outcomes, because Day 6 handles each one differently:

#     ok      the tool ran and found something to work with
#     empty   the tool ran fine but found nothing usable          → try another approach
#     error   bad_args:    the step itself was wrong (planner)     → fix the step
#             tool_failed: the tool crashed on valid args          → a bug or a bad filing

# Rules:
#   - execute_step never raises. Every outcome comes back as an Observation.
#   - Tools are called ONLY through call_tool, so the Day 2 @traced log keeps working.
#   - No LLM here. This file costs nothing to run.
# """

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentforge_core.plan import PlannedStep
from agentforge_core.tools import REGISTRY, call_tool

Status = Literal["ok", "empty", "error"]
ErrorKind = Literal["bad_args", "tool_failed"]


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
    empty_reason: Optional[str] = None  # why an 'empty' result counts as empty
    error_kind: Optional[ErrorKind] = None  # whose fault: the step or the tool
    error_type: Optional[str] = None  # e.g. "ValidationError", "UnknownTool", "ToolError"
    error: Optional[str] = None  # short message, safe to show the critic
    started_at: str  # UTC ISO time, for ordering steps in a trace

    @model_validator(mode="after")
    def fields_agree(self) -> Observation:
        if self.success_flag_from_tool != (self.status == "ok"):
            raise ValueError("success_flag_from_tool must be True exactly when status is 'ok'")
        is_error = self.status == "error"
        if is_error != bool(self.error_kind and self.error_type):
            raise ValueError("error_kind and error_type are set exactly when status is 'error'")
        if (self.status == "empty") != bool(self.empty_reason):
            raise ValueError("empty_reason is set exactly when status is 'empty'")
        if (self.raw_output is None) != is_error:
            raise ValueError("raw_output is None exactly when status is 'error'")
        return self


# ---------- what counts as "empty", per tool ----------
#
# Each rule gets (output, args) and returns a reason string, or None if the result is usable.
# Rules check TOTALS, not filtered lists: extract_cross_references(only_problems=True)
# returning [] with total=88 means "no problems found". That's a real answer, so it's ok.

EmptyRule = Callable[[dict[str, Any], dict[str, Any]], Optional[str]]


def _read_document(out: dict, args: dict) -> Optional[str]:
    if out.get("total_sections", 0) == 0:
        return "no sections could be parsed from the filing"
    if args.get("section") and not out.get("sections"):
        return f"section '{args['section']}' does not exist in this filing"
    return None


def _section_map(out: dict, args: dict) -> Optional[str]:
    if out.get("section_count", 0) == 0 or not out.get("entries"):
        return "no section headings found"
    return None


def _definitions(out: dict, args: dict) -> Optional[str]:
    if out.get("total_terms", 0) == 0:
        return "no defined terms found (no clean Definitions section?)"
    if args.get("term") and not out.get("definitions"):
        return f"term '{args['term']}' is not defined in this filing"
    return None


def _cross_refs(out: dict, args: dict) -> Optional[str]:
    if out.get("total", 0) == 0:
        return "no cross-references found anywhere in the filing"
    return None


def _dates(out: dict, args: dict) -> Optional[str]:
    if out.get("total", 0) == 0:
        return "no dates or time periods found"
    return None


def _fetch_related(out: dict, args: dict) -> Optional[str]:
    if out.get("section_count", 0) == 0:
        return "original was fetched but no sections could be parsed"
    return None


EMPTY_RULES: dict[str, EmptyRule] = {
    "read_document": _read_document,
    "extract_section_map": _section_map,
    "extract_definitions": _definitions,
    "extract_cross_references": _cross_refs,
    "extract_dates": _dates,
    "fetch_related_filing": _fetch_related,
    # flag_inconsistency writes a finding; it can't come back "empty".
}
NO_EMPTY_RULE = frozenset({"flag_inconsistency"})


def empty_reason(tool_name: str, output: dict[str, Any], args: dict[str, Any]) -> Optional[str]:
    rule = EMPTY_RULES.get(tool_name)
    return rule(output, args) if rule else None


# ---------- run one step ----------

MAX_ERROR_CHARS = 1000


def execute_step(step: PlannedStep) -> Observation:
    """Run one step through the tool registry. Never raises."""
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    args = dict(step.tool_args or {})
    start = time.perf_counter()

    def fail(kind: ErrorKind, err_type: str, msg: str) -> Observation:
        return Observation(step_id=step.step_id, tool_name=step.tool_name, tool_args=args, status="error",
                           success_flag_from_tool=False, latency_ms=_ms_since(start), error_kind=kind,
                           error_type=err_type, error=msg[:MAX_ERROR_CHARS], started_at=started_at)

    # 1. Is the step itself valid? Checked here, before calling, so that a
    #    ValidationError raised INSIDE a tool's own code isn't blamed on the planner.
    tool_cls = REGISTRY.get(step.tool_name)
    if tool_cls is None:
        return fail("bad_args", "UnknownTool",
                    f"Unknown tool '{step.tool_name}'. Known tools: {', '.join(sorted(REGISTRY))}")
    try:
        tool_cls.Input.model_validate(args)
    except ValidationError as e:
        return fail("bad_args", "ValidationError", str(e))

    # 2. Run it. Anything raised now is the tool's problem, not the step's.
    try:
        output = call_tool(step.tool_name, args).model_dump(mode="json")
    except Exception as e:  # noqa: BLE001  any failure becomes an Observation
        return fail("tool_failed", type(e).__name__, str(e))

    # 3. It ran. Did it find anything usable?
    reason = empty_reason(step.tool_name, output, args)
    status: Status = "empty" if reason else "ok"
    return Observation(step_id=step.step_id, tool_name=step.tool_name, tool_args=args, status=status,
                       raw_output=output, success_flag_from_tool=(status == "ok"), latency_ms=_ms_since(start),
                       empty_reason=reason, started_at=started_at)


def _ms_since(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)