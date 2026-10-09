"""The eval task format (Day 7). One YAML file in evals/tasks/ = one EvalTask.

Every task must be checkable by a script, with no human in the loop:

    every must_flag matched  AND  no must_not_flag hit  AND  extras <= max_extra_findings  ->  PASS

A finding that satisfies a must_flag is never also checked against the traps, so a word-match
trap can't catch a correct answer by accident.

The finding types are imported from flag_inconsistency, so a task can never use a label
the agent is unable to produce. Files whose names start with "_" (the template) are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentforge_core.tools.flag_inconsistency import FindingType

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"
TIER_DIRS = {1: "tier1_cross_reference", 2: "tier2_defined_terms", 3: "tier3_amendment_contradiction"}


def normalize_location(location: str) -> str:
    """'Section 15.1(a)' -> '15.1'. Same rule flag_inconsistency uses to verify locations."""
    base = re.sub(r"^(?:section|§)\s*", "", location.strip(), flags=re.I)
    return base.split("(")[0].strip().rstrip(".").lower()


_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
          "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15}


def in_scope(location: str, scope: str) -> bool:
    """Is a finding at `location` inside a task's `scope`? ('7.02' is in 'Article VII' and 'Section 7')."""
    if scope == "whole_document":
        return True
    loc, sc = normalize_location(location), scope.strip().lower()
    if loc == sc:
        return True
    m = re.fullmatch(r"(?:section\s+)?(\d+(?:\.\d+)*)", sc)
    if m:
        return loc == m.group(1) or loc.startswith(m.group(1) + ".")
    m = re.fullmatch(r"article\s+([ivxl]+|\d+)", sc)
    if m:
        n = str(_ROMAN.get(m.group(1).upper(), m.group(1)))
        return loc.startswith(n + ".")
    return False


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a typo like "must_flagg" fails loudly


class ExpectedFinding(_Strict):
    """A finding the agent must report."""
    type: Union[FindingType, list[FindingType]]  # one label, or a short list of acceptable ones
    location: Union[str, list[str]]              # "15.1", or ["11.02", "11.11"] = flagging any one counts
    filing: Optional[str] = None                 # required only when the task has two filings

    def accepted_types(self) -> set[str]:
        return set(self.type) if isinstance(self.type, list) else {self.type}

    def accepted_locations(self) -> set[str]:
        locs = self.location if isinstance(self.location, list) else [self.location]
        return {normalize_location(loc) for loc in locs}

    @model_validator(mode="after")
    def _locations_not_empty(self) -> "ExpectedFinding":
        locs = self.location if isinstance(self.location, list) else [self.location]
        if not locs or any(not loc.strip() for loc in locs):
            raise ValueError("location must be a non-empty string or a non-empty list of them")
        return self


class Trap(_Strict):
    """Something that looks wrong but isn't. A finding that matches it fails the task.

    A finding matches when every field you set matches:
      location  same place ("1.01")            -- leave out for "anywhere in scope"
      type      one of these labels            -- leave out for "any label"
      mentions  any of these words appear in the finding's description or evidence
    Use type or mentions to tell a trap apart from a real finding at the same location.
    """
    location: Optional[str] = Field(None, min_length=1)
    type: Optional[Union[FindingType, list[FindingType]]] = None
    mentions: list[str] = []
    reason: str = Field(min_length=5)            # why it looks wrong but isn't
    filing: Optional[str] = None

    @model_validator(mode="after")
    def _says_where_or_what(self) -> "Trap":
        if self.location is None and not self.mentions:
            raise ValueError("a trap needs a location, mentions, or both")
        return self


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
                raise ValueError(f"task has two filings, so '{item.location or item.mentions}' needs a 'filing:'")
            if item.filing is not None and item.filing not in self.filings:
                raise ValueError(f"'{item.filing}' is not in this task's filings {self.filings}")
        return self


def load_task(path: Path) -> EvalTask:
    """Load and validate one task file. The id must equal the file name, the tier the folder."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: empty or not a YAML mapping. Delete it, or fill it in from _template.yaml")
    try:
        task = EvalTask.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise ValueError(f"{path.name}: {problems}") from None
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