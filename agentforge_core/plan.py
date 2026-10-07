"""Plan schema: the shape the planner must return.

    Plan
     └── steps: [PlannedStep, PlannedStep, ...]
           step_id · tool_name · tool_args · expected_outcome

Lives in its own module (not planner.py) so state.py can import it
without pulling in any LLM code.

Every check runs at validation time, so a bad plan fails right here,
never later inside the executor. The error messages are written for
the LLM too: on Day 4 task 3, instructor sends them back to the model
as its one chance to fix the plan.
"""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agentforge_core.tools import REGISTRY

# Tools the planner may NOT schedule. flag_inconsistency's args (location,
# description) come from results the planner hasn't seen yet. Findings are
# flagged after observing, on Day 6.
PLAN_TIME_EXCLUDED: frozenset[str] = frozenset({"flag_inconsistency"})

MAX_STEPS = 12


def planner_tool_names() -> list[str]:
    """The tools the planner is allowed to use. Task 2 builds its menu from this."""
    return sorted(n for n in REGISTRY if n not in PLAN_TIME_EXCLUDED)


def _short_errors(err: ValidationError) -> str:
    """'filing_idd: Extra inputs are not permitted; filing_id: Field required'"""
    parts = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "(args)"
        parts.append(f"{loc}: {e['msg']}")
    return "; ".join(parts)


class PlannedStep(BaseModel):
    """One step: which tool, with what args, and what success looks like."""

    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(ge=1, description="1, 2, 3... in order")
    tool_name: str = Field(description="Exact name of a tool from the menu")
    tool_args: dict[str, Any] = Field(
        default_factory=dict, description="Args matching that tool's input schema"
    )
    expected_outcome: str = Field(
        min_length=10,
        description="A checkable result, e.g. 'a non-empty section map for tva_facility_lease'",
    )

    @field_validator("tool_name")
    @classmethod
    def tool_must_exist(cls, v: str) -> str:
        if v in PLAN_TIME_EXCLUDED:
            raise ValueError(
                f"'{v}' can't be planned ahead; findings are flagged after observing results. "
                f"Use only: {', '.join(planner_tool_names())}"
            )
        if v not in REGISTRY:
            raise ValueError(
                f"Unknown tool '{v}'. Use only: {', '.join(planner_tool_names())}"
            )
        return v

    @model_validator(mode="after")
    def args_must_fit_tool(self) -> PlannedStep:
        tool_cls = REGISTRY[self.tool_name]
        try:
            tool_cls.Input.model_validate(self.tool_args)
        except ValidationError as e:
            raise ValueError(
                f"step {self.step_id}: bad tool_args for {self.tool_name}: {_short_errors(e)}"
            ) from None
        return self


class Plan(BaseModel):
    """An ordered list of steps.

    Optional validation context {"filing_ids": {...}} restricts which filings
    the plan may touch. Task 3 passes it; without it, that check is skipped.
    """

    model_config = ConfigDict(extra="forbid")

    steps: list[PlannedStep] = Field(min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def step_ids_in_order(self) -> Plan:
        ids = [s.step_id for s in self.steps]
        want = list(range(1, len(ids) + 1))
        if ids != want:
            raise ValueError(f"step_ids must be {want}, got {ids}")
        return self

    @model_validator(mode="after")
    def only_allowed_filings(self, info: ValidationInfo) -> Plan:
        allowed = (info.context or {}).get("filing_ids")
        if not allowed:
            return self
        for s in self.steps:
            for key in ("filing_id", "original_id", "amendment_id"):
              fid = s.tool_args.get(key)
              if fid is not None and fid not in allowed:
                raise ValueError(
                    f"step {s.step_id} uses {key} '{fid}', but this task only "
                    f"covers: {', '.join(sorted(allowed))}"
                )
        return self