"""Offline tests for llm.py: no network, no API cost.

A fake HTTP transport plays the LLM, so we can test retries and logging.
"""

import json

import httpx
import instructor
import pytest
from openai import OpenAI
from pydantic import BaseModel, field_validator

from agentforge_core.llm import LLMConfigError, get_settings, structured_call


class Ping(BaseModel):
    reply: str

    @field_validator("reply")
    @classmethod
    def must_be_ok(cls, v: str) -> str:
        if v != "ok":
            raise ValueError("reply must be 'ok'")
        return v


def fake_client(replies: list[str], calls: list) -> instructor.Instructor:
    """An instructor client whose 'LLM' returns the given replies in order."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        content = replies[len(calls) - 1]
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0,
            "model": "deepseek-v4.1-flash-served",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        })

    oa = OpenAI(api_key="test", base_url="https://fake.local/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return instructor.from_openai(oa, mode=instructor.Mode.JSON)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)


def test_valid_reply_returns_object_and_record():
    calls = []
    ping, rec = structured_call([{"role": "user", "content": "hi"}], Ping,
                                client=fake_client(['{"reply": "ok"}'], calls))
    assert ping.reply == "ok"
    assert len(calls) == 1
    assert rec.profile == "deepseek"
    assert rec.requested_model == "deepseek-flash"
    assert rec.served_model == "deepseek-v4.1-flash-served"
    assert rec.prompt_tokens == 12 and rec.training_data_ok is True
    assert calls[0]["temperature"] == 0


def test_bad_reply_is_retried_once():
    calls = []
    ping, _ = structured_call(
        [{"role": "user", "content": "hi"}], Ping,
        client=fake_client(['{"reply": "nope"}', '{"reply": "ok"}'], calls))
    assert ping.reply == "ok"
    assert len(calls) == 2  # first failed validation, second fixed it


def test_two_bad_replies_fail_loudly():
    calls = []
    with pytest.raises(Exception):
        structured_call([{"role": "user", "content": "hi"}], Ping,
                        client=fake_client(['{"reply": "no"}', '{"reply": "still no"}'], calls))
    assert len(calls) == 2  # never a third attempt


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(LLMConfigError, match="DEEPSEEK_API_KEY"):
        get_settings()


def test_groq_profile_is_marked_not_for_training(monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    s = get_settings()
    assert s.model == "qwen/qwen3.8-27b" and s.training_data_ok is False


def test_unknown_profile(monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "gemini")
    with pytest.raises(LLMConfigError, match="unknown"):
        get_settings()