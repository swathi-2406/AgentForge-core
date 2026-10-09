"""The eval task format (Day 7). One YAML file in evals/tasks/ = one EvalTask.

Every task must be checkable by a script, with no human in the loop:

    every must_flag matched  AND  no must_not_flag hit  AND  extras <= max_extra_findings  ->  PASS

The finding types are imported from flag_inconsistency, so a task can never use a label
the agent is unable to produce. Files whose names start with "_" (the template) are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentforge_core.tools.flag_inconsistency import FindingType

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"
TIER_DIRS = {1: "tier1_cross_reference", 2: "tier2_defined_terms", 3: "tier3_amendment_contradiction"}


def normalize_location(location: str) -> str:
    """'Section 15.1(a)' -> '15.1'. Same rule flag_inconsistency uses to verify locations."""
    base = re.sub(r"^(?:section|§)\s*", "", location.strip(), flags=re.I)
    return base.split("(")[0].strip().rstrip(".").lower()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a typo like "must_flagg" fails loudly


class ExpectedFinding(_Strict):
    """A finding the agent must report."""
    type: Union[FindingType, list[FindingType]]  # one label, or a short list of acceptable ones
    location: str = Field(min_length=1)          # "15.1", "Schedule A", "Article II"
    filing: Optional[str] = None                 # required only when the task has two filings

    def accepted_types(self) -> set[str]:
        return set(self.type) if isinstance(self.type, list) else {self.type}


class Trap(_Strict):
    """A place where a finding would be wrong. Any finding here fails the task."""
    location: str = Field(min_length=1)
    reason: str = Field(min_length=5)            # why it looks wrong but isn't
    filing: Optional[str] = None


class Expected(_Strict):
    must_flag: list[ExpectedFinding]             # [] means "this should come back clean"
    must_not_flag: list[Trap] = []
    max_extra_findings: int = Field(0, ge=0, le=5)


class EvalTask(_Strict):
    id: str = Field(pattern=r"^t[123]_[a-z0-9_]+$")
    tier: Literal[1, 2, 3]
    filings: list[str] = Field(min_length=1, max_length=2)
    task: str = Field(min_length=15)
    scope: str = "whole_document"
    expected: Expected
    ground_truth_ref: list[str] = []             # row ids in docs/ground_truth.md
    optimal_steps: int = Field(ge=1, le=20)
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "EvalTask":
        if not self.id.startswith(f"t{self.tier}_"):
            raise ValueError(f"id {self.id!r} must start with 't{self.tier}_' for a tier {self.tier} task")
        clean = not self.expected.must_flag
        if clean and self.ground_truth_ref:
            raise ValueError("a clean task (must_flag: []) cannot cite ground-truth rows")
        if not clean and not self.ground_truth_ref:
            raise ValueError("must_flag needs ground_truth_ref: which verified row says so?")
        for item in [*self.expected.must_flag, *self.expected.must_not_flag]:
            if item.filing is None and len(self.filings) > 1:
                raise ValueError(f"task has two filings, so '{item.location}' needs a 'filing:'")
            if item.filing is not None and item.filing not in self.filings:
                raise ValueError(f"'{item.filing}' is not in this task's filings {self.filings}")
        return self


def load_task(path: Path) -> EvalTask:
    """Load and validate one task file. The id must equal the file name, the tier the folder."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: empty or not a YAML mapping. Delete it, or fill it in from _template.yaml")
    task = EvalTask.model_validate(data)
    if task.id != path.stem:
        raise ValueError(f"{path.name}: id is {task.id!r}, but the file name says {path.stem!r}")
    if path.parent.name != TIER_DIRS[task.tier]:
        raise ValueError(f"{path.name}: tier {task.tier} task belongs in {TIER_DIRS[task.tier]}/")
    return task


def load_all(root: Path = TASKS_DIR) -> list[EvalTask]:
    """Every task under evals/tasks/, sorted by id. Skips _template.yaml and other _ files."""
    tasks = [load_task(p) for p in sorted(Path(root).rglob("*.yaml")) if not p.name.startswith("_")]
    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate task ids: {sorted(dupes)}")
    return sorted(tasks, key=lambda t: t.id)