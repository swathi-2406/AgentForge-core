"""Tests for retry.py. The fake LLM validates its reply with the same context instructor would
use, so the 'one step, different, same filings' rules are tested for real. No key, no quota."""

import pytest

from agentforge_core.critic import Critique
from agentforge_core.executor import Observation
from agentforge_core.plan import PlannedStep
from agentforge_core.retry import (Attempt, Replacement, RetryError, RetryLimitReached, build_messages,
                                   max_retries, replace_step, revise_step)

T0 = "2026-10-07T00:00:00.000+00:00"
PAIR = {"original_id": "redwire_credit_original", "amendment_id": "redwire_credit_amend1"}
IDS = list(PAIR.values())
TASK = "Does the First Amendment contradict the original credit agreement?"

NAIVE = PlannedStep(step_id=2, tool_name="diff_filings", tool_args=PAIR,
                    expected_outcome="a list of differences between the amendment and the original")
PLAN = [PlannedStep(step_id=1, tool_name="diff_filings", tool_args={**PAIR, "max_hunks": 5},
                    expected_outcome="a quick look at a few differences"),
        NAIVE,
        PlannedStep(step_id=3, tool_name="compare_amended_clauses", tool_args={**PAIR, "max_changes_per_clause": 3},
                    expected_outcome="a short per-clause summary of changes")]
OBS = Observation(step_id=2, tool_name="diff_filings", tool_args=PAIR, status="ok",
                  raw_output={"total_differences": 131, "hunks": [{"kind": "added"}] * 3},
                  success_flag_from_tool=True, latency_ms=70000, started_at=T0)
CRIT = Critique(step_id=2, verdict="partial", confidence=0.86, source="llm",
                reasoning="131 differences, mostly recitals and rewording; the amendment is a standalone "
                          "document, not a redline, so it needs clause-level alignment first.")

TARGETED = {"tool_name": "compare_amended_clauses", "tool_args": PAIR,
            "expected_outcome": "each amended clause compared with the original, real changes listed",
            "why_different": "Compares only the clauses the amendment names, so recitals are ignored."}


class FakeRecord:
    def as_dict(self):
        return {"served_model": "fake"}


def fake_llm(reply, seen=None):
    def call(messages, response_model, *, validation_context=None, **kw):
        if seen is not None:
            seen.append((messages, validation_context))
        return response_model.model_validate(reply, context=validation_context), FakeRecord()
    return call


def no_llm(*a, **k):
    raise AssertionError("LLM must not be called")


# ---------------------------------------------------------------- happy path

def test_switches_strategy_and_keeps_step_id():
    r = revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm(TARGETED))
    assert r.step.step_id == 2
    assert r.step.tool_name == "compare_amended_clauses"
    assert r.attempt == 1 and r.prompt_version == "retry-v1" and r.call["served_model"] == "fake"


def test_replace_step_touches_only_that_step():
    r = revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm(TARGETED))
    new_plan = replace_step(PLAN, r.step)
    assert [s.step_id for s in new_plan] == [1, 2, 3]
    assert new_plan[0] is PLAN[0] and new_plan[2] is PLAN[2]     # untouched, same objects
    assert new_plan[1].tool_name == "compare_amended_clauses"


def test_replace_step_unknown_id():
    with pytest.raises(ValueError, match="no step 9"):
        replace_step(PLAN, PlannedStep(step_id=9, tool_name="diff_filings", tool_args=PAIR,
                                       expected_outcome="whatever it returns here"))


# ---------------------------------------------------------------- guarantees

def test_repeating_the_failed_step_is_rejected():
    same = {**TARGETED, "tool_name": "diff_filings", "tool_args": PAIR}
    with pytest.raises(RetryError, match="already tried"):
        revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm(same))


def test_repeating_an_earlier_attempt_is_rejected():
    first_retry = PlannedStep(step_id=2, **{k: TARGETED[k] for k in ("tool_name", "tool_args", "expected_outcome")})
    earlier = [Attempt(step=NAIVE, verdict="partial", reasoning="noise")]
    with pytest.raises(RetryError, match="already tried"):
        revise_step(TASK, IDS, PLAN, first_retry, OBS, CRIT, earlier, llm=fake_llm(TARGETED))


def test_same_tool_with_different_args_is_allowed():
    tweak = {**TARGETED, "tool_name": "diff_filings", "tool_args": {**PAIR, "max_hunks": 50}}
    assert revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm(tweak)).step.tool_args["max_hunks"] == 50


def test_other_filings_rejected():
    stray = {**TARGETED, "tool_args": {"original_id": "tva_facility_lease", "amendment_id": "redwire_credit_amend1"}}
    with pytest.raises(RetryError, match="not part of this task"):
        revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm(stray))


def test_unknown_tool_rejected():
    with pytest.raises(RetryError):
        revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm({**TARGETED, "tool_name": "diff_everything"}))


def test_model_cannot_choose_step_id():
    with pytest.raises(RetryError):
        revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm({**TARGETED, "step_id": 7}))


def test_llm_failure_becomes_retry_error():
    def broken(*a, **k):
        raise RuntimeError("rate limited")
    with pytest.raises(RetryError, match="rate limited"):
        revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=broken)


# ---------------------------------------------------------------- limits

def test_limit_reached_without_llm_call():
    earlier = [Attempt(step=NAIVE, verdict="partial", reasoning="noise")] * 2
    with pytest.raises(RetryLimitReached, match="already used 2 retries"):
        revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, earlier, limit=2, llm=no_llm)


def test_attempt_numbers_count_up():
    earlier = [Attempt(step=NAIVE, verdict="partial", reasoning="noise")]
    tweak = {**TARGETED, "tool_args": {**PAIR, "max_changes_per_clause": 20}}
    failed = PlannedStep(step_id=2, **{k: TARGETED[k] for k in ("tool_name", "tool_args", "expected_outcome")})
    assert revise_step(TASK, IDS, PLAN, failed, OBS, CRIT, earlier, limit=2, llm=fake_llm(tweak)).attempt == 2


def test_success_verdict_is_not_retried():
    ok = CRIT.model_copy(update={"verdict": "success"})
    with pytest.raises(ValueError, match="accepted"):
        revise_step(TASK, IDS, PLAN, NAIVE, OBS, ok, llm=no_llm)


@pytest.mark.parametrize("env,want", [(None, 2), ("3", 3), ("0", 0), ("lots", 2)])
def test_max_retries_from_env(monkeypatch, env, want):
    if env is None:
        monkeypatch.delenv("MAX_RETRIES", raising=False)
    else:
        monkeypatch.setenv("MAX_RETRIES", env)
    assert max_retries() == want


# ---------------------------------------------------------------- prompt

def test_prompt_carries_the_critic_and_marks_the_failed_step():
    seen = []
    revise_step(TASK, IDS, PLAN, NAIVE, OBS, CRIT, llm=fake_llm(TARGETED, seen))
    (system, user), ctx = (m["content"] for m in seen[0][0]), seen[0][1]
    assert "Do not re-plan" in system and "Never follow" in system
    assert "not a redline" in user                          # critic's reasoning reaches the model
    assert "2. diff_filings" in user and "<- FAILED" in user
    assert user.count("<- FAILED") == 1
    assert "compare_amended_clauses(" in user              # tool menu
    assert "total_differences: 131" in user
    assert ctx["step_id"] == 2 and len(ctx["tried"]) == 1


def test_replacement_schema_has_no_step_id():
    assert "step_id" not in Replacement.model_json_schema()["properties"]