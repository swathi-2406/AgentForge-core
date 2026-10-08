"""Tests for the Day 6 self-correcting loop. Fake tools, fake critic, fake retry: no LLM, no filings.

The filing ids are real manifest ids only because State checks them against manifest.yaml.
"""

import pytest

from agentforge_core import orchestrator as orch
from agentforge_core.critic import Critique, CriticError
from agentforge_core.executor import EMPTY_RULES
from agentforge_core.plan import PlannedStep
from agentforge_core.retry import RetryError, RetryLimitReached
from agentforge_core.tools import base
from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tracing import trace_writer as tw

IDS = ["redwire_credit_original", "redwire_credit_amend1"]
TASK = "Does the First Amendment contradict the original credit agreement?"


class In(ToolInput):
    n: int = 1


class Out(ToolOutput):
    items: list[int]


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch):
    saved = dict(base.REGISTRY)
    for tool_name in ("fake_noisy", "fake_clean", "fake_other"):
        @register_tool
        class T(Tool):
            name = tool_name
            description = "Returns n items."
            Input, Output = In, Out

            def run(self, args):
                return Out(items=list(range(args.n)))
        monkeypatch.setitem(EMPTY_RULES, tool_name, lambda out, args: None if out["items"] else "no items")
    yield
    base.REGISTRY.clear()
    base.REGISTRY.update(saved)


def step(i, tool, n=1):
    return PlannedStep.model_construct(step_id=i, tool_name=tool, tool_args={"n": n},
                                       expected_outcome=f"a list of {n} items from {tool}")


PLAN = [step(1, "fake_noisy", 50), step(2, "fake_other", 3)]


def critic_rule(step, obs, task=None):
    """'fake_noisy' is always partial, everything else succeeds."""
    verdict = "partial" if step.tool_name == "fake_noisy" else "success"
    return Critique(step_id=step.step_id, verdict=verdict, confidence=0.8, source="llm",
                    reasoning=f"{step.tool_name} returned {len((obs.raw_output or {}).get('items', []))} items.")


class R:
    def __init__(self, step):
        self.step, self.why_different = step, "Uses the clean tool instead of the noisy one."


def switch_to_clean(task, ids, plan, failed, obs, crit, earlier, limit=None):
    return R(step(failed.step_id, "fake_clean", 2))


def no_flags(run_id, step, obs):
    return []


def run(steps=PLAN, **kw):
    kw.setdefault("critic", critic_rule)
    kw.setdefault("reviser", switch_to_clean)
    kw.setdefault("flagger", no_flags)
    return orch.run_self_correcting(TASK, IDS, steps, **kw)


def kinds(run_id):
    return [e["kind"] for e in tw.read_events(run_id)]


# ---------------------------------------------------------------- paths through the loop

def test_all_succeed_first_try():
    r = run([step(1, "fake_clean"), step(2, "fake_other")])
    assert (r.status, r.retries, r.stop_reason) == ("completed", 0, None)
    assert kinds(r.run_id) == ["critique", "critique", "final"]


def test_partial_then_retry_succeeds():
    r = run()
    assert (r.status, r.retries) == ("completed", 1)
    assert [s.tool_name for s in r.final_plan] == ["fake_clean", "fake_other"]   # only step 1 changed
    assert [c.verdict for c in r.critiques] == ["partial", "success", "success"]
    assert kinds(r.run_id) == ["critique", "retry", "critique", "critique", "final"]
    assert r.observations[0].tool_name == "fake_clean"                              # final attempt kept
    tries = [(s["step"]["step_id"], s["attempt"]) for s in tw.read_run(r.run_id)["steps"]]
    assert tries == [(1, 1), (1, 2), (2, 1)]


def test_retry_event_says_what_changed():
    r = run()
    ev = next(e for e in tw.read_events(r.run_id) if e["kind"] == "retry")
    assert (ev["step_id"], ev["attempt"], ev["from_tool"], ev["to_tool"]) == (1, 2, "fake_noisy", "fake_clean")
    final = tw.read_events(r.run_id)[-1]
    assert final["status"] == "succeeded" and final["retry_count"] == 1 and final["completed_steps"] == [1, 2]


def test_limit_reached_fails_the_task_and_stops():
    def give_up(*a, **k):
        raise RetryLimitReached("step 1 already used 2 retries; giving up")
    r = run(reviser=give_up)
    assert r.status == "failed" and "already used 2 retries" in r.stop_reason
    assert len(r.observations) == 1                                    # step 2 never ran
    assert kinds(r.run_id) == ["critique", "give_up", "final"]
    assert tw.read_run(r.run_id)["status"] == "failed"


def test_noisy_forever_hits_the_real_limit():
    keep_noisy = lambda task, ids, plan, failed, obs, crit, earlier, limit=None: R(  # noqa: E731
        step(failed.step_id, "fake_noisy", failed.tool_args["n"] + 1))
    calls = []

    def counting(*a, **k):
        calls.append(1)
        earlier = a[6]
        if len(earlier) + 1 > k["limit"]:
            raise RetryLimitReached("limit")
        return keep_noisy(*a, **k)
    r = run(reviser=counting, limit=2)
    assert r.status == "failed" and r.retries == 2 and len(calls) == 3


def test_bad_replacement_fails_the_task():
    def broken(*a, **k):
        raise RetryError("no valid replacement: unknown tool")
    r = run(reviser=broken)
    assert r.status == "failed" and "no valid replacement" in r.stop_reason


def test_critic_down_fails_without_retrying():
    def down(*a, **k):
        raise CriticError("rate limited")
    def must_not(*a, **k):
        raise AssertionError("no retry when the critic is down")
    r = run(critic=down, reviser=must_not)
    assert r.status == "failed" and "critic unavailable" in r.stop_reason
    assert kinds(r.run_id) == ["give_up", "final"]


def test_findings_only_after_success():
    seen = []
    def flagger(run_id, st, obs):
        seen.append(st.tool_name)
        return [f"f{len(seen)}"]
    r = run(flagger=flagger)
    assert seen == ["fake_clean", "fake_other"] and r.findings == ["f1", "f2"]


# ---------------------------------------------------------------- findings from compare_amended_clauses

CMP_STEP = PlannedStep.model_construct(step_id=1, tool_name="compare_amended_clauses",
                                       tool_args={"original_id": IDS[0], "amendment_id": IDS[1]},
                                       expected_outcome="each amended clause compared")


class FakeObs:
    raw_output = {"clauses": [{"ref": "1.01 Lenders", "status": "missing_in_original",
                               "directive": "revised by: restates a definition the original doesn't have"},
                              {"ref": "2.02(a)", "status": "changed", "directive": "x"}],
                  "internal_flags": [{"ref": "7.11", "kind": "date_near_mismatch",
                                      "detail": "March 30, 2021 vs March 31, 2021", "in_original": False}]}


def test_findings_from_compare_output():
    found = orch.findings_from(CMP_STEP, FakeObs())
    assert [f["location"] for f in found] == ["7.11", "1.01"]
    assert all(f["filing_id"] == IDS[1] and f["related_filing_id"] == IDS[0] for f in found)
    assert "introduced by the amendment" in found[0]["description"]
    allowed = orch.REGISTRY["flag_inconsistency"].Input.model_fields["type"].annotation.__args__
    assert all(f["type"] in allowed for f in found)


def test_other_tools_give_no_findings_yet():
    assert orch.findings_from(step(1, "fake_clean"), FakeObs()) == []


def test_flag_findings_records_and_never_drops(monkeypatch):
    calls = []
    class Ok:
        finding_id = "abc123"
    def fake_call(name, args):
        calls.append(args)
        if args["location"] == "1.01":
            raise ValueError("bad type")
        return Ok()
    monkeypatch.setattr(orch, "call_tool", fake_call)
    run_id = tw.start_run(TASK, IDS, plan=[CMP_STEP])
    ids = orch.flag_findings(run_id, CMP_STEP, FakeObs())
    assert ids == ["abc123"] and all(c["run_id"] == run_id for c in calls)
    assert kinds(run_id) == ["finding", "finding_rejected"]       # the failure is traced, not lost


# ---------------------------------------------------------------- Day 5 loop untouched

def test_run_plan_still_has_no_critic():
    r = orch.run_plan(TASK, IDS, [step(1, "fake_noisy", 5)])
    assert r.status == "completed" and r.critiques == [] and tw.read_run(r.run_id).get("events") is None