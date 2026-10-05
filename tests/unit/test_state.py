"""Day 3: State validates a real example and rejects bad values loudly."""

import pytest
from pydantic import ValidationError

from agentforge_core.state import State


def redwire_example(**overrides) -> dict:
    """Hand-built mid-run state for the hard-tier amendment task."""
    data = {
        "task": "Check whether the First Amendment contradicts the original Redwire credit agreement.",
        "contract_refs": ["redwire_credit_amend1", "redwire_credit_original"],
        "plan": [
            {"step_id": 1, "tool_name": "read_document", "tool_args": {"filing_id": "redwire_credit_amend1"}},
            {"step_id": 2, "tool_name": "fetch_related_filing", "tool_args": {"filing_id": "redwire_credit_amend1"}},
            {"step_id": 3, "tool_name": "extract_dates", "tool_args": {"filing_id": "redwire_credit_amend1", "section": "7.11"}},
        ],
        "completed_steps": [1, 2],
        "last_observation": {"step_id": 2, "raw_output": {"filing_id": "redwire_credit_original"}},
        "retry_count": 1,
        "status": "running",
    }
    data.update(overrides)
    return data


def test_hand_built_example_validates_and_round_trips():
    state = State(**redwire_example())
    assert state.status == "running" and state.confidence is None
    assert State.model_validate_json(state.model_dump_json()) == state


def test_new_task_defaults():
    state = State(task="Find dangling cross-references.", contract_refs=["tva_facility_lease"])
    assert (state.plan, state.retry_count, state.status) == ([], 0, "pending")


@pytest.mark.parametrize("bad", [
    {"retry_count": -1},
    {"confidence": 1.5},
    {"status": "done"},
    {"contract_refs": ["redwire_credit_amend2"]},           # not in manifest
    {"contract_refs": ["tva_facility_lease"] * 2},          # duplicate
    {"contract_refs": []},
    {"task": ""},
    {"completed_steps": [1, 2, 9]},                         # 9 was never planned
    {"retries": 2},                                         # typo for retry_count
])
def test_bad_values_fail_loudly(bad):
    with pytest.raises(ValidationError):
        State(**redwire_example(**bad))


def test_rules_still_apply_after_creation():
    state = State(**redwire_example())
    state.retry_count += 1
    assert state.retry_count == 2
    with pytest.raises(ValidationError):
        state.confidence = -0.2
    with pytest.raises(ValidationError):
        state.status = "finished"