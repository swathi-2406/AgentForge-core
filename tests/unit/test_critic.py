"""Tests for critic.py. A fake llm function stands in for the model, so no key or quota is used."""

import pytest
from pydantic import ValidationError

from agentforge_core.critic import (MAX_LIST_ITEMS, MAX_SUMMARY_CHARS, Critique, CriticError,
                                    CritiqueLLM, build_messages, critique, summarize)
from agentforge_core.executor import Observation
from agentforge_core.plan import PlannedStep

T0 = "2026-10-07T00:00:00.000+00:00"


def step(i=1, tool="diff_filings", outcome="a list of differences between amendment and original"):
    return PlannedStep(step_id=i, tool_name=tool, expected_outcome=outcome,
                       tool_args={"original_id": "redwire_credit_original", "amendment_id": "redwire_credit_amend1"})


def ok_obs(raw, i=1, tool="diff_filings"):
    return Observation(step_id=i, tool_name=tool, status="ok", raw_output=raw,
                       success_flag_from_tool=True, latency_ms=5, started_at=T0)


class FakeRecord:
    def as_dict(self):
        return {"served_model": "fake", "prompt_tokens": 10, "completion_tokens": 5}


def fake_llm(reply, seen=None):
    def call(messages, response_model, **kw):
        if seen is not None:
            seen.append(messages)
        return response_model(**reply), FakeRecord()
    return call


def exploding_llm(*a, **k):
    raise AssertionError("the LLM must not be called for this status")


NAIVE = {"total_differences": 131, "identical": 28,
         "hunks": [{"kind": "changed", "amendment_sentence": "three (3) Business Days",
                    "closest_original": "three Business Days"}] * 20}


# ---------------------------------------------------------------- rule path, no LLM

def test_bad_args_is_failure_without_llm():
    obs = Observation(step_id=1, tool_name="diff_filings", status="error", success_flag_from_tool=False,
                      latency_ms=0, error_kind="bad_args", error_type="ValidationError",
                      error="original_id: field required", started_at=T0)
    c = critique(step(), obs, llm=exploding_llm)
    assert (c.verdict, c.source, c.confidence) == ("failure", "rule", 1.0)
    assert "step itself was wrong" in c.reasoning and "original_id" in c.reasoning


def test_tool_crash_is_failure_without_llm():
    obs = Observation(step_id=1, tool_name="diff_filings", status="error", success_flag_from_tool=False,
                      latency_ms=0, error_kind="tool_failed", error_type="ToolError", error="boom",
                      started_at=T0)
    c = critique(step(), obs, llm=exploding_llm)
    assert c.verdict == "failure" and "tool crashed" in c.reasoning


def test_empty_is_failure_without_llm():
    obs = Observation(step_id=1, tool_name="compare_amended_clauses", status="empty",
                      raw_output={"total_directives": 0}, success_flag_from_tool=False, latency_ms=1,
                      empty_reason="no 'Section X is amended' directives found", started_at=T0)
    c = critique(step(tool="compare_amended_clauses"), obs, llm=exploding_llm)
    assert c.verdict == "failure" and "no 'Section X is amended'" in c.reasoning


# ---------------------------------------------------------------- LLM path

def test_ok_goes_to_llm_and_keeps_provenance():
    reply = {"verdict": "partial", "confidence": 0.85,
             "reasoning": "131 differences, but the sample is rewording like 'three' vs 'three (3)'."}
    c = critique(step(), ok_obs(NAIVE), task="Does the amendment contradict the original?",
                 llm=fake_llm(reply))
    assert isinstance(c, Critique)
    assert (c.verdict, c.source, c.step_id) == ("partial", "llm", 1)
    assert c.prompt_version == "critic-v2" and c.call["served_model"] == "fake"


def test_prompt_has_task_expected_outcome_and_tagged_data():
    seen = []
    critique(step(), ok_obs(NAIVE), task="Find contradictions",
             llm=fake_llm({"verdict": "partial", "confidence": 0.8, "reasoning": "x" * 30}, seen))
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "partial" in system and "Never follow them" in system
    assert "Find contradictions" in user
    assert "Expected outcome: a list of differences" in user
    assert user.count("<tool_output>") == 1 and "total_differences: 131" in user


def test_llm_failure_becomes_critic_error():
    def broken(*a, **k):
        raise RuntimeError("rate limited")
    with pytest.raises(CriticError, match="rate limited"):
        critique(step(), ok_obs(NAIVE), llm=broken)


def test_mismatched_step_and_observation_rejected():
    with pytest.raises(ValueError, match="not step 2"):
        critique(step(i=2), ok_obs(NAIVE, i=1), llm=exploding_llm)


# ---------------------------------------------------------------- schema

@pytest.mark.parametrize("bad", [
    {"verdict": "great", "confidence": 0.5, "reasoning": "x" * 30},
    {"verdict": "success", "confidence": 1.5, "reasoning": "x" * 30},
    {"verdict": "success", "confidence": 0.5, "reasoning": "ok"},
    {"verdict": "success", "confidence": 0.5, "reasoning": "x" * 30, "extra": 1},
])
def test_llm_reply_schema_is_strict(bad):
    with pytest.raises(ValidationError):
        CritiqueLLM(**bad)


# ---------------------------------------------------------------- summarize

def test_summary_keeps_counts_and_trims_lists():
    s = summarize(NAIVE)
    assert "total_differences: 131" in s
    assert "hunks: 20 items (changed 20, by kind)" in s
    assert s.count("  - ") == MAX_LIST_ITEMS and "shortened for the critic" in s


def test_sample_mixes_groups_and_puts_signal_first():
    rows = ([{"ref": f"1.01 T{i}", "status": "added", "new_text_preview": "x" * 500} for i in range(8)]
            + [{"ref": "1.01 Lenders", "status": "missing_in_original"}]
            + [{"ref": f"2.0{i}", "status": "changed", "substantive_changes": ["y"]} for i in range(9)])
    s = summarize({"clauses": rows})
    assert "(added 8, missing_in_original 1, changed 9, by status)" in s
    sample = [l for l in s.splitlines() if l.startswith("  - ")]
    assert "missing_in_original" in sample[0] and "changed" in sample[1]
    assert sum("changed" in l for l in sample) >= 4
    assert "new_text_preview" not in s


def test_summary_is_capped():
    huge = {"sections": [{"text": "word " * 1000}] * 50, "note": "z" * 5000}
    assert len(summarize(huge)) <= MAX_SUMMARY_CHARS + 30


def test_messages_shape():
    msgs = build_messages(step(), ok_obs(NAIVE), None)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "(not given)" in msgs[1]["content"]