"""LLM client for AgentForge-Core.

This is the ONLY module that knows which provider is in use. Everything else
(planner, critic, retry) calls `structured_call()` and never imports a provider SDK.

Switching provider = change LLM_PROFILE in .env. Nothing else changes.

Profiles
--------
deepseek (default)  DeepSeek V4.1 Flash. Main model for dev AND official runs.
                    Its terms allow using outputs to train other models (Tune).
groq                Free fallback for days DeepSeek is unavailable. Its outputs
                    must NOT go into Tune's training data (terms unverified),
                    so every call records which profile produced it.

There is deliberately NO automatic fallback: a run that silently switched models
halfway would mix two models' behaviour in one trace.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, TypeVar

import instructor
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Profile:
    base_url: str
    key_env: str
    default_model: str
    training_data_ok: bool  # may this profile's outputs feed AgentForge-Tune?


PROFILES: dict[str, Profile] = {
    "deepseek": Profile(
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-flash",
        training_data_ok=True,
    ),
    "groq": Profile(
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        default_model="qwen/qwen3.8-27b",
        training_data_ok=False,
    ),
}


class LLMConfigError(RuntimeError):
    """Raised when .env is missing something. Fails loudly at startup, not mid-run."""


@dataclass(frozen=True)
class LLMSettings:
    profile: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    training_data_ok: bool


def get_settings() -> LLMSettings:
    name = os.getenv("LLM_PROFILE", "deepseek").strip().lower()
    if name not in PROFILES:
        raise LLMConfigError(
            f"LLM_PROFILE={name!r} is unknown. Use one of: {', '.join(PROFILES)}"
        )
    profile = PROFILES[name]

    api_key = os.getenv(profile.key_env, "").strip()
    if not api_key:
        raise LLMConfigError(
            f"LLM_PROFILE={name} needs {profile.key_env} in your .env file."
        )

    return LLMSettings(
        profile=name,
        model=os.getenv("LLM_MODEL", "").strip() or profile.default_model,
        base_url=profile.base_url,
        api_key=api_key,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
        training_data_ok=profile.training_data_ok,
    )


@lru_cache(maxsize=1)
def get_client() -> instructor.Instructor:
    """One shared client per process.

    Mode.JSON (not strict JSON-schema mode): DeepSeek supports JSON output but not
    strict schemas, so our Pydantic validators do the real enforcement.
    """
    s = get_settings()
    return instructor.from_openai(
        OpenAI(api_key=s.api_key, base_url=s.base_url),
        mode=instructor.Mode.JSON,
    )


@dataclass(frozen=True)
class CallRecord:
    """What every LLM call logs. Goes into the trace (Day 5) and eval results."""

    profile: str
    requested_model: str
    served_model: str  # what the API says it ran; aliases like deepseek-flash move
    called_at_utc: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    training_data_ok: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def structured_call(
    messages: list[dict[str, str]],
    response_model: type[T],
    *,
    max_retries: int = 1,
    validation_context: dict[str, Any] | None = None,
    client: instructor.Instructor | None = None,
) -> tuple[T, CallRecord]:
    """Call the LLM and return a validated Pydantic object plus a CallRecord.

    max_retries=1 means: if the output fails validation, instructor sends the
    error back to the model ONCE (2 attempts total in instructor 1.17).
    If both fail, instructor raises; callers wrap that in their own error.
    """
    s = get_settings()
    client = client or get_client()

    called_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    start = time.perf_counter()
    obj, completion = client.create_with_completion(
        model=s.model,
        messages=messages,
        response_model=response_model,
        max_retries=max_retries,
        context=validation_context,
        temperature=s.temperature,
    )
    latency_ms = int((time.perf_counter() - start) * 1000)

    usage = getattr(completion, "usage", None)
    record = CallRecord(
        profile=s.profile,
        requested_model=s.model,
        served_model=getattr(completion, "model", None) or s.model,
        called_at_utc=called_at,
        latency_ms=latency_ms,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        training_data_ok=s.training_data_ok,
    )
    return obj, record