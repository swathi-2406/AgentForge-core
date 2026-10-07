"""Tests for agentforge_core/tracing/trace_writer.py (Day 5 task 3).

conftest.py points AGENTFORGE_TRACES_DIR at a tmp folder, so nothing here touches data/traces.
"""

import json
import sqlite3

import pytest

from agentforge_core.executor import EMPTY_RULES, execute_and_record, execute_step
from agentforge_core.plan import PlannedStep
from agentforge_core.tools import base
from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tracing import trace_writer as tw
from agentforge_core.tracing.tool_calls import read_tool_calls


class EchoIn(ToolInput):
    text: str


class EchoOut(ToolOutput):
    echoed: str


@pytest.fixture(autouse=True)
def fake_tool(monkeypatch):
    saved = dict(base.REGISTRY)

    @register_tool
    class Echo(Tool):
        name = "fake_echo"
        description = "Returns the text it was given."
        Input, Output = EchoIn, EchoOut

        def run(self, args):
            return EchoOut(echoed=args.text)

    monkeypatch.setitem(EMPTY_RULES, "fake_echo", lambda out, args: None if out["echoed"] else "nothing")
    yield
    base.REGISTRY.clear()
    base.REGISTRY.update(saved)


def step(i=1, text="hi", tool="fake_echo"):
    return PlannedStep.model_construct(step_id=i, tool_name=tool, tool_args={"text": text},
                                       expected_outcome="the text comes back unchanged")


def rows(sql, *params):
    with sqlite3.connect(tw.db_path()) as con:
        out = con.execute(sql, params).fetchall()
    con.close()
    return out


# ---------- a full run ----------

def test_run_writes_json_and_sqlite():
    run_id = tw.start_run("echo things", ["tva_facility_lease"])
    execute_and_record(run_id, step(1, "a"))
    execute_and_record(run_id, step(2, ""))            # empty
    execute_and_record(run_id, step(3, tool="nope"))   # error
    tw.finish_run(run_id, "completed")

    data = tw.read_run(run_id)
    assert data["schema_version"] == tw.SCHEMA_VERSION
    assert data["status"] == "completed" and data["finished_at"]
    assert [s["observation"]["status"] for s in data["steps"]] == ["ok", "empty", "error"]
    assert data["steps"][0]["step"]["expected_outcome"]  # the plan side is kept next to the result

    assert rows("SELECT status, n_steps FROM runs WHERE run_id=?", run_id) == [("completed", 3)]
    got = rows("SELECT step_id, attempt, status, error_kind FROM steps WHERE run_id=? ORDER BY step_id", run_id)
    assert got == [(1, 1, "ok", None), (2, 1, "empty", None), (3, 1, "error", "bad_args")]


def test_tool_calls_are_linked_to_the_run():
    run_id = tw.start_run("t", ["x"])
    execute_and_record(run_id, step(1))
    calls = read_tool_calls(run_id)
    assert len(calls) == 1 and calls[0]["step_id"] == "1"


def test_plan_is_saved_when_given():
    s = PlannedStep(step_id=1, tool_name="fake_echo", tool_args={"text": "a"}, expected_outcome="echo comes back")
    run_id = tw.start_run("t", ["x"], plan=[s])
    assert tw.read_run(run_id)["plan"][0]["tool_name"] == "fake_echo"


def test_retries_get_their_own_row():
    run_id = tw.start_run("t", ["x"])
    execute_and_record(run_id, step(1, ""), attempt=1)
    execute_and_record(run_id, step(1, "fixed"), attempt=2)
    assert rows("SELECT attempt, status FROM steps WHERE run_id=? ORDER BY attempt", run_id) == [
        (1, "empty"), (2, "ok")]
    assert len(tw.read_run(run_id)["steps"]) == 2


def test_runs_do_not_mix():
    a, b = tw.start_run("a", ["x"]), tw.start_run("b", ["x"])
    execute_and_record(a, step(1))
    assert len(tw.step_rows(a)) == 1 and tw.step_rows(b) == []
    assert tw.latest_run_id() == b


# ---------- size and safety ----------

def test_huge_outputs_are_capped_but_size_is_kept(monkeypatch):
    monkeypatch.setattr(tw, "MAX_OUTPUT_BYTES", 100)
    run_id = tw.start_run("t", ["x"])
    execute_and_record(run_id, step(1, "x" * 5000))
    stored = tw.read_run(run_id)["steps"][0]["observation"]["raw_output"]
    assert stored["_truncated"] is True and stored["bytes"] > 5000
    assert tw.step_rows(run_id)[0]["output_bytes"] > 5000


def test_errors_have_no_output_bytes():
    run_id = tw.start_run("t", ["x"])
    execute_and_record(run_id, step(1, tool="nope"))
    assert tw.step_rows(run_id)[0]["output_bytes"] is None


def test_trace_failure_never_breaks_the_step(monkeypatch, caplog):
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(tw, "_write_json", broken)
    run_id = tw.start_run("t", ["x"])              # still returns an id
    obs = execute_and_record(run_id, step(1))      # still runs the tool
    tw.finish_run(run_id, "completed")             # still doesn't raise
    assert obs.status == "ok"
    assert "could not" in caplog.text


def test_execute_step_alone_writes_nothing():
    execute_step(step(1))
    assert not tw.db_path().exists()
    assert not (tw.traces_dir() / "runs").exists()


def test_json_is_valid_utf8_with_non_ascii():
    run_id = tw.start_run("Lessor’s “Rent”", ["x"])
    execute_and_record(run_id, step(1, "§ 13.2 — ok"))
    raw = tw.run_json_path(run_id).read_text(encoding="utf-8")
    assert "§ 13.2" in raw and json.loads(raw)["task"] == "Lessor’s “Rent”"