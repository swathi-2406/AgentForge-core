# """Writes structured execution traces to persistent storage."""
# """Run traces: what the agent did, step by step, in each run.

#     data/traces/runs/<run_id>.json   the full story of one run (steps + observations)
#     data/traces/agentforge.db        SQLite: one row per run, one row per step attempt
#     data/traces/tool_calls.jsonl     (Day 2) every tool call, now tagged with run_id/step_id

# The JSON is the detailed record Project 2 will train on. SQLite is for quick questions
# ("which tool fails most?") without opening hundreds of JSON files.

# Rules:
#   - Tracing never breaks the agent. If a write fails, it's logged and the run continues.
#   - SCHEMA_VERSION marks this as the Day 5 draft. Day 8 locks the final shape.
#   - Big tool outputs (like read_document's full text) are capped in the JSON.
# """

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from agentforge_core.tracing.tool_calls import tool_calls_path

if TYPE_CHECKING:  # type hints only, so tracing never imports the agent
    from agentforge_core.executor import Observation
    from agentforge_core.plan import PlannedStep

log = logging.getLogger(__name__)

SCHEMA_VERSION = "day5-draft"
MAX_OUTPUT_BYTES = 200_000  # per step, in the JSON trace
PREVIEW_CHARS = 2_000
RunStatus = Literal["running", "completed", "failed"]

# ---------- where things live ----------


def traces_dir() -> Path:
    """Same folder as tool_calls.jsonl, so tests' tmp redirect covers both."""
    return tool_calls_path().parent


def run_json_path(run_id: str) -> Path:
    return traces_dir() / "runs" / f"{run_id}.json"


def db_path() -> Path:
    return traces_dir() / "agentforge.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    task         TEXT NOT NULL,
    filing_ids   TEXT NOT NULL,          -- JSON list
    status       TEXT NOT NULL,          -- running | completed | failed
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    n_steps      INTEGER NOT NULL DEFAULT 0,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    run_id       TEXT NOT NULL,
    step_id      INTEGER NOT NULL,
    attempt      INTEGER NOT NULL,       -- 1 = first try; Day 6 retries add 2, 3, ...
    tool_name    TEXT NOT NULL,
    tool_args    TEXT NOT NULL,          -- JSON
    status       TEXT NOT NULL,          -- ok | empty | error
    error_kind   TEXT,
    error_type   TEXT,
    empty_reason TEXT,
    latency_ms   REAL NOT NULL,
    output_bytes INTEGER,
    started_at   TEXT NOT NULL,
    PRIMARY KEY (run_id, step_id, attempt)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _connect() -> closing[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)
    return closing(con)  # closing() matters on Windows: open files block tmp cleanup


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write via a temp file, so a crash mid-write never leaves half a trace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _cap_output(raw: Optional[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], Optional[int]]:
    """(output to store, its full size in bytes). Huge outputs become a preview."""
    if raw is None:
        return None, None
    text = json.dumps(raw, ensure_ascii=False)
    size = len(text.encode("utf-8"))
    if size <= MAX_OUTPUT_BYTES:
        return raw, size
    return {"_truncated": True, "bytes": size, "keys": sorted(raw), "preview": text[:PREVIEW_CHARS]}, size


# ---------- the API the agent uses ----------


def start_run(task: str, filing_ids: list[str], plan: Optional[list[PlannedStep]] = None) -> str:
    """Begin a run. Returns its run_id (always, even if writing fails)."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    started = _now()
    try:
        _write_json(run_json_path(run_id), {
            "schema_version": SCHEMA_VERSION, "run_id": run_id, "task": task, "filing_ids": list(filing_ids),
            "status": "running", "started_at": started, "finished_at": None,
            "plan": [s.model_dump(mode="json") for s in plan] if plan else None, "steps": [],
        })
        with _connect() as con, con:
            con.execute("INSERT INTO runs (run_id, task, filing_ids, status, started_at, schema_version) "
                        "VALUES (?, ?, ?, 'running', ?, ?)",
                        (run_id, task, json.dumps(list(filing_ids)), started, SCHEMA_VERSION))
    except Exception:  # noqa: BLE001
        log.warning("trace_writer: could not start run %s", run_id, exc_info=True)
    return run_id


def record_step(run_id: str, step: PlannedStep, obs: Observation, attempt: int = 1) -> None:
    """Append one step (what was planned + what happened) to the run's JSON and SQLite."""
    try:
        stored_output, size = _cap_output(obs.raw_output)
        entry = {"attempt": attempt, "step": step.model_dump(mode="json"),
                 "observation": {**obs.model_dump(mode="json"), "raw_output": stored_output}}

        path = run_json_path(run_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["steps"].append(entry)
        _write_json(path, data)

        with _connect() as con, con:
            con.execute(
                "INSERT OR REPLACE INTO steps (run_id, step_id, attempt, tool_name, tool_args, status, error_kind, "
                "error_type, empty_reason, latency_ms, output_bytes, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, obs.step_id, attempt, obs.tool_name, json.dumps(obs.tool_args), obs.status,
                 obs.error_kind, obs.error_type, obs.empty_reason, obs.latency_ms, size, obs.started_at))
            con.execute("UPDATE runs SET n_steps = n_steps + 1 WHERE run_id = ?", (run_id,))
    except Exception:  # noqa: BLE001
        log.warning("trace_writer: could not record step %s of run %s", obs.step_id, run_id, exc_info=True)


def finish_run(run_id: str, status: RunStatus) -> None:
    try:
        finished = _now()
        path = run_json_path(run_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(status=status, finished_at=finished)
        _write_json(path, data)
        with _connect() as con, con:
            con.execute("UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?", (status, finished, run_id))
    except Exception:  # noqa: BLE001
        log.warning("trace_writer: could not finish run %s", run_id, exc_info=True)


# ---------- reading back ----------


def read_run(run_id: str) -> dict[str, Any]:
    return json.loads(run_json_path(run_id).read_text(encoding="utf-8"))


def step_rows(run_id: str) -> list[dict[str, Any]]:
    if not db_path().exists():
        return []
    with _connect() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY step_id, attempt", (run_id,))
        return [dict(r) for r in rows]


def latest_run_id() -> Optional[str]:
    if not db_path().exists():
        return None
    with _connect() as con:
        row = con.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return row[0] if row else None


# ---------- Day 6: events (critique, retry, finding, give_up) ----------

EVENTS_DDL = ("CREATE TABLE IF NOT EXISTS events (run_id TEXT, at TEXT, kind TEXT, "
              "step_id INTEGER, attempt INTEGER, payload TEXT)")


def record_event(run_id: str, kind: str, payload: dict[str, Any]) -> None:
    """Append one event to the run's JSON (under "events") and to the SQLite events table.

    Same rule as record_step: tracing must never crash a run, so failures only log a warning.
    """
    try:
        at = _now()
        path = run_json_path(run_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("events", []).append({"at": at, "kind": kind, **payload})
        _write_json(path, data)
        with _connect() as con, con:
            con.execute(EVENTS_DDL)
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                        (run_id, at, kind, payload.get("step_id"), payload.get("attempt"),
                         json.dumps(payload, default=str)))
    except Exception:  # noqa: BLE001
        log.warning("trace_writer: could not record %s event for run %s", kind, run_id, exc_info=True)


def read_events(run_id: str) -> list[dict[str, Any]]:
    return read_run(run_id).get("events", [])
