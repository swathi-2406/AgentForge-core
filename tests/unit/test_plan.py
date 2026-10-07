"""Tests for agentforge_core/plan.py against the real tool registry. No LLM needed."""

import pytest
from pydantic import ValidationError

from agentforge_core.plan import Plan, PlannedStep, planner_tool_names

TVA = "tva_facility_lease"


def step(i=1, tool="extract_section_map", args=None, outcome="a non-empty section map"):
    return {
        "step_id": i,
        "tool_name": tool,
        "tool_args": {"filing_id": TVA} if args is None else args,
        "expected_outcome": outcome,
    }


# ---------- happy path ----------

def test_valid_two_step_plan():
    plan = Plan.model_validate({"steps": [
        step(1, "extract_section_map"),
        step(2, "extract_cross_references", {"filing_id": TVA}),
        # step(2, "extract_cross_references", {"filing_id": TVA, "only_issues": True}),
    ]})
    assert [s.tool_name for s in plan.steps] == ["extract_section_map", "extract_cross_references"]


def test_menu_excludes_flag_inconsistency():
    names = planner_tool_names()
    assert "flag_inconsistency" not in names
    assert "extract_section_map" in names


# ---------- tool_name ----------

def test_unknown_tool_rejected_and_lists_menu():
    with pytest.raises(ValidationError) as e:
        PlannedStep.model_validate(step(tool="diff_everything"))
    assert "Unknown tool 'diff_everything'" in str(e.value)
    assert "extract_section_map" in str(e.value)  # the model is told what IS allowed


def test_flag_inconsistency_not_plannable():
    args = {"filing_id": TVA, "location": "x", "description": "y"}
    with pytest.raises(ValidationError, match="can't be planned ahead"):
        PlannedStep.model_validate(step(tool="flag_inconsistency", args=args))


# ---------- tool_args ----------

def test_typo_in_arg_name_rejected():
    with pytest.raises(ValidationError, match="filing_idd"):
        PlannedStep.model_validate(step(args={"filing_idd": TVA}))


def test_missing_required_arg_rejected():
    with pytest.raises(ValidationError, match="filing_id"):
        PlannedStep.model_validate(step(args={}))


# def test_wrong_arg_type_rejected():
#     bad = {"filing_id": TVA, "limit": "lots"}
#     with pytest.raises(ValidationError, match="limit"):
#         PlannedStep.model_validate(step(tool="extract_cross_references", args=bad))

def test_wrong_arg_type_rejected():
    with pytest.raises(ValidationError, match="filing_id: Input should be a valid string"):
        PlannedStep.model_validate(step(args={"filing_id": 123}))

# ---------- other step fields ----------

def test_vague_outcome_rejected():
    with pytest.raises(ValidationError):
        PlannedStep.model_validate(step(outcome="works"))


def test_extra_field_on_step_rejected():
    with pytest.raises(ValidationError):
        PlannedStep.model_validate({**step(), "reason": "because"})


# ---------- plan-level ----------

def test_empty_plan_rejected():
    with pytest.raises(ValidationError):
        Plan.model_validate({"steps": []})


@pytest.mark.parametrize("ids", [[1, 3], [2, 3], [1, 1], [2, 1]])
def test_step_ids_must_be_1_to_n(ids):
    with pytest.raises(ValidationError, match="step_ids must be"):
        Plan.model_validate({"steps": [step(i) for i in ids]})


def test_filing_outside_task_rejected_with_context():
    data = {"steps": [step(1, args={"filing_id": "redwire_credit_original"})]}
    with pytest.raises(ValidationError, match="only covers"):
        Plan.model_validate(data, context={"filing_ids": {TVA}})


def test_filing_check_skipped_without_context():
    data = {"steps": [step(1, args={"filing_id": "redwire_credit_original"})]}
    Plan.model_validate(data)  # no context → no filing restriction