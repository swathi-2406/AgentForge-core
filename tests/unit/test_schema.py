"""Day 8 task 4: the trajectory schema accepts a real-looking run and rejects drift."""
import copy
import json

import pytest
from pydantic import ValidationError

from evals.harness.schema import JSON_SCHEMA_PATH, SCHEMA_VERSION, Trajectory, json_schema


def attempt(n, tool="extract_section_map", verdict="success", ms=10.0, why=None):
    return {"attempt": n, "tool_call": {"tool_name": tool, "tool_args": {"filing_id": "redwire_credit_amend1"}},
            "expected_outcome": "the clauses the amendment changes",
            "observation": {"status": "ok", "latency_ms": ms, "output_preview": "..."},
            "critique": {"verdict": verdict, "reasoning": "checked against expected_outcome", "confidence": 0.8},
            "retry_reason": why}


def run(**over):
    """The Day 6 checkpoint shape: step 2's naive diff is partial, its retry succeeds."""
    data = {
        "run_id": "abc12345", "task_id": "t3_rwa_covenant_7_11",
        "task": "Check whether the amended covenant in Section 7.11 contradicts the original.",
        "filings": ["redwire_credit_amend1", "redwire_credit_original"],
        "plan": [{"step_id": 1, "tool_name": "fetch_related_filing", "tool_args": {}, "expected_outcome": "original"},
                 {"step_id": 2, "tool_name": "diff_text", "tool_args": {}, "expected_outcome": "contradictions"}],
        "steps": [{"step_id": 1, "attempts": [attempt(1, "fetch_related_filing")]},
                  {"step_id": 2, "attempts": [attempt(1, "diff_text", "partial", 20.0),
                                              attempt(2, "compare_clauses", "success", 30.0,
                                                      why="naive diff flagged intended ratio changes")]}],
        "findings": [{"finding_id": "f1", "type": "date_inconsistency", "location": "7.11",
                      "filing_id": "redwire_credit_amend1", "description": "lead-in March 30 vs table March 31",
                      "severity": "medium", "step_id": 2}],
        "final_status": "completed", "started_at": "2026-10-18T10:00:00Z", "finished_at": "2026-10-18T10:00:05Z",
        "total_latency_ms": 60.0, "total_retries": 1,
    }
    data.update(over)
    return data


def test_checkpoint_run_validates():
    t = Trajectory.model_validate(run())
    assert t.schema_version == SCHEMA_VERSION == "v1"
    assert t.steps[1].retries == 1 and t.steps[1].final.critique.verdict == "success"


def test_round_trips_through_json():
    t = Trajectory.model_validate(run())
    assert Trajectory.model_validate_json(t.model_dump_json()) == t


def test_reward_starts_empty_and_accepts_minus_one_to_one():
    data = run()
    assert Trajectory.model_validate(data).steps[0].attempts[0].reward is None
    data["steps"][0]["attempts"][0]["reward"] = -1
    Trajectory.model_validate(data)


def broken(edit):
    data = copy.deepcopy(run())
    edit(data)
    return data


@pytest.mark.parametrize("edit, why", [
    (lambda d: d.update(total_retries=0), "retry count doesn't match the steps"),
    (lambda d: d.update(total_latency_ms=1.0), "total latency less than the steps' sum"),
    (lambda d: d["steps"][1]["attempts"][1].update(retry_reason=None), "retry without a reason"),
    (lambda d: d["steps"][1]["attempts"][0].update(retry_reason="x"), "attempt 1 with a retry_reason"),
    (lambda d: d["steps"][1]["attempts"][1].update(attempt=3), "attempt numbers skip"),
    (lambda d: d["steps"][0]["attempts"][0].update(reward=2), "reward out of range"),
    (lambda d: d["steps"][0]["attempts"][0]["critique"].update(verdict="ok"), "unknown verdict"),
    (lambda d: d["findings"][0].update(type="list_numbering"), "finding type not in the enum"),
    (lambda d: d["findings"][0].update(step_id=9), "finding points at a missing step"),
    (lambda d: d["steps"].append({"step_id": 7, "attempts": [attempt(1)]}), "step not in the plan"),
    (lambda d: d.update(final_status="failed"), "failed run without a give_up_reason"),
    (lambda d: d.update(schema_version="day5-draft"), "old schema version"),
    (lambda d: d.update(extra_field=1), "unknown key"),
])
def test_drift_is_rejected(edit, why):
    with pytest.raises(ValidationError):
        Trajectory.model_validate(broken(edit))


def test_locked_json_schema_file_matches_the_code():
    """If this fails you changed schema.py: that's a migration. Bump SCHEMA_VERSION, add a changelog
    line, then regenerate with:  python -m evals.harness.schema"""
    assert JSON_SCHEMA_PATH.exists(), "run: python -m evals.harness.schema"
    assert json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8")) == json_schema()