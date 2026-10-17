# """Schema definitions for eval task specification files."""
# """The trajectory schema, locked on Day 8. One Trajectory = one full agent run on one task.

# Every component that writes run data writes this shape, and Project 2 (AgentForge-Tune)
# trains on it as-is. So from now on a change here is a MIGRATION, not an edit:
# bump SCHEMA_VERSION, regenerate trajectory.schema.json, and say what changed below.

#     Trajectory
#     ├─ run_id, task_id, task, filings, schema_version="v1"
#     ├─ plan[]                  the plan as first made: step_id, tool, args, expected_outcome
#     ├─ steps[]                 what actually ran
#     │   └─ attempts[]          1 = first try; 2, 3... = retries of THIS step only
#     │       ├─ tool_call       tool_name + tool_args
#     │       ├─ observation     status ok|empty|error, latency, output preview
#     │       ├─ critique        verdict success|partial|failure, reasoning, confidence
#     │       ├─ retry_reason    why this attempt replaced the previous one (None on attempt 1)
#     │       └─ reward          -1 | 0 | +1 | None   (None until the Day 9 scorer fills it in)
#     ├─ findings[]              every flag_inconsistency call, including suppressed ones (Sprint 4)
#     ├─ final_status, give_up_reason
#     └─ total_latency_ms, total_retries, confidence (Day 12)

# How Project 2 reads it as (instruction, context, output) examples:
#     instruction = task
#     context     = plan + earlier attempts' tool calls and observations
#     output      = the next tool call, or the critique, or the retry with its retry_reason
# That's why every attempt keeps its own observation and critique instead of only the last one.

# Changelog
#     v1  (Day 8)  first locked version
# """
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentforge_core.tools.flag_inconsistency import FindingType

SCHEMA_VERSION = "v1"
JSON_SCHEMA_PATH = Path(__file__).with_name("trajectory.schema.json")
PREVIEW_CHARS = 2_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")      # a misspelled key fails loudly, never silently


class PlannedStep(_Strict):
    step_id: int = Field(ge=1)
    tool_name: str = Field(min_length=1)
    tool_args: dict[str, Any] = {}
    expected_outcome: str = Field(min_length=1)


class ToolCall(_Strict):
    tool_name: str = Field(min_length=1)
    tool_args: dict[str, Any] = {}


class Observation(_Strict):
    status: Literal["ok", "empty", "error"]
    latency_ms: float = Field(ge=0)
    output_preview: str = Field("", max_length=PREVIEW_CHARS)
    output_bytes: Optional[int] = Field(None, ge=0)
    error_kind: Optional[str] = None               # tool error vs. LLM error (Day 5)
    error_type: Optional[str] = None
    empty_reason: Optional[str] = None


class Critique(_Strict):
    verdict: Literal["success", "partial", "failure"]
    reasoning: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    source: Literal["rule", "llm"] = "llm"


class Attempt(_Strict):
    attempt: int = Field(ge=1)
    tool_call: ToolCall
    expected_outcome: str = Field(min_length=1)
    observation: Observation
    critique: Optional[Critique] = None            # None only if the run stopped before critiquing
    retry_reason: Optional[str] = None             # why this attempt replaced the previous one
    reward: Optional[Literal[-1, 0, 1]] = None     # did it move the task closer to success? (Day 9)


class Step(_Strict):
    step_id: int = Field(ge=1)
    attempts: list[Attempt] = Field(min_length=1)

    @model_validator(mode="after")
    def _attempts_in_order(self) -> "Step":
        numbers = [a.attempt for a in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"step {self.step_id}: attempts must be numbered 1, 2, 3...; got {numbers}")
        for a in self.attempts:
            if a.attempt == 1 and a.retry_reason:
                raise ValueError(f"step {self.step_id}: attempt 1 is not a retry, so it has no retry_reason")
            if a.attempt > 1 and not (a.retry_reason or "").strip():
                raise ValueError(f"step {self.step_id}: retry {a.attempt} needs a retry_reason")
        return self

    @property
    def retries(self) -> int:
        return len(self.attempts) - 1

    @property
    def final(self) -> Attempt:
        return self.attempts[-1]


class Finding(_Strict):
    finding_id: str = Field(min_length=1)
    type: FindingType
    location: str = Field(min_length=1)
    filing_id: Optional[str] = None
    description: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    step_id: Optional[int] = None                  # which step flagged it
    suppressed: bool = False                       # Sprint 4: kept and marked, never dropped


class Trajectory(_Strict):
    schema_version: Literal["v1"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    task_id: Optional[str] = None                  # eval task id; None for a free run
    task: str = Field(min_length=1)
    filings: list[str] = Field(min_length=1, max_length=2)
    plan: list[PlannedStep]
    steps: list[Step]
    findings: list[Finding] = []
    final_status: Literal["completed", "failed"]
    give_up_reason: Optional[str] = None
    started_at: str
    finished_at: str
    total_latency_ms: float = Field(ge=0)
    total_retries: int = Field(ge=0)
    confidence: Optional[float] = Field(None, ge=0, le=1)   # Day 12 fills this in

    @model_validator(mode="after")
    def _consistent(self) -> "Trajectory":
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate step_id in steps: {ids}")
        planned = {p.step_id for p in self.plan}
        if not set(ids) <= planned:
            raise ValueError(f"steps {sorted(set(ids) - planned)} are not in the plan")
        retries = sum(s.retries for s in self.steps)
        if self.total_retries != retries:
            raise ValueError(f"total_retries is {self.total_retries}, but the steps hold {retries} retries")
        step_ms = sum(a.observation.latency_ms for s in self.steps for a in s.attempts)
        if self.total_latency_ms + 1e-6 < step_ms:
            raise ValueError(f"total_latency_ms {self.total_latency_ms} is less than the steps' sum {step_ms:.1f}")
        if self.final_status == "failed" and not (self.give_up_reason or "").strip():
            raise ValueError("a failed run needs a give_up_reason")
        known = set(ids)
        for f in self.findings:
            if f.step_id is not None and f.step_id not in known:
                raise ValueError(f"finding {f.finding_id} points at step {f.step_id}, which isn't in steps")
        return self


def json_schema() -> dict[str, Any]:
    return Trajectory.model_json_schema()


def write_json_schema(path: Path = JSON_SCHEMA_PATH) -> Path:
    path.write_text(json.dumps(json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":       # python -m evals.harness.schema  -> regenerate the locked JSON Schema
    print(f"wrote {write_json_schema()}")