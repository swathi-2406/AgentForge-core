"""Day 8 task 5: raw run traces convert into the locked Trajectory schema."""
import copy

import pytest

from evals.harness.trajectories import NOT_RECORDED, to_trajectory

AMEND, ORIG = "redwire_credit_amend1", "redwire_credit_original"


def raw_run(nested=True):
    """A Day 6 checkpoint-shaped run: step 1's naive diff is partial, the retry succeeds."""
    def rec(sid, n, tool, ms, expected):
        step = {"step_id": sid, "tool_name": tool, "tool_args": {"original_id": ORIG, "amendment_id": AMEND},
                "expected_outcome": expected}
        obs = {"step_id": sid, "status": "ok", "latency_ms": ms, "raw_output": {"changes": ["..."]},
               "output_bytes": 120}
        return {"attempt": n, "step": step, "observation": obs} if nested else {"attempt": n, **step, **obs}

    return {
        "run_id": "run00001", "schema_version": "v1", "task": "Does the amendment contradict the original?",
        "filing_ids": [ORIG, AMEND], "status": "completed",
        "started_at": "2026-10-18T10:00:00.000+00:00", "finished_at": "2026-10-18T10:00:02.000+00:00",
        "plan": [{"step_id": 1, "tool_name": "diff_filings", "tool_args": {}, "expected_outcome": "differences"},
                 {"step_id": 2, "tool_name": "extract_definitions", "tool_args": {}, "expected_outcome": "terms"}],
        "steps": [rec(1, 1, "diff_filings", 900.0, "differences"),
                  rec(1, 2, "compare_amended_clauses", 400.0, "contradictions in amended clauses only"),
                  rec(2, 1, "extract_definitions", 50.0, "terms")],
        "events": [
            {"kind": "critique", "step_id": 1, "attempt": 1, "tool_name": "diff_filings", "verdict": "partial",
             "reasoning": "flags intended ratio changes", "confidence": 0.8, "source": "llm"},
            {"kind": "retry", "step_id": 1, "attempt": 2, "from_tool": "diff_filings",
             "to_tool": "compare_amended_clauses", "tool_args": {}, "why_different": "compare only amended clauses"},
            {"kind": "critique", "step_id": 1, "attempt": 2, "tool_name": "compare_amended_clauses",
             "verdict": "success", "reasoning": "finds the 7.11 date issue", "confidence": 0.9, "source": "llm"},
            {"kind": "critique", "step_id": 2, "attempt": 1, "tool_name": "extract_definitions",
             "verdict": "success", "reasoning": "ok", "confidence": 0.95, "source": "rule"},
            {"kind": "finding", "step_id": 1, "type": "date_inconsistency", "location": "7.11",
             "description": "lead-in March 30 vs table March 31", "severity": "medium"},
            {"kind": "final", "status": "succeeded", "retry_count": 1, "completed_steps": [1, 2], "final_plan": []},
        ],
    }


@pytest.mark.parametrize("nested", [True, False])
def test_checkpoint_run_converts(nested):
    t = to_trajectory(raw_run(nested), task_id="t3_rwa_covenant_7_11")
    s1 = t.steps[0]
    assert t.total_retries == 1 and len(s1.attempts) == 2
    assert s1.attempts[0].critique.verdict == "partial"
    assert s1.attempts[1].retry_reason == "compare only amended clauses"
    assert s1.attempts[1].expected_outcome == "contradictions in amended clauses only"
    assert t.findings[0].type == "date_inconsistency" and t.findings[0].step_id == 1
    assert t.total_latency_ms == 2000.0              # wall clock beats the 1350 ms step sum
    assert t.steps[1].attempts[0].critique.source == "rule"


def test_failed_run_keeps_its_give_up_reason():
    run = raw_run()
    run["status"] = "failed"
    run["events"].append({"kind": "give_up", "step_id": 2, "attempt": 1, "reason": "retries used up"})
    assert to_trajectory(run).give_up_reason == "retries used up"


def test_day5_run_without_plan_or_critique_still_converts():
    run = raw_run()
    run.update(plan=[], events=[], steps=run["steps"][:1])
    t = to_trajectory(run)
    assert t.plan[0].tool_name == "diff_filings" and t.steps[0].attempts[0].critique is None


def test_retry_without_event_is_marked_not_dropped():
    run = raw_run()
    run["events"] = [e for e in run["events"] if e["kind"] != "retry"]
    assert to_trajectory(run).steps[0].attempts[1].retry_reason == NOT_RECORDED


def test_unfinished_run_is_refused():
    run = raw_run()
    run["status"] = "running"
    with pytest.raises(ValueError, match="only finished runs"):
        to_trajectory(run)


def test_bad_finding_type_is_refused():
    run = copy.deepcopy(raw_run())
    run["events"][4]["type"] = "list_numbering"
    with pytest.raises(Exception):
        to_trajectory(run)