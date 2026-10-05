# """Shared state definitions passed between planner, executor, and critic."""
# """State: the one object every step of the agent reads and writes.

#     task  ->  plan  ->  [execute -> critique -> retry?] per step  ->  status + confidence

# Planner, executor, critic, retry and orchestrator all import this model.
# None of them define their own copies of these fields.

# Two types are loose on purpose and get tightened later:
#     plan              list of dicts now, list[PlannedStep] on Day 4
#     last_observation  dict now, Observation on Day 5
# """

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "contracts" / "manifest.yaml"

Status = Literal["pending", "running", "succeeded", "failed"]


@lru_cache(maxsize=None)
def manifest_ids(path: Path = MANIFEST_PATH) -> frozenset[str]:
    """Every filing id in data/contracts/manifest.yaml."""
    data = yaml.safe_load(path.read_text()) or []
    if isinstance(data, dict):  # also accept a top-level "filings:" key
        data = data.get("filings", [])
    return frozenset(entry["id"] for entry in data)


class State(BaseModel):
    # extra="forbid": a typo like `retries=2` fails instead of being dropped.
    # validate_assignment=True: rules still apply when the orchestrator
    # changes a field later, e.g. state.retry_count += 1.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    task: str = Field(min_length=1, description="The checking task, in plain English.")
    contract_refs: list[str] = Field(min_length=1, description="Manifest ids of the filings in play.")
    plan: list[dict[str, Any]] = Field(default_factory=list, description="Ordered planned steps, each with a step_id.")
    completed_steps: list[int] = Field(default_factory=list, description="step_ids that finished, in order.")
    last_observation: dict[str, Any] | None = Field(default=None, description="Raw result of the latest tool call.")
    retry_count: int = Field(default=0, ge=0, description="Retries used so far in this task.")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="Composite score, set in Sprint 5.")
    status: Status = "pending"

    @field_validator("contract_refs")
    @classmethod
    def refs_exist_in_manifest(cls, refs: list[str]) -> list[str]:
        if len(set(refs)) != len(refs):
            raise ValueError(f"duplicate filing ids in contract_refs: {refs}")
        unknown = sorted(set(refs) - manifest_ids())
        if unknown:
            raise ValueError(f"not in manifest.yaml: {unknown}. Known ids: {sorted(manifest_ids())}")
        return refs

    @model_validator(mode="after")
    def completed_steps_are_planned(self) -> State:
        planned = {step.get("step_id") for step in self.plan}
        stray = [s for s in self.completed_steps if s not in planned]
        if stray:
            raise ValueError(f"completed_steps {stray} are not step_ids in the plan")
        return self