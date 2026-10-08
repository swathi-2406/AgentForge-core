# """Orchestrator: the agent loop.

# Day 6 loop, run_self_correcting():

#     plan ──► for each step:
#                execute ──► critique ──► success? ──► findings ──► next step
#                                │
#                                └─ partial / failure ──► retry ONE step ──► execute again
#                                                           │
#                                                           └─ retries used up ──► task failed, stop

#     Every arrow updates State and writes a trace event:
#         step      (record_step, Day 5)   what ran and what came back, per attempt
#         critique  verdict, reasoning, confidence, source (rule / llm)
#         retry     old tool -> new tool, why_different, attempt number
#         finding   one per flag_inconsistency call
#         give_up   why the task stopped early

# Day 5 loop, run_plan(): no critic, no retry, no LLM. Kept unchanged for free runs and tests.

#     python -m agentforge_core.orchestrator --latest tva_facility_lease              self-correcting
#     python -m agentforge_core.orchestrator --latest tva_facility_lease --no-critic  Day 5 loop, free
#     python -m agentforge_core.orchestrator data/plans/<file>.json
# """

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field

from agentforge_core.critic import Critique, CriticError, critique
from agentforge_core.executor import Observation, execute_and_record
from agentforge_core.plan import PlannedStep
from agentforge_core.planner import PLANS_DIR, PlanResult
from agentforge_core.retry import Attempt, RetryError, RetryLimitReached, replace_step, revise_step
from agentforge_core.state import State
from agentforge_core.tools import REGISTRY, call_tool
from agentforge_core.tracing import trace_writer


class RunResult(BaseModel):
    """What one run produced. The full detail lives in the trace."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str  # completed | failed
    observations: list[Observation]  # the final attempt of each step that ran
    critiques: list[Critique] = Field(default_factory=list)  # every critique, all attempts
    retries: int = 0
    findings: list[str] = Field(default_factory=list)  # finding_ids from flag_inconsistency
    final_plan: list[PlannedStep] = Field(default_factory=list)
    stop_reason: Optional[str] = None

    def count(self, status: str) -> int:
        return sum(o.status == status for o in self.observations)


# ================================================================ Day 5 loop (unchanged)

def run_plan(task: str, filing_ids: list[str], steps: list[PlannedStep]) -> RunResult:
    """Execute every step in order and trace the whole run. No critic, no retry, no LLM.

    A failing step doesn't stop the run, and the run is 'failed' if any step errored.
    """
    run_id = trace_writer.start_run(task, filing_ids, plan=steps)
    observations = [execute_and_record(run_id, step) for step in steps]
    status = "failed" if any(o.status == "error" for o in observations) else "completed"
    trace_writer.finish_run(run_id, status)
    return RunResult(run_id=run_id, status=status, observations=observations)


# ================================================================ findings

def _pick(field: str, preferred: list[str]) -> str:
    """First preferred value that flag_inconsistency's Literal allows, else its first value."""
    allowed = list(get_args(REGISTRY["flag_inconsistency"].Input.model_fields[field].annotation))
    return next((p for p in preferred if p in allowed), allowed[0])


def findings_from(step: PlannedStep, obs: Observation) -> list[dict[str, Any]]:
    """Turn a successful step's output into flag_inconsistency args. One extractor per tool;
    tools without one produce no findings yet (Day 9 adds more as eval tasks need them)."""
    out = obs.raw_output or {}
    if step.tool_name != "compare_amended_clauses":
        return []
    amend, orig = step.tool_args["amendment_id"], step.tool_args["original_id"]
    found = []
    for f in out.get("internal_flags", []):
        who = "introduced by the amendment" if not f.get("in_original") else "also present in the original"
        found.append(dict(filing_id=amend, related_filing_id=orig, location=f["ref"],
                          type=_pick("type", ["date_inconsistency", "date_mismatch", "amendment_conflict"]),
                          severity=_pick("severity", ["medium"]),
                          description=f"Restated §{f['ref']} uses near-identical dates ({f['detail']}); {who}."))
    for c in out.get("clauses", []):
        if c.get("status") == "missing_in_original":
            found.append(dict(filing_id=amend, related_filing_id=orig, location=c["ref"].split()[0],
                              type=_pick("type", ["term_mismatch", "undefined_term", "amendment_conflict"]),
                              severity=_pick("severity", ["medium"]),
                              description=f"{c['ref']}: the amendment {c['directive']}, but the original "
                                          "has no matching clause or definition."))
    return found


def flag_findings(run_id: str, step: PlannedStep, obs: Observation) -> list[str]:
    """Record every finding. A rejected finding is traced, never silently dropped."""
    ids = []
    for args in findings_from(step, obs):
        try:
            out = call_tool("flag_inconsistency", {**args, "run_id": run_id})
            ids.append(out.finding_id)
            trace_writer.record_event(run_id, "finding", {"step_id": step.step_id, "finding_id": out.finding_id,
                                                          "location": args["location"], "type": args["type"]})
        except Exception as e:  # noqa: BLE001
            trace_writer.record_event(run_id, "finding_rejected", {"step_id": step.step_id, "args": args,
                                                                   "error": f"{type(e).__name__}: {e}"[:400]})
    return ids


# ================================================================ Day 6 loop

Critic = Callable[..., Critique]
Reviser = Callable[..., Any]
Flagger = Callable[[str, PlannedStep, Observation], list[str]]


def run_self_correcting(task: str, filing_ids: list[str], steps: list[PlannedStep], *,
                        critic: Critic = critique, reviser: Reviser = revise_step,
                        flagger: Flagger = flag_findings, limit: Optional[int] = None) -> RunResult:
    """plan → for each step: execute → critique → (retry that step, up to the limit) → next."""
    state = State(task=task, contract_refs=list(filing_ids), plan=list(steps), status="running")
    run_id = trace_writer.start_run(task, filing_ids, plan=steps)
    finals: list[Observation] = []
    crits: list[Critique] = []
    findings: list[str] = []
    stop: Optional[str] = None

    for planned in steps:
        current, earlier, attempt = planned, [], 1
        while True:
            obs = execute_and_record(run_id, current, attempt=attempt)
            state.last_observation = obs.model_dump(mode="json")
            try:
                crit = critic(current, obs, task)
            except CriticError as e:
                stop = f"step {current.step_id}: critic unavailable ({e})"
                break
            crits.append(crit)
            trace_writer.record_event(run_id, "critique", {
                "step_id": current.step_id, "attempt": attempt, "tool_name": current.tool_name,
                **crit.model_dump(include={"verdict", "confidence", "reasoning", "source", "prompt_version"})})
            if crit.verdict == "success":
                break
            try:
                r = reviser(task, list(filing_ids), state.plan, current, obs, crit, earlier, limit=limit)
            except RetryLimitReached as e:
                stop = str(e)
                break
            except RetryError as e:
                stop = f"step {current.step_id}: no valid replacement ({e})"
                break
            earlier.append(Attempt(step=current, verdict=crit.verdict, reasoning=crit.reasoning))
            state.plan = replace_step(state.plan, r.step)
            state.retry_count += 1
            trace_writer.record_event(run_id, "retry", {
                "step_id": current.step_id, "attempt": attempt + 1, "from_tool": current.tool_name,
                "to_tool": r.step.tool_name, "tool_args": r.step.tool_args, "why_different": r.why_different})
            current, attempt = r.step, attempt + 1

        finals.append(obs)
        if stop:
            trace_writer.record_event(run_id, "give_up", {"step_id": current.step_id, "attempt": attempt,
                                                          "reason": stop})
            break
        state.completed_steps = state.completed_steps + [current.step_id]
        findings += flagger(run_id, current, obs)

    state.status = "failed" if stop else "succeeded"
    status = "failed" if stop else "completed"
    trace_writer.record_event(run_id, "final", {"status": state.status, "retry_count": state.retry_count,
                                                "completed_steps": state.completed_steps,
                                                "final_plan": [s.model_dump() for s in state.plan]})
    trace_writer.finish_run(run_id, status)
    return RunResult(run_id=run_id, status=status, observations=finals, critiques=crits,
                     retries=state.retry_count, findings=findings, final_plan=state.plan, stop_reason=stop)


# ================================================================ saved plans + CLI

def load_plan(path: Path) -> PlanResult:
    return PlanResult.model_validate_json(path.read_text(encoding="utf-8"))


def run_saved_plan(path: Path, self_correct: bool = False) -> RunResult:
    saved = load_plan(path)
    run = run_self_correcting if self_correct else run_plan
    return run(saved.task, saved.filing_ids, saved.plan.steps)


def latest_plan_for(filing_id: str, plans_dir: Path = PLANS_DIR) -> Path:
    """Newest saved plan whose first filing is filing_id (file names start with a UTC timestamp)."""
    matches = sorted(plans_dir.glob(f"*_{filing_id}.json"))
    if not matches:
        raise SystemExit(f"No saved plan for '{filing_id}' in {plans_dir}. "
                         f"Make one with: python -m agentforge_core.planner --run \"<task>\" {filing_id}")
    return matches[-1]


def print_timeline(result: RunResult) -> None:
    for ev in trace_writer.read_events(result.run_id):
        k = ev["kind"]
        if k == "critique":
            print(f"  step {ev['step_id']} try {ev['attempt']}  {ev['tool_name']:24} -> {ev['verdict']:8} "
                  f"({ev['confidence']:.2f}, {ev['source']})")
        elif k == "retry":
            print(f"  step {ev['step_id']} RETRY    {ev['from_tool']} -> {ev['to_tool']}")
            print(f"                 why: {ev['why_different'][:150]}")
        elif k == "finding":
            print(f"  step {ev['step_id']} FINDING  §{ev['location']} {ev['type']}  ({ev['finding_id']})")
        elif k in ("give_up", "finding_rejected"):
            print(f"  {k.upper()}: {ev.get('reason') or ev.get('error')}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m agentforge_core.orchestrator")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("plan_path", nargs="?", type=Path, help="a saved plan JSON")
    group.add_argument("--latest", metavar="FILING_ID", help="run the newest saved plan for this filing")
    ap.add_argument("--no-critic", action="store_true", help="Day 5 loop: no critic, no retry, no LLM")
    args = ap.parse_args(argv)

    path = args.plan_path or latest_plan_for(args.latest)
    result = run_saved_plan(path, self_correct=not args.no_critic)
    print(f"plan: {path.name}")
    print(f"run:  {result.run_id}  [{result.status}]  ok={result.count('ok')} empty={result.count('empty')} "
          f"error={result.count('error')}  retries={result.retries}  findings={len(result.findings)}")
    if not args.no_critic:
        print_timeline(result)
    print("details: python -m scripts.show_run")


if __name__ == "__main__":
    main()