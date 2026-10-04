# """Log every tool call: which tool, the args, how long it took, how big the result was, and any error.

#     data/traces/tool_calls.jsonl    one JSON line per call

# This is the Day 2 stand-in for trace_writer.py. Sprint 2's orchestrator sets the run and step with
# `with trace_context(run_id=..., step_id=...):` and these lines become part of the full trajectory.

# Tracing must never break the agent: if writing the log fails, the tool call still succeeds.
# Set AGENTFORGE_TRACE=0 to switch it off.
# """

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from pydantic import BaseModel

from agentforge_core.paths import get_root

log = logging.getLogger(__name__)
_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("run_id", default=None)
_step_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("step_id", default=None)
MAX_STR = 500        # long arg strings are cut in the log (the tool still gets the full value)
PREVIEW = 300


def tool_calls_path() -> Path:
    folder = os.getenv("AGENTFORGE_TRACES_DIR")
    return (Path(folder) if folder else get_root() / "data" / "traces") / "tool_calls.jsonl"


@contextmanager
def trace_context(run_id: Optional[str] = None, step_id: Optional[str] = None) -> Iterator[None]:
    """Tag every tool call inside this block with a run and step id."""
    run_token = _run_id.set(run_id if run_id is not None else _run_id.get())
    step_token = _step_id.set(step_id)
    try:
        yield
    finally:
        _step_id.reset(step_token)
        _run_id.reset(run_token)


def _shorten(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_STR:
        return value[:MAX_STR] + f"... [{len(value)} chars]"
    if isinstance(value, dict):
        return {k: _shorten(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shorten(v) for v in value[:50]]
    return value


def _summarize(result: Any) -> dict:
    """Size and shape of the output, not the whole thing (Redwire outputs can be megabytes)."""
    try:
        text = result.model_dump_json() if hasattr(result, "model_dump_json") else json.dumps(result, default=str)
    except Exception:  # noqa: BLE001 -- a summary is best-effort
        text = str(result)
    counts = {}
    if isinstance(result, BaseModel):
        counts = {k: len(v) for k, v in vars(result).items() if isinstance(v, (list, dict))}
    return {"type": type(result).__name__, "bytes": len(text.encode("utf-8")), "counts": counts,
            "preview": text[:PREVIEW]}


def _write(record: dict) -> None:
    if os.getenv("AGENTFORGE_TRACE", "1") == "0":
        return
    try:
        path = tool_calls_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001 -- never let logging break a tool call
        log.warning("Could not write tool-call trace: %s", e)


def traced(fn: Callable) -> Callable:
    """Wrap call_tool(name, args): time it, log it, re-raise any error unchanged."""

    @functools.wraps(fn)
    def wrapper(name: str, args: Optional[dict] = None, *rest, **kw):
        record = {
            "call_id": uuid.uuid4().hex[:12],
            "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": _run_id.get(), "step_id": _step_id.get(),
            "tool": name, "args": _shorten(dict(args or {})),
        }
        start = time.perf_counter()
        try:
            result = fn(name, args, *rest, **kw)
        except Exception as e:
            record.update(status="error", latency_ms=round((time.perf_counter() - start) * 1000, 2),
                          error_type=type(e).__name__, error=str(e)[:1000])
            _write(record)
            raise
        record.update(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2),
                      output=_summarize(result))
        _write(record)
        return result

    return wrapper


def read_tool_calls(run_id: Optional[str] = None) -> list[dict]:
    path = tool_calls_path()
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if run_id is None or r.get("run_id") == run_id]