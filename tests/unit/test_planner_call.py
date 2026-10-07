"""Offline tests for make_plan (Day 4 task 3). A fake LLM replies; no network, no cost."""

import json

import httpx
import instructor
import pytest
from openai import OpenAI

from agentforge_core.planner import PROMPT_VERSION, PlannerError, make_plan, save_plan

TVA = "tva_facility_lease"
TASK = "Check this contract for cross-references to sections that don't exist."


def plan_json(*steps):
    return json.dumps({"steps": list(steps)})


def step(i, tool, filing=TVA, outcome="a section map for tva_facility_lease with sections"):
    return {"step_id": i, "tool_name": tool, "tool_args": {"filing_id": filing},
            "expected_outcome": outcome}


GOOD = plan_json(step(1, "extract_section_map"), step(2, "extract_cross_references"))
BAD_TOOL = plan_json(step(1, "diff_everything"))
WRONG_FILING = plan_json(step(1, "extract_section_map", filing="redwire_credit_original"))


def fake_client(replies, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0,
            "model": "deepseek-served",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": replies[len(calls) - 1]}}],
            "usage": {"prompt_tokens": 1400, "completion_tokens": 120, "total_tokens": 1520},
        })
    oa = OpenAI(api_key="test", base_url="https://fake.local/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return instructor.from_openai(oa, mode=instructor.Mode.JSON)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)


def all_text(request_body):
    return "\n".join(str(m.get("content")) for m in request_body["messages"])


# ---------- success ----------

def test_good_plan_first_try():
    calls = []
    r = make_plan(TASK, [TVA], client=fake_client([GOOD], calls))
    assert len(calls) == 1
    assert [s.tool_name for s in r.plan.steps] == ["extract_section_map", "extract_cross_references"]
    assert r.prompt_version == PROMPT_VERSION
    assert r.call["served_model"] == "deepseek-served"
    assert r.call["prompt_tokens"] == 1400


def test_prompt_sent_is_the_planner_prompt():
    calls = []
    make_plan(TASK, [TVA], client=fake_client([GOOD], calls))
    sent = all_text(calls[0])
    assert "planning component" in sent and TASK in sent


# ---------- one retry ----------

def test_bad_tool_fixed_on_retry_and_error_was_sent_back():
    calls = []
    r = make_plan(TASK, [TVA], client=fake_client([BAD_TOOL, GOOD], calls))
    assert len(calls) == 2
    assert "Unknown tool 'diff_everything'" in all_text(calls[1])  # the model saw why
    assert len(r.plan.steps) == 2


def test_filing_outside_task_triggers_retry():
    calls = []
    make_plan(TASK, [TVA], client=fake_client([WRONG_FILING, GOOD], calls))
    assert len(calls) == 2
    assert "only covers" in all_text(calls[1])


# ---------- loud failure ----------

def test_two_bad_plans_raise_planner_error():
    calls = []
    with pytest.raises(PlannerError, match="diff_everything"):
        make_plan(TASK, [TVA], client=fake_client([BAD_TOOL, BAD_TOOL], calls))
    assert len(calls) == 2  # never a third attempt


def test_not_json_twice_raises_planner_error():
    calls = []
    with pytest.raises(PlannerError, match="No valid plan"):
        make_plan(TASK, [TVA], client=fake_client(["sure! step 1...", "{oops"], calls))
    assert len(calls) == 2


def test_bad_input_fails_before_any_call():
    calls = []
    with pytest.raises(PlannerError, match="Unknown filing"):
        make_plan(TASK, ["redwire_credit_amend2"], client=fake_client([GOOD], calls))
    assert calls == []  # no tokens spent


# ---------- saving ----------

def test_saved_plan_round_trips(tmp_path):
    r = make_plan(TASK, [TVA], client=fake_client([GOOD], []))
    path = save_plan(r, tmp_path)
    assert path.name.endswith("_tva_facility_lease.json")
    loaded = type(r).model_validate_json(path.read_text(encoding="utf-8"))
    assert loaded.model_dump() == r.model_dump()  # instructor adds a private raw-response attr